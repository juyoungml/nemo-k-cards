"use client";

import Link from "next/link";

import { LoadState, Panel, StatRow } from "@/components/page";
import { JobStatusBadge, StatusBadge } from "@/components/status-badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api } from "@/lib/api";
import { atLeast, useMe } from "@/lib/roles";
import { formatDateTime, formatNumber } from "@/lib/format";
import { useApi } from "@/lib/use-api";

export function DashboardView() {
  const metrics = useApi(api.getMetrics, [], 5000);
  const accounts = useApi(api.getInstagramAccounts);
  const jobs = useApi(api.listJobs, [], 3000);
  const { role } = useMe();
  const profile = useApi(api.getOrgProfile);

  if (!metrics.data || !accounts.data || !jobs.data) return <LoadState error={metrics.error ?? accounts.error ?? jobs.error} />;

  return (
    <>
      {atLeast(role, "admin") && profile.data && !profile.data.complete && (
        <div role="status" className="flex flex-wrap items-center gap-3 rounded-xl border bg-card p-4 text-sm">
          <span className="flex-1">
            <span className="font-semibold">Fill in your Organization profile.</span>{" "}
            <span className="text-muted-foreground">Until you do, the agents write as a neutral guide, not as you.</span>
          </span>
          <Link href="/profile" className="text-[13px] font-medium text-primary hover:underline">
            Open profile →
          </Link>
        </div>
      )}
      <StatRow metrics={metrics.data} />

      <div className="flex flex-col gap-4 lg:flex-row lg:items-start">
        <Panel
          title="Instagram accounts"
          description="This Organization's connected accounts"
          className="w-full lg:w-[520px] lg:shrink-0"
          action={
            <Link href="/instagram" className="text-[13px] font-medium text-primary hover:underline">
              Manage →
            </Link>
          }
        >
          {accounts.data.length === 0 ? (
            <p className="py-4 text-sm text-muted-foreground">
              No Instagram account connected yet.{" "}
              <Link href="/instagram" className="font-medium text-primary hover:underline">
                Connect one
              </Link>
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Account</TableHead>
                  <TableHead>Platform</TableHead>
                  <TableHead className="text-right">Followers</TableHead>
                  <TableHead>Status</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {accounts.data.map((c) => (
                  <TableRow key={c.id}>
                    <TableCell>{c.handle}</TableCell>
                    <TableCell>{c.platform}</TableCell>
                    <TableCell className="text-right tabular-nums">{c.followers === null ? "—" : formatNumber(c.followers)}</TableCell>
                    <TableCell>
                      <StatusBadge tone={c.live ? "success" : "warning"}>{c.live ? "Connected" : "Reconnect needed"}</StatusBadge>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </Panel>

        <Panel
          title="Recent decks"
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
              {jobs.data.filter((j) => j.status !== "FAILED").slice(0, 8).map((j) => (
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
