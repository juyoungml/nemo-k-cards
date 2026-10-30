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
> touches them, and `docker compose config` is validated there. The stack has **not yet run against Supabase**.
> Run the checklist below on the first real host (#61/#63) before anyone relies on it.

## Where things come from

- **Images**: CI builds them; it does not push them yet. Pushing to GHCR is #59, and the deploy button that moves the
  `staging` tag is #60. Until then `docker compose ... up --build` builds on the server. Nobody builds images on a
  laptop.
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
  Admin image per Supabase project** (staging, later production). In #59, CI passes them from that GitHub
  environment's variables. Pull request builds use placeholders. Both values are public by design; never pass a
  secret or service key.

The API image takes no build args: one image serves every environment.

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
