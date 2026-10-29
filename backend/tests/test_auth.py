"""Sign-in (#12): every route except /health needs a valid Supabase session; first sign-up creates an
Organization with the person as its Admin. Tokens are signed locally (conftest `auth_keys`), no Supabase."""

import re
import threading
import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app import auth, orgs, store
from app.config import settings
from app.main import app
from app.pipeline import orchestrator

anon = TestClient(app)


def _routes():
    for r in app.routes:
        if isinstance(r, APIRoute) and r.path != "/health" and r.include_in_schema:
            for m in r.methods:
                yield m, re.sub(r"\{[^}]+\}", "x", r.path)


@pytest.mark.parametrize(("method", "path"), sorted(_routes()))
def test_every_route_but_health_needs_a_session(method, path, auth_keys):
    assert anon.request(method, path).status_code == 401
    bad = auth_keys.token(ttl=-3600)  # expired
    r = anon.request(method, path, headers={"Authorization": f"Bearer {bad}"})
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"


def test_health_and_slide_files_stay_public():
    assert anon.get("/health").status_code == 200
    (settings.output_dir / "pub").mkdir(parents=True, exist_ok=True)
    (settings.output_dir / "pub/slide-00.jpg").write_bytes(b"jpg")
    assert anon.get("/assets/pub/slide-00.jpg").status_code == 200  # Instagram fetches these anonymously


def test_admin_token_is_gone():
    assert not hasattr(settings, "admin_token")


def test_401_carries_cors_headers():
    r = anon.get("/jobs", headers={"Origin": "http://localhost:3000"})
    assert r.status_code == 401 and r.headers.get("access-control-allow-origin") == "http://localhost:3000"


def _forged_tokens(k):
    other = ec.generate_private_key(ec.SECP256R1())
    yield "expired", k.token(ttl=-3600)
    yield "wrong audience", k.token(aud="anon")
    yield "wrong issuer", k.token(iss="http://evil.test/auth/v1")
    yield "anon role", k.token(role="anon")
    yield "service role", k.token(role="service_role")
    yield "anonymous user", k.token(is_anonymous=True)
    yield "no sub", k.token(sub=None)
    yield "sub not a uuid", k.token(sub="admin")
    yield "no exp", k.token(exp=None)
    yield "other key, same kid", k.token(key=other)
    yield "unknown kid", k.token(key=other, kid="rotated-away")
    yield "HS256 without a configured secret", k.token(key="super-secret-jwt-token-with-at-least-32-characters", alg="HS256")
    yield "alg none", jwt.encode({"sub": str(uuid.uuid4()), "aud": "authenticated", "role": "authenticated",
                                  "iss": "http://supabase.test/auth/v1", "exp": int(time.time()) + 60},
                                 None, algorithm="none")
    yield "garbage", "not-a-jwt"


@pytest.mark.parametrize("case", range(14))
def test_forged_or_wrong_tokens_are_rejected(case, auth_keys, sign_in):
    name, token = list(_forged_tokens(auth_keys))[case]
    r = anon.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401, name


def test_token_must_be_a_bearer_header(auth_keys):
    token = auth_keys.token()
    assert anon.get("/me", headers={"Authorization": token}).status_code == 401
    assert anon.get("/me", headers={"Authorization": f"Basic {token}"}).status_code == 401
    assert anon.get(f"/me?token={token}").status_code == 401  # never in the URL
    assert anon.get("/me", headers={"Authorization": f"bearer {token}"}).status_code == 200


