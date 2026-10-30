// In-memory mock backend for QA before FastAPI exists. Implements the SPEC §12 contract
// behind /api/mock/*. Pipeline progress is derived from elapsed time (no timers), so state
// survives hot reloads and is deterministic per job.

import bad from "./scenarios/bad.json";
import fail from "./scenarios/fail.json";
import good from "./scenarios/good.json";
import slideSources from "./scenarios/slide-sources.json";
import { seedInstagramAccounts, seedBrainstorm, seedPolicyEvents } from "./seed";
import type {
  Brainstorm,
  BrainstormUpdate,
  CardDeck,
  DeckChange,
  DeckEdit,
  DeckVersion,
  EventBrief,
  Issue,
  Job,
  JobStatus,
  LogLine,
  Material,
  MaterialRef,
  Metric,
  OrgProfile,
  OrgProfileState,
  PipelineStep,
  PolicyEvent,
  Post,
  SafeguardVerdict,
  Scenario,
  SlideSources,
  VerificationReport,
} from "@/lib/types";

interface ScenarioData {
  description: string;
  prompt: string;
  briefs: EventBrief[];
  verification: VerificationReport;
  deck: Omit<CardDeck, "job_id">;
  issues: Issue[];
  safeguard?: SafeguardVerdict;
  notes: Record<string, string>;
  log: { stage: string; message: string; level?: "info" | "warn" }[];
  policy_events: { stage: string; binary: string; host: string; request: string; result: PolicyEvent["result"] }[];
  fail_at?: string;
  error?: string;
}

export const SCENARIOS = { good, bad, fail } as unknown as Record<Scenario, ScenarioData>;

const STAGES = [
  { key: "research", name: "Research", where: "researcher · sandbox", status: "RESEARCHING", ms: 3000 },
  { key: "verify", name: "Verify links", where: "host", status: "VERIFYING", ms: 2000 },
  { key: "copy", name: "Outline & copy", where: "copywriter · sandbox", status: "WRITING", ms: 2000 },
  { key: "render", name: "Render", where: "host · Playwright", status: "RENDERING", ms: 2500 },
  { key: "qa", name: "Visual QA", where: "host", status: "QA", ms: 1500 },
  { key: "review", name: "Final review", where: "reviewer · sandbox", status: "REVIEWING", ms: 2000 },
  { key: "safeguard", name: "Safeguard", where: "safeguard · sandbox", status: "REVIEWING", ms: 1500 },
] as const;
const PUBLISH_MS = 2500;

interface MockJob {
  id: string;
  prompt: string;
  scenario: Scenario;
  source: "quick" | "brainstorm";
  createdAt: number;
  /** Brainstorm jobs bring their own facts/outline and skip Research. */
  override?: { briefs: EventBrief[]; deck: Omit<CardDeck, "job_id"> };
  titleOverride?: string;
  material?: MaterialRef[];
  decision?: { kind: "approve" | "reject"; at: number; reason?: string; accountId?: string; deckVersion?: number };
  /** Saved edits (#19): version 2, 3, … The safeguard "checks" each for CHECK_MS after it is saved. */
  versions?: { version: number; deck: Omit<CardDeck, "job_id">; at: number; changes: DeckChange[] }[];
}

const CHECK_MS = 1500;

interface Store {
  profile?: OrgProfileState;
  jobs: Map<string, MockJob>;
  brainstorms: Map<string, Brainstorm>;
  materials: Map<string, Material>;
}

const g = globalThis as unknown as { __wokMock?: Store };

