/**
 * Pure helpers for the story's "honest bill" chapter: what it prints from a cost report (GET /ratchet/cost, ADR 0023),
 * whether this gateway has one. Every number comes
 * from the report; nothing here estimates one. No JSX, so `web/tests/story-model.test.mjs` can import it under Node.
 */
import type { CostReport, Recommendation } from "@/lib/api";
import { baselineOf, fmtMoney, fmtRate, fmtSignedPct, idleBuckets, meterScale, recMeta, type MeterScale } from "../dashboard/economics-model";

export interface BillView {
  total: number;
  /** Baseline less total: negative when Ratchet cost more than all-teacher (never clamped). */
  savings: number;
  pct: number | null;
}

/** A cost report reduced to what the chapter prints. A field the gateway did not send is null, never 0. */
export interface Bill {
  baseline: number;
  perToken: BillView;
  gpuHours: BillView;
  teacherUsd: number | null;
  studentTokensUsd: number | null;
  endpointUsd: number | null;
  endpointHours: number | null;
  trainingUsd: number | null;
  trainingHours: number | null;
  hostedStudentUsd: number | null;
  /** The whole endpoint's USD per hour: the rate break-even divides (as recorded), else the configured price times GPUs. */
  endpointPerHour: number | null;
  gpus: number | null;
  avoided: number | null;
  breakEven: number | null;
  hostedPerAnswer: number | null;
  crossover: number | null;
  observed: number | null;
  utilization: number | null;
  idleShare: number | null;
  /** "in hours" (live ledger) or "on days": how idle time and off-endpoint answers were counted. */
  idleBuckets: "in hours" | "on days";
  recommendation: Recommendation;
  explanation: string | null;
  verified: boolean;
  teacherModel: string | null;
}

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

function view(v: { total_usd: number; savings_usd: number; savings_pct: number | null }): BillView {
  return { total: v.total_usd, savings: v.savings_usd, pct: num(v.savings_pct) };
}

function endpointRate(r: CostReport): number | null {
  // Break-even and the crossover divide the rate the window's intervals recorded, so the formula the chapter prints
  // gives the report's result also when an interval was recorded at another price than the configured one.
  const recorded = num(r.endpoint_usd_per_hour);
  if (recorded != null) return recorded;
  const p = r.prices;
  const whole = num(p?.student_endpoint_usd_per_hour);
  if (whole != null) return whole;
  const per = num(p?.student_gpu_usd_per_hour);
  return per == null ? null : per * (num(p?.student_gpus) ?? 1);
}

export function billOf(r: CostReport): Bill {
  return {
    baseline: baselineOf(r),
    perToken: view(r.views.per_token),
    gpuHours: view(r.views.gpu_hours),
    teacherUsd: num(r.teacher_usd),
    studentTokensUsd: num(r.student_per_token_usd),
    endpointUsd: num(r.endpoint_usd),
    endpointHours: num(r.endpoint_hours),
    trainingUsd: num(r.training_usd),
    trainingHours: num(r.training_hours),
    hostedStudentUsd: num(r.hosted_student_usd),
    endpointPerHour: endpointRate(r),
    gpus: num(r.prices?.student_gpus),
    avoided: num(r.avoided_teacher_usd_per_student_request),
    breakEven: num(r.break_even_student_requests_per_gpu_hour),
    hostedPerAnswer: num(r.hosted_fallback_usd_per_request),
    crossover: num(r.self_host_beats_hosted_above_requests_per_hour),
    observed: num(r.observed_student_requests_per_endpoint_hour),
    utilization: num(r.utilization_vs_break_even),
    idleShare: num(r.idle_share),
    idleBuckets: idleBuckets(r),
    recommendation: r.recommendation,
    explanation: typeof r.explanation === "string" && r.explanation.trim() ? r.explanation : null,
    verified: r.prices?.verified === true,
    teacherModel: typeof r.prices?.teacher_model === "string" ? r.prices.teacher_model : null,
  };
}

