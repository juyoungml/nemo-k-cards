"""Timed Posts (#26): approve for a KST time, the Post waits `scheduled` (the workflow sleeps durably), an Approver
can reschedule or cancel it until publishing starts, an edit puts it on hold, approving again resumes it with the new
version, and a time missed while the server was down holds it instead of posting late. Mock mode throughout (the
publisher's Instagram path is test_posts.py's)."""

import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from test_deck_versions import body, checked, post, ready_job

from app import posts, store
from app.pipeline import publishing
from app.schemas import JobStatus

KST = ZoneInfo("Asia/Seoul")


def at(delta: timedelta) -> str:
    """A KST timestamp `delta` from now, as the Admin sends it."""
    return (datetime.now(KST) + delta).replace(microsecond=0).isoformat()


@pytest.fixture
def no_publish(monkeypatch):
    """Approving queues the Post's workflow; record it instead of running it."""
    calls = []

    async def fake(*args, **kwargs):
        calls.append((args, kwargs))
    monkeypatch.setattr(publishing, "start", fake)
    return calls


def approve(client, job, **extra):
    return client.post(f"/jobs/{job.id}/approve", json={"deck_version": job.deck_version, "mode": "mock", **extra})


def audit_actions(client, job_id) -> list[str]:
    return [e["action"] for e in reversed(client.get("/audit").json()) if e["target"] == job_id]


# ---------------------------------------------------------------- scheduling

async def test_approve_for_later_schedules_the_post_and_the_job(client, no_publish):
    job = await ready_job(client.org_id)
    when = at(timedelta(days=2))
    r = await asyncio.to_thread(approve, client, job, scheduled_at=when)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "SCHEDULED"
    [p] = client.get(f"/posts?job_id={job.id}").json()
    assert p["state"] == "scheduled" and datetime.fromisoformat(p["scheduled_at"]) == datetime.fromisoformat(when)
    assert len(no_publish) == 1  # its workflow is queued now and sleeps until then (durable)
    assert "deck.approved" in audit_actions(client, job.id)
    # Scheduled: approving again is refused; reschedule or cancel instead.
    again = await asyncio.to_thread(approve, client, job)
    assert again.status_code == 409 and "already scheduled" in again.json()["detail"]


@pytest.mark.parametrize(("when", "why"), [
    (lambda: at(-timedelta(minutes=5)), "at least 2 minutes"),
    (lambda: at(timedelta(seconds=30)), "at least 2 minutes"),
    (lambda: at(timedelta(days=61)), "60 days"),
    (lambda: (datetime.now(KST) + timedelta(days=1)).replace(tzinfo=None).isoformat(), "timezone"),
])
async def test_a_bad_time_is_refused_before_anything_happens(client, no_publish, when, why):
    job = await ready_job(client.org_id)
    r = await asyncio.to_thread(approve, client, job, scheduled_at=when())
    assert r.status_code == 422 and why in r.json()["detail"]
    assert client.get(f"/posts?job_id={job.id}").json() == [] and no_publish == []


async def test_the_workflow_waits_until_the_time_then_publishes(client, no_publish, monkeypatch):
    job = await ready_job(client.org_id)
    await asyncio.to_thread(approve, client, job, scheduled_at=at(timedelta(hours=3)))
    [p] = posts.list_posts(client.org_id, job.id)
    # Before the time: one step says "sleep this long" and changes nothing.
    out = await publishing.advance(str(client.org_id), str(p.id))
    assert out["state"] == "scheduled" and not out["done"] and 3 * 3600 - 120 < out["wait"] <= 3 * 3600
    # The time has come (the durable sleep is over): it publishes like any other Post.
    monkeypatch.setattr(publishing, "MOCK_STEP_S", 0)
    monkeypatch.setattr(publishing, "_now", lambda: p.scheduled_at + timedelta(seconds=5))
    assert await publishing.run(client.org_id, p.id) == "published"
    assert store.get_job(client.org_id, job.id).status == JobStatus.PUBLISHED
    assert "post.started" in audit_actions(client, job.id)


