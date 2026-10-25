// The demo video's short link, video.ratchet.dualmindlab.tech: every GET or HEAD is a 302 to the video on YouTube, and each GET is
// counted in Cloudflare D1 by UTC day, source (?from=, an allowlist) and kind (human, or a link preview or bot by its user agent).
// What is stored: the day, the source, the two-letter country, the time and the kind. Never an IP address, a user agent, a cookie, the
// raw query or any other identifier. Counting runs after the answer (ctx.waitUntil), so D1 can never slow or break the redirect.
// GET /stats reads the counts with `Authorization: Bearer <STATS_KEY>` (a Worker secret) and is a 404 without it.
// Email alerts (one per human visit, at most EMAIL_CAP per UTC day, and a summary of yesterday at 09:00 IST) stay off until
// EMAIL_ENABLED is "1". See docs/DEPLOYMENT.md, "The demo video link".
import { EmailMessage } from "cloudflare:email";
import { mime, plainAddress, summaryEmail, visitEmail, type Mail } from "./mail";

export const VIDEO_URL = "https://youtu.be/GIeSmDtopfs";

/** The sources a link may name with ?from=. A missing or empty one is "direct", anything else "other". */
export const SOURCES = [
  "devpost",
  "readme",
  "site",
  "live",
  "demo",
  "docs",
  "hf",
  "youtube",
  "x",
  "linkedin",
  "discord",
  "email",
  "release",
  "judges",
  "other",
] as const;

/** At most this many visit emails per UTC day; the daily summary counts every click. */
export const EMAIL_CAP = 40;
/** Visit rows (time, source, country, kind) older than this are deleted by the daily cron. */
export const EVENT_DAYS = 30;
const DAY_MS = 86_400_000;

export interface Env {
  DB?: D1Database;
  EMAIL?: SendEmail;
  STATS_KEY?: string;
  EMAIL_ENABLED?: string;
  EMAIL_FROM?: string;
  EMAIL_TO?: string;
}

export type Kind = "human" | "bot";

export interface Click {
  source: string;
  kind: Kind;
  country: string;
  at: number;
}

export function sourceOf(url: URL): string {
  const raw = (url.searchParams.get("from") ?? "").trim().toLowerCase();
  if (raw === "") return "direct";
  return (SOURCES as readonly string[]).includes(raw) ? raw : "other";
}

/**
 * Link previews, crawlers, scripts and checkers, by user agent: every name with "bot" in it (Slackbot, Twitterbot, Discordbot,
 * LinkedInBot, TelegramBot, Googlebot, bingbot, Applebot, ...), the preview fetchers without it, command-line and library clients,
 * headless browsers, and this repository's own link check and deploy smoke.
 */
const BOT_UA =
  /bot|crawl|spider|preview|slurp|facebookexternalhit|facebookcatalog|whatsapp|embedly|iframely|mastodon|curl|wget|python-requests|python-urllib|aiohttp|httpx|go-http-client|okhttp|java\/|libwww|node-fetch|undici|axios|headlesschrome|phantomjs|lighthouse|link-?check|smoke|monitor|uptime|scanner|scrapy/i;

/** "bot" for a preview, crawler, script, prefetch or empty user agent; otherwise "human". The user agent is never stored. */
export function kindOf(request: Request): Kind {
  const ua = (request.headers.get("user-agent") ?? "").trim();
  if (ua === "" || BOT_UA.test(ua)) return "bot";
  const purpose = [request.headers.get("sec-purpose"), request.headers.get("purpose"), request.headers.get("x-purpose")].join(" ");
  if (/prefetch|prerender|preview/i.test(purpose)) return "bot";
  const category = (request as { cf?: { verifiedBotCategory?: unknown } }).cf?.verifiedBotCategory;
  if (typeof category === "string" && category.trim() !== "") return "bot";
  return "human";
}

/** The two-letter country Cloudflare puts on the request, or "XX". */
export function countryOf(request: Request): string {
  const c = (request as { cf?: { country?: unknown } }).cf?.country;
  return typeof c === "string" && /^[A-Z][A-Z0-9]$/.test(c) ? c : "XX";
}

export const dayOf = (ms: number): string => new Date(ms).toISOString().slice(0, 10);
const tsOf = (ms: number): string => new Date(ms).toISOString().slice(0, 19) + "Z";

