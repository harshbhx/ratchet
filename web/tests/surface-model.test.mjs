// The header switch, the last-call line and the shared-simulation notice (src/lib/surface-model.ts). Pure functions over
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

test("the switch on the live host links the simulation and names Token Factory", () => {
  assert.deepEqual(m.surfaceSwitch(LIVE), {
    current: "live",
    liveLabel: "Live on Nebius Token Factory",
    liveHref: null,
    simHref: "https://sim.example.org",
  });
});

test("a live gateway not on Token Factory says only Live", () => {
  const sw = m.surfaceSwitch({ ...LIVE, upstream: { ...LIVE.upstream, token_factory: false } });
  assert.equal(sw.liveLabel, "Live");
});

test("the switch on the simulation host links the live gateway", () => {
  const sw = m.surfaceSwitch({
    mode: "mock",
    links: { live: "https://live.example.org", sim: null },
    demo: { surface: "simulation", controls: true, shared: true },
    upstream: { recording: false, since: null, token_factory: true, calls: [] },
  });
  assert.equal(sw.current, "simulation");
  assert.equal(sw.liveHref, "https://live.example.org");
  assert.equal(sw.simHref, null);
});

test("an older gateway without the blocks has no switch", () => {
  assert.equal(m.surfaceSwitch({ mode: "live" }), null);
  assert.equal(m.surfaceSwitch(null), null);
});

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

test("sharedNotice names only the limits that are set and is null on the live gateway", () => {
  const all = { shared: true, idle_reset_s: 7200, max_days: 30, max_requests: 200000 };
  assert.equal(
    m.sharedNotice(all),
    "Shared simulation: every visitor sees and changes the same state. It resets after 2 h without activity, 30 simulated days or 200,000 requests.",
  );
  assert.equal(
    m.sharedNotice({ shared: true, idle_reset_s: null, max_days: 30, max_requests: null }),
    "Shared simulation: every visitor sees and changes the same state. It resets after 30 simulated days.",
  );
  assert.equal(m.sharedNotice({ shared: true }), "Shared simulation: every visitor sees and changes the same state.");
  assert.equal(m.sharedNotice({ shared: false }), null);
  assert.equal(m.sharedNotice(undefined), null);
});
