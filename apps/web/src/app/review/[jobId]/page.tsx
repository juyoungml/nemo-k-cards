import { notFound } from "next/navigation";

import { PageHeader, Panel } from "@/components/page";
import { ReviewActions } from "@/components/review-actions";
import { SlidePreview } from "@/components/slide-preview";
import { JobStatusBadge, StatusBadge, type Tone } from "@/components/status-badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { getJob, getJobs } from "@/lib/api";
import type { Issue, Job, LinkCheck } from "@/lib/types";
import { cn } from "@/lib/utils";

export async function generateStaticParams() {
  return (await getJobs()).map((j) => ({ jobId: j.id }));
}

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

export default async function ReviewDetailPage(props: PageProps<"/review/[jobId]">) {
  const { jobId } = await props.params;
  const job = await getJob(jobId);
  if (!job) notFound();
  const deck = job.deck;
  const checksById = new Map(job.verification?.checks.map((c) => [c.url, c]));

  return (
    <>
      <PageHeader
        title={deck?.title ?? job.prompt}
        description={`job ${job.id} · ${deck?.slides.length ?? 0} slides · ${job.status === "READY_FOR_REVIEW" ? "automated checks passed · waiting for your decision" : job.status.toLowerCase().replaceAll("_", " ")}`}
      >
        <JobStatusBadge status={job.status} />
      </PageHeader>

      <div className="flex items-start gap-5">
        {deck && deck.slides.length > 0 && <SlidePreview slides={deck.slides} />}

        <div className="min-w-0 flex-1 space-y-4">
          <Panel title="Automated checks">
            <div className="flex flex-wrap gap-2">{CHECKS.map((c) => checkBadge(job, c.category, c.label))}</div>
            {job.issues.length > 0 && (
              <ul className="mt-3 space-y-2">
                {job.issues.map((i, n) => (
                  <li
                    key={n}
                    className={cn(
                      "rounded-lg px-3 py-2.5 text-xs",
                      i.severity === "block" ? "bg-danger-soft text-destructive" : "bg-warning-soft text-warning",
                    )}
                  >
                    {i.severity} · {i.slide_index != null && `slide ${i.slide_index + 1} · `}
                    {i.category} — {i.message}
                  </li>
                ))}
              </ul>
            )}
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

          <ReviewActions jobId={job.id} status={job.status} initialCaption={deck?.caption ?? ""} />
        </div>
      </div>
    </>
  );
}
