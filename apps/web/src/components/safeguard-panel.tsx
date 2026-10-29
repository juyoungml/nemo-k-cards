"use client";

import { Loader2, ShieldAlert, ShieldCheck } from "lucide-react";

import { StatusBadge } from "@/components/status-badge";
import type { HarmCategory, Job, SafeguardFinding } from "@/lib/types";
import { cn } from "@/lib/utils";

const CATEGORY: Record<HarmCategory, string> = {
  hate: "Hate / discrimination",
  harassment: "Harassment",
  sexual: "Sexual content",
  violence: "Violence",
  self_harm: "Self-harm",
  dangerous: "Dangerous or illegal",
  historical_tragedy: "Historical tragedy",
  political: "Political",
  territorial_cultural: "Territory / cultural origin",
  scam: "Scam / misleading",
  other: "Other",
};

function where(f: SafeguardFinding) {
  if (f.location === "caption") return "Caption";
  if (f.location === "hashtags") return "Hashtags";
  const slide = f.slide_index != null ? `Slide ${f.slide_index + 1}` : "Deck";
  return f.location === "image" ? `${slide} image` : slide;
}

export function Finding({ f, onJump }: { f: SafeguardFinding; onJump: (slide: number) => void }) {
  const block = f.severity === "block";
  const jump = f.slide_index != null && (f.location === "slide" || f.location === "image");
  return (
    <li className={cn("rounded-lg border p-3 text-xs", block ? "border-destructive/30 bg-card" : "border-warning/30 bg-warning-soft")}>
      <div className="flex flex-wrap items-center gap-2">
        <span className={cn("font-semibold", block ? "text-destructive" : "text-warning")}>
          {block ? "Block" : "Warn"} · {CATEGORY[f.category] ?? f.category}
        </span>
        {jump ? (
          <button type="button" onClick={() => onJump(f.slide_index!)} className="font-medium text-foreground underline-offset-2 hover:underline" title="Show this slide">
            {where(f)}
          </button>
        ) : (
          <span className="font-medium text-foreground">{where(f)}</span>
        )}
      </div>
      <blockquote className="mt-2 border-l-2 border-current/30 pl-2 font-medium text-foreground">“{f.excerpt}”</blockquote>
      <p className="mt-2 text-foreground">{f.explanation}</p>
      {f.fix && (
        <p className="mt-1.5 text-muted-foreground">
          <span className="font-semibold text-foreground">Fix: </span>
          {f.fix}
        </p>
      )}
      <p className="mt-1.5 text-[11px] break-all text-muted-foreground">Rule: {f.rule}</p>
    </li>
  );
}

/** The one binding check (ADR 0004). Blocked: why, finding by finding, and what to change; no Override can approve
 * it. Allowed: one line plus any warnings. */
export function SafeguardPanel({ job, onJump }: { job: Job; onJump: (slide: number) => void }) {
  const sg = job.safeguard;
  if (job.safeguard_pending && job.status === "READY_FOR_REVIEW") {
    // An edited version (#19): the full check runs on exactly this version before it can be approved.
    return (
      <section aria-label="Safeguard verdict" aria-live="polite" className="flex items-start gap-3 rounded-xl border bg-card p-4 md:p-5">
        <Loader2 className="mt-0.5 size-5 shrink-0 text-muted-foreground motion-safe:animate-spin" />
        <div className="space-y-1">
          <h2 className="text-base font-semibold">Safeguard is checking version {job.deck_version ?? 1}…</h2>
          <p className="text-xs text-muted-foreground">
            The deck was edited, so the earlier verdict no longer applies. It can be approved once this check passes.
          </p>
        </div>
      </section>
    );
  }
  if (!sg) {
    if (job.status !== "READY_FOR_REVIEW") return null;
    return (
      <section className="rounded-xl border bg-card p-4 text-xs text-muted-foreground md:p-5">
        No safeguard verdict for this job (it was created before the safeguard existed). Regenerate it to publish.
      </section>
    );
  }
  const blocked = sg.decision === "block";
  const blocks = sg.findings.filter((f) => f.severity === "block");
  const warns = sg.findings.filter((f) => f.severity === "warn");
  const Icon = blocked ? ShieldAlert : ShieldCheck;

  return (
    <section
      aria-label="Safeguard verdict"
      className={cn("min-w-0 rounded-xl border p-4 md:p-5", blocked ? "border-destructive/40 bg-danger-soft" : "bg-card")}
    >
      <div className="flex items-start gap-3">
        <Icon className={cn("mt-0.5 size-5 shrink-0", blocked ? "text-destructive" : "text-success")} />
        <div className="min-w-0 flex-1 space-y-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className={cn("text-base font-semibold", blocked && "text-destructive")}>
              {blocked ? "Blocked by safeguard" : "Safeguard: safe to post"}
            </h2>
            {blocked ? (
              <StatusBadge tone="danger">{blocks.length ? `${blocks.length} block` : "not proven safe"}</StatusBadge>
            ) : (
              <StatusBadge tone="success">allowed</StatusBadge>
            )}
            {warns.length > 0 && <StatusBadge tone="warning">{`${warns.length} warn`}</StatusBadge>}
          </div>
          <p className="text-sm text-foreground">{sg.summary}</p>
          {blocked && (
            <p className="text-xs text-muted-foreground">
              {sg.deck_version != null && job.status === "READY_FOR_REVIEW"
                ? `Nobody can approve version ${sg.deck_version}, not even with an Override. Edit the deck again (fix what is listed below), or reject it.`
                : "Nobody can approve this deck, not even with an Override. Regenerate it with a different request, or reject it."}
            </p>
          )}
        </div>
      </div>

      {sg.findings.length > 0 && (
        <ul className="mt-4 space-y-2">
          {[...blocks, ...warns].map((f, i) => (
            <Finding key={i} f={f} onJump={onJump} />
          ))}
        </ul>
      )}

      <p className="mt-3 text-[11px] text-muted-foreground">
        Checked by {sg.checked_by.join(" + ") || "—"} · policy {sg.policy_version || "—"}
        {sg.deck_version != null && <> · version {sg.deck_version}</>}
        {blocked && sg.model_decision === "allow" && <> · the model allowed it; a blocked phrase decides</>}
        {sg.model_error && <span className="text-destructive"> · model error: {sg.model_error}</span>}
      </p>
    </section>
  );
}
