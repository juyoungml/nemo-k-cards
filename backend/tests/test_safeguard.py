"""Safeguard (#43, ADR 0004): the one binding check. Host rules + the safeguard agent decide allow/block, the gate
fails closed, a block explains itself, and nothing gets around it: not an Override reason, not any Role, not a
caption edit at Approve or during publishing, not another Organization, not a server restart."""

import asyncio
import secrets
from collections import Counter
from datetime import date

import pytest
from dbos import DBOS, SetEnqueueOptions, SetWorkflowID
from factories import allowed_job, blocked_job, connect_account, scenario_deck
from test_durable_jobs import Crash, restart_dbos, wait_for, workflow_done
from test_roles import add_member, team  # noqa: F401  (fixture)

from app import posts, store
from app.config import settings
from app.pipeline import agent_runner, durable, orchestrator, publishing
from app.schemas import CardDeck, JobStatus, LinkCheck, SafeguardFinding, SafeguardModelOutput
from app.services import image_host, link_checker, publisher, safeguard

DONE = (JobStatus.READY_FOR_REVIEW, JobStatus.BLOCKED, JobStatus.FAILED)


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(orchestrator, "FIXTURE_DELAY", 0)


@pytest.fixture
def no_network(monkeypatch):
    async def fake_check(url, client=None):
        return LinkCheck(url=url, status="ok", http_code=200)
    monkeypatch.setattr(link_checker, "check_url", fake_check)


@pytest.fixture
def no_publish(monkeypatch):
    calls = []

    async def fake(*args, **kwargs):
        calls.append((args, kwargs))
    monkeypatch.setattr(publishing, "start", fake)
    return calls


def model_says(decision: str, *findings: SafeguardFinding, summary: str = "model summary"):
    async def call(payload, files):
        call.payload = payload
        return SafeguardModelOutput(decision=decision, summary=summary, findings=list(findings))
    return call


async def check(d: CardDeck, model):
    return await safeguard.check(d, slide_paths=[], today=date(2026, 10, 29), model=model)


def actions(admin, job_id) -> list[str]:
    return [e["action"] for e in admin.get("/audit").json() if e["target"] == job_id]


def audit_of(admin, job_id, action) -> dict:
    return next(e for e in admin.get("/audit").json() if e["target"] == job_id and e["action"] == action)


# ---------------------------------------------------------------- policy and rules

def test_policy_files_parse_and_every_block_phrase_has_a_category():
    p = safeguard.load_policy()
    assert len(p.phrases) >= 7 and len(p.version) == 7
    assert "## Block" in p.markdown and "date_gate_days" in p.sensitive_yaml
    cats = {ph.note: ph.category for ph in p.phrases}
    assert cats["use East Sea / Dokdo"] == "territorial_cultural"
    assert cats["스타벅스 탱크데이 (2026-05-18)"] == "historical_tragedy"


def test_a_block_phrase_without_a_valid_category_fails_loudly(tmp_path, monkeypatch):
    bad = tmp_path / "topics.yaml"
    bad.write_text('phrases:\n  - { pattern: "x", severity: block, category: nonsense }\n')
    monkeypatch.setattr(safeguard, "SENSITIVE_YAML", bad)
    with pytest.raises(ValueError):
        safeguard.load_policy()


@pytest.mark.parametrize("text", ["Join our TANK DAY event", "책상을 탁 치니", "Cruise on the Sea of Japan",
                                  "광주 폭동 기념 세일"])
def test_rule_phrases_are_caught_in_slides(text):
    d = scenario_deck("good")
    d.slides[2].body = text
    [f] = safeguard.rule_findings(d)
    assert f.severity == "block" and f.location == "slide" and f.slide_index == 2
    assert f.excerpt in text and f.explanation and f.fix and f.rule.startswith(safeguard.RULE_PREFIX)


def test_rule_phrases_are_caught_in_caption_and_hashtags():
    d = scenario_deck("good")
    d.caption = "A day in Seoul\n#seoul #tankday"
    [f] = safeguard.rule_findings(d)
    assert f.location == "hashtags" and f.excerpt.lower() == "tankday" and f.category == "historical_tragedy"


def test_good_deck_has_no_rule_hits():
    assert safeguard.rule_findings(scenario_deck("good")) == []


