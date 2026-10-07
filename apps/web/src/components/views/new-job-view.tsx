"use client";

import Link from "next/link";
import { useEffect, useRef, useState, useTransition } from "react";
import { ArrowRight, Check, Play, RotateCcw, X } from "lucide-react";

import { LoadState } from "@/components/page";
import { JobStatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { API_URL, IS_MOCK, api } from "@/lib/api";
import { SCENARIO_LABELS, presets } from "@/lib/constants";
import type { Job, PipelineStep, Scenario } from "@/lib/types";
import { TERMINAL_STATUSES as TERMINAL, useApi, useLiveJob } from "@/lib/use-api";
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

export function NewJobView() {
  const [prompt, setPrompt] = useState(presets[1].prompt);
  const [scenario, setScenario] = useState<Scenario>("good");
  const [job, setJob] = useState<Job>();
  const [error, setError] = useState<string>();
  const [pending, startTransition] = useTransition();

  // On arrival, resume a job that is still running; finished jobs live in Review, not here.
  const recent = useApi(api.listJobs);
  const started = useRef(false); // set once the operator runs a job; a late background load must not overwrite it
  const last = recent.data?.[0];
  useEffect(() => {
    if (!last || TERMINAL.includes(last.status)) return;
    api
      .getJob(last.id)
      .then((j) => !started.current && setJob((cur) => cur ?? j))
      .catch(() => {});
  }, [last]);

  const { live, stale } = useLiveJob<Job>(job, setJob, api.jobEventsUrl, api.getJob);
  const busy = pending || live;

  const run = (text = prompt) =>
    startTransition(async () => {
      started.current = true;
      try {
        setError(undefined);
        setJob(await api.createJob(text, IS_MOCK ? scenario : undefined));
      } catch (e) {
        setError((e as Error).message);
      }
    });

  // Keep the newest log line in view unless the operator scrolled up to read.
  const logRef = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  useEffect(() => {
    const el = logRef.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [job?.log?.length]);

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
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && !busy && prompt.trim().length >= 3) run();
          }}
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
            <Button size="lg" disabled={busy || prompt.trim().length < 3} onClick={() => run()} title="⌘/Ctrl + Enter">
              <Play /> {pending ? "Starting…" : live ? "Agent running…" : "Run agent"}
            </Button>
          </div>
        </div>
        {live && !pending && (
          <p className="text-xs text-muted-foreground">
            Job {job?.id} is still running. One job at a time keeps agent cost and the log readable; you can start the next
            one when it finishes.
          </p>
        )}
        {error && <p className="text-sm text-destructive">{error}</p>}
      </section>

      {!job ? (
        recent.error || !recent.data ? (
          <LoadState error={recent.error} />
        ) : (
          <div className="rounded-xl border bg-card p-5 text-sm text-muted-foreground">
            Write a request and run the agent. Progress and the live log show up here.
            {last && (
              <>
                {" "}
                Last job:{" "}
                <Link href={`/review/${last.id}`} className="font-medium text-foreground underline-offset-2 hover:underline">
                  {last.deck?.slides[0]?.heading ?? last.prompt}
                </Link>
                .
              </>
            )}
          </div>
        )
      ) : (
        <div className="flex items-stretch gap-4">
          <section className="w-[420px] shrink-0 rounded-xl border bg-card p-5">
            <div className="flex items-center gap-2 pb-3">
              <h2 className="flex-1 text-base font-semibold">Pipeline · job {job.id}</h2>
              <JobStatusBadge status={job.status} mock={!!job.published_url?.includes("/p/MOCK")} />
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
            {job.status === "FAILED" && (
              <Button variant="outline" size="lg" className="mt-3 w-full" disabled={busy} onClick={() => run(job.prompt)}>
                <RotateCcw /> Retry this request
              </Button>
            )}
            {TERMINAL.includes(job.status) && job.status !== "FAILED" && (
              <Button variant="outline" size="lg" className="mt-3 w-full" asChild>
                <Link href={`/review/${job.id}`}>
                  Open in Review <ArrowRight />
                </Link>
              </Button>
            )}
          </section>

          <section
            ref={logRef}
            onScroll={(e) => {
              const el = e.currentTarget;
              pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
            }}
            className="max-h-[560px] min-w-0 flex-1 space-y-2 overflow-y-auto rounded-xl bg-[#111418] p-5 font-mono text-xs"
          >
            <h2 className="sticky -top-5 -mx-5 -mt-5 bg-[#111418] px-5 pt-5 pb-1 font-sans text-[13px] font-medium text-white">
              Live log (SSE {API_URL}/jobs/{job.id}/events)
              {stale && <span className="ml-2 text-warning-soft">· reconnecting…</span>}
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
