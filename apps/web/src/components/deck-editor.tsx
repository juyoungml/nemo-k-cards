"use client";

import { ArrowDown, ArrowUp, Check, ChevronDown, GripVertical, Loader2, ShieldAlert, ShieldCheck, Sparkles, TriangleAlert, Undo2 } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

import { Finding } from "@/components/safeguard-panel";
import { SlidePreview } from "@/components/slide-preview";
import { SlideSourcesPanel } from "@/components/slide-sources-panel";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ApiError, api, assetUrl } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { ROLE_LABEL, atLeast, useMe } from "@/lib/roles";
import type { DeckChange, DeckEdit, DeckVersion, FitResult, Issue, Job, PhotoChoice, PhotoOption, PhotoOptions, SafeguardFinding, Slide } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { cn } from "@/lib/utils";

// The Deck editor (#19), laid out as a slide inspector (DESIGN.md, design choice 05b "B"): a filmstrip to pick and
// reorder slides, the selected slide large, and one inspector for it with tabs (Text · Sources · Issues · Caption).
// The header bar (version chip with history, safeguard state, Save) sits in the page header. A save re-renders only
// the changed slides and becomes the next Deck version; the safeguard checks that version before it can be approved.

const HASHTAG = /#[\p{L}\p{N}_]+/gu;
const inputCls =
  "w-full rounded-lg border border-input bg-transparent px-2.5 py-1.5 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-60";

/** Same split as the API (backend/app/deck_versions.py `split_caption`): the hashtag lines at the end vs the text. */
export function splitCaption(caption: string): { text: string; tags: string[] } {
  const lines = caption.trimEnd().split("\n");
  let tags: string[] = [];
  while (lines.length && lines.at(-1)!.trim() && !lines.at(-1)!.replace(HASHTAG, "").trim()) {
    tags = [...(lines.pop()!.match(HASHTAG) ?? []), ...tags];
  }
  return { text: lines.join("\n").trimEnd(), tags };
}

// ---------------------------------------------------------------- the draft

interface DraftSlide {
  from_index: number; // its index in the saved version the draft started from
  layout: Slide["layout"];
  heading: string;
  body: string;
}
interface Draft {
  base: number;
  slides: DraftSlide[];
  caption: string;
  hashtags: string;
}

type SaveProblem =
  | { kind: "overflow"; message: string; issues: Issue[] }
  | { kind: "blocked"; message: string; findings: SafeguardFinding[] }
  | { kind: "stale"; message: string }
  | { kind: "other"; message: string };

function draftOf(job: Job): Draft {
  const deck = job.deck!;
  const { text, tags } = splitCaption(deck.caption);
  return {
    base: job.deck_version ?? 1,
    slides: [...deck.slides].sort((a, b) => a.index - b.index).map((s) => ({ from_index: s.index, layout: s.layout, heading: s.heading, body: s.body })),
    caption: text,
    hashtags: tags.join(" "),
  };
}

const same = (a: Draft, b: Draft) => JSON.stringify({ ...a, base: 0 }) === JSON.stringify({ ...b, base: 0 });
const tagsOf = (d: Draft) => d.hashtags.split(/[\s,]+/).filter(Boolean);

function problemOf(e: unknown): SaveProblem {
  if (e instanceof ApiError && e.detail && typeof e.detail === "object") {
    const d = e.detail as { code?: string; message?: string; issues?: Issue[]; findings?: SafeguardFinding[] };
    if (d.code === "overflow") return { kind: "overflow", message: d.message ?? "", issues: d.issues ?? [] };
    if (d.code === "blocked") return { kind: "blocked", message: d.message ?? "", findings: d.findings ?? [] };
    if (d.code === "stale") return { kind: "stale", message: d.message ?? "" };
  }
  if (e instanceof ApiError && typeof e.detail === "string") return { kind: "other", message: e.detail };
  return { kind: "other", message: (e as Error).message };
}

