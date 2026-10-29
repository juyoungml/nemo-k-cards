"""Fact sources on slides (#25): each slide lists its facts with their source (a checked link with its status, or the
Organization's own Source material with the quoted words); dead and unchecked links are marked; claims no brief
supports are flagged; another Organization's material is never named; older Jobs still get sources; the view is
never stored. No network and no agents."""

import json
from datetime import UTC, datetime

from app import materials, slide_sources, store
from app.schemas import CardDeck, EventBrief, Job, LinkCheck, MaterialRef, VerificationReport

OK, DEAD, SUS, NEVER = ("https://public.example/event", "https://gone.example/page", "https://visitkorea-tix.xyz/",
                        "https://never-checked.example/")
MID = "m-" + "1" * 16


def _brief(**kw) -> dict:
    return {"id": "lantern", "title_en": "Lantern Walk", "title_ko": "등불 산책", "category": "festival",
            "start_date": "2026-11-07", "end_date": "2026-11-09", "venue_en": "Cheonggyecheon",
            "nearest_station": "Jonggak Stn. Exit 5", "price": "Free", "address_ko": "서울 중구 청계천로 1",
            "sources": [{"url": OK, "kind": "official"}], **kw}


def _deck(**event_slide) -> dict:
    slides = [{"index": 0, "layout": "cover", "heading": "Lanterns this weekend"},
              {"index": 1, "layout": "event", "heading": "Lantern Walk", "event_id": "lantern", **event_slide},
              {"index": 2, "layout": "tips", "heading": "Know before you go",
               "body": "Last trains leave around 23:30"},
              {"index": 3, "layout": "map", "heading": "Getting there", "event_id": "lantern"},
              {"index": 4, "layout": "event", "heading": "Night Market", "event_id": "market"},
              {"index": 5, "layout": "cta", "heading": "Save this"}]
    return {"title": "Lanterns", "caption": "c", "slides": slides}


def _job(briefs, deck, checks=(), excluded=(), material=(), **kw) -> Job:
    return Job(id=kw.pop("id", "j1"), prompt="Lantern Walk", created_at=datetime.now(UTC),
               briefs=[EventBrief.model_validate(b) for b in briefs], deck=CardDeck.model_validate(deck),
               verification=VerificationReport(checks=list(checks), excluded_event_ids=list(excluded)),
               material=list(material), **kw)


def _ref(mid=MID, name="press-release.pdf") -> MaterialRef:
    return MaterialRef(id=mid, kind="file", name=name, content_type="application/pdf")


CHECKS = [LinkCheck(url=OK, status="ok", http_code=200), LinkCheck(url=DEAD, status="dead", http_code=404),
          LinkCheck(url=SUS, status="suspicious", reason="lookalike of visitkorea.or.kr")]
MARKET = {"id": "market", "title_en": "Night Market", "title_ko": "야시장", "start_date": "2026-11-07",
          "end_date": "2026-11-08", "venue_en": "Mangwon", "sources": [{"url": DEAD, "kind": "sns"},
                                                                      {"url": NEVER, "kind": "news"}]}


def _by_slide(job: Job):
    return {s.slide_index: s for s in slide_sources.for_job(job)}


def test_event_slide_lists_the_facts_it_shows_with_recorded_sources():
    facts = [{"field": "dates", "value": "Nov 7–9", "origin": "material", "material_id": MID, "quote": "Nov 7-9",
              "verified": True},
             {"field": "price", "value": "Free", "origin": "material", "material_id": MID, "quote": "Free entry",
              "verified": True},
             {"field": "station", "value": "Jonggak Stn. Exit 5", "origin": "link", "url": OK, "verified": True}]
    job = _job([_brief(facts=facts, materials=[MID]), MARKET], _deck(), CHECKS, material=[_ref()])
    s = _by_slide(job)[1]
    assert s.event_id == "lantern" and s.event_title == "Lantern Walk" and s.unsourced == []
    by = {f.field: f for f in s.facts}
    # The fields the event template shows, in reading order; ones the brief lacks (booking, access) are left out.
    assert list(by) == ["title", "dates", "venue", "station", "price"]
    assert by["dates"].value == "Nov 7–9" and by["dates"].recorded
    [m] = by["price"].sources
    assert (m.origin, m.material_name, m.quote, m.verified) == ("material", "press-release.pdf", "Free entry", True)
    [link] = by["station"].sources
    assert (link.origin, link.url, link.status, link.http_code, link.verified) == ("link", OK, "ok", 200, True)
    # No recorded source for the title: the event's own links and material stand in, marked as not per-fact.
    assert not by["title"].recorded
    assert [(x.origin, x.url or x.material_name) for x in by["title"].sources] == [("link", OK),
                                                                                  ("material", "press-release.pdf")]


def test_map_slide_shows_the_getting_there_facts():
    s = _by_slide(_job([_brief(), MARKET], _deck(), CHECKS))[3]
    assert [(f.field, f.value) for f in s.facts] == [("station", "Jonggak Stn. Exit 5"),
                                                     ("venue", "Cheonggyecheon"), ("address", "서울 중구 청계천로 1")]


