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
    (lambda: at(-timedelta(minutes=5)), "at least 5 minutes"),
    (lambda: at(timedelta(minutes=4)), "at least 5 minutes"),
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
    # Before the time: one step says "sleep this long" (at most MAX_SLEEP, then it re-reads) and changes nothing.
    out = await publishing.advance(str(client.org_id), str(p.id))
    assert out["state"] == "scheduled" and not out["done"] and out["wait"] == 3600
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


# ---------------------------------------------------------------- follow-up (#66): races, Reject, audit, isolation

async def test_a_reschedule_that_lands_as_the_old_time_arrives_is_honoured(client, no_publish, monkeypatch):
    """The workflow reads the row as the old time arrives; an Approver reschedules (200) before it writes. The write
    must not start publishing at the old time."""
    monkeypatch.setattr(publishing, "restart", lambda org, pid: None)
    job = await ready_job(client.org_id)
    await asyncio.to_thread(approve, client, job, scheduled_at=at(timedelta(minutes=10)))
    [p] = posts.list_posts(client.org_id, job.id)
    seen = posts.raw(client.org_id, p.id)                                     # 1. the workflow reads the row
    monkeypatch.setattr(publishing, "_now", lambda: p.scheduled_at + timedelta(seconds=1))
    later = at(timedelta(days=3))
    r = await asyncio.to_thread(client.post, f"/posts/{p.id}/reschedule", json={"scheduled_at": later})
    assert r.status_code == 200                                               # 2. the reschedule lands
    out = await asyncio.to_thread(publishing._due, client.org_id, seen)       # 3. the workflow writes
    now = posts.get(client.org_id, p.id)
    assert now.state == "scheduled" and now.scheduled_at == datetime.fromisoformat(later)
    assert store.get_job(client.org_id, job.id).status == JobStatus.SCHEDULED
    assert not out["done"] and out["wait"] == 0  # it re-reads the row, and then sleeps toward the new time
    again = await publishing.advance(str(client.org_id), str(p.id))
    assert again["state"] == "scheduled" and 0 < again["wait"] <= publishing.MAX_SLEEP.total_seconds()


async def test_an_edit_and_a_post_becoming_due_at_the_same_moment_do_not_deadlock(client, no_publish, monkeypatch):
    """Each takes one row lock and then wants the other's (the Post's workflow: Post then Job; the edit: Job then
    Post) unless both lock the Job first. Postgres would abort one with DeadlockDetected."""
    import threading
    from contextlib import contextmanager

    from psycopg import errors

    from app import deck_versions
    job = await ready_job(client.org_id)
    await asyncio.to_thread(approve, client, job, scheduled_at=at(timedelta(minutes=10)))
    [p] = posts.list_posts(client.org_id, job.id)
    seen = posts.raw(client.org_id, p.id)
    monkeypatch.setattr(publishing, "_now", lambda: p.scheduled_at + timedelta(seconds=1))
    due_locked, edit_started, edit_locked = threading.Event(), threading.Event(), threading.Event()
    real_log = publishing._job_log

    def paused_log(*a, **k):  # the due transition, between its Post write and its Job write
        inner = real_log(*a, **k)

        def update(j):
            due_locked.set()
            edit_started.wait(30)
            edit_locked.wait(1.5)  # the edit holds the Job (before the fix) or waits for it (after)
            inner(j)
        return update
    monkeypatch.setattr(publishing, "_job_log", paused_log)
    real_locked = store.locked_job

    @contextmanager
    def locked(*a, **k):
        edit_started.set()
        with real_locked(*a, **k) as x:
            yield x
    monkeypatch.setattr(store, "locked_job", locked)
    real_hold = deck_versions._hold

    def hold(*a):  # the edit, holding the Job row, about to write the Post
        edit_locked.set()
        return real_hold(*a)
    monkeypatch.setattr(deck_versions, "_hold", hold)

    due = asyncio.ensure_future(asyncio.to_thread(publishing._due, client.org_id, seen))
    assert await asyncio.to_thread(due_locked.wait, 30)
    req = body(job)
    req["slides"][0]["heading"] = "This weekend in Seoul: three easy picks"
    results = await asyncio.gather(due, post(client, f"/jobs/{job.id}/versions", req), return_exceptions=True)
    assert not [r for r in results if isinstance(r, errors.DeadlockDetected)], results
    assert not [r for r in results if isinstance(r, BaseException)], results
    out, edit = results
    # The due Post got the Job row first: it is publishing, so the edit is refused cleanly (409), not a 500.
    assert out["state"] == "queued" and edit.status_code == 409, edit.text


