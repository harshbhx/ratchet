/**
 * Pure helpers for the dashboard: derived series, event and state metadata, formatting.
 * Everything here works from `Stats` alone so the page never invents numbers.
 */
import type { DayStats, LedgerEvent, Stats, Version, VersionState } from "@/lib/api";

export const PINNED_FALLBACK = new Set(["research-plan", "research-write"]);

/** Release policy numbers (from ratchet/release.py), shown as gate rules in the pipeline. */
export const POLICY = {
  minPrecision: 0.95,
  minCoverage: 0.25,
  shadowMin: 100,
  auditsPerStage: 20,
  liveFloor: 0.92,
};

export function latest<T>(xs: T[]): T | undefined {
  return xs.length ? xs[xs.length - 1] : undefined;
}

export function prev<T>(xs: T[]): T | undefined {
  return xs.length > 1 ? xs[xs.length - 2] : undefined;
}

/** Cumulative $ saved by end of each day: Σ (baseline − cost) per 1k × requests / 1000. */
export function cumulativeSaved(days: DayStats[]): number[] {
  let acc = 0;
  return days.map((d) => {
    acc += ((d.baseline_per_1k - d.cost_per_1k) * d.requests) / 1000;
    return acc;
  });
}

export function liveVersion(versions: Version[]): Version | undefined {
  return versions.filter((v) => v.state === "live").pop();
}

export function inFlight(versions: Version[]): Version[] {
  return versions.filter((v) => v.state === "shadow" || v.state === "canary" || v.state === "candidate");
}

/** `$0.17` per 1k: two decimals above ten cents, three below. */
export function fmtPer1k(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "–";
  return "$" + v.toFixed(v >= 0.1 ? 2 : 3);
}

/** Signed percentage-point delta between two shares: `+12 pts`. */
export function fmtPts(delta: number): string {
  const pts = Math.round(delta * 100);
  if (pts === 0) return "±0 pts";
  return (pts > 0 ? "+" : "−") + Math.abs(pts) + " pts";
}