def test_dead_suspicious_and_unchecked_links_are_marked():
    facts = [{"field": "price", "value": "Free", "origin": "link", "url": SUS, "verified": False,
              "note": "not one of the event's sources"}]
    job = _job([_brief(facts=facts), MARKET], _deck(source_urls=[OK, DEAD]), CHECKS)
    s = _by_slide(job)
    [sus] = {f.field: f for f in s[1].facts}["price"].sources
    assert (sus.status, sus.reason, sus.verified, sus.note) == ("suspicious", "lookalike of visitkorea.or.kr", False,
                                                                "not one of the event's sources")
    assert [(x.url, x.status) for x in s[1].links] == [(OK, "ok"), (DEAD, "dead")]
    market = {f.field: f for f in s[4].facts}["title"].sources
    assert [(x.url, x.status, x.http_code) for x in market] == [(DEAD, "dead", 404), (NEVER, "unchecked", None)]


def test_claims_no_brief_supports_are_flagged_and_tips_are_general():
    job = _job([_brief(), MARKET], _deck(body="Entry ₩5,000 · opens 6pm"), CHECKS)
    s = _by_slide(job)
    assert s[1].unsourced == ["price '₩5,000' is not in the briefs", "time '6pm' is not in the briefs"]
    assert s[2].general and s[2].unsourced == ["time '23:30' is not in the briefs"]
    assert not s[0].general and s[0].unsourced == [] and s[0].facts == [] and s[5].facts == []


def test_material_facts_without_a_proven_quote_say_so():
    facts = [{"field": "price", "value": "Free", "origin": "material", "material_id": MID, "quote": "Free entry",
              "verified": False, "note": "the quoted words are not in the material"}]
    s = _by_slide(_job([_brief(facts=facts, materials=[MID]), MARKET], _deck(), CHECKS, material=[_ref()]))[1]
    [m] = {f.field: f for f in s.facts}["price"].sources
    assert m.verified is False and m.note == "the quoted words are not in the material"
    assert m.material_name == "press-release.pdf"


def test_another_organizations_material_is_never_named_or_quoted():
    theirs = "m-" + "9" * 16
    facts = [{"field": "price", "value": "Free", "origin": "material", "material_id": theirs,
              "quote": "THEIR SECRET LINE", "verified": False}]
    job = _job([_brief(facts=facts, materials=[theirs]), MARKET], _deck(), CHECKS, material=[_ref()])
    dumped = json.dumps([s.model_dump() for s in slide_sources.for_job(job)])
    assert theirs not in dumped and "THEIR SECRET LINE" not in dumped
    [m] = {f.field: f for f in _by_slide(job)[1].facts}["price"].sources
    assert (m.material_id, m.material_name, m.quote, m.verified) == (None, None, None, False)
    assert m.note == slide_sources.NOT_YOURS


def test_excluded_or_unknown_events_and_no_deck():
    job = _job([_brief(), MARKET], _deck(), CHECKS, excluded=["market"])
    assert "excluded" in _by_slide(job)[4].note
    job.briefs = [job.briefs[0]]
    assert "not in the research" in _by_slide(job)[4].note
    assert slide_sources.for_job(job.model_copy(update={"deck": None})) == []


def test_old_jobs_without_fact_origins_or_verification_still_get_sources():
    old = {"id": "old1", "prompt": "p", "status": "READY_FOR_REVIEW", "created_at": "2026-10-20T00:00:00Z",
           "briefs": [_brief(), MARKET], "deck": _deck()}  # before #20: no facts, materials or material refs
    s = _by_slide(Job.model_validate(old))
    facts = {f.field: f for f in s[1].facts}
    assert all(not f.recorded for f in facts.values())
    assert [(x.url, x.status) for x in facts["dates"].sources] == [(OK, "unchecked")]


def test_the_api_returns_sources_per_slide_and_never_stores_them(sign_in):
    a, b = sign_in(org="A"), sign_in(org="B")
    theirs = store.save_material(a.org_id, materials.from_note("A-ONLY: free entry"))
    mine = store.save_material(b.org_id, materials.from_note("Lantern Walk, Nov 7-9. Free entry."))
    facts = [{"field": "price", "value": "Free", "origin": "material", "material_id": mine.id, "quote": "Free entry",
              "verified": True},
             {"field": "dates", "value": "Nov 7–9", "origin": "material", "material_id": theirs.id,
              "quote": "A-ONLY", "verified": False}]
    job = _job([_brief(facts=facts, materials=[mine.id, theirs.id]), MARKET], _deck(), CHECKS, id="srcjob1",
               material=[MaterialRef.model_validate(mine.model_dump())])
    store.save_job(b.org_id, job)

    r = b.get("/jobs/srcjob1")
    assert r.status_code == 200, r.text
    slides = {s["slide_index"]: s for s in r.json()["slide_sources"]}
    assert len(slides) == 6
    facts = {f["field"]: f for f in slides[1]["facts"]}
    [m] = facts["price"]["sources"]
    assert m["material_name"] == mine.name and m["quote"] == "Free entry" and m["verified"] is True
    # The per-slide view names only this Job's own material (the raw briefs keep whatever ids the agent wrote).
    view = json.dumps(r.json()["slide_sources"])
    assert theirs.id not in view and "A-ONLY" not in view and "A-ONLY: free entry" not in r.text

    assert a.get("/jobs/srcjob1").status_code == 404  # the Job itself is the Organization's own
    assert all(j["slide_sources"] == [] for j in b.get("/jobs").json())  # lists stay light

    stored = store.get_job(b.org_id, "srcjob1")
    store.save_job(b.org_id, stored.model_copy(update={"slide_sources": slide_sources.for_job(stored)}))
    with store.tenant(b.org_id) as c:
        doc = c.execute("select doc from jobs where id = 'srcjob1'").fetchone()[0]
    assert doc["slide_sources"] == []
