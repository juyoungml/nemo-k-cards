"""Golden set formats: the versioned cases and the recorded pipeline outputs they are checked against.

A case is a Prompt plus what a good result looks like. A recording is what one run of the pipeline produced
for that case (briefs, link checks, Deck, Issues), trimmed to what the checks read. Bump `version` in
cases.json when a case's prompt or expectations change; recordings made for an older version are reported stale.
"""

import hashlib
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from app.config import REPO_ROOT
from app.schemas import Issue, Job, JobStatus

GOLDEN_DIR = REPO_ROOT / "backend/golden"
CASES_FILE = GOLDEN_DIR / "cases.json"
RECORDINGS_DIR = GOLDEN_DIR / "recordings"
# What the agents are told. A recording made with other prompts no longer says how today's prompts behave.
PROMPT_FILES = sorted((REPO_ROOT / "agent/.claude/agents").glob("*.md")) + [REPO_ROOT / "agent/CLAUDE.md"]


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
    cases: list[Case]


class Recording(BaseModel):
    """One pipeline run for a case. Briefs and Deck stay raw so a schema break is a reported check, not a crash."""
    case_id: str
    set_version: int
    provenance: Literal["live", "demo-scenario", "synthetic"]
    recorded_at: datetime
    today: date                     # the run's "today" (Asia/Seoul); date windows are checked against it
    model: str = ""                 # AGENT_MODEL at record time; "" = CLI default
    prompts_sha: str = ""           # prompts_sha() at record time; "" for non-live recordings
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


def prompts_sha() -> str:
    h = hashlib.sha256()
    for f in PROMPT_FILES:
        h.update(f.name.encode() + b"\0" + f.read_bytes() + b"\0")
    return h.hexdigest()[:16]


def _local_name(url: str) -> str:
    """Local file URLs (vetted photo copies, stock files) differ per machine; keep only the file name."""
    return "local:" + url.rsplit("/", 1)[-1] if url.startswith("file:") else url


def recording_from_job(job: Job, case: Case, set_version: int, today: date, model: str) -> Recording:
    deck = job.deck.model_dump(mode="json") if job.deck else None
    for sl in (deck or {}).get("slides", []):
        if sl.get("image"):
            sl["image"]["url"] = _local_name(sl["image"]["url"])
    return Recording(
        case_id=case.id, set_version=set_version, provenance="live", recorded_at=datetime.now().astimezone(),
        today=today, model=model, prompts_sha=prompts_sha(), prompt=job.prompt, status=job.status,
        error=job.error, briefs=[b.model_dump(mode="json") for b in job.briefs],
        verification=job.verification.model_dump(mode="json") if job.verification else None, deck=deck,
        issues=job.issues, log=[f"{line.stage}: {line.message}" for line in job.log])
