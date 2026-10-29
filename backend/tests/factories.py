"""Shared job builders for API and publish tests."""

import json
import secrets

from app.config import settings
from app.pipeline import orchestrator
from app.schemas import CardDeck, Job, JobStatus, SafeguardVerdict
from app.services import safeguard


def scenario_deck(name: str = "good") -> CardDeck:
    return CardDeck.model_validate(json.loads((settings.scenarios_dir / name / "deck.json").read_text()))


def allowed_job(prompt: str = "approve test") -> Job:
    """A READY_FOR_REVIEW job with the good deck and an "allow" safeguard verdict for exactly that deck."""
    job = orchestrator.new_job(secrets.token_hex(4), prompt)
    job.status = JobStatus.READY_FOR_REVIEW
    job.deck = scenario_deck("good")
    job.safeguard = SafeguardVerdict(decision="allow", summary="ok", content_hash=safeguard.content_hash(job.deck))
    return job
