import { UNREACHABLE } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { Metric } from "@/lib/types";

export function PageHeader({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children?: React.ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-center gap-x-4 gap-y-2">
      <div className="min-w-0 flex-1 space-y-1">
        <h1 className="text-xl font-bold tracking-tight break-words md:text-2xl">{title}</h1>
        {description && <p className="text-sm text-muted-foreground">{description}</p>}
      </div>
      {children}
    </header>
  );
}

/** Mirrors the Figma "StatCard" component. */
export function StatCard({ label, value, delta }: Metric) {
  return (
    <div className="min-w-0 flex-1 space-y-1.5 rounded-xl border bg-card p-4 md:p-5">
      <p className="text-[13px] font-medium text-muted-foreground">{label}</p>
      <p className="text-[22px] leading-none font-bold md:text-[28px]">{value}</p>
      <p className="text-xs font-medium text-success">{delta}</p>
    </div>
  );
}

export function StatRow({ metrics }: { metrics: Metric[] }) {
  return (
    <div className="grid grid-cols-2 gap-3 md:flex md:gap-4">
      {metrics.map((m) => (
        <StatCard key={m.label} {...m} />
      ))}
    </div>
  );
}

export function Panel({ title, description, action, className, children }: {
  title?: string;
  description?: string;
  action?: React.ReactNode;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <section className={cn("min-w-0 rounded-xl border bg-card p-4 md:p-5", className)}>
      {(title || action) && (
        <div className="mb-3 flex items-start gap-2">
          <div className="flex-1">
            {title && <h2 className="text-base font-semibold">{title}</h2>}
            {description && <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>}
          </div>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

export function LoadState({ error, label = "Loading…" }: { error?: Error; label?: string }) {
  return (
    <div className={cn("rounded-xl border bg-card p-5 text-sm", error ? "border-destructive/30 bg-danger-soft text-destructive" : "animate-pulse text-muted-foreground")}>
      {error ? (error.message.startsWith(UNREACHABLE) ? error.message : `Couldn't load — ${error.message}`) : label}
    </div>
  );
}
