// The bot verdict and its reasons, the device parser, the daily visitor hash, the summary text and migration 0002 (src/visitor.ts,
// src/mail.ts, migrations/). Run with `npm test`.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { register } from "node:module";
import { DatabaseSync } from "node:sqlite";
import { test } from "node:test";

register("./hooks.mjs", import.meta.url);
const v = await import("../src/visitor.ts");
const m = await import("../src/mail.ts");

const CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36";
const KEY = "visitor-test-stats-key-0123456789";

const verdict = (over = {}) => v.verdictOf({ method: "GET", userAgent: CHROME, acceptLanguage: "en-US,en;q=0.9", purpose: "", ...over });

test("a person on a home or mobile network is a human with no reason", () => {
  assert.deepEqual(verdict(), { kind: "human", reason: "" });
  assert.deepEqual(verdict({ cf: { asn: 55836, asOrganization: "Reliance Jio Infocomm Limited" } }), { kind: "human", reason: "" });
  assert.deepEqual(verdict({ cf: { asn: 7922, asOrganization: "Comcast Cable Communications, LLC", botManagement: { verifiedBot: false } } }), { kind: "human", reason: "" });
});

test("each bot reason", () => {
  assert.deepEqual(verdict({ userAgent: "Slackbot-LinkExpanding 1.0" }), { kind: "bot", reason: "ua" });
  assert.deepEqual(verdict({ userAgent: "" }), { kind: "bot", reason: "ua" });
  assert.deepEqual(verdict({ purpose: "prefetch" }), { kind: "bot", reason: "ua" });
  assert.deepEqual(verdict({ cf: { botManagement: { verifiedBot: true } } }), { kind: "bot", reason: "verified-bot" });
  assert.deepEqual(verdict({ cf: { verifiedBotCategory: "Search Engine Crawler" } }), { kind: "bot", reason: "verified-bot" });
  assert.deepEqual(verdict({ method: "HEAD" }), { kind: "bot", reason: "head" });
  assert.deepEqual(verdict({ cf: { asn: 22843, asOrganization: "Proofpoint, Inc." } }), { kind: "bot", reason: "scanner" });
  assert.deepEqual(verdict({ cf: { asn: 0, asOrganization: "Mimecast Services Limited" } }), { kind: "bot", reason: "scanner" });
  assert.deepEqual(verdict({ cf: { asn: 16509, asOrganization: "Amazon.com, Inc." } }), { kind: "bot", reason: "datacenter" });
  assert.deepEqual(verdict({ cf: { asn: 64500, asOrganization: "Example Web Hosting Ltd" } }), { kind: "bot", reason: "datacenter" });
  assert.deepEqual(verdict({ acceptLanguage: "" }), { kind: "bot", reason: "no-accept-language" });
  assert.deepEqual(verdict({ acceptLanguage: "   " }), { kind: "bot", reason: "no-accept-language" });
});

test("the first matching rule wins: a bot user agent from a data centre is ua, a scanner network over a data-centre name is scanner", () => {
  assert.equal(verdict({ userAgent: "curl/8", cf: { asn: 16509 } }).reason, "ua");
  assert.equal(verdict({ method: "HEAD", cf: { asn: 16509 } }).reason, "head");
  assert.equal(verdict({ cf: { asn: 26211, asOrganization: "Proofpoint cloud" } }).reason, "scanner");
  assert.equal(verdict({ cf: { asn: 16509 }, acceptLanguage: "" }).reason, "datacenter");
});

test("every curated data-centre and scanner ASN is a bot with the right reason", () => {
  assert.deepEqual([...v.DATACENTER_ASNS.keys()].sort((a, b) => a - b), [8075, 12876, 13335, 14061, 14618, 15169, 16276, 16509, 20473, 24940, 31898, 32934, 45102, 51167, 63949, 132203, 396982]);
  assert.deepEqual([...v.SCANNER_ASNS.keys()].sort((a, b) => a - b), [15324, 22843, 26211, 30031]);
  for (const asn of v.DATACENTER_ASNS.keys()) assert.equal(verdict({ cf: { asn } }).reason, "datacenter", String(asn));
  for (const asn of v.SCANNER_ASNS.keys()) assert.equal(verdict({ cf: { asn } }).reason, "scanner", String(asn));
});

test("network names that say hosting are data centres; ordinary providers are not", () => {
  for (const org of ["Acme Hosting", "Foo Cloud Services", "Bar Data Center", "Bar Datacenter GmbH", "Bar Datacentre", "Baz Servers LLC", "Quux VPS", "Colo Colocation Inc"]) {
    assert.equal(verdict({ cf: { asOrganization: org } }).reason, "datacenter", org);
  }
  for (const org of ["Reliance Jio Infocomm", "Bharti Airtel", "Comcast Cable", "Deutsche Telekom AG", "AT&T Services"]) {
    assert.equal(verdict({ cf: { asOrganization: org } }).kind, "human", org);
  }
});

