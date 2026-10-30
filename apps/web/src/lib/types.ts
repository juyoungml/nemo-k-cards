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
  | "BLOCKED" // the Safeguard refused the Deck: nobody can approve it, only regenerate or reject
  | "SCHEDULED" // approved for a later time (#26): its Post waits; reschedule or cancel it, or edit the Deck to hold it
  | "PUBLISHING"
  | "PUBLISHED"
  | "FAILED";

export type SourceKind = "official" | "ticketing" | "news" | "sns" | "public_api" | "other";

export interface Source {
  url: string;
  kind: SourceKind;
}

// Source material (#20; GLOSSARY): the Organization's own links, files and notes. The primary source when given.
export interface MaterialRef {
  id: string;
  kind: "link" | "file" | "note";
  name: string;
  url?: string | null;
  content_type?: string | null;
  size?: number | null;
  pages?: number | null;
  chars: number;
  truncated: boolean;
  image: boolean;
}

export interface Material extends MaterialRef {
  text: string;
  created_at: string;
  created_by?: string | null;
}

/** Where one fact of an event came from (ADR 0003). `verified` is set by the host at Verify. */
export interface FactSource {
  field: string;
  value: string;
  origin: "material" | "link";
  material_id?: string | null;
  url?: string | null;
  quote?: string | null;
  verified?: boolean | null;
  note?: string | null;
}

/** Source material and a checked public page disagree; shown as an Issue for a person to decide. */
export interface Conflict {
  field: string;
  material_id: string;
  material_says: string;
  url: string;
  public_says: string;
}

export interface EventBrief {
  id: string;
  title_en: string;
  title_ko: string;
  sources: Source[];
  materials?: string[];
  facts?: FactSource[];
  conflicts?: Conflict[];
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
  hashtags?: string[];
  slides: Slide[];
}

/** One saved state of a Deck (GLOSSARY "Deck version"). Every edit makes a new one; approval pins exactly one. */
export interface DeckVersion {
  version: number;
  deck: CardDeck;
  rerendered: number[]; // slides rendered for this version; the others reuse earlier files
  changes: DeckChange[];
  edited_by: string | null; // null: version 1, the pipeline's
  edited_by_email: string | null;
  created_at: string;
  current: boolean;
}

export interface DeckChange {
  field: "order" | "slide.heading" | "slide.body" | "caption" | "hashtags";
  slide?: number;
  from_slide?: number;
  before: unknown;
  after: unknown;
}

/** Does one slide's text fit, as typed? (POST /jobs/{id}/fit, measured by the renderer after auto-fit.) */
export interface FitResult {
  position: number;
  fits: boolean;
  small_text: boolean; // some text shrank below the 30px floor
  blocks: { selector: string; lines: number; font_px: number; overflow: boolean }[];
}

/** A save in the Deck editor: every slide in its new order (`from_index` = its index in `base_version`). */
export interface DeckEdit {
  base_version: number;
  slides?: { from_index: number; heading: string; body: string }[];
  caption?: string; // without its hashtags
  hashtags?: string[];
  allow_overflow?: boolean;
}

export interface Issue {
  severity: "block" | "warn";
  category: "fact" | "link" | "sensitive" | "pii" | "visual" | "tone";
  slide_index?: number | null;
  message: string;
}

export type HarmCategory =
  | "hate"
  | "harassment"
  | "sexual"
  | "violence"
  | "self_harm"
  | "dangerous"
  | "historical_tragedy"
  | "political"
  | "territorial_cultural"
  | "scam"
  | "other";

export interface SafeguardFinding {
  category: HarmCategory;
  severity: "block" | "warn";
  location: "slide" | "caption" | "hashtags" | "image";
  slide_index?: number | null;
  excerpt: string;
  rule: string;
  explanation: string;
  fix?: string;
}

/** The Safeguard's verdict, the one binding check. "block" means nobody can approve this Deck. */
export interface SafeguardVerdict {
  decision: "allow" | "block";
  summary: string;
  findings: SafeguardFinding[];
  checked_by: ("rules" | "model")[];
  model_decision?: "allow" | "block" | null; // what the model said on its own; null if it didn't run
  model_error?: string | null;
  policy_version: string;
  content_hash: string;
  checked_at?: string | null;
  deck_version?: number | null; // the edited Deck version this verdict vouches for; null: the pipeline's
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
  safeguard?: SafeguardVerdict | null;
  error?: string | null;
  slide_urls?: string[]; // rendered JPEGs: short-lived signed links relative to API_URL (/assets/{org}/{id}/slide-NN.jpg?exp=…&sig=…)
  // Publishing lives on Posts (#21): GET /posts?job_id=… (the Job no longer carries published_url / publish_progress).
  // View extras returned by GET /jobs/{id} and the SSE stream.
  source?: "quick" | "brainstorm";
  pipeline?: PipelineStep[];
  log?: LogLine[];
  material?: MaterialRef[]; // Source material attached when the Job started (or carried over from its Brainstorm)
  slide_sources?: SlideSources[]; // where each slide's facts came from (#25); GET /jobs/{id} and SSE only, not lists
  deck_version?: number; // the version `deck` and `slide_urls` show (1 when absent)
  safeguard_pending?: boolean; // the safeguard is checking an edited version: it can't be approved yet
}

export type LinkStatus = LinkCheck["status"] | "unchecked";

