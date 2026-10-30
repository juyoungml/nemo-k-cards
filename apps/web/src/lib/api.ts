// HTTP client for the backend contract in SPEC §12. Used from client components.
// NEXT_PUBLIC_API_URL unset → the in-app mock backend at /api/mock (src/mock/server.ts).
// Set it to the FastAPI URL (e.g. http://localhost:8000) to run against the real backend.

import { EventSource } from "eventsource";

import { orgHeader, setActiveOrgId } from "./org";
import { accessToken, isAuthPath, reloadTo } from "./supabase";
import type {
  AuditEvent, Brainstorm, DeckEdit, DeckVersion, FitResult, Invitation, InvitationCreated, InstagramAccount, InstagramConnection, Job,
  Material, MaterialRef, Me, Member, Metric, Org, OrgProfile, OrgProfileState, PolicyEvent, Post, PublishMode, Role, Scenario,
} from "./types";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "/api/mock";
export const IS_MOCK = !process.env.NEXT_PUBLIC_API_URL;

/** Absolute URL for a slide link from the API (/assets/{org}/{job}/slide-00.jpg?exp=…&sig=…). The link is signed
 * and expires in about 15 minutes; the review page polls the Job, so it always holds fresh links. */
export const assetUrl = (path: string) => (path.startsWith("http") ? path : `${API_URL}${path}`);

/** Where the Admin sends someone the API turned away: signed out → /sign-in, no Organization yet → /welcome. */
function leaveFor(status: number, detail: unknown) {
  if (typeof window === "undefined" || IS_MOCK) return;
  const here = window.location.pathname;
  if (isAuthPath(here) || here === "/welcome") return;
  if (status === 401) reloadTo(`/sign-in?next=${encodeURIComponent(here + window.location.search)}`);
  else if (status === 403 && typeof detail === "string" && detail.startsWith("no Organization")) reloadTo("/welcome");
  else if (status === 403 && detail === "not a member of this Organization") {
    // The Organization this browser had chosen is no longer one of theirs: fall back to their default.
    setActiveOrgId(null);
    reloadTo("/");
  }
}
export const UNREACHABLE = "Can't reach the backend";

/** An error answer from the API. `detail` is what it sent: a string, or an object such as a Deck edit's
 * `{ code: "overflow" | "blocked" | "stale", message, issues | findings }`. */
export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: unknown,
  ) {
    const text =
      typeof detail === "string"
        ? detail
        : detail && typeof detail === "object" && "message" in detail
          ? String((detail as { message: unknown }).message)
          : JSON.stringify(detail);
    super(`${status} ${text}`);
  }
}

