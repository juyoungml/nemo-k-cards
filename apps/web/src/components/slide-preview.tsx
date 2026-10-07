"use client";

import { useState } from "react";

import type { Slide } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * HTML stand-in for the rendered 1080x1350 PNGs (scaled to 400x500).
 * TODO(backend): show the renderer's PNGs once job.slide_paths are served.
 */
export function SlidePreview({ slides }: { slides: Slide[] }) {
  const [current, setCurrent] = useState(0);
  const slide = slides[current];

  return (
    <div className="w-[400px] shrink-0 space-y-3">
      <div className="flex aspect-[4/5] flex-col rounded-xl border bg-[#FFFDF8] px-8 pt-9 pb-6">
        <div className="h-1.5 w-12 rounded-full bg-[linear-gradient(90deg,var(--brand-red)_50%,var(--primary)_50%)]" />
        <div className={cn("space-y-3.5", slide.layout === "cover" ? "mt-24" : "mt-8")}>
          <h3 className={cn("font-bold", slide.layout === "cover" ? "text-[34px] leading-tight" : "text-2xl")}>
            {slide.heading}
          </h3>
          <p className="text-sm whitespace-pre-line text-muted-foreground">{slide.body}</p>
        </div>
        <div className="mt-auto flex text-xs">
          <span className="flex-1 font-bold text-primary">@whatsonkorea</span>
          <span className="text-muted-foreground">
            {current + 1} / {slides.length}
          </span>
        </div>
      </div>
      <div className="flex gap-2">
        {slides.map((s) => (
          <button
            key={s.index}
            onClick={() => setCurrent(s.index)}
            aria-label={`Slide ${s.index + 1}`}
            className={cn(
              "h-[60px] w-12 rounded-md border bg-card text-[10px] text-muted-foreground",
              s.index === current && "border-2 border-primary bg-brand-soft text-primary",
            )}
          >
            {s.index + 1}
          </button>
        ))}
      </div>
    </div>
  );
}
