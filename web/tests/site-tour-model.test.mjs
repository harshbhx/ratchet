// The avatars' state transitions and the site tour's steps and wording, as pure functions. `npm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { EXPRESSIONS, lookOffset, nextExpression, themeLook } from "../src/components/avatar/avatar-model.ts";
import { SITE_STEPS, nextAvailable, progressText, sendLines, sendMood, stepBody, wantsSiteTour } from "../src/components/tour/site-tour-model.ts";

test("each event sets one expression, and reset goes back to idle", () => {
  assert.equal(nextExpression("idle", { type: "greet" }), "wave");
  assert.equal(nextExpression("wave", { type: "send" }), "thinking");
  assert.equal(nextExpression("thinking", { type: "answer" }), "happy");
  assert.equal(nextExpression("thinking", { type: "limit" }), "sleepy");
  assert.equal(nextExpression("thinking", { type: "error" }), "surprised");
  assert.equal(nextExpression("happy", { type: "scores" }), "proud");
  assert.equal(nextExpression("proud", { type: "reset" }), "idle");
  for (const e of ["greet", "send", "answer", "limit", "error", "scores", "reset"]) {
    assert.ok(EXPRESSIONS.includes(nextExpression("idle", { type: e })));
  }
});

test("the eyes stay within the limit and settle when the pointer is close", () => {
  for (const [px, py] of [[1000, 0], [-500, 300], [0, 900], [3, 4]]) {
    const o = lookOffset(px, py, 0, 0, 2.2);
    assert.ok(Math.hypot(o.x, o.y) <= 2.2 + 1e-9);
  }
  assert.deepEqual(lookOffset(5, 5, 5, 5, 2), { x: 0, y: 0 });
  assert.ok(Math.hypot(lookOffset(10, 0, 0, 0, 2).x, 0) < 2);
  assert.ok(lookOffset(1000, 0, 0, 0, 2).x > 1.99);
});

test("sunglasses only in the dark theme, and not while squinting", () => {
  assert.deepEqual(themeLook(true, false), { squint: false, sunglasses: true });
  assert.deepEqual(themeLook(true, true), { squint: true, sunglasses: false });
  assert.deepEqual(themeLook(false, false), { squint: false, sunglasses: false });
});

test("the send step maps each outcome to a mood, and Ultra speaks only after a teacher answer", () => {
  assert.equal(sendMood("sending"), "thinking");
  assert.equal(sendMood("answer"), "happy");
  assert.equal(sendMood("limit"), "sleepy");
  assert.equal(sendMood("error"), "surprised");
  assert.equal(sendLines("sending", null, true).ultra, null);
  assert.match(sendLines("answer", "teacher", true).ultra, /Nebius Token Factory/);
  assert.doesNotMatch(sendLines("answer", "teacher", false).ultra, /Nebius/);
  assert.equal(sendLines("answer", "student", true).ultra, null);
  assert.match(sendLines("limit", null, true).nano, /00:00 UTC/);
});

test("the speech claims no live student and no live training, and uses no banned wording", () => {
  const exams = { exam1: { adapter: "39.0%", base: "12.8%" }, exam2: { adapter: "33.0%", base: "12.5%" } };
  const text = [
    ...SITE_STEPS.map((s) => s.title + " " + stepBody(s.id, { exams, who: "Teacher" })),
    ...["waiting", "sending", "answer", "limit", "error"].flatMap((o) => [...Object.values(sendLines(o, "teacher", true)), ...Object.values(sendLines(o, "teacher", false))]),
  ]
    .filter(Boolean)
    .join(" ");
  assert.ok(!/simulat|demo/i.test(text));
  assert.match(stepBody("scores", { exams }), /39\.0% and 33\.0%.*12\.8% and 12\.5%/);
  assert.match(stepBody("scores", { exams }), /does not serve this site's calls yet/);
  assert.match(sendLines("answer", "teacher", true).nano, /has not run yet/);
});

test("steps are found past unavailable ones, in both directions", () => {
  const ok = (i) => i !== 3;
  assert.equal(nextAvailable(9, 2, 1, ok), 4);
  assert.equal(nextAvailable(9, 4, -1, ok), 2);
  assert.equal(nextAvailable(9, 8, 1, ok), null);
  assert.equal(nextAvailable(9, 0, -1, ok), null);
  assert.equal(progressText(2, 9), "3 / 9");
});

test("?tour=1 starts the site tour only on the story page and /live", () => {
  assert.equal(wantsSiteTour("/", "?tour=1"), true);
  assert.equal(wantsSiteTour("/live", "?x=1&tour=1"), true);
  assert.equal(wantsSiteTour("/judges", "?tour=1"), false);
  assert.equal(wantsSiteTour("/dashboard", "?tour=1"), false);
  assert.equal(wantsSiteTour("/", ""), false);
});
