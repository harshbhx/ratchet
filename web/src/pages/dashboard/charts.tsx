import { useState, type ReactNode } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  Rectangle,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  type BarShapeProps,
  type TooltipContentProps,
} from "recharts";
import { useReducedMotion } from "motion/react";
import { AlertTriangle, LineChart as LineIcon, ShieldCheck, Undo2 } from "lucide-react";
import type { DayStats } from "@/lib/api";
import { cn, fmtInt, fmtPct } from "@/lib/utils";
import { studentCopy } from "@/lib/who-model";
import { fmtPer1k, fmtShare, latest, niceTicks, POLICY } from "./model";
import { DataTable, Empty, LegendKey, Panel, td, tdNum, th, thNum, ViewToggle } from "./ui";

const OWNED = "var(--owned)";
const RENTED = "var(--rented)";
const AXIS_TICK = { fill: "var(--muted)", fontSize: 11 } as const;
const CHART_H = 236;

function dayTick(d: number) {
  return `Day ${d}`;
}

/* ---------------- shared bits ---------------- */

function TipShell({ title, sub, children }: { title: ReactNode; sub?: ReactNode; children: ReactNode }) {
  return (
    <div className="min-w-[184px] max-w-[280px] rounded-xl border border-line bg-surface px-3 py-2.5 text-[12px] shadow-card">
      <div className="flex items-baseline justify-between gap-4">
        <span className="font-semibold text-ink">{title}</span>
        {sub && <span className="text-muted tabular">{sub}</span>}
      </div>
      <div className="mt-2 space-y-1.5">{children}</div>
    </div>
  );
}

function TipRow({ value, name, keyEl }: { value: ReactNode; name: ReactNode; keyEl?: ReactNode }) {
  return (
    <div className="flex items-center gap-2">
      {keyEl}
      <span className="font-semibold text-ink tabular">{value}</span>
      <span className="text-muted">{name}</span>
    </div>
  );
}

function datumOf<T>(p: TooltipContentProps): T | null {
  if (!p.active || !p.payload?.length) return null;
  return (p.payload[0]?.payload as T) ?? null;
}

function ChartFrame({ summary, children }: { summary: string; children: ReactNode }) {
  return (
    <div role="figure" aria-label={summary} className="-ml-1 select-none" style={{ height: CHART_H }}>
      <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 520, height: CHART_H }}>
        {children as React.ReactElement}
      </ResponsiveContainer>
    </div>
  );
}

function Legend({ children }: { children: ReactNode }) {
  return <ul className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-ink-2">{children}</ul>;
}

function NoTraffic({ action }: { action?: ReactNode }) {
  return (
    <Empty icon={LineIcon} title="No traffic yet" action={action} className="h-[200px]">
      Charts fill in after the first day of requests.
    </Empty>
  );
}

/* ---------------- 1. cost per 1,000 requests ---------------- */

type CostDatum = DayStats & { reduction: number };

function CostTip(p: TooltipContentProps) {
  const d = datumOf<CostDatum>(p);
  if (!d) return null;
  return (
    <TipShell title={dayTick(d.day)} sub={`${fmtInt(d.requests)} req`}>
      <TipRow keyEl={<LegendKey color={OWNED} />} value={fmtPer1k(d.cost_per_1k)} name="Ratchet" />
      <TipRow keyEl={<LegendKey color={RENTED} dashed />} value={fmtPer1k(d.baseline_per_1k)} name="All-teacher" />
      <div className="border-t border-line pt-1.5 text-muted">
        <span className="font-semibold text-ink tabular">{fmtPct(d.savings_pct)}</span> lower than all-teacher
      </div>
    </TipShell>
  );
}

type EndLabelProps = { x?: unknown; y?: unknown; index?: unknown; value?: unknown };

function endLabel(n: number, name: string, show: boolean, offset: number) {
  // Recharts calls this for every point; only the last point gets a direct label.
  function EndLabel(props: EndLabelProps) {
    if (!show || props.index !== n - 1 || props.x == null || props.y == null) return <g />;
    const x = Number(props.x) + 10;
    const y = Number(props.y) + offset;
    return (
      <text x={x} y={y} className="tabular">
        <tspan x={x} dy={-2} fontSize={11} fill="var(--muted)">
          {name}
        </tspan>
        <tspan x={x} dy={14} fontSize={12} fontWeight={600} fill="var(--ink)">
          {fmtPer1k(Number(props.value))}
        </tspan>
      </text>
    );
  }
  return EndLabel;
}