/** Everything the header bar and the workspace share: the draft, the selected slide, saving. */
export function useDeckEditor(job: Job, onChange: (job: Job) => void) {
  const { role } = useMe();
  const version = job.deck_version ?? 1;
  const [state, setState] = useState<{ draft: Draft; saved: Draft; jobId: string }>();
  const [selected, setSelected] = useState(0);
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState<SaveProblem>();
  const [announce, setAnnounce] = useState("");

  // Follow the saved Deck while there are no unsaved changes (a new version, someone else's save, the pipeline
  // finishing). With unsaved changes, keep them: saving then reports the stale version.
  const dirty = !!state && !same(state.draft, state.saved);
  if (job.deck && (!state || state.jobId !== job.id || (state.saved.base !== version && !dirty))) {
    const fresh = draftOf(job);
    setState({ draft: fresh, saved: fresh, jobId: job.id });
  }

  const draft = state?.draft;
  const mayEdit = atLeast(role, "editor");
  // A scheduled Deck stays editable (#26): saving puts its Post on hold until it is approved again.
  const editable = mayEdit && (job.status === "READY_FOR_REVIEW" || job.status === "SCHEDULED") && !!job.deck;
  const tags = draft ? tagsOf(draft) : [];
  const count = draft?.slides.length ?? 0;
  const current = Math.min(selected, Math.max(0, count - 1));

  const update = (fn: (d: Draft) => Draft) => setState((s) => s && { ...s, draft: fn(s.draft) });
  const setSlide = (i: number, patch: Partial<DraftSlide>) => update((d) => ({ ...d, slides: d.slides.map((s, j) => (j === i ? { ...s, ...patch } : s)) }));
  const move = (from: number, to: number) => {
    if (!draft || to < 0 || to >= draft.slides.length || from === to) return;
    const slides = [...draft.slides];
    const [it] = slides.splice(from, 1);
    slides.splice(to, 0, it);
    update((d) => ({ ...d, slides }));
    setSelected(to);
    setAnnounce(`“${it.heading}” moved to position ${to + 1} of ${slides.length}.`);
  };
  const discard = () => {
    setState((s) => s && { ...s, draft: s.saved });
    setProblem(undefined);
  };
  const reload = () => {
    if (!job.deck) return;
    const fresh = draftOf(job);
    setState({ draft: fresh, saved: fresh, jobId: job.id });
    setProblem(undefined);
  };
  const save = async (allowOverflow = false) => {
    if (!draft) return;
    setSaving(true);
    setProblem(undefined);
    const edit: DeckEdit = {
      base_version: draft.base,
      slides: draft.slides.map(({ from_index, heading, body }) => ({ from_index, heading, body })),
      caption: draft.caption,
      hashtags: tags,
      allow_overflow: allowOverflow,
    };
    try {
      const saved = await api.saveVersion(job.id, edit);
      onChange(saved);
      const fresh = draftOf(saved);
      setState({ draft: fresh, saved: fresh, jobId: saved.id });
      setAnnounce(`Saved as version ${saved.deck_version}. The safeguard is checking it.`);
    } catch (e) {
      setProblem(problemOf(e));
    } finally {
      setSaving(false);
    }
  };
  /** A one-click change made on the server (#23: swap a photo, regenerate a slide): it becomes the next version. Only
   * without unsaved text changes, so nothing typed is lost or mixed in. */
  const act = async (fn: () => Promise<Job>, done: string) => {
    setSaving(true);
    setProblem(undefined);
    try {
      const saved = await fn();
      onChange(saved);
      const fresh = draftOf(saved);
      setState({ draft: fresh, saved: fresh, jobId: saved.id });
      setAnnounce(done);
    } catch (e) {
      setProblem(problemOf(e));
    } finally {
      setSaving(false);
    }
  };
  /** Jump to the slide that was at `baseIndex` in the saved version (Issues and findings point there). */
  const selectBase = (baseIndex: number) => {
    const at = draft?.slides.findIndex((s) => s.from_index === baseIndex) ?? -1;
    setSelected(at >= 0 ? at : baseIndex);
  };

  return {
    job, role, version, draft, dirty, editable, mayEdit, tags, current, saving, problem, announce,
    select: setSelected, selectBase, setSlide, move, update, save, discard, reload, act,
  };
}
export type DeckEditorState = ReturnType<typeof useDeckEditor>;

// ---------------------------------------------------------------- header bar

/** Page-header controls: version chip with its history, the safeguard state, and Save. */
export function DeckBar({ editor }: { editor: DeckEditorState }) {
  const { job, draft, dirty, editable, tags, saving, version } = editor;
  if (!job.deck || !draft) return null;
  return (
    <div className="flex flex-wrap items-center gap-2">
      <VersionChip job={job} />
      <SafeguardChip job={job} />
      {editable && (
        <>
          {dirty && (
            <Button variant="ghost" size="sm" onClick={editor.discard} disabled={saving}>
              <Undo2 /> Discard
            </Button>
          )}
          <Button size="sm" onClick={() => void editor.save()} disabled={!dirty || saving || tags.length > 5} title={dirty ? undefined : "No unsaved changes"}>
            {saving ? (
              <>
                <Loader2 className="motion-safe:animate-spin" /> Rendering…
              </>
            ) : (
              `Save as v${draft.base + 1}`
            )}
          </Button>
        </>
      )}
      {dirty && draft.base !== version && <span className="sr-only">Someone saved a newer version.</span>}
    </div>
  );
}

