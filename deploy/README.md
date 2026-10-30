# deploy/: the container stack

The API and the Admin as containers behind one HTTPS front, on any server with Docker. Nothing in this folder is
specific to one server: every server-specific value lives in one env file kept outside the repo.

| File | What it is |
|---|---|
| `../backend/Dockerfile` | API image: FastAPI + DBOS, the Playwright renderer (headless Chromium) and the `claude` CLI. Built from the repo root. |
| `../apps/web/Dockerfile` | Admin image: the Next.js standalone server, non-root, no secrets. Built from `apps/web`. |
| `compose.yml` | `api`, `admin`, `caddy` and, with `--profile tunnel`, `cloudflared`. |
| `Caddyfile` | The only public entry: `/backend/*` goes to the API (prefix stripped), everything else to the Admin. |
| `env.example` | Template for the stack env file: names and comments, never values. |

> **Status:** the images are built in CI (`.github/workflows/container-images.yml`) on every pull request that
> touches them, and `docker compose config` is validated there. Every commit on main pushes private images to GHCR
> after the backend and web checks pass. The stack has **not yet run against Supabase**.
> Run the checklist below on the first real host (#61/#63) before anyone relies on it.

## Where things come from

- **Images**: CI builds them and pushes every commit of main to GHCR as private packages (see
  [Images on GHCR](#images-on-ghcr)). The deploy button that moves the `staging` tag is #60. Without a registry
  login, `docker compose ... up --build` builds on the server instead. Nobody builds images on a laptop.
- **Secrets and server settings**: the stack env file (template `env.example`), mode 600, outside the repo, for
  example `/srv/nemo/stack.env`. Only the `api` container reads it. Images contain no `.env` and no Claude login.
  CI fails the build if one turns up.
- **Database**: Supabase Postgres. Apply `supabase/migrations` with the Supabase CLI (`supabase db push`) before
  the first start and before each deploy that adds a migration. The API does not migrate the app schema itself;
  DBOS creates its own tables in the `dbos` schema.

## The Admin's public settings (`NEXT_PUBLIC_*`)

Next inlines `NEXT_PUBLIC_*` into the browser bundle at **build** time, so they are build args, not runtime env:

- `NEXT_PUBLIC_API_URL` is always `/backend`, the image's default. The browser calls the API on the Admin's own
  origin and Caddy routes it. No domain is baked into the image, one image works for any host name, and there is
  no CORS (`CORS_ORIGINS` stays unset).
- `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` name the Supabase project, so there is **one
  Admin image per Supabase project** (staging, later production). On main, CI builds it with the repository
  variables `STAGING_NEXT_PUBLIC_SUPABASE_URL` / `STAGING_NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` and tags it
  `staging-sha-<sha>`. Pull request builds use placeholders. Both values are public by design; CI refuses a key that
  isn't `sb_publishable_…`, so a secret key can't be baked in.

The API image takes no build args: one image serves every environment.

Why repository variables and not the `staging` environment's: a job that names an environment waits for that
environment's required reviewers. Reading staging's settings there would put an approval in front of every merge.
A production Admin later gets its own pair (`PRODUCTION_NEXT_PUBLIC_…`) and its own `production-sha-<sha>` tags.

## Images on GHCR

| Image | Tags pushed for each commit on main | Moving tag (#60) |
|---|---|---|
| `ghcr.io/juyoungml/nemo-k-cards-api` | `sha-<full sha>`, `sha-<7>` | `staging` |
| `ghcr.io/juyoungml/nemo-k-cards-admin` | `staging-sha-<full sha>`, `staging-sha-<7>` | `staging` |

- **When**: `container-images.yml` on every push to main. It runs `backend-checks.yml` and `web-checks.yml` first
  (as reusable workflows) and pushes only if both pass. There is no `latest`. Pull requests build and smoke-test
  the same Dockerfiles but push nothing, get a read-only token, and never see a secret. No workflow uses
  `pull_request_target`.
- **Labels**: each image carries `org.opencontainers.image.revision=<sha>` and `.source` (the repo), so a running
  container tells you its commit.
- **Cache**: BuildKit layers are kept in the GitHub Actions cache (`type=gha`). main's cache is readable from every
  branch, so PR builds start warm.
- **Private**: the repo is public, the packages are not. New GHCR packages start private, and CI fails the push job
  if one turns public (anonymous pull allowed). Making a package public can't be undone, so don't. Only the two
  push jobs get `packages: write`.

### A read-only pull token for a server

GHCR accepts only a **classic** personal access token. Fine-grained tokens and deploy keys don't work with it, and
GitHub has no registry deploy tokens.

1. Use an account that can read the repo. A dedicated machine account added as a **read** collaborator is best,
   because a classic token reaches every package that account can see.
2. GitHub → Settings → Developer settings → Personal access tokens (classic) → Generate. Select only
   **`read:packages`** and set an expiry. Name it after the server.
3. On the server, log in once. Docker keeps the login in `~/.docker/config.json` (mode 600):
   `echo "$TOKEN" | docker login ghcr.io -u <account> --password-stdin`. Keep the token in the server's env
   file or password manager, never in the repo, and don't put it on a command line where shell history keeps it.
4. In the stack env file: `API_IMAGE=ghcr.io/juyoungml/nemo-k-cards-api`,
   `ADMIN_IMAGE=ghcr.io/juyoungml/nemo-k-cards-admin`, `IMAGE_TAG=staging` (or a fixed `sha-…` pair to pin).
   Then `docker compose ... pull && docker compose ... up -d` without `--build`. The updater is #61.
5. Revoke the token when the server goes away. A leaked token only reads images, and the images hold no secrets.

### If an image push fails

- `STAGING_NEXT_PUBLIC_…` error: the repository variables are missing or wrong. A repo admin sets them under
  Settings → Secrets and variables → Actions → **Variables**, then re-runs the failed jobs.
- `denied: permission_denied` on push: the package exists but this repository has no write access to it. In the
  package's settings → Manage Actions access, add `juyoungml/nemo-k-cards` with **Write**.
- `… is PUBLIC`: GitHub can't make a public package private again. Tell the team, delete the package (package
  settings → Danger Zone), and re-run the job: the push creates it again, private. The images hold no secrets,
  but they aren't meant to be distributed.

## Run it

```bash
sudo install -m 600 /dev/null /srv/nemo/stack.env    # then fill it from deploy/env.example
export ENV_FILE=/srv/nemo/stack.env
docker compose -f deploy/compose.yml --env-file "$ENV_FILE" up -d --build
docker compose -f deploy/compose.yml --env-file "$ENV_FILE" ps
```

- **Domain + open ports 80/443**: set `SITE_ADDRESS=staging.example.com`. Caddy gets a Let's Encrypt certificate.
- **No open ports**: keep `SITE_ADDRESS=:80`, set `TUNNEL_TOKEN`, point the Cloudflare tunnel's public hostname at
  `http://caddy:80`, and add `--profile tunnel`.
- The API and Admin are also published on `127.0.0.1:18000` / `127.0.0.1:13000` for checks on the host itself.
- `AGENT_RUNNER` is fixed to `local` (`claude -p` inside the API container). OpenShell sandboxes from a container is #58.
- Restarts are safe for data. Jobs and Posts are durable and resume at the next start. A restart still interrupts
  running agent stages, so check `/jobs` first (see the root `CLAUDE.md`).

## First-host smoke checklist (#61/#63)

Run in order on the first real host, with a **staging** Supabase project and `PUBLISH_MODE=dryrun`. Never use `graph`
here. Tick each line in the PR or issue that brings the host up.

**Stack**
- [ ] `supabase db push` against the staging project succeeded; `ENV_FILE` is mode 600 and outside the repo.
- [ ] `docker compose ... config --quiet` passes with the real env file.
- [ ] `docker compose ... up -d` and `ps` shows `api` and `admin` **healthy** (both images have a HEALTHCHECK).
- [ ] `curl -s 127.0.0.1:18000/health` shows `demo_mode`, `agent_runner: local`, `publish_mode: dryrun`.
- [ ] `docker compose ... logs api` shows DBOS launched against Postgres and no tracebacks.
- [ ] `docker compose ... exec api claude --version` works, and `exec api env` shows no unexpected secrets
      (only those in the env file).

**Front**
- [ ] `https://<SITE_ADDRESS>/` serves the Admin with a valid certificate (or through the tunnel).
- [ ] `https://<SITE_ADDRESS>/backend/health` returns the API's health, so the prefix is stripped.
- [ ] The Admin's sidebar shows **no** yellow "Mock API" box, so the image was built with `/backend`.

**Sign-in and data**
- [ ] `<SITE_ADDRESS>` is on the Supabase Auth redirect allow list; `ADMIN_URL` matches.
- [ ] Sign up or sign in; onboarding asks for the Organization profile; the profile saves.
- [ ] Invite a second Member: Supabase's email arrives and its link opens `<SITE_ADDRESS>/invite?token=…`.

**Pipeline**
- [ ] With `DEMO_MODE=fixture`, start a Job: the SSE stream on the Job page updates live through Caddy (not all at
      the end), and slides render (Chromium in the container, Korean text and emoji show, no tofu boxes).
- [ ] With `DEMO_MODE=live` and `ANTHROPIC_API_KEY`, one Job runs research → … → `READY_FOR_REVIEW`.
- [ ] Slide images load in the Admin: signed `/backend/assets/...` links (IMAGE_HOST=local) or signed Supabase links.
- [ ] Approve in **dryrun**: uploads and Instagram containers are created, nothing posts.

**Durability**
- [ ] `docker compose ... restart api` while a fixture Job runs: the Job resumes and finishes after the start.
- [ ] `docker compose ... down && up -d`: Jobs, Posts and rendered slides (`api_data` volume) are all still there.

**Instagram (only if `INSTAGRAM_APP_ID` is set)**
- [ ] `INSTAGRAM_REDIRECT_URI` is `https://<SITE_ADDRESS>/backend/instagram-accounts/callback` on the Meta app, and
      connecting an account completes.
