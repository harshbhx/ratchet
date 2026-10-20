import type { ReactNode } from "react";
import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";
import type { Stats } from "@/lib/api";
import { cn, fmtPct, fmtUsd } from "@/lib/utils";
import { cumulativeSaved, fmtPts, inFlight, latest, liveVersion, prev, stateLabel } from "./model";

/** Tiny trend line. De-emphasis line with the current point in the owned accent. */
function Sparkline({ values, max, label }: { values: number[]; max?: number; label: string }) {
  if (values.length < 2) return null;
  const W = 96;
  const H = 32;
  const hi = max ?? Math.max(...values, 1e-9);
  const lo = Math.min(0, ...values);
  const x = (i: number) => 2 + (i * (W - 6)) / (values.length - 1);
  const y = (v: number) => H - 3 - ((v - lo) / (hi - lo || 1)) * (H - 7);
  const d = values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(" ");
  const area = `${d} L${x(values.length - 1).toFixed(1)} ${H} L${x(0).toFixed(1)} ${H} Z`;
  const lx = x(values.length - 1);
  const ly = y(values[values.length - 1]);
  return (
    <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label} className="shrink-0 overflow-visible">
      <path d={area} fill="var(--owned)" opacity={0.08} />
      <path d={d} fill="none" stroke="var(--owned)" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={lx} cy={ly} r={3} fill="var(--owned)" stroke="var(--surface)" strokeWidth={2} />
    </svg>
  );
}

function Delta({ delta, vs }: { delta: number | null; vs?: number }) {
  if (delta == null) return null;
  const pts = Math.round(delta * 100);
  const Icon = pts > 0 ? ArrowUpRight : pts < 0 ? ArrowDownRight : Minus;
  const tone = pts > 0 ? "text-good" : pts < 0 ? "text-bad" : "text-muted";
  return (
    <span className="inline-flex items-center gap-0.5 whitespace-nowrap text-[12px] font-medium">
      <Icon className={cn("size-3.5", tone)} aria-hidden />
      <span className="text-ink-2 tabular">{fmtPts(delta)}</span>
      {vs != null && <span className="font-normal text-muted">&nbsp;vs day {vs}</span>}
    </span>
  );
}

function Tile({ label, value, foot, trend, className }: { label: string; value: ReactNode; foot?: ReactNode; trend?: ReactNode; className?: string }) {
  return (
    <div className={cn("flex min-w-0 flex-col justify-between gap-3 rounded-2xl border border-line bg-surface p-4 shadow-card sm:p-5", className)}>
      <div className="min-w-0">
        <div className="truncate text-[13px] text-muted">{label}</div>
        <div className="mt-2 flex items-end justify-between gap-3">
          <div className="min-w-0 truncate text-[28px] font-semibold leading-none tracking-[-0.03em] text-ink sm:text-[30px]">{value}</div>
          {trend}
        </div>
      </div>
      {foot && <div className="min-h-5 text-[12.5px] leading-5 text-muted">{foot}</div>}
    </div>
  );
}

export function Kpis({ stats }: { stats: Stats }) {
  const { days, totals, versions } = stats;
  const last = latest(days);
  const before = prev(days);
  const cum = cumulativeSaved(days);
  const live = liveVersion(versions);
  const flight = inFlight(versions).pop();

  return (
    <div className="grid grid-cols-1 gap-3 min-[480px]:grid-cols-2 lg:grid-cols-4">
      <Tile
        label="Saved vs all-teacher, per token"
        value={fmtUsd(totals.saved_usd, 2)}
        trend={<Sparkline values={cum} label="Cumulative savings by day, rising" />}
        foot={
          totals.baseline_usd > 0 ? (
            <>
              <span className="text-ink-2 tabular">{fmtPct(totals.savings_pct)}</span> of {fmtUsd(totals.baseline_usd, 2)} all-teacher spend, since day {days[0]?.day ?? 0}
            </>
          ) : (
            "Nothing spent yet"
          )
        }
      />
      <Tile
        label={last ? `Cost reduction · day ${last.day}` : "Cost reduction"}
        value={last ? fmtPct(last.savings_pct) : "–"}
        trend={<Sparkline values={days.map((d) => d.savings_pct)} max={1} label="Daily cost reduction by day" />}
        foot={last && before ? <Delta delta={last.savings_pct - before.savings_pct} vs={before.day} /> : last ? "First day of traffic" : "No traffic yet"}
      />
      <Tile
        label={last ? `Answered by your model · day ${last.day}` : "Answered by your model"}
        value={last ? fmtPct(last.student_share) : "–"}
        trend={<Sparkline values={days.map((d) => d.student_share)} max={1} label="Share answered by your model by day" />}
        foot={
          last ? (
            <span className="flex flex-wrap items-center gap-x-2">
              {before && <Delta delta={last.student_share - before.student_share} vs={before.day} />}
              <span>
                <span className="text-ink-2 tabular">{fmtPct(last.escalation_rate)}</span> escalated
              </span>
            </span>
          ) : (
            "No traffic yet"
          )
        }
      />
      <Tile
        label="Live version"
        value={
          live ? (
            <span className="font-mono tracking-[-0.04em]">v{live.id}</span>
          ) : (
            <span className="text-[22px] tracking-[-0.02em] text-muted sm:text-[24px]">Teacher only</span>
          )
        }
        trend={
          live?.eval.precision != null ? (
            <div className="text-right">
              <div className="text-[18px] font-semibold leading-none text-ink">{fmtPct(live.eval.precision)}</div>
              <div className="mt-1 text-[11px] text-muted">golden precision</div>
            </div>
          ) : undefined
        }
        foot={
          flight ? (
            <>
              Next: <span className="font-mono font-medium text-ink-2">v{flight.id}</span> in {stateLabel(flight).toLowerCase()}
            </>
          ) : live ? (
            `Trained day ${live.day} on ${live.train_examples.toLocaleString("en-US")} examples`
          ) : (
            "Cold start: no student yet"
          )
        }
      />
    </div>
  );
}

export function KpiSkeleton() {
  return (
    <div className="grid grid-cols-1 gap-3 min-[480px]:grid-cols-2 lg:grid-cols-4" aria-hidden>
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="h-[118px] animate-pulse rounded-2xl border border-line bg-surface" />
      ))}
    </div>
  );
}