# ---------------------------------------------------------------- the host decides

async def test_a_rule_hit_blocks_even_when_the_model_allows():
    v = await check(scenario_deck("bad"), model_says("allow", summary="Looks fine."))
    assert v.decision == "block" and v.model_decision == "allow"
    assert v.summary.startswith('Blocked phrase "Tank Day"')
    assert v.checked_by == ["rules", "model"]
    assert any(f.excerpt == "Tank Day" for f in v.findings)  # the model can't remove a rule hit


async def test_a_model_block_finding_blocks_even_if_its_decision_says_allow():
    f = SafeguardFinding(category="hate", severity="block", location="caption", excerpt="x", rule="hate",
                         explanation="Demeans a nationality.")
    v = await check(scenario_deck("good"), model_says("allow", f, summary=""))
    assert v.decision == "block" and v.summary == "Blocked: Demeans a nationality."


async def test_warnings_alone_are_allowed():
    f = SafeguardFinding(category="other", severity="warn", location="caption", excerpt="x", rule="taste",
                         explanation="Edgy.")
    v = await check(scenario_deck("good"), model_says("allow", f, summary="Safe, one warning."))
    assert v.decision == "allow" and v.summary == "Safe, one warning." and len(v.findings) == 1


async def test_a_failing_model_blocks_fail_closed():
    async def broken(payload, files):
        raise RuntimeError("safeguard timed out after 300s")
    v = await check(scenario_deck("good"), broken)
    assert v.decision == "block" and "could not run" in v.summary
    assert v.model_error.startswith("safeguard timed out") and v.checked_by == ["rules"]


async def test_the_model_gets_the_policy_and_the_rule_hits():
    m = model_says("block")
    await check(scenario_deck("bad"), m)
    assert m.payload["rule_findings"][0]["excerpt"] == "Tank Day"
    assert "phrases:" in m.payload["sensitive_topics_yaml"] and "## Block" in m.payload["policy_md"]
    assert m.payload["today"] == "2026-10-29"


async def test_the_live_agent_runs_through_the_runner_under_the_organization_with_local_slides(
        org, monkeypatch, tmp_path):
    """Live path: the `safeguard` subagent via run_stage, with the Job's Organization and the rendered files on this
    host (the runner uploads them into the sandbox), never a public link. An agent error blocks."""
    slides = [tmp_path / "slide-00.jpg", tmp_path / "slide-01.jpg"]
    for i, f in enumerate(slides):
        f.write_bytes(bytes([i]))
    seen = {}

    async def run_stage(agent, task, payload, out_type, *, files=(), job_id=None, org_id):
        seen.update(agent=agent, out_type=out_type, files=list(files), org_id=org_id, paths=payload["slide_paths"])
        raise agent_runner.AgentError("safeguard timed out after 300s")
    monkeypatch.setattr(orchestrator, "run_stage", run_stage)
    v = await orchestrator.run_safeguard(org, "j1", scenario_deck("good"), slides)
    assert seen == {"agent": "safeguard", "out_type": SafeguardModelOutput, "files": slides, "org_id": org,
                    "paths": [str(f) for f in slides]}
    assert v.decision == "block" and v.model_error and "timed out" in v.model_error
    assert agent_runner.TOOLS["safeguard"] == ["Read"]


async def test_the_verdict_is_tied_to_the_text_and_the_images():
    d = scenario_deck("good")
    v = await check(d, None)
    assert v.content_hash == safeguard.content_hash(d)
    assert safeguard.content_hash(d.model_copy(update={"caption": d.caption + "!"})) != v.content_hash
    swapped = d.model_copy(deep=True)
    swapped.slides[1].stock_id = "festival"
    assert safeguard.content_hash(swapped) != v.content_hash


# ---------------------------------------------------------------- the pipeline (fixture mode)