/** True for something shaped like a cost report: both views with finite totals and savings, and a recommendation. */
export function isCostReport(r: unknown): r is CostReport {
  const v = (r as CostReport | null)?.views;
  const ok = (x: unknown) => num((x as { total_usd?: unknown } | null)?.total_usd) != null && num((x as { savings_usd?: unknown }).savings_usd) != null;
  return !!v && ok(v.per_token) && ok(v.gpu_hours) && typeof (r as CostReport).recommendation === "string";
}

/**
 * True when this gateway's report can tell the story by itself: it has requests, recorded endpoint hours and a
 * break-even volume. Otherwise the chapter says there is no break-even yet.
 */
export function tellsTheStory(r: CostReport | null | undefined): boolean {
  if (!r || !isCostReport(r)) return false;
  return (num(r.requests) ?? 0) > 0 && (num(r.endpoint_hours) ?? 0) > 0 && num(r.break_even_student_requests_per_gpu_hour) != null;
}

/** "all-Ultra" when the teacher is Nemotron 3 Ultra (the story's name for it), "all-teacher" otherwise. */
export function baselineName(teacherModel: string | null): string {
  return teacherModel && /ultra/i.test(teacherModel) ? "all-Ultra" : "all-teacher";
}

/** How many times the baseline: `17×`, `1.4×`, `2×`, `0.5×`. */
export function fmtTimes(x: number | null | undefined): string {
  if (x == null || !Number.isFinite(x)) return "–";
  const a = Math.abs(x);
  const s = a >= 10 ? Math.round(a).toLocaleString("en-US") : String(+a.toFixed(1));
  return `${x < 0 ? "−" : ""}${s}×`;
}

/**
 * Dollars with `sig` significant digits below $1, so per-answer prices stay readable: `$0.00098`, `$0.000030`, `$1.55`.
 * The break-even formula prints its divisor with three (`$0.000984`), so the division it shows gives the result beside it.
 */