function SafeguardChip({ job }: { job: Job }) {
  const sg = job.safeguard;
  if (job.safeguard_pending && job.status === "READY_FOR_REVIEW")
    return (
      <StatusBadge tone="info">
        <Loader2 className="size-3 motion-safe:animate-spin" aria-hidden /> Safeguard checking…
      </StatusBadge>
    );
  if (!sg) return null;
  return sg.decision === "block" ? (
    <StatusBadge tone="danger">
      <ShieldAlert className="size-3" aria-hidden /> Safeguard: blocked
    </StatusBadge>
  ) : (
    <StatusBadge tone="success">
      <ShieldCheck className="size-3" aria-hidden /> Safeguard: allowed
    </StatusBadge>
  );
}

const FIELD: Record<DeckChange["field"], string> = {
  order: "order",
  "slide.heading": "heading",
  "slide.body": "body",
  "slide.photo": "photo",
  caption: "caption",
  hashtags: "hashtags",
};

function changeLine(c: DeckChange) {
  if (c.field === "order") return "Slides reordered";
  if (c.field === "caption") return "Caption";
  if (c.field === "hashtags") return `Hashtags: ${(c.before as string[]).join(" ") || "—"} → ${(c.after as string[]).join(" ") || "—"}`;
  if (c.field === "slide.photo") return `Slide ${(c.slide ?? 0) + 1} photo: ${String(c.before)} → ${String(c.after)}`;
  return `Slide ${(c.slide ?? 0) + 1} ${FIELD[c.field]}${c.by === "agent" ? " (agent rewrite)" : ""}`;
}

