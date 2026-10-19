import { useCallback, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Activity, CheckCircle2, Pause, Play, Snowflake, Sprout, TrendingUp, Undo2, type LucideIcon } from "lucide-react";
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Button } from "@/components/ui/button";
import { Container } from "@/components/ui/section";
import { CountUp } from "@/components/ui/stat";
import { useInterval } from "@/lib/hooks";
import { SIM_DAYS, SIM_SOURCE, SIM_TOTAL, type SimDay } from "@/lib/sim";
import { cn, fmtInt, fmtPct, fmtUsd } from "@/lib/utils";
import { Chapter, ChapterHead, EASE, Lead, Reveal, SimBadge } from "../shared";

const LAST = SIM_DAYS.length;

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
  return `${fmtPct(d.student)} answered by your model, ${fmtPct(d.escalated)} sent to the teacher${d.audit != null ? `, ${fmtPct(d.audit)} of audits agreed` : ""}.`;
}

interface TipProps {
  active?: boolean;
  label?: string | number;
  payload?: ReadonlyArray<{ payload?: SimDay }>;
}

function ChartTip({ active, payload }: TipProps) {
  const d = payload?.[0]?.payload;
  if (!active || !d) return null;
  return (
    <div className="min-w-44 rounded-xl border border-line bg-surface px-3 py-2.5 text-[12px] shadow-card">
      <div className="mb-1.5 font-semibold text-ink">Day {d.day}</div>
      <div className="flex items-center justify-between gap-4 text-ink-2">
        <span className="inline-flex items-center gap-1.5">
          <span className="h-0.5 w-3 rounded-full bg-owned" /> With Ratchet
        </span>
        <span className="font-mono tabular text-ink">{fmtUsd(d.costPer1k, 3)}</span>
      </div>
      <div className="mt-1 flex items-center justify-between gap-4 text-ink-2">
        <span className="inline-flex items-center gap-1.5">
          <svg width="12" height="4" aria-hidden>
            <line x1="0" x2="12" y1="2" y2="2" stroke="var(--rented)" strokeWidth="2" strokeDasharray="4 2" />
          </svg>{" "}
          All-Ultra
        </span>
        <span className="font-mono tabular text-ink">{fmtUsd(d.baselinePer1k, 3)}</span>
      </div>
      <div className="mt-1.5 border-t border-line pt-1.5 text-muted">{fmtPct(d.student)} answered by your model</div>
    </div>
  );
}

