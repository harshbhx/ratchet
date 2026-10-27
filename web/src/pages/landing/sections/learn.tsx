import { Suspense, lazy, useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion, useInView, useReducedMotion } from "motion/react";
import { Activity, CheckCircle2, Pause, Play, Snowflake, Sprout, TrendingUp, Undo2, type LucideIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Container } from "@/components/ui/section";
import { CountUp } from "@/components/ui/stat";
import { useInterval } from "@/lib/hooks";
import { SIM_DAYS, SIM_SOURCE, SIM_TOTAL, type SimDay } from "@/lib/sim";
import { cn, fmtInt, fmtPct, fmtUsd } from "@/lib/utils";
import { Chapter, ChapterHead, EASE, Lead, Reveal, SimBadge } from "../shared";
import { RealCurve } from "./real-curve";

const LAST = SIM_DAYS.length;
// recharts is the largest library on the page; the chart is far below the fold, so it loads after the hero paints.
const CostChart = lazy(() => import("./cost-chart"));

const NOTE_STYLE: Record<number, { icon: LucideIcon; tone: string }> = {
  1: { icon: Snowflake, tone: "text-muted" },
  2: { icon: Sprout, tone: "text-owned-ink" },
  3: { icon: CheckCircle2, tone: "text-good" },
  4: { icon: Undo2, tone: "text-bad" },
  5: { icon: TrendingUp, tone: "text-warn" },
  8: { icon: CheckCircle2, tone: "text-good" },
};

function noteFor(d: SimDay) {
  if (d.note) return d.note;
  return `${fmtPct(d.student)} answered by your adapter, ${fmtPct(d.escalated)} sent to the teacher${d.audit != null ? `, ${fmtPct(d.audit)} of audits agreed` : ""}.`;
}

