// The last-call line (src/lib/surface-model.ts). Pure functions over
// /ratchet/stats; an older gateway without the blocks gets null. Run with `npm test`.
import assert from "node:assert/strict";
import { register } from "node:module";
import { test } from "node:test";

register("./ts-hooks.mjs", import.meta.url);
const m = await import("../src/lib/surface-model.ts");

const call = (over = {}) => ({
  role: "teacher",
  host: "token_factory",
  requested_model: "nvidia/Nemotron-3-Ultra-550b-a55b",
  model: "nvidia/Nemotron-3-Ultra-550b-a55b",
  ok: true,
  status: 200,
  error: null,
  reason: null,
  latency_ms: 4210.3,
  at: 1000,
  age_s: 184.2,
  last_ok_age_s: 184.2,
  ...over,
});

const LIVE = {
  mode: "live",
  links: { live: null, sim: "https://sim.example.org" },
  demo: { surface: "live", controls: false, shared: false },
  upstream: { recording: true, since: 1, token_factory: true, calls: [] },
};

test("lastCall picks the newest Token Factory entry across the teacher and the hosted student", () => {
  const up = {
    calls: [
      call({ at: 10 }),
      call({ role: "student", at: 30, model: "nvidia/Nano" }),
      call({ role: "tavily", host: "tavily", at: 99, requested_model: null, model: null }),
    ],
  };
  assert.equal(m.lastCall(up, "token_factory").role, "student");
  assert.equal(m.lastCall(up, "tavily").role, "tavily");
  assert.equal(m.lastCall({ calls: [] }, "tavily"), null);
  assert.equal(m.lastCall(undefined, "token_factory"), null);
});

test("lastCallLine covers success, timeout, HTTP errors with an earlier success, and a missing model", () => {
  assert.equal(m.lastCallLine(call()), "3 min ago · nvidia/Nemotron-3-Ultra-550b-a55b · 4.2 s");
  assert.equal(m.lastCallLine(call({ model: null })), "3 min ago · nvidia/Nemotron-3-Ultra-550b-a55b · 4.2 s");
  assert.equal(m.lastCallLine(call({ ok: false, reason: "timeout", status: null, last_ok_age_s: null, age_s: 200 })), "failed 3 min ago (timeout)");
  assert.equal(
    m.lastCallLine(call({ ok: false, reason: "http", status: 429, age_s: 30, last_ok_age_s: 2400 })),
    "failed just now (HTTP 429), last success 40 min ago",
  );
});

test("fmtAge boundaries", () => {
  assert.equal(m.fmtAge(0), "just now");
  assert.equal(m.fmtAge(59.9), "just now");
  assert.equal(m.fmtAge(60), "1 min ago");
  assert.equal(m.fmtAge(3599), "59 min ago");
  assert.equal(m.fmtAge(3600), "1 h ago");
  assert.equal(m.fmtAge(86400), "1 d ago");
});
