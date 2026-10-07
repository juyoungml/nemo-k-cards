import os
import tempfile

# Before app.config is imported: keep test jobs/slides out of backend/.data and never call real agents.
os.environ["OUTPUT_DIR"] = os.path.join(tempfile.mkdtemp(prefix="wok-test-"), "out")
os.environ["DEMO_MODE"] = "fixture"

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
