// The Scout chips and runs-left counter, and the landing page's live request box (pure helpers). Run with `npm test`.
import assert from "node:assert/strict";
import { register } from "node:module";
import { test } from "node:test";

register("./ts-hooks.mjs", import.meta.url);
const s = await import("../src/pages/scout/scout-model.ts");
const r = await import("../src/pages/landing/try-request-model.ts");

test("chips: curated in live mode, the gateway's own in simulation, none before it answers", () => {
  assert.deepEqual(s.chipQuestions(null), []);
  assert.deepEqual(s.chipQuestions({ mode: "live", examples: ["a", "b"], free_text: true }), s.LIVE_CHIPS);
  assert.deepEqual(s.chipQuestions({ mode: "mock", examples: ["a", "b"], free_text: false }), ["a", "b"]);
  assert.ok(s.LIVE_CHIPS.length >= 3 && s.LIVE_CHIPS.length <= 4);
});

test("runs left comes only from the fields the status sends", () => {
  assert.deepEqual(s.runsLeftToday({ runs_today: 3, max_runs_per_day: 20 }), { left: 17, max: 20, used: 3 });
  assert.equal(s.runsLeftToday({ runs_today: 25, max_runs_per_day: 20 }).left, 0);
  assert.equal(s.runsLeftToday({ runs_today: 3, max_runs_per_day: null }), null);
  assert.equal(s.runsLeftToday({ runs_today: undefined, max_runs_per_day: 20 }), null);
  assert.equal(s.runsLeftToday({ runs_today: 0, max_runs_per_day: 0 }), null);
  assert.equal(s.runsLeftToday(null), null);
  assert.equal(s.runsLeftText({ left: 1, max: 20, used: 19 }), "1 run left today");
  assert.equal(s.runsLeftText({ left: 5, max: 20, used: 15 }), "5 runs left today");
  assert.match(s.runsLeftText({ left: 0, max: 20, used: 20 }), /No demo runs left/);
});

test("the live request is short, small and not stored", () => {
  const b = r.buildBody("  " + "x".repeat(500));
  assert.equal(b.messages[0].content.length, r.MAX_PROMPT_CHARS);
  assert.equal(b.max_tokens, r.MAX_TOKENS);
  assert.ok(b.max_tokens <= 150);
  assert.equal(b.store, false);
  assert.equal(b.model, "ratchet");
});

test("the answer is read defensively", () => {
  const a = r.parseAnswer({ model: "m1", choices: [{ message: { content: " Hi " } }], ratchet: { served_by: "student" } });
  assert.deepEqual(a, { text: "Hi", servedBy: "student", model: "m1" });
  assert.equal(r.parseAnswer({ choices: [{ message: { content: "x" } }] }).servedBy, null);
  assert.equal(r.parseAnswer({ choices: [{ message: { content: "x" } }], ratchet: { served_by: "upstream" } }).servedBy, null);
  assert.equal(r.parseAnswer({ choices: [] }), null);
  assert.equal(r.parseAnswer(null), null);
});

test("errors are calm and never blame the visitor", () => {
  for (const st of [0, 400, 429, 502, 503, 504, 500]) assert.match(r.errorText(st), /\./);
  assert.match(r.errorText(0), /Nothing was charged/);
});
