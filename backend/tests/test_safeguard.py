"""Safeguard: the hard gate. Rules + model, fail closed, explanations, and no way around it at approve/publish."""

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import store
from app.config import settings
from app.main import app
from app.pipeline import orchestrator
from app.schemas import CardDeck, JobStatus, SafeguardFinding, SafeguardModelOutput
from app.services import safeguard
from tests.test_pipeline_fixture import allowed_job


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(orchestrator, "FIXTURE_DELAY", 0)


def deck(name: str) -> CardDeck:
    return CardDeck.model_validate(json.loads((settings.scenarios_dir / name / "deck.json").read_text()))


def model_says(decision: str, *findings: SafeguardFinding, summary: str = "model summary"):
    async def call(payload, files):
        call.payload = payload
        return SafeguardModelOutput(decision=decision, summary=summary, findings=list(findings))
    return call


async def check(d: CardDeck, model):
    return await safeguard.check(d, slide_paths=[], today=date(2026, 10, 29), model=model)


# ---------------------------------------------------------------- rules

def test_policy_files_parse_and_have_block_phrases():
    p = safeguard.load_policy()
    assert len(p.phrases) >= 5 and len(p.version) == 7
    assert "Block" in p.markdown


@pytest.mark.parametrize(("text", "where"), [
    ("Join our TANK DAY event", "slide"),
    ("책상을 탁 치니", "slide"),
    ("Cruise on the Sea of Japan", "slide"),
])
def test_rule_phrases_are_caught_in_slides(text, where):
    d = deck("good")
    d.slides[2].body = text
    [f] = safeguard.rule_findings(d)
    assert f.severity == "block" and f.location == where and f.slide_index == 2
    assert f.explanation and f.fix


def test_rule_phrases_are_caught_in_caption_and_hashtags():
    d = deck("good")
    d.caption = "A day in Seoul\n#seoul #tankday"
    [f] = safeguard.rule_findings(d)
    assert f.location == "hashtags" and f.excerpt.lower() == "tankday"


def test_good_deck_has_no_rule_hits():
    assert safeguard.rule_findings(deck("good")) == []


# ---------------------------------------------------------------- merging

async def test_rule_hit_blocks_even_when_the_model_allows():
    v = await check(deck("bad"), model_says("allow", summary="Looks fine."))
    assert v.decision == "block"
    assert v.summary.startswith('Blocked phrase "Tank Day"')
    assert v.checked_by == ["rules", "model"]


async def test_model_block_finding_blocks_even_if_its_decision_says_allow():
    f = SafeguardFinding(category="hate", severity="block", location="caption", excerpt="x", rule="hate",
                         explanation="Demeans a nationality.")
    v = await check(deck("good"), model_says("allow", f, summary=""))
    assert v.decision == "block" and v.summary == "Blocked: Demeans a nationality."


async def test_warn_only_is_allowed():
    f = SafeguardFinding(category="other", severity="warn", location="caption", excerpt="x", rule="taste",
                         explanation="Edgy.")
    v = await check(deck("good"), model_says("allow", f, summary="Safe, one warning."))
    assert v.decision == "allow" and v.summary == "Safe, one warning." and len(v.findings) == 1


async def test_model_failure_fails_closed():
    async def broken(payload, files):
        raise RuntimeError("safeguard timed out after 300s")
    v = await check(deck("good"), broken)
    assert v.decision == "block" and "could not run" in v.summary
    assert v.model_error.startswith("safeguard timed out") and v.checked_by == ["rules"]


async def test_model_gets_policy_and_rule_hits():
    m = model_says("block")
    await check(deck("bad"), m)
    assert m.payload["rule_findings"][0]["excerpt"] == "Tank Day"
    assert "phrases:" in m.payload["sensitive_topics_yaml"] and m.payload["today"] == "2026-10-29"


