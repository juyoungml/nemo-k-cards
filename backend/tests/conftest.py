import os
import secrets
import tempfile
from pathlib import Path

import psycopg
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
except Exception:
    _drop_test_db()
    raise

# Tests must never reach real accounts, even on a machine whose backend/.env holds live credentials:
# explicit env vars win over .env in pydantic-settings, so blank every outward-facing secret and mode.
for key, value in {
    "PUBLISH_MODE": "mock", "IMAGE_HOST": "local", "AGENT_RUNNER": "local", "ADMIN_TOKEN": "",
    "IG_USER_ID": "", "IG_ACCESS_TOKEN": "", "PUBLIC_ASSET_BASE_URL": "",
    "SUPABASE_URL": "", "SUPABASE_SERVICE_KEY": "",
    "ANTHROPIC_API_KEY": "", "CLAUDE_CODE_OAUTH_TOKEN": "",
}.items():
    os.environ[key] = value


def pytest_sessionstart(session):
    """Fail fast if isolation is broken: a test run must never see live credentials or publish for real."""
    from app.config import settings
    assert settings.publish_mode == "mock" and not settings.ig_access_token and not settings.supabase_service_key, \
        "tests are not isolated from live credentials"
    assert "wok-test-" in str(settings.output_dir), "tests would write into the real data dir"
    assert conninfo_to_dict(settings.database_url).get("dbname") == TEST_DB, \
        "tests would write into the real database"


def pytest_sessionfinish(session, exitstatus):
    from app import store
    store.close()
    _drop_test_db()
