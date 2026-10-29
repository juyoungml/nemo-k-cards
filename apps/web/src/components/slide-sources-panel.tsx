"use client";

import { FileText, Link2, TriangleAlert } from "lucide-react";

import { Panel } from "@/components/page";
import { StatusBadge, type Tone } from "@/components/status-badge";
import type { Job, SlideFact, SlideFactSource, SlideSources } from "@/lib/types";
import { cn } from "@/lib/utils";

const FIELD: Record<string, string> = {
  title: "Event",
  dates: "When",
  time: "Time",
  venue: "Where",
  station: "Station",
  address: "Address",
  price: "Price",
  booking: "Entry",
  access: "Access",
  why_go: "Why go",
};

function host(url: string) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

/** A source a reader can rely on: a link that passed the check, or this Job's own material whose words were found. */
const isGood = (s: SlideFactSource) =>
  s.verified !== false && (s.origin === "material" ? !!s.material_name : s.status === "ok" || s.status === "redirect");

function linkStatus(s: SlideFactSource): [Tone, string] {
  switch (s.status) {
    case "ok":
      return ["success", `${s.http_code ?? 200} OK`];
    case "redirect":
      return ["success", "redirect"];
    case "dead":
      return ["danger", s.http_code ? `dead · ${s.http_code}` : "dead"];
    case "suspicious":
      return ["danger", s.reason ? `suspicious · ${s.reason}` : "suspicious"];
    case "timeout":
      return ["warning", "timed out"];
    default:
      return ["warning", "unchecked"];
  }
}

function SourceLine({ s }: { s: SlideFactSource }) {
  if (s.origin === "material") {
    return (
      <li className="min-w-0 space-y-1">
        <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
          <FileText className="size-3.5 shrink-0 text-muted-foreground" />
          {s.material_name ? (
            <span className="min-w-0 truncate">
              From your material · <span className="font-medium">{s.material_name}</span>
            </span>
          ) : (
            <span className="text-muted-foreground">Material that isn&apos;t part of this request</span>
          )}
          {s.verified === false ? (
            <StatusBadge tone="warning">not found in your material</StatusBadge>
          ) : (
            s.material_name && <StatusBadge tone="success">verified</StatusBadge>
          )}
        </div>
        {s.quote && (
          <blockquote className="ml-5.5 border-l-2 pl-2 text-muted-foreground italic break-words">“{s.quote}”</blockquote>
        )}
        {s.note && s.verified === false && s.material_name && <p className="ml-5.5 text-muted-foreground">{s.note}</p>}
      </li>
    );
  }
  const [tone, label] = linkStatus(s);
  return (
    <li className="min-w-0 space-y-1">
      <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
        <Link2 className="size-3.5 shrink-0 text-muted-foreground" />
        {s.url ? (
          <a href={s.url} target="_blank" rel="noreferrer noopener" className="min-w-0 truncate hover:underline" title={s.url}>
            {host(s.url)}
          </a>
        ) : (
          <span className="text-muted-foreground">no link given</span>
        )}
        <StatusBadge tone={tone}>{label}</StatusBadge>
      </div>
      {s.note && s.verified === false && <p className="ml-5.5 text-muted-foreground">{s.note}</p>}
    </li>
  );
}

function FactRow({ f }: { f: SlideFact }) {
  const backed = f.sources.some(isGood);
  return (
    <li className={cn("rounded-lg border px-3 py-2.5 text-xs", !backed && "border-warning/40 bg-warning-soft/40")}>
      <div className="flex min-w-0 flex-col gap-0.5 sm:flex-row sm:items-baseline sm:gap-3">
        <span className="shrink-0 text-[11px] font-medium tracking-wide text-muted-foreground uppercase sm:w-16">
          {FIELD[f.field] ?? f.field}
        </span>
        <span className="min-w-0 text-[13px] break-words">{f.value}</span>
      </div>
      <div className="mt-2 space-y-1.5 sm:pl-19">
        {!f.recorded && !f.edited && <p className="text-[11px] text-muted-foreground">The event&apos;s sources (no source recorded for this fact)</p>}
        {f.edited ? (
          <StatusBadge tone="warning">edited — re-check sources</StatusBadge>
        ) : f.sources.length ? (
          <ul className="space-y-1.5">
            {f.sources.map((s, i) => (
              <SourceLine key={i} s={s} />
            ))}
          </ul>
        ) : (
          <StatusBadge tone="danger">no source</StatusBadge>
        )}
        {!backed && !f.edited && f.sources.length > 0 && (
          <p className="flex items-center gap-1 font-medium text-warning">
            <TriangleAlert className="size-3.5" /> No working source: check before approving
          </p>
        )}
      </div>
    </li>
  );
}

