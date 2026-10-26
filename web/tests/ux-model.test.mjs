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
  // An older gateway without the public counts: the cap minus the runs so far.
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

test("a visitor's runs and budget leave out the judges' reserve", () => {
  // docs/API.md's example: cap 22, 7 held back for judges, 13 left for the public.
  const status = {
    mode: "live",
    budget_usd: 3,
    spent_today_usd: 0.0412,
    runs_today: 3,
    max_runs_per_day: 22,
    public_runs_left_today: 13,
    public_budget_left_usd: 2.0088,
    judge: { enabled: true, reserved_runs_per_day: 7 },
  };
  assert.deepEqual(s.runsLeftToday(status), { left: 13, max: 15, used: 2 });
  // Never more left than the gateway says, even if the reserve is missing.
  assert.deepEqual(s.runsLeftToday({ ...status, judge: undefined }), { left: 13, max: 22, used: 9 });
  assert.equal(s.runsLeftToday({ ...status, public_runs_left_today: 0 }).left, 0);
  // Mock mode: no cap, no counter.
  assert.equal(s.runsLeftToday({ ...status, max_runs_per_day: null, public_runs_left_today: null }), null);

  const b = s.budgetLeft(status);
  assert.equal(b.left, 2.0088);
  assert.equal(b.total, 3);
  assert.ok(Math.abs(b.share - 2.0088 / 3) < 1e-12);
  // An older gateway: the budget minus today's spend.
  assert.equal(s.budgetLeft({ ...status, public_budget_left_usd: undefined }).left, 3 - 0.0412);
  assert.equal(s.budgetLeft({ ...status, public_budget_left_usd: -1 }).left, 0);
  assert.equal(s.budgetLeft({ ...status, mode: "mock" }), null);
  assert.equal(s.budgetLeft({ ...status, budget_usd: null }), null);
  assert.equal(s.budgetLeft(null), null);
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
