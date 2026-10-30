"""The publisher (#21; docs/spec/v1.md "Publish state machine" and pseudocode 4; HARDENING PUB-1): each Post runs as a
durable DBOS workflow (ADR 0002) that moves it through

    [scheduled →] queued → container_ready → publishing → published | failed | unknown,   unknown → reconciler

A scheduled Post (#26) waits in `scheduled`: the workflow sleeps durably (DBOS) until `scheduled_at`, then moves it
to `queued` and carries on as below. Rescheduling restarts the workflow so it wakes at the new time; cancelling or an
edit (→ `on_hold`) ends it. A Post whose time passed more than MISSED_BY ago (the server was down) is held, not
posted late.

The `posts` row is the source of truth; the workflow only drives it. Its one step, `advance`, reads the Post's
state and makes exactly one transition, each saved *before* the Instagram call it guards. So whatever point a
process dies at, re-running the step (DBOS recovery after a restart, or a fresh workflow) does the right thing:

  queued           Safeguard re-check of exactly what will be posted (ADR 0004) → upload the slides on links that
                   expire (#18) → child containers → carousel container FINISHED → save its creation_id.
                   Cut off here: the step runs again and builds new containers. Unpublished containers are harmless
                   (they expire after 24 h), and nothing has been posted.
  container_ready  Save `publishing` (and the attempt), then call media_publish with the saved creation_id.
                   A media id → published. A clear rejection → failed (nothing posted). Anything else (rate limit
                   codes 4/17/32/613, timeouts, 5xx, a dropped connection) → unknown.
  publishing       Found on entry = the process died while media_publish was in flight → unknown, never failed.
  unknown          The reconciler asks Instagram for the container's status_code, with backoff:
                   PUBLISHED → published (the post went live after all; its media is looked up by time and caption);
                   FINISHED → not posted: container_ready again, and the same container is retried;
                   EXPIRED / ERROR → failed. After 30 minutes a person is alerted; they can resolve it by hand
                   (POST /posts/{id}/resolve). Checks stop once the container would have expired (25 h).

A container can be published once, so retrying with the saved creation_id can only finish the same post, never
make a second one. Approving again while a Post is open, or after it is live on that account, is refused by the
database (unique indexes in supabase/migrations/*_posts.sql).

The token is read from Vault inside each step (instagram_accounts.access_token, privileged) and never enters a
workflow input, a step output, a log line or the sandbox. Workflow inputs are only the Organization and the Post id.
"""

import asyncio
import logging
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from dbos import DBOS, SetWorkflowID, WorkflowSerializationFormat

from app import assets, audit, deck_versions, instagram_accounts, posts, store
from app.config import settings
from app.pipeline import durable, orchestrator
from app.schemas import Job, JobStatus, LogLine, Post, PostProgress
from app.services import image_host, publisher, visual_qa
from app.services.publisher import GraphError, Instagram

log = logging.getLogger("publishing")
KST = ZoneInfo("Asia/Seoul")

POST_QUEUE = "posts"
MAX_PUBLISH_ATTEMPTS = 3                  # media_publish calls per Post, all with the same container
CONTAINER_MAX_AGE = timedelta(hours=23)   # Instagram expires containers after 24 h; rebuild before that
ALERT_AFTER = timedelta(minutes=30)       # unknown this long: tell a person
CHECK_FOR = timedelta(hours=25)           # after this an unpublished container is EXPIRED: stop checking
RECONCILE_DELAYS_S = [120, 240, 480, 900]  # then every 30 minutes
RECONCILE_EVERY_S = 1800
MOCK_STEP_S = 0.15                        # mock publish: how long each simulated sub-step shows
MISSED_BY = timedelta(hours=6)            # a scheduled Post this late (server was down) is held, not posted late

# Progress bands per sub-step (percent of the bar), so the Admin shows steady, honest progress.
BANDS = {"upload": (0, 30), "containers": (30, 60), "processing": (60, 85), "publish": (85, 99)}
LABELS = {"upload": "Uploading slides on private links", "containers": "Sending slides to Instagram",
          "processing": "Instagram is processing the images", "publish": "Publishing"}


