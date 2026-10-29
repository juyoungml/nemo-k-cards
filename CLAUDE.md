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

Postgres holds Jobs, Brainstorms and policy events. Locally it comes from the [Supabase CLI](https://supabase.com/docs/guides/local-development/cli/getting-started) (Docker), so dev matches staging and production (both Supabase Cloud). The schema is plain SQL in `supabase/migrations/` (timestamped); the same files go to local, staging and production. Tests create and drop their own `wok_test_*` database on the local server and apply those files themselves.

```bash
supabase db start          # Postgres only, migrations applied (lighter); `supabase start` adds Auth, Storage, Vault
supabase migration new <name>   # new migration in supabase/migrations/
supabase migration up      # apply pending migrations locally
supabase stop              # data is kept in a Docker volume, outside the repo
```

Staging is one shared Supabase Cloud project, updated only from `main` (CI will apply migrations); nobody edits it by hand. Production is a separate project. The old SQLite file `backend/.data/app.db` is not imported.

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
| `DATABASE_URL` | `postgresql://…` | default is the local Supabase database (`127.0.0.1:54322/postgres`). Tests never use it: they create a throwaway database on `TEST_DATABASE_URL`'s server (default: the same local server) |
| `SUPABASE_URL` | e.g. `http://127.0.0.1:54321` | Supabase project for sign-in (and slide hosting with `IMAGE_HOST=supabase`). Every route except `/health` needs a session from it; empty = every route 401 |
| `SUPABASE_JWT_SECRET` | secret | only for projects still signing with the legacy HS256 secret. The local CLI and new cloud projects use asymmetric keys from `SUPABASE_URL/auth/v1/.well-known/jwks.json` (cached 10 min) |

Always use `--reload`. Without it, a long-running API keeps old Python while Jinja templates load fresh from disk, and renders fail (`'slide_class' is undefined`).

**Before restarting the API**, check that nothing is in flight. Restarting kills running agents and publishes:
```bash
curl -s -H "Authorization: Bearer $TOKEN" localhost:8000/jobs | python3 -c "import json,sys;print([(j['id'],j['status']) for j in json.load(sys.stdin) if j['status'] in ('QUEUED','RESEARCHING','VERIFYING','WRITING','RENDERING','QA','REVIEWING','PUBLISHING')])"
```

## Run the Admin UI (port 3000)

```bash
cd apps/web && pnpm install
NEXT_PUBLIC_API_URL=http://localhost:8000 pnpm dev --port 3000
```

Without `NEXT_PUBLIC_API_URL` the Admin silently uses its in-memory mock API (a yellow "Mock API" box appears in the sidebar) and needs no sign-in. The value is read at startup, so restart `pnpm dev` after changing it. CORS allows `localhost:3000`.

## Sign-in (Supabase Auth)

With the real API, the Admin requires sign-in and the API rejects every route except `/health` without a valid Supabase session (401). Signing up creates an Organization and makes you its Admin (`POST /orgs`, called by the Admin; `GET /me` shows user, Organization and Role). Local setup:

```bash
supabase start -x realtime,storage-api,imgproxy,postgres-meta,studio,edge-runtime,logflare,vector,supavisor
supabase status            # API URL (54321), Publishable key, Mailpit URL (54324)
cd apps/web && NEXT_PUBLIC_API_URL=http://localhost:8000 NEXT_PUBLIC_SUPABASE_URL=http://127.0.0.1:54321 \
  NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=<sb_publishable_…> pnpm dev --port 3000
```

Backend: `SUPABASE_URL=http://127.0.0.1:54321` in `backend/.env`. Then open http://localhost:3000/sign-up. Local sign-up needs no email confirmation (`enable_confirmations = false` in `supabase/config.toml`); password-reset and confirmation emails land in Mailpit at http://127.0.0.1:54324, and their links must be opened in the browser that asked for them. A token for curl:

```bash
TOKEN=$(curl -s "http://127.0.0.1:54321/auth/v1/token?grant_type=password" -H "apikey: <publishable key>" \
  -H 'content-type: application/json' -d '{"email":"you@example.com","password":"…"}' | python3 -c "import json,sys;print(json.load(sys.stdin)['access_token'])")
curl -H "Authorization: Bearer $TOKEN" localhost:8000/me
```

`/assets/*` (rendered slides) stays public because Instagram fetches it anonymously. Tests sign their own tokens with a throwaway key (`backend/tests/conftest.py`), so they need no Supabase Auth.

## Run the agents

The API runs them. You normally start a job, not an agent:

```bash
curl -s -X POST localhost:8000/jobs -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' -d '{"prompt":"Seongsu pop-ups this weekend"}'
curl -N -H "Authorization: Bearer $TOKEN" localhost:8000/jobs/<id>/events   # SSE: full Job snapshots ($TOKEN: see Sign-in)
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

Safe to run next to a live server: tests use a temp data dir, a throwaway Postgres database, fixture mode and blanked credentials (`backend/tests/conftest.py` refuses to start otherwise), run in-process with no ports, and don't trigger `--reload`. Don't run `pnpm build` in `apps/web` while `pnpm dev` is running (they share `.next/`); CI builds it.

```bash
cd backend && uv run pytest -q                       # unit + API tests
cd backend && uv run python -m app.renderer.preview  # renders demo deck → backend/.data/preview, reports overflow
cd apps/web && npx tsc --noEmit && pnpm lint
```

**Golden set** (HARDENING AGT-3): fixed requests in `backend/golden/cases.json` (bump `version` when a case changes), checked for schema, slide structure, ≤ 5 hashtags, valid links, no invented facts, relevant photos and the case's expectations. Checks marked `[llm]` read the recorded reviewer verdict; everything else is a deterministic rule (`backend/app/golden/checks.py`).

```bash
cd backend && uv run python -m app.golden            # replay recordings: free, deterministic, what CI runs
cd backend && uv run python -m app.golden --live --case seongsu-popups --record   # real agents, costs tokens
```

Run it after editing `agent/**`, the orchestrator's stage tasks, stage output schemas or `AGENT_MODEL`. Replay re-checks recorded outputs, so it marks a recording STALE when any of those changed (a hash of all of them) or when it was made on another model than the `model` pinned in `cases.json` (override with `--model`). Re-record stale cases with `--live --record` (one case ≈ three `claude -p` stages) and commit the JSON; a recording stores the model id and `claude` CLI version the run actually used. Recordings built from demo data (`demo-scenario`, `synthetic`) report as FIXTURE and never count as a pass.

Live runs never publish (`PUBLISH_MODE=mock`, publishing credentials blanked) and never write the Admin's data: slides go under `--out` (default `backend/.data/golden`). With the SQLite store its database sits there too; with the Postgres store live mode uses `GOLDEN_DATABASE_URL` (its database name must contain `golden` or `test`) or creates a scratch database on `TEST_DATABASE_URL`'s server, applies `supabase/migrations` and drops it at exit, and refuses to start otherwise. CI (`.github/workflows/golden-set.yml`) runs replay; live CI needs an `ANTHROPIC_API_KEY` secret (steps in that file).

## Rules

- Never set `PUBLISH_MODE=graph` or approve a job unless a human explicitly asked. It posts publicly.
- Secrets (`IG_ACCESS_TOKEN`, `SUPABASE_SERVICE_KEY`, Anthropic keys) stay in `backend/.env` on the host and never go into the sandbox, the agent workspace, logs or commits.
- Data and caches don't live in the repo (`/data` is a gitignored symlink).
- Organization data goes through `app/store.py` with the caller's Organization (`ctx.org_id`): `store.tenant(org_id)` runs as the `wok_api` role so row-level security applies. `store.system()` bypasses it and is only for work that spans Organizations (creating one, restart recovery). New Organization-owned tables need `org_id`, RLS and a `wok_api` policy (see `supabase/migrations/20261029140000_org_isolation.sql`).
- Card or UI changes must follow `DESIGN.md`. Re-run the renderer preview after template edits.

## Agent skills

### Issue tracker

Issues live in GitHub Issues for juyoungml/nemo-k-cards (`gh` CLI). See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary: needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` and `docs/adr/` at the repo root, created as terms and decisions are settled. See `docs/agents/domain.md`.
