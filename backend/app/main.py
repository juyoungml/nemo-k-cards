"""FastAPI entrypoint. Routes follow SPEC §12 / BACKEND §4 and match apps/web/src/lib/api.ts."""

import asyncio
import hashlib
import secrets
import time
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app import (
    assets,
    audit,
    auth,
    brainstorms,
    deck_versions,
    instagram_accounts,
    materials,
    members,
    org_profile,
    orgs,
    posts,
    seed,
    slide_sources,
    store,
)
from app.config import settings
from app.pipeline import durable, orchestrator, publishing
from app.schemas import (
    MAX_MATERIALS,
    TERMINAL,
    ApproveRequest,
    Brainstorm,
    BrainstormMessageRequest,
    BrainstormPatch,
    CreateBrainstormRequest,
    CreateJobRequest,
    Job,
    JobStatus,
    Metric,
    RejectRequest,
)
from app.services import safeguard


def recover_interrupted() -> None:
    """After a restart (and `durable.launch()`, which resumes interrupted Jobs and Posts where they stopped): a Job
    whose pipeline can't resume is marked failed (`orchestrator.reconcile_after_restart`); an open Post whose
    workflow can't resume gets a fresh one (`publishing.recover_after_restart`), which checks Instagram before
    anything is retried. A Post is never marked failed by a restart: it may have gone live."""
    orchestrator.reconcile_after_restart()
    publishing.recover_after_restart()
    deck_versions.recover_checks()  # an edited Deck still waiting for its Safeguard check (#19)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # DBOS gets its own event loop thread for the pipeline (launched from a worker thread, not uvicorn's loop).
    await asyncio.to_thread(durable.launch)
    await asyncio.to_thread(recover_interrupted)
    yield
    await asyncio.to_thread(durable.shutdown)  # running Jobs stay checkpointed and resume at the next start
    store.close()


app = FastAPI(title="What's On Korea API", lifespan=lifespan)


# Sign-in: every route except /health (and /assets, whose signed links are their own credential) needs a
# Supabase session.
# Registered before CORS so CORS wraps it and a 401 still carries CORS headers.
app.middleware("http")(auth.require_session)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])

# Rendered slides are private (#18): served only through short-lived signed links (app/assets.py), which the
# API hands to the Admin in `slide_urls` and the publisher hands to Instagram.
settings.output_dir.mkdir(parents=True, exist_ok=True)
app.include_router(assets.router)
app.include_router(orgs.router)
app.include_router(members.router)
app.include_router(org_profile.router)
app.include_router(instagram_accounts.router)
app.include_router(materials.router)
app.include_router(posts.router)
app.include_router(deck_versions.router)


# Every Organization-owned read and write below goes through the store with the caller's Organization
# (`ctx.org_id`): RLS makes another Organization's Job or Brainstorm look like it doesn't exist (404).
# Each route names the least Role it needs (GLOSSARY "Role"; docs/spec/v1.md API table): Viewer reads,
# Editor starts Jobs and Brainstorms, Approver approves and rejects, Admin manages Members (app/members.py).

def _job_or_404(org_id: UUID, job_id: str) -> Job:
    if not (job := store.get_job(org_id, job_id)):
        raise HTTPException(404, "job not found")
    return job


def _out(org_id: UUID, job: Job, *, sources: bool = True) -> Job:
    """A Job leaving the API: its slide paths become short-lived signed links for this Organization, and (except in
    lists) each slide says where its facts came from (#25)."""
    job = assets.for_admin(org_id, job)
    return job.model_copy(update={"slide_sources": slide_sources.for_job(job)}) if sources else job


def _member(ctx: auth.Ctx) -> dict:
    """Who the host acts for when it writes audit events later, outside this request (pipeline, publishing)."""
    return {"id": str(ctx.user.id), "email": ctx.user.email}


def _brainstorm_or_404(org_id: UUID, brainstorm_id: str) -> Brainstorm:
    if not (d := store.get_brainstorm(org_id, brainstorm_id)):
        raise HTTPException(404, "brainstorm not found")
    return d


