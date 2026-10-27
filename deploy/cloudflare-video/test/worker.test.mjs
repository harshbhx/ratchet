// The video link Worker (src/index.ts) under Node with a fake D1 (test/fake-d1.mjs) and a stub `cloudflare:email`. Run with `npm test`.
import assert from "node:assert/strict";
import { register } from "node:module";
import { test } from "node:test";
import { brokenD1, fakeD1, hangingD1 } from "./fake-d1.mjs";

register("./hooks.mjs", import.meta.url);
const w = await import("../src/index.ts");
const m = await import("../src/mail.ts");
const worker = w.default;

const VIDEO = "https://youtu.be/GIeSmDtopfs";
const ORIGIN = "https://video.ratchet.dualmindlab.tech";
const KEY = "k".repeat(8) + "-stats-key-for-tests-0123456789";
const CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36";
const IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.6 Mobile/15E148 Safari/604.1";
const FIREFOX = "Mozilla/5.0 (X11; Linux x86_64; rv:144.0) Gecko/20100101 Firefox/144.0";

function req(path, { method = "GET", headers = {}, cf } = {}) {
  const r = new Request(ORIGIN + path, { method, headers: { "user-agent": CHROME, "accept-language": "en-US,en;q=0.9", ...headers } });
  if (cf) Object.defineProperty(r, "cf", { value: cf });
  return r;
}

/** Run one request; returns the response and waits for whatever the Worker left running after it (ctx.waitUntil). */
async function hit(env, path, opts) {
  const pending = [];
  const ctx = { waitUntil: (p) => pending.push(p), passThroughOnException() {} };
  const res = await worker.fetch(req(path, opts), env, ctx);
  await Promise.all(pending);
  return res;
}

function mailer({ fail = false } = {}) {
  const sent = [];
  return { sent, send: async (msg) => (fail ? Promise.reject(new Error("send failed")) : void sent.push(msg)) };
}

const counts = (db) => Object.fromEntries(db.dump().clicks.map((r) => [`${r.source}/${r.kind}`, r.n]));

function assertRedirect(res) {
  assert.equal(res.status, 302);
  assert.equal(res.headers.get("location"), VIDEO);
  assert.equal(res.headers.get("cache-control"), "no-store");
}

test("GET on any path is a 302 to the video with no-store, and is counted", async () => {
  const db = fakeD1();
  for (const path of ["/", "/?from=devpost", "/anything/at/all", "/x?y=1"]) assertRedirect(await hit({ DB: db }, path));
  assert.deepEqual(counts(db), { "devpost/human": 1, "direct/human": 3 });
  assert.equal(db.dump().events.length, 4);
});

test("HEAD gets the same redirect and is counted as a bot, never emailed; other methods are a 405", async () => {
  const db = fakeD1();
  const email = mailer();
  const env = { DB: db, EMAIL: email, EMAIL_ENABLED: "1", EMAIL_FROM: "video-alerts@mail.dualmindlab.tech", EMAIL_TO: "owner@example.com" };
  assertRedirect(await hit(env, "/?from=readme", { method: "HEAD" }));
  assert.deepEqual(counts(db), { "readme/bot": 1 });
  assert.deepEqual(db.dump().events.map((e) => [e.kind, e.reason]), [["bot", "head"]]);
  assert.equal(email.sent.length, 0);
  const post = await hit(env, "/", { method: "POST" });
  assert.equal(post.status, 405);
  assert.equal(post.headers.get("allow"), "GET, HEAD");
});

test("?from= is held to the allowlist: missing or empty is direct, anything else other", () => {
  const src = (q) => w.sourceOf(new URL(ORIGIN + "/" + q));
  for (const s of ["devpost", "readme", "site", "live", "demo", "docs", "hf", "youtube", "x", "linkedin", "discord", "email", "release", "judges", "other"]) {
    assert.equal(src(`?from=${s}`), s);
  }
  assert.equal(src("?from=DevPost"), "devpost");
  assert.equal(src("?from=%20hf%20"), "hf");
  assert.equal(src(""), "direct");
  assert.equal(src("?from="), "direct");
  assert.equal(src("?utm_source=devpost"), "direct");
  for (const bad of ["smoke", "evil<script>", "devpost2", "'; DROP TABLE clicks;--", "a".repeat(5000)]) assert.equal(src(`?from=${encodeURIComponent(bad)}`), "other");
});

