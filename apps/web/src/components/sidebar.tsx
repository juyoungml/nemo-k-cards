"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Suspense } from "react";
import { FileCheck2, LayoutDashboard, RotateCcw, ShieldAlert, Sparkles } from "lucide-react";

import { IS_MOCK, api } from "@/lib/api";

import { cn } from "@/lib/utils";

const NAV = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/jobs/new", label: "New Job", icon: Sparkles },
  { href: "/review", label: "Review", icon: FileCheck2 },
  { href: "/policy", label: "Policy Log", icon: ShieldAlert },
];

function NavLinks({ pathname }: { pathname: string | null }) {
  const isActive = (href: string) => pathname !== null && (href === "/" ? pathname === "/" : pathname.startsWith(href));
  return (
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
  );
}

// usePathname() is request data under cacheComponents, so it lives behind a Suspense boundary.
function ActiveNav() {
  return <NavLinks pathname={usePathname()} />;
}

export function Sidebar() {
  return (
    <aside className="sticky top-0 flex h-screen w-60 shrink-0 flex-col border-r bg-card px-4 py-6">
      <div className="flex items-center gap-2.5 px-2 pb-6">
        <div className="size-7 rounded-[8px] bg-[linear-gradient(90deg,var(--brand-red)_50%,var(--primary)_50%)]" />
        <div className="leading-tight">
          <p className="text-[15px] font-bold">What&apos;s On Korea</p>
          <p className="text-[11px] text-muted-foreground">@whatsonkorea · Admin</p>
        </div>
      </div>
      <Suspense fallback={<NavLinks pathname={null} />}>
        <ActiveNav />
      </Suspense>
      {IS_MOCK && (
        <div className="mt-auto space-y-1.5 rounded-lg border border-dashed border-warning/50 bg-warning-soft p-3 text-xs text-warning">
          <p className="font-semibold">Mock API</p>
          <p>Data is simulated in-memory (QA mode). Set NEXT_PUBLIC_API_URL to use FastAPI.</p>
          <button
            type="button"
            onClick={() => api.resetMock().then(() => location.reload())}
            className="inline-flex items-center gap-1 font-semibold underline-offset-2 hover:underline"
          >
            <RotateCcw className="size-3" /> Reset mock data
          </button>
        </div>
      )}
      <div className={cn("flex items-center gap-2.5 px-2", IS_MOCK ? "mt-4" : "mt-auto")}>
        <div className="size-7 rounded-full bg-muted" />
        <span className="text-[13px] font-medium text-muted-foreground">Content Manager</span>
      </div>
    </aside>
  );
}
