// The "verified human" session cookie: one solved Turnstile check is good for the rest of the visit. No Cloudflare types: the Worker uses it, and
// web/tests/live-human.test.mjs runs it under Node.
//
// After a token passes siteverify (success, and a hostname on the allowlist) the Worker answers with a Set-Cookie, rt_human. A later paid keyless call
// that carries a valid cookie needs no token: it is a verified human, and is still under the per-visitor daily limits and every dollar cap.
//
//   value   v1.<expiry, seconds since 1970>.<first 32 hex of SHA-256 of the visitor's address>.<HMAC-SHA256 signature, 64 hex>
//   signed  over "v1.<expiry>.<address hash>" with a key derived from the Turnstile secret: HMAC-SHA256(TURNSTILE_SECRET_KEY, "rt_human_v1").
//           No new secret exists, and rotating the Turnstile secret signs everybody out.
//   bound   to the visitor's address, so one solved check cannot be handed to a farm of machines: another address simply verifies again. The address
//           itself is never in the cookie, only a truncated hash, as everywhere in the Worker.
//   life    12 hours from the check, not tied to midnight (the daily limits are counted per UTC day by address, independently of this cookie).
//   flags   HttpOnly (a script cannot read it), Secure, SameSite=Lax, Path=/ (the Scout stream, the /v1 calls and /ratchet/human all see it).
//
// A cookie that is missing, malformed, tampered with, expired or for another address counts as absent: no failure is recorded and nothing is refused
// for that reason, the visitor is just asked for the check again. The signature is checked with crypto.subtle.verify (constant time) and the address
// hash with a comparison that does not stop at the first difference.
import { sha256Hex } from "./visitor";

export const COOKIE_NAME = "rt_human";
/** How long one check lasts: 12 hours. */
export const COOKIE_SECONDS = 12 * 60 * 60;

const VALUE = /^v1\.(\d{1,12})\.([0-9a-f]{32})\.([0-9a-f]{64})$/;

const enc = new TextEncoder();

/** The signing key: HMAC(secret, "rt_human_v1"), used as an HMAC key itself, so the Turnstile secret is never used directly to sign. */
async function signingKey(secret: string): Promise<CryptoKey> {
  const root = await crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const derived = await crypto.subtle.sign("HMAC", root, enc.encode("rt_human_v1"));
  return crypto.subtle.importKey("raw", derived, { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"]);
}

const toHex = (bytes: ArrayBuffer): string => [...new Uint8Array(bytes)].map((b) => b.toString(16).padStart(2, "0")).join("");

function fromHex(hex: string): Uint8Array {
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  return out;
}

/** Equal strings of the same length, with no early exit. */
function sameText(a: string, b: string): boolean {
  let diff = a.length ^ b.length;
  for (let i = 0; i < a.length && i < b.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

const addressHash = async (ip: string): Promise<string> => (await sha256Hex(ip)).slice(0, 32);

/** The cookie to set for a visitor who just passed the check: the Set-Cookie header value and the expiry. null without an address (nothing to bind it to). */
export async function issueCookie(secret: string, ip: string | null, now: number = Date.now()): Promise<{ header: string; expires: number } | null> {
  if (!ip) return null;
  const expires = Math.floor(now / 1000) + COOKIE_SECONDS;
  const payload = `v1.${expires}.${await addressHash(ip)}`;
  const signature = toHex(await crypto.subtle.sign("HMAC", await signingKey(secret), enc.encode(payload)));
  const header = `${COOKIE_NAME}=${payload}.${signature}; Max-Age=${COOKIE_SECONDS}; Path=/; HttpOnly; Secure; SameSite=Lax`;
  return { header, expires };
}

/** The cookie's value in a Cookie header, or null. */
export function cookieValue(cookieHeader: string | null, name: string = COOKIE_NAME): string | null {
  const m = new RegExp(`(?:^|;\\s*)${name}=([^;\\s]*)`).exec(cookieHeader ?? "");
  return m ? m[1] : null;
}

/** The expiry (seconds since 1970) of a valid cookie for this address, or null when it is absent, tampered with, expired or for another address. */
export async function verifyCookie(cookieHeader: string | null, secret: string, ip: string | null, now: number = Date.now()): Promise<number | null> {
  try {
    const raw = cookieValue(cookieHeader);
    const m = raw === null ? null : VALUE.exec(raw);
    if (!m || !ip) return null;
    const expires = Number(m[1]);
    const signed = await crypto.subtle.verify("HMAC", await signingKey(secret), fromHex(m[3]), enc.encode(`v1.${m[1]}.${m[2]}`));
    if (!signed) return null;
    const sameAddress = sameText(m[2], await addressHash(ip));
    return sameAddress && expires > Math.floor(now / 1000) ? expires : null;
  } catch {
    return null;
  }
}

/** A copy of the Cookie header without the human cookie: the gateway has no use for it. null when nothing is left. */
export function withoutHumanCookie(cookieHeader: string | null): string | null {
  if (cookieHeader === null) return null;
  const rest = cookieHeader.split(";").map((p) => p.trim()).filter((p) => p !== "" && !p.startsWith(`${COOKIE_NAME}=`));
  return rest.length > 0 ? rest.join("; ") : null;
}

/** GET /ratchet/human: is this visitor verified, and until when (an ISO time, or null). Never wakes the container. */
export async function humanAnswer(secret: string | null, headers: Headers, ip: string | null, now: number = Date.now()): Promise<Response> {
  const expires = secret ? await verifyCookie(headers.get("cookie"), secret, ip, now) : null;
  const body = { verified: expires !== null, expires: expires === null ? null : new Date(expires * 1000).toISOString() };
  return new Response(JSON.stringify(body), { headers: { "content-type": "application/json", "cache-control": "no-store", vary: "cookie" } });
}
