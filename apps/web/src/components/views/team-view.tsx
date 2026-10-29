"use client";

import { Check, Copy, Loader2, UserMinus } from "lucide-react";
import { useState } from "react";

import { LoadState, Panel } from "@/components/page";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { IS_MOCK, api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { setActiveOrgId } from "@/lib/org";
import { ROLES, ROLE_HINT, ROLE_LABEL, atLeast, useMe } from "@/lib/roles";
import { reloadTo } from "@/lib/supabase";
import type { InvitationCreated, Member, Role } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { cn } from "@/lib/utils";

const selectClass =
  "h-10 rounded-lg border bg-background px-3 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-60";

function RoleSelect({ value, onChange, disabled, label }: { value: Role; onChange: (r: Role) => void; disabled?: boolean; label: string }) {
  return (
    <select aria-label={label} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value as Role)} className={cn(selectClass, "h-8 text-[13px]")}>
      {ROLES.map((r) => (
        <option key={r} value={r}>{ROLE_LABEL[r]}</option>
      ))}
    </select>
  );
}

function CopyLink({ url }: { url: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      variant="outline"
      size="sm"
      onClick={() => navigator.clipboard.writeText(url).then(() => setCopied(true)).catch(() => {})}
    >
      {copied ? <Check /> : <Copy />} {copied ? "Copied" : "Copy link"}
    </Button>
  );
}

function InviteForm({ onInvited }: { onInvited: () => void }) {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("editor");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [sent, setSent] = useState<InvitationCreated>();

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(undefined);
    setSent(undefined);
    try {
      setSent(await api.invite(email.trim(), role));
      setEmail("");
      onInvited();
    } catch (err) {
      setError((err as Error).message.replace(/^\d+ /, ""));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel title="Invite someone" description="They get an email with a link that works only for that address. Links expire after 7 days.">
      <form onSubmit={submit} className="flex flex-col gap-2.5 md:flex-row md:items-end">
        <label className="flex-1 space-y-1.5">
          <span className="text-[13px] font-medium">Email</span>
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="name@example.com"
            className="h-10 w-full rounded-lg border bg-background px-3 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
          />
        </label>
        <label className="space-y-1.5">
          <span className="block text-[13px] font-medium">Role</span>
          <select value={role} onChange={(e) => setRole(e.target.value as Role)} className={cn(selectClass, "w-full md:w-40")}>
            {ROLES.map((r) => (
              <option key={r} value={r}>{ROLE_LABEL[r]}</option>
            ))}
          </select>
        </label>
        <Button type="submit" size="lg" disabled={busy || !email.trim()}>
          {busy && <Loader2 className="motion-safe:animate-spin" />} Send invite
        </Button>
      </form>
      <p className="mt-2 text-xs text-muted-foreground">{ROLE_LABEL[role]}: {ROLE_HINT[role]}.</p>
      {error && <p role="alert" className="mt-3 rounded-lg bg-danger-soft px-3 py-2 text-sm text-destructive">{error}</p>}
      {sent && (
        <div role="status" className="mt-3 flex flex-wrap items-center gap-2 rounded-lg bg-muted px-3 py-2 text-sm">
          <span className="flex-1">
            {sent.email_sent
              ? `Invitation emailed to ${sent.email}. You can also send them the link yourself.`
              : `Email isn't set up on this server, so nothing was sent. Send ${sent.email} this link yourself.`}
          </span>
          <CopyLink url={sent.invite_url} />
        </div>
      )}
    </Panel>
  );
}

