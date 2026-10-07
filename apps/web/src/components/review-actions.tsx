"use client";

import { useState, useTransition } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { approveJob, rejectJob } from "@/lib/api";
import type { JobStatus } from "@/lib/types";

export function ReviewActions({ jobId, status, initialCaption }: { jobId: string; status: JobStatus; initialCaption: string }) {
  const [caption, setCaption] = useState(initialCaption);
  const [result, setResult] = useState<"approved" | "rejected" | null>(null);
  const [pending, startTransition] = useTransition();
  const canDecide = status === "READY_FOR_REVIEW" && !result && !pending;

  return (
    <>
      <section className="space-y-2 rounded-xl border bg-card p-5">
        <label htmlFor="caption" className="text-[13px] font-medium text-muted-foreground">
          Caption (editable)
        </label>
        <Textarea
          id="caption"
          value={caption}
          onChange={(e) => setCaption(e.target.value)}
          disabled={!canDecide}
          className="min-h-28 text-[13px]"
        />
      </section>

      <div className="flex items-center gap-2.5">
        <p className="flex-1 text-xs text-muted-foreground">
          {result === "approved"
            ? "Approved — the host is publishing to Instagram."
            : result === "rejected"
              ? "Rejected."
              : "Publishing runs on the host with the IG token — the agent sandbox cannot post or delete."}
        </p>
        <Button
          variant="destructive"
          size="lg"
          disabled={!canDecide}
          onClick={() => startTransition(async () => { await rejectJob(jobId, ""); setResult("rejected"); })}
        >
          Reject
        </Button>
        {/* TODO(backend): Regenerate = POST /jobs with the same prompt */}
        <Button variant="outline" size="lg" disabled={!canDecide}>
          Regenerate
        </Button>
        <Button
          size="lg"
          disabled={!canDecide}
          onClick={() => startTransition(async () => { await approveJob(jobId, caption); setResult("approved"); })}
        >
          Approve &amp; Publish
        </Button>
      </div>
    </>
  );
}
