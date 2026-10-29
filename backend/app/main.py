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

from app import assets, audit, auth, brainstorms, members, orgs, seed, store
from app.config import settings
from app.pipeline import durable, orchestrator
from app.schemas import (
    TERMINAL,
    ApproveRequest,
    Brainstorm,
    BrainstormMessageRequest,
    BrainstormPatch,
    CreateBrainstormRequest,
    CreateJobRequest,
    InstagramAccount,
    Job,
    JobStatus,
    Metric,
    RejectRequest,
)


def recover_interrupted() -> None:
    """After a restart (and `durable.launch()`, which resumes interrupted Jobs at their unfinished stage): a publish
    is not durable yet (#21), so a Job left PUBLISHING goes back to Review; a Job whose pipeline can't resume is
    marked failed (`orchestrator.reconcile_after_restart`). Every Organization's; each saved under its own."""
    for org_id, job in store.jobs_in({JobStatus.PUBLISHING}):
        job.status = JobStatus.READY_FOR_REVIEW
        job.error = "Publish interrupted by a server restart. Nothing was posted; approve again."
        if job.publish_progress:
            job.publish_progress.step, job.publish_progress.error = "failed", job.error
            job.publish_progress.label = "Interrupted by a server restart"
        store.save_job(org_id, job)
    orchestrator.reconcile_after_restart()


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


# Every Organization-owned read and write below goes through the store with the caller's Organization
# (`ctx.org_id`): RLS makes another Organization's Job or Brainstorm look like it doesn't exist (404).
# Each route names the least Role it needs (GLOSSARY "Role"; docs/spec/v1.md API table): Viewer reads,
# Editor starts Jobs and Brainstorms, Approver approves and rejects, Admin manages Members (app/members.py).

def _job_or_404(org_id: UUID, job_id: str) -> Job:
    if not (job := store.get_job(org_id, job_id)):
        raise HTTPException(404, "job not found")
    return job


def _out(org_id: UUID, job: Job) -> Job:
    """A Job leaving the API: its slide paths become short-lived signed links for this Organization."""
    return assets.for_admin(org_id, job)


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
    job = orchestrator.new_job(secrets.token_hex(8), req.prompt.strip())
    await asyncio.to_thread(lambda: store.save_job(ctx.org_id, job, audit=audit.entry(ctx, "job.created", job.id)))
    await orchestrator.start_job(ctx.org_id, job, scenario=req.scenario or "good")
    return _out(ctx.org_id, job)


@app.get("/jobs", response_model=list[Job])
def list_jobs(ctx: auth.Viewer, limit: int = Query(50, ge=1, le=500)) -> list[Job]:
    return [_out(ctx.org_id, j) for j in store.list_jobs(ctx.org_id, limit)]


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
    """The ONLY publish path: human approves in Admin -> host publishes (sandbox has no token).
    Approving a Deck with blocking Issues is an Override and needs a written reason (GLOSSARY). Self-approval is
    allowed: whoever started the Job may approve it if their Role allows."""
    job = await asyncio.to_thread(_job_or_404, ctx.org_id, job_id)
    if job.status != JobStatus.READY_FOR_REVIEW:
        raise HTTPException(409, f"job is {job.status}, not READY_FOR_REVIEW")
    blocking = [i for i in job.issues if i.severity == "block"]
    reason = (req.override_reason or "").strip()
    if blocking and not reason:
        raise HTTPException(422, "this Deck has blocking Issues: approving it is an Override and needs a "
                                 "written reason (override_reason)")
    # Blocking issues are advisory: the operator saw them in Review and chose to approve.
    # Hard stop only for leaked credentials — those must never be published.
    if any(i.category == "pii" and "credential" in i.message for i in job.issues):
        raise HTTPException(409, "deck contains a credential-like string; regenerate before publishing")
    if req.caption:
        from app.services.visual_qa import HASHTAG, scan_text
        if len(HASHTAG.findall(req.caption)) > 5 or scan_text(req.caption, "caption",
                                                               secrets=(settings.ig_access_token,)):
            raise HTTPException(422, "edited caption fails checks (PII/credential or more than 5 hashtags)")
    # A request may ask for a safer mode than PUBLISH_MODE, never a stronger one (e.g. graph on a mock server).
    rank = {"mock": 0, "dryrun": 1, "graph": 2}
    mode = req.mode or settings.publish_mode
    if rank[mode] > rank[settings.publish_mode]:
        raise HTTPException(403, f"publish mode {mode!r} is not enabled on this server "
                                 f"(PUBLISH_MODE={settings.publish_mode})")
    await asyncio.to_thread(_audit_approval, ctx, job, req.caption, mode, blocking, reason)
    orchestrator.spawn(orchestrator.publish_job(ctx.org_id, job_id, req.caption, mode))
    job.status = JobStatus.PUBLISHING
    return _out(ctx.org_id, job)


def _audit_approval(ctx: auth.Ctx, job: Job, caption: str | None, mode: str, blocking: list, reason: str) -> None:
    """Recorded before the publish starts: the decision stands whatever Instagram later says (that is on the Job).
    A changed caption is a Deck edit, logged with the before and after text."""
    before = job.deck.caption if job.deck else None
    with store.tenant(ctx.org_id, user_id=ctx.user.id) as c:
        if caption and caption != before:
            audit.write(c, ctx.org_id, audit.entry(ctx, "deck.edited", job.id, field="caption",
                                                   before=before, after=caption))
        if blocking:
            audit.write(c, ctx.org_id, audit.entry(
                ctx, "deck.overridden", job.id, reason=reason, mode=mode,
                issues=[{"category": i.category, "slide_index": i.slide_index, "message": i.message}
                        for i in blocking]))
        else:
            audit.write(c, ctx.org_id, audit.entry(ctx, "deck.approved", job.id, mode=mode))


@app.post("/jobs/{job_id}/reject", response_model=Job)
def reject(job_id: str, req: RejectRequest, ctx: auth.Approver) -> Job:
    job = _job_or_404(ctx.org_id, job_id)
    if job.status != JobStatus.READY_FOR_REVIEW:
        raise HTTPException(409, f"job is {job.status}, not READY_FOR_REVIEW")
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
    return await brainstorms.send_message(ctx.org_id, d, req.text, req.urls)


@app.post("/brainstorms/{brainstorm_id}/generate", response_model=Job, status_code=201)
async def generate(brainstorm_id: str, ctx: auth.Editor) -> Job:
    d = await asyncio.to_thread(_brainstorm_or_404, ctx.org_id, brainstorm_id)
    angle = next((a for a in d.angles if a.id == d.selected_angle_id), None)
    if not angle:
        raise HTTPException(422, "pick an angle first")
    job = orchestrator.new_job(secrets.token_hex(8), f"Brainstorm: {angle.title}", "brainstorm", d.id)
    await asyncio.to_thread(lambda: store.save_job(ctx.org_id, job, audit=audit.entry(
        ctx, "job.created", job.id, brainstorm=d.id)))
    await asyncio.to_thread(store.save_brainstorm, ctx.org_id, d.model_copy(update={"job_id": job.id}))
    await orchestrator.start_job(ctx.org_id, job, brainstorm=d)
    return _out(ctx.org_id, job)


# ---------------------------------------------------------------- dashboard & policy

@app.get("/metrics", response_model=list[Metric])
def metrics(ctx: auth.Viewer) -> list[Metric]:
    return seed.metrics(store.list_jobs(ctx.org_id, 200))


@app.get("/instagram-accounts", response_model=list[InstagramAccount])
def instagram_accounts(_: auth.Viewer) -> list[InstagramAccount]:
    return seed.instagram_accounts()


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
