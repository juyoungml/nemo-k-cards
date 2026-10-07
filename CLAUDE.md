# nemo-k-cards · What's On Korea (developer guide)

> Pipeline stage agents (`claude -p` running in `agent/`): this file is for developers. Your rules are in `agent/CLAUDE.md`. Ignore everything below.

A publishing agent finds what's on in Korea, then researches, verifies, writes and renders Instagram card news (1080×1350 carousels) for tourists. A human approves each post in the Admin before it goes to @whatsonkorea.

- Spec: `docs/BACKEND.md` (frozen v1.1) and `docs/SPEC.md`. Design system: `DESIGN.md`. Read it before touching cards or UI.
- Layout: `backend/` is FastAPI, the trusted host that holds the IG token and does all publishing. `apps/web/` is the Next.js Admin. `agent/` is the Claude Code workspace mounted into OpenShell sandboxes. `policies/` holds OpenShell and content policies. `demo/scenarios/` holds fixtures.

## Prerequisites

`uv`, Node 22 + `pnpm` (or `npx`), and the `claude` CLI logged in (`claude` on PATH) for live agents. Chromium for the renderer:

```bash
cd backend && uv sync && uv run playwright install chromium
```

`backend/.env` is gitignored. Copy `backend/.env.example` and ask the team for real values. Never commit or print secrets.

## Run the API (port 8000)

```bash
cd backend
# Safe default: fixture data, no agents, no network publishing
uv run uvicorn app.main:app --port 8000 --reload --reload-dir app
# Live agents (local claude -p), publish dry run (uploads + IG containers, never posts)
DEMO_MODE=live PUBLISH_MODE=dryrun uv run uvicorn app.main:app --port 8000 --reload --reload-dir app
```

Check it with `curl localhost:8000/health`, which returns `demo_mode`, `agent_runner` and `publish_mode`.

| Env | Values | Meaning |
|---|---|---|
| `DEMO_MODE` | `fixture` \| `live` | fixture replays `demo/scenarios/<name>`; live runs real agents |
| `AGENT_RUNNER` | `local` \| `openshell` | local = `claude -p` on the host (dev, no sandbox); openshell = per-stage sandbox (submission) |
| `PUBLISH_MODE` | `mock` \| `dryrun` \| `graph` | **`graph` posts to the real @whatsonkorea account.** Use `dryrun` unless a human asked to go live |
| `IMAGE_HOST` | `local` \| `supabase` | Instagram needs public URLs; supabase uploads slides to the public `slides` bucket |
| `AGENT_MODEL` | e.g. `sonnet` | optional model override for agent stages |

Always use `--reload`. Without it, a long-running API keeps old Python while Jinja templates load fresh from disk, and renders fail (`'slide_class' is undefined`).

**Before restarting the API**, check that nothing is in flight. Restarting kills running agents and publishes:
```bash
curl -s localhost:8000/jobs | python3 -c "import json,sys;print([(j['id'],j['status']) for j in json.load(sys.stdin) if j['status'] in ('QUEUED','RESEARCHING','VERIFYING','WRITING','RENDERING','QA','REVIEWING','PUBLISHING')])"
```

## Run the Admin UI (port 3000)

```bash
cd apps/web && pnpm install
NEXT_PUBLIC_API_URL=http://localhost:8000 pnpm dev --port 3000
```

Without `NEXT_PUBLIC_API_URL` the Admin silently uses its in-memory mock API (a yellow "Mock API" box appears in the sidebar). The value is read at startup, so restart `pnpm dev` after changing it. CORS allows `localhost:3000`.

## Run the agents

The API runs them. You normally start a job, not an agent:

```bash
curl -s -X POST localhost:8000/jobs -H 'content-type: application/json' -d '{"prompt":"Seongsu pop-ups this weekend"}'
curl -N localhost:8000/jobs/<id>/events        # SSE: full Job snapshots
```

Pipeline: research → verify (host link check) → copy → render (Jinja2 + Playwright) → visual QA → review → `READY_FOR_REVIEW` → human approves in the Admin → publish. Nothing is auto-rejected; blocking issues are shown and the operator decides.

To run one stage by hand (what `LocalClaudeRunner` does, cwd `agent/`):

```bash
cd agent
claude -p "<task text + JSON input>" --agent researcher --output-format json \
  --json-schema '<schema>' --allowedTools WebSearch WebFetch Read
# result is in .structured_output
```

Agents: `researcher` (WebSearch/WebFetch/Read), `planner`, `copywriter`, `reviewer` (Read only), defined in `agent/.claude/agents/`. Agents never publish. Only the host does.

**OpenShell (submission path):** one-time `scripts/openshell_setup.sh` (builds the image, imports the provider, registers the key), then `AGENT_RUNNER=openshell`. Policy: `policies/openshell/agent-policy.yaml`. Probe it with `scripts/openshell_probe.sh`.

## Checks

```bash
cd backend && uv run pytest -q                       # unit + API tests
cd backend && uv run python -m app.renderer.preview  # renders demo deck → backend/.data/preview, reports overflow
cd apps/web && npx tsc --noEmit && pnpm lint
```

## Rules

- Never set `PUBLISH_MODE=graph` or approve a job unless a human explicitly asked. It posts publicly.
- Secrets (`IG_ACCESS_TOKEN`, `SUPABASE_SERVICE_KEY`, Anthropic keys) stay in `backend/.env` on the host and never go into the sandbox, the agent workspace, logs or commits.
- Data and caches don't live in the repo (`/data` is a gitignored symlink).
- Card or UI changes must follow `DESIGN.md`. Re-run the renderer preview after template edits.
