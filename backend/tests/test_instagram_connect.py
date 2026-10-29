"""Connect an Instagram account (#15): Instagram's OAuth against a fake Meta (httpx.MockTransport, never the network),
the signed single-use state, Pilot-only and per-Organization limits, personal accounts refused, the token kept in
Vault (never in a response, a log line or the `instagram_accounts` row), disconnect, audit, and isolation."""

import logging
import secrets
import time
import uuid
from urllib.parse import parse_qs, urlparse

import conftest
import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from app import instagram_accounts as ig
from app import instagram_api, pilot, store
from app.config import settings
from app.main import app
from app.secret_store import secrets as secret_store

anon = TestClient(app)  # Instagram's redirect carries no session
APP_SECRET = "app-secret-" + secrets.token_hex(8)
CALLBACK = "https://api.example.test/instagram-accounts/callback"


class FakeMeta:
    """Instagram's OAuth and /me endpoints. Tokens are random per test so a leak can't hide behind a constant."""

    def __init__(self):
        self.account_type = "BUSINESS"
        self.user_id, self.username = "17841400000000001", "seoulbrand"
        self.short = "IGshort" + secrets.token_hex(12)
        self.long = "IGlong" + secrets.token_hex(12)
        self.fail_exchange = False
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url, q = request.url, dict(request.url.params)
        if url.host == "api.instagram.com" and url.path == "/oauth/access_token":
            form = parse_qs(request.content.decode())
            if self.fail_exchange:
                return httpx.Response(400, json={"error_type": "OAuthException", "code": 400,
                                                 "error_message": f"Invalid code {form['code'][0]}"})
            assert form["client_secret"] == [APP_SECRET] and form["redirect_uri"] == [CALLBACK]
            assert form["grant_type"] == ["authorization_code"] and form["client_id"] == ["1234"]
            assert not form["code"][0].endswith("#_")
            return httpx.Response(200, json={"data": [{"access_token": self.short, "user_id": "999",
                                                       "permissions": "instagram_business_basic"}]})
        if url.host == "graph.instagram.com" and url.path == "/access_token":
            assert q == {"grant_type": "ig_exchange_token", "client_secret": APP_SECRET, "access_token": self.short}
            return httpx.Response(200, json={"access_token": self.long, "token_type": "bearer",
                                             "expires_in": 5183944})
        if url.host == "graph.instagram.com" and url.path == f"/{settings.instagram_api_version}/me":
            assert q["access_token"] == self.long
            body = {"user_id": self.user_id, "username": self.username}
            if self.account_type is not None:
                body["account_type"] = self.account_type
            return httpx.Response(200, json={"data": [body]})
        return httpx.Response(404, json={"error": {"type": "NotFound", "code": 1}})

    def secrets(self) -> list[str]:
        return [self.short, self.long, APP_SECRET]


@pytest.fixture
def meta(monkeypatch):
    m = FakeMeta()
    monkeypatch.setattr(instagram_api, "transport", httpx.MockTransport(m.handler))
    monkeypatch.setattr(settings, "instagram_app_id", "1234")
    monkeypatch.setattr(settings, "instagram_app_secret", APP_SECRET)
    monkeypatch.setattr(settings, "instagram_redirect_uri", CALLBACK)
    monkeypatch.setattr(settings, "admin_url", "https://admin.example.test")
    return m


def set_pilot(org_id, on=True, limit=None):
    with store.system() as c:
        c.execute("update orgs set is_pilot = %s, instagram_account_limit = %s where id = %s", (on, limit, org_id))


def add_member(org_id, role):
    from test_roles import add_member as add
    uid = add(org_id, role)
    return uid


def member_client(auth_keys, org_id, uid):
    c = TestClient(app, headers={"Authorization": f"Bearer {auth_keys.token(uid, email=f'{uid.hex[:8]}@example.com')}"})
    c.org_id, c.user_id = org_id, uid
    return c