test("the device parser on common user agents", () => {
  const d = (ua) => v.deviceOf(ua);
  assert.deepEqual(d(CHROME), { device: "desktop", os: "Windows", browser: "Chrome" });
  assert.deepEqual(d("Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.6 Mobile/15E148 Safari/604.1"), { device: "phone", os: "iOS", browser: "Safari" });
  assert.deepEqual(d("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36"), { device: "phone", os: "Android", browser: "Chrome" });
  assert.deepEqual(d("Mozilla/5.0 (Linux; Android 13; SM-X710) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"), { device: "tablet", os: "Android", browser: "Chrome" });
  assert.deepEqual(d("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15"), { device: "desktop", os: "macOS", browser: "Safari" });
  assert.deepEqual(d("Mozilla/5.0 (iPad; CPU OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"), { device: "tablet", os: "iOS", browser: "Safari" });
  assert.deepEqual(d("Mozilla/5.0 (X11; Linux x86_64; rv:144.0) Gecko/20100101 Firefox/144.0"), { device: "desktop", os: "Linux", browser: "Firefox" });
  assert.deepEqual(d("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36 Edg/141.0.0.0"), { device: "desktop", os: "Windows", browser: "Edge" });
  assert.deepEqual(d("Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/25.0 Chrome/121.0.0.0 Mobile Safari/537.36"), { device: "phone", os: "Android", browser: "Samsung" });
  assert.deepEqual(d("Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"), { device: "desktop", os: "ChromeOS", browser: "Chrome" });
  assert.deepEqual(d("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/141.0.0.0 Mobile/15E148 Safari/604.1"), { device: "phone", os: "iOS", browser: "Chrome" });
  // Outlook, SafeLinks-like and script user agents: coarse words only.
  assert.deepEqual(d("Microsoft Office/16.0 (Windows NT 10.0; Microsoft Outlook 16.0.18025; Pro)"), { device: "desktop", os: "Windows", browser: "other" });
  assert.deepEqual(d("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Microsoft-SafeLinks"), { device: "desktop", os: "Windows", browser: "Chrome" });
  assert.deepEqual(d("curl/8.5.0"), { device: "unknown", os: "other", browser: "other" });
  assert.deepEqual(d(""), { device: "unknown", os: "other", browser: "other" });
  assert.equal(v.deviceLabel({ device: "desktop", os: "Windows", browser: "Chrome" }), "Windows desktop, Chrome");
  assert.equal(v.deviceLabel({ device: "unknown", os: "other", browser: "other" }), "other, other");
  assert.equal(v.deviceLabel({ device: "", os: "", browser: "" }), "unknown");
  assert.equal(v.networkLabel("Reliance Jio", 55836), "Reliance Jio (AS55836)");
  assert.equal(v.networkLabel("", 0), "unknown network");
});

test("Cloudflare's text is cleaned and kept short", () => {
  assert.equal(v.cleanText("  Pune\r\n<b> "), "Pune b");
  assert.equal(v.cleanText("x".repeat(500)).length, 64);
  assert.equal(v.cleanText(42), "");
  assert.equal(v.asnOf({ asn: "55836" }), 55836);
  assert.equal(v.asnOf({ asn: -1 }), 0);
});

test("the visitor hash is 16 hex characters, changes every day, differs by key and address, and is never or contains the IP", async () => {
  const ips = ["203.0.113.9", "198.51.100.23", "2001:db8:85a3::8a2e:370:7334", "2001:db8::1"];
  const seen = new Set();
  for (const ip of ips) {
    for (const day of ["2026-10-26", "2026-10-27", "2026-10-28"]) {
      const h = await v.visitorHash(ip, KEY, day);
      assert.match(h, /^[0-9a-f]{16}$/);
      assert.notEqual(h, ip);
      assert.ok(!h.includes(ip) && !ip.includes(h));
      assert.ok(!seen.has(h), "no two (address, day) pairs collide here");
      seen.add(h);
    }
  }
  const a = await v.visitorHash("203.0.113.9", KEY, "2026-10-26");
  assert.equal(a, await v.visitorHash("203.0.113.9", KEY, "2026-10-26"), "the same day gives the same value");
  assert.notEqual(a, await v.visitorHash("203.0.113.9", KEY, "2026-10-27"));
  assert.notEqual(a, await v.visitorHash("203.0.113.9", KEY + "x", "2026-10-26"));
  assert.notEqual(a, await v.visitorHash("203.0.113.10", KEY, "2026-10-26"));
  // No address or no key of 16 characters: no visitor is counted.
  assert.equal(await v.visitorHash("", KEY, "2026-10-26"), "");
  assert.equal(await v.visitorHash(null, KEY, "2026-10-26"), "");
  assert.equal(await v.visitorHash("203.0.113.9", "short", "2026-10-26"), "");
  assert.equal(await v.visitorHash("203.0.113.9", undefined, "2026-10-26"), "");
});