async def test_a_failing_safeguard_agent_blocks_the_job(org, no_network, monkeypatch):
    """Fail closed end to end: the stand-in for the agent raises, the Job ends BLOCKED and says why."""
    real = orchestrator._scenario

    def scenario(name="good"):
        load = real(name)

        def fx(fname):
            if fname == "safeguard.json":
                raise TimeoutError("safeguard timed out after 300s")
            return load(fname)
        return fx
    monkeypatch.setattr(orchestrator, "_scenario", scenario)
    job = store.save_job(org, orchestrator.new_job(secrets.token_hex(3), "fail closed"))
    await orchestrator.run_job(org, job.id)
    job = store.get_job(org, job.id)
    assert job.status == JobStatus.BLOCKED
    assert job.safeguard.model_error and "could not run" in job.safeguard.summary
    assert any("fail closed" in line.message for line in job.log)


async def test_a_blocked_pipeline_job_is_audited_for_the_member_who_started_it(client, no_network):
    r = await asyncio.to_thread(client.post, "/jobs", json={"prompt": "Seoul pop-ups", "scenario": "bad"})
    job = await wait_for(client.org_id, r.json()["id"], DONE)
    assert job.status == JobStatus.BLOCKED
    e = await asyncio.to_thread(audit_of, client, job.id, "safeguard.blocked")
    assert e["actor"] == str(client.user_id) and e["detail"]["stage"] == "pipeline"
    assert {f["excerpt"] for f in e["detail"]["findings"]} >= {"Tank Day"}
    assert e["detail"]["checked_by"] == ["rules", "model"] and e["detail"]["policy_version"]


# ---------------------------------------------------------------- approve / reject

def test_a_blocked_deck_cant_be_approved_by_any_role_even_with_an_override_reason(team, no_publish):  # noqa: F811
    job = store.save_job(team["admin"].org_id, blocked_job())
    for role in ("approver", "admin"):
        r = team[role].post(f"/jobs/{job.id}/approve",
                            json={"override_reason": "I checked it myself, it's fine.", "mode": "mock"})
        assert r.status_code == 409, role
        assert "blocked by safeguard" in r.json()["detail"] and "Mocks the 5·18" in r.json()["detail"]
    for role in ("viewer", "editor"):  # can't approve anything: the Role check comes first
        assert team[role].post(f"/jobs/{job.id}/approve", json={"override_reason": "x"}).status_code == 403
    assert no_publish == [] and store.get_job(team["admin"].org_id, job.id).status == JobStatus.BLOCKED
    refused = [e for e in team["admin"].get("/audit").json() if e["target"] == job.id]
    assert [e["action"] for e in refused] == ["deck.approve_refused"] * 2
    assert refused[0]["detail"]["why"] == "blocked_by_safeguard"
    assert refused[0]["detail"]["override_reason"] == "I checked it myself, it's fine."
    assert {e["actor"] for e in refused} == {str(team["approver"].user_id), str(team["admin"].user_id)}


def test_a_block_verdict_wins_even_if_the_status_says_ready(client, no_publish):
    job = blocked_job()
    job.status = JobStatus.READY_FOR_REVIEW
    store.save_job(client.org_id, job)
    assert client.post(f"/jobs/{job.id}/approve", json={}).status_code == 409 and no_publish == []


def test_a_blocked_deck_can_be_rejected(client):
    job = store.save_job(client.org_id, blocked_job())
    r = client.post(f"/jobs/{job.id}/reject", json={"reason": "harmful"})
    assert r.status_code == 200 and r.json()["status"] == "REJECTED"
    assert actions(client, job.id) == ["deck.rejected"]


def test_a_job_without_a_verdict_cant_be_approved(client, no_publish):
    job = allowed_job()
    job.safeguard = None
    store.save_job(client.org_id, job)
    r = client.post(f"/jobs/{job.id}/approve", json={})
    assert r.status_code == 409 and "no safeguard verdict" in r.json()["detail"] and no_publish == []
    assert audit_of(client, job.id, "deck.approve_refused")["detail"]["why"] == "no_safeguard_verdict"


def test_an_allowed_deck_is_approved_and_publishing_knows_the_approver(client, no_publish):
    job = store.save_job(client.org_id, allowed_job())
    assert client.post(f"/jobs/{job.id}/approve", json={}).status_code == 200
    [((org_id, post_id), _)] = no_publish
    post = posts.raw(client.org_id, post_id)
    assert org_id == client.org_id and post["job_id"] == job.id and post["mode"] == "mock"
    assert post["approved_by"] == client.user_id and post["caption"] is None


