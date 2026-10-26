// The edge's daily limit per visitor (PR #79) and the recorded answers shown instead: detection, the time left, the
// recorded data, and the Scout page's recording choices. Pure helpers; run with `npm test`.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { register } from "node:module";
import { test } from "node:test";

register("./ts-hooks.mjs", import.meta.url);
const m = await import("../src/pages/landing/try-request-model.ts");
const s = await import("../src/pages/scout/scout-model.ts");

const LIMIT_BODY = {
  error: {
    message: "Daily limit reached: 3 requests per visitor per day. It resets at 00:00 UTC.",
    type: "rate_limit_error",
    param: null,
    code: "daily_limit_reached",
  },
};

test("only the edge's daily-limit 429 is the daily limit", () => {
  assert.equal(m.isDailyLimit(429, LIMIT_BODY), true);
  // The gateway's own rate and budget limits are 429s with other codes: they keep the generic message.
  assert.equal(m.isDailyLimit(429, { error: { code: "rate_limit_exceeded", message: "Too many requests" } }), false);
  assert.equal(m.isDailyLimit(429, { error: { code: "insufficient_quota" } }), false);
  assert.equal(m.isDailyLimit(429, { detail: "Daily limit reached: 3 Scout runs per visitor per day." }), false);
  assert.equal(m.isDailyLimit(429, null), false);
  assert.equal(m.isDailyLimit(429, "daily_limit_reached"), false);
  assert.equal(m.isDailyLimit(429, {}), false);
  // The code on another status is not the limit.
  assert.equal(m.isDailyLimit(400, LIMIT_BODY), false);
  assert.equal(m.isDailyLimit(503, LIMIT_BODY), false);
  assert.equal(m.errorText(429), "The live demo has reached its limit for now. Please try again later.");
});

test("the limit message is the owner's words", () => {
  assert.equal(m.DAILY_LIMIT_TEXT, "You've used your 3 free requests today. They reset at 00:00 UTC.");
});

test("retry-after is whole seconds, else nothing", () => {
  assert.equal(m.retryAfterSeconds("18720"), 18720);
  assert.equal(m.retryAfterSeconds(" 60 "), 60);
  for (const bad of [null, undefined, "", "0", "-5", "1.5", "soon", "Wed, 21 Oct 2026 07:28:00 GMT"]) assert.equal(m.retryAfterSeconds(bad), null, String(bad));
});

test("the time left reads in hours and minutes, rounded up", () => {
  assert.equal(m.formatWait(5 * 3600 + 12 * 60), "5 h 12 min");
  assert.equal(m.formatWait(5 * 3600 + 11 * 60 + 1), "5 h 12 min");
  assert.equal(m.formatWait(5 * 3600), "5 h");
  assert.equal(m.formatWait(12 * 60), "12 min");
  assert.equal(m.formatWait(61), "2 min");
  assert.equal(m.formatWait(60), "under a minute");
  assert.equal(m.formatWait(1), "under a minute");
  assert.equal(m.formatWait(23 * 3600 + 59 * 60 + 59), "24 h");
  assert.equal(m.formatWait(0), null);
  assert.equal(m.formatWait(-3), null);
  assert.equal(m.formatWait(null), null);
  assert.equal(m.formatWait(Number.NaN), null);
  assert.equal(m.resetsIn(18720), "Resets in 5 h 12 min.");
  assert.equal(m.resetsIn(null), null);
});

const raw = readFileSync(new URL("../src/data/recorded-tries.json", import.meta.url), "utf8");

