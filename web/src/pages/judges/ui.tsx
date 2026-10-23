import { useState, type ReactNode } from "react";
import { ArrowUpRight, Check, ChevronDown, CircleSlash, Copy, FlaskConical, Loader2, Lock, Play, ServerOff, ShieldAlert, Unplug } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useJudges, type DemoAction } from "./data";
import { unavailable, type Fetched } from "./judges-model";

export { Chapter, ChapterProvider, useChapters } from "./chapter";

const ACTION_LABEL: Record<DemoAction, string> = {
  day: "Run a simulated day",
  break: "Inject a bad deploy",
  outage: "Start a 45 s student outage",
};

/**
 * The "Try it" strip: buttons for the mock demo's own controls that move this chapter's numbers. Shown only on a mock
 * gateway with its controls on; the result line says what happened, in words.
 */
export function TryIt({ actions, note, extra }: { actions: DemoAction[]; note?: ReactNode; extra?: ReactNode }) {
  const { mock, busy, act, stats } = useJudges();
  const [line, setLine] = useState<{ ok: boolean; text: string } | null>(null);
  if (!stats) return null;
  if (!mock)
    return (
      <p className="mt-4 flex items-center gap-2 text-[13px] text-muted">
        <CircleSlash className="size-3.5" aria-hidden /> Demo controls are on the mock demo only.
      </p>
    );
  return (
    <div className="mt-5 rounded-xl border border-dashed border-line-strong bg-bg-sunken/60 p-3 sm:p-3.5">
      <div className="flex flex-wrap items-center gap-2">
        <span className="mr-1 inline-flex items-center gap-1.5 text-[12.5px] font-medium text-ink-2">
          <FlaskConical className="size-3.5 text-muted" aria-hidden /> Try it
          <span className="font-normal text-muted">(simulation, shared demo)</span>
        </span>
        {actions.map((a) => (
          <Button
            key={a}
            size="sm"
            variant={a === "day" ? "primary" : "outline"}
            disabled={busy != null}
            onClick={async () => {
              setLine(null);
              setLine(await act(a));
            }}
          >
            {busy === a ? <Loader2 className="animate-spin motion-reduce:animate-none" aria-hidden /> : <Play aria-hidden />}
            {ACTION_LABEL[a]}
          </Button>
        ))}
        {extra}
      </div>
      {note && <p className="mt-2 text-[12.5px] leading-relaxed text-muted">{note}</p>}
      <p className={cn("text-[13px]", line ? "mt-2" : "sr-only", line && !line.ok ? "text-bad" : "text-ink-2")} aria-live="polite">
        {busy ? "Running…" : (line?.text ?? "")}
      </p>
    </div>
  );
}

/** A failed read, said plainly: never a number. */
export function Unavailable({ path, read, className }: { path: string; read: Fetched<unknown>; className?: string }) {
  if (read.state !== "error") return null;
  const u = unavailable(path, read.status, read.message);
  const Icon = u.kind === "offline" ? Unplug : u.kind === "locked" ? Lock : u.kind === "missing" ? ServerOff : ShieldAlert;
  return (
    <p className={cn("flex items-start gap-2 rounded-xl border border-line bg-bg-sunken/60 px-3.5 py-3 text-[13.5px] leading-relaxed text-ink-2", className)}>
      <Icon className="mt-0.5 size-4 shrink-0 text-muted" aria-hidden />
      <span>{u.text}</span>
    </p>
  );
}

/** A small grey "loading" line. */
export function Loading({ what }: { what: string }) {
  return (
    <p className="flex items-center gap-2 text-[13px] text-muted" aria-busy="true">
      <Loader2 className="size-3.5 animate-spin motion-reduce:animate-none" aria-hidden /> Reading {what}…
    </p>
  );
}

/** Label/value pairs in a responsive grid; values in tabular figures. */
export function Facts({ items, className }: { items: { label: ReactNode; value: ReactNode; hint?: ReactNode }[]; className?: string }) {
  return (
    <dl className={cn("grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-line bg-line sm:grid-cols-3 lg:grid-cols-4", className)}>
      {items.map((it, i) => (
        <div key={i} className="min-w-0 bg-surface px-3.5 py-3">
          <dt className="text-[12px] leading-snug text-muted">{it.label}</dt>
          <dd className="mt-1 text-[17px] font-semibold tabular-nums tracking-[-0.01em] text-ink [overflow-wrap:anywhere]">{it.value}</dd>
          {it.hint && <dd className="mt-0.5 text-[12px] leading-snug text-muted">{it.hint}</dd>}
        </div>
      ))}
    </dl>
  );
}

