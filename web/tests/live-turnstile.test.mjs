// The live Worker's human check (deploy/cloudflare-live/src/turnstile.ts) and the browser's single-use token handling (src/lib/scout-stream.ts).
// Cloudflare's published test keys are used and siteverify is mocked: nothing here touches the network. Run with `npm test`.
import assert from "node:assert/strict";
import { register } from "node:module";
import { test } from "node:test";

register("./ts-hooks.mjs", import.meta.url);
const t = await import("../../deploy/cloudflare-live/src/turnstile.ts");
const v = await import("../../deploy/cloudflare-live/src/visitor.ts");
const stream = await import("../src/lib/scout-stream.ts");
const model = await import("../src/lib/turnstile-model.ts");

// Cloudflare's documented test keys: this secret passes every token, that one fails every token.
const SITE_KEY = "1x00000000000000000000AA";
const PASS = "1x0000000000000000000000000000000AA";
const FAIL = "2x0000000000000000000000000000000AA";

/** A siteverify that behaves like Cloudflare for the test secrets. Tokens "dup*" are spent ones; "5xx" and "boom" break the call; "badsecret" is our fault. */
function siteverify() {
  const calls = [];
  const send = async (url, init) => {
    assert.equal(url, t.SITEVERIFY_URL);
    const form = init.body;
    const token = form.get("response");
    calls.push({ secret: form.get("secret"), token, remoteip: form.get("remoteip") });
    if (token === "boom") throw new Error("network down");
    if (token === "5xx") return new Response("bad gateway", { status: 502 });
    if (token === "badsecret") return Response.json({ success: false, "error-codes": ["invalid-input-secret"] });
    if (form.get("secret") === PASS) return Response.json({ success: true });
    if (token.startsWith("dup")) return Response.json({ success: false, "error-codes": ["timeout-or-duplicate"] });
    return Response.json({ success: false, "error-codes": ["invalid-input-response"] });
  };
  return { send, calls };
}

/** The Durable Object namespace in memory, over the same counter functions the Worker uses. */
function namespace() {
  // `stores` holds only counters that were written to, so an empty map means nothing was counted.
  const stores = new Map();
  const alarms = new Map();
  const storeOf = (name) => ({
    get: async (k) => stores.get(name)?.get(k),
    put: async (k, x) => void (stores.get(name) ?? stores.set(name, new Map()).get(name)).set(k, x),
    getAlarm: async () => alarms.get(name) ?? null,
    setAlarm: async (x) => void alarms.set(name, x),
  });
  return {
    stores,
    idFromName: (name) => name,
    get: (name) => ({
      take: (limit) => v.takeQuota(storeOf(name), limit),
      peek: () => v.peekCount(storeOf(name)),
      bump: () => v.bumpCount(storeOf(name)),
    }),
  };
}

const settingsOn = (extra = {}) => t.turnstileSettings({ TURNSTILE_SECRET_KEY: PASS, TURNSTILE_SITE_KEY: SITE_KEY, ...extra });

function guard(quotas, over = {}) {
  const url = new URL(over.url ?? "https://live.ratchet.dualmindlab.tech/v1/chat/completions");
  const headers = new Headers(over.headers ?? {});
  return t.guardPaidCall({
    quotas,
    limits: { try: 3, scout: 3 },
    settings: settingsOn(),
    method: over.method ?? "POST",
    url,
    headers,
    ip: over.ip ?? "203.0.113.7",
    now: Date.UTC(2026, 9, 29, 12),
    ...over.rest,
  });
}
const withToken = (token, more = {}) => ({ headers: { "cf-turnstile-response": token, ...more } });
const body = async (r) => r.json();

test("everything is off without the secret: no check, no curl total, the site key is not given out", async () => {
  const off = t.turnstileSettings({ TURNSTILE_SITE_KEY: SITE_KEY });
  assert.equal(off.secret, null);
  assert.deepEqual(await body(t.siteKeyAnswer(off)), { site_key: null });
  assert.deepEqual(await body(t.siteKeyAnswer(settingsOn())), { site_key: SITE_KEY });
  assert.deepEqual(await body(t.siteKeyAnswer(t.turnstileSettings({ TURNSTILE_SECRET_KEY: PASS }))), { site_key: null });
  const ns = namespace();
  const sv = siteverify();
  for (let i = 0; i < 3; i++) assert.equal(await guard(ns, { rest: { settings: off, send: sv.send } }), null);
  assert.equal((await guard(ns, { rest: { settings: off, send: sv.send } })).status, 429, "the ordinary 3 a day still holds");
  assert.equal(sv.calls.length, 0);
  assert.equal([...ns.stores.keys()].some((k) => k.startsWith("curl:") || k.startsWith("fails:")), false);
});

