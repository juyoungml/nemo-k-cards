"""Shared Job builders for API and publish tests."""

import json
import secrets

from app.config import settings
from app.pipeline import orchestrator
from app.schemas import CardDeck, Job, JobStatus, SafeguardVerdict
from app.services import safeguard


def scenario_deck(name: str = "good") -> CardDeck:
    return CardDeck.model_validate(json.loads((settings.scenarios_dir / name / "deck.json").read_text()))


def allowed(job: Job, deck: CardDeck | None = None) -> Job:
    """Give `job` a Deck (the good scenario's unless given) and an "allow" Safeguard verdict for exactly that Deck,
    as if the pipeline had finished. Without a verdict a Job can't be approved."""
    job.deck = deck or job.deck or scenario_deck("good")
    job.safeguard = SafeguardVerdict(decision="allow", summary="Safe to post.", checked_by=["rules", "model"],
                                     model_decision="allow", content_hash=safeguard.content_hash(job.deck))
    return job


def allowed_job(prompt: str = "approve test") -> Job:
    """A READY_FOR_REVIEW Job with the good Deck and an "allow" verdict for exactly that Deck (not saved)."""
    job = orchestrator.new_job(secrets.token_hex(4), prompt)
    job.status = JobStatus.READY_FOR_REVIEW
    return allowed(job)


def blocked_job(summary: str = "Mocks the 5·18 Gwangju Uprising.") -> Job:
    """A Job the Safeguard blocked (not saved)."""
    job = allowed_job("blocked test")
    job.status = JobStatus.BLOCKED
    job.safeguard = job.safeguard.model_copy(update={"decision": "block", "model_decision": "block",
                                                     "summary": summary})
    return job
