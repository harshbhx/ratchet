import { Fragment, useState, type ReactNode } from "react";
import { Bar, BarChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis, type BarShapeProps, type TooltipContentProps } from "recharts";
import { useReducedMotion } from "motion/react";
import {
  CircleDashed,
  CloudCog,
  Coins,
  FlaskConical,
  Info,
  PowerOff,
  ServerCog,
  ShieldCheck,
  TrendingDown,
  TriangleAlert,
  type LucideIcon,
} from "lucide-react";
import type { CostDay, CostReport, CostSummary, CostView, Recommendation, Stats } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { cn, fmtInt, fmtUsd } from "@/lib/utils";
import {
  baselineOf,
  codeParts,
  dailySavings,
  fmtAxisMoney,
  fmtDecade,
  fmtHours,
  fmtMoney,
  fmtRate,
  fmtSignedPct,
  fmtTokenPrice,
  isEmptyReport,
  meterScale,
  recMeta,
  signedTicks,
  windowLabel,
  type DailySavings,
} from "./economics-model";
import { DataTable, Empty, Panel, td, tdNum, th, thNum, ViewToggle } from "./ui";
import type { CostState } from "./use-dashboard";

const OWNED = "var(--owned)";
const AXIS_TICK = { fill: "var(--muted)", fontSize: 11 } as const;
const CHART_H = 220;
/** The chart shows the latest days; the table view lists every day the report sent. */
const CHART_DAYS = 60;

const REC_ICON: Record<string, LucideIcon> = {
  self_host: ServerCog,
  hosted_fallback: CloudCog,
  scale_to_zero: PowerOff,
  insufficient_data: CircleDashed,
};

/* ---------------- small pieces ---------------- */

/** A figure inside a sentence: kept on one line, so a minus sign never wraps away from its number. */
function Num({ children }: { children: ReactNode }) {
  return <b className="whitespace-nowrap font-medium text-ink tabular">{children}</b>;
}

/** A report sentence with its `commands` as code; React text only. */
function ReportText({ text }: { text: string }) {
  return (
    <>
      {codeParts(text).map((p, i) =>
        p.code ? (
          <code key={i} className="rounded border border-line bg-surface-2 px-1 py-px font-mono text-[11.5px] text-ink [overflow-wrap:anywhere]">
            {p.text}
          </code>
        ) : (
          <Fragment key={i}>{p.text}</Fragment>
        ),
      )}
    </>
  );
}

export function RecommendationChip({ recommendation, className }: { recommendation: Recommendation; className?: string }) {
  const meta = recMeta(recommendation);
  const Icon = REC_ICON[recommendation] ?? Info;
  return (
    <Badge tone={meta.tone} className={className}>
      <Icon aria-hidden /> {meta.label}
    </Badge>
  );
}

function Tile({ title, sub, badge, children, className }: { title: string; sub?: ReactNode; badge?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <div className={cn("flex min-w-0 flex-col rounded-xl border border-line bg-bg-sunken/50 p-3.5", className)}>
      <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1.5">
        <div className="min-w-0">
          <h3 className="text-[13px] font-medium text-ink">{title}</h3>
          {sub && <p className="mt-0.5 text-pretty text-[12px] leading-[18px] text-muted">{sub}</p>}
        </div>
        {badge}
      </div>
      <div className="mt-3 flex min-w-0 flex-1 flex-col">{children}</div>
    </div>
  );
}

/** A thin horizontal bar against the larger of the tile's two values; a non-zero value always shows a sliver. */
function CompareBar({ label, value, max, fill }: { label: string; value: number; max: number; fill: "owned" | "owned-soft" | "rented" }) {
  const w = max > 0 && value > 0 ? Math.max(0.8, (value / max) * 100) : 0;
  return (
    <div className="grid grid-cols-[6.5rem_minmax(0,1fr)_auto] items-center gap-2 text-[12px]">
      <span className="truncate text-ink-2">{label}</span>
      <span className="relative h-2 rounded-full bg-surface-2" aria-hidden>
        {w > 0 && (
          <span
            className={cn(
              "absolute inset-y-0 left-0 rounded-full",
              fill === "owned" && "bg-owned",
              fill === "owned-soft" && "border border-owned bg-owned-soft",
              fill === "rented" && "bg-rented",
            )}
            style={{ width: `${w}%` }}
          />
        )}
      </span>
      <span className="text-right font-medium text-ink tabular">{fmtMoney(value)}</span>
    </div>
  );
}

