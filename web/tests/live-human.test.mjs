// The "verified human" session cookie (deploy/cloudflare-live/src/human.ts) and how it meets the human check (turnstile.ts) and the Pages proxy.
// Siteverify is mocked; nothing here touches the network. Run with `npm test`.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { register } from "node:module";
import { test } from "node:test";

register("./ts-hooks.mjs", import.meta.url);
const h = await import("../../deploy/cloudflare-live/src/human.ts");
const t = await import("../../deploy/cloudflare-live/src/turnstile.ts");
const v = await import("../../deploy/cloudflare-live/src/visitor.ts");

const SECRET = "1x0000000000000000000000000000000AA";
const NOW = Date.UTC(2026, 9, 29, 12);
const IP = "203.0.113.7";

/** The Cookie header a browser would send back for a Set-Cookie value. */
const cookieOf = (header) => header.split(";")[0];

test("the cookie is HttpOnly, Secure, SameSite=Lax, Path=/ and lasts 12 hours; the value carries an expiry, an address hash and a signature, not the address", async () => {
  const c = await h.issueCookie(SECRET, IP, NOW);
  assert.match(c.header, /^rt_human=v1\.\d+\.[0-9a-f]{32}\.[0-9a-f]{64}; Max-Age=43200; Path=\/; HttpOnly; Secure; SameSite=Lax$/);
  assert.equal(c.expires, Math.floor(NOW / 1000) + 12 * 3600);
  assert.ok(!c.header.includes(IP));
  assert.equal(await h.issueCookie(SECRET, null, NOW), null, "nothing to bind it to without an address");
});

test("a fresh cookie verifies for its address, until it expires", async () => {
  const c = await h.issueCookie(SECRET, IP, NOW);
  assert.equal(await h.verifyCookie(cookieOf(c.header), SECRET, IP, NOW + 1000), c.expires);
  assert.equal(await h.verifyCookie(`a=b; ${cookieOf(c.header)}; c=d`, SECRET, IP, NOW), c.expires, "among other cookies");
  assert.equal(await h.verifyCookie(cookieOf(c.header), SECRET, IP, NOW + 12 * 3600 * 1000 - 1000), c.expires, "just before the end");
  assert.equal(await h.verifyCookie(cookieOf(c.header), SECRET, IP, NOW + 12 * 3600 * 1000), null, "expired");
  assert.equal(await h.verifyCookie(cookieOf(c.header), SECRET, IP, NOW + 13 * 3600 * 1000), null);
});

test("a cookie for another address, another secret, a tampered value or no cookie at all is absent", async () => {
  const c = await h.issueCookie(SECRET, IP, NOW);
  const mine = cookieOf(c.header);
  assert.equal(await h.verifyCookie(mine, SECRET, "198.51.100.9", NOW), null, "another address");
  assert.equal(await h.verifyCookie(mine, SECRET, null, NOW), null);
  assert.equal(await h.verifyCookie(mine, "another-secret", IP, NOW), null, "rotating the Turnstile secret signs everyone out");
  const [name, value] = mine.split("=");
  const parts = value.split(".");
  // A later expiry, a different address hash, a flipped signature bit, a truncated signature, junk.
  const later = [parts[0], String(Number(parts[1]) + 86400), parts[2], parts[3]].join(".");
  const otherHash = [parts[0], parts[1], "0".repeat(32), parts[3]].join(".");
  const flipped = [...parts.slice(0, 3), (parts[3][0] === "a" ? "b" : "a") + parts[3].slice(1)].join(".");
  for (const bad of [later, otherHash, flipped, value.slice(0, -2), "v1.1.2.3", "", "x".repeat(300), value.replace("v1.", "v2.")]) {
    assert.equal(await h.verifyCookie(`${name}=${bad}`, SECRET, IP, NOW), null, bad.slice(0, 30));
  }
  assert.equal(await h.verifyCookie(null, SECRET, IP, NOW), null);
  assert.equal(await h.verifyCookie("other=1", SECRET, IP, NOW), null);
});

test("the human cookie is removed from what the gateway sees; other cookies stay", () => {
  assert.equal(h.withoutHumanCookie("ratchet_judge=abc; rt_human=v1.1.2.3; a=b"), "ratchet_judge=abc; a=b");
  assert.equal(h.withoutHumanCookie("rt_human=v1.1.2.3"), null);
  assert.equal(h.withoutHumanCookie(null), null);
});

