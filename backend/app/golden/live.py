"""Live mode: run one case through the real orchestrator (research → … → review) with the configured runner.

Costs tokens: three `claude -p` stages per case. Never publishes; the job stops at READY_FOR_REVIEW like any
other. `__main__` points OUTPUT_DIR (slides, SQLite) at a scratch directory before this is imported, so golden
jobs don't show up in the Admin.
"""

import secrets
from datetime import datetime

from app import store
from app.config import settings
from app.golden.cases import Case, Recording, recording_from_job
from app.pipeline import orchestrator


async def run_case(case: Case, set_version: int) -> Recording:
    if case.kind != "event":
        raise ValueError(f"{case.id}: the pipeline only makes Event Decks so far (#27)")
    assert settings.demo_mode == "live" and settings.publish_mode != "graph"
    job = store.save_job(orchestrator.new_job(f"golden-{secrets.token_hex(3)}", case.prompt))
    today = datetime.now(orchestrator.KST).date()
    await orchestrator.run_job(job.id)
    return recording_from_job(store.get_job(job.id), case, set_version, today, settings.agent_model)
