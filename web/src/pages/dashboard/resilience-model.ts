/**
 * Pure helpers for the Resilience panel. Everything comes from `/ratchet/stats`: breaker states from its `circuit`
 * events (the gateway reports no breaker snapshot yet), fallbacks from each task's `routes`, the outage drill from its
 * `fault` event. Nothing here guesses a value the gateway did not send.
 *
 * The `*_RE` literals are checked against the gateway's real event text by
 * tests/test_contract.py::test_resilience_panel_contract; keep each on one line.
 */
import type { DayStats, LedgerEvent, TaskStats } from "@/lib/api";
import { parseCircuit, type BreakerState, type Tone } from "./model";

/** The outage drill's `fault` event (gateway.demo_student_outage): `student endpoint down for 60 s (fault injection)`. */
export const OUTAGE_RE = /^student endpoint down for (\d+) s\b/;
/** The wait a failed probe set (`half_open->open for 60 s after a failed probe (ConnectError)`). */
export const WAIT_RE = /^for (\d+(?:\.\d+)?) s after a failed probe/;

/** How many events /ratchet/stats returns (`events[-40:]`): what "recent" means on this panel. */
export const EVENT_WINDOW = 40;

export const BREAKER_WORD: Record<BreakerState, string> = { closed: "Closed", open: "Open", half_open: "Half-open" };
export const BREAKER_TONE: Record<BreakerState, Tone> = { closed: "good", open: "bad", half_open: "warn" };

export interface BreakerMeta {
  label: string;
  who: "student" | "teacher";
  /** What the breaker guards, and what an open breaker means for requests. */
  guards: string;
  open: string;
}

const META: Record<string, BreakerMeta> = {
  student: {
    label: "Student endpoint",
    who: "student",
    guards: "Live and canary traffic to the student (Nano with your adapter).",
    open: "Requests skip the student and go straight to the teacher, without waiting on the endpoint.",
  },
  shadow: {
    label: "Shadow candidate",
    who: "student",
    guards: "Only the shadow candidate's calls. Live traffic does not depend on it.",
    open: "The shadow candidate is not sampled; the request itself is not affected.",
  },
  teacher: {
    label: "Teacher",
    who: "teacher",
    guards: "Every teacher call: escalations, cold start, pinned tasks, audits and arbitrations.",
    open: "Requests that need the teacher get a 503 with Retry-After at once, unless a fallback teacher or degraded mode stands in; audits are skipped.",
  },
  fallback_teacher: {
    label: "Fallback teacher",
    who: "teacher",
    guards: "The fallback teacher (RATCHET_TEACHER_FALLBACK_MODEL).",
    open: "The fallback teacher is not called: when the primary is down too, requests get the primary's error, or a degraded answer when that mode is on.",
  },
};

/** Display facts for a breaker name. Another endpoint in a role is named `<role>-2`, `<role>-3`, ... (router._breaker). */
export function breakerMeta(name: string): BreakerMeta {
  const exact = META[name];
  if (exact) return exact;
  const m = /^(student|shadow)-(\d+)$/.exec(name);
  if (m) return { ...META[m[1]], label: `${META[m[1]].label} ${m[2]}` };
  return { label: name, who: name.includes("teacher") ? "teacher" : "student", guards: "An upstream endpoint.", open: "Calls to it are skipped." };
}

/** The breakers every gateway has (the teacher's from startup, the student's from its first call), shown even when quiet. */
const ALWAYS = ["student", "teacher"];
const ORDER = ["student", "shadow", "teacher", "fallback_teacher"];

export interface BreakerChange {
  from: BreakerState;
  to: BreakerState;
  reason: string;
  /** Epoch seconds, when the gateway sent it. */
  ts: number | null;
  day: number;
}

export interface BreakerView {
  name: string;
  meta: BreakerMeta;
  /** The state its latest reported change moved it to; null when none of the recent events changed it. */
  state: BreakerState | null;
  since: number | null;
  reason: string;
  /** Seconds until the next probe, as the latest failed probe reported it; null for the first opening (not reported). */
  waitS: number | null;
  /** Oldest first. */
  changes: BreakerChange[];
}

/**
 * One view per breaker, from the `circuit` events in order. The state is where the latest reported change moved it:
 * a breaker only changes when a call arrives, so an open breaker stays open until a request after its wait probes it.
 * At the longest wait the gateway logs only some open<->half_open pairs, so the latest change can lag by one probe.
 */
export function breakerViews(events: LedgerEvent[]): BreakerView[] {
  const views = new Map<string, BreakerView>();
  const view = (name: string) => {
    let v = views.get(name);
    if (!v) {
      v = { name, meta: breakerMeta(name), state: null, since: null, reason: "", waitS: null, changes: [] };
      views.set(name, v);
    }
    return v;
  };
  for (const name of ALWAYS) view(name);
  for (const e of events) {
    if (e.kind !== "circuit") continue;
    const c = parseCircuit(e.detail);
    if (!c) continue;
    const v = view(c.breaker);
    const ts = typeof e.ts === "number" && Number.isFinite(e.ts) ? e.ts : null;
    v.changes.push({ from: c.from, to: c.to, reason: c.reason, ts, day: e.day });
    v.state = c.to;
    v.since = ts;
    v.reason = c.reason;
    const wait = c.to === "open" ? WAIT_RE.exec(c.reason) : null;
    v.waitS = wait ? Number(wait[1]) : null;
  }
  const rank = (n: string) => {
    const role = n.replace(/-\d+$/, "");
    const i = ORDER.indexOf(role);
    return i < 0 ? ORDER.length : i;
  };
  return [...views.values()].sort((a, b) => rank(a.name) - rank(b.name) || a.name.localeCompare(b.name));
}

