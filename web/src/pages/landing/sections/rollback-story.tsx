import { useEffect, useMemo, useRef, useState } from "react";
import { animate, useInView, useReducedMotion } from "motion/react";
import { Check, RotateCcw, Undo2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn, fmtPct } from "@/lib/utils";
import { SimBadge, useElementWidth, wilson } from "../shared";

/*
  The simulation's day 4 (seed 7): v3 was deployed corrupted, entered canary at 10% and was rolled back
  at 41.2% audited precision (n = 17, Wilson upper bound 64.0% < 92%), traffic back to live v1.
  The audit order below is illustrative; the totals (7 of 17 agree) match the simulation's event.
  The simulation's gate decides after 15 audits (`sim/simulate.py --min-audits 15`); the default is 20.
*/
const AUDITS = [1, 1, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0];
const FLOOR = 0.92;
const MIN_AUDITS = 15;
const N = AUDITS.length;

const SERIES = AUDITS.map((_, i) => {
  const n = i + 1;
  const k = AUDITS.slice(0, n).reduce((a, b) => a + b, 0);
  const [lo, hi] = wilson(k, n);
  return { n, k, p: k / n, lo, hi };
});
const CROSS = SERIES.find((s) => s.hi < FLOOR)!.n;
const FINAL = SERIES[N - 1];

const STORY = [
  { at: 0, title: "v3 enters canary at 10%", body: "It passed the golden gate and shadow. Live v1 keeps 90% of traffic." },
  { at: 1, title: "The teacher audits what v3 serves", body: "A share of v3's answers is checked by Ultra. Each audit agrees or not." },
  { at: CROSS, title: "The upper bound drops below 92%", body: "Even the most generous estimate of v3's precision is now under the floor." },
  { at: N, title: "Rolled back automatically", body: "Enough audits to decide. v3 is pulled; users are back on v1." },
];

