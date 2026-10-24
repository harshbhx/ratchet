// The live training panel's logic (src/pages/judges/training-model.ts): it shows nothing unless a run is under way or ended
// recently, and what it shows comes from the status the job reported. Run with `npm test` (Node 22.6 or later).
import assert from "node:assert/strict";
import { register } from "node:module";
import { test } from "node:test";

register("./ts-hooks.mjs", import.meta.url);
const m = await import("../src/pages/judges/training-model.ts");

const NOW = 1_800_000_000;
const RUN = { state: "running", phase: "training", step: 200, total: 800, started_at: NOW - 3600, updated_at: NOW - 30, now: NOW, last_loss: 0.21, cap_usd: 8, spent_s: 3600 };

test("nothing is shown when there is no run, no service answer or nothing started", () => {
  assert.equal(m.runView(null), null);
  assert.equal(m.runView(undefined), null);
  assert.equal(m.runView({ state: "waiting", note: "no run has reported yet", now: NOW }), null);
  assert.equal(m.runView({ state: "unknown", now: NOW }), null);
  assert.equal(m.runView({ state: "running", now: NOW }), null); // no started_at yet
});

test("a running job shows its step, share, time left and spend", () => {
  const v = m.runView(RUN);
  assert.equal(v.state, "running");
  assert.equal(v.phase, "Training");
  assert.equal(v.progress, "step 200 of 800");
  assert.equal(v.percent, 25);
  assert.equal(v.elapsedS, 3600);
  assert.equal(Math.round(v.etaS), 10800); // 600 steps left at 18 s a step
  assert.ok(Math.abs(v.spentUsd - 0.8126) < 0.001, "an hour of GPU at the job's list price");
  assert.equal(v.capUsd, 8);
  assert.equal(v.loss, 0.21);
  assert.equal(v.staleS, null);
});

test("an exam phase counts rows, and a stale running job says so", () => {
  const exam = m.runView({ ...RUN, phase: "exam", exam_row: 30, exam_total: 150 });
  assert.equal(exam.progress, "exam row 30 of 150");
  assert.equal(exam.percent, 20);
  assert.equal(exam.etaS, null);
  assert.equal(m.runView({ ...RUN, updated_at: NOW - 900 }).staleS, 900);
});

test("a finished run stays for a day, then goes away; a failed one shows its error", () => {
  const done = { ...RUN, state: "done", phase: "done", updated_at: NOW - 3600 };
  assert.equal(m.runView(done).percent, 100);
  assert.equal(m.runView({ ...done, updated_at: NOW - 25 * 3600 }), null);
  assert.equal(m.runView({ ...RUN, state: "failed", error: "out of memory" }).error, "out of memory");
});

test("durations and the loss path", () => {
  assert.equal(m.duration(7500), "2 h 5 min");
  assert.equal(m.duration(2400), "40 min");
  assert.equal(m.duration(-5), "0 min");
  assert.equal(m.lossPath([[1, 0.5]]), "");
  assert.match(m.lossPath([[1, 0.5], [2, 0.3], [3, 0.2]]), /^M6\.0 6\.0 L/);
});
