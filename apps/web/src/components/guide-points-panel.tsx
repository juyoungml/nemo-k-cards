"use client";

import { Panel } from "@/components/page";
import { StatusBadge } from "@/components/status-badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDateTime } from "@/lib/format";
import type { FactSource, Job } from "@/lib/types";
import { cn } from "@/lib/utils";

function where(f: FactSource, job: Job) {
  if (f.origin === "material") {
    const m = job.material?.find((x) => x.id === f.material_id);
    return `Your material${m ? `: ${m.name}` : ""}`;
  }
  try {
    return new URL(f.url ?? "").hostname.replace(/^www\./, "");
  } catch {
    return f.url ?? "no link";
  }
}

/**
 * A Guide Deck's points (#27) and where each one's facts came from. Every point on the Deck rests on an Official
 * source the host checked; points without one were left off at Verify and are shown greyed with the reason.
 */
export function GuidePointsPanel({ job }: { job: Job }) {
  const guide = job.guide;
  if (!guide) return null;
  const left = new Set(job.verification?.excluded_point_ids ?? []);
  const checked = job.verification?.checked_at;
  return (
    <Panel
      title="Guide points & official sources"
      description={
        `Every point on the Deck traces to a government or agency site, the operator's own site, or your Source material.` +
        (checked ? ` Facts checked ${formatDateTime(checked)} KST.` : "")
      }
    >
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Point</TableHead>
            <TableHead>Source</TableHead>
            <TableHead>Status</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {guide.points.map((p) => {
            const out = left.has(p.id);
            const facts = p.facts.length ? p.facts : [null];
            return facts.map((f, i) => (
              <TableRow key={`${p.id}-${i}`} className={cn(out && "text-muted-foreground")}>
                <TableCell className="max-w-[28ch] whitespace-normal pr-6 align-top">{i === 0 && <span className={cn(!out && "font-medium")}>{p.title}</span>}</TableCell>
                <TableCell className="align-top">
                  {f?.url && f.origin === "link" ? (
                    <a href={f.url} target="_blank" rel="noreferrer" className="hover:underline">
                      {where(f, job)}
                    </a>
                  ) : f ? (
                    where(f, job)
                  ) : (
                    "no source recorded"
                  )}
                </TableCell>
                <TableCell className="align-top">
                  {f?.verified ? (
                    <StatusBadge tone="success">Official ✓</StatusBadge>
                  ) : (
                    <span className="inline-flex flex-wrap items-center gap-1.5">
                      <StatusBadge tone={out ? "danger" : "warning"}>{out ? "Left off" : "Not counted"}</StatusBadge>
                      {f?.note && <span className="text-xs text-muted-foreground">{f.note}</span>}
                    </span>
                  )}
                </TableCell>
              </TableRow>
            ));
          })}
        </TableBody>
      </Table>
    </Panel>
  );
}
