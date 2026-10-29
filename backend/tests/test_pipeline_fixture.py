"""DEMO_MODE=fixture runs of the orchestrator: scenarios (good / bad / fail) and Brainstorm verification.

Render and QA run for real, so these need Playwright's Chromium (`uv run playwright install chromium`).
"""

import functools
import secrets
import uuid
from datetime import date

import pytest
from factories import allowed_job

from app import brainstorms, orgs, posts, store
from app.pipeline import orchestrator, publishing
from app.schemas import Fact, JobStatus, LinkCheck
from app.services import link_checker


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(orchestrator, "FIXTURE_DELAY", 0)


@pytest.fixture
def no_network(monkeypatch):
    """Any URL not covered by a fixture gets this answer instead of a real request."""
    answers: dict[str, LinkCheck] = {}

    async def fake_check(url, client=None):
        return answers.get(url) or LinkCheck(url=url, status="dead", http_code=404)
    monkeypatch.setattr(link_checker, "check_url", fake_check)
    return answers


@functools.cache
def org() -> uuid.UUID:
    """The Organization these pipeline runs belong to."""
    return orgs.create_org(uuid.uuid4(), "Pipeline tests").org_id


async def run_quick(scenario: str):
    job = store.save_job(org(), orchestrator.new_job(secrets.token_hex(3), "test prompt"))
    await orchestrator.run_job(org(), job.id, scenario=scenario)
    return store.get_job(org(), job.id)


async def run_brainstorm(brainstorm):
    store.save_brainstorm(org(), brainstorm)
    job = store.save_job(org(), orchestrator.new_job(secrets.token_hex(3), "bs", "brainstorm", brainstorm.id))
    await orchestrator.run_job(org(), job.id, brainstorm=brainstorm)
    return store.get_job(org(), job.id)


# ---------------------------------------------------------------- scenarios

async def test_good_scenario_is_ready(no_network):
    job = await run_quick("good")
    assert job.status == JobStatus.READY_FOR_REVIEW
    assert job.safeguard.decision == "allow" and job.safeguard.findings == []
    assert job.safeguard.checked_by == ["rules", "model"]
    assert len(job.slide_urls) == len(job.deck.slides)
    assert set(job.verification.excluded_event_ids) == {"ev-kpop-giveaway", "ev-hongdae-character"}


async def test_bad_scenario_is_blocked_by_the_safeguard(no_network):
    job = await run_quick("bad")
    # The Safeguard is the one binding check (ADR 0004): "Tank Day" on 5·18 can't be approved by anyone.
    assert job.status == JobStatus.BLOCKED
    sg = job.safeguard
    assert sg.decision == "block" and sg.checked_by == ["rules", "model"] and sg.model_decision == "block"
    assert {f.excerpt for f in sg.findings} >= {"Tank Day", "Celebrate with 51.8% off!"}
    assert sg.summary.startswith('Blocked phrase "Tank Day"')
    assert all(f.explanation and f.fix and f.rule for f in sg.findings)
    states = {s.name: s.state for s in job.pipeline}
    assert states["Safeguard"] == "failed" and states["Human publish"] == "skipped"
    # The advisory checks still ran and are shown next to the verdict.
    assert any(i.severity == "block" for i in job.issues)
    cats = {(i.category, i.severity) for i in job.issues}
    assert ("sensitive", "block") in cats
    assert ("pii", "block") in cats  # phone number on slide 2, found by host QA (not the fixture)
    events = [e for e in store.list_policy_events(org()) if e.job_id == job.id]
    assert {(e.host, e.result) for e in events} >= {("graph.facebook.com", "policy_denied"),
                                                    ("pastebin.com", "policy_denied")}


async def test_fail_scenario_fails_at_render(no_network):
    job = await run_quick("fail")
    assert job.status == JobStatus.FAILED
    assert "timed out" in job.error
    states = {s.name: s.state for s in job.pipeline}
    assert states["Render"] == "failed" and states["Visual QA"] == "pending"


async def test_research_without_events_fails_before_copy(no_network, monkeypatch):
    monkeypatch.setattr(orchestrator, "_scenario", lambda name="good": lambda f: [] if f == "briefs.json" else None)
    job = await run_quick("good")
    assert job.status == JobStatus.FAILED and "found no events" in job.error
    states = {s.name: s.state for s in job.pipeline}
    assert states["Research"] == "failed" and states["Outline & copy"] == "pending"
    assert job.deck is None


async def test_quick_job_fails_when_every_event_loses_its_sources(monkeypatch):
    async def all_dead(url, client=None):
        return LinkCheck(url=url, status="dead", http_code=404)
    monkeypatch.setattr(link_checker, "check_url", all_dead)
    real = orchestrator._scenario("good")
    # Drop the fixture's precomputed link checks so every source goes through the (dead) checker.
    monkeypatch.setattr(orchestrator, "_scenario", lambda name="good": lambda f: None if f == "verification.json"
                        else real(f))
    job = await run_quick("good")
    assert job.status == JobStatus.FAILED and "failed link verification" in job.error
    states = {s.name: s.state for s in job.pipeline}
    assert states["Verify links"] == "failed" and states["Outline & copy"] == "pending"


