"use client";

import { AlertCircle, CheckCircle2, ExternalLink, HelpCircle, Info, Loader2, X } from "lucide-react";
import { useState } from "react";

import { isDryRunDone } from "@/components/publish-progress";
import type { Post } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * Publish feedback (design pick C): a thin progress line across the top of the viewport plus a toast
 * in the bottom-right, for the latest Post. Neither blocks the page; the toast ends with the next action or the
 * honest outcome ("Nothing was posted" only when the Post failed).
 */
export function PublishToast({ post }: { post?: Post }) {
  const [dismissed, setDismissed] = useState<string | null>(null);
  const [openedAt] = useState(() => Date.now());
  // A Timed Post that hasn't started (#26) has its own card; a cancelled one posted nothing and needs no toast.
  if (!post || ["scheduled", "on_hold", "cancelled"].includes(post.state)) return null;
  const dryrun = isDryRunDone(post);
  const failed = post.state === "failed";
  const unknown = post.state === "unknown";
  const done = post.state === "published" || dryrun;
  const running = !done && !failed && !unknown;
  const key = `${post.id}:${post.state}`;
  if (!running && dismissed === key) return null;
  // A Post that finished well before this page opened is shown in the card only, not as a toast.
  if (post.finished_at && openedAt - new Date(post.finished_at).getTime() > 10 * 60_000) return null;

  const where = post.handle ?? "Instagram";
  const percent = running ? (post.progress?.percent ?? 2) : 100;
  const title = running
    ? `Publishing to ${where}`
    : failed
      ? "Not posted"
      : unknown
        ? "Checking Instagram…"
        : dryrun
          ? "Dry run complete"
          : post.mode === "mock"
            ? "Mock publish complete"
            : `Published to ${where}`;
  const detail = running
    ? (post.progress?.label ?? "Handing off to the publisher…")
    : failed
      ? `${post.last_error ?? "Instagram refused it."} Nothing was posted.`
      : unknown
        ? "Instagram didn't confirm the outcome. We're checking before anything is retried."
        : dryrun
          ? "Instagram prepared the carousel. Nothing was posted."
          : post.mode === "mock"
            ? "No network call was made."
            : "Your carousel is live.";

  return (
    <>
      {running && (
        <div className="fixed inset-x-0 top-0 z-50 h-[3px] bg-transparent" role="progressbar" aria-label="Publish progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}>
          <div className="h-full bg-primary transition-[width] duration-500 ease-out" style={{ width: `${percent}%` }} />
        </div>
      )}
      <div
        role="status"
        aria-live="polite"
        className={cn(
          "fixed right-4 bottom-28 z-50 w-[min(360px,calc(100vw-2rem))] rounded-xl border p-4 shadow-[0_16px_40px_-16px_rgba(0,0,0,0.45)] motion-safe:animate-in motion-safe:slide-in-from-bottom-2 md:right-6",
          failed ? "border-destructive/40 bg-card" : unknown ? "border-warning/40 bg-card" : "bg-card",
        )}
      >
        <div className="flex items-start gap-3">
          {running && <Loader2 className="mt-0.5 size-5 shrink-0 text-primary motion-safe:animate-spin" />}
          {done && !dryrun && <CheckCircle2 className="mt-0.5 size-5 shrink-0 text-success" />}
          {done && dryrun && <Info className="mt-0.5 size-5 shrink-0 text-info" />}
          {failed && <AlertCircle className="mt-0.5 size-5 shrink-0 text-destructive" />}
          {unknown && <HelpCircle className="mt-0.5 size-5 shrink-0 text-warning" />}
          <div className="min-w-0 flex-1 space-y-1">
            <p className="text-sm font-semibold">{title}</p>
            <p className={cn("text-xs", failed ? "text-destructive" : "text-muted-foreground")}>{detail}</p>
          </div>
          {running ? (
            <span className="text-xs text-muted-foreground tabular-nums">{percent}%</span>
          ) : (
            <button type="button" aria-label="Dismiss" onClick={() => setDismissed(key)} className="rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground">
              <X className="size-4" />
            </button>
          )}
        </div>
        {running && (
          <div className="mt-3 h-1 overflow-hidden rounded-full bg-muted">
            <div className="h-full rounded-full bg-primary transition-[width] duration-500 ease-out" style={{ width: `${percent}%` }} />
          </div>
        )}
        <div className="mt-3 flex items-center gap-3 text-xs">
          <a href="#publish-progress" className="font-medium text-muted-foreground hover:text-foreground hover:underline">
            Details
          </a>
          {done && post.permalink && !dryrun && post.mode !== "mock" && (
            <a href={post.permalink} target="_blank" rel="noreferrer" className="ml-auto inline-flex h-8 items-center gap-1.5 rounded-lg bg-primary px-3 font-medium text-primary-foreground hover:bg-primary/90">
              View on Instagram <ExternalLink className="size-3.5" />
            </a>
          )}
        </div>
      </div>
    </>
  );
}