/** Requests a fallback answered, by route (router.ROUTES): why the student (Nano with your adapter) did not serve them. */
export const FALLBACK_ROUTES: { route: string; label: string; who: "student" | "teacher"; hint: string }[] = [
  { route: "student_breaker", label: "Breaker open", who: "teacher", hint: "The student's breaker was open, so the teacher answered at once." },
  { route: "student_error", label: "Student call failed", who: "teacher", hint: "The student call raised (a timeout, no connection, a 5xx), so the teacher answered." },
  { route: "degraded", label: "Degraded answers", who: "student", hint: "The teacher was unavailable, so the student's own below-threshold answer was served, marked degraded." },
  { route: "fallback_teacher", label: "Fallback teacher", who: "teacher", hint: "The primary teacher was unavailable, so the fallback teacher answered." },
];

export interface FallbackTotals {
  /** False on a gateway whose /ratchet/stats has no `routes` per task (older than v0.5). */
  known: boolean;
  requests: number;
  byRoute: Record<string, number>;
  total: number;
}

/** Every task's `routes` added up: all days, since the ledger began. */
export function fallbackTotals(tasks: TaskStats[]): FallbackTotals {
  const byRoute: Record<string, number> = {};
  let known = false;
  let requests = 0;
  for (const t of tasks) {
    requests += t.requests;
    if (!t.routes) continue;
    known = true;
    for (const [route, n] of Object.entries(t.routes)) {
      if (typeof n === "number" && Number.isFinite(n)) byRoute[route] = (byRoute[route] ?? 0) + n;
    }
  }
  const total = FALLBACK_ROUTES.reduce((acc, r) => acc + (byRoute[r.route] ?? 0), 0);
  return { known, requests, byRoute, total };
}

/** The latest day's stand-in answers (`degraded`, `fallback_teacher`), when the gateway reports them. */
export function latestStandIns(days: DayStats[]): { day: number; degraded: number; fallback: number } | null {
  const d = days.length ? days[days.length - 1] : undefined;
  if (!d || (d.degraded == null && d.fallback_teacher == null)) return null;
  return { day: d.day, degraded: d.degraded ?? 0, fallback: d.fallback_teacher ?? 0 };
}

/** Upstream failures and recoveries (docs/RUNBOOK.md#event-kinds); a `fault` only when it is the outage drill. */
const FAILURE_KINDS = new Set([
  "circuit",
  "student_error",
  "audit_error",
  "shadow_error",
  "degraded",
  "fallback_teacher",
  "adapter_reloaded",
  "adapter_reload_failed",
  "gateway_error",
]);

export function isResilienceEvent(e: Pick<LedgerEvent, "kind" | "detail">): boolean {
  return FAILURE_KINDS.has(e.kind) || (e.kind === "fault" && OUTAGE_RE.test(e.detail));
}

/** Newest first, at most `limit`. */
export function resilienceEvents(events: LedgerEvent[], limit = 8): LedgerEvent[] {
  const out: LedgerEvent[] = [];
  for (let i = events.length - 1; i >= 0 && out.length < limit; i--) {
    if (isResilienceEvent(events[i])) out.push(events[i]);
  }
  return out;
}

export interface Outage {
  /** Epoch seconds on the gateway's clock. */
  start: number;
  seconds: number;
  until: number;
}

/** The latest outage drill, from its `fault` event. A later drill replaces an earlier one, as on the gateway. */
export function lastOutage(events: LedgerEvent[]): Outage | null {
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    if (e.kind !== "fault") continue;
    const m = OUTAGE_RE.exec(e.detail);
    if (!m || typeof e.ts !== "number" || !Number.isFinite(e.ts)) continue;
    const seconds = Number(m[1]);
    return { start: e.ts, seconds, until: e.ts + seconds };
  }
  return null;
}

/** Seconds left in an outage at `nowMs` (the viewer's clock plus its measured offset from the gateway's), never negative. */
export function outageLeft(o: Outage | null, nowMs: number): number {
  if (!o) return 0;
  return Math.max(0, Math.min(o.seconds, o.until - nowMs / 1000));
}

/** `12 s`, `2 min 5 s`, `1 h 3 min`. */
export function fmtSpan(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  if (s < 3600) {
    const m = Math.floor(s / 60);
    const r = s % 60;
    return r ? `${m} min ${r} s` : `${m} min`;
  }
  const h = Math.floor(s / 3600);
  const m = Math.round((s % 3600) / 60);
  return m ? `${h} h ${m} min` : `${h} h`;
}

/** A gateway timestamp (epoch seconds) as a local clock time. */
export function fmtClock(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

/** `12 s ago`, `3 min ago`; `just now` under 5 s. */
export function fmtAgo(seconds: number): string {
  if (seconds < 5) return "just now";
  return `${fmtSpan(seconds)} ago`;
}
