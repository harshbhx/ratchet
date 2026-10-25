// The waking-up state machine and its copy (src/lib/waking-model.ts): idle, waking, ready, slow, retry. Run with `npm test`.
import assert from "node:assert/strict";
import { register } from "node:module";
import { test } from "node:test";

register("./ts-hooks.mjs", import.meta.url);
const m = await import("../src/lib/waking-model.ts");
const { BANNER_SLOW, BANNER_WAKING, GIVE_UP_MS, POLL_MS, SLOW_CALL_MS, bannerText, initialWaking, isSlowFirstCall, isWaking, isWakingResponse, reduce, waitedSeconds } = m;

const T0 = 1_000_000;

test("idle stays idle on a health answer and on ticks", () => {
  assert.equal(reduce(initialWaking, { type: "health_ok", at: T0 }), initialWaking);
  assert.equal(reduce(initialWaking, { type: "tick", at: T0 + GIVE_UP_MS * 2 }), initialWaking);
  assert.equal(bannerText(initialWaking), null);
  assert.equal(isWaking(initialWaking), false);
});

test("a failure starts waking, and a second failure does not restart the clock", () => {
  const w = reduce(initialWaking, { type: "failure", at: T0 });
  assert.equal(w.phase, "waking");
  assert.equal(w.since, T0);
  assert.equal(reduce(w, { type: "failure", at: T0 + 5000 }), w);
  assert.equal(bannerText(w), BANNER_WAKING);
  assert.equal(isWaking(w), true);
  assert.equal(waitedSeconds(w, T0 + 12_400), 12);
});

test("waking becomes ready when /healthz answers, and counts the recovery", () => {
  const w = reduce(initialWaking, { type: "failure", at: T0 });
  const r = reduce(w, { type: "health_ok", at: T0 + 20_000 });
  assert.deepEqual(r, { phase: "ready", since: null, recoveries: 1 });
  assert.equal(bannerText(r), null);
  assert.equal(isWaking(r), false);
  const again = reduce(reduce(r, { type: "failure", at: T0 + 99_000 }), { type: "health_ok", at: T0 + 100_000 });
  assert.equal(again.recoveries, 2);
});

test("after the give-up time waking turns slow; ticks before it change nothing", () => {
  const w = reduce(initialWaking, { type: "failure", at: T0 });
  assert.equal(reduce(w, { type: "tick", at: T0 + GIVE_UP_MS - 1 }), w);
  const s = reduce(w, { type: "tick", at: T0 + GIVE_UP_MS });
  assert.equal(s.phase, "slow");
  assert.equal(bannerText(s), BANNER_SLOW);
  assert.equal(isWaking(s), true);
});

test("slow still recovers by itself if the gateway answers late", () => {
  const s = reduce(reduce(initialWaking, { type: "failure", at: T0 }), { type: "tick", at: T0 + GIVE_UP_MS });
  assert.equal(reduce(s, { type: "health_ok", at: T0 + GIVE_UP_MS + 1 }).phase, "ready");
});

test("retry from slow starts a new wait; retry elsewhere does nothing", () => {
  const s = reduce(reduce(initialWaking, { type: "failure", at: T0 }), { type: "tick", at: T0 + GIVE_UP_MS });
  const r = reduce(s, { type: "retry", at: T0 + GIVE_UP_MS + 10 });
  assert.equal(r.phase, "waking");
  assert.equal(r.since, T0 + GIVE_UP_MS + 10);
  assert.equal(reduce(initialWaking, { type: "retry", at: T0 }), initialWaking);
  const w = reduce(initialWaking, { type: "failure", at: T0 });
  assert.equal(reduce(w, { type: "retry", at: T0 + 1 }), w);
});

test("what counts as the demo waking: 503 warming_up, 502, 504; not a gateway's own 503 or a 404", () => {
  assert.equal(isWakingResponse(503, { warming_up: true, error: { code: "warming_up" } }), true);
  assert.equal(isWakingResponse(502, null), true);
  assert.equal(isWakingResponse(504, null), true);
  assert.equal(isWakingResponse(503, { error: { code: "teacher_circuit_open" } }), false);
  assert.equal(isWakingResponse(503, null), false);
  assert.equal(isWakingResponse(404, { warming_up: true }), false);
});

test("a first call is slow only after 6 s and only before any call has succeeded", () => {
  assert.equal(isSlowFirstCall(SLOW_CALL_MS, false), false);
  assert.equal(isSlowFirstCall(SLOW_CALL_MS + 1, false), true);
  assert.equal(isSlowFirstCall(60_000, true), false);
});

test("the poll and give-up times are the ones the copy promises", () => {
  assert.equal(POLL_MS, 3000);
  assert.ok(GIVE_UP_MS >= 85_000 && GIVE_UP_MS <= 95_000);
  assert.match(BANNER_WAKING, /about 20 seconds.*update by itself/);
  assert.match(BANNER_SLOW, /longer than usual.*Try again in a minute/);
});