/** A copyable one-line command (or a few lines) with its own copy button. */
export function Command({ cmd, label, className, wrap }: { cmd: string; label?: string; className?: string; wrap?: boolean }) {
  const [done, setDone] = useState(false);
  return (
    <div className={cn("group flex min-w-0 items-stretch overflow-hidden rounded-xl border border-line bg-bg-sunken", className)}>
      <pre
        className={cn(
          "min-w-0 flex-1 px-3.5 py-2.5 font-mono text-[12.5px] leading-6 text-ink-2",
          wrap ? "whitespace-pre-wrap text-[12px] leading-5 [overflow-wrap:anywhere]" : "overflow-x-auto",
        )}
      >
        <code>{cmd}</code>
      </pre>
      <button
        type="button"
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(cmd);
            setDone(true);
            window.setTimeout(() => setDone(false), 1600);
          } catch {
            /* clipboard blocked */
          }
        }}
        className="inline-flex shrink-0 items-center gap-1.5 border-l border-line px-3 text-[12px] text-muted transition-colors hover:bg-surface-2 hover:text-ink"
        aria-label={label ? `Copy: ${label}` : "Copy the command"}
      >
        {done ? <Check className="size-3.5 text-good" aria-hidden /> : <Copy className="size-3.5" aria-hidden />}
        <span className="hidden sm:inline">{done ? "Copied" : "Copy"}</span>
      </button>
    </div>
  );
}

/** An external link with the arrow the site uses. */
export function Ext({ href, children, className }: { href: string; children: ReactNode; className?: string }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className={cn("inline-flex items-baseline gap-0.5 font-medium text-ink underline decoration-line-strong underline-offset-[3px] transition-colors hover:decoration-ink", className)}
    >
      {children}
      <ArrowUpRight aria-hidden className="size-3 shrink-0 self-center text-muted" />
    </a>
  );
}

/** A panel inside a chapter. */
export function Panel({
  title,
  children,
  className,
  aside,
  flush,
}: {
  title?: ReactNode;
  children: ReactNode;
  className?: string;
  aside?: ReactNode;
  /** No padding around the body (a list that runs to the edges); the title keeps its own. */
  flush?: boolean;
}) {
  return (
    <div className={cn("min-w-0 rounded-2xl border border-line bg-surface shadow-card", flush ? "overflow-hidden" : "p-4 sm:p-5", className)}>
      {(title || aside) && (
        <div className={cn("mb-3 flex flex-wrap items-center justify-between gap-2", flush && "px-4 pt-4 sm:px-5")}>
          {title && <h3 className="text-[14px] font-semibold text-ink">{title}</h3>}
          {aside}
        </div>
      )}
      {children}
    </div>
  );
}

/** A horizontal meter, with its value in words beside it (never colour alone). */
export function Meter({ value, tone = "ink", label }: { value: number; tone?: "ink" | "owned" | "rented" | "warn" | "bad"; label: string }) {
  const bg = { ink: "bg-ink", owned: "bg-owned", rented: "bg-rented", warn: "bg-warn", bad: "bg-bad" }[tone];
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-line" role="img" aria-label={label}>
      <div className={cn("h-full rounded-full", bg)} style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }} />
    </div>
  );
}

/** A native disclosure for detail a reader may skip (a table, more charts): keyboard and screen readers work without script. */
export function Disclosure({ title, hint, children, className }: { title: ReactNode; hint?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <details className={cn("group/d rounded-2xl border border-line bg-surface shadow-card open:border-line-strong", className)}>
      <summary className="flex cursor-pointer list-none items-center gap-3 rounded-2xl px-4 py-3.5 text-left transition-colors hover:bg-surface-2/60 sm:px-5 [&::-webkit-details-marker]:hidden">
        <span className="min-w-0 flex-1">
          <span className="block text-[14.5px] font-semibold text-ink">{title}</span>
          {hint && <span className="mt-0.5 block text-[13px] text-muted">{hint}</span>}
        </span>
        <ChevronDown className="size-4 shrink-0 text-muted transition-transform duration-300 group-open/d:rotate-180 motion-reduce:transition-none" aria-hidden />
      </summary>
      <div className="border-t border-line p-4 sm:p-5">{children}</div>
    </details>
  );
}
