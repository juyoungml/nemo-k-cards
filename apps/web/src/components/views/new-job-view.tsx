"use client";

import Link from "next/link";
import { useEffect, useState, useTransition } from "react";
import { ArrowRight, Check, Play, X } from "lucide-react";

import { LoadState } from "@/components/page";
import { JobStatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { API_URL, IS_MOCK, api } from "@/lib/api";
import { SCENARIO_LABELS, presets } from "@/lib/constants";
import type { Job, PipelineStep, Scenario } from "@/lib/types";
import { useApi, useEventSource } from "@/lib/use-api";
import { cn } from "@/lib/utils";

function StepDot({ step, n }: { step: PipelineStep; n: number }) {
  return (
    <span
      className={cn(
        "flex size-6 shrink-0 items-center justify-center rounded-full text-xs font-bold",
        step.state === "done" && "bg-success text-white",
        step.state === "running" && "animate-pulse bg-primary text-white",
        step.state === "failed" && "bg-destructive text-white",
        step.state === "pending" && "bg-muted text-muted-foreground",
      )}
    >
      {step.state === "done" ? <Check className="size-3.5" strokeWidth={3} /> : step.state === "failed" ? <X className="size-3.5" strokeWidth={3} /> : n}
    </span>
  );
}

const TERMINAL = ["READY_FOR_REVIEW", "REJECTED", "PUBLISHED", "FAILED"];

export function NewJobView() {
  const [prompt, setPrompt] = useState(presets[1].prompt);
  const [scenario, setScenario] = useState<Scenario>("good");
  const [drill, setDrill] = useState(false);
  const [job, setJob] = useState<Job>();
  const [error, setError] = useState<string>();
  const [pending, startTransition] = useTransition();

  // Show the most recent job until the operator starts a new one.
  const recent = useApi(api.listJobs);
  useEffect(() => {
    if (!job && recent.data?.[0]) api.getJob(recent.data[0].id).then(setJob).catch(() => {});
  }, [recent.data, job]);

  const live = job && !TERMINAL.includes(job.status) ? api.jobEventsUrl(job.id) : null;
  useEventSource<Job>(live, setJob);

  const run = () =>
    startTransition(async () => {
      try {
        setError(undefined);
        setJob(await api.createJob(prompt, IS_MOCK ? scenario : drill ? "injection" : undefined));
      } catch (e) {
        setError((e as Error).message);
      }
    });

  return (
    <>
      <section className="space-y-3.5 rounded-xl border bg-card p-5">
        <label htmlFor="prompt" className="text-[13px] font-medium text-muted-foreground">
          Request
        </label>
        <Textarea
          id="prompt"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          className="min-h-24 bg-muted text-[15px]"
          placeholder="예: 이번 주말 서울 팝업 5개 조사해서 카드뉴스 만들어줘"
        />
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-medium text-muted-foreground">Presets</span>
          {presets.map((p) => (
            <Button key={p.label} variant="outline" size="lg" onClick={() => setPrompt(p.prompt)}>
              {p.label}
            </Button>
          ))}
          <div className="ml-auto flex items-center gap-2">
            {IS_MOCK && (
              <label className="flex items-center gap-1.5 rounded-lg border border-dashed border-warning/50 bg-warning-soft px-2 py-1 text-xs font-medium text-warning">
                QA scenario
                <select
                  value={scenario}
                  onChange={(e) => setScenario(e.target.value as Scenario)}
                  className="bg-transparent font-semibold outline-none"
                  data-testid="scenario"
                >
                  {Object.entries(SCENARIO_LABELS).map(([k, v]) => (
                    <option key={k} value={k}>
                      {v}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {!IS_MOCK && (
              <label
                className="flex items-center gap-1.5 rounded-lg border border-dashed border-warning/50 bg-warning-soft px-2 py-1 text-xs font-medium text-warning"
                title="The researcher also reads a demo event page with hidden instructions; a drill stage then replays them in a sandbox so OpenShell's blocks show up in the Policy Log."
              >
                <input type="checkbox" checked={drill} onChange={(e) => setDrill(e.target.checked)} data-testid="injection-drill" />
                Injection drill
              </label>
            )}
            <Button size="lg" disabled={pending || prompt.trim().length < 3} onClick={run}>
              <Play /> {pending ? "Starting…" : "Run agent"}
            </Button>
          </div>
        </div>
        {error && <p className="text-sm text-destructive">{error}</p>}
      </section>

      {!job ? (
        <LoadState error={recent.error} label={recent.data?.length === 0 ? "No jobs yet — run the agent." : "Loading…"} />
      ) : (
        <div className="flex items-stretch gap-4">
          <section className="w-[420px] shrink-0 rounded-xl border bg-card p-5">
            <div className="flex items-center gap-2 pb-3">
              <h2 className="flex-1 text-base font-semibold">Pipeline · job {job.id}</h2>
              <JobStatusBadge status={job.status} />
            </div>
            <ol>
              {job.pipeline?.map((s, i) => (
                <li key={s.name} className="flex gap-3 py-2.5">
                  <StepDot step={s} n={i + 1} />
                  <div className="space-y-0.5">
                    <p className={cn("text-sm", s.state === "pending" ? "text-muted-foreground" : "font-semibold")}>{s.name}</p>
                    <p className="text-xs text-muted-foreground">
                      {s.where}
                      {s.note && `  ·  ${s.note}`}
                    </p>
                  </div>
                </li>
              ))}
            </ol>
            {job.error && <p className="mt-2 rounded-lg bg-danger-soft px-3 py-2 text-xs text-destructive">{job.error}</p>}
            {TERMINAL.includes(job.status) && job.status !== "FAILED" && (
              <Button variant="outline" size="lg" className="mt-3 w-full" asChild>
                <Link href={`/review/${job.id}`}>
                  Open in Review <ArrowRight />
                </Link>
              </Button>
            )}
          </section>

          <section className="min-w-0 flex-1 space-y-2 rounded-xl bg-[#111827] p-5 font-mono text-xs">
            <h2 className="pb-1 font-sans text-[13px] font-medium text-white">
              Live log (SSE {API_URL}/jobs/{job.id}/events)
            </h2>
            {job.log?.map((l, i) => (
              <p key={i} className="flex gap-3">
                <span className="text-gray-500">{l.time}</span>
                <span className={cn("w-16 shrink-0 font-semibold", l.level === "warn" ? "text-warning-soft" : "text-brand-soft")}>{l.stage}</span>
                <span className="break-all text-white">{l.message}</span>
              </p>
            ))}
            {live && <p className="animate-pulse text-gray-500">…</p>}
          </section>
        </div>
      )}
    </>
  );
}