/** `hosted`: the hosted fallback serves the student now, so the legend says "the student", not "your adapter" (FR-7.40). */
export function CostChart({ days, emptyAction, hosted = false }: { days: DayStats[]; emptyAction?: ReactNode; hosted?: boolean }) {
  const who = studentCopy(hosted);
  const [table, setTable] = useState(false);
  const reduce = useReducedMotion();
  const last = latest(days);
  const data: CostDatum[] = days.map((d) => ({ ...d, reduction: d.savings_pct }));
  const yTicks = niceTicks(Math.max(0.05, ...days.map((d) => Math.max(d.baseline_per_1k, d.cost_per_1k))));
  const max = yTicks[yTicks.length - 1];
  // Direct labels only when the two ends are far enough apart not to collide.
  const apart = last ? Math.abs(last.baseline_per_1k - last.cost_per_1k) / max > 0.14 : false;
  const summary = last
    ? `Line chart of cost per 1,000 requests over ${days.length} day${days.length === 1 ? "" : "s"}. Day ${last.day}: Ratchet ${fmtPer1k(last.cost_per_1k)}, all-teacher baseline ${fmtPer1k(last.baseline_per_1k)}, ${fmtPct(last.savings_pct)} lower.`
    : "No traffic yet.";
  const dot = (color: string) => ({ r: 4, fill: color, stroke: "var(--surface)", strokeWidth: 2 });
  const endDot = (color: string) =>
    function EndDot(props: { cx?: number; cy?: number; index?: number }) {
      if (props.index !== days.length - 1 || props.cx == null || props.cy == null) return <g key={props.index} />;
      return <circle key={props.index} cx={props.cx} cy={props.cy} r={4} fill={color} stroke="var(--surface)" strokeWidth={2} />;
    };

  return (
    <Panel
      id="cost"
      title="Cost per 1,000 requests"
      description="What you paid per day, with the student priced per token, against sending every request to the teacher."
      actions={days.length > 0 && <ViewToggle table={table} onChange={setTable} label="cost per 1,000 requests" />}
    >
      {days.length === 0 ? (
        <NoTraffic action={emptyAction} />
      ) : table ? (
        <DataTable caption="Cost per 1,000 requests by day" minWidth={420}>
          <thead>
            <tr>
              <th className={th}>Day</th>
              <th className={thNum}>Requests</th>
              <th className={thNum}>Ratchet</th>
              <th className={thNum}>All-teacher</th>
              <th className={thNum}>Reduction</th>
            </tr>
          </thead>
          <tbody>
            {days.map((d) => (
              <tr key={d.day}>
                <td className={td}>{d.day}</td>
                <td className={tdNum}>{fmtInt(d.requests)}</td>
                <td className={cn(tdNum, "font-medium text-ink")}>{fmtPer1k(d.cost_per_1k)}</td>
                <td className={tdNum}>{fmtPer1k(d.baseline_per_1k)}</td>
                <td className={tdNum}>{fmtPct(d.savings_pct)}</td>
              </tr>
            ))}
          </tbody>
        </DataTable>
      ) : (
        <>
          <Legend>
            <li className="flex items-center gap-1.5">
              <LegendKey color={OWNED} /> Ratchet <span className="text-muted">({who.name} + escalations)</span>
            </li>
            <li className="flex items-center gap-1.5">
              <LegendKey color={RENTED} dashed /> All-teacher baseline
            </li>
          </Legend>
          <ChartFrame summary={summary}>
            <LineChart data={data} margin={{ top: 10, right: apart ? 74 : 16, bottom: 0, left: 0 }}>
              <CartesianGrid vertical={false} stroke="var(--line)" />
              <XAxis
                dataKey="day"
                tickFormatter={dayTick}
                tick={AXIS_TICK}
                tickLine={false}
                axisLine={{ stroke: "var(--line-strong)" }}
                interval="preserveStartEnd"
                minTickGap={18}
                padding={{ left: 12, right: 12 }}
              />
              <YAxis
                domain={[0, max]}
                ticks={yTicks}
                tickFormatter={(v: number) => "$" + v.toFixed(v === 0 ? 0 : 2)}
                tick={AXIS_TICK}
                tickLine={false}
                axisLine={false}
                width={48}
              />
              <Tooltip
                content={CostTip}
                cursor={{ stroke: "var(--line-strong)", strokeWidth: 1 }}
                isAnimationActive={false}
                wrapperStyle={{ outline: "none", zIndex: 10 }}
              />
              <Line
                type="linear"
                dataKey="cost_per_1k"
                name="Ratchet"
                stroke={OWNED}
                strokeWidth={2}
                strokeLinecap="round"
                strokeLinejoin="round"
                dot={endDot(OWNED)}
                activeDot={dot(OWNED)}
                isAnimationActive={!reduce}
                animationDuration={700}
                label={endLabel(days.length, "Ratchet", apart, 0)}
              />
              <Line
                type="linear"
                dataKey="baseline_per_1k"
                name="All-teacher baseline"
                stroke={RENTED}
                strokeWidth={2}
                strokeDasharray="5 4"
                strokeLinecap="round"
                dot={endDot(RENTED)}
                activeDot={dot(RENTED)}
                isAnimationActive={!reduce}
                animationDuration={700}
                label={endLabel(days.length, "All-teacher", apart, 0)}
              />
            </LineChart>
          </ChartFrame>
        </>
      )}
    </Panel>
  );
}

