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

function Brand() {
  return (
    <div className="flex items-center gap-2.5">
      <div className="relative size-7 rounded-[8px] after:absolute after:inset-[8px] after:rounded-full after:bg-card after:content-[''] bg-[conic-gradient(from_200deg,var(--taegeuk-red)_0_50%,var(--taegeuk-blue)_50%_100%)]" />
      <div className="leading-tight">
        <p className="font-heading text-[16px] font-extrabold tracking-[-0.01em]">What&apos;s On Korea</p>
        <p className="text-[11px] text-muted-foreground">@whatsonkorea · Admin</p>
      </div>
    </div>
  );
}

function TabLinks({ pathname }: { pathname: string | null }) {
  const isActive = (href: string) => pathname !== null && (href === "/" ? pathname === "/" : pathname.startsWith(href));
  return (
    <nav className="grid grid-cols-4">
      {NAV.map(({ href, label, icon: Icon }) => (
        <Link
          key={href}
          href={href}
          className={cn(
            "flex flex-col items-center gap-1 py-2 text-[11px] text-muted-foreground",
            isActive(href) && "font-semibold text-primary",
          )}
        >
          <Icon className="size-5" />
          {label}
        </Link>
      ))}
    </nav>
  );
}

function ActiveTabs() {
  return <TabLinks pathname={usePathname()} />;
}

/** Phones: brand bar on top, tab bar at the bottom (the sidebar is md+ only). */
export function MobileNav() {
  return (
    <>
      <header className="sticky top-0 z-30 border-b bg-card/95 px-4 pt-[calc(env(safe-area-inset-top)+0.75rem)] pb-3 backdrop-blur md:hidden">
        <Brand />
      </header>
      <div className="fixed inset-x-0 bottom-0 z-30 border-t bg-card/95 pb-[env(safe-area-inset-bottom)] backdrop-blur md:hidden">
        <Suspense fallback={<TabLinks pathname={null} />}>
          <ActiveTabs />
        </Suspense>
      </div>
    </>
  );
}

export function Sidebar() {
  return (
    <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col border-r bg-card px-4 py-6 md:flex">
      <div className="px-2 pb-6">
        <Brand />
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
