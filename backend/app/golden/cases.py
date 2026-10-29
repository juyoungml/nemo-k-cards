"""Golden set formats: the versioned cases and the recorded pipeline outputs they are checked against.

A case is a Prompt plus what a good result looks like. A recording is what one run of the pipeline produced
for that case (briefs, link checks, Deck, Issues), trimmed to what the checks read. Bump `version` in
cases.json when a case's prompt or expectations change; recordings made for an older version are reported stale.
"""

import ast
import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, TypeAdapter

from app import schemas
from app.config import REPO_ROOT
from app.schemas import Issue, Job, JobStatus

GOLDEN_DIR = REPO_ROOT / "backend/golden"
CASES_FILE = GOLDEN_DIR / "cases.json"
RECORDINGS_DIR = GOLDEN_DIR / "recordings"
AGENT_DIR = REPO_ROOT / "agent"
ORCHESTRATOR = REPO_ROOT / "backend/app/pipeline/orchestrator.py"
AGENT_RUNNER = REPO_ROOT / "backend/app/pipeline/agent_runner.py"


class Expect(BaseModel):
    """What a good result for the case looks like. Empty lists / None mean "not checked"."""
    outcome: Literal["ready", "no_deck"] = "ready"  # no_deck: the job stops before copy (nothing found / all links dead)
    min_events: int = 1
    max_events: int = 6
    categories_any: list[str] = Field(default_factory=list)       # at least one event has one of these categories
    every_event_mentions_any: list[str] = Field(default_factory=list)  # each event's place fields name one of these
    no_event_mentions: list[str] = Field(default_factory=list)    # no event's place fields name any of these
    window_days: int | None = None  # every event runs within [today, today + window_days]


class Case(BaseModel):
    id: str
    kind: Literal["event", "guide"] = "event"  # guide: Guide Decks (#27), not produced by the pipeline yet
    prompt: str
    why: str = ""
    expect: Expect = Field(default_factory=Expect)


class GoldenSet(BaseModel):
    version: int
    model: str = ""  # the resolved model id recordings are expected to come from; replay flags others as stale
    cases: list[Case]


class Recording(BaseModel):
    """One pipeline run for a case. Briefs and Deck stay raw so a schema break is a reported check, not a crash."""
    case_id: str
    set_version: int
    provenance: Literal["live", "demo-scenario", "synthetic"]
    recorded_at: datetime
    today: date                     # the run's "today" (Asia/Seoul); date windows are checked against it
    model: str = ""                 # resolved model id the stages ran on (most output tokens), from the CLI
    models: list[str] = Field(default_factory=list)  # every model the CLI reported (sub-calls may use others)
    cli_version: str = ""           # `claude --version` of the local runner; "" when unknown (OpenShell image)
    runner: str = ""                # AGENT_RUNNER at record time
    agent_sha: str = ""             # agent_sha() at record time; "" for fixture recordings
    prompt: str
    status: JobStatus
    error: str | None = None
    briefs: list[dict] = Field(default_factory=list)
    verification: dict | None = None
    deck: dict | None = None
    issues: list[Issue] = Field(default_factory=list)  # host + QA + reviewer, as shown in Review
    log: list[str] = Field(default_factory=list)


def load_set(path: Path = CASES_FILE) -> GoldenSet:
    gs = GoldenSet.model_validate_json(path.read_text(encoding="utf-8"))
    ids = [c.id for c in gs.cases]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate case ids in {path.name}")
    return gs


def load_recording(case_id: str, directory: Path = RECORDINGS_DIR) -> Recording | None:
    p = directory / f"{case_id}.json"
    return Recording.model_validate_json(p.read_text(encoding="utf-8")) if p.exists() else None


def save_recording(rec: Recording, directory: Path = RECORDINGS_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / f"{rec.case_id}.json"
    p.write_text(rec.model_dump_json(indent=2, exclude_none=True) + "\n", encoding="utf-8")
    return p


# The order the stage calls are hashed in. Fixed by agent name, so moving a call within the orchestrator (e.g. into
# its own step function, #17) doesn't change the hash; this is the order the hash has always used.
STAGE_ORDER = ["copywriter", "reviewer", "safeguard", "researcher"]


def _stage_calls() -> list[ast.Call]:
    tree = ast.parse(ORCHESTRATOR.read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "run_stage"]

    def key(call: ast.Call) -> tuple[int, str]:
        agent = getattr(call.args[0], "value", "")
        return (STAGE_ORDER.index(agent) if agent in STAGE_ORDER else len(STAGE_ORDER), agent)
    return sorted(calls, key=key)


def agent_sha() -> str:
    """Everything that decides what the agents do, so a recording made under anything else is reported stale:
    all of agent/** (prompts, subagents, skills), each stage's task text and output type in the orchestrator,
    the JSON schema of those output types, and the per-agent tool lists. Read from source, not imported, so
    computing it never opens the store."""
    h = hashlib.sha256()
    for f in sorted(p for p in AGENT_DIR.rglob("*") if p.is_file() and p.name != ".DS_Store"):
        h.update(str(f.relative_to(AGENT_DIR)).encode() + b"\0" + f.read_bytes() + b"\0")
    for call in _stage_calls():
        h.update(ast.dump(call.args[0]).encode() + ast.dump(call.args[1]).encode())
        # A type expression from our own orchestrator source (e.g. `list[EventBrief]`), resolved in app.schemas.
        out_type = eval(compile(ast.Expression(call.args[3]), "<stage>", "eval"), vars(schemas))
        h.update(json.dumps(TypeAdapter(out_type).json_schema(), sort_keys=True).encode())
    tools = next(n for n in ast.parse(AGENT_RUNNER.read_text(encoding="utf-8")).body
                 if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "TOOLS")
    h.update(ast.dump(tools.value).encode())
    return h.hexdigest()[:16]


def _local_name(url: str) -> str:
    """Local file URLs (vetted photo copies, stock files) differ per machine; keep only the file name."""
    return "local:" + url.rsplit("/", 1)[-1] if url.startswith("file:") else url


def recording_from_job(job: Job, case: Case, set_version: int, today: date, models: dict[str, int],
                       cli_version: str, runner: str) -> Recording:
    """`models`: model id → output tokens, as the CLI reported them across the run's stages."""
    deck = job.deck.model_dump(mode="json") if job.deck else None
    for sl in (deck or {}).get("slides", []):
        if sl.get("image"):
            sl["image"]["url"] = _local_name(sl["image"]["url"])
    return Recording(
        case_id=case.id, set_version=set_version, provenance="live", recorded_at=datetime.now().astimezone(),
        today=today, model=max(models, key=models.get, default=""), models=sorted(models),
        cli_version=cli_version, runner=runner, agent_sha=agent_sha(), prompt=job.prompt, status=job.status,
        error=job.error, briefs=[b.model_dump(mode="json") for b in job.briefs],
        verification=job.verification.model_dump(mode="json") if job.verification else None, deck=deck,
        issues=job.issues, log=[f"{line.stage}: {line.message}" for line in job.log])