def reconcile_delay(n: int) -> float:
    return RECONCILE_DELAYS_S[n] if n < len(RECONCILE_DELAYS_S) else RECONCILE_EVERY_S


def workflow_id(post_id: UUID | str, runs: int = 1) -> str:
    """One workflow per Post; a restart that has to drive it again uses a fresh id (`runs`)."""
    return f"post-{post_id}" if runs <= 1 else f"post-{post_id}-r{runs}"


def register_queue() -> None:
    """No per-Organization cap: a Post waiting for the reconciler sleeps in its workflow, and must never hold up
    another Organization's (or its own next) Post."""
    DBOS.register_queue(POST_QUEUE, worker_concurrency=settings.posts_per_worker,
                        polling_interval_sec=durable.QUEUE_POLL_S, on_conflict="always_update")


def _now() -> datetime:
    return datetime.now(UTC)


def _progress(step: str, done: int = 0, total: int = 0, label: str | None = None) -> PostProgress:
    lo, hi = BANDS.get(step, (100, 100))
    pct = 100 if step in ("done", "failed") else int(lo + (hi - lo) * (done / total if total else 0))
    text = label or (f"{LABELS[step]} ({done}/{total})" if total > 1 else LABELS.get(step, step))
    return PostProgress(step=step, label=text, done=done, total=total, percent=pct)


def _job_log(message: str, level: str = "info", step: tuple[str, str | None] | None = None):
    """A job_update for `posts.transition`: a line in the Job's log and, optionally, the "Human publish" step."""
    def update(job: Job) -> None:
        job.log.append(LogLine(time=datetime.now(KST).strftime("%H:%M:%S"), stage="publish", message=message,
                               level=level))
        if step:
            for s in job.pipeline:
                if s.name == orchestrator.STEPS["publish"][0]:
                    s.state = step[0]
                    if step[1] is not None:
                        s.note = step[1]
    return update


def _by(p: dict) -> dict:
    return {"id": str(p["approved_by"]), "email": p.get("approved_by_email") or ""}


def _token(org_id: UUID, p: dict) -> str | None:
    """The account's token from Vault, on the privileged path; None if disconnected, revoked or another
    Organization's."""
    if not p.get("instagram_account_id"):
        return None
    with store.system() as c:
        return instagram_accounts.access_token(c, org_id, p["instagram_account_id"])


def _revoke(org_id: UUID, p: dict, e: GraphError) -> None:
    """Instagram says the token is invalid (code 190): the account needs reconnecting (#22 refreshes in time)."""
    if not e.token_invalid or not p.get("instagram_account_id"):
        return
    with store.system() as c:
        c.execute("update instagram_accounts set status = 'revoked' where id = %s and org_id = %s "
                  "and status in ('connected', 'expiring')", (p["instagram_account_id"], org_id))
    log.warning("Instagram rejected the token of account %s (Organization %s): marked revoked",
                p["instagram_account_id"], org_id)


def _out(post: Post | None, *, wait: float = 0) -> dict:
    """What a step returns (checkpointed by DBOS: small and token-free)."""
    if post is None:
        return {"state": "gone", "done": True, "wait": 0}
    person = (post.state == "unknown" and post.next_check_at is None) or post.state == "on_hold"
    return {"state": post.state, "done": bool(post.finished_at) or person, "wait": wait}


def _fail(org_id: UUID, p: dict, error: str, *, expect: set[str], job_status: JobStatus | None = None,
          label: str = "Not posted", ui_step: bool = True) -> Post | None:
    """`ui_step=False`: the Job's "Human publish" step was already set (the Safeguard stop says why)."""
    error = error[:500]
    return posts.transition(
        org_id, p["id"], expect, state="failed", finished_at=_now(), last_error=error, next_check_at=None,
        progress=_progress("failed", label=f"{label}: nothing was posted"), job_status=job_status,
        job_update=_job_log(f"not posted: {error}", "warn",
                            ("pending", "Not posted · approve again to retry") if ui_step else None),
        audit_action="post.failed", audit_detail={"error": error})


