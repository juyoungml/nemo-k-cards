"use client";

import Link from "next/link";
import { useState } from "react";

import { LoadState, Panel } from "@/components/page";
import { StatusBadge, type Tone } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { IS_MOCK, api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { ROLE_LABEL, atLeast, useMe } from "@/lib/roles";
import type { AuditEvent, Role } from "@/lib/types";
import { useApi } from "@/lib/use-api";

const ACTION: Record<string, [Tone, string]> = {
  "deck.overridden": ["danger", "Override"],
  "deck.approved": ["success", "Approved"],
  "deck.rejected": ["warning", "Rejected"],
  "deck.edited": ["info", "Edited"],
  "deck.approve_refused": ["danger", "Approval refused"],
  "safeguard.blocked": ["danger", "Blocked by safeguard"],
  "safeguard.edit_blocked": ["danger", "Caption edit blocked"],
  "job.created": ["neutral", "Deck started"],
  "material.added": ["neutral", "Source material added"],
  "member.invited": ["neutral", "Invited"],
  "invitation.revoked": ["neutral", "Invitation withdrawn"],
  "member.joined": ["neutral", "Joined"],
  "member.role_changed": ["info", "Role changed"],
  "member.removed": ["warning", "Removed"],
  "org.created": ["neutral", "Organization created"],
  "instagram.connected": ["success", "Instagram connected"],
  "instagram.disconnected": ["warning", "Instagram disconnected"],
  "post.published": ["success", "Post published"],
  "post.failed": ["danger", "Post not posted"],
  "post.unknown": ["warning", "Post outcome unknown"],
  "post.unknown_alert": ["warning", "Post needs a check"],
  "post.settled": ["info", "Post settled by reconciler"],
  "post.resolved": ["info", "Post resolved by hand"],
};

const role = (r: unknown) => (typeof r === "string" && r in ROLE_LABEL ? ROLE_LABEL[r as Role] : String(r ?? ""));

/** One readable line for an event's detail; the full JSON is in the row's title. */
function describe(e: AuditEvent): React.ReactNode {
  const d = e.detail;
  switch (e.action) {
    case "deck.overridden":
      return <>Reason: “{String(d.reason)}” · {(d.issues as unknown[] | undefined)?.length ?? 0} blocking issue(s)</>;
    case "deck.rejected":
      return <>Reason: “{String(d.reason)}”</>;
    case "deck.edited":
      return <>Caption changed</>;
    case "deck.approve_refused":
      return d.why === "no_safeguard_verdict" ? (
        <>No safeguard verdict: regenerate</>
      ) : (
        <>
          Blocked by safeguard{d.override_reason ? <> · Override reason given: “{String(d.override_reason)}”</> : null}
        </>
      );
    case "safeguard.blocked":
    case "safeguard.edit_blocked":
      return (
        <>
          {e.action === "safeguard.blocked" ? `${d.stage === "publish" ? "At publish" : "Deck"} (Job started by this Member)` : `At ${String(d.stage)}`} ·{" "}
          {(d.findings as unknown[] | undefined)?.length ?? 0} finding(s) · {String(d.summary ?? "")}
        </>
      );
    case "member.invited":
    case "member.joined":
      return <>as {role(d.role)}</>;
    case "member.role_changed":
      return <>{role(d.before)} → {role(d.after)}</>;
    case "member.removed":
      return <>was {role(d.role)}</>;
    case "org.created":
      return <>{String(d.name)}</>;
    case "instagram.connected":
      return <>{d.account_type === "MEDIA_CREATOR" ? "Creator" : "Business"} account{d.reconnected ? " · reconnected" : ""}</>;
    case "post.published":
    case "post.failed":
    case "post.unknown":
    case "post.unknown_alert":
    case "post.settled":
    case "post.resolved":
      return (
        <>
          {String(d.mode ?? "")} · {String(d.account ?? "no account")}
          {d.error ? ` · ${String(d.error)}` : ""}
          {e.action === "post.resolved" ? ` · ${d.posted ? "posted" : "not posted"}` : ""}
          {e.action === "post.settled" && d.container ? ` · container ${String(d.container)}` : ""}
        </>
      );
    case "material.added":
      return <>{String(d.kind)}: {String(d.name ?? d.url ?? "")}</>;
    default:
      return null;
  }
}

function Target({ e }: { e: AuditEvent }) {
  if (!e.target) return null;
  if (e.action.startsWith("deck.") || e.action.startsWith("safeguard.") || e.action.startsWith("post.") || e.action === "job.created") {
    return <Link href={`/review/${e.target}`} className="font-mono text-xs hover:underline">{e.target}</Link>;
  }
  return <span className="text-[13px]">{e.target}</span>;
}

export function AuditView() {
  const { role: myRole, loading } = useMe();
  const isAdmin = atLeast(myRole, "admin");
  const { data, error, setData } = useApi(() => (isAdmin && !IS_MOCK ? api.getAudit() : Promise.resolve([] as AuditEvent[])), [isAdmin]);
  const [more, setMore] = useState(true);

  if (IS_MOCK) return <LoadState error={new Error("The audit log needs the real API: set NEXT_PUBLIC_API_URL and sign in.")} />;
  if (loading) return <LoadState />;
  if (!isAdmin) return <LoadState error={new Error("Only Admins can read the audit log.")} />;
  if (!data) return <LoadState error={error} />;

  const older = async () => {
    const next = await api.getAudit(data[data.length - 1]?.id);
    setMore(next.length === 100);
    setData([...data, ...next]);
  };

  return (
    <Panel description="Append-only: entries can't be edited or deleted.">
      {data.length === 0 ? (
        <p className="text-sm text-muted-foreground">Nothing recorded yet.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Time</TableHead>
              <TableHead>Who</TableHead>
              <TableHead>Action</TableHead>
              <TableHead>On</TableHead>
              <TableHead>Details</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.map((e) => {
              const [tone, label] = ACTION[e.action] ?? (["neutral", e.action] as [Tone, string]);
              return (
                <TableRow key={e.id} title={JSON.stringify(e.detail, null, 2)}>
                  <TableCell className="text-muted-foreground tabular-nums">{formatDateTime(e.at)}</TableCell>
                  <TableCell className="text-[13px]">{e.actor_email ?? e.actor.slice(0, 8)}</TableCell>
                  <TableCell><StatusBadge tone={tone}>{label}</StatusBadge></TableCell>
                  <TableCell><Target e={e} /></TableCell>
                  <TableCell className="max-w-[28rem] text-[13px] whitespace-normal">{describe(e)}</TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      )}
      {data.length >= 100 && more && (
        <Button variant="outline" size="lg" className="mt-3 w-full" onClick={older}>Load older entries</Button>
      )}
    </Panel>
  );
}