function Chart({ progress }: { progress: number }) {
  const [ref, W] = useElementWidth<HTMLDivElement>();
  const H = W < 480 ? 240 : 280;
  const m = { l: 40, r: 16, t: 16, b: 30 };
  const iw = Math.max(0, W - m.l - m.r);
  const ih = H - m.t - m.b;
  const x = (n: number) => m.l + ((n - 1) / (N - 1)) * iw;
  const y = (v: number) => m.t + (1 - v) * ih;
  const shown = SERIES.filter((s) => s.n <= Math.max(1, Math.floor(progress)));
  const frac = progress - Math.floor(progress);
  const nextS = SERIES[Math.floor(progress)];
  const pts = [...shown];
  // Interpolate toward the next audit so lines draw smoothly.
  if (nextS && progress >= 1 && frac > 0) {
    const prev = shown[shown.length - 1];
    pts.push({
      n: prev.n + frac,
      k: 0,
      p: prev.p + (nextS.p - prev.p) * frac,
      lo: prev.lo + (nextS.lo - prev.lo) * frac,
      hi: prev.hi + (nextS.hi - prev.hi) * frac,
    });
  }
  const line = (key: "p" | "hi") => pts.map((s, i) => `${i ? "L" : "M"}${x(s.n).toFixed(1)} ${y(s[key]).toFixed(1)}`).join(" ");
  const band =
    pts.length > 1
      ? `M${pts.map((s) => `${x(s.n).toFixed(1)} ${y(s.hi).toFixed(1)}`).join(" L")} L${[...pts]
          .reverse()
          .map((s) => `${x(s.n).toFixed(1)} ${y(s.lo).toFixed(1)}`)
          .join(" L")} Z`
      : "";
  const done = progress >= N;
  const crossed = progress >= CROSS;
  const head = pts[pts.length - 1];
  const yTicks = [0, 0.25, 0.5, 0.75, 1];
  // Labels sit right of the moving head, then flip to its left near the edge.
  const side = (hx: number) => (hx > W * 0.62 ? { right: W - hx + 10 } : { left: hx + 10 });

  return (
    <div ref={ref} className="relative w-full">
      {W > 0 && (
        <svg
          width={W}
          height={H}
          viewBox={`0 0 ${W} ${H}`}
          className="block"
          role="img"
          aria-label={`Chart, simulation: audited precision of v3 and its 95% Wilson upper bound over ${N} audits. The upper bound falls below the 92% floor after ${CROSS} audits. After ${N} audits precision is ${fmtPct(FINAL.p, 1)} with an upper bound of ${fmtPct(FINAL.hi, 1)}, and v3 is rolled back.`}
        >
          {yTicks.map((v) => (
            <g key={v}>
              <line x1={m.l} x2={W - m.r} y1={y(v)} y2={y(v)} stroke="var(--line)" />
              <text x={m.l - 8} y={y(v) + 4} textAnchor="end" className="fill-muted" style={{ fontSize: 11 }}>
                {v * 100}%
              </text>
            </g>
          ))}
          {[1, 5, 10, 15, N].map((n) => (
            <text key={n} x={x(n)} y={H - 10} textAnchor="middle" className="fill-muted tabular" style={{ fontSize: 11 }}>
              {n}
            </text>
          ))}

          {/* minimum audits before the gate decides */}
          <line x1={x(MIN_AUDITS)} x2={x(MIN_AUDITS)} y1={m.t} y2={m.t + ih} stroke="var(--line-strong)" strokeDasharray="2 4" />

          {/* floor */}
          <line x1={m.l} x2={W - m.r} y1={y(FLOOR)} y2={y(FLOOR)} stroke="var(--ink-2)" strokeWidth={1.25} strokeDasharray="5 4" />
          <text x={W - m.r - 4} y={y(FLOOR) - 7} textAnchor="end" className="fill-ink-2 font-medium" style={{ fontSize: 11 }}>
            floor 92%
          </text>

          {/* interval band + lines */}
          {band && <path d={band} fill="var(--owned)" opacity={0.1} />}
          {pts.length > 1 && (
            <>
              <path d={line("hi")} fill="none" stroke="var(--owned)" strokeWidth={1.5} strokeDasharray="4 3" strokeLinecap="round" />
              <path d={line("p")} fill="none" stroke="var(--owned)" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />
            </>
          )}
          {head && (
            <>
              <circle cx={x(head.n)} cy={y(head.p)} r={3.5} fill="var(--owned)" />
              <circle cx={x(head.n)} cy={y(head.hi)} r={3} fill="var(--surface)" stroke="var(--owned)" strokeWidth={1.5} />
            </>
          )}

          {/* crossing marker */}
          {crossed && (
            <g>
              <circle cx={x(CROSS)} cy={y(SERIES[CROSS - 1].hi)} r={6} fill="none" stroke="var(--bad)" strokeWidth={1.5} />
            </g>
          )}

          {/* rollback marker */}
          {done && (
            <g>
              <line x1={x(N)} x2={x(N)} y1={m.t} y2={m.t + ih} stroke="var(--bad)" strokeWidth={1.5} />
            </g>
          )}
        </svg>
      )}
      {/* direct labels (HTML for crisp wrapping) */}
      {W > 0 && head && pts.length > 1 && (
        <>
          <span
            className="pointer-events-none absolute whitespace-nowrap text-[11px] font-medium text-owned-ink"
            style={{ ...side(x(head.n)), top: y(head.hi) - (head.hi > 0.9 ? -6 : 24) }}
          >
            upper bound {fmtPct(head.hi, 0)}
          </span>
          <span
            className="pointer-events-none absolute whitespace-nowrap text-[11px] font-medium text-ink-2"
            style={{ ...side(x(head.n)), top: y(head.p) + 6 }}
          >
            precision {fmtPct(head.p, 0)}
          </span>
        </>
      )}
      <div className="mt-1 flex justify-between pl-10 text-[11px] text-muted">
        <span>Teacher audits of v3's answers →</span>
        <span className="hidden sm:inline">dotted: {MIN_AUDITS} audits before the gate decides (simulation)</span>
      </div>
    </div>
  );
}

