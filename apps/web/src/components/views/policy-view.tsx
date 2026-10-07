"use client";

import { LoadState, Panel, StatRow } from "@/components/page";
import { StatusBadge, type Tone } from "@/components/status-badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api } from "@/lib/api";
import type { PolicyEvent } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { cn } from "@/lib/utils";

const RESULT: Record<PolicyEvent["result"], [Tone, string]> = {
  policy_denied: ["danger", "policy_denied"],
  fs_denied: ["danger", "fs_denied"],
  audit: ["warning", "audit · allowed"],
  allowed: ["neutral", "allowed"],
};

export function PolicyLogView() {
  const { data, error } = useApi(api.getPolicyLog, [], 2000);
  if (!data) return <LoadState error={error} />;
  const { stats, events } = data;
  // Denials from the most recent sandbox are the demo highlight (SPEC §7).
  const latest = events.find((e) => e.result === "policy_denied")?.sandbox;

  return (
    <>
      <StatRow metrics={stats} />

      <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
        Show
        <StatusBadge tone="danger">Denied</StatusBadge>
        <StatusBadge tone="warning">Audit</StatusBadge>
        <StatusBadge tone="neutral">Allowed</StatusBadge>
      </div>

      <Panel>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Time</TableHead>
              <TableHead>Sandbox</TableHead>
              <TableHead>Binary</TableHead>
              <TableHead>Host</TableHead>
              <TableHead>Method · Path</TableHead>
              <TableHead>Result</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {events.map((e, i) => {
              const highlight = e.result === "policy_denied" && e.sandbox === latest && i < 4;
              const [tone, label] = RESULT[e.result];
              return (
                <TableRow key={`${e.time}-${i}`} className={cn(highlight && "bg-danger-soft hover:bg-danger-soft")}>
                  <TableCell className="tabular-nums">{e.time}</TableCell>
                  <TableCell>{e.sandbox}</TableCell>
                  <TableCell className="font-mono text-xs">{e.binary}</TableCell>
                  <TableCell>{e.host}</TableCell>
                  <TableCell className={cn("font-mono text-xs", highlight && "font-semibold")}>{e.request}</TableCell>
                  <TableCell>
                    <StatusBadge tone={tone}>{label}</StatusBadge>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </Panel>
    </>
  );
}
