"""Organization profile (#16): Admins write it, every Member reads it, it stays inside its Organization, every Job
keeps a snapshot, every agent stage receives it, and the "never say" list raises Issues."""

import ast
import secrets
from pathlib import Path

import pytest
from factories import scenario_deck
from test_roles import add_member

from app import brainstorms, org_profile, store
from app.config import settings
from app.deck_versions import remap_issues
from app.pipeline import orchestrator
from app.schemas import CardDeck, Issue, JobStatus, LinkCheck, OrgProfile
from app.services import link_checker

PROFILE = {
    "what_it_does": "Jeju's tourism office: festivals, food and nature on the island.",
    "audience": " Families and first-time visitors ",
    "home_region": "Jeju",
    "usual_subjects": ["festivals", " Festivals", "  night markets ", ""],
    "never_say": ["Black Friday", "casino"],
}
REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def team(sign_in, auth_keys):
    """One Organization with a signed-in client per Role (the Admin created it)."""
    from fastapi.testclient import TestClient

    from app.main import app
    admin = sign_in(org="Profile team")
    clients = {"admin": admin}
    for role in ("viewer", "editor", "approver"):
        uid = add_member(admin.org_id, role)
        c = TestClient(app, headers={"Authorization": f"Bearer {auth_keys.token(uid, email=f'{uid.hex[:8]}@x.com')}"})
        c.org_id = admin.org_id
        clients[role] = c
    return clients


# ---------------------------------------------------------------- the API

def test_admin_writes_every_member_reads_and_it_is_audited(team):
    admin, viewer = team["admin"], team["viewer"]
    empty = viewer.get("/orgs/current/profile").json()
    assert empty["complete"] is False and empty["profile"]["never_say"] == [] and empty["updated_at"] is None

    r = admin.put("/orgs/current/profile", json=PROFILE)
    assert r.status_code == 200, r.text
    saved = viewer.get("/orgs/current/profile").json()
    assert saved["complete"] is True and saved["updated_at"]
    p = saved["profile"]
    assert p["audience"] == "Families and first-time visitors"         # trimmed
    assert p["usual_subjects"] == ["festivals", "night markets"]       # blanks and repeats dropped
    assert p["never_say"] == ["Black Friday", "casino"]

    admin.put("/orgs/current/profile", json={**PROFILE, "home_region": "Jeju-do"})
    events = [e for e in admin.get("/audit").json() if e["action"] == "org.profile_updated"]
    assert len(events) == 2
    assert events[0]["detail"]["changed"] == {"home_region": {"before": "Jeju", "after": "Jeju-do"}}
    assert set(events[1]["detail"]["changed"]) == {"what_it_does", "audience", "home_region", "usual_subjects",
                                                   "never_say"}


@pytest.mark.parametrize("role", ["viewer", "editor", "approver"])
def test_only_admins_can_change_it(team, role):
    assert team[role].put("/orgs/current/profile", json=PROFILE).status_code == 403
    assert team["admin"].get("/orgs/current/profile").json()["complete"] is False


@pytest.mark.parametrize("bad", [
    {"never_say": ["x" * 201]},
    {"usual_subjects": ["y" * 81]},
    {"what_it_does": "z" * 501},
    {"never_say": [f"item {i}" for i in range(31)]},
    {"tone": "playful"},  # unknown field: the Brand kit is something else
])
def test_rejects_oversized_or_unknown_fields(client, bad):
    assert client.put("/orgs/current/profile", json=bad).status_code == 422


def test_stays_inside_its_organization(sign_in):
    a, b = sign_in(org="A"), sign_in(org="B")
    a.put("/orgs/current/profile", json=PROFILE)
    assert b.get("/orgs/current/profile").json()["complete"] is False
    assert org_profile.get(b.org_id) is None and org_profile.get(a.org_id).home_region == "Jeju"


# ---------------------------------------------------------------- Jobs keep a snapshot

def test_a_job_keeps_the_profile_it_started_with(client, monkeypatch):
    async def no_run(*a, **k):
        return None
    monkeypatch.setattr(orchestrator, "start_job", no_run)
    client.put("/orgs/current/profile", json=PROFILE)
    job_id = client.post("/jobs", json={"prompt": "Jeju festivals this weekend"}).json()["id"]
    client.put("/orgs/current/profile", json={**PROFILE, "never_say": []})
    job = store.get_job(client.org_id, job_id)
    assert job.org_profile.never_say == ["Black Friday", "casino"]  # the snapshot, not the edit after it


def test_a_job_without_a_profile_has_none(client, monkeypatch):
    async def no_run(*a, **k):
        return None
    monkeypatch.setattr(orchestrator, "start_job", no_run)
    job_id = client.post("/jobs", json={"prompt": "Seoul this weekend"}).json()["id"]
    assert store.get_job(client.org_id, job_id).org_profile is None


# ---------------------------------------------------------------- agents receive it

