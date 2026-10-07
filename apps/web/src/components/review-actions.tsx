"use client";

import { Loader2 } from "lucide-react";
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
  const publishing = job.status === "PUBLISHING" || (pending && !error);
  const progress = job.publish_progress;
  const percent = progress && progress.step !== "done" && progress.step !== "failed" ? progress.percent : publishing ? 2 : 0;
  const blocks = job.issues.filter((i) => i.severity === "block").length;

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

      {/* Decision bar: sticks to the bottom of the viewport so the action and its feedback stay where the user is. */}
      <div className="sticky bottom-4 z-10 rounded-xl border bg-card/95 p-4 shadow-[0_8px_24px_-12px_rgba(0,0,0,0.25)] backdrop-blur supports-[backdrop-filter]:bg-card/80">
        {publishing ? (
          <div className="flex items-center gap-4" aria-live="polite">
            <Loader2 className="size-5 shrink-0 text-primary motion-safe:animate-spin" />
            <div className="min-w-0 flex-1 space-y-1.5">
              <div className="flex text-[13px]">
                <span className="flex-1 truncate font-medium">{progress?.label ?? "Approved, handing off to the publisher…"}</span>
                <span className="text-muted-foreground tabular-nums">{percent}%</span>
              </div>
              <div role="progressbar" aria-label="Publish progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent} className="h-1.5 overflow-hidden rounded-full bg-muted">
                <div className="h-full rounded-full bg-primary transition-[width] duration-500 ease-out" style={{ width: `${percent}%` }} />
              </div>
            </div>
            <a href="#publish-progress" className="shrink-0 text-xs font-medium text-primary hover:underline">
              Details
            </a>
          </div>
        ) : (
          <div className="flex items-center gap-2.5">
            <p className={error ? "flex-1 text-xs text-destructive" : "flex-1 text-xs text-muted-foreground"}>
              {error ??
                STATUS_NOTE[job.status] ??
                (blocks > 0
                  ? `${blocks} blocking issue${blocks > 1 ? "s" : ""} flagged. Approving publishes anyway; you are the final check.`
                  : "Publishing runs on the host with the IG token — the agent sandbox cannot post or delete.")}
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
            <Button
              size="lg"
              disabled={!canDecide}
              onClick={() => {
                act(() => api.approveJob(job.id, caption));
                document.getElementById("publish-progress")?.scrollIntoView({ behavior: "smooth", block: "center" });
              }}
            >
              {blocks > 0 ? "Approve anyway & Publish" : "Approve & Publish"}
            </Button>
          </div>
        )}
      </div>
    </>
  );
}
