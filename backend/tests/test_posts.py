"""Posts with an honest state (#21; HARDENING PUB-1, PUB-3, PUB-4; docs/spec/v1.md "Publish state machine").

Instagram is faked with httpx.MockTransport (FakeInstagram below): nothing here reaches Meta. The fake keeps what a
real account would show, so every test can count the posts that actually went live:

- a rate limit (code 4) or a timeout on media_publish never becomes "failed": the Post goes to unknown, and the
  reconciler settles it from the container's status_code: live after all → published; not live → the same container
  is published again. Either way exactly one post exists;
- a clear rejection → failed, nothing posted, approve again makes a new Post;
- a restart while media_publish is in flight → unknown → reconciled, never a second media_publish blindly;
- double-clicked Approve, approving while a Post is open, and a second post of the same Deck to the same account are
  refused by the database;
- the account's own token (Vault) is used, and never lands in a Post, a DBOS checkpoint, the audit log or a log line.
"""

import asyncio
import json
import logging
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import psycopg
import pytest
from conftest import TEST_SERVER
from dbos import DBOS
from factories import allowed_job, connect_account
from psycopg.conninfo import make_conninfo
from test_durable_jobs import Crash, restart_dbos

from app import posts, seed, store
from app.config import settings
from app.pipeline import publishing
from app.schemas import JobStatus
from app.services import publisher

ROOT = Path(__file__).resolve().parents[2]


