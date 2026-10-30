"use client";

import Link from "next/link";
import { useTransition } from "react";

import { LoadState, Panel } from "@/components/page";
import { MODE, postBadge } from "@/components/publish-progress";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { atLeast, useMe } from "@/lib/roles";
import type { Post } from "@/lib/types";
import { useApi } from "@/lib/use-api";

/** The Organization's Posts (#26): upcoming (scheduled and on hold) soonest first, then recent ones newest first. */
export function PostsView() {
  const upcoming = useApi(() => api.listPosts(undefined, "upcoming"), [], 5000);
  const recent = useApi(() => api.listPosts(undefined, "recent"), [], 5000);
  const { data: jobs } = useApi(() => api.listJobs(), []);
  const title = (p: Post) => jobs?.find((j) => j.id === p.job_id)?.deck?.title ?? jobs?.find((j) => j.id === p.job_id)?.prompt ?? p.job_id;
  const reload = () => {
    void upcoming.reload();
    void recent.reload();
  };

  if (!upcoming.data || !recent.data) return <LoadState error={upcoming.error ?? recent.error} />;
  return (
    <div className="space-y-4">
      <Panel title="Upcoming" description="Scheduled Posts wait for their time. A Post on hold waits for a person: open its Deck to resume it.">
        {upcoming.data.length ? (
          <PostTable posts={upcoming.data} title={title} upcoming onChange={reload} />
        ) : (
          <p className="text-[13px] text-muted-foreground">Nothing scheduled. Choose “Schedule” when you approve a Deck.</p>
        )}
      </Panel>
      <Panel title="Recent">
        {recent.data.length ? (
          <PostTable posts={recent.data} title={title} onChange={reload} />
        ) : (
          <p className="text-[13px] text-muted-foreground">No Posts yet.</p>
        )}
      </Panel>
    </div>
  );
}

function PostTable({ posts, title, upcoming = false, onChange }: {
  posts: Post[];
  title: (p: Post) => string;
  upcoming?: boolean;
  onChange: () => void;
}) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>{upcoming ? "Posts at (KST)" : "When (KST)"}</TableHead>
          <TableHead>Deck</TableHead>
          <TableHead>Account</TableHead>
          <TableHead>State</TableHead>
          {upcoming && <TableHead className="text-right">Actions</TableHead>}
        </TableRow>
      </TableHeader>
      <TableBody>
        {posts.map((p) => (
          <TableRow key={p.id}>
            <TableCell className="whitespace-nowrap tabular-nums">
              {formatDateTime(upcoming && p.scheduled_at ? p.scheduled_at : (p.published_at ?? p.finished_at ?? p.created_at))}
            </TableCell>
            <TableCell className="max-w-[24ch] truncate">
              <Link href={`/review/${p.job_id}`} className="font-medium hover:underline">
                {title(p)}
              </Link>
              {(p.deck_version ?? 1) > 1 && <span className="text-muted-foreground"> · v{p.deck_version}</span>}
            </TableCell>
            <TableCell>
              <span className="inline-flex items-center gap-2">
                {p.handle ?? "no account"}
                <StatusBadge tone={MODE[p.mode][0]}>{MODE[p.mode][1]}</StatusBadge>
              </span>
            </TableCell>
            <TableCell title={p.held_reason ?? p.last_error ?? undefined}>{postBadge(p)}</TableCell>
            {upcoming && (
              <TableCell className="text-right">
                <CancelButton post={p} onChange={onChange} />
              </TableCell>
            )}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function CancelButton({ post, onChange }: { post: Post; onChange: () => void }) {
  const { role } = useMe();
  const [pending, startTransition] = useTransition();
  if (!atLeast(role, "approver")) return null;
  return (
    <span className="inline-flex gap-2">
      <Link href={`/review/${post.job_id}#publish-progress`} className="inline-flex h-7 items-center rounded-lg border px-2.5 text-[0.8rem] font-medium hover:bg-muted">
        {post.state === "on_hold" ? "Resume" : "Reschedule"}
      </Link>
      <Button
        size="sm"
        variant="outline"
        disabled={pending}
        onClick={() =>
          startTransition(async () => {
            try {
              await api.cancelPost(post.id);
            } finally {
              onChange();
            }
          })
        }
      >
        Cancel
      </Button>
    </span>
  );
}
