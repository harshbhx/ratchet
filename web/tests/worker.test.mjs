// The Pages proxy (public/_worker.js): which gateway a path reaches. Run through the worker's own fetch with a stubbed network.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const src = readFileSync(new URL("../public/_worker.js", import.meta.url), "utf8");
const worker = (await import("data:text/javascript;base64," + Buffer.from(src).toString("base64"))).default;

const SIM = "https://demo.ratchet.dualmindlab.tech";
const LIVE = "https://live.ratchet.dualmindlab.tech";

async function route(path, env = {}, init = {}) {
  const seen = [];
  const real = globalThis.fetch;
  globalThis.fetch = async (req) => (seen.push(req), new Response("ok"));
  try {
    const assets = { fetch: async () => new Response("asset") };
    const r = await worker.fetch(new Request("https://ratchet.dualmindlab.tech" + path, init), { ASSETS: assets, ...env });
    return { seen, body: await r.text() };
  } finally {
    globalThis.fetch = real;
  }
}
const origin = (r) => new URL(r.seen[0].url).origin;

test("every gateway-backed path goes to the live gateway by default", async () => {
  assert.equal(origin(await route("/scout/status")), LIVE);
  assert.equal(origin(await route("/scout/stream?q=x")), LIVE);
  for (const p of ["/ratchet/stats", "/ratchet/cost", "/v1/models", "/healthz", "/readyz", "/livez"]) assert.equal(origin(await route(p)), LIVE, p);
});

test("pages are static and never reach a gateway", async () => {
  const r = await route("/scout");
  assert.equal(r.seen.length, 0);
  assert.equal(r.body, "asset");
});

test("SCOUT_ORIGIN overrides only scout; an empty or bad value falls back to the live default", async () => {
  assert.equal(origin(await route("/scout/status", { SCOUT_ORIGIN: "https://x.example.com/ignored" })), "https://x.example.com");
  for (const bad of ["", "  ", "http://x.example.com", "not a url", undefined, 5]) {
    assert.equal(origin(await route("/scout/status", { SCOUT_ORIGIN: bad })), LIVE, String(bad));
  }
  assert.equal(origin(await route("/ratchet/stats", { SCOUT_ORIGIN: "https://x.example.com" })), LIVE);
});

test("GATEWAY_ORIGIN set to the simulation restores the old split: the simulation for pages, live for scout", async () => {
  assert.equal(origin(await route("/ratchet/stats", { GATEWAY_ORIGIN: SIM })), SIM);
  assert.equal(origin(await route("/healthz", { GATEWAY_ORIGIN: SIM })), SIM);
  assert.equal(origin(await route("/scout/status", { GATEWAY_ORIGIN: SIM })), LIVE);
  for (const bad of ["", "http://x.example.com", "nope", undefined]) assert.equal(origin(await route("/ratchet/stats", { GATEWAY_ORIGIN: bad })), LIVE, String(bad));
});

test("GATEWAY_ORIGIN moves everything but scout; scout set to the same origin moves it too", async () => {
  const g = "https://g.example.com";
  assert.equal(origin(await route("/ratchet/stats", { GATEWAY_ORIGIN: g })), g);
  assert.equal(origin(await route("/scout/status", { GATEWAY_ORIGIN: g })), LIVE);
  assert.equal(origin(await route("/scout/status", { GATEWAY_ORIGIN: g, SCOUT_ORIGIN: g })), g);
});

test("/video and /video/ are a no-store 302 to the video's counting link (from=site), before any gateway or asset", async () => {
  const VIDEO = "https://video.ratchet.dualmindlab.tech/?from=site";
  assert.equal(worker._test.VIDEO_URL, VIDEO);
  for (const path of ["/video", "/video/", "/video?x=1"]) {
    for (const method of ["GET", "HEAD", "POST"]) {
      let assets = 0;
      const seen = [];
      const real = globalThis.fetch;
      globalThis.fetch = async (req) => (seen.push(req), new Response("gateway"));
      try {
        const env = { ASSETS: { fetch: async () => (assets++, new Response("asset")) }, GATEWAY_ORIGIN: "https://g.example.com" };
        const r = await worker.fetch(new Request("https://ratchet.dualmindlab.tech" + path, { method }), env);
        assert.equal(r.status, 302, `${method} ${path}`);
        assert.equal(r.headers.get("location"), VIDEO, `${method} ${path}`);
        assert.equal(r.headers.get("cache-control"), "no-store", `${method} ${path}`);
        assert.equal(await r.text(), "");
      } finally {
        globalThis.fetch = real;
      }
      assert.equal(seen.length, 0, `${method} ${path}: no gateway call`);
      assert.equal(assets, 0, `${method} ${path}: no asset read`);
    }
  }
  // Only the exact paths: anything else that starts with /video is a static page like any other.
  for (const path of ["/videos", "/video/x"]) {
    const r = await route(path);
    assert.equal(r.seen.length, 0, path);
    assert.equal(r.body, "asset", path);
  }
});

test("_routes.json sends /video and /video/ to the worker, so the static site never answers them", () => {
  const routes = JSON.parse(readFileSync(new URL("../public/_routes.json", import.meta.url), "utf8"));
  assert.ok(routes.include.includes("/video") && routes.include.includes("/video/"));
  assert.deepEqual(routes.exclude, []);
});

test("the judge cookie travels: headers and body pass through unchanged", async () => {
  const r = await route("/scout/judge", {}, { method: "POST", headers: { cookie: "ratchet_judge=abc", "content-type": "application/json" } });
  const req = r.seen[0];
  assert.equal(req.headers.get("cookie"), "ratchet_judge=abc");
  assert.equal(req.method, "POST");
  assert.equal(req.redirect, "manual");
});

