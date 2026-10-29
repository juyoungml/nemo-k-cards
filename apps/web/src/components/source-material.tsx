"use client";

import { useLayoutEffect, useRef, useState } from "react";
import { FileText, ImageIcon, Link2, Paperclip, StickyNote, X } from "lucide-react";

import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import type { MaterialRef } from "@/lib/types";
import { cn } from "@/lib/utils";

/** Source material (#20; GLOSSARY): the Organization's own links, files and notes. Limits match backend/app/materials.py. */
export const MAX_MATERIALS = 10;
const ACCEPT = ".pdf,.png,.jpg,.jpeg,.webp,.txt,.md,.csv,application/pdf,image/jpeg,image/png,image/webp,text/plain";

function kb(n?: number | null) {
  if (!n) return "";
  return n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1000))} KB`;
}

/** One line describing what the agents get from a piece of material. */
export function materialMeta(m: MaterialRef) {
  if (m.image) return `Image · ${kb(m.size)}`;
  const parts = [
    m.kind === "link" ? (m.content_type === "application/pdf" ? "Linked PDF" : "Web page") : m.kind === "note" ? "Note" : m.content_type === "application/pdf" ? "PDF" : "Text",
    m.pages ? `${m.pages} page${m.pages > 1 ? "s" : ""}` : "",
    `${m.chars.toLocaleString()} characters`,
  ];
  return parts.filter(Boolean).join(" · ");
}

export function MaterialIcon({ m, className }: { m: MaterialRef; className?: string }) {
  const Icon = m.image ? ImageIcon : m.kind === "link" ? Link2 : m.kind === "note" ? StickyNote : FileText;
  return <Icon className={cn("size-3.5 shrink-0", className)} />;
}

/** An attached piece of material: icon, name, what the agents get, and (in the picker) a remove button. */
export function MaterialChip({ m, onRemove }: { m: MaterialRef; onRemove?: () => void }) {
  return (
    <li className="flex min-w-0 items-center gap-2 rounded-lg border bg-card px-2.5 py-1.5 text-xs">
      <MaterialIcon m={m} className="text-muted-foreground" />
      <span className="min-w-0 truncate font-medium" title={m.url ?? m.name}>
        {m.name}
      </span>
      <span className="hidden shrink-0 text-muted-foreground sm:inline">{materialMeta(m)}</span>
      {m.truncated && (
        <span title="Only the first part is given to the agents (size cap)">
          <StatusBadge tone="warning">first part only</StatusBadge>
        </span>
      )}
      {onRemove && (
        <Button variant="ghost" size="icon-xs" className="ml-auto" aria-label={`Remove ${m.name}`} onClick={onRemove}>
          <X />
        </Button>
      )}
    </li>
  );
}

type Mode = "link" | "note" | null;

/**
 * Attach Source material before starting a Job or sending a Brainstorm message. Each piece is uploaded (and, for a link,
 * fetched on the host) as soon as it is added, so a bad file is refused right away with the reason; the caller sends
 * the ids with its request.
 */
export function SourceMaterialPicker({
  value,
  onChange,
  disabled,
  max = MAX_MATERIALS,
  compact,
}: {
  value: MaterialRef[];
  onChange: (next: MaterialRef[]) => void;
  disabled?: boolean;
  max?: number;
  compact?: boolean;
}) {
  const [mode, setMode] = useState<Mode>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(0);
  const [error, setError] = useState<string>();
  const fileRef = useRef<HTMLInputElement>(null);
  const latest = useRef(value);
  useLayoutEffect(() => {
    latest.current = value; // uploads finish one by one; each adds to the newest list, not the one it started with
  });
  const full = value.length >= max;

  const add = async (make: () => Promise<MaterialRef>) => {
    setBusy((n) => n + 1);
    try {
      const ref = await make();
      onChange([...latest.current, ref].slice(0, max));
      return true;
    } catch (e) {
      setError((e as Error).message.replace(/^\d{3} /, ""));
      return false;
    } finally {
      setBusy((n) => n - 1);
    }
  };

  const onFiles = async (files: FileList | null) => {
    setError(undefined);
    for (const f of Array.from(files ?? []).slice(0, max - latest.current.length)) await add(() => api.uploadMaterial(f));
    if (fileRef.current) fileRef.current.value = "";
  };

  const submitDraft = async () => {
    const text = draft.trim();
    if (!text) return;
    setError(undefined);
    const ok = await add(() => (mode === "link" ? api.addMaterialLink(text) : api.addMaterialNote(text)));
    if (ok) {
      setDraft("");
      setMode(null);
    }
  };

  return (
    <div className="space-y-2">
      {!compact && (
        <div className="space-y-0.5">
          <p className="text-[13px] font-medium text-muted-foreground">Source material (optional)</p>
          <p className="text-xs text-muted-foreground">
            Your press release, poster, notice or page. Facts from it count as verified; the agent only researches what it
            leaves out, and flags any public page that disagrees for you to decide.
          </p>
        </div>
      )}

      {value.length > 0 && (
        <ul className="grid gap-1.5" aria-label="Attached source material">
          {value.map((m) => (
            <MaterialChip key={m.id} m={m} onRemove={disabled ? undefined : () => onChange(value.filter((x) => x.id !== m.id))} />
          ))}
        </ul>
      )}

      {mode && (
        <div className="flex flex-col gap-2 sm:flex-row sm:items-start">
          {mode === "link" ? (
            <input
              autoFocus
              type="url"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && (e.preventDefault(), void submitDraft())}
              placeholder="https://your-site.kr/notice"
              aria-label="Link to your page"
              className="h-9 min-w-0 flex-1 rounded-lg border bg-card px-3 text-[13px] outline-none focus:ring-1 focus:ring-ring"
            />
          ) : (
            <textarea
              autoFocus
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              rows={3}
              placeholder="Paste your announcement or the facts the agent should use"
              aria-label="Note"
              className="min-w-0 flex-1 resize-y rounded-lg border bg-card px-3 py-2 text-[13px] outline-none focus:ring-1 focus:ring-ring"
            />
          )}
          <div className="flex gap-2">
            <Button size="lg" onClick={() => void submitDraft()} disabled={!draft.trim() || busy > 0}>
              Attach
            </Button>
            <Button variant="ghost" size="lg" onClick={() => (setMode(null), setDraft(""))}>
              Cancel
            </Button>
          </div>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <input ref={fileRef} type="file" multiple accept={ACCEPT} className="hidden" onChange={(e) => void onFiles(e.target.files)} />
        <Button variant="outline" size={compact ? "sm" : "lg"} disabled={disabled || full} onClick={() => fileRef.current?.click()}>
          <Paperclip /> File
        </Button>
        <Button variant="outline" size={compact ? "sm" : "lg"} disabled={disabled || full} onClick={() => (setMode("link"), setDraft(""))}>
          <Link2 /> Link
        </Button>
        <Button variant="outline" size={compact ? "sm" : "lg"} disabled={disabled || full} onClick={() => (setMode("note"), setDraft(""))}>
          <StickyNote /> Note
        </Button>
        {busy > 0 && <span className="text-xs text-muted-foreground">Checking and reading…</span>}
        {full && <span className="text-xs text-muted-foreground">{max} pieces at most</span>}
        {!compact && !busy && !full && <span className="text-xs text-muted-foreground">PDF, JPEG, PNG, WebP or text · 10 MB each</span>}
      </div>
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  );
}