@pytest.fixture
def admin(sign_in, meta):
    c = sign_in(org="Pilot Co")
    set_pilot(c.org_id)
    return c


def start(client) -> str:
    r = client.post("/instagram-accounts/connect")
    assert r.status_code == 200, r.text
    url = urlparse(r.json()["authorize_url"])
    q = parse_qs(url.query)
    assert (url.scheme, url.netloc, url.path) == ("https", "www.instagram.com", "/oauth/authorize")
    assert q["client_id"] == ["1234"] and q["redirect_uri"] == [CALLBACK] and q["response_type"] == ["code"]
    assert q["scope"] == ["instagram_business_basic,instagram_business_content_publish"]
    return q["state"][0]


def finish(state: str, **params) -> dict:
    """Instagram's redirect back to the API; returns the Admin page's query string the API redirects to."""
    params = {"code": "AQDcode" + secrets.token_hex(6) + "#_", "state": state, **params}
    r = anon.get("/instagram-accounts/callback", params={k: v for k, v in params.items() if v is not None},
                 follow_redirects=False)
    assert r.status_code == 303, r.text
    loc = urlparse(r.headers["location"])
    assert (loc.scheme, loc.netloc, loc.path) == ("https", "admin.example.test", "/instagram")
    return {k: v[0] for k, v in parse_qs(loc.query).items()}


def rows(org_id):
    with store.system() as c:
        return c.execute("select id, status, token_ref, row_to_json(a)::text from instagram_accounts a "
                         "where org_id = %s order by connected_at", (org_id,)).fetchall()


def vault_count(name_prefix: str) -> int:
    with store.system() as c:
        return c.execute("select count(*) from vault.secrets where name like %s", (name_prefix + "%",)).fetchone()[0]


def audit_actions(org_id):
    with store.system() as c:
        return [r[0] for r in c.execute("select action from audit_events where org_id = %s order by id",
                                        (org_id,)).fetchall()]


# ---------------------------------------------------------------- the happy path

def test_admin_connects_a_business_account(admin, meta, caplog):
    caplog.set_level(logging.DEBUG)
    assert admin.get("/instagram-accounts/connection").json() == {
        "configured": True, "pilot": True, "limit": 1, "connected": 0, "can_connect": True, "reason": None}

    assert finish(start(admin)) == {"connected": "seoulbrand"}

    listed = admin.get("/instagram-accounts")
    assert listed.status_code == 200
    [acct] = listed.json()
    assert acct["handle"] == "@seoulbrand" and acct["account_type"] == "BUSINESS" and acct["status"] == "connected"
    assert acct["live"] is True and acct["token_expires_at"] and set(acct) >= {"id", "connected_at"}
    assert "token_ref" not in acct and "ig_user_id" not in acct

    # The row holds a Vault reference only; the token comes back only through the publisher's helper.
    [(acct_id, status, ref, raw)] = rows(admin.org_id)
    assert status == "connected" and ref is not None and str(acct_id) == acct["id"]
    assert not any(s in raw for s in meta.secrets())
    with store.system() as c:
        assert ig.access_token(c, admin.org_id, acct_id) == meta.long
        assert ig.access_token(c, uuid.uuid4(), acct_id) is None  # another Organization's id finds nothing

    # Meta was called in the documented order; the "#_" Instagram appends was stripped (FakeMeta asserts it).
    assert [r.url.path for r in meta.requests] == ["/oauth/access_token", "/access_token",
                                                   f"/{settings.instagram_api_version}/me"]
    assert "instagram.connected" in audit_actions(admin.org_id)

    # No secret in any log line; httpx did log the requests, with the secrets redacted.
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert not any(s in logged for s in meta.secrets())
    assert "access_token=[redacted]" in logged


def test_creator_accounts_connect_too(admin, meta):
    meta.account_type = "Media_Creator"  # Meta's docs spell it like this; compared case-insensitively
    assert finish(start(admin)) == {"connected": "seoulbrand"}
    assert admin.get("/instagram-accounts").json()[0]["account_type"] == "MEDIA_CREATOR"


