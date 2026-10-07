import { Check } from "lucide-react";

import { ModeSwitch } from "@/components/mode-switch";
import { NewJobForm } from "@/components/new-job-form";
import { PageHeader } from "@/components/page";
import { StatusBadge } from "@/components/status-badge";
import { getPipeline } from "@/lib/api";
import { presets } from "@/lib/mock";
import type { PipelineStep } from "@/lib/types";
import { cn } from "@/lib/utils";

function StepDot({ step, n }: { step: PipelineStep; n: number }) {
  return (
    <span
      className={cn(
        "flex size-6 shrink-0 items-center justify-center rounded-full text-xs font-bold",
        step.state === "done" && "bg-success text-white",
        step.state === "running" && "bg-primary text-white",
        step.state === "failed" && "bg-destructive text-white",
        step.state === "pending" && "bg-muted text-muted-foreground",
      )}
    >
      {step.state === "done" ? <Check className="size-3.5" strokeWidth={3} /> : n}
    </span>
  );
}

export default async function NewJobPage() {
  const { job, steps, log } = await getPipeline();

  return (
    <>
      <PageHeader
        title="New Job"
        description="Describe what to research. The agent runs in an OpenShell sandbox; you approve before anything is posted."
      />

      <ModeSwitch active="quick" />

      <NewJobForm presets={presets} initialPrompt={job.prompt} />

      <div className="flex items-stretch gap-4">
        <section className="w-[420px] shrink-0 rounded-xl border bg-card p-5">
          <div className="flex items-center pb-3">
            <h2 className="flex-1 text-base font-semibold">Pipeline · job {job.id}</h2>
            {/* TODO(backend): drive from SSE GET /jobs/{id}/events */}
            <StatusBadge tone="info">Running</StatusBadge>
          </div>
          <ol>
            {steps.map((s, i) => (
              <li key={s.name} className="flex gap-3 py-2.5">
                <StepDot step={s} n={i + 1} />
                <div className="space-y-0.5">
                  <p className={cn("text-sm", s.state === "pending" ? "text-muted-foreground" : "font-semibold")}>
                    {s.name}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {s.where}
                    {s.note && `  ·  ${s.note}`}
                  </p>
                </div>
              </li>
            ))}
          </ol>
        </section>

        <section className="flex-1 space-y-2 rounded-xl bg-[#111827] p-5 font-mono text-xs">
          <h2 className="pb-1 font-sans text-[13px] font-medium text-white">Live log (SSE /jobs/{job.id}/events)</h2>
          {log.map((l, i) => (
            <p key={i} className="flex gap-3">
              <span className="text-gray-500">{l.time}</span>
              <span className={cn("w-16 shrink-0 font-semibold", l.level === "warn" ? "text-warning-soft" : "text-brand-soft")}>
                {l.stage}
              </span>
              <span className="text-white">{l.message}</span>
            </p>
          ))}
        </section>
      </div>
    </>
  );
}
