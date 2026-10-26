import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { SIM_DAYS, type SimDay } from "@/lib/sim";
import { fmtPct, fmtUsd } from "@/lib/utils";

/**
 * The "Watch it learn" cost chart, in its own module so recharts loads after the hero has painted: the story page imports
 * it with React.lazy instead of shipping the chart library in its first bundle.
 */

const LAST = SIM_DAYS.length;

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
      <div className="mt-1.5 border-t border-line pt-1.5 text-muted">{fmtPct(d.student)} answered by your adapter</div>
    </div>
  );
}

export default function CostChart({ day, onPick }: { day: number; onPick: (d: number) => void }) {
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
