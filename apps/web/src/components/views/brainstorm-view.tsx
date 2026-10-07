"use client";

import { Brainstorm } from "@/components/brainstorm";
import { LoadState } from "@/components/page";
import { api } from "@/lib/api";
import { useApi } from "@/lib/use-api";

/** Starts a fresh draft on every visit (the backend seeds the Mangwon sample only in DEMO_MODE=fixture). */
export function BrainstormView() {
  const { data: draft, error } = useApi(() => api.createDraft(true));
  if (!draft) return <LoadState error={error} label="Creating draft…" />;
  return <Brainstorm initialDraft={draft} />;
}
