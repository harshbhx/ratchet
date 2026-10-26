// The landing page's "Try a real request" box: the request it sends and how it reads the answer. Pure: no DOM, no network.
import type { ServedBy } from "@/lib/api";

export const MAX_PROMPT_CHARS = 140;
/** Small on purpose: one short answer costs a fraction of a cent. */
export const MAX_TOKENS = 120;
export const DEFAULT_PROMPT = "In one sentence, what is LoRA fine-tuning?";

/**
 * The body for one request. `store: false` keeps the visitor's text out of the training set (the cascade still runs).
 * Sent once, never retried.
 */
export function buildBody(prompt: string): { model: string; messages: { role: "user"; content: string }[]; max_tokens: number; store: boolean } {
  return { model: "ratchet", messages: [{ role: "user", content: prompt.trim().slice(0, MAX_PROMPT_CHARS) }], max_tokens: MAX_TOKENS, store: false };
}

export interface Answer {
  text: string;
  servedBy: ServedBy | null;
  model: string | null;
  /** `ratchet.route`: `demo_split` is labelled on the badge. */
  route: string | null;
}

/** Read a chat completion defensively. null when it holds no usable choice. */
export function parseAnswer(body: unknown): Answer | null {
  const b = body as { model?: unknown; choices?: { message?: { content?: unknown } }[]; ratchet?: { served_by?: unknown; model?: unknown; route?: unknown } } | null;
  const content = b?.choices?.[0]?.message?.content;
  if (typeof content !== "string") return null;
  const by = b?.ratchet?.served_by;
  const servedBy = by === "student" || by === "teacher" ? by : null;
  const model = typeof b?.model === "string" && b.model ? b.model : null;
  const route = typeof b?.ratchet?.route === "string" && b.ratchet.route ? b.ratchet.route : null;
  return { text: content.trim(), servedBy, model, route };
}

/** A calm message for a failed request, by HTTP status (0: no answer at all). */
export function errorText(status: number): string {
  if (status === 0) return "Could not reach the live demo. Nothing was charged. Try again in a moment.";
  if (status === 429) return "The live demo has reached its limit for now. Please try again later.";
  if (status === 503 || status === 502 || status === 504) return "The live demo is not ready yet. Try again in a minute.";
  if (status === 400) return "The demo did not accept that text. Try a shorter, plainer question.";
  return `The live demo could not answer (${status}). Try again in a moment.`;
}

export const EMPTY_TEXT = "The model returned no text this time. Try a different question.";

// ---------- the daily limit, and the recorded answers shown instead

/** The edge's own code for the per-visitor daily limit on /v1 (an OpenAI-shaped 429). Other 429s have other codes. */
export const DAILY_LIMIT_CODE = "daily_limit_reached";
export const DAILY_LIMIT_TEXT = "You've used your 3 free requests today. They reset at 00:00 UTC.";
/** Said quietly under the box before the limit is reached. The edge counts; nothing is counted in the browser. */
export const DAILY_NOTE = "3 free requests per visitor per day.";
export const RECORDED_HEADING = "Meanwhile, read a recorded answer:";

/** True for the edge's daily-limit refusal: HTTP 429 and `error.code === "daily_limit_reached"`. Any other 429 is false. */
export function isDailyLimit(status: number, body: unknown): boolean {
  if (status !== 429) return false;
  const code = (body as { error?: { code?: unknown } } | null)?.error?.code;
  return code === DAILY_LIMIT_CODE;
}

/** Seconds from a `retry-after` header (whole seconds); null when absent or not a positive number. */
export function retryAfterSeconds(header: string | null | undefined): number | null {
  if (typeof header !== "string" || !/^\s*\d+\s*$/.test(header)) return null;
  const n = Number(header);
  return n > 0 && Number.isSafeInteger(n) ? n : null;
}

/** "5 h 12 min", "12 min", "5 h" or "under a minute": the time left, rounded up to a minute. null without a usable number. */
export function formatWait(seconds: number | null): string | null {
  if (seconds == null || !Number.isFinite(seconds) || seconds <= 0) return null;
  if (seconds <= 60) return "under a minute";
  const minutes = Math.ceil(seconds / 60);
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (h === 0) return `${m} min`;
  return m === 0 ? `${h} h` : `${h} h ${m} min`;
}

/** "Resets in 5 h 12 min." (a second sentence for the limit message), or null without a time. */
export function resetsIn(seconds: number | null): string | null {
  const w = formatWait(seconds);
  return w ? `Resets in ${w}.` : null;
}

/** One answer recorded from the live demo. */
export interface RecordedTry {
  prompt: string;
  text: string;
  servedBy: "student" | "teacher";
  model: string;
  route: string;
  costUsd: number;
  /** The day it was recorded, `YYYY-MM-DD`. */
  recorded: string;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "27 Oct 2026" from `2026-10-27`; the input unchanged when it is not a date. */
export function recordedDay(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  const month = m ? MONTHS[Number(m[2]) - 1] : undefined;
  return m && month ? `${Number(m[3])} ${month} ${m[1]}` : iso;
}

/** The words that say an answer is not a live call. */
export function recordedLabel(iso: string): string {
  return `Recorded on ${recordedDay(iso)} · not a live call`;
}

/** Read the recorded answers defensively: an entry missing a field is dropped, so a bad edit never shows half an answer. */
export function parseRecordedTries(text: string): RecordedTry[] {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    return [];
  }
  if (!Array.isArray(raw)) return [];
  const out: RecordedTry[] = [];
  for (const e of raw) {
    const r = e as Record<string, unknown> | null;
    if (!r || typeof r !== "object") continue;
    const { prompt, text: body, served_by: by, model, route, cost_usd: cost, recorded } = r;
    if (typeof prompt !== "string" || !prompt || typeof body !== "string" || !body) continue;
    if (by !== "student" && by !== "teacher") continue;
    if (typeof model !== "string" || !model || typeof route !== "string" || !route) continue;
    if (typeof cost !== "number" || !Number.isFinite(cost)) continue;
    if (typeof recorded !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(recorded)) continue;
    out.push({ prompt, text: body, servedBy: by, model, route, costUsd: cost, recorded });
  }
  return out;
}

/** A recorded answer in the shape a live one has, so the answer area and its badge are the same code. */
export function recordedAnswer(r: RecordedTry): Answer {
  return { text: r.text, servedBy: r.servedBy, model: r.model, route: r.route };
}
