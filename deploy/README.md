# deploy/: the container stack

The API and the Admin as containers behind one HTTPS front, on any Linux server with Docker. Nothing in this folder is
specific to one server: every server-specific value lives in one env file kept outside the repo.

| File | What it is |
|---|---|
| `../backend/Dockerfile` | API image: FastAPI + DBOS, the Playwright renderer (headless Chromium) and the `claude` CLI. Built from the repo root. |
| `../apps/web/Dockerfile` | Admin image: the Next.js standalone server, non-root, no secrets. Built from `apps/web`. |
| `compose.yml` | `api`, `admin`, `caddy` and, with `--profile tunnel`, `cloudflared` (or `quicktunnel` with `--profile quicktunnel`). |
| `compose.server.yml` | Server override: never build, rotate container logs. Used by `wok`. |
| `Caddyfile` | The only public entry: `/backend/*` goes to the API (prefix stripped), everything else to the Admin. |
| `env.example` | Template for the stack env file: names and comments, never values. |
| `install.sh` | Bootstraps a host: Docker + Compose, service user, `/opt/wok`, registry login, updater, start. Idempotent. |
| `wok` | The host's command (`/usr/local/bin/wok`): `status`, `pause`, `resume`, `update-now`, `logs`, `rollback`, … Pulls the CI images, or builds them on the host in [build mode](#build-mode-no-registry-yet). |
| `systemd/` | `wok-update.timer` runs `wok update` every 2 minutes. |
| `tests/wok_test.sh` | Tests the updater's logic (update, rollback, skip, pause) against a fake `docker`. |

> **Status:** the images are built in CI (`.github/workflows/container-images.yml`) and `docker compose config` is
> validated there. `install.sh` and `wok` are shellchecked, `install.sh --dry-run` and `tests/wok_test.sh` pass, but
> **neither has run on a real host yet**. The first run is #63 (Brev). Run the smoke checklist below there.

## Where things come from

