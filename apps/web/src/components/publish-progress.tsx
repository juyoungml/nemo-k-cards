"use client";

import { AlertCircle, Check, Circle, ExternalLink, Loader2, MinusCircle } from "lucide-react";
import { useEffect, useState } from "react";

import { StatusBadge, type Tone } from "@/components/status-badge";
import type { Job, PublishProgress as Progress } from "@/lib/types";
import { cn } from "@/lib/utils";

const STEPS: { key: Progress["step"]; label: string }[] = [
  { key: "upload", label: "Upload slides to public storage" },
  { key: "containers", label: "Send slides to Instagram" },
  { key: "processing", label: "Instagram processes the images" },
  { key: "publish", label: "Publish the carousel" },
];
const ORDER = STEPS.map((s) => s.key);

const MODE: Record<Progress["mode"], [Tone, string]> = {
  graph: ["danger", "Live"],
  dryrun: ["info", "Dry run"],
  mock: ["neutral", "Mock"],
};

function useElapsed(start?: string, end?: string | null) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!start || end) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [start, end]);
  if (!start) return "0:00";
  const ms = (end ? new Date(end).getTime() : now) - new Date(start).getTime();
  const s = Math.max(0, Math.round(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/**
 * Publish progress after "Approve & Publish": determinate bar + named steps with real counts,
 * elapsed time, and a clear end state (View on Instagram / error + retry / dry-run result).
 */
export function PublishProgress({ job }: { job: Job }) {
  const p = job.publish_progress;
  const elapsed = useElapsed(p?.started_at, p?.finished_at);
  const starting = job.status === "PUBLISHING" && !p;
  if (!p && !starting) return null;

  const failed = p?.step === "failed";
  const finished = p?.step === "done";
  const dryrun = p?.mode === "dryrun";
  const percent = starting ? 2 : (p?.percent ?? 0);
  // Index of the step in progress (failed: the step that was running when it failed).
  const failedAt = failed ? (p?.label.toLowerCase() ?? "") : "";
  const current = finished
    ? ORDER.length
    : failed
      ? Math.max(0, STEPS.findIndex((s) => failedAt.includes(s.label.toLowerCase().split(" ")[0])))
      : Math.max(0, ORDER.indexOf(p?.step ?? "upload"));

  const title = starting
    ? "Starting publish…"
    : failed
      ? "Publish failed"
      : finished
        ? dryrun
          ? "Dry run complete"
          : p?.mode === "mock"
            ? "Mock publish complete"
            : "Published to @whatsonkorea"
        : "Publishing to @whatsonkorea";

  return (
    <section
      id="publish-progress"
      className={cn(
        "scroll-mt-6 space-y-4 rounded-xl border bg-card p-5",
        failed && "border-destructive/40",
        finished && !dryrun && p?.mode === "graph" && "border-success/40",
      )}
      aria-live="polite"
    >
      <div className="flex items-center gap-2.5">
        {failed ? (
          <AlertCircle className="size-5 text-destructive" />
        ) : finished ? (
          <span className="grid size-5 place-items-center rounded-full bg-success text-white">
            <Check className="size-3.5" />
          </span>
        ) : (
          <Loader2 className="size-5 text-primary motion-safe:animate-spin" />
        )}
        <h2 className="flex-1 text-base font-semibold">{title}</h2>
        {p && <StatusBadge tone={MODE[p.mode][0]}>{MODE[p.mode][1]}</StatusBadge>}
        <span className="text-xs text-muted-foreground tabular-nums" title="Elapsed">
          {elapsed}
        </span>
      </div>

      <div className="space-y-1.5">
        <div
          role="progressbar"
          aria-label="Publish progress"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={percent}
          className="h-2 overflow-hidden rounded-full bg-muted"
        >
          <div
            className={cn(
              "h-full rounded-full transition-[width] duration-500 ease-out",
              failed ? "bg-destructive" : finished ? "bg-success" : "bg-primary",
            )}
            style={{ width: `${percent}%` }}
          />
        </div>
        <div className="flex text-xs text-muted-foreground">
          <span className="flex-1">{starting ? "Approved, handing off to the host publisher…" : p?.label}</span>
          <span className="tabular-nums">{percent}%</span>
        </div>
      </div>

      <ol className="space-y-2">
        {STEPS.map((s, i) => {
          const skipped = dryrun && s.key === "publish" && finished;
          const state = skipped
            ? "skipped"
            : i < current
              ? "done"
              : i === current && failed
                ? "failed"
                : i === current && !finished
                  ? "running"
                  : "pending";
          const count = p && p.step === s.key && p.total > 1 ? ` ${p.done}/${p.total}` : "";
          return (
            <li key={s.key} className="flex items-center gap-2.5 text-[13px]">
              {state === "done" && <Check className="size-4 text-success" />}
              {state === "running" && <Loader2 className="size-4 text-primary motion-safe:animate-spin" />}
              {state === "failed" && <AlertCircle className="size-4 text-destructive" />}
              {state === "pending" && <Circle className="size-4 text-muted-foreground/50" />}
              {state === "skipped" && <MinusCircle className="size-4 text-muted-foreground" />}
              <span
                className={cn(
                  state === "pending" && "text-muted-foreground",
                  state === "running" && "font-medium",
                  state === "failed" && "text-destructive",
                )}
              >
                {s.label}
                {state === "running" && count}
                {state === "skipped" && " — skipped (dry run, nothing posted)"}
              </span>
            </li>
          );
        })}
      </ol>

      {failed && p?.error && (
        <p className="rounded-lg bg-danger-soft px-3 py-2.5 text-xs text-destructive">
          {p.error}
          <br />
          Nothing was posted. Fix the issue and click Approve &amp; Publish again.
        </p>
      )}
      {finished && dryrun && (
        <p className="rounded-lg bg-info-soft px-3 py-2.5 text-xs text-info">
          Instagram accepted all slides and prepared the carousel. Nothing was posted. Switch the backend to live
          publishing and approve again to post.
        </p>
      )}
      {finished && job.published_url && !dryrun && (
        <a
          href={job.published_url}
          target="_blank"
          rel="noreferrer"
          className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-primary px-3.5 text-[13px] font-medium text-primary-foreground hover:bg-primary/90"
        >
          View on Instagram <ExternalLink className="size-3.5" />
        </a>
      )}
    </section>
  );
}