test("the settings, their defaults and the off switch", () => {
  const d = t.turnstileSettings({ TURNSTILE_SECRET_KEY: " " + PASS + "\n" });
  assert.equal(d.secret, PASS);
  assert.equal(d.maxFails, 5);
  assert.equal(d.curlTotal, 100);
  const s = t.turnstileSettings({ TURNSTILE_SECRET_KEY: PASS, EDGE_TURNSTILE_MAX_FAILS: "2", EDGE_DAILY_CURL_TOTAL: "0" });
  assert.equal(s.maxFails, 2);
  assert.equal(s.curlTotal, 0);
  assert.equal(t.turnstileSettings({ TURNSTILE_SECRET_KEY: PASS, EDGE_DAILY_CURL_TOTAL: "many" }).curlTotal, 100);
});

test("a good token passes, goes to Cloudflare with the visitor's address, and still counts against the daily limit", async () => {
  const ns = namespace();
  const sv = siteverify();
  for (let i = 0; i < 3; i++) assert.equal(await guard(ns, { ...withToken("good" + i), rest: { send: sv.send } }), null);
  assert.equal(sv.calls[0].remoteip, "203.0.113.7");
  assert.equal(sv.calls[0].secret, PASS);
  const over = await guard(ns, { ...withToken("good3"), rest: { send: sv.send } });
  assert.equal(over.status, 429);
  assert.equal((await body(over)).error.code, "daily_limit_reached");
});

test("an expired or already-used token is a 403 with the same words and is NOT counted", async () => {
  const ns = namespace();
  const sv = siteverify();
  const failing = settingsOn({ TURNSTILE_SECRET_KEY: FAIL });
  for (let i = 0; i < 12; i++) {
    const r = await guard(ns, { ...withToken("dup" + i), rest: { settings: failing, send: sv.send } });
    assert.equal(r.status, 403);
    const b = await body(r);
    assert.equal(b.error.message, "Human check failed. Refresh the page and try again.");
    assert.equal(b.error.code, "human_check_failed");
  }
  assert.equal([...ns.stores.keys()].some((k) => k.startsWith("fails:")), false, "no failure was recorded");
  // The same visitor with a fresh, good token is not blocked, and a refused check did not use up the daily limit.
  assert.equal(await guard(ns, { ...withToken("fresh"), rest: { send: sv.send } }), null);
});

test("five invalid tokens block the visitor for every keyless paid call until midnight; other visitors are untouched", async () => {
  const ns = namespace();
  const sv = siteverify();
  const failing = settingsOn({ TURNSTILE_SECRET_KEY: FAIL });
  for (let i = 0; i < 5; i++) assert.equal((await guard(ns, { ...withToken("forged" + i), rest: { settings: failing, send: sv.send } })).status, 403);
  // Blocked now, even with a good token, with no token, and on Scout.
  const blocked = await guard(ns, { ...withToken("good"), rest: { send: sv.send } });
  assert.equal(blocked.status, 429);
  assert.equal((await body(blocked)).error.code, "human_check_blocked");
  assert.equal(blocked.headers.get("retry-after"), "43200", "12:00 UTC to the next 00:00 UTC");
  assert.equal((await guard(ns, { rest: { send: sv.send } })).status, 429);
  const scout = await guard(ns, { method: "GET", url: "https://live.ratchet.dualmindlab.tech/scout/stream?q=x", rest: { send: sv.send } });
  assert.equal(scout.status, 429);
  assert.match((await body(scout)).detail, /Too many failed human checks/);
  assert.equal(await guard(ns, { ip: "198.51.100.9", ...withToken("good"), rest: { send: sv.send } }), null);
  // Calls with an API key are not part of this.
  assert.equal(await guard(ns, { headers: { authorization: "Bearer k" }, rest: { send: sv.send } }), null);
  // EDGE_TURNSTILE_MAX_FAILS counts the same way.
  const two = namespace();
  const s2 = settingsOn({ TURNSTILE_SECRET_KEY: FAIL, EDGE_TURNSTILE_MAX_FAILS: "2" });
  for (let i = 0; i < 2; i++) await guard(two, { ...withToken("forged" + i), rest: { settings: s2, send: sv.send } });
  assert.equal((await guard(two, { ...withToken("x"), rest: { settings: s2, send: sv.send } })).status, 429);
});