export function fmtTime(ts: number): string {
  return new Date(ts).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function stateLabel(v: Pick<Version, "state" | "traffic_pct">): string {
  switch (v.state) {
    case "canary":
      return `Canary ${v.traffic_pct}%`;
    case "rolled_back":
      return "Rolled back";
    default:
      return v.state.charAt(0).toUpperCase() + v.state.slice(1);
  }
}

export type Tone = "neutral" | "owned" | "rented" | "good" | "warn" | "bad" | "outline";

export const STATE_TONE: Record<VersionState, Tone> = {
  live: "good",
  canary: "owned",
  shadow: "owned",
  candidate: "outline",
  retired: "neutral",
  rejected: "bad",
  rolled_back: "bad",
};

/**
 * Event kinds from docs/RUNBOOK.md, in the order the filter chips show them: training and the release gate first, then
 * upstream failures and recovery, then the task registry and operations. A kind not listed still shows, after these.
 */
export const EVENT_KINDS = [
  "trained",
  "skip",
  "shadow",
  "canary",
  "live",
  "rejected",
  "rollback",
  "alert",
  "retired",
  "rescored",
  "rescore_failed",
  "student_error",
  "circuit",
  "audit_error",
  "shadow_error",
  "degraded",
  "fallback_teacher",
  "adapter_reloaded",
  "adapter_reload_failed",
  "gateway_error",
  "skip_example",
  "task_registered",
  "task_changed",
  "task_deleted",
  "task_skipped",
  "endpoint",
  "fault",
  "warmup",
] as const;

export const EVENT_TONE: Record<string, Tone> = {
  trained: "neutral",
  skip: "outline",
  shadow: "owned",
  canary: "owned",
  live: "good",
  rejected: "bad",
  rollback: "bad",
  alert: "warn",
  retired: "neutral",
  rescored: "neutral",
  rescore_failed: "warn",
  student_error: "bad",
  circuit: "warn",
  audit_error: "warn",
  shadow_error: "warn",
  degraded: "warn",
  fallback_teacher: "warn",
  adapter_reloaded: "good",
  adapter_reload_failed: "bad",
  gateway_error: "bad",
  skip_example: "outline",
  task_registered: "neutral",
  task_changed: "neutral",
  task_deleted: "neutral",
  task_skipped: "warn",
  endpoint: "neutral",
  fault: "warn",
  warmup: "outline",
};

export const EVENT_LABEL: Record<string, string> = {
  trained: "Trained",
  skip: "Skipped",
  shadow: "Shadow",
  canary: "Canary",
  live: "Live",
  rejected: "Rejected",
  rollback: "Rollback",
  alert: "Alert",
  retired: "Retired",
  rescored: "Re-scored",
  rescore_failed: "Re-score failed",
  student_error: "Student error",
  circuit: "Circuit breaker",
  audit_error: "Audit failed",
  shadow_error: "Shadow call failed",
  degraded: "Degraded answer",
  fallback_teacher: "Fallback teacher",
  adapter_reloaded: "Adapter reloaded",
  adapter_reload_failed: "Adapter reload failed",
  gateway_error: "Gateway error",
  skip_example: "Answer not stored",
  task_registered: "Task registered",
  task_changed: "Task changed",
  task_deleted: "Task deleted",
  task_skipped: "Task skipped",
  endpoint: "Endpoint",
  fault: "Fault injected",
  warmup: "Warm-up",
};

/**
 * A `circuit` event's detail, `<breaker>: <old>-><new> <reason>`, as `router.Ratchet._circuit_event` writes it. The
 * grammar is checked against the gateway's real events by tests/test_contract.py::test_resilience_panel_contract,
 * which reads the `*_RE` literals in this file and resilience-model.ts; keep each on one line.
 */
export const CIRCUIT_RE = /^([A-Za-z0-9_.-]+): (closed|open|half_open)->(closed|open|half_open)(?: (.*))?$/;

export type BreakerState = "closed" | "open" | "half_open";

export interface CircuitChange {
  breaker: string;
  from: BreakerState;
  to: BreakerState;
  reason: string;
}

export function parseCircuit(detail: string): CircuitChange | null {
  const m = CIRCUIT_RE.exec(detail);
  if (!m) return null;
  return { breaker: m[1], from: m[2] as BreakerState, to: m[3] as BreakerState, reason: m[4] ?? "" };
}

const CIRCUIT_TONE: Record<BreakerState, Tone> = { open: "bad", half_open: "warn", closed: "good" };
const CIRCUIT_LABEL: Record<BreakerState, string> = { open: "Breaker opened", half_open: "Breaker probing", closed: "Breaker closed" };

/** An event's tone: its kind's, except a `circuit` event, whose tone is the state it moved to. */
export function eventTone(e: Pick<LedgerEvent, "kind" | "detail">): Tone {
  if (e.kind === "circuit") {
    const c = parseCircuit(e.detail);
    if (c) return CIRCUIT_TONE[c.to];
  }
  return EVENT_TONE[e.kind] ?? "neutral";
}

/** An event's heading: its kind's label, except a `circuit` event, which says what the breaker did. */
export function eventLabel(e: Pick<LedgerEvent, "kind" | "detail">): string {
  if (e.kind === "circuit") {
    const c = parseCircuit(e.detail);
    if (c) return CIRCUIT_LABEL[c.to];
  }
  return EVENT_LABEL[e.kind] ?? e.kind;
}

export function eventKey(e: LedgerEvent): string {
  return `${e.ts ?? ""}|${e.day}|${e.kind}|${e.detail}`;
}

/** Events in `next` that were not in `before` (events carry no id; day+kind+detail is unique enough). */
export function newEvents(before: LedgerEvent[] | undefined, next: LedgerEvent[]): LedgerEvent[] {
  if (!before) return [];
  const seen = new Map<string, number>();
  for (const e of before) seen.set(eventKey(e), (seen.get(eventKey(e)) ?? 0) + 1);
  const out: LedgerEvent[] = [];
  for (const e of next) {
    const k = eventKey(e);
    const n = seen.get(k) ?? 0;
    if (n > 0) seen.set(k, n - 1);
    else out.push(e);
  }
  return out;
}

/**
 * The version a `fault` event targets (`student-v4 will be deployed corrupted`), while that is
 * still unresolved: not trained yet, or trained and still on its way (candidate, shadow, canary).
 */
export function armedFault(s: Stats): { id: number; trained: boolean } | null {
  for (let i = s.events.length - 1; i >= 0; i--) {
    const e = s.events[i];
    if (e.kind !== "fault") continue;
    const m = /student-v(\d+)/.exec(e.detail);
    if (!m) continue; // another fault (the student outage drill) says nothing about an armed deploy
    const id = Number(m[1]);
    const v = s.versions.find((x) => x.id === id);
    if (!v) return { id, trained: false };
    return v.state === "candidate" || v.state === "shadow" || v.state === "canary" ? { id, trained: true } : null;
  }
  return null;
}

export function isPinned(task: { task: string; pinned?: boolean }): boolean {
  return task.pinned ?? PINNED_FALLBACK.has(task.task);
}

/** SHA-256 of the empty string: the BOM fingerprint before any example exists. */
export const EMPTY_SHA = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";

/** Shares: one decimal under 10% (so 0.4% does not read as 0%), whole percent above. */
export function fmtShare(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "–";
  if (v > 0 && v < 0.1) return (v * 100).toFixed(1) + "%";
  return Math.round(v * 100) + "%";
}

/** Clean y-axis ticks from 0 to just above `max`, at most ~5 intervals. */
export function niceTicks(max: number): number[] {
  const steps = [0.01, 0.02, 0.05, 0.1, 0.2, 0.25, 0.5, 1, 2, 2.5, 5, 10, 20, 25, 50, 100];
  const target = Math.max(max * 1.04, 1e-6);
  const step = steps.find((s) => Math.ceil(target / s) <= 5) ?? Math.ceil(target / 5);
  const n = Math.ceil(target / step);
  return Array.from({ length: n + 1 }, (_, i) => +(i * step).toFixed(4));
}