test("the raw query, path, IP address, user agent, cookie and referrer are never stored", async () => {
  const db = fakeD1();
  const headers = {
    "user-agent": "Mozilla/5.0 UniqueAgentMarker/9.9 Safari/537.36",
    "cf-connecting-ip": "203.0.113.77",
    "x-forwarded-for": "198.51.100.23",
    "x-real-ip": "192.0.2.5",
    cookie: "session=cookie-marker-abc",
    referer: "https://referrer-marker.example/page",
  };
  const cf = { country: "IN", city: "Pune", region: "Maharashtra", asn: 55836, asOrganization: "Reliance Jio" };
  await hit({ DB: db, STATS_KEY: KEY }, "/path-marker?from=evilmarker&utm_campaign=querymarker&from=devpost", { headers, cf });
  const stored = JSON.stringify(db.dump());
  for (const marker of ["UniqueAgentMarker", "203.0.113.77", "198.51.100.23", "192.0.2.5", "cookie-marker", "referrer-marker", "path-marker", "querymarker", "evilmarker", "Mozilla", "Safari", "537.36"]) {
    assert.ok(!stored.includes(marker), marker);
  }
  const [event] = db.dump().events;
  assert.deepEqual(Object.keys(event).sort(), ["asn", "browser", "city", "country", "device", "kind", "org", "os", "reason", "region", "source", "ts", "visitor"]);
  assert.equal(event.source, "other");
  assert.equal(event.country, "IN");
  assert.deepEqual([event.city, event.region, event.asn, event.org], ["Pune", "Maharashtra", 55836, "Reliance Jio"]);
  assert.deepEqual([event.kind, event.reason, event.device, event.os, event.browser], ["human", "", "unknown", "other", "other"]);
  assert.match(event.visitor, /^[0-9a-f]{16}$/);
  assert.match(event.ts, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/);
});

test("country is the two letters Cloudflare gives, else XX", () => {
  assert.equal(w.countryOf(req("/", { cf: { country: "US" } })), "US");
  assert.equal(w.countryOf(req("/", { cf: { country: "T1" } })), "T1");
  for (const bad of [undefined, "", "usa", "u", "IN\n", 42]) assert.equal(w.countryOf(req("/", { cf: { country: bad } })), "XX", String(bad));
  assert.equal(w.countryOf(req("/")), "XX");
});

test("previews, crawlers, scripts and checkers count as bots; browsers as humans", () => {
  const kind = (ua, extra = {}) => w.kindOf(req("/", { headers: { "user-agent": ua, ...extra } }));
  const bots = [
    "Slackbot-LinkExpanding 1.0 (+https://api.slack.com/robots)",
    "Mozilla/5.0 (compatible; Twitterbot/1.0)",
    "Mozilla/5.0 (compatible; Discordbot/2.0; +https://discordapp.com)",
    "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
    "LinkedInBot/1.0 (compatible; Mozilla/5.0; Apache-HttpClient +http://www.linkedin.com)",
    "WhatsApp/2.23.20.0",
    "TelegramBot (like TwitterBot)",
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
    "Mozilla/5.0 (Macintosh) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15 (Applebot/0.1)",
    "curl/8.5.0",
    "python-requests/2.32.3",
    "Go-http-client/2.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/141.0.0.0 Safari/537.36",
    "Mozilla/5.0 (compatible; SomeCrawler/1.0)",
    "MySpider 3.1",
    "SkypeUriPreview Preview/0.5",
    "Mozilla/5.0 (compatible; ratchet-link-check/1)",
    "ratchet-video-smoke/1",
    "Wget/1.21",
    "",
  ];
  for (const ua of bots) assert.equal(kind(ua), "bot", ua);
  for (const ua of [CHROME, IPHONE, FIREFOX]) assert.equal(kind(ua), "human", ua);
  assert.equal(kind(CHROME, { "sec-purpose": "prefetch;prerender" }), "bot");
  assert.equal(kind(CHROME, { purpose: "prefetch" }), "bot");
  assert.equal(w.kindOf(req("/", { cf: { verifiedBotCategory: "Search Engine Crawler" } })), "bot");
  assert.equal(w.kindOf(req("/", { cf: { verifiedBotCategory: "" } })), "human");
});

