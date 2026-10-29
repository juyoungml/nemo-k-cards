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
    # Postgres for Jobs, Brainstorms and policy events. Default: the local `supabase start` database.
    # Staging and production are Supabase Cloud connection strings (host-only secrets).
    database_url: str = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"

    # Agent runtime
    agent_model: str = ""                 # e.g. "sonnet" to cut cost; empty = CLI default
    agent_timeout_s: int = 300
    openshell_providers: list[str] = ["claude-code"]
    publish_mode: Literal["mock", "dryrun", "graph"] = "mock"
    theme: Literal["bold", "clean", "pop"] = "clean"
    # Admin origins allowed to call this API from the browser (e.g. a tunnel URL for a shared test).
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    # Instagram Graph API — host-only secrets, never forwarded to the sandbox.
    ig_user_id: str = ""
    ig_access_token: str = ""
    public_asset_base_url: str = ""

    # Supabase project: sign-in (every route except /health needs its access token) and, with
    # IMAGE_HOST=supabase, slide hosting. Local: `supabase start` → http://127.0.0.1:54321. Empty = all 401.
    supabase_url: str = ""
    # Only for projects still signing tokens with the legacy shared secret (HS256). New projects and the
    # local CLI sign with asymmetric keys published as JWKS at SUPABASE_URL/auth/v1/.well-known/jwks.json.
    supabase_jwt_secret: str = ""

    # Public image hosting for Instagram (host-only secrets)
    image_host: Literal["supabase", "local"] = "local"
    supabase_service_key: str = ""
    supabase_bucket: str = "slides"


settings = Settings()