def test_no_response_ever_carries_the_token(admin, meta):
    state = start(admin)
    r = anon.get("/instagram-accounts/callback", params={"code": "c", "state": state}, follow_redirects=False)
    bodies = [r.text, r.headers["location"]]
    for path in ("/instagram-accounts", "/instagram-accounts/connection", "/audit", "/me"):
        bodies.append(admin.get(path).text)
    assert not any(s in b for s in meta.secrets() for b in bodies)


# ---------------------------------------------------------------- personal accounts

@pytest.mark.parametrize("kind", ["PERSONAL", None, ""])
def test_personal_account_is_refused_with_a_guide(admin, meta, kind):
    meta.account_type = kind
    assert finish(start(admin)) == {"error": "personal_account"}
    assert rows(admin.org_id) == [] and vault_count(f"instagram/{admin.org_id}/") == 0
    assert "instagram.connected" not in audit_actions(admin.org_id)


# ---------------------------------------------------------------- state: signed, unexpired, single use

def test_reused_state_is_refused(admin, meta):
    state = start(admin)
    assert finish(state) == {"connected": "seoulbrand"}
    assert finish(state) == {"error": "state"}
    assert len(meta.requests) == 3  # the second callback never reached Instagram


def test_forged_states_are_refused(admin, meta):
    state = start(admin)
    payload, sig = state.split(".")
    other = ig._b64(b'{"o":"%s","u":"%s","n":"x","e":%d}' % (str(admin.org_id).encode(), str(admin.user_id).encode(),
                                                            int(time.time()) + 600))
    for forged in ["", "garbage", payload, f"{payload}.", f"{payload}.{sig[:-2]}AA", f"{other}.{sig}",
                   state + "x", "a.b.c"]:
        assert finish(forged) == {"error": "state"}, forged
    assert meta.requests == []
    assert finish(state) == {"connected": "seoulbrand"}  # the real one still works once


def test_expired_state_is_refused(admin, meta, monkeypatch):
    state = start(admin)
    monkeypatch.setattr(time, "time", lambda: 10**10)
    assert finish(state) == {"error": "state"}


def test_a_validly_signed_state_without_a_saved_nonce_or_for_someone_else_is_refused(admin, meta, sign_in):
    exp = int(time.time()) + 600
    assert finish(ig.sign_state(admin.org_id, admin.user_id, "never-issued", exp)) == {"error": "state"}
    # A nonce issued to this Admin can't be replayed under another Organization or person.
    real = ig.verify_state(start(admin))
    other = sign_in(org="Other")
    assert finish(ig.sign_state(other.org_id, admin.user_id, real[2], exp)) == {"error": "state"}
    real2 = ig.verify_state(start(admin))
    assert finish(ig.sign_state(admin.org_id, other.user_id, real2[2], exp)) == {"error": "state"}
    assert rows(admin.org_id) == [] and rows(other.org_id) == []


def test_state_signed_with_another_secret_is_refused(admin, meta, monkeypatch):
    state = start(admin)
    monkeypatch.setattr(settings, "instagram_app_secret", "rotated-" + APP_SECRET)
    assert finish(state) == {"error": "state"}


def test_declined_on_instagram(admin, meta):
    state = start(admin)
    assert finish(state, code=None, error="access_denied", error_reason="user_denied") == {"error": "denied"}
    assert finish(state) == {"error": "state"}  # used up even though nothing was connected
    assert meta.requests == []


def test_instagram_error_is_reported_without_leaking(admin, meta, caplog):
    caplog.set_level(logging.DEBUG)
    meta.fail_exchange = True
    assert finish(start(admin), code="AQDsecretcode") == {"error": "instagram"}
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "AQDsecretcode" not in logged and APP_SECRET not in logged
    assert "HTTP 400 (OAuthException 400)" in logged
    assert rows(admin.org_id) == []


