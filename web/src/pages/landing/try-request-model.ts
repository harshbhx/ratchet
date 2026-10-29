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
  /** `ratchet.route`: `demo_split` is labelled "Random split" on the badge. */
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
  if (status === 0) return "Could not reach the live gateway. Nothing was charged. Try again in a moment.";
  if (status === 403) return "Human check failed. Refresh the page and try again.";
  if (status === 429) return "The live gateway has reached its limit for now. Please try again later.";
  if (status === 503 || status === 502 || status === 504) return "The live gateway is not ready yet. Try again in a minute.";
  if (status === 400) return "The gateway did not accept that text. Try a shorter, plainer question.";
  return `The live gateway could not answer (${status}). Try again in a moment.`;
}

export const EMPTY_TEXT = "The model returned no text this time. Try a different question.";

// ---------- the daily limit

/** The edge's own code for the per-visitor daily limit on /v1 (an OpenAI-shaped 429). Other 429s have other codes. */
export const DAILY_LIMIT_CODE = "daily_limit_reached";
export const DAILY_LIMIT_TEXT = "You've used today's free tries. They reset at 00:00 UTC.";
/** Said quietly under the box before the limit is reached. The edge counts; nothing is counted in the browser. */
export const DAILY_NOTE = "3 free requests per visitor per day.";

/** True for the edge's daily-limit refusal: HTTP 429 and `error.code === "daily_limit_reached"`. Any other 429 is false. */
export function isDailyLimit(status: number, body: unknown): boolean {
  if (status !== 429) return false;
  const code = (body as { error?: { code?: unknown } } | null)?.error?.code;
  return code === DAILY_LIMIT_CODE;
}
