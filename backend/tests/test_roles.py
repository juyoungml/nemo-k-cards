"""Roles (#14; GLOSSARY "Role"): Viewer sees everything, Editor also starts Jobs and Brainstorms and edits, Approver
also approves and rejects, Admin also manages Members and reads the audit log. Every route declares its least
Role, and each Role is tried against every route: allowed below its level is 403, at or above it is not."""

import re
import secrets
import uuid

import pytest
from conftest import all_api_routes
from factories import allowed
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app import auth, orgs, store
from app.main import app
from app.pipeline import orchestrator, publishing
from app.schemas import Issue, JobStatus

ROLES = auth.ROLES
NO_ORG = {("GET", "/me"), ("POST", "/orgs"), ("POST", "/invitations/accept")}


def _declared_role(route: APIRoute) -> str | None:
    """The least Role a route's dependencies require (`auth.requires`), or "signed-in" for `auth.signed_in`."""
    found = []

    def walk(dep):
        call = dep.call
        if getattr(call, "min_role", None):
            found.append(call.min_role)
        elif call is auth.signed_in:
            found.append("signed-in")
        for d in dep.dependencies:
            walk(d)
    walk(route.dependant)
    return found[0] if found else None


def _api_routes():
    for r in all_api_routes():
        # Routes the sign-in middleware lets through (health, signed /assets links) carry no Role by design.
        if r.path not in auth.PUBLIC_PATHS and not r.path.startswith(auth.PUBLIC_PREFIXES):
            for m in sorted(r.methods):
                yield m, r.path, _declared_role(r)


# The matrix (docs/spec/v1.md API table, with GLOSSARY "Viewer sees everything" for the read routes).
EXPECTED = {
    ("GET", "/me"): "signed-in",
    ("POST", "/orgs"): "signed-in",
    ("POST", "/invitations/accept"): "signed-in",
    ("GET", "/jobs"): "viewer",
    ("GET", "/jobs/{job_id}"): "viewer",
    ("GET", "/jobs/{job_id}/events"): "viewer",
    ("GET", "/jobs/{job_id}/versions"): "viewer",
    ("POST", "/jobs/{job_id}/versions"): "editor",
    ("POST", "/jobs/{job_id}/fit"): "editor",
    ("GET", "/brainstorms/{brainstorm_id}"): "viewer",
    ("GET", "/metrics"): "viewer",
    ("GET", "/instagram-accounts"): "viewer",
    ("GET", "/policy-events"): "viewer",
    ("GET", "/orgs/current/members"): "viewer",
    ("POST", "/reset"): "viewer",
    ("POST", "/jobs"): "editor",
    ("POST", "/brainstorms"): "editor",
    ("PATCH", "/brainstorms/{brainstorm_id}"): "editor",
    ("POST", "/brainstorms/{brainstorm_id}/messages"): "editor",
    ("POST", "/brainstorms/{brainstorm_id}/generate"): "editor",
    ("POST", "/jobs/{job_id}/approve"): "approver",
    ("POST", "/jobs/{job_id}/reject"): "approver",
    ("POST", "/orgs/current/members"): "admin",
    ("PATCH", "/orgs/current/members/{user_id}"): "admin",
    ("DELETE", "/orgs/current/members/{user_id}"): "admin",
    ("GET", "/orgs/current/invitations"): "admin",
    ("DELETE", "/orgs/current/invitations/{invitation_id}"): "admin",
    ("GET", "/audit"): "admin",
    ("GET", "/instagram-accounts/connection"): "viewer",
    ("POST", "/instagram-accounts/connect"): "admin",
    ("DELETE", "/instagram-accounts/{account_id}"): "admin",
    ("POST", "/materials"): "editor",
    ("POST", "/materials/files"): "editor",
    ("GET", "/materials/{material_id}"): "viewer",
    ("GET", "/posts"): "viewer",
    ("GET", "/posts/{post_id}"): "viewer",
    ("POST", "/posts/{post_id}/resolve"): "approver",
    ("POST", "/posts/{post_id}/cancel"): "approver",
    ("POST", "/posts/{post_id}/reschedule"): "approver",
}


