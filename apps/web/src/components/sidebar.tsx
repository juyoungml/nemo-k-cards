"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Suspense } from "react";
import { AtSign, ChevronsUpDown, FileCheck2, LayoutDashboard, LogOut, RotateCcw, ScrollText, ShieldAlert, Sparkles, Users } from "lucide-react";

import { IS_MOCK, api } from "@/lib/api";
import { setActiveOrgId } from "@/lib/org";
import { ROLE_LABEL, atLeast, useMe } from "@/lib/roles";
import { reloadTo, signOut } from "@/lib/supabase";
import type { Role } from "@/lib/types";

import { cn } from "@/lib/utils";

type NavItem = { href: string; label: string; icon: typeof LayoutDashboard; min?: Role; desktopOnly?: boolean };
// `min`: hidden below that Role (the API refuses it anyway). Team and Audit log live in the sidebar; phones reach
// Team from the top bar so the tab bar keeps four tabs.
const NAV: NavItem[] = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/jobs/new", label: "New deck", icon: Sparkles, min: "editor" },
  { href: "/review", label: "Review", icon: FileCheck2 },
  { href: "/policy", label: "Policy Log", icon: ShieldAlert },
  { href: "/team", label: "Team", icon: Users, desktopOnly: true },
  { href: "/instagram", label: "Instagram", icon: AtSign, desktopOnly: true },
  { href: "/audit", label: "Audit log", icon: ScrollText, min: "admin", desktopOnly: true },
];

function useNav(desktop: boolean) {
  const { role } = useMe();
  return NAV.filter((n) => (desktop || !n.desktopOnly) && (!n.min || atLeast(role, n.min)));
}

function NavLinks({ pathname }: { pathname: string | null }) {
  const isActive = (href: string) => pathname !== null && (href === "/" ? pathname === "/" : pathname.startsWith(href));
  return (
    <nav className="flex flex-col gap-1">
      {useNav(true).map(({ href, label, icon: Icon }) => (
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

export function Brand() {
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
  const tabs = useNav(false);
  return (
    <nav className={cn("grid", tabs.length === 4 ? "grid-cols-4" : "grid-cols-3")}>
      {tabs.map(({ href, label, icon: Icon }) => (
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
      <header className="admin-chrome sticky top-0 z-30 border-b bg-card/95 px-4 pt-[calc(env(safe-area-inset-top)+0.75rem)] pb-3 backdrop-blur md:hidden">
        <div className="flex items-center justify-between gap-3">
          <Brand />
          <div className="flex items-center gap-1">
            <Link
              href="/team"
              aria-label="Team"
              title="Team"
              className="flex size-8 shrink-0 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              <Users className="size-4" />
            </Link>
            {!IS_MOCK && <SignOutButton />}
          </div>
        </div>
      </header>
      <div className="admin-chrome fixed inset-x-0 bottom-0 z-30 border-t bg-card/95 pb-[env(safe-area-inset-bottom)] backdrop-blur md:hidden">
        <Suspense fallback={<TabLinks pathname={null} />}>
          <ActiveTabs />
        </Suspense>
      </div>
    </>
  );
}

function SignOutButton() {
  return (
    <button
      type="button"
      onClick={signOut}
      aria-label="Sign out"
      title="Sign out"
      className="flex size-8 shrink-0 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
    >
      <LogOut className="size-4" />
    </button>
  );
}

/** People in several Organizations pick the one they work in; the choice goes to the API as X-Org-Id. */
function OrgSwitcher() {
  const { me } = useMe();
  if (!me || me.orgs.length < 2) return null;
  return (
    <label className="relative mb-3 block">
      <span className="sr-only">Organization</span>
      <select
        value={me.org?.id ?? ""}
        onChange={(e) => {
          setActiveOrgId(e.target.value);
          reloadTo("/"); // start over: every screen reloads its data for the new Organization
        }}
        className="h-10 w-full appearance-none rounded-lg border bg-card pr-8 pl-3 text-[13px] font-semibold outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
      >
        {me.orgs.map((o) => (
          <option key={o.id} value={o.id}>
            {o.name} · {ROLE_LABEL[o.role]}
          </option>
        ))}
      </select>
      <ChevronsUpDown className="pointer-events-none absolute top-3 right-2.5 size-4 text-muted-foreground" />
    </label>
  );
}

/** Who is signed in, for which Organization and Role, and the way out. */
function Account() {
  const { me } = useMe();
  if (IS_MOCK) {
    return (
      <>
        <div className="size-7 rounded-full bg-muted" />
        <span className="text-[13px] font-medium text-muted-foreground">Content Manager</span>
      </>
    );
  }
  const initial = (me?.user.email || "?").charAt(0).toUpperCase();
  return (
    <>
      <div className="flex size-7 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-bold text-muted-foreground">
        {initial}
      </div>
      <div className="min-w-0 flex-1 leading-tight">
        <p className="truncate text-[13px] font-semibold">{me?.org?.name ?? " "}</p>
        <p className="truncate text-[11px] text-muted-foreground" title={me?.user.email}>
          {me?.org ? `${ROLE_LABEL[me.org.role]} · ` : ""}
          {me?.user.email ?? " "}
        </p>
      </div>
      <SignOutButton />
    </>
  );
}

export function Sidebar() {
  return (
    <aside className="admin-chrome sticky top-0 hidden h-screen w-60 shrink-0 flex-col border-r bg-card px-4 py-6 md:flex">
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
      <div className={cn(IS_MOCK ? "mt-4" : "mt-auto")}>
        <OrgSwitcher />
        <div className="flex items-center gap-2.5 px-2">
          <Account />
        </div>
      </div>
    </aside>
  );
}
