/**
 * The "waking up" state of the live gateway, as a pure model (no DOM, no React, no timers).
 *
 * The live gateway sleeps 30 minutes after its last request. The first visitor after that waits for the container to
 * start (about 20 seconds). `waking-store.ts` feeds this reducer with what the page's own gateway calls saw and with
 * /healthz answers; the banner, the footer, the dashboard and Scout's Run button only read its state.
 */

export type Phase = "idle" | "waking" | "slow" | "ready";

export interface WakingState {
  phase: Phase;
  /** When the current wait began (ms), null while idle or ready. */
  since: number | null;
  /** How many times a wait ended with the gateway answering; pages re-fetch when it grows. */
  recoveries: number;
}

export type WakingEvent =
  | { type: "failure"; at: number }
  | { type: "health_ok"; at: number }
  | { type: "tick"; at: number }
  | { type: "retry"; at: number };

/** /healthz is asked this often while waking. */
export const POLL_MS = 3000;
/** After this long without an answer the banner changes to "taking longer than usual". */
export const GIVE_UP_MS = 90_000;
/** A first call that is still waiting after this long counts as the gateway waking up. */
export const SLOW_CALL_MS = 6000;

export const initialWaking: WakingState = { phase: "idle", since: null, recoveries: 0 };

export function reduce(state: WakingState, ev: WakingEvent): WakingState {
  switch (ev.type) {
    case "failure":
      if (state.phase === "waking" || state.phase === "slow") return state;
      return { ...state, phase: "waking", since: ev.at };
    case "health_ok":
      if (state.phase === "waking" || state.phase === "slow") return { phase: "ready", since: null, recoveries: state.recoveries + 1 };
      return state;
    case "tick":
      if (state.phase === "waking" && state.since != null && ev.at - state.since >= GIVE_UP_MS) return { ...state, phase: "slow" };
      return state;
    case "retry":
      if (state.phase === "slow") return { ...state, phase: "waking", since: ev.at };
      return state;
  }
}

/** True while the visitor should be told the gateway is starting (or has not started in time). */
export function isWaking(state: WakingState): boolean {
  return state.phase === "waking" || state.phase === "slow";
}

/**
 * Does this answer mean "the container is not ready"? The proxy and the live Worker send 503 with
 * `{"warming_up": true}`; a bare 502/504 from the edge in front of a sleeping container counts too.
 */
export function isWakingResponse(status: number, body: unknown): boolean {
  if (status === 503 && body && typeof body === "object" && (body as { warming_up?: unknown }).warming_up === true) return true;
  return status === 502 || status === 504;
}

/** A call that has not answered after `elapsedMs` while no call has succeeded yet. */
export function isSlowFirstCall(elapsedMs: number, anySuccess: boolean): boolean {
  return !anySuccess && elapsedMs > SLOW_CALL_MS;
}

export const BANNER_WAKING = "Waking up the live gateway, about 20 seconds. This page will update by itself.";
export const BANNER_SLOW = "The live gateway is taking longer than usual. Try again in a minute.";
export const FOOTER_WAKING = "Waking up the live gateway...";
export const RUN_WAKING = "Waking up...";

export function bannerText(state: WakingState): string | null {
  if (state.phase === "waking") return BANNER_WAKING;
  if (state.phase === "slow") return BANNER_SLOW;
  return null;
}

/** Whole seconds waited, for a screen reader or a caption; 0 when not waiting. */
export function waitedSeconds(state: WakingState, now: number): number {
  return state.since == null ? 0 : Math.max(0, Math.floor((now - state.since) / 1000));
}