async def test_rejecting_a_deck_cancels_its_held_post(client, no_publish):
    job = await ready_job(client.org_id)
    await asyncio.to_thread(approve, client, job, scheduled_at=at(timedelta(days=2)))
    [p] = client.get(f"/posts?job_id={job.id}").json()
    req = body(job)
    req["slides"][0]["heading"] = "Seoul this weekend, revised"
    assert (await post(client, f"/jobs/{job.id}/versions", req)).status_code == 201
    await checked(client.org_id, job.id)
    assert posts.get(client.org_id, p["id"]).state == "on_hold"
    r = await asyncio.to_thread(client.post, f"/jobs/{job.id}/reject", json={"reason": "not this week"})
    assert r.status_code == 200 and r.json()["status"] == "REJECTED"
    gone = posts.get(client.org_id, p["id"])
    assert gone.state == "cancelled" and gone.finished_at and str(gone.cancelled_by) == str(client.user_id)
    assert client.get("/posts?when=upcoming").json() == []
    assert audit_actions(client, job.id)[-2:] == ["deck.rejected", "post.cancelled"]


async def test_audit_says_scheduled_resumed_and_which_held_post_was_replaced(client, no_publish):
    job = await ready_job(client.org_id)
    when = at(timedelta(days=2))
    await asyncio.to_thread(approve, client, job, scheduled_at=when)
    [first] = client.get(f"/posts?job_id={job.id}").json()
    assert audit_actions(client, job.id)[-2:] == ["deck.approved", "post.scheduled"]
    req = body(job)
    req["slides"][0]["heading"] = "Seoul this weekend, revised"
    await post(client, f"/jobs/{job.id}/versions", req)
    edited = await checked(client.org_id, job.id)
    assert (await asyncio.to_thread(approve, client, edited, scheduled_at=when)).status_code == 200
    assert audit_actions(client, job.id)[-3:] == ["deck.approved", "post.cancelled", "post.resumed"]
    events = {e["action"]: e["detail"] for e in client.get("/audit").json() if e["target"] == job.id}
    new = next(x for x in client.get(f"/posts?job_id={job.id}").json() if x["id"] != first["id"])
    assert events["post.cancelled"]["post"] == first["id"] and events["post.cancelled"]["replaced_by"] == new["id"]
    assert events["post.resumed"]["post"] == new["id"] and events["post.resumed"]["replaces"] == first["id"]
    assert datetime.fromisoformat(events["post.resumed"]["scheduled_at"]) == datetime.fromisoformat(when)


async def test_re_approving_a_held_post_without_a_time_posts_now_and_says_so(client, no_publish):
    """API behaviour (documented on ApproveRequest): no `scheduled_at` means now, also when it replaces a held
    Post. The Admin always sends the held Post's time while it is still ahead."""
    job = await ready_job(client.org_id)
    await asyncio.to_thread(approve, client, job, scheduled_at=at(timedelta(days=2)))
    req = body(job)
    req["slides"][0]["heading"] = "Seoul this weekend, revised"
    await post(client, f"/jobs/{job.id}/versions", req)
    edited = await checked(client.org_id, job.id)
    r = await asyncio.to_thread(approve, client, edited)
    assert r.status_code == 200 and r.json()["status"] == "PUBLISHING"
    resumed = next(e["detail"] for e in client.get("/audit").json()
                   if e["target"] == job.id and e["action"] == "post.resumed")
    assert resumed["scheduled_at"] is None and resumed["now"] is True


