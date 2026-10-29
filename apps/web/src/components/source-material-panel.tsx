"use client";

import { ChevronDown } from "lucide-react";
import { useState } from "react";

import { Panel } from "@/components/page";
import { MaterialIcon, materialMeta } from "@/components/source-material";
import { StatusBadge } from "@/components/status-badge";
import { api } from "@/lib/api";
import type { Job, MaterialRef } from "@/lib/types";

/** One attached piece; opening it loads the text the agents were given. */
function MaterialRow({ m }: { m: MaterialRef }) {
  const [text, setText] = useState<string>();
  const [error, setError] = useState<string>();
  const load = () => {
    if (text !== undefined || m.image) return;
    api
      .getMaterial(m.id)
      .then((full) => setText(full.text))
      .catch((e: Error) => setError(e.message));
  };
  return (
    <li>
      <details className="group rounded-lg border" onToggle={(e) => e.currentTarget.open && load()}>
        <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-xs">
          <MaterialIcon m={m} className="text-muted-foreground" />
          <span className="min-w-0 truncate font-medium">{m.name}</span>
          <span className="hidden shrink-0 text-muted-foreground sm:inline">{materialMeta(m)}</span>
          {m.truncated && <StatusBadge tone="warning">first part only</StatusBadge>}
          <ChevronDown className="ml-auto size-3.5 shrink-0 text-muted-foreground transition-transform group-open:rotate-180" />
        </summary>
        <div className="border-t px-3 py-2 text-xs text-muted-foreground">
          {m.url && (
            <p className="mb-1.5 truncate">
              <a href={m.url} target="_blank" rel="noreferrer noopener" className="hover:underline">
                {m.url}
              </a>
            </p>
          )}
          {m.image ? (
            <p>An image: the agent read it directly.</p>
          ) : error ? (
            <p className="text-destructive">{error}</p>
          ) : text === undefined ? (
            <p>Loading…</p>
          ) : (
            <p className="max-h-48 overflow-y-auto whitespace-pre-line text-foreground">{text}</p>
          )}
        </div>
      </details>
    </li>
  );
}

/**
 * The Source material this Deck was made from (#20), and how its facts were used. Per-slide fact sources are #25.
 * Kept in its own component so the review page only gains one line.
 */
export function SourceMaterialPanel({ job }: { job: Job }) {
  const material = job.material ?? [];
  if (!material.length) return null;
  const excluded = new Set(job.verification?.excluded_event_ids ?? []);
  const briefs = job.briefs.filter((b) => !excluded.has(b.id));
  const facts = briefs.flatMap((b) => b.facts ?? []);
  const fromMaterial = facts.filter((f) => f.origin === "material" && f.verified).length;
  const fromLinks = facts.filter((f) => f.origin === "link" && f.verified).length;
  const unproven = facts.filter((f) => f.origin === "material" && f.verified === false).length;
  const conflicts = briefs.reduce((n, b) => n + (b.conflicts?.length ?? 0), 0);

  return (
    <Panel title="Source material" description="Your own material is the primary source: facts taken from it count as verified.">
      {job.verification && facts.length > 0 && (
        <div className="mb-3 flex flex-wrap gap-2">
          <StatusBadge tone="success">{`${fromMaterial} fact${fromMaterial === 1 ? "" : "s"} from your material`}</StatusBadge>
          <StatusBadge tone="neutral">{`${fromLinks} from checked links`}</StatusBadge>
          {conflicts > 0 && <StatusBadge tone="warning">{`${conflicts} contradiction${conflicts > 1 ? "s" : ""} · see Issues`}</StatusBadge>}
          {unproven > 0 && <StatusBadge tone="warning">{`${unproven} not found in your material`}</StatusBadge>}
        </div>
      )}
      <ul className="space-y-1.5">
        {material.map((m) => (
          <MaterialRow key={m.id} m={m} />
        ))}
      </ul>
    </Panel>
  );
}