/* ---------------- 2. share answered by the student ---------------- */

type ShareDatum = { day: number; requests: number; student: number; escalated: number; direct: number };
const SHARE_KEYS = ["student", "escalated", "direct"] as const;

function ShareTip(p: TooltipContentProps & { name: string }) {
  const d = datumOf<ShareDatum>(p);
  if (!d) return null;
  return (
    <TipShell title={dayTick(d.day)} sub={`${fmtInt(d.requests)} req`}>
      <TipRow keyEl={<LegendKey color={OWNED} />} value={fmtShare(d.student)} name={p.name} />
      <TipRow keyEl={<LegendKey color={RENTED} />} value={fmtShare(d.escalated)} name="escalated to teacher" />
      <TipRow keyEl={<LegendKey color="var(--teacher-only)" />} value={fmtShare(d.direct)} name="teacher only" />
    </TipShell>
  );
}

/** Stacked segment: 2px surface gap (stroke), rounded data-end only on the topmost visible segment. */
function segShape(key: (typeof SHARE_KEYS)[number]) {
  function Seg(props: BarShapeProps) {
    const d = props.payload as ShareDatum | undefined;
    const idx = SHARE_KEYS.indexOf(key);
    const top = d ? SHARE_KEYS.slice(idx + 1).every((k) => d[k] < 0.0005) : false;
    const { x, y, width, height, fill } = props;
    if (!height || height <= 0) return <g />;
    return (
      <Rectangle
        x={x}
        y={y}
        width={width}
        height={height}
        fill={fill}
        stroke="var(--surface)"
        strokeWidth={2}
        radius={top ? [5, 5, 0, 0] : 0}
      />
    );
  }
  return Seg;
}

/** `hosted`: the hosted fallback serves the student now, so the chart says "the student", not "your adapter" (FR-7.40). */
export function ShareChart({ days, emptyAction, hosted = false }: { days: DayStats[]; emptyAction?: ReactNode; hosted?: boolean }) {
  const who = studentCopy(hosted);
  const [table, setTable] = useState(false);
  const reduce = useReducedMotion();
  const data: ShareDatum[] = days.map((d) => ({
    day: d.day,
    requests: d.requests,
    student: d.student_share,
    escalated: d.escalation_rate,
    direct: (() => {
      const r = 1 - d.student_share - d.escalation_rate;
      return r < 0.0005 ? 0 : r;
    })(),
  }));
  const last = latest(data);
  const summary = last
    ? `Stacked bar chart of who answered each day over ${data.length} day${data.length === 1 ? "" : "s"}. Day ${last.day}: ${who.name} ${fmtShare(last.student)}, escalated to the teacher ${fmtShare(last.escalated)}, teacher only ${fmtShare(last.direct)}.`
    : "No traffic yet.";

  return (
    <Panel
      id="share"
      title={`Share answered ${who.byShort}`}
      description="Who answered each day's requests."
      actions={days.length > 0 && <ViewToggle table={table} onChange={setTable} label={`share answered ${who.byShort}`} />}
    >
      {days.length === 0 ? (
        <NoTraffic action={emptyAction} />
      ) : table ? (
        <DataTable caption={`Share of requests answered ${who.byShort}, by day`} minWidth={420}>
          <thead>
            <tr>
              <th className={th}>Day</th>
              <th className={thNum}>{who.short}</th>
              <th className={thNum}>Escalated</th>
              <th className={thNum}>Teacher only</th>
            </tr>
          </thead>
          <tbody>
            {data.map((d) => (
              <tr key={d.day}>
                <td className={td}>{d.day}</td>
                <td className={cn(tdNum, "font-medium text-ink")}>{fmtShare(d.student)}</td>
                <td className={tdNum}>{fmtShare(d.escalated)}</td>
                <td className={tdNum}>{fmtShare(d.direct)}</td>
              </tr>
            ))}
          </tbody>
        </DataTable>
      ) : (
        <>
          <Legend>
            <li className="flex items-center gap-1.5">
              <LegendKey color={OWNED} kind="rect" /> {who.short}
            </li>
            <li className="flex items-center gap-1.5">
              <LegendKey color={RENTED} kind="rect" /> Escalated to teacher
            </li>
            <li className="flex items-center gap-1.5">
              <LegendKey color="var(--teacher-only)" kind="rect" /> Teacher only <span className="text-muted">(no student yet, or pinned)</span>
            </li>
          </Legend>
          <ChartFrame summary={summary}>
            <BarChart data={data} margin={{ top: 10, right: 8, bottom: 0, left: 0 }} barCategoryGap="28%">
              <CartesianGrid vertical={false} stroke="var(--line)" />
              <XAxis
                dataKey="day"
                tickFormatter={dayTick}
                tick={AXIS_TICK}
                tickLine={false}
                axisLine={{ stroke: "var(--line-strong)" }}
                interval="preserveStartEnd"
                minTickGap={18}
              />
              <YAxis
                domain={[0, 1]}
                ticks={[0, 0.25, 0.5, 0.75, 1]}
                tickFormatter={(v: number) => Math.round(v * 100) + "%"}
                tick={AXIS_TICK}
                tickLine={false}
                axisLine={false}
                width={44}
              />
              <Tooltip
                content={(tp) => <ShareTip {...tp} name={who.name} />}
                cursor={{ fill: "var(--grid)" }}
                isAnimationActive={false}
                wrapperStyle={{ outline: "none", zIndex: 10 }}
              />
              <Bar dataKey="student" stackId="s" fill={OWNED} maxBarSize={28} shape={segShape("student")} isAnimationActive={!reduce} animationDuration={600} />
              <Bar dataKey="escalated" stackId="s" fill={RENTED} maxBarSize={28} shape={segShape("escalated")} isAnimationActive={!reduce} animationDuration={600} />
              <Bar dataKey="direct" stackId="s" fill="var(--teacher-only)" maxBarSize={28} shape={segShape("direct")} isAnimationActive={!reduce} animationDuration={600} />
            </BarChart>
          </ChartFrame>
        </>
      )}
    </Panel>
  );
}

