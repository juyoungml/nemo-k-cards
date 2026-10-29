"use client";

import { BrainstormEditor } from "@/components/brainstorm-editor";
import { LoadState } from "@/components/page";
import { api } from "@/lib/api";
import { useApi } from "@/lib/use-api";

/** Starts a fresh Brainstorm on every visit (the backend seeds the Mangwon sample only in DEMO_MODE=fixture). */
export function BrainstormView() {
  const { data: brainstorm, error } = useApi(() => api.createBrainstorm(true));
  if (!brainstorm) return <LoadState error={error} label="Starting brainstorm…" />;
  return <BrainstormEditor initialBrainstorm={brainstorm} />;
}