function seed(): Store {
  const now = Date.now();
  const H = 3_600_000;
  const jobs: MockJob[] = [
    { id: "3f9a1c", prompt: good.prompt, scenario: "good", source: "quick", createdAt: now - 1 * H },
    { id: "a17c02", prompt: "A festival looks fun — what to know first", scenario: "good", source: "brainstorm", createdAt: now - 2 * H, titleOverride: "A festival looks fun — what to know first" },
    { id: "e04b91", prompt: bad.prompt, scenario: "bad", source: "quick", createdAt: now - 20 * H },
    { id: "c3d810", prompt: "3 exhibitions worth visiting this week", scenario: "good", source: "quick", createdAt: now - 46 * H, titleOverride: "3 exhibitions worth visiting this week", decision: { kind: "approve", at: now - 45 * H } },
    { id: "9be7f3", prompt: "Hidden local events beyond the checklist", scenario: "good", source: "quick", createdAt: now - 70 * H, titleOverride: "Hidden local events beyond the checklist", decision: { kind: "approve", at: now - 69 * H } },
  ];
  return { jobs: new Map(jobs.map((j) => [j.id, j])), brainstorms: new Map(), materials: new Map() };
}

export const store = () => (g.__wokMock ??= seed());
export const resetStore = () => {
  g.__wokMock = seed();
};

const newId = () => Math.random().toString(16).slice(2, 8);

// ---------------------------------------------------------------- jobs

function stagesFor(job: MockJob) {
  return job.source === "brainstorm" ? STAGES.filter((s) => s.key !== "research") : STAGES;
}

/** Snapshot of a job at `now`: status, revealed data, pipeline steps and log. */
export function snapshot(job: MockJob, now = Date.now()): Job {
  const sc = SCENARIOS[job.scenario];
  const stages = stagesFor(job);
  const elapsed = now - job.createdAt;
  const fmt = (t: number) => new Date(t).toLocaleTimeString("en-GB", { timeZone: "Asia/Seoul" });

  let t = 0;
  let status: JobStatus = "READY_FOR_REVIEW";
  let currentIdx = stages.length; // index of the running stage; length = all done
  let failed = false;
  for (let i = 0; i < stages.length; i++) {
    if (elapsed < t + stages[i].ms) {
      currentIdx = i;
      status = stages[i].status;
      break;
    }
    if (sc.fail_at === stages[i].key) {
      currentIdx = i;
      failed = true;
      status = "FAILED";
      break;
    }
    t += stages[i].ms;
  }
  const finished = currentIdx === stages.length;
  // Only the Safeguard can block; other blocking Issues wait for the operator (same as the FastAPI backend).
  const blockedBySafeguard = finished && sc.safeguard?.decision === "block";
  if (finished) status = blockedBySafeguard ? "BLOCKED" : "READY_FOR_REVIEW";

  if (job.decision?.kind === "reject" && status === "BLOCKED") status = "REJECTED";
  if (job.decision && finished && status === "READY_FOR_REVIEW") {
    if (job.decision.kind === "reject") status = "REJECTED";
    else status = now - job.decision.at < PUBLISH_MS ? "PUBLISHING" : "PUBLISHED";
  }

  const reached = (key: string) => {
    const i = stages.findIndex((s) => s.key === key);
    return i === -1 ? true : i < currentIdx || finished;
  };

  const pipeline: PipelineStep[] = [
    ...stages.map((s, i) => ({
      name: s.name,
      where: s.where,
      state: (s.key === "safeguard" && blockedBySafeguard
        ? "failed"
        : i < currentIdx ? "done" : i === currentIdx ? (failed ? "failed" : "running") : "pending") as PipelineStep["state"],
      note: i < currentIdx || (i === currentIdx && failed) ? sc.notes[s.key] : undefined,
    })),
    {
      name: "Human publish",
      where: "you",
      state: status === "PUBLISHED" ? "done" : status === "PUBLISHING" ? "running" : blockedBySafeguard ? "skipped" : "pending",
      note: status === "PUBLISHED" ? "mock post created" : blockedBySafeguard ? "Blocked by safeguard: regenerate or reject" : "Approve in Review",
    },
  ];

  let stageStart = job.createdAt;
  const log: LogLine[] = [];
  stages.forEach((s, i) => {
    if (i <= currentIdx) {
      sc.log.filter((l) => l.stage === s.key).forEach((l) => log.push({ time: fmt(stageStart), stage: s.key, message: l.message, level: l.level }));
    }
    stageStart += s.ms;
  });
  if (job.source === "brainstorm") log.unshift({ time: fmt(job.createdAt), stage: "brainstorm", message: "Generated from a Brainstorm — Research skipped" });

  const briefs = job.override?.briefs ?? sc.briefs;
  const edits = job.versions ?? [];
  const last = edits.at(-1);
  const deckSrc = last?.deck ?? job.override?.deck ?? sc.deck;
  const checking = !!last && now - last.at < CHECK_MS;
  const deck = reached("copy") ? { ...deckSrc, job_id: job.id, title: job.titleOverride ?? deckSrc.title } : null;

  return {
    id: job.id,
    prompt: job.prompt,
    status,
    created_at: new Date(job.createdAt).toISOString(),
    source: job.source,
    briefs: reached("research") ? briefs : [],
    verification: reached("verify") ? sc.verification : null,
    deck,
    issues: finished ? sc.issues : [],
    safeguard: !finished || checking ? null : last && sc.safeguard ? { ...sc.safeguard, deck_version: last.version } : (sc.safeguard ?? null),
    deck_version: last?.version ?? 1,
    safeguard_pending: finished && checking,
    error: failed ? sc.error : job.decision?.reason ?? null,
    pipeline,
    log,
    material: job.material ?? [],
    // Fact sources per slide (#25): fixtures here; the real API computes them (backend/app/slide_sources.py).
    slide_sources: deck && !job.override && reached("verify")
      ? ((slideSources as unknown as Record<string, SlideSources[]>)[job.scenario] ?? []).map((s) => {
          // Text a person typed (#19): flag it like the API does (the mock doesn't follow reorders).
          const edited = [...new Set(edits.flatMap((v) => v.changes).filter((c) => c.slide === s.slide_index && c.field.startsWith("slide.")).map((c) => c.field.slice(6) as "heading" | "body"))];
          return edited.length ? { ...s, edited } : s;
        })
      : [],
  };
}

