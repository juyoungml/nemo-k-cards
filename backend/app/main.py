"""FastAPI entrypoint. Routes follow SPEC §12 / BACKEND §4 and match apps/web/src/lib/api.ts."""

import asyncio
import hashlib
import secrets
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from app import auth, brainstorms, orgs, seed, store
from app.config import settings
from app.pipeline import orchestrator
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
    """A restart kills in-flight background tasks: never leave jobs stuck in a running state."""
    running = {JobStatus.QUEUED, JobStatus.RESEARCHING, JobStatus.VERIFYING, JobStatus.WRITING,
               JobStatus.RENDERING, JobStatus.QA, JobStatus.REVIEWING}
    for job in store.list_jobs(500):
        if job.status == JobStatus.PUBLISHING:
            job.status = JobStatus.READY_FOR_REVIEW
            job.error = "Publish interrupted by a server restart. Nothing was posted; approve again."
            if job.publish_progress:
                job.publish_progress.step, job.publish_progress.error = "failed", job.error
                job.publish_progress.label = "Interrupted by a server restart"
            store.save_job(job)
        elif job.status in running:
            job.status, job.error = JobStatus.FAILED, "Interrupted by a server restart; start the job again."
            store.save_job(job)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await asyncio.to_thread(recover_interrupted)
    yield
    store.close()


app = FastAPI(title="What's On Korea API", lifespan=lifespan)


# Sign-in: every route except /health (and the public slide files under /assets) needs a Supabase session.
# Registered before CORS so CORS wraps it and a 401 still carries CORS headers.
app.middleware("http")(auth.require_session)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])

# Rendered slides, publicly reachable via PUBLIC_ASSET_BASE_URL so Instagram can fetch them.
settings.output_dir.mkdir(parents=True, exist_ok=True)
app.mount("/assets", StaticFiles(directory=settings.output_dir), name="assets")
app.include_router(orgs.router)


def _job_or_404(job_id: str) -> Job:
    if not (job := store.get_job(job_id)):
        raise HTTPException(404, "job not found")
    return job


def _brainstorm_or_404(brainstorm_id: str) -> Brainstorm:
    if not (d := store.get_brainstorm(brainstorm_id)):
        raise HTTPException(404, "brainstorm not found")
    return d


@app.get("/health")
def health() -> dict:
    return {"ok": True, "demo_mode": settings.demo_mode, "agent_runner": settings.agent_runner,
            "publish_mode": settings.publish_mode}


# ---------------------------------------------------------------- jobs

@app.post("/jobs", response_model=Job, status_code=201)
async def create_job(req: CreateJobRequest) -> Job:
    job = orchestrator.new_job(secrets.token_hex(3), req.prompt.strip())
    await asyncio.to_thread(store.save_job, job)
    orchestrator.spawn(orchestrator.run_job(job.id, scenario=req.scenario or "good"))
    return job


@app.get("/jobs", response_model=list[Job])
def list_jobs(limit: int = Query(50, ge=1, le=500)) -> list[Job]:
    return store.list_jobs(limit)


@app.get("/jobs/{job_id}", response_model=Job)
def get_job(job_id: str) -> Job:
    return _job_or_404(job_id)


@app.get("/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request, ctx: auth.Member) -> StreamingResponse:
    """SSE: a full Job snapshot whenever it changes, until a terminal state (same as the mock backend).
    The Admin reads it with fetch so the session token goes in the Authorization header, never the URL; the
    stream ends when that token expires and the client reconnects with a fresh one."""
    await asyncio.to_thread(_job_or_404, job_id)

    async def stream():
        last = ""
        while not await request.is_disconnected() and time.time() < ctx.user.expires_at:
            job = await asyncio.to_thread(store.get_job, job_id)
            if not job:
                break
            data = job.model_dump_json()
            if data != last:
                yield f"data: {data}\n\n"
                last = data
            if job.status in TERMINAL:
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"cache-control": "no-cache"})