/* ---------------- 3. audited agreement vs the rollback floor ---------------- */

type PrecDatum = { day: number; requests: number; agreement: number | null };

function PrecTip(p: TooltipContentProps & { rollbacks?: { day: number; label: string }[] }) {
  const d = datumOf<PrecDatum>(p);
  if (!d) return null;
  const below = d.agreement != null && d.agreement < POLICY.liveFloor;
  return (
    <TipShell title={dayTick(d.day)} sub={`${fmtInt(d.requests)} req`}>
      {d.agreement == null ? (
        <p className="text-muted">No audits this day (no student answers yet).</p>
      ) : (
        <>
          <TipRow keyEl={<LegendKey color="var(--ink-2)" />} value={fmtPct(d.agreement, 1)} name="audited agreement" />
          <div className="flex items-center gap-1.5 border-t border-line pt-1.5 text-muted">
            {below ? <AlertTriangle className="size-3.5 text-warn" aria-hidden /> : <ShieldCheck className="size-3.5 text-good" aria-hidden />}
            {below ? "Below" : "Above"} the {fmtPct(POLICY.liveFloor)} floor
          </div>
        </>
      )}
      {p.rollbacks
        ?.filter((r) => r.day === d.day)
        .map((r) => (
          <div key={r.label} className="flex items-center gap-1.5 text-ink-2">
            <Undo2 className="size-3.5 text-bad" aria-hidden /> <span>{r.label}: its canary audits failed</span>
          </div>
        ))}
    </TipShell>
  );
}

