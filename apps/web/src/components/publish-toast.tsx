"use client";

import { AlertCircle, CheckCircle2, ExternalLink, Info, Loader2, X } from "lucide-react";
import { useState } from "react";

import type { Job } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * Publish feedback (design pick C): a thin progress line across the top of the viewport plus a toast
 * in the bottom-right. Neither blocks the page; the toast ends with the next action or the error.
 */
export function PublishToast({ job }: { job: Job }) {
  const p = job.publish_progress;
  const publishing = job.status === "PUBLISHING";
  const [dismissed, setDismissed] = useState<string | null>(null);
  const key = p?.finished_at ?? p?.started_at ?? null;
  if (!publishing && (!p || !p.finished_at)) return null;
  if (!publishing && dismissed === key) return null;

  const percent = publishing ? (p?.percent ?? 2) : 100;
  const failed = p?.step === "failed";
  const dryrun = p?.mode === "dryrun";
  const mock = p?.mode === "mock";
  const done = !publishing && !failed;

  const title = publishing
    ? "Publishing to @whatsonkorea"
    : failed
      ? "Publish failed"
      : dryrun
        ? "Dry run complete"
        : mock
          ? "Mock publish complete"
          : "Published to @whatsonkorea";
  const detail = publishing
    ? (p?.label ?? "Handing off to the host publisher…")
    : failed
      ? `${p?.error ?? "Something went wrong."} Nothing was posted.`
      : dryrun
        ? "Instagram prepared the carousel. Nothing was posted."
        : mock
          ? "No network call was made."
          : "Your carousel is live.";

  return (
    <>
      {publishing && (
        <div className="fixed inset-x-0 top-0 z-50 h-[3px] bg-transparent" role="progressbar" aria-label="Publish progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}>
          <div className="h-full bg-primary transition-[width] duration-500 ease-out" style={{ width: `${percent}%` }} />
        </div>
      )}
      <div
        role="status"
        aria-live="polite"
        className={cn(
          "fixed right-6 bottom-28 z-50 w-[360px] rounded-xl border p-4 shadow-[0_16px_40px_-16px_rgba(0,0,0,0.45)] motion-safe:animate-in motion-safe:slide-in-from-bottom-2",
          failed ? "border-destructive/40 bg-card" : "bg-card",
        )}
      >
        <div className="flex items-start gap-3">
          {publishing && <Loader2 className="mt-0.5 size-5 shrink-0 text-primary motion-safe:animate-spin" />}
          {done && !dryrun && <CheckCircle2 className="mt-0.5 size-5 shrink-0 text-success" />}
          {done && dryrun && <Info className="mt-0.5 size-5 shrink-0 text-info" />}
          {failed && <AlertCircle className="mt-0.5 size-5 shrink-0 text-destructive" />}
          <div className="min-w-0 flex-1 space-y-1">
            <p className="text-sm font-semibold">{title}</p>
            <p className={cn("text-xs", failed ? "text-destructive" : "text-muted-foreground")}>{detail}</p>
          </div>
          {publishing ? (
            <span className="text-xs text-muted-foreground tabular-nums">{percent}%</span>
          ) : (
            <button type="button" aria-label="Dismiss" onClick={() => setDismissed(key)} className="rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground">
              <X className="size-4" />
            </button>
          )}
        </div>
        {publishing && (
          <div className="mt-3 h-1 overflow-hidden rounded-full bg-muted">
            <div className="h-full rounded-full bg-primary transition-[width] duration-500 ease-out" style={{ width: `${percent}%` }} />
          </div>
        )}
        <div className="mt-3 flex items-center gap-3 text-xs">
          <a href="#publish-progress" className="font-medium text-muted-foreground hover:text-foreground hover:underline">
            Details
          </a>
          {done && job.published_url && !dryrun && !mock && (
            <a href={job.published_url} target="_blank" rel="noreferrer" className="ml-auto inline-flex h-8 items-center gap-1.5 rounded-lg bg-primary px-3 font-medium text-primary-foreground hover:bg-primary/90">
              View on Instagram <ExternalLink className="size-3.5" />
            </a>
          )}
        </div>
      </div>
    </>
  );
}