async function req<T>(path: string, init?: RequestInit & { json?: unknown }): Promise<T> {
  const token = IS_MOCK ? "" : await accessToken();
  const headers: Record<string, string> = IS_MOCK ? {} : orgHeader();
  if (init?.json !== undefined) headers["content-type"] = "application/json";
  if (token) headers.authorization = `Bearer ${token}`;
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      ...init,
      cache: "no-store",
      headers,
      body: init?.json !== undefined ? JSON.stringify(init.json) : init?.body,
    });
  } catch {
    // fetch only throws on network failure (server down, CORS, offline), never on HTTP errors.
    throw new Error(`${UNREACHABLE} at ${API_URL}. Is the API running?`);
  }
  if (res.status === 204) return undefined as T;
  if (!res.ok) {
    const detail = await res.json().then((b) => b.detail).catch(() => res.statusText);
    leaveFor(res.status, detail);
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  listJobs: () => req<Job[]>("/jobs"),
  getJob: (id: string) => req<Job>(`/jobs/${id}`),
  // `scenario` is honored by the mock backend only (QA); FastAPI ignores it.
  createJob: (prompt: string, scenario?: Scenario, materialIds: string[] = []) =>
    req<Job>("/jobs", { method: "POST", json: { prompt, scenario, material_ids: materialIds } }),
  /** Approve → a Post to the chosen Instagram account (#21) of exactly the Deck version the Approver saw (#19); the
   * API refuses it if the Deck has been edited since. Its outcome is on the Post, not this response. */
  approveJob: (id: string, deckVersion: number, overrideReason?: string, accountId?: string) =>
    req<Job>(`/jobs/${id}/approve`, {
      method: "POST",
      json: { deck_version: deckVersion, override_reason: overrideReason || undefined, account_id: accountId || undefined },
    }),
  listPosts: (jobId?: string) => req<Post[]>(`/posts${jobId ? `?job_id=${encodeURIComponent(jobId)}` : ""}`),
  getPost: (id: string) => req<Post>(`/posts/${id}`),
  /** For an unknown Post: "I checked Instagram: it is posted / not posted" (logged under your name). */
  resolvePost: (id: string, posted: boolean, permalink?: string) =>
    req<Post>(`/posts/${id}/resolve`, { method: "POST", json: { posted, permalink: permalink || undefined } }),
  /** Public server facts: which publish mode Approve uses (mock, dryrun, graph). */
  health: () => req<{ ok: boolean; publish_mode: PublishMode; demo_mode: string }>("/health"),
  // Deck versions (#19): the history, a save that makes the next version, and the fit check while typing.
  getVersions: (id: string) => req<DeckVersion[]>(`/jobs/${id}/versions`),
  saveVersion: (id: string, edit: DeckEdit) => req<Job>(`/jobs/${id}/versions`, { method: "POST", json: edit }),
  fitCheck: (id: string, edit: Required<Pick<DeckEdit, "base_version" | "slides">>, position: number) =>
    req<FitResult>(`/jobs/${id}/fit`, { method: "POST", json: { ...edit, position } }),
  rejectJob: (id: string, reason: string) => req<Job>(`/jobs/${id}/reject`, { method: "POST", json: { reason } }),
  /**
   * Live Job snapshots (SSE). Read with fetch rather than the browser's EventSource so the session token
   * travels in the Authorization header, never in a URL that proxies and tunnels log. Each (re)connect asks
   * for a fresh token; the API closes the stream when the token it was opened with expires.
   */
  openJobEvents: (id: string) =>
    new EventSource(new URL(`${API_URL}/jobs/${id}/events`, window.location.href), {
      fetch: async (input, init) => {
        const token = IS_MOCK ? "" : await accessToken();
        const auth = token ? { authorization: `Bearer ${token}`, ...orgHeader() } : {};
        return fetch(input, { ...init, headers: { ...init.headers, ...auth } });
      },
    }),

  me: () => req<Me>("/me"),
  createOrg: (name: string) => req<Org>("/orgs", { method: "POST", json: { name } }),
  acceptInvitation: (token: string) => req<Org>("/invitations/accept", { method: "POST", json: { token } }),

  // Members of the current Organization (listing: any Member; the rest: Admins)
  listMembers: () => req<Member[]>("/orgs/current/members"),
  invite: (email: string, role: Role) => req<InvitationCreated>("/orgs/current/members", { method: "POST", json: { email, role } }),
  changeRole: (userId: string, role: Role) => req<Member>(`/orgs/current/members/${userId}`, { method: "PATCH", json: { role } }),
  removeMember: (userId: string) => req<void>(`/orgs/current/members/${userId}`, { method: "DELETE" }),
  listInvitations: () => req<Invitation[]>("/orgs/current/invitations"),
  revokeInvitation: (id: string) => req<void>(`/orgs/current/invitations/${id}`, { method: "DELETE" }),
  // Organization profile (#16): any Member reads it, Admins save it. Every new Job takes a snapshot of it.
  getOrgProfile: () => req<OrgProfileState>("/orgs/current/profile"),
  saveOrgProfile: (profile: OrgProfile) => req<OrgProfileState>("/orgs/current/profile", { method: "PUT", json: profile }),
  getAudit: (before?: number) => req<AuditEvent[]>(`/audit?limit=100${before ? `&before=${before}` : ""}`),

  getMetrics: () => req<Metric[]>("/metrics"),
  // Instagram accounts of the current Organization (listing: any Member; connect and disconnect: Admins).
  // Connecting returns Instagram's authorization URL; the browser goes there and Instagram sends it back through
  // the API's callback to /instagram?connected=… or ?error=….
  getInstagramAccounts: () => req<InstagramAccount[]>("/instagram-accounts"),
  getInstagramConnection: () => req<InstagramConnection>("/instagram-accounts/connection"),
  connectInstagram: () => req<{ authorize_url: string }>("/instagram-accounts/connect", { method: "POST" }),
  disconnectInstagram: (id: string) => req<void>(`/instagram-accounts/${id}`, { method: "DELETE" }),
  getPolicyLog: () => req<{ stats: Metric[]; events: PolicyEvent[] }>("/policy-events"),

  createBrainstorm: (sample = true) => req<Brainstorm>("/brainstorms", { method: "POST", json: { sample } }),
  getBrainstorm: (id: string) => req<Brainstorm>(`/brainstorms/${id}`),
  patchBrainstorm: (id: string, patch: Partial<Brainstorm>) => req<Brainstorm>(`/brainstorms/${id}`, { method: "PATCH", json: patch }),
  sendBrainstormMessage: (id: string, text: string, urls: string[], materialIds: string[] = []) =>
    req<Brainstorm>(`/brainstorms/${id}/messages`, { method: "POST", json: { text, urls, material_ids: materialIds } }),
  generateFromBrainstorm: (id: string) => req<Job>(`/brainstorms/${id}/generate`, { method: "POST" }),

  // Source material (#20): attach first, then pass the ids with the Job or the Brainstorm message.
  addMaterialLink: (url: string) => req<MaterialRef>("/materials", { method: "POST", json: { kind: "link", url } }),
  addMaterialNote: (text: string) => req<MaterialRef>("/materials", { method: "POST", json: { kind: "note", text } }),
  uploadMaterial: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return req<MaterialRef>("/materials/files", { method: "POST", body: form });
  },
  getMaterial: (id: string) => req<Material>(`/materials/${id}`),

  resetMock: () => req<{ ok: true }>("/reset", { method: "POST" }),
};