def test_every_route_declares_a_role():
    """A new route without `auth.Viewer/Editor/Approver/Admin` (or `SignedIn`) fails here, and so does one whose
    Role differs from the matrix: review the matrix when adding routes."""
    declared = {(m, p): role for m, p, role in _api_routes()}
    assert [k for k, v in declared.items() if v is None] == []
    assert declared == EXPECTED
    assert {k for k, v in declared.items() if v == "signed-in"} == NO_ORG == auth.NO_ORG_ROUTES


def add_member(org_id, role: str, user_id=None, email=None):
    user_id = user_id or uuid.uuid4()
    with store.system() as c:
        c.execute("insert into members (org_id, user_id, role, email) values (%s, %s, %s, %s)",
                  (org_id, user_id, role, email or f"{user_id.hex[:8]}@example.com"))
    return user_id


@pytest.fixture
def team(sign_in, auth_keys):
    """One Organization with a signed-in client per Role (the Admin created it)."""
    admin = sign_in(org="Team")
    clients = {"admin": admin}
    for role in ("viewer", "editor", "approver"):
        uid = add_member(admin.org_id, role)
        c = TestClient(app, headers={"Authorization": f"Bearer {auth_keys.token(uid, email=f'{uid.hex[:8]}@example.com')}"})
        c.org_id, c.user_id = admin.org_id, uid
        clients[role] = c
    return clients


@pytest.fixture
def no_background(monkeypatch):
    calls = []

    async def fake(*args, **kwargs):
        calls.append(args)
    monkeypatch.setattr(orchestrator, "run_job", fake)
    monkeypatch.setattr(publishing, "start", fake)
    return calls


def _ready_job(org_id, issues=()):
    """A finished Job waiting for a decision: the Safeguard allowed its Deck (factories.allowed)."""
    job = orchestrator.new_job(secrets.token_hex(4), "Seongsu pop-ups")
    job.status = JobStatus.READY_FOR_REVIEW
    job.issues = list(issues)
    return store.save_job(org_id, allowed(job))


def _concrete(path: str, ids: dict) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: ids[m.group(1)], path)


@pytest.mark.parametrize("role", ROLES)
def test_each_role_is_allowed_exactly_its_routes(team, role, no_background):
    """Every route, every Role: below the route's Role → 403 "<role> role required"; at or above → anything but
    403 (validation errors and 404s for made-up ids are fine: the Role check passed)."""
    c = team[role]
    other = add_member(c.org_id, "viewer")
    ids = {"job_id": _ready_job(c.org_id).id, "brainstorm_id": "nope", "user_id": str(other),
           "invitation_id": str(uuid.uuid4()), "account_id": str(uuid.uuid4()), "material_id": "m-0000000000000000",
           "post_id": str(uuid.uuid4())}
    for (method, path), need in EXPECTED.items():
        if need == "signed-in":
            continue
        r = c.request(method, _concrete(path, ids), json={})
        allowed = auth.ROLE_RANK[role] >= auth.ROLE_RANK[need]
        if allowed:
            assert r.status_code != 403, (role, method, path, r.text)
        else:
            assert (r.status_code, r.json()["detail"]) == (403, f"{need} role required"), (role, method, path)


def test_viewer_cannot_edit_editor_cannot_approve_approver_cannot_invite(team, no_background):
    """docs/spec/v1.md acceptance check W1 "Role matrix", with real request bodies."""
    job = _ready_job(team["admin"].org_id)
    board = team["editor"].post("/brainstorms", json={"sample": False}).json()
    assert team["viewer"].post("/jobs", json={"prompt": "Seongsu pop-ups"}).status_code == 403
    assert team["viewer"].patch(f"/brainstorms/{board['id']}", json={"tones": ["x"]}).status_code == 403
    assert team["viewer"].get(f"/brainstorms/{board['id']}").status_code == 200  # Viewer sees everything
    assert team["editor"].patch(f"/brainstorms/{board['id']}", json={"tones": ["x"]}).status_code == 200
    assert team["editor"].post(f"/jobs/{job.id}/approve", json={"deck_version": 1}).status_code == 403
    assert team["editor"].post(f"/jobs/{job.id}/reject", json={"reason": "no"}).status_code == 403
    assert store.get_job(team["admin"].org_id, job.id).status == JobStatus.READY_FOR_REVIEW
    assert team["approver"].post("/orgs/current/members",
                                 json={"email": "x@example.com", "role": "viewer"}).status_code == 403
    assert team["approver"].post(f"/jobs/{job.id}/approve", json={"deck_version": 1}).status_code == 200


