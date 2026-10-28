// The video link's short link, video.ratchet.dualmindlab.tech: every GET or HEAD is a 302 to the video on YouTube. Each GET and HEAD
// is counted in Cloudflare D1 by UTC day, source (?from=, an allowlist) and kind: a person ("human") or a bot, with the reason.
// Stored, per visit, for 30 days: the time, the source, the country, city and region and the network's name and number that
// Cloudflare reports, three coarse words for the device (desktop/phone/tablet, the OS family, the browser family), the bot reason
// (ua, datacenter, scanner, no-accept-language, head, verified-bot; empty for a human) and a visitor hash that changes every UTC day
// (16 hex characters of an HMAC of the IP with a salt derived from STATS_KEY and the day: it cannot follow anyone across days or be
// turned back into the IP). Never stored: the IP address, the full user agent, a cookie, the path or the raw query.
// Counting runs after the answer (ctx.waitUntil), so D1 can never slow or break the redirect.
// GET /stats reads the counts with `Authorization: Bearer <STATS_KEY>` (a Worker secret) and is a 404 without it.
// Email alerts (one per human visitor per UTC day, at most EMAIL_CAP per UTC day, and a summary of yesterday at 09:00 IST) stay off
// until EMAIL_ENABLED is "1". See docs/DEPLOYMENT.md, "The video link".
import { EmailMessage } from "cloudflare:email";
import { mime, plainAddress, summaryEmail, visitEmail, type Mail } from "./mail";
import { asnOf, cleanText, deviceLabel, deviceOf, networkLabel, verdictOf, visitorHash, type Cf, type Kind, type Reason } from "./visitor";

export type { Kind, Reason } from "./visitor";

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
/** Visit rows (every field of the events table) older than this are deleted by the daily cron. */
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

export interface Click {
  source: string;
  kind: Kind;
  country: string;
  at: number;
  /** Why a bot is one; "" for a human. The rest are the details of the visit, "" (asn 0) when unknown. */
  reason?: Reason;
  city?: string;
  region?: string;
  asn?: number;
  org?: string;
  device?: string;
  os?: string;
  browser?: string;
  visitor?: string;
}

export function sourceOf(url: URL): string {
  const raw = (url.searchParams.get("from") ?? "").trim().toLowerCase();
  if (raw === "") return "direct";
  return (SOURCES as readonly string[]).includes(raw) ? raw : "other";
}

const cfOf = (request: Request): Cf | undefined => (request as { cf?: Cf }).cf;

/** Human or bot, and why (the rules are in visitor.ts). The user agent and the headers are read here and never stored. */
export function verdictOfRequest(request: Request): { kind: Kind; reason: Reason } {
  const h = request.headers;
  return verdictOf({
    method: request.method,
    userAgent: h.get("user-agent") ?? "",
    acceptLanguage: h.get("accept-language") ?? "",
    purpose: [h.get("sec-purpose"), h.get("purpose"), h.get("x-purpose")].join(" "),
    cf: cfOf(request),
  });
}

/** "bot" for a preview, crawler, script, scanner, prefetch or HEAD; otherwise "human". */
export const kindOf = (request: Request): Kind => verdictOfRequest(request).kind;