def test_a_caption_edit_with_a_blocked_phrase_is_refused_at_approve_and_explained(client, no_publish):
    job = store.save_job(client.org_id, allowed_job())
    r = client.post(f"/jobs/{job.id}/approve", json={"caption": "탱크데이 특가! #seoul"})
    assert r.status_code == 422 and "blocked by safeguard" in r.json()["detail"] and no_publish == []
    saved = store.get_job(client.org_id, job.id)
    assert saved.status == JobStatus.READY_FOR_REVIEW and saved.deck.caption == job.deck.caption
    assert saved.safeguard.decision == "allow"  # the original Deck is untouched and still approvable
    [f] = saved.blocked_edit.verdict.findings
    assert f.location == "caption" and f.excerpt == "탱크데이" and f.explanation and f.fix
    e = audit_of(client, job.id, "safeguard.edit_blocked")
    assert e["detail"]["caption"] == "탱크데이 특가! #seoul" and e["detail"]["stage"] == "approve"
    assert client.post(f"/jobs/{job.id}/approve", json={}).status_code == 200  # the original still goes


def test_a_graph_post_of_an_edit_is_refused_on_a_fixture_server(client, no_publish, monkeypatch):
    monkeypatch.setattr(settings, "publish_mode", "graph")
    connect_account(client.org_id)
    job = store.save_job(client.org_id, allowed_job())
    r = client.post(f"/jobs/{job.id}/approve", json={"caption": "Safe new caption #seoul"})
    assert r.status_code == 409 and "safeguard model" in r.json()["detail"] and no_publish == []


# ---------------------------------------------------------------- publishing re-checks what will be posted

async def test_a_caption_edit_the_full_check_blocks_keeps_the_original_deck(org, monkeypatch):
    job = store.save_job(org, allowed_job())
    original = job.deck.caption
    uploads = []
    monkeypatch.setattr(image_host, "exposed", lambda *a, **k: uploads.append(a))

    async def blocking(org_id, job_id, deck, paths, fx=None, *, use_model=True):
        return await safeguard.check(deck, slide_paths=paths, today=date(2026, 10, 29),
                                     model=model_says("block", summary="Sexualised framing of a venue."))
    monkeypatch.setattr(orchestrator, "run_safeguard", blocking)
    post = await publishing.publish_now(org, job.id, "Edited caption #seoul", "dryrun",
                                        account=connect_account(org))
    job = store.get_job(org, job.id)
    assert uploads == [] and post.state == "failed"
    assert job.status == JobStatus.READY_FOR_REVIEW
    assert job.deck.caption == original and job.safeguard.decision == "allow"
    assert job.blocked_edit.caption == "Edited caption #seoul"
    assert job.blocked_edit.verdict.summary == "Sexualised framing of a venue."
    assert "Sexualised" in post.last_error and post.progress.step == "failed"
    # The original Deck is still approvable, and unchanged content isn't checked again.
    monkeypatch.setattr(orchestrator, "run_safeguard", None)
    assert (await publishing.publish_now(org, job.id, None, "mock")).state == "published"
    assert store.get_job(org, job.id).status == JobStatus.PUBLISHED


async def test_a_blocked_edit_during_publishing_is_audited_for_the_approver(client, monkeypatch):
    job = store.save_job(client.org_id, allowed_job())

    async def blocking(org_id, job_id, deck, paths, fx=None, *, use_model=True):
        return await safeguard.check(deck, slide_paths=paths, today=date(2026, 10, 29),
                                     model=model_says("block", summary="Mocks a disaster."))
    monkeypatch.setattr(orchestrator, "run_safeguard", blocking)
    by = {"id": str(client.user_id), "email": "x@example.com"}
    await publishing.publish_now(client.org_id, job.id, "Edited caption #seoul", "mock", by=by)
    e = await asyncio.to_thread(audit_of, client, job.id, "safeguard.edit_blocked")
    assert e["actor"] == str(client.user_id) and e["detail"]["stage"] == "publish"


async def test_a_deck_changed_after_the_check_is_checked_again_and_blocked(org):
    job = allowed_job()
    job.deck.slides[1].body = "Tank Day sale"  # changed after the check, not through a caption edit
    store.save_job(org, job)
    post = await publishing.publish_now(org, job.id, None, "mock")
    job = store.get_job(org, job.id)
    assert job.status == JobStatus.BLOCKED and post.state == "failed" and job.safeguard.decision == "block"
    states = {s.name: s.state for s in job.pipeline}
    assert states["Human publish"] == "skipped"