# ---------------------------------------------------------------- Pilot Organizations, Admins, limits

def test_only_pilot_organizations_can_connect(sign_in, meta):
    c = sign_in(org="Not a pilot")
    s = c.get("/instagram-accounts/connection").json()
    assert s["pilot"] is False and s["can_connect"] is False and s["reason"] == ig.NOT_PILOT
    r = c.post("/instagram-accounts/connect")
    assert (r.status_code, r.json()["detail"]) == (403, ig.NOT_PILOT)


def test_pilot_withdrawn_mid_flow(admin, meta):
    state = start(admin)
    set_pilot(admin.org_id, False)
    assert finish(state) == {"error": "not_pilot"}
    assert rows(admin.org_id) == [] and vault_count(f"instagram/{admin.org_id}/") == 0


def test_only_admins_connect_and_disconnect(admin, meta, auth_keys):
    for role in ("viewer", "editor", "approver"):
        c = member_client(auth_keys, admin.org_id, add_member(admin.org_id, role))
        s = c.get("/instagram-accounts/connection").json()
        assert s["can_connect"] is False and s["reason"] == ig.NOT_ADMIN
        assert c.post("/instagram-accounts/connect").status_code == 403
    finish(start(admin))
    viewer = member_client(auth_keys, admin.org_id, add_member(admin.org_id, "viewer"))
    [acct] = viewer.get("/instagram-accounts").json()  # every Member sees the accounts
    assert viewer.delete(f"/instagram-accounts/{acct['id']}").status_code == 403
    assert rows(admin.org_id)[0][1] == "connected"


def test_admin_demoted_mid_flow(admin, meta):
    state = start(admin)
    add_member(admin.org_id, "admin")  # someone else stays Admin
    with store.system() as c:
        c.execute("update members set role = 'editor' where org_id = %s and user_id = %s",
                  (admin.org_id, admin.user_id))
    assert finish(state) == {"error": "not_admin"}
    assert rows(admin.org_id) == []


def test_account_limit(admin, meta):
    first = start(admin)
    early = start(admin)  # a second tab, started while there was still room
    assert finish(first) == {"connected": "seoulbrand"}
    s = admin.get("/instagram-accounts/connection").json()
    assert (s["connected"], s["limit"], s["can_connect"]) == (1, 1, False)
    assert s["reason"] == ig.limit_reached(1)
    assert admin.post("/instagram-accounts/connect").status_code == 409

    meta.user_id, meta.username = "17841400000000002", "secondbrand"
    assert finish(early) == {"error": "limit"}  # checked again when the account comes back
    assert len(rows(admin.org_id)) == 1 and vault_count(f"instagram/{admin.org_id}/") == 1

    set_pilot(admin.org_id, limit=2)  # what billing (#29) will do per plan
    assert finish(start(admin)) == {"connected": "secondbrand"}
    assert [a["handle"] for a in admin.get("/instagram-accounts").json()] == ["@seoulbrand", "@secondbrand"]

    set_pilot(admin.org_id, limit=0)
    assert admin.get("/instagram-accounts/connection").json()["reason"] == ig.limit_reached(0)


def test_reconnecting_the_same_account_replaces_its_token(admin, meta):
    finish(start(admin))
    [(acct_id, _, ref, _)] = rows(admin.org_id)
    old = meta.long
    meta.long = "IGlong" + secrets.token_hex(12)
    with store.system() as c:  # what token refresh (#22) does when Instagram revokes the token
        c.execute("update instagram_accounts set status = 'revoked' where id = %s", (acct_id,))
    # At the limit, but an account that needs reconnecting keeps Connect available and takes no new slot.
    assert admin.get("/instagram-accounts/connection").json()["can_connect"] is True
    assert finish(start(admin)) == {"connected": "seoulbrand"}
    [(acct_id2, status, ref2, _)] = rows(admin.org_id)
    assert (acct_id2, status, ref2) == (acct_id, "connected", ref)  # same row, same Vault secret, new value
    with store.system() as c:
        assert ig.access_token(c, admin.org_id, acct_id) == meta.long != old
    assert audit_actions(admin.org_id).count("instagram.connected") == 2


