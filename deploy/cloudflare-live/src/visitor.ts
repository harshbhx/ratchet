// Who the visitor is, and the daily limit on the demo's paid calls. No imports and no Cloudflare types: the Worker uses it, and
// web/tests/live-visitor.test.mjs runs it under Node.

/** Set by the Pages proxy (web/public/_worker.js) from the EDGE_TOKEN secret; the Worker checks it and always removes it. */
export const EDGE_HEADER = "x-ratchet-edge";
/** The visitor's address as the Pages proxy saw it. Trusted only together with a matching EDGE_HEADER. */
export const CLIENT_IP_HEADER = "x-ratchet-client-ip";

async function digest(text: string): Promise<Uint8Array> {
  return new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text)));
}

/** The hex SHA-256 of a text. A visitor's address is only ever kept in this form. */
export async function sha256Hex(text: string): Promise<string> {
  return [...(await digest(text))].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** Does the presented token equal the secret? Compares SHA-256 digests with no early exit, so the time says nothing about the secret. */
export async function tokenMatches(presented: string | null, secret: unknown): Promise<boolean> {
  const wanted = typeof secret === "string" ? secret.trim() : "";
  if (wanted === "" || presented === null || presented.trim() === "") return false;
  const [a, b] = await Promise.all([digest(presented.trim()), digest(wanted)]);
  let diff = a.length ^ b.length;
  for (let i = 0; i < a.length && i < b.length; i++) diff |= a[i] ^ b[i];
  return diff === 0;
}

/** An address as text: IPv4 or IPv6 characters only, short. Anything else is not used. */
const ADDRESS = /^[0-9a-fA-F:.]{2,45}$/;

/**
 * The visitor's address. The Pages proxy's x-ratchet-client-ip counts only when x-ratchet-edge matches EDGE_TOKEN (a request straight
 * to this Worker can carry any header, so without the token the header is ignored); otherwise Cloudflare's cf-connecting-ip.
 */
export async function visitorIp(headers: Headers, edgeToken: unknown): Promise<string | null> {
  const claimed = headers.get(CLIENT_IP_HEADER)?.trim() ?? "";
  if (ADDRESS.test(claimed) && (await tokenMatches(headers.get(EDGE_HEADER), edgeToken))) return claimed;
  const own = headers.get("cf-connecting-ip")?.trim() ?? "";
  return own === "" ? null : own;
}

/** The headers the container sees: the gateway's per-IP limits read x-forwarded-for, and the edge token and its companion never get that far. */
export function forwardedHeaders(headers: Headers, ip: string | null): Headers {
  const out = new Headers(headers);
  out.delete(EDGE_HEADER);
  out.delete(CLIENT_IP_HEADER);
  if (ip) out.set("x-forwarded-for", ip);
  else out.delete("x-forwarded-for"); // never a visitor's own: the gateway trusts this one
  return out;
}

/** The calls the Worker adds the shared demo key to: /v1/* without an Authorization header. */
export function usesDemoKey(pathname: string, headers: Headers): boolean {
  return pathname.startsWith("/v1/") && !headers.get("authorization");
}

// ---------------------------------------------------------------------------------------------------------------------
// The daily limit per visitor. Every paid call reaches Token Factory, so each visitor gets a few a day, counted at the edge
// before the container wakes, in one small Durable Object per (bucket, UTC day, hash of the address).

/** `try`: the "Try a real request" box (a keyless POST /v1/*). `scout`: a Scout run (about 20 Token Factory calls). */
export type Bucket = "try" | "scout";

/** Calls per visitor per UTC day when the setting is absent or unreadable. */
export const DEFAULT_DAILY = 3;

export type DailyLimits = Record<Bucket, number>;

/** A whole number from the setting: absent, empty or unreadable is the default, "0" is off (no limit). */
function limitOf(raw: unknown): number {
  const text = (typeof raw === "number" ? String(raw) : typeof raw === "string" ? raw : "").trim();
  if (!/^\d{1,6}$/.test(text)) return DEFAULT_DAILY;
  return Number(text);
}

/** The limits from the Worker's settings EDGE_DAILY_TRIES and EDGE_DAILY_SCOUT (no RATCHET_ prefix: those would reach the gateway). */
export function dailyLimits(env: { EDGE_DAILY_TRIES?: unknown; EDGE_DAILY_SCOUT?: unknown }): DailyLimits {
  return { try: limitOf(env.EDGE_DAILY_TRIES), scout: limitOf(env.EDGE_DAILY_SCOUT) };
}

/** Did the browser send a judge's cookie? The gateway verifies it and keeps its own guard; here it only skips the Scout count. */
export function hasJudgeCookie(headers: Headers): boolean {
  return /(?:^|;\s*)ratchet_judge=[^;\s]/.test(headers.get("cookie") ?? "");
}

/**
 * Which bucket a request counts in, if any. A keyless POST /v1/* is a `try`. GET /scout/stream and POST /scout/run start a Scout run
 * and are a `scout`, unless the request carries a judge cookie. Reads (/scout/status, /v1/models, /ratchet/*) never count.
 */
export function bucketOf(method: string, pathname: string, headers: Headers): Bucket | null {
  if (method === "POST" && usesDemoKey(pathname, headers)) return "try";
  const run = (method === "GET" && /^\/scout\/stream(\/|$)/.test(pathname)) || (method === "POST" && /^\/scout\/run(\/|$)/.test(pathname));
  if (run && !hasJudgeCookie(headers)) return "scout";
  return null;
}

/** The UTC day of a time in milliseconds, as YYYY-MM-DD. */
export function utcDay(now: number): string {
  return new Date(now).toISOString().slice(0, 10);
}

/** The time of the next 00:00 UTC after `now`, in milliseconds. */
export function nextMidnight(now: number): number {
  const d = new Date(now);
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate() + 1);
}