/** Paths a browser or crawler asks for by itself: redirected like any other, never counted. */
const HOUSEKEEPING = /^\/(robots\.txt|favicon\.ico|apple-touch-icon[^/]*\.png|\.well-known\/.*)$/;

function redirect(): Response {
  return new Response(null, {
    status: 302,
    headers: { location: VIDEO_URL, "cache-control": "no-store", "referrer-policy": "no-referrer", "x-robots-tag": "noindex" },
  });
}

const notFound = (): Response =>
  new Response("Not found\n", { status: 404, headers: { "content-type": "text/plain; charset=utf-8", "cache-control": "no-store" } });

const json = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body, null, 2) + "\n", {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
  });

/** A failure in the logs (seen with `wrangler tail`): what failed and the error's class, never a visitor's details. */
function logFailure(event: string, err: unknown): void {
  const name = err instanceof Error ? err.name : typeof err;
  const message = err instanceof Error ? err.message.slice(0, 200) : "";
  console.log(JSON.stringify({ event, error: name, message }));
}

export function emailOn(env: Env): boolean {
  return String(env.EMAIL_ENABLED ?? "").trim() === "1" && !!env.EMAIL && plainAddress(env.EMAIL_FROM) && plainAddress(env.EMAIL_TO);
}

async function send(env: Env, mail: Mail, at: number): Promise<void> {
  const from = String(env.EMAIL_FROM);
  const to = String(env.EMAIL_TO);
  const messageId = `${crypto.randomUUID()}@${from.split("@")[1]}`;
  await env.EMAIL!.send(new EmailMessage(from, to, mime({ ...mail, from, to, at, messageId })));
}

/** Count one GET, then (for a human, with email on, under the day's cap) send its alert. Runs after the redirect went out. */
export async function record(env: Env, click: Click): Promise<void> {
  const db = env.DB;
  if (!db) return;
  const day = dayOf(click.at);
  await db.batch([
    db
      .prepare("INSERT INTO clicks (day, source, kind, n) VALUES (?1, ?2, ?3, 1) ON CONFLICT (day, source, kind) DO UPDATE SET n = n + 1")
      .bind(day, click.source, click.kind),
    db.prepare("INSERT INTO events (ts, source, country, kind) VALUES (?1, ?2, ?3, ?4)").bind(tsOf(click.at), click.source, click.country, click.kind),
  ]);
  if (click.kind !== "human" || !emailOn(env)) return;
  // Take one of the day's email slots in one statement: past the cap the UPSERT's WHERE fails and nothing is returned.
  const slot = await db
    .prepare("INSERT INTO mail (day, visits) VALUES (?1, 1) ON CONFLICT (day) DO UPDATE SET visits = visits + 1 WHERE visits < ?2 RETURNING visits")
    .bind(day, EMAIL_CAP)
    .first<number>("visits");
  if (slot === null || slot === undefined) return;
  const today = await db.prepare("SELECT COALESCE(SUM(n), 0) AS n FROM clicks WHERE day = ?1 AND kind = 'human'").bind(day).first<number>("n");
  const mail = visitEmail({ source: click.source, country: click.country, at: click.at, today: Number(today ?? 0), day, slot, cap: EMAIL_CAP });
  await send(env, mail, click.at);
}

/** Constant-time check of `Authorization: Bearer <STATS_KEY>`. An unset or short key never opens /stats. */
export async function authorized(request: Request, key: string | undefined): Promise<boolean> {
  const want = String(key ?? "").trim();
  if (want.length < 16) return false;
  const m = /^Bearer\s+(\S+)$/i.exec((request.headers.get("authorization") ?? "").trim());
  const got = m ? m[1] : "";
  const enc = new TextEncoder();
  const [a, b] = await Promise.all([crypto.subtle.digest("SHA-256", enc.encode(got)), crypto.subtle.digest("SHA-256", enc.encode(want))]);
  const x = new Uint8Array(a);
  const y = new Uint8Array(b);
  let diff = got === "" ? 1 : 0;
  for (let i = 0; i < x.length; i++) diff |= x[i] ^ y[i];
  return diff === 0;
}

interface Row {
  source: string;
  human: number;
  bot: number;
}

