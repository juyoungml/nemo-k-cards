"""FastAPI entrypoint. Routes follow SPEC §12."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.schemas import CreateJobRequest, Job

app = FastAPI(title="What's On Korea API")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000"],
                   allow_methods=["*"], allow_headers=["*"])

# Rendered slides, publicly reachable via PUBLIC_ASSET_BASE_URL so Instagram can fetch them.
settings.output_dir.mkdir(parents=True, exist_ok=True)
app.mount("/assets", StaticFiles(directory=settings.output_dir), name="assets")


@app.get("/health")
def health() -> dict:
    return {"ok": True, "demo_mode": settings.demo_mode, "agent_runner": settings.agent_runner}


@app.post("/jobs", response_model=Job)
async def create_job(req: CreateJobRequest) -> Job:
    raise NotImplementedError  # TODO: create job, start pipeline.orchestrator.run_job in background


@app.get("/jobs", response_model=list[Job])
def list_jobs() -> list[Job]:
    raise NotImplementedError


@app.get("/jobs/{job_id}", response_model=Job)
def get_job(job_id: str) -> Job:
    raise NotImplementedError


@app.get("/jobs/{job_id}/events")
async def job_events(job_id: str):
    raise NotImplementedError  # TODO: SSE stream of status changes


@app.post("/jobs/{job_id}/approve", response_model=Job)
async def approve(job_id: str) -> Job:
    """The ONLY publish path: human approves in Admin -> host calls services.publisher."""
    raise NotImplementedError


@app.post("/jobs/{job_id}/reject", response_model=Job)
def reject(job_id: str, reason: str = "") -> Job:
    raise NotImplementedError


@app.get("/policy-events")
def policy_events() -> list[dict]:
    raise NotImplementedError  # TODO: parse `openshell logs <sandbox> --source sandbox`