@app.get("/health")
def health() -> dict:
    return {"ok": True, "demo_mode": settings.demo_mode, "agent_runner": settings.agent_runner,
            "publish_mode": settings.publish_mode}


# ---------------------------------------------------------------- jobs

@app.post("/jobs", response_model=Job, status_code=201)
async def create_job(req: CreateJobRequest, ctx: auth.Editor) -> Job:
    job = orchestrator.new_job(secrets.token_hex(8), req.prompt.strip(), kind=req.kind)
    job.material = await asyncio.to_thread(materials.refs, ctx.org_id, req.material_ids)  # 422 if not the caller's
    job.org_profile = await asyncio.to_thread(org_profile.get, ctx.org_id)  # every stage reads this snapshot (#16)
    await asyncio.to_thread(lambda: store.save_job(ctx.org_id, job, audit=audit.entry(
        ctx, "job.created", job.id, kind=req.kind or "auto",
        **({"material": [m.id for m in job.material]} if job.material else {}))))
    # Fixture mode: a Guide Deck request replays the guide scenario unless a scenario was named.
    scenario = req.scenario or ("guide" if req.kind == "guide" else "good")
    await orchestrator.start_job(ctx.org_id, job, scenario=scenario, started_by=_member(ctx))
    return _out(ctx.org_id, job)


@app.get("/jobs", response_model=list[Job])
def list_jobs(ctx: auth.Viewer, limit: int = Query(50, ge=1, le=500)) -> list[Job]:
    return [_out(ctx.org_id, j, sources=False) for j in store.list_jobs(ctx.org_id, limit)]


@app.get("/jobs/{job_id}", response_model=Job)
def get_job(job_id: str, ctx: auth.Viewer) -> Job:
    return _out(ctx.org_id, _job_or_404(ctx.org_id, job_id))


@app.get("/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request, ctx: auth.Viewer) -> StreamingResponse:
    """SSE: a full Job snapshot whenever it changes, until a terminal state (same as the mock backend).
    The Admin reads it with fetch so the session token goes in the Authorization header, never the URL; the
    stream ends when that token expires and the client reconnects with a fresh one."""
    await asyncio.to_thread(_job_or_404, ctx.org_id, job_id)

    async def stream():
        last = ""
        while not await request.is_disconnected() and time.time() < ctx.user.expires_at:
            job = await asyncio.to_thread(store.get_job, ctx.org_id, job_id)
            if not job:
                break
            data = _out(ctx.org_id, job).model_dump_json()  # links change at most once per signing window
            if data != last:
                yield f"data: {data}\n\n"
                last = data
            if job.status in TERMINAL:
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"cache-control": "no-cache"})


