import Link from "next/link";

import { PageHeader, Panel } from "@/components/page";
import { JobStatusBadge } from "@/components/status-badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { getJobs } from "@/lib/api";
import { formatDateTime } from "@/lib/format";

export default async function ReviewQueuePage() {
  const jobs = await getJobs();
  const waiting = jobs.filter((j) => j.status === "READY_FOR_REVIEW").length;

  return (
    <>
      <PageHeader title="Review" description={`${waiting} card news waiting for your decision`} />
      <Panel>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Title</TableHead>
              <TableHead>Job</TableHead>
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
