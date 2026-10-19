import { useEffect, useState } from "react";
import { motion } from "motion/react";
import { CircleCheck, CircleSlash, LoaderCircle, Square, TriangleAlert } from "lucide-react";
import { cn, fmtMs } from "@/lib/utils";
import { Container } from "@/components/ui/section";
import { AnimatedNumber, usd } from "./bits";
import type { Derived, RunState } from "./state";

function useElapsed(run: RunState) {
  const [t, setT] = useState(0);
  useEffect(() => {
    if (run.status !== "running" || run.startedAt == null) return;
    let raf = 0;
    const tick = () => {
      setT(performance.now() - (run.startedAt ?? performance.now()));
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [run.status, run.startedAt]);
  if (run.done?.totals?.wall_ms) return run.done.totals.wall_ms;
  if (run.status !== "running" && run.startedAt != null && run.endedAt != null) return run.endedAt - run.startedAt;
  return t;
}

function Metric({ label, children, className }: { label: string; children: React.ReactNode; className?: string }) {
  return (
    <div className={cn("min-w-0", className)}>
      <div className="truncate text-[11.5px] text-muted sm:text-[12px]">
        <span className="sm:hidden">{label}</span>
        <span className="hidden sm:inline">{label === "Your model" ? "Answered by your model" : label}</span>
      </div>
      <div className="mt-0.5 truncate text-[17px] font-semibold leading-tight tracking-[-0.02em] text-ink tabular sm:text-[20px]">{children}</div>
    </div>
  );
}

/** Sticky run totals: model calls, share answered by the owned model, cost vs all-Ultra, % saved. */
export function TotalsBar({ run, derived, onStop }: { run: RunState; derived: Derived; onStop: () => void }) {
  const stopBtn = run.status === "running" && (
    <button
      type="button"
      onClick={onStop}
      className="inline-flex h-7 items-center gap-1.5 rounded-full border border-line-strong bg-surface px-2.5 text-[12px] font-medium text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink"
    >
      <Square className="size-2.5 fill-current" aria-hidden /> Stop run
    </button>
  );
  const t = derived.totals;
  const elapsed = useElapsed(run);
  const sShare = t.llm ? t.student / t.llm : 0;
  const tShare = t.llm ? t.teacher / t.llm : 0;
  const status =
    run.status === "running" ? (
      <span className="inline-flex items-center gap-1.5 text-ink-2">
        <LoaderCircle className="size-3.5 animate-spin motion-reduce:animate-none" aria-hidden /> Running
      </span>
    ) : run.status === "done" ? (
      <span className="inline-flex items-center gap-1.5 text-good">
        <CircleCheck className="size-3.5" aria-hidden /> Done
      </span>
    ) : run.status === "error" ? (
      <span className="inline-flex items-center gap-1.5 text-bad">
        <TriangleAlert className="size-3.5" aria-hidden /> Stopped
      </span>
    ) : (
      <span className="inline-flex items-center gap-1.5 text-muted">
        <CircleSlash className="size-3.5" aria-hidden /> Stopped
      </span>
    );

  return (
    <div className="sticky top-16 z-30 py-2">
      <Container>
        <section aria-label="Run totals" className="rounded-2xl border border-line bg-surface px-3.5 py-2.5 shadow-card sm:py-3 sm:px-5">
          {/* Phones: status + cost on one line, three metrics below. */}
          <div className="mb-1.5 flex items-center justify-between gap-3 text-[12px] font-medium sm:hidden">
            <span className="inline-flex items-center gap-2">
              {status}
              <span className="font-mono text-[11.5px] text-muted tabular">{fmtMs(elapsed)}</span>
              {stopBtn}
            </span>
            <span className="truncate text-muted tabular">
              <AnimatedNumber value={t.cost} format={usd} className="font-semibold text-ink" /> vs <AnimatedNumber value={t.baseline} format={usd} />
            </span>
          </div>
          <div className="grid grid-cols-3 gap-x-3 sm:grid-cols-[auto_repeat(4,minmax(0,1fr))] sm:items-end sm:gap-x-6">
            <div className="hidden min-w-[88px] flex-col items-start justify-end gap-1 pr-2 text-[12.5px] font-medium sm:flex">
              <span className="inline-flex items-center gap-2 whitespace-nowrap">
                {status}
                <span className="font-mono text-[12px] text-muted tabular">{fmtMs(elapsed)}</span>
              </span>
              {stopBtn}
            </div>
            <Metric label="Model calls">
              <AnimatedNumber value={t.llm} format={(v) => String(Math.round(v))} />
            </Metric>
            <Metric label="Your model">
              <span className="text-owned-ink">
                <AnimatedNumber value={t.student} format={(v) => String(Math.round(v))} />
              </span>
              <span className="ml-1.5 text-[13px] font-medium text-muted sm:text-[14px]">
                <AnimatedNumber value={sShare} format={(v) => `${Math.round(v * 100)}%`} />
              </span>
            </Metric>
            <Metric label="Cost vs all-Ultra" className="hidden sm:block">
              <AnimatedNumber value={t.cost} format={usd} />
              <span className="ml-1.5 text-[12px] font-medium text-muted sm:text-[13px]">
                vs <AnimatedNumber value={t.baseline} format={usd} />
              </span>
            </Metric>
            <Metric label="Saved">
              <AnimatedNumber value={t.saved} format={(v) => `${Math.round(v * 100)}%`} />
            </Metric>
          </div>
          <div className="mt-2.5 flex h-1.5 w-full overflow-hidden rounded-full bg-line" aria-hidden>
            <motion.span
              className="h-full bg-owned"
              initial={false}
              animate={{ width: `${sShare * 100}%` }}
              transition={{ duration: 0.4, ease: [0.22, 1, 0.36, 1] }}
            />
            <motion.span
              className="h-full bg-rented"
              initial={false}
              animate={{ width: `${tShare * 100}%` }}
              transition={{ duration: 0.4, ease: [0.22, 1, 0.36, 1] }}
            />
          </div>
          <p className="sr-only">
            {t.student} of {t.llm} model calls answered by your model, {t.teacher} by Ultra. Cost {usd(t.cost)} against {usd(t.baseline)} on Ultra
            alone.
          </p>
        </section>
      </Container>
    </div>
  );
}