test("the repository's link check (HEAD or GET) and the deploy smoke never count as human and never send an email", async () => {
  const db = fakeD1();
  const email = mailer();
  const env = { DB: db, EMAIL: email, EMAIL_ENABLED: "1", EMAIL_FROM: "video-alerts@mail.dualmindlab.tech", EMAIL_TO: "owner@example.com" };
  const checker = { "user-agent": "Mozilla/5.0 (compatible; ratchet-link-check/1)", accept: "*/*" };
  for (const path of ["/?from=readme", "/?from=docs"]) {
    assertRedirect(await hit(env, path, { method: "HEAD", headers: checker }));
    assertRedirect(await hit(env, path, { headers: checker }));
  }
  assertRedirect(await hit(env, "/?from=smoke", { method: "HEAD", headers: { "user-agent": "ratchet-video-smoke/1" } }));
  assertRedirect(await hit(env, "/?from=smoke", { headers: { "user-agent": "ratchet-video-smoke/1" } }));
  assert.deepEqual(counts(db), { "readme/bot": 2, "docs/bot": 2, "other/bot": 2 });
  assert.equal(email.sent.length, 0);
  assert.deepEqual(db.dump().mail, []);
  assert.ok(db.dump().events.every((e) => e.kind === "bot"));
});

test("robots.txt, favicon and .well-known are redirected but not counted", async () => {
  const db = fakeD1();
  for (const path of ["/robots.txt", "/favicon.ico", "/apple-touch-icon-precomposed.png", "/.well-known/security.txt"]) assertRedirect(await hit({ DB: db }, path));
  assert.deepEqual(db.dump().clicks, []);
});

test("D1 down, hanging or missing: the redirect still goes out", async () => {
  const logs = [];
  const log = console.log;
  console.log = (line) => logs.push(line);
  try {
    assertRedirect(await hit({ DB: brokenD1() }, "/?from=devpost"));
    assert.ok(logs.some((l) => l.includes("count_failed") && l.includes("D1_ERROR")));
    assertRedirect(await hit({}, "/?from=devpost"));
    // A D1 call that never answers: the redirect is returned at once; the count is left to ctx.waitUntil.
    const pending = [];
    const res = await worker.fetch(req("/?from=devpost"), { DB: hangingD1() }, { waitUntil: (p) => pending.push(p) });
    assertRedirect(res);
    assert.equal(pending.length, 1);
    // A context whose waitUntil throws cannot break it either.
    assertRedirect(await worker.fetch(req("/"), { DB: fakeD1() }, { waitUntil: () => { throw new Error("no context"); } }));
  } finally {
    console.log = log;
  }
  for (const line of logs) assert.doesNotMatch(line, /203\.0\.113|Mozilla|cookie/i);
});

test("counts add up per UTC day, source and kind with the UPSERT", async () => {
  const db = fakeD1();
  const at = Date.UTC(2026, 9, 26, 10, 0, 0);
  for (let i = 0; i < 3; i++) await w.record({ DB: db }, { source: "devpost", kind: "human", country: "IN", at });
  await w.record({ DB: db }, { source: "devpost", kind: "bot", country: "US", at });
  await w.record({ DB: db }, { source: "devpost", kind: "human", country: "IN", at: at + 86_400_000 });
  const rows = db.dump().clicks.map((r) => `${r.day} ${r.source} ${r.kind} ${r.n}`).sort();
  assert.deepEqual(rows, ["2026-10-26 devpost bot 1", "2026-10-26 devpost human 3", "2026-10-27 devpost human 1"]);
});

// ---- /stats ----

async function seeded() {
  const db = fakeD1();
  const now = Date.now();
  const jio = { city: "Pune", region: "Maharashtra", asn: 55836, org: "Reliance Jio", device: "desktop", os: "Windows", browser: "Chrome", visitor: "aaaaaaaaaaaaaaaa" };
  await w.record({ DB: db }, { source: "devpost", kind: "human", country: "IN", at: now, ...jio });
  await w.record({ DB: db }, { source: "devpost", kind: "human", country: "US", at: now, device: "phone", os: "iOS", browser: "Safari", visitor: "bbbbbbbbbbbbbbbb" });
  await w.record({ DB: db }, { source: "readme", kind: "bot", country: "US", at: now, reason: "datacenter", asn: 16509, org: "Amazon.com, Inc." });
  return db;
}