export const listJobs = () =>
  [...store().jobs.values()].sort((a, b) => b.createdAt - a.createdAt).map((j) => snapshot(j));

export const getJob = (id: string) => {
  const j = store().jobs.get(id);
  return j && snapshot(j);
};

// ---------------------------------------------------------------- source material (#20)

const refOf = (m: Material): MaterialRef => ({
  id: m.id, kind: m.kind, name: m.name, url: m.url, content_type: m.content_type, size: m.size, pages: m.pages,
  chars: m.chars, truncated: m.truncated, image: m.image,
});

/** Like the API: extracted text is kept (here the file isn't parsed; the mock records a placeholder). */
export function addMaterial(m: Omit<Material, "id" | "created_at" | "chars" | "truncated" | "image"> & { image?: boolean }): MaterialRef {
  const full: Material = { ...m, id: `m-${newId()}${newId()}`.slice(0, 18), created_at: new Date().toISOString(), chars: m.text.length, truncated: false, image: m.image ?? false };
  store().materials.set(full.id, full);
  return refOf(full);
}

export const getMaterial = (id: string) => store().materials.get(id);

/** Refs for ids sent with a Job or message; undefined if any is unknown (the API answers 422). */
export function materialRefs(ids: string[]): MaterialRef[] | undefined {
  const found = [...new Set(ids)].map((id) => store().materials.get(id));
  return found.every(Boolean) ? found.map((m) => refOf(m!)) : undefined;
}

export function createJob(prompt: string, scenario: Scenario = "good", material: MaterialRef[] = []): Job {
  const job: MockJob = { id: newId(), prompt, scenario, source: "quick", createdAt: Date.now(), material };
  store().jobs.set(job.id, job);
  return snapshot(job);
}

