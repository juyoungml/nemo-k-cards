"use client";

import Link from "next/link";

import { LoadState, Panel } from "@/components/page";
import { JobStatusBadge } from "@/components/status-badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { useApi } from "@/lib/use-api";

export function ReviewQueueView() {
  const { data: jobs, error } = useApi(api.listJobs, [], 3000);
  if (!jobs) return <LoadState error={error} />;
  const waiting = jobs.filter((j) => j.status === "READY_FOR_REVIEW").length;

  return (
    <>
      <p className="-mt-4 text-sm text-muted-foreground">{waiting} card news waiting for your decision</p>
      <Panel>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Title</TableHead>
              <TableHead>Job</TableHead>
              <TableHead>Mode</TableHead>
              <TableHead>Slides</TableHead>
              <TableHead>Issues</TableHead>
              <TableHead>Created</TableHead>
              <TableHead>Status</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {jobs.map((j) => (
              <TableRow key={j.id}>
                <TableCell>
                  <Link href={`/review/${j.id}`} className="font-medium hover:underline">
                    {j.deck?.title ?? j.prompt}
                  </Link>
                </TableCell>
                <TableCell className="font-mono text-xs">{j.id}</TableCell>
                <TableCell className="text-xs text-muted-foreground">{j.source ?? "quick"}</TableCell>
                <TableCell>{j.deck?.slides.length ?? "—"}</TableCell>
                <TableCell>{j.issues.length || "—"}</TableCell>
                <TableCell>{formatDateTime(j.created_at)}</TableCell>
                <TableCell>
                  <JobStatusBadge status={j.status} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Panel>
    </>
  );
}