@app.post("/jobs/{job_id}/approve", response_model=Job)
async def approve(job_id: str, req: ApproveRequest, ctx: auth.Approver) -> Job:
    """The ONLY publish path: a person approves in the Admin → a Post (#21) the host publishes (the sandbox has no
    token). The Approver picks one of the Organization's connected Instagram accounts (preselected when there is
    only one; a mock post may go without), and posts now or at `scheduled_at` (#26: the Post waits `scheduled`;
    approving a Deck whose Post is on hold replaces that Post). Approval creates the Post and starts its durable workflow
    (app/pipeline/publishing.py); the Post's state, not this response, says what happened on Instagram.
    A Deck the Safeguard blocked can't be approved by anyone, with or without an Override reason (ADR 0004); the
    refusal is audited. Approving a Deck with blocking Issues is an Override and needs a written reason (GLOSSARY).
    Approval pins one Deck version (#19), and what is posted is exactly that version: its slides, caption and hashtags.
    There is no editing at Approve; a change is a Deck edit, which makes a new version the Safeguard checks first.
    Self-approval is allowed: whoever started the Job may approve it if their Role allows."""
    job = await asyncio.to_thread(_job_or_404, ctx.org_id, job_id)
    reason = (req.override_reason or "").strip()
    mode = req.mode or settings.publish_mode
    if job.status == JobStatus.BLOCKED or (job.safeguard and job.safeguard.decision == "block"):
        await asyncio.to_thread(_refuse, ctx, job, "blocked_by_safeguard", reason, mode)
        edited = job.status != JobStatus.BLOCKED and job.safeguard.deck_version is not None
        raise HTTPException(409, "blocked by safeguard, which no Override can approve: "
                                 f"{job.safeguard.summary if job.safeguard else ''} "
                                 + ("Edit this version again, or reject the Deck." if edited
                                    else "Regenerate or reject this Deck."))
    if job.status == JobStatus.PUBLISHING:
        # Approve is locked while a Post is open, unknown included: approving again could post twice.
        raise HTTPException(409, "a Post of this Deck is still in progress or being checked on Instagram; wait "
                                 "until it settles")
    if job.status == JobStatus.SCHEDULED:
        raise HTTPException(409, "this Deck is already scheduled: reschedule or cancel its Post instead")
    # Timed Posts (#26): checked before anything else is decided, so a bad time is a plain 422.
    scheduled_at = posts.check_time(req.scheduled_at) if req.scheduled_at else None
    if job.status != JobStatus.READY_FOR_REVIEW:
        raise HTTPException(409, f"job is {job.status}, not READY_FOR_REVIEW")
    pinned = deck_versions.pin(job, req.deck_version)  # 409 if edited since the Approver loaded it (#19)
    if job.safeguard_pending:
        await asyncio.to_thread(_refuse, ctx, job, "safeguard_checking", reason, mode)
        raise HTTPException(409, f"the safeguard is still checking version {job.deck_version} after an edit; "
                                 "approve once it has finished")
    if not job.safeguard or not job.deck:
        await asyncio.to_thread(_refuse, ctx, job, "no_safeguard_verdict", reason, mode)
        raise HTTPException(409, "this job has no safeguard verdict (created before the safeguard existed); "
                                 "regenerate it")
    if not _verdict_matches(job):  # a verdict only vouches for the exact content it checked (ADR 0004)
        await asyncio.to_thread(_refuse, ctx, job, "verdict_for_other_content", reason, mode)
        raise HTTPException(409, f"the safeguard verdict is not for version {job.deck_version} as it is now; "
                                 "edit and save it again to have it checked")
    # A request may ask for a safer mode than PUBLISH_MODE, never a stronger one (e.g. graph on a mock server).
    rank = {"mock": 0, "dryrun": 1, "graph": 2}
    if rank[mode] > rank[settings.publish_mode]:
        raise HTTPException(403, f"publish mode {mode!r} is not enabled on this server "
                                 f"(PUBLISH_MODE={settings.publish_mode})")
    account = await asyncio.to_thread(_pick_account, ctx, req.account_id, mode)
    blocking = [i for i in job.issues if i.severity == "block"]
    if blocking and not reason:
        raise HTTPException(422, "this Deck has blocking Issues: approving it is an Override and needs a "
                                 "written reason (override_reason)")
    # Blocking issues are advisory: the operator saw them in Review and chose to approve.
    # Hard stop only for leaked credentials — those must never be published.
    if any(i.category == "pii" and "credential" in i.message for i in job.issues):
        raise HTTPException(409, "deck contains a credential-like string; regenerate before publishing")
    # The chosen account's own token is a credential too: read on the privileged path, only to look for it.
    tokens = tuple(t for t in [await asyncio.to_thread(_token_of, ctx.org_id, account)] if t)
    if any(t in f"{job.deck.caption}\n" + "\n".join(f"{s.heading}\n{s.body}" for s in job.deck.slides)
           for t in tokens):
        raise HTTPException(409, "deck contains a credential-like string; regenerate before publishing")
    del tokens
    entries = _approval_entries(ctx, job, mode, blocking, reason, account, pinned)
    try:
        # The Post records the pinned version and posts exactly it; the publisher stops if the Deck moves on (#19).
        post = await asyncio.to_thread(lambda: publishing.create(
            ctx.org_id, job, mode=mode, by=_member(ctx), account=account, deck_version=pinned, audits=entries,
            scheduled_at=scheduled_at))
    except posts.Taken as t:
        raise HTTPException(409, "a Post of this Deck is already in progress; wait until it settles" if t.open
                                 else f"this Deck is already posted to {account['handle'] if account else 'it'}; "
                                      "a correction needs a new Deck version") from None
    await publishing.start(ctx.org_id, post.id)
    return _out(ctx.org_id, job)