def test_unknown_scenario_is_rejected_by_the_api(client):
    r = client.post("/jobs", json={"prompt": "hello there", "scenario": "../etc"})
    assert r.status_code == 422


# ---------------------------------------------------------------- brainstorm verify

async def test_brainstorm_sample_verifies_its_own_source(no_network):
    job = await run_brainstorm(brainstorms.sample_brainstorm())
    assert job.status == JobStatus.READY_FOR_REVIEW
    [brief] = job.briefs
    assert brief.title_en == "Mangwon Night Market" and brief.title_ko == "망원 야시장"
    assert (brief.start_date.month, brief.start_date.day, brief.end_date.day) == (10, 17, 19)
    assert [c.url for c in job.verification.checks] == [brainstorms.SAMPLE_URL]
    assert job.deck.slides[1].event_id == brief.id
    assert job.safeguard.decision == "allow"  # Brainstorm Jobs go through the Safeguard too
    assert [s.name for s in job.pipeline][-2:] == ["Safeguard", "Human publish"]


async def test_brainstorm_dead_source_blocks_and_demotes_facts(no_network):
    d = brainstorms.sample_brainstorm()
    url = "https://www.visitseoul.net/en/mangwon-gone"
    d.facts = [f.model_copy(update={"source_url": url}) if f.source_url else f for f in d.facts]
    job = await run_brainstorm(d)  # url is not in the fixture -> fake checker says 404
    assert job.verification.checks[0].status == "dead"
    assert job.verification.excluded_event_ids == [job.briefs[0].id]
    # BACKEND §11 #4 (v1.1): checks never auto-reject; blocking issues wait for the operator in Review.
    assert job.status == JobStatus.READY_FOR_REVIEW
    assert any(i.severity == "block" for i in job.issues)
    assert any(i.category == "link" and i.severity == "block" for i in job.issues)
    assert any("marked unverified" in line.message for line in job.log)


async def test_brainstorm_without_sources_is_blocked(no_network):
    d = brainstorms.sample_brainstorm()
    d.facts = [f.model_copy(update={"source_url": None}) for f in d.facts]
    job = await run_brainstorm(d)
    assert job.briefs == []
    # BACKEND §11 #4 (v1.1): checks never auto-reject; blocking issues wait for the operator in Review.
    assert job.status == JobStatus.READY_FOR_REVIEW
    assert any(i.severity == "block" for i in job.issues)
    assert any(i.category == "fact" and "source URL" in i.message for i in job.issues)


def test_demote_unverified_keeps_only_checked_sources():
    d = brainstorms.sample_brainstorm()
    d.facts.append(Fact(key="other", value="x", verified=True, source_url="https://example.com"))
    out, demoted = brainstorms.demote_unverified(d, {brainstorms.SAMPLE_URL})
    assert demoted == 1
    assert [f.verified for f in out.facts] == [True, True, True, False, False]


@pytest.mark.parametrize(("text", "expected"), [
    ("Oct 17–19 · 5–10 pm", (date(2026, 10, 17), date(2026, 10, 19))),
    ("Oct 30 – Nov 2", (date(2026, 10, 30), date(2026, 11, 2))),
    ("2026.12.30 - 2027.01.02", (date(2026, 12, 30), date(2027, 1, 2))),
    ("10/17–10/19", (date(2026, 10, 17), date(2026, 10, 19))),
    ("Dec 28 – Jan 3", (date(2026, 12, 28), date(2027, 1, 3))),
    ("Jan 5", (date(2027, 1, 5), date(2027, 1, 5))),
    ("Night Market 17", None),
    ("Weekends", None),
])
def test_parse_dates(text, expected):
    assert brainstorms.parse_dates(text, date(2026, 10, 7)) == expected


def test_approve_cannot_ask_for_a_stronger_publish_mode(monkeypatch, client):
    from app.config import settings
    calls = []

    async def fake_start(org_id, post_id, runs=1):
        calls.append(posts.get(org_id, post_id).mode)
    monkeypatch.setattr(publishing, "start", fake_start)
    monkeypatch.setattr(settings, "publish_mode", "mock")
    for mode, code in (("graph", 403), ("dryrun", 403), ("mock", 200), (None, 200)):
        job = store.save_job(client.org_id, allowed_job())
        r = client.post(f"/jobs/{job.id}/approve", json={"deck_version": 1, "mode": mode} if mode else {"deck_version": 1})
        assert r.status_code == code, (mode, r.text)
    assert calls == ["mock", "mock"]


def test_live_server_starts_brainstorm_with_an_empty_board(monkeypatch, client):
    from app.config import settings
    c = client
    monkeypatch.setattr(settings, "demo_mode", "live")
    live = c.post("/brainstorms", json={"sample": True}).json()
    assert live["messages"] == [] and live["facts"] == [] and live["angles"] == []
    monkeypatch.setattr(settings, "demo_mode", "fixture")
    demo = c.post("/brainstorms", json={"sample": True}).json()
    assert any("Mangwon" in f["value"] for f in demo["facts"])
