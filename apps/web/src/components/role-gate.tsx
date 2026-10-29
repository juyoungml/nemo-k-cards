"use client";

import { LoadState } from "@/components/page";
import { ROLE_LABEL, atLeast, useMe } from "@/lib/roles";
import type { Role } from "@/lib/types";

/** Renders `children` only for Members with at least `min` Role; others see why (the API would refuse anyway). */
export function RoleGate({ min, what, children }: { min: Role; what: string; children: React.ReactNode }) {
  const { role, loading } = useMe();
  if (loading) return <LoadState />;
  if (!atLeast(role, min)) {
    return (
      <div role="status" className="rounded-xl border bg-card p-5 text-sm text-muted-foreground">
        {what} needs the {ROLE_LABEL[min]} Role or higher{role ? `; you are a ${ROLE_LABEL[role]} here` : ""}. Ask an
        Admin to change your Role.
      </div>
    );
  }
  return <>{children}</>;
}

/** Shows `children` only to Members with at least `min` Role (nothing while loading). */
export function RoleOnly({ min, children }: { min: Role; children: React.ReactNode }) {
  const { role } = useMe();
  return atLeast(role, min) ? <>{children}</> : null;
}