async def test_a_graph_post_of_unchecked_content_is_refused_on_a_fixture_server(org, monkeypatch):
    uploads = []
    monkeypatch.setattr(image_host, "exposed", lambda *a, **k: uploads.append(a))
    job = store.save_job(org, allowed_job())
    post = await publishing.publish_now(org, job.id, "Safe new caption #seoul", "graph", account=connect_account(org))
    job = store.get_job(org, job.id)
    assert uploads == [] and job.status == JobStatus.READY_FOR_REVIEW and post.state == "failed"
    assert "can't check this edit" in post.last_error


async def test_a_rules_only_check_of_an_edit_is_not_enough_for_a_real_post(org, monkeypatch):
    """A dry run of an edit on a fixture server checks it with the rules only; posting it for real still needs
    the model, so the same caption is refused in graph mode."""
    job = store.save_job(org, allowed_job())

    async def instagram(ig, urls, caption, on_progress=lambda *a: None):
        return "17890000000000001"
    monkeypatch.setattr(publisher, "build_carousel", instagram)
    monkeypatch.setattr(settings, "public_asset_base_url", "https://tunnel.example")
    acct = connect_account(org)
    dry = await publishing.publish_now(org, job.id, "Safe new caption #seoul", "dryrun", account=acct)
    assert (dry.state, dry.mode, dry.creation_id) == ("container_ready", "dryrun", "17890000000000001")
    job = store.get_job(org, job.id)
    assert job.deck.caption == "Safe new caption #seoul" and job.safeguard.checked_by == ["rules"]
    real = await publishing.publish_now(org, job.id, None, "graph", account=acct)
    job = store.get_job(org, job.id)
    assert job.status == JobStatus.READY_FOR_REVIEW and real.state == "failed" and real.attempts == 0


async def test_unchanged_content_skips_the_recheck(org, monkeypatch):
    job = store.save_job(org, allowed_job())

    async def boom(*a, **k):
        raise AssertionError("should not re-check unchanged content")
    monkeypatch.setattr(orchestrator, "run_safeguard", boom)
    await publishing.publish_now(org, job.id, None, "mock")
    assert store.get_job(org, job.id).status == JobStatus.PUBLISHED


async def test_a_safe_edit_is_checked_again_and_posted(org):
    job = store.save_job(org, allowed_job())
    post = await publishing.publish_now(org, job.id, "Three easy things to do in Seoul. #seoul", "mock")
    assert post.state == "published" and post.permalink.endswith(f"/p/MOCK{job.id.upper()}/")
    job = store.get_job(org, job.id)
    assert job.status == JobStatus.PUBLISHED and job.deck.caption.startswith("Three easy things")
    assert job.safeguard.content_hash == safeguard.content_hash(job.deck)
    assert any("checking again" in line.message for line in job.log)


# ---------------------------------------------------------------- Organization isolation

def test_another_organization_cant_see_or_act_on_a_verdict(sign_in, no_publish):
    a, b = sign_in(org="A"), sign_in(org="B")
    blocked = store.save_job(a.org_id, blocked_job("A's secret reason"))
    ready = store.save_job(a.org_id, allowed_job())
    assert a.get(f"/jobs/{blocked.id}").json()["safeguard"]["summary"] == "A's secret reason"
    for job in (blocked, ready):
        assert b.get(f"/jobs/{job.id}").status_code == 404
        assert b.post(f"/jobs/{job.id}/approve", json={"override_reason": "x"}).status_code == 404
        assert b.post(f"/jobs/{job.id}/reject", json={"reason": "B says no"}).status_code == 404
        assert b.post(f"/jobs/{job.id}/approve", json={"caption": "탱크데이 #seoul"}).status_code == 404
    assert all("secret" not in str(j.get("safeguard")) for j in b.get("/jobs?limit=500").json())
    assert store.get_job(a.org_id, blocked.id).status == JobStatus.BLOCKED
    assert store.get_job(a.org_id, ready.id).blocked_edit is None  # B's edit attempt wrote nothing to A's Job
    assert no_publish == []
    assert all(e["target"] not in (blocked.id, ready.id) for e in b.get("/audit").json())  # nothing in B's log
    assert actions(a, blocked.id) == [] and actions(a, ready.id) == []  # nor in A's: B never reached the Job
    assert store.get_job(b.org_id, blocked.id) is None