def _pick_account(ctx: auth.Ctx, account_id: UUID | None, mode: str) -> dict | None:
    """The Instagram account the Post goes to: one of this Organization's live (connected or expiring) accounts,
    read under RLS. Preselected when there is exactly one; a mock post may go without any."""
    with store.tenant(ctx.org_id) as c:
        rows = c.execute("select id, ig_user_id, username, status from instagram_accounts where org_id = %s "
                         "and status in ('connected', 'expiring', 'revoked') order by connected_at, id",
                         (ctx.org_id,)).fetchall()
    live = [{"id": r[0], "ig_user_id": r[1], "username": r[2], "handle": f"@{r[2]}"} for r in rows if r[3] != "revoked"]
    revoked = {r[0]: f"@{r[2]}" for r in rows if r[3] == "revoked"}
    if account_id is not None:
        if account_id in revoked:  # its token was revoked or expired (#22): say what to do
            raise HTTPException(409, f"{revoked[account_id]} needs reconnecting: an Admin can reconnect it on the "
                                     "Instagram page, then approve again")
        if not (match := [a for a in live if a["id"] == account_id]):
            raise HTTPException(422, "pick one of this Organization's connected Instagram accounts")
        return match[0]
    if len(live) == 1:
        return live[0]
    if live:
        raise HTTPException(422, "this Organization has several Instagram accounts: choose one (account_id)")
    if mode == "mock":
        return None
    if revoked:
        raise HTTPException(409, f"{', '.join(revoked.values())} needs reconnecting: an Admin can reconnect it on the "
                                 "Instagram page, then approve again")
    raise HTTPException(422, "connect an Instagram account first: a dry run or a real post goes to the "
                             "Organization's own account")


def _token_of(org_id: UUID, account: dict | None) -> str | None:
    if not account:
        return None
    with store.system() as c:
        return instagram_accounts.access_token(c, org_id, account["id"])


def _refuse(ctx: auth.Ctx, job: Job, why: str, reason: str, mode: str) -> None:
    """An approval the Safeguard refused, kept in the audit log with any Override reason that was offered."""
    audit.record(ctx, "deck.approve_refused", job.id, why=why, override_reason=reason or None, mode=mode,
                 summary=job.safeguard.summary if job.safeguard else None)


def _verdict_matches(job: Job) -> bool:
    try:
        return job.safeguard.content_hash == safeguard.content_hash(job.deck, assets.slide_files(job))
    except OSError:  # a slide file is gone: nothing proves what the verdict saw
        return False


def _approval_entries(ctx: auth.Ctx, job: Job, mode: str, blocking: list, reason: str, account: dict | None,
                      deck_version: int) -> list[audit.Entry]:
    """Written with the Post, in its transaction: the decision stands whatever Instagram later says (that is on the
    Post). Each names the Deck version it approved (#19)."""
    where = account["handle"] if account else None
    if blocking:
        return [audit.entry(
            ctx, "deck.overridden", job.id, reason=reason, mode=mode, account=where, deck_version=deck_version,
            issues=[{"category": i.category, "slide_index": i.slide_index, "message": i.message} for i in blocking])]
    return [audit.entry(ctx, "deck.approved", job.id, mode=mode, account=where, deck_version=deck_version)]


@app.post("/jobs/{job_id}/reject", response_model=Job)
def reject(job_id: str, req: RejectRequest, ctx: auth.Approver) -> Job:
    job = _job_or_404(ctx.org_id, job_id)
    if job.status not in (JobStatus.READY_FOR_REVIEW, JobStatus.BLOCKED):  # a blocked Deck can still be rejected
        raise HTTPException(409, f"job is {job.status}, not READY_FOR_REVIEW or BLOCKED")
    job.status, job.error = JobStatus.REJECTED, req.reason or "rejected by operator"
    return _out(ctx.org_id, store.save_job(ctx.org_id, job, audit=audit.entry(ctx, "deck.rejected", job.id, reason=job.error)))