export function Learn() {
  const [day, setDay] = useState(1);
  const [playing, setPlaying] = useState(false);
  const d = SIM_DAYS[day - 1];
  const tick = useCallback(() => {
    setDay((cur) => {
      if (cur >= LAST) {
        setPlaying(false);
        return cur;
      }
      if (cur + 1 >= LAST) setPlaying(false);
      return cur + 1;
    });
  }, []);
  useInterval(tick, playing ? 1150 : null);

  // "Watch it learn" plays the ten days once, the first time the card is on screen, so the payoff is visible without a
  // click. With reduced motion it shows the last day instead. Any choice the reader makes wins over the autoplay.
  const reduce = useReducedMotion();
  const cardRef = useRef<HTMLDivElement | null>(null);
  const seen = useInView(cardRef, { once: true, amount: 0.35 });
  const touched = useRef(false);
  useEffect(() => {
    if (reduce && !touched.current) setDay(LAST);
  }, [reduce]);
  useEffect(() => {
    if (!seen || reduce || touched.current) return;
    touched.current = true;
    setDay(1);
    setPlaying(true);
  }, [seen, reduce]);

  const onPlay = () => {
    touched.current = true;
    if (playing) return setPlaying(false);
    if (day >= LAST) setDay(1);
    setPlaying(true);
  };
  const pick = (n: number) => {
    touched.current = true;
    setPlaying(false);
    setDay(n);
  };
  const style = NOTE_STYLE[day] ?? { icon: Activity, tone: "text-muted" };
  const NoteIcon = style.icon;

  return (
    <Chapter id="learn" className="border-t border-line bg-bg-sunken/60">
      {/* the day, written large behind the chapter, so the scrubber's position is visible from across the room */}
      <div aria-hidden className="pointer-events-none absolute right-[2%] top-4 select-none overflow-hidden lg:top-0">
        <AnimatePresence mode="popLayout" initial={false}>
          <motion.span
            key={day}
            initial={{ opacity: 0, y: 24 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -24 }}
            transition={{ duration: 0.45, ease: EASE }}
            className="block font-mono text-[11rem] font-semibold leading-none tracking-[-0.08em] text-ink/[0.045] tabular sm:text-[17rem] lg:text-[24rem] dark:text-ink/[0.04]"
          >
            {String(day).padStart(2, "0")}
          </motion.span>
        </AnimatePresence>
      </div>
      <Container className="relative">
        <ChapterHead
          id="learn"
          title="Watch it learn."
          lead={
            <Lead>
              The real curve first: the same small model, untrained, after one round of teacher labels and after two, on two exams it never
              saw. Then, labelled Simulation, ten days of traffic with the parts no real run has measured yet: a bad release caught, a product
              launch, a student that learns the new questions overnight.
            </Lead>
          }
        />

        <Reveal className="mt-12">
          <RealCurve />
        </Reveal>

        <Reveal className="mt-14">
          <div className="mb-4 flex flex-wrap items-center gap-x-3 gap-y-2">
            <SimBadge>Simulation · reference run</SimBadge>
            <h3 className="text-[15px] font-semibold tracking-[-0.01em] text-ink">Ten days of traffic, with a bad release and a launch</h3>
          </div>
          <div ref={cardRef} className="overflow-hidden rounded-[24px] border border-line bg-surface shadow-card">
            {/* scrubber */}
            <div className="flex flex-wrap items-center gap-x-4 gap-y-3 border-b border-line px-4 py-3.5 sm:flex-nowrap sm:px-6">
              <Button variant="outline" size="sm" onClick={onPlay} aria-label={playing ? "Pause" : "Play through the days"} className="shrink-0">
                {playing ? <Pause /> : <Play />} {playing ? "Pause" : day >= LAST ? "Replay" : "Play"}
              </Button>
              <div className="order-last w-full min-w-0 sm:order-none sm:flex-1">
                <label htmlFor="sim-day" className="sr-only">
                  Simulation day
                </label>
                <input
                  id="sim-day"
                  type="range"
                  min={1}
                  max={LAST}
                  step={1}
                  value={day}
                  onChange={(e) => pick(Number(e.target.value))}
                  aria-valuetext={`Day ${day} of ${LAST}`}
                  className="block h-6 w-full cursor-pointer accent-owned"
                />
                <div className="mt-0.5 flex justify-between px-[7px] font-mono text-[10.5px] text-muted" aria-hidden>
                  {SIM_DAYS.map((x) => (
                    <button
                      key={x.day}
                      type="button"
                      tabIndex={-1}
                      onClick={() => pick(x.day)}
                      className={cn("w-3 text-center tabular transition-colors hover:text-ink", x.day === day && "font-semibold text-ink")}
                    >
                      {x.day}
                    </button>
                  ))}
                </div>
              </div>
              <div className="ml-auto flex shrink-0 items-center gap-2 sm:ml-0">
                <span className="font-mono text-[13px] text-ink tabular">
                  Day {String(day).padStart(2, "0")}
                  <span className="text-muted">/{LAST}</span>
                </span>
                <SimBadge />
              </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1.55fr)_minmax(0,1fr)]">
              <div className="border-b border-line p-4 sm:p-6 lg:border-b-0 lg:border-r">
                <div className="mb-2 flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
                  <p className="text-[13px] font-medium text-ink">Cost per 1,000 requests</p>
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-ink-2">
                    <span className="inline-flex items-center gap-2">
                      <svg width="20" height="6" aria-hidden>
                        <line x1="1" x2="19" y1="3" y2="3" stroke="var(--owned)" strokeWidth="2" strokeLinecap="round" />
                      </svg>
                      With Ratchet
                    </span>
                    <span className="inline-flex items-center gap-2">
                      <svg width="20" height="6" aria-hidden>
                        <line x1="1" x2="19" y1="3" y2="3" stroke="var(--rented)" strokeWidth="2" strokeDasharray="5 4" />
                      </svg>
                      All-Ultra baseline
                    </span>
                  </div>
                </div>
                <Suspense fallback={<div className="h-[260px] w-full sm:h-[320px]" aria-hidden />}>
                  <CostChart day={day} onPick={pick} />
                </Suspense>
                {/* A table grows to its content whatever its width, so the 1px sr-only box wraps it instead. */}
                <div className="sr-only">
                  <table>
                    <caption>Simulation: cost per 1,000 requests by day</caption>
                    <thead>
                      <tr>
                        <th>Day</th>
                        <th>Answered by your adapter</th>
                        <th>With Ratchet</th>
                        <th>All-Ultra</th>
                        <th>Saved</th>
                      </tr>
                    </thead>
                    <tbody>
                      {SIM_DAYS.map((x) => (
                        <tr key={x.day}>
                          <td>{x.day}</td>
                          <td>{fmtPct(x.student)}</td>
                          <td>{fmtUsd(x.costPer1k, 3)}</td>
                          <td>{fmtUsd(x.baselinePer1k, 3)}</td>
                          <td>{fmtPct(x.saved)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              <div className="flex flex-col p-4 sm:p-6">
                <dl className="grid grid-cols-3 gap-3 lg:grid-cols-1 lg:gap-0 lg:divide-y lg:divide-line lg:pb-6">
                  <div className="lg:pb-4">
                    <dt className="min-h-[2.6em] text-[12px] leading-tight text-muted lg:min-h-0">Answered by your adapter</dt>
                    <dd className="mt-1.5 text-[23px] font-semibold leading-none tracking-[-0.03em] text-ink min-[400px]:text-[26px] sm:text-[34px] lg:text-[40px]">
                      <CountUp value={d.student} format={(v) => fmtPct(v)} />
                    </dd>
                    <dd className="mt-2 hidden h-1.5 overflow-hidden rounded-full bg-rented/70 lg:flex" aria-hidden>
                      <span className="h-full rounded-full bg-owned transition-[width] duration-700" style={{ width: `${d.student * 100}%` }} />
                    </dd>
                  </div>
                  <div className="lg:py-4">
                    <dt className="min-h-[2.6em] text-[12px] leading-tight text-muted lg:min-h-0">Cost per 1k</dt>
                    <dd className="mt-1.5 text-[23px] font-semibold leading-none tracking-[-0.03em] text-ink min-[400px]:text-[26px] sm:text-[34px] lg:text-[40px]">
                      <CountUp value={d.costPer1k} format={(v) => fmtUsd(v, 3)} />
                    </dd>
                    <dd className="mt-1.5 text-[12px] text-muted tabular">vs {fmtUsd(d.baselinePer1k, 3)} all-Ultra</dd>
                  </div>
                  <div className="lg:pt-4">
                    <dt className="min-h-[2.6em] text-[12px] leading-tight text-muted lg:min-h-0">Saved that day</dt>
                    <dd className="mt-1.5 text-[23px] font-semibold leading-none tracking-[-0.03em] text-ink min-[400px]:text-[26px] sm:text-[34px] lg:text-[40px]">
                      <CountUp value={d.saved} format={(v) => fmtPct(v)} />
                    </dd>
                  </div>
                </dl>
                <div className="mt-5 min-h-[96px] rounded-xl border border-line bg-surface-2/70 p-3.5 lg:mt-auto" aria-live="polite">
                  <AnimatePresence mode="wait" initial={false}>
                    <motion.div
                      key={day}
                      initial={{ opacity: 0, y: 6 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0, y: -6 }}
                      transition={{ duration: 0.25, ease: EASE }}
                      className="flex gap-2.5"
                    >
                      <NoteIcon className={cn("mt-0.5 size-4 shrink-0", style.tone)} aria-hidden />
                      <div>
                        <div className="font-mono text-[11px] uppercase tracking-[0.12em] text-muted">Day {day}</div>
                        <p className="mt-1 text-[14px] leading-snug text-ink">{noteFor(d)}</p>
                      </div>
                    </motion.div>
                  </AnimatePresence>
                </div>
              </div>
            </div>

            <div className="flex flex-col gap-2 border-t border-line bg-surface-2/60 px-4 py-3.5 text-[12.5px] sm:px-6 lg:flex-row lg:items-center lg:justify-between">
              <p className="text-ink-2">
                <span className="font-medium text-ink">Over {LAST} days:</span> {fmtInt(SIM_TOTAL.requests)} requests,{" "}
                <span className="font-mono tabular">{fmtUsd(SIM_TOTAL.cost, 2)}</span> with Ratchet vs{" "}
                <span className="font-mono tabular">{fmtUsd(SIM_TOTAL.baseline, 2)}</span> all-Ultra,{" "}
                <span className="font-medium text-ink">{fmtPct(SIM_TOTAL.saved)} saved</span>.
              </p>
              <p className="text-muted">{SIM_SOURCE} Not real model quality.</p>
            </div>
          </div>
        </Reveal>
      </Container>
    </Chapter>
  );
}
