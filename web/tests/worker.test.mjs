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

test("the judge cookie travels: headers and body pass through unchanged", async () => {
  const r = await route("/scout/judge", {}, { method: "POST", headers: { cookie: "ratchet_judge=abc", "content-type": "application/json" } });
  const req = r.seen[0];
  assert.equal(req.headers.get("cookie"), "ratchet_judge=abc");
  assert.equal(req.method, "POST");
  assert.equal(req.redirect, "manual");
});