/** Seconds until the next 00:00 UTC, rounded up, at least 1: the `retry-after` of a refusal. */
export function secondsToMidnight(now: number): number {
  return Math.max(1, Math.ceil((nextMidnight(now) - now) / 1000));
}

/** The name of a visitor's counter: bucket, UTC day and the hex SHA-256 of the address (never the address). */
export function quotaName(bucket: Bucket, day: string, ipHash: string): string {
  return `${bucket}:${day}:${ipHash}`;
}

/** The part of Durable Object storage the counter uses. */
export interface QuotaStore {
  get(key: string): Promise<unknown>;
  put(key: string, value: number): Promise<unknown>;
  getAlarm(): Promise<number | null>;
  setAlarm(time: number): Promise<unknown>;
}

/** How long after midnight a finished day's counter is deleted. */
const KEEP_AFTER_DAY_MS = 60 * 60 * 1000;

/**
 * Count one call against `limit`: allowed while the count so far is below it, refused after. In a Durable Object the read and the write
 * of one call are not interleaved with another call's, so this is atomic. A refused call is not counted. The first call sets an alarm
 * after the day ends, which deletes the counter.
 */
export async function takeQuota(store: QuotaStore, limit: number, now: number = Date.now()): Promise<{ allowed: boolean; count: number }> {
  const raw = await store.get("n");
  const count = typeof raw === "number" && raw > 0 ? raw : 0;
  if (count >= limit) return { allowed: false, count };
  await store.put("n", count + 1);
  if ((await store.getAlarm()) === null) await store.setAlarm(nextMidnight(now) + KEEP_AFTER_DAY_MS);
  return { allowed: true, count: count + 1 };
}

/** What the Worker needs of the VisitorQuota namespace: a counter by name, with one method. */
export interface QuotaNamespace {
  idFromName(name: string): unknown;
  get(id: unknown): { take(limit: number): Promise<{ allowed: boolean; count: number }> };
}

/** The 429 for a visitor past their day's limit, without waking the container. The shape follows the path that was refused. */
export function dailyLimitReached(bucket: Bucket, limit: number, now: number): Response {
  const headers = { "content-type": "application/json", "retry-after": String(secondsToMidnight(now)), "cache-control": "no-store" };
  if (bucket === "scout") {
    const detail = `Daily limit reached: ${limit} Scout runs per visitor per day. It resets at 00:00 UTC.`;
    return new Response(JSON.stringify({ detail }), { status: 429, headers });
  }
  const message = `Daily limit reached: ${limit} requests per visitor per day. It resets at 00:00 UTC.`;
  const body = { error: { message, type: "rate_limit_error", param: null, code: "daily_limit_reached" } };
  return new Response(JSON.stringify(body), { status: 429, headers });
}

/**
 * The answer for a call past its visitor's daily limit, or null to let it through. No namespace, a limit of 0, or a failing
 * Durable Object lets the call through (fail open): the gateway's own caps still hold. A failure is logged without the address.
 */
export async function overQuota(
  quotas: QuotaNamespace | undefined,
  limits: DailyLimits,
  method: string,
  pathname: string,
  headers: Headers,
  ip: string | null,
  now: number = Date.now(),
): Promise<Response | null> {
  const bucket = bucketOf(method, pathname, headers);
  if (!bucket || !quotas || limits[bucket] <= 0) return null;
  try {
    const name = quotaName(bucket, utcDay(now), await sha256Hex(ip ?? "unknown"));
    const { allowed } = await quotas.get(quotas.idFromName(name)).take(limits[bucket]);
    return allowed ? null : dailyLimitReached(bucket, limits[bucket], now);
  } catch {
    console.log(JSON.stringify({ event: "quota_error", bucket }));
    return null;
  }
}