def test_self_approval_is_allowed(team, no_background):
    """The Approver who started the Job approves it themselves."""
    c = team["approver"]
    jid = c.post("/jobs", json={"prompt": "Seongsu pop-ups"}).json()["id"]
    job = store.get_job(c.org_id, jid)
    job.status = JobStatus.READY_FOR_REVIEW
    store.save_job(c.org_id, allowed(job))
    assert c.post(f"/jobs/{jid}/approve", json={"deck_version": 1}).status_code == 200


def test_role_is_read_per_request(team):
    """A Role change applies to the next call: no stale Role in a long-lived session."""
    viewer = team["viewer"]
    assert viewer.post("/brainstorms", json={"sample": False}).status_code == 403
    assert team["admin"].patch(f"/orgs/current/members/{viewer.user_id}", json={"role": "editor"}).status_code == 200
    assert viewer.post("/brainstorms", json={"sample": False}).status_code == 201


# ---------------------------------------------------------------- Override

BLOCK = Issue(severity="block", category="fact", slide_index=1, message="date contradicts the source")
WARN = Issue(severity="warn", category="tone", message="a bit salesy")


def test_approving_with_blocking_issues_needs_an_override_reason(team, no_background):
    c = team["approver"]
    job = _ready_job(c.org_id, [BLOCK, WARN])
    for body in ({"deck_version": 1}, {"deck_version": 1, "override_reason": ""},
                 {"deck_version": 1, "override_reason": "   "}):
        r = c.post(f"/jobs/{job.id}/approve", json=body)
        assert r.status_code == 422 and "Override" in r.json()["detail"]
    assert no_background == [] and store.get_job(c.org_id, job.id).status == JobStatus.READY_FOR_REVIEW
    r = c.post(f"/jobs/{job.id}/approve", json={"deck_version": 1, "override_reason": "Checked the date with the venue by phone."})
    assert r.status_code == 200 and len(no_background) == 1
    events = [e for e in team["admin"].get("/audit").json() if e["target"] == job.id]
    assert [e["action"] for e in events] == ["deck.overridden"]
    d = events[0]["detail"]
    assert d["reason"] == "Checked the date with the venue by phone."
    assert d["issues"] == [{"category": "fact", "slide_index": 1, "message": "date contradicts the source"}]
    assert events[0]["actor"] == str(c.user_id)


def test_warnings_alone_need_no_override(team, no_background):
    c = team["approver"]
    job = _ready_job(c.org_id, [WARN])
    assert c.post(f"/jobs/{job.id}/approve", json={"deck_version": 1}).status_code == 200
    assert [e["action"] for e in team["admin"].get("/audit").json() if e["target"] == job.id] == ["deck.approved"]


def test_override_reason_has_a_limit(team, no_background):
    job = _ready_job(team["approver"].org_id, [BLOCK])
    assert team["approver"].post(f"/jobs/{job.id}/approve", json={"deck_version": 1, "override_reason": "x" * 2001}).status_code == 422


def test_existing_org_creator_still_gets_one_organization(sign_in):
    """POST /orgs: one created Organization per person (the double-submit guard); joining others is by invite."""
    c = sign_in(org="Mine")
    assert c.post("/orgs", json={"name": "Second"}).status_code == 409
    other = sign_in(org="Theirs")
    add_member(other.org_id, "viewer", user_id=c.user_id)
    assert len(orgs.memberships(c.user_id)) == 2
