// Running totals of the live gateway that survive its restarts. No imports and no Cloudflare types: the Worker uses it, and
// web/tests/live-counters.test.mjs runs it under Node.
//
// WHY: the gateway keeps its ledger on the container's own disk (/tmp), so a restart or a redeploy empties /ratchet/stats. The container
// answers GET /ratchet/counters with cumulative aggregates of ITS process (see docs/API.md) and a random `instance` id. The Worker keeps
// the sums here, in one small Durable Object (LiveCounters), by adding the DIFFERENCE between two snapshots of the same instance, or the
// whole first snapshot of a new instance. So a snapshot read twice counts once, and a restart starts a new instance without losing or
// repeating what the old one counted. A counter that goes DOWN inside one instance (a pruned failures table) adds nothing, never a
// negative number. What can be lost: what the old instance counted after our last snapshot of it; the Worker takes one after every paid
// call and when the container's idle timeout expires, so that is at most the last seconds before a crash or a redeploy.
//
// PRIVACY: only the nine numbers below are ever read from a snapshot or stored: counts and dollars, never text, an address, a client or a
// key. The instance id is a random hex string of the process, kept only to tell snapshots apart.

/** The counters, in the order the page shows them. `requests` is the sum of the three route groups. `errors` counts every failures row,
 * a refused key included, and is kept only for compatibility; `server_errors` is the new key the pages show: failed model calls and
 * the gateway's own 5xx, never an auth, rate, quota or budget refusal. */
export const FIELDS = [
  "requests",
  "requests_teacher",
  "requests_split",
  "requests_student",
  "student_tokens_in",
  "student_tokens_out",
  "cost_usd",
  "errors",
  "server_errors",
  "scout_runs",
] as const;

export type Field = (typeof FIELDS)[number];
export type Totals = Record<Field, number>;

/** One reading of the container: its instance and the counters since that instance began. */
export interface Snapshot {
  instance: string;
  values: Totals;
}

export interface CounterState {
  /** UTC day (YYYY-MM-DD) of the first snapshot ever recorded; null while nothing was. */
  since: string | null;
  /** Time of the latest snapshot that was recorded, in milliseconds; null while nothing was. */
  updated: number | null;
  /** How many container instances have been counted (1 = no restart since `since`). */
  instances: number;
  totals: Totals;
  /** The last snapshot of the instance now running, to take the next difference from. */
  last: Snapshot | null;
}

export const emptyTotals = (): Totals => Object.fromEntries(FIELDS.map((f) => [f, 0])) as Totals;

export const emptyState = (): CounterState => ({ since: null, updated: null, instances: 0, totals: emptyTotals(), last: null });

const INSTANCE = /^[0-9a-f]{8,64}$/;

/** The snapshot in the container's JSON, or null when it is not one (a warming-up page, an error, anything unexpected). */
export function parseSnapshot(raw: unknown): Snapshot | null {
  if (typeof raw !== "object" || raw === null) return null;
  const r = raw as Record<string, unknown>;
  if (typeof r.instance !== "string" || !INSTANCE.test(r.instance)) return null;
  const values = emptyTotals();
  for (const f of FIELDS) {
    const v = f === "server_errors" && r[f] === undefined ? 0 : r[f]; // a container from before the field counts none
    if (typeof v !== "number" || !Number.isFinite(v) || v < 0) return null;
    values[f] = v;
  }
  return { instance: r.instance, values };
}

/** Dollars to the millionth, so a long sum of small floats does not drift into digits nobody measured. */
const round = (f: Field, v: number): number => (f === "cost_usd" ? Math.round(v * 1e6) / 1e6 : Math.round(v));

/** The UTC day of a time in milliseconds, as YYYY-MM-DD. */
export const dayOf = (ms: number): string => new Date(ms).toISOString().slice(0, 10);

/** The larger of each counter: after a dip inside one instance the next difference is taken from the highest reading. */
const highest = (a: Totals, b: Totals): Totals => Object.fromEntries(FIELDS.map((f) => [f, Math.max(a[f], b[f])])) as Totals;