@app.post("/jobs/{job_id}/approve", response_model=Job)
async def approve(job_id: str, req: ApproveRequest) -> Job:
    """The ONLY publish path: human approves in Admin -> host publishes (sandbox has no token)."""
    job = await asyncio.to_thread(_job_or_404, job_id)
    if job.status != JobStatus.READY_FOR_REVIEW:
        raise HTTPException(409, f"job is {job.status}, not READY_FOR_REVIEW")
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
    orchestrator.spawn(orchestrator.publish_job(job_id, req.caption, mode))
    job.status = JobStatus.PUBLISHING
    return job


@app.post("/jobs/{job_id}/reject", response_model=Job)
def reject(job_id: str, req: RejectRequest) -> Job:
    job = _job_or_404(job_id)
    if job.status != JobStatus.READY_FOR_REVIEW:
        raise HTTPException(409, f"job is {job.status}, not READY_FOR_REVIEW")
    job.status, job.error = JobStatus.REJECTED, req.reason or "rejected by operator"
    return store.save_job(job)


# ---------------------------------------------------------------- brainstorms

@app.post("/brainstorms", response_model=Brainstorm, status_code=201)
def create_brainstorm(req: CreateBrainstormRequest | None = None) -> Brainstorm:
    # The Mangwon sample (its facts are marked verified against a page that doesn't exist) is for demos only;
    # a live server starts every Brainstorm from an empty board.
    return brainstorms.create((req.sample if req else True) and settings.demo_mode == "fixture")


@app.get("/brainstorms/{brainstorm_id}", response_model=Brainstorm)
def get_brainstorm(brainstorm_id: str) -> Brainstorm:
    return _brainstorm_or_404(brainstorm_id)


@app.patch("/brainstorms/{brainstorm_id}", response_model=Brainstorm)
def patch_brainstorm(brainstorm_id: str, patch: BrainstormPatch) -> Brainstorm:
    d = _brainstorm_or_404(brainstorm_id)
    return store.save_brainstorm(d.model_copy(update=patch.model_dump(exclude_unset=True)))


@app.post("/brainstorms/{brainstorm_id}/messages", response_model=Brainstorm)
async def brainstorm_message(brainstorm_id: str, req: BrainstormMessageRequest) -> Brainstorm:
    return await brainstorms.send_message(await asyncio.to_thread(_brainstorm_or_404, brainstorm_id), req.text, req.urls)


@app.post("/brainstorms/{brainstorm_id}/generate", response_model=Job, status_code=201)
async def generate(brainstorm_id: str) -> Job:
    d = await asyncio.to_thread(_brainstorm_or_404, brainstorm_id)
    angle = next((a for a in d.angles if a.id == d.selected_angle_id), None)
    if not angle:
        raise HTTPException(422, "pick an angle first")
    job = orchestrator.new_job(secrets.token_hex(3), f"Brainstorm: {angle.title}", "brainstorm", d.id)
    await asyncio.to_thread(store.save_job, job)
    await asyncio.to_thread(store.save_brainstorm, d.model_copy(update={"job_id": job.id}))
    orchestrator.spawn(orchestrator.run_job(job.id, brainstorm=d))
    return job


# ---------------------------------------------------------------- dashboard & policy

@app.get("/metrics", response_model=list[Metric])
def metrics() -> list[Metric]:
    return seed.metrics(store.list_jobs(200))


@app.get("/instagram-accounts", response_model=list[InstagramAccount])
def instagram_accounts() -> list[InstagramAccount]:
    return seed.instagram_accounts()


@app.get("/policy-events")
def policy_events() -> dict:
    events = store.list_policy_events() + (seed.POLICY_SEED if settings.demo_mode == "fixture" else [])
    try:
        version = hashlib.sha256(settings.openshell_policy.read_bytes()).hexdigest()[:7]
    except OSError:
        version = "—"
    return {"stats": seed.policy_stats(events, version),
            "events": [e.model_dump() for e in events]}


@app.post("/reset")
def reset() -> dict:
    """Mock-backend QA endpoint; no-op on the real backend."""
    return {"ok": True}