def _run_stage_payloads(path: Path) -> list[tuple[str, ast.Dict]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "run_stage":
            payload = n.args[2]
            assert isinstance(payload, ast.Dict), f"{path.name}: run_stage payload should be a dict literal"
            out.append((n.args[0].value, payload))
    return out


def test_every_agent_stage_gets_the_profile():
    """Every `run_stage` call (researcher, copywriter, reviewer, safeguard, planner) passes `org_profile`."""
    calls = (_run_stage_payloads(REPO / "backend/app/pipeline/orchestrator.py")
             + _run_stage_payloads(REPO / "backend/app/brainstorms.py")
             + _run_stage_payloads(REPO / "backend/app/slide_actions.py"))
    agents = {agent for agent, _ in calls}
    assert agents == {"researcher", "copywriter", "reviewer", "safeguard", "planner"}
    for agent, payload in calls:
        keys = {k.value for k in payload.keys if isinstance(k, ast.Constant)}
        assert "org_profile" in keys, agent


def test_agent_prompts_do_not_assume_one_account():
    for f in [REPO / "agent/CLAUDE.md", *sorted((REPO / "agent/.claude/agents").glob("*.md")),
              REPO / "policies/content/safeguard_policy.md"]:
        text = f.read_text(encoding="utf-8")
        assert "whatsonkorea" not in text.lower(), f.name
    for name in ("researcher", "planner", "copywriter", "reviewer", "safeguard"):
        assert "org_profile" in (REPO / f"agent/.claude/agents/{name}.md").read_text(encoding="utf-8"), name


async def test_safeguard_model_gets_the_profile_and_brainstorm_planner_too(monkeypatch, org):
    seen = {}

    async def fake_run_stage(agent, task, payload, out_type, **kw):
        seen[agent] = payload
        if agent == "safeguard":
            return out_type(decision="allow", summary="ok")
        return out_type(reply="ok")
    monkeypatch.setattr(orchestrator, "run_stage", fake_run_stage)
    monkeypatch.setattr(brainstorms, "run_stage", fake_run_stage)
    profile = OrgProfile.model_validate(PROFILE)

    await orchestrator.run_safeguard(org, "j1", scenario_deck("good"), [], profile=profile)
    assert seen["safeguard"]["org_profile"]["home_region"] == "Jeju"
    await orchestrator.run_safeguard(org, "j2", scenario_deck("good"), [], profile=None)
    assert seen["safeguard"]["org_profile"] is None  # no profile: the agent writes as a neutral guide

    store.save_brainstorm(org, brainstorms.sample_brainstorm())
    with store.system() as c:
        c.execute("insert into org_profiles (org_id, doc) values (%s, %s)", (org, profile.model_dump_json()))
    monkeypatch.setattr(settings, "demo_mode", "live")
    await brainstorms.send_message(org, brainstorms.sample_brainstorm(), "다른 앵글", [])
    assert seen["planner"]["org_profile"]["never_say"] == ["Black Friday", "casino"]


# ---------------------------------------------------------------- "never say"

def _deck(caption: str, body: str = "Lantern walk along the stream.") -> CardDeck:
    d = scenario_deck("good")
    d.caption = caption
    d.slides[1].body = body
    return d


@pytest.mark.parametrize(("caption", "body", "hits"), [
    ("Weekend picks. #jeju", "Black  friday deals at the market.", [1]),       # case and spacing
    ("Weekend picks. #BlackFriday #jeju", "Lantern walk.", [None]),             # a hashtag
    ("Try the CASINO buffet", "Lantern walk.", [None]),
    ("Weekend picks. #jeju", "Casinos are not a festival.", []),                # whole words only
    ("Weekend picks. #jeju", "Lantern walk.", []),
])
def test_never_say_matches_words_not_fragments(caption, body, hits):
    issues = org_profile.never_say_issues(_deck(caption, body), OrgProfile.model_validate(PROFILE))
    assert [i.slide_index for i in issues] == hits
    assert all(i.severity == "block" and i.category == "sensitive" and i.message.startswith(org_profile.NEVER_SAY)
               for i in issues)


def test_no_profile_no_never_say_issues():
    assert org_profile.never_say_issues(_deck("Black Friday"), None) == []
    assert org_profile.never_say_issues(_deck("Black Friday"), OrgProfile()) == []


def test_deck_edits_recompute_never_say_issues():
    old = [Issue(severity="block", category="sensitive", slide_index=1, message=f'{org_profile.NEVER_SAY} "x"'),
           Issue(severity="warn", category="tone", slide_index=1, message="hype")]
    assert [i.message for i in remap_issues(old, [0, 1], set())] == ["hype"]


async def test_pipeline_flags_never_say_for_the_operator(org, monkeypatch):
    """A fixture run whose Deck says something the profile rules out: a blocking Issue (an Override can approve it;
    the Safeguard is not involved)."""
    async def fake_check(url, client=None):
        return LinkCheck(url=url, status="dead", http_code=404)
    monkeypatch.setattr(link_checker, "check_url", fake_check)
    monkeypatch.setattr(orchestrator, "FIXTURE_DELAY", 0)
    good = scenario_deck("good")
    phrase = good.slides[1].heading.split()[0]  # a word the good Deck certainly uses
    job = orchestrator.new_job(secrets.token_hex(4), "test")
    job.org_profile = OrgProfile(never_say=[phrase])
    store.save_job(org, job)
    await orchestrator.run_job(org, job.id)
    done = store.get_job(org, job.id)
    assert done.status == JobStatus.READY_FOR_REVIEW and done.safeguard.decision == "allow"
    assert any(i.message.startswith(f'{org_profile.NEVER_SAY} "{phrase}"') for i in done.issues)
    assert any('"never say"' in line.message for line in done.log)


def test_migration_keeps_profiles_private():
    sql = next((REPO / "supabase/migrations").glob("*_org_profiles.sql")).read_text()
    assert "enable row level security" in sql and "to wok_api" in sql
