import { Download } from "lucide-react";

import { PageHeader, Panel, StatRow } from "@/components/page";
import { StatusBadge, type Tone } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { getPolicyLog } from "@/lib/api";
import type { PolicyEvent } from "@/lib/types";
import { cn } from "@/lib/utils";

const RESULT: Record<PolicyEvent["result"], [Tone, string]> = {
  policy_denied: ["danger", "policy_denied"],
  fs_denied: ["danger", "fs_denied"],
  audit: ["warning", "audit · allowed"],
  allowed: ["neutral", "allowed"],
};

export default async function PolicyLogPage() {
  const { stats, events } = await getPolicyLog();

  return (
    <>
      <PageHeader
        title="Policy Log"
        description="OpenShell sandbox network & filesystem events · source: openshell logs <sandbox> --source sandbox"
      >
        <Button variant="outline" size="lg">
          <Download /> Export
        </Button>
      </PageHeader>

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
              // Denials from the latest job are the demo highlight (SPEC §7).
              const highlight = e.result === "policy_denied" && i < 2;
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
