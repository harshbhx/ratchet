/**
 * Pure helpers for the Economics panel (GET /ratchet/cost, ADR 0023): formatting, the daily savings in dollars, and
 * the scales the break-even meter and the daily chart use. Every number comes from the report; nothing is estimated.
 */
import type { CostDay, CostReport, Recommendation } from "@/lib/api";
import { fmtUsd } from "@/lib/utils";
import type { Tone } from "./model";

const MINUS = "−";

/** Signed dollars that never hide a sign: `−$147.93`, `$1.71`, `$0.00099`, `$0`. */
export function fmtMoney(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "–";
  if (v === 0) return "$0";
  const a = Math.abs(v);
  return (v < 0 ? MINUS : "") + fmtUsd(a, a >= 1 ? 2 : undefined);
}

/** A share that may be far below −100%: `26%`, `−2,217%`, `0.4%`. */
export function fmtSignedPct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "–";
  const p = v * 100;
  const a = Math.abs(p);
  const digits = a > 0 && a < 10 ? 1 : 0;
  const s = a.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return (p < 0 && s !== "0" && s !== "0.0" ? MINUS : "") + s + "%";
}

/** Requests per hour, as the report's explanation prints them: whole numbers from 10 up, one decimal below. */
export function fmtRate(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "–";
  const d = Math.abs(v) >= 10 ? 0 : 1;
  return v.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
}

/** Hours: `96 h`, `0.6 h`, `1,250 h`. */
export function fmtHours(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "–";
  const d = Math.abs(v) >= 100 || Number.isInteger(v) ? 0 : 1;
  return v.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }) + " h";
}

/** Per million tokens: `$1.00 in · $3.00 out`. */
export function fmtTokenPrice(p: { input: number; output: number } | undefined): string {
  if (!p) return "–";
  return `${fmtUsd(p.input, 2)} in · ${fmtUsd(p.output, 2)} out`;
}

export type RecMeta = { tone: Tone; label: string };

/** The recommendation as a status chip: a word (and an icon, in the component) on every tone. */
export const REC_META: Record<string, RecMeta> = {
  self_host: { tone: "good", label: "Self-host" },
  hosted_fallback: { tone: "warn", label: "Use the hosted fallback" },
  scale_to_zero: { tone: "warn", label: "Scale to zero when idle" },
  insufficient_data: { tone: "outline", label: "Not enough data" },
};

export function recMeta(r: Recommendation): RecMeta {
  return REC_META[r] ?? { tone: "neutral", label: r.replace(/_/g, " ") };
}

/** True when the report has nothing in it yet: no requests, no endpoint hours, no training time. */
export function isEmptyReport(r: CostReport): boolean {
  return !(r.requests ?? 0) && !(r.endpoint_hours ?? 0) && !(r.training_hours ?? 0) && (r.daily ?? []).length === 0;
}

/** The all-teacher baseline of the window. `views.*.savings_usd` is defined as baseline − total, so either gives it. */
export function baselineOf(r: CostReport): number {
  return r.baseline_usd ?? r.views.per_token.total_usd + r.views.per_token.savings_usd;
}

export type DailySavings = CostDay & {
  perTokenCost: number;
  gpuCost: number;
  /** Dollars saved against all-teacher; negative when that day cost more. */
  perToken: number;
  gpuHours: number;
};

/**
 * One report day in dollars, both ways. Mirrors `ratchet/cost.py` `_daily`: per token is teacher spend + the student's
 * per-token spend; by GPU-hours it is teacher spend + endpoint and training GPU-hours + the student's per-token spend on
 * a day with no recorded endpoint uptime. Dollars rather than the report's shares, because a day with endpoint hours and
 * no requests has a cost but no baseline (its share is null). tests/test_contract.py::test_cost_report_contract checks
 * these sums against the report's shares.
 */
export function dailySavings(d: CostDay): DailySavings {
  const perTokenCost = d.teacher_usd + d.student_per_token_usd;
  const gpuCost = d.teacher_usd + d.endpoint_usd + d.training_usd + (d.endpoint_hours > 0 ? 0 : d.student_per_token_usd);
  return { ...d, perTokenCost, gpuCost, perToken: d.baseline_usd - perTokenCost, gpuHours: d.baseline_usd - gpuCost };
}

