"""Postgres store (BACKEND §8). Models are stored as jsonb documents; one table per kind.
The schema is in supabase/migrations, applied by the Supabase CLI (`supabase start`, `supabase db push`).
Calls block: async code runs them with `asyncio.to_thread`."""

import logging
import re
import threading

from psycopg_pool import ConnectionPool
from pydantic import BaseModel, ValidationError

from app.config import settings
from app.schemas import Draft, Job, PolicyEvent

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


def _db() -> ConnectionPool:
    global _pool
    with _lock:
        if _pool is None:
            # prepare_threshold=None: also works behind Supabase's transaction pooler.
            _pool = ConnectionPool(settings.database_url, min_size=1, max_size=8, open=True, timeout=5,
                                   max_idle=300, check=ConnectionPool.check_connection,
                                   kwargs={"prepare_threshold": None})
        return _pool


def close() -> None:
    global _pool
    with _lock:
        if _pool is not None:
            _pool.close()
            _pool = None


def _put(table: str, id_: str, model: BaseModel, extra: dict | None = None) -> None:
    cols = {"id": id_, "doc": _json(model), **(extra or {})}
    updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c != "id")
    with _db().connection() as c:
        c.execute(f"insert into {table} ({', '.join(cols)}) values ({', '.join(['%s'] * len(cols))}) "
                  f"on conflict (id) do update set {updates}", list(cols.values()))


def _rows(sql: str, params: tuple) -> list[str]:
    with _db().connection() as c:
        return [r[0] for r in c.execute(sql, params).fetchall()]


def save_job(job: Job) -> Job:
    _put("jobs", job.id, job, {"created_at": job.created_at})
    return job


def get_job(job_id: str) -> Job | None:
    rows = _rows("select doc::text from jobs where id = %s", (job_id,))
    return _load(Job, rows[0]) if rows else None


def list_jobs(limit: int = 50) -> list[Job]:
    rows = _rows("select doc::text from jobs order by created_at desc, id desc limit %s", (limit,))
    return [j for r in rows if (j := _load(Job, r))]


def save_draft(draft: Draft) -> Draft:
    _put("drafts", draft.id, draft)
    return draft


def get_draft(draft_id: str) -> Draft | None:
    rows = _rows("select doc::text from drafts where id = %s", (draft_id,))
    return _load(Draft, rows[0]) if rows else None


def add_policy_events(events: list[PolicyEvent]) -> None:
    with _db().connection() as c:
        c.cursor().executemany("insert into policy_events (job_id, doc) values (%s, %s)",
                               [(e.job_id, _json(e)) for e in events])


def list_policy_events(limit: int = 200) -> list[PolicyEvent]:
    rows = _rows("select doc::text from policy_events order by id desc limit %s", (limit,))
    return [e for r in rows if (e := _load(PolicyEvent, r))]