class FakeInstagram:
    """What Instagram's content publishing API does for our calls, per account (by token).

    `publish_plan`: what each media_publish call does, in order: "ok"; ("error", code, posted); ("timeout", posted);
    ("crash", posted) raises Crash (the process dies mid-call). `posted` says whether the post went live anyway.
    `status_plan`: answers to container status checks made by the reconciler: a status string, or ("error", code)."""

    def __init__(self):
        self.accounts: dict[str, str] = {}          # token → ig_user_id
        self.containers: dict[str, dict] = {}       # id → {"status", "caption", "user", "created"}
        self.live: list[dict] = []                   # posts that went public: {"id", "creation_id", "caption"}
        self.publish_plan: list = []
        self.status_plan: list = []
        self.calls: list[tuple[str, str]] = []       # (method, path)
        self.publish_calls: list[str] = []           # creation_ids sent to media_publish
        self.create_error: tuple | None = None       # ("error", code) on creating a slide container
        self.seen_tokens: set[str] = set()

    def add(self, account: dict) -> dict:
        self.accounts[account["token"]] = account["ig_user_id"]
        return account

    def _go_live(self, creation_id: str) -> str:
        media_id = "1790" + secrets.token_hex(6)
        c = self.containers[creation_id]
        c["status"] = "PUBLISHED"
        self.live.append({"id": media_id, "creation_id": creation_id, "caption": c["caption"], "user": c["user"],
                          "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S+0000")})
        return media_id

    def handler(self, request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        token = auth.removeprefix("Bearer ")
        self.seen_tokens.add(token)
        assert "access_token" not in str(request.url), "the token must travel in a header, never the URL"
        user = self.accounts.get(token)
        if user is None:
            return httpx.Response(400, json={"error": {"message": "Invalid OAuth access token", "code": 190}})
        path, q = request.url.path.split("/", 2)[2], request.url.params  # strip "/v24.0/"
        self.calls.append((request.method, path))
        parts = path.split("/")
        if request.method == "POST" and parts == [user, "media"]:
            if self.create_error:
                return httpx.Response(400, json={"error": {"message": "nope", "code": self.create_error[1]}})
            cid = "1789" + secrets.token_hex(6)
            self.containers[cid] = {"status": "FINISHED", "caption": q.get("caption"), "user": user,
                                    "carousel": q.get("media_type") == "CAROUSEL"}
            return httpx.Response(200, json={"id": cid})
        if request.method == "POST" and parts == [user, "media_publish"]:
            cid = q["creation_id"]
            self.publish_calls.append(cid)
            assert self.containers[cid]["carousel"] and self.containers[cid]["user"] == user
            if self.containers[cid]["status"] == "PUBLISHED":  # Meta: a container is published once
                return httpx.Response(400, json={"error": {"message": "already published", "code": 9004}})
            if self.containers[cid]["status"] == "EXPIRED":
                return httpx.Response(400, json={"error": {"message": "expired", "code": 9004,
                                                           "error_subcode": 2207008}})
            step = self.publish_plan.pop(0) if self.publish_plan else "ok"
            if step == "ok":
                return httpx.Response(200, json={"id": self._go_live(cid)})
            kind, *rest = step
            if kind == "error":
                code, posted = rest
                if posted:
                    self._go_live(cid)
                return httpx.Response(400 if code != 2 else 500, json={"error": {
                    "message": "Application request limit reached" if code == 4 else "refused", "code": code,
                    "type": "OAuthException"}})
            if kind == "timeout":
                if rest[0]:
                    self._go_live(cid)
                raise httpx.ReadTimeout("timed out", request=request)
            if kind == "crash":
                if rest[0]:
                    self._go_live(cid)
                raise Crash()
        if request.method == "GET" and parts == [user, "media"]:
            mine = [m for m in reversed(self.live) if m["user"] == user]
            return httpx.Response(200, json={"data": [
                {"id": m["id"], "caption": m["caption"], "timestamp": m["timestamp"],
                 "permalink": f"https://www.instagram.com/p/{m['id']}/"} for m in mine]})
        if request.method == "GET" and len(parts) == 1 and q.get("fields") == "status_code":
            if self.publish_calls and self.status_plan:  # the reconciler asking, after a publish attempt
                step = self.status_plan.pop(0)
                if isinstance(step, tuple):
                    return httpx.Response(400, json={"error": {"message": "limit", "code": step[1]}})
                return httpx.Response(200, json={"status_code": step, "id": parts[0]})
            return httpx.Response(200, json={"status_code": self.containers[parts[0]]["status"], "id": parts[0]})
        if request.method == "GET" and len(parts) == 1 and q.get("fields") == "permalink":
            return httpx.Response(200, json={"permalink": f"https://www.instagram.com/p/{parts[0]}/", "id": parts[0]})
        return httpx.Response(404, json={"error": {"message": f"unexpected {request.method} {path}", "code": 100}})


@pytest.fixture
def ig(monkeypatch):
    fake = FakeInstagram()
    monkeypatch.setattr(publisher, "transport", httpx.MockTransport(fake.handler))
    monkeypatch.setattr(publisher, "sleep", lambda s: asyncio.sleep(0))
    # The reconciler's backoff, made instant (the real one is 2, 4, 8, 15 minutes, then every 30).
    monkeypatch.setattr(publishing, "RECONCILE_DELAYS_S", [0, 0, 0, 0])
    monkeypatch.setattr(publishing, "RECONCILE_EVERY_S", 0)
    monkeypatch.setattr(publishing, "MOCK_STEP_S", 0)
    monkeypatch.setattr(settings, "public_asset_base_url", "https://tunnel.example")
    monkeypatch.setattr(settings, "publish_mode", "graph")
    monkeypatch.setattr(settings, "demo_mode", "live")  # a real post needs a model-checked verdict (ADR 0004)
    return fake


def _ready(org_id):
    """A Job ready for review with three rendered slides on disk and an allowed, model-checked verdict."""
    job = allowed_job()
    out = settings.output_dir / job.id
    out.mkdir(parents=True, exist_ok=True)
    for i in range(3):
        (out / f"slide-{i:02d}.jpg").write_bytes(b"\xff\xd8\xff\xe0 fake jpeg")
    from factories import allowed
    allowed(job)
    from app.services import safeguard
    job.safeguard.content_hash = safeguard.content_hash(job.deck, sorted(out.glob("slide-*.jpg")))
    return store.save_job(org_id, job)


async def _publish(org_id, job_id, account, **kw):
    return await publishing.publish_now(org_id, job_id, "graph", account=account, **kw)


def audit_actions(org_id, job_id) -> list[str]:
    with store.system() as c:
        return [r[0] for r in c.execute("select action from audit_events where org_id = %s and target = %s "
                                         "order by id", (org_id, job_id)).fetchall()]


async def wait_post(org_id, post_id, *, final=True, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        p = await asyncio.to_thread(posts.get, org_id, post_id)
        if p and (p.finished_at if final else p.state != "queued"):
            return p
        await asyncio.sleep(0.05)
    raise AssertionError(f"post {post_id} still {p.state if p else None}")


# ---------------------------------------------------------------- the happy path

async def test_a_post_goes_to_the_chosen_account_with_its_own_token(sign_in, ig):
    a = sign_in(org="A")
    acct = ig.add(connect_account(a.org_id))
    job = _ready(a.org_id)
    post = await _publish(a.org_id, job.id, acct)
    assert (post.state, post.handle, post.attempts) == ("published", "@seoulbrand", 1)
    assert post.permalink == f"https://www.instagram.com/p/{post.media_id}/" and post.published_at
    assert len(ig.live) == 1 and ig.live[0]["creation_id"] == post.creation_id
    assert ig.seen_tokens == {acct["token"]}  # the account's token, from Vault
    assert all(path.startswith((acct["ig_user_id"], "1789", "1790")) for _, path in ig.calls)
    saved = store.get_job(a.org_id, job.id)
    assert saved.status == JobStatus.PUBLISHED
    assert "published_url" not in saved.model_dump() and "publish_progress" not in saved.model_dump()
    assert audit_actions(a.org_id, job.id)[-2:] == ["deck.approved", "post.published"]


# ---------------------------------------------------------------- ambiguous answers → unknown → reconciler

async def test_rate_limit_on_publish_but_it_went_live_is_reconciled_to_published_once(sign_in, ig):
    """The 2026-10-07 incident: "Application request limit reached" on media_publish, yet the post was live."""
    a = sign_in(org="A")
    acct = ig.add(connect_account(a.org_id))
    job = _ready(a.org_id)
    ig.publish_plan = [("error", 4, True)]
    post = await _publish(a.org_id, job.id, acct)
    assert post.state == "published" and post.attempts == 1
    assert len(ig.publish_calls) == 1 and len(ig.live) == 1  # never published twice
    assert post.media_id == ig.live[0]["id"]  # found by the reconciler among the account's recent media
    assert post.unknown_since is not None and "request limit" in post.last_error
    assert audit_actions(a.org_id, job.id)[-3:] == ["deck.approved", "post.unknown", "post.settled"]
    assert store.get_job(a.org_id, job.id).status == JobStatus.PUBLISHED


@pytest.mark.parametrize("plan", [("error", 4, False), ("error", 17, False), ("error", 32, False),
                                  ("error", 613, False), ("error", 2, False), ("timeout", False)])
async def test_ambiguous_and_not_posted_is_retried_with_the_same_container(sign_in, ig, plan):
    a = sign_in(org="A")
    acct = ig.add(connect_account(a.org_id))
    job = _ready(a.org_id)
    ig.publish_plan = [plan]
    post = await _publish(a.org_id, job.id, acct)
    assert post.state == "published" and post.attempts == 2
    assert ig.publish_calls == [post.creation_id, post.creation_id]  # the same container both times
    assert sum(1 for m, p in ig.calls if m == "POST" and p.endswith("/media")) == 4  # 3 slides + carousel, once
    assert len(ig.live) == 1


async def test_a_timeout_after_instagram_posted_is_published_not_failed(sign_in, ig):
    a = sign_in(org="A")
    acct = ig.add(connect_account(a.org_id))
    job = _ready(a.org_id)
    ig.publish_plan = [("timeout", True)]
    post = await _publish(a.org_id, job.id, acct)
    assert post.state == "published" and len(ig.publish_calls) == 1 and len(ig.live) == 1


async def test_rate_limited_checks_keep_the_post_unknown_until_instagram_answers(sign_in, ig):
    a = sign_in(org="A")
    acct = ig.add(connect_account(a.org_id))
    job = _ready(a.org_id)
    ig.publish_plan = [("error", 4, True)]
    ig.status_plan = [("error", 4), ("error", 613), "PUBLISHED"]
    post = await _publish(a.org_id, job.id, acct)
    assert post.state == "published" and len(ig.live) == 1
    raw = posts.raw(a.org_id, post.id)
    assert raw["reconcile_attempts"] == 2


async def test_a_post_unknown_for_30_minutes_alerts_and_a_person_can_resolve_it(sign_in, ig, monkeypatch, caplog):
    a = sign_in(org="A")
    acct = ig.add(connect_account(a.org_id))
    job = _ready(a.org_id)
    ig.publish_plan = [("error", 4, False)]
    ig.status_plan = ["IN_PROGRESS"]
    monkeypatch.setattr(publishing, "CHECK_FOR", timedelta(0))  # stop checking at once: a person decides
    post = await _publish(a.org_id, job.id, acct)
    assert post.state == "unknown" and post.next_check_at is None and post.alerted_at is not None
    assert store.get_job(a.org_id, job.id).status == JobStatus.PUBLISHING  # Approve stays locked
    assert a.post(f"/jobs/{job.id}/approve", json={"deck_version": 1}).status_code == 409
    assert "post.unknown_alert" in audit_actions(a.org_id, job.id)
    # The person checked Instagram: not posted. Logged under their name; approving again is possible.
    r = a.post(f"/posts/{post.id}/resolve", json={"posted": False})
    assert r.status_code == 200 and r.json()["state"] == "failed" and r.json()["resolved_by"] == str(a.user_id)
    assert store.get_job(a.org_id, job.id).status == JobStatus.READY_FOR_REVIEW
    assert audit_actions(a.org_id, job.id)[-1] == "post.resolved"
    assert a.post(f"/posts/{post.id}/resolve", json={"posted": True}).status_code == 409  # settled already


async def test_not_posted_cant_be_claimed_while_the_reconciler_is_still_checking(sign_in, ig):
    a = sign_in(org="A")
    acct = connect_account(a.org_id)
    job = _ready(a.org_id)
    with store.tenant(a.org_id, user_id=a.user_id) as c:
        pid = posts.insert(c, job_id=job.id, mode="graph", approved_by=a.user_id, approved_by_email="", account_id=acct["id"],
                           ig_user_id=acct["ig_user_id"], username="seoulbrand").id
        c.execute("update posts set state = 'unknown', creation_id = '1', unknown_since = now(), "
                  "next_check_at = now() + interval '2 minutes' where id = %s", (pid,))
    r = a.post(f"/posts/{pid}/resolve", json={"posted": False})
    assert r.status_code == 409 and "30 minutes" in r.json()["detail"]
    r = a.post(f"/posts/{pid}/resolve", json={"posted": True, "permalink": "https://www.instagram.com/p/abc/"})
    assert r.status_code == 200 and (r.json()["state"], r.json()["permalink"]) == \
        ("published", "https://www.instagram.com/p/abc/")
    assert store.get_job(a.org_id, job.id).status == JobStatus.PUBLISHED
    v = sign_in(org=None)
    assert v.post(f"/posts/{pid}/resolve", json={"posted": True}).status_code in (403, 404)


# ---------------------------------------------------------------- clear rejections → failed

@pytest.mark.parametrize("code", [100, 9004, 10])
async def test_a_clear_rejection_fails_honestly_and_approve_works_again(sign_in, ig, code):
    a = sign_in(org="A")
    acct = ig.add(connect_account(a.org_id))
    job = _ready(a.org_id)
    ig.publish_plan = [("error", code, False)]
    post = await _publish(a.org_id, job.id, acct)
    assert post.state == "failed" and ig.live == [] and f"code {code}" in post.last_error
    assert post.progress.label.endswith("nothing was posted")
    assert store.get_job(a.org_id, job.id).status == JobStatus.READY_FOR_REVIEW
    again = await _publish(a.org_id, job.id, acct)  # a new Post, new containers
    assert again.state == "published" and again.creation_id != post.creation_id and len(ig.live) == 1


async def test_a_revoked_token_fails_before_anything_is_posted_and_marks_the_account(sign_in, ig):
    a = sign_in(org="A")
    acct = connect_account(a.org_id)  # Instagram doesn't know this token: it was revoked
    job = _ready(a.org_id)
    post = await _publish(a.org_id, job.id, acct)
    assert post.state == "failed" and "code 190" in post.last_error and ig.live == []
    with store.system() as c:
        assert c.execute("select status from instagram_accounts where id = %s", (acct["id"],)).fetchone()[0] == "revoked"
    # A revoked account can't be picked any more until it is reconnected, and Approve says so (#22).
    r = a.post(f"/jobs/{job.id}/approve", json={"deck_version": 1, "account_id": str(acct["id"])})
    assert r.status_code == 409 and "needs reconnecting" in r.json()["detail"]


# ---------------------------------------------------------------- 24 h container expiry

async def test_a_container_older_than_a_day_is_rebuilt_not_published(sign_in, ig):
    a = sign_in(org="A")
    acct = ig.add(connect_account(a.org_id))
    job = _ready(a.org_id)
    post = await publishing.publish_now(a.org_id, job.id, "dryrun", account=acct)  # containers only
    old = post.creation_id
    ig.containers[old]["status"] = "EXPIRED"
    # Pretend a real Post sat at container_ready for 25 hours (e.g. scheduled, #26).
    with store.tenant(a.org_id, user_id=a.user_id) as c:
        pid = posts.insert(c, job_id=job.id, mode="graph", approved_by=a.user_id, approved_by_email="",
                           account_id=acct["id"], ig_user_id=acct["ig_user_id"], username="seoulbrand").id
        c.execute("update posts set state = 'container_ready', creation_id = %s, "
                  "container_created_at = now() - interval '25 hours' where id = %s", (old, pid))
    await publishing.run(a.org_id, pid)
    done = posts.get(a.org_id, pid)
    assert done.state == "published" and done.creation_id != old and old not in ig.publish_calls
    assert len(ig.live) == 1


async def test_an_unknown_post_whose_container_expired_failed(sign_in, ig):
    a = sign_in(org="A")
    acct = ig.add(connect_account(a.org_id))
    job = _ready(a.org_id)
    ig.publish_plan = [("error", 4, False)]
    ig.status_plan = ["EXPIRED"]
    post = await _publish(a.org_id, job.id, acct)
    assert post.state == "failed" and "never published" in post.last_error and ig.live == []


async def test_the_retries_stop_after_three_attempts_with_nothing_posted(sign_in, ig):
    a = sign_in(org="A")
    acct = ig.add(connect_account(a.org_id))
    job = _ready(a.org_id)
    ig.publish_plan = [("error", 4, False)] * 3
    post = await _publish(a.org_id, job.id, acct)
    assert post.state == "failed" and post.attempts == 3 and ig.live == []
    assert len(set(ig.publish_calls)) == 1  # always the same container


# ---------------------------------------------------------------- restarts (DBOS)

async def test_a_restart_while_media_publish_is_in_flight_reconciles_instead_of_failing(sign_in, ig):
    a = sign_in(org="A")
    acct = ig.add(connect_account(a.org_id))
    job = _ready(a.org_id)
    ig.publish_plan = [("crash", True)]  # Instagram posts it; our process dies before the answer arrives
    by = {"id": str(a.user_id), "email": ""}
    post = await asyncio.to_thread(lambda: publishing.create(
        a.org_id, store.get_job(a.org_id, job.id), mode="graph", by=by, account=acct))
    with pytest.raises(Crash):
        await publishing.run(a.org_id, post.id)
    assert posts.get(a.org_id, post.id).state == "publishing"
    st = await asyncio.to_thread(DBOS.get_workflow_status, publishing.workflow_id(post.id))
    assert st.status == "PENDING"
    # Startup recovery leaves a Post whose workflow DBOS will resume alone...
    from app.main import recover_interrupted
    await asyncio.to_thread(recover_interrupted)
    assert posts.raw(a.org_id, post.id)["runs"] == 1
    # ...and "restarting" resumes it: publishing → unknown → the reconciler finds it live.
    restart_dbos()
    done = await wait_post(a.org_id, post.id)
    assert done.state == "published" and len(ig.publish_calls) == 1 and len(ig.live) == 1
    assert done.media_id == ig.live[0]["id"]
    assert store.get_job(a.org_id, job.id).status == JobStatus.PUBLISHED


async def test_a_restart_before_anything_was_posted_publishes_once(sign_in, ig):
    a = sign_in(org="A")
    acct = ig.add(connect_account(a.org_id))
    job = _ready(a.org_id)
    ig.publish_plan = [("crash", False)]
    by = {"id": str(a.user_id), "email": ""}
    post = await asyncio.to_thread(lambda: publishing.create(
        a.org_id, store.get_job(a.org_id, job.id), mode="graph", by=by, account=acct))
    with pytest.raises(Crash):
        await publishing.run(a.org_id, post.id)
    restart_dbos()
    done = await wait_post(a.org_id, post.id)
    assert done.state == "published" and len(ig.live) == 1
    assert ig.publish_calls == [done.creation_id, done.creation_id]  # checked, then retried, same container


async def test_a_post_without_a_live_workflow_is_driven_again_after_a_restart(sign_in, ig):
    """A crash between saving the Post and queueing it, or a workflow from another WORKFLOW_VERSION."""
    a = sign_in(org="A")
    acct = ig.add(connect_account(a.org_id))
    job = _ready(a.org_id)
    by = {"id": str(a.user_id), "email": ""}
    post = await asyncio.to_thread(lambda: publishing.create(
        a.org_id, store.get_job(a.org_id, job.id), mode="graph", by=by, account=acct))
    from app.main import recover_interrupted
    await asyncio.to_thread(recover_interrupted)
    done = await wait_post(a.org_id, post.id)
    assert done.state == "published" and len(ig.live) == 1
    assert posts.raw(a.org_id, post.id)["runs"] == 2
    st = await asyncio.to_thread(DBOS.get_workflow_status, publishing.workflow_id(post.id, 2))
    assert st.status in ("SUCCESS", "PENDING")


# ---------------------------------------------------------------- approve: accounts, double clicks, duplicates

def test_the_approver_picks_one_of_the_organizations_accounts(sign_in, ig, monkeypatch):
    started = []

    async def fake_start(org_id, post_id, runs=1):
        started.append(post_id)
    monkeypatch.setattr(publishing, "start", fake_start)
    a, b = sign_in(org="A"), sign_in(org="B")
    job = _ready(a.org_id)
    # No account: a dry run or real post is refused; a mock post may go without.
    assert a.post(f"/jobs/{job.id}/approve", json={"deck_version": 1}).status_code == 422
    one = connect_account(a.org_id, "first")
    two = connect_account(a.org_id, "second")
    theirs = connect_account(b.org_id, "theirs")
    r = a.post(f"/jobs/{job.id}/approve", json={"deck_version": 1})
    assert r.status_code == 422 and "choose one" in r.json()["detail"]
    r = a.post(f"/jobs/{job.id}/approve", json={"deck_version": 1, "account_id": str(theirs["id"])})  # another Organization's
    assert r.status_code == 422
    r = a.post(f"/jobs/{job.id}/approve", json={"deck_version": 1, "account_id": str(two["id"])})
    assert r.status_code == 200 and r.json()["status"] == "PUBLISHING"
    [post] = a.get(f"/posts?job_id={job.id}").json()
    assert (post["handle"], post["instagram_account_id"], post["state"]) == ("@second", str(two["id"]), "queued")
    assert started == [__import__("uuid").UUID(post["id"])]
    assert b.get(f"/posts/{post['id']}").status_code == 404 and b.get(f"/posts?job_id={job.id}").json() == []
    # One account: preselected.
    with store.system() as c:
        c.execute("update instagram_accounts set status = 'disconnected' where id = %s", (one["id"],))
    job2 = _ready(a.org_id)
    assert a.post(f"/jobs/{job2.id}/approve", json={"deck_version": 1}).status_code == 200
    assert a.get(f"/posts?job_id={job2.id}").json()[0]["handle"] == "@second"


def test_mock_posts_need_no_account(client, monkeypatch, ig):
    monkeypatch.setattr(settings, "publish_mode", "mock")
    job = _ready(client.org_id)
    assert client.post(f"/jobs/{job.id}/approve", json={"deck_version": 1}).status_code == 200
    post = asyncio.run(wait_post(client.org_id, client.get(f"/posts?job_id={job.id}").json()[0]["id"]))
    assert (post.state, post.mode, post.handle) == ("published", "mock", None)
    assert ig.calls == []


def test_a_double_clicked_approve_makes_one_post(sign_in, ig, monkeypatch):
    async def hold(org_id, post_id, runs=1):  # the Post stays open, as it would mid-publish
        pass
    monkeypatch.setattr(publishing, "start", hold)
    a = sign_in(org="A")
    connect_account(a.org_id)
    job = _ready(a.org_id)
    with ThreadPoolExecutor(4) as pool:
        codes = sorted(pool.map(lambda _: a.post(f"/jobs/{job.id}/approve", json={"deck_version": 1}).status_code, range(4)))
    assert codes == [200, 409, 409, 409]
    assert len(a.get(f"/posts?job_id={job.id}").json()) == 1


def test_the_database_refuses_a_second_open_post_and_a_second_live_post(sign_in):
    a = sign_in(org="A")
    acct = connect_account(a.org_id)
    job = _ready(a.org_id)
    kw = {"job_id": job.id, "mode": "graph", "approved_by": a.user_id, "approved_by_email": "", "account_id": acct["id"],
              "ig_user_id": acct["ig_user_id"], "username": "seoulbrand", "caption": None}
    with store.tenant(a.org_id, user_id=a.user_id) as c:
        first = posts.insert(c, **kw)
        with pytest.raises(posts.Taken) as t:
            posts.insert(c, **kw)
        assert t.value.open
        c.execute("update posts set state = 'published', finished_at = now() where id = %s", (first.id,))
        with pytest.raises(posts.Taken) as t:  # same Deck version, same account: never twice
            posts.insert(c, **kw)
        assert not t.value.open
        posts.insert(c, **{**kw, "mode": "dryrun"})  # a dry run of it is fine
    # A different Deck version (#19) of the same Job may be posted to the same account.
    with store.tenant(a.org_id, user_id=a.user_id) as c:
        c.execute("update posts set finished_at = now() where job_id = %s and finished_at is null", (job.id,))
        posts.insert(c, **{**kw, "deck_version": 2})


async def test_approve_after_published_is_refused(sign_in, ig):
    a = sign_in(org="A")
    acct = ig.add(connect_account(a.org_id))
    job = _ready(a.org_id)
    await _publish(a.org_id, job.id, acct)
    r = a.post(f"/jobs/{job.id}/approve", json={"deck_version": 1})
    assert r.status_code == 409 and len(ig.live) == 1


# ---------------------------------------------------------------- the token stays put

async def test_the_token_is_nowhere_but_vault(sign_in, ig, caplog):
    caplog.set_level(logging.DEBUG)
    a = sign_in(org="A")
    acct = ig.add(connect_account(a.org_id))
    job = _ready(a.org_id)
    ig.publish_plan = [("error", 4, True)]
    post = await _publish(a.org_id, job.id, acct)
    token = acct["token"]
    with store.system() as c:
        rows = [r[0] for r in c.execute("select t::text from posts t where id = %s", (post.id,)).fetchall()]
        rows += [r[0] for r in c.execute("select t::text from audit_events t where org_id = %s", (a.org_id,))]
        rows += [r[0] for r in c.execute("select t::text from dbos.workflow_status t where workflow_uuid like %s",
                                         (f"post-{post.id}%",))]
        rows += [r[0] for r in c.execute("select t::text from dbos.operation_outputs t where workflow_uuid like %s",
                                         (f"post-{post.id}%",))]
        rows += [r[0] for r in c.execute("select doc::text from jobs where id = %s", (job.id,))]
    assert rows and all(token not in r for r in rows)
    assert token not in caplog.text and token not in json.dumps(a.get(f"/posts/{post.id}").json())


# ---------------------------------------------------------------- dashboard reads the Organization's account

def test_dashboard_numbers_come_from_the_organizations_own_account(sign_in, monkeypatch):
    seen = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["authorization"])
        if request.url.path.endswith("/me"):
            return httpx.Response(200, json={"username": "seoulbrand", "followers_count": 1234, "media_count": 9})
        return httpx.Response(400, json={"error": {"message": "no insights", "code": 10}})
    monkeypatch.setattr(seed, "transport", httpx.MockTransport(answer))
    seed._cache.clear()
    a, b = sign_in(org="A"), sign_in(org="B")
    acct = connect_account(a.org_id)
    m = {x["label"]: x for x in a.get("/metrics").json()}
    assert m["Followers"]["value"] == "1,234" and "@seoulbrand" in m["Followers"]["delta"]
    assert set(seen) == {f"Bearer {acct['token']}"}
    assert {x["label"]: x for x in b.get("/metrics").json()}["Followers"]["delta"] == "connect an Instagram account"


# ---------------------------------------------------------------- the migration moves Job publish fields to Posts

def test_the_migration_moves_published_and_interrupted_jobs_to_posts():
    name = f"wok_mig_{secrets.token_hex(4)}"
    with psycopg.connect(TEST_SERVER, autocommit=True) as c:
        c.execute(f"create database {name}")
    try:
        files = sorted((ROOT / "supabase/migrations").glob("*.sql"))
        posts_sql = next(f for f in files if f.name.endswith("_posts.sql"))
        with psycopg.connect(make_conninfo(TEST_SERVER, dbname=name)) as c:
            for f in files:
                if f.name >= posts_sql.name:
                    break
                c.execute(f.read_text())
            org = c.execute("insert into orgs (name, created_by) values ('Old', gen_random_uuid()) returning id"
                            ).fetchone()[0]
            docs = {
                "live1": {"id": "live1", "status": "PUBLISHED", "published_url": "https://www.instagram.com/p/X/",
                          "publish_progress": {"mode": "graph", "step": "done", "label": "Published",
                                               "started_at": "2026-10-07T01:00:00Z",
                                               "finished_at": "2026-10-07T01:01:00Z"}},
                "mock1": {"id": "mock1", "status": "PUBLISHED", "published_url": "https://www.instagram.com/p/MOCK1/"},
                "mid1": {"id": "mid1", "status": "PUBLISHING",
                         "publish_progress": {"mode": "graph", "step": "publish", "label": "…",
                                              "started_at": "2026-10-07T02:00:00Z"}},
                "ready1": {"id": "ready1", "status": "READY_FOR_REVIEW"},
            }
            for jid, doc in docs.items():
                c.execute("insert into jobs (id, created_at, doc, org_id) values (%s, now(), %s, %s)",
                          (jid, json.dumps(doc), org))
            c.execute(posts_sql.read_text())
            got = {r[0]: r[1:] for r in c.execute(
                "select job_id, mode, state, permalink, finished_at is not null, legacy from posts").fetchall()}
            assert got == {"live1": ("graph", "published", "https://www.instagram.com/p/X/", True, True),
                           "mock1": ("mock", "published", "https://www.instagram.com/p/MOCK1/", True, True),
                           "mid1": ("graph", "unknown", None, False, True)}
            left = c.execute("select count(*) from jobs where doc ? 'published_url' or doc ? 'publish_progress'"
                             ).fetchone()[0]
            assert left == 0
    finally:
        with psycopg.connect(TEST_SERVER, autocommit=True) as c:
            c.execute(f"drop database if exists {name} with (force)")