# ---------------------------------------------------------------- the workflow

@DBOS.workflow(name="wok.post", max_recovery_attempts=20, serialization_type=WorkflowSerializationFormat.PORTABLE)
async def post_workflow(org: str, post_id: str) -> str:
    """Drive one Post until it is final or waits for a person. Deterministic: the loop only follows step outputs."""
    while True:
        out = await DBOS.run_step_async({"name": "advance"}, advance, org, post_id)
        if out["done"]:
            return out["state"]
        await DBOS.sleep_async(out["wait"])


async def advance(org: str, post_id: str) -> dict:
    """One transition of the Post, chosen by its saved state (see the module docstring)."""
    org_id, pid = UUID(org), UUID(post_id)
    p = await asyncio.to_thread(posts.raw, org_id, pid)
    if not p or p["finished_at"]:
        return {"state": p["state"] if p else "gone", "done": True, "wait": 0}
    state = p["state"]
    if state == "on_hold":
        return {"state": state, "done": True, "wait": 0}  # a person acts (approve again, reschedule or cancel)
    if state == "scheduled":
        return await asyncio.to_thread(_due, org_id, p)
    if state == "queued":
        return await _prepare(org_id, p)
    if state == "container_ready":
        return await _publish(org_id, p)
    if state == "publishing":
        # The process died while media_publish was in flight: it may have gone live. Never "failed".
        return await asyncio.to_thread(_to_unknown, org_id, p, "The server restarted while Instagram was "
                                       "publishing; checking whether it went live")
    return await _reconcile(org_id, p)


def _due(org_id: UUID, p: dict) -> dict:
    """A scheduled Post: sleep until its time (durably), then start publishing. Too late (the server was down):
    hold it for a person rather than post it hours after the time they chose."""
    now = _now()
    when = p["scheduled_at"]
    if when > now:
        return {"state": "scheduled", "done": False, "wait": (when - now).total_seconds()}
    if now - when > MISSED_BY:
        late = f"it was due at {posts.kst(when)}, but the server was down then; approve the Deck again to post it"
        return _out(posts.transition(
            org_id, p["id"], {"scheduled"}, state="on_hold", held_at=now, held_reason=late,
            progress=_progress("upload", label="On hold: missed its time"),
            job_update=_job_log(f"on hold: {late}", "warn", ("pending", "On hold · missed its time")),
            audit_action="post.held", audit_detail={"reason": late}))
    return _out(posts.transition(
        org_id, p["id"], {"scheduled"}, state="queued", progress=_progress("upload", label="Starting"),
        job_update=_job_log(f"scheduled time {posts.kst(when)} reached → publishing", "info",
                            ("running", "publishing")),
        audit_action="post.started", audit_detail={"scheduled_at": when.isoformat()}))


def _to_unknown(org_id: UUID, p: dict, error: str) -> dict:
    now = _now()
    wait = reconcile_delay(0)
    post = posts.transition(
        org_id, p["id"], {"publishing"}, state="unknown", unknown_since=now, last_error=error[:500],
        next_check_at=now + timedelta(seconds=wait), progress=_progress("publish", label="Checking Instagram…"),
        job_update=_job_log(f"outcome unknown ({error[:160]}) → checking Instagram before anything is retried",
                            "warn", ("running", "Checking Instagram…")),
        audit_action="post.unknown", audit_detail={"error": error[:500]})
    log.warning("post %s: outcome unknown (%s); reconciling", p["id"], error[:200])
    return _out(post, wait=wait)


# ---------------------------------------------------------------- queued → container_ready

