# Production hardening: issues and robust fixes

What has to change before What's On Korea runs for real customers. Each issue has the **evidence** (file:line on `main`, 2026-10-07), **why it matters**, a **robust fix**, and **done when** (the test or check that proves it). The high-level priorities are in [ROADMAP.md](ROADMAP.md). This file is the engineering detail.

**Priority:** **P0** = can cause a public mistake or a security hole · **P1** = needed to run reliably for real users · **P2** = maturity.
Items marked *(verified)* were confirmed in the code. The rest are standard gaps for a prototype of this shape.

## Summary

| ID | Priority | Issue |
|---|---|---|
| [PUB-1](#pub-1-publishing-is-not-idempotent-and-trusts-ambiguous-errors) | P0 | Publishing isn't safe to repeat and trusts ambiguous errors (duplicate posts, false "Nothing was posted") |
| [PUB-2](#pub-2-one-instagram-token-is-used-by-several-machines) | P0 | One Instagram token is used by several machines |
| [PUB-3](#pub-3-instagram-token-is-never-refreshed) | P0 | Instagram token is never refreshed |
| [SEC-1](#sec-1-ssrf-the-host-fetches-agent--and-user-supplied-urls) | P0 | SSRF: the host fetches agent- and user-supplied URLs |
| [SEC-2](#sec-2-authentication-is-optional) | P0 | Authentication is optional |
| [LEG-1](#leg-1-ai-generated-images-are-not-disclosed-on-the-post) | P0 | AI-generated images aren't disclosed on the Instagram post |
| [PUB-4](#pub-4-rate-limit-blind-instagram-api-usage) | P1 | Instagram API usage ignores rate limits |
| [PUB-5](#pub-5-single-account-dev-mode-instagram-integration) | P1 | Single-account, development-mode Instagram integration |
| [SEC-3](#sec-3-generated-assets-are-public-and-never-cleaned-up) | P1 | Generated assets are public and never cleaned up |
| [SEC-4](#sec-4-shared-access-code-no-roles-no-audit-log) | P1 | Shared access code, no roles, no audit log |
| [SEC-5](#sec-5-secrets-live-in-env-files) | P1 | Secrets live in `.env` files |
| [AGT-1](#agt-1-no-cost-or-concurrency-limits-on-agent-runs) | P1 | No cost or concurrency limits on agent runs |
| [AGT-2](#agt-2-sandbox-allowlist-vs-real-event-sites) | P1 | Sandbox allowlist vs real event sites |
| [AGT-3](#agt-3-no-quality-regression-checks-for-prompts-and-models) | P1 | No quality-regression checks for prompts and models |
| [AGT-4](#agt-4-prompt-injection-is-tested-by-one-demo-not-a-suite) | P1 | Prompt injection is tested by one demo, not a suite |
| [REL-1](#rel-1-jobs-run-inside-the-api-process) | P1 | Jobs run inside the API process |
| [REL-2](#rel-2-storage-sqlite-with-one-shared-connection-no-migrations-no-backups) | P1 | SQLite with one shared connection, no migrations, no backups |
| [REL-3](#rel-3-outbound-calls-lack-consistent-timeouts-and-retries) | P2 | Outbound calls lack consistent timeouts and retries |
| [TEN-1](#ten-1-no-per-user-or-per-org-rate-limits-and-quotas) | P1 | No per-user or per-org rate limits and quotas |
| [LEG-2](#leg-2-image-licensing-records-and-takedowns) | P1 | Image licensing records and takedowns |
| [LEG-3](#leg-3-personal-data-retention-pipa) | P1 | Personal-data retention (PIPA) |
| [OPS-1](#ops-1-observability-and-alerting) | P1 | Observability and alerting |
| [OPS-2](#ops-2-no-staging-or-real-deployment) | P1 | No staging or real deployment |
| [TST-1](#tst-1-failure-paths-are-untested) | P1 | Failure paths are untested |
| [UI-1](#ui-1-ui-shows-outcomes-it-hasnt-confirmed) | P1 | UI shows outcomes it hasn't confirmed |
| [UI-2](#ui-2-accessibility-mobile-error-boundaries) | P2 | Accessibility, mobile, error boundaries |

---

## Publishing and Instagram

### PUB-1 Publishing is not idempotent and trusts ambiguous errors
**P0** *(verified; seen live on 2026-10-07: Instagram returned "Application request limit reached" on `media_publish`, but the post went live)*

**Evidence**
- `backend/app/services/publisher.py`: every call creates new child containers and a new carousel container. `_call()` raises on any error body, including on `media_publish`.
- `backend/app/pipeline/orchestrator.py:388`: any exception → `READY_FOR_REVIEW` + "Publish failed", so Approve is offered again.
- `backend/app/main.py:32-37` (`recover_interrupted`): a restart during `PUBLISHING` marks the job failed.
- `apps/web/src/components/publish-toast.tsx:39`: the failed state always says "Nothing was posted."

**Why it matters:** Instagram has no idempotency key, and some errors (rate limit, timeout, connection drop, 5xx) don't tell you whether the post happened. Clicking Approve again creates a **duplicate public post**, and the UI tells the operator something false.

**Robust fix**
1. **Persist a publish state machine on the job**, writing each transition *before* the outside call:
   `queued → uploading → containers_ready(creation_id) → publishing → published | failed | unknown`
2. **Container ID = idempotency key.** Save the carousel `creation_id`; every retry reuses it instead of creating new containers. A container can only be published once (to confirm: publishing an already-published container should return an error or `PUBLISHED`, never a second post).
3. **Classify `media_publish` errors:**
   - Success with an ID → `published`.
   - Clear rejection (validation, permissions, expired container) → `failed`, nothing posted.
   - Ambiguous (Meta codes 4/17/32/613, timeout, network error, 5xx) → `unknown`.
4. **Reconciler:** for `unknown`, call `GET /{creation_id}?fields=status_code` with backoff. `PUBLISHED` → mark published and fetch the link. `FINISHED` → not posted, safe to retry *with the same container*. `EXPIRED` (after 24 h) → failed. Alert a human if it's still unknown after N minutes.
5. **Restart recovery** puts interrupted `PUBLISHING` jobs into `unknown`, never `failed`.
6. **After a confirmed publish, link-lookup errors are non-fatal:** store the media ID and fetch the permalink later.

**Done when:** fake-Instagram tests cover rate limit on publish, timeout on publish, double Approve, and restart mid-publish. Each yields exactly one post and an honest status.

### PUB-2 One Instagram token is used by several machines
**P0** *(verified: the Mac and the Brev instance both run the API with the same token)*

**Why it matters:** each machine has its own database and cache. They drain one rate limit without knowing about each other and can't see each other's posts, so duplicates and limit hits follow.

**Robust fix:** one **publisher service** owns the token, and publishes go through its queue. Every other environment runs `PUBLISH_MODE=mock` or `dryrun` against a **separate test Instagram account and token**. Enforce it in config: refuse to start with `PUBLISH_MODE=graph` unless `ENVIRONMENT=production`.

**Done when:** only the production deployment can publish for real; dev and staging can't, by configuration.

### PUB-3 Instagram token is never refreshed
**P0** *(verified: no refresh code exists)*

**Why it matters:** long-lived Instagram tokens expire after about 60 days. When this one does, publishing and insights silently stop.

**Robust fix:** a scheduled job calls `GET /refresh_access_token` (for example weekly), stores the new token and expiry in the secret store, and alerts at 14 days left. The Admin shows "Instagram connected · expires in N days".

**Done when:** the token's expiry is visible, refreshes automatically, and an expired token raises an alert, not a silent failure.

### PUB-4 Rate-limit-blind Instagram API usage
**P1** *(verified)*

**Evidence**
- `publisher.py:25-32`: fixed 2 s polling, up to 30 tries per container.
- `seed.py:55-79`: insights make one call per post, cached 5 min per process.
- `dashboard-view.tsx:13`: metrics are requested every 5 s.

**Robust fix**
- Exponential backoff with jitter for container polling.
- Read `X-App-Usage` / `X-Business-Use-Case-Usage` on every response; slow down at 75 %, pause at 90 % until the reset time.
- One insights call (`/me/media?fields=id,insights.metric(saved)`), cached 30 min in shared storage.
- Check `content_publishing_limit` before publishing (Instagram's cap is 50 posts per 24 h).
- Show rate-limit errors as "Instagram rate limit, try at HH:MM".

**Done when:** a full publish plus a Dashboard session stays under 25 % of the app's quota, and hitting the limit produces a clear message with a retry time.

### PUB-5 Single-account, dev-mode Instagram integration
**P1**

**Why it matters:** customers need to post to *their own* accounts. Publishing on behalf of other accounts requires Meta **App Review** for `instagram_business_content_publish` (plus insights permissions). Development mode only works for app testers.

**Robust fix:** an Instagram Login OAuth flow per org, tokens stored per connected account in the secret store, then submit for App Review with a screencast and privacy policy.

**Done when:** a non-tester org can connect an account and publish.

---

## Security

### SEC-1 SSRF: the host fetches agent- and user-supplied URLs
**P0** *(verified)*

**Evidence:** `link_checker.py:69,101`, `photos.py:153` and `drafts.py:71` all fetch URLs that came from the agent or the user, with `follow_redirects=True`. `link_checker.suspicious_reason()` only blocks *literal* IP addresses.

**Why it matters:** a hostname that resolves to `127.0.0.1`, or a redirect to `169.254.169.254` (the AWS instance-metadata endpoint; Brev runs on AWS), gets fetched by the trusted host. A prompt-injected page can steer the agent's URLs, so this is a real attack path *around* the sandbox.

**Robust fix:** one shared `safe_fetch()` used by every host-side fetch:
- https only (http only for an explicit allowlist such as the Seoul Open API).
- Resolve the DNS first and refuse private, loopback, link-local, multicast and metadata addresses (IPv4 and IPv6).
- Pin the connection to the vetted IP.
- Follow redirects **manually** (max 3), re-checking every hop.
- Cap the response size and the time.

**Done when:** tests prove `http://localhost`, a hostname resolving to `10.x`, a redirect to `169.254.169.254`, and IPv6 `::1` are all refused.

### SEC-2 Authentication is optional
**P0** *(verified: `main.py:62`; an empty `ADMIN_TOKEN` means no auth)*

**Why it matters:** the Brev deployment is exposed through public Cloudflare tunnels.

**Robust fix:** refuse to start without auth when bound to anything but localhost or when `ENVIRONMENT != dev`. Longer term, real user accounts (SEC-4).

**Done when:** a public deployment can't start unauthenticated.

### SEC-3 Generated assets are public and never cleaned up
**P1** *(verified: `main.py:75` serves `/assets` without auth; `image_host.py` creates a **public** Supabase bucket; nothing deletes uploads)*

**Why it matters:** every dry run and every rejected deck stays reachable on the public internet forever. Job IDs are only 6 hex characters, so the URLs are guessable.

**Robust fix:**
- Serve Admin previews through an authenticated route.
- Upload to a **private** bucket and give Instagram **signed URLs** valid for about 1 h, which is long enough for it to fetch the images.
- Delete uploads after publish, reject or dry run, plus a bucket lifecycle rule as a backstop.
- Use longer random job IDs.

**Done when:** after a publish or reject, no slide URL from that job resolves.

### SEC-4 Shared access code, no roles, no audit log
**P1**

**Robust fix:** user accounts (SSO or email links) with roles: viewer, editor (create jobs), publisher (approve). Only publishers can approve or override blocking issues. An append-only audit log records create, approve, override, reject and delete, with who and when.

**Done when:** every publish and override can be traced to a named person.

### SEC-5 Secrets live in `.env` files
**P1**

**Robust fix:** a secret manager (e.g. Doppler, 1Password, AWS Secrets Manager), separate dev, staging and prod credentials, rotation, and no production secrets on laptops. The `/hackathon/secrets` → OpenShell provider path (BACKEND §0.3) stays the pattern for agent-side credentials.

---

## Agents and AI safety

### AGT-1 No cost or concurrency limits on agent runs
**P1** *(verified: `main.py:101,206` start jobs without any limit)*

**Why it matters:** each job runs several `claude -p` stages. Anyone with access can start many at once, which drives cost up and makes the API slow.

**Robust fix:** a global concurrency cap (e.g. 2 running jobs), per-stage turn and token limits, recorded token usage and cost per stage and job, and a monthly budget with an 80 % alert (see TEN-1 for per-user limits).

### AGT-2 Sandbox allowlist vs real event sites
**P1** *(verified 2026-10-07: the researcher cited `m.yudeung.com` (Jinju festival); not in `agent-policy.yaml`, so it's denied inside OpenShell)*

**Robust fix:** a reviewed process for adding hosts. A denied fetch in `openshell logs` becomes a suggestion in the Policy Log, a human approves it, and `openshell policy update` plus a commit to the policy file follow. Track the denial rate per job.

### AGT-3 No quality-regression checks for prompts and models
**P1**

**Robust fix:** pin the model version per stage. Keep a golden set of requests with expected outcomes (which events are included or excluded, no invented facts, at most 5 hashtags, photo relevance), run in CI on prompt or model changes, and track the scores over time.

### AGT-4 Prompt injection is tested by one demo, not a suite
**P1**

**Robust fix:** a red-team fixture set: hidden instructions in pages, in image alt text, in `/hackathon/input`-style files, requests to read `restricted/` or `secrets/`, and exfiltration URLs. CI asserts that each is reported as an issue and that the sandbox log shows a denial.

---

## Reliability and data

### REL-1 Jobs run inside the API process
**P1** *(verified: `orchestrator.spawn()` at `main.py:101,158,206`; `recover_interrupted` at `main.py:32` marks them failed after a restart)*

**Why it matters:** any deploy, crash or `--reload` kills running agent jobs and publishes.

**Robust fix:** a persisted queue and a separate worker process: the API only enqueues, and the worker claims jobs with a lease. Each stage's output is already stored on the job, so a restarted worker **resumes from the last completed stage** instead of failing. Publishing follows PUB-1's state machine.

**Done when:** killing the worker mid-job and restarting it completes the job without re-running the finished stages.

### REL-2 Storage: SQLite with one shared connection, no migrations, no backups
**P1** *(verified: `store.py:13,29`)*

**Robust fix:** Postgres for production (or, while it's one machine, SQLite in WAL mode with one connection per thread). Schema migrations (Alembic), nightly backups with a tested restore, and a retention policy for job data and rendered assets.

### REL-3 Outbound calls lack consistent timeouts and retries
**P2**

**Robust fix:** one HTTP client factory with timeouts, bounded retries with jitter for idempotent calls only, and circuit breakers for Supabase, link checks and insights.

---

## Rate limits and multi-tenancy

### TEN-1 No per-user or per-org rate limits and quotas
**P1**

**Robust fix:** three layers.

| Layer | Keyed by | Mechanism |
|---|---|---|
| API requests | user (+ IP before login) | token bucket, `429` + `Retry-After` |
| Expensive actions (jobs, spend) | user → org | concurrency lease, sliding-window jobs/day, budget ledger (reserve then settle) |
| Upstream (Instagram) | **Instagram account / app**, not user | per-account single-flight publish queue + shared budget from Meta usage headers |

- Enforce at the API edge **and** again in the worker.
- Use atomic counters (Redis, or SQLite transactions while there's one machine).
- Limits come from a plan table (Free / Pro / Team), not from code.
- Show quotas in the UI ("2 of 30 jobs today · next publish slot 14:20").

---

## Content and legal

### LEG-1 AI-generated images are not disclosed on the post
**P0** *(the card says "Image · AI-generated", but the Instagram post itself doesn't)*

**Robust fix:** when any slide uses `license: ai_generated`, set Instagram's AI-info label where the API supports it, and add a caption line ("Some images are AI-generated illustrations"). The approve check blocks a publish that's missing the caption line.

### LEG-2 Image licensing records and takedowns
**P1**

**Robust fix:**
- Keep license, credit, source URL and fetch date for every image actually published (already in `ImageAsset`; persist it with the published post).
- Add a takedown workflow: edit or delete a live post from the Admin, with an audit entry.

### LEG-3 Personal-data retention (PIPA)
**P1**

**Robust fix:** a defined retention period for jobs, briefs and logs. Delete on request. Keep personal data out of logs (the visual QA scanner covers slides; extend it to logs). Write down what's stored and why.

---

## Operations

### OPS-1 Observability and alerting
**P1** *(verified: about 6 `log.exception` calls in `backend/app`, no request IDs)*

**Robust fix:**
- Structured logs with job ID, stage and request ID.
- Metrics: job duration and failures per stage, agent tokens and cost, publish outcomes, Instagram quota usage.
- Error tracking.
- **Alerts:** a publish in `unknown` or `failed`, the token expiring, budget at 80 %, agent error rate, sandbox denial spikes.

### OPS-2 No staging or real deployment
**P1** *(verified: Brev runs `next start` and `uvicorn` behind Cloudflare **quick** tunnels)*

**Robust fix:**
- A fixed domain with TLS and health checks, and deploys from CI with zero-downtime restarts (once REL-1 lets jobs survive them).
- A staging environment on a test Instagram account.
- Runbooks: wrong post published, Instagram down, token expired, budget exceeded.

---

## Testing and CI

### TST-1 Failure paths are untested
**P1** *(verified: no tests for publisher failures, SSRF or auth on every route)*

**Robust fix:**
- A fake Instagram transport (`httpx.MockTransport`) covering PUB-1's scenarios.
- SSRF cases (SEC-1).
- A test that walks every route with and without the token.
- An end-to-end smoke run (fixture job → Review → mock publish) in CI.
- Mock the network in link-check tests so CI is deterministic.

**Already done (2026-10-07):** tests are isolated from live credentials and fail fast if they aren't (`backend/tests/conftest.py`); `web-checks.yml` runs the frontend typecheck, lint and build.

---

## Frontend

### UI-1 UI shows outcomes it hasn't confirmed
**P1** *(verified: `publish-toast.tsx:39`)*

**Robust fix:**
- Show only states the backend has confirmed: Published / Failed (nothing posted) / **Unknown, verifying with Instagram**.
- While a job is `unknown`, disable Approve and offer "I checked Instagram" as a logged override.
- Rate-limit messages include the retry time.

### UI-2 Accessibility, mobile, error boundaries
**P2**

**Robust fix:** keyboard and screen-reader pass, a mobile layout (in progress on `fix/mobile-and-empty-defaults`), React error boundaries per page, and visible quotas and roles once TEN-1 and SEC-4 land.

---

## Suggested order

1. **Before any real publishing:** PUB-1 (minimal: saved and reused container, `unknown` state, honest toast), PUB-2 (one publisher), SEC-1, SEC-2, LEG-1.
2. **First week:** PUB-3, PUB-4, REL-1, AGT-1, TST-1, OPS-1 alerts.
3. **Before the first paying customer:** PUB-5 (OAuth + App Review), SEC-3, SEC-4, SEC-5, TEN-1, REL-2, OPS-2, LEG-2, LEG-3.
4. **Scale:** plans and billing, fair scheduling, AGT-3 eval suite, AGT-4 red-team suite, load tests, UI-2.
