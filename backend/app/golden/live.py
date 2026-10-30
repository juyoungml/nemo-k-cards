"""Live mode: run one case through the real orchestrator (research → … → review → safeguard) with the configured
runner.

Costs tokens: four `claude -p` stages per case. Never publishes; the job stops at READY_FOR_REVIEW (or BLOCKED) like
any other. `__main__` isolates the run before this is imported (scratch OUTPUT_DIR, and a scratch database once the
store is Postgres); `isolation_problem` refuses to start if that didn't happen.
"""

import json
import re
import secrets
import shutil
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import REPO_ROOT
from app.golden.cases import Case, Recording, recording_from_job

DEFAULT_OUTPUT_DIR = REPO_ROOT / "backend/.data/out"  # the Admin's data; live golden runs must never write here
SCRATCH_DB = re.compile(r"golden|test", re.IGNORECASE)


def db_name(url: str) -> str:
    return url.rsplit("/", 1)[-1].split("?", 1)[0]


def isolation_problem(settings: Any) -> str | None:
    """Why a live golden run must not start with these settings, or None. Works with the SQLite store (data next
    to OUTPUT_DIR) and the Postgres store (settings.database_url)."""
    if settings.demo_mode != "live":
        return "DEMO_MODE is not live"
    if settings.publish_mode != "mock":
        return f"PUBLISH_MODE is {settings.publish_mode}; golden runs use mock"
    if settings.supabase_service_key:  # Instagram tokens live in Vault, never in the scratch database (#21)
        return "publishing credentials are set"
    if hasattr(settings, "database_url"):
        if not SCRATCH_DB.search(db_name(settings.database_url)):
            return f"DATABASE_URL database '{db_name(settings.database_url)}' is not a golden/test database"
    elif Path(settings.output_dir).resolve() == DEFAULT_OUTPUT_DIR.resolve():
        return "OUTPUT_DIR is the Admin's data dir (the SQLite store lives next to it)"
    return None


def cli_version() -> str:
    claude = shutil.which("claude")
    if not claude:
        return ""
    try:
        out = subprocess.run([claude, "--version"], capture_output=True, text=True, timeout=30, check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out.split()[0] if out.strip() else ""


async def run_case(case: Case, set_version: int) -> Recording:
    from app import orgs, store
    from app.config import settings
    from app.pipeline import agent_runner, orchestrator

    if case.kind == "safeguard":
        raise ValueError(f"{case.id}: Safeguard cases replay a fixed Deck; there is no request to run live")
    if problem := isolation_problem(settings):
        raise RuntimeError(f"refusing a live golden run: {problem}")

    # The CLI reports which model(s) actually answered (`modelUsage`); keep that instead of trusting AGENT_MODEL.
    models: dict[str, int] = {}
    real_parse = agent_runner._parse

    def parse_and_note_model(stdout: bytes) -> Any:
        try:
            for name, usage in (json.loads(stdout).get("modelUsage") or {}).items():
                models[name] = models.get(name, 0) + int((usage or {}).get("outputTokens", 0))
        except (ValueError, AttributeError, TypeError):
            pass
        return real_parse(stdout)

    agent_runner._parse = parse_and_note_model
    try:
        # Each case runs as its own Organization in the scratch database, like any other Job.
        org = orgs.create_org(uuid.uuid4(), f"Golden {case.id}"[:100]).org_id
        kind = case.request_kind if case.kind == "guide" else (case.request_kind or "event")
        job = store.save_job(org, orchestrator.new_job(f"golden-{secrets.token_hex(3)}", case.prompt, kind=kind))
        today = datetime.now(orchestrator.KST).date()
        await orchestrator.run_job(org, job.id)
    finally:
        agent_runner._parse = real_parse
    return recording_from_job(store.get_job(org, job.id), case, set_version, today, models,
                              cli_version() if settings.agent_runner == "local" else "", settings.agent_runner)
