// HTTP client for the backend contract in SPEC §12. Used from client components.
// NEXT_PUBLIC_API_URL unset → the in-app mock backend at /api/mock (src/mock/server.ts).
// Set it to the FastAPI URL (e.g. http://localhost:8000) to run against the real backend.

import type { Channel, Draft, Job, Metric, PolicyEvent, Scenario } from "./types";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "/api/mock";
export const IS_MOCK = !process.env.NEXT_PUBLIC_API_URL;

/** Absolute URL for a backend asset path such as /assets/{job}/slide-00.jpg. */
export const assetUrl = (path: string) => (path.startsWith("http") ? path : `${API_URL}${path}`);

async function req<T>(path: string, init?: RequestInit & { json?: unknown }): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    cache: "no-store",
    headers: init?.json !== undefined ? { "content-type": "application/json" } : undefined,
    body: init?.json !== undefined ? JSON.stringify(init.json) : init?.body,
  });
  if (!res.ok) {
    const detail = await res.json().then((b) => b.detail).catch(() => res.statusText);
    throw new Error(`${res.status} ${detail}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  listJobs: () => req<Job[]>("/jobs"),
  getJob: (id: string) => req<Job>(`/jobs/${id}`),
  // `scenario` picks a QA scenario in the mock backend. FastAPI honors it in DEMO_MODE=fixture, and in live mode
  // only "injection" (the researcher reads the demo injection page, then an injection drill stage runs).
  createJob: (prompt: string, scenario?: Scenario | "injection") =>
    req<Job>("/jobs", { method: "POST", json: { prompt, scenario } }),
  approveJob: (id: string, caption: string) => req<Job>(`/jobs/${id}/approve`, { method: "POST", json: { caption } }),
  rejectJob: (id: string, reason: string) => req<Job>(`/jobs/${id}/reject`, { method: "POST", json: { reason } }),
  jobEventsUrl: (id: string) => `${API_URL}/jobs/${id}/events`,

  getMetrics: () => req<Metric[]>("/metrics"),
  getChannels: () => req<Channel[]>("/channels"),
  getPolicyLog: () => req<{ stats: Metric[]; events: PolicyEvent[] }>("/policy-events"),

  createDraft: (sample = true) => req<Draft>("/drafts", { method: "POST", json: { sample } }),
  getDraft: (id: string) => req<Draft>(`/drafts/${id}`),
  patchDraft: (id: string, patch: Partial<Draft>) => req<Draft>(`/drafts/${id}`, { method: "PATCH", json: patch }),
  sendDraftMessage: (id: string, text: string, urls: string[]) =>
    req<Draft>(`/drafts/${id}/messages`, { method: "POST", json: { text, urls } }),
  generateFromDraft: (id: string) => req<Job>(`/drafts/${id}/generate`, { method: "POST" }),

  resetMock: () => req<{ ok: true }>("/reset", { method: "POST" }),
};