/** The state after one snapshot. Pure: the same snapshot twice adds nothing the second time. */
export function applySnapshot(state: CounterState, snap: Snapshot, now: number): CounterState {
  const same = state.last !== null && state.last.instance === snap.instance;
  const totals = { ...state.totals };
  for (const f of FIELDS) {
    const add = same ? Math.max(0, snap.values[f] - (state.last as Snapshot).values[f]) : snap.values[f];
    totals[f] = round(f, totals[f] + add);
  }
  return {
    since: state.since ?? dayOf(now),
    updated: now,
    instances: same ? state.instances : state.instances + 1,
    totals,
    last: same ? { instance: snap.instance, values: highest(state.last!.values, snap.values) } : snap,
  };
}

/** What GET /ratchet/stats/persistent answers: the totals and since when, never the instance id or the last snapshot. */
export interface PublicView {
  /** false: nothing recorded yet. The page then shows the measured snapshot, never a bare zero. */
  recorded: boolean;
  since: string | null;
  updated: string | null;
  instances: number;
  totals: Totals;
}

export function publicView(state: CounterState): PublicView {
  return {
    recorded: state.since !== null,
    since: state.since,
    updated: state.updated === null ? null : new Date(state.updated).toISOString(),
    instances: state.instances,
    totals: state.totals,
  };
}

/** The part of Durable Object storage the counters use. */
export interface CounterStore {
  get(key: string): Promise<unknown>;
  put(key: string, value: unknown): Promise<unknown>;
}

const STATE_KEY = "state";
const PULLED_KEY = "pulled";

/** The stored state, or an empty one when there is none or it is not shaped like one. */
export async function loadState(store: CounterStore): Promise<CounterState> {
  const raw = await store.get(STATE_KEY);
  if (typeof raw !== "object" || raw === null) return emptyState();
  const r = raw as Partial<CounterState>;
  const totals = emptyTotals();
  for (const f of FIELDS) {
    const v = (r.totals as Record<string, unknown> | undefined)?.[f];
    if (typeof v === "number" && Number.isFinite(v) && v >= 0) totals[f] = v;
  }
  const last = r.last && parseSnapshot({ instance: r.last.instance, ...r.last.values });
  return {
    since: typeof r.since === "string" && /^\d{4}-\d{2}-\d{2}$/.test(r.since) ? r.since : null,
    updated: typeof r.updated === "number" ? r.updated : null,
    instances: typeof r.instances === "number" && r.instances >= 0 ? Math.floor(r.instances) : 0,
    totals,
    last,
  };
}

/** Record one snapshot. In a Durable Object the read and the write of one call are not interleaved with another's. */
export async function recordSnapshot(store: CounterStore, snap: Snapshot, now: number = Date.now()): Promise<PublicView> {
  const next = applySnapshot(await loadState(store), snap, now);
  await store.put(STATE_KEY, next);
  return publicView(next);
}

export async function readView(store: CounterStore): Promise<PublicView> {
  return publicView(await loadState(store));
}

/** Whether a pull is due: true (and the time noted) when none was started within `gapMs`, so a burst of calls pulls once. */
export async function claimPull(store: CounterStore, gapMs: number, now: number = Date.now()): Promise<boolean> {
  const at = await store.get(PULLED_KEY);
  if (typeof at === "number" && now - at < gapMs && now >= at) return false;
  await store.put(PULLED_KEY, now);
  return true;
}

/** Calls whose result the counters should catch up with straight after: the paid ones. */
export function isPaidCall(method: string, pathname: string): boolean {
  return (method === "POST" && /^\/(v1|openai)\//.test(pathname)) || (method === "POST" && /^\/scout\/run(\/|$)/.test(pathname));
}

/** A Scout stream answers at once and runs for a while after; its totals are read later. */
export function isStreamedRun(method: string, pathname: string): boolean {
  return method === "GET" && /^\/scout\/stream(\/|$)/.test(pathname);
}

/** The path the Worker answers itself, without waking the container. */
export const PERSISTENT_PATH = "/ratchet/stats/persistent";