export function PrecisionChart({ days, rollbacks = [], emptyAction }: { days: DayStats[]; rollbacks?: { day: number; label: string }[]; emptyAction?: ReactNode }) {
  const [table, setTable] = useState(false);
  const reduce = useReducedMotion();
  const data: PrecDatum[] = days.map((d) => ({ day: d.day, requests: d.requests, agreement: d.audit_agreement }));
  const audited = data.filter((d) => d.agreement != null) as { day: number; agreement: number }[];
  const minVal = audited.length ? Math.min(...audited.map((d) => d.agreement)) : 1;
  const lo = Math.min(0.85, Math.floor((minVal - 0.03) * 20) / 20);
  const ticks: number[] = [];
  for (let t = Math.round(lo * 100); t <= 100; t += lo < 0.7 ? 10 : 5) ticks.push(t / 100);
  if (!ticks.includes(POLICY.liveFloor)) ticks.push(POLICY.liveFloor);
  ticks.sort((a, b) => a - b);
  const lastA = latest(audited);
  const summary = lastA
    ? `Line chart of audited agreement between the student (Nano with your adapter) and the teacher, ${audited.length} audited day${audited.length === 1 ? "" : "s"}. Day ${lastA.day}: ${fmtPct(lastA.agreement, 1)}; rollback floor ${fmtPct(POLICY.liveFloor)}.`
    : "No audits yet.";

  return (
    <Panel
      id="precision"
      title="Audited agreement"
      description={
        <>
          Daily share of audited student answers the teacher agreed with, across the versions serving. The gate rolls back a canary whose own audits are
          clearly under {fmtPct(POLICY.liveFloor)}.
        </>
      }
      actions={audited.length > 0 && <ViewToggle table={table} onChange={setTable} label="audited agreement" />}
    >
      {days.length === 0 ? (
        <NoTraffic action={emptyAction} />
      ) : audited.length === 0 ? (
        <Empty icon={ShieldCheck} title="No audits yet" className="h-[200px]">
          Audits start once a student version serves traffic. Until then the teacher answers everything.
        </Empty>
      ) : table ? (
        <DataTable caption="Audited agreement by day" minWidth={320}>
          <thead>
            <tr>
              <th className={th}>Day</th>
              <th className={thNum}>Requests</th>
              <th className={thNum}>Audited agreement</th>
              <th className={th}>vs {fmtPct(POLICY.liveFloor)} floor</th>
            </tr>
          </thead>
          <tbody>
            {data.map((d) => (
              <tr key={d.day}>
                <td className={td}>{d.day}</td>
                <td className={tdNum}>{fmtInt(d.requests)}</td>
                <td className={cn(tdNum, "font-medium text-ink")}>{d.agreement == null ? "no audits" : fmtPct(d.agreement, 1)}</td>
                <td className={td}>{d.agreement == null ? "–" : d.agreement < POLICY.liveFloor ? "below" : "above"}</td>
              </tr>
            ))}
          </tbody>
        </DataTable>
      ) : (
        <ChartFrame summary={summary}>
          <LineChart data={data} margin={{ top: 10, right: 16, bottom: 0, left: 0 }}>
            <CartesianGrid vertical={false} stroke="var(--line)" />
            <XAxis
              dataKey="day"
              tickFormatter={dayTick}
              tick={AXIS_TICK}
              tickLine={false}
              axisLine={{ stroke: "var(--line-strong)" }}
              interval="preserveStartEnd"
              minTickGap={18}
              padding={{ left: 12, right: 12 }}
            />
            <YAxis
              domain={[lo, 1]}
              ticks={ticks}
              tickFormatter={(v: number) => Math.round(v * 100) + "%"}
              tick={AXIS_TICK}
              tickLine={false}
              axisLine={false}
              width={44}
              allowDataOverflow
            />
            <ReferenceLine
              y={POLICY.liveFloor}
              stroke="var(--muted)"
              strokeDasharray="3 4"
              strokeWidth={1.25}
              ifOverflow="extendDomain"
              label={{ value: `Rollback floor ${fmtPct(POLICY.liveFloor)}`, position: "insideBottomRight", fill: "var(--muted)", fontSize: 11, dy: -4 }}
            />
            {rollbacks.map((r) => (
              <ReferenceLine
                key={`rb-${r.day}-${r.label}`}
                x={r.day}
                stroke="var(--muted)"
                strokeWidth={1}
                strokeDasharray="2 3"
                label={{ value: r.label, position: "insideBottomLeft", fill: "var(--ink-2)", fontSize: 11, dx: 2, dy: -6 }}
              />
            ))}
            <Tooltip
              content={(tp) => <PrecTip {...tp} rollbacks={rollbacks} />}
              cursor={{ stroke: "var(--line-strong)", strokeWidth: 1 }}
              isAnimationActive={false}
              wrapperStyle={{ outline: "none", zIndex: 10 }}
            />
            <Line
              type="linear"
              dataKey="agreement"
              name="Audited agreement"
              stroke="var(--ink-2)"
              strokeWidth={2}
              strokeLinecap="round"
              strokeLinejoin="round"
              connectNulls={false}
              dot={{ r: 3.5, fill: "var(--ink-2)", stroke: "var(--surface)", strokeWidth: 2 }}
              activeDot={{ r: 5, fill: "var(--ink)", stroke: "var(--surface)", strokeWidth: 2 }}
              isAnimationActive={!reduce}
              animationDuration={700}
            />
          </LineChart>
        </ChartFrame>
      )}
    </Panel>
  );
}