function summary(s: SlideSources) {
  const all = [...s.facts.flatMap((f) => f.sources), ...s.links];
  const material = s.facts.filter((f) => f.sources.some((x) => x.origin === "material" && isGood(x))).length;
  const bad = all.filter((x) => x.origin === "link" && ["dead", "suspicious"].includes(x.status ?? "")).length;
  const unchecked = all.filter((x) => x.origin === "link" && (x.status === "unchecked" || x.status === "timeout")).length;
  const unbacked = s.facts.filter((f) => !f.sources.some(isGood)).length;
  return { material, bad, unchecked, unbacked };
}

/**
 * Where each fact on the selected slide came from (#25, story 2): a checked link with its status, or the
 * Organization's own Source material with the quoted words. Dead, suspicious and unchecked links are marked, and
 * claims in the slide's text that no source supports are listed.
 *
 * Sources describe the text the pipeline wrote. After a Deck edit (#19) the API recomputes `unsourced` for the new
 * text and flags what a person typed (`edited`): it reads "edited — re-check sources" instead of the old sources.
 */
export function SlideSourcesPanel({ job, slide, embedded = false }: { job: Job; slide: number; embedded?: boolean }) {
  const current = job.deck?.slides[slide];
  const s = current && job.slide_sources?.find((x) => x.slide_index === current.index);
  if (!s) return null;
  const n = summary(s);
  const empty = !s.facts.length && !s.links.length && !s.unsourced.length;

  return (
    <Panel
      title={embedded ? undefined : `Sources · slide ${slide + 1}`}
      description={s.event_title ? `${s.event_title}: where each fact on this slide came from.` : "Where the facts on this slide came from."}
      className={embedded ? "border-0 bg-transparent p-0 md:p-0" : undefined}
    >
      {embedded && (
        <p className="mb-3 text-xs text-muted-foreground">
          {s.event_title ? `${s.event_title}: where each fact on this slide came from.` : "Where the facts on this slide came from."}
        </p>
      )}
      {!empty && (
        <div className="mb-3 flex flex-wrap gap-2">
          {s.facts.length > 0 && <StatusBadge tone="neutral">{`${s.facts.length} fact${s.facts.length === 1 ? "" : "s"}`}</StatusBadge>}
          {n.material > 0 && <StatusBadge tone="success">{`${n.material} from your material`}</StatusBadge>}
          {n.bad > 0 && <StatusBadge tone="danger">{`${n.bad} dead or suspicious link${n.bad === 1 ? "" : "s"}`}</StatusBadge>}
          {n.unchecked > 0 && <StatusBadge tone="warning">{`${n.unchecked} unchecked`}</StatusBadge>}
          {n.unbacked > 0 && <StatusBadge tone="warning">{`${n.unbacked} without a working source`}</StatusBadge>}
          {s.unsourced.length > 0 && (
            <StatusBadge tone={s.general ? "neutral" : "danger"}>{`${s.unsourced.length} not in any source`}</StatusBadge>
          )}
        </div>
      )}

      {s.note && <p className="mb-3 rounded-lg bg-warning-soft px-3 py-2 text-xs text-warning">{s.note}</p>}
      {!!s.edited?.length && (
        <p className="mb-3 rounded-lg bg-warning-soft px-3 py-2 text-xs text-warning">
          Edited — re-check sources: this slide&apos;s {s.edited.join(" and ")} {s.edited.length > 1 ? "were" : "was"} changed by a person after
          research, so its sources may not cover the new words.
        </p>
      )}

      {s.facts.length > 0 && (
        <ul className="space-y-2">
          {s.facts.map((f) => (
            <FactRow key={f.field} f={f} />
          ))}
        </ul>
      )}

      {s.links.length > 0 && (
        <div className="mt-3">
          <h3 className="mb-1.5 text-xs font-semibold">Links on this slide</h3>
          <ul className="space-y-1.5 text-xs">
            {s.links.map((l, i) => (
              <SourceLine key={i} s={l} />
            ))}
          </ul>
        </div>
      )}

      {s.unsourced.length > 0 && (
        <div className="mt-3">
          <h3 className="mb-1.5 text-xs font-semibold">{s.general ? "General advice (no source expected)" : "No source found"}</h3>
          <ul className="space-y-1.5">
            {s.unsourced.map((u) => (
              <li
                key={u}
                className={cn("rounded-lg px-3 py-2 text-xs", s.general ? "bg-muted text-muted-foreground" : "bg-danger-soft text-destructive")}
              >
                {u.charAt(0).toUpperCase() + u.slice(1)}
              </li>
            ))}
          </ul>
        </div>
      )}

      {empty && (
        <p className="text-xs text-muted-foreground">
          No event facts on this slide, and its text has no price, date or time that needs a source.
        </p>
      )}
    </Panel>
  );
}