test("/stats is a 404 without the right bearer key, and is never counted", async () => {
  const db = await seeded();
  const before = JSON.stringify(db.dump());
  for (const headers of [{}, { authorization: "Bearer wrong-key-0123456789abcdef" }, { authorization: KEY }, { authorization: `Basic ${KEY}` }, { authorization: "Bearer " }]) {
    const res = await hit({ DB: db, STATS_KEY: KEY }, "/stats", { headers });
    assert.equal(res.status, 404, JSON.stringify(headers));
    assert.equal(await res.text(), "Not found\n");
  }
  // No key configured, or one too short to be a key: closed whatever is sent.
  for (const k of [undefined, "", "short"]) {
    assert.equal((await hit({ DB: db, STATS_KEY: k }, "/stats", { headers: { authorization: `Bearer ${k ?? ""}` } })).status, 404);
  }
  // Right key, wrong method.
  assert.equal((await hit({ DB: db, STATS_KEY: KEY }, "/stats", { method: "POST", headers: { authorization: `Bearer ${KEY}` } })).status, 404);
  assert.equal(JSON.stringify(db.dump()), before);
});

test("/stats with the key: totals per day and source, human and bot apart, never an IP or user agent", async () => {
  const db = await seeded();
  for (const scheme of ["Bearer", "bearer"]) {
    const res = await hit({ DB: db, STATS_KEY: `${KEY}\n` }, "/stats", { headers: { authorization: `${scheme} ${KEY}` } });
    assert.equal(res.status, 200);
    assert.equal(res.headers.get("cache-control"), "no-store");
    const s = await res.json();
    assert.equal(s.video, VIDEO);
    assert.deepEqual(s.totals, { human: 2, bot: 1 });
    assert.deepEqual(s.by_source, [
      { source: "devpost", human: 2, bot: 0 },
      { source: "readme", human: 0, bot: 1 },
    ]);
    assert.deepEqual(s.by_day.map((r) => [r.source, r.human, r.bot]), [["devpost", 2, 0], ["readme", 0, 1]]);
    assert.deepEqual(s.countries_30d, [
      { country: "US", human: 1, bot: 1 },
      { country: "IN", human: 1, bot: 0 },
    ]);
    assert.deepEqual(s.bots_by_reason_30d, [{ reason: "datacenter", n: 1 }]);
    assert.deepEqual(s.top_networks_30d.human, [{ network: "unknown network", n: 1 }, { network: "Reliance Jio (AS55836)", n: 1 }]);
    assert.deepEqual(s.top_networks_30d.bot, [{ network: "Amazon.com, Inc. (AS16509)", n: 1 }]);
    assert.deepEqual(s.top_cities_30d.map((c) => [c.city, c.n]), [["", 1], ["Pune", 1]]);
    assert.deepEqual(s.devices_30d.map((d) => d.device).sort(), ["Windows desktop, Chrome", "iOS phone, Safari"]);
    assert.deepEqual(s.unique_humans_by_day, [{ day: w.dayOf(Date.now()), n: 2 }]);
    assert.match(s.stored, /never an IP address, the full user agent or a cookie/);
  }
  const html = await hit({ DB: db, STATS_KEY: KEY }, "/stats?format=html", { headers: { authorization: `Bearer ${KEY}` } });
  assert.equal(html.status, 200);
  assert.match(html.headers.get("content-type"), /text\/html/);
  assert.match(html.headers.get("content-security-policy"), /default-src 'none'/);
  const page = await html.text();
  assert.match(page, /<td>devpost<\/td><td>2<\/td><td>0<\/td>/);
  assert.match(page, /<td>datacenter<\/td><td>1<\/td>/);
  assert.match(page, /Reliance Jio \(AS55836\)/);
  assert.doesNotMatch(page, /<script/i);
});

test("/stats with D1 down is a 503, not a crash", async () => {
  const log = console.log;
  console.log = () => {};
  try {
    const res = await hit({ DB: brokenD1(), STATS_KEY: KEY }, "/stats", { headers: { authorization: `Bearer ${KEY}` } });
    assert.equal(res.status, 503);
    assert.deepEqual(await res.json(), { error: "stats unavailable" });
  } finally {
    console.log = log;
  }
});

