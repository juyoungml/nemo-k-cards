"""Swap a photo, regenerate one slide (#23): from Review, each makes a new Deck version, re-renders only that slide and
starts the Safeguard check of the new version; the rest of the Deck is untouched. Photos come from the event's vetted
candidates, the stock set (same rules as the pipeline) or none; a rewrite is checked against the research, and agent
rewrites have a fair-use cap per Deck."""

import asyncio
import shutil
from pathlib import Path

import httpx
import pytest
from test_deck_versions import checked, get, post, ready_job
from test_photos import BRIEF, OK, _jpeg, img

from app import assets, deck_versions, slide_actions, store
from app.config import settings
from app.schemas import ImageAsset, Issue, JobStatus
from app.services import photos

POPUP_SLIDE, FESTIVAL_SLIDE, TIPS_SLIDE, CTA_SLIDE = 1, 2, 4, 6


@pytest.fixture(autouse=True)
def fixture_mode(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", "fixture")


def _version(org_id, job, n):
    return next(v for v in deck_versions.list_versions(org_id, job) if v.version == n)


def _audit(client, job_id):
    return [e for e in client.get("/audit").json() if e["target"] == job_id and e["action"] == "deck.edited"]


# ---------------------------------------------------------------- photo options

async def test_options_show_candidates_stock_and_poster_with_reasons(client):
    job = await ready_job(client.org_id)
    r = await get(client, f"/jobs/{job.id}/slides/{POPUP_SLIDE}/photos")
    assert r.status_code == 200, r.text
    o = r.json()
    assert o["event_id"] == "ev-seongsu-stationery"
    stock = {x["stock_id"]: x for x in o["options"] if x["source"] == "stock"}
    assert stock["popup-paper-goods"]["allowed"] and stock["popup-paper-goods"]["url"] == "/stock/popup.jpg"
    assert not stock["hanji-lanterns-dusk"]["allowed"]
    assert stock["hanji-lanterns-dusk"]["why_not"] == "a festival photo on a popup event"
    [none] = [x for x in o["options"] if x["source"] == "none"]
    assert none["current"]  # the good Deck's event slides are posters
    # Only event slides carry a photo.
    assert (await get(client, f"/jobs/{job.id}/slides/{TIPS_SLIDE}/photos")).status_code == 409
    assert (await get(client, f"/jobs/{job.id}/slides/99/photos")).status_code == 404


def test_stock_files_are_served_without_sign_in_but_only_the_catalog(client):
    anon = httpx.Client(transport=client._transport, base_url=str(client.base_url))
    r = anon.get("/stock/popup.jpg")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert anon.get("/stock/catalog.json").status_code == 404
    assert anon.get("/stock/..%2F..%2Fconfig.py").status_code == 404


# ---------------------------------------------------------------- swap

async def test_swap_to_stock_makes_a_version_and_rerenders_only_that_slide(client):
    job = await ready_job(client.org_id)
    r = await post(client, f"/jobs/{job.id}/slides/{POPUP_SLIDE}/photo",
                   {"base_version": 1, "source": "stock", "stock_id": "popup-paper-goods"})
    assert r.status_code == 201, r.text
    new = r.json()
    assert new["deck_version"] == 2 and new["safeguard_pending"]
    slide = new["deck"]["slides"][POPUP_SLIDE]
    assert slide["stock_id"] == "popup-paper-goods" and slide["image"]["license"] == "ai_generated"
    saved = store.get_job(client.org_id, job.id)
    v = _version(client.org_id, saved, 2)
    # The closing card lists photo credits, so it changes too ("Photos: Image · AI-generated").
    assert v.action == "photo" and v.rerendered == [POPUP_SLIDE, CTA_SLIDE]
    assert v.changes == [{"field": "slide.photo", "slide": POPUP_SLIDE, "before": "no photo (poster)",
                          "after": "stock photo popup-paper-goods (AI-generated)"}]
    # Every other slide keeps its file and its text.
    old_urls = [u.rsplit("/", 1)[-1] for u in job.slide_urls]
    new_urls = [u.rsplit("/", 1)[-1] for u in saved.slide_urls]
    assert [i for i, (a, b) in enumerate(zip(old_urls, new_urls, strict=True)) if a != b] == [POPUP_SLIDE, CTA_SLIDE]
    assert [s.heading for s in saved.deck.slides] == [s.heading for s in job.deck.slides]
    [entry] = _audit(client, job.id)
    assert entry["detail"]["via"] == "photo"
    done = await checked(client.org_id, job.id)
    assert done.safeguard.deck_version == 2

    # Back to a poster; the same choice twice is refused.
    r = await post(client, f"/jobs/{job.id}/slides/{POPUP_SLIDE}/photo", {"base_version": 2, "source": "none"})
    assert r.status_code == 201 and r.json()["deck"]["slides"][POPUP_SLIDE]["image"] is None
    r = await post(client, f"/jobs/{job.id}/slides/{POPUP_SLIDE}/photo", {"base_version": 3, "source": "none"})
    assert r.status_code == 422 and "already" in r.json()["detail"]


async def test_stock_rules_match_the_pipeline(client):
    job = await ready_job(client.org_id)
    url = f"/jobs/{job.id}/slides/{POPUP_SLIDE}/photo"
    wrong = await post(client, url, {"base_version": 1, "source": "stock", "stock_id": "hanji-lanterns-dusk"})
    assert wrong.status_code == 422 and "can't stand for a popup event" in wrong.json()["detail"]
    unknown = await post(client, url, {"base_version": 1, "source": "stock", "stock_id": "made-up"})
    assert unknown.status_code == 422

    # Each stock photo once per Deck: a second festival slide can't reuse the lantern photo.
    r = await post(client, f"/jobs/{job.id}/slides/{FESTIVAL_SLIDE}/photo",
                   {"base_version": 1, "source": "stock", "stock_id": "hanji-lanterns-dusk"})
    assert r.status_code == 201, r.text
    j = store.get_job(client.org_id, job.id)
    j.deck.slides[3].event_id = "ev-lantern-walk"  # another slide about the same festival
    store.save_job(client.org_id, j)
    again = await post(client, f"/jobs/{job.id}/slides/3/photo",
                       {"base_version": 2, "source": "stock", "stock_id": "hanji-lanterns-dusk"})
    assert again.status_code == 422 and "already on slide 3" in again.json()["detail"]


async def test_swap_to_another_vetted_photo(client):
    job = await ready_job(client.org_id)
    folder = settings.output_dir / job.id / "photos"
    folder.mkdir(parents=True)
    cands = []
    for i in range(2):
        f = folder / f"ev-seongsu-stationery-{i}.jpg"
        f.write_bytes(_jpeg(1600, 1200))
        cands.append(ImageAsset(url=f.as_uri(), license="kogl_1", credit=f"© Seongdong-gu {i}", allow_overlay=True))
    j = store.get_job(client.org_id, job.id)
    j.photo_candidates = {"ev-seongsu-stationery": cands}
    store.save_job(client.org_id, j)

    o = (await get(client, f"/jobs/{job.id}/slides/{POPUP_SLIDE}/photos")).json()
    found = [x for x in o["options"] if x["source"] == "candidate"]
    assert [x["credit"] for x in found] == ["© Seongdong-gu 0", "© Seongdong-gu 1"]
    # The Admin's link works without a token; a tampered one doesn't.
    anon = httpx.Client(transport=client._transport, base_url=str(client.base_url))
    assert anon.get(found[1]["url"]).status_code == 200
    assert anon.get(found[1]["url"].replace("stationery-1", "stationery-0")).status_code == 404

    r = await post(client, f"/jobs/{job.id}/slides/{POPUP_SLIDE}/photo",
                   {"base_version": 1, "source": "candidate", "candidate": 1})
    assert r.status_code == 201, r.text
    assert r.json()["deck"]["slides"][POPUP_SLIDE]["image"]["credit"] == "© Seongdong-gu 1"
    out = await post(client, f"/jobs/{job.id}/slides/{POPUP_SLIDE}/photo",
                     {"base_version": 2, "source": "candidate", "candidate": 5})
    assert out.status_code == 422 and "2 vetted photo(s)" in out.json()["detail"]
    # A photo path that isn't under this Job's photos folder never gets a link.
    assert assets.photo_link(client.org_id, job.id, Path("/etc/passwd").as_uri()) is None


async def test_swaps_follow_the_edit_rules(client):
    job = await ready_job(client.org_id)
    stale = await post(client, f"/jobs/{job.id}/slides/{POPUP_SLIDE}/photo", {"base_version": 2, "source": "none"})
    assert stale.status_code == 409
    j = store.get_job(client.org_id, job.id)
    j.status = JobStatus.PUBLISHED
    store.save_job(client.org_id, j)
    done = await post(client, f"/jobs/{job.id}/slides/{POPUP_SLIDE}/photo",
                      {"base_version": 1, "source": "stock", "stock_id": "popup-paper-goods"})
    assert done.status_code == 409


# ---------------------------------------------------------------- regenerate one slide

async def test_regenerate_rewrites_one_slide_from_the_research(client):
    job = await ready_job(client.org_id, issues=[
        Issue(severity="warn", category="tone", slide_index=POPUP_SLIDE, message="old text is a bit flat"),
        Issue(severity="warn", category="tone", slide_index=FESTIVAL_SLIDE, message="keep me")])
    j = store.get_job(client.org_id, job.id)
    j.edited_text = {POPUP_SLIDE: ["body"], FESTIVAL_SLIDE: ["heading"]}
    store.save_job(client.org_id, j)

    r = await post(client, f"/jobs/{job.id}/slides/{POPUP_SLIDE}/regenerate",
                   {"base_version": 1, "instruction": "say why it's worth it"})
    assert r.status_code == 201, r.text
    saved = store.get_job(client.org_id, job.id)
    brief = next(b for b in saved.briefs if b.id == "ev-seongsu-stationery")
    assert saved.deck.slides[POPUP_SLIDE].body == brief.foreigner_tips[0]  # the fixture rewrite: from the research
    others = [(s.heading, s.body) for s in saved.deck.slides if s.index != POPUP_SLIDE]
    assert others == [(s.heading, s.body) for s in job.deck.slides if s.index != POPUP_SLIDE]
    v = _version(client.org_id, saved, 2)
    assert v.action == "regenerate" and v.rerendered == [POPUP_SLIDE]
    assert v.changes[0]["by"] == "agent" and v.changes[0]["instruction"] == "say why it's worth it"
    # The old text's notes and "edited" mark go with it; other slides keep theirs.
    assert [i.message for i in saved.issues if i.category == "tone"] == ["keep me"]
    assert saved.edited_text == {FESTIVAL_SLIDE: ["heading"]}
    assert "agent rewrite of slide 2" in saved.log[-1].message
    assert (await checked(client.org_id, job.id)).safeguard.deck_version == 2


async def test_a_rewrite_that_changes_nothing_is_refused(client):
    job = await ready_job(client.org_id)
    r = await post(client, f"/jobs/{job.id}/slides/{CTA_SLIDE}/regenerate", {"base_version": 1})
    assert r.status_code == 422 and "the same" in r.json()["detail"]


async def test_fair_use_cap(client, monkeypatch):
    monkeypatch.setattr(settings, "regenerations_per_deck", 1)
    job = await ready_job(client.org_id)
    assert (await post(client, f"/jobs/{job.id}/slides/{POPUP_SLIDE}/regenerate",
                       {"base_version": 1})).status_code == 201
    capped = await post(client, f"/jobs/{job.id}/slides/{FESTIVAL_SLIDE}/regenerate", {"base_version": 2})
    assert capped.status_code == 429 and "fair use" in capped.json()["detail"]
    # Photo swaps and text edits aren't agent calls: not capped.
    assert (await post(client, f"/jobs/{job.id}/slides/{POPUP_SLIDE}/photo",
                       {"base_version": 2, "source": "stock", "stock_id": "popup-paper-goods"})).status_code == 201


async def test_live_rewrite_gets_the_context_and_an_invented_price_is_flagged(client, monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", "live")
    calls = []

    async def fake_run_stage(agent, task, payload, out_type, **kw):
        calls.append((agent, task, payload))
        return out_type(heading="Seongsu stationery pop-up", body="Tickets are ₩50,000 and worth every won.")
    monkeypatch.setattr(slide_actions, "run_stage", fake_run_stage)
    job = await ready_job(client.org_id)
    r = await post(client, f"/jobs/{job.id}/slides/{POPUP_SLIDE}/regenerate",
                   {"base_version": 1, "instruction": "mention the price"})
    assert r.status_code == 201, r.text
    [(agent, task, payload)] = calls
    assert agent == "copywriter" and "ONE slide" in task
    assert payload["slide_index"] == POPUP_SLIDE and payload["instruction"] == "mention the price"
    assert {b["id"] for b in payload["briefs"]} >= {"ev-seongsu-stationery"} and "org_profile" in payload
    saved = store.get_job(client.org_id, job.id)
    [flag] = [i for i in saved.issues if i.category == "fact" and i.slide_index == POPUP_SLIDE]
    assert flag.severity == "block" and "₩50,000" in flag.message


async def test_a_rewrite_with_a_blocked_phrase_is_never_saved(client, monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", "live")

    async def fake_run_stage(agent, task, payload, out_type, **kw):
        return out_type(heading="Tank Day sale", body="Celebrate with 50% off.")
    monkeypatch.setattr(slide_actions, "run_stage", fake_run_stage)
    job = await ready_job(client.org_id)
    r = await post(client, f"/jobs/{job.id}/slides/{POPUP_SLIDE}/regenerate", {"base_version": 1})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "blocked"
    assert store.get_job(client.org_id, job.id).deck_version == 1


# ---------------------------------------------------------------- the photos stage keeps candidates

async def test_the_photos_stage_keeps_every_vetted_photo(tmp_path, monkeypatch):
    files = {f"https://www.yudeung.com/{n}.jpg": ("image/jpeg", _jpeg(1600, 1200)) for n in ("a", "b")}

    def handler(req: httpx.Request) -> httpx.Response:
        ctype, data = files[str(req.url)]
        return httpx.Response(200, headers={"content-type": ctype}, stream=httpx.ByteStream(data))

    async def public(host: str, port: int) -> list[str]:
        return ["93.184.215.14"]
    monkeypatch.setattr(photos.safe_fetch, "resolve", public)
    monkeypatch.setattr(photos.safe_fetch, "client",
                        lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kw))
    brief = BRIEF.model_copy(update={"images": [img(u) for u in files]})
    every, _ = await photos.collect_all([brief], OK, tmp_path)
    assert [v.remote_url.rsplit("/", 1)[-1] for v in every["ev-jinju"]] == ["a.jpg", "b.jpg"]
    first, _ = await photos.collect([brief], OK, tmp_path)
    assert first["ev-jinju"].remote_url.endswith("a.jpg")
    shutil.rmtree(tmp_path / "photos")


def test_older_jobs_load_without_candidates():
    from app.schemas import Job
    raw = {"id": "x", "prompt": "p", "created_at": "2026-10-01T00:00:00Z"}
    assert Job.model_validate(raw).photo_candidates == {}
    assert asyncio.iscoroutinefunction(slide_actions.regenerate)