async def test_a_time_missed_while_the_server_was_down_is_held_not_posted_late(client, no_publish, monkeypatch):
    job = await ready_job(client.org_id)
    await asyncio.to_thread(approve, client, job, scheduled_at=at(timedelta(minutes=10)))
    [p] = posts.list_posts(client.org_id, job.id)
    monkeypatch.setattr(publishing, "_now", lambda: p.scheduled_at + timedelta(hours=7))
    assert await publishing.run(client.org_id, p.id) == "on_hold"
    held = posts.get(client.org_id, p.id)
    assert held.state == "on_hold" and "server was down" in held.held_reason and held.finished_at is None
    assert store.get_job(client.org_id, job.id).status == JobStatus.READY_FOR_REVIEW


# ---------------------------------------------------------------- cancel and reschedule

async def test_cancel_before_publishing_starts(client, no_publish):
    job = await ready_job(client.org_id)
    await asyncio.to_thread(approve, client, job, scheduled_at=at(timedelta(days=1)))
    [p] = client.get(f"/posts?job_id={job.id}").json()
    r = client.post(f"/posts/{p['id']}/cancel")
    assert r.status_code == 200 and r.json()["state"] == "cancelled" and r.json()["finished_at"]
    assert store.get_job(client.org_id, job.id).status == JobStatus.READY_FOR_REVIEW
    assert "post.cancelled" in audit_actions(client, job.id)
    assert client.post(f"/posts/{p['id']}/cancel").status_code == 409
    # The Deck can be approved again (a cancelled Post posted nothing, so the version isn't "used").
    assert (await asyncio.to_thread(approve, client, job)).status_code == 200


async def test_once_publishing_has_started_it_cant_be_cancelled_or_rescheduled(client, no_publish):
    job = await ready_job(client.org_id)
    await asyncio.to_thread(approve, client, job)  # post now: queued straight away
    [p] = client.get(f"/posts?job_id={job.id}").json()
    assert client.post(f"/posts/{p['id']}/cancel").status_code == 409
    assert client.post(f"/posts/{p['id']}/reschedule", json={"scheduled_at": at(timedelta(days=1))}).status_code == 409


async def test_reschedule_moves_the_time_and_restarts_the_sleeping_workflow(client, no_publish, monkeypatch):
    restarted = []
    monkeypatch.setattr(publishing, "restart", lambda org, pid: restarted.append(pid))
    job = await ready_job(client.org_id)
    await asyncio.to_thread(approve, client, job, scheduled_at=at(timedelta(days=1)))
    [p] = client.get(f"/posts?job_id={job.id}").json()
    later = at(timedelta(days=3))
    r = client.post(f"/posts/{p['id']}/reschedule", json={"scheduled_at": later})
    assert r.status_code == 200 and datetime.fromisoformat(r.json()["scheduled_at"]) == datetime.fromisoformat(later)
    assert [str(x) for x in restarted] == [p["id"]]
    assert "post.rescheduled" in audit_actions(client, job.id)
    assert client.post(f"/posts/{p['id']}/reschedule",
                       json={"scheduled_at": at(-timedelta(hours=1))}).status_code == 422


def test_cancel_and_reschedule_need_the_approver_role(sign_in, auth_keys):
    from fastapi.testclient import TestClient
    from test_roles import add_member

    from app.main import app
    admin = sign_in(org="Roles")
    uid = add_member(admin.org_id, "editor")
    editor = TestClient(app, headers={"Authorization": f"Bearer {auth_keys.token(uid)}"})
    pid = "00000000-0000-4000-8000-000000000000"
    assert editor.post(f"/posts/{pid}/cancel").status_code == 403
    assert editor.post(f"/posts/{pid}/reschedule", json={"scheduled_at": at(timedelta(days=1))}).status_code == 403


# ---------------------------------------------------------------- edit after approval → hold → approve again

