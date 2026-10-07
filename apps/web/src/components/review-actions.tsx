"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import type { Job } from "@/lib/types";

const STATUS_NOTE: Partial<Record<Job["status"], string>> = {
  PUBLISHING: "Approved — the host is publishing to Instagram…",
  PUBLISHED: "Published to @whatsonkorea.",
  REJECTED: "Rejected — this deck will not be posted.",
  FAILED: "The pipeline failed before review.",
};

export function ReviewActions({ job, onChange }: { job: Job; onChange: (job: Job) => void }) {
  const router = useRouter();
  const [caption, setCaption] = useState(job.deck?.caption ?? "");
  const [error, setError] = useState<string>();
  const [pending, startTransition] = useTransition();
  const canDecide = job.status === "READY_FOR_REVIEW" && !pending;

  const act = (fn: () => Promise<Job>) =>
    startTransition(async () => {
      try {
        setError(undefined);
        onChange(await fn());
      } catch (e) {
        setError((e as Error).message);
      }
    });

  return (
    <>
      <section className="space-y-2 rounded-xl border bg-card p-5">
        <label htmlFor="caption" className="text-[13px] font-medium text-muted-foreground">
          Caption (editable)
        </label>
        <Textarea id="caption" value={caption} onChange={(e) => setCaption(e.target.value)} disabled={!canDecide} className="min-h-28 text-[13px]" />
      </section>

      <div className="flex items-center gap-2.5">
        <p className={error ? "flex-1 text-xs text-destructive" : "flex-1 text-xs text-muted-foreground"}>
          {error ??
            STATUS_NOTE[job.status] ??
            "Publishing runs on the host with the IG token — the agent sandbox cannot post or delete."}
        </p>
        <Button variant="destructive" size="lg" disabled={!canDecide} onClick={() => act(() => api.rejectJob(job.id, "Rejected by operator"))}>
          Reject
        </Button>
        <Button
          variant="outline"
          size="lg"
          disabled={!canDecide}
          onClick={() =>
            startTransition(async () => {
              await api.createJob(job.prompt);
              router.push("/jobs/new"); // shows the newest job's pipeline
            })
          }
          title="Start a new job with the same request"
        >
          Regenerate
        </Button>
        <Button size="lg" disabled={!canDecide} onClick={() => act(() => api.approveJob(job.id, caption))}>
          Approve &amp; Publish
        </Button>
      </div>
    </>
  );
}
