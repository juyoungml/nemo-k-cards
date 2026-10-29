"""Members (#14): inviting by email and accepting, changing Roles and removing Members (never the last Admin),
switching between Organizations with X-Org-Id, and the append-only audit log. Checked through the API and, for
the new policies, directly in Postgres as the API role."""

import threading
import uuid
from urllib.parse import parse_qs, urlparse

import psycopg
import pytest
from fastapi.testclient import TestClient
from test_roles import add_member

from app import audit, members, orgs, store
from app.config import settings
from app.main import app


@pytest.fixture
def mail(monkeypatch):
    """Captures invitation emails instead of calling Supabase."""
    sent = []

    def fake(email, link):
        sent.append((email, link))
        return True
    monkeypatch.setattr(members, "send_invite_email", fake)
    return sent


@pytest.fixture
def person(auth_keys):
    """`person(email)` → a signed-in TestClient for a new account with that email (no Organization)."""
    def make(email: str, user_id=None, **headers) -> TestClient:
        user_id = user_id or uuid.uuid4()
        c = TestClient(app, headers={"Authorization": f"Bearer {auth_keys.token(user_id, email=email)}", **headers})
        c.user_id, c.email = user_id, email
        return c
    return make


def _token(link: str) -> str:
    return parse_qs(urlparse(link).query)["token"][0]


def _actions(admin, target=None) -> list[str]:
    return [e["action"] for e in admin.get("/audit").json() if target is None or e["target"] == target]


# ---------------------------------------------------------------- invite and accept

def test_invite_then_accept_joins_the_inviting_organization(client, person, mail):
    r = client.post("/orgs/current/members", json={"email": "  New.Person@Example.com ", "role": "editor"})
    assert r.status_code == 201
    inv = r.json()
    assert inv["email"] == "new.person@example.com" and inv["role"] == "editor" and inv["email_sent"] is True
    assert inv["invite_url"].startswith(f"{settings.admin_url}/invite?token=")
    assert mail == [("new.person@example.com", inv["invite_url"])]
    assert [i["email"] for i in client.get("/orgs/current/invitations").json()] == ["new.person@example.com"]

    invitee = person("new.person@example.com")
    assert invitee.get("/jobs").status_code == 403  # no Organization yet
    r = invitee.post("/invitations/accept", json={"token": _token(inv["invite_url"])})
    assert r.status_code == 200 and r.json() == {"id": str(client.org_id), "name": "Test Org", "role": "editor"}
    me = invitee.get("/me").json()
    assert me["org"]["id"] == str(client.org_id) and [o["role"] for o in me["orgs"]] == ["editor"]
    assert invitee.post("/brainstorms", json={"sample": False}).status_code == 201  # an Editor now
    with store.system() as c:  # joined the inviting Organization; no new one was made
        assert c.execute("select count(*) from orgs where created_by = %s", (invitee.user_id,)).fetchone()[0] == 0
    listed = {m["email"]: m["role"] for m in client.get("/orgs/current/members").json()}
    assert listed[invitee.email] == "editor"
    assert client.get("/orgs/current/invitations").json() == []
    assert _actions(client) == ["member.joined", "member.invited", "org.created"]


def test_accept_needs_the_invited_email_address(client, person, mail):
    link = client.post("/orgs/current/members", json={"email": "right@example.com", "role": "viewer"}).json()
    token = _token(link["invite_url"])
    r = person("wrong@example.com").post("/invitations/accept", json={"token": token})
    assert r.status_code == 403 and "different email" in r.json()["detail"]
    assert person("").post("/invitations/accept", json={"token": token}).status_code == 403  # no email claim
    assert person("RIGHT@example.com").post("/invitations/accept", json={"token": token}).status_code == 200


def test_accept_needs_the_token(client, person, mail):
    client.post("/orgs/current/members", json={"email": "who@example.com", "role": "admin"})
    p = person("who@example.com")
    assert p.post("/invitations/accept", json={"token": "x" * 43}).status_code == 404
    assert p.get("/me").json()["orgs"] == []