async def test_an_edit_after_approval_holds_the_post_and_approving_again_resumes_it(client, no_publish):
    job = await ready_job(client.org_id)
    when = at(timedelta(days=2))
    await asyncio.to_thread(approve, client, job, scheduled_at=when)
    [first] = client.get(f"/posts?job_id={job.id}").json()

    req = body(job)
    req["slides"][0]["heading"] = "This weekend in Seoul: three easy picks"
    r = await post(client, f"/jobs/{job.id}/versions", req)
    assert r.status_code == 201, r.text
    held = posts.get(client.org_id, first["id"])
    assert held.state == "on_hold" and "edited after approval" in held.held_reason and held.finished_at is None
    edited = await checked(client.org_id, job.id)
    assert edited.status == JobStatus.READY_FOR_REVIEW and edited.deck_version == 2
    assert "post.held" in audit_actions(client, job.id)
    # It shows as upcoming (on hold) until someone acts.
    assert [x["state"] for x in client.get("/posts?when=upcoming").json()] == ["on_hold"]

    # Approving version 2 (at the same time) replaces the held Post: a new one with the new version.
    r = await asyncio.to_thread(approve, client, edited, scheduled_at=when)
    assert r.status_code == 200, r.text
    states = {x["id"]: (x["state"], x["deck_version"]) for x in client.get(f"/posts?job_id={job.id}").json()}
    assert states.pop(first["id"]) == ("cancelled", 1)
    [(state, version)] = states.values()
    assert (state, version) == ("scheduled", 2)
    assert posts.get(client.org_id, first["id"]).last_error.startswith("replaced by the approval of version 2")


async def test_a_held_post_can_be_cancelled_or_rescheduled_and_stays_on_hold(client, no_publish):
    job = await ready_job(client.org_id)
    await asyncio.to_thread(approve, client, job, scheduled_at=at(timedelta(days=2)))
    [p] = client.get(f"/posts?job_id={job.id}").json()
    req = body(job)
    req["slides"][0]["heading"] = "Seoul this weekend, revised"
    await post(client, f"/jobs/{job.id}/versions", req)
    await checked(client.org_id, job.id)
    r = client.post(f"/posts/{p['id']}/reschedule", json={"scheduled_at": at(timedelta(days=4))})
    assert r.status_code == 200 and r.json()["state"] == "on_hold"
    assert client.post(f"/posts/{p['id']}/cancel").json()["state"] == "cancelled"


# ---------------------------------------------------------------- lists and restarts

async def test_upcoming_lists_scheduled_soonest_first_and_recent_the_rest(client, no_publish):
    a, b, c = [await ready_job(client.org_id) for _ in range(3)]
    await asyncio.to_thread(approve, client, a, scheduled_at=at(timedelta(days=3)))
    await asyncio.to_thread(approve, client, b, scheduled_at=at(timedelta(days=1)))
    await asyncio.to_thread(approve, client, c)
    upcoming = client.get("/posts?when=upcoming").json()
    assert [x["job_id"] for x in upcoming] == [b.id, a.id]
    assert [x["job_id"] for x in client.get("/posts?when=recent").json()] == [c.id]


async def test_after_a_restart_a_scheduled_post_is_driven_again_but_a_held_one_waits(client, no_publish, monkeypatch):
    enqueued = []

    class Q:
        def enqueue(self, fn, org, pid):
            enqueued.append(pid)
    monkeypatch.setattr(publishing.DBOS, "retrieve_queue", lambda name: Q())
    monkeypatch.setattr(publishing.DBOS, "get_workflow_status", lambda wid: None)
    sched, held = await ready_job(client.org_id), await ready_job(client.org_id)
    await asyncio.to_thread(approve, client, sched, scheduled_at=at(timedelta(days=1)))
    await asyncio.to_thread(approve, client, held, scheduled_at=at(timedelta(days=1)))
    [hp] = posts.list_posts(client.org_id, held.id)
    posts.transition(client.org_id, hp.id, {"scheduled"}, state="on_hold", held_reason="test")
    [sp] = posts.list_posts(client.org_id, sched.id)
    publishing.recover_after_restart()
    assert str(sp.id) in enqueued and str(hp.id) not in enqueued


async def test_the_migration_refuses_a_scheduled_post_without_a_time(client):
    job = await ready_job(client.org_id)
    with pytest.raises(Exception, match="posts_scheduled_has_time"), store.system() as c:
        c.execute("insert into posts (org_id, job_id, deck_version, mode, approved_by, state) "
                  "select org_id, id, 1, 'mock', gen_random_uuid(), 'scheduled' from jobs where id = %s",
                  (job.id,))
