"use client";

import { Brainstorm } from "@/components/brainstorm";
import { LoadState } from "@/components/page";
import { api } from "@/lib/api";
import { useApi } from "@/lib/use-api";

/** Starts a fresh draft (seeded with the Mangwon sample) on every visit. */
export function BrainstormView() {
  const { data: draft, error } = useApi(() => api.createDraft(true));
  if (!draft) return <LoadState error={error} label="Creating draft…" />;
  return <Brainstorm initialDraft={draft} />;
}
