"""Guide Decks (#27): a Deck kind picked or asked for at request time, every Guide fact on an Official source, a
"facts checked" date on the cards and in Review, the point and checklist layouts, and golden checks for guides.

The fixture runs render for real (Playwright's Chromium), like test_pipeline_fixture.py."""

import functools
import secrets
import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from test_source_material import FakeRunner

from app import guides, materials, official, orgs, slide_sources, store
from app.config import settings
from app.golden.cases import Case, Expect, recording_from_job
from app.golden.checks import run_checks
from app.pipeline import agent_runner, orchestrator
from app.renderer.html import HtmlRenderer
from app.schemas import CardDeck, GuideBrief, JobStatus, LinkCheck, Research
from app.services import link_checker

GUIDE_DIR = settings.scenarios_dir / "guide"


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(orchestrator, "FIXTURE_DELAY", 0)


@pytest.fixture
def links(monkeypatch):
    """Every URL not in the scenario's recorded checks is dead (no network in tests)."""
    async def check(url, client=None):
        return LinkCheck(url=url, status="dead", http_code=404)
    monkeypatch.setattr(link_checker, "check_url", check)


@functools.cache
def org() -> uuid.UUID:
    return orgs.create_org(uuid.uuid4(), "Guide tests").org_id


def scenario_guide() -> GuideBrief:
    return GuideBrief.model_validate_json((GUIDE_DIR / "guide.json").read_text())


def ok(*urls: str) -> dict[str, LinkCheck]:
    return {u: LinkCheck(url=u, status="ok", http_code=200) for u in urls}


# ---------------------------------------------------------------- what counts as official

@pytest.mark.parametrize(("url", "kind", "want"), [
    ("https://www.hikorea.go.kr/info", "official", "allowlist"),
    ("https://english.visitkorea.or.kr/svc/x", "other", "allowlist"),   # the label doesn't matter for known domains
    ("https://www.t-money.co.kr/", "official", "allowlist"),
    ("https://www.mmca.go.kr/", "news", "allowlist"),
    ("https://www.leeum.org/", "official", "claimed"),                  # a museum's own site: a person confirms
    ("https://www.leeum.org/", "other", "no"),
    ("https://blog.naver.com/someone/1", "official", "no"),             # never, whatever the label
    ("https://www.koreaherald.com/view", "official", "no"),
    ("https://www.klook.com/activity/1", "official", "no"),
    ("not a url", "official", "no"),
])
def test_official_standing(url, kind, want):
    assert official.standing(url, kind) == want


def test_a_point_needs_an_official_verified_source():
    g = scenario_guide()
    checks = ok(*guides.source_urls(g))
    excluded, issues, lines = guides.check(g, checks, {})
    # The souvenir point rests only on a blog: left off, with the reason in the log.
    assert excluded == ["souvenir"] and "not an official source" in lines[0]
    assert issues == []  # every other point is on an allowlisted domain
    assert all(f.verified for p in g.points if p.id != "souvenir" for f in p.facts)

    # A dead link takes its points with it.
    checks["https://www.t-money.co.kr/"] = LinkCheck(url="https://www.t-money.co.kr/", status="dead")
    excluded, _, _ = guides.check(g, checks, {})
    assert set(excluded) == {"buy", "topup", "refund", "souvenir"}


def test_a_site_called_official_counts_but_a_person_confirms_it():
    g = GuideBrief.model_validate({
        "topic_en": "Leeum opening hours", "sources": [{"url": "https://www.leeum.org/", "kind": "official"}],
        "points": [{"id": "hours", "title": "Open 10:00–18:00", "facts": [
            {"field": "other", "value": "10:00–18:00", "origin": "link", "url": "https://www.leeum.org/"}]}]})
    excluded, issues, _ = guides.check(g, ok("https://www.leeum.org/"), {})
    assert excluded == []
    [i] = issues
    assert i.severity == "warn" and "www.leeum.org" in i.message and "Open 10:00–18:00" in i.message


def test_source_material_counts_when_its_quote_is_there():
    m = materials.from_note("From Nov 1 the museum opens at 9:00 on weekends.")
    g = GuideBrief.model_validate({
        "topic_en": "New hours", "materials": [m.id],
        "points": [{"id": "weekend", "title": "Weekends from 9:00", "facts": [
            {"field": "other", "value": "9:00", "origin": "material", "material_id": m.id,
             "quote": "opens at 9:00 on weekends"}]},
                   {"id": "made-up", "title": "Free on Mondays", "facts": [
            {"field": "other", "value": "free", "origin": "material", "material_id": m.id,
             "quote": "free on Mondays"}]}]})
    excluded, _, _ = guides.check(g, {}, {m.id: m})
    assert excluded == ["made-up"]
    assert g.points[1].facts[0].note == "the quoted words are not in the material"


