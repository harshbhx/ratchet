// The request lifecycle explorer (src/pages/landing/lifecycle-model.ts). It is an illustration, but its stages name real
// files and real defaults, so this test holds them to the repo: every file exists, every number is the default in the
// Python source it comes from, every scenario walks known stages in map order, and keyboard focus covers every stage.
// Run with `npm test`.
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { register } from "node:module";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

register("./ts-hooks.mjs", import.meta.url);
const m = await import("../src/pages/landing/lifecycle-model.ts");
const REPO = fileURLToPath(new URL("../../", import.meta.url));
const read = (rel) => readFileSync(REPO + rel, "utf-8");

test("every file a stage names exists in the repo", () => {
  const missing = m.STAGES.flatMap((s) => s.files.filter((f) => !existsSync(REPO + f)).map((f) => `${s.id}: ${f}`));
  assert.deepEqual(missing, []);
  assert.ok(m.STAGES.every((s) => s.files.length > 0 && s.safeguards.length > 0 && s.does && s.inputs && s.outputs));
});

test("the numbers the explorer prints are the code's defaults", () => {
  const release = read("ratchet/release.py");
  const policy = (field) => Number(new RegExp(`^    ${field}: (?:float|int) = ([\\d.]+)`, "m").exec(release)?.[1]);
  for (const f of ["min_precision", "min_coverage", "tolerance", "min_shadow", "min_audits", "live_floor"]) {
    assert.equal(policy(f), m.LIFECYCLE_NUMBERS[f], `ReleasePolicy.${f}`);
  }
  const config = read("ratchet/config.py");
  const setting = (v) => Number(new RegExp(`\\("${v}", "([\\d.]+)"\\)`).exec(config)?.[1]);
  assert.equal(setting("RATCHET_SAMPLES"), m.LIFECYCLE_NUMBERS.samples);
  assert.equal(setting("RATCHET_AUDIT_RATE"), m.LIFECYCLE_NUMBERS.audit_rate);
  const score = /^def score\(.*w_prob: float = ([\d.]+), w_agree: float = ([\d.]+)\)/m.exec(read("ratchet/confidence.py"));
  assert.deepEqual([Number(score?.[1]), Number(score?.[2])], [m.LIFECYCLE_NUMBERS.w_prob, m.LIFECYCLE_NUMBERS.w_agree]);
  assert.equal(Number(/^FREQUENT = (\d+)/m.exec(read("ratchet/nightschool/dataset.py"))?.[1]), m.LIFECYCLE_NUMBERS.frequent);
});

test("the pinned built-in task the pinned scenario names is pinned in code", () => {
  assert.match(read("ratchet/tasks.py"), /"research-plan": Task\("research-plan", pinned=True\)/);
  assert.match(m.SCENARIO_BY_ID.pinned.notes.request, /research-plan/);
});

test("each scenario walks known stages, in map order, and has a note for every step", () => {
  assert.deepEqual(
    m.SCENARIOS.map((s) => s.id),
    ["student", "escalated", "pinned", "rollback"],
  );
  for (const s of m.SCENARIOS) {
    const order = s.path.map((id) => m.MAP_ORDER.indexOf(id));
    assert.ok(order.every((i) => i >= 0), `${s.id} names an unknown stage`);
    assert.deepEqual([...order].sort((a, b) => a - b), order, `${s.id} goes backwards on the map`);
    assert.deepEqual(Object.keys(s.notes).sort(), [...s.path].sort(), `${s.id}: one note per step`);
  }
  // A fork is one way or the other, never both.
  for (const s of m.SCENARIOS) {
    assert.ok(!(s.path.includes("served") && s.path.includes("teacher")), s.id);
    assert.ok(!(s.path.includes("live") && s.path.includes("rollback")), s.id);
  }
  // Only the teacher's path stores a training example: the student and pinned paths never reach Night School.
  assert.ok(!m.SCENARIO_BY_ID.student.path.includes("school"));
  assert.ok(!m.SCENARIO_BY_ID.pinned.path.includes("student") && !m.SCENARIO_BY_ID.pinned.path.includes("school"));
});

test("stepping and keyboard focus", () => {
  const esc = m.SCENARIO_BY_ID.escalated;
  assert.equal(m.stepOf(esc, "request"), 1);
  assert.equal(m.stepOf(esc, "served"), 0);
  assert.equal(m.stepFrom(esc, "confidence", 1), "teacher");
  assert.equal(m.stepFrom(esc, "request", -1), "request");
  assert.equal(m.stepFrom(esc, "live", 1), "live");
  assert.equal(m.stepFrom(m.SCENARIO_BY_ID.pinned, "student", 1), "request", "off the path: back to its start");
  assert.equal(m.MAP_ORDER.length, m.STAGES.length);
  assert.deepEqual([...m.MAP_ORDER].sort(), m.STAGES.map((s) => s.id).sort());
  assert.equal(m.moveFocus("request", "ArrowLeft"), "request");
  assert.equal(m.moveFocus("request", "ArrowRight"), "route");
  assert.equal(m.moveFocus("ledger", "ArrowDown"), "school", "the day lane runs into the night lane");
  assert.equal(m.moveFocus("route", "End"), "rollback");
  assert.equal(m.moveFocus("route", "Home"), "request");
  assert.equal(m.moveFocus("route", "a"), null);
  assert.equal(m.pathText(m.SCENARIO_BY_ID.pinned), "Request → Route → Teacher answers → Ledger");
  assert.equal(m.sourceUrl("https://github.com/x/y", "ratchet/router.py"), "https://github.com/x/y/blob/main/ratchet/router.py");
});