test("GET /ratchet/human says whether this visitor is verified, without a container", async () => {
  const c = await h.issueCookie(SECRET, IP, NOW);
  const yes = await (await h.humanAnswer(SECRET, new Headers({ cookie: cookieOf(c.header) }), IP, NOW)).json();
  assert.deepEqual(yes, { verified: true, expires: new Date(c.expires * 1000).toISOString() });
  const no = await h.humanAnswer(SECRET, new Headers(), IP, NOW);
  assert.deepEqual(await no.json(), { verified: false, expires: null });
  assert.equal(no.headers.get("cache-control"), "no-store");
  assert.deepEqual(await (await h.humanAnswer(null, new Headers({ cookie: cookieOf(c.header) }), IP, NOW)).json(), { verified: false, expires: null }, "off without the secret");
  assert.deepEqual(await (await h.humanAnswer(SECRET, new Headers({ cookie: cookieOf(c.header) }), "198.51.100.9", NOW)).json(), { verified: false, expires: null });
});

// ---- the guard

function namespace() {
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
    idFromName: (n) => n,
    get: (n) => ({ take: (l) => v.takeQuota(storeOf(n), l), peek: () => v.peekCount(storeOf(n)), bump: () => v.bumpCount(storeOf(n)) }),
  };
}

function siteverify(over = {}) {
  const calls = [];
  const send = async (url, init) => {
    calls.push(init.body.get("response"));
    if (over.throws) throw new Error("down");
    return Response.json(over.answer ?? { success: true, hostname: "ratchet.dualmindlab.tech" });
  };
  return { send, calls };
}

const guard = (ns, over = {}) =>
  t.guardPaidCall({
    quotas: ns,
    limits: { try: 3, scout: 3 },
    settings: t.turnstileSettings({ TURNSTILE_SECRET_KEY: SECRET }),
    method: over.method ?? "POST",
    url: new URL(over.url ?? "https://live.ratchet.dualmindlab.tech/v1/chat/completions"),
    headers: new Headers(over.headers ?? {}),
    ip: over.ip ?? IP,
    now: NOW,
    ...over.rest,
  });

test("a good token earns the cookie; a later call with the cookie needs no token and spends none", async () => {
  const ns = namespace();
  const sv = siteverify();
  const out = {};
  assert.equal(await guard(ns, { headers: { "cf-turnstile-response": "tok" }, rest: { send: sv.send, out } }), null);
  assert.ok(out.cookie.startsWith("rt_human="));
  assert.deepEqual(sv.calls, ["tok"]);
  // The cookie alone: Scout (which needs a human) and /v1, no siteverify call, no cookie re-issued.
  const again = {};
  const cookie = cookieOf(out.cookie);
  assert.equal(await guard(ns, { method: "GET", url: "https://live.ratchet.dualmindlab.tech/scout/stream?q=x", headers: { cookie }, rest: { send: sv.send, out: again } }), null);
  assert.equal(await guard(ns, { headers: { cookie }, rest: { send: sv.send, out: again } }), null);
  assert.deepEqual(sv.calls, ["tok"]);
  assert.equal(again.cookie, undefined);
  // A token sent along with a valid cookie is not spent either.
  assert.equal(await guard(ns, { headers: { cookie, "cf-turnstile-response": "unused" }, rest: { send: sv.send } }), null);
  assert.deepEqual(sv.calls, ["tok"]);
});

test("a verified visitor is still under the per-visitor daily limit and the curl total does not apply to them", async () => {
  const ns = namespace();
  const sv = siteverify();
  const cookie = cookieOf((await h.issueCookie(SECRET, IP, NOW)).header);
  const few = t.turnstileSettings({ TURNSTILE_SECRET_KEY: SECRET, EDGE_DAILY_CURL_TOTAL: "1" });
  for (let i = 0; i < 3; i++) assert.equal(await guard(ns, { headers: { cookie }, rest: { send: sv.send, settings: few } }), null);
  const over = await guard(ns, { headers: { cookie }, rest: { send: sv.send, settings: few } });
  assert.equal(over.status, 429);
  assert.equal((await over.json()).error.code, "daily_limit_reached");
  assert.equal([...ns.stores.keys()].some((k) => k.startsWith("curl:")), false);
});

