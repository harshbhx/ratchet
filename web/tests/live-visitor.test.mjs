// The live Worker's visitor address and daily per-visitor limit (deploy/cloudflare-live/src/visitor.ts). Run with `npm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";

const v = await import("../../deploy/cloudflare-live/src/visitor.ts");

const TOKEN = "t".repeat(40);
const h = (o) => new Headers(o);

test("x-ratchet-client-ip is trusted only with the right token", async () => {
  const headers = h({ "x-ratchet-edge": TOKEN, "x-ratchet-client-ip": "203.0.113.7", "cf-connecting-ip": "172.70.0.1" });
  assert.equal(await v.visitorIp(headers, TOKEN), "203.0.113.7");
  assert.equal(await v.visitorIp(headers, `  ${TOKEN}\n`), "203.0.113.7", "a secret piped in with a line break still matches");
  assert.equal(await v.visitorIp(h({ "x-ratchet-edge": TOKEN, "x-ratchet-client-ip": "2001:db8::1" }), TOKEN), "2001:db8::1");
});

test("a wrong token, no token, or no EDGE_TOKEN falls back to cf-connecting-ip", async () => {
  const base = { "x-ratchet-client-ip": "203.0.113.7", "cf-connecting-ip": "172.70.0.1" };
  assert.equal(await v.visitorIp(h({ ...base, "x-ratchet-edge": "wrong" }), TOKEN), "172.70.0.1");
  assert.equal(await v.visitorIp(h({ ...base, "x-ratchet-edge": TOKEN + "x" }), TOKEN), "172.70.0.1");
  assert.equal(await v.visitorIp(h(base), TOKEN), "172.70.0.1");
  for (const unset of [undefined, "", "   ", null, 5]) {
    assert.equal(await v.visitorIp(h({ ...base, "x-ratchet-edge": "anything" }), unset), "172.70.0.1", String(unset));
    assert.equal(await v.visitorIp(h({ ...base, "x-ratchet-edge": "" }), unset), "172.70.0.1", `empty header, ${String(unset)}`);
  }
  assert.equal(await v.visitorIp(h({}), TOKEN), null);
});

test("a trusted but malformed address is not used", async () => {
  for (const bad of ["not an ip", "1.2.3.4, 5.6.7.8", "<script>", "x".repeat(80), ""]) {
    const headers = h({ "x-ratchet-edge": TOKEN, "x-ratchet-client-ip": bad, "cf-connecting-ip": "172.70.0.1" });
    assert.equal(await v.visitorIp(headers, TOKEN), "172.70.0.1", bad);
  }
});

test("tokenMatches compares the whole value", async () => {
  assert.equal(await v.tokenMatches(TOKEN, TOKEN), true);
  assert.equal(await v.tokenMatches(TOKEN.slice(0, -1), TOKEN), false);
  assert.equal(await v.tokenMatches(null, TOKEN), false);
  assert.equal(await v.tokenMatches("", ""), false);
});

test("the edge headers are stripped before forwarding, and x-forwarded-for is the visitor's address", () => {
  const headers = h({
    "x-ratchet-edge": TOKEN,
    "x-ratchet-client-ip": "203.0.113.7",
    "x-forwarded-for": "6.6.6.6",
    "cf-connecting-ip": "172.70.0.1",
    cookie: "a=b",
  });
  const out = v.forwardedHeaders(headers, "203.0.113.7");
  assert.equal(out.get("x-ratchet-edge"), null);
  assert.equal(out.get("x-ratchet-client-ip"), null);
  assert.equal(out.get("x-forwarded-for"), "203.0.113.7");
  assert.equal(out.get("cookie"), "a=b");
  assert.equal(headers.get("x-ratchet-edge"), TOKEN, "the original is untouched");
  const none = v.forwardedHeaders(headers, null);
  assert.equal(none.get("x-forwarded-for"), null, "never a visitor's own x-forwarded-for");
  assert.equal(none.get("x-ratchet-edge"), null);
});

// ---- the daily limit ----

/** An in-memory stand-in for a Durable Object's storage. */
function memoryStore() {
  const data = new Map();
  let alarm = null;
  return {
    data,
    alarm: () => alarm,
    get: async (k) => data.get(k),
    put: async (k, val) => void data.set(k, val),
    getAlarm: async () => alarm,
    setAlarm: async (t) => void (alarm = t),
  };
}