async def test_verdict_is_tied_to_the_content():
    d = deck("good")
    v = await check(d, None)
    assert v.content_hash == safeguard.content_hash(d)
    assert safeguard.content_hash(d.model_copy(update={"caption": d.caption + "!"})) != v.content_hash


# ---------------------------------------------------------------- approve / reject / publish

def blocked_job():
    job = allowed_job()
    job.status = JobStatus.BLOCKED
    job.safeguard = job.safeguard.model_copy(update={"decision": "block", "summary": "Mocks 5·18."})
    return store.save_job(job)


def test_blocked_job_cannot_be_approved_but_can_be_rejected(monkeypatch):
    called = []

    async def fake_publish(*a):
        called.append(a)
    monkeypatch.setattr(orchestrator, "publish_job", fake_publish)
    c = TestClient(app)
    job = blocked_job()
    r = c.post(f"/jobs/{job.id}/approve", json={})
    assert r.status_code == 409 and "Mocks 5·18." in r.json()["detail"]
    assert called == []
    assert c.post(f"/jobs/{job.id}/reject", json={"reason": "harmful"}).json()["status"] == "REJECTED"


def test_block_verdict_wins_even_if_status_says_ready(monkeypatch):
    monkeypatch.setattr(orchestrator, "publish_job", lambda *a: None)
    job = blocked_job()
    job.status = JobStatus.READY_FOR_REVIEW
    store.save_job(job)
    assert TestClient(app).post(f"/jobs/{job.id}/approve", json={}).status_code == 409


def test_job_without_a_verdict_cannot_be_approved():
    job = allowed_job()
    job.safeguard = None
    store.save_job(job)
    r = TestClient(app).post(f"/jobs/{job.id}/approve", json={})
    assert r.status_code == 409 and "no safeguard verdict" in r.json()["detail"]


def test_edited_caption_with_a_blocked_phrase_is_refused_at_approve(monkeypatch):
    monkeypatch.setattr(orchestrator, "publish_job", lambda *a: None)
    job = store.save_job(allowed_job())
    r = TestClient(app).post(f"/jobs/{job.id}/approve", json={"caption": "탱크데이 특가! #seoul"})
    assert r.status_code == 422 and "blocked by safeguard" in r.json()["detail"]


async def test_publish_rechecks_an_edited_caption_and_blocks(monkeypatch):
    job = store.save_job(allowed_job())
    # Simulates a caption that slips past the fast rules at approve but the full check blocks.
    async def blocking(*a, **k):
        return await safeguard.check(store.get_job(job.id).deck, slide_paths=[], today=date(2026, 10, 29),
                                     model=model_says("block", summary="Sexualised framing of a venue."))
    monkeypatch.setattr(orchestrator, "run_safeguard", blocking)
    await orchestrator.publish_job(job.id, "Edited caption #seoul", "mock")
    job = store.get_job(job.id)
    assert job.status == JobStatus.BLOCKED and job.published_url is None
    assert job.publish_progress.step == "failed" and "Sexualised" in job.publish_progress.error
    assert job.safeguard.summary == "Sexualised framing of a venue."


async def test_publish_with_unchanged_text_skips_the_recheck(monkeypatch):
    job = store.save_job(allowed_job())

    async def boom(*a, **k):
        raise AssertionError("should not re-check unchanged content")
    monkeypatch.setattr(orchestrator, "run_safeguard", boom)
    await orchestrator.publish_job(job.id, None, "mock")
    assert store.get_job(job.id).status == JobStatus.PUBLISHED


async def test_publish_with_a_safe_edit_rechecks_and_posts():
    job = store.save_job(allowed_job())
    await orchestrator.publish_job(job.id, "Three easy things to do in Seoul. #seoul", "mock")
    job = store.get_job(job.id)
    assert job.status == JobStatus.PUBLISHED
    assert job.safeguard.content_hash == safeguard.content_hash(job.deck)
    assert any("checking again" in line.message for line in job.log)
