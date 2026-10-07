"use client";

import { useState } from "react";

import type { Slide } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * Shows the renderer's 1080x1350 JPEGs (job.slide_urls) when the real backend serves them;
 * falls back to an HTML stand-in (scaled to 400x500) for the mock backend.
 * Pass `current` + `onSelect` to control which slide is shown (e.g. clicking an issue jumps to its slide).
 */
export function SlidePreview({
  slides,
  images,
  current: controlled,
  onSelect,
}: {
  slides: Slide[];
  images?: string[];
  current?: number;
  onSelect?: (index: number) => void;
}) {
  const [own, setOwn] = useState(0);
  const current = Math.min(controlled ?? own, slides.length - 1);
  const select = (i: number) => (onSelect ? onSelect(i) : setOwn(i));
  const slide = slides[current];
  const img = images?.[current];

  return (
    <div className="w-[400px] shrink-0 space-y-3">
      {img ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={img} alt={`Slide ${current + 1}: ${slide.heading}`} className="aspect-[4/5] w-full rounded-xl border object-cover" />
      ) : (
        <div className="flex aspect-[4/5] flex-col rounded-xl border bg-[#FFFDF8] px-8 pt-9 pb-6">
          <div className="h-1.5 w-12 rounded-full bg-[linear-gradient(90deg,var(--brand-red)_50%,var(--primary)_50%)]" />
          <div className={cn("space-y-3.5", slide.layout === "cover" ? "mt-24" : "mt-8")}>
            <h3 className={cn("font-bold", slide.layout === "cover" ? "text-[34px] leading-tight" : "text-2xl")}>{slide.heading}</h3>
            <p className="text-sm whitespace-pre-line text-muted-foreground">{slide.body}</p>
          </div>
          <div className="mt-auto flex text-xs">
            <span className="flex-1 font-bold text-primary">@whatsonkorea</span>
            <span className="text-muted-foreground">
              {current + 1} / {slides.length}
            </span>
          </div>
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        {slides.map((s, i) => (
          <button
            key={s.index}
            onClick={() => select(i)}
            aria-label={`Slide ${i + 1}: ${s.heading}`}
            aria-current={i === current}
            className={cn(
              "h-[60px] w-12 overflow-hidden rounded-md border bg-card text-[10px] text-muted-foreground transition-shadow",
              i === current ? "border-2 border-primary ring-2 ring-primary/20" : "hover:border-muted-foreground",
            )}
          >
            {images?.[i] ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={images[i]} alt="" className="size-full object-cover" />
            ) : (
              i + 1
            )}
          </button>
        ))}
      </div>
    </div>
  );
}