export function TeamView() {
  const { me, role, loading } = useMe();
  const isAdmin = atLeast(role, "admin");
  const members = useApi(() => (IS_MOCK ? Promise.resolve([] as Member[]) : api.listMembers()));
  const invitations = useApi(() => (isAdmin && !IS_MOCK ? api.listInvitations() : Promise.resolve([])), [isAdmin]);
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState<string>();

  if (IS_MOCK) {
    return <LoadState error={new Error("Members need the real API: set NEXT_PUBLIC_API_URL and sign in.")} />;
  }
  if (!members.data || loading) return <LoadState error={members.error} />;

  const admins = members.data.filter((m) => m.role === "admin").length;
  const act = async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key);
    setError(undefined);
    try {
      await fn();
      await Promise.all([members.reload(), invitations.reload()]);
    } catch (e) {
      setError((e as Error).message.replace(/^\d+ /, ""));
    } finally {
      setBusy(undefined);
    }
  };

  return (
    <>
      {isAdmin && <InviteForm onInvited={invitations.reload} />}
      {error && <p role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-sm text-destructive">{error}</p>}

      <Panel
        title="Members"
        description={isAdmin ? "An Organization always keeps at least one Admin." : "Only Admins can invite people or change Roles."}
      >
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Email</TableHead>
              <TableHead>Role</TableHead>
              <TableHead>Joined</TableHead>
              {isAdmin && <TableHead className="text-right">Actions</TableHead>}
            </TableRow>
          </TableHeader>
          <TableBody>
            {members.data.map((m) => {
              const lastAdmin = m.role === "admin" && admins <= 1;
              const you = m.user_id === me?.user.id;
              return (
                <TableRow key={m.user_id}>
                  <TableCell className="font-medium">
                    {m.email ?? <span className="text-muted-foreground">{m.user_id.slice(0, 8)}</span>}
                    {you && <span className="ml-2 text-xs text-muted-foreground">(you)</span>}
                  </TableCell>
                  <TableCell>
                    {isAdmin ? (
                      <RoleSelect
                        label={`Role of ${m.email ?? m.user_id}`}
                        value={m.role}
                        disabled={busy !== undefined || lastAdmin}
                        onChange={(r) => act(`role:${m.user_id}`, () => api.changeRole(m.user_id, r))}
                      />
                    ) : (
                      <StatusBadge tone={m.role === "admin" ? "info" : "neutral"}>{ROLE_LABEL[m.role]}</StatusBadge>
                    )}
                  </TableCell>
                  <TableCell className="text-muted-foreground tabular-nums">{formatDateTime(m.joined_at)}</TableCell>
                  {isAdmin && (
                    <TableCell className="text-right">
                      <Button
                        variant="ghost"
                        size="sm"
                        className="text-destructive hover:text-destructive"
                        disabled={busy !== undefined || lastAdmin}
                        title={lastAdmin ? "Make someone else Admin first" : undefined}
                        onClick={() => {
                          if (window.confirm(you ? "Leave this Organization?" : `Remove ${m.email ?? "this Member"}?`)) {
                            void act(`remove:${m.user_id}`, () => api.removeMember(m.user_id)).then(() => {
                              if (!you) return;
                              setActiveOrgId(null); // back to whichever Organization is left
                              reloadTo("/");
                            });
                          }
                        }}
                      >
                        <UserMinus /> {you ? "Leave" : "Remove"}
                      </Button>
                    </TableCell>
                  )}
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </Panel>

      {isAdmin && (invitations.data?.length ?? 0) > 0 && (
        <Panel title="Pending invitations" description="To send a link again, invite the same address: the old link stops working.">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Email</TableHead>
                <TableHead>Role</TableHead>
                <TableHead>Expires</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {invitations.data!.map((i) => (
                <TableRow key={i.id}>
                  <TableCell className="font-medium">{i.email}</TableCell>
                  <TableCell>{ROLE_LABEL[i.role]}</TableCell>
                  <TableCell className="text-muted-foreground tabular-nums">{formatDateTime(i.expires_at)}</TableCell>
                  <TableCell className="text-right">
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-destructive hover:text-destructive"
                      disabled={busy !== undefined}
                      onClick={() => act(`revoke:${i.id}`, () => api.revokeInvitation(i.id))}
                    >
                      Withdraw
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Panel>
      )}
    </>
  );
}