test("no cookie, or a cookie that is bad, expired or from another address, means the token flow applies, with no failure counted", async () => {
  const ns = namespace();
  const sv = siteverify();
  const good = cookieOf((await h.issueCookie(SECRET, IP, NOW)).header);
  const scout = { method: "GET", url: "https://live.ratchet.dualmindlab.tech/scout/stream?q=x" };
  for (const cookie of [undefined, good.slice(0, -3) + "000", `rt_human=garbage`]) {
    const r = await guard(ns, { ...scout, headers: cookie ? { cookie } : {}, rest: { send: sv.send } });
    assert.equal(r.status, 403, String(cookie));
    assert.match((await r.json()).detail, /Human check required/);
  }
  const other = await guard(ns, { ...scout, ip: "198.51.100.9", headers: { cookie: good }, rest: { send: sv.send } });
  assert.equal(other.status, 403, "another address");
  const expired = await t.guardPaidCall({ ...{ quotas: ns, limits: { try: 3, scout: 3 }, settings: t.turnstileSettings({ TURNSTILE_SECRET_KEY: SECRET }), method: "GET", url: new URL(scout.url), headers: new Headers({ cookie: good }), ip: IP }, now: NOW + 13 * 3600 * 1000 });
  assert.equal(expired.status, 403, "expired");
  assert.equal([...ns.stores.keys()].some((k) => k.startsWith("fails:")), false, "nothing was counted as a failure");
  assert.deepEqual(sv.calls, []);
});

test("only a token Cloudflare accepted earns the cookie: not an invalid or spent one, not a fail-open call, not a wrong hostname", async () => {
  for (const over of [
    { answer: { success: false, "error-codes": ["invalid-input-response"] } },
    { answer: { success: false, "error-codes": ["timeout-or-duplicate"] } },
    { answer: { success: true, hostname: "evil.example" } },
    { throws: true },
  ]) {
    const out = {};
    await guard(namespace(), { headers: { "cf-turnstile-response": "tok" }, rest: { send: siteverify(over).send, out } });
    assert.equal(out.cookie, undefined, JSON.stringify(over));
  }
  // Off without the secret: no cookie, no check.
  const out = {};
  const off = t.turnstileSettings({});
  assert.equal(await guard(namespace(), { headers: { "cf-turnstile-response": "tok" }, rest: { settings: off, send: siteverify().send, out } }), null);
  assert.equal(out.cookie, undefined);
});

test("a blocked visitor stays blocked even with a valid cookie", async () => {
  const ns = namespace();
  const cookie = cookieOf((await h.issueCookie(SECRET, IP, NOW)).header);
  const bad = siteverify({ answer: { success: false, "error-codes": ["invalid-input-response"] } });
  for (let i = 0; i < 5; i++) await guard(ns, { headers: { "cf-turnstile-response": "forged" + i }, rest: { send: bad.send } });
  assert.equal((await guard(ns, { headers: { cookie }, rest: { send: bad.send } })).status, 429);
});

test("the Worker adds the cookie to its answer and strips it before the gateway, and the Pages proxy passes Set-Cookie and Cookie through", async () => {
  const index = readFileSync(new URL("../../deploy/cloudflare-live/src/index.ts", import.meta.url), "utf8");
  assert.match(index, /headers\.append\("set-cookie", cookie\)/);
  assert.match(index, /withoutHumanCookie\(headers\.get\("cookie"\)\)/);
  assert.match(index, /url\.pathname === "\/ratchet\/human"/);

  const src = readFileSync(new URL("../public/_worker.js", import.meta.url), "utf8");
  const worker = (await import("data:text/javascript;base64," + Buffer.from(src).toString("base64"))).default;
  const seen = [];
  const real = globalThis.fetch;
  globalThis.fetch = async (req) => {
    seen.push(req);
    const headers = new Headers({ "content-type": "application/json" });
    headers.append("set-cookie", "rt_human=v1.1.2.3; Max-Age=43200; Path=/; HttpOnly; Secure; SameSite=Lax");
    return new Response("{}", { headers });
  };
  try {
    const assets = { fetch: async () => new Response("asset") };
    const r = await worker.fetch(
      new Request("https://ratchet.dualmindlab.tech/scout/stream?q=x", { headers: { cookie: "rt_human=abc; ratchet_judge=j" } }),
      { ASSETS: assets },
    );
    assert.equal(seen[0].headers.get("cookie"), "rt_human=abc; ratchet_judge=j", "Cookie is forwarded to the live Worker");
    assert.match(r.headers.get("set-cookie"), /^rt_human=.*HttpOnly; Secure; SameSite=Lax$/, "Set-Cookie reaches the browser, with no Domain, so it belongs to the main site");
    assert.ok(!/domain=/i.test(r.headers.get("set-cookie")));
    const human = await worker.fetch(new Request("https://ratchet.dualmindlab.tech/ratchet/human"), { ASSETS: assets });
    assert.equal(new URL(seen[1].url).pathname, "/ratchet/human");
    assert.equal(human.status, 200);
  } finally {
    globalThis.fetch = real;
  }
});