// ---- email ----

const ON = (db, email) => ({ DB: db, EMAIL: email, EMAIL_ENABLED: "1", EMAIL_FROM: "video-alerts@mail.dualmindlab.tech", EMAIL_TO: "owner@example.com" });

test("email is off unless EMAIL_ENABLED is 1 with a binding and both addresses", async () => {
  const email = mailer();
  const env = ON(fakeD1(), email);
  for (const off of [{ EMAIL_ENABLED: "0" }, { EMAIL_ENABLED: undefined }, { EMAIL_ENABLED: "true" }, { EMAIL: undefined }, { EMAIL_TO: "" }, { EMAIL_FROM: "not an address" }, { EMAIL_TO: "a@b.c\r\nBcc: x@y.z" }]) {
    assert.equal(w.emailOn({ ...env, ...off }), false, JSON.stringify(off));
    assertRedirect(await hit({ ...env, ...off }, "/?from=devpost", { cf: { country: "IN" } }));
  }
  assert.equal(email.sent.length, 0);
  assert.equal(w.emailOn(env), true);
});

test("a human visit sends one short plain-text email: city, network, device, source, IST time, unique humans; no IP or user agent", async () => {
  const db = fakeD1();
  const email = mailer();
  const env = { ...ON(db, email), STATS_KEY: KEY };
  const cf = { country: "IN", city: "Pune", region: "Maharashtra", asn: 55836, asOrganization: "Reliance Jio" };
  await hit(env, "/?from=devpost", { cf, headers: { "cf-connecting-ip": "203.0.113.9" } });
  await hit(env, "/?from=readme", { headers: { "user-agent": "Slackbot-LinkExpanding 1.0", "cf-connecting-ip": "198.51.100.1" } }); // a preview: no email
  await hit(env, "/?from=readme", { cf: { country: "US", city: "Austin", asn: 7018, asOrganization: "AT&T" }, headers: { "user-agent": IPHONE, "cf-connecting-ip": "203.0.113.10" } });
  assert.equal(email.sent.length, 2);
  const [first, second] = email.sent;
  assert.equal(first.from, "video-alerts@mail.dualmindlab.tech");
  assert.equal(first.to, "owner@example.com");
  assert.match(first.raw, /^From: Ratchet video link <video-alerts@mail\.dualmindlab\.tech>\r\nTo: owner@example\.com\r\nSubject: Ratchet video opened \(Pune, IN, devpost\)\r\n/);
  assert.match(first.raw, /\r\nMessage-ID: <[0-9a-f-]{36}@mail\.dualmindlab\.tech>\r\n/);
  assert.match(
    first.raw,
    /\r\n\r\nSomeone opened the Ratchet video link\.\r\nWhere: Pune, IN\r\nNetwork: Reliance Jio \(AS55836\)\r\nDevice: Windows desktop, Chrome\r\nSource: devpost\r\nTime \(IST\): \d{4}-\d{2}-\d{2} \d{2}:\d{2}\r\nUnique humans today: 1 \(UTC day \d{4}-\d{2}-\d{2}\)\.\r\n/,
  );
  assert.match(second.raw, /Where: Austin, US\r\nNetwork: AT&T \(AS7018\)\r\nDevice: iOS phone, Safari\r\n/);
  assert.match(second.raw, /Unique humans today: 2 /);
  for (const msg of email.sent) assert.doesNotMatch(msg.raw, /203\.0\.113|198\.51|Mozilla|AppleWebKit|Slackbot/);
});