async def _prepare(org_id: UUID, p: dict) -> dict:
    job = await asyncio.to_thread(store.get_job, org_id, p["job_id"])
    if not job or not job.deck:
        return _out(await asyncio.to_thread(_fail, org_id, p, "the Deck is gone", expect={"queued"}))
    mode = p["mode"]
    pinned = p.get("deck_version")
    if pinned is not None and (job.deck_version != pinned or job.safeguard_pending):
        # Edited after approval (#19): what was approved is not what would be posted. Nothing was uploaded.
        return _out(await asyncio.to_thread(
            _fail, org_id, p, f"version {pinned} was approved, but the Deck is at version {job.deck_version} now "
                              "(edited after approval); review it and approve again", expect={"queued"},
            job_status=JobStatus.READY_FOR_REVIEW, label="Edited after approval"))
    paths = assets.slide_files(job)  # the approved version's own files (#19)
    r = orchestrator.Run(org_id, job)
    ok, why = await orchestrator.safeguard_before_publish(r, mode, paths, _by(p))
    if ok:
        n_block = sum(i.severity == "block" for i in job.issues)
        r.step("publish", "running", f"publishing ({mode})")
        r.log("publish", f"approved → Post to {('@' + p['username']) if p['username'] else 'no account'} "
              f"(mode: {mode})" + (f" · operator overrode {n_block} blocking issue(s)" if n_block else ""))
    await r.flush()  # the Job (Deck, verdict, log) is saved before the Post moves; nothing below re-saves `r`
    if not ok:
        return _out(await asyncio.to_thread(_fail, org_id, p, why or "blocked", expect={"queued"},
                                            job_status=job.status, label="Stopped by the safeguard",
                                            ui_step=False))
    deck = job.deck
    content = job.safeguard.content_hash if job.safeguard else None

    def progress(step: str, done: int, total: int) -> None:
        posts.transition(org_id, p["id"], {"queued"}, progress=_progress(step, done, total))

    if mode == "mock":
        for step, n in (("upload", len(paths)), ("containers", len(paths)), ("processing", 1)):
            for i in range(1, n + 1):
                await asyncio.to_thread(progress, step, i, n)
                await asyncio.sleep(MOCK_STEP_S)
        creation_id = f"mock-{secrets.token_hex(6)}"
    else:
        token = await asyncio.to_thread(_token, org_id, p)
        if not token:
            return _out(await asyncio.to_thread(
                _fail, org_id, p, f"the Instagram account @{p['username']} is disconnected or needs reconnecting",
                expect={"queued"}))
        if visual_qa.scan_text(deck.caption, "caption", secrets=(token,)) or any(
                visual_qa.scan_text(f"{s.heading}\n{s.body}", "slide", secrets=(token,)) for s in deck.slides):
            return _out(await asyncio.to_thread(_fail, org_id, p, "the Deck contains a credential-like string",
                                                expect={"queued"}))
        try:
            async with image_host.exposed(org_id, job.id, paths,
                                          lambda d, t: progress("upload", d, t)) as urls, \
                    Instagram(p["ig_user_id"], token) as ig:
                creation_id = await publisher.build_carousel(
                    ig, urls, deck.caption, on_progress=lambda s, d, t: progress(s, d, t))
        except GraphError as e:
            await asyncio.to_thread(_revoke, org_id, p, e)
            return _out(await asyncio.to_thread(_fail, org_id, p, str(e), expect={"queued"},
                                                label="Instagram refused the slides"))
        except Exception as e:  # noqa: BLE001 — upload or container errors: nothing is public yet
            return _out(await asyncio.to_thread(_fail, org_id, p, f"{type(e).__name__}: {e}", expect={"queued"}))
    now = _now()
    if mode == "dryrun":
        post = await asyncio.to_thread(lambda: posts.transition(
            org_id, p["id"], {"queued"}, state="container_ready", creation_id=creation_id, container_created_at=now,
            content_hash=content, finished_at=now,
            progress=_progress("done", label="Dry run complete: carousel ready on Instagram, nothing posted"),
            job_status=JobStatus.READY_FOR_REVIEW,
            job_update=_job_log(f"dry run: carousel container {creation_id} FINISHED · media_publish skipped "
                                "(nothing posted)", step=("pending", "dry run OK · approve with graph mode to post"))))
        return _out(post)
    post = await asyncio.to_thread(lambda: posts.transition(
        org_id, p["id"], {"queued"}, state="container_ready", creation_id=creation_id, container_created_at=now,
        content_hash=content, progress=_progress("publish", 0, 1, label="Carousel ready · publishing")))
    return _out(post)


