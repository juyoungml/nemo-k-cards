"""Golden set (app.golden): the recorded cases pass, and each check catches the regression it exists for."""

import json
from datetime import date

import pytest

from app.config import settings
from app.golden import cases as golden
from app.golden.checks import run_checks, unsupported_facts
from app.schemas import EventBrief, Issue

GS = golden.load_set()
CASES = {c.id: c for c in GS.cases}
RECORDED = [c.id for c in GS.cases if golden.load_recording(c.id)]


def statuses(case_id: str, rec: golden.Recording) -> dict[str, str]:
    return {r.name: r.status for r in run_checks(CASES[case_id], rec)}


def good() -> golden.Recording:
    """The demo-scenario recording, a READY Deck with three verified events (no photos)."""
    return golden.load_recording("ambiguous-weekend").model_copy(deep=True)


def test_set_is_versioned_and_every_recording_has_a_case():
    assert GS.version >= 1 and len(GS.cases) >= 6
    for p in golden.RECORDINGS_DIR.glob("*.json"):
        rec = golden.Recording.model_validate_json(p.read_text())
        assert rec.case_id == p.stem and rec.case_id in CASES
        assert rec.set_version == GS.version, f"{p.name} was recorded for set v{rec.set_version}"


@pytest.mark.parametrize("case_id", RECORDED)
def test_recorded_case_passes(case_id):
    failed = [f"{r.name}: {r.detail}" for r in run_checks(CASES[case_id], golden.load_recording(case_id))
              if r.status == "fail"]
    assert not failed


def test_too_many_hashtags_fail():
    rec = good()
    rec.deck["caption"] += " #one #more"
    assert statuses("ambiguous-weekend", rec)["hashtags"] == "fail"


def test_invented_price_and_date_fail_but_brief_facts_pass():
    rec = good()
    rec.deck["slides"][2]["body"] = "Entry ₩20,000 on Nov 30"   # lantern walk: Free, Oct 9–11
    s = statuses("ambiguous-weekend", rec)
    assert s["facts"] == "fail"
    rec.deck["slides"][2]["body"] = "Free, Oct 9–11, start at sunset"
    assert statuses("ambiguous-weekend", rec)["facts"] == "warn"  # only the tips slide's last-train time


def test_free_claim_needs_a_free_price():
    b = EventBrief.model_validate(json.loads((settings.scenarios_dir / "good/briefs.json").read_text())[2])
    assert b.price == "₩5,000"
    fails, _ = unsupported_facts("Free entry all week", [b])
    assert fails == ["says 'free' but no brief does"]


def test_heading_must_name_its_event():
    rec = good()
    rec.deck["slides"][1]["heading"] = "Tank Day Sale!"
    assert statuses("ambiguous-weekend", rec)["facts"] == "fail"


def test_excluded_event_on_a_slide_fails_links():
    rec = good()
    rec.deck["slides"][3]["event_id"] = "ev-kpop-giveaway"      # its source is a lookalike domain
    s = statuses("ambiguous-weekend", rec)
    assert s["links"] == "fail" and s["slides"] == "fail"


def test_unchecked_url_on_a_slide_fails_links():
    rec = good()
    rec.deck["slides"][6]["body"] = "Tickets: https://example.com/tix"
    assert statuses("ambiguous-weekend", rec)["links"] == "fail"


def test_stock_photo_of_another_category_fails():
    rec = good()
    lantern = rec.deck["slides"][2]                               # festival
    lantern["stock_id"] = "theater-curtain"                       # performance
    lantern["image"] = {"url": "local:performance.jpg", "license": "ai_generated", "credit": "Image · AI-generated"}
    assert statuses("ambiguous-weekend", rec)["photos"] == "fail"
    lantern["stock_id"] = "hanji-lanterns-dusk"
    lantern["image"]["url"] = "local:festival.jpg"
    assert statuses("ambiguous-weekend", rec)["photos"] == "pass"


def test_reviewer_photo_mismatch_fails_the_llm_check():
    rec = good()
    rec.issues.append(Issue(severity="warn", category="visual", slide_index=2,
                            message="Photo shows fireworks, but this is a lantern walk."))
    s = statuses("ambiguous-weekend", rec)
    assert s["photos·llm"] == "fail" and s["photos"] == "pass"


def test_events_outside_the_window_fail_expectations():
    rec = good()
    rec.today = date(2026, 10, 20)                               # lantern walk ended Oct 11
    assert statuses("ambiguous-weekend", rec)["expectations"] == "fail"


def test_a_deck_where_none_was_expected_fails_the_outcome():
    rec = good()
    rec.case_id = "nothing-to-find"
    assert statuses("nothing-to-find", rec)["outcome"] == "fail"


def test_demo_bad_scenario_is_caught():
    sc = settings.scenarios_dir / "bad"
    rec = good()
    rec.briefs = json.loads((sc / "briefs.json").read_text())
    rec.verification = json.loads((sc / "verification.json").read_text())
    rec.deck = json.loads((sc / "deck.json").read_text())
    rec.issues = [Issue.model_validate(i) for i in json.loads((sc / "review.json").read_text())["issues"]]
    rec.today = date(2026, 10, 8)
    results = {r.name: r for r in run_checks(CASES["ambiguous-weekend"], rec)}
    facts = results["facts"].detail
    assert results["facts"].status == "fail"
    assert "May 18" in facts and "51.8%" in facts and "doesn't name" in facts