test("only humans email, and one email per visitor per UTC day; a new day or a new visitor emails again", async () => {
  const db = fakeD1();
  const email = mailer();
  const env = { ...ON(db, email), STATS_KEY: KEY };
  const human = (ip) => ({ headers: { "cf-connecting-ip": ip }, cf: { country: "IN", city: "Pune", asn: 55836, asOrganization: "Reliance Jio" } });
  await hit(env, "/?from=devpost", human("203.0.113.9"));
  await hit(env, "/?from=readme", human("203.0.113.9")); // the same person again: counted, no second email
  await hit(env, "/", human("203.0.113.9"));
  assert.equal(email.sent.length, 1);
  await hit(env, "/", human("203.0.113.10")); // another person
  assert.equal(email.sent.length, 2);
  assert.match(email.sent[1].raw, /Unique humans today: 2 /);
  // Bots never email: a data-centre network, a scanner, a missing Accept-Language, a HEAD, a verified bot.
  await hit(env, "/", { headers: { "cf-connecting-ip": "203.0.113.50" }, cf: { country: "US", asn: 16509, asOrganization: "Amazon.com, Inc." } });
  await hit(env, "/", { headers: { "cf-connecting-ip": "203.0.113.51" }, cf: { country: "US", asn: 22843, asOrganization: "Proofpoint" } });
  await hit(env, "/", { headers: { "cf-connecting-ip": "203.0.113.52", "accept-language": "" } });
  await hit(env, "/", { method: "HEAD", headers: { "cf-connecting-ip": "203.0.113.53" } });
  await hit(env, "/", { headers: { "cf-connecting-ip": "203.0.113.54" }, cf: { botManagement: { verifiedBot: true } } });
  assert.equal(email.sent.length, 2);
  const rows = db.dump().events;
  assert.equal(rows.length, 9);
  assert.deepEqual(rows.filter((r) => r.kind === "bot").map((r) => r.reason), ["datacenter", "scanner", "no-accept-language", "head", "verified-bot"]);
  assert.equal(new Set(rows.filter((r) => r.kind === "human").map((r) => r.visitor)).size, 2);
  assert.ok(!JSON.stringify(db.dump()).includes("203.0.113"));
  // The next UTC day the same person is a new visitor and may email again.
  const at = Date.now();
  const tomorrow = at + 86_400_000;
  const day2 = await w.clickOf(req("/", human("203.0.113.9")), env, new URL(ORIGIN + "/"), tomorrow);
  const day1 = await w.clickOf(req("/", human("203.0.113.9")), env, new URL(ORIGIN + "/"), at);
  assert.notEqual(day1.visitor, day2.visitor);
  await w.record(env, day2);
  assert.equal(email.sent.length, 3);
  assert.match(email.sent[2].raw, /Unique humans today: 1 /);
});

test("visit emails stop at 40 per UTC day; the 40th says so; counting goes on", async () => {
  const db = fakeD1();
  const email = mailer();
  const at = Date.UTC(2026, 9, 26, 10, 0, 0);
  for (let i = 0; i < 45; i++) await w.record(ON(db, email), { source: "devpost", kind: "human", country: "IN", at: at + i * 1000 });
  assert.equal(email.sent.length, 40);
  assert.match(email.sent[39].raw, /visit email 40 of 40 for this UTC day/);
  assert.doesNotMatch(email.sent[38].raw, /of 40/);
  assert.equal(counts(db)["devpost/human"], 45);
  assert.deepEqual(db.dump().mail.map((r) => ({ ...r })), [{ day: "2026-10-26", visits: 40, summary: 0 }]);
  // The next UTC day starts a new allowance.
  await w.record(ON(db, email), { source: "devpost", kind: "human", country: "IN", at: at + 86_400_000 });
  assert.equal(email.sent.length, 41);
});

test("a failed send or a missing binding never touches the redirect or the count", async () => {
  const log = console.log;
  console.log = () => {};
  try {
    const db = fakeD1();
    assertRedirect(await hit(ON(db, mailer({ fail: true })), "/?from=devpost"));
    assertRedirect(await hit({ ...ON(db, undefined), EMAIL: undefined }, "/?from=devpost"));
    assert.equal(counts(db)["devpost/human"], 2);
  } finally {
    console.log = log;
  }
});

test("IST is UTC+5:30", () => {
  assert.equal(m.istTime(Date.UTC(2026, 9, 26, 10, 12, 30)), "2026-10-26 15:42");
  assert.equal(m.istTime(Date.UTC(2026, 9, 26, 20, 0, 0)), "2026-10-27 01:30");
});

// ---- the daily cron ----

const CRON = Date.UTC(2026, 9, 27, 3, 30, 0); // 09:00 IST on Oct 27: the summary is of Oct 26 (UTC)

