/**
 * Pure helpers for the judges' console: what an endpoint's failure means to a reader, route names in words, nano-USD,
 * and the summaries the chapters print. No DOM, no JSX: `tests/live-empty.test.mjs` and `tests/tour-model.test.mjs` run them, and
 * `tests/test_judges_page.py` holds `ROUTE_WORDS` to the router's routes.
 */

/** The result of one read from the gateway. `status` 0: no answer at all (a static preview, or the network). */
export type Fetched<T> =
  | { state: "idle" }
  | { state: "loading" }
  | { state: "ok"; data: T }
  | { state: "error"; status: number; message: string };

export type Unavailability = { kind: "missing" | "locked" | "offline" | "mock-only" | "failed"; text: string };

/**
 * What a failed read tells a reader, never a number: a 404 on a route this build does not serve, a key the gateway asks
 * for, a gateway that did not answer, or the gateway's own message.
 */
export function unavailable(path: string, status: number, message: string): Unavailability {
  if (status === 0) return { kind: "offline", text: "The gateway did not answer, so this part has nothing to show. It needs the page served by a running gateway." };
  if (status === 404 && /\/ratchet\/(versions|receipts|demo)\//.test(path) && message && !/^Request failed/.test(message)) {
    return { kind: "missing", text: `Nothing here yet: ${message}.` };
  }
  if (status === 404 || status === 405) return { kind: "missing", text: `Not available in this build: ${path} answered ${status}.` };
  if (status === 401 || status === 403) {
    if (/mock mode/i.test(message)) return { kind: "mock-only", text: "That action is available in mock mode only; this gateway is live." };
    return { kind: "locked", text: `This gateway asks for a key to read ${path} (${status}). The public gateway needs none.` };
  }
  if (status === 409) return { kind: "failed", text: message ? `Not yet: ${message}.` : `${path} answered 409.` };
  return { kind: "failed", text: message ? `The gateway answered ${status}: ${message}` : `The gateway answered ${status}.` };
}

/** The error message of a failed response body: FastAPI's `detail` string, OpenAI's `error.message`, else nothing. */
export function errorMessage(body: unknown): string {
  if (!body || typeof body !== "object") return "";
  const b = body as { detail?: unknown; error?: { message?: unknown } };
  if (typeof b.detail === "string") return b.detail;
  if (b.error && typeof b.error.message === "string") return b.error.message;
  return "";
}

/** Every route the router records (ratchet/router.py ROUTES), in words. An unknown one is shown as it is. */
export const ROUTE_WORDS: Record<string, string> = {
  student: "student answered",
  confidence: "escalated: student unsure",
  cold_start: "teacher: no student yet",
  pinned: "teacher: task pinned",
  student_error: "teacher: student call failed",
  student_breaker: "teacher: student breaker open",
  degraded: "student stood in for the teacher",
  fallback_teacher: "fallback teacher",
  n_choices: "escalated: choices disagreed",
  unknown_task: "teacher: unknown task",
  tools: "teacher: tool request",
  multimodal: "teacher: image or audio",
  task_disabled: "teacher: task disabled",
  student_disabled: "teacher: student turned off",
  passthrough: "passed through (embeddings)",
  demo_split: "student: random split, picked at random (not by confidence)",
};

export function routeWords(route: string | null | undefined): string {
  if (!route) return "not recorded";
  if (route.startsWith("ineligible:")) return `teacher: not eligible (${route.slice(11)})`;
  if (route.startsWith("rule:")) return `escalated by rule ${route.slice(5)}`;
  return ROUTE_WORDS[route] ?? route;
}

/** Whether a route ended with the student's answer. */
export function studentRoute(route: string | null | undefined): boolean {
  return route === "student" || route === "degraded" || route === "demo_split";
}

/** Route counts summed over tasks, largest first. */
export function routeTotals(tasks: { routes?: Record<string, number> }[]): { route: string; n: number }[] {
  const sum = new Map<string, number>();
  for (const t of tasks) for (const [r, n] of Object.entries(t.routes ?? {})) sum.set(r, (sum.get(r) ?? 0) + (Number(n) || 0));
  return [...sum.entries()].map(([route, n]) => ({ route, n })).filter((r) => r.n > 0).sort((a, b) => b.n - a.n || a.route.localeCompare(b.route));
}

/** Nano-USD (receipts' whole-number amounts) as dollars, with enough digits for small sums. */
export function nusdToUsd(nusd: number | null | undefined): number | null {
  if (nusd == null || !Number.isFinite(nusd)) return null;
  return nusd / 1e9;
}

/** "a1b2c3d4…e5f6" for a long hex digest; short strings as they are. */
export function shortHash(h: string | null | undefined, head = 10, tail = 6): string {
  if (!h) return "–";
  return h.length <= head + tail + 1 ? h : `${h.slice(0, head)}…${h.slice(-tail)}`;
}

/** The newest receipt period the gateway sealed for the whole gateway (`global`), or null. */
/**
 * Receipts are private: `/ratchet/receipts` answers 401 without a reader key, and a 401 is a red console error on every
 * visit. The public page holds no reader key, so it never asks; with one, the path is returned as it is.
 */
export function receiptsPath(path: string, readerKey: string | null = null): string | null {
  return readerKey ? path : null;
}

export function latestGlobalPeriod(list: { receipts?: { period: number; global?: boolean }[] } | null | undefined): number | null {
  const ps = (list?.receipts ?? []).filter((r) => r.global).map((r) => r.period);
  return ps.length ? Math.max(...ps) : null;
}

/** Parse NDJSON leaves, newest last; bad lines are skipped. */
export function parseLeaves(text: string): Record<string, unknown>[] {
  const out: Record<string, unknown>[] = [];
  for (const line of text.split("\n")) {
    const l = line.trim();
    if (!l) continue;
    try {
      const v = JSON.parse(l);
      if (v && typeof v === "object") out.push(v as Record<string, unknown>);
    } catch {
      /* skip */
    }
  }
  return out;
}

/** A drift stream's progress toward its alarm: log10 of the test martingale over log10 of the threshold, 0 to 1. */
export function alarmProgress(log10Martingale: number | null | undefined, threshold: number | null | undefined): number {
  if (log10Martingale == null || !threshold || threshold <= 1 || !Number.isFinite(log10Martingale)) return 0;
  return Math.min(1, Math.max(0, log10Martingale / Math.log10(threshold)));
}

/** The version a chapter should show: the live one, else the newest with a bill of materials, else the newest. */
export function focusVersion<V extends { id: number; state: string; bom?: string | null }>(versions: V[] | null | undefined): V | null {
  const vs = versions ?? [];
  return vs.find((v) => v.state === "live") ?? [...vs].reverse().find((v) => v.bom) ?? vs[vs.length - 1] ?? null;
}

/** The version the release chapter's stage strip names for a stage. */
export function stageOf(state: string, pct: number): "shadow" | "canary10" | "canary50" | "live" | null {
  if (state === "shadow") return "shadow";
  if (state === "canary") return pct >= 50 ? "canary50" : "canary10";
  if (state === "live") return "live";
  return null;
}

/** The base URL a client would use against this page's gateway, for the copyable examples. */
export function baseUrl(origin: string | null | undefined): string {
  const o = (origin ?? "").replace(/\/+$/, "");
  return /^https?:\/\//.test(o) ? `${o}/v1` : "http://localhost:8000/v1";
}
