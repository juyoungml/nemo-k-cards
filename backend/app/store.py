"""Postgres store (BACKEND §8). Models are stored as jsonb documents; one table per kind.
The schema is in supabase/migrations, applied by the Supabase CLI (`supabase start`, `supabase db push`).
Calls block: async code runs them with `asyncio.to_thread`.

Organization isolation (#13): every function that reads or writes an Organization's rows takes that
Organization first and runs in `tenant(org_id)`: a transaction that has switched to the `wok_api` role (no RLS
bypass) and set `app.org_id`, so Postgres row-level security limits it to that Organization's rows whatever the
SQL says. Both settings are transaction-local: they end with the transaction and never reach the next user of
a pooled connection. `system()` is the narrow exception for work that spans Organizations (creating one,
restart recovery) and runs as the connecting role."""

import logging
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING
from uuid import UUID

from psycopg import Connection
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, ValidationError

from app.config import settings
from app.schemas import Brainstorm, Job, JobStatus, Material, PolicyEvent

if TYPE_CHECKING:
    from app.audit import Entry

API_ROLE = "wok_api"  # supabase/migrations/20261029140000_org_isolation.sql
_lock = threading.Lock()
_pool: ConnectionPool | None = None
_NUL = re.compile(r"(?<!\\)((?:\\\\)*)\\u0000")
log = logging.getLogger("store")


def _load[M: BaseModel](model: type[M], raw: str) -> M | None:
    """Validate a stored row; never let one bad/legacy row break a list endpoint."""
    try:
        return model.model_validate_json(raw)
    except ValidationError as e:
        log.warning("stored %s failed validation (%d errors); skipping", model.__name__, e.error_count())
        return None


def _json(model: BaseModel) -> str:
    """jsonb rejects NUL characters, so drop them (an escaped backslash before "u0000" is kept)."""
    return _NUL.sub(r"\1", model.model_dump_json())


def _reset(c: Connection) -> None:
    """A connection back in the pool keeps nothing session-level. `tenant` only sets transaction-local values,
    so this is belt and braces against a future `set role` / `set` without `local`."""
    c.execute("reset role; reset all")
    c.commit()


def _db() -> ConnectionPool:
    global _pool
    with _lock:
        if _pool is None:
            # prepare_threshold=None: also works behind Supabase's transaction pooler.
            _pool = ConnectionPool(settings.database_url, min_size=1, max_size=8, open=True, timeout=5,
                                   max_idle=300, check=ConnectionPool.check_connection, reset=_reset,
                                   kwargs={"prepare_threshold": None})
        return _pool


def close() -> None:
    global _pool
    with _lock:
        if _pool is not None:
            _pool.close()
            _pool = None


@contextmanager
def tenant(org_id: UUID | None, *, user_id: UUID | None = None) -> Iterator[Connection]:
    """One transaction as `wok_api`, scoped to `org_id`; or, before an Organization is chosen (sign-in), to the
    memberships of `user_id`. Commits on success, rolls back on error."""
    if org_id is None and user_id is None:
        raise ValueError("tenant() needs an Organization (or, for sign-in, a user)")
    with _db().connection() as c, c.transaction():
        c.execute(f"set local role {API_ROLE}")
        c.execute("select set_config('app.org_id', %s, true), set_config('app.user_id', %s, true)",
                  (str(org_id or ""), str(user_id or "")))
        yield c


@contextmanager
def system() -> Iterator[Connection]:
    """One transaction as the connecting role, which RLS does not limit. Only for work that belongs to no single
    Organization: creating one, recovering jobs after a restart."""
    with _db().connection() as c, c.transaction():
        yield c


def _put(org_id: UUID, table: str, id_: str, model: BaseModel, extra: dict | None = None,
         audit: "Entry | None" = None) -> None:
    cols = {"id": id_, "doc": _json(model), **(extra or {})}
    updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c != "id")
    # org_id is the column default (the transaction's Organization) and never updated; RLS refuses to overwrite
    # another Organization's row that happens to have the same id.
    with tenant(org_id, user_id=audit.actor if audit else None) as c:
        c.execute(f"insert into {table} ({', '.join(cols)}) values ({', '.join(['%s'] * len(cols))}) "
                  f"on conflict (id) do update set {updates}", list(cols.values()))
        if audit:  # same transaction: the change and its audit event are saved together or not at all
            from app import audit as audit_log
            audit_log.write(c, org_id, audit)


