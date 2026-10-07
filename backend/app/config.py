from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    demo_mode: Literal["fixture", "live"] = "fixture"
    agent_runner: Literal["local", "openshell"] = "local"
    openshell_policy: Path = REPO_ROOT / "policies/openshell/agent-policy.yaml"
    openshell_image: str = "whatsonkorea-agent:latest"
    agent_dir: Path = REPO_ROOT / "agent"
    scenarios_dir: Path = REPO_ROOT / "demo/scenarios"
    output_dir: Path = REPO_ROOT / "backend/.data/out"

    # Agent runtime
    agent_model: str = ""                 # e.g. "sonnet" to cut cost; empty = CLI default
    agent_timeout_s: int = 300
    openshell_providers: list[str] = ["claude-code"]
    publish_mode: Literal["mock", "dryrun", "graph"] = "mock"
    theme: Literal["bold", "clean", "pop"] = "clean"
    # Admin origins allowed to call this API from the browser (e.g. a tunnel URL for a shared test).
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]
    # When set, every API call except /health and /assets needs `Authorization: Bearer <ADMIN_TOKEN>`
    # (SSE: `?token=`). Set it whenever the API is reachable from outside this machine.
    admin_token: str = ""

    # Instagram Graph API — host-only secrets, never forwarded to the sandbox.
    ig_user_id: str = ""
    ig_access_token: str = ""
    public_asset_base_url: str = ""

    # Public image hosting for Instagram (host-only secrets)
    image_host: Literal["supabase", "local"] = "local"
    supabase_url: str = ""
    supabase_service_key: str = ""
    supabase_bucket: str = "slides"


settings = Settings()