/** The Post an approval made (#21), as the API would show it at `now`: mock mode, so nothing leaves the server. */
function postOf(job: MockJob, now = Date.now()): Post | null {
  const d = job.decision;
  if (!d || d.kind !== "approve") return null;
  const account = seedInstagramAccounts.find((a) => a.id === d.accountId);
  const elapsed = now - d.at;
  const done = elapsed >= PUBLISH_MS;
  const at = new Date(d.at).toISOString();
  const step = done ? "done" : elapsed < PUBLISH_MS / 3 ? "upload" : elapsed < (2 * PUBLISH_MS) / 3 ? "containers" : "publish";
  return {
    id: `00000000-0000-4000-9000-${job.id.padStart(12, "0")}`,
    job_id: job.id,
    instagram_account_id: account?.id ?? null,
    handle: account?.handle ?? null,
    mode: "mock",
    state: done ? "published" : elapsed < PUBLISH_MS / 2 ? "queued" : "publishing",
    caption: null, // the Deck version's caption is posted (#19)
    deck_version: job.versions?.at(-1)?.version ?? 1,
    creation_id: done ? `mock-${job.id}` : null,
    media_id: done ? "MOCK" : null,
    permalink: done ? `https://www.instagram.com/p/MOCK${job.id.toUpperCase()}/` : null,
    attempts: done ? 1 : 0,
    progress: {
      step,
      label: done ? "Mock publish complete (nothing posted)" : "Publishing (mock)",
      done: 0,
      total: 0,
      percent: done ? 100 : Math.round((elapsed / PUBLISH_MS) * 99),
    },
    created_at: at,
    updated_at: new Date(Math.min(now, d.at + PUBLISH_MS)).toISOString(),
    published_at: done ? new Date(d.at + PUBLISH_MS).toISOString() : null,
    finished_at: done ? new Date(d.at + PUBLISH_MS).toISOString() : null,
  };
}

export const listPosts = (jobId?: string | null) =>
  [...store().jobs.values()]
    .filter((j) => !jobId || j.id === jobId)
    .map((j) => postOf(j))
    .filter((p): p is Post => p !== null)
    .sort((a, b) => b.created_at.localeCompare(a.created_at));

export function decide(
  id: string,
  kind: "approve" | "reject",
  extra: { reason?: string; overrideReason?: string; accountId?: string; deckVersion?: number },
) {
  const j = store().jobs.get(id);
  if (!j) return { error: "not found", code: 404 } as const;
  const snap = snapshot(j);
  // Same rules as the API: the Safeguard is binding (no Override approves a blocked Deck), a Deck without a
  // verdict can't be approved, and a blocked Deck can still be rejected.
  if (kind === "approve" && (snap.status === "BLOCKED" || snap.safeguard?.decision === "block"))
    return { error: `blocked by safeguard, which no Override can approve: ${snap.safeguard?.summary ?? ""}`, code: 409 } as const;
  if (kind === "approve" && extra.deckVersion !== snap.deck_version)
    return { error: `you approved version ${extra.deckVersion}, but the Deck is at version ${snap.deck_version} now`, code: 409 } as const;
  if (kind === "approve" && snap.safeguard_pending)
    return { error: `the safeguard is still checking version ${snap.deck_version} after an edit`, code: 409 } as const;
  if (kind === "approve" && snap.status === "READY_FOR_REVIEW" && !snap.safeguard)
    return { error: "this job has no safeguard verdict (created before the safeguard existed); regenerate it", code: 409 } as const;
  const allowed = kind === "reject" ? ["READY_FOR_REVIEW", "BLOCKED"] : ["READY_FOR_REVIEW"];
  if (!allowed.includes(snap.status)) return { error: `job is ${snap.status}, not ${allowed.join(" or ")}`, code: 409 } as const;
  // Same rule as the API: approving with blocking Issues is an Override and needs a reason.
  if (kind === "approve" && snap.issues.some((i) => i.severity === "block") && !extra.overrideReason?.trim())
    return { error: "this Deck has blocking Issues: approving it is an Override and needs a written reason", code: 422 } as const;
  // Same rule as the API: one of the Organization's live accounts, preselected when there is only one.
  if (kind === "approve") {
    const live = seedInstagramAccounts.filter((a) => a.live);
    if (extra.accountId && !live.some((a) => a.id === extra.accountId))
      return { error: "pick one of this Organization's connected Instagram accounts", code: 422 } as const;
    if (!extra.accountId && live.length > 1)
      return { error: "this Organization has several Instagram accounts: choose one (account_id)", code: 422 } as const;
    extra = { ...extra, accountId: extra.accountId ?? live[0]?.id };
  }
  j.decision = { kind, at: Date.now(), ...extra };
  return { job: snapshot(j) } as const;
}