/** A stand-in for the VisitorQuota namespace: one in-memory store per name, the real counting logic, and a record of what was asked. */
function namespace({ fail = false } = {}) {
  const stores = new Map();
  const names = [];
  return {
    stores,
    names,
    idFromName: (name) => (names.push(name), name),
    get: (id) => ({
      take: async (limit) => {
        if (fail) throw new Error("durable object down");
        if (!stores.has(id)) stores.set(id, memoryStore());
        return v.takeQuota(stores.get(id), limit, NOON);
      },
    }),
  };
}

const NOON = Date.UTC(2026, 9, 27, 12, 0, 0); // 27 Oct 2026 12:00 UTC
const LIMITS = { try: 3, scout: 3 };
const call = (ns, method, path, headers = {}, ip = "203.0.113.7", limits = LIMITS, now = NOON) =>
  v.overQuota(ns, limits, method, new URL(path, "https://live.example").pathname, h(headers), ip, now);

test("takeQuota allows while below the limit, then refuses, and a refusal is not counted", async () => {
  const s = memoryStore();
  const seen = [];
  for (let i = 0; i < 5; i++) seen.push((await v.takeQuota(s, 3, NOON)).allowed);
  assert.deepEqual(seen, [true, true, true, false, false]);
  assert.equal(s.data.get("n"), 3);
  assert.deepEqual(await v.takeQuota(s, 3, NOON), { allowed: false, count: 3 });
  assert.deepEqual(await v.takeQuota(s, 4, NOON), { allowed: true, count: 4 }, "a raised limit applies at once");
});

test("the first call sets an alarm an hour after the day ends, once", async () => {
  const s = memoryStore();
  await v.takeQuota(s, 3, NOON);
  const expected = Date.UTC(2026, 9, 28, 1, 0, 0);
  assert.equal(s.alarm(), expected);
  await v.takeQuota(s, 3, NOON + 1000);
  assert.equal(s.alarm(), expected);
});

test("the third try is allowed and the fourth gets the OpenAI-shaped 429 with a retry-after to midnight", async () => {
  const ns = namespace();
  for (let i = 0; i < 3; i++) assert.equal(await call(ns, "POST", "/v1/chat/completions"), null, `call ${i + 1}`);
  const r = await call(ns, "POST", "/v1/chat/completions");
  assert.equal(r.status, 429);
  assert.equal(r.headers.get("retry-after"), String(12 * 3600));
  assert.equal(r.headers.get("cache-control"), "no-store");
  assert.match(r.headers.get("content-type"), /application\/json/);
  assert.deepEqual(await r.json(), {
    error: {
      message: "Daily limit reached: 3 requests per visitor per day. It resets at 00:00 UTC.",
      type: "rate_limit_error",
      param: null,
      code: "daily_limit_reached",
    },
  });
});

test("a Scout run past the limit gets the gateway-shaped {detail} 429", async () => {
  const ns = namespace();
  for (let i = 0; i < 3; i++) assert.equal(await call(ns, "GET", "/scout/stream?q=x"), null);
  for (const [method, path] of [["GET", "/scout/stream?q=x"], ["POST", "/scout/run"]]) {
    const r = await call(ns, method, path);
    assert.equal(r.status, 429, `${method} ${path}`);
    assert.deepEqual(await r.json(), { detail: "Daily limit reached: 3 Scout runs per visitor per day. It resets at 00:00 UTC." });
    assert.equal(r.headers.get("retry-after"), String(12 * 3600));
  }
});

test("the stream and the run share one Scout count; Try has its own count; visitors and days are separate", async () => {
  const ns = namespace();
  await call(ns, "GET", "/scout/stream?q=x");
  await call(ns, "POST", "/scout/run");
  await call(ns, "POST", "/scout/run");
  assert.equal((await call(ns, "GET", "/scout/stream?q=x")).status, 429);
  assert.equal(await call(ns, "POST", "/v1/chat/completions"), null, "the try bucket is untouched by Scout");
  assert.equal(await call(ns, "GET", "/scout/stream?q=x", {}, "198.51.100.9"), null, "another visitor");
  assert.equal(await call(ns, "GET", "/scout/stream?q=x", {}, "203.0.113.7", LIMITS, NOON + 13 * 3600 * 1000), null, "the next UTC day");
});

