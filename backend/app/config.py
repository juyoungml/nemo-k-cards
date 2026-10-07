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

    # Instagram Graph API — host-only secrets, never forwarded to the sandbox.
    ig_user_id: str = ""
    ig_access_token: str = ""
    public_asset_base_url: str = ""


settings = Settings()
