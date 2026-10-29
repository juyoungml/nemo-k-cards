"use client";

import { Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import { ROLE_LABEL, atLeast, useMe } from "@/lib/roles";
import type { Job } from "@/lib/types";
import { cn } from "@/lib/utils";

const STATUS_NOTE: Partial<Record<Job["status"], string>> = {
  PUBLISHING: "Approved — the host is publishing to Instagram…",
  PUBLISHED: "Published to @whatsonkorea.",
  REJECTED: "Rejected — this deck will not be posted.",
  BLOCKED: "Blocked by the safeguard — this deck can't be published, not even with an Override. Regenerate it, or reject it.",
  FAILED: "The pipeline failed before review.",
};

export function ReviewActions({ job, onChange }: { job: Job; onChange: (job: Job) => void }) {
  const router = useRouter();
  const [caption, setCaption] = useState(job.deck?.caption ?? "");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string>();
  const [pending, startTransition] = useTransition();
  const { role } = useMe();
  // Approve/Reject need the Approver Role, Regenerate (a new Job) the Editor Role; the API enforces both.
  const mayDecide = atLeast(role, "approver");
  const mayStart = atLeast(role, "editor");
  // The Safeguard is binding (ADR 0004): a blocked Deck can't be approved by anyone, Override or not. A Deck
  // without a verdict (made before the Safeguard existed) can't be approved either; the API refuses both.
  const blocked = job.status === "BLOCKED" || job.safeguard?.decision === "block";
  const unchecked = job.status === "READY_FOR_REVIEW" && !job.safeguard;
  const reviewable = job.status === "READY_FOR_REVIEW" || job.status === "BLOCKED";
  const canDecide = job.status === "READY_FOR_REVIEW" && !blocked && !unchecked && !pending && mayDecide;
  const canReject = reviewable && !pending && mayDecide;
  const publishing = job.status === "PUBLISHING" || (pending && !error);
  const blocks = job.issues.filter((i) => i.severity === "block").length;
  // Approving despite blocking Issues is an Override: it needs a written reason, kept in the audit log.
  const override = blocks > 0 && !blocked && !unchecked;
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
      <section className="space-y-2 rounded-xl border bg-card p-5">
        <label htmlFor="caption" className="text-[13px] font-medium text-muted-foreground">
          Caption (editable · an edit is checked by the safeguard again before it is posted)
        </label>
        <Textarea id="caption" value={caption} onChange={(e) => setCaption(e.target.value)} disabled={!canDecide} className="min-h-28 text-[13px]" />
      </section>

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

      {/* Decision bar stays reachable at the bottom of the viewport; publish progress shows in the toast + top line. */}
      <div className="sticky bottom-20 z-10 rounded-xl md:bottom-4 border bg-card/95 p-4 shadow-[0_8px_24px_-12px_rgba(0,0,0,0.25)] backdrop-blur supports-[backdrop-filter]:bg-card/80">
          <div className="flex flex-wrap items-center gap-2.5">
            <p className={cn("w-full text-xs md:w-auto md:flex-1", error ? "text-destructive" : "text-muted-foreground")}>
              {error ??
                STATUS_NOTE[job.status] ??
                roleNote ??
                (blocked
                  ? STATUS_NOTE.BLOCKED
                  : unchecked
                  ? "No safeguard verdict: this job was created before the safeguard existed. Regenerate it to publish."
                  : blocks > 0
                  ? `${blocks} blocking issue${blocks > 1 ? "s" : ""} flagged. Approving is an Override; you are the final check.`
                  : "Publishing runs on the host with the IG token — the agent sandbox cannot post or delete.")}
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
              disabled={!canDecide || reasonMissing}
              title={
                blocked
                  ? "The safeguard blocked this deck. No Override can approve it."
                  : reasonMissing && mayDecide
                  ? "Write an Override reason first"
                  : undefined
              }
              onClick={() => {
                act(() => api.approveJob(job.id, caption, override ? reason.trim() : undefined));
              }}
            >
              {publishing ? (
                <>
                  <Loader2 className="size-4 motion-safe:animate-spin" /> Publishing…
                </>
              ) : blocked ? (
                "Blocked by safeguard"
              ) : unchecked ? (
                "Needs safeguard check"
              ) : blocks > 0 ? (
                "Override & Publish"
              ) : (
                "Approve & Publish"
              )}
            </Button>
          </div>
      </div>
    </>
  );
}