def test_research_output_must_match_its_kind():
    with pytest.raises(ValidationError, match="no guide"):
        Research(kind="guide")
    with pytest.raises(ValidationError, match="a guide was returned"):
        Research(kind="event", guide=scenario_guide())
    with pytest.raises(ValidationError, match="at least one source"):
        GuideBrief.model_validate({"topic_en": "x", "points": [{"id": "a", "title": "a"}]})


# ---------------------------------------------------------------- the pipeline (fixture mode)

async def run(kind, scenario="guide", prompt="How to use a T-money card"):
    job = store.save_job(org(), orchestrator.new_job(secrets.token_hex(3), prompt, kind=kind))
    await orchestrator.run_job(org(), job.id, scenario=scenario)
    return store.get_job(org(), job.id)


async def test_a_guide_deck_runs_end_to_end(links):
    job = await run("guide")
    assert job.status == JobStatus.READY_FOR_REVIEW, job.error
    assert job.kind == "guide" and job.requested_kind == "guide" and job.briefs == []
    assert job.verification.excluded_point_ids == ["souvenir"]
    assert job.verification.checked_at is not None
    assert [s.layout for s in job.deck.slides] == ["cover", "point", "point", "point", "point", "checklist", "cta"]
    assert len(job.slide_urls) == 7
    step = next(p for p in job.pipeline if p.name.lower().startswith("verify"))
    assert "5/6 points officially sourced" in step.note

    # The cards say when the facts were checked, and where they came from.
    out = settings.output_dir / job.id
    checked = job.verification.checked_at.astimezone(orchestrator.KST).strftime("%Y.%m.%d")
    assert f"FACTS CHECKED {checked}" in (out / "slide-00.html").read_text()
    cta = (out / "slide-06.html").read_text()
    assert f"Facts checked {checked}" in cta and "blog.naver.com" not in cta and "t-money.co.kr" in cta
    assert "Official source: t-money.co.kr" in (out / "slide-01.html").read_text()

    # Review's fact sources: a point slide lists its point's official page.
    srcs = slide_sources.for_job(job)
    point = next(s for s in srcs if s.layout == "point")
    assert point.event_title == "Buy one at a convenience store"
    [fact] = point.facts
    assert fact.sources[0].url == "https://www.t-money.co.kr/" and fact.sources[0].verified
    assert next(s for s in srcs if s.layout == "checklist").general


async def test_left_to_the_researcher_it_can_pick_a_guide(links):
    job = await run(None)  # fixture research returns the scenario's guide
    assert job.status == JobStatus.READY_FOR_REVIEW and job.kind == "guide" and job.requested_kind is None
    assert any("picked by the researcher" in line.message for line in job.log)


async def test_an_event_request_stays_an_event_deck(links):
    job = await run("event", scenario="guide")  # even with a guide in the fixture, an Event Deck is researched
    assert job.kind == "event" and job.guide is None and job.briefs


async def test_older_jobs_load_as_event_decks():
    job = orchestrator.new_job("old", "x")
    raw = job.model_dump(mode="json")
    raw.pop("kind"), raw.pop("requested_kind")
    loaded = type(job).model_validate(raw)
    assert loaded.kind == "event" and loaded.requested_kind == "event"  # not shown as the researcher's pick


# ---------------------------------------------------------------- live research (fake agent)

def _guide_out(**kw) -> dict:
    return {"kind": "guide", "guide": {**scenario_guide().model_dump(mode="json"), **kw}}


async def _research_and_verify(job, runner, monkeypatch):
    monkeypatch.setattr(agent_runner, "get_runner", lambda: runner)
    monkeypatch.setattr(settings, "demo_mode", "live")
    out = await orchestrator._run_stage_step(str(org()), job.id, "research", None, "good", {})
    if out["ok"]:
        out = await orchestrator._run_stage_step(str(org()), job.id, "verify", None, "good", {})
    return store.get_job(org(), job.id), out


async def test_the_researcher_is_told_the_kind_and_its_pick_is_kept(monkeypatch):
    good = ok(*guides.source_urls(scenario_guide()))

    async def check(url, client=None):
        return good.get(url) or LinkCheck(url=url, status="dead")
    monkeypatch.setattr(link_checker, "check_url", check)
    job = store.save_job(org(), orchestrator.new_job(secrets.token_hex(3), "How do I use T-money?", kind=None))
    runner = FakeRunner({"researcher": _guide_out()})
    job, out = await _research_and_verify(job, runner, monkeypatch)
    assert out["ok"] and job.kind == "guide" and job.verification.excluded_point_ids == ["souvenir"]
    call = runner.calls[0]
    assert call["payload"]["kind"] is None and "Decide the Deck kind" in call["task"]


