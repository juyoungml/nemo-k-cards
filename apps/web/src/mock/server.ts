// In-memory mock backend for QA before FastAPI exists. Implements the SPEC §12 contract
// behind /api/mock/*. Pipeline progress is derived from elapsed time (no timers), so state
// survives hot reloads and is deterministic per job.

import bad from "./scenarios/bad.json";
import fail from "./scenarios/fail.json";
import good from "./scenarios/good.json";
import { seedChannels, seedDraft, seedPolicyEvents } from "./seed";
import type {
  CardDeck,
  Draft,
  DraftUpdate,
  EventBrief,
  Issue,
  Job,
  JobStatus,
  LogLine,
  Metric,
  PipelineStep,
  PolicyEvent,
  Scenario,
  VerificationReport,
} from "@/lib/types";

interface ScenarioData {
  description: string;
  prompt: string;
  briefs: EventBrief[];
  verification: VerificationReport;
  deck: Omit<CardDeck, "job_id">;
  issues: Issue[];
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
  decision?: { kind: "approve" | "reject"; at: number; caption?: string; reason?: string };
}

interface Store {
  jobs: Map<string, MockJob>;
  drafts: Map<string, Draft>;
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
  return { jobs: new Map(jobs.map((j) => [j.id, j])), drafts: new Map() };
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
  if (finished) status = sc.issues.some((i) => i.severity === "block") ? "REJECTED" : "READY_FOR_REVIEW";

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
      state: (i < currentIdx ? "done" : i === currentIdx ? (failed ? "failed" : "running") : "pending") as PipelineStep["state"],
      note: i < currentIdx || (i === currentIdx && failed) ? sc.notes[s.key] : undefined,
    })),
    {
      name: "Human publish",
      where: "you",
      state: status === "PUBLISHED" ? "done" : status === "PUBLISHING" ? "running" : "pending",
      note: status === "PUBLISHED" ? "posted to @whatsonkorea" : "Approve in Review",
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
  if (job.source === "brainstorm") log.unshift({ time: fmt(job.createdAt), stage: "draft", message: "Generated from Brainstorm draft — Research skipped" });

  const briefs = job.override?.briefs ?? sc.briefs;
  const deckSrc = job.override?.deck ?? sc.deck;
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
    error: failed ? sc.error : job.decision?.reason ?? null,
    published_url: status === "PUBLISHED" ? `https://www.instagram.com/p/MOCK${job.id.toUpperCase()}/` : null,
    pipeline,
    log,
  };
}

export const listJobs = () =>
  [...store().jobs.values()].sort((a, b) => b.createdAt - a.createdAt).map((j) => snapshot(j));

export const getJob = (id: string) => {
  const j = store().jobs.get(id);
  return j && snapshot(j);
};

export function createJob(prompt: string, scenario: Scenario = "good"): Job {
  const job: MockJob = { id: newId(), prompt, scenario, source: "quick", createdAt: Date.now() };
  store().jobs.set(job.id, job);
  return snapshot(job);
}

export function decide(id: string, kind: "approve" | "reject", extra: { caption?: string; reason?: string }) {
  const j = store().jobs.get(id);
  if (!j) return { error: "not found", code: 404 } as const;
  const snap = snapshot(j);
  if (snap.status !== "READY_FOR_REVIEW") return { error: `job is ${snap.status}, not READY_FOR_REVIEW`, code: 409 } as const;
  j.decision = { kind, at: Date.now(), ...extra };
  return { job: snapshot(j) } as const;
}

export const isTerminal = (s: JobStatus) => ["READY_FOR_REVIEW", "REJECTED", "PUBLISHED", "FAILED"].includes(s);

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

export const channels = () => seedChannels;

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

// ---------------------------------------------------------------- drafts (Brainstorm)

export function createDraft(sample = true): Draft {
  const d: Draft = sample
    ? { ...structuredClone(seedDraft), id: `d-${newId()}` }
    : { id: `d-${newId()}`, messages: [], facts: [], angles: [], selected_angle_id: null, targets: [], tones: [], outline: [] };
  store().drafts.set(d.id, d);
  return d;
}

export const getDraft = (id: string) => store().drafts.get(id);

export function patchDraft(id: string, patch: Partial<Draft>) {
  const d = store().drafts.get(id);
  if (!d) return undefined;
  const next = { ...d, ...patch, id: d.id, messages: d.messages };
  store().drafts.set(id, next);
  return next;
}

const SUSPICIOUS = /\.(xyz|top|click|zip|icu)\b|bit\.ly|tinyurl|visitkorea-/i;

/** Deterministic stand-in for the sandboxed `planner` subagent (SPEC §4-1). */
function mockPlanner(d: Draft, text: string, urls: string[]): DraftUpdate {
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

export function sendMessage(id: string, text: string, urls: string[]) {
  const d = store().drafts.get(id);
  if (!d) return undefined;
  const update = mockPlanner(d, text, urls);
  const next: Draft = {
    ...d,
    messages: [...d.messages, { role: "user", text, urls }, { role: "agent", text: update.reply }],
    facts: update.facts ?? d.facts,
    angles: update.angles ?? d.angles,
    outline: update.outline ?? d.outline,
  };
  store().drafts.set(id, next);
  return next;
}

export function generateFromDraft(id: string): { job: Job } | { error: string } {
  const d = store().drafts.get(id);
  if (!d) return { error: "draft not found" };
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
  store().drafts.set(id, { ...d, job_id: job.id });
  return { job: snapshot(job) };
}
