// Data access for the Admin UI. Currently serves lib/mock.ts.
// TODO(backend): swap each function for a fetch to the FastAPI routes in SPEC §12
// (NEXT_PUBLIC_API_URL, default http://localhost:8000). With cacheComponents on, wrap
// components that read live data in <Suspense>.

import * as mock from "./mock";
import type { Draft, DraftUpdate, Job } from "./types";

export async function getMetrics() {
  return mock.metrics;
}

export async function getChannels() {
  return mock.channels;
}

export async function getJobs(): Promise<Job[]> {
  return mock.jobs;
}

export async function getJob(id: string): Promise<Job | undefined> {
  return mock.jobs.find((j) => j.id === id);
}

export async function getPipeline() {
  return { job: mock.runningJob, steps: mock.pipeline, log: mock.logLines };
}

export async function getPolicyLog() {
  return { stats: mock.policyStats, events: mock.policyEvents };
}

export async function getDraft(): Promise<Draft> {
  // TODO(backend): POST /drafts (new) or GET /drafts/{id} (resume)
  return mock.draft;
}

// Mutations — called from client components. No-ops until the backend is wired.

export async function createJob(prompt: string): Promise<{ id: string }> {
  // TODO(backend): POST /jobs {prompt}
  void prompt;
  return { id: mock.runningJob.id };
}

export async function approveJob(id: string, caption: string): Promise<void> {
  // TODO(backend): POST /jobs/{id}/approve — the host publishes; never the sandbox.
  void id;
  void caption;
}

export async function rejectJob(id: string, reason: string): Promise<void> {
  // TODO(backend): POST /jobs/{id}/reject
  void id;
  void reason;
}

export async function sendDraftMessage(draftId: string, text: string, urls: string[]): Promise<DraftUpdate> {
  // TODO(backend): POST /drafts/{id}/messages {text, urls} — host verifies & fetches URLs,
  // then the sandboxed `planner` returns a DraftUpdate (reply + board changes).
  void draftId;
  void text;
  return {
    reply: urls.length
      ? `(mock) URL ${urls.length}개를 받았어요. 백엔드가 연결되면 링크를 검증하고 페이지 내용을 보드에 반영합니다.`
      : "(mock) 메모를 받았어요. 백엔드가 연결되면 planner가 앵글과 아웃라인을 업데이트합니다.",
  };
}

export async function updateDraft(draft: Draft): Promise<void> {
  // TODO(backend): PATCH /drafts/{id}
  void draft;
}

export async function generateFromDraft(draft: Draft): Promise<{ jobId: string }> {
  // TODO(backend): POST /drafts/{id}/generate — starts the pipeline at Verify.
  void draft;
  return { jobId: mock.runningJob.id };
}