test("which requests count: only keyless POST /v1/*, GET /scout/stream and POST /scout/run", () => {
  const b = (m, p, headers = {}) => v.bucketOf(m, p, h(headers));
  assert.equal(b("POST", "/v1/chat/completions"), "try");
  assert.equal(b("POST", "/v1/embeddings"), "try");
  assert.equal(b("GET", "/scout/stream"), "scout");
  assert.equal(b("POST", "/scout/run"), "scout");
  for (const [m, p] of [
    ["GET", "/v1/models"],
    ["GET", "/scout/status"],
    ["GET", "/scout/examples"],
    ["POST", "/scout/judge"],
    ["GET", "/ratchet/stats"],
    ["POST", "/ratchet/redact"],
    ["GET", "/healthz"],
    ["POST", "/scout/stream"],
    ["GET", "/scout/run"],
  ]) {
    assert.equal(b(m, p), null, `${m} ${p}`);
  }
  assert.equal(b("POST", "/v1/chat/completions", { authorization: "Bearer mine" }), null, "a caller's own key is not the shared demo key");
});

test("a judge cookie skips the Scout count only; any other cookie does not", async () => {
  const ns = namespace();
  for (let i = 0; i < 6; i++) assert.equal(await call(ns, "GET", "/scout/stream?q=x", { cookie: "ratchet_judge=abc.def" }), null);
  assert.equal(ns.names.length, 0, "the Durable Object is not asked");
  assert.equal(v.hasJudgeCookie(h({ cookie: "a=b; ratchet_judge=abc" })), true);
  assert.equal(v.hasJudgeCookie(h({ cookie: "ratchet_judge=; a=b" })), false);
  assert.equal(v.hasJudgeCookie(h({ cookie: "x_ratchet_judge=abc" })), false);
  assert.equal(v.hasJudgeCookie(h({})), false);
  for (let i = 0; i < 3; i++) await call(ns, "GET", "/scout/stream?q=x", { cookie: "session=1" });
  assert.equal((await call(ns, "GET", "/scout/stream?q=x", { cookie: "session=1" })).status, 429);
  // A judge cookie does not lift the Try limit.
  for (let i = 0; i < 3; i++) await call(ns, "POST", "/v1/chat/completions", { cookie: "ratchet_judge=abc" });
  assert.equal((await call(ns, "POST", "/v1/chat/completions", { cookie: "ratchet_judge=abc" })).status, 429);
});

