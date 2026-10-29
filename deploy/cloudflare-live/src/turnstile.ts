// The "verify you are human" check (Cloudflare Turnstile) in front of the live site's paid calls. No imports from Cloudflare and no Cloudflare types:
// the Worker uses it, and web/tests/live-turnstile.test.mjs runs it under Node with a mocked siteverify.
//
// WHAT IT DOES. A browser button that spends Token Factory money (Try a real request, a Scout run, the judges page's request box) sends a
// Turnstile token with the call: in the header cf-turnstile-response, or, for Scout's EventSource, which cannot set headers, in the query
// parameter of the same name. The Worker asks Cloudflare (siteverify) whether the token is good BEFORE the call reaches the container or
// Token Factory, and removes the token before forwarding. A token works once and is valid for five minutes, so the page asks for a new one per call.
//   - A good token: the call goes on to the visitor's daily limit (visitor.ts), exactly as before.
//   - A token Cloudflare says is not a token at all (siteverify: invalid-input-response, so forged or malformed): 403 "Human check failed", and one
//     failure is counted for that visitor and UTC day. At EDGE_TURNSTILE_MAX_FAILS failures (default 5) the visitor is blocked from every keyless
//     paid call until 00:00 UTC: 429 with retry-after.
//   - A token that is expired or already used (timeout-or-duplicate, which is what a retry or a reconnect of a request produces) gets the same 403 and
//     the same words, but is NOT counted: a real person who retries must never be locked out. The same for a missing token on a call that needs one.
//   - Siteverify itself unreachable or answering with an error (a network failure, a 5xx, a wrong secret on our side): fail open. The call goes on to the
//     ordinary per-visitor limit, nothing is counted against the visitor, and the event is logged. A Cloudflare hiccup never locks a judge out.
//   - No token at all on a keyless POST /v1/* (curl): still allowed, at the same 3 a day per visitor, and now ALSO counted in one shared total
//     for the whole day, EDGE_DAILY_CURL_TOTAL (default 100, 0 is off), so a bot that rotates addresses cannot drain the money kept for verified
//     people. When it is used up: 429 that points to the browser. A Scout run without a token is refused (Scout is browser-only).
//   - A call with an Authorization header (a real API key) never comes here: the gateway checks the key.
// A token is checked ONCE per request, here, at the top of the Worker. If the Worker later sends the same request on again while the container
// wakes up, it does not ask Cloudflare again (the check is not part of that loop).
//
// EVERYTHING IS OFF UNLESS THE SECRET TURNSTILE_SECRET_KEY IS SET on the Worker: no check, no failure count, no shared curl total. The widget in the
// web app appears only when the Worker also answers GET /ratchet/turnstile with a site key (the public TURNSTILE_SITE_KEY variable), so the code can
// be deployed before the widget exists. An address is only ever kept as a SHA-256 hash, as in visitor.ts.
import { overQuota, paidBucket, secondsToMidnight, sha256Hex, utcDay, type Bucket, type DailyLimits } from "./visitor";

/** The header the page sends the token in, and the query parameter Scout's EventSource uses instead. Both are removed before forwarding. */
export const TOKEN_HEADER = "cf-turnstile-response";
export const TOKEN_PARAM = "cf-turnstile-response";

export const SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify";

/** Failed checks per visitor per UTC day before the visitor is blocked, when EDGE_TURNSTILE_MAX_FAILS is absent or unreadable. */
export const DEFAULT_MAX_FAILS = 5;
/** Keyless calls without a token, all visitors together, per UTC day, when EDGE_DAILY_CURL_TOTAL is absent or unreadable. */
export const DEFAULT_CURL_TOTAL = 100;

/** How long siteverify gets to answer before the check fails open. */
const SITEVERIFY_MS = 4000;
/** Turnstile tokens are at most 2048 characters; anything longer is not one. */
const MAX_TOKEN_CHARS = 2048;

