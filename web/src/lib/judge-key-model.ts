// A judge's own API key for the live gateway: where the browser keeps it, how a call carries it, what the answers say. Pure: no DOM, no network.
// The key lives only in the judge's browser (sessionStorage, or localStorage when they ask), is sent only as an Authorization header to the
// same origin, and is never put in a URL, a query string, a log or a state that is shown.

export const KEY_STORAGE = "ratchet.judges-key";

/** The two stores, as far as this file needs them (window.sessionStorage and window.localStorage; tests pass fakes). */
export interface KeyStore {
  getItem(name: string): string | null;
  setItem(name: string, value: string): void;
  removeItem(name: string): void;
}

/** A key as typed or pasted: surrounding whitespace and line breaks dropped; empty means none. */
export const cleanKey = (v: unknown): string => String(v ?? "").trim();

/** The key a judge has set: the tab's first, then the one remembered on this device. Every storage call may throw (private windows, blocked data). */
export function loadKey(session: KeyStore | null, local: KeyStore | null): string {
  for (const store of [session, local]) {
    try {
      const k = cleanKey(store?.getItem(KEY_STORAGE));
      if (k) return k;
    } catch {
      /* storage is not available: no key */
    }
  }
  return "";
}

/** Save a key: this tab's sessionStorage, plus localStorage only when `remember`. Anything saved earlier is replaced, never left in the other store. */
export function saveKey(key: string, remember: boolean, session: KeyStore | null, local: KeyStore | null): void {
  const k = cleanKey(key);
  forgetKey(session, local);
  if (!k) return;
  try {
    (remember ? local : session)?.setItem(KEY_STORAGE, k);
  } catch {
    /* nothing kept: the key then lasts until the page is closed */
  }
}

/** Forget the key everywhere it could be kept. */
export function forgetKey(session: KeyStore | null, local: KeyStore | null): void {
  for (const store of [session, local]) {
    try {
      store?.removeItem(KEY_STORAGE);
    } catch {
      /* nothing to clear */
    }
  }
}

/** True when the key sits on this device (localStorage), for the "Remember on this device" box. */
export function isRemembered(local: KeyStore | null): boolean {
  try {
    return cleanKey(local?.getItem(KEY_STORAGE)) !== "";
  } catch {
    return false;
  }
}

/** The request header for a key: none without one. Never a URL. */
export function keyHeaders(key: string): Record<string, string> {
  const k = cleanKey(key);
  return k ? { authorization: `Bearer ${k}` } : {};
}

export const USING_KEY_TEXT = "Using your judges' key";
export const KEY_REFUSED_TEXT = "That key wasn't accepted. Check it or forget it.";

/**
 * What a refused call with a key says. 401: the key was not accepted. 429: the gateway's own message (the daily limit or the rate), which
 * names the limit and when it resets; a plain fallback when it sent none. null for any other status (the caller's usual text applies).
 */
export function keyErrorText(status: number, body: unknown): string | null {
  if (status === 401) return KEY_REFUSED_TEXT;
  if (status !== 429) return null;
  const message = (body as { error?: { message?: unknown } } | null)?.error?.message;
  return typeof message === "string" && message.trim() ? message.trim() : "Your key has reached its limit for now. Try again later.";
}

/** The gateway's `X-RateLimit-Remaining-Requests`: how many calls the key has left today. null when absent or not a whole number. */
export function remainingFromHeaders(headers: { get(name: string): string | null } | null | undefined): number | null {
  const raw = headers?.get("x-ratelimit-remaining-requests");
  if (raw == null || !/^\d{1,9}$/.test(raw.trim())) return null;
  return Number(raw.trim());
}

/** "N left today", or null when the gateway did not say. */
export const leftText = (left: number | null): string | null => (left == null ? null : `${left} left today`);
