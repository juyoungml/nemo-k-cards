import os
import secrets
import tempfile
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

# Before app.config is imported: keep test jobs/slides out of backend/.data and never call real agents.
os.environ["OUTPUT_DIR"] = os.path.join(tempfile.mkdtemp(prefix="wok-test-"), "out")
os.environ["DEMO_MODE"] = "fixture"

# A throwaway database per run (never DATABASE_URL, which may be live) with supabase/migrations applied in
# order; dropped in pytest_sessionfinish. The default server is the local `supabase start` Postgres.
TEST_SERVER = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres")
TEST_DB = f"wok_test_{secrets.token_hex(4)}"
_host = conninfo_to_dict(TEST_SERVER).get("host") or "localhost"
assert os.environ.get("CI") or _host in ("localhost", "127.0.0.1", "::1") or _host.startswith("/"), \
    f"TEST_DATABASE_URL must point at a local server, not {_host}"


def _drop_test_db() -> None:
    with psycopg.connect(TEST_SERVER, autocommit=True) as c:
        c.execute(f"drop database if exists {TEST_DB} with (force)")


with psycopg.connect(TEST_SERVER, autocommit=True) as _c:
    _c.execute(f"create database {TEST_DB}")
os.environ["DATABASE_URL"] = make_conninfo(TEST_SERVER, dbname=TEST_DB)
try:
    with psycopg.connect(os.environ["DATABASE_URL"]) as _c:
        for sql in sorted((Path(__file__).resolve().parents[2] / "supabase/migrations").glob("*.sql")):
            _c.execute(sql.read_text())
        # Instagram tokens (#15) go to Supabase Vault. The migration installs it where Postgres has it (local
        # Supabase); plain Postgres gets a plain-text stand-in with the same interface, for tests only.
        if _c.execute("select to_regclass('vault.decrypted_secrets')").fetchone()[0] is None:
            _c.execute((Path(__file__).parent / "vault_shim.sql").read_text())
            VAULT = "shim"
        else:
            VAULT = "supabase_vault"
except Exception:
    _drop_test_db()
    raise

# Tests must never reach real accounts, even on a machine whose backend/.env holds live credentials:
# explicit env vars win over .env in pydantic-settings, so blank every outward-facing secret and mode.
for key, value in {
    "PUBLISH_MODE": "mock", "IMAGE_HOST": "local", "AGENT_RUNNER": "local", "DBOS_DATABASE_URL": "",
    "IG_USER_ID": "", "IG_ACCESS_TOKEN": "", "PUBLIC_ASSET_BASE_URL": "",
    # Instagram connect (#15): tests that need it set a fake app on `settings` and fake Meta with MockTransport.
    "INSTAGRAM_APP_ID": "", "INSTAGRAM_APP_SECRET": "", "INSTAGRAM_REDIRECT_URI": "",
    "INSTAGRAM_ACCOUNTS_PER_ORG": "1",
    # Sign-in is checked against a fake Supabase whose signing key exists only in this process (see `auth_keys`).
    "SUPABASE_URL": "http://supabase.test", "SUPABASE_JWT_SECRET": "", "SUPABASE_SERVICE_KEY": "",
    "ANTHROPIC_API_KEY": "", "CLAUDE_CODE_OAUTH_TOKEN": "",
}.items():
    os.environ[key] = value


def pytest_sessionstart(session):
    """Fail fast if isolation is broken: a test run must never see live credentials or publish for real."""
    from app.config import settings
    assert settings.publish_mode == "mock" and not settings.supabase_service_key \
        and not settings.instagram_app_secret, \
        "tests are not isolated from live credentials"
    assert "wok-test-" in str(settings.output_dir), "tests would write into the real data dir"
    assert conninfo_to_dict(settings.database_url).get("dbname") == TEST_DB, \
        "tests would write into the real database"
    assert not settings.dbos_database_url, "DBOS would keep its checkpoints outside the test database"
    # Durable Jobs (#17): DBOS runs in this process against the test database, as it does in the API.
    from app.pipeline import durable
    durable.launch()


def pytest_sessionfinish(session, exitstatus):
    from app import store
    from app.pipeline import durable
    durable.shutdown()
    store.close()
    _drop_test_db()


