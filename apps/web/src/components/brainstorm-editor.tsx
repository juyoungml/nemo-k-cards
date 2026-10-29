"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { ArrowDown, ArrowRight, ArrowUp, Link2, Send } from "lucide-react";

import { MAX_MATERIALS, MaterialChip, MaterialIcon, SourceMaterialPicker } from "@/components/source-material";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { targetOptions, toneOptions } from "@/lib/constants";
import type { Brainstorm, Fact, MaterialRef } from "@/lib/types";
import { cn } from "@/lib/utils";

const URL_RE = /https?:\/\/\S+/g;

const FACT_LABEL: Record<Fact["key"], string> = {
  event: "Event",
  dates: "Dates",
  venue: "Venue",
  price: "Price",
  booking: "Booking",
  other: "Other",
};

function Chip({ selected, onClick, children }: { selected: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={selected}
      className={cn(
        "inline-flex h-6 items-center gap-1.5 rounded-full px-2.5 text-xs font-medium",
        selected ? "bg-info-soft text-info" : "bg-muted text-muted-foreground hover:text-foreground",
      )}
    >
      <span className="size-1.5 rounded-full bg-current" />
      {selected ? children : <>+ {children}</>}
    </button>
  );
}

const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

export function BrainstormEditor({ initialBrainstorm }: { initialBrainstorm: Brainstorm }) {
  const router = useRouter();
  const [brainstorm, setBrainstorm] = useState(initialBrainstorm);
  const [input, setInput] = useState("");
  const [attaching, setAttaching] = useState<MaterialRef[]>([]); // Source material to send with the next message
  const [sending, startSending] = useTransition();
  const [generating, startGenerating] = useTransition();
  const [error, setError] = useState<string>();

  // Board edits apply locally right away and are persisted with PATCH /brainstorms/{id}.
  const boardOf = (d: Brainstorm) => ({
    facts: d.facts,
    angles: d.angles,
    selected_angle_id: d.selected_angle_id,
    targets: d.targets,
    tones: d.tones,
    outline: d.outline,
  });
  const edit = (next: Brainstorm) => {
    setBrainstorm(next);
    api.patchBrainstorm(next.id, boardOf(next)).catch((e: Error) => setError(e.message));
  };

  const send = () => {
    const text = input.trim();
    if (!text && !attaching.length) return;
    const urls = text.match(URL_RE) ?? [];
    const message = text.replace(URL_RE, "").trim();
    const attached = attaching;
    setInput("");
    setAttaching([]);
    setError(undefined);
    // Optimistic user bubble; the server returns the whole updated brainstorm (reply + board).
    setBrainstorm((d) => ({
      ...d,
      messages: [...d.messages, { role: "user", text: message, urls, material_ids: attached.map((m) => m.id) }],
      material: [...(d.material ?? []), ...attached],
    }));
    startSending(async () => {
      try {
        setBrainstorm(await api.sendBrainstormMessage(brainstorm.id, message, urls, attached.map((m) => m.id)));
      } catch (e) {
        setError((e as Error).message);
      }
    });
  };

  const move = (i: number, dir: -1 | 1) => {
    const outline = [...brainstorm.outline];
    [outline[i], outline[i + dir]] = [outline[i + dir], outline[i]];
    edit({ ...brainstorm, outline });
  };

  const sourceOk = brainstorm.facts.some((f) => f.verified && f.source_url);
  const materialById = new Map((brainstorm.material ?? []).map((m) => [m.id, m]));

  return (
    <div className="flex flex-col gap-4 lg:flex-row lg:items-stretch">
      {/* ---- Chat */}
      <section className="flex w-full flex-col gap-3 rounded-xl border bg-card p-4 md:p-5 lg:w-[440px] lg:shrink-0">
        <div className="flex items-center">
          <h2 className="flex-1 text-[15px] font-semibold">Brainstorm with agent</h2>
          <StatusBadge tone="neutral">planner · sandbox</StatusBadge>
        </div>

        <ol className="flex flex-1 flex-col gap-2.5 overflow-y-auto">
          {brainstorm.messages.map((m, i) => (
            <li key={i} className={cn("flex flex-col gap-1.5", m.role === "user" ? "items-end" : "items-start")}>
              {m.material_ids?.map((id) => {
                const ref = materialById.get(id);
                return (
                  ref && (
                    <span key={id} className="inline-flex max-w-[330px] items-center gap-1.5 truncate rounded-full border px-2.5 py-1 text-[11px] font-medium text-muted-foreground">
                      <MaterialIcon m={ref} className="size-3" />
                      {ref.name}
                    </span>
                  )
                );
              })}
              {m.urls?.map((u) => (
                <span key={u} className="inline-flex max-w-[330px] items-center gap-1.5 truncate rounded-full border px-2.5 py-1 text-[11px] font-medium text-muted-foreground">
                  <Link2 className="size-3 shrink-0" />
                  {u.replace(/^https?:\/\//, "")}
                </span>
              ))}
              {m.text && <p
                className={cn(
                  "max-w-[360px] rounded-2xl px-3.5 py-2.5 text-[13px] leading-relaxed whitespace-pre-line",
                  m.role === "user" ? "bg-primary text-primary-foreground" : "bg-muted",
                )}
              >
                {m.text}
              </p>}
            </li>
          ))}
          {sending && <li className="text-xs text-muted-foreground">planner is thinking…</li>}
        </ol>

        <div className="space-y-2 rounded-lg border bg-muted p-3">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                send();
              }
            }}
            rows={2}
            placeholder="메시지를 입력하거나 행사 URL을 붙여 넣으세요…"
            className="w-full resize-none bg-transparent text-[13px] outline-none placeholder:text-muted-foreground"
          />
          {/* Source material (#20): the agent's primary source; sent with this message and kept on the Brainstorm. */}
          <SourceMaterialPicker
            value={attaching}
            onChange={setAttaching}
            disabled={sending}
            max={MAX_MATERIALS - (brainstorm.material?.length ?? 0)}
            compact
          />
          <div className="flex items-center gap-2">
            <Button variant="outline" size="lg" onClick={() => setInput((v) => (v ? `${v} https://` : "https://"))}>
              <Link2 /> URL
            </Button>
            <Button size="lg" className="ml-auto" onClick={send} disabled={sending || (!input.trim() && !attaching.length)}>
              <Send /> Send
            </Button>
          </div>
        </div>
      </section>

      {/* ---- Board */}
      <div className="flex min-w-0 flex-1 flex-col gap-3">
        <section className="space-y-2.5 rounded-xl border bg-card px-5 py-3.5">
          <div className="flex items-center">
            <h2 className="flex-1 text-[15px] font-semibold">① Event facts · from your material and links</h2>
            {sourceOk && <StatusBadge tone="success">Link 200 OK · official</StatusBadge>}
          </div>
          {(brainstorm.material?.length ?? 0) > 0 && (
            <ul className="grid gap-1.5" aria-label="Source material on this brainstorm">
              {brainstorm.material!.map((m) => (
                <MaterialChip key={m.id} m={m} />
              ))}
            </ul>
          )}
          <div className="flex gap-2">
            {brainstorm.facts.map((f) => (
              <div
                key={f.key}
                title={
                  f.verified
                    ? f.material_id
                      ? `From your material: ${materialById.get(f.material_id)?.name ?? f.material_id}`
                      : f.source_url ?? undefined
                    : "Not found in the source — copywriter won't state it as fact"
                }
                className={cn("flex-1 space-y-1 rounded-lg px-3 py-2.5", f.verified ? "bg-muted" : "bg-warning-soft")}
              >
                <p className={cn("text-[11px] font-medium", f.verified ? "text-muted-foreground" : "text-warning")}>
                  {FACT_LABEL[f.key]}
                  {!f.verified && " · unverified"}
                  {f.verified && f.material_id && " · your material"}
                </p>
                <p className="text-xs font-semibold">{f.value}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="space-y-2.5 rounded-xl border bg-card px-5 py-3.5">
          <h2 className="text-[15px] font-semibold">② Pick an angle</h2>
          <div className="flex gap-2.5">
            {brainstorm.angles.map((a, i) => {
              const selected = a.id === brainstorm.selected_angle_id;
              return (
                <button
                  key={a.id}
                  type="button"
                  aria-pressed={selected}
                  onClick={() => edit({ ...brainstorm, selected_angle_id: a.id })}
                  className={cn(
                    "flex-1 space-y-1.5 rounded-[10px] border px-3.5 py-3 text-left transition-colors hover:border-primary/40",
                    selected && "border-2 border-primary bg-brand-soft hover:border-primary",
                  )}
                >
                  <p className={cn("text-[11px] font-semibold", selected ? "text-primary" : "text-muted-foreground")}>
                    Angle {String.fromCharCode(65 + i)}
                  </p>
                  <p className="text-sm font-semibold">{a.title}</p>
                  <p className="text-xs text-muted-foreground">{a.hook}</p>
                </button>
              );
            })}
          </div>
          {(
            [
              ["Target", "targets", targetOptions],
              ["Tone", "tones", toneOptions],
            ] as const
          ).map(([label, field, options]) => (
            <div key={field} className="flex flex-wrap items-center gap-2 text-xs font-medium text-muted-foreground">
              <span className="w-12">{label}</span>
              {options.map((t) => (
                <Chip key={t} selected={brainstorm[field].includes(t)} onClick={() => edit({ ...brainstorm, [field]: toggle(brainstorm[field], t) })}>
                  {t}
                </Chip>
              ))}
            </div>
          ))}
        </section>

        <section className="flex-1 rounded-xl border bg-card px-5 py-3.5">
          <div className="flex items-center pb-1.5">
            <h2 className="flex-1 text-[15px] font-semibold">③ Slide outline · {brainstorm.outline.length} slides</h2>
            <span className="text-xs text-muted-foreground">click to edit · arrows to reorder</span>
          </div>
          <ol className="space-y-0.5">
            {brainstorm.outline.map((o, i) => (
              <li
                key={i}
                className={cn("group flex items-center gap-2.5 rounded-lg px-2.5 py-1", o.updated_from_chat && "bg-brand-soft")}
              >
                <span className="w-5 text-xs font-semibold text-muted-foreground tabular-nums">{String(i + 1).padStart(2, "0")}</span>
                <StatusBadge tone="neutral" className="w-14 justify-center">{o.layout}</StatusBadge>
                <input
                  value={o.heading}
                  onChange={(e) =>
                    edit({ ...brainstorm, outline: brainstorm.outline.map((x, j) => (j === i ? { ...x, heading: e.target.value } : x)) })
                  }
                  aria-label={`Slide ${i + 1} heading`}
                  className={cn(
                    "min-w-0 flex-1 rounded bg-transparent px-1 py-0.5 text-[13px] outline-none focus:bg-card focus:ring-1 focus:ring-ring",
                    o.updated_from_chat && "font-semibold",
                  )}
                />
                {o.updated_from_chat && <StatusBadge tone="info">updated from chat</StatusBadge>}
                <span className="flex opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
                  <Button variant="ghost" size="icon-xs" aria-label="Move up" disabled={i === 0} onClick={() => move(i, -1)}>
                    <ArrowUp />
                  </Button>
                  <Button variant="ghost" size="icon-xs" aria-label="Move down" disabled={i === brainstorm.outline.length - 1} onClick={() => move(i, 1)}>
                    <ArrowDown />
                  </Button>
                </span>
              </li>
            ))}
          </ol>
        </section>

        <div className="flex items-center gap-2.5">
          <p className={cn("flex-1 text-xs", error ? "text-destructive" : "text-muted-foreground")}>
            {error ? `Error: ${error}` : null}
            {!error && "Generate skips Research and starts at Verify → Copy → Render → QA → Review. You still approve before posting."}
          </p>
          <Button variant="outline" size="lg" onClick={() => edit(brainstorm)}>
            Save brainstorm
          </Button>
          <Button
            size="lg"
            disabled={generating || !brainstorm.selected_angle_id}
            onClick={() =>
              startGenerating(async () => {
                try {
                  await api.generateFromBrainstorm(brainstorm.id);
                  router.push("/jobs/new"); // shows the newest job's pipeline
                } catch (e) {
                  setError((e as Error).message);
                }
              })
            }
          >
            {generating ? "Starting…" : "Generate card news"} <ArrowRight />
          </Button>
        </div>
      </div>
    </div>
  );
}
