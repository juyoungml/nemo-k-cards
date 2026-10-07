// Mirrors backend/app/schemas.py. Keep in sync when the backend contract changes.

export type JobStatus =
  | "QUEUED"
  | "RESEARCHING"
  | "VERIFYING"
  | "WRITING"
  | "RENDERING"
  | "QA"
  | "REVIEWING"
  | "READY_FOR_REVIEW"
  | "REJECTED"
  | "PUBLISHING"
  | "PUBLISHED"
  | "FAILED";

export type SourceKind = "official" | "ticketing" | "news" | "sns" | "public_api" | "other";

export interface Source {
  url: string;
  kind: SourceKind;
}

export interface EventBrief {
  id: string;
  title_en: string;
  title_ko: string;
  sources: Source[];
}

export interface LinkCheck {
  url: string;
  status: "ok" | "dead" | "redirect" | "suspicious" | "timeout";
  http_code?: number | null;
  reason?: string | null;
}

export interface VerificationReport {
  checks: LinkCheck[];
  excluded_event_ids: string[];
}

export interface Slide {
  index: number;
  layout: "cover" | "event" | "tips" | "map" | "cta";
  heading: string;
  body: string;
  event_id?: string | null;
  stock_id?: string | null;
}

export interface CardDeck {
  job_id: string;
  title: string;
  caption: string;
  slides: Slide[];
}

export interface Issue {
  severity: "block" | "warn";
  category: "fact" | "link" | "sensitive" | "pii" | "visual" | "tone";
  slide_index?: number | null;
  message: string;
}

export interface Job {
  id: string;
  prompt: string;
  status: JobStatus;
  created_at: string;
  briefs: EventBrief[];
  verification?: VerificationReport | null;
  deck?: CardDeck | null;
  issues: Issue[]; // QAReport.issues + ReviewVerdict.issues, merged for display
  published_url?: string | null;
  error?: string | null;
  slide_urls?: string[]; // rendered JPEGs, relative to API_URL (/assets/{id}/slide-NN.jpg)
  publish_progress?: PublishProgress | null; // live sub-steps after Approve & Publish
  // View extras returned by GET /jobs/{id} and the SSE stream.
  source?: "quick" | "brainstorm";
  pipeline?: PipelineStep[];
  log?: LogLine[];
}

export interface PublishProgress {
  mode: "mock" | "dryrun" | "graph";
  step: "upload" | "containers" | "processing" | "publish" | "done" | "failed";
  label: string;
  done: number;
  total: number;
  percent: number;
  started_at: string;
  finished_at?: string | null;
  error?: string | null;
}

export type Scenario = "good" | "bad" | "fail";

// Admin-only view models (no backend schema yet).

export interface Channel {
  handle: string;
  platform: string;
  followers: number;
  live: boolean; // true = IG Insights, false = seed mock
}

export interface Metric {
  label: string;
  value: string;
  delta: string;
}

export type PipelineStepState = "done" | "running" | "pending" | "failed";

export interface PipelineStep {
  name: string;
  where: string;
  state: PipelineStepState;
  note?: string;
}

export interface LogLine {
  time: string;
  stage: string;
  message: string;
  level?: "info" | "warn";
}

export interface PolicyEvent {
  time: string;
  sandbox: string;
  binary: string;
  host: string;
  request: string;
  result: "policy_denied" | "fs_denied" | "audit" | "allowed";
}

// Brainstorm mode (SPEC §4-1)

export interface Fact {
  key: "event" | "dates" | "venue" | "price" | "booking" | "other";
  value: string;
  verified: boolean;
  source_url?: string | null;
}

export interface Angle {
  id: string;
  title: string;
  hook: string;
}

export interface OutlineItem {
  layout: Slide["layout"];
  heading: string;
  updated_from_chat?: boolean;
}

export interface ChatMessage {
  role: "user" | "agent";
  text: string;
  urls?: string[];
}

export interface Draft {
  id: string;
  messages: ChatMessage[];
  facts: Fact[];
  angles: Angle[];
  selected_angle_id: string | null;
  targets: string[];
  tones: string[];
  outline: OutlineItem[];
  job_id?: string | null;
}

export interface DraftUpdate {
  reply: string;
  facts?: Fact[] | null;
  angles?: Angle[] | null;
  outline?: OutlineItem[] | null;
}