// ---- the retry while the live container starts ----
const { forward, retryable } = worker._test;

/** Run `forward` against a scripted gateway: each entry is a Response, or an Error to throw. Returns what was sent and the waits. */
async function scripted(method, path, script, init = {}) {
  const sent = [];
  const waits = [];
  const answers = [...script];
  const fetchImpl = async (req) => {
    sent.push(req);
    const a = answers.shift() ?? new Response("late", { status: 200 });
    if (a instanceof Error) throw a;
    return a;
  };
  const req = new Request("https://ratchet.dualmindlab.tech" + path, { method, ...init });
  const res = await forward(req, new URL(path, "https://live.ratchet.dualmindlab.tech"), { fetch: fetchImpl, sleep: async (ms) => void waits.push(ms) });
  return { res, sent, waits };
}
const down = () => new Response("error code: 502", { status: 502 });

test("a 200 passes through untouched: same object, one call, no wait", async () => {
  const ok = new Response('{"a":1}', { status: 200, headers: { "set-cookie": "ratchet_judge=abc; HttpOnly", "x-k": "v" } });
  const { res, sent, waits } = await scripted("GET", "/ratchet/stats", [ok]);
  assert.equal(res, ok);
  assert.equal(sent.length, 1);
  assert.deepEqual(waits, []);
  assert.equal(res.headers.get("set-cookie"), "ratchet_judge=abc; HttpOnly");
});

test("a GET is retried up to three times with 0.7 s, 1.5 s and 3 s waits, then succeeds", async () => {
  const { res, sent, waits } = await scripted("GET", "/ratchet/stats", [down(), new Error("Network connection lost"), down(), new Response("fine")]);
  assert.equal(await res.text(), "fine");
  assert.equal(sent.length, 4);
  assert.deepEqual(waits, [700, 1500, 3000]);
});

test("a GET that never recovers gets 503 warming_up with Retry-After 5 after four tries", async () => {
  const { res, sent } = await scripted("GET", "/healthz", [down(), down(), down(), down(), new Response("never")]);
  assert.equal(sent.length, 4);
  assert.equal(res.status, 503);
  assert.equal(res.headers.get("retry-after"), "5");
  const body = await res.json();
  assert.equal(body.warming_up, true);
  assert.equal(body.error.code, "warming_up");
  assert.match(body.error.message, /waking up/);
});

test("the container's own texts count as not ready, even at status 500", async () => {
  for (const text of ["Container suddenly disconnected", "The container is not running", "Network connection lost."]) {
    const { res, sent } = await scripted("GET", "/ratchet/stats", [new Response(text, { status: 500 }), new Response("up")]);
    assert.equal(await res.text(), "up", text);
    assert.equal(sent.length, 2, text);
  }
});

test("a real gateway answer is never retried or changed: 404, 429, and a JSON 503 such as /readyz", async () => {
  for (const [status, body] of [[404, "{}"], [429, '{"error":{"code":"rate_limited"}}'], [503, '{"status":"unavailable","checks":{"ledger":false}}'], [500, '{"error":"boom"}']]) {
    const r = new Response(body, { status });
    const { res, sent } = await scripted("GET", "/readyz", [r]);
    assert.equal(res, r, String(status));
    assert.equal(sent.length, 1);
  }
});

test("POST to /v1/* and /scout/run is never retried, whatever the failure", async () => {
  for (const path of ["/v1/chat/completions", "/v1/embeddings", "/scout/run"]) {
    for (const first of [down(), new Error("Network connection lost")]) {
      const { res, sent, waits } = await scripted("POST", path, [first, new Response("paid twice")], { body: "{}" });
      assert.equal(sent.length, 1, path);
      assert.deepEqual(waits, []);
      assert.equal(res.status, 503);
      assert.equal((await res.json()).warming_up, true);
    }
  }
});

test("reads that could start a paid run or a demo action are never retried either", async () => {
  for (const path of ["/scout/stream?q=x", "/scout/run", "/ratchet/demo/day"]) {
    const { sent } = await scripted("GET", path, [down(), new Response("again")]);
    assert.equal(sent.length, 1, path);
  }
  assert.equal(retryable("GET", "/v1/models"), false);
});

test("the free POSTs are retried with their body intact; others are not", async () => {
  const { sent, res } = await scripted("POST", "/scout/judge", [down(), new Response("ok")], { body: '{"code":"x"}', headers: { cookie: "a=b" } });
  assert.equal(sent.length, 2);
  assert.equal(await sent[1].text(), '{"code":"x"}');
  assert.equal(sent[1].headers.get("cookie"), "a=b");
  assert.equal(await res.text(), "ok");
  assert.equal(retryable("POST", "/ratchet/redact"), true);
  assert.equal(retryable("POST", "/ratchet/admin/promote"), false);
  assert.equal(retryable("DELETE", "/scout/judge"), false);
});

test("cookies and headers go through unchanged on every try", async () => {
  const { sent } = await scripted("GET", "/scout/status", [down(), new Response("ok")], { headers: { cookie: "ratchet_judge=abc", "cf-connecting-ip": "1.2.3.4" } });
  for (const req of sent) {
    assert.equal(req.headers.get("cookie"), "ratchet_judge=abc");
    assert.equal(req.headers.get("x-forwarded-for"), "1.2.3.4");
    assert.equal(req.redirect, "manual");
  }
});