def test_invitation_is_single_use_and_expires(client, person, mail):
    first = person("one@example.com")
    link = client.post("/orgs/current/members", json={"email": "one@example.com", "role": "viewer"}).json()
    token = _token(link["invite_url"])
    assert first.post("/invitations/accept", json={"token": token}).status_code == 200
    assert first.post("/invitations/accept", json={"token": token}).status_code == 200  # idempotent for them
    other = person("one@example.com")  # a second account with the same address can't reuse it
    assert other.post("/invitations/accept", json={"token": token}).status_code == 410

    link = client.post("/orgs/current/members", json={"email": "late@example.com", "role": "viewer"}).json()
    with store.system() as c:
        c.execute("update invitations set expires_at = now() - interval '1 second' where id = %s", (link["id"],))
    r = person("late@example.com").post("/invitations/accept", json={"token": _token(link["invite_url"])})
    assert r.status_code == 410 and "expired" in r.json()["detail"]


def test_inviting_again_replaces_the_open_invitation_and_revoke_withdraws_it(client, person, mail):
    old = client.post("/orgs/current/members", json={"email": "twice@example.com", "role": "viewer"}).json()
    new = client.post("/orgs/current/members", json={"email": "twice@example.com", "role": "approver"}).json()
    p = person("twice@example.com")
    assert p.post("/invitations/accept", json={"token": _token(old["invite_url"])}).status_code == 410
    assert client.delete(f"/orgs/current/invitations/{new['id']}").status_code == 204
    assert client.delete(f"/orgs/current/invitations/{new['id']}").status_code == 404
    assert p.post("/invitations/accept", json={"token": _token(new["invite_url"])}).status_code == 410
    assert "invitation.revoked" in _actions(client)


def test_inviting_a_member_or_a_bad_address_is_refused(client, mail):
    assert client.post("/orgs/current/members", json={"email": "not-an-email", "role": "viewer"}).status_code == 422
    assert client.post("/orgs/current/members", json={"email": "a@b.co", "role": "owner"}).status_code == 422
    me = client.get("/me").json()["user"]["email"]
    assert client.post("/orgs/current/members", json={"email": me, "role": "viewer"}).status_code == 409


def test_invitation_without_email_configured_still_returns_the_link(client, monkeypatch):
    monkeypatch.setattr(settings, "supabase_service_key", "")
    r = client.post("/orgs/current/members", json={"email": "manual@example.com", "role": "viewer"})
    assert r.status_code == 201 and r.json()["email_sent"] is False and "/invite?token=" in r.json()["invite_url"]


