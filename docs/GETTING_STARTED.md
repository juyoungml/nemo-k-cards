# Getting started

## Local sample workflow

Use Python 3.12 or newer, uv, Node.js 22 and pnpm 10.10.0. The backend lockfile is committed. A Chromium installation is required for rendering.

From the repository root:

```bash
cd backend
uv sync --frozen
uv run playwright install chromium
DEMO_MODE=fixture PUBLISH_MODE=mock uv run uvicorn app.main:app --port 8000 --reload --reload-dir app
```

In another terminal, also starting at the repository root:

```bash
cd apps/web
pnpm install --frozen-lockfile
NEXT_PUBLIC_API_URL=http://localhost:8000 pnpm dev
```

Open http://localhost:3000. New Job starts a request; Review shows the resulting cards, source links and issues. Approval in `PUBLISH_MODE=mock` simulates posting.

Check the backend configuration with:

```bash
curl http://localhost:8000/health
```

The response reports `demo_mode`, `agent_runner` and `publish_mode`. For this walkthrough, expect `fixture`, `local` and `mock`. The runner setting is not invoked for fixture agent outputs.

## Modes

| Setting | Values | Meaning |
|---|---|---|
| `DEMO_MODE` | `fixture`, `live` | Recorded agent outputs or actual agent execution |
| `AGENT_RUNNER` | `local`, `openshell` | Host Claude CLI or stage-specific OpenShell sandbox |
| `PUBLISH_MODE` | `mock`, `dryrun`, `graph` | Simulated post; remote preparation without final publication; real post |
| `IMAGE_HOST` | `local`, `supabase` | Public backend `/assets` URLs or Supabase Storage |
| `NEXT_PUBLIC_API_URL` | Backend URL | Compile/start-time Admin connection; omission enables an independent UI mock |

## Live agents

Install and authenticate the Claude Code CLI before using the local runner. Copy `backend/.env.example` to `backend/.env` if it does not already exist, then set your own values. The file is ignored by Git.

```bash
cd backend
DEMO_MODE=live AGENT_RUNNER=local PUBLISH_MODE=mock uv run uvicorn app.main:app --port 8000 --reload --reload-dir app
```

This uses a real model but does not publish to Instagram. Model usage may incur provider charges. The local runner is for development and has no OpenShell isolation.

## OpenShell

Follow NVIDIA's [installation](https://docs.nvidia.com/openshell/latest/about/installation/) and [support matrix](https://docs.nvidia.com/openshell/latest/about/support-matrix). The project's probe fixture was captured with OpenShell 0.1.2. Recheck CLI and policy compatibility when upgrading.

The setup script builds the image and registers a `claude-code` provider using `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN` from your environment or gitignored backend `.env`.

```bash
# Repository root; OpenShell gateway and Docker must already be configured.
bash scripts/openshell_setup.sh

cd backend
DEMO_MODE=live AGENT_RUNNER=openshell PUBLISH_MODE=mock uv run uvicorn app.main:app --port 8000 --reload --reload-dir app
```

The runner creates a sandbox, uploads inputs, executes Claude Code, collects policy logs and deletes the sandbox. See [the implementation](../backend/app/pipeline/agent_runner.py), [policy](../policies/openshell/agent-policy.yaml) and [provider profile](../policies/openshell/providers/claude-code.yaml).

The backend spec records a MicroVM configuration for the team's older Brev host. Do not assume that any Docker or host kernel version can apply the policy. Verify the driver and Landlock requirements on the deployment host.

To reproduce policy checks in your own configured sandbox:

```bash
bash scripts/openshell_probe.sh
```

This script creates and deletes a sandbox and makes network probe requests, including write-method requests that the policy is expected to deny. Do not weaken the policy or inject Instagram credentials for this test. A denied read can also be an executable-scope restriction: the policy allows Claude Code to read event pages but does not grant every such host to curl.

## Publishing to your own Instagram account

The backend supports `PUBLISH_MODE=graph` using the Instagram Graph API. Configure an eligible Instagram account and its current API permissions, then set host-only `IG_USER_ID` and `IG_ACCESS_TOKEN`. Meta setup is account-dependent; follow the [official Instagram Platform documentation](https://developers.facebook.com/docs/instagram-platform/).

Images must be public JPEG URLs so Instagram can fetch them. Configure either:

- `IMAGE_HOST=local` and a reachable `PUBLIC_ASSET_BASE_URL`; or
- `IMAGE_HOST=supabase`, `SUPABASE_URL` and the host-only `SUPABASE_SERVICE_KEY`.

Use `mock` until local checks pass. `dryrun` creates remote image containers and needs credentials but stops before final posting. `graph` publishes publicly after approval. In the current app, editorial blocking issues remain advisory; the operator must read them. Credential-like output is a hard stop.

## Hosting the Admin

The Admin can run on Railway while FastAPI runs on Brev or another suitable host. Set `NEXT_PUBLIC_API_URL` before building the Admin. Configure the backend's `CORS_ORIGINS` as a JSON array of exact allowed origins.

Set a strong `ADMIN_TOKEN` when the backend is externally reachable. Do not publish that value in a README, QR image, screenshot or demo URL. The Admin access gate stores the entered code in the browser. It is a shared access gate, not a multi-user authorization system. In the current implementation, SSE URLs carry a token query parameter; consider URL logging when choosing your deployment setup.

The [Railway configuration](../apps/web/railway.json) is in the web app. Temporary tunnel addresses used at the hackathon are intentionally not permanent project links. The stable [demo information page](https://juyoung.site/nemo-k-cards/try/) should explain which experience is available.

## Common setup issues

| Symptom | Check |
|---|---|
| Yellow Mock API box | Set `NEXT_PUBLIC_API_URL`; restart the dev server or rebuild the deployment. |
| Admin cannot reach the API | Backend URL, `/health`, CORS origin and access code. |
| Chromium executable missing | Run `uv run playwright install chromium` from `backend/`. |
| Provider or sandbox fails | OpenShell gateway status, image, provider registration and compatible driver. |
| Instagram cannot fetch images | Public JPEG URLs reachable without credentials. |
| Old template/schema errors | Start Uvicorn with `--reload`; avoid restarting while a job is running. |

## Validation

```bash
cd backend
uv run pytest -q
uv run python -m app.renderer.preview
```

Tests use fixture data and temporary output directories. The renderer preview writes into `backend/.data/preview`. For frontend work, use `pnpm exec tsc --noEmit` and `pnpm lint` from `apps/web`.