test("a retry with a fresh token passes: the spent one is refused, the new one is accepted, nothing is counted", async () => {
  const ns = namespace();
  const seen = new Set();
  const send = async (url, init) => {
    const token = init.body.get("response");
    const ok = !seen.has(token); // Cloudflare accepts a token once
    seen.add(token);
    return Response.json(ok ? { success: true } : { success: false, "error-codes": ["timeout-or-duplicate"] });
  };
  assert.equal(await guard(ns, { ...withToken("one"), rest: { send } }), null);
  const again = await guard(ns, { ...withToken("one"), rest: { send } });
  assert.equal(again.status, 403, "a reconnect or resend with the same token");
  assert.equal(await guard(ns, { ...withToken("two"), rest: { send } }), null, "a fresh token");
  assert.equal([...ns.stores.keys()].some((k) => k.startsWith("fails:")), false);
});

test("siteverify down or erroring fails open to the daily limit, and counts nothing against the visitor", async () => {
  const ns = namespace();
  const sv = siteverify();
  for (const tok of ["boom", "5xx", "badsecret"]) assert.equal(await guard(ns, { ...withToken(tok), rest: { send: sv.send } }), null, tok);
  assert.equal((await guard(ns, { ...withToken("boom"), rest: { send: sv.send } })).status, 429, "the per-visitor limit still holds");
  assert.equal([...ns.stores.keys()].some((k) => k.startsWith("fails:")), false);
});

test("a call without a token: curl keeps 3 a day and shares one total across visitors; Scout without a token is a plain, uncounted 403", async () => {
  const ns = namespace();
  const sv = siteverify();
  const few = settingsOn({ EDGE_DAILY_CURL_TOTAL: "4" });
  const curl = (ip) => guard(ns, { ip, rest: { settings: few, send: sv.send } });
  for (let i = 0; i < 3; i++) assert.equal(await curl("203.0.113.1"), null);
  assert.equal((await curl("203.0.113.1")).status, 429, "the visitor's own 3 a day");
  assert.equal(await curl("203.0.113.2"), null, "the fourth call of the day, from another address");
  const used = await curl("203.0.113.3");
  assert.equal(used.status, 429);
  const b = await body(used);
  assert.equal(b.error.code, "curl_allowance_used");
  assert.match(b.error.message, /shared curl allowance for today is used up/);
  assert.match(b.error.message, /https:\/\/live\.ratchet\.dualmindlab\.tech/);
  assert.ok(Number(used.headers.get("retry-after")) > 0);
  // A verified browser is not held back by the curl total; 0 turns the total off.
  assert.equal(await guard(ns, { ip: "203.0.113.4", ...withToken("good"), rest: { settings: few, send: sv.send } }), null);
  const unlimited = settingsOn({ EDGE_DAILY_CURL_TOTAL: "0" });
  for (let i = 0; i < 3; i++) assert.equal(await guard(namespace(), { ip: "203.0.113." + (10 + i), rest: { settings: unlimited, send: sv.send } }), null);
  // Scout: browser-only, refused without a token, even with a judge cookie; not counted as a failure.
  const scoutNs = namespace();
  for (const headers of [{}, { cookie: "ratchet_judge=abc" }]) {
    for (let i = 0; i < 8; i++) {
      const r = await guard(scoutNs, { method: "GET", url: "https://live.ratchet.dualmindlab.tech/scout/stream?q=x", headers, rest: { send: sv.send } });
      assert.equal(r.status, 403);
      assert.match((await body(r)).detail, /Human check required/);
    }
  }
  assert.equal(scoutNs.stores.size, 0);
  const run = await guard(scoutNs, { url: "https://live.ratchet.dualmindlab.tech/scout/run", ...withToken("good"), rest: { send: sv.send } });
  assert.equal(run, null, "a verified Scout run");
});

test("the Worker's own failures of its counters never lock anyone out", async () => {
  const broken = { idFromName: (n) => n, get: () => ({ take: async () => Promise.reject(new Error("x")), peek: async () => Promise.reject(new Error("x")), bump: async () => Promise.reject(new Error("x")) }) };
  const sv = siteverify();
  assert.equal(await guard(broken, { ...withToken("good"), rest: { send: sv.send } }), null);
  assert.equal(await guard(broken, { rest: { send: sv.send } }), null);
  const failing = settingsOn({ TURNSTILE_SECRET_KEY: FAIL });
  assert.equal((await guard(broken, { ...withToken("forged"), rest: { settings: failing, send: sv.send } })).status, 403);
});

