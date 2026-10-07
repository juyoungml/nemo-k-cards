import Link from "next/link";
import { Plus } from "lucide-react";

import { PageHeader, Panel, StatRow } from "@/components/page";
import { JobStatusBadge, StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { getChannels, getJobs, getMetrics } from "@/lib/api";
import { formatDateTime, formatNumber } from "@/lib/format";

export default async function DashboardPage() {
  const [metrics, channels, jobs] = await Promise.all([getMetrics(), getChannels(), getJobs()]);

  return (
    <>
      <PageHeader title="Dashboard" description="Channels, reach and recent card news for @whatsonkorea">
        <Button size="lg" asChild>
          <Link href="/jobs/new">
            <Plus /> New Job
          </Link>
        </Button>
      </PageHeader>

      <StatRow metrics={metrics} />

      <div className="flex items-start gap-4">
        <Panel
          title="SNS / Channels"
          description="IG Insights live for @whatsonkorea · others are mock"
          className="w-[520px] shrink-0"
        >
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Channel</TableHead>
                <TableHead>Platform</TableHead>
                <TableHead className="text-right">Followers</TableHead>
                <TableHead>Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {channels.map((c) => (
                <TableRow key={`${c.handle}-${c.platform}`}>
                  <TableCell>{c.handle}</TableCell>
                  <TableCell>{c.platform}</TableCell>
                  <TableCell className="text-right tabular-nums">{formatNumber(c.followers)}</TableCell>
                  <TableCell>
                    <StatusBadge tone={c.live ? "success" : "neutral"}>{c.live ? "Live" : "Mock"}</StatusBadge>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Panel>

        <Panel
          title="Recent card news"
          className="min-w-0 flex-1"
          action={
            <Link href="/review" className="text-[13px] font-medium text-primary hover:underline">
              View all →
            </Link>
          }
        >
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Title</TableHead>
                <TableHead>Slides</TableHead>
                <TableHead>Created</TableHead>
                <TableHead>Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {jobs.map((j) => (
                <TableRow key={j.id}>
                  <TableCell className="whitespace-normal">
                    <Link href={`/review/${j.id}`} className="hover:underline">
                      {j.deck?.title ?? j.prompt}
                    </Link>
                  </TableCell>
                  <TableCell>{j.deck?.slides.length ?? "—"}</TableCell>
                  <TableCell>{formatDateTime(j.created_at)}</TableCell>
                  <TableCell>
                    <JobStatusBadge status={j.status} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Panel>
      </div>
    </>
  );
}