async def test_a_guide_for_an_event_request_fails_the_job(monkeypatch):
    job = store.save_job(org(), orchestrator.new_job(secrets.token_hex(3), "Busan festivals", kind="event"))
    job, out = await _research_and_verify(job, FakeRunner({"researcher": _guide_out()}), monkeypatch)
    assert not out["ok"] and job.status == JobStatus.FAILED
    assert "returned a guide Deck for a event Deck request" in job.error


async def test_too_few_official_points_fails_with_a_way_forward(monkeypatch, links):
    # Every link is dead: no point has an official source left.
    job = store.save_job(org(), orchestrator.new_job(secrets.token_hex(3), "T-money", kind="guide"))
    job, out = await _research_and_verify(job, FakeRunner({"researcher": _guide_out()}), monkeypatch)
    assert not out["ok"] and job.status == JobStatus.FAILED
    assert "0 of 6 guide point(s) trace to an official source" in job.error and "Source material" in job.error


# ---------------------------------------------------------------- API and renderer

def test_the_api_takes_the_kind_and_starts_a_guide(client, monkeypatch):
    started = {}

    async def fake_start(org_id, job, scenario="good", **kw):
        started.update(job=job, scenario=scenario)
    monkeypatch.setattr(orchestrator, "start_job", fake_start)
    r = client.post("/jobs", json={"prompt": "How to get a tax refund", "kind": "guide"})
    assert r.status_code == 201, r.text
    assert r.json()["requested_kind"] == "guide" and r.json()["kind"] == "guide"
    assert started["scenario"] == "guide"
    r = client.post("/jobs", json={"prompt": "What's on in Seoul"})
    assert r.json()["requested_kind"] is None and r.json()["kind"] is None and started["scenario"] == "good"
    assert client.post("/jobs", json={"prompt": "x" * 5, "kind": "listicle"}).status_code == 422
    audit = [e for e in client.get("/audit").json() if e["action"] == "job.created"]
    assert {e["detail"]["kind"] for e in audit} >= {"guide", "auto"}


async def test_guide_layouts_fit_and_say_where_the_facts_came_from(tmp_path):
    g = scenario_guide()
    guides.check(g, ok(*guides.source_urls(g)), {})
    g = g.model_copy(update={"points": [p for p in g.points if p.id != "souvenir"]})
    deck = CardDeck.model_validate_json((GUIDE_DIR / "deck.json").read_text())
    when = datetime(2026, 10, 30, 1, 0, tzinfo=UTC)
    slides = await HtmlRenderer().render(deck, tmp_path, [], guide=g, checked=when)
    bad = [f"{s.index}:{m.selector}" for s in slides for m in s.measures if m.overflow or m.font_px < 30]
    assert bad == []
    point = (tmp_path / "slide-03.html").read_text()
    assert "Official source: seoulmetro.co.kr" in point and ">03<" in point
    assert "2026.10.30" in (tmp_path / "slide-06.html").read_text()


# ---------------------------------------------------------------- golden checks

async def test_golden_guide_checks_pass_on_the_fixture_run_and_catch_a_bad_point(links):
    job = await run("guide")
    case = Case(id="guide-tmoney", kind="guide", prompt=job.prompt,
                expect=Expect(official_hosts_any=["t-money.co.kr"]))
    rec = recording_from_job(job, case, 1, datetime.now(orchestrator.KST).date(), {}, "", "local")
    results = {r.name: r for r in run_checks(case, rec)}
    for name in ("outcome", "kind", "schema", "slides", "official", "facts", "expectations"):
        assert results[name].status == "pass", (name, results[name].detail)

    # A point slide showing the blog-only point, and a fee no point states, both fail.
    deck = rec.deck
    deck["slides"][1]["point_id"] = "souvenir"
    deck["slides"][2]["body"] = "Top-ups cost ₩500 each."
    results = {r.name: r for r in run_checks(case, rec)}
    assert results["slides"].status == "fail" and "souvenir" in results["slides"].detail
    assert results["facts"].status == "fail" and "₩500" in results["facts"].detail


def test_a_recording_without_a_guide_skips_cleanly():
    case = Case(id="guide-x", kind="guide", prompt="x", expect=Expect(outcome="no_deck"))
    from app.golden.cases import Recording
    rec = Recording(case_id="guide-x", set_version=1, provenance="synthetic", recorded_at=datetime.now(UTC),
                    today=datetime.now(UTC).date(), prompt="x", status=JobStatus.FAILED,
                    error="only 1 of 4 guide point(s) trace to an official source")
    results = run_checks(case, rec)
    assert results[0].status == "pass" and {r.status for r in results[3:]} == {"skip"}

