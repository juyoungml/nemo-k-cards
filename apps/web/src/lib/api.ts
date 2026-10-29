// HTTP client for the backend contract in SPEC §12. Used from client components.
// NEXT_PUBLIC_API_URL unset → the in-app mock backend at /api/mock (src/mock/server.ts).
// Set it to the FastAPI URL (e.g. http://localhost:8000) to run against the real backend.

import { EventSource } from "eventsource";

import { orgHeader, setActiveOrgId } from "./org";
import { accessToken, isAuthPath, reloadTo } from "./supabase";
import type {
  AuditEvent, Brainstorm, Invitation, InvitationCreated, InstagramAccount, Job, Me, Member, Metric, Org, PolicyEvent, Role, Scenario,
} from "./types";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "/api/mock";
export const IS_MOCK = !process.env.NEXT_PUBLIC_API_URL;

/** Absolute URL for a backend asset path such as /assets/{job}/slide-00.jpg. */
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
    throw new Error(`${res.status} ${detail}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  listJobs: () => req<Job[]>("/jobs"),
  getJob: (id: string) => req<Job>(`/jobs/${id}`),
  // `scenario` is honored by the mock backend only (QA); FastAPI ignores it.
  createJob: (prompt: string, scenario?: Scenario) => req<Job>("/jobs", { method: "POST", json: { prompt, scenario } }),
  approveJob: (id: string, caption: string, overrideReason?: string) =>
    req<Job>(`/jobs/${id}/approve`, { method: "POST", json: { caption, override_reason: overrideReason || undefined } }),
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
  getAudit: (before?: number) => req<AuditEvent[]>(`/audit?limit=100${before ? `&before=${before}` : ""}`),

  getMetrics: () => req<Metric[]>("/metrics"),
  getInstagramAccounts: () => req<InstagramAccount[]>("/instagram-accounts"),
  getPolicyLog: () => req<{ stats: Metric[]; events: PolicyEvent[] }>("/policy-events"),

  createBrainstorm: (sample = true) => req<Brainstorm>("/brainstorms", { method: "POST", json: { sample } }),
  getBrainstorm: (id: string) => req<Brainstorm>(`/brainstorms/${id}`),
  patchBrainstorm: (id: string, patch: Partial<Brainstorm>) => req<Brainstorm>(`/brainstorms/${id}`, { method: "PATCH", json: patch }),
  sendBrainstormMessage: (id: string, text: string, urls: string[]) =>
    req<Brainstorm>(`/brainstorms/${id}/messages`, { method: "POST", json: { text, urls } }),
  generateFromBrainstorm: (id: string) => req<Job>(`/brainstorms/${id}/generate`, { method: "POST" }),

  resetMock: () => req<{ ok: true }>("/reset", { method: "POST" }),
};
