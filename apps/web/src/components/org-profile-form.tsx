"use client";

import { Loader2 } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import type { OrgProfile, OrgProfileState } from "@/lib/types";

const inputClass =
  "h-10 w-full rounded-lg border bg-background px-3 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-60";

const lines = (text: string) => text.split("\n").map((l) => l.trim()).filter(Boolean);

function Row({ label, hint, htmlFor, children }: { label: string; hint: string; htmlFor: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={htmlFor} className="text-[13px] font-medium">{label}</label>
      {children}
      <p className="text-xs text-muted-foreground">{hint}</p>
    </div>
  );
}

/**
 * The Organization profile (#16) as a form: onboarding and the Profile page both use it. Lists are one entry per
 * line. `readOnly` shows the same fields to Members who aren't Admins.
 */
export function OrgProfileForm({ initial, readOnly = false, submitLabel = "Save profile", onSaved, secondary }: {
  initial: OrgProfile;
  readOnly?: boolean;
  submitLabel?: string;
  onSaved?: (state: OrgProfileState) => void;
  secondary?: React.ReactNode;
}) {
  const [whatItDoes, setWhatItDoes] = useState(initial.what_it_does);
  const [audience, setAudience] = useState(initial.audience);
  const [region, setRegion] = useState(initial.home_region);
  const [subjects, setSubjects] = useState(initial.usual_subjects.join("\n"));
  const [neverSay, setNeverSay] = useState(initial.never_say.join("\n"));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [saved, setSaved] = useState(false);

  const tooMany = lines(subjects).length > 12 ? "At most 12 usual subjects." : lines(neverSay).length > 30 ? "At most 30 “never say” entries." : undefined;
  const tooLong = lines(subjects).some((l) => l.length > 80)
    ? "Each usual subject must be at most 80 characters."
    : lines(neverSay).some((l) => l.length > 200)
      ? "Each “never say” entry must be at most 200 characters."
      : undefined;
  const problem = tooMany ?? tooLong;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(undefined);
    setSaved(false);
    try {
      const state = await api.saveOrgProfile({
        what_it_does: whatItDoes.trim(),
        audience: audience.trim(),
        home_region: region.trim(),
        usual_subjects: lines(subjects),
        never_say: lines(neverSay),
      });
      // Show what the API stored (trimmed, blanks and repeats dropped).
      setWhatItDoes(state.profile.what_it_does);
      setAudience(state.profile.audience);
      setRegion(state.profile.home_region);
      setSubjects(state.profile.usual_subjects.join("\n"));
      setNeverSay(state.profile.never_say.join("\n"));
      setSaved(true);
      onSaved?.(state);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="space-y-5">
      <Row label="What your Organization does" htmlFor="p-what" hint="One or two sentences. The agents write as you, for your readers.">
        <Textarea id="p-what" value={whatItDoes} maxLength={500} disabled={readOnly} onChange={(e) => setWhatItDoes(e.target.value)}
          placeholder="e.g. Jeju's tourism office: festivals, food and nature on the island." className="min-h-20 text-sm" />
      </Row>
      <div className="grid gap-5 md:grid-cols-2">
        <Row label="Audience" htmlFor="p-audience" hint="Who reads your Decks.">
          <input id="p-audience" className={inputClass} value={audience} maxLength={300} disabled={readOnly}
            onChange={(e) => setAudience(e.target.value)} placeholder="e.g. Families and first-time visitors" />
        </Row>
        <Row label="Home region" htmlFor="p-region" hint="Used when a request doesn't name a place.">
          <input id="p-region" className={inputClass} value={region} maxLength={100} disabled={readOnly}
            onChange={(e) => setRegion(e.target.value)} placeholder="e.g. Jeju" />
        </Row>
      </div>
      <div className="grid gap-5 md:grid-cols-2">
        <Row label="Usual subjects" htmlFor="p-subjects" hint="One per line, up to 12.">
          <Textarea id="p-subjects" value={subjects} disabled={readOnly} onChange={(e) => setSubjects(e.target.value)}
            placeholder={"festivals\nnight markets\nhiking trails"} className="min-h-28 text-sm" />
        </Row>
        <Row label="Never say or promote" htmlFor="p-never" hint="One per line, up to 30. Agents avoid these, and Review flags any Deck that uses one (approving it then needs an Override).">
          <Textarea id="p-never" value={neverSay} disabled={readOnly} onChange={(e) => setNeverSay(e.target.value)}
            placeholder={"competitor brand names\ncasinos\nBlack Friday"} className="min-h-28 text-sm" />
        </Row>
      </div>
      {!readOnly && (
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" size="lg" disabled={busy || !!problem}>
            {busy && <Loader2 className="size-4 motion-safe:animate-spin" />} {submitLabel}
          </Button>
          {secondary}
          <p role={error || problem ? "alert" : "status"} className={error || problem ? "text-xs text-destructive" : "text-xs text-muted-foreground"}>
            {error ?? problem ?? (saved ? "Saved. New decks and Brainstorm messages use it from now on." : "")}
          </p>
        </div>
      )}
    </form>
  );
}
