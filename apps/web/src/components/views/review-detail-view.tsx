"use client";

import { ChevronDown, ExternalLink } from "lucide-react";

import { DeckBar, SlideWorkspace, useDeckEditor } from "@/components/deck-editor";
import { GuidePointsPanel } from "@/components/guide-points-panel";
import { LoadState, PageHeader, Panel } from "@/components/page";
import { PostsList, PublishProgress } from "@/components/publish-progress";
import { PublishToast } from "@/components/publish-toast";
import { ReviewActions } from "@/components/review-actions";
import { SafeguardPanel } from "@/components/safeguard-panel";
import { SourceMaterialPanel } from "@/components/source-material-panel";
import { JobStatusBadge, StatusBadge, type Tone } from "@/components/status-badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { Issue, Job, LinkCheck, Post } from "@/lib/types";
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
    const kept = new Set(
      job.guide
        ? job.guide.sources.map((s) => s.url) // a Guide Deck's links (#27)
        : job.briefs.filter((b) => !excluded.has(b.id)).flatMap((b) => b.sources.map((s) => s.url)),
    );
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
  if (job.status === "BLOCKED") return "blocked by safeguard · can't be published";
  if (job.status !== "READY_FOR_REVIEW") return job.status.toLowerCase().replaceAll("_", " ");
  const blocks = job.issues.filter((i) => i.severity === "block").length;
  const warns = job.issues.length - blocks;
  if (!job.issues.length) return "all automated checks passed · waiting for your decision";
  return [blocks && `${blocks} blocking`, warns && `${warns} warning${warns > 1 ? "s" : ""}`].filter(Boolean).join(" · ") + " · your decision";
}

export function ReviewDetailView({ id }: { id: string }) {
  const { data: job, error, setData } = useApi(() => api.getJob(id), [id], 1000);
  // Publishing is the Post's (#21): its state, progress and link come from /posts, polled like the Job.
  const { data: posts, reload: reloadPosts } = useApi(() => api.listPosts(id), [id], 1000);
  if (!job) return <LoadState error={error} />;
  return <ReviewDetail job={job} setData={setData} posts={posts} reloadPosts={() => void reloadPosts()} />;
}

/** The review page (#19 layout "B"): header bar with the version and safeguard chips and Save; the deck-level
 * safeguard summary; the slide workspace (filmstrip · selected slide · inspector with Text, Sources, Issues and
 * Caption tabs); then the deck-level checks, material, links and the decision bar. */
function ReviewDetail({ job, setData, posts, reloadPosts }: { job: Job; setData: (j: Job) => void; posts?: Post[]; reloadPosts: () => void }) {
  const editor = useDeckEditor(job, setData);
  const setSlide = editor.selectBase; // Issues and findings point at the saved version's slide indexes
  const deck = job.deck;
  const checksById = new Map(job.verification?.checks.map((c) => [c.url, c]));
  const latest = posts?.[0];
  const live = posts?.find((p) => p.state === "published");
  const held = posts?.find((p) => p.state === "on_hold");

  return (
    <>
      <PageHeader
        title={deck?.title ?? job.prompt}
        description={
          `job ${job.id} · ${deck?.slides.length ?? 0} slides · ${reviewSummary(job)}` +
          (job.kind === "guide" && job.verification?.checked_at ? ` · facts checked ${formatDateTime(job.verification.checked_at)} KST` : "")
        }
      >
        {job.kind && (
          <StatusBadge tone="neutral">
            {job.kind === "guide" ? "Guide Deck" : "Event Deck"}
            {!job.requested_kind && " · auto"}
          </StatusBadge>
        )}
        {live?.permalink && live.mode !== "mock" && (
          <a href={live.permalink} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-[13px] font-medium text-primary hover:underline">
            View post <ExternalLink className="size-3.5" />
          </a>
        )}
        <JobStatusBadge status={job.status} mock={live?.mode === "mock"} />
        <DeckBar editor={editor} />
      </PageHeader>

      <PublishToast post={latest} />

      <div className="space-y-4">
        <PublishProgress post={latest} onChange={reloadPosts} />

        <SafeguardPanel job={job} onJump={setSlide} />

        {deck && deck.slides.length > 0 && <SlideWorkspace editor={editor} />}

        <div className="min-w-0 space-y-4">
          <PostsList posts={posts ?? []} />

          <Panel title="Automated checks" description="Advisory: you decide on these. Only the safeguard can block publishing.">
            <div className="flex flex-wrap gap-2">{CHECKS.map((c) => checkBadge(job, c.category, c.label))}</div>
            {job.issues.length > 0 && <IssueList issues={job.issues} onJump={setSlide} />}
          </Panel>

          <GuidePointsPanel job={job} />

          <SourceMaterialPanel job={job} />

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

          {/* Keyed by the held Post so its time prefills once the Posts load. */}
          <ReviewActions key={held?.id ?? "none"} job={job} onChange={setData} held={held} />
        </div>
      </div>
    </>
  );
}
