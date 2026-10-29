import { motion, useReducedMotion } from "motion/react";
import { ArrowUpRight, Check, ShieldCheck, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { RealBadge } from "@/components/ui/real-badge";
import { MEASURED } from "@/components/measured/measured";
import { EASE } from "@/lib/motion";
import { meter, nightRun, type NightStep } from "@/lib/night-run-model";
import { cn, REPO_URL } from "@/lib/utils";

/** Where the sanitised evidence of the run lives (`evidence/nightschool-nebius/`; `tests/test_nightschool_nebius_evidence.py` holds the numbers to it). */
export const NIGHT_NEBIUS_EVIDENCE = `${REPO_URL}/tree/main/evidence/nightschool-nebius`;

/** The line through the steps (wide screens): it draws in once as the card scrolls into view, green up to the gate and warn for the last stretch. */
function Track({ reduce }: { reduce: boolean }) {
  return (
    <div aria-hidden className="pointer-events-none absolute left-[12.5%] right-[12.5%] top-[20px] hidden h-[3px] rounded-full bg-line md:block">
      <motion.div
        className="absolute inset-0 rounded-full"
        style={{ background: "linear-gradient(to right, var(--good) 0 66.6%, var(--warn) 66.6% 100%)", transformOrigin: "left" }}
        initial={reduce ? false : { scaleX: 0 }}
        whileInView={{ scaleX: 1 }}
        viewport={{ once: true, margin: "-12% 0px" }}
        transition={{ duration: reduce ? 0 : 1.5, ease: EASE, delay: 0.15 }}
      />
    </div>
  );
}

/** The same line on a phone: one segment from each step down to the next, the last one in the warn colour. */
function Segment({ warn, i, reduce }: { warn: boolean; i: number; reduce: boolean }) {
  return (
    <span aria-hidden className="pointer-events-none absolute left-[20.5px] top-[22px] h-full w-[3px] rounded-full bg-line md:hidden">
      <motion.span
        className={cn("absolute inset-0 rounded-full", warn ? "bg-warn" : "bg-good")}
        style={{ transformOrigin: "top" }}
        initial={reduce ? false : { scaleY: 0 }}
        whileInView={{ scaleY: 1 }}
        viewport={{ once: true, margin: "-8% 0px" }}
        transition={{ duration: reduce ? 0 : 0.5, ease: EASE, delay: reduce ? 0 : 0.3 + i * 0.42 }}
      />
    </span>
  );
}

function Node({ step, i, last, reduce }: { step: NightStep; i: number; last: boolean; reduce: boolean }) {
  const stop = step.stops;
  return (
    <li className="relative grid min-w-0 grid-cols-[44px_minmax(0,1fr)] items-start gap-x-3.5 md:grid-cols-1 md:justify-items-center md:text-center">
      {!last && <Segment warn={i === 2} i={i} reduce={reduce} />}
      <motion.span
        className={cn(
          "relative z-10 inline-flex size-11 items-center justify-center rounded-full border-2 bg-surface",
          stop ? "border-warn text-warn-ink" : "border-good text-good-ink",
        )}
        initial={reduce ? false : { scale: 0.6, opacity: 0 }}
        whileInView={{ scale: 1, opacity: 1 }}
        viewport={{ once: true, margin: "-12% 0px" }}
        transition={{ duration: reduce ? 0 : 0.4, ease: EASE, delay: reduce ? 0 : 0.15 + i * 0.42 }}
      >
        <span aria-hidden className={cn("absolute inset-1 rounded-full", stop ? "bg-warn-soft" : "bg-good-soft")} />
        {stop ? <X className="relative size-5" strokeWidth={2.75} aria-hidden /> : <Check className="relative size-5" strokeWidth={2.75} aria-hidden />}
        <span className="sr-only">{stop ? "Stopped here" : "Done"}</span>
      </motion.span>
      <div className="min-w-0 pb-5 md:mt-3 md:pb-0">
        <p className="text-[11.5px] font-medium uppercase tracking-[0.08em] text-muted">{step.label}</p>
        <p className={cn("mt-0.5 text-[14.5px] font-semibold leading-snug tracking-[-0.01em]", stop ? "text-warn-ink" : "text-ink")}>{step.title}</p>
        <p className="mt-0.5 text-pretty text-[12.5px] leading-snug text-ink-2">{step.detail}</p>
      </div>
    </li>
  );
}

/**
 * "Measured on Nebius · 28 Oct 2026": the one end-to-end Night School run, stated with its verdict, as four steps and a precision
 * meter. It ran once, by hand; the golden gate rejected the student and it was never made live; the live site's own gateway is on
 * Cloudflare and is not connected to that endpoint; the loop has not run nightly on live traffic. No saving is claimed. Every number
 * is read from the measured snapshot (`night-run-model.ts`).
 */
export function MeasuredOnNebius({ className }: { className?: string }) {
  const reduce = useReducedMotion() ?? false;
  const run = nightRun(MEASURED);
  if (!run) return null;
  const m = meter(run);
  return (
    <aside
      aria-label="Night School measured on Nebius"
      data-companion-avoid
      className={cn("min-w-0 overflow-hidden rounded-3xl border border-line bg-surface shadow-card", className)}
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-line px-4 py-3.5 sm:px-6">
        <RealBadge status="real" />
        <p className="text-[13.5px] font-semibold text-ink">Measured on Nebius · {run.date}</p>
        <Badge tone="warn" className="ml-auto gap-1">
          <X aria-hidden /> Rejected by the gate
        </Badge>
      </div>

      <div className="px-4 pb-5 pt-6 sm:px-6">
        <ol className="relative grid min-w-0 gap-y-0 md:grid-cols-4 md:gap-x-4" aria-label="The run, step by step">
          <Track reduce={reduce} />
          {run.steps.map((s, i) => (
            <Node key={s.id} step={s} i={i} last={i === run.steps.length - 1} reduce={reduce} />
          ))}
        </ol>

        <div className="mt-6 rounded-2xl border border-line bg-bg-sunken/60 p-4 sm:p-5">
          <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
            <p className="text-[13px] text-ink-2">Precision at the gate's threshold</p>
            <p className="text-[12.5px] text-muted tabular-nums">
              {run.right} of {run.answered} right · coverage {run.coverage.replace(/\s*\(.*$/, "")}
            </p>
          </div>
          <div className="mt-1 flex items-baseline gap-2">
            <span className="text-[30px] font-semibold leading-none tracking-[-0.03em] text-ink tabular-nums">{run.precisionText}</span>
            <span className="text-[13px] text-muted">of its answers were right</span>
          </div>
          <div className="relative mt-6 h-3.5 rounded-full border border-line bg-surface-2" role="img" aria-label={`Precision ${run.precisionText}, target ${run.targetText}`}>
            <motion.div
              className="absolute inset-y-0 left-0 rounded-full bg-warn"
              style={{ width: `${m.fill}%`, transformOrigin: "left" }}
              initial={reduce ? false : { scaleX: 0 }}
              whileInView={{ scaleX: 1 }}
              viewport={{ once: true, margin: "-10% 0px" }}
              transition={{ duration: reduce ? 0 : 1.1, ease: EASE, delay: reduce ? 0 : 1.6 }}
            />
            <motion.div
              aria-hidden
              className="absolute inset-y-0 rounded-r-full border-y border-dashed border-warn/60 bg-warn-soft"
              style={{ left: `${m.gapFrom}%`, width: `${m.gapWidth}%` }}
              initial={reduce ? false : { opacity: 0 }}
              whileInView={{ opacity: 1 }}
              viewport={{ once: true, margin: "-10% 0px" }}
              transition={{ duration: reduce ? 0 : 0.5, delay: reduce ? 0 : 2.6 }}
            />
            <span aria-hidden className="absolute -inset-y-1.5 w-0.5 rounded-full bg-ink" style={{ left: `calc(${m.marker}% - 1px)` }} />
            <span
              aria-hidden
              className="absolute -top-6 -translate-x-full whitespace-nowrap pr-1.5 text-[11.5px] font-medium text-ink"
              style={{ left: `${m.marker}%` }}
            >
              target {run.targetText}
            </span>
          </div>
        </div>

        <p className="mt-5 flex items-start gap-2 text-[14px] font-semibold leading-snug text-ink">
          <ShieldCheck className="mt-0.5 size-[18px] shrink-0 text-good-ink" aria-hidden />
          The gate did its job: the new student was never made live.
        </p>
        <p className="mt-2 text-pretty text-[12.5px] leading-relaxed text-muted">
          One run, started by hand: {run.rows.imported} rows imported, {run.rows.training} used for training and {run.rows.golden} held out as the golden set. The live
          site is not connected to that endpoint, and the loop has not run nightly on live traffic. No saving is claimed.{" "}
          <a
            href={NIGHT_NEBIUS_EVIDENCE}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-0.5 font-medium text-ink underline underline-offset-2 hover:text-ink-2"
          >
            Read the evidence <ArrowUpRight className="size-3.5 text-muted" aria-hidden />
          </a>
        </p>
      </div>
    </aside>
  );
}