test("the visit email text", () => {
  const mail = m.visitEmail({
    source: "devpost",
    city: "Pune",
    country: "IN",
    network: "Reliance Jio (AS55836)",
    device: "Windows desktop, Chrome",
    at: Date.UTC(2026, 9, 26, 10, 12, 30),
    unique: 3,
    day: "2026-10-26",
    slot: 4,
    cap: 40,
  });
  assert.equal(mail.subject, "Ratchet video opened (Pune, IN, devpost)");
  assert.equal(
    mail.text,
    [
      "Someone opened the Ratchet video link.",
      "Where: Pune, IN",
      "Network: Reliance Jio (AS55836)",
      "Device: Windows desktop, Chrome",
      "Source: devpost",
      "Time (IST): 2026-10-26 15:42",
      "Unique humans today: 3 (UTC day 2026-10-26).",
      "",
      "Kept 30 days: country, city, network, coarse device and a daily-changing visitor hash. Never the IP address, user agent or a cookie.",
      "",
    ].join("\n"),
  );
  assert.equal(m.visitEmail({ source: "x", city: "", country: "US", network: "AS1", device: "unknown", at: 0, unique: 1, day: "d", slot: 1, cap: 40 }).subject, "Ratchet video opened (US, x)");
});

test("the daily summary text splits humans from bots by reason and lists the top 5 networks of each", () => {
  const nets = (names) => names.map((network, i) => ({ network, n: 10 - i }));
  const mail = m.summaryEmail({
    day: "2026-10-26",
    sources: [
      { source: "devpost", human: 2, bot: 5 },
      { source: "readme", human: 1, bot: 9 },
    ],
    reasons: [
      { reason: "head", n: 1 },
      { reason: "datacenter", n: 9 },
      { reason: "scanner", n: 4 },
      { reason: "", n: 2 },
    ],
    unique: 2,
    humanNetworks: nets(["Reliance Jio (AS55836)", "AT&T (AS7018)", "N3", "N4", "N5", "N6-hidden"]),
    botNetworks: nets(["Amazon.com, Inc. (AS16509)", "Microsoft Corporation (AS8075)"]),
    countries: [{ country: "IN", n: 2 }, { country: "US", n: 1 }],
    emails: 2,
    cap: 40,
  });
  assert.equal(mail.subject, "Ratchet video link, 2026-10-26 (UTC): 2 unique humans, 14 bot visits");
  assert.equal(
    mail.text,
    [
      "The Ratchet video link on 2026-10-26 (UTC day).",
      "Humans: 3 clicks, 2 unique",
      "Bots: 14",
      "",
      "Bots by reason:",
      "  datacenter: 9",
      "  scanner: 4",
      "  not recorded: 2",
      "  head: 1",
      "",
      "Top 5 networks, humans:",
      "  1. Reliance Jio (AS55836): 10",
      "  2. AT&T (AS7018): 9",
      "  3. N3: 8",
      "  4. N4: 7",
      "  5. N5: 6",
      "",
      "Top 5 networks, bots:",
      "  1. Amazon.com, Inc. (AS16509): 10",
      "  2. Microsoft Corporation (AS8075): 9",
      "",
      "By source (human / bots):",
      "  devpost: 2 / 5",
      "  readme: 1 / 9",
      "",
      "Countries (human clicks): IN 2, US 1",
      "Visit emails sent: 2 of 40.",
      "",
    ].join("\n"),
  );
  assert.doesNotMatch(mail.text, /N6-hidden/);
  const empty = m.summaryEmail({ day: "d", sources: [], reasons: [], unique: 0, humanNetworks: [], botNetworks: [], countries: [], emails: 0, cap: 40 });
  assert.match(empty.text, /Bots by reason:\n  none\n/);
  assert.match(empty.text, /Top 5 networks, humans:\n  none\n/);
});

test("migration 0002 adds the columns and the index to a database that already holds 0001 rows, which keep empty fields", () => {
  const sql = (name) => readFileSync(new URL(`../migrations/${name}`, import.meta.url), "utf8");
  const db = new DatabaseSync(":memory:");
  db.exec(sql("0001_init.sql"));
  db.prepare("INSERT INTO events (ts, source, country, kind) VALUES ('2026-10-01T00:00:00Z', 'devpost', 'IN', 'human')").run();
  db.exec(sql("0002_visitor_details.sql"));
  const cols = db.prepare("PRAGMA table_info(events)").all().map((c) => c.name);
  for (const c of ["city", "region", "asn", "org", "device", "os", "browser", "reason", "visitor"]) assert.ok(cols.includes(c), c);
  const old = db.prepare("SELECT * FROM events").get();
  assert.deepEqual({ ...old }, { ts: "2026-10-01T00:00:00Z", source: "devpost", country: "IN", kind: "human", city: "", region: "", asn: 0, org: "", device: "", os: "", browser: "", reason: "", visitor: "" });
  assert.ok(db.prepare("SELECT name FROM sqlite_master WHERE type = 'index' AND name = 'events_visitor'").get());
  // The migration holds no column for an IP address or a user agent.
  assert.ok(!cols.some((c) => /^(ip|ua|user_?agent|cookie)/i.test(c)));
});