def _rows(org_id: UUID, sql: str, params: tuple) -> list[str]:
    with tenant(org_id) as c:
        return [r[0] for r in c.execute(sql, params).fetchall()]


def save_job(org_id: UUID, job: Job, *, audit: "Entry | None" = None) -> Job:
    stored = job.model_copy(update={"slide_sources": []}) if job.slide_sources else job  # view only (#25)
    _put(org_id, "jobs", job.id, stored, {"created_at": job.created_at}, audit)
    return job


@contextmanager
def locked_job(org_id: UUID, job_id: str, *, user_id: UUID | None = None) -> Iterator[tuple[Connection, Job | None]]:
    """One `tenant` transaction holding the Job's row lock (`for update`): read, decide, write and audit with no
    other writer in between (Deck edits and the Safeguard re-check, #19). Write back with `write_job(c, job)`.
    Yields (connection, None) when the Job isn't this Organization's."""
    with tenant(org_id, user_id=user_id) as c:
        row = c.execute("select doc::text from jobs where id = %s for update", (job_id,)).fetchone()
        yield c, (_load(Job, row[0]) if row else None)


def write_job(c: Connection, job: Job) -> None:
    """Update a Job inside `locked_job`'s transaction."""
    stored = job.model_copy(update={"slide_sources": []}) if job.slide_sources else job  # view only (#25)
    c.execute("update jobs set doc = %s where id = %s", (_json(stored), job.id))


def get_job(org_id: UUID, job_id: str) -> Job | None:
    rows = _rows(org_id, "select doc::text from jobs where id = %s", (job_id,))
    return _load(Job, rows[0]) if rows else None


def list_jobs(org_id: UUID, limit: int = 50) -> list[Job]:
    rows = _rows(org_id, "select doc::text from jobs order by created_at desc, id desc limit %s", (limit,))
    return [j for r in rows if (j := _load(Job, r))]


def jobs_in(statuses: set[JobStatus]) -> list[tuple[UUID, Job]]:
    """Every Organization's Jobs in these states, each with its Organization (restart recovery, via `system`).
    Rows from before Organizations existed are left alone."""
    with system() as c:
        rows = c.execute("select org_id, doc::text from jobs where org_id is not null and doc->>'status' = any(%s)",
                         ([str(s) for s in statuses],)).fetchall()
    return [(org, j) for org, raw in rows if (j := _load(Job, raw))]


def save_brainstorm(org_id: UUID, brainstorm: Brainstorm) -> Brainstorm:
    _put(org_id, "brainstorms", brainstorm.id, brainstorm)
    return brainstorm


def get_brainstorm(org_id: UUID, brainstorm_id: str) -> Brainstorm | None:
    rows = _rows(org_id, "select doc::text from brainstorms where id = %s", (brainstorm_id,))
    return _load(Brainstorm, rows[0]) if rows else None


def save_material(org_id: UUID, material: Material, *, audit: "Entry | None" = None) -> Material:
    """Insert one piece of Source material (append-only: the API role may not update it)."""
    with tenant(org_id, user_id=audit.actor if audit else None) as c:
        c.execute("insert into materials (id, created_at, doc) values (%s, %s, %s)",
                  (material.id, material.created_at, _json(material)))
        if audit:
            from app import audit as audit_log
            audit_log.write(c, org_id, audit)
    return material


def get_materials(org_id: UUID, ids: list[str]) -> list[Material]:
    """These ids' Source material in the given order; ids of another Organization (RLS) or unknown are left out."""
    if not ids:
        return []
    rows = dict(r for r in _pairs(org_id, "select id, doc::text from materials where id = any(%s)", (list(ids),)))
    return [m for i in dict.fromkeys(ids) if i in rows and (m := _load(Material, rows[i]))]


def _pairs(org_id: UUID, sql: str, params: tuple) -> list[tuple]:
    with tenant(org_id) as c:
        return c.execute(sql, params).fetchall()


def add_policy_events(org_id: UUID, events: list[PolicyEvent]) -> None:
    with tenant(org_id) as c:
        c.cursor().executemany("insert into policy_events (job_id, doc) values (%s, %s)",
                               [(e.job_id, _json(e)) for e in events])


def list_policy_events(org_id: UUID, limit: int = 200) -> list[PolicyEvent]:
    rows = _rows(org_id, "select doc::text from policy_events order by id desc limit %s", (limit,))
    return [e for r in rows if (e := _load(PolicyEvent, r))]
