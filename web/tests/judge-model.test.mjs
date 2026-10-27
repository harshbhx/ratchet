// The Scout page's judge control (src/pages/scout/judge-model.ts, ADR 0120): the status block read defensively and the
// plain-words error for each status the gateway answers. Run with `npm test`.
import assert from "node:assert/strict";
import { register } from "node:module";
import { test } from "node:test";

register("./ts-hooks.mjs", import.meta.url);
const m = await import("../src/pages/scout/judge-model.ts");

test("the control shows only when the gateway says judge access is on", () => {
  assert.deepEqual(m.parseJudge({ judge: { enabled: true, session: false, runs_left_today: 4 } }), { enabled: true, session: false, runsLeft: 4 });
  assert.deepEqual(m.parseJudge({ judge: { enabled: true, session: true, runs_left_today: 0 } }), { enabled: true, session: true, runsLeft: 0 });
  const off = { enabled: false, session: false, runsLeft: null };
  assert.deepEqual(m.parseJudge({ judge: { enabled: false, session: true, runs_left_today: 3 } }), off);
  assert.deepEqual(m.parseJudge({}), off);
  assert.deepEqual(m.parseJudge(null), off);
  assert.deepEqual(m.parseJudge({ judge: "yes" }), off);
  assert.deepEqual(m.parseJudge({ judge: { enabled: "true" } }), off);
});

test("a runs-left value that is not a count is left out", () => {
  for (const v of [null, undefined, -1, "3", NaN, Infinity]) {
    assert.equal(m.parseJudge({ judge: { enabled: true, runs_left_today: v } }).runsLeft, null);
  }
  assert.equal(m.parseJudge({ judge: { enabled: true, runs_left_today: 2.9 } }).runsLeft, 2);
});

test("each status has a plain message that never mentions the code's value", () => {
  assert.equal(m.unlockError(400), "Type the judge code first.");
  assert.equal(m.unlockError(401), "That code is not right. Check it and try again.");
  assert.equal(m.unlockError(404), "Judge access is not turned on for this gateway.");
  assert.equal(m.unlockError(429), "Too many tries from this address. Try again in an hour.");
  assert.match(m.unlockError(0), /Could not reach the gateway/);
  assert.match(m.unlockError(500), /\(500\)/);
  assert.match(m.signOutError(0), /Could not reach/);
  assert.match(m.signOutError(500), /\(500\)/);
});

test("the runs-left line", () => {
  assert.equal(m.runsLeftText(null), "");
  assert.equal(m.runsLeftText(1), "1 judge run left today.");
  assert.equal(m.runsLeftText(7), "7 judge runs left today.");
  assert.match(m.runsLeftText(0), /No judge runs left today/);
});