# ---------------------------------------------------------------- brainstorms

@app.post("/brainstorms", response_model=Brainstorm, status_code=201)
def create_brainstorm(ctx: auth.Editor, req: CreateBrainstormRequest | None = None) -> Brainstorm:
    # The Mangwon sample (its facts are marked verified against a page that doesn't exist) is for demos only;
    # a live server starts every Brainstorm from an empty board.
    return brainstorms.create(ctx.org_id, (req.sample if req else True) and settings.demo_mode == "fixture")


@app.get("/brainstorms/{brainstorm_id}", response_model=Brainstorm)
def get_brainstorm(brainstorm_id: str, ctx: auth.Viewer) -> Brainstorm:
    return _brainstorm_or_404(ctx.org_id, brainstorm_id)


@app.patch("/brainstorms/{brainstorm_id}", response_model=Brainstorm)
def patch_brainstorm(brainstorm_id: str, patch: BrainstormPatch, ctx: auth.Editor) -> Brainstorm:
    d = _brainstorm_or_404(ctx.org_id, brainstorm_id)
    return store.save_brainstorm(ctx.org_id, d.model_copy(update=patch.model_dump(exclude_unset=True)))


@app.post("/brainstorms/{brainstorm_id}/messages", response_model=Brainstorm)
async def brainstorm_message(brainstorm_id: str, req: BrainstormMessageRequest, ctx: auth.Editor) -> Brainstorm:
    d = await asyncio.to_thread(_brainstorm_or_404, ctx.org_id, brainstorm_id)
    attached = await asyncio.to_thread(materials.refs, ctx.org_id, req.material_ids)  # 422 if not the caller's
    if len({m.id for m in d.material} | {m.id for m in attached}) > MAX_MATERIALS:
        raise HTTPException(422, f"at most {MAX_MATERIALS} pieces of Source material per Brainstorm")
    return await brainstorms.send_message(ctx.org_id, d, req.text, req.urls, attached)


@app.post("/brainstorms/{brainstorm_id}/generate", response_model=Job, status_code=201)
async def generate(brainstorm_id: str, ctx: auth.Editor) -> Job:
    d = await asyncio.to_thread(_brainstorm_or_404, ctx.org_id, brainstorm_id)
    angle = next((a for a in d.angles if a.id == d.selected_angle_id), None)
    if not angle:
        raise HTTPException(422, "pick an angle first")
    job = orchestrator.new_job(secrets.token_hex(8), f"Brainstorm: {angle.title}", "brainstorm", d.id)
    job.material = d.material  # the Brainstorm's Source material carries over to its Job
    job.org_profile = await asyncio.to_thread(org_profile.get, ctx.org_id)  # every stage reads this snapshot (#16)
    await asyncio.to_thread(lambda: store.save_job(ctx.org_id, job, audit=audit.entry(
        ctx, "job.created", job.id, brainstorm=d.id)))
    await asyncio.to_thread(store.save_brainstorm, ctx.org_id, d.model_copy(update={"job_id": job.id}))
    await orchestrator.start_job(ctx.org_id, job, brainstorm=d, started_by=_member(ctx))
    return _out(ctx.org_id, job)


# ---------------------------------------------------------------- dashboard & policy

@app.get("/metrics", response_model=list[Metric])
def metrics(ctx: auth.Viewer) -> list[Metric]:
    return seed.metrics(ctx.org_id, store.list_jobs(ctx.org_id, 200))


@app.get("/policy-events")
def policy_events(ctx: auth.Viewer) -> dict:
    events = store.list_policy_events(ctx.org_id) + (seed.POLICY_SEED if settings.demo_mode == "fixture" else [])
    try:
        version = hashlib.sha256(settings.openshell_policy.read_bytes()).hexdigest()[:7]
    except OSError:
        version = "—"
    return {"stats": seed.policy_stats(events, version),
            "events": [e.model_dump() for e in events]}


@app.post("/reset")
def reset(_: auth.Viewer) -> dict:
    """Mock-backend QA endpoint; no-op on the real backend."""
    return {"ok": True}