# ---------------------------------------------------------------- disconnect

def test_disconnect_drops_the_token_from_vault(admin, meta):
    finish(start(admin))
    [acct] = admin.get("/instagram-accounts").json()
    assert vault_count(f"instagram/{admin.org_id}/") == 1
    assert admin.delete(f"/instagram-accounts/{acct['id']}").status_code == 204
    assert admin.get("/instagram-accounts").json() == []
    [(_, status, ref, _)] = rows(admin.org_id)
    assert (status, ref) == ("disconnected", None) and vault_count(f"instagram/{admin.org_id}/") == 0
    assert audit_actions(admin.org_id)[-1] == "instagram.disconnected"
    assert admin.delete(f"/instagram-accounts/{acct['id']}").status_code == 404
    with store.system() as c:
        assert ig.access_token(c, admin.org_id, uuid.UUID(acct["id"])) is None
    # The slot is free again, and reconnecting brings the same row back.
    assert admin.get("/instagram-accounts/connection").json()["can_connect"] is True
    assert finish(start(admin)) == {"connected": "seoulbrand"}
    assert admin.get("/instagram-accounts").json()[0]["id"] == acct["id"]


def test_deleting_an_organization_drops_its_tokens(admin, meta):
    finish(start(admin))
    with store.system() as c:
        c.execute("delete from orgs where id = %s", (admin.org_id,))
    assert vault_count(f"instagram/{admin.org_id}/") == 0


# ---------------------------------------------------------------- isolation and the database's own guarantees

def test_other_organizations_see_and_touch_nothing(admin, meta, sign_in):
    finish(start(admin))
    [acct] = admin.get("/instagram-accounts").json()
    b = sign_in(org="B")
    set_pilot(b.org_id)
    assert b.get("/instagram-accounts").json() == []
    assert b.get("/instagram-accounts/connection").json()["connected"] == 0
    assert b.delete(f"/instagram-accounts/{acct['id']}").status_code == 404
    assert rows(admin.org_id)[0][1] == "connected"
    # B connecting the same Instagram account gets its own row and its own secret.
    assert finish(start(b)) == {"connected": "seoulbrand"}
    assert len(rows(admin.org_id)) == len(rows(b.org_id)) == 1


def _as_api(org_id, user_id):
    return store.tenant(org_id, user_id=user_id)


def test_api_role_cannot_reach_tokens_or_change_what_matters(admin, meta, sign_in):
    finish(start(admin))
    [(acct_id, _, ref, _)] = rows(admin.org_id)
    attempts = [
        "select * from vault.decrypted_secrets",
        "select * from vault.secrets",
        "select vault.create_secret('x', 'y', '')",
        f"update instagram_accounts set token_ref = gen_random_uuid() where id = '{acct_id}'",
        f"update instagram_accounts set username = 'evil' where id = '{acct_id}'",
        ("insert into instagram_accounts (ig_user_id, username, account_type, token_ref, connected_by) "
         f"values ('1', 'x', 'BUSINESS', '{ref}', '{admin.user_id}')"),
        f"delete from instagram_accounts where id = '{acct_id}'",
        f"update orgs set is_pilot = true where id = '{admin.org_id}'",
        f"update orgs set instagram_account_limit = 99 where id = '{admin.org_id}'",
        "select * from instagram_connect_states",
    ]
    for sql in attempts:
        with pytest.raises(psycopg.errors.InsufficientPrivilege), _as_api(admin.org_id, admin.user_id) as c:
            c.execute(sql)
    # Even an Admin can only move an account to `disconnected`, never back to connected.
    with pytest.raises(psycopg.errors.InsufficientPrivilege), _as_api(admin.org_id, admin.user_id) as c:
        c.execute("update instagram_accounts set status = 'revoked' where id = %s", (acct_id,))
    # Another Organization's Admin updates nothing.
    b = sign_in(org="B")
    with _as_api(b.org_id, b.user_id) as c:
        assert c.execute("update instagram_accounts set status = 'disconnected' where id = %s",
                         (acct_id,)).rowcount == 0
        assert c.execute("select count(*) from instagram_accounts").fetchone()[0] == 0
    assert rows(admin.org_id)[0][1] == "connected"