export function RollbackStory() {
  const ref = useRef<HTMLDivElement | null>(null);
  const inView = useInView(ref, { once: true, margin: "0px 0px -25% 0px" });
  const reduce = useReducedMotion() ?? false;
  const [progress, setProgress] = useState(reduce ? N : 0);
  const [run, setRun] = useState(0);

  useEffect(() => {
    if (!inView) return;
    if (reduce) {
      setProgress(N);
      return;
    }
    setProgress(0);
    const c = animate(0, N, { duration: 6, ease: "linear", delay: 0.4, onUpdate: setProgress });
    return () => c.stop();
  }, [inView, reduce, run]);

  const done = progress >= N;
  const stage = useMemo(() => STORY.reduce((acc, s, i) => (progress >= s.at ? i : acc), 0), [progress]);
  const v3Share = done ? 0 : 10;

  return (
    <div
      ref={ref}
      className={cn(
        "overflow-hidden rounded-[24px] border bg-surface shadow-card transition-[box-shadow,border-color] duration-700",
        done ? "border-bad/40 shadow-[0_0_90px_-30px_var(--bad)]" : "border-line",
      )}
    >
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3 sm:px-6">
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-[13px] font-semibold text-ink">A bad release, caught</p>
          <SimBadge>Simulation · day 4</SimBadge>
        </div>
        <Button variant="ghost" size="sm" onClick={() => setRun((r) => r + 1)} disabled={!done && progress > 0} aria-label="Replay the rollback story">
          <RotateCcw /> Replay
        </Button>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)]">
        <div className="border-b border-line p-4 sm:p-6 lg:border-b-0 lg:border-r">
          <ol className="relative space-y-5">
            {STORY.map((s, i) => {
              const reached = i <= stage && (progress >= s.at || i === 0);
              const isLast = i === STORY.length - 1;
              return (
                <li key={s.title} className={cn("relative flex gap-3.5 transition-opacity duration-500", reached ? "opacity-100" : "opacity-35")}>
                  <span
                    className={cn(
                      "relative z-10 mt-0.5 inline-flex size-6 shrink-0 items-center justify-center rounded-full border text-[11px] font-mono transition-colors duration-500",
                      reached && isLast ? "border-bad bg-bad-soft text-bad" : reached ? "border-ink bg-ink text-bg" : "border-line-strong bg-surface text-muted",
                    )}
                  >
                    {reached && isLast ? <Undo2 className="size-3.5" aria-hidden /> : i + 1}
                  </span>
                  {!isLast && <span aria-hidden className="absolute left-[11.5px] top-7 h-[calc(100%-4px)] w-px bg-line" />}
                  <div className="min-w-0">
                    <p className="text-[14.5px] font-semibold text-ink">{s.title}</p>
                    <p className="mt-1 text-[13.5px] leading-relaxed text-ink-2">{s.body}</p>
                  </div>
                </li>
              );
            })}
          </ol>

          <div className="mt-6 rounded-xl border border-line bg-surface-2/70 p-3.5">
            <div className="flex items-center justify-between text-[12px] text-muted">
              <span>Traffic</span>
              <span aria-live="polite">
                {done ? (
                  <Badge tone="bad">
                    <Undo2 aria-hidden /> Rolled back
                  </Badge>
                ) : (
                  <Badge tone="warn">v3 in canary</Badge>
                )}
              </span>
            </div>
            <div className="mt-2.5 flex h-6 overflow-hidden rounded-md text-[11px] font-medium" aria-hidden>
              <div className="flex items-center bg-owned-ink px-2 text-white transition-[width] duration-700 dark:bg-owned dark:text-[#06101f]" style={{ width: `${100 - v3Share}%` }}>
                v1 live · {100 - v3Share}%
              </div>
              <div
                className="flex items-center justify-center overflow-hidden whitespace-nowrap border-l-2 border-surface bg-owned-soft text-owned-ink transition-[width] duration-700"
                style={{ width: `${v3Share}%` }}
              >
                v3
              </div>
            </div>
            <p className="mt-2.5 flex items-center gap-1.5 text-[12px] text-muted">
              {done ? (
                <>
                  <Check className="size-3.5 text-good" aria-hidden /> Users are back on v1. Nobody had to step in.
                </>
              ) : (
                <>v3 serves 10% while the audits come in.</>
              )}
            </p>
          </div>
        </div>
        <div className="p-4 sm:p-6">
          <div className="mb-3 flex flex-wrap items-center gap-x-5 gap-y-1.5 text-[12px] text-ink-2">
            <span className="inline-flex items-center gap-2">
              <svg width="22" height="8" aria-hidden>
                <line x1="1" x2="21" y1="4" y2="4" stroke="var(--owned)" strokeWidth="2" strokeLinecap="round" />
              </svg>
              Audited precision
            </span>
            <span className="inline-flex items-center gap-2">
              <svg width="22" height="8" aria-hidden>
                <line x1="1" x2="21" y1="4" y2="4" stroke="var(--owned)" strokeWidth="1.5" strokeDasharray="4 3" />
              </svg>
              95% Wilson upper bound
            </span>
            <span className="inline-flex items-center gap-2">
              <svg width="22" height="8" aria-hidden>
                <line x1="1" x2="21" y1="4" y2="4" stroke="var(--ink-2)" strokeWidth="1.25" strokeDasharray="5 4" />
              </svg>
              Floor
            </span>
          </div>
          <Chart progress={Math.max(progress, 1)} />
          <div className="mt-4 grid grid-cols-3 gap-3 border-t border-line pt-4">
            {[
              ["Audits", String(Math.max(1, Math.floor(progress)))],
              ["Precision", fmtPct(SERIES[Math.max(0, Math.floor(progress) - 1)].p, 1)],
              ["Upper bound", fmtPct(SERIES[Math.max(0, Math.floor(progress) - 1)].hi, 1)],
            ].map(([k, v]) => (
              <div key={k}>
                <div className="text-[11.5px] text-muted">{k}</div>
                <div className="mt-1 font-mono text-[17px] font-medium text-ink tabular sm:text-[19px]">{v}</div>
              </div>
            ))}
          </div>
        </div>
      </div>
      <p className="border-t border-line bg-surface-2/60 px-4 py-3 text-[12px] leading-relaxed text-muted sm:px-6">
        Simulation, mock models, seed 7: v3 was deployed corrupted on day 4 and rolled back at {fmtPct(FINAL.p, 1)} audited precision (n ={" "}
        {N}, upper bound {fmtPct(FINAL.hi, 1)} &lt; 92%). Audit order is illustrative. The simulation decides after {MIN_AUDITS} audits; the
        default is 20.
      </p>
    </div>
  );
}