export const FAILED_TEXT = "Human check failed. Refresh the page and try again.";
export const REQUIRED_TEXT = "Human check required. Open the site in a browser, refresh the page and try again.";
export const BLOCKED_TEXT = "Too many failed human checks from your address today. It resets at 00:00 UTC.";
export const CURL_USED_TEXT = "The shared curl allowance for today is used up. Try it in the browser at https://live.ratchet.dualmindlab.tech, or come back after 00:00 UTC.";

export interface TurnstileEnv {
  TURNSTILE_SECRET_KEY?: unknown;
  TURNSTILE_SITE_KEY?: unknown;
  EDGE_TURNSTILE_MAX_FAILS?: unknown;
  EDGE_DAILY_CURL_TOTAL?: unknown;
  TURNSTILE_HOSTNAMES?: unknown;
}

export interface TurnstileSettings {
  /** The secret key; null means the whole check is off. */
  secret: string | null;
  /** The public site key the page renders the widget with; null means no widget. */
  siteKey: string | null;
  maxFails: number;
  curlTotal: number;
  /** The hostnames a good token may have been solved on (siteverify returns it). Lower case. */
  hostnames: string[];
}

/** A whole number from a setting: absent, empty or unreadable is the default, "0" is off. */
function countOf(raw: unknown, fallback: number): number {
  const text = (typeof raw === "number" ? String(raw) : typeof raw === "string" ? raw : "").trim();
  return /^\d{1,6}$/.test(text) ? Number(text) : fallback;
}

const textOf = (raw: unknown): string | null => {
  const t = typeof raw === "string" ? raw.trim() : "";
  return t === "" ? null : t;
};

/**
 * The hostnames a token may come from when TURNSTILE_HOSTNAMES is absent: the site and the live host, and never localhost or 127.0.0.1 (a token
 * solved on a page an attacker runs on their own machine must not pass). Cloudflare's own test keys answer with the hostname example.com, so
 * testing with them needs TURNSTILE_HOSTNAMES=example.com.
 */
export const DEFAULT_HOSTNAMES = ["ratchet.dualmindlab.tech", "live.ratchet.dualmindlab.tech"];

/** A comma-separated list of hostnames, lower case. Absent or empty is the default list. */
export function hostnamesOf(raw: unknown): string[] {
  const list = (typeof raw === "string" ? raw : "").split(",").map((h) => h.trim().toLowerCase()).filter((h) => h !== "");
  return list.length > 0 ? list : [...DEFAULT_HOSTNAMES];
}

export function turnstileSettings(env: TurnstileEnv): TurnstileSettings {
  return {
    secret: textOf(env.TURNSTILE_SECRET_KEY),
    siteKey: textOf(env.TURNSTILE_SITE_KEY),
    maxFails: countOf(env.EDGE_TURNSTILE_MAX_FAILS, DEFAULT_MAX_FAILS),
    curlTotal: countOf(env.EDGE_DAILY_CURL_TOTAL, DEFAULT_CURL_TOTAL),
    hostnames: hostnamesOf(env.TURNSTILE_HOSTNAMES),
  };
}

/** GET /ratchet/turnstile: the public site key, or null when the check is off or has no site key. The page draws the widget only when it is set. */
export function siteKeyAnswer(settings: TurnstileSettings): Response {
  const site_key = settings.secret && settings.siteKey ? settings.siteKey : null;
  return new Response(JSON.stringify({ site_key }), { headers: { "content-type": "application/json", "cache-control": "no-store" } });
}

/** The token a request carries: the header first, else the query parameter. null when there is none (or it is empty or absurdly long). */
export function tokenOf(headers: Headers, url: URL): string | null {
  const raw = (headers.get(TOKEN_HEADER) ?? url.searchParams.get(TOKEN_PARAM) ?? "").trim();
  return raw !== "" && raw.length <= MAX_TOKEN_CHARS ? raw : null;
}

