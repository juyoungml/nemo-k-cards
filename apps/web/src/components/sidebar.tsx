"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { FileCheck2, LayoutDashboard, ShieldAlert, Sparkles } from "lucide-react";

import { cn } from "@/lib/utils";

const NAV = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/jobs/new", label: "New Job", icon: Sparkles },
  { href: "/review", label: "Review", icon: FileCheck2 },
  { href: "/policy", label: "Policy Log", icon: ShieldAlert },
];

export function Sidebar() {
  const pathname = usePathname();
  const isActive = (href: string) => (href === "/" ? pathname === "/" : pathname.startsWith(href));

  return (
    <aside className="sticky top-0 flex h-screen w-60 shrink-0 flex-col border-r bg-card px-4 py-6">
      <div className="flex items-center gap-2.5 px-2 pb-6">
        <div className="size-7 rounded-[8px] bg-[linear-gradient(90deg,var(--brand-red)_50%,var(--primary)_50%)]" />
        <div className="leading-tight">
          <p className="text-[15px] font-bold">What&apos;s On Korea</p>
          <p className="text-[11px] text-muted-foreground">@whatsonkorea · Admin</p>
        </div>
      </div>
      <nav className="flex flex-col gap-1">
        {NAV.map(({ href, label, icon: Icon }) => (
          <Link
            key={href}
            href={href}
            className={cn(
              "flex h-10 items-center gap-2.5 rounded-lg px-3 text-sm text-muted-foreground transition-colors hover:bg-muted",
              isActive(href) && "bg-brand-soft font-semibold text-primary hover:bg-brand-soft",
            )}
          >
            <Icon className="size-4" />
            {label}
          </Link>
        ))}
      </nav>
      <div className="mt-auto flex items-center gap-2.5 px-2">
        <div className="size-7 rounded-full bg-muted" />
        <span className="text-[13px] font-medium text-muted-foreground">Content Manager</span>
      </div>
    </aside>
  );
}
