import Link from "next/link";

import { cn } from "@/lib/utils";

const MODES = [
  {
    key: "quick",
    href: "/jobs/new",
    title: "⚡ Quick — one click",
    description: "한 줄 요청이면 끝. 조사부터 검수까지 에이전트가 알아서 진행해요.",
  },
  {
    key: "brainstorm",
    href: "/jobs/new/brainstorm",
    title: "💬 Brainstorm — plan together",
    description: "홍보하고 싶은 행사나 아이디어가 있을 때. URL과 메모로 에이전트와 같이 기획해요.",
  },
] as const;

/** Mirrors the Figma "ModeCard" component. */
export function ModeSwitch({ active }: { active: (typeof MODES)[number]["key"] }) {
  return (
    <nav className="flex gap-3" aria-label="Generation mode">
      {MODES.map((m) => {
        const selected = m.key === active;
        return (
          <Link
            key={m.key}
            href={m.href}
            aria-current={selected ? "page" : undefined}
            className={cn(
              "flex-1 space-y-1 rounded-xl border bg-card px-4 py-3.5 transition-colors hover:border-primary/40",
              selected && "border-2 border-primary bg-brand-soft hover:border-primary",
            )}
          >
            <p className={cn("text-[15px] font-semibold", selected && "text-primary")}>{m.title}</p>
            <p className="text-xs text-muted-foreground">{m.description}</p>
          </Link>
        );
      })}
    </nav>
  );
}