/** Everything a visit keeps, from the request: never the IP address (only its daily hash) or the user agent (only three words). */
export async function clickOf(request: Request, env: Env, url: URL, at: number): Promise<Click> {
  const cf = cfOf(request);
  const { kind, reason } = verdictOfRequest(request);
  const d = deviceOf(request.headers.get("user-agent") ?? "");
  return {
    source: sourceOf(url),
    kind,
    reason,
    country: countryOf(request),
    city: cleanText(cf?.city),
    region: cleanText(cf?.region),
    asn: asnOf(cf),
    org: cleanText(cf?.asOrganization),
    device: d.device,
    os: d.os,
    browser: d.browser,
    visitor: await visitorHash(request.headers.get("cf-connecting-ip"), env.STATS_KEY, dayOf(at)),
    at,
  };
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

/** Count one visit, then (for a human, with email on, the day's first visit of that visitor, under the day's cap) send its alert. */
export async function record(env: Env, click: Click): Promise<void> {
  const db = env.DB;
  if (!db) return;
  const day = dayOf(click.at);
  const visitor = click.visitor ?? "";
  await db.batch([
    db
      .prepare("INSERT INTO clicks (day, source, kind, n) VALUES (?1, ?2, ?3, 1) ON CONFLICT (day, source, kind) DO UPDATE SET n = n + 1")
      .bind(day, click.source, click.kind),
    db
      .prepare(
        "INSERT INTO events (ts, source, country, kind, city, region, asn, org, device, os, browser, reason, visitor) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13)",
      )
      .bind(
        tsOf(click.at),
        click.source,
        click.country,
        click.kind,
        click.city ?? "",
        click.region ?? "",
        click.asn ?? 0,
        click.org ?? "",
        click.device ?? "",
        click.os ?? "",
        click.browser ?? "",
        click.reason ?? "",
        visitor,
      ),
  ]);
  if (click.kind !== "human" || !emailOn(env)) return;
  const start = `${day}T00:00:00Z`;
  const end = `${dayOf(click.at + DAY_MS)}T00:00:00Z`;
  // One email per visitor per UTC day: this visit is the visitor's first of the day only when it is their only row so far.
  if (visitor !== "") {
    const seen = await db
      .prepare("SELECT COUNT(*) AS n FROM events WHERE visitor = ?1 AND kind = 'human' AND ts >= ?2 AND ts < ?3")
      .bind(visitor, start, end)
      .first<number>("n");
    if (Number(seen ?? 0) > 1) return;
  }
  // Take one of the day's email slots in one statement: past the cap the UPSERT's WHERE fails and nothing is returned.
  const slot = await db
    .prepare("INSERT INTO mail (day, visits) VALUES (?1, 1) ON CONFLICT (day) DO UPDATE SET visits = visits + 1 WHERE visits < ?2 RETURNING visits")
    .bind(day, EMAIL_CAP)
    .first<number>("visits");
  if (slot === null || slot === undefined) return;
  const unique = await db
    .prepare("SELECT COUNT(DISTINCT visitor) AS n FROM events WHERE kind = 'human' AND visitor <> '' AND ts >= ?1 AND ts < ?2")
    .bind(start, end)
    .first<number>("n");
  const mail = visitEmail({
    source: click.source,
    city: click.city ?? "",
    country: click.country,
    network: networkLabel(click.org ?? "", click.asn ?? 0),
    device: deviceLabel(click),
    at: click.at,
    unique: Number(unique ?? 0),
    day,
    slot,
    cap: EMAIL_CAP,
  });
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
  /** Bot visits by reason; "" is a row from before reasons were kept. */
  bots_by_reason_30d: { reason: string; n: number }[];
  top_networks_30d: { human: { network: string; n: number }[]; bot: { network: string; n: number }[] };
  top_cities_30d: { city: string; country: string; n: number }[];
  devices_30d: { device: string; n: number }[];
  unique_humans_by_day: { day: string; n: number }[];
  emails: { day: string; visits: number; summary: boolean }[];
}

export async function readStats(db: D1Database, now: number): Promise<Stats> {
  const since = tsOf(now - EVENT_DAYS * DAY_MS);
  const [clicks, countries, mail, reasons, networks, cities, devices, uniques] = await Promise.all([
    db.prepare("SELECT day, source, kind, n FROM clicks ORDER BY day DESC, source").all<{ day: string; source: string; kind: string; n: number }>(),
    db
      .prepare("SELECT country, kind, COUNT(*) AS n FROM events WHERE ts >= ?1 GROUP BY country, kind")
      .bind(since)
      .all<{ country: string; kind: string; n: number }>(),
    db.prepare("SELECT day, visits, summary FROM mail ORDER BY day DESC").all<{ day: string; visits: number; summary: number }>(),
    db
      .prepare("SELECT reason, COUNT(*) AS n FROM events WHERE ts >= ?1 AND kind = 'bot' GROUP BY reason ORDER BY n DESC, reason")
      .bind(since)
      .all<{ reason: string; n: number }>(),
    db
      .prepare("SELECT kind, asn, org, COUNT(*) AS n FROM events WHERE ts >= ?1 GROUP BY kind, asn, org ORDER BY n DESC, org LIMIT 400")
      .bind(since)
      .all<{ kind: string; asn: number; org: string; n: number }>(),
    db
      .prepare("SELECT city, country, COUNT(*) AS n FROM events WHERE ts >= ?1 AND kind = 'human' GROUP BY city, country ORDER BY n DESC, city LIMIT 10")
      .bind(since)
      .all<{ city: string; country: string; n: number }>(),
    db
      .prepare("SELECT device, os, browser, COUNT(*) AS n FROM events WHERE ts >= ?1 AND kind = 'human' GROUP BY device, os, browser ORDER BY n DESC LIMIT 15")
      .bind(since)
      .all<{ device: string; os: string; browser: string; n: number }>(),
    db
      .prepare(
        "SELECT substr(ts, 1, 10) AS day, COUNT(DISTINCT visitor) AS n FROM events WHERE ts >= ?1 AND kind = 'human' AND visitor <> '' GROUP BY day ORDER BY day DESC",
      )
      .bind(since)
      .all<{ day: string; n: number }>(),
  ]);
  const topNetworks = (kind: string) =>
    (networks.results ?? [])
      .filter((r) => r.kind === kind)
      .slice(0, 5)
      .map((r) => ({ network: networkLabel(String(r.org ?? ""), Number(r.asn) || 0), n: Number(r.n) || 0 }));
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
    stored:
      "per visit, for 30 days: time, source, kind, bot reason, country, city, region, network name and number, device class, OS and browser family, and a visitor hash that changes every UTC day; never an IP address, the full user agent or a cookie",
    totals,
    by_source: [...bySource.values()].sort((a, b) => most(a, b) || a.source.localeCompare(b.source)),
    by_day: [...byDay.values()],
    countries_30d: [...byCountry.values()].sort((a, b) => most(a, b) || a.country.localeCompare(b.country)),
    bots_by_reason_30d: (reasons.results ?? []).map((r) => ({ reason: String(r.reason ?? ""), n: Number(r.n) || 0 })),
    top_networks_30d: { human: topNetworks("human"), bot: topNetworks("bot") },
    top_cities_30d: (cities.results ?? []).map((r) => ({ city: String(r.city ?? ""), country: String(r.country ?? ""), n: Number(r.n) || 0 })),
    devices_30d: (devices.results ?? []).map((r) => ({ device: deviceLabel(r), n: Number(r.n) || 0 })),
    unique_humans_by_day: (uniques.results ?? []).map((r) => ({ day: String(r.day), n: Number(r.n) || 0 })),
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
<p>Total: ${esc(s.totals.human)} human clicks, ${esc(s.totals.bot)} bots (link previews, scanners, crawlers).</p>
<h2>By source</h2>${table(["Source", "Human", "Previews and bots"], s.by_source.map((r) => [r.source, r.human, r.bot]))}
<h2>By day (UTC)</h2>${table(["Day", "Source", "Human", "Previews and bots"], s.by_day.map((r) => [r.day, r.source, r.human, r.bot]))}
<h2>Countries, last ${EVENT_DAYS} days</h2>${table(["Country", "Human", "Previews and bots"], s.countries_30d.map((r) => [r.country, r.human, r.bot]))}
<h2>Unique humans per day (UTC)</h2>${table(["Day", "Unique humans"], s.unique_humans_by_day.map((r) => [r.day, r.n]))}
<h2>Bots by reason, last ${EVENT_DAYS} days</h2>${table(["Reason", "Visits"], s.bots_by_reason_30d.map((r) => [r.reason || "not recorded", r.n]))}
<h2>Top networks, humans</h2>${table(["Network", "Visits"], s.top_networks_30d.human.map((r) => [r.network, r.n]))}
<h2>Top networks, bots</h2>${table(["Network", "Visits"], s.top_networks_30d.bot.map((r) => [r.network, r.n]))}
<h2>Cities and devices, humans</h2>${table(["City", "Country", "Visits"], s.top_cities_30d.map((r) => [r.city || "unknown", r.country, r.n]))}${table(["Device", "Visits"], s.devices_30d.map((r) => [r.device, r.n]))}
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
        const reasons = await db
          .prepare("SELECT reason, COUNT(*) AS n FROM events WHERE ts >= ?1 AND ts < ?2 AND kind = 'bot' GROUP BY reason")
          .bind(start, end)
          .all<{ reason: string; n: number }>();
        const networks = (kind: string) =>
          db
            .prepare("SELECT asn, org, COUNT(*) AS n FROM events WHERE ts >= ?1 AND ts < ?2 AND kind = ?3 GROUP BY asn, org ORDER BY n DESC, org LIMIT 5")
            .bind(start, end, kind)
            .all<{ asn: number; org: string; n: number }>();
        const [humanNets, botNets] = await Promise.all([networks("human"), networks("bot")]);
        const unique = await db
          .prepare("SELECT COUNT(DISTINCT visitor) AS n FROM events WHERE ts >= ?1 AND ts < ?2 AND kind = 'human' AND visitor <> ''")
          .bind(start, end)
          .first<number>("n");
        const named = (r: { asn: number; org: string; n: number }) => ({ network: networkLabel(String(r.org ?? ""), Number(r.asn) || 0), n: Number(r.n) || 0 });
        const emails = await db.prepare("SELECT visits FROM mail WHERE day = ?1").bind(day).first<number>("visits");
        const mail = summaryEmail({
          day,
          sources: [...sources.values()],
          reasons: (reasons.results ?? []).map((r) => ({ reason: String(r.reason ?? ""), n: Number(r.n) || 0 })),
          unique: Number(unique ?? 0),
          humanNetworks: (humanNets.results ?? []).map(named),
          botNetworks: (botNets.results ?? []).map(named),
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
    // A HEAD (link checkers, the deploy smoke) gets the same redirect and is counted as a bot (reason "head"), never as a person.
    if (!HOUSEKEEPING.test(url.pathname)) {
      try {
        const at = Date.now();
        ctx.waitUntil(
          clickOf(request, env, url, at)
            .then((click) => record(env, click))
            .catch((err) => logFailure("count_failed", err)),
        );
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
