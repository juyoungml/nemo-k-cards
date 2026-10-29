"""Postgres store: upserts, ordering, and one bad row never breaking a list."""

import json
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app import store
from app.schemas import Brainstorm, Job, JobStatus, PolicyEvent


def _job(minutes_ago: int) -> Job:
    return Job(id=secrets.token_hex(4), prompt="p", created_at=datetime.now(UTC) - timedelta(minutes=minutes_ago))


def test_save_job_upserts_and_lists_newest_first():
    old, new = _job(10), _job(0)
    store.save_job(new)
    store.save_job(old)
    store.save_job(old.model_copy(update={"status": JobStatus.FAILED}))
    assert store.get_job(old.id).status == JobStatus.FAILED
    ids = [j.id for j in store.list_jobs(500)]
    assert ids.index(new.id) < ids.index(old.id) and ids.count(old.id) == 1
    assert store.get_job("missing") is None


def test_nul_characters_are_dropped():
    job = store.save_job(_job(0).model_copy(update={"prompt": "a\x00b"}))
    assert store.get_job(job.id).prompt == "ab"
    literal = store.save_job(_job(0).model_copy(update={"prompt": "C:\\u0000"}))  # text, not a NUL
    assert store.get_job(literal.id).prompt == "C:\\u0000"


def test_brainstorm_round_trip():
    d = store.save_brainstorm(Brainstorm(id=f"d-{secrets.token_hex(4)}", tones=["playful"]))
    assert store.get_brainstorm(d.id) == d


def test_job_stored_before_the_rename_still_loads():
    """Jobs written before the glossary rename carry "draft_id"; they load as brainstorm_id and re-save under it."""
    job = _job(0)
    doc = job.model_dump(mode="json", exclude={"brainstorm_id"}) | {"draft_id": "d-old123"}
    with store._db().connection() as c:
        c.execute("insert into jobs (id, created_at, doc) values (%s, %s, %s)",
                  (job.id, job.created_at, json.dumps(doc)))
    loaded = store.get_job(job.id)
    assert loaded.brainstorm_id == "d-old123"
    store.save_job(loaded)
    with store._db().connection() as c:
        saved = c.execute("select doc from jobs where id = %s", (job.id,)).fetchone()[0]
    assert saved["brainstorm_id"] == "d-old123" and "draft_id" not in saved


def test_rename_migration_keeps_drafts_and_rls():
    """Apply the migrations to a scratch schema with a pre-rename draft row in between, then roll it all back."""
    migrations = sorted((Path(__file__).resolve().parents[2] / "supabase/migrations").glob("*.sql"))
    rename = next(m for m in migrations if "rename_drafts_to_brainstorms" in m.name)
    schema = f"mig_{secrets.token_hex(4)}"
    with store._db().connection() as c, c.transaction(force_rollback=True):
        c.execute(f"create schema {schema}")
        c.execute(f"set local search_path to {schema}")
        for m in migrations:
            if m.name < rename.name:
                c.execute(m.read_text())
        old = Brainstorm(id="d-legacy", tones=["playful"])
        c.execute("insert into drafts (id, doc) values (%s, %s)", (old.id, old.model_dump_json()))
        c.execute(rename.read_text())
        [(raw,)] = c.execute("select doc::text from brainstorms where id = %s", (old.id,)).fetchall()
        assert Brainstorm.model_validate_json(raw) == old
        [(rls,)] = c.execute("select relrowsecurity from pg_class where oid = 'brainstorms'::regclass").fetchall()
        assert rls is True


def test_policy_events_newest_first():
    events = [PolicyEvent(time=f"12:00:0{i}", sandbox="sb", binary="curl", host="x", request="GET /",
                          result="policy_denied", job_id="j") for i in range(3)]
    store.add_policy_events(events)
    assert store.list_policy_events(3) == events[::-1]


def test_bad_row_is_skipped():
    bad = _job(-60)  # newest, so it is in the first page
    with store._db().connection() as c:
        c.execute("insert into jobs (id, created_at, doc) values (%s, %s, '{\"id\": 1}')",
                  (bad.id, bad.created_at))
    assert bad.id not in [j.id for j in store.list_jobs()]


def test_jobs_limit_is_bounded(client):
    assert client.get("/jobs?limit=0").status_code == 422
    assert client.get("/jobs?limit=501").status_code == 422
    assert client.get("/jobs?limit=500").status_code == 200