/** The URL without the token parameter, so the gateway's logs and routes never see it. The same URL when there is none. */
export function withoutTokenParam(url: URL): URL {
  if (!url.searchParams.has(TOKEN_PARAM)) return url;
  const out = new URL(url.toString());
  out.searchParams.delete(TOKEN_PARAM);
  return out;
}

/**
 * `valid`: Cloudflare accepts it. `invalid`: Cloudflare says it is forged or malformed (counts toward the block). `spent`: expired or already used, or
 * rejected for any other reason that is not clearly forgery (refused, never counted). `error`: no usable answer; the caller fails open.
 */
export type Verdict = "valid" | "invalid" | "spent" | "error";

/** Error codes that say our own side is wrong (the secret) or Cloudflare is: fail open, never count a failure. */
const OUR_FAULT = new Set(["missing-input-secret", "invalid-input-secret", "bad-request", "internal-error"]);

/**
 * Ask Cloudflare whether a token is good (see Verdict). Only invalid-input-response counts as invalid; timeout-or-duplicate, which is what a retry
 * or a reconnect with an old token produces, is `spent`, as is any other refusal.
 */
export async function verifyToken(
  secret: string,
  token: string,
  ip: string | null,
  send: (url: string, init: RequestInit) => Promise<Response> = (url, init) => fetch(url, init),
  hostnames: string[] | null = null,
): Promise<Verdict> {
  try {
    const form = new URLSearchParams({ secret, response: token });
    if (ip) form.set("remoteip", ip);
    const r = await send(SITEVERIFY_URL, { method: "POST", body: form, signal: AbortSignal.timeout(SITEVERIFY_MS) });
    if (r.status >= 400) return "error";
    const body = (await r.json()) as { success?: unknown; hostname?: unknown; "error-codes"?: unknown } | null;
    if (body && body.success === true) {
      // A token is only good if it was solved on one of our hostnames (Cloudflare's guide: check `hostname`). A token for another site, or one with
      // no hostname at all, is treated as forged: invalid, and counted. Only the hostname is logged.
      if (hostnames) {
        const host = typeof body.hostname === "string" ? body.hostname.trim().toLowerCase() : "";
        if (!hostnames.includes(host)) {
          console.log(JSON.stringify({ event: "turnstile_wrong_hostname", hostname: host.slice(0, 100) }));
          return "invalid";
        }
      }
      return "valid";
    }
    if (!body || body.success !== false) return "error";
    const codes = Array.isArray(body["error-codes"]) ? (body["error-codes"] as unknown[]).map(String) : [];
    if (codes.some((c) => OUR_FAULT.has(c))) return "error";
    return codes.includes("invalid-input-response") ? "invalid" : "spent";
  } catch {
    return "error";
  }
}

const headersOf = (extra: Record<string, string> = {}) => ({ "content-type": "application/json", "cache-control": "no-store", ...extra });

/** The shape follows the path that was refused, as in visitor.ts: an OpenAI-style error for /v1, `{detail}` for Scout. */
function refusal(bucket: Bucket, status: number, message: string, code: string, type: string, extra: Record<string, string> = {}): Response {
  if (bucket === "scout") return new Response(JSON.stringify({ detail: message }), { status, headers: headersOf(extra) });
  return new Response(JSON.stringify({ error: { message, type, param: null, code } }), { status, headers: headersOf(extra) });
}

export const humanCheckFailed = (bucket: Bucket): Response => refusal(bucket, 403, FAILED_TEXT, "human_check_failed", "invalid_request_error");
export const humanCheckRequired = (bucket: Bucket): Response => refusal(bucket, 403, REQUIRED_TEXT, "human_check_required", "invalid_request_error");
export const humanCheckBlocked = (bucket: Bucket, now: number): Response =>
  refusal(bucket, 429, BLOCKED_TEXT, "human_check_blocked", "rate_limit_error", { "retry-after": String(secondsToMidnight(now)) });