async def test_publishing_under_another_organization_checks_nothing(sign_in, monkeypatch):
    a, b = sign_in(org="A"), sign_in(org="B")
    job = store.save_job(a.org_id, allowed_job())

    async def boom(*a, **k):
        raise AssertionError("B's publish must not reach A's Job")
    monkeypatch.setattr(orchestrator, "run_safeguard", boom)
    assert await publishing.publish_now(b.org_id, job.id, "탱크데이 #seoul", "mock") is None
    after = store.get_job(a.org_id, job.id)
    assert after.status == JobStatus.READY_FOR_REVIEW and after.blocked_edit is None and after.log == []


# ---------------------------------------------------------------- durable Jobs across the new stage

async def test_a_restart_during_the_safeguard_resumes_there_without_redoing_the_pipeline(org, no_network,
                                                                                       monkeypatch):
    calls: Counter[str] = Counter()
    real = dict(orchestrator.STAGE_FUNCS)

    def counted(name):
        async def run(s):
            calls[name] += 1
            if name == "safeguard" and calls[name] == 1:  # the process dies while the Safeguard runs
                s.r.step("safeguard", "running")
                await s.r.flush()
                raise Crash()
            await real[name](s)
        return run
    for name in real:
        monkeypatch.setitem(orchestrator.STAGE_FUNCS, name, counted(name))

    job = store.save_job(org, orchestrator.new_job(secrets.token_hex(3), "crash in safeguard"))
    with pytest.raises(Crash):
        await orchestrator.run_job(org, job.id, scenario="bad")
    cut = store.get_job(org, job.id)
    assert cut.status == JobStatus.REVIEWING and cut.safeguard is None  # never READY without a verdict
    restart_dbos()
    done = await wait_for(org, job.id, DONE)
    assert done.status == JobStatus.BLOCKED and done.safeguard.decision == "block"
    assert calls["safeguard"] == 2 and all(n == 1 for k, n in calls.items() if k != "safeguard"), calls
    assert any("Safeguard was cut off by a server restart" in line.message for line in done.log)
    assert await workflow_done(org, job.id) == "SUCCESS"


async def test_a_job_started_under_the_previous_pipeline_is_failed_not_resumed(org, monkeypatch):
    """WORKFLOW_VERSION 2 added the Safeguard stage: a Job still running under version 1 is cancelled and marked
    failed at startup (#42), so it can never reach review without a verdict."""
    assert settings.workflow_version == "2"
    job = orchestrator.new_job(secrets.token_hex(3), "old pipeline")
    job.status = JobStatus.REVIEWING
    store.save_job(org, job)
    wid = orchestrator.workflow_id(org, job.id)
    queue = await DBOS.retrieve_queue_async(durable.JOB_QUEUE)
    with SetWorkflowID(wid), SetEnqueueOptions(queue_partition_key=str(org), app_version="1"):
        await queue.enqueue_async(orchestrator.job_workflow, str(org), job.id, "quick", None, "good")
    from app.main import recover_interrupted
    await asyncio.to_thread(recover_interrupted)
    after = store.get_job(org, job.id)
    assert after.status == JobStatus.FAILED and "pipeline changed" in after.error
    assert (await asyncio.to_thread(DBOS.get_workflow_status, wid)).status == "CANCELLED"


def test_a_job_finished_before_the_safeguard_must_be_regenerated(client, no_publish):
    """A Deck that reached review under the old pipeline has no verdict: Approve refuses it (regenerate)."""
    job = orchestrator.new_job(secrets.token_hex(3), "old deck")
    job.status, job.deck = JobStatus.READY_FOR_REVIEW, scenario_deck("good")
    store.save_job(client.org_id, job)
    assert client.post(f"/jobs/{job.id}/approve", json={}).status_code == 409
    assert client.post(f"/jobs/{job.id}/reject", json={"reason": "old"}).status_code == 200

