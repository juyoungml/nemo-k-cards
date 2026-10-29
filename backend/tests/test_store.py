"""Postgres store: upserts, ordering, and one bad row never breaking a list."""

import secrets
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app import store
from app.main import app
from app.schemas import Draft, Job, JobStatus, PolicyEvent


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


def test_draft_round_trip():
    d = store.save_draft(Draft(id=f"d-{secrets.token_hex(4)}", tones=["playful"]))
    assert store.get_draft(d.id) == d


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


def test_jobs_limit_is_bounded():
    client = TestClient(app)
    assert client.get("/jobs?limit=0").status_code == 422
    assert client.get("/jobs?limit=501").status_code == 422
    assert client.get("/jobs?limit=500").status_code == 200
