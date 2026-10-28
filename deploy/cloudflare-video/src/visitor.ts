// What the video link Worker makes of one request: the human-or-bot verdict with its reason, the coarse device, and the daily
// pseudonymous visitor hash. No imports, so the tests run it under Node. Nothing here stores anything; the callers keep only the
// short words and numbers these functions return, never the IP address or the user agent they were computed from.

export type Kind = "human" | "bot";
/** Why a visit is a bot; "" for a human. */
export type Reason = "" | "ua" | "datacenter" | "scanner" | "no-accept-language" | "head" | "verified-bot";

/**
 * Link previews, crawlers, scripts and checkers, by user agent: every name with "bot" in it (Slackbot, Twitterbot, Discordbot,
 * LinkedInBot, TelegramBot, Googlebot, bingbot, Applebot, ...), the preview fetchers without it, command-line and library clients,
 * headless browsers, and this repository's own link check and deploy smoke.
 */
export const BOT_UA =
  /bot|crawl|spider|preview|slurp|facebookexternalhit|facebookcatalog|whatsapp|embedly|iframely|mastodon|curl|wget|python-requests|python-urllib|aiohttp|httpx|go-http-client|okhttp|java\/|libwww|node-fetch|undici|axios|headlesschrome|phantomjs|lighthouse|link-?check|smoke|monitor|uptime|scanner|scrapy/i;

/** Networks of email and chat link scanners: they open every link in a message before a person does. */
export const SCANNER_ASNS: ReadonlyMap<number, string> = new Map([
  [22843, "Proofpoint"],
  [26211, "Proofpoint"],
  [30031, "Mimecast"],
  [15324, "Barracuda"],
]);

/** Data-centre and cloud networks: a real person's browser rarely comes from one, a crawler or a scanner often does. */
export const DATACENTER_ASNS: ReadonlyMap<number, string> = new Map([
  [16509, "Amazon"],
  [14618, "Amazon"],
  [15169, "Google"],
  [396982, "Google Cloud"],
  [8075, "Microsoft (also Outlook and Defender link scanning)"],
  [13335, "Cloudflare"],
  [14061, "DigitalOcean"],
  [16276, "OVH"],
  [24940, "Hetzner"],
  [63949, "Akamai / Linode"],
  [31898, "Oracle Cloud"],
  [45102, "Alibaba Cloud"],
  [132203, "Tencent Cloud"],
  [51167, "Contabo"],
  [20473, "Vultr"],
  [12876, "Scaleway"],
  [32934, "Meta (link previews)"],
]);

/** A network name that says it is hosting, not an internet provider. */
export const DATACENTER_ORG = /hosting|cloud|data ?cent(?:er|re)|server|vps|colocation/i;
/** A network name of an email-security vendor. */
export const SCANNER_ORG = /proofpoint|mimecast|barracuda/i;

export interface Cf {
  country?: unknown;
  city?: unknown;
  region?: unknown;
  asn?: unknown;
  asOrganization?: unknown;
  verifiedBotCategory?: unknown;
  botManagement?: { verifiedBot?: unknown };
}

export interface VerdictInput {
  method: string;
  userAgent: string;
  acceptLanguage: string;
  /** The Sec-Purpose, Purpose and X-Purpose headers, joined. */
  purpose: string;
  cf?: Cf;
}

export const asnOf = (cf?: Cf): number => {
  const n = Number(cf?.asn);
  return Number.isInteger(n) && n > 0 ? n : 0;
};

/** Text from Cloudflare kept short and plain for the database and the emails: no control characters, at most 64 characters. */
export const cleanText = (v: unknown): string =>
  typeof v === "string" ? v.replace(/[\u0000-\u001f\u007f<>]/g, " ").replace(/\s+/g, " ").trim().slice(0, 64) : "";

/**
 * The one place that decides human or bot, and why. The first rule that matches wins:
 *   ua                  a bot, crawler, preview, script, headless-browser or checker user agent, no user agent, or a prefetch
 *   verified-bot        Cloudflare knows it as a verified bot
 *   head                a HEAD request (a link checker; a browser opening a link sends GET)
 *   scanner             a network of an email-security scanner (Proofpoint, Mimecast, Barracuda)
 *   datacenter          a hosting or cloud network, by number or by name
 *   no-accept-language  a GET without Accept-Language (every browser sends one, many scripts do not)
 */
