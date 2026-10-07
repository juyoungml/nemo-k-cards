"""FastAPI entrypoint. Routes follow SPEC §12 / BACKEND §4 and match apps/web/src/lib/api.ts."""

import asyncio
import secrets

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from app import drafts, seed, store
from app.config import settings
from app.pipeline import orchestrator
from app.schemas import (
    TERMINAL,
    ApproveRequest,
    Channel,
    CreateDraftRequest,
    CreateJobRequest,
    Draft,
    DraftMessageRequest,
    DraftPatch,
    Job,
    JobStatus,
    Metric,
    RejectRequest,
)

app = FastAPI(title="What's On Korea API")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
                   allow_methods=["*"], allow_headers=["*"])

# Rendered slides, publicly reachable via PUBLIC_ASSET_BASE_URL so Instagram can fetch them.
settings.output_dir.mkdir(parents=True, exist_ok=True)
app.mount("/assets", StaticFiles(directory=settings.output_dir), name="assets")


def _job_or_404(job_id: str) -> Job:
    if not (job := store.get_job(job_id)):
        raise HTTPException(404, "job not found")
    return job


def _draft_or_404(draft_id: str) -> Draft:
    if not (d := store.get_draft(draft_id)):
        raise HTTPException(404, "draft not found")
    return d


@app.get("/health")
def health() -> dict:
    return {"ok": True, "demo_mode": settings.demo_mode, "agent_runner": settings.agent_runner,
            "publish_mode": settings.publish_mode}


# ---------------------------------------------------------------- jobs

@app.post("/jobs", response_model=Job, status_code=201)
async def create_job(req: CreateJobRequest) -> Job:
    job = store.save_job(orchestrator.new_job(secrets.token_hex(3), req.prompt.strip()))
    orchestrator.spawn(orchestrator.run_job(job.id, scenario=req.scenario or "good"))
    return job


@app.get("/jobs", response_model=list[Job])
def list_jobs(limit: int = 50) -> list[Job]:
    return store.list_jobs(limit)


@app.get("/jobs/{job_id}", response_model=Job)
def get_job(job_id: str) -> Job:
    return _job_or_404(job_id)


@app.get("/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request) -> StreamingResponse:
    """SSE: a full Job snapshot whenever it changes, until a terminal state (same as the mock backend)."""
    _job_or_404(job_id)

    async def stream():
        last = ""
        while not await request.is_disconnected():
            job = store.get_job(job_id)
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
    job = _job_or_404(job_id)
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
    orchestrator.spawn(orchestrator.publish_job(job_id, req.caption, req.mode or settings.publish_mode))
    job.status = JobStatus.PUBLISHING
    return job


@app.post("/jobs/{job_id}/reject", response_model=Job)
def reject(job_id: str, req: RejectRequest) -> Job:
    job = _job_or_404(job_id)
    if job.status != JobStatus.READY_FOR_REVIEW:
        raise HTTPException(409, f"job is {job.status}, not READY_FOR_REVIEW")
    job.status, job.error = JobStatus.REJECTED, req.reason or "rejected by operator"
    return store.save_job(job)


# ---------------------------------------------------------------- drafts (Brainstorm)

@app.post("/drafts", response_model=Draft, status_code=201)
def create_draft(req: CreateDraftRequest | None = None) -> Draft:
    return drafts.create(req.sample if req else True)


@app.get("/drafts/{draft_id}", response_model=Draft)
def get_draft(draft_id: str) -> Draft:
    return _draft_or_404(draft_id)


@app.patch("/drafts/{draft_id}", response_model=Draft)
def patch_draft(draft_id: str, patch: DraftPatch) -> Draft:
    d = _draft_or_404(draft_id)
    return store.save_draft(d.model_copy(update=patch.model_dump(exclude_unset=True)))


@app.post("/drafts/{draft_id}/messages", response_model=Draft)
async def draft_message(draft_id: str, req: DraftMessageRequest) -> Draft:
    return await drafts.send_message(_draft_or_404(draft_id), req.text, req.urls)


@app.post("/drafts/{draft_id}/generate", response_model=Job, status_code=201)
async def generate(draft_id: str) -> Job:
    d = _draft_or_404(draft_id)
    angle = next((a for a in d.angles if a.id == d.selected_angle_id), None)
    if not angle:
        raise HTTPException(422, "pick an angle first")
    job = orchestrator.new_job(secrets.token_hex(3), f"Brainstorm: {angle.title}", "brainstorm", d.id)
    store.save_job(job)
    store.save_draft(d.model_copy(update={"job_id": job.id}))
    orchestrator.spawn(orchestrator.run_job(job.id, draft=d))
    return job


# ---------------------------------------------------------------- dashboard & policy

@app.get("/metrics", response_model=list[Metric])
def metrics() -> list[Metric]:
    return seed.metrics(store.list_jobs(200))


@app.get("/channels", response_model=list[Channel])
def channels() -> list[Channel]:
    return seed.CHANNELS


@app.get("/policy-events")
def policy_events() -> dict:
    events = store.list_policy_events() + seed.POLICY_SEED
    return {"stats": seed.policy_stats(events, len(store.list_jobs(200))),
            "events": [e.model_dump() for e in events]}


@app.post("/reset")
def reset() -> dict:
    """Mock-backend QA endpoint; no-op on the real backend."""
    return {"ok": True}