test("the token travels in a header or the query, and never reaches the gateway", () => {
  const url = new URL("https://live.ratchet.dualmindlab.tech/scout/stream?q=hello&cf-turnstile-response=abc.def");
  assert.equal(t.tokenOf(new Headers(), url), "abc.def");
  assert.equal(t.tokenOf(new Headers({ "cf-turnstile-response": "fromheader" }), url), "fromheader");
  assert.equal(t.tokenOf(new Headers(), new URL("https://x.test/v1/chat/completions")), null);
  assert.equal(t.tokenOf(new Headers({ "cf-turnstile-response": "x".repeat(3000) }), new URL("https://x.test/")), null);
  const clean = t.withoutTokenParam(url);
  assert.equal(clean.search, "?q=hello");
  assert.equal(t.withoutTokenParam(new URL("https://x.test/a?b=1")).search, "?b=1");
  assert.equal(v.forwardedHeaders(new Headers({ "cf-turnstile-response": "abc" }), "203.0.113.7").get("cf-turnstile-response"), null);
});

test("the page: the site key answer, the address of the stream and the headers", () => {
  assert.equal(model.parseSiteKey({ site_key: SITE_KEY }), SITE_KEY);
  for (const bad of [null, {}, { site_key: null }, { site_key: "  " }, { site_key: 5 }, "x"]) assert.equal(model.parseSiteKey(bad), null);
  assert.equal(model.scoutStreamUrl("a b", "tok"), "/scout/stream?q=a+b&cf-turnstile-response=tok");
  assert.equal(model.scoutStreamUrl("a b", null), "/scout/stream?q=a+b");
  assert.deepEqual(model.tokenHeaders("tok"), { "cf-turnstile-response": "tok" });
  assert.deepEqual(model.tokenHeaders(null), {});
  assert.equal(model.isHumanCheckFailure(403, { error: { code: "human_check_failed" } }), true);
  assert.equal(model.isHumanCheckFailure(429, { error: { code: "human_check_failed" } }), false);
});

/** A fake EventSource that records what was opened and closed. */
function fakeSources() {
  const opened = [];
  const makeSource = (url) => {
    const s = { url, closed: false, onmessage: null, onerror: null, close() { this.closed = true; } };
    opened.push(s);
    return s;
  };
  return { opened, makeSource };
}

test("Scout: an EventSource error closes it for good, and the fallback run gets a NEW token, never the old one", async () => {
  const { opened, makeSource } = fakeSources();
  let n = 0;
  const tokens = [];
  const token = async () => {
    const x = `tok${++n}`;
    tokens.push(x);
    return x;
  };
  const ran = [];
  const events = [];
  const finished = new Promise((resolve) => {
    stream.streamScoutWith("q?", (e) => (events.push(e), e.type === "done" && resolve()), {
      token,
      makeSource,
      run: async (q, tk) => (ran.push({ q, tk }), { events: [{ type: "done", run_id: "r", answer: "a", citations: [], totals: {} }] }),
      pause: async () => undefined,
    });
  });
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(opened.length, 1);
  assert.match(opened[0].url, /cf-turnstile-response=tok1/);
  opened[0].onerror(); // the stream failed before any event (a 403 shows up like this)
  await finished;
  assert.equal(opened[0].closed, true, "closed by us, so the browser cannot reconnect with tok1");
  assert.equal(opened.length, 1, "no second stream with the old token");
  assert.deepEqual(ran, [{ q: "q?", tk: "tok2" }]);
  assert.deepEqual(tokens, ["tok1", "tok2"]);
  assert.equal(events.length, 1);
});

test("Scout: a stream that dies after events closes and says so; with no EventSource the run uses one fresh token; cancel before the token ends it", async () => {
  const { opened, makeSource } = fakeSources();
  const events = [];
  let calls = 0;
  const deps = { token: async () => `t${++calls}`, makeSource, run: async () => ({ events: [] }), pause: async () => undefined };
  stream.streamScoutWith("q", (e) => events.push(e), deps);
  await new Promise((r) => setTimeout(r, 0));
  opened[0].onmessage({ data: JSON.stringify({ type: "start", run_id: "r", question: "q", mode: "live" }) });
  opened[0].onerror();
  assert.equal(opened[0].closed, true);
  assert.equal(events.at(-1).type, "error");
  assert.equal(opened.length, 1);
  assert.equal(calls, 1, "no further token, no fallback run after events began");

  const ranWith = [];
  stream.streamScoutWith("q", () => undefined, { ...deps, makeSource: undefined, run: async (q, tk) => (ranWith.push(tk), { events: [] }) });
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(ranWith, ["t2"]);

  let release;
  const slow = new Promise((r) => (release = r));
  const cancelled = fakeSources();
  const cancel = stream.streamScoutWith("q", () => undefined, { ...deps, token: () => slow, makeSource: cancelled.makeSource });
  cancel();
  release("late");
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(cancelled.opened.length, 0);
});
