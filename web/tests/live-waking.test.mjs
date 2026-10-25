// The live Worker's waking-up answers (deploy/cloudflare-live/src/waking.ts): a splash page for a browser, the warming_up JSON
// for everything else, and which requests may be sent again while the container starts. Run with `npm test`.
import assert from "node:assert/strict";
import { test } from "node:test";

const w = await import("../../deploy/cloudflare-live/src/waking.ts");

test("a browser navigation gets the splash page, anything else the JSON", async () => {
  const page = w.warmingFor("GET", "text/html,application/xhtml+xml");
  assert.equal(page.status, 503);
  assert.match(page.headers.get("content-type"), /text\/html/);
  assert.equal(page.headers.get("retry-after"), "5");
  for (const [method, accept] of [["GET", "application/json"], ["GET", null], ["POST", "text/html"], ["GET", "*/*"]]) {
    const r = w.warmingFor(method, accept);
    assert.equal(r.status, 503, `${method} ${accept}`);
    assert.match(r.headers.get("content-type"), /application\/json/);
    assert.equal(r.headers.get("retry-after"), "5");
    const body = await r.json();
    assert.equal(body.warming_up, true);
    assert.equal(body.error.code, "warming_up");
    assert.match(body.error.message, /waking up.*about 20 seconds/);
  }
});

test("the splash is self-contained, accessible and honest", () => {
  const html = w.WAKING_HTML;
  assert.match(html, /<meta http-equiv="refresh" content="3"/);
  assert.match(html, /role="status"/);
  assert.match(html, /prefers-reduced-motion: reduce/);
  assert.match(html, /Waking up the live demo/);
  assert.match(html, /It sleeps when idle to save cost\. This takes about 20 seconds\./);
  // No external resources: the only absolute links are the two anchors the visitor may follow.
  const srcs = [...html.matchAll(/(?:src|href)="(https?:[^"]+)"/g)].map((m) => m[1]);
  assert.deepEqual(srcs.sort(), ["https://docs.ratchet.dualmindlab.tech/", "https://github.com/harshbhx/ratchet"]);
  assert.doesNotMatch(html, /<link |@import|<script[^>]+src=/);
});

test("only reads may be sent again; paid runs, /v1 and demo actions never", () => {
  assert.equal(w.isRetryableRead("GET", "/ratchet/stats"), true);
  assert.equal(w.isRetryableRead("HEAD", "/healthz"), true);
  assert.equal(w.isRetryableRead("GET", "/scout/status"), true);
  assert.equal(w.isRetryableRead("POST", "/scout/judge"), false);
  assert.equal(w.isRetryableRead("GET", "/scout/stream"), false);
  assert.equal(w.isRetryableRead("POST", "/scout/run"), false);
  assert.equal(w.isRetryableRead("GET", "/v1/models"), false);
  assert.equal(w.isRetryableRead("POST", "/v1/chat/completions"), false);
  assert.equal(w.isRetryableRead("GET", "/ratchet/demo/day"), false);
});

test("the wait is bounded at about 25 seconds", () => {
  assert.equal(w.API_WAIT_MS, 25_000);
  assert.ok(w.WAKE_AFTER_MS <= 3000);
});