export function fmtSmallUsd(v: number | null | undefined, sig = 2): string {
  if (v == null || !Number.isFinite(v)) return "–";
  if (v === 0) return "$0";
  const a = Math.abs(v);
  const digits = a >= 1 ? 2 : Math.min(12, Math.max(2, sig - 1 - Math.floor(Math.log10(a))));
  return (v < 0 ? "−" : "") + "$" + a.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

/** A rounded figure for "about N a day": three significant digits, `37,800`, `37,500`, `1,240`. */
export function fmtAbout(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "–";
  if (v === 0) return "0";
  const k = Math.pow(10, Math.max(0, Math.floor(Math.log10(Math.abs(v))) - 2));
  return (Math.round(v / k) * k).toLocaleString("en-US", { maximumFractionDigits: 0 });
}

export type VerdictKind = "less" | "more" | "same" | "unknown";

/** One view against the baseline in a few words: `69% less than all-Ultra`, `17× the all-Ultra bill`. */
export function verdict(v: BillView, baseline: number, name: string): { kind: VerdictKind; text: string } {
  if (!(baseline > 0)) return { kind: "unknown", text: "No baseline yet" };
  if (v.savings > 0) return { kind: "less", text: `${fmtSignedPct(v.pct ?? v.savings / baseline)} less than ${name}` };
  if (v.savings < 0) return { kind: "more", text: `${fmtTimes(v.total / baseline)} the ${name} bill` };
  return { kind: "same", text: `The same as ${name}` };
}

export type BarPart = { key: string; label: string; usd: number; fill: "rented" | "owned" | "owned-soft" };
export type BarRow = { key: "baseline" | "per_token" | "gpu_hours"; label: string; total: number; parts: BarPart[] };

/**
 * The three bills on one dollar scale: all-teacher, Ratchet with the student priced per token, and Ratchet with the
 * student priced by the GPU-hours it ran. A total the report does not break down is drawn as one part.
 */
export function barRows(b: Bill, name: string): BarRow[] {
  const known = (parts: Array<BarPart | null>, total: number, fallback: BarPart): BarPart[] => {
    const ps = parts.filter((p): p is BarPart => p != null);
    return ps.length === parts.length ? ps.filter((p) => p.usd > 0) : [{ ...fallback, usd: total }];
  };
  const part = (key: string, label: string, usd: number | null, fill: BarPart["fill"]): BarPart | null =>
    usd == null ? null : { key, label, usd, fill };
  return [
    { key: "baseline", label: name.charAt(0).toUpperCase() + name.slice(1), total: b.baseline, parts: [{ key: "teacher", label: "Teacher answers everything", usd: b.baseline, fill: "rented" }] },
    {
      key: "per_token",
      label: "Ratchet, student priced per token",
      total: b.perToken.total,
      parts: known(
        [part("teacher", "Teacher calls", b.teacherUsd, "rented"), part("student", "Student tokens", b.studentTokensUsd, "owned-soft")],
        b.perToken.total,
        { key: "total", label: "Total", usd: 0, fill: "owned-soft" },
      ),
    },
    {
      key: "gpu_hours",
      label: "Ratchet, student priced by GPU-hours paid",
      total: b.gpuHours.total,
      parts: known(
        [
          part("teacher", "Teacher calls", b.teacherUsd, "rented"),
          part("endpoint", "Endpoint hours", b.endpointUsd, "owned"),
          part("training", "Training", b.trainingUsd, "owned-soft"),
          part("hosted", "Hosted student tokens", b.hostedStudentUsd, "owned-soft"),
        ],
        b.gpuHours.total,
        { key: "total", label: "Total", usd: 0, fill: "owned" },
      ),
    },
  ];
}

/** Each part's width in percent of `max`; a non-zero part keeps at least a sliver so it never disappears. */
export function partWidths(parts: BarPart[], max: number, sliver = 0.6): number[] {
  if (!(max > 0)) return parts.map(() => 0);
  return parts.map((p) => (p.usd > 0 ? Math.max(sliver, (p.usd / max) * 100) : 0));
}

export type MeterMark = "dot" | "tick" | "ring";
export type MeterRow = { key: string; label: string; value: number | null; mark: MeterMark };

/** The break-even meter: this traffic, break-even and the hosted crossover, and the scale that fits them. */
export function meter(b: Bill): { rows: MeterRow[]; scale: MeterScale | null } {
  const rows: MeterRow[] = [
    { key: "observed", label: "This traffic: student answers per endpoint hour", value: b.observed, mark: "dot" },
    { key: "break_even", label: "Break-even: where the endpoint pays for itself", value: b.breakEven, mark: "tick" },
  ];
  if (b.crossover != null) rows.push({ key: "crossover", label: "Above this, it also beats the hosted Nano", value: b.crossover, mark: "ring" });
  return { rows, scale: meterScale(rows.map((r) => r.value)) };
}

/** The meter as one sentence, for screen readers and as the chart's text alternative. */
export function meterSummary(rows: MeterRow[], log: boolean): string {
  const parts = rows.map((r) => `${r.label}: ${r.value == null ? "unknown" : `${fmtRate(r.value)} an hour`}`);
  return `Break-even meter${log ? " on a log scale" : ""}. ${parts.join("; ")}.`;
}

export type Outcome = { key: Recommendation; label: string; when: string; current: boolean };

const OUTCOMES: Array<{ key: Recommendation; when: string }> = [
  { key: "self_host", when: "This traffic is at or above both break-even and the hosted crossover." },
  { key: "hosted_fallback", when: "Below the crossover, the hosted Nano on Token Factory, billed per token, costs less than a GPU." },
  { key: "scale_to_zero", when: "The endpoint sat idle more than half its hours, or at this volume neither it nor the hosted Nano pays: run it fewer hours." },
  { key: "insufficient_data", when: "No endpoint hours recorded, or no student answers yet to judge by." },
];

/** The four recommendations the report can make (ADR 0023), with the one this report made marked. */
export function outcomes(current: Recommendation): Outcome[] {
  return OUTCOMES.map((o) => ({ ...o, label: recMeta(o.key).label, current: o.key === current }));
}

/** A total and its breakdown as text, for the bars' screen-reader table: `$380.34 (teacher calls $6.26, ...)`. */
export function rowText(row: BarRow): string {
  const parts = row.parts.length > 1 ? ` (${row.parts.map((p) => `${p.label.toLowerCase()} ${fmtMoney(p.usd)}`).join(", ")})` : "";
  return `${fmtMoney(row.total)}${parts}`;
}
