"""Safeguard: the hard gate. Rules + model, fail closed, explanations, and no way around it at approve/publish."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import store
from app.config import settings
from app.main import app
from app.pipeline import orchestrator
from app.schemas import CardDeck, JobStatus, SafeguardFinding, SafeguardModelOutput
from app.services import safeguard
from tests.factories import allowed_job, scenario_deck


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(orchestrator, "FIXTURE_DELAY", 0)


deck = scenario_deck


def model_says(decision: str, *findings: SafeguardFinding, summary: str = "model summary"):
    async def call(payload, files):
        call.payload = payload
        return SafeguardModelOutput(decision=decision, summary=summary, findings=list(findings))
    return call


async def check(d: CardDeck, model):
    return await safeguard.check(d, slide_paths=[], today=date(2026, 10, 29), model=model)


# ---------------------------------------------------------------- rules

def test_policy_files_parse_and_every_block_phrase_has_a_category():
    p = safeguard.load_policy()
    assert len(p.phrases) >= 5 and len(p.version) == 7
    assert "Block" in p.markdown
    cats = {ph.note: ph.category for ph in p.phrases}
    assert cats["use East Sea / Dokdo"] == "territorial_cultural"
    assert set(cats.values()) <= {"historical_tragedy", "territorial_cultural"}


def test_a_block_phrase_without_a_valid_category_fails_loudly(tmp_path, monkeypatch):
    bad = tmp_path / "topics.yaml"
    bad.write_text('phrases:\n  - { pattern: "x", severity: block, category: nonsense }\n')
    monkeypatch.setattr(safeguard, "SENSITIVE_YAML", bad)
    with pytest.raises(ValueError):
        safeguard.load_policy()


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
    assert f.category == "historical_tragedy"


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


def test_hash_covers_image_choices_and_rendered_slides(tmp_path):
    d = deck("good")
    base = safeguard.content_hash(d)
    swapped = d.model_copy(deep=True)
    swapped.slides[1].stock_id = "festival"
    assert safeguard.content_hash(swapped) != base
    f = tmp_path / "slide-01.jpg"
    f.write_bytes(b"one")
    first = safeguard.content_hash(d, [f])
    f.write_bytes(b"two")
    assert safeguard.content_hash(d, [f]) != first != base


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


def test_edited_caption_with_a_blocked_phrase_is_refused_at_approve_and_explained(monkeypatch):
    monkeypatch.setattr(orchestrator, "publish_job", lambda *a: None)
    job = store.save_job(allowed_job())
    r = TestClient(app).post(f"/jobs/{job.id}/approve", json={"caption": "탱크데이 특가! #seoul"})
    assert r.status_code == 422 and "blocked by safeguard" in r.json()["detail"]
    saved = store.get_job(job.id)
    assert saved.status == JobStatus.READY_FOR_REVIEW and saved.deck.caption == job.deck.caption
    [f] = saved.blocked_edit.verdict.findings
    assert f.location == "caption" and f.excerpt == "탱크데이" and f.explanation


async def test_blocked_caption_edit_keeps_the_original_deck(monkeypatch):
    job = store.save_job(allowed_job())
    original = job.deck.caption

    # A caption that passes the fast rules at approve but that the full check blocks.
    async def blocking(job_id, deck, paths, fx=None, *, use_model=True):
        return await safeguard.check(deck, slide_paths=paths, today=date(2026, 10, 29),
                                     model=model_says("block", summary="Sexualised framing of a venue."))
    monkeypatch.setattr(orchestrator, "run_safeguard", blocking)
    await orchestrator.publish_job(job.id, "Edited caption #seoul", "mock")
    job = store.get_job(job.id)
    assert job.status == JobStatus.READY_FOR_REVIEW and job.published_url is None
    assert job.deck.caption == original and job.safeguard.decision == "allow"
    assert job.blocked_edit.caption == "Edited caption #seoul"
    assert job.blocked_edit.verdict.summary == "Sexualised framing of a venue."
    assert job.publish_progress.step == "failed" and "Sexualised" in job.publish_progress.error
    # The original Deck is still approvable.
    monkeypatch.setattr(orchestrator, "run_safeguard", None)  # unchanged content must not be re-checked
    await orchestrator.publish_job(job.id, None, "mock")
    assert store.get_job(job.id).status == JobStatus.PUBLISHED


async def test_unverifiable_deck_is_blocked_at_publish():
    job = allowed_job()
    job.deck.slides[1].body = "Tank Day sale"  # changed after the check, not through a caption edit
    store.save_job(job)
    await orchestrator.publish_job(job.id, None, "mock")
    job = store.get_job(job.id)
    assert job.status == JobStatus.BLOCKED and job.published_url is None
    assert job.safeguard.decision == "block"


def test_graph_post_of_an_edit_is_refused_on_a_fixture_server(monkeypatch):
    called = []

    async def fake_publish(*a):
        called.append(a)
    monkeypatch.setattr(orchestrator, "publish_job", fake_publish)
    monkeypatch.setattr(settings, "publish_mode", "graph")
    job = store.save_job(allowed_job())
    r = TestClient(app).post(f"/jobs/{job.id}/approve", json={"caption": "Safe new caption #seoul"})
    assert r.status_code == 409 and "safeguard model" in r.json()["detail"]
    assert called == []


async def test_publish_refuses_a_graph_post_of_unchecked_content_on_a_fixture_server(monkeypatch):
    posted = []

    async def fake_upload(*a, **k):
        posted.append(a)
        return []
    monkeypatch.setattr(orchestrator.image_host, "publish_images", fake_upload)
    job = store.save_job(allowed_job())
    await orchestrator.publish_job(job.id, "Safe new caption #seoul", "graph")
    job = store.get_job(job.id)
    assert posted == [] and job.status == JobStatus.READY_FOR_REVIEW and job.published_url is None
    assert "can't check this edit" in job.publish_progress.label


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
