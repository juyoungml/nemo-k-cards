"use client";

import { AlertCircle, CalendarClock, Check, Circle, ExternalLink, HelpCircle, Loader2, MinusCircle, PauseCircle } from "lucide-react";
import { useEffect, useState, useTransition } from "react";

import { StatusBadge, type Tone } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { formatDateTime, fromKstInput, scheduleBounds, toKstInput } from "@/lib/format";
import { atLeast, useMe } from "@/lib/roles";
import type { Post, PostProgress, PostState, PublishMode } from "@/lib/types";
import { cn } from "@/lib/utils";

const STEPS: { key: PostProgress["step"]; label: string }[] = [
  { key: "upload", label: "Upload slides on private links" },
  { key: "containers", label: "Send slides to Instagram" },
  { key: "processing", label: "Instagram processes the images" },
  { key: "publish", label: "Publish the carousel" },
];
const ORDER = STEPS.map((s) => s.key);

export const MODE: Record<PublishMode, [Tone, string]> = {
  graph: ["danger", "Live"],
  dryrun: ["info", "Dry run"],
  mock: ["neutral", "Mock"],
};

/** What each Post state means, in words an Approver can act on (docs/spec/v1.md "Publish state machine"). */
export const POST_STATE: Record<PostState, [Tone, string]> = {
  scheduled: ["info", "Scheduled"],
  on_hold: ["warning", "On hold"],
  cancelled: ["neutral", "Cancelled"],
  queued: ["warning", "Queued"],
  container_ready: ["warning", "Carousel ready"],
  publishing: ["warning", "Publishing"],
  published: ["success", "Published"],
  failed: ["danger", "Not posted"],
  unknown: ["warning", "Checking Instagram"],
};

export const isDryRunDone = (p: Post) => p.mode === "dryrun" && p.state === "container_ready" && !!p.finished_at;

export function postBadge(p: Post) {
  if (isDryRunDone(p)) return <StatusBadge tone="info">Dry run · nothing posted</StatusBadge>;
  if (p.state === "published" && p.mode === "mock") return <StatusBadge tone="neutral">Published (mock)</StatusBadge>;
  const [tone, label] = POST_STATE[p.state];
  return <StatusBadge tone={tone}>{label}</StatusBadge>;
}

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
 * The latest Post of this Deck after "Approve & Publish": determinate bar and named steps while it runs, then an
 * honest end state. "Nothing was posted" appears only for `failed`. An `unknown` Post says Instagram didn't prove
 * the outcome, keeps Approve locked, and (once the reconciler has alerted) lets an Approver settle it by hand.
 */
export function PublishProgress({ post, onChange }: { post?: Post; onChange?: (p: Post) => void }) {
  if (post?.state === "scheduled" || post?.state === "on_hold") return <UpcomingPost post={post} onChange={onChange} />;
  if (post?.state === "cancelled") return null; // posted nothing; the Posts list shows it
  return <RunningPost post={post} onChange={onChange} />;
}