async def test_a_waiting_workflow_sleeps_in_chunks_so_a_lost_restart_is_caught(client, no_publish, monkeypatch):
    """Reschedule saved, but the process died before the old workflow was swapped: that workflow wakes within
    MAX_SLEEP, re-reads the row and follows the new time (here: earlier)."""
    monkeypatch.setattr(publishing, "restart", lambda org, pid: None)  # the crash: no restart happened
    job = await ready_job(client.org_id)
    await asyncio.to_thread(approve, client, job, scheduled_at=at(timedelta(days=3)))
    [p] = posts.list_posts(client.org_id, job.id)
    first = await publishing.advance(str(client.org_id), str(p.id))
    assert first["wait"] == publishing.MAX_SLEEP.total_seconds() <= 3600
    sooner = at(timedelta(minutes=20))
    assert client.post(f"/posts/{p.id}/reschedule", json={"scheduled_at": sooner}).status_code == 200
    woke = await publishing.advance(str(client.org_id), str(p.id))
    assert 18 * 60 < woke["wait"] <= 20 * 60


def test_admin_and_api_agree_on_the_minimum_lead():
    import re
    from pathlib import Path
    web = Path(__file__).resolve().parents[2] / "apps/web/src"
    admin = re.search(r"MIN_LEAD_MINUTES = (\d+)", (web / "lib/format.ts").read_text())
    assert admin and timedelta(minutes=int(admin[1])) == posts.MIN_LEAD
    assert "MIN_LEAD_MINUTES" in (web / "mock/server.ts").read_text()


async def test_another_organization_can_not_cancel_or_reschedule_a_post(sign_in, no_publish):
    a, b = sign_in(org="A"), sign_in(org="B")
    job = await ready_job(a.org_id)
    await asyncio.to_thread(approve, a, job, scheduled_at=at(timedelta(days=2)))
    [p] = a.get(f"/posts?job_id={job.id}").json()
    assert b.post(f"/posts/{p['id']}/cancel").status_code == 404
    assert b.post(f"/posts/{p['id']}/reschedule", json={"scheduled_at": at(timedelta(days=4))}).status_code == 404
    assert b.get(f"/posts/{p['id']}").status_code == 404 and b.get("/posts?when=upcoming").json() == []
    mine = posts.get(a.org_id, p["id"])
    assert mine.state == "scheduled" and mine.scheduled_at == datetime.fromisoformat(p["scheduled_at"])


async def test_a_scheduled_post_publishes_through_instagram_at_its_time(sign_in, monkeypatch):
    """The graph path (Instagram faked as in test_posts.py): nothing is sent before the time; then exactly one post."""
    import httpx
    from factories import connect_account
    from test_posts import FakeInstagram, _ready

    from app import audit
    from app.config import settings
    from app.services import publisher
    fake = FakeInstagram()
    monkeypatch.setattr(publisher, "transport", httpx.MockTransport(fake.handler))
    monkeypatch.setattr(publisher, "sleep", lambda s: asyncio.sleep(0))
    monkeypatch.setattr(settings, "public_asset_base_url", "https://tunnel.example")
    monkeypatch.setattr(settings, "publish_mode", "graph")
    monkeypatch.setattr(settings, "demo_mode", "live")  # a real post needs a model-checked verdict (ADR 0004)
    a = sign_in(org="A")
    acct = fake.add(connect_account(a.org_id))
    job = _ready(a.org_id)
    by = {"id": str(a.user_id), "email": f"{a.user_id.hex[:8]}@example.com"}
    entry = audit.Entry(actor=a.user_id, actor_email=by["email"], action="deck.approved", target=job.id)
    when = datetime.now(KST) + timedelta(hours=2)
    p = await asyncio.to_thread(lambda: publishing.create(a.org_id, job, mode="graph", by=by, account=acct,
                                                          deck_version=1, audits=[entry], scheduled_at=when))
    early = await publishing.advance(str(a.org_id), str(p.id))
    assert early["state"] == "scheduled" and fake.calls == []  # nothing reaches Instagram before the time
    monkeypatch.setattr(publishing, "_now", lambda: p.scheduled_at + timedelta(seconds=5))
    assert await publishing.run(a.org_id, p.id) == "published"
    done = posts.get(a.org_id, p.id)
    assert done.state == "published" and len(fake.live) == 1 and fake.publish_calls == [done.creation_id]
    assert store.get_job(a.org_id, job.id).status == JobStatus.PUBLISHED
    with store.system() as c:
        actions = [r[0] for r in c.execute("select action from audit_events where target = %s order by id",
                                           (job.id,)).fetchall()]
    assert actions == ["deck.approved", "post.scheduled", "post.started", "post.published"]