def test_invitation_email_goes_through_supabase_auth(monkeypatch):
    """New address: Supabase's invite email. Existing account: a sign-in link, never a new account."""
    import httpx
    calls = []

    def handler(request: httpx.Request):
        calls.append((request.url.path, dict(request.url.params), request.headers.get("apikey"),
                      request.headers.get("authorization"), request.content))
        if request.url.path.endswith("/invite") and b"exists" in request.content:
            return httpx.Response(422, json={"error_code": "email_exists"})
        return httpx.Response(200, json={})
    real = httpx.Client
    monkeypatch.setattr(members.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(settings, "supabase_service_key", "sb_secret_test")
    assert members.send_invite_email("new@example.com", "http://localhost:3000/invite?token=t") is True
    assert members.send_invite_email("exists@example.com", "http://localhost:3000/invite?token=u") is True
    assert [c[0] for c in calls] == ["/auth/v1/invite", "/auth/v1/invite", "/auth/v1/otp"]
    assert calls[0][1] == {"redirect_to": "http://localhost:3000/invite?token=t"}
    assert calls[0][2] == "sb_secret_test" and calls[0][3] is None  # new-style keys go in apikey only
    assert b'"create_user":false' in calls[2][4].replace(b" ", b"")


# ---------------------------------------------------------------- Roles and the last Admin

def test_admin_changes_roles_and_removes_members(client, person):
    uid = add_member(client.org_id, "viewer")
    r = client.patch(f"/orgs/current/members/{uid}", json={"role": "approver"})
    assert r.status_code == 200 and r.json()["role"] == "approver"
    assert client.patch(f"/orgs/current/members/{uid}", json={"role": "boss"}).status_code == 422
    assert client.delete(f"/orgs/current/members/{uid}").status_code == 204
    assert client.delete(f"/orgs/current/members/{uid}").status_code == 404
    assert str(uid) not in {m["user_id"] for m in client.get("/orgs/current/members").json()}
    assert _actions(client)[:2] == ["member.removed", "member.role_changed"]


def test_the_last_admin_can_be_neither_demoted_nor_removed(client):
    me = client.user_id
    r = client.patch(f"/orgs/current/members/{me}", json={"role": "editor"})
    assert r.status_code == 409 and "at least one Admin" in r.json()["detail"]
    assert client.delete(f"/orgs/current/members/{me}").status_code == 409
    other = add_member(client.org_id, "admin")
    assert client.patch(f"/orgs/current/members/{me}", json={"role": "editor"}).status_code == 200  # now fine
    assert client.get("/orgs/current/members").status_code == 200
    assert client.get("/audit").status_code == 403  # an Editor now
    with store.system() as c:
        assert c.execute("select role from members where org_id = %s and user_id = %s",
                         (client.org_id, other)).fetchone() == ("admin",)


def test_two_admins_demoting_each_other_at_once_leave_one_admin(sign_in, auth_keys):
    a = sign_in(org="Pair")
    b_id = add_member(a.org_id, "admin")
    b = TestClient(app, headers={"Authorization": f"Bearer {auth_keys.token(b_id, email='b@example.com')}"})
    barrier, codes = threading.Barrier(2), []

    def demote(c, target):
        barrier.wait()
        codes.append(c.patch(f"/orgs/current/members/{target}", json={"role": "viewer"}).status_code)
    ts = [threading.Thread(target=demote, args=(a, b_id)), threading.Thread(target=demote, args=(b, a.user_id))]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    with store.system() as c:
        admins = c.execute("select count(*) from members where org_id = %s and role = 'admin'",
                           (a.org_id,)).fetchone()[0]
    assert admins == 1 and sorted(codes) in ([200, 403], [200, 409])


def test_database_keeps_an_admin_even_without_the_api_check(org):
    """The trigger is the backstop for any path that skips app/members.py."""
    with pytest.raises(psycopg.errors.CheckViolation), store.system() as c:
        c.execute("update members set role = 'viewer' where org_id = %s", (org,))
    with pytest.raises(psycopg.errors.CheckViolation), store.system() as c:
        c.execute("delete from members where org_id = %s", (org,))
    with store.system() as c:  # deleting the whole Organization still works (cascades), audit rows included
        c.execute("delete from orgs where id = %s", (org,))
        assert c.execute("select count(*) from members where org_id = %s", (org,)).fetchone()[0] == 0
        assert c.execute("select count(*) from audit_events where org_id = %s", (org,)).fetchone()[0] == 0


def test_admin_of_one_organization_cannot_manage_another(sign_in):
    a, b = sign_in(org="A"), sign_in(org="B")
    b_member = add_member(b.org_id, "viewer")
    assert a.patch(f"/orgs/current/members/{b_member}", json={"role": "admin"}).status_code == 404
    assert a.delete(f"/orgs/current/members/{b_member}").status_code == 404
    assert a.delete(f"/orgs/current/members/{b.user_id}").status_code == 404
    assert str(b_member) not in {m["user_id"] for m in a.get("/orgs/current/members").json()}
    inv = b.post("/orgs/current/members", json={"email": "b-only@example.com", "role": "viewer"}).json()
    assert a.delete(f"/orgs/current/invitations/{inv['id']}").status_code == 404
    assert a.get("/orgs/current/invitations").json() == []


def test_members_list_shows_only_the_current_organization(sign_in):
    """The membership read policy also shows the caller's own rows in other Organizations (for sign-in); the
    list must still name only the current one."""
    a, b = sign_in(org="A"), sign_in(org="B")
    add_member(b.org_id, "editor", user_id=a.user_id)
    assert [m["user_id"] for m in a.get("/orgs/current/members").json()] == [str(a.user_id)]


# ---------------------------------------------------------------- switching Organizations

def test_switching_between_organizations(sign_in, auth_keys):
    home, other = sign_in(org="Home"), sign_in(org="Other")
    add_member(other.org_id, "viewer", user_id=home.user_id)
    token = auth_keys.token(home.user_id, email="home@example.com")
    as_other = TestClient(app, headers={"Authorization": f"Bearer {token}", "X-Org-Id": str(other.org_id)})

    me = home.get("/me").json()  # no header: the oldest membership
    assert me["org"]["name"] == "Home" and [o["name"] for o in me["orgs"]] == ["Home", "Other"]
    me = as_other.get("/me").json()
    assert me["org"] == {"id": str(other.org_id), "name": "Other", "role": "viewer"}

    jid = other.post("/jobs", json={"prompt": "Other's job"}).json()["id"]
    assert jid in [j["id"] for j in as_other.get("/jobs").json()]
    assert jid not in [j["id"] for j in home.get("/jobs").json()]
    assert as_other.post("/brainstorms", json={"sample": False}).status_code == 403  # Viewer there
    assert home.post("/brainstorms", json={"sample": False}).status_code == 201  # Admin at home


def test_switching_to_an_organization_you_are_not_in_is_refused(sign_in, auth_keys):
    mine, theirs = sign_in(org="Mine"), sign_in(org="Theirs")
    job = theirs.post("/jobs", json={"prompt": "secret"}).json()["id"]
    token = auth_keys.token(mine.user_id, email="m@example.com")
    for header in (str(theirs.org_id), str(uuid.uuid4()), "not-a-uuid", "' or 1=1 --"):
        c = TestClient(app, headers={"Authorization": f"Bearer {token}", "X-Org-Id": header})
        for method, path in (("GET", "/jobs"), ("GET", f"/jobs/{job}"), ("GET", "/orgs/current/members"),
                             ("POST", "/jobs/x/approve"), ("GET", "/audit")):
            r = c.request(method, path, json={})
            assert (r.status_code, r.json()["detail"]) == (403, "not a member of this Organization"), (header, path)
        me = c.get("/me").json()  # /me still answers, with the real choice, so the Admin can recover
        assert me["org"]["id"] == str(mine.org_id) and [o["name"] for o in me["orgs"]] == ["Mine"]


def test_removed_member_loses_access_on_the_next_call(sign_in, auth_keys):
    admin = sign_in(org="Team")
    uid = add_member(admin.org_id, "editor")
    c = TestClient(app, headers={"Authorization": f"Bearer {auth_keys.token(uid, email='e@example.com')}",
                                 "X-Org-Id": str(admin.org_id)})
    assert c.get("/jobs").status_code == 200
    assert admin.delete(f"/orgs/current/members/{uid}").status_code == 204
    assert c.get("/jobs").status_code == 403


def test_invited_person_can_still_create_their_own_organization(client, person, mail):
    link = client.post("/orgs/current/members", json={"email": "joiner@example.com", "role": "viewer"}).json()
    p = person("joiner@example.com")
    p.post("/invitations/accept", json={"token": _token(link["invite_url"])})
    r = p.post("/orgs", json={"name": "Joiner's own"})
    assert r.status_code == 201 and len(orgs.memberships(p.user_id)) == 2


# ---------------------------------------------------------------- audit log

def test_audit_log_records_decisions_edits_and_member_changes(sign_in, monkeypatch):
    from test_roles import _ready_job

    from app.pipeline import orchestrator, publishing

    async def fake(*a, **k):
        pass
    monkeypatch.setattr(publishing, "start", fake)
    monkeypatch.setattr(orchestrator, "run_job", fake)
    admin = sign_in(org="Audited")
    j1, j2 = _ready_job(admin.org_id), _ready_job(admin.org_id)
    assert admin.post(f"/jobs/{j1.id}/approve", json={"deck_version": 1}).status_code == 200
    assert admin.post(f"/jobs/{j2.id}/reject", json={"reason": "Off-brand"}).status_code == 200
    jid = admin.post("/jobs", json={"prompt": "Hongdae buskers"}).json()["id"]
    events = admin.get("/audit").json()
    by_action = {e["action"]: e for e in events}
    approved = by_action["deck.approved"]
    assert approved["target"] == j1.id and approved["detail"]["post"]  # the approval names its Post (#21)
    assert approved["detail"]["deck_version"] == 1 and approved["detail"]["mode"] == "mock"  # and its version (#19)
    assert by_action["deck.rejected"]["detail"] == {"reason": "Off-brand"}
    assert by_action["job.created"]["target"] == jid
    assert all(e["actor"] == str(admin.user_id) for e in events)
    assert events == sorted(events, key=lambda e: -e["id"])  # newest first
    page = admin.get(f"/audit?limit=2&before={events[1]['id']}").json()
    assert [e["id"] for e in page] == [e["id"] for e in events[2:4]]


def test_only_admins_read_the_audit_log_and_only_their_own(sign_in, auth_keys):
    a, b = sign_in(org="A"), sign_in(org="B")
    approver = add_member(a.org_id, "approver")
    c = TestClient(app, headers={"Authorization": f"Bearer {auth_keys.token(approver)}"})
    assert c.get("/audit").status_code == 403
    assert {e["action"] for e in a.get("/audit").json()} == {"org.created"}
    assert [e["detail"]["name"] for e in b.get("/audit").json()] == ["B"]


def test_rejection_and_its_audit_event_are_one_transaction(sign_in, monkeypatch):
    """If the audit event can't be written, the rejection isn't saved either."""
    from test_roles import _ready_job

    admin = sign_in(org="Atomic")
    job = _ready_job(admin.org_id)

    def broken(c, org_id, e):
        raise RuntimeError("audit down")
    monkeypatch.setattr(audit, "write", broken)
    with pytest.raises(RuntimeError):
        admin.post(f"/jobs/{job.id}/reject", json={"reason": "x"})
    assert store.get_job(admin.org_id, job.id).status == "READY_FOR_REVIEW"


# ---------------------------------------------------------------- database, as the API role

def _as_api(c, org_id, user_id):
    c.execute("set local role wok_api")
    c.execute("select set_config('app.org_id', %s, true), set_config('app.user_id', %s, true)",
              (str(org_id), str(user_id)))


def test_db_audit_events_are_append_only(org):
    with store.system() as c:
        admin = c.execute("select user_id from members where org_id = %s", (org,)).fetchone()[0]
    for sql in ("update audit_events set action = 'x'", "delete from audit_events"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege), store.tenant(org, user_id=admin) as c:
            c.execute(sql)
        # the table owner can't either (trigger)
        with pytest.raises(psycopg.errors.InsufficientPrivilege), store.system() as c:
            c.execute(sql + " where org_id = %s", (org,))
    with pytest.raises(psycopg.errors.InsufficientPrivilege), store.system() as c:
        c.execute("truncate audit_events")
    with store.system() as c:
        assert c.execute("select action from audit_events where org_id = %s", (org,)).fetchall() == [("org.created",)]


def test_db_audit_events_only_as_yourself_in_your_organization(org):
    other_org = orgs.create_org(uuid.uuid4(), "Other").org_id
    with store.system() as c:
        admin = c.execute("select user_id from members where org_id = %s", (org,)).fetchone()[0]
    viewer = add_member(org, "viewer")
    stranger = uuid.uuid4()
    ins = "insert into audit_events (org_id, actor, action) values (%s, %s, 'forged')"
    for org_id, actor, as_user in ((org, admin, viewer),       # someone else's name
                                   (other_org, viewer, viewer),  # another Organization
                                   (org, stranger, stranger)):   # not a Member at all
        with pytest.raises(psycopg.errors.InsufficientPrivilege), store.system() as c:
            _as_api(c, org, as_user)
            c.execute(ins, (org_id, actor))
    with store.system() as c:  # a Viewer may record their own action, but can't read the log
        _as_api(c, org, viewer)
        c.execute(ins, (org, viewer))
        assert c.execute("select count(*) from audit_events").fetchone()[0] == 0
    with store.system() as c:
        _as_api(c, org, admin)
        assert {r[0] for r in c.execute("select action from audit_events")} == {"org.created", "forged"}


def test_db_only_admins_change_members_and_only_the_role(org):
    with store.system() as c:
        admin = c.execute("select user_id from members where org_id = %s", (org,)).fetchone()[0]
    editor = add_member(org, "editor")
    with store.system() as c:  # an Editor's update matches no row (policy); a delete neither
        _as_api(c, org, editor)
        assert c.execute("update members set role = 'admin' where user_id = %s", (editor,)).rowcount == 0
        assert c.execute("delete from members where user_id = %s", (admin,)).rowcount == 0
    with pytest.raises(psycopg.errors.InsufficientPrivilege), store.system() as c:  # columns other than role
        _as_api(c, org, admin)
        c.execute("update members set user_id = %s where user_id = %s", (uuid.uuid4(), editor))
    with pytest.raises(psycopg.errors.InsufficientPrivilege), store.system() as c:  # joining is privileged
        _as_api(c, org, uuid.uuid4())
        c.execute("insert into members (org_id, user_id, role) values (%s, %s, 'admin')", (org, uuid.uuid4()))
    with store.system() as c:
        _as_api(c, org, admin)
        assert c.execute("update members set role = 'approver' where user_id = %s", (editor,)).rowcount == 1


def test_db_invitations_are_admin_only_and_hide_other_organizations(org):
    other = orgs.create_org(uuid.uuid4(), "Other").org_id
    with store.system() as c:
        admin = c.execute("select user_id from members where org_id = %s", (org,)).fetchone()[0]
        other_admin = c.execute("select user_id from members where org_id = %s", (other,)).fetchone()[0]
    editor = add_member(org, "editor")
    ins = "insert into invitations (email, role, token_hash, invited_by) values (%s, 'admin', %s, %s)"
    with pytest.raises(psycopg.errors.InsufficientPrivilege), store.system() as c:
        _as_api(c, org, editor)
        c.execute(ins, ("e@example.com", uuid.uuid4().hex, editor))
    with store.system() as c:
        _as_api(c, other, other_admin)
        c.execute(ins, ("o@example.com", uuid.uuid4().hex, other_admin))
    with store.system() as c:
        _as_api(c, org, admin)
        assert c.execute("select count(*) from invitations").fetchone()[0] == 0
        assert c.execute("update invitations set revoked_at = now()").rowcount == 0
    with pytest.raises(psycopg.errors.InsufficientPrivilege), store.system() as c:  # tokens and roles are fixed
        _as_api(c, other, other_admin)
        c.execute("update invitations set role = 'viewer'")


def test_db_role_function_answers_only_for_the_caller(org):
    with store.system() as c:
        admin = c.execute("select user_id from members where org_id = %s", (org,)).fetchone()[0]
    with store.system() as c:
        _as_api(c, org, admin)
        assert c.execute("select wok_role(%s)", (org,)).fetchone()[0] == "admin"
    with store.system() as c:
        _as_api(c, org, uuid.uuid4())
        assert c.execute("select wok_role(%s)", (org,)).fetchone()[0] is None
    with store.system() as c:
        for r in ("anon", "authenticated"):
            if c.execute("select 1 from pg_roles where rolname = %s", (r,)).fetchone():
                assert not c.execute("select has_function_privilege(%s, 'wok_role(uuid)', 'execute')",
                                     (r,)).fetchone()[0]
        assert not c.execute("select has_function_privilege('public', 'wok_role(uuid)', 'execute')").fetchone()[0]
