"""SQLite store (BACKEND §8). Models are stored as JSON; one table per kind."""

import logging
import sqlite3
import threading
from pathlib import Path

from pydantic import BaseModel, ValidationError

from app.config import settings
from app.schemas import Draft, Job, PolicyEvent

_lock = threading.Lock()
log = logging.getLogger("store")


def _load[M: BaseModel](model: type[M], raw: str) -> M | None:
    """Validate a stored row; never let one bad/legacy row break a list endpoint."""
    try:
        return model.model_validate_json(raw)
    except ValidationError as e:
        log.warning("stored %s failed validation (%d errors); skipping", model.__name__, e.error_count())
        return None


def _conn() -> sqlite3.Connection:
    db: Path = settings.output_dir.parent / "app.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(db, check_same_thread=False)
    c.execute("create table if not exists jobs (id text primary key, created_at text, json text)")
    c.execute("create table if not exists drafts (id text primary key, json text)")
    c.execute("create table if not exists policy_events (rowid integer primary key, job_id text, json text)")
    return c


_db = _conn()


def _put(table: str, id_: str, model: BaseModel, extra: dict | None = None) -> None:
    cols = {"id": id_, "json": model.model_dump_json(), **(extra or {})}
    with _lock:
        _db.execute(f"insert or replace into {table} ({','.join(cols)}) values ({','.join('?' * len(cols))})",
                    list(cols.values()))
        _db.commit()


def save_job(job: Job) -> Job:
    _put("jobs", job.id, job, {"created_at": job.created_at.isoformat()})
    return job


def get_job(job_id: str) -> Job | None:
    with _lock:  # one shared connection: reads must not interleave with writes
        row = _db.execute("select json from jobs where id = ?", (job_id,)).fetchone()
    return _load(Job, row[0]) if row else None


def list_jobs(limit: int = 50) -> list[Job]:
    with _lock:  # one shared connection: reads must not interleave with writes
        rows = _db.execute("select json from jobs order by created_at desc limit ?", (limit,)).fetchall()
    return [j for r in rows if (j := _load(Job, r[0]))]


def save_draft(draft: Draft) -> Draft:
    _put("drafts", draft.id, draft)
    return draft


def get_draft(draft_id: str) -> Draft | None:
    with _lock:  # one shared connection: reads must not interleave with writes
        row = _db.execute("select json from drafts where id = ?", (draft_id,)).fetchone()
    return _load(Draft, row[0]) if row else None


def add_policy_events(events: list[PolicyEvent]) -> None:
    with _lock:
        _db.executemany("insert into policy_events (job_id, json) values (?, ?)",
                        [(e.job_id, e.model_dump_json()) for e in events])
        _db.commit()


def list_policy_events(limit: int = 200) -> list[PolicyEvent]:
    with _lock:  # one shared connection: reads must not interleave with writes
        rows = _db.execute("select json from policy_events order by rowid desc limit ?", (limit,)).fetchall()
    return [e for r in rows if (e := _load(PolicyEvent, r[0]))]