export function verdictOf(i: VerdictInput): { kind: Kind; reason: Reason } {
  const bot = (reason: Reason) => ({ kind: "bot" as const, reason });
  const ua = i.userAgent.trim();
  if (ua === "" || BOT_UA.test(ua) || /prefetch|prerender|preview/i.test(i.purpose)) return bot("ua");
  const category = i.cf?.verifiedBotCategory;
  if (i.cf?.botManagement?.verifiedBot === true || (typeof category === "string" && category.trim() !== "")) return bot("verified-bot");
  if (i.method === "HEAD") return bot("head");
  const asn = asnOf(i.cf);
  const org = cleanText(i.cf?.asOrganization);
  if (SCANNER_ASNS.has(asn) || SCANNER_ORG.test(org)) return bot("scanner");
  if (DATACENTER_ASNS.has(asn) || DATACENTER_ORG.test(org)) return bot("datacenter");
  if (i.acceptLanguage.trim() === "") return bot("no-accept-language");
  return { kind: "human", reason: "" };
}

export interface Device {
  device: "desktop" | "phone" | "tablet" | "unknown";
  os: "Windows" | "macOS" | "Android" | "iOS" | "Linux" | "ChromeOS" | "other";
  browser: "Chrome" | "Safari" | "Firefox" | "Edge" | "Samsung" | "other";
}

/** Three short words from a user agent. The user agent itself is not kept. */
export function deviceOf(ua: string): Device {
  let os: Device["os"] = "other";
  if (/iPhone|iPad|iPod/.test(ua)) os = "iOS";
  else if (/Android/.test(ua)) os = "Android";
  else if (/CrOS/.test(ua)) os = "ChromeOS";
  else if (/Windows/.test(ua)) os = "Windows";
  else if (/Macintosh|Mac OS X/.test(ua)) os = "macOS";
  else if (/Linux|X11/.test(ua)) os = "Linux";
  let device: Device["device"] = "unknown";
  if (/iPad|Tablet/.test(ua) || (os === "Android" && !/Mobile/.test(ua))) device = "tablet";
  else if (/iPhone|iPod|Mobile/.test(ua)) device = "phone";
  else if (os === "Windows" || os === "macOS" || os === "Linux" || os === "ChromeOS") device = "desktop";
  let browser: Device["browser"] = "other";
  if (/Edg\/|EdgA\/|EdgiOS\/|Edge\//.test(ua)) browser = "Edge";
  else if (/SamsungBrowser/.test(ua)) browser = "Samsung";
  else if (/OPR\/|Opera|OPiOS/.test(ua)) browser = "other";
  else if (/Firefox\/|FxiOS\//.test(ua)) browser = "Firefox";
  else if (/Chrome\/|CriOS\//.test(ua)) browser = "Chrome";
  else if (/Safari\//.test(ua) && /Version\//.test(ua)) browser = "Safari";
  return { device, os, browser };
}

/** "Windows desktop, Chrome", "iOS phone, Safari"; empty fields (old rows) are left out. */
export function deviceLabel(d: { device?: string; os?: string; browser?: string }): string {
  const left = [d.os, d.device && d.device !== "unknown" ? d.device : ""].filter(Boolean).join(" ");
  return [left, d.browser].filter(Boolean).join(", ") || "unknown";
}

/** "Reliance Jio (AS55836)"; either part may be missing. */
export function networkLabel(org: string, asn: number): string {
  const name = org.trim();
  if (name && asn > 0) return `${name} (AS${asn})`;
  if (name) return name;
  return asn > 0 ? `AS${asn}` : "unknown network";
}

const hex = (b: ArrayBuffer): string => [...new Uint8Array(b)].map((x) => x.toString(16).padStart(2, "0")).join("");

async function hmac(key: ArrayBuffer | Uint8Array, message: string): Promise<ArrayBuffer> {
  const k = await crypto.subtle.importKey("raw", key, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return crypto.subtle.sign("HMAC", k, new TextEncoder().encode(message));
}

/**
 * A pseudonym for one visitor on one UTC day: the first 16 hex characters of HMAC-SHA256(salt, IP), where the salt is itself
 * HMAC-SHA256(STATS_KEY, "ratchet-video-visitor|" + day). The salt differs every day, so the same person has a different value on
 * the next day and cannot be followed, and without the key the value cannot be turned back into the IP. "" without an IP address or
 * a key of 16 or more characters (the same bar as /stats): then no visitor is counted.
 */
export async function visitorHash(ip: string | null | undefined, secret: string | undefined, day: string): Promise<string> {
  const key = String(secret ?? "").trim();
  const addr = String(ip ?? "").trim();
  if (key.length < 16 || addr === "") return "";
  const salt = await hmac(new TextEncoder().encode(key), `ratchet-video-visitor|${day}`);
  return hex(await hmac(salt, addr)).slice(0, 16);
}