test("the counter is named by bucket, UTC day and a hash of the address, never the address", async () => {
  const ns = namespace();
  await call(ns, "POST", "/v1/chat/completions", {}, "203.0.113.7");
  await call(ns, "GET", "/scout/stream?q=x", {}, "203.0.113.7");
  const hash = await v.sha256Hex("203.0.113.7");
  assert.match(hash, /^[0-9a-f]{64}$/);
  assert.deepEqual(ns.names, [`try:2026-10-27:${hash}`, `scout:2026-10-27:${hash}`]);
  for (const name of ns.names) assert.equal(name.includes("203.0.113.7"), false);
  assert.equal(await v.sha256Hex("abc"), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
});

test("seconds to the next UTC midnight, rounded up, never 0", () => {
  assert.equal(v.secondsToMidnight(Date.UTC(2026, 9, 27, 12, 0, 0)), 43200);
  assert.equal(v.secondsToMidnight(Date.UTC(2026, 9, 27, 0, 0, 0)), 86400);
  assert.equal(v.secondsToMidnight(Date.UTC(2026, 9, 27, 23, 59, 59, 500)), 1);
  assert.equal(v.secondsToMidnight(Date.UTC(2026, 9, 27, 23, 59, 59, 999)), 1);
  assert.equal(v.secondsToMidnight(Date.UTC(2026, 11, 31, 23, 0, 0)), 3600, "across a year end");
  assert.equal(v.utcDay(Date.UTC(2026, 11, 31, 23, 59, 59)), "2026-12-31");
  assert.equal(v.nextMidnight(Date.UTC(2026, 11, 31, 23, 0, 0)), Date.UTC(2027, 0, 1));
});

test("if the Durable Object fails, the call goes through and the log has no address", async () => {
  const lines = [];
  const real = console.log;
  console.log = (...a) => void lines.push(a.join(" "));
  try {
    assert.equal(await call(namespace({ fail: true }), "POST", "/v1/chat/completions"), null);
    assert.equal(await call(namespace({ fail: true }), "GET", "/scout/stream?q=x"), null);
  } finally {
    console.log = real;
  }
  assert.equal(lines.length, 2);
  for (const line of lines) {
    assert.equal(JSON.parse(line).event, "quota_error");
    assert.equal(line.includes("203.0.113.7"), false);
  }
});

test("without the binding, or with a limit of 0, nothing is counted", async () => {
  assert.equal(await call(undefined, "POST", "/v1/chat/completions"), null);
  const ns = namespace();
  for (let i = 0; i < 10; i++) assert.equal(await call(ns, "POST", "/v1/chat/completions", {}, "1.2.3.4", { try: 0, scout: 3 }), null);
  for (let i = 0; i < 10; i++) assert.equal(await call(ns, "GET", "/scout/stream?q=x", {}, "1.2.3.4", { try: 3, scout: 0 }), null);
  assert.equal(ns.names.length, 0);
});

test("a call with no address is counted under one shared key, not skipped", async () => {
  const ns = namespace();
  await call(ns, "POST", "/v1/chat/completions", {}, null);
  assert.deepEqual(ns.names, [`try:2026-10-27:${await v.sha256Hex("unknown")}`]);
});

test("the limits come from EDGE_DAILY_TRIES and EDGE_DAILY_SCOUT: default 3, 0 is off, junk is the default", () => {
  assert.deepEqual(v.dailyLimits({}), { try: 3, scout: 3 });
  assert.deepEqual(v.dailyLimits({ EDGE_DAILY_TRIES: "10", EDGE_DAILY_SCOUT: " 1 " }), { try: 10, scout: 1 });
  assert.deepEqual(v.dailyLimits({ EDGE_DAILY_TRIES: "0", EDGE_DAILY_SCOUT: 0 }), { try: 0, scout: 0 });
  for (const bad of ["", "  ", "-1", "abc", "1.5", "1e3", null, undefined, {}]) {
    assert.deepEqual(v.dailyLimits({ EDGE_DAILY_TRIES: bad, EDGE_DAILY_SCOUT: bad }), { try: 3, scout: 3 }, String(bad));
  }
  const l = v.dailyLimits({ EDGE_DAILY_TRIES: "5", EDGE_DAILY_SCOUT: "2" });
  const message = async (bucket, limit) => JSON.stringify(await v.dailyLimitReached(bucket, limit, NOON).json());
  return Promise.all([message("try", l.try), message("scout", l.scout)]).then(([a, b]) => {
    assert.match(a, /5 requests per visitor per day/);
    assert.match(b, /2 Scout runs per visitor per day/);
  });
});

test("the Worker is wired to the quota and the stripping; wrangler.json declares the Durable Object; the limits stay out of the container", () => {
  const index = readFileSync(new URL("../../deploy/cloudflare-live/src/index.ts", import.meta.url), "utf8");
  // The daily limit is applied inside guardPaidCall (turnstile.ts), which calls overQuota with the same arguments.
  assert.match(index, /guardPaidCall\(\{ quotas, limits: dailyLimits\(env\), settings, method: request\.method, url, headers: request\.headers, ip, out \}\)/);
  assert.match(readFileSync(new URL("../../deploy/cloudflare-live/src/turnstile.ts", import.meta.url), "utf8"), /overQuota\(a\.quotas, a\.limits, a\.method, a\.url\.pathname, a\.headers, a\.ip, now\)/);
  assert.match(index, /forwardedHeaders\(request\.headers, ip\)/);
  assert.match(index, /export class VisitorQuota extends DurableObject/);
  assert.match(readFileSync(new URL("../../deploy/cloudflare-live/src/containerenv.ts", import.meta.url), "utf8"), /demo:daily_requests=300,daily_usd=1\.5,rpm=60,concurrency=4/);
  assert.doesNotMatch(index, /RATCHET_EDGE/, "a RATCHET_* name would be passed to the gateway, which warns about unknown variables");
  assert.doesNotMatch(index, /EDGE_TOKEN\s*[:=]\s*["'`][^"'`]{8,}/, "no secret value in the source");
  const wrangler = JSON.parse(readFileSync(new URL("../../deploy/cloudflare-live/wrangler.json", import.meta.url), "utf8"));
  assert.equal(wrangler.ratelimits, undefined);
  assert.ok(wrangler.durable_objects.bindings.some((b) => b.name === "VISITOR_QUOTA" && b.class_name === "VisitorQuota"));
  assert.ok(wrangler.migrations.some((m) => m.tag === "v2" && m.new_sqlite_classes.includes("VisitorQuota")));
  assert.ok(wrangler.migrations.some((m) => m.tag === "v1" && m.new_sqlite_classes.includes("RatchetLive")), "v1 is kept");
});