def test_nonces_are_inserted_only_by_an_admin_for_themselves(admin, auth_keys):
    viewer = add_member(admin.org_id, "viewer")
    for org_id, user_id, row_user in [(admin.org_id, viewer, viewer), (admin.org_id, admin.user_id, viewer)]:
        with pytest.raises(psycopg.errors.InsufficientPrivilege), _as_api(org_id, user_id) as c:
            c.execute("insert into instagram_connect_states (nonce_hash, user_id, expires_at) "
                      "values (%s, %s, now() + interval '1 hour')", (secrets.token_hex(8), row_user))


@pytest.mark.skipif(conftest.VAULT != "supabase_vault", reason="needs a Postgres with the supabase_vault extension")
def test_real_vault_encrypts_the_token(admin, meta):
    finish(start(admin))
    with store.system() as c:
        stored = c.execute("select secret from vault.secrets where name like %s",
                           (f"instagram/{admin.org_id}/%",)).fetchone()[0]
    assert meta.long not in stored


# ---------------------------------------------------------------- server set-up

def test_not_configured(sign_in, monkeypatch):
    c = sign_in(org="X")
    set_pilot(c.org_id)
    s = c.get("/instagram-accounts/connection").json()
    assert (s["configured"], s["can_connect"], s["reason"]) == (False, False, ig.NOT_CONFIGURED)
    assert c.post("/instagram-accounts/connect").status_code == 503
    r = anon.get("/instagram-accounts/callback", params={"code": "c", "state": "s"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("/instagram?error=state")


def test_no_vault_means_no_connecting(admin, meta, monkeypatch):
    monkeypatch.setattr(secret_store, "available", lambda c: False)
    s = admin.get("/instagram-accounts/connection").json()
    assert s["reason"] == ig.NO_VAULT
    assert admin.post("/instagram-accounts/connect").status_code == 503


def test_vault_round_trip():
    name = f"test/{uuid.uuid4()}"
    with store.system() as c:
        ref = secret_store.put(c, name, "one")
        assert secret_store.put(c, name, "two") == ref
        assert secret_store.get(c, ref) == "two"
        secret_store.delete(c, ref)
        assert secret_store.get(c, ref) is None


def test_log_redaction_covers_uvicorn_access_lines(caplog):
    caplog.set_level(logging.INFO)
    logging.getLogger("uvicorn.access").info('%s - "%s %s HTTP/%s" %d', "1.2.3.4", "GET",
                                             "/instagram-accounts/callback?code=AQDsecret&state=abc.def", "1.1", 303)
    assert "AQDsecret" not in caplog.text and "abc.def" not in caplog.text
    assert "code=[redacted]" in caplog.text


def test_pilot_command(org, capsys):
    pilot.main(["on", str(org)])
    pilot.main(["limit", str(org), "3"])
    with store.system() as c:
        assert c.execute("select is_pilot, instagram_account_limit from orgs where id = %s", (org,)).fetchone() == (True, 3)
    pilot.main(["limit", str(org), "default"])
    pilot.main(["off", str(org)])
    pilot.main(["list"])
    assert f"{org}" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        pilot.main(["on", "not-a-uuid"])
    with pytest.raises(SystemExit):
        pilot.main(["on", str(uuid.uuid4())])
