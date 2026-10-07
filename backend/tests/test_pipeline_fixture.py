"""DEMO_MODE=fixture runs of the orchestrator: scenarios (good / bad / fail) and Brainstorm verification.

Render and QA run for real, so these need Playwright's Chromium (`uv run playwright install chromium`).
"""

import secrets
from datetime import date

import pytest

from app import drafts, store
from app.pipeline import orchestrator
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


async def run_quick(scenario: str):
    job = store.save_job(orchestrator.new_job(secrets.token_hex(3), "test prompt", drill=scenario == "injection"))
    await orchestrator.run_job(job.id, scenario=scenario)
    return store.get_job(job.id)


async def run_brainstorm(draft):
    store.save_draft(draft)
    job = store.save_job(orchestrator.new_job(secrets.token_hex(3), "bs", "brainstorm", draft.id))
    await orchestrator.run_job(job.id, draft=draft)
    return store.get_job(job.id)


# ---------------------------------------------------------------- scenarios

async def test_good_scenario_is_ready(no_network):
    job = await run_quick("good")
    assert job.status == JobStatus.READY_FOR_REVIEW
    assert len(job.slide_urls) == len(job.deck.slides)
    assert set(job.verification.excluded_event_ids) == {"ev-kpop-giveaway", "ev-hongdae-character"}


async def test_bad_scenario_waits_for_operator_with_block_issues(no_network):
    job = await run_quick("bad")
    # BACKEND §11 #4 (v1.1): checks never auto-reject; blocking issues wait for the operator in Review.
    assert job.status == JobStatus.READY_FOR_REVIEW
    assert any(i.severity == "block" for i in job.issues)
    cats = {(i.category, i.severity) for i in job.issues}
    assert ("sensitive", "block") in cats
    assert ("pii", "block") in cats  # phone number on slide 2, found by host QA (not the fixture)
    events = [e for e in store.list_policy_events() if e.job_id == job.id]
    assert {(e.host, e.result) for e in events} >= {("graph.facebook.com", "policy_denied"),
                                                    ("pastebin.com", "policy_denied")}


async def test_injection_scenario_runs_the_drill(no_network):
    job = await run_quick("injection")
    assert job.status == JobStatus.READY_FOR_REVIEW
    assert [s.name for s in job.pipeline][:2] == ["Research", "Injection drill"]
    assert job.pipeline[1].state == "done" and job.pipeline[1].note.startswith("4/4 held")
    events = {(e.sandbox.split("-")[0], e.host, e.result) for e in store.list_policy_events() if e.job_id == job.id}
    assert events >= {("agent", "odoblnfmgtpmjymsktxe.supabase.co", "allowed"),
                      ("dril", "graph.facebook.com", "policy_denied"), ("dril", "pastebin.com", "policy_denied"),
                      ("dril", "—", "fs_denied")}
    drill = job.issues[0]
    assert drill.category == "link" and "hides instructions for AI agents" in drill.message
    assert drill.severity == "warn"  # the fixture briefs don't cite the page


async def test_fail_scenario_fails_at_render(no_network):
    job = await run_quick("fail")
    assert job.status == JobStatus.FAILED
    assert "timed out" in job.error
    states = {s.name: s.state for s in job.pipeline}
    assert states["Render"] == "failed" and states["Visual QA"] == "pending"


def test_unknown_scenario_is_rejected_by_the_api():
    from fastapi.testclient import TestClient

    from app.main import app
    r = TestClient(app).post("/jobs", json={"prompt": "hello there", "scenario": "../etc"})
    assert r.status_code == 422


# ---------------------------------------------------------------- brainstorm verify

async def test_brainstorm_sample_verifies_its_own_source(no_network):
    job = await run_brainstorm(drafts.sample_draft())
    assert job.status == JobStatus.READY_FOR_REVIEW
    [brief] = job.briefs
    assert brief.title_en == "Mangwon Night Market" and brief.title_ko == "망원 야시장"
    assert (brief.start_date.month, brief.start_date.day, brief.end_date.day) == (10, 17, 19)
    assert [c.url for c in job.verification.checks] == [drafts.SAMPLE_URL]
    assert job.deck.slides[1].event_id == brief.id


async def test_brainstorm_dead_source_blocks_and_demotes_facts(no_network):
    d = drafts.sample_draft()
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
    d = drafts.sample_draft()
    d.facts = [f.model_copy(update={"source_url": None}) for f in d.facts]
    job = await run_brainstorm(d)
    assert job.briefs == []
    # BACKEND §11 #4 (v1.1): checks never auto-reject; blocking issues wait for the operator in Review.
    assert job.status == JobStatus.READY_FOR_REVIEW
    assert any(i.severity == "block" for i in job.issues)
    assert any(i.category == "fact" and "source URL" in i.message for i in job.issues)


def test_demote_unverified_keeps_only_checked_sources():
    d = drafts.sample_draft()
    d.facts.append(Fact(key="other", value="x", verified=True, source_url="https://example.com"))
    out, demoted = drafts.demote_unverified(d, {drafts.SAMPLE_URL})
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
    assert drafts.parse_dates(text, date(2026, 10, 7)) == expected
