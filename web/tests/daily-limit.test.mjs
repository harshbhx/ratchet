// The edge's daily limit per visitor: detection and the one plain message shown, with no recorded fallback.
// Pure helpers; run with `npm test`.
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
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
  assert.equal(m.errorText(429), "The live gateway has reached its limit for now. Please try again later.");
});

test("the limit message is one plain sentence pair, on both pages", () => {
  assert.equal(m.DAILY_LIMIT_TEXT, "You've used today's free tries. They reset at 00:00 UTC.");
  assert.equal(`${s.SCOUT_LIMIT_TITLE} ${s.SCOUT_LIMIT_TEXT}`, "You've used today's free tries. They reset at 00:00 UTC.");
});

test("there is no recorded fallback left: no recorded answers, no recorded runs", () => {
  assert.equal(existsSync(new URL("../src/data/recorded-tries.json", import.meta.url)), false);
  assert.equal(existsSync(new URL("../src/lib/recording-model.ts", import.meta.url)), false);
  assert.equal(m.recordedAnswer, undefined);
  assert.equal(s.recordingChoices, undefined);
});

test("Scout: the edge's refusal of a fourth run of a day is the daily limit", () => {
  assert.equal(s.isScoutDailyLimit("Daily limit reached: 3 Scout runs per visitor per day. It resets at 00:00 UTC."), true);
  assert.equal(s.isScoutDailyLimit("  Daily limit reached: anything"), true);
  assert.equal(s.isScoutDailyLimit("Too many Scout runs per hour. Try again later."), false);
  assert.equal(s.isScoutDailyLimit("Today's free budget is used up."), false);
  assert.equal(s.isScoutDailyLimit("The daily limit reached"), false);
  assert.equal(s.isScoutDailyLimit(""), false);
  assert.equal(s.isScoutDailyLimit(null), false);
});