# ---------------------------------------------------------------- container_ready → publishing → …

async def _publish(org_id: UUID, p: dict) -> dict:
    now = _now()
    if p["mode"] != "mock" and p["container_created_at"] and now - p["container_created_at"] > CONTAINER_MAX_AGE:
        # Never published (we are about to publish it), so building a fresh one can't double-post.
        post = await asyncio.to_thread(lambda: posts.transition(
            org_id, p["id"], {"container_ready"}, state="queued", creation_id=None, container_created_at=None,
            progress=_progress("upload", label="The carousel container expired; preparing a fresh one"),
            job_update=_job_log("carousel container older than 23 h → building a fresh one")))
        return _out(post)
    token = None
    if p["mode"] != "mock":
        token = await asyncio.to_thread(_token, org_id, p)
        if not token:
            return _out(await asyncio.to_thread(
                _fail, org_id, p, f"the Instagram account @{p['username']} is disconnected or needs reconnecting",
                expect={"container_ready"}))
    # Saved before the call: if we die during it, the restart finds `publishing` and checks instead of retrying.
    started = await asyncio.to_thread(lambda: posts.transition(
        org_id, p["id"], {"container_ready"}, state="publishing", attempts=p["attempts"] + 1,
        publishing_started_at=p["publishing_started_at"] or now,
        progress=_progress("publish", 0, 1, label="Publishing to Instagram")))
    if started is None:
        return _out(await asyncio.to_thread(posts.get, org_id, p["id"]))
    if p["mode"] == "mock":
        return _out(await asyncio.to_thread(
            _published, org_id, p, "MOCK", f"https://www.instagram.com/p/MOCK{p['job_id'].upper()}/", {"publishing"},
            "mock publish: no network call"))
    try:
        async with Instagram(p["ig_user_id"], token) as ig:
            media_id = await ig.media_publish(p["creation_id"])
            try:
                link = await ig.permalink(media_id)
            except GraphError as e:  # posted all the same; the link is only nice to have
                log.info("post %s: published, permalink lookup failed (%s)", p["id"], e)
                link = None
    except GraphError as e:
        if e.ambiguous:
            return await asyncio.to_thread(_to_unknown, org_id, p, str(e))
        await asyncio.to_thread(_revoke, org_id, p, e)
        return _out(await asyncio.to_thread(_fail, org_id, p, str(e), expect={"publishing"},
                                            label="Instagram refused to publish"))
    except Exception as e:  # noqa: BLE001 — we can't tell what happened: treat it as maybe posted
        return await asyncio.to_thread(_to_unknown, org_id, p, f"{type(e).__name__}: {e}")
    return _out(await asyncio.to_thread(_published, org_id, p, media_id, link, {"publishing"}, "published"))


def _published(org_id: UUID, p: dict, media_id: str | None, link: str | None, expect: set[str], how: str,
               action: str = "post.published", detail: dict | None = None) -> Post | None:
    now = _now()
    where = f"@{p['username']}" if p.get("username") else "Instagram"
    label = ("Mock publish complete (nothing posted)" if p["mode"] == "mock" else f"Published to {where}")
    post = posts.transition(
        org_id, p["id"], expect, state="published", media_id=media_id, permalink=link, published_at=now,
        finished_at=now, next_check_at=None, progress=_progress("done", label=label),
        job_update=_job_log(f"{how}{f' · {link}' if link else ''}",
                            step=("done", "mock post created" if p["mode"] == "mock" else f"posted to {where}")),
        audit_action=action, audit_detail={"media_id": media_id, "permalink": link, **(detail or {})})
    if post and p["mode"] == "graph":
        log.info("post %s published to %s", p["id"], where)
    return post


# ---------------------------------------------------------------- unknown → reconciler

