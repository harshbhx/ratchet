import type { ReactNode } from "react";
import {
  AlertTriangle,
  Archive,
  BarChart3,
  Bug,
  CheckCircle2,
  Circle,
  CircleDashed,
  Eye,
  FlaskConical,
  Gauge,
  ServerCrash,
  SkipForward,
  Table2,
  Timer,
  Undo2,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import type { VersionState } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { EVENT_LABEL, EVENT_TONE, STATE_TONE, stateLabel, type Tone } from "./model";

export const EVENT_ICON: Record<string, LucideIcon> = {
  trained: FlaskConical,
  skip: SkipForward,
  shadow: Eye,
  canary: Gauge,
  live: CheckCircle2,
  rejected: XCircle,
  rollback: Undo2,
  alert: AlertTriangle,
  retired: Archive,
  student_error: ServerCrash,
  fault: Bug,
  warmup: Timer,
};

export const STATE_ICON: Record<VersionState, LucideIcon> = {
  live: CheckCircle2,
  canary: Gauge,
  shadow: Eye,
  candidate: CircleDashed,
  retired: Archive,
  rejected: XCircle,
  rolled_back: Undo2,
};

/** Text + icon tone classes for small inline icons (status meaning always comes with a word). */
export const TONE_TEXT: Record<Tone, string> = {
  neutral: "text-ink-2",
  owned: "text-owned-ink",
  rented: "text-rented-ink",
  good: "text-good",
  warn: "text-warn",
  bad: "text-bad",
  outline: "text-muted",
};

export const TONE_DOT: Record<Tone, string> = {
  neutral: "bg-surface-2 border-line-strong text-ink-2",
  owned: "bg-owned-soft border-owned/30 text-owned-ink",
  rented: "bg-rented-soft border-rented/30 text-rented-ink",
  good: "bg-good-soft border-good/30 text-good",
  warn: "bg-warn-soft border-warn/30 text-warn",
  bad: "bg-bad-soft border-bad/30 text-bad",
  outline: "bg-surface border-line-strong text-muted",
};

export function StateChip({ state, traffic, className }: { state: VersionState; traffic: number; className?: string }) {
  const Icon = STATE_ICON[state] ?? Circle;
  return (
    <Badge tone={STATE_TONE[state] ?? "neutral"} className={className}>
      <Icon aria-hidden /> {stateLabel({ state, traffic_pct: traffic })}
    </Badge>
  );
}

export function KindChip({ kind, className }: { kind: string; className?: string }) {
  const Icon = EVENT_ICON[kind] ?? Circle;
  return (
    <Badge tone={EVENT_TONE[kind] ?? "neutral"} className={className}>
      <Icon aria-hidden /> {EVENT_LABEL[kind] ?? kind}
    </Badge>
  );
}

/** A dashboard panel: title row (title, optional description, actions) and a body. */
export function Panel({
  title,
  description,
  actions,
  children,
  className,
  bodyClassName,
  id,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  id?: string;
}) {
  const headingId = id ? `${id}-title` : undefined;
  return (
    <section
      id={id}
      aria-labelledby={headingId}
      className={cn("flex min-w-0 flex-col rounded-2xl border border-line bg-surface shadow-card", className)}
    >
      <header className="flex items-start justify-between gap-x-4 px-4 pt-4 sm:px-5 sm:pt-5">
        <div className="min-w-0">
          <h2 id={headingId} className="text-[15px] font-semibold tracking-[-0.01em] text-ink">
            {title}
          </h2>
          {description && <p className="mt-0.5 text-pretty text-[13px] leading-5 text-muted">{description}</p>}
        </div>
        {actions && <div className="flex shrink-0 items-center gap-1.5">{actions}</div>}
      </header>
      <div className={cn("min-w-0 flex-1 px-4 pb-4 pt-3 sm:px-5 sm:pb-5", bodyClassName)}>{children}</div>
    </section>
  );
}

/** Chart / table switch. One button that says what it will show next. */
export function ViewToggle({ table, onChange, label }: { table: boolean; onChange: (t: boolean) => void; label: string }) {
  return (
    <button
      type="button"
      onClick={() => onChange(!table)}
      aria-pressed={table}
      aria-label={table ? `Show ${label} as chart` : `View ${label} as table`}
      className="inline-flex h-7 items-center gap-1.5 rounded-full border border-line px-2.5 text-[12px] font-medium text-ink-2 transition-colors hover:border-line-strong hover:bg-surface-2 hover:text-ink"
    >
      {table ? <BarChart3 className="size-3.5" aria-hidden /> : <Table2 className="size-3.5" aria-hidden />}
      {table ? "Chart" : "Table"}
    </button>
  );
}

export function Empty({ icon: Icon, title, children, action, className }: { icon: LucideIcon; title: string; children?: ReactNode; action?: ReactNode; className?: string }) {
  return (
    <div className={cn("flex flex-col items-center justify-center rounded-xl border border-dashed border-line-strong bg-bg-sunken/60 px-6 py-8 text-center", className)}>
      <span className="grid size-9 place-items-center rounded-full border border-line bg-surface text-muted">
        <Icon className="size-4" aria-hidden />
      </span>
      <p className="mt-3 text-[14px] font-medium text-ink">{title}</p>
      {children && <p className="mt-1 max-w-sm text-pretty text-[13px] leading-5 text-muted">{children}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

/** Compact data table used by the chart table views and the panels. Scrolls inside its card. */
export function DataTable({ children, minWidth = 0, className, caption }: { children: ReactNode; minWidth?: number; className?: string; caption?: string }) {
  return (
    <div className={cn("-mx-4 overflow-x-auto sm:-mx-5", className)}>
      <table className="w-full border-collapse text-[13px] tabular" style={minWidth ? { minWidth } : undefined}>
        {caption && <caption className="sr-only">{caption}</caption>}
        {children}
      </table>
    </div>
  );
}

export const th = "whitespace-nowrap border-b border-line px-3 py-2 text-left text-[12px] font-medium text-muted first:pl-4 last:pr-4 sm:first:pl-5 sm:last:pr-5";
export const thNum = th + " text-right";
export const td = "whitespace-nowrap border-b border-line px-3 py-2.5 text-ink-2 first:pl-4 last:pr-4 sm:first:pl-5 sm:last:pr-5";
export const tdNum = td + " text-right";

/** Legend key that mirrors its mark: a short line for lines, a square for bars. */
export function LegendKey({ color, kind = "line", dashed }: { color: string; kind?: "line" | "rect"; dashed?: boolean }) {
  if (kind === "rect") return <span aria-hidden className="inline-block size-2.5 rounded-[3px]" style={{ background: color }} />;
  return (
    <svg aria-hidden width="18" height="8" className="shrink-0">
      <line x1="1" x2="17" y1="4" y2="4" stroke={color} strokeWidth="2" strokeLinecap="round" strokeDasharray={dashed ? "4 3" : undefined} />
    </svg>
  );
}

/** Highlight version names (v3) inside free-text event details. */
export function DetailText({ text }: { text: string }) {
  const parts = text.split(/(\bv\d+\b)/g);
  return (
    <>
      {parts.map((p, i) =>
        /^v\d+$/.test(p) ? (
          <span key={i} className="font-mono text-[12.5px] font-semibold text-ink">
            {p}
          </span>
        ) : (
          <span key={i}>{p}</span>
        ),
      )}
    </>
  );
}