/** A round step (1, 2, 2.5 or 5 × 10^k) that splits `span` into at most `n` intervals. */
export function niceStep(span: number, n = 5): number {
  if (!(span > 0) || !Number.isFinite(span)) return 1;
  const raw = span / n;
  const k = Math.pow(10, Math.floor(Math.log10(raw)));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * k >= raw) return m * k;
  return 10 * k;
}

/** Ticks from below `min` to above `max`, always including 0, for a chart whose values can be negative. */
export function signedTicks(min: number, max: number, n = 5): number[] {
  const lo = Math.min(0, min);
  const hi = Math.max(0, max);
  const step = niceStep(hi - lo || 1, n);
  const a = Math.floor(lo / step + 1e-9) * step;
  const b = Math.ceil(hi / step - 1e-9) * step;
  const out: number[] = [];
  for (let t = a; t <= b + step / 2; t += step) out.push(+t.toPrecision(12));
  if (out.length < 2) out.push(+(a + step).toPrecision(12));
  return out;
}

export type MeterTick = { value: number; at: number; label: string };
export type MeterScale = { log: boolean; at: (v: number) => number; ticks: MeterTick[] };

/** `1`, `10`, `100`, `1k`, `10k`, `100k`, `1M`. */
export function fmtDecade(v: number): string {
  if (v >= 1e6) return `${+(v / 1e6).toPrecision(3)}M`;
  if (v >= 1e3) return `${+(v / 1e3).toPrecision(3)}k`;
  return `${+v.toPrecision(3)}`;
}

/**
 * The break-even meter's scale. A log scale (decades) when the values span 20× or more, as observed volume and
 * break-even usually do, so both stay readable; a linear one from 0 otherwise. `at` maps a value to 0..1; zero and
 * negative values sit at the left end of a log scale.
 */
export function meterScale(values: Array<number | null | undefined>): MeterScale | null {
  const vs = values.filter((v): v is number => v != null && Number.isFinite(v) && v >= 0);
  const pos = vs.filter((v) => v > 0);
  if (pos.length === 0) return null;
  const min = Math.min(...pos);
  const max = Math.max(...pos);
  if (max / min >= 20) {
    const lo = Math.floor(Math.log10(min));
    let hi = Math.ceil(Math.log10(max));
    if (hi === lo) hi = lo + 1;
    const span = hi - lo;
    const at = (v: number) => (v <= 0 ? 0 : Math.min(1, Math.max(0, (Math.log10(v) - lo) / span)));
    const every = span > 6 ? 2 : 1;
    const ticks: MeterTick[] = [];
    for (let e = lo; e <= hi; e += every) ticks.push({ value: 10 ** e, at: (e - lo) / span, label: fmtDecade(10 ** e) });
    return { log: true, at, ticks };
  }
  const step = niceStep(max * 1.1, 4);
  const top = Math.ceil((max * 1.1) / step) * step;
  const at = (v: number) => Math.min(1, Math.max(0, v / top));
  const ticks: MeterTick[] = [];
  const digits = step >= 1 ? 0 : step >= 0.1 ? 1 : 2;
  for (let i = 0; i * step <= top + step / 2; i++) {
    const t = +(i * step).toPrecision(12);
    ticks.push({ value: t, at: t / top, label: t.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits }) });
  }
  return { log: false, at, ticks };
}

/** "Days 1–4", "Day 4": the days the report covers, from its window and its first daily row. */
export function windowLabel(r: CostReport): string | null {
  const until = r.window?.until_day;
  if (until == null) return null;
  const first = r.window?.since_day ?? r.daily?.[0]?.day ?? until;
  return first >= until ? `Day ${until}` : `Days ${first}–${until}`;
}

/** Split a report sentence on `code` spans, so commands render as code (as React text, never as HTML). */
export function codeParts(text: string): Array<{ code: boolean; text: string }> {
  return text
    .split(/(`[^`]+`)/g)
    .filter((p) => p !== "")
    .map((p) => (p.length > 2 && p.startsWith("`") && p.endsWith("`") ? { code: true, text: p.slice(1, -1) } : { code: false, text: p }));
}