test("the cron emails yesterday's summary once, then prunes events older than 30 days", async () => {
  const db = fakeD1();
  const quiet = mailer();
  const day = Date.UTC(2026, 9, 26, 12, 0, 0);
  const jio = { asn: 55836, org: "Reliance Jio" };
  await w.record({ DB: db }, { source: "devpost", kind: "human", country: "IN", at: day, visitor: "aaaaaaaaaaaaaaaa", ...jio });
  await w.record({ DB: db }, { source: "devpost", kind: "human", country: "IN", at: day + 1000, visitor: "aaaaaaaaaaaaaaaa", ...jio });
  await w.record({ DB: db }, { source: "readme", kind: "human", country: "US", at: day + 2000, visitor: "bbbbbbbbbbbbbbbb", asn: 7018, org: "AT&T" });
  await w.record({ DB: db }, { source: "readme", kind: "bot", country: "US", at: day + 3000, reason: "datacenter", asn: 16509, org: "Amazon.com, Inc." });
  await w.record({ DB: db }, { source: "readme", kind: "bot", country: "US", at: day + 4000, reason: "datacenter", asn: 16509, org: "Amazon.com, Inc." });
  await w.record({ DB: db }, { source: "readme", kind: "bot", country: "DE", at: day + 5000, reason: "head" });
  await w.record({ DB: db }, { source: "hf", kind: "human", country: "DE", at: Date.UTC(2026, 9, 27, 1, 0, 0) }); // today: not in the summary
  await w.record({ DB: db }, { source: "x", kind: "human", country: "FR", at: CRON - 31 * 86_400_000 }); // older than 30 days
  const email = mailer();
  await w.daily(ON(db, email), CRON);
  assert.equal(email.sent.length, 1);
  const raw = email.sent[0].raw;
  assert.match(raw, /Subject: Ratchet video link, 2026-10-26 \(UTC\): 2 unique humans, 3 bot visits\r\n/);
  assert.match(raw, /Humans: 3 clicks, 2 unique\r\nBots: 3\r\n/);
  assert.match(raw, /Bots by reason:\r\n  datacenter: 2\r\n  head: 1\r\n/);
  assert.match(raw, /Top 5 networks, humans:\r\n  1\. Reliance Jio \(AS55836\): 2\r\n  2\. AT&T \(AS7018\): 1\r\n/);
  assert.match(raw, /Top 5 networks, bots:\r\n  1\. Amazon\.com, Inc\. \(AS16509\): 2\r\n  2\. unknown network: 1\r\n/);
  assert.match(raw, /  devpost: 2 \/ 0\r\n  readme: 1 \/ 3\r\n/);
  assert.match(raw, /Countries \(human clicks\): IN 2, US 1\r\n/);
  assert.doesNotMatch(raw, /hf|DE|FR/);
  // Once per day, also when the cron runs again.
  await w.daily(ON(db, email), CRON + 60_000);
  assert.equal(email.sent.length, 1);
  // Events older than 30 days are gone; the daily counts stay.
  const events = db.dump().events;
  assert.equal(events.length, 7);
  assert.ok(events.every((e) => e.ts >= "2026-09-27"));
  assert.equal(counts(db)["x/human"], 1);
  assert.equal(quiet.sent.length, 0);
});

test("the cron sends nothing for a day without clicks or with email off, and still prunes", async () => {
  const db = fakeD1();
  await w.record({ DB: db }, { source: "x", kind: "human", country: "FR", at: CRON - 40 * 86_400_000 });
  const email = mailer();
  await w.daily(ON(db, email), CRON);
  assert.equal(email.sent.length, 0);
  assert.equal(db.dump().events.length, 0);
  await w.record({ DB: db }, { source: "devpost", kind: "human", country: "IN", at: CRON - 86_400_000 });
  await w.daily({ DB: db }, CRON);
  assert.equal(email.sent.length, 0);
  await w.daily({}, CRON); // no database bound: nothing to do, no throw
});

test("the scheduled handler runs the daily job at the trigger's time", async () => {
  const db = fakeD1();
  await w.record({ DB: db }, { source: "devpost", kind: "human", country: "IN", at: CRON - 6 * 3600_000 }); // 21:30 UTC the day before
  const email = mailer();
  await worker.scheduled({ scheduledTime: CRON, cron: "30 3 * * *" }, ON(db, email), { waitUntil() {} });
  assert.equal(email.sent.length, 1);
});