def _pick_media(media: list[publisher.Media], p: dict, caption: str) -> publisher.Media | None:
    """The account's post that is this one: published after we started, with our caption if Instagram says."""
    since = (p["publishing_started_at"] or p["created_at"]) - timedelta(minutes=5)
    head = caption.strip()[:60]
    fits = [m for m in media if m.timestamp is None or m.timestamp >= since]
    by_caption = [m for m in fits if m.caption and head and m.caption.strip().startswith(head)]
    pick = by_caption or ([m for m in fits if m.caption is None] if head else fits)
    return min(pick, key=lambda m: m.timestamp or _now()) if pick else None


async def _reconcile(org_id: UUID, p: dict) -> dict:
    now = _now()
    if p["next_check_at"] and p["next_check_at"] > now + timedelta(seconds=1):
        return {"state": "unknown", "done": False, "wait": (p["next_check_at"] - now).total_seconds()}
    if p["next_check_at"] is None:
        return {"state": "unknown", "done": True, "wait": 0}  # waiting for a person
    since = p["unknown_since"] or now
    alert = None
    if now - since >= ALERT_AFTER and not p["alerted_at"]:
        alert = now
        log.error("ALERT post %s (Organization %s) has been unknown for %d min: check Instagram before retrying",
                  p["id"], org_id, (now - since).total_seconds() // 60)

    def keep_checking(error: str) -> dict:
        n = p["reconcile_attempts"] + 1
        wait = reconcile_delay(n)
        give_up = now - since >= CHECK_FOR
        post = posts.transition(
            org_id, p["id"], {"unknown"}, reconcile_attempts=n, last_error=error[:500],
            alerted_at=p["alerted_at"] or alert or (now if give_up else None),
            next_check_at=None if give_up else now + timedelta(seconds=wait),
            progress=_progress("publish", label="Couldn't confirm: check Instagram, then resolve it" if give_up
                               else "Checking Instagram…"),
            audit_action="post.unknown_alert" if (alert or give_up) and not p["alerted_at"] else None,
            audit_detail={"error": error[:300], "unknown_for_min": int((now - since).total_seconds() // 60)})
        return _out(post, wait=wait)

    token = await asyncio.to_thread(_token, org_id, p)
    if not token:
        # Can't ask Instagram: a person must check. Not "failed": it may be live.
        post = await asyncio.to_thread(lambda: posts.transition(
            org_id, p["id"], {"unknown"}, next_check_at=None, alerted_at=p["alerted_at"] or now,
            last_error=f"can't check: the Instagram account @{p['username']} is disconnected or needs reconnecting",
            progress=_progress("publish", label="Couldn't check Instagram: check it yourself, then resolve it"),
            audit_action="post.unknown_alert", audit_detail={"error": "no token to check with"}))
        return _out(post)
    job = await asyncio.to_thread(store.get_job, org_id, p["job_id"])
    caption = (job.deck.caption if job and job.deck else "") or ""
    try:
        async with Instagram(p["ig_user_id"], token) as ig:
            status = await ig.status(p["creation_id"])
            found = None
            if status == "PUBLISHED":
                try:
                    found = _pick_media(await ig.recent_media(), p, caption)
                except GraphError as e:
                    log.info("post %s: live, but its media lookup failed (%s)", p["id"], e)
    except GraphError as e:
        await asyncio.to_thread(_revoke, org_id, p, e)
        if e.ambiguous:
            return await asyncio.to_thread(keep_checking, f"checking: {e}")
        why = f"can't check: {e}"[:500]
        post = await asyncio.to_thread(lambda: posts.transition(
            org_id, p["id"], {"unknown"}, next_check_at=None, alerted_at=p["alerted_at"] or now, last_error=why,
            progress=_progress("publish", label="Couldn't check Instagram: check it yourself, then resolve it"),
            audit_action="post.unknown_alert", audit_detail={"error": why}))
        return _out(post)

    settled = {"by": "reconciler", "container": status, "unknown_for_min": int((now - since).total_seconds() // 60)}
    if status == "PUBLISHED":
        post = await asyncio.to_thread(
            _published, org_id, p, found.id if found else None, found.permalink if found else None, {"unknown"},
            "Instagram confirms it went live after all", "post.settled", settled)
        return _out(post)
    if status == "FINISHED":
        if p["attempts"] >= MAX_PUBLISH_ATTEMPTS:
            return _out(await asyncio.to_thread(
                _fail, org_id, p, f"Instagram did not publish the carousel in {p['attempts']} attempts "
                f"({p['last_error'] or 'no reason given'})", expect={"unknown"}))
        # Not posted: safe to retry, with the same container.
        post = await asyncio.to_thread(lambda: posts.transition(
            org_id, p["id"], {"unknown"}, state="container_ready", next_check_at=None,
            progress=_progress("publish", label="Not posted yet: retrying with the same carousel"),
            job_update=_job_log("Instagram: container still FINISHED (not posted) → retrying with the same "
                                "container"),
            audit_action="post.settled", audit_detail={**settled, "outcome": "retry"}))
        return _out(post)
    if status in ("EXPIRED", "ERROR"):
        return _out(await asyncio.to_thread(
            _fail, org_id, p, f"Instagram reports the carousel {status}; it was never published", expect={"unknown"}))
    return await asyncio.to_thread(keep_checking, f"container is {status or 'not reported'}")


# ---------------------------------------------------------------- starting, restarting

def create(org_id: UUID, job: Job, *, mode: str, by: dict, account: dict | None,
           deck_version: int | None = None, audits: list[audit.Entry] = (),
           scheduled_at: datetime | None = None) -> Post:
    """Approval: a `queued` Post (or `scheduled`, #26), the Job moved to PUBLISHING (SCHEDULED) and the approval's
    audit events, in one transaction. The Post posts exactly the Deck version it pins (#19), caption included:
    nothing is edited at Approve, so the Post's own `caption` stays NULL (the Deck's). Version 1 is stored here if
    it never was. A Post on hold for this Job is replaced (cancelled) by this one: re-approval resumes it.
    Raises posts.Taken when a Post of this Job is still open, or this Deck is already on that account."""
    with store.tenant(org_id, user_id=UUID(by["id"])) as c:
        deck_versions.ensure_initial(c, job)
        held = c.execute("update posts set state = 'cancelled', finished_at = now(), updated_at = now(), "
                         "last_error = %s where job_id = %s and state = 'on_hold' returning id",
                         (f"replaced by the approval of version {deck_version or 1}", job.id)).fetchone()
        post = posts.insert(c, job_id=job.id, mode=mode, approved_by=UUID(by["id"]), approved_by_email=by["email"],
                            account_id=account["id"] if account else None,
                            ig_user_id=account["ig_user_id"] if account else None,
                            username=account["username"] if account else None, caption=None,
                            deck_version=deck_version, scheduled_at=scheduled_at)
        job.status = JobStatus.SCHEDULED if scheduled_at else JobStatus.PUBLISHING
        job.error = None
        for s in job.pipeline:
            if s.name == orchestrator.STEPS["publish"][0]:
                s.state, s.note = (("pending", f"scheduled for {posts.kst(scheduled_at)} · {mode}") if scheduled_at
                                   else ("running", f"approved · {mode}"))
        c.execute("update jobs set doc = %s where id = %s", (store._json(job), job.id))
        for e in audits:
            extra = {"post": str(post.id), **({"scheduled_at": scheduled_at.isoformat()} if scheduled_at else {}),
                     **({"replaces_held_post": str(held[0])} if held else {})}
            audit.write(c, org_id, e.model_copy(update={"detail": {**e.detail, **extra}}))
    return post


def stop(post: Post) -> None:
    """End the Post's workflow if it is still waiting (a cancelled or held Post has nothing left to do). Harmless when
    it already finished."""
    try:
        DBOS.cancel_workflow(workflow_id(post.id, _runs(post)))
    except Exception:  # noqa: BLE001 — no such workflow (never started, or a test drove it inline)
        log.debug("no workflow to stop for post %s", post.id)


def restart(org_id: UUID, post_id: UUID) -> None:
    """Rescheduled (#26): stop the sleeping workflow and drive the Post with a fresh one, which reads the new time.
    Every step starts from the saved state, so the swap is safe whenever it happens."""
    with store.tenant(org_id) as c:
        (runs,) = c.execute("select runs from posts where id = %s", (post_id,)).fetchone()
        c.execute("update posts set runs = %s where id = %s", (runs + 1, post_id))
    try:
        DBOS.cancel_workflow(workflow_id(post_id, runs))
    except Exception:  # noqa: BLE001
        log.debug("no workflow to stop for post %s", post_id)
    with SetWorkflowID(workflow_id(post_id, runs + 1)):
        DBOS.retrieve_queue(POST_QUEUE).enqueue(post_workflow, str(org_id), str(post_id))


def _runs(post: Post) -> int:
    with store.system() as c:
        row = c.execute("select runs from posts where id = %s", (post.id,)).fetchone()
    return row[0] if row else 1


async def start(org_id: UUID, post_id: UUID, runs: int = 1) -> None:
    """Queue the Post's workflow. Once this returns, the Post survives a restart."""
    queue = await DBOS.retrieve_queue_async(POST_QUEUE)
    with SetWorkflowID(workflow_id(post_id, runs)):
        await queue.enqueue_async(post_workflow, str(org_id), str(post_id))


async def run(org_id: UUID, post_id: UUID) -> str:
    """Drive a Post now, in this task, as the same durable workflow (tests; the API queues with `start`)."""
    if not durable.launched():
        await asyncio.to_thread(durable.launch)
    with SetWorkflowID(workflow_id(post_id)):
        return await post_workflow(str(org_id), str(post_id))


async def publish_now(org_id: UUID, job_id: str, mode: str = "mock", account: dict | None = None,
                      by: dict | None = None) -> Post | None:
    """Approve-and-publish without the route (tests, scripts): create the Post and run it to the end. `account`:
    {"id", "ig_user_id", "username"} of a connected account."""
    job = await asyncio.to_thread(store.get_job, org_id, job_id)
    if not job or not job.deck:
        return None
    if by is None:  # a Member to act as: the Organization's first Admin
        with store.system() as c:
            uid, email = c.execute("select user_id, email from members where org_id = %s order by created_at "
                                   "limit 1", (org_id,)).fetchone()
        by = {"id": str(uid), "email": email or ""}
    approved = audit.Entry(actor=UUID(by["id"]), actor_email=by.get("email", ""), action="deck.approved",
                           target=job.id, detail={"mode": mode, "account": f"@{account['username']}" if account
                                                  else None})
    post = await asyncio.to_thread(lambda: create(org_id, job, mode=mode, by=by, account=account,
                                                  deck_version=job.deck_version, audits=[approved]))
    await run(org_id, post.id)
    return await asyncio.to_thread(posts.get, org_id, post.id)


def recover_after_restart() -> None:
    """At startup, after `durable.launch()` has resumed the workflows it can: an open Post whose workflow will not
    continue (never queued, errored, out of recovery attempts, or from another WORKFLOW_VERSION) gets a fresh
    workflow. Because every step starts from the saved state, the fresh one carries on correctly: a Post left
    `publishing` is checked as unknown, never failed or published twice. Blocking: run it off the event loop."""
    queue = DBOS.retrieve_queue(POST_QUEUE)
    for org_id, post_id, state, runs in posts.open_posts():
        wid = workflow_id(post_id, runs)
        st = DBOS.get_workflow_status(wid)
        if st is not None and st.status in ("PENDING", "ENQUEUED") and st.app_version == settings.workflow_version:
            continue
        if state == "on_hold" or (st is not None and st.status == "SUCCESS" and state == "unknown"):
            continue  # it stopped on purpose: a person resolves it (or approves the held Deck again)
        if st is not None and st.status in ("PENDING", "ENQUEUED"):
            DBOS.cancel_workflow(wid)  # checkpoints from another version: drive it afresh instead
        with store.tenant(org_id) as c:
            c.execute("update posts set runs = %s where id = %s", (runs + 1, post_id))
        with SetWorkflowID(workflow_id(post_id, runs + 1)):
            queue.enqueue(post_workflow, str(org_id), str(post_id))
        log.warning("post %s (%s) had no live workflow after a restart: driving it again", post_id, state)