def test_legacy_hs256_projects_work_when_the_secret_is_set(monkeypatch, auth_keys):
    secret = "legacy-secret-with-at-least-32-characters!"
    ident = auth.SupabaseIdentity("http://supabase.test", secret, fetch=auth_keys.fetch)
    monkeypatch.setattr(auth, "_identity", ((settings.supabase_url, settings.supabase_jwt_secret), ident))
    good = auth_keys.token(key=secret, alg="HS256")
    assert anon.get("/me", headers={"Authorization": f"Bearer {good}"}).status_code == 200
    forged = auth_keys.token(key="another-secret-with-at-least-32-characters", alg="HS256")
    assert anon.get("/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_unconfigured_server_rejects_everything(monkeypatch, auth_keys):
    monkeypatch.setattr(settings, "supabase_url", "")
    r = anon.get("/me", headers={"Authorization": f"Bearer {auth_keys.token()}"})
    assert r.status_code == 401 and "SUPABASE_URL" in r.json()["detail"]


# ---------------------------------------------------------------- JWKS cache

def test_jwks_is_cached_and_unknown_kids_refetch_at_most_every_min_refresh(auth_keys):
    fetches = []

    def fetch(url):
        fetches.append(url)
        return auth_keys.jwks

    ident = auth.SupabaseIdentity("http://supabase.test", fetch=fetch, min_refresh=60)
    for _ in range(5):
        assert ident.verify(auth_keys.token()) is not None
    assert len(fetches) == 1
    other = ec.generate_private_key(ec.SECP256R1())
    for i in range(5):  # forged kids must not turn into one Supabase request each
        assert ident.verify(auth_keys.token(key=other, kid=f"forged-{i}")) is None
    assert len(fetches) == 1  # the first fetch was < 60 s ago


def test_rotated_key_is_picked_up_and_a_failed_fetch_keeps_old_keys(auth_keys):
    from jwt.algorithms import ECAlgorithm

    new = ec.generate_private_key(ec.SECP256R1())
    new_jwk = {**ECAlgorithm.to_jwk(new.public_key(), as_dict=True), "kid": "k2", "alg": "ES256", "use": "sig"}
    served = [auth_keys.jwks]

    def fetch(url):
        if served[0] is None:
            raise OSError("supabase down")
        return served[0]

    ident = auth.SupabaseIdentity("http://supabase.test", fetch=fetch, min_refresh=0)
    assert ident.verify(auth_keys.token()) is not None
    served[0] = {"keys": [*auth_keys.jwks["keys"], new_jwk]}
    assert ident.verify(auth_keys.token(key=new, kid="k2")) is not None  # unknown kid → refetch
    served[0] = None
    ident._fetched_at -= 10_000  # TTL passed and Supabase is unreachable
    assert ident.verify(auth_keys.token()) is not None


def test_jwks_ignores_symmetric_and_encryption_keys():
    import base64

    secret = b"s" * 32
    jwks = {"keys": [{"kty": "oct", "kid": "hs", "k": base64.urlsafe_b64encode(secret).decode().rstrip("=")}]}
    ident = auth.SupabaseIdentity("http://supabase.test", fetch=lambda _: jwks)
    token = jwt.encode({"sub": str(uuid.uuid4()), "aud": "authenticated", "role": "authenticated",
                        "iss": "http://supabase.test/auth/v1", "exp": int(time.time()) + 60},
                       secret, algorithm="HS256", headers={"kid": "hs"})
    assert ident.verify(token) is None


# ---------------------------------------------------------------- Organizations

def test_first_sign_up_creates_an_organization_with_the_person_as_admin(sign_in):
    c = sign_in(org=None)
    me = c.get("/me").json()
    assert me["org"] is None and me["orgs"] == [] and me["user"]["email"].endswith("@example.com")
    assert c.get("/jobs").status_code == 403  # signed in, but no Organization yet
    r = c.post("/orgs", json={"name": "  Seoul   Weekly "})
    assert r.status_code == 201 and r.json()["name"] == "Seoul Weekly" and r.json()["role"] == "admin"
    me = c.get("/me").json()
    assert me["org"] == r.json() and me["orgs"] == [r.json()]
    assert c.get("/jobs").status_code == 200
    assert c.post("/orgs", json={"name": "Second"}).status_code == 409  # one Organization per person (#14)


@pytest.mark.parametrize("name", ["", "   ", "x" * 101])
def test_organization_needs_a_name(sign_in, name):
    assert sign_in(org=None).post("/orgs", json={"name": name}).status_code == 422


def test_double_submitted_sign_up_creates_one_organization():
    user = uuid.uuid4()
    results = []

    def go():
        try:
            results.append(orgs.create_org(user, "Race"))
        except orgs.AlreadyMember:
            results.append(None)

    threads = [threading.Thread(target=go) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(r is not None for r in results) == 1 and len(orgs.memberships(user)) == 1


def test_people_see_only_their_own_membership(sign_in):
    a, b = sign_in(org="A"), sign_in(org="B")
    assert [o["name"] for o in a.get("/me").json()["orgs"]] == ["A"]
    assert [o["name"] for o in b.get("/me").json()["orgs"]] == ["B"]


def test_org_tables_have_row_level_security():
    with store._db().connection() as c:
        rows = c.execute("select relname from pg_class where relnamespace = 'public'::regnamespace "
                         "and relkind = 'r' and not relrowsecurity").fetchall()
    assert rows == []  # nothing in public is readable through Supabase's auto-generated API


# ---------------------------------------------------------------- live updates

def test_job_events_end_when_the_session_expires(auth_keys):
    user = uuid.uuid4()
    orgs.create_org(user, "SSE")
    job = store.save_job(orchestrator.new_job(uuid.uuid4().hex[:6], "still running"))  # QUEUED: not terminal
    almost = auth_keys.token(user, ttl=-5)  # expired 5 s ago, still inside the clock-skew leeway
    with anon.stream("GET", f"/jobs/{job.id}/events", headers={"Authorization": f"Bearer {almost}"}) as r:
        assert r.status_code == 200 and r.read() == b""  # closes at once; the Admin reconnects with a new token
    short = auth_keys.token(user, ttl=1)  # valid: sends the snapshot, then ends when the token does
    r = anon.get(f"/jobs/{job.id}/events", headers={"Authorization": f"Bearer {short}"})
    assert r.status_code == 200 and r.text.startswith("data: ") and r.text.count("data: ") == 1
