"""Deck versions and text edits (#19): an Editor edits slide text, the order, the caption and the hashtags in Review.
Each save re-renders only what changed and stores a new version. Overflow is warned about before saving, and a
blocked phrase is refused on save. The edited version can't be approved until the Safeguard has checked exactly that
version. Approval pins a version, and a stale one is refused. Other Organizations see nothing, and old Jobs read as
version 1."""

import asyncio
import json
import secrets
import shutil
import threading
import time
from datetime import UTC, datetime

import pytest
from factories import scenario_deck
from test_roles import team  # noqa: F401  (fixture)

from app import assets, deck_versions, posts, store
from app.config import settings
from app.pipeline import orchestrator, publishing
from app.renderer.html import HtmlRenderer
from app.schemas import EventBrief, Issue, JobStatus, SafeguardModelOutput
from app.services import safeguard

BRIEFS = [EventBrief.model_validate(b) for b in json.loads((settings.scenarios_dir / "good/briefs.json").read_text())]
_template: dict = {}


async def _rendered_template():
    """The good Deck rendered once per run (Playwright); each test copies the files into its own Job's folder."""
    if "dir" not in _template:
        out = settings.output_dir.parent / "deck-template"
        rendered = await HtmlRenderer(theme=settings.theme).render(scenario_deck("good"), out, BRIEFS)
        _template.update(dir=out, names=[r.path.name for r in rendered])
    return _template


async def ready_job(org_id, issues=()):
    """A Job in review with the good Deck rendered on disk and an "allow" verdict for exactly those files, as the
    pipeline leaves it."""
    t = await _rendered_template()
    job = orchestrator.new_job(secrets.token_hex(4), "Seoul this weekend")
    folder = settings.output_dir / job.id
    shutil.copytree(t["dir"], folder)
    job.status, job.deck, job.briefs = JobStatus.READY_FOR_REVIEW, scenario_deck("good"), BRIEFS
    job.deck.job_id = job.id
    job.slide_urls = [f"/assets/{job.id}/{n}" for n in t["names"]]
    job.issues = list(issues)
    job.safeguard = await safeguard.check(job.deck, slide_paths=assets.slide_files(job), today=datetime.now(UTC).date(),
                                          model=None)
    job.safeguard.checked_by = ["rules", "model"]
    return await asyncio.to_thread(store.save_job, org_id, job)


def body(job, **changes) -> dict:
    """An edit request from the Job's current Deck: every slide in order, plus `changes`."""
    slides = [{"from_index": s.index, "heading": s.heading, "body": s.body} for s in job.deck.slides]
    return {"base_version": job.deck_version, "slides": slides, **changes}


async def post(client, path, json_):
    return await asyncio.to_thread(client.post, path, json=json_)


async def get(client, path):
    return await asyncio.to_thread(client.get, path)