function Rows({ rows }: { rows: Array<[ReactNode, ReactNode] | null | false> }) {
  const shown = rows.filter(Boolean) as Array<[ReactNode, ReactNode]>;
  if (shown.length === 0) return null;
  return (
    <dl className="mt-3 space-y-1 border-t border-line pt-2.5 text-[12px] leading-[18px]">
      {shown.map(([k, v], i) => (
        <div key={i} className="flex items-baseline justify-between gap-3">
          <dt className="min-w-0 text-muted">{k}</dt>
          <dd className="shrink-0 text-right text-ink-2 tabular">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

/* ---------------- 1. the two views ---------------- */

function Savings({ view, baseline }: { view: CostView; baseline: number }) {
  const neg = view.savings_usd < 0;
  return (
    <div className="min-w-0">
      {neg && (
        <Badge tone="warn" className="mb-2">
          <TrendingDown aria-hidden /> Costs more than all-teacher
        </Badge>
      )}
      <p className="text-[12px] text-muted">Savings</p>
      <p className="text-[24px] font-semibold leading-tight tracking-[-0.02em] text-ink">{fmtMoney(view.savings_usd)}</p>
      <p className="mt-0.5 text-[12.5px] text-muted">
        {view.savings_pct == null ? (
          baseline > 0 ? (
            "No savings share without a baseline"
          ) : (
            "No requests yet, so no all-teacher baseline to compare"
          )
        ) : (
          <>
            <span className="font-medium text-ink-2 tabular">{fmtSignedPct(view.savings_pct)}</span> against the {fmtMoney(baseline)} all-teacher cost
          </>
        )}
      </p>
    </div>
  );
}

function ViewTile({
  title,
  sub,
  view,
  baseline,
  fill,
  headline,
  children,
}: {
  title: string;
  sub: ReactNode;
  view: CostView;
  baseline: number;
  fill: "owned" | "owned-soft";
  headline: boolean;
  children?: ReactNode;
}) {
  const max = Math.max(view.total_usd, baseline);
  return (
    <Tile
      title={title}
      sub={sub}
      badge={
        headline ? (
          <Badge tone="outline" title="The view RATCHET_STUDENT_COST_MODEL picks for the report's headline">
            Headline
          </Badge>
        ) : undefined
      }
    >
      <Savings view={view} baseline={baseline} />
      <div className="mt-3 space-y-1.5">
        <CompareBar label="Ratchet" value={view.total_usd} max={max} fill={fill} />
        <CompareBar label="All-teacher" value={baseline} max={max} fill="rented" />
      </div>
      {children}
    </Tile>
  );
}

/* ---------------- 2. recommendation ---------------- */

function RecommendationTile({ report }: { report: CostReport }) {
  const warnings = report.warnings ?? [];
  return (
    <Tile title="Recommendation" badge={<RecommendationChip recommendation={report.recommendation} />} className="md:col-span-2 lg:col-span-1">
      {report.explanation ? (
        <p className="text-pretty text-[13px] leading-5 text-ink-2">
          <ReportText text={report.explanation} />
        </p>
      ) : (
        <p className="text-[13px] leading-5 text-muted">This gateway sent no explanation.</p>
      )}
      {warnings.length > 0 && (
        <ul className="mt-3 space-y-1.5 border-t border-line pt-2.5" aria-label="Warnings">
          {warnings.map((w, i) => (
            <li key={i} className="flex gap-2 text-[12.5px] leading-[18px] text-ink-2">
              <TriangleAlert className="mt-0.5 size-3.5 shrink-0 text-warn" aria-hidden />
              <span className="min-w-0 [overflow-wrap:anywhere]">
                <span className="sr-only">Warning: </span>
                <ReportText text={w} />
              </span>
            </li>
          ))}
        </ul>
      )}
    </Tile>
  );
}

/* ---------------- 3. break-even meter ---------------- */

type MeterRow = { key: string; label: string; value: number | null | undefined; mark: "dot" | "tick" | "ring"; note?: string };

function Mark({ kind }: { kind: MeterRow["mark"] }) {
  if (kind === "dot") return <span className="block size-3 rounded-full bg-owned ring-2 ring-surface" />;
  if (kind === "tick") return <span className="block h-4 w-[3px] rounded-full bg-ink-2 ring-2 ring-surface" />;
  return <span className="block size-3 rounded-full border-2 border-muted bg-surface" />;
}

function BreakEven({ report }: { report: CostReport }) {
  const observed = report.observed_student_requests_per_endpoint_hour;
  const be = report.break_even_student_requests_per_gpu_hour;
  const cross = report.self_host_beats_hosted_above_requests_per_hour;
  const util = report.utilization_vs_break_even;
  const rows: MeterRow[] = [
    { key: "observed", label: "Observed: student answers per endpoint-hour", value: observed, mark: "dot" },
    { key: "be", label: "Break-even against the teacher spend it replaces", value: be, mark: "tick" },
  ];
  if (cross != null) rows.push({ key: "cross", label: "Beats the hosted fallback above", value: cross, mark: "ring" });
  const scale = meterScale(rows.map((r) => r.value));

  let caption: ReactNode;
  if (observed == null) {
    caption = "No endpoint uptime is recorded in this window, so there is no observed volume to compare.";
  } else if (be == null) {
    caption = (
      <>
        The endpoint served <Num>{fmtRate(observed)}</Num> student answers per hour it ran. Break-even is unknown until
        student answers avoid teacher spend.
      </>
    );
  } else {
    caption = (
      <>
        <Num>{fmtRate(observed)}</Num> an hour is{" "}
        {util != null && <Num>{fmtSignedPct(util)}</Num>} {util != null ? "of" : "against"} the{" "}
        <Num>{fmtRate(be)}</Num> the endpoint needs to pay for itself
        {cross != null && (
          <>
            ; above <Num>{fmtRate(cross)}</Num> it also beats the hosted fallback
          </>
        )}
        .
      </>
    );
  }
  const summary = `Break-even meter${scale?.log ? " on a log scale" : ""}: ${rows
    .map((r) => `${r.label} ${r.value == null ? "unknown" : fmtRate(r.value)}`)
    .join("; ")}.`;

  return (
    <Tile
      title="Break-even"
      sub="Student answers per hour the endpoint ran, against the volume at which it pays for its GPU-hours."
      className="lg:col-span-7"
    >
      {scale ? (
        <div role="img" aria-label={summary}>
          <div className="space-y-2.5">
            {rows.map((r) => (
              <div key={r.key}>
                <div className="flex items-baseline justify-between gap-3 text-[12px] leading-4">
                  <span className="min-w-0 text-ink-2">{r.label}</span>
                  <span className="shrink-0 font-medium text-ink tabular">{r.value == null ? "unknown" : `${fmtRate(r.value)} / h`}</span>
                </div>
                <div aria-hidden className="relative mt-1 h-4">
                  <span className="absolute inset-x-0 top-1/2 h-1.5 -translate-y-1/2 rounded-full bg-surface-2" />
                  {scale.ticks.map((t) => (
                    <span key={t.value} className="absolute inset-y-0 w-px bg-line-strong/70" style={{ left: `${t.at * 100}%` }} />
                  ))}
                  {be != null && r.key !== "be" && (
                    <span className="absolute -inset-y-1 border-l border-dashed border-ink-2/60" style={{ left: `${scale.at(be) * 100}%` }} />
                  )}
                  {r.value != null && (
                    <span className="absolute top-1/2 -translate-x-1/2 -translate-y-1/2" style={{ left: `${scale.at(r.value) * 100}%` }}>
                      <Mark kind={r.mark} />
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
          <div aria-hidden className="relative mt-1.5 h-4 text-[11px] text-muted tabular">
            {scale.ticks.map((t) => (
              <span
                key={t.value}
                className="absolute top-0 whitespace-nowrap"
                style={{
                  left: `${t.at * 100}%`,
                  transform: t.at <= 0 ? "none" : t.at >= 1 ? "translateX(-100%)" : "translateX(-50%)",
                }}
              >
                {t.label}
              </span>
            ))}
          </div>
          <p className="mt-1 text-right text-[11px] text-muted">{scale.log ? `Log scale: each gridline is ${fmtDecade(scale.ratio)}× the last.` : "Linear scale from 0."}</p>
        </div>
      ) : (
        <Empty icon={Coins} title="Nothing to compare yet" className="py-6">
          Break-even appears once the endpoint has recorded uptime and the student has served.
        </Empty>
      )}
      <p className="mt-3 text-pretty text-[12.5px] leading-5 text-ink-2">{caption}</p>
    </Tile>
  );
}

/* ---------------- 4. prices ---------------- */

function gpuRate(gpus: number | undefined, perGpu: number | undefined): string {
  if (perGpu == null) return "–";
  const n = gpus ?? 1;
  return `${n} GPU${n === 1 ? "" : "s"} at ${fmtUsd(perGpu, 2)} per GPU-hour`;
}

/** The model a price belongs to, on its own line under the price (a second `dd` of the same term). */
function ModelName({ id }: { id: string | undefined }) {
  if (!id) return null;
  const [org, ...rest] = id.split("/");
  return (
    <dd className="mt-0.5 basis-full break-all font-mono text-[11.5px] leading-4 text-muted" title={id}>
      {rest.length ? `${org}/` : ""}
      <span className="text-ink-2">{rest.length ? rest.join("/") : id}</span>
    </dd>
  );
}

function PricesTile({ report }: { report: CostReport }) {
  const p = report.prices;
  const verified = p?.verified === true;
  const label = verified ? (p?.label ?? "prices confirmed by the operator") : "unverified list prices";
  const avoided = report.avoided_teacher_usd_per_student_request;
  const hosted = report.hosted_fallback_usd_per_request;
  return (
    <Tile
      title="Prices used"
      sub={verified ? "Confirmed by the operator (RATCHET_PRICES_VERIFIED=1)." : "List prices, unverified until the day-1 checks pass."}
      badge={
        verified ? (
          <Badge tone="good">
            <ShieldCheck aria-hidden /> {label.charAt(0).toUpperCase() + label.slice(1)}
          </Badge>
        ) : (
          <Badge tone="outline">
            <Info aria-hidden /> Unverified list prices
          </Badge>
        )
      }
      className="lg:col-span-5"
    >
      {p ? (
        <dl className="space-y-2 text-[12px] leading-[18px]">
          <div className="flex flex-wrap items-baseline justify-between gap-x-3">
            <dt className="text-muted">Student endpoint</dt>
            <dd className="ml-auto text-right text-ink-2">{gpuRate(p.student_gpus, p.student_gpu_usd_per_hour)}</dd>
          </div>
          <div className="flex flex-wrap items-baseline justify-between gap-x-3">
            <dt className="text-muted">Training jobs</dt>
            <dd className="ml-auto text-right text-ink-2">{gpuRate(p.train_gpus, p.train_gpu_usd_per_hour)}</dd>
          </div>
          <div className="flex flex-wrap items-baseline justify-between gap-x-3">
            <dt className="text-muted">Teacher, per M tokens</dt>
            <dd className="ml-auto text-right text-ink-2 tabular">{fmtTokenPrice(p.teacher_usd_per_m_tokens)}</dd>
            <ModelName id={p.teacher_model} />
          </div>
          <div className="flex flex-wrap items-baseline justify-between gap-x-3">
            <dt className="text-muted">Student, per M tokens</dt>
            <dd className="ml-auto text-right text-ink-2 tabular">{fmtTokenPrice(p.student_usd_per_m_tokens)}</dd>
          </div>
          <div className="flex flex-wrap items-baseline justify-between gap-x-3">
            <dt className="text-muted">Hosted fallback, per M tokens</dt>
            <dd className="ml-auto text-right text-ink-2 tabular">{fmtTokenPrice(p.fallback_student_usd_per_m_tokens)}</dd>
            <ModelName id={p.fallback_student_model} />
          </div>
        </dl>
      ) : (
        <p className="text-[12.5px] text-muted">This gateway did not send its prices.</p>
      )}
      <Rows
        rows={[
          avoided != null && ["Teacher spend one student answer avoids", fmtMoney(avoided)],
          hosted != null && ["Hosted fallback, per student answer", fmtMoney(hosted)],
        ]}
      />
    </Tile>
  );
}

/* ---------------- 5. daily savings ---------------- */

function SwatchKey({ solid }: { solid: boolean }) {
  return <span aria-hidden className={cn("inline-block size-2.5 rounded-[3px]", solid ? "bg-owned" : "border border-owned bg-owned-soft")} />;
}

/** A bar with a rounded data end: the top for savings, the bottom for a loss. */
function barShape(key: "perToken" | "gpuHours", solid: boolean) {
  function SavingsBar(props: BarShapeProps) {
    const x = Number(props.x);
    const w = Number(props.width);
    const y = Number(props.y);
    const h = Number(props.height);
    if (!Number.isFinite(x + w + y + h) || w <= 0 || h === 0) return <g />;
    const top = Math.min(y, y + h);
    const height = Math.abs(h);
    const down = ((props.payload as DailySavings | undefined)?.[key] ?? 0) < 0;
    const r = Math.min(3, w / 2, height);
    const bottom = top + height;
    const d = down
      ? `M${x},${top} H${x + w} V${bottom - r} Q${x + w},${bottom} ${x + w - r},${bottom} H${x + r} Q${x},${bottom} ${x},${bottom - r} Z`
      : `M${x},${bottom} V${top + r} Q${x},${top} ${x + r},${top} H${x + w - r} Q${x + w},${top} ${x + w},${top + r} V${bottom} Z`;
    return <path d={d} fill={solid ? OWNED : "var(--owned-soft)"} stroke={solid ? "none" : OWNED} strokeWidth={solid ? 0 : 1} />;
  }
  return SavingsBar;
}

function DailyTip(p: TooltipContentProps) {
  if (!p.active || !p.payload?.length) return null;
  const d = p.payload[0]?.payload as DailySavings | undefined;
  if (!d) return null;
  return (
    <div className="min-w-[200px] max-w-[300px] rounded-xl border border-line bg-surface px-3 py-2.5 text-[12px] shadow-card">
      <div className="flex items-baseline justify-between gap-4">
        <span className="font-semibold text-ink">Day {d.day}</span>
        <span className="text-muted tabular">{fmtInt(d.requests)} req</span>
      </div>
      <div className="mt-2 space-y-1.5">
        <div className="flex items-center gap-2">
          <SwatchKey solid={false} />
          <span className="font-semibold text-ink tabular">{fmtMoney(d.perToken)}</span>
          <span className="text-muted">per token{d.savings_pct_per_token != null && ` (${fmtSignedPct(d.savings_pct_per_token)})`}</span>
        </div>
        <div className="flex items-center gap-2">
          <SwatchKey solid />
          <span className="font-semibold text-ink tabular">{fmtMoney(d.gpuHours)}</span>
          <span className="text-muted">by GPU-hours{d.savings_pct_gpu_hours != null && ` (${fmtSignedPct(d.savings_pct_gpu_hours)})`}</span>
        </div>
        <div className="border-t border-line pt-1.5 text-muted">
          {fmtInt(d.student_served)} student answers · endpoint {fmtHours(d.endpoint_hours)} ({fmtMoney(d.endpoint_usd)})
          {d.training_usd > 0 && <> · training {fmtMoney(d.training_usd)}</>}
        </div>
      </div>
    </div>
  );
}

function DailySavingsChart({ daily, truncated }: { daily: CostDay[]; truncated: boolean }) {
  const [table, setTable] = useState(false);
  const reduce = useReducedMotion();
  const all = daily.map(dailySavings);
  const shown = all.slice(-CHART_DAYS);
  const last = all[all.length - 1];
  const ticks = signedTicks(Math.min(...shown.map((d) => Math.min(d.perToken, d.gpuHours))), Math.max(...shown.map((d) => Math.max(d.perToken, d.gpuHours))));
  const step = ticks.length > 1 ? ticks[1] - ticks[0] : 1;
  const latestLine = last ? (
    <>
      Day {last.day}: per token <Num>{fmtMoney(last.perToken)}</Num>, by GPU-hours paid{" "}
      <Num>{fmtMoney(last.gpuHours)}</Num>.
    </>
  ) : null;
  const summary = last
    ? `Bar chart of dollars saved against all-teacher each day, per token and by GPU-hours paid, over ${shown.length} day${shown.length === 1 ? "" : "s"}; below zero costs more. Day ${last.day}: per token ${fmtMoney(last.perToken)}, by GPU-hours paid ${fmtMoney(last.gpuHours)}.`
    : "No days yet.";

  return (
    <div className="min-w-0 rounded-xl border border-line px-4 pb-4 pt-3 sm:px-5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-[13px] font-medium text-ink">Saved each day</h3>
          <p className="mt-0.5 text-pretty text-[12px] leading-[18px] text-muted">
            Against sending every request to the teacher. Below zero, that day cost more than all-teacher.
          </p>
        </div>
        {all.length > 0 && <ViewToggle table={table} onChange={setTable} label="savings by day" />}
      </div>
      {all.length === 0 ? (
        <p className="mt-3 rounded-xl border border-dashed border-line-strong bg-bg-sunken/60 px-4 py-6 text-center text-[13px] text-muted">No days with requests or compute yet.</p>
      ) : table ? (
        <div className="mt-3">
          <DataTable caption="Savings against all-teacher by day, per token and by GPU-hours paid" minWidth={720} className="max-h-[420px] overflow-y-auto">
            <thead className="sticky top-0 z-[1] bg-surface">
              <tr>
                <th className={th}>Day</th>
                <th className={thNum}>Requests</th>
                <th className={thNum}>Student answers</th>
                <th className={thNum}>Endpoint</th>
                <th className={thNum}>GPU cost</th>
                <th className={thNum}>All-teacher</th>
                <th className={thNum}>Saved per token</th>
                <th className={thNum}>Saved by GPU-hours</th>
              </tr>
            </thead>
            <tbody>
              {all
                .slice()
                .reverse()
                .map((d) => (
                  <tr key={d.day}>
                    <td className={td}>{d.day}</td>
                    <td className={tdNum}>{fmtInt(d.requests)}</td>
                    <td className={tdNum}>{fmtInt(d.student_served)}</td>
                    <td className={tdNum}>{fmtHours(d.endpoint_hours)}</td>
                    <td className={tdNum} title="Endpoint and training GPU-hours">
                      {fmtMoney(d.endpoint_usd + d.training_usd)}
                    </td>
                    <td className={tdNum}>{fmtMoney(d.baseline_usd)}</td>
                    <td className={tdNum}>
                      <span className="font-medium text-ink">{fmtMoney(d.perToken)}</span>
                      <span className="ml-1.5 text-[11.5px] text-muted">{fmtSignedPct(d.savings_pct_per_token)}</span>
                    </td>
                    <td className={tdNum}>
                      <span className="font-medium text-ink">{fmtMoney(d.gpuHours)}</span>
                      <span className="ml-1.5 text-[11.5px] text-muted">{fmtSignedPct(d.savings_pct_gpu_hours)}</span>
                    </td>
                  </tr>
                ))}
            </tbody>
          </DataTable>
        </div>
      ) : (
        <>
          <ul className="mb-2 mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-ink-2">
            <li className="flex items-center gap-1.5">
              <SwatchKey solid={false} /> Per token
            </li>
            <li className="flex items-center gap-1.5">
              <SwatchKey solid /> By GPU-hours paid
            </li>
          </ul>
          <div role="figure" aria-label={summary} className="-ml-1 select-none" style={{ height: CHART_H }}>
            <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 520, height: CHART_H }}>
              <BarChart data={shown} margin={{ top: 8, right: 8, bottom: 0, left: 0 }} barGap={2} barCategoryGap="24%">
                <CartesianGrid vertical={false} stroke="var(--line)" />
                <XAxis
                  dataKey="day"
                  tickFormatter={(d: number) => `Day ${d}`}
                  tick={AXIS_TICK}
                  tickLine={false}
                  axisLine={false}
                  interval="preserveStartEnd"
                  minTickGap={18}
                />
                <YAxis
                  domain={[ticks[0], ticks[ticks.length - 1]]}
                  ticks={ticks}
                  tickFormatter={(v: number) => fmtAxisMoney(v, step)}
                  tick={AXIS_TICK}
                  tickLine={false}
                  axisLine={false}
                  width={56}
                />
                <ReferenceLine y={0} stroke="var(--line-strong)" strokeWidth={1.25} />
                <Tooltip content={DailyTip} cursor={{ fill: "var(--grid)" }} isAnimationActive={false} wrapperStyle={{ outline: "none", zIndex: 10 }} />
                <Bar dataKey="perToken" name="Per token" maxBarSize={18} shape={barShape("perToken", false)} isAnimationActive={!reduce} animationDuration={600} />
                <Bar dataKey="gpuHours" name="By GPU-hours paid" maxBarSize={18} shape={barShape("gpuHours", true)} isAnimationActive={!reduce} animationDuration={600} />
              </BarChart>
            </ResponsiveContainer>
          </div>
          <p className="mt-2 text-[12px] leading-[18px] text-muted">
            {latestLine}
            {all.length > shown.length && ` The chart shows the latest ${CHART_DAYS} days; the table has all ${fmtInt(all.length)}.`}
          </p>
        </>
      )}
      {truncated && <p className="mt-2 text-[12px] text-muted">The report lists its latest 731 days; the totals above cover the whole window.</p>}
    </div>
  );
}

/* ---------------- the panel ---------------- */

function SimulationNote() {
  return (
    <p className="mt-3 flex items-start gap-1.5 text-[12px] leading-[18px] text-muted">
      <FlaskConical className="mt-0.5 size-3.5 shrink-0" aria-hidden />
      <span>
        Simulation: mock traffic, with the endpoint's uptime (<span className="font-mono">RATCHET_MOCK_ENDPOINT_HOURS</span> a day) and each training job's length
        simulated in <span className="font-mono">sim/</span>. Not measured GPU time.
      </span>
    </p>
  );
}

const DESCRIPTION = "What the student costs per token and by the GPU-hours you pay for, where self-hosting breaks even, and what to do.";

function Full({ report, mock, stale }: { report: CostReport; mock: boolean; stale: string | null }) {
  const baseline = baselineOf(report);
  const endpointOpen = (report.open_intervals ?? []).filter((iv) => iv.kind === "student_endpoint");
  const jobsOpen = (report.open_intervals ?? []).filter((iv) => iv.kind !== "student_endpoint");
  const empty = isEmptyReport(report);
  const win = empty ? null : windowLabel(report);
  const idle = report.idle_hours ?? 0;
  const hours = report.endpoint_hours ?? 0;
  return (
    <Panel id="economics" title="Economics" description={DESCRIPTION} actions={win && <span className="text-[12px] text-muted tabular">{win}</span>}>
      {stale && (
        <p role="status" className="mb-3 flex items-center gap-2 rounded-lg border border-warn/30 bg-warn-soft/60 px-3 py-1.5 text-[12.5px] text-ink-2">
          <TriangleAlert className="size-3.5 shrink-0 text-warn" aria-hidden />
          <span>Could not refresh the cost report ({stale}). Showing the last one that loaded.</span>
        </p>
      )}
      {empty ? (
        <Empty icon={Coins} title="No cost data yet">
          {mock ? (
            "Run a day: the simulator records the endpoint's hours and each training job beside the requests."
          ) : (
            <>
              Send requests, and record the student endpoint's uptime with <span className="font-mono">python -m ratchet.cost endpoint up</span> and{" "}
              <span className="font-mono">down</span> (or <span className="font-mono">POST /ratchet/cost/endpoint</span>).
            </>
          )}
        </Empty>
      ) : (
        <>
          <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
            <RecommendationTile report={report} />
            <ViewTile
              title="Per token"
              sub="The student priced at the hosted Nano's token price. The tiles above and the charts below use this view."
              view={report.views.per_token}
              baseline={baseline}
              fill="owned-soft"
              headline={report.model === "per_token"}
            >
              <Rows
                rows={[
                  report.teacher_usd != null && ["Teacher calls", fmtMoney(report.teacher_usd)],
                  report.student_per_token_usd != null && ["Student, per token", fmtMoney(report.student_per_token_usd)],
                ]}
              />
            </ViewTile>
            <ViewTile
              title="By GPU-hours paid"
              sub="The endpoint and training hours you pay for, busy or idle."
              view={report.views.gpu_hours}
              baseline={baseline}
              fill="owned"
              headline={report.model === "gpu_hours"}
            >
              <Rows
                rows={[
                  report.teacher_usd != null && ["Teacher calls", fmtMoney(report.teacher_usd)],
                  report.endpoint_usd != null && [`Endpoint, ${fmtHours(hours)}`, fmtMoney(report.endpoint_usd)],
                  report.training_usd != null &&
                    (report.training_usd > 0 || (report.training_hours ?? 0) > 0) && [
                      `Training and evals, ${fmtHours(report.training_hours ?? 0)}`,
                      fmtMoney(report.training_usd),
                    ],
                  (report.hosted_student_usd ?? 0) > 0 && [
                    `Student per token on days without uptime (${fmtInt(report.student_served_off_endpoint ?? 0)} answers)`,
                    fmtMoney(report.hosted_student_usd),
                  ],
                ]}
              />
              {(idle > 0 || endpointOpen.length > 0 || jobsOpen.length > 0) && (
                <p className="mt-2 text-pretty text-[12px] leading-[18px] text-muted">
                  {idle > 0 && (
                    <>
                      {fmtHours(idle)} of the endpoint's {fmtHours(hours)}
                      {report.idle_share != null && ` (${fmtSignedPct(report.idle_share)})`} fell on days with no student traffic.{" "}
                    </>
                  )}
                  {endpointOpen.length > 0 && "The endpoint is up now; its hours count up to now. "}
                  {jobsOpen.length > 0 &&
                    (jobsOpen.length === 1
                      ? "A training or eval job is still running; its hours count up to now."
                      : `${jobsOpen.length} training or eval jobs are still running; their hours count up to now.`)}
                </p>
              )}
            </ViewTile>
          </div>

          <div className="mt-3 grid gap-3 lg:grid-cols-12">
            <BreakEven report={report} />
            <PricesTile report={report} />
          </div>

          <div className="mt-3">
            <DailySavingsChart daily={report.daily ?? []} truncated={report.daily_truncated === true} />
          </div>
        </>
      )}
      {mock && <SimulationNote />}
    </Panel>
  );
}

/** When only /ratchet/stats `cost` is available: the headline numbers, and why the full report is missing. */
function Summary({ cost, mock, error }: { cost: CostSummary; mock: boolean; error: string | null }) {
  const pct = cost.gpu_hours_savings_pct;
  return (
    <Panel id="economics" title="Economics" description={DESCRIPTION}>
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3 rounded-xl border border-line bg-bg-sunken/50 p-3.5">
        <RecommendationChip recommendation={cost.recommendation} />
        <dl className="flex flex-wrap gap-x-6 gap-y-2 text-[12.5px]">
          <div>
            <dt className="text-muted">Saved by GPU-hours paid</dt>
            <dd className="mt-0.5 flex items-center gap-1.5 font-medium text-ink tabular">
              {pct != null && pct < 0 && <TrendingDown className="size-3.5 text-warn" aria-hidden />}
              {pct == null ? "no baseline yet" : `${fmtSignedPct(pct)}${pct < 0 ? " (costs more than all-teacher)" : ""}`}
            </dd>
          </div>
          <div>
            <dt className="text-muted">Endpoint hours</dt>
            <dd className="mt-0.5 font-medium text-ink tabular">{fmtHours(cost.endpoint_hours)}</dd>
          </div>
          <div>
            <dt className="text-muted">Break-even</dt>
            <dd className="mt-0.5 font-medium text-ink tabular">
              {cost.break_even_student_requests_per_gpu_hour == null ? "unknown" : `${fmtRate(cost.break_even_student_requests_per_gpu_hour)} student answers / h`}
            </dd>
          </div>
        </dl>
      </div>
      <p className="mt-2 text-[12.5px] text-muted">
        The full report (<span className="font-mono">GET /ratchet/cost</span>) did not load{error ? `: ${error}` : ""}. These numbers come from{" "}
        <span className="font-mono">/ratchet/stats</span>.
      </p>
      {mock && <SimulationNote />}
    </Panel>
  );
}

function Loading() {
  return <div aria-busy="true" aria-label="Loading the cost report" className="h-[420px] animate-pulse rounded-2xl border border-line bg-surface" />;
}

/** Whether the gateway has anything for the panel: the report, or at least the `cost` summary in /ratchet/stats. */
export function hasEconomics(stats: Stats, cost: CostState): boolean {
  return cost.report != null || stats.cost != null;
}

/**
 * The Economics panel (FR-7.20): the full cost report when GET /ratchet/cost answers, the `cost` summary from
 * /ratchet/stats when it does not, and nothing on a gateway that has neither (older than v0.5).
 */
export function EconomicsPanel({ stats, cost }: { stats: Stats; cost: CostState }) {
  if (!hasEconomics(stats, cost)) return null;
  const mock = stats.mode !== "live";
  if (cost.report) return <Full report={cost.report} mock={mock} stale={cost.error} />;
  if (cost.loading && !cost.missing) return <Loading />;
  return stats.cost ? <Summary cost={stats.cost} mock={mock} error={cost.error} /> : null;
}
