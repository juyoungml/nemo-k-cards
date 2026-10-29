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


def connect_account(org_id, username: str = "seoulbrand") -> dict:
    """A connected Instagram account for `org_id` with a random token in Vault (the shim in tests), as the connect
    flow (#15) would leave it. Returns {"id", "ig_user_id", "username", "handle", "token"}."""
    from app import store
    from app.secret_store import secrets as secret_store

    ig_user_id = "1784" + str(secrets.randbelow(10**13)).zfill(13)
    token = "IGtest" + secrets.token_hex(16)
    with store.system() as c:
        ref = secret_store.put(c, f"instagram/{org_id}/{ig_user_id}", token, "test token")
        creator = c.execute("select user_id from members where org_id = %s order by created_at limit 1",
                            (org_id,)).fetchone()[0]
        (acct,) = c.execute("insert into instagram_accounts (org_id, ig_user_id, username, account_type, token_ref, "
                            "token_expires_at, connected_by) values (%s, %s, %s, 'BUSINESS', %s, "
                            "now() + interval '60 days', %s) returning id",
                            (org_id, ig_user_id, username, ref, creator)).fetchone()
    return {"id": acct, "ig_user_id": ig_user_id, "username": username, "handle": f"@{username}", "token": token}


def blocked_job(summary: str = "Mocks the 5·18 Gwangju Uprising.") -> Job:
    """A Job the Safeguard blocked (not saved)."""
    job = allowed_job("blocked test")
    job.status = JobStatus.BLOCKED
    job.safeguard = job.safeguard.model_copy(update={"decision": "block", "model_decision": "block",
                                                     "summary": summary})
    return job
