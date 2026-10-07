"use client";

import Link from "next/link";

import { LoadState, Panel, StatRow } from "@/components/page";
import { JobStatusBadge, StatusBadge } from "@/components/status-badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api } from "@/lib/api";
import { formatDateTime, formatNumber } from "@/lib/format";
import { useApi } from "@/lib/use-api";

export function DashboardView() {
  const metrics = useApi(api.getMetrics, [], 5000);
  const channels = useApi(api.getChannels);
  const jobs = useApi(api.listJobs, [], 3000);

  if (!metrics.data || !channels.data || !jobs.data) return <LoadState error={metrics.error ?? channels.error ?? jobs.error} />;

  return (
    <>
      <StatRow metrics={metrics.data} />

      <div className="flex items-start gap-4">
        <Panel title="SNS / Channels" description="IG Insights live for @whatsonkorea · others are mock" className="w-[520px] shrink-0">
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
              {channels.data.map((c) => (
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
              {jobs.data.slice(0, 8).map((j) => (
                <TableRow key={j.id}>
                  <TableCell className="whitespace-normal">
                    <Link href={`/review/${j.id}`} className="hover:underline">
                      {j.deck?.title ?? j.prompt}
                    </Link>
                  </TableCell>
                  <TableCell>{j.deck?.slides.length ?? "—"}</TableCell>
                  <TableCell>{formatDateTime(j.created_at)}</TableCell>
                  <TableCell>
                    <JobStatusBadge status={j.status} mock={!!j.published_url?.includes("/p/MOCK")} />
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