export interface Stats {
  video: string;
  generated_at: string;
  stored: string;
  totals: { human: number; bot: number };
  by_source: Row[];
  by_day: (Row & { day: string })[];
  countries_30d: { country: string; human: number; bot: number }[];
  emails: { day: string; visits: number; summary: boolean }[];
}

export async function readStats(db: D1Database, now: number): Promise<Stats> {
  const since = tsOf(now - EVENT_DAYS * DAY_MS);
  const [clicks, countries, mail] = await Promise.all([
    db.prepare("SELECT day, source, kind, n FROM clicks ORDER BY day DESC, source").all<{ day: string; source: string; kind: string; n: number }>(),
    db
      .prepare("SELECT country, kind, COUNT(*) AS n FROM events WHERE ts >= ?1 GROUP BY country, kind")
      .bind(since)
      .all<{ country: string; kind: string; n: number }>(),
    db.prepare("SELECT day, visits, summary FROM mail ORDER BY day DESC").all<{ day: string; visits: number; summary: number }>(),
  ]);
  const byDay = new Map<string, Row & { day: string }>();
  const bySource = new Map<string, Row>();
  const totals = { human: 0, bot: 0 };
  for (const r of clicks.results ?? []) {
    const kind = r.kind === "human" ? "human" : "bot";
    const n = Number(r.n) || 0;
    const d = byDay.get(`${r.day}|${r.source}`) ?? { day: r.day, source: r.source, human: 0, bot: 0 };
    d[kind] += n;
    byDay.set(`${r.day}|${r.source}`, d);
    const s = bySource.get(r.source) ?? { source: r.source, human: 0, bot: 0 };
    s[kind] += n;
    bySource.set(r.source, s);
    totals[kind] += n;
  }
  const byCountry = new Map<string, { country: string; human: number; bot: number }>();
  for (const r of countries.results ?? []) {
    const c = byCountry.get(r.country) ?? { country: r.country, human: 0, bot: 0 };
    c[r.kind === "human" ? "human" : "bot"] += Number(r.n) || 0;
    byCountry.set(r.country, c);
  }
  const most = <T extends { human: number; bot: number }>(a: T, b: T) => b.human - a.human || b.bot - a.bot;
  return {
    video: VIDEO_URL,
    generated_at: new Date(now).toISOString(),
    stored: "UTC day, source, kind, two-letter country and time; never an IP address, user agent or cookie",
    totals,
    by_source: [...bySource.values()].sort((a, b) => most(a, b) || a.source.localeCompare(b.source)),
    by_day: [...byDay.values()],
    countries_30d: [...byCountry.values()].sort((a, b) => most(a, b) || a.country.localeCompare(b.country)),
    emails: (mail.results ?? []).map((r) => ({ day: r.day, visits: Number(r.visits) || 0, summary: Number(r.summary) === 1 })),
  };
}

const esc = (v: unknown): string =>
  String(v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] as string);

