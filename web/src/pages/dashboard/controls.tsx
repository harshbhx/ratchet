import { useId, useState, type ReactNode } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Bug, CheckCircle2, Info, Loader2, Play, RotateCcw, TriangleAlert, X, XCircle } from "lucide-react";
import type { Workload } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export type Busy = "day" | "break" | "reset" | null;

export interface Notice {
  id: number;
  tone: "good" | "warn" | "bad" | "info";
  title: ReactNode;
  body?: ReactNode;
}

export const WORKLOADS: { id: Workload; label: string; short: string; hint: string }[] = [
  { id: "mixed", label: "Tickets + Scout", short: "Mixed", hint: "Half support tickets, half Scout research calls" },
  { id: "tickets", label: "Support tickets", short: "Tickets", hint: "Support tickets only (ticket-triage)" },
  { id: "research", label: "Scout research", short: "Scout", hint: "Scout's triage and reader calls" },
];

function Segmented({ value, onChange, disabled }: { value: Workload; onChange: (w: Workload) => void; disabled?: boolean }) {
  const name = useId();
  return (
    <div role="radiogroup" aria-label="Simulated traffic for the next day" className="grid w-full grid-cols-3 rounded-full border border-line bg-bg-sunken p-0.5 sm:inline-grid sm:w-auto">
      {WORKLOADS.map((w) => {
        const on = w.id === value;
        return (
          <label
            key={w.id}
            title={w.hint}
            className={cn(
              "relative flex h-8 cursor-pointer items-center justify-center rounded-full px-2 text-center text-[12.5px] font-medium leading-tight transition-colors sm:px-3.5",
              on ? "text-ink" : "text-muted hover:text-ink",
              disabled && "cursor-not-allowed opacity-60",
            )}
          >
            <input type="radio" name={name} value={w.id} aria-label={w.label} checked={on} disabled={disabled} onChange={() => onChange(w.id)} className="peer sr-only" />
            {on && (
              <motion.span
                layoutId={`${name}-pill`}
                className="absolute inset-0 rounded-full border border-line bg-surface shadow-card"
                transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
              />
            )}
            <span className="relative z-[1] hidden whitespace-nowrap min-[380px]:inline" aria-hidden>
              {w.label}
            </span>
            <span className="relative z-[1] whitespace-nowrap min-[380px]:hidden" aria-hidden>
              {w.short}
            </span>
            <span className="pointer-events-none absolute inset-0 rounded-full peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-[var(--focus)]" />
          </label>
        );
      })}
    </div>
  );
}

const NOTICE_ICON = { good: CheckCircle2, warn: TriangleAlert, bad: XCircle, info: Info };
const NOTICE_TONE = {
  good: "border-good/30 bg-good-soft/60 [&_svg.lead]:text-good",
  warn: "border-warn/30 bg-warn-soft/70 [&_svg.lead]:text-warn",
  bad: "border-bad/30 bg-bad-soft/70 [&_svg.lead]:text-bad",
  info: "border-line bg-surface-2 [&_svg.lead]:text-muted",
};

export function DemoControls({
  workload,
  onWorkload,
  busy,
  onRunDay,
  onBreak,
  onReset,
  armed,
  armedPending,
  nextDay,
  notice,
  onDismiss,
}: {
  workload: Workload;
  onWorkload: (w: Workload) => void;
  busy: Busy;
  onRunDay: () => void;
  onBreak: () => void;
  onReset: () => void;
  armed: number | null;
  armedPending: boolean;
  nextDay: number;
  notice: Notice | null;
  onDismiss: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const any = busy != null;
  const Icon = notice ? NOTICE_ICON[notice.tone] : Info;

  return (
    <section aria-labelledby="demo-title" className="rounded-2xl border border-line bg-surface shadow-card">
      <div className="flex flex-col gap-3 p-3 sm:p-4 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-center sm:gap-3">
          <h2 id="demo-title" className="flex items-center gap-2 text-[13px] font-medium text-ink-2">
            <span className="rounded-md border border-line bg-surface-2 px-1.5 py-0.5 font-mono text-[11px] uppercase tracking-[0.1em] text-muted">Demo</span>
            Traffic
          </h2>
          <Segmented value={workload} onChange={onWorkload} disabled={any} />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {armed != null && (
            <span className="inline-flex h-8 items-center gap-1.5 rounded-full border border-warn/30 bg-warn-soft px-2.5 text-[12px] font-medium text-warn">
              <Bug className="size-3.5" aria-hidden /> <span className="text-ink-2">{armedPending ? "Fault armed for" : "Corrupted:"}</span> <span className="font-mono">v{armed}</span>
            </span>
          )}
          <AnimatePresence mode="wait" initial={false}>
            {confirming ? (
              <motion.div
                key="confirm"
                initial={{ opacity: 0, x: 8 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: 8 }}
                transition={{ duration: 0.2 }}
                className="flex w-full flex-wrap items-center gap-2 rounded-full sm:w-auto"
              >
                <span className="text-[13px] text-ink-2">Reset the shared demo for everyone?</span>
                <Button
                  size="sm"
                  variant="danger"
                  autoFocus
                  onClick={() => {
                    setConfirming(false);
                    onReset();
                  }}
                >
                  Yes, reset
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>
                  Cancel
                </Button>
              </motion.div>
            ) : (
              <motion.div
                key="actions"
                initial={{ opacity: 0, x: -8 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: -8 }}
                transition={{ duration: 0.2 }}
                className="grid w-full grid-cols-2 gap-2 sm:flex sm:w-auto sm:flex-wrap sm:items-center"
              >
                <Button size="sm" onClick={onRunDay} disabled={any} aria-busy={busy === "day"} className="col-span-2 h-9 sm:h-8">
                  {busy === "day" ? <Loader2 className="animate-spin" aria-hidden /> : <Play aria-hidden />}
                  {busy === "day" ? `Running day ${nextDay}…` : "Run 1 day + night"}
                </Button>
                <Button size="sm" variant="danger" onClick={onBreak} disabled={any || armedPending} aria-busy={busy === "break"} title={armedPending ? `v${armed} is already armed` : undefined}>
                  {busy === "break" ? <Loader2 className="animate-spin" aria-hidden /> : <Bug aria-hidden />}
                  Break next deploy
                </Button>
                <Button size="sm" variant="outline" onClick={() => setConfirming(true)} disabled={any} aria-busy={busy === "reset"}>
                  {busy === "reset" ? <Loader2 className="animate-spin" aria-hidden /> : <RotateCcw aria-hidden />}
                  Reset
                </Button>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>
      <div role="status" aria-live="polite">
        <AnimatePresence initial={false}>
          {notice && (
            <motion.div
              key={notice.id}
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              exit={{ opacity: 0, height: 0 }}
              transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
              className="overflow-hidden"
            >
              <div className="px-3 pb-3 sm:px-4 sm:pb-4">
                <div className={cn("flex items-start gap-2.5 rounded-xl border px-3 py-2.5", NOTICE_TONE[notice.tone])}>
                  <Icon className="lead mt-0.5 size-4 shrink-0" aria-hidden />
                  <div className="min-w-0 flex-1 text-[13px] leading-5">
                    <div className="font-medium text-ink">{notice.title}</div>
                    {notice.body && <div className="mt-0.5 text-ink-2">{notice.body}</div>}
                  </div>
                  <button type="button" onClick={onDismiss} aria-label="Dismiss message" className="-m-1 rounded-md p-1 text-muted hover:bg-surface hover:text-ink">
                    <X className="size-4" />
                  </button>
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </section>
  );
}
