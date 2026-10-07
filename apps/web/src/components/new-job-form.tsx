"use client";

import { useState, useTransition } from "react";
import { Play } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { createJob } from "@/lib/api";

export function NewJobForm({ presets, initialPrompt }: { presets: { label: string; prompt: string }[]; initialPrompt: string }) {
  const [prompt, setPrompt] = useState(initialPrompt);
  const [pending, startTransition] = useTransition();

  return (
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
      <div className="flex items-center gap-2">
        <span className="text-xs font-medium text-muted-foreground">Presets</span>
        {presets.map((p) => (
          <Button key={p.label} variant="outline" size="lg" onClick={() => setPrompt(p.prompt)}>
            {p.label}
          </Button>
        ))}
        <Button
          size="lg"
          className="ml-auto"
          disabled={pending || prompt.trim().length < 3}
          onClick={() => startTransition(async () => void (await createJob(prompt)))}
        >
          <Play /> {pending ? "Starting…" : "Run agent"}
        </Button>
      </div>
    </section>
  );
}
