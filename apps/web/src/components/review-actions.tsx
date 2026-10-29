"use client";

import { Loader2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

import { MODE } from "@/components/publish-progress";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import { ROLE_LABEL, atLeast, useMe } from "@/lib/roles";
import type { Job } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { cn } from "@/lib/utils";

const STATUS_NOTE: Partial<Record<Job["status"], string>> = {
  PUBLISHING: "Approved: a Post is in progress or being checked on Instagram. Approve is locked until it settles.",
  PUBLISHED: "Published. A correction needs a new Post of an edited Deck.",
  REJECTED: "Rejected — this deck will not be posted.",
  BLOCKED: "Blocked by the safeguard — this deck can't be published, not even with an Override. Regenerate it, or reject it.",
  FAILED: "The pipeline failed before review.",
};

export function ReviewActions({ job, onChange }: { job: Job; onChange: (job: Job) => void }) {
  const router = useRouter();
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string>();
  const [pending, startTransition] = useTransition();
  const { role } = useMe();
  // Where the Post goes (#21): one of this Organization's connected accounts, preselected when there is only one.
  const { data: accounts } = useApi(() => api.getInstagramAccounts(), []);
  const { data: health } = useApi(() => api.health(), []);
  const live = (accounts ?? []).filter((a) => a.live);
  const [picked, setPicked] = useState<string>();
  const accountId = live.length === 1 ? live[0].id : live.find((a) => a.id === picked)?.id;
  const mode = health?.publish_mode;
  const needsAccount = mode !== undefined && mode !== "mock" && live.length === 0;
  const mustPick = live.length > 1 && !accountId;
  // Approve/Reject need the Approver Role, Regenerate (a new Job) the Editor Role; the API enforces both.
  const mayDecide = atLeast(role, "approver");
  const mayStart = atLeast(role, "editor");
  // The Safeguard is binding (ADR 0004): a blocked Deck can't be approved by anyone, Override or not. A Deck
  // without a verdict (made before the Safeguard existed) can't be approved either; the API refuses both.
  const blocked = job.status === "BLOCKED" || job.safeguard?.decision === "block";
  // An edited version waits for its own safeguard check (#19); it is never approved on an earlier version's verdict.
  const checking = job.status === "READY_FOR_REVIEW" && !!job.safeguard_pending;
  const unchecked = job.status === "READY_FOR_REVIEW" && !job.safeguard && !checking;
  const reviewable = job.status === "READY_FOR_REVIEW" || job.status === "BLOCKED";
  const canDecide = job.status === "READY_FOR_REVIEW" && !blocked && !unchecked && !checking && !pending && mayDecide;
  const version = job.deck_version ?? 1;
  const canReject = reviewable && !pending && mayDecide;
  const publishing = job.status === "PUBLISHING" || (pending && !error);
  const blocks = job.issues.filter((i) => i.severity === "block").length;
  // Approving despite blocking Issues is an Override: it needs a written reason, kept in the audit log.
  const override = blocks > 0 && !blocked && !unchecked && !checking;
  const reasonMissing = override && !reason.trim();
  const roleNote =
    role && !mayDecide && reviewable
      ? `Your Role (${ROLE_LABEL[role]}) can't approve or reject. Ask an Approver or Admin.`
      : undefined;

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
      {override && mayDecide && job.status === "READY_FOR_REVIEW" && (
        <section className="space-y-2 rounded-xl border border-destructive/30 bg-card p-5">
          <label htmlFor="override-reason" className="text-[13px] font-semibold text-destructive">
            Override reason (required)
          </label>
          <p className="text-xs text-muted-foreground">
            This deck has {blocks} blocking issue{blocks > 1 ? "s" : ""}. Say why it can be published anyway. The reason is
            recorded under your name in the audit log.
          </p>
          <Textarea
            id="override-reason"
            value={reason}
            maxLength={2000}
            onChange={(e) => setReason(e.target.value)}
            disabled={!canDecide}
            placeholder="e.g. Checked the dates with the venue by phone on Oct 29."
            className="min-h-20 text-[13px]"
          />
        </section>
      )}

      {mayDecide && job.status === "READY_FOR_REVIEW" && !blocked && !unchecked && (
        <section className="space-y-2 rounded-xl border bg-card p-5" aria-labelledby="post-to">
          <div className="flex flex-wrap items-center gap-2">
            <h2 id="post-to" className="flex-1 text-[13px] font-medium text-muted-foreground">
              Post to
            </h2>
            {mode && <StatusBadge tone={MODE[mode][0]}>{MODE[mode][1]}</StatusBadge>}
          </div>
          {live.length > 1 ? (
            <div role="radiogroup" aria-label="Instagram account" className="flex flex-wrap gap-2">
              {live.map((a) => (
                <label
                  key={a.id}
                  className={cn(
                    "inline-flex h-9 cursor-pointer items-center gap-2 rounded-lg border px-3 text-[13px] font-medium",
                    accountId === a.id ? "border-2 border-primary bg-brand-soft" : "hover:bg-muted",
                  )}
                >
                  <input type="radio" name="account" value={a.id} checked={accountId === a.id} onChange={() => setPicked(a.id)} className="sr-only" />
                  {a.handle}
                </label>
              ))}
            </div>
          ) : live.length === 1 ? (
            <p className="text-[13px] font-medium">{live[0].handle}</p>
          ) : accounts === undefined ? (
            <p className="text-xs text-muted-foreground">Loading Instagram accounts…</p>
          ) : needsAccount ? (
            <p className="text-xs text-destructive">
              No Instagram account is connected.{" "}
              <Link href="/instagram" className="font-medium underline">
                Connect one
              </Link>{" "}
              to publish.
            </p>
          ) : (
            <p className="text-xs text-muted-foreground">No Instagram account connected: this is a mock post, nothing leaves the server.</p>
          )}
        </section>
      )}

      {/* Decision bar stays reachable at the bottom of the viewport; publish progress shows in the toast + top line. */}
      <div className="sticky bottom-20 z-10 rounded-xl md:bottom-4 border bg-card/95 p-4 shadow-[0_8px_24px_-12px_rgba(0,0,0,0.25)] backdrop-blur supports-[backdrop-filter]:bg-card/80">
          <div className="flex flex-wrap items-center gap-2.5">
            <p className={cn("w-full text-xs md:w-auto md:flex-1", error ? "text-destructive" : "text-muted-foreground")}>
              {error ??
                STATUS_NOTE[job.status] ??
                roleNote ??
                (blocked
                  ? job.safeguard?.deck_version != null && job.status === "READY_FOR_REVIEW"
                    ? `The safeguard blocked version ${version}. Nobody can approve it, not even with an Override. Edit it again, or reject it.`
                    : STATUS_NOTE.BLOCKED
                  : checking
                  ? `The safeguard is checking version ${version}. You can approve it once the check passes.`
                  : unchecked
                  ? "No safeguard verdict: this job was created before the safeguard existed. Regenerate it to publish."
                  : blocks > 0
                  ? `${blocks} blocking issue${blocks > 1 ? "s" : ""} flagged. Approving is an Override; you are the final check.`
                  : "Publishing runs on the host with the account's own token; the agent sandbox cannot post or delete.")}
            </p>
            <Button variant="destructive" size="lg" disabled={!canReject} onClick={() => act(() => api.rejectJob(job.id, "Rejected by operator"))}>
              Reject
            </Button>
            <Button
              variant="outline"
              size="lg"
              disabled={!reviewable || pending || !mayStart}
              onClick={() =>
                startTransition(async () => {
                  await api.createJob(job.prompt);
                  router.push("/jobs/new"); // shows the newest job's pipeline
                })
              }
              title="Make a new deck from the same request"
            >
              Regenerate
            </Button>
            <Button
              size="lg"
              disabled={!canDecide || reasonMissing || needsAccount || mustPick}
              title={
                blocked
                  ? "The safeguard blocked this deck. No Override can approve it."
                  : mustPick && mayDecide
                  ? "Choose the Instagram account to post to"
                  : needsAccount && mayDecide
                  ? "Connect an Instagram account first"
                  : reasonMissing && mayDecide
                  ? "Write an Override reason first"
                  : undefined
              }
              onClick={() => {
                act(() => api.approveJob(job.id, version, override ? reason.trim() : undefined, accountId));
              }}
            >
              {publishing ? (
                <>
                  <Loader2 className="size-4 motion-safe:animate-spin" /> Publishing…
                </>
              ) : blocked ? (
                "Blocked by safeguard"
              ) : checking ? (
                <>
                  <Loader2 className="size-4 motion-safe:animate-spin" /> Safeguard checking…
                </>
              ) : unchecked ? (
                "Needs safeguard check"
              ) : blocks > 0 ? (
                `Override & Publish${version > 1 ? ` v${version}` : ""}`
              ) : (
                `Approve & Publish${version > 1 ? ` v${version}` : ""}`
              )}
            </Button>
          </div>
      </div>
    </>
  );
}
