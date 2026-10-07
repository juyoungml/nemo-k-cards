"use client";

import { ChevronDown, ExternalLink } from "lucide-react";
import { useState } from "react";

import { LoadState, PageHeader, Panel } from "@/components/page";
import { PublishProgress } from "@/components/publish-progress";
import { PublishToast } from "@/components/publish-toast";
import { ReviewActions } from "@/components/review-actions";
import { SlidePreview } from "@/components/slide-preview";
import { JobStatusBadge, StatusBadge, type Tone } from "@/components/status-badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, assetUrl } from "@/lib/api";
import type { Issue, Job, LinkCheck } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { cn } from "@/lib/utils";

const CHECKS: { category: Issue["category"]; label: string }[] = [
  { category: "fact", label: "Facts" },
  { category: "link", label: "Links" },
  { category: "sensitive", label: "Sensitive" },
  { category: "pii", label: "PII" },
  { category: "visual", label: "Visual QA" },
  { category: "tone", label: "Tone" },
];

function checkBadge(job: Job, category: Issue["category"], label: string) {
  const found = job.issues.filter((i) => i.category === category);
  const blocks = found.filter((i) => i.severity === "block").length;
  let text = `${label} ✓`;
  if (category === "link" && job.verification) {
    // Count only sources of events that made it into the deck; excluded ones are shown in the table.
    const excluded = new Set(job.verification.excluded_event_ids);
    const kept = new Set(job.briefs.filter((b) => !excluded.has(b.id)).flatMap((b) => b.sources.map((s) => s.url)));
    const checks = job.verification.checks.filter((c) => kept.has(c.url));
    const ok = checks.filter((c) => c.status === "ok" || c.status === "redirect").length;
    text = `${label} ${ok}/${checks.length} ✓`;
  }
  if (blocks) return <StatusBadge key={category} tone="danger">{`${label} · ${blocks} block`}</StatusBadge>;
  if (found.length) return <StatusBadge key={category} tone="warning">{`${label} · ${found.length} warn`}</StatusBadge>;
  return <StatusBadge key={category} tone="success">{text}</StatusBadge>;
}

function linkBadge(check: LinkCheck | undefined, excluded: boolean) {
  if (!check) return <StatusBadge tone="neutral">unchecked</StatusBadge>;
  const [tone, label]: [Tone, string] =
    check.status === "ok" ? ["success", `${check.http_code ?? 200} OK`]
    : check.status === "redirect" ? ["success", "redirect"]
    : check.status === "suspicious" ? ["danger", `${check.reason ?? "suspicious"}`]
    : check.status === "dead" ? ["danger", `${check.http_code ?? "dead"}`]
    : ["warning", "timeout"];
  return <StatusBadge tone={tone}>{excluded ? `Excluded · ${label}` : label}</StatusBadge>;
}

/** Blocking issues first and always open; warnings collapsed. Clicking an issue opens its slide. */
function IssueList({ issues, onJump }: { issues: Issue[]; onJump: (slide: number) => void }) {
  const blocks = issues.filter((i) => i.severity === "block");
  const warns = issues.filter((i) => i.severity === "warn");
  const item = (i: Issue, n: number) => {
    const body = (
      <>
        <span className="font-semibold">
          {i.slide_index != null ? `Slide ${i.slide_index + 1}` : "Deck"} · {i.category}
        </span>{" "}
        — {i.message}
      </>
    );
    const cls = cn(
      "block w-full rounded-lg px-3 py-2.5 text-left text-xs",
      i.severity === "block" ? "bg-danger-soft text-destructive" : "bg-warning-soft text-warning",
    );
    return (
      <li key={n}>
        {i.slide_index != null ? (
          <button type="button" className={cn(cls, "hover:ring-1 hover:ring-current")} onClick={() => onJump(i.slide_index!)} title="Show this slide">
            {body}
          </button>
        ) : (
          <div className={cls}>{body}</div>
        )}
      </li>
    );
  };
  return (
    <div className="mt-3 space-y-3">
      {blocks.length > 0 && <ul className="space-y-2">{blocks.map(item)}</ul>}
      {warns.length > 0 && (
        <details className="group">
          <summary className="flex cursor-pointer list-none items-center gap-1.5 text-xs font-medium text-muted-foreground hover:text-foreground">
            <ChevronDown className="size-3.5 transition-transform group-open:rotate-180" />
            {warns.length} warning{warns.length > 1 ? "s" : ""} (advisory)
          </summary>
          <ul className="mt-2 space-y-2">{warns.map(item)}</ul>
        </details>
      )}
    </div>
  );
}

function reviewSummary(job: Job) {
  if (job.status !== "READY_FOR_REVIEW") return job.status.toLowerCase().replaceAll("_", " ");
  const blocks = job.issues.filter((i) => i.severity === "block").length;
  const warns = job.issues.length - blocks;
  if (!job.issues.length) return "all automated checks passed · waiting for your decision";
  return [blocks && `${blocks} blocking`, warns && `${warns} warning${warns > 1 ? "s" : ""}`].filter(Boolean).join(" · ") + " · your decision";
}

export function ReviewDetailView({ id }: { id: string }) {
  const { data: job, error, setData } = useApi(() => api.getJob(id), [id], 1000);
  const [slide, setSlide] = useState(0);
  if (!job) return <LoadState error={error} />;
  const deck = job.deck;
  const checksById = new Map(job.verification?.checks.map((c) => [c.url, c]));

  return (
    <>
      <PageHeader
        title={deck?.title ?? job.prompt}
        description={`job ${job.id} · ${deck?.slides.length ?? 0} slides · ${reviewSummary(job)}`}
      >
        {job.published_url && (
          <a href={job.published_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-[13px] font-medium text-primary hover:underline">
            View post <ExternalLink className="size-3.5" />
          </a>
        )}
        <JobStatusBadge status={job.status} mock={!!job.published_url?.includes("/p/MOCK")} />
      </PageHeader>

      <PublishToast job={job} />

      <div className="flex flex-col gap-5 lg:flex-row lg:items-start">
        {deck && deck.slides.length > 0 && (
          <SlidePreview slides={deck.slides} images={job.slide_urls?.map(assetUrl)} current={slide} onSelect={setSlide} />
        )}

        <div className="min-w-0 flex-1 space-y-4">
          <PublishProgress job={job} />

          <Panel title="Automated checks">
            <div className="flex flex-wrap gap-2">{CHECKS.map((c) => checkBadge(job, c.category, c.label))}</div>
            {job.issues.length > 0 && <IssueList issues={job.issues} onJump={setSlide} />}
          </Panel>

          {job.briefs.length > 0 && (
            <Panel title="Sources & link status">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Event</TableHead>
                    <TableHead>Source</TableHead>
                    <TableHead>Link</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {job.briefs.flatMap((b) =>
                    b.sources.map((s) => {
                      const excluded = job.verification?.excluded_event_ids.includes(b.id) ?? false;
                      return (
                        <TableRow key={`${b.id}-${s.url}`} className={cn(excluded && "text-muted-foreground")}>
                          <TableCell>
                            {b.title_en} ({b.title_ko})
                          </TableCell>
                          <TableCell>
                            <a href={s.url} target="_blank" rel="noreferrer" className="hover:underline">
                              {new URL(s.url).hostname}
                            </a>
                          </TableCell>
                          <TableCell>{linkBadge(checksById.get(s.url), excluded)}</TableCell>
                        </TableRow>
                      );
                    }),
                  )}
                </TableBody>
              </Table>
            </Panel>
          )}

          <ReviewActions job={job} onChange={setData} />
        </div>
      </div>
    </>
  );
}