function CostChart({ day, onPick }: { day: number; onPick: (d: number) => void }) {
  const summary = `Line chart, simulation: cost per 1,000 requests over ${LAST} days. All-Ultra stays at ${fmtUsd(SIM_DAYS[0].baselinePer1k, 3)}. With Ratchet it starts at ${fmtUsd(SIM_DAYS[0].costPer1k, 3)} on day 1 and ends at ${fmtUsd(SIM_DAYS[LAST - 1].costPer1k, 3)} on day ${LAST}, with a bump on day 5 when a product launch brings new questions.`;
  return (
    <div className="h-[260px] w-full sm:h-[320px]" role="img" aria-label={summary}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart
          data={SIM_DAYS}
          margin={{ top: 22, right: 12, bottom: 0, left: -6 }}
          onClick={(s) => {
            const n = Number(s?.activeLabel);
            if (n >= 1 && n <= LAST) onPick(n);
          }}
          style={{ cursor: "pointer" }}
        >
          <CartesianGrid stroke="var(--line)" vertical={false} />
          <XAxis
            dataKey="day"
            tickLine={false}
            axisLine={{ stroke: "var(--line-strong)" }}
            tick={{ fill: "var(--muted)", fontSize: 11.5 }}
            tickMargin={8}
            interval={0}
            padding={{ left: 8, right: 8 }}
          />
          <YAxis
            domain={[0, 1.2]}
            ticks={[0, 0.3, 0.6, 0.9, 1.2]}
            tickFormatter={(v: number) => `$${v.toFixed(2)}`}
            tickLine={false}
            axisLine={false}
            tick={{ fill: "var(--muted)", fontSize: 11.5 }}
            width={50}
          />
          <Tooltip
            content={(p) => <ChartTip active={p.active} payload={p.payload as TipProps["payload"]} />}
            cursor={{ stroke: "var(--line-strong)", strokeWidth: 1 }}
            isAnimationActive={false}
          />
          <ReferenceLine x={day} stroke="var(--ink-2)" strokeDasharray="3 3" strokeWidth={1} />
          <Line
            type="monotone"
            dataKey="baselinePer1k"
            name="All-Ultra"
            stroke="var(--rented)"
            strokeWidth={2}
            strokeDasharray="6 5"
            dot={false}
            activeDot={{ r: 4, fill: "var(--rented)", stroke: "var(--surface)", strokeWidth: 2 }}
            isAnimationActive={false}
            label={(p: { index?: number; x?: number | string; y?: number | string }) =>
              p.index === LAST - 1 ? (
                <text key="lbl-base" x={Number(p.x)} y={Number(p.y) - 10} textAnchor="end" fill="var(--rented-ink)" fontSize={12} fontWeight={600}>
                  All-Ultra
                </text>
              ) : (
                <g key={`lb-${p.index}`} />
              )
            }
          />
          <Line
            type="monotone"
            dataKey="costPer1k"
            name="With Ratchet"
            stroke="var(--owned)"
            strokeWidth={2}
            strokeLinecap="round"
            isAnimationActive={false}
            dot={(p: { index?: number; cx?: number; cy?: number; payload?: SimDay }) => {
              const sel = p.payload?.day === day;
              const noted = !!p.payload?.note;
              return (
                <circle
                  key={`dot-${p.index}`}
                  cx={p.cx}
                  cy={p.cy}
                  r={sel ? 5.5 : noted ? 3.5 : 0}
                  fill={sel ? "var(--owned)" : "var(--surface)"}
                  stroke={sel ? "var(--surface)" : "var(--owned)"}
                  strokeWidth={sel ? 2.5 : 1.5}
                />
              );
            }}
            activeDot={{ r: 5, fill: "var(--owned)", stroke: "var(--surface)", strokeWidth: 2 }}
            label={(p: { index?: number; x?: number | string; y?: number | string }) =>
              p.index === LAST - 1 ? (
                <text key="lbl-own" x={Number(p.x)} y={Number(p.y) - 18} textAnchor="end" fill="var(--owned-ink)" fontSize={12} fontWeight={600}>
                  With Ratchet
                </text>
              ) : (
                <g key={`lo-${p.index}`} />
              )
            }
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
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

  const onPlay = () => {
    if (playing) return setPlaying(false);
    if (day >= LAST) setDay(1);
    setPlaying(true);
  };
  const pick = (n: number) => {
    setPlaying(false);
    setDay(n);
  };
  const style = NOTE_STYLE[day] ?? { icon: Activity, tone: "text-muted" };
  const NoteIcon = style.icon;

  return (
    <Chapter id="learn" className="border-t border-line bg-bg-sunken/60">
      <Container>
        <ChapterHead
          id="learn"
          title="Watch it learn."
          lead={
            <Lead>
              Ten days of support tickets in the reference simulation. Drag through the days: cold start, a first student, a bad release caught,
              a product launch that brings new questions, and a student that learns them overnight.
            </Lead>
          }
        />

        <Reveal className="mt-12">
          <div className="overflow-hidden rounded-[24px] border border-line bg-surface shadow-card">
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
                <CostChart day={day} onPick={pick} />
                <table className="sr-only">
                  <caption>Simulation: cost per 1,000 requests by day</caption>
                  <thead>
                    <tr>
                      <th>Day</th>
                      <th>Answered by your model</th>
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

              <div className="flex flex-col p-4 sm:p-6">
                <dl className="grid grid-cols-3 gap-3 lg:grid-cols-1 lg:gap-0 lg:divide-y lg:divide-line lg:pb-6">
                  <div className="lg:pb-4">
                    <dt className="min-h-[2.6em] text-[12px] leading-tight text-muted lg:min-h-0">Answered by your model</dt>
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