// ---------------------------------------------------------------- deck versions (#19)

const BLOCKED_PHRASE = /tank\s?day|탱크\s?데이|sea of japan/i;

/** Same split as the API: hashtag lines at the end of the caption vs the text above them. */
function splitCaption(caption: string) {
  const lines = caption.trimEnd().split("\n");
  let tags: string[] = [];
  while (lines.length && lines.at(-1)!.trim() && !lines.at(-1)!.replace(/#[\p{L}\p{N}_]+/gu, "").trim())
    tags = [...(lines.pop()!.match(/#[\p{L}\p{N}_]+/gu) ?? []), ...tags];
  return { text: lines.join("\n").trimEnd(), tags };
}

export function listVersions(id: string): DeckVersion[] | undefined {
  const j = store().jobs.get(id);
  const snap = j && snapshot(j);
  if (!j || !snap?.deck) return undefined;
  const first = { version: 1, deck: { ...(j.override?.deck ?? SCENARIOS[j.scenario].deck), job_id: j.id }, at: j.createdAt, changes: [] };
  return [first, ...(j.versions ?? []).map((v) => ({ ...v, deck: { ...v.deck, job_id: j.id } }))]
    .map((v) => ({
      version: v.version, deck: v.deck, changes: v.changes, created_at: new Date(v.at).toISOString(), current: v.version === snap.deck_version,
      edited_by: v.version === 1 ? null : "mock-user", edited_by_email: v.version === 1 ? null : "you@example.com",
      rerendered: v.changes.flatMap((c) => (c.slide != null ? [c.slide] : [])),
    }))
    .reverse();
}

/** A save in the Deck editor, with the API's refusals: stale base, blocked phrase, overflow (a length heuristic). */
export function saveVersion(id: string, edit: Partial<DeckEdit>) {
  const j = store().jobs.get(id);
  if (!j) return { error: "job not found", code: 404 } as const;
  const snap = snapshot(j);
  if (snap.status !== "READY_FOR_REVIEW" || !snap.deck) return { error: `only a Deck in review can be edited; this one is ${snap.status}`, code: 409 } as const;
  if (edit.base_version !== snap.deck_version)
    return { error: { code: "stale", message: `Not saved: someone saved version ${snap.deck_version} while you were editing version ${edit.base_version}.` }, code: 409 } as const;
  const old = snap.deck;
  const changes: DeckChange[] = [];
  const slides = (edit.slides ?? old.slides.map((s) => ({ from_index: s.index, heading: s.heading, body: s.body }))).map((e, pos) => {
    const o = old.slides[e.from_index];
    if (o.heading !== e.heading) changes.push({ field: "slide.heading", slide: pos, before: o.heading, after: e.heading });
    if (o.body !== e.body) changes.push({ field: "slide.body", slide: pos, before: o.body, after: e.body });
    return { ...o, index: pos, heading: e.heading, body: e.body };
  });
  if (slides.some((s, i) => s.heading !== old.slides[i].heading && changes.every((c) => c.slide !== i)))
    changes.unshift({ field: "order", before: old.slides.map((s) => s.heading), after: slides.map((s) => s.heading) });
  const before = splitCaption(old.caption);
  const text = edit.caption ?? before.text;
  const tags = (edit.hashtags ?? before.tags).map((t) => (t.startsWith("#") ? t : `#${t}`));
  if (text !== before.text) changes.push({ field: "caption", before: before.text, after: text });
  if (tags.join(" ") !== before.tags.join(" ")) changes.push({ field: "hashtags", before: before.tags, after: tags });
  if (!changes.length) return { error: "nothing changed", code: 422 } as const;
  const caption = `${text}${tags.length ? `\n${tags.join(" ")}` : ""}`;
  const hit = [...slides.map((s, i) => [i, `${s.heading}\n${s.body}`] as const), [null, caption] as const].find(([, t]) => BLOCKED_PHRASE.test(t));
  if (hit) {
    const excerpt = hit[1].match(BLOCKED_PHRASE)![0];
    return {
      error: {
        code: "blocked",
        message: `Not saved: Blocked phrase "${excerpt}" can't be posted.`,
        findings: [{ category: "historical_tragedy", severity: "block", location: hit[0] == null ? "caption" : "slide", slide_index: hit[0], excerpt,
          rule: "sensitive_topics.yaml · blocked phrase", explanation: `"${excerpt}" is on the blocked-phrase list.`, fix: `Remove or reword "${excerpt}".` }],
      },
      code: 422,
    } as const;
  }
  const overflow = slides.filter((s) => s.heading.length > 70 || s.body.length > 500);
  if (overflow.length && !edit.allow_overflow)
    return {
      error: {
        code: "overflow",
        message: `Text overflows its box on ${overflow.map((s) => `slide ${s.index + 1}`).join(", ")}. Shorten it, or save anyway (it stays a blocking Issue).`,
        issues: overflow.map((s) => ({ severity: "block", category: "visual", slide_index: s.index, message: "text overflows its box" })),
      },
      code: 422,
    } as const;
  j.versions = [...(j.versions ?? []), { version: snap.deck_version! + 1, deck: { ...old, slides, caption, hashtags: tags }, at: Date.now(), changes }];
  return { job: snapshot(j) } as const;
}

export const isTerminal = (s: JobStatus) => ["READY_FOR_REVIEW", "REJECTED", "BLOCKED", "PUBLISHED", "FAILED"].includes(s);

// ---------------------------------------------------------------- dashboard & policy

export function metrics(): Metric[] {
  const jobs = listJobs();
  const published = jobs.filter((j) => j.status === "PUBLISHED").length;
  const waiting = jobs.filter((j) => j.status === "READY_FOR_REVIEW").length;
  return [
    { label: "Followers", value: "12,480", delta: "+4.2% vs last week" },
    { label: "Reach (7d)", value: "48.2K", delta: "+11.8% vs last week" },
    { label: "Avg. saves / post", value: "312", delta: "+38 vs last week" },
    { label: "Published this week", value: String(published + 4), delta: `${waiting} waiting for review` },
  ];
}

export const instagramAccounts = () => seedInstagramAccounts;
// Connecting goes through Instagram's sign-in, which only the real API can do.
export const instagramConnection = () => ({
  configured: false,
  pilot: false,
  limit: 1,
  connected: seedInstagramAccounts.length,
  can_connect: false,
  reason: "Connecting Instagram needs the real API: set NEXT_PUBLIC_API_URL and sign in.",
});

export function policyEvents(): PolicyEvent[] {
  const now = Date.now();
  const derived: { at: number; event: PolicyEvent }[] = [];
  for (const j of store().jobs.values()) {
    const sc = SCENARIOS[j.scenario];
    let start = j.createdAt;
    for (const s of stagesFor(j)) {
      if (now < start) break;
      sc.policy_events
        .filter((e) => e.stage === s.key)
        .forEach((e, i) => {
          const at = start + 400 * (i + 1);
          derived.push({
            at,
            event: {
              time: new Date(at).toLocaleTimeString("en-GB", { timeZone: "Asia/Seoul" }),
              sandbox: `agent-${j.id}`,
              binary: e.binary,
              host: e.host,
              request: e.request,
              result: e.result,
            },
          });
        });
      start += s.ms;
    }
  }
  derived.sort((a, b) => b.at - a.at);
  return [...derived.map((d) => d.event), ...seedPolicyEvents];
}

export function policyStats(): Metric[] {
  const ev = policyEvents();
  const denied = ev.filter((e) => e.result.endsWith("denied")).length;
  const publishAttempts = ev.filter((e) => e.host === "graph.facebook.com" && e.result === "policy_denied").length;
  return [
    { label: "Denied (24h)", value: String(denied), delta: `${publishAttempts} publish / delete attempts` },
    { label: "Audited (24h)", value: String(ev.filter((e) => e.result === "audit").length + 38), delta: "event_pages in audit mode" },
    { label: "Sandboxes run", value: String(store().jobs.size * 3), delta: "all discarded (--no-keep)" },
    { label: "Policy version", value: "v3", delta: "prover: no risky diff" },
  ];
}

// ---------------------------------------------------------------- brainstorms

export function createBrainstorm(sample = true): Brainstorm {
  const d: Brainstorm = sample
    ? { ...structuredClone(seedBrainstorm), id: `d-${newId()}` }
    : { id: `d-${newId()}`, messages: [], facts: [], angles: [], selected_angle_id: null, targets: [], tones: [], outline: [] };
  store().brainstorms.set(d.id, d);
  return d;
}

export const getBrainstorm = (id: string) => store().brainstorms.get(id);

export function patchBrainstorm(id: string, patch: Partial<Brainstorm>) {
  const d = store().brainstorms.get(id);
  if (!d) return undefined;
  const next = { ...d, ...patch, id: d.id, messages: d.messages };
  store().brainstorms.set(id, next);
  return next;
}

const SUSPICIOUS = /\.(xyz|top|click|zip|icu)\b|bit\.ly|tinyurl|visitkorea-/i;

/** Deterministic stand-in for the sandboxed `planner` subagent (SPEC §4-1). */
function mockPlanner(d: Brainstorm, text: string, urls: string[]): BrainstormUpdate {
  const bad = urls.filter((u) => SUSPICIOUS.test(u));
  if (bad.length) {
    return { reply: `⚠️ 링크 검증에서 막혔어요: ${bad.join(", ")} → 의심 도메인(lookalike/단축 URL)이라 사용하지 않을게요. 공식 페이지 URL을 주시면 다시 확인할게요.` };
  }
  if (urls.length) {
    const host = new URL(urls[0]).hostname;
    const facts = d.facts.length
      ? d.facts
      : [
          { key: "event" as const, value: "Event from " + host, verified: true, source_url: urls[0] },
          { key: "dates" as const, value: "dates unverified", verified: false },
        ];
    return { reply: `링크 확인했어요 ✓ (${host}, 200 OK) 페이지에서 확인된 정보만 보드에 반영했어요. 확인 안 된 항목은 노란색으로 표시돼요.`, facts };
  }
  if (/팁|tip/i.test(text)) {
    const outline = [...d.outline];
    const at = Math.max(1, outline.length - 1);
    outline.splice(at, 0, { layout: "tips", heading: "Local tips: timing, lines & payment", updated_from_chat: true });
    return { reply: "팁 슬라이드를 하나 추가했어요 (CTA 바로 앞). 순서는 화살표로 바꿀 수 있어요.", outline: outline.slice(0, 8) };
  }
  if (/앵글|angle|다른/i.test(text)) {
    return {
      reply: "앵글 후보를 새로 3개 뽑았어요.",
      angles: [
        { id: `n${newId()}`, title: "What locals actually do on a weekend night", hook: "Insider · first-timers" },
        { id: `n${newId()}`, title: "One evening, three neighborhoods", hook: "Itinerary · walkable" },
        { id: `n${newId()}`, title: "Under ₩20,000: a night out in Seoul", hook: "Budget · students" },
      ],
    };
  }
  return { reply: "메모 반영했어요. 앵글을 바꾸고 싶으면 '다른 앵글', 팁을 넣고 싶으면 '팁 추가'라고 말해 주세요. (mock planner)" };
}

export function sendMessage(id: string, text: string, urls: string[], attached: MaterialRef[] = []) {
  const d = store().brainstorms.get(id);
  if (!d) return undefined;
  const update: BrainstormUpdate = attached.length
    ? { reply: `자료 ${attached.length}개 받았어요 ✓ 이 자료를 1차 출처로 쓰고, 빠진 정보(가는 길, 외국인 가격 등)만 따로 확인할게요. (mock planner)` }
    : mockPlanner(d, text, urls);
  const known = new Set((d.material ?? []).map((m) => m.id));
  const next: Brainstorm = {
    ...d,
    material: [...(d.material ?? []), ...attached.filter((m) => !known.has(m.id))],
    messages: [...d.messages, { role: "user", text, urls, material_ids: attached.map((m) => m.id) }, { role: "agent", text: update.reply }],
    facts: update.facts ?? d.facts,
    angles: update.angles ?? d.angles,
    outline: update.outline ?? d.outline,
  };
  store().brainstorms.set(id, next);
  return next;
}

export function generateFromBrainstorm(id: string): { job: Job } | { error: string } {
  const d = store().brainstorms.get(id);
  if (!d) return { error: "brainstorm not found" };
  if (!d.selected_angle_id) return { error: "pick an angle first" };
  const angle = d.angles.find((a) => a.id === d.selected_angle_id)!;
  const factText = d.facts.filter((f) => f.verified).map((f) => f.value).join("\n");
  const job: MockJob = {
    id: newId(),
    prompt: `Brainstorm: ${angle.title}`,
    scenario: "good",
    source: "brainstorm",
    createdAt: Date.now(),
    titleOverride: angle.title,
    material: d.material ?? [],
    override: {
      briefs: d.facts.some((f) => f.source_url)
        ? [{ id: "evt-d", title_en: d.facts.find((f) => f.key === "event")?.value ?? angle.title, title_ko: "", sources: [{ url: d.facts.find((f) => f.source_url)!.source_url!, kind: "official" }] }]
        : [],
      deck: {
        title: angle.title,
        caption: `${angle.title}\n${angle.hook}\nDetails & links in bio.\n#seoul #korea #whatsonkorea`,
        slides: d.outline.map((o, i) => ({ index: i, layout: o.layout, heading: o.heading, body: i === 1 ? factText : "" })),
      },
    },
  };
  store().jobs.set(job.id, job);
  store().brainstorms.set(id, { ...d, job_id: job.id });
  return { job: snapshot(job) };
}

// ---------------------------------------------------------------- Organization profile (#16)

const EMPTY_PROFILE: OrgProfile = { what_it_does: "", audience: "", home_region: "", usual_subjects: [], never_say: [] };

export const orgProfile = (): OrgProfileState => store().profile ?? { profile: EMPTY_PROFILE, complete: false, updated_at: null };

export function saveOrgProfile(p: Partial<OrgProfile>): OrgProfileState {
  const clean = (xs: unknown) =>
    [...new Set((Array.isArray(xs) ? xs : []).map((x) => String(x).split(/\s+/).join(" ").trim()).filter(Boolean))];
  const profile: OrgProfile = {
    what_it_does: String(p.what_it_does ?? "").trim(),
    audience: String(p.audience ?? "").trim(),
    home_region: String(p.home_region ?? "").trim(),
    usual_subjects: clean(p.usual_subjects),
    never_say: clean(p.never_say),
  };
  const complete = Object.values(profile).some((v) => (Array.isArray(v) ? v.length > 0 : v !== ""));
  store().profile = { profile, complete, updated_at: new Date().toISOString() };
  return store().profile!;
}
