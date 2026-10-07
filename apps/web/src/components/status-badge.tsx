import { cn } from "@/lib/utils";
import type { JobStatus } from "@/lib/types";

export type Tone = "success" | "warning" | "danger" | "info" | "neutral";

const TONES: Record<Tone, string> = {
  success: "bg-success-soft text-success",
  warning: "bg-warning-soft text-warning",
  danger: "bg-danger-soft text-destructive",
  info: "bg-info-soft text-info",
  neutral: "bg-muted text-muted-foreground",
};

/** Mirrors the Figma "Badge" component (Tone variants, dot + label). */
export function StatusBadge({ tone, children, className }: { tone: Tone; children: React.ReactNode; className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex h-6 items-center gap-1.5 rounded-full px-2.5 text-xs font-medium whitespace-nowrap",
        TONES[tone],
        className,
      )}
    >
      <span className="size-1.5 rounded-full bg-current" />
      {children}
    </span>
  );
}

const JOB_STATUS: Record<JobStatus, [Tone, string]> = {
  QUEUED: ["neutral", "Queued"],
  RESEARCHING: ["info", "Researching"],
  VERIFYING: ["info", "Verifying"],
  WRITING: ["info", "Writing"],
  RENDERING: ["info", "Rendering"],
  QA: ["info", "QA"],
  REVIEWING: ["info", "Reviewing"],
  READY_FOR_REVIEW: ["info", "Ready for review"],
  REJECTED: ["danger", "Rejected"],
  PUBLISHING: ["warning", "Publishing"],
  PUBLISHED: ["success", "Published"],
  FAILED: ["danger", "Failed"],
};

export function JobStatusBadge({ status, mock = false }: { status: JobStatus; mock?: boolean }) {
  if (status === "PUBLISHED" && mock) return <StatusBadge tone="neutral">Published (mock)</StatusBadge>;
  const [tone, label] = JOB_STATUS[status];
  return <StatusBadge tone={tone}>{label}</StatusBadge>;
}