export function statsHtml(s: Stats): string {
  const table = (head: string[], rows: unknown[][]) =>
    `<table><thead><tr>${head.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows
      .map((r) => `<tr>${r.map((v) => `<td>${esc(v)}</td>`).join("")}</tr>`)
      .join("")}</tbody></table>`;
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex"><title>Video link stats</title>
<style>body{font:14px/1.5 system-ui,sans-serif;margin:16px;color:#111;background:#fff}table{border-collapse:collapse;margin:0 0 20px}th,td{border:1px solid #ccc;padding:4px 8px;text-align:left}td:nth-child(n+2){font-variant-numeric:tabular-nums}@media (prefers-color-scheme:dark){body{color:#eee;background:#111}th,td{border-color:#444}}</style></head><body>
<h1>Video link stats</h1><p>Generated ${esc(s.generated_at)}. Stored: ${esc(s.stored)}.</p>
<p>Total: ${esc(s.totals.human)} human clicks, ${esc(s.totals.bot)} link previews and bots.</p>
<h2>By source</h2>${table(["Source", "Human", "Previews and bots"], s.by_source.map((r) => [r.source, r.human, r.bot]))}
<h2>By day (UTC)</h2>${table(["Day", "Source", "Human", "Previews and bots"], s.by_day.map((r) => [r.day, r.source, r.human, r.bot]))}
<h2>Countries, last ${EVENT_DAYS} days</h2>${table(["Country", "Human", "Previews and bots"], s.countries_30d.map((r) => [r.country, r.human, r.bot]))}
</body></html>
`;
}

async function stats(request: Request, url: URL, env: Env): Promise<Response> {
  if (request.method !== "GET" || !(await authorized(request, env.STATS_KEY))) return notFound();
  if (!env.DB) return json({ error: "no database bound" }, 503);
  let data: Stats;
  try {
    data = await readStats(env.DB, Date.now());
  } catch (err) {
    logFailure("stats_failed", err);
    return json({ error: "stats unavailable" }, 503);
  }
  if (url.searchParams.get("format") !== "html") return json(data);
  return new Response(statsHtml(data), {
    headers: {
      "content-type": "text/html; charset=utf-8",
      "cache-control": "no-store",
      "content-security-policy": "default-src 'none'; style-src 'unsafe-inline'",
    },
  });
}

/** The cron (03:30 UTC, 09:00 IST): yesterday's summary email (once, and only for a day with clicks), then the 30-day prune. */
export async function daily(env: Env, now: number): Promise<void> {
  const db = env.DB;
  if (!db) return;
  const day = dayOf(now - DAY_MS);
  if (emailOn(env)) {
    try {
      const rows = await db.prepare("SELECT source, kind, n FROM clicks WHERE day = ?1").bind(day).all<{ source: string; kind: string; n: number }>();
      const sources = new Map<string, Row>();
      for (const r of rows.results ?? []) {
        const s = sources.get(r.source) ?? { source: r.source, human: 0, bot: 0 };
        s[r.kind === "human" ? "human" : "bot"] += Number(r.n) || 0;
        sources.set(r.source, s);
      }
      const any = [...sources.values()].some((s) => s.human + s.bot > 0);
      const claimed = any
        ? await db
            .prepare("INSERT INTO mail (day, summary) VALUES (?1, 1) ON CONFLICT (day) DO UPDATE SET summary = 1 WHERE summary = 0 RETURNING summary")
            .bind(day)
            .first<number>("summary")
        : null;
      if (claimed !== null && claimed !== undefined) {
        const start = `${day}T00:00:00Z`;
        const end = `${dayOf(now)}T00:00:00Z`;
        const countries = await db
          .prepare("SELECT country, COUNT(*) AS n FROM events WHERE ts >= ?1 AND ts < ?2 AND kind = 'human' GROUP BY country ORDER BY n DESC, country")
          .bind(start, end)
          .all<{ country: string; n: number }>();
        const emails = await db.prepare("SELECT visits FROM mail WHERE day = ?1").bind(day).first<number>("visits");
        const mail = summaryEmail({
          day,
          sources: [...sources.values()],
          countries: (countries.results ?? []).map((c) => ({ country: c.country, n: Number(c.n) || 0 })),
          emails: Number(emails ?? 0),
          cap: EMAIL_CAP,
        });
        await send(env, mail, now);
      }
    } catch (err) {
      logFailure("summary_failed", err);
    }
  }
  try {
    const cutoff = now - EVENT_DAYS * DAY_MS;
    await db.batch([
      db.prepare("DELETE FROM events WHERE ts < ?1").bind(tsOf(cutoff)),
      db.prepare("DELETE FROM mail WHERE day < ?1").bind(dayOf(cutoff)),
    ]);
  } catch (err) {
    logFailure("prune_failed", err);
  }
}

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname === "/stats" || url.pathname === "/stats/") return stats(request, url, env);
    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response(null, { status: 405, headers: { allow: "GET, HEAD", "cache-control": "no-store" } });
    }
    // HEAD (link checkers, the deploy smoke) gets the same redirect and is never counted.
    if (request.method === "GET" && !HOUSEKEEPING.test(url.pathname)) {
      try {
        const click: Click = { source: sourceOf(url), kind: kindOf(request), country: countryOf(request), at: Date.now() };
        ctx.waitUntil(record(env, click).catch((err) => logFailure("count_failed", err)));
      } catch (err) {
        logFailure("count_failed", err);
      }
    }
    return redirect();
  },

  async scheduled(controller: ScheduledController, env: Env): Promise<void> {
    await daily(env, controller.scheduledTime);
  },
} satisfies ExportedHandler<Env>;