- **Images**: CI builds them and pushes them to private GHCR packages tagged by commit (#59):
  `ghcr.io/juyoungml/nemo-k-cards-api` and `ghcr.io/juyoungml/nemo-k-cards-admin`. The "Deploy to staging" button
  (#60) runs the migrations and then moves the `staging` tag on both. The server **pulls** that tag. GitHub never
  reaches the server, and nobody builds images on a laptop. (Until the registry settings exist, a host can build
  them itself: [build mode](#build-mode-no-registry-yet).)
- **Secrets and server settings**: one env file, `/opt/wok/.env` (template `env.example`), mode 600, owned by the
  service user `wok`. `wok` passes the API container a copy without the host-only lines (`GHCR_*`, `WOK_*`,
  `TUNNEL_TOKEN`). Images contain no `.env` and no Claude login. CI fails the build if one turns up.
- **Database**: Supabase Postgres. The deploy button applies `supabase/migrations` before it moves the tag. The API
  does not migrate the app schema itself; DBOS creates its own tables in the `dbos` schema.

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

## Bring up a new host

Any Linux machine with systemd. Docker Engine and Compose ≥ 2.24.4 are installed if missing on Debian/Ubuntu; on
other distributions install Docker first. 2 vCPU / 4 GB is the floor (Chromium renders slides, agents run in the API).

1. **Pick the front.** Either a domain whose DNS A record points at the host, with TCP 80 and 443 open, or a
   Cloudflare tunnel with no open ports (see [HTTPS](#https-domain-or-tunnel)).
2. **Make a registry token.** A *classic* GitHub personal access token with only `read:packages`. GHCR doesn't take
   fine-grained tokens. Prefer a bot account, and never use a token with repo scopes.
3. **Get the script** (as a user with sudo):
   ```bash
   curl -fsSLO https://raw.githubusercontent.com/juyoungml/nemo-k-cards/main/deploy/install.sh
   ```
   You can also `git clone` the repo and use `deploy/install.sh`, which installs that checkout's files.
4. **Preview it:** `bash install.sh --dry-run --ref <commit>`. It prints every action and changes nothing.
5. **First run:** `sudo bash install.sh --ref <commit>`. It installs Docker if needed, creates the `wok` user,
   `/opt/wok` and the updater, writes `/opt/wok/.env` from the template, and stops there.
6. **Fill the env file:** `sudo -e /opt/wok/.env`. Set `GHCR_USER`/`GHCR_TOKEN`, `DATABASE_URL` (session pooler,
   port 5432), `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `ADMIN_URL=https://<host name>`, `SITE_ADDRESS` or
   `TUNNEL_TOKEN`, `PUBLIC_ASSET_BASE_URL=https://<host name>/backend`, `ANTHROPIC_API_KEY`, and
   `PUBLISH_MODE=dryrun`. Paste values in the editor, never on a command line (shell history).
7. **Second run:** `sudo bash install.sh --ref <commit>`. It checks the env file, logs in to GHCR as `wok`, enables
   the updater timer, pulls `staging`, and starts the stack. It waits until `api` and `admin` are healthy.
8. **Supabase Auth:** add `https://<host name>` to *Authentication → URL Configuration → Redirect URLs*, and set the
   Site URL if this is the main host.
9. **Check it:** `sudo wok status`, then the [smoke checklist](#first-host-smoke-checklist-6163).

Re-running `install.sh` is safe at any time. It never overwrites the env file, re-applies the files, re-logs in, and
restarts only what changed. While updates are paused it starts the images already on the host and pulls nothing. To update the compose files on a host, re-run it with the newer `--ref`.

## How updates work

`wok-update.timer` runs `wok update` every 2 minutes, so a moved tag is live within about 3 minutes:

1. `docker compose pull api admin` for `$API_IMAGE:$IMAGE_TAG` / `$ADMIN_IMAGE:$IMAGE_TAG` (default `staging`).
   If the API changed, `python -m app.migration_check` runs from the new image first: a database without every
   migration that image ships with means no restart (the old image goes back under the tag, the next check retries).
2. If neither image ID changed, it does nothing. Otherwise it tags the running image `:wok-previous` and restarts
   only the changed service with `docker compose up -d --wait`. That waits for the image's own HEALTHCHECK: the
   API's `/health` and the Admin's `/sign-in`.
3. **Healthy:** it logs `updated api <old commit>-><new commit>` and prunes dangling images older than 7 days.
4. **Not healthy within `WOK_HEALTH_TIMEOUT` (240 s):** it puts the previous image back, restarts it, and records
   the new image as bad, so later checks skip it until the tag moves again. The unit then fails, which shows in
   `wok status` and `wok logs updater`.

**Rollback limits.** It restores images, not the database. The deploy button runs migrations *before* it moves the
tag, so an automatic or manual rollback runs the old code against the new schema. Keep migrations additive
(expand, then contract in a later deploy). The intended rollback is to re-run the deploy button with an older commit
(#60). `sudo wok rollback` is the on-host shortcut to the image before the last update.

Restart policy: every service is `unless-stopped`, and Docker is enabled at boot, so the stack comes back after a
reboot. Container logs are rotated at 5 × 10 MB per container (`compose.server.yml`).

**Why a timer and not Watchtower.** `containrrr/watchtower` (Apache-2.0) is archived (last push December 2025).
It also recreates containers outside Compose, so the env file and override drift, and it has no health-gated
rollback. Its maintained forks (e.g. `nicholas-fedor/watchtower`, Apache-2.0) fix maintenance but not the other two
points, and they need the Docker socket in one more long-running container. Diun (MIT) only notifies. A systemd
timer around `docker compose pull && up --wait` uses only tools that are already on the host. It is about 300 lines
of shellchecked bash with a test (`tests/wok_test.sh`).

## Build mode: no registry yet

The registry path needs one-time repository settings that only a repo admin can make: the Admin's build values as
repository variables (#59) and the protected `staging` environment for the deploy button (#60). Until those exist,
set `WOK_SOURCE=build` in `/opt/wok/.env`. The host then clones the public repo (`WOK_GIT_URL`, default this repo,
no token) into `/opt/wok/src` and builds both images itself:

```bash
# 1. on a laptop with the Supabase CLI linked to the project: migrations first, as the deploy button would
supabase db push --linked
# 2. on the host: build main (or any branch, tag or commit) and deploy it
sudo wok update-now            # WOK_GIT_REF, default main
sudo wok update-now <commit>
```

- The same health check, rollback and skip-bad-image rules apply. A failed build leaves the stack as it is.
- Before anything restarts, `wok` runs `python -m app.migration_check` from the new API image. If the database lacks
  a migration that image ships with, the deploy is refused, the stack is untouched, and it says to run
  `supabase db push`. `sudo wok update-now --skip-migration-check` overrides it (only when you know why).
- The timer does nothing in build mode: a deploy only happens when someone runs `update-now`, so nothing goes out
  without its migrations. `GHCR_*` aren't needed.
- The Admin's `NEXT_PUBLIC_SUPABASE_*` come from the env file as build args (public values).
- A build takes about 10 minutes on 8 vCPU (Chromium and Playwright in the API image) and needs ~4 vCPU / 8 GB;
  Docker's build cache keeps later builds shorter, and cache older than 7 days is pruned after a deploy.

Switching to the registry later: remove `WOK_SOURCE` (or set `registry`), fill `GHCR_*`, re-run `install.sh` (it
logs in), then `sudo wok update-now`. The host's env file is the only thing that changes.

## Pause updates

For the 11/10 finals demo, or any time the running version must not change:

```bash
sudo wok pause finals demo 11/10     # the reason is shown in `wok status`
sudo wok status                      # "Updates: PAUSED (since=… by=… reason=…)"
sudo wok resume                      # follow staging again; next check within ~2 min
sudo wok update-now                  # check right away (refused while paused; --force overrides)
```

While paused, the stack keeps running, the deploy button can still move `staging`, and the server ignores it. Pause
the day before the demo, check `wok status` shows the commit you rehearsed, and resume afterwards.

Other commands: `sudo wok url`, `sudo wok logs [api|admin|caddy|cloudflared|quicktunnel|updater]`, `sudo wok up`, `sudo wok down`,
`sudo wok config`, `sudo wok rollback`. Everything is logged to `/opt/wok/state/history.log`.

## HTTPS: domain or tunnel

- **Domain + open ports** (`SITE_ADDRESS=staging.example.com`): Caddy gets a Let's Encrypt certificate on first
  start. Ports 80 and 443 must reach the host, and nothing else on the host may use them (`install.sh` warns).
  Certificates persist in the `caddy_data` volume.
- **Cloudflare tunnel** (no inbound ports): keep `SITE_ADDRESS=:80` (or unset). In Cloudflare Zero Trust go to
  *Networks → Tunnels* and create a `cloudflared` tunnel. Copy its token into `TUNNEL_TOKEN`, and add a public
  hostname `staging.<your domain>` → `HTTP` → `caddy:80`. `wok` starts the `cloudflared` service whenever
  `TUNNEL_TOKEN` is set. Cloudflare terminates TLS.
- **Quick tunnel** (no Cloudflare account, no domain, no inbound ports): `WOK_TUNNEL=quick`, `SITE_ADDRESS` unset.
  `wok` starts the `quicktunnel` service, a [TryCloudflare](https://try.cloudflare.com) tunnel to `caddy:80`, and
  `sudo wok url` prints its random `https://<words>.trycloudflare.com` address. **The address changes whenever that
  container restarts** (a reboot, `wok down`/`up`, a new `cloudflared` version; API and Admin deploys don't touch
  it). After a change: set `ADMIN_URL` and `PUBLIC_ASSET_BASE_URL` to it, `sudo wok up`, and replace the old
  address on the Supabase Auth redirect allow list (exact address, never a `*.trycloudflare.com` wildcard: anyone
  can get one of those, and auth emails would then send people there). Cloudflare offers it for testing, with no
  uptime guarantee: fine for staging and a demo, not for production.

`cloudflared` is pinned (`2026.10.6`); bump it deliberately in `compose.yml`.

Either way, `ADMIN_URL` and `PUBLIC_ASSET_BASE_URL` use the public `https://` host name.

### On an NVIDIA Brev instance

Brev runs the VM (for staging, a GCP n2d-standard-8 CPU instance, Ubuntu). Nothing in the scripts is Brev-specific.

- **Prefer the tunnel.** It needs no inbound firewall change, and it keeps working if Brev moves or re-IPs the VM.
  For a domain instead, open TCP 80 and 443 for the instance in the Brev console (or the GCP firewall, if the team
  owns the project), and point the A record at the instance's external IP. Check that the IP is static first.
- Docker usually comes preinstalled. `install.sh` keeps it and only adds a newer Compose plugin if needed.
- The box is shared. Before the first run, tell the teammate who uses it, and stop the old hackathon stack. It ran
  a vulnerable Next.js behind a public tunnel (#63). `sudo ss -ltnp` shows what holds 80, 443, 3000 and 8000.
- A Brev instance can be stopped or deleted from the console. Use `IMAGE_HOST=supabase` and
  `MATERIAL_STORE=supabase` so the host holds no data that isn't elsewhere. Then losing the VM means only
  [moving to another host](#move-to-another-host).

## Move to another host

The host is stateless when slides and source material live in Supabase (`IMAGE_HOST=supabase`,
`MATERIAL_STORE=supabase`). Jobs, Posts and DBOS state are in Postgres.

1. **New host:** `sudo bash install.sh --ref <commit>` (the first run stops at the env file).
2. **Copy the env file** from the old host over SSH, never through chat, email or the repo:
   `ssh old sudo cat /opt/wok/.env | ssh new 'sudo tee /opt/wok/.env >/dev/null'`. Change any host-name values
   (`ADMIN_URL`, `PUBLIC_ASSET_BASE_URL`, `INSTAGRAM_REDIRECT_URI`, `SITE_ADDRESS`).
3. **Set it up without starting:** `sudo bash install.sh --ref <commit> --no-start` on the new host.
4. **Stop the old host:** `sudo wok pause moving; sudo wok down` there, then
   `sudo systemctl disable --now wok-update.timer`. Do this before step 5. Two APIs on one database would both
   resume the same durable Jobs.
5. **Start the new one:** `sudo wok up` on the new host.
6. **Repoint the front.** For a domain, move the DNS A record (Caddy gets a new certificate). For a tunnel, the same
   `TUNNEL_TOKEN` now connects from the new host. Check that the old `cloudflared` is gone.
7. **If the host name changed,** add the new URL in Supabase Auth (*Redirect URLs*, Site URL) and in the Meta app's
   Instagram redirect URI. Remove the old ones after the move.
8. **Local data only** (`IMAGE_HOST=local` or `MATERIAL_STORE=local`): copy the `nemo-k-cards_api_data` volume
   before step 5, e.g. `docker run --rm -v nemo-k-cards_api_data:/data alpine tar -C /data -cz . > api_data.tgz`
   and the reverse on the new host.
9. Run the smoke checklist on the new host, then delete `/opt/wok/.env` on the old one.

## Run it by hand (no `wok`)

For a local test of the stack, or a host where you don't want the updater:

```bash
sudo install -m 600 /dev/null /srv/nemo/stack.env    # then fill it from deploy/env.example
export ENV_FILE=/srv/nemo/stack.env
docker compose -f deploy/compose.yml --env-file "$ENV_FILE" up -d --build
docker compose -f deploy/compose.yml --env-file "$ENV_FILE" ps
```

- The API and Admin are also published on `127.0.0.1:18000` / `127.0.0.1:13000` for checks on the host itself.
- `AGENT_RUNNER` is fixed to `local` (`claude -p` inside the API container). OpenShell sandboxes from a container is #58.
- Restarts are safe for data. Jobs and Posts are durable and resume at the next start. A restart still interrupts
  running agent stages, so check `/jobs` first (see the root `CLAUDE.md`). The updater restarts the API whenever
  `staging` moves, so deploy when no live Job is running, or pause.

## First-host smoke checklist (#61/#63)

Run in order on the first real host, with a **staging** Supabase project and `PUBLISH_MODE=dryrun`. Never use `graph`
here. Tick each line in the PR or issue that brings the host up. `compose …` below means
`docker compose -f /opt/wok/stack/compose.yml -f /opt/wok/stack/compose.server.yml --env-file /opt/wok/.env`
(run as `wok`), or use the `wok` command shown.

**Host and updater**
- [ ] `bash install.sh --dry-run` printed the expected actions; the real runs (first and second) finished cleanly.
- [ ] `/opt/wok/.env` is mode 600, owner `wok`; `/opt/wok/stack/api.env` has no `GHCR_`, `WOK_` or `TUNNEL_TOKEN` lines.
- [ ] `sudo wok config` passes; `systemctl list-timers wok-update.timer` shows the next run.
- [ ] Moving `staging` with the deploy button: within ~3 minutes `sudo wok status` shows the new commit and
      `wok logs updater` shows `updated`.
- [ ] `sudo wok pause test`, move the tag: nothing changes; `sudo wok resume`: it updates.
- [ ] Re-running `install.sh` changes nothing and restarts nothing.
- [ ] Reboot the host: the stack comes back healthy without anyone logging in.

**Stack**
- [ ] `supabase db push` against the staging project succeeded (via the deploy button).
- [ ] `sudo wok status` shows `api` and `admin` **healthy** (both images have a HEALTHCHECK).
- [ ] `curl -s 127.0.0.1:18000/health` shows `demo_mode`, `agent_runner: local`, `publish_mode: dryrun`.
- [ ] `sudo wok logs api` shows DBOS launched against Postgres and no tracebacks.
- [ ] `compose exec api claude --version` works, and `compose exec api env` shows no unexpected secrets
      (only those in the env file, minus the host-only lines).

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
- [ ] `compose restart api` while a fixture Job runs: the Job resumes and finishes after the start.
- [ ] `sudo wok down && sudo wok up`: Jobs, Posts and rendered slides (`api_data` volume) are all still there.

**Instagram (only if `INSTAGRAM_APP_ID` is set)**
- [ ] `INSTAGRAM_REDIRECT_URI` is `https://<SITE_ADDRESS>/backend/instagram-accounts/callback` on the Meta app, and
      connecting an account completes.
