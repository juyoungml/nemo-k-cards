"use client";

import { Loader2, Plus, Unplug } from "lucide-react";
import { useEffect, useState } from "react";

import { LoadState, Panel } from "@/components/page";
import { StatusBadge, type Tone } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { IS_MOCK, api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { atLeast, useMe } from "@/lib/roles";
import type { InstagramAccount } from "@/lib/types";
import { useApi } from "@/lib/use-api";

const ACCOUNT_TYPE: Record<InstagramAccount["account_type"], string> = { BUSINESS: "Business", MEDIA_CREATOR: "Creator" };
const STATUS: Record<InstagramAccount["status"], [Tone, string]> = {
  connected: ["success", "Connected"],
  expiring: ["warning", "Expiring soon"],
  revoked: ["danger", "Reconnect needed"],
};

/** What the API's Instagram callback reports in `?error=` (backend/app/instagram_accounts.py). */
const CALLBACK_ERRORS: Record<string, string> = {
  denied: "You cancelled on Instagram, so nothing was connected.",
  state: "That connection attempt expired or was already used. Start again with Connect Instagram.",
  instagram: "Instagram didn't complete the connection. Try again in a few minutes.",
  not_pilot: "Only Pilot Organizations can connect Instagram accounts while Meta reviews our app.",
  not_admin: "Only Admins can connect Instagram accounts.",
  limit: "Your plan's Instagram account limit is reached. Disconnect an account to connect another.",
  unavailable: "This server can't store Instagram tokens safely yet. Nothing was connected.",
};

function PersonalAccountGuide() {
  return (
    <div role="alert" className="space-y-2 rounded-lg bg-warning-soft px-4 py-3 text-sm">
      <p className="font-semibold">That&apos;s a personal Instagram account</p>
      <p>
        Only Business and Creator accounts can publish through Instagram&apos;s API. Switching is free, keeps your
        posts and followers, and can be undone.
      </p>
      <ol className="list-decimal space-y-0.5 pl-5">
        <li>In the Instagram app, open your profile and tap the menu.</li>
        <li>Go to Settings and activity, then Account type and tools.</li>
        <li>Tap Switch to professional account and choose Business or Creator.</li>
        <li>Come back here and connect again.</li>
      </ol>
    </div>
  );
}

/** Reads (once) and clears the outcome the API's callback put in the address bar. */
function useCallbackResult() {
  const [result, setResult] = useState<{ connected?: string; error?: string }>();
  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    const connected = q.get("connected") ?? undefined;
    const error = q.get("error") ?? undefined;
    if (!connected && !error) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- one-shot read of the redirect's query string
    setResult({ connected, error });
    window.history.replaceState(null, "", window.location.pathname);
  }, []);
  return result;
}

export function InstagramView() {
  const { role, loading } = useMe();
  const isAdmin = atLeast(role, "admin") && !IS_MOCK; // the mock API can't connect or disconnect
  const accounts = useApi(api.getInstagramAccounts);
  const connection = useApi(api.getInstagramConnection);
  const result = useCallbackResult();
  const [busy, setBusy] = useState<string>();
  const [error, setError] = useState<string>();

  if (!accounts.data || !connection.data || loading) return <LoadState error={accounts.error ?? connection.error} />;
  const c = connection.data;

  const connect = async () => {
    setBusy("connect");
    setError(undefined);
    try {
      const { authorize_url } = await api.connectInstagram();
      window.location.assign(authorize_url); // Instagram brings the browser back through the API to this page
    } catch (e) {
      setError((e as Error).message.replace(/^\d+ /, ""));
      setBusy(undefined);
    }
  };

  const disconnect = async (a: InstagramAccount) => {
    if (!window.confirm(`Disconnect ${a.handle}? Decks can't be posted to it until an Admin connects it again.`)) return;
    setBusy(a.id);
    setError(undefined);
    try {
      await api.disconnectInstagram(a.id);
      await Promise.all([accounts.reload(), connection.reload()]);
    } catch (e) {
      setError((e as Error).message.replace(/^\d+ /, ""));
    } finally {
      setBusy(undefined);
    }
  };

  return (
    <>
      {result?.connected && (
        <p role="status" className="rounded-lg bg-success-soft px-3 py-2 text-sm text-success">
          Connected @{result.connected}.
        </p>
      )}
      {result?.error === "personal_account" && <PersonalAccountGuide />}
      {result?.error && result.error !== "personal_account" && (
        <p role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-sm text-destructive">
          {CALLBACK_ERRORS[result.error] ?? "The connection didn't complete. Try again."}
        </p>
      )}
      {error && <p role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-sm text-destructive">{error}</p>}

      <Panel
        title="Instagram accounts"
        description={`${c.connected} of ${c.limit} connected · Business or Creator accounts only`}
        action={
          isAdmin && c.can_connect ? (
            <Button onClick={connect} disabled={busy !== undefined}>
              {busy === "connect" ? <Loader2 className="motion-safe:animate-spin" /> : <Plus />} Connect Instagram
            </Button>
          ) : undefined
        }
      >
        {!c.can_connect && c.reason && (
          <p className="mb-3 rounded-lg bg-muted px-3 py-2 text-sm text-muted-foreground">{c.reason}</p>
        )}
        {accounts.data.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">No Instagram account connected yet.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Account</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Token expires</TableHead>
                <TableHead>Connected</TableHead>
                {isAdmin && <TableHead className="text-right">Actions</TableHead>}
              </TableRow>
            </TableHeader>
            <TableBody>
              {accounts.data.map((a) => {
                const [tone, label] = STATUS[a.status];
                return (
                  <TableRow key={a.id}>
                    <TableCell className="font-medium">{a.handle}</TableCell>
                    <TableCell>{ACCOUNT_TYPE[a.account_type]}</TableCell>
                    <TableCell>
                      <StatusBadge tone={tone}>{label}</StatusBadge>
                    </TableCell>
                    <TableCell className="text-muted-foreground tabular-nums">
                      {a.token_expires_at ? formatDateTime(a.token_expires_at) : "—"}
                    </TableCell>
                    <TableCell className="text-muted-foreground tabular-nums">{formatDateTime(a.connected_at)}</TableCell>
                    {isAdmin && (
                      <TableCell className="text-right">
                        <Button
                          variant="ghost"
                          size="sm"
                          className="text-destructive hover:text-destructive"
                          disabled={busy !== undefined}
                          onClick={() => disconnect(a)}
                        >
                          <Unplug /> Disconnect
                        </Button>
                      </TableCell>
                    )}
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
        {isAdmin && c.can_connect && (
          <p className="mt-3 text-xs text-muted-foreground">
            You&apos;ll sign in on Instagram with the account you want to connect. While Meta reviews our app, that account
            must have accepted our tester invitation.
          </p>
        )}
      </Panel>
    </>
  );
}
