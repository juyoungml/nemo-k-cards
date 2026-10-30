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

    # Durable Jobs (#17, ADR 0002): DBOS keeps its checkpoints in the `dbos` schema of this same database.
    # Empty = DATABASE_URL. Set it only to give DBOS a different connection (e.g. Supabase's session pooler).
    dbos_database_url: str = ""
    regenerations_per_deck: int = 10  # fair-use cap on "regenerate one slide" (#23): agent rewrites per Deck
    jobs_per_org: int = 2         # Jobs of one Organization running at once; the rest wait as QUEUED
    jobs_per_worker: int = 4      # Jobs one API/worker process runs at once (Chromium, agent CLIs)
    # Bump when the pipeline's stage list or order changes: a Job started under another version is not resumed
    # (its checkpoints no longer line up with the code) and is marked failed at the next start instead.
    # 2: the Safeguard stage runs after review (#43).
    workflow_version: str = "2"

    # Agent runtime
    agent_model: str = ""                 # e.g. "sonnet" to cut cost; empty = CLI default
    agent_timeout_s: int = 300
    openshell_providers: list[str] = ["claude-code"]
    publish_mode: Literal["mock", "dryrun", "graph"] = "mock"
    theme: Literal["bold", "clean", "pop"] = "clean"
    # Admin origins allowed to call this API from the browser (e.g. a tunnel URL for a shared test).
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]
    # Where the Admin runs: invitation links point at ADMIN_URL/invite. Must be on the Supabase project's
    # Auth redirect allow list (supabase/config.toml locally, the dashboard on Cloud).
    admin_url: str = "http://localhost:3000"

    # Publishing (#21) posts to the Organization's own connected Instagram account with its token from Vault; the
    # old env account (IG_USER_ID / IG_ACCESS_TOKEN) is gone and those variables are ignored.
    # Post workflows one process drives at once (a Post waiting for the reconciler holds one while it sleeps).
    posts_per_worker: int = 16
    # IMAGE_HOST=local: this API's public URL, where Instagram fetches the signed slide links (e.g. a tunnel).
    public_asset_base_url: str = ""

    # Connecting an Organization's own Instagram account (#15): "Instagram API with Instagram Login" on our Meta
    # app (App Dashboard → Instagram → API setup with Instagram login). Host-only secrets. Connecting is off
    # (and the Admin says so) until all three are set. The redirect URI is this API's public
    # `/instagram-accounts/callback` URL and must be listed exactly as "Valid OAuth Redirect URIs" on the app.
    instagram_app_id: str = ""
    instagram_app_secret: str = ""
    instagram_redirect_uri: str = ""
    instagram_api_version: str = "v24.0"
    # Instagram accounts an Organization may connect when `orgs.instagram_account_limit` is unset (billing, #29,
    # will set it from the plan).
    instagram_accounts_per_org: int = 1
    # Alert emails (app/notify.py): SMTP_URL like smtps://user:password@smtp.example.com:465 (or smtp://…:587, which
    # uses STARTTLS). Empty: alerts are only logged. Host-only secret.
    smtp_url: str = ""
    mail_from: str = "What's On Korea <alerts@whatsonkorea.app>"

    # Supabase project: sign-in (every route except /health needs its access token) and, with
    # IMAGE_HOST=supabase, slide hosting. Local: `supabase start` → http://127.0.0.1:54321. Empty = all 401.
    supabase_url: str = ""
    # Only for projects still signing tokens with the legacy shared secret (HS256). New projects and the
    # local CLI sign with asymmetric keys published as JWKS at SUPABASE_URL/auth/v1/.well-known/jwks.json.
    supabase_jwt_secret: str = ""

    # Slide hosting for Instagram (host-only secrets). Slides are private (#18): the Admin and Instagram only
    # get signed links that expire. supabase = private bucket + Storage signed URLs; local = this API's
    # /assets route with HMAC-signed URLs (PUBLIC_ASSET_BASE_URL must reach it, e.g. a tunnel).
    image_host: Literal["supabase", "local"] = "local"
    supabase_service_key: str = ""
    supabase_bucket: str = "slides"
    # How long Instagram's links work. Instagram copies each image while it builds the container, so minutes
    # are enough; every Approve uploads and signs again, so a retry never needs an old link.
    publish_link_ttl_s: int = 15 * 60
    # Key for signed /assets links. Empty: derived from SUPABASE_SERVICE_KEY, else random per process.
    asset_signing_key: str = ""

    # Source material files (#20), private to their Organization under `<org_id>/<material_id>/`.
    # local = MATERIAL_DIR on this host (empty: `materials/` next to OUTPUT_DIR); supabase = the private Storage
    # bucket MATERIAL_BUCKET via SUPABASE_SERVICE_KEY (needed once workers run on more than one machine).
    material_store: Literal["local", "supabase"] = "local"
    material_dir: str = ""
    material_bucket: str = "materials"

    @property
    def materials_root(self) -> Path:
        return Path(self.material_dir) if self.material_dir else self.output_dir.parent / "materials"


settings = Settings()