async def checked(org_id, job_id, timeout=20.0):
    """Wait for the Safeguard re-check (a DBOS workflow) to record its verdict."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = await asyncio.to_thread(store.get_job, org_id, job_id)
        if not job.safeguard_pending:
            return job
        await asyncio.sleep(0.05)
    raise AssertionError("the safeguard re-check never finished")


@pytest.fixture
def no_publish(monkeypatch):
    calls = []

    async def fake(*args, **kwargs):
        calls.append((args, kwargs))
    monkeypatch.setattr(publishing, "start", fake)
    return calls


def audit_of(admin, job_id, action):
    return [e for e in admin.get("/audit").json() if e["target"] == job_id and e["action"] == action]


# ---------------------------------------------------------------- saving a version

async def test_an_edit_stores_a_version_and_re_renders_only_the_changed_slide(client):
    job = await ready_job(client.org_id)
    req = body(job)
    req["slides"][4]["body"] = "Tap your T-money card.\nCarry cash for street food."
    r = await post(client, f"/jobs/{job.id}/versions", req)
    assert r.status_code == 201, r.text
    saved = store.get_job(client.org_id, job.id)
    assert saved.deck_version == 2 and saved.deck.slides[4].body.startswith("Tap your T-money")
    before, after = [u.rsplit("/", 1)[-1] for u in job.slide_urls], [u.rsplit("/", 1)[-1] for u in saved.slide_urls]
    assert [i for i in range(7) if before[i] != after[i]] == [4]  # one slide re-rendered, the rest reused
    assert (settings.output_dir / job.id / before[4]).is_file()  # version 1's file is untouched
    assert (settings.output_dir / job.id / after[4]).is_file()
    assert saved.safeguard is None or saved.safeguard.deck_version == 2  # never version 1's verdict
    history = (await get(client, f"/jobs/{job.id}/versions")).json()
    assert [(v["version"], v["current"]) for v in history] == [(2, True), (1, False)]
    assert history[0]["edited_by_email"] and history[0]["rerendered"] == [4] and history[1]["edited_by"] is None
    assert history[1]["deck"]["slides"][4]["body"] == job.deck.slides[4].body
    done = await checked(client.org_id, job.id)
    assert done.safeguard.decision == "allow" and done.safeguard.deck_version == 2
    assert done.safeguard.content_hash == safeguard.content_hash(done.deck, assets.slide_files(done))


async def test_reordering_re_renders_the_moved_slides_and_moves_their_issues(client):
    fact = Issue(severity="warn", category="fact", slide_index=2, message="check the date")
    job = await ready_job(client.org_id, [fact])
    req = body(job)
    req["slides"][2], req["slides"][3] = req["slides"][3], req["slides"][2]
    r = await post(client, f"/jobs/{job.id}/versions", req)
    assert r.status_code == 201, r.text
    saved = store.get_job(client.org_id, job.id)
    assert [s.heading for s in saved.deck.slides][2:4] == ["Modern Hanbok Exhibition", "Seoul Lantern Walk"]
    assert [s.index for s in saved.deck.slides] == list(range(7))
    v = (await get(client, f"/jobs/{job.id}/versions")).json()[0]
    assert set(v["rerendered"]) >= {2, 3} and v["changes"][0]["field"] == "order"
    assert [i.slide_index for i in saved.issues if i.category == "fact"] == [3]  # followed its slide


async def test_caption_and_hashtags_are_edited_without_re_rendering_slides(client):
    job = await ready_job(client.org_id)
    r = await post(client, f"/jobs/{job.id}/versions",
                   {"base_version": 1, "caption": "Three easy things in Seoul.", "hashtags": ["seoul", "#koreatravel"]})
    assert r.status_code == 201, r.text
    saved = store.get_job(client.org_id, job.id)
    assert saved.deck.caption == "Three easy things in Seoul.\n#seoul #koreatravel"
    assert saved.deck.hashtags == ["#seoul", "#koreatravel"] and saved.slide_urls == job.slide_urls
    assert (await get(client, f"/jobs/{job.id}/versions")).json()[0]["rerendered"] == []
    bad = await post(client, f"/jobs/{job.id}/versions", {"base_version": 2, "hashtags": ["two words"]})
    assert bad.status_code == 422
    six = await post(client, f"/jobs/{job.id}/versions", {"base_version": 2, "hashtags": list("abcdef")})
    assert six.status_code == 422  # at most 5


def test_split_and_compose_caption():
    text, tags = deck_versions.split_caption("Line one #inline\nLine two\n#seoul #korea\n#travel")
    assert text == "Line one #inline\nLine two" and tags == ["#seoul", "#korea", "#travel"]
    assert deck_versions.compose_caption(text, tags) == "Line one #inline\nLine two\n#seoul #korea #travel"


async def test_the_audit_log_keeps_before_and_after(client):
    job = await ready_job(client.org_id)
    req = body(job, caption="New words.")
    req["slides"][0]["heading"] = "This weekend in Busan"
    assert (await post(client, f"/jobs/{job.id}/versions", req)).status_code == 201
    [e] = await asyncio.to_thread(audit_of, client, job.id, "deck.edited")
    assert e["actor"] == str(client.user_id) and e["detail"]["version"] == 2 and e["detail"]["base_version"] == 1
    changes = {c["field"]: c for c in e["detail"]["changes"]}
    assert changes["slide.heading"]["before"] == "This weekend in Seoul"
    assert changes["slide.heading"]["after"] == "This weekend in Busan" and changes["slide.heading"]["slide"] == 0
    assert changes["caption"]["after"] == "New words." and changes["caption"]["before"].startswith("3 things")


async def test_a_save_on_top_of_a_newer_version_is_refused(client):
    job = await ready_job(client.org_id)
    first, second = body(job), body(job)
    first["slides"][6]["body"] = "Save it now."
    second["slides"][6]["body"] = "Share it with a friend."
    assert (await post(client, f"/jobs/{job.id}/versions", first)).status_code == 201
    r = await post(client, f"/jobs/{job.id}/versions", second)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "stale"
    assert store.get_job(client.org_id, job.id).deck.slides[6].body == "Save it now."


async def test_nothing_changed_and_bad_orders_are_refused(client):
    job = await ready_job(client.org_id)
    assert (await post(client, f"/jobs/{job.id}/versions", body(job))).status_code == 422
    req = body(job)
    req["slides"][1]["from_index"] = 0  # slide 0 twice, slide 1 gone
    assert (await post(client, f"/jobs/{job.id}/versions", req)).status_code == 422


async def test_only_a_deck_in_review_can_be_edited(client):
    job = await ready_job(client.org_id)
    for status in (JobStatus.BLOCKED, JobStatus.PUBLISHING, JobStatus.PUBLISHED, JobStatus.REJECTED):
        job.status = status
        await asyncio.to_thread(store.save_job, client.org_id, job)
        req = body(job)
        req["slides"][6]["body"] = "x"
        assert (await post(client, f"/jobs/{job.id}/versions", req)).status_code == 409, status


# ---------------------------------------------------------------- warnings and refusals before saving

async def test_overflowing_text_is_warned_about_before_saving(client):
    job = await ready_job(client.org_id)
    req = body(job)
    req["slides"][5]["body"] = " ".join(["Take exit 4, walk past the fountain and turn left at the bakery."] * 30)
    r = await post(client, f"/jobs/{job.id}/versions", req)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "overflow"
    assert "slide 6" in r.json()["detail"]["message"] and r.json()["detail"]["issues"][0]["slide_index"] == 5
    assert store.get_job(client.org_id, job.id).deck_version == 1  # nothing saved
    r = await post(client, f"/jobs/{job.id}/versions", {**req, "allow_overflow": True})
    assert r.status_code == 201
    saved = store.get_job(client.org_id, job.id)
    assert any(i.category == "visual" and i.severity == "block" and i.slide_index == 5 for i in saved.issues)
    [e] = await asyncio.to_thread(audit_of, client, job.id, "deck.edited")
    assert e["detail"]["overflow_accepted"] is True


async def test_a_blocked_phrase_is_refused_on_save_and_explained(client):
    job = await ready_job(client.org_id)
    req = body(job)
    req["slides"][2]["body"] = "Join the Tank Day sale"
    r = await post(client, f"/jobs/{job.id}/versions", req)
    assert r.status_code == 422
    d = r.json()["detail"]
    assert d["code"] == "blocked" and d["message"].startswith('Not saved: Blocked phrase "Tank Day"')
    [f] = d["findings"]
    assert f["slide_index"] == 2 and f["excerpt"] == "Tank Day" and f["explanation"] and f["fix"]
    after = store.get_job(client.org_id, job.id)
    assert after.deck_version == 1 and after.safeguard.decision == "allow"  # the good Deck is untouched
    [e] = await asyncio.to_thread(audit_of, client, job.id, "safeguard.edit_blocked")
    assert e["detail"]["stage"] == "edit" and e["detail"]["findings"][0]["excerpt"] == "Tank Day"
    cap = await post(client, f"/jobs/{job.id}/versions", {"base_version": 1, "hashtags": ["seoul", "tankday"]})
    assert cap.status_code == 422 and cap.json()["detail"]["findings"][0]["location"] == "hashtags"


async def test_a_credential_in_the_text_is_refused(client):
    job = await ready_job(client.org_id)
    req = body(job)
    req["slides"][6]["body"] = "key sk-ant-abcdefghijklmnop"
    r = await post(client, f"/jobs/{job.id}/versions", req)
    assert r.status_code == 422 and "credential" in r.json()["detail"]


# ---------------------------------------------------------------- the Safeguard re-check and approval

async def test_an_edited_version_cant_be_approved_until_its_check_passes(client, no_publish, monkeypatch):
    job = await ready_job(client.org_id)
    gate = threading.Event()
    real = orchestrator.run_safeguard

    async def slow(*a, **k):
        await asyncio.to_thread(gate.wait, 20)
        return await real(*a, **k)
    monkeypatch.setattr(orchestrator, "run_safeguard", slow)
    req = body(job)
    req["slides"][6]["body"] = "Save it now."
    assert (await post(client, f"/jobs/{job.id}/versions", req)).status_code == 201
    pending = store.get_job(client.org_id, job.id)
    assert pending.safeguard_pending and pending.safeguard is None
    assert client.get(f"/jobs/{job.id}").json()["safeguard_pending"] is True
    r = await post(client, f"/jobs/{job.id}/approve", {"deck_version": 2})
    assert r.status_code == 409 and "still checking version 2" in r.json()["detail"]
    old = await post(client, f"/jobs/{job.id}/approve", {"deck_version": 1})
    assert old.status_code == 409 and "version 2 now" in old.json()["detail"]  # stale: the edit came in between
    assert no_publish == []
    gate.set()
    await checked(client.org_id, job.id)
    r = await post(client, f"/jobs/{job.id}/approve", {"deck_version": 2})
    assert r.status_code == 200, r.text
    [((org_id, post_id), _)] = no_publish
    assert posts.raw(org_id, post_id)["deck_version"] == 2  # the Post pins the approved version (#21)
    approved = await asyncio.to_thread(audit_of, client, job.id, "deck.approved")
    assert approved[0]["detail"]["deck_version"] == 2
    refused = await asyncio.to_thread(audit_of, client, job.id, "deck.approve_refused")
    assert refused[0]["detail"]["why"] == "safeguard_checking"


async def test_a_version_the_full_check_blocks_cant_be_approved_but_can_be_fixed(client, no_publish, monkeypatch):
    job = await ready_job(client.org_id)
    real = orchestrator.run_safeguard

    async def blocking(org_id, job_id, deck, paths, fx=None, *, use_model=True):
        async def model(payload, files):
            return SafeguardModelOutput(decision="block", summary="Sexualised framing of a venue.")
        return await safeguard.check(deck, slide_paths=paths, today=datetime.now(UTC).date(), model=model)
    monkeypatch.setattr(orchestrator, "run_safeguard", blocking)
    req = body(job)
    req["slides"][6]["body"] = "Hot nights out."
    assert (await post(client, f"/jobs/{job.id}/versions", req)).status_code == 201
    blocked = await checked(client.org_id, job.id)
    assert blocked.status == JobStatus.READY_FOR_REVIEW and blocked.safeguard.decision == "block"
    assert blocked.safeguard.deck_version == 2
    for reason in (None, "I checked it."):
        r = await post(client, f"/jobs/{job.id}/approve", {"deck_version": 2, "override_reason": reason})
        assert r.status_code == 409 and "Edit this version again" in r.json()["detail"]
    [e] = await asyncio.to_thread(audit_of, client, job.id, "safeguard.blocked")
    assert e["detail"]["stage"] == "edit" and e["detail"]["deck_version"] == 2
    monkeypatch.setattr(orchestrator, "run_safeguard", real)
    req = body(blocked)
    req["slides"][6]["body"] = "Save it for Friday."
    assert (await post(client, f"/jobs/{job.id}/versions", req)).status_code == 201
    fixed = await checked(client.org_id, job.id)
    assert fixed.safeguard.decision == "allow" and fixed.deck_version == 3
    assert (await post(client, f"/jobs/{job.id}/approve", {"deck_version": 3})).status_code == 200


async def test_a_verdict_for_other_content_cant_approve(client, no_publish):
    job = await ready_job(client.org_id)
    job.deck.slides[1].heading = "Changed behind the verdict's back"
    await asyncio.to_thread(store.save_job, client.org_id, job)
    r = await post(client, f"/jobs/{job.id}/approve", {"deck_version": 1})
    assert r.status_code == 409 and "not for version 1" in r.json()["detail"] and no_publish == []


async def test_publishing_stops_if_the_deck_was_edited_after_approval(client):
    job = await ready_job(client.org_id)
    req = body(job)
    req["slides"][6]["body"] = "Save it now."
    assert (await post(client, f"/jobs/{job.id}/versions", req)).status_code == 201
    await checked(client.org_id, job.id)
    # A Post approved for version 1 before the edit landed: the publisher stops before any upload.
    by = {"id": str(client.user_id), "email": "x@example.com"}
    stale = await asyncio.to_thread(lambda: publishing.create(
        client.org_id, store.get_job(client.org_id, job.id), mode="mock", by=by, account=None, deck_version=1))
    await publishing.run(client.org_id, stale.id)
    stale = await asyncio.to_thread(posts.get, client.org_id, stale.id)
    after = store.get_job(client.org_id, job.id)
    assert stale.state == "failed" and "edited after approval" in stale.last_error
    assert after.status == JobStatus.READY_FOR_REVIEW
    fresh = await publishing.publish_now(client.org_id, job.id, "mock", by=by)
    assert fresh.state == "published" and fresh.deck_version == 2
    assert store.get_job(client.org_id, job.id).status == JobStatus.PUBLISHED


async def test_an_edited_slide_is_served_through_a_signed_link(client):
    job = await ready_job(client.org_id)
    req = body(job)
    req["slides"][6]["body"] = "Save it now."
    saved = (await post(client, f"/jobs/{job.id}/versions", req)).json()
    link = saved["slide_urls"][6]
    assert ".jpg?exp=" in link and link.split("?")[0].rsplit("/", 1)[-1].count(".") == 2
    r = await get(client, link)
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"


async def test_a_check_left_behind_by_a_restart_runs_again(client):
    job = await ready_job(client.org_id)
    job.deck_version, job.safeguard, job.safeguard_pending = 2, None, True  # no workflow was ever queued
    await asyncio.to_thread(store.save_job, client.org_id, job)
    await asyncio.to_thread(deck_versions.recover_checks)
    done = await checked(client.org_id, job.id)
    assert done.safeguard.decision == "allow" and done.safeguard.deck_version == 2


# ---------------------------------------------------------------- old Jobs, Roles, Organizations

async def test_an_old_job_reads_as_version_1_and_approving_stores_it(client, no_publish):
    job = await ready_job(client.org_id)
    raw = json.loads(job.model_dump_json(exclude={"deck_version", "safeguard_pending"}))
    loaded = type(job).model_validate(raw)  # a document saved before versions existed
    assert loaded.deck_version == 1 and not loaded.safeguard_pending
    [v] = (await get(client, f"/jobs/{job.id}/versions")).json()
    assert v["version"] == 1 and v["current"] and v["edited_by"] is None
    assert (await post(client, f"/jobs/{job.id}/approve", {"deck_version": 1})).status_code == 200
    with store.tenant(client.org_id) as c:
        assert c.execute("select version from deck_versions where job_id = %s", (job.id,)).fetchall() == [(1,)]


async def test_only_editors_and_up_can_edit_and_every_member_can_read_history(team):  # noqa: F811
    job = await ready_job(team["admin"].org_id)
    req = body(job)
    req["slides"][6]["body"] = "Save it now."
    r = await post(team["viewer"], f"/jobs/{job.id}/versions", req)
    assert (r.status_code, r.json()["detail"]) == (403, "editor role required")
    assert (await get(team["viewer"], f"/jobs/{job.id}/versions")).status_code == 200
    assert (await post(team["editor"], f"/jobs/{job.id}/versions", req)).status_code == 201
    assert (await post(team["approver"], f"/jobs/{job.id}/versions", {"base_version": 2, "caption": "Hi."})).status_code == 201
    assert store.get_job(team["admin"].org_id, job.id).deck_version == 3


async def test_another_organization_cant_read_or_edit_versions(sign_in):
    a, b = sign_in(org="A"), sign_in(org="B")
    job = await ready_job(a.org_id)
    req = body(job)
    req["slides"][6]["body"] = "Save it now."
    assert (await post(a, f"/jobs/{job.id}/versions", req)).status_code == 201
    assert (await get(b, f"/jobs/{job.id}/versions")).status_code == 404
    req["base_version"] = 2
    req["slides"][6]["body"] = "B was here"
    assert (await post(b, f"/jobs/{job.id}/versions", req)).status_code == 404
    with store.tenant(b.org_id) as c:  # RLS: B sees none of A's rows, whatever the SQL says
        assert c.execute("select count(*) from deck_versions").fetchone()[0] == 0
    with pytest.raises(Exception, match="row-level security"), store.tenant(b.org_id) as c:
        c.execute("insert into deck_versions (job_id, version, doc) values (%s, 9, '{}')", (job.id,))
    with pytest.raises(Exception, match="permission denied"), store.tenant(a.org_id) as c:
        c.execute("update deck_versions set version = 7 where job_id = %s", (job.id,))  # insert-only
    assert store.get_job(a.org_id, job.id).deck.slides[6].body == "Save it now."
    assert [v["version"] for v in (await get(a, f"/jobs/{job.id}/versions")).json()] == [2, 1]


# ---------------------------------------------------------------- fit check while typing

async def test_the_fit_check_measures_one_slide_as_typed_and_saves_nothing(team):  # noqa: F811
    job = await ready_job(team["admin"].org_id)
    req = body(job)
    r = await post(team["editor"], f"/jobs/{job.id}/fit", {**req, "position": 1})
    assert r.status_code == 200, r.text
    fit = r.json()
    assert fit["fits"] and fit["position"] == 1
    heading = next(b for b in fit["blocks"] if b["selector"] == "event.heading")
    assert heading["lines"] >= 1 and not heading["overflow"]
    req["slides"][5]["body"] = " ".join(["Take exit 4, walk past the fountain and turn left at the bakery."] * 30)
    over = (await post(team["editor"], f"/jobs/{job.id}/fit", {**req, "position": 5})).json()
    assert not over["fits"] and any(b["overflow"] for b in over["blocks"])
    assert store.get_job(team["admin"].org_id, job.id).deck_version == 1  # nothing saved
    assert (await post(team["viewer"], f"/jobs/{job.id}/fit", {**req, "position": 1})).status_code == 403


async def test_another_organization_cant_fit_check(sign_in):
    a, b = sign_in(org="A"), sign_in(org="B")
    job = await ready_job(a.org_id)
    assert (await post(b, f"/jobs/{job.id}/fit", {**body(job), "position": 0})).status_code == 404


# ---------------------------------------------------------------- fact sources on edited text (#25)

async def test_text_a_person_typed_says_re_check_sources_and_follows_its_slide(client):
    job = await ready_job(client.org_id)
    before = {s["slide_index"]: s for s in (await get(client, f"/jobs/{job.id}")).json()["slide_sources"]}
    assert before[1]["edited"] == [] and next(f for f in before[1]["facts"] if f["field"] == "title")["sources"]
    req = body(job)
    req["slides"][1]["heading"] = "Stationery pop-up, now in Hongdae"
    req["slides"][4]["body"] = "Tap your T-money card."
    saved = (await post(client, f"/jobs/{job.id}/versions", req)).json()
    s = {x["slide_index"]: x for x in saved["slide_sources"]}
    title = next(f for f in s[1]["facts"] if f["field"] == "title")
    assert s[1]["edited"] == ["heading"] and title["edited"] and title["sources"] == []
    assert title["value"] == "Stationery pop-up, now in Hongdae"
    assert s[4]["edited"] == ["body"] and s[0]["edited"] == []
    # Reordered, the flag moves with its slide; the stored Job keeps no view-only sources.
    req = body(store.get_job(client.org_id, job.id))
    req["slides"][1], req["slides"][2] = req["slides"][2], req["slides"][1]
    assert (await post(client, f"/jobs/{job.id}/versions", req)).status_code == 201
    s = {x["slide_index"]: x for x in (await get(client, f"/jobs/{job.id}")).json()["slide_sources"]}
    assert s[2]["edited"] == ["heading"] and s[1]["edited"] == []
    assert store.get_job(client.org_id, job.id).slide_sources == []