/** Days, hours and minutes until `iso`, for "in 2d 3h". */
function until(iso: string) {
  const m = Math.max(0, Math.round((new Date(iso).getTime() - Date.now()) / 60_000));
  const d = Math.floor(m / 1440);
  const h = Math.floor((m % 1440) / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m % 60}m` : `${m}m`;
}

/**
 * A Timed Post that hasn't started (#26): when it posts, and Reschedule / Cancel for an Approver. On hold, it says
 * why and how to resume: approve the Deck again (an edit) or pick a new time (a missed time).
 */
export function UpcomingPost({ post, onChange }: { post: Post; onChange?: (p: Post) => void }) {
  const { role } = useMe();
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string>();
  const [editing, setEditing] = useState(false);
  const [when, setWhen] = useState(() => toKstInput(post.scheduled_at ?? Date.now() + 3_600_000));
  const [bounds] = useState(scheduleBounds);
  const may = atLeast(role, "approver");
  const held = post.state === "on_hold";
  const where = post.handle ?? (post.mode === "mock" ? "a mock post" : "Instagram");

  const run = (fn: () => Promise<Post>) =>
    startTransition(async () => {
      try {
        setError(undefined);
        onChange?.(await fn());
        setEditing(false);
      } catch (e) {
        setError((e as Error).message);
      }
    });

  return (
    <section
      id="publish-progress"
      className={cn("scroll-mt-6 space-y-3 rounded-xl border bg-card p-5", held && "border-warning/40")}
      aria-live="polite"
    >
      <div className="flex items-center gap-2.5">
        {held ? <PauseCircle className="size-5 text-warning" /> : <CalendarClock className="size-5 text-info" />}
        <h2 className="flex-1 text-base font-semibold">
          {held ? "On hold" : `Scheduled for ${post.scheduled_at ? formatDateTime(post.scheduled_at) : "later"} KST`}
        </h2>
        <StatusBadge tone={MODE[post.mode][0]}>{MODE[post.mode][1]}</StatusBadge>
        {!held && post.scheduled_at && (
          <span className="text-xs text-muted-foreground tabular-nums" title="Time left">
            in {until(post.scheduled_at)}
          </span>
        )}
      </div>
      <p className="text-[13px] text-muted-foreground">
        Version {post.deck_version ?? 1} to {where}
        {post.approved_by_email ? `, approved by ${post.approved_by_email}` : ""}.
        {held && post.scheduled_at ? ` It was due ${formatDateTime(post.scheduled_at)} KST.` : ""}
      </p>
      {held && (
        <p className="rounded-lg bg-warning-soft px-3 py-2.5 text-xs text-warning">
          Nothing will be posted until someone acts. Why: {post.held_reason}.
          <br />
          Approve the Deck again below, now or at a new time, and that replaces this Post. Or cancel it.
        </p>
      )}
      {may && (
        <div className="flex flex-wrap items-center gap-2">
          {editing ? (
            <>
              <input
                type="datetime-local"
                aria-label="New time (Korea time)"
                value={when}
                min={bounds.min}
                max={bounds.max}
                onChange={(e) => setWhen(e.target.value)}
                className="h-8 rounded-lg border bg-background px-2.5 text-[13px] tabular-nums"
              />
              <span className="text-xs text-muted-foreground">KST</span>
              <Button size="sm" disabled={pending || !when} onClick={() => run(() => api.reschedulePost(post.id, fromKstInput(when)))}>
                Save time
              </Button>
              <Button size="sm" variant="ghost" disabled={pending} onClick={() => setEditing(false)}>
                Keep the time
              </Button>
            </>
          ) : (
            <>
              {!held && (
                <Button size="sm" variant="outline" disabled={pending} onClick={() => setEditing(true)}>
                  Reschedule
                </Button>
              )}
              <Button size="sm" variant="outline" disabled={pending} onClick={() => run(() => api.cancelPost(post.id))}>
                Cancel post
              </Button>
            </>
          )}
        </div>
      )}
      {error && <p className="text-xs text-destructive">{error}</p>}
    </section>
  );
}

function RunningPost({ post, onChange }: { post?: Post; onChange?: (p: Post) => void }) {
  const elapsed = useElapsed(post?.created_at, post?.finished_at);
  const { role } = useMe();
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string>();
  if (!post) return null;

  const p = post.progress;
  const dryrun = isDryRunDone(post);
  const failed = post.state === "failed";
  const unknown = post.state === "unknown";
  const published = post.state === "published";
  const finished = published || dryrun;
  const where = post.handle ?? "Instagram";
  const percent = finished || failed ? 100 : (p?.percent ?? 2);
  const current = finished ? ORDER.length : Math.max(0, ORDER.indexOf(p?.step && p.step !== "failed" ? p.step : "upload"));
  const canResolve = unknown && atLeast(role, "approver");
  const mayNotPosted = !!(post.alerted_at || !post.next_check_at);

  const title = failed
    ? "Not posted"
    : unknown
      ? "Checking Instagram…"
      : dryrun
        ? "Dry run complete"
        : published
          ? post.mode === "mock"
            ? "Mock publish complete"
            : `Published to ${where}`
          : `Publishing to ${where}`;

  const resolve = (posted: boolean) =>
    startTransition(async () => {
      try {
        setError(undefined);
        onChange?.(await api.resolvePost(post.id, posted));
      } catch (e) {
        setError((e as Error).message);
      }
    });

  return (
    <section
      id="publish-progress"
      className={cn(
        "scroll-mt-6 space-y-4 rounded-xl border bg-card p-5",
        failed && "border-destructive/40",
        unknown && "border-warning/40",
        published && post.mode === "graph" && "border-success/40",
      )}
      aria-live="polite"
    >
      <div className="flex items-center gap-2.5">
        {failed ? (
          <AlertCircle className="size-5 text-destructive" />
        ) : unknown ? (
          <HelpCircle className="size-5 text-warning" />
        ) : finished ? (
          <span className="grid size-5 place-items-center rounded-full bg-success text-white">
            <Check className="size-3.5" />
          </span>
        ) : (
          <Loader2 className="size-5 text-primary motion-safe:animate-spin" />
        )}
        <h2 className="flex-1 text-base font-semibold">{title}</h2>
        <StatusBadge tone={MODE[post.mode][0]}>{MODE[post.mode][1]}</StatusBadge>
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
              failed ? "bg-destructive" : unknown ? "bg-warning" : finished ? "bg-success" : "bg-primary",
            )}
            style={{ width: `${percent}%` }}
          />
        </div>
        <div className="flex text-xs text-muted-foreground">
          <span className="flex-1">{p?.label ?? "Approved, handing off to the publisher…"}</span>
          <span className="tabular-nums">{percent}%</span>
        </div>
      </div>

      {!failed && !unknown && (
        <ol className="space-y-2">
          {STEPS.map((s, i) => {
            const skipped = dryrun && s.key === "publish";
            const state = skipped ? "skipped" : i < current ? "done" : i === current && !finished ? "running" : "pending";
            const count = p && p.step === s.key && p.total > 1 ? ` ${p.done}/${p.total}` : "";
            return (
              <li key={s.key} className="flex items-center gap-2.5 text-[13px]">
                {state === "done" && <Check className="size-4 text-success" />}
                {state === "running" && <Loader2 className="size-4 text-primary motion-safe:animate-spin" />}
                {state === "pending" && <Circle className="size-4 text-muted-foreground/50" />}
                {state === "skipped" && <MinusCircle className="size-4 text-muted-foreground" />}
                <span className={cn(state === "pending" && "text-muted-foreground", state === "running" && "font-medium")}>
                  {s.label}
                  {state === "running" && count}
                  {state === "skipped" && " — skipped (dry run, nothing posted)"}
                </span>
              </li>
            );
          })}
        </ol>
      )}

      {failed && (
        <p className="rounded-lg bg-danger-soft px-3 py-2.5 text-xs text-destructive">
          {post.last_error ?? "Instagram refused the post."}
          <br />
          Nothing was posted. Fix the cause and approve again.
        </p>
      )}
      {unknown && (
        <div className="space-y-3 rounded-lg bg-warning-soft px-3 py-2.5 text-xs text-warning">
          <p>
            Instagram&apos;s answer didn&apos;t say whether the carousel went live ({post.last_error ?? "no answer"}). We are
            checking with Instagram before anything is retried, so it can&apos;t be posted twice. Approve stays locked until
            this settles.
            {post.alerted_at && " It has been over 30 minutes: please check the account on Instagram."}
          </p>
          {canResolve && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-foreground">I checked {where} on Instagram:</span>
              <Button size="sm" variant="outline" disabled={pending} onClick={() => resolve(true)}>
                It is posted
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={pending || !mayNotPosted}
                title={mayNotPosted ? undefined : "Available after 30 minutes, while the check may still find it"}
                onClick={() => resolve(false)}
              >
                It is not posted
              </Button>
            </div>
          )}
          {error && <p className="text-destructive">{error}</p>}
        </div>
      )}
      {dryrun && (
        <p className="rounded-lg bg-info-soft px-3 py-2.5 text-xs text-info">
          Instagram accepted all slides and prepared the carousel on {where}. Nothing was posted. Switch the backend to
          live publishing and approve again to post.
        </p>
      )}
      {published && post.permalink && post.mode !== "mock" && (
        <a
          href={post.permalink}
          target="_blank"
          rel="noreferrer"
          className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-primary px-3.5 text-[13px] font-medium text-primary-foreground hover:bg-primary/90"
        >
          View on Instagram <ExternalLink className="size-3.5" />
        </a>
      )}
      {published && !post.permalink && post.mode === "graph" && (
        <p className="text-xs text-muted-foreground">Live on {where}; Instagram hasn&apos;t given us its link yet.</p>
      )}
    </section>
  );
}

/** Every Post of this Deck, newest first: where it went, in which mode, and how it ended. */
export function PostsList({ posts }: { posts: Post[] }) {
  if (posts.length < 2) return null;
  return (
    <section className="space-y-2 rounded-xl border bg-card p-5">
      <h2 className="text-base font-semibold">Posts of this Deck</h2>
      <ul className="divide-y text-[13px]">
        {posts.map((p) => (
          <li key={p.id} className="flex flex-wrap items-center gap-2 py-2">
            {postBadge(p)}
            <StatusBadge tone={MODE[p.mode][0]}>{MODE[p.mode][1]}</StatusBadge>
            <span className="min-w-0 flex-1 truncate">{p.handle ?? "no account"}</span>
            <span className="text-xs text-muted-foreground tabular-nums">
              {p.scheduled_at && ["scheduled", "on_hold"].includes(p.state)
                ? `for ${formatDateTime(p.scheduled_at)} KST`
                : new Date(p.created_at).toLocaleString("en-GB", { timeZone: "Asia/Seoul", dateStyle: "short", timeStyle: "short" })}
            </span>
            {p.permalink && p.mode !== "mock" && (
              <a href={p.permalink} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline">
                View <ExternalLink className="size-3" />
              </a>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
