/**
 * The page-wide "waking up" store: one instance per tab, fed by `gatewayFetch` (every gateway read goes through it) and by
 * a /healthz poll that runs only while waking. /healthz costs nothing. Nothing is stored in the browser.
 */
import { useSyncExternalStore } from "react";
import { POLL_MS, SLOW_CALL_MS, initialWaking, isSlowFirstCall, isWakingResponse, reduce, type WakingEvent, type WakingState } from "./waking-model";

let state: WakingState = initialWaking;
let anySuccess = false;
let timer: ReturnType<typeof setInterval> | null = null;
const listeners = new Set<() => void>();

function dispatch(ev: WakingEvent) {
  const next = reduce(state, ev);
  if (next === state) return;
  state = next;
  if (state.phase === "waking" || state.phase === "slow") startPolling();
  else stopPolling();
  for (const l of listeners) l();
}

async function probe() {
  try {
    const r = await fetch("/healthz", { cache: "no-store" });
    const type = r.headers.get("content-type") ?? "";
    if (r.ok && type.includes("json")) {
      anySuccess = true;
      dispatch({ type: "health_ok", at: Date.now() });
      return;
    }
  } catch {
    /* still down */
  }
  dispatch({ type: "tick", at: Date.now() });
}

function startPolling() {
  if (timer != null || typeof window === "undefined") return;
  timer = setInterval(() => void probe(), POLL_MS);
}

function stopPolling() {
  if (timer != null) clearInterval(timer);
  timer = null;
}

/** Called by the Retry button after the banner gave up: ask again for another round of waiting. */
export function retryWaking() {
  dispatch({ type: "retry", at: Date.now() });
  void probe();
}

export function subscribeWaking(cb: () => void): () => void {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

export function getWaking(): WakingState {
  return state;
}

/** Call `cb` each time a wait ends with the gateway answering (pages re-fetch what failed). Returns the unsubscribe. */
export function onRecovered(cb: () => void): () => void {
  let seen = state.recoveries;
  return subscribeWaking(() => {
    if (state.recoveries !== seen) {
      seen = state.recoveries;
      cb();
    }
  });
}

export function useWaking(): WakingState {
  return useSyncExternalStore(subscribeWaking, getWaking, getWaking);
}

/**
 * `fetch` for a read of the page's own gateway. It changes nothing about the request or the answer; it only watches:
 * a network error, a 503 `warming_up` or a first call still waiting after 6 s starts the waiting state, and any good
 * answer ends it. It never retries (a retry could pay twice).
 */
export async function gatewayFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const started = Date.now();
  const slow = anySuccess ? null : setTimeout(() => {
    if (isSlowFirstCall(Date.now() - started, anySuccess)) dispatch({ type: "failure", at: Date.now() });
  }, SLOW_CALL_MS + 50);
  try {
    const r = await fetch(input, init);
    if (r.ok) {
      anySuccess = true;
      dispatch({ type: "health_ok", at: Date.now() });
    } else if (r.status === 502 || r.status === 503 || r.status === 504) {
      let body: unknown = null;
      try {
        body = await r.clone().json();
      } catch {
        /* not JSON */
      }
      if (isWakingResponse(r.status, body)) dispatch({ type: "failure", at: Date.now() });
    }
    return r;
  } catch (e) {
    if (!(e instanceof DOMException && e.name === "AbortError")) dispatch({ type: "failure", at: Date.now() });
    throw e;
  } finally {
    if (slow) clearTimeout(slow);
  }
}
