// Stream a Scout run over Server-Sent Events, with a fresh human-check token for every attempt. Pure of the browser: the token source, the
// EventSource class and the fallback call are passed in, so web/tests/live-turnstile.test.mjs can run it with fakes.
//
// A token is single-use, and a browser quietly reconnects an EventSource to the same address, which would send the same spent token. So an error
// closes the EventSource at once (never a reconnect), and the one fallback, POST /scout/run, asks for a NEW token before it goes out.
import type { ScoutEvent } from "./api";
import { scoutStreamUrl } from "./turnstile-model";

export interface ScoutSource {
  onmessage: ((m: MessageEvent) => void) | null;
  onerror: ((e: Event) => void) | null;
  close(): void;
}

export interface StreamDeps {
  /** A fresh token for one attempt, or null when there is none to give (the visit's cookie covers it). */
  token: () => Promise<string | null>;
  /** A token already obtained for the first attempt (the page ran the inline check before starting). Used once; every later attempt calls `token`. */
  first?: string | null;
  /** The browser's EventSource; undefined where there is none (the fallback runs at once). */
  makeSource: ((url: string) => ScoutSource) | undefined;
  /** POST /scout/run with the given token. */
  run: (question: string, token: string | null) => Promise<{ events: ScoutEvent[] }>;
  /** The pause between replayed events of the fallback. */
  pause: (ms: number) => Promise<unknown>;
}

/** Returns a cancel function. */
export function streamScoutWith(question: string, onEvent: (e: ScoutEvent) => void, deps: StreamDeps): () => void {
  let closed = false;
  let started = false;
  let es: ScoutSource | null = null;

  const finish = () => {
    closed = true;
    es?.close();
  };

  const fallback = async () => {
    try {
      const token = await deps.token(); // never the stream's token: that one is spent
      if (closed) return;
      const { events } = await deps.run(question, token);
      for (const ev of events) {
        if (closed) return;
        onEvent(ev);
        await deps.pause(ev.type === "step" ? 90 : 0);
      }
    } catch (e) {
      if (!closed) onEvent({ type: "error", message: e instanceof Error ? e.message : "Scout failed." });
    } finally {
      finish();
    }
  };

  const open = async () => {
    const token = deps.first !== undefined ? deps.first : await deps.token();
    deps.first = undefined; // single-use: the fallback below asks for its own
    if (closed) return;
    const source = deps.makeSource!(scoutStreamUrl(question, token));
    es = source;
    source.onmessage = (m) => {
      if (closed) return;
      let ev: ScoutEvent;
      try {
        ev = JSON.parse(m.data);
      } catch {
        return;
      }
      started = true;
      onEvent(ev);
      if (ev.type === "done" || ev.type === "error") finish();
    };
    source.onerror = () => {
      if (closed) return;
      source.close(); // no automatic reconnect: it would present the same single-use token again
      if (!started) void fallback();
      else {
        onEvent({ type: "error", message: "The connection to Scout was lost." });
        finish();
      }
    };
  };

  // With no EventSource the first (only) attempt is the POST, which can use the token the page already has.
  if (!deps.makeSource) {
    const first = deps.first;
    deps.first = undefined;
    if (first !== undefined) deps = { ...deps, token: async () => first };
    void fallback();
  }
  else void open();
  return finish;
}