test("the recorded data has the four real answers with every field", () => {
  const list = m.parseRecordedTries(raw);
  assert.equal(list.length, 4);
  assert.deepEqual(
    list.map((t) => t.prompt),
    [
      "In one sentence, what is LoRA fine-tuning?",
      "In one sentence, what is knowledge distillation?",
      "What does a model cascade do, in one line?",
      "In one sentence, what is a canary release?",
    ],
  );
  for (const t of list) {
    assert.ok(t.text.length > 40);
    assert.ok(t.servedBy === "student" || t.servedBy === "teacher");
    assert.match(t.model, /^nvidia\//);
    assert.ok(t.route);
    assert.ok(t.costUsd > 0);
    assert.equal(t.recorded, "2026-10-27");
  }
  // The raw file keeps its snake_case keys: every entry has all eight.
  for (const e of JSON.parse(raw)) assert.deepEqual(Object.keys(e).sort(), ["cost_usd", "model", "prompt", "recorded", "route", "served_by", "text"]);
  const cascade = list[2];
  assert.equal(cascade.route, "demo_split");
  assert.equal(cascade.servedBy, "student");
  assert.equal(cascade.model, "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B");
  assert.equal(list[1].text.includes('"student"'), true, "the quotes in the text are kept as recorded");
});

test("a recorded answer wears the same badge as a live one, and says it is not live", async () => {
  const who = await import("../src/lib/who-model.ts");
  const list = m.parseRecordedTries(raw);
  const a = m.recordedAnswer(list[2]);
  assert.deepEqual(a, { text: list[2].text, servedBy: "student", model: "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B", route: "demo_split" });
  // The demo-split route reads "Nano 30B · demo split", exactly as a live answer's badge does.
  assert.equal(who.whoLabel({ servedBy: a.servedBy, model: a.model, route: a.route }).text, "Nano 30B · demo split");
  const t = m.recordedAnswer(list[0]);
  assert.equal(who.whoLabel({ servedBy: t.servedBy, model: t.model, route: t.route }).text, "Ultra · Token Factory");
  assert.equal(m.recordedLabel("2026-10-27"), "Recorded on 27 Oct 2026 · not a live call");
  assert.equal(m.recordedDay("not a date"), "not a date");
});

test("a bad edit never shows half an answer", () => {
  assert.deepEqual(m.parseRecordedTries("not json"), []);
  assert.deepEqual(m.parseRecordedTries("{}"), []);
  const good = JSON.parse(raw)[0];
  const { text: _t, ...noText } = good;
  const kept = m.parseRecordedTries(JSON.stringify([good, noText, { ...good, served_by: "tool" }, { ...good, recorded: "yesterday" }, null, 3]));
  assert.equal(kept.length, 1);
});

test("Scout: the daily-limit detail is recognised, other errors are not", () => {
  assert.equal(s.isScoutDailyLimit("Daily limit reached: 3 Scout runs per visitor per day. It resets at 00:00 UTC."), true);
  assert.equal(s.isScoutDailyLimit("  Daily limit reached: anything"), true);
  assert.equal(s.isScoutDailyLimit("Too many Scout runs per hour. Try again later."), false);
  assert.equal(s.isScoutDailyLimit("Today's demo budget is used up."), false);
  assert.equal(s.isScoutDailyLimit("The daily limit reached"), false);
  assert.equal(s.isScoutDailyLimit(""), false);
  assert.equal(s.isScoutDailyLimit(null), false);
  assert.equal(s.SCOUT_LIMIT_TITLE, "You've used your 3 free Scout runs today.");
  assert.equal(`${s.SCOUT_LIMIT_TITLE} ${s.SCOUT_LIMIT_TEXT}`, "You've used your 3 free Scout runs today. They reset at 00:00 UTC. Meanwhile, watch a recorded run:");
});

test("Scout: recordings are offered live first, newest first within each", () => {
  const list = [
    { id: "a", mode: "mock" },
    { id: "b", mode: "live" },
    { id: "c", mode: "live" },
  ];
  assert.deepEqual(s.recordingChoices(list).map((r) => r.id), ["b", "c", "a"]);
  assert.deepEqual(s.recordingChoices([]), []);
  assert.deepEqual(s.recordingChoices(null), []);
});
