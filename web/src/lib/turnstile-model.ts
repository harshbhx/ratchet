// The human check (Cloudflare Turnstile), the part that needs no browser: names, the site key's answer, how a token travels. Pure: no DOM, no network.
// The live Worker (deploy/cloudflare-live/src/turnstile.ts) verifies the token; src/lib/turnstile.ts draws the widget.

/** The header a call carries its token in, and the query parameter Scout's EventSource uses (it cannot set headers). The Worker removes both. */
export const TOKEN_HEADER = "cf-turnstile-response";
export const TOKEN_PARAM = "cf-turnstile-response";

/** Answered by the live Worker itself: `{ "site_key": "<public key>" }`, or `{ "site_key": null }` while the check is off. */
export const SITE_KEY_PATH = "/ratchet/turnstile";

/** The site key from the Worker's answer. null for anything else: no widget is drawn and calls go without a token. */
export function parseSiteKey(body: unknown): string | null {
  const key = (body as { site_key?: unknown } | null)?.site_key;
  return typeof key === "string" && key.trim() !== "" ? key.trim() : null;
}

/** The request headers for a token: none without one. */
export function tokenHeaders(token: string | null): Record<string, string> {
  return token ? { [TOKEN_HEADER]: token } : {};
}

/** Scout's stream address: the question, and the token when there is one. A token is single-use, so every address is built with a fresh one. */
export function scoutStreamUrl(question: string, token: string | null): string {
  const query = new URLSearchParams({ q: question });
  if (token) query.set(TOKEN_PARAM, token);
  return `/scout/stream?${query.toString()}`;
}

/** The Worker's own codes for a refused human check: 403 on a bad or missing token, 429 on a blocked visitor or a used-up curl allowance. */
export const HUMAN_CHECK_CODES = ["human_check_failed", "human_check_required", "human_check_blocked", "curl_allowance_used"] as const;

/** True for the Worker's refusal of a failed or missing human check (HTTP 403, `error.code` of the /v1 shape). */
export function isHumanCheckFailure(status: number, body: unknown): boolean {
  if (status !== 403) return false;
  const code = (body as { error?: { code?: unknown } } | null)?.error?.code;
  return code === "human_check_failed" || code === "human_check_required";
}

export const HUMAN_CHECK_TEXT = "Human check failed. Refresh the page and try again.";