/** One source behind a fact on a slide: a link with the host's check, or the Organization's own Source material. */
export interface SlideFactSource {
  origin: "material" | "link";
  url?: string | null;
  status?: LinkStatus | null;
  http_code?: number | null;
  reason?: string | null;
  material_id?: string | null;
  material_name?: string | null; // only for this Job's own material
  quote?: string | null;
  verified?: boolean | null; // the host's verdict at Verify; null when no per-fact source was recorded
  note?: string | null;
}

/** A fact a slide shows (event and map slides are filled from the event's brief) and its sources. */
export interface SlideFact {
  field: string;
  value: string;
  recorded: boolean; // false: no per-fact source was recorded, these are the event's own sources
  sources: SlideFactSource[];
  edited?: boolean; // a person typed this in a Deck edit (#19): re-check its sources
}

/** Where the facts on one slide came from (#25). Computed by the API on the way out. */
export interface SlideSources {
  slide_index: number;
  layout: Slide["layout"];
  event_id?: string | null;
  event_title?: string | null;
  facts: SlideFact[];
  links: SlideFactSource[]; // links attached to the slide itself
  unsourced: string[]; // claims in the slide's text that no brief supports
  general: boolean; // tips: general know-how
  edited?: ("heading" | "body")[]; // text a person changed in a Deck edit (#19): its sources need re-checking
  note?: string | null;
}

export interface PostProgress {
  step: "upload" | "containers" | "processing" | "publish" | "done" | "failed";
  label: string;
  done: number;
  total: number;
  percent: number;
}

/** `scheduled` waits for its time; `on_hold` waits for a person (the Deck was edited, or the time was missed while the
 * server was down); `cancelled` posted nothing (#26). */
export type PostState =
  | "scheduled"
  | "on_hold"
  | "queued"
  | "container_ready"
  | "publishing"
  | "published"
  | "failed"
  | "unknown"
  | "cancelled";
export type PublishMode = "mock" | "dryrun" | "graph";

/**
 * One publication of an approved Deck to one Instagram account (GLOSSARY "Post", #21). Only `published` and
 * `failed` are final for a real post; `unknown` means Instagram's answer didn't prove the outcome and the
 * reconciler is checking. A dry run ends at `container_ready` with `finished_at` set (nothing posted).
 */
export interface Post {
  id: string;
  job_id: string;
  deck_version?: number | null;
  instagram_account_id?: string | null;
  handle?: string | null; // "@username"; null for a mock post without an account
  mode: PublishMode;
  state: PostState;
  caption?: string | null;
  creation_id?: string | null;
  media_id?: string | null;
  permalink?: string | null;
  attempts: number;
  last_error?: string | null;
  progress?: PostProgress | null;
  approved_by_email?: string | null;
  created_at: string;
  updated_at: string;
  publishing_started_at?: string | null;
  unknown_since?: string | null;
  next_check_at?: string | null; // unknown: the reconciler's next look; null = a person decides
  alerted_at?: string | null; // unknown for 30 minutes: a person was alerted
  published_at?: string | null;
  finished_at?: string | null;
  resolved_by?: string | null;
  scheduled_at?: string | null; // a Timed Post (#26): when it posts; null = posted at approval
  held_reason?: string | null; // on_hold: why, in words
  held_at?: string | null;
  cancelled_by?: string | null;
  legacy?: boolean;
}

export type Scenario = "good" | "bad" | "fail";

// Admin-only view models (no backend schema yet).

/** An Instagram account the Organization connected (GLOSSARY). The API never sends its token. */
export interface InstagramAccount {
  id: string;
  handle: string; // "@username"
  platform: "Instagram";
  account_type: "BUSINESS" | "MEDIA_CREATOR";
  status: "connected" | "expiring" | "revoked";
  token_expires_at: string | null;
  connected_at: string;
  followers: number | null; // not read per Organization yet
  live: boolean; // connected and usable
}

/** Whether this Organization can connect another Instagram account, and if not, why. */
export interface InstagramConnection {
  configured: boolean;
  pilot: boolean;
  limit: number;
  connected: number;
  can_connect: boolean;
  reason: string | null;
}

export interface Metric {
  label: string;
  value: string;
  delta: string;
}

export type PipelineStepState = "done" | "running" | "pending" | "failed" | "skipped";

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
  material_id?: string | null; // taken from this Source material (counts as verified)
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
  material_ids?: string[];
}

export interface Brainstorm {
  id: string;
  messages: ChatMessage[];
  facts: Fact[];
  angles: Angle[];
  selected_angle_id: string | null;
  targets: string[];
  tones: string[];
  outline: OutlineItem[];
  job_id?: string | null;
  material?: MaterialRef[];
}

export interface BrainstormUpdate {
  reply: string;
  facts?: Fact[] | null;
  angles?: Angle[] | null;
  outline?: OutlineItem[] | null;
}

// ---------------------------------------------------------------- sign-in (backend/app/orgs.py)

export type Role = "viewer" | "editor" | "approver" | "admin";

export interface Org {
  id: string;
  name: string;
  role: Role;
}

export interface Me {
  user: { id: string; email: string };
  org: Org | null; // the Organization this session acts for; null until one is created
  orgs: Org[];
}

export interface Member {
  user_id: string;
  email: string | null;
  role: Role;
  invited_by: string | null;
  joined_at: string;
}

export interface Invitation {
  id: string;
  email: string;
  role: Role;
  invited_by: string;
  created_at: string;
  expires_at: string;
}

export interface InvitationCreated extends Invitation {
  invite_url: string; // works only for the invited email address
  email_sent: boolean;
}

export interface AuditEvent {
  id: number;
  actor: string;
  actor_email: string | null;
  action: string;
  target: string | null;
  detail: Record<string, unknown>;
  at: string;
}