# ---------------------------------------------------------------- sign-in

class AuthKeys:
    """A local stand-in for Supabase Auth: an ES256 key whose public half is served as the JWKS (no network)."""

    def __init__(self):
        from cryptography.hazmat.primitives.asymmetric import ec
        from jwt.algorithms import ECAlgorithm

        self.private = ec.generate_private_key(ec.SECP256R1())
        self.kid = secrets.token_hex(8)
        self.jwks = {"keys": [{**ECAlgorithm.to_jwk(self.private.public_key(), as_dict=True),
                               "kid": self.kid, "alg": "ES256", "use": "sig"}]}
        self.fetches = 0

    def fetch(self, url: str) -> dict:
        assert url == "http://supabase.test/auth/v1/.well-known/jwks.json", url
        self.fetches += 1
        return self.jwks

    def token(self, user_id=None, *, email="", ttl=3600, key=None, kid=None, alg="ES256", **claims) -> str:
        import time
        import uuid

        import jwt

        now = int(time.time())
        body = {"iss": "http://supabase.test/auth/v1", "aud": "authenticated", "role": "authenticated",
                "sub": str(user_id or uuid.uuid4()), "email": email, "iat": now, "exp": now + ttl,
                "is_anonymous": False, **claims}
        return jwt.encode({k: v for k, v in body.items() if v is not None}, key or self.private, algorithm=alg,
                          headers={"kid": kid or self.kid})


@pytest.fixture(scope="session")
def auth_keys() -> AuthKeys:
    return AuthKeys()


@pytest.fixture(autouse=True)
def _fake_supabase(auth_keys, monkeypatch):
    from app import auth
    from app.config import settings

    ident = auth.SupabaseIdentity(settings.supabase_url, fetch=auth_keys.fetch)
    monkeypatch.setattr(auth, "_identity", ((settings.supabase_url, settings.supabase_jwt_secret), ident))


@pytest.fixture(autouse=True)
def _no_instagram(monkeypatch):
    """The publisher and the dashboard never reach Meta in tests: any call they make that a test did not fake
    fails like a dropped connection. Tests that exercise them install their own httpx.MockTransport."""
    import httpx

    from app import seed
    from app.services import publisher

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"test isolation: {request.url.host} is not reachable from tests")
    monkeypatch.setattr(publisher, "transport", httpx.MockTransport(refuse))
    monkeypatch.setattr(seed, "transport", httpx.MockTransport(refuse))


@pytest.fixture
def sign_in(auth_keys):
    """`sign_in(org="Name")` → a TestClient for a new person who is Admin of a new Organization;
    `sign_in(org=None)` → signed in, no Organization yet."""
    import uuid

    from fastapi.testclient import TestClient

    from app import orgs
    from app.main import app

    def make(org: str | None = "Test Org", user_id=None) -> TestClient:
        user_id = user_id or uuid.uuid4()
        email = f"{user_id.hex[:8]}@example.com"
        org_id = orgs.create_org(user_id, org, email).org_id if org else None
        token = auth_keys.token(user_id, email=email)
        c = TestClient(app, headers={"Authorization": f"Bearer {token}"})
        c.org_id, c.user_id = org_id, user_id  # for tests that also use the store directly
        return c
    return make


@pytest.fixture
def org():
    """A fresh Organization's id, for tests that call the store directly."""
    import uuid

    from app import orgs
    return orgs.create_org(uuid.uuid4(), "Store test").org_id


@pytest.fixture
def client(sign_in):
    """A TestClient signed in as the Admin of a fresh Organization."""
    return sign_in()


def all_api_routes(routes=None):
    """Every APIRoute of the app, including those added with `include_router` (FastAPI 0.14x keeps them inside
    an `_IncludedRouter`, so `app.routes` alone misses /me, /orgs and the Member routes)."""
    from fastapi.routing import APIRoute

    if routes is None:
        from app.main import app
        routes = app.routes
    for r in routes:
        if isinstance(r, APIRoute):
            yield r
        elif hasattr(r, "original_router"):
            yield from all_api_routes(r.original_router.routes)