export const curlAllowanceUsed = (now: number): Response =>
  refusal("try", 429, CURL_USED_TEXT, "curl_allowance_used", "rate_limit_error", { "retry-after": String(secondsToMidnight(now)) });

/** What the Worker needs of the VisitorQuota namespace for the whole guard: a counter by name with the three methods. */
export interface GuardNamespace {
  idFromName(name: string): unknown;
  get(id: unknown): {
    take(limit: number): Promise<{ allowed: boolean; count: number }>;
    peek(): Promise<number>;
    bump(): Promise<number>;
  };
}

/** The names of the two counters this file adds, next to visitor.ts's `quotaName`: failed checks per visitor and day, and the day's shared curl total. */
export const failsName = (day: string, ipHash: string): string => `fails:${day}:${ipHash}`;
export const curlName = (day: string): string => `curl:${day}:all`;

export interface GuardInput {
  quotas: GuardNamespace | undefined;
  limits: DailyLimits;
  settings: TurnstileSettings;
  method: string;
  url: URL;
  headers: Headers;
  ip: string | null;
  now?: number;
  /** Replaces fetch for siteverify (tests). */
  send?: (url: string, init: RequestInit) => Promise<Response>;
}

/**
 * Everything the Worker decides about a paid keyless call before waking the container: the human check, the block, the visitor's daily limit and the
 * shared curl total. Returns the refusal to send, or null to let the call through. Off (no secret): only the daily limit, exactly as before.
 * Every Durable Object failure lets the call through: the gateway's own limits and budgets still cap the spend (and it is logged, without the address).
 */
export async function guardPaidCall(a: GuardInput): Promise<Response | null> {
  const now = a.now ?? Date.now();
  const daily = () => overQuota(a.quotas, a.limits, a.method, a.url.pathname, a.headers, a.ip, now);
  const secret = a.settings.secret;
  const bucket = paidBucket(a.method, a.url.pathname, a.headers);
  if (!secret || !bucket) return daily();

  const day = utcDay(now);
  const counters = a.quotas;
  let failures: ReturnType<GuardNamespace["get"]> | null = null;
  if (counters) {
    try {
      failures = counters.get(counters.idFromName(failsName(day, await sha256Hex(a.ip ?? "unknown"))));
      // A visitor past the failure limit is shut out of every keyless paid call for the rest of the day, token or not.
      if (a.settings.maxFails > 0 && (await failures.peek()) >= a.settings.maxFails) return humanCheckBlocked(bucket, now);
    } catch {
      console.log(JSON.stringify({ event: "turnstile_error", where: "block" }));
    }
  }

  const token = tokenOf(a.headers, a.url);
  let verified = false;
  if (token) {
    const verdict = await verifyToken(secret, token, a.ip, a.send, a.settings.hostnames);
    if (verdict === "invalid" || verdict === "spent") {
      try {
        if (verdict === "invalid" && failures) await failures.bump();
      } catch {
        console.log(JSON.stringify({ event: "turnstile_error", where: "count" }));
      }
      return humanCheckFailed(bucket);
    }
    if (verdict === "error") console.log(JSON.stringify({ event: "turnstile_unavailable" }));
    verified = true; // valid, or no answer from Cloudflare: failing open means the visitor's own daily limit is the only gate
  } else if (bucket === "scout") {
    return humanCheckRequired(bucket); // a plain 403, never counted
  }

  const refused = await daily();
  if (refused) return refused;

  // A call with no token (curl) also draws on one allowance shared by everybody, so rotating addresses cannot empty the budget for verified people.
  if (!verified && bucket === "try" && counters && a.settings.curlTotal > 0) {
    try {
      const { allowed } = await counters.get(counters.idFromName(curlName(day))).take(a.settings.curlTotal);
      if (!allowed) return curlAllowanceUsed(now);
    } catch {
      console.log(JSON.stringify({ event: "turnstile_error", where: "curl_total" }));
    }
  }
  return null;
}