/** "v3 ▾": every saved version, newest first, with who saved it, when, and what changed (before → after). */
function VersionChip({ job }: { job: Job }) {
  const [open, setOpen] = useState(false);
  const [versions, setVersions] = useState<DeckVersion[]>();
  const [error, setError] = useState<string>();
  const ref = useRef<HTMLDivElement>(null);
  const id = useId();
  const current = job.deck_version ?? 1;

  useEffect(() => {
    if (!open) return;
    let live = true;
    api.getVersions(job.id).then(
      (v) => live && (setVersions(v), setError(undefined)),
      (e) => live && setError((e as Error).message),
    );
    const close = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => {
      live = false;
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", close);
    };
  }, [open, job.id, current]);

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((o) => !o)}
        className="inline-flex h-6 items-center gap-1 rounded-full border bg-card px-2.5 text-xs font-semibold text-muted-foreground hover:text-foreground"
        title="Version history"
      >
        v{current} <ChevronDown className={cn("size-3 transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <div id={id} role="dialog" aria-label="Version history" className="absolute right-0 z-30 mt-2 max-h-[60vh] w-[min(380px,calc(100vw-32px))] overflow-y-auto rounded-xl border bg-card p-3 shadow-lg">
          <p className="mb-2 text-xs font-semibold">Version history</p>
          {error && <p className="text-xs text-destructive">{error}</p>}
          {!versions && !error && <p className="animate-pulse text-xs text-muted-foreground">Loading…</p>}
          {versions && (
            <ol className="space-y-2">
              {versions.map((v) => (
                <li key={v.version} className="rounded-lg border p-2.5 text-xs">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold">v{v.version}</span>
                    {v.current && <StatusBadge tone="info">current</StatusBadge>}
                    <span className="text-muted-foreground">
                      {v.edited_by ? (v.edited_by_email ?? "a Member") : "the pipeline"} · {formatDateTime(v.created_at)}
                    </span>
                  </div>
                  {v.changes.length > 0 && (
                    <ul className="mt-1.5 space-y-1">
                      {v.changes.map((c, n) => (
                        <li key={n}>
                          <span className="font-medium">{changeLine(c)}</span>
                          {(c.field === "caption" || c.field.startsWith("slide.")) && (
                            <div className="mt-0.5 grid gap-0.5 pl-2 text-muted-foreground">
                              <del className="line-clamp-3 whitespace-pre-line">{String(c.before) || "—"}</del>
                              <ins className="line-clamp-3 whitespace-pre-line text-foreground no-underline">{String(c.after) || "—"}</ins>
                            </div>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}
                  {v.edited_by && (
                    <p className="mt-1 text-[11px] text-muted-foreground">
                      {v.rerendered.length ? `${v.rerendered.length} slide(s) re-rendered` : "no slides re-rendered"}
                    </p>
                  )}
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- workspace

/** Filmstrip · selected slide · inspector. */
export function SlideWorkspace({ editor }: { editor: DeckEditorState }) {
  const { job, draft, current, editable, role, mayEdit, problem, announce } = editor;
  if (!job.deck || !draft) return null;
  const images = job.slide_urls?.map(assetUrl);
  const slides: Slide[] = draft.slides.map((d, i) => ({ ...job.deck!.slides.find((s) => s.index === d.from_index)!, index: i, heading: d.heading, body: d.body }));
  const ordered = images && draft.slides.map((d) => images[d.from_index]);
  const textChanged = (i: number) => {
    const d = draft.slides[i];
    const s = job.deck!.slides.find((x) => x.index === d.from_index);
    return !!s && (s.heading !== d.heading || s.body !== d.body || d.from_index !== i);
  };

  return (
    <section aria-label="Deck editor" className="space-y-3">
      {problem && <SaveProblemNotice problem={problem} editor={editor} />}
      {editor.dirty && draft.base !== editor.version && (
        <p role="status" className="rounded-lg bg-warning-soft px-3 py-2 text-xs text-warning">
          Someone saved version {editor.version} while you were editing version {draft.base}. Saving will be refused; discard your changes to see it.
        </p>
      )}
      {job.status === "SCHEDULED" && mayEdit && editor.dirty && (
        <p role="status" className="rounded-lg bg-warning-soft px-3 py-2 text-xs text-warning">
          This Deck is scheduled. Saving puts its Post on hold: nothing is posted until someone approves the new version.
        </p>
      )}
      {job.status === "READY_FOR_REVIEW" && role && !mayEdit && (
        <p className="text-xs text-muted-foreground">Your Role ({ROLE_LABEL[role]}) can&apos;t edit decks. Ask an Editor.</p>
      )}
      <p className="sr-only" aria-live="polite">
        {announce}
      </p>

      <div className="grid gap-4 lg:grid-cols-[76px_minmax(0,400px)_minmax(0,1fr)] lg:items-start">
        <Filmstrip editor={editor} slides={slides} images={ordered} />
        <div className="min-w-0 space-y-2">
          <SlidePreview slides={slides} images={ordered} current={current} onSelect={editor.select} thumbnails={false} className="lg:w-full" />
          {ordered && textChanged(current) && (
            <p className="text-[11px] text-muted-foreground">The image shows the saved slide; your changes render when you save.</p>
          )}
        </div>
        <Inspector editor={editor} editable={editable} />
      </div>
    </section>
  );
}

/** Thumbnails in slide order. Drag to reorder; or focus one and press Alt/Option + Arrow; the inspector has buttons too. */
function Filmstrip({ editor, slides, images }: { editor: DeckEditorState; slides: Slide[]; images?: string[] }) {
  const { current, editable } = editor;
  const [dragging, setDragging] = useState<number>();
  const [over, setOver] = useState<number>();
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const moveKeys = (e: React.KeyboardEvent, i: number) => {
    const back = e.key === "ArrowUp" || e.key === "ArrowLeft";
    const fwd = e.key === "ArrowDown" || e.key === "ArrowRight";
    if (!back && !fwd) return;
    e.preventDefault();
    const to = i + (back ? -1 : 1);
    if (to < 0 || to >= slides.length) return;
    if (e.altKey && editable) editor.move(i, to);
    else editor.select(to);
    requestAnimationFrame(() => refs.current[to]?.focus());
  };

  return (
    <div className="min-w-0">
      <ol
        aria-label={editable ? "Slides. Drag to reorder, or press Alt (Option) and an arrow key." : "Slides"}
        className="flex gap-2 overflow-x-auto pb-1 lg:flex-col lg:overflow-visible lg:pb-0"
      >
        {slides.map((s, i) => (
          <li
            key={editor.draft!.slides[i].from_index}
            className={cn("relative shrink-0", over === i && dragging !== undefined && dragging !== i && "before:absolute before:-top-1.5 before:left-0 before:h-0.5 before:w-full before:rounded before:bg-primary max-lg:before:-left-1.5 max-lg:before:top-0 max-lg:before:h-full max-lg:before:w-0.5")}
            draggable={editable}
            onDragStart={(e) => {
              setDragging(i);
              e.dataTransfer.effectAllowed = "move";
            }}
            onDragOver={(e) => {
              if (dragging === undefined) return;
              e.preventDefault();
              setOver(i);
            }}
            onDrop={(e) => {
              e.preventDefault();
              if (dragging !== undefined) editor.move(dragging, i);
              setDragging(undefined);
              setOver(undefined);
            }}
            onDragEnd={() => {
              setDragging(undefined);
              setOver(undefined);
            }}
          >
            <button
              ref={(el) => {
                refs.current[i] = el;
              }}
              type="button"
              onClick={() => editor.select(i)}
              onKeyDown={(e) => moveKeys(e, i)}
              aria-label={`Slide ${i + 1}: ${s.heading}`}
              aria-current={i === current}
              className={cn(
                "group relative block h-[70px] w-14 overflow-hidden rounded-md border bg-card text-left transition-shadow lg:h-[85px] lg:w-[68px]",
                i === current ? "ring-2 ring-primary ring-offset-2" : "hover:border-muted-foreground",
                dragging === i && "opacity-50",
                editable && "cursor-grab active:cursor-grabbing",
              )}
            >
              {images?.[i] ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={images[i]} alt="" className="size-full object-cover" draggable={false} />
              ) : (
                <span className={cn("flex size-full flex-col justify-end p-1 text-[8px] leading-tight", s.layout === "cover" || s.layout === "cta" ? "bg-[#111418] text-white" : "bg-[#FFFDF8] text-foreground")}>
                  <span className="line-clamp-3 font-semibold">{s.heading}</span>
                </span>
              )}
              <span className="absolute top-0.5 left-0.5 rounded bg-black/55 px-1 text-[9px] font-bold text-white tabular-nums">{String(i + 1).padStart(2, "0")}</span>
              {editable && i === current && <GripVertical aria-hidden className="absolute top-0.5 right-0.5 size-3 rounded bg-black/55 text-white" />}
            </button>
          </li>
        ))}
      </ol>
    </div>
  );
}

type Tab = "text" | "photo" | "sources" | "issues" | "caption";

function Inspector({ editor, editable }: { editor: DeckEditorState; editable: boolean }) {
  const { job, draft, current, tags } = editor;
  const [tab, setTab] = useState<Tab>("text");
  const tabsId = useId();
  const d = draft!.slides[current];
  const base = d.from_index;
  const issues = job.issues.filter((i) => i.slide_index === base);
  const findings = (job.safeguard?.findings ?? []).filter((f) => f.slide_index === base && (f.location === "slide" || f.location === "image"));
  const capFindings = (job.safeguard?.findings ?? []).filter((f) => f.location === "caption" || f.location === "hashtags");
  const src = job.slide_sources?.find((s) => s.slide_index === base);
  const nSources = src ? src.facts.length + src.links.length : 0;
  const tabs: { key: Tab; label: string; count?: number; alert?: boolean }[] = [
    { key: "text", label: "Text" },
    ...(d.layout === "event" ? [{ key: "photo" as const, label: "Photo" }] : []),
    { key: "sources", label: "Sources", count: nSources },
    { key: "issues", label: "Issues", count: issues.length + findings.length, alert: issues.some((i) => i.severity === "block") || findings.some((f) => f.severity === "block") },
    { key: "caption", label: "Caption" },
  ];
  const keys = (e: React.KeyboardEvent, i: number) => {
    const to = e.key === "ArrowRight" ? i + 1 : e.key === "ArrowLeft" ? i - 1 : e.key === "Home" ? 0 : e.key === "End" ? tabs.length - 1 : -2;
    if (to === -2) return;
    e.preventDefault();
    const next = tabs[(to + tabs.length) % tabs.length].key;
    setTab(next);
    requestAnimationFrame(() => document.getElementById(`${tabsId}-${next}`)?.focus());
  };

  return (
    <div className="min-w-0 rounded-xl border bg-card p-4 md:p-5">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h2 className="text-base font-semibold">
          Slide {current + 1} <span className="text-sm font-normal text-muted-foreground">· {d.layout}</span>
        </h2>
        {d.from_index !== current && <StatusBadge tone="info">{`moved from ${d.from_index + 1}`}</StatusBadge>}
        {editable && (
          <div className="ml-auto flex gap-1">
            <Button variant="outline" size="sm" disabled={current === 0} onClick={() => editor.move(current, current - 1)} aria-label={`Move slide ${current + 1} earlier`}>
              <ArrowUp /> Earlier
            </Button>
            <Button variant="outline" size="sm" disabled={current === draft!.slides.length - 1} onClick={() => editor.move(current, current + 1)} aria-label={`Move slide ${current + 1} later`}>
              <ArrowDown /> Later
            </Button>
          </div>
        )}
      </div>

      <div role="tablist" aria-label="Slide inspector" className="mb-4 flex gap-1 overflow-x-auto border-b">
        {tabs.map((t, i) => (
          <button
            key={t.key}
            id={`${tabsId}-${t.key}`}
            role="tab"
            type="button"
            aria-selected={tab === t.key}
            aria-controls={`${tabsId}-${t.key}-panel`}
            tabIndex={tab === t.key ? 0 : -1}
            onClick={() => setTab(t.key)}
            onKeyDown={(e) => keys(e, i)}
            className={cn(
              "-mb-px shrink-0 border-b-2 px-2.5 py-1.5 text-[13px] font-semibold whitespace-nowrap",
              tab === t.key ? "border-primary text-foreground" : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            {t.label}
            {t.count ? <span className={cn("ml-1 text-xs tabular-nums", t.alert ? "text-destructive" : "text-muted-foreground")}>{t.count}</span> : null}
          </button>
        ))}
      </div>

      <div role="tabpanel" id={`${tabsId}-${tab}-panel`} aria-labelledby={`${tabsId}-${tab}`}>
        {tab === "text" && <TextTab editor={editor} editable={editable} />}
        {tab === "photo" && d.layout === "event" && <PhotoTab editor={editor} editable={editable} />}
        {tab === "sources" && <SlideSourcesPanel job={job} slide={job.deck!.slides.findIndex((s) => s.index === base)} embedded />}
        {tab === "sources" && !src && <p className="text-xs text-muted-foreground">No sources recorded for this slide.</p>}
        {tab === "issues" && (
          <div className="space-y-3">
            {findings.length > 0 && (
              <ul className="space-y-2">
                {findings.map((f, n) => (
                  <Finding key={n} f={f} onJump={() => undefined} />
                ))}
              </ul>
            )}
            {issues.length > 0 && (
              <ul className="space-y-2">
                {issues.map((i, n) => (
                  <li key={n} className={cn("rounded-lg px-3 py-2.5 text-xs", i.severity === "block" ? "bg-danger-soft text-destructive" : "bg-warning-soft text-warning")}>
                    <span className="font-semibold">
                      {i.severity === "block" ? "Blocking" : "Warning"} · {i.category}
                    </span>{" "}
                    — {i.message}
                  </li>
                ))}
              </ul>
            )}
            {!issues.length && !findings.length && <p className="text-xs text-muted-foreground">No issues on this slide.</p>}
          </div>
        )}
        {tab === "caption" && (
          <div className="space-y-2">
            <p className="text-xs text-muted-foreground">Deck-level: one caption for the whole carousel.</p>
            <label htmlFor="deck-caption" className="block text-[13px] font-medium">
              Caption
            </label>
            <Textarea
              id="deck-caption"
              value={draft!.caption}
              maxLength={2200}
              disabled={!editable}
              onChange={(e) => editor.update((dr) => ({ ...dr, caption: e.target.value }))}
              className="min-h-28 text-[13px]"
            />
            <label htmlFor="deck-hashtags" className="flex text-[13px] font-medium">
              Hashtags
              <span className={cn("ml-auto text-xs tabular-nums", tags.length > 5 ? "font-semibold text-destructive" : "text-muted-foreground")}>{tags.length} / 5</span>
            </label>
            <input
              id="deck-hashtags"
              className={inputCls}
              value={draft!.hashtags}
              placeholder="#seoul #koreatravel"
              aria-describedby="deck-hashtags-hint"
              aria-invalid={tags.length > 5}
              disabled={!editable}
              onChange={(e) => editor.update((dr) => ({ ...dr, hashtags: e.target.value }))}
            />
            <p id="deck-hashtags-hint" className="text-[11px] text-muted-foreground">
              Separated by spaces. Instagram allows at most 5, counting any in the caption text.
            </p>
            {capFindings.length > 0 && (
              <ul className="space-y-2 pt-1">
                {capFindings.map((f, n) => (
                  <Finding key={n} f={f} onJump={() => undefined} />
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function TextTab({ editor, editable }: { editor: DeckEditorState; editable: boolean }) {
  const { draft, current, job } = editor;
  const d = draft!.slides[current];
  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <label className="block text-[11px] font-semibold tracking-wide text-muted-foreground uppercase" htmlFor="slide-heading">
          Heading
        </label>
        <input
          id="slide-heading"
          className={inputCls}
          value={d.heading}
          maxLength={200}
          disabled={!editable}
          onChange={(e) => editor.setSlide(current, { heading: e.target.value })}
        />
      </div>
      <div className="space-y-1">
        <label className="block text-[11px] font-semibold tracking-wide text-muted-foreground uppercase" htmlFor="slide-body">
          Body
        </label>
        <Textarea
          id="slide-body"
          value={d.body}
          maxLength={2000}
          disabled={!editable}
          onChange={(e) => editor.setSlide(current, { body: e.target.value })}
          className="min-h-28 text-[13px]"
        />
        {d.layout === "event" && !d.body && (
          <p className="text-[11px] text-muted-foreground">Empty: the card shows the event&apos;s “why go” line from research.</p>
        )}
      </div>
      {editable && job.status === "READY_FOR_REVIEW" && <FitIndicator editor={editor} />}
      {editable && <RegenerateBox editor={editor} />}
    </div>
  );
}

/** "Unsaved text changes" blocks the one-click actions: they save on the server, from the saved version. */
function OneClickNote({ editor }: { editor: DeckEditorState }) {
  if (!editor.dirty) return null;
  return <p className="text-[11px] text-warning">Save or discard your text changes first.</p>;
}

/** Regenerate one slide (#23): the copywriter rewrites this slide's text from the research; nothing else changes. */
function RegenerateBox({ editor }: { editor: DeckEditorState }) {
  const { job, current, draft, dirty, saving } = editor;
  const [instruction, setInstruction] = useState("");
  const n = draft!.slides[current].from_index;
  const run = () =>
    void editor
      .act(() => api.regenerateSlide(job.id, n, draft!.base, instruction), `Slide ${current + 1} rewritten. The safeguard is checking the new version.`)
      .then(() => setInstruction(""));
  return (
    <div className="space-y-2 border-t pt-3">
      <label htmlFor="regen-instruction" className="block text-[11px] font-semibold tracking-wide text-muted-foreground uppercase">
        Rewrite with the agent
      </label>
      <div className="flex flex-wrap gap-2">
        <input
          id="regen-instruction"
          className={cn(inputCls, "min-w-0 flex-1")}
          value={instruction}
          maxLength={300}
          placeholder="Optional: shorter, lead with the price, friendlier…"
          disabled={dirty || saving}
          onChange={(e) => setInstruction(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && !dirty && !saving && run()}
        />
        <Button variant="outline" size="sm" disabled={dirty || saving} onClick={run}>
          {saving ? <Loader2 className="motion-safe:animate-spin" /> : <Sparkles />} Regenerate slide
        </Button>
      </div>
      <p className="text-[11px] text-muted-foreground">
        Rewrites only this slide from the same research and saves it as a new version. Other slides stay as they are.
      </p>
      <OneClickNote editor={editor} />
    </div>
  );
}

/** Swap a photo (#23): the event's other vetted photos, the stock set, or none (a color poster). */
function PhotoTab({ editor, editable }: { editor: DeckEditorState; editable: boolean }) {
  const { job, current, draft, dirty, saving } = editor;
  const n = draft!.slides[current].from_index;
  const version = job.deck_version ?? 1;
  const { data: opts, error } = useApi<PhotoOptions>(() => api.slidePhotoOptions(job.id, n), [job.id, n, version]);
  if (error) return <p className="text-xs text-destructive">{error.message}</p>;
  if (!opts) return <p className="text-xs text-muted-foreground">Loading photos…</p>;
  const pick = (o: PhotoOption) => {
    const choice: PhotoChoice =
      o.source === "candidate" ? { source: "candidate", candidate: o.candidate ?? 0 } : o.source === "stock" ? { source: "stock", stock_id: o.stock_id ?? "" } : { source: "none" };
    void editor.act(() => api.swapPhoto(job.id, n, draft!.base, choice), `Photo on slide ${current + 1} changed. The safeguard is checking the new version.`);
  };
  const official = opts.options.filter((o) => o.source === "candidate");
  const stock = opts.options.filter((o) => o.source === "stock");
  const none = opts.options.find((o) => o.source === "none")!;
  const card = (o: PhotoOption) => (
    <li key={`${o.source}-${o.candidate ?? o.stock_id ?? "none"}`} className={cn("overflow-hidden rounded-lg border", o.current && "border-2 border-primary")}>
      {o.url ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={assetUrl(o.url)} alt={o.label} className={cn("aspect-[4/5] w-full object-cover", !o.allowed && "opacity-40")} />
      ) : (
        <div className="grid aspect-[4/5] w-full place-items-center bg-muted text-xs text-muted-foreground">Poster</div>
      )}
      <div className="space-y-1 p-2">
        <p className="line-clamp-2 text-[11px] text-muted-foreground" title={o.label}>
          {o.source === "stock" ? o.label : o.credit || o.label}
        </p>
        {o.current ? (
          <StatusBadge tone="info">On the slide</StatusBadge>
        ) : o.allowed ? (
          <Button size="sm" variant="outline" className="w-full" disabled={!editable || dirty || saving} onClick={() => pick(o)}>
            Use this
          </Button>
        ) : (
          <p className="text-[11px] text-muted-foreground">{o.why_not}</p>
        )}
      </div>
    </li>
  );
  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">
        {opts.event_title}. Official photos were vetted for this event at research time. Stock photos are AI-generated and labeled on the card.
      </p>
      <OneClickNote editor={editor} />
      <section className="space-y-1.5">
        <h3 className="text-[11px] font-semibold tracking-wide text-muted-foreground uppercase">Official photos</h3>
        {official.length ? (
          <ul className="grid grid-cols-3 gap-2">{official.map(card)}</ul>
        ) : (
          <p className="text-xs text-muted-foreground">None usable were found for this event.</p>
        )}
      </section>
      <section className="space-y-1.5">
        <h3 className="text-[11px] font-semibold tracking-wide text-muted-foreground uppercase">Stock</h3>
        <ul className="grid grid-cols-3 gap-2">{stock.map(card)}</ul>
      </section>
      <section className="space-y-1.5">
        <h3 className="text-[11px] font-semibold tracking-wide text-muted-foreground uppercase">No photo</h3>
        <ul className="grid grid-cols-3 gap-2">{card(none)}</ul>
      </section>
    </div>
  );
}

const BLOCK_LABEL: Record<string, string> = { heading: "heading", body: "body", lead: "body" };

/** "Fits · heading 2 lines · body 3 lines", measured by the renderer on the text as typed (debounced). */
function FitIndicator({ editor }: { editor: DeckEditorState }) {
  const { draft, current, job } = editor;
  const key = JSON.stringify([job.id, draft!.base, draft!.slides.map((s) => [s.from_index, s.heading, s.body]), current]);
  const [result, setResult] = useState<{ key: string; fit?: FitResult; error?: string }>();

  useEffect(() => {
    let live = true;
    const t = setTimeout(() => {
      api
        .fitCheck(job.id, { base_version: draft!.base, slides: draft!.slides.map(({ from_index, heading, body }) => ({ from_index, heading, body })) }, current)
        .then(
          (fit) => live && setResult({ key, fit }),
          (e) => live && setResult({ key, error: (e as Error).message }),
        );
    }, 600);
    return () => {
      live = false;
      clearTimeout(t);
    };
    // `key` covers everything the check depends on
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const fresh = result?.key === key ? result : undefined;
  if (!fresh) return <p className="flex items-center gap-1.5 text-xs text-muted-foreground"><Loader2 className="size-3 motion-safe:animate-spin" /> Checking fit…</p>;
  if (fresh.error) return <p className="text-xs text-muted-foreground">Couldn&apos;t check the fit ({fresh.error}). Saving still checks it.</p>;
  const fit = fresh.fit!;
  const parts = fit.blocks
    .filter((b) => /\.(heading|body)$/.test(b.selector))
    .map((b) => `${BLOCK_LABEL[b.selector.split(".")[1]] ?? b.selector} ${b.lines} line${b.lines === 1 ? "" : "s"}`);
  return (
    <p aria-live="polite" className={cn("flex items-start gap-1.5 rounded-lg px-3 py-2 text-xs", fit.fits && !fit.small_text ? "bg-success-soft text-success" : "bg-warning-soft text-warning")}>
      {fit.fits ? <Check className="mt-px size-3.5 shrink-0" /> : <TriangleAlert className="mt-px size-3.5 shrink-0" />}
      <span>
        {fit.fits ? "Fits" : "Doesn't fit: shorten it, or it will be saved as a blocking Issue"}
        {parts.length > 0 && ` · ${parts.join(" · ")}`}
        {fit.fits && fit.small_text && " · text shrank below 30px to fit"}
      </span>
    </p>
  );
}

function SaveProblemNotice({ problem, editor }: { problem: SaveProblem; editor: DeckEditorState }) {
  if (problem.kind === "overflow")
    return (
      <div role="alert" className="flex flex-wrap items-start gap-3 rounded-xl border border-warning/40 bg-warning-soft p-3 text-xs">
        <div className="min-w-0 flex-1 space-y-1">
          <p className="flex items-center gap-1.5 font-semibold text-warning">
            <TriangleAlert className="size-4 shrink-0" /> Not saved yet: text doesn&apos;t fit
          </p>
          <p className="text-foreground">{problem.message}</p>
          <ul className="flex flex-wrap gap-1.5">
            {problem.issues.map((i, n) => (
              <li key={n}>
                <button type="button" className="underline-offset-2 hover:underline" onClick={() => editor.select(i.slide_index ?? 0)}>
                  Slide {(i.slide_index ?? 0) + 1}: {i.message}
                </button>
              </li>
            ))}
          </ul>
        </div>
        <Button variant="outline" size="sm" disabled={editor.saving} onClick={() => void editor.save(true)}>
          Save anyway
        </Button>
      </div>
    );
  if (problem.kind === "blocked")
    return (
      <div role="alert" className="space-y-2 rounded-xl border border-destructive/40 bg-danger-soft p-3 text-xs">
        <p className="font-semibold text-destructive">{problem.message}</p>
        <ul className="space-y-2">
          {problem.findings.map((f, n) => (
            <Finding key={n} f={f} onJump={(s) => editor.select(s)} />
          ))}
        </ul>
      </div>
    );
  return (
    <div role="alert" className="flex flex-wrap items-center gap-2 rounded-xl border border-destructive/30 bg-danger-soft p-3 text-xs text-destructive">
      <span className="flex-1">{problem.message}</span>
      {problem.kind === "stale" && (
        <Button variant="outline" size="sm" onClick={editor.reload}>
          Start again from the latest version
        </Button>
      )}
    </div>
  );
}
