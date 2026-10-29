// Judges see all the numbers: the live strip's headline is the Worker's cumulative totals, never one process's numbers since its last restart.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { register } from "node:module";

register("./ts-hooks.mjs", import.meta.url);
const live = await import("../src/lib/live-strip-model.ts");
const m = await import("../src/lib/measured-model.ts");
const measured = m.parseMeasured(readFileSync(new URL("../src/data/measured.json", import.meta.url), "utf8"));

const persistent = (over = {}) => ({
  recorded: true,
  since: "2026-10-29",
  updated: "2026-10-29T10:00:00.000Z",
  instances: 2,
  totals: {
    requests: 19, requests_teacher: 15, requests_split: 4, requests_student: 0, student_tokens_in: 100, student_tokens_out: 50,
    cost_usd: 0.0161, errors: 28, server_errors: 0, baseline_usd: 0, scout_runs: 1, ...over,
  },
});
const byId = (items, id) => items.find((i) => i.id === id);
const text = (items) => items.map((i) => `${i.label} ${i.value} ${i.sub}`).join(" | ");

test("persistent totals are the headline: 19 requests, the Nano share as a count and a percent, since the date", () => {
  const items = live.cumulativeItems(persistent());
  assert.equal(byId(items, "requests").value, "19");
  assert.match(byId(items, "requests").label, /29 Oct 2026/);
  assert.equal(byId(items, "nano").value, "21%");
  assert.equal(byId(items, "nano").sub, "4 of 19, picked at random");
  assert.ok(items.every((i) => i.from?.tag === "Live"));
});

test("no baseline, no savings percentage: the Token Factory cost is shown instead", () => {
  const items = live.cumulativeItems(persistent({ baseline_usd: 0 }));
  assert.equal(byId(items, "savings"), undefined);
  assert.equal(byId(items, "cost").value, "$0.0161");
  const noField = live.cumulativeItems(persistent({ baseline_usd: undefined }));
  assert.equal(byId(noField, "savings"), undefined);
});

test("savings come only from the cumulative baseline, and only when real", () => {
  assert.equal(byId(live.cumulativeItems(persistent({ baseline_usd: 0.0644 })), "savings").value, "75%");
  // A baseline below the cost is no saving, and a rounding of nothing is not shown as 0%.
  assert.equal(byId(live.cumulativeItems(persistent({ baseline_usd: 0.01 })), "savings"), undefined);
  assert.equal(byId(live.cumulativeItems(persistent({ baseline_usd: 0.01616 })), "savings"), undefined);
});

test("no split answers: a sentence, never 0%", () => {
  const items = live.cumulativeItems(persistent({ requests_split: 0, requests_teacher: 19 }));
  assert.equal(byId(items, "nano").value, "none yet");
  assert.equal(byId(items, "nano").sub, "Ultra answered all 19 requests so far");
  assert.doesNotMatch(text(items), /\b0%|\$0\.00\b/);
});

test("server errors: none in words, a count otherwise, the old errors total never", () => {
  assert.deepEqual([byId(live.cumulativeItems(persistent()), "errors").value, byId(live.cumulativeItems(persistent()), "errors").sub], ["none", "no failed model calls"]);
  assert.equal(byId(live.cumulativeItems(persistent({ server_errors: 2 })), "errors").value, "2");
  assert.doesNotMatch(text(live.cumulativeItems(persistent())), /28/);
});

test("no totals, no cumulative strip: the caller falls back to the dated measured cards", () => {
  assert.equal(live.cumulativeItems(null), null);
  assert.equal(live.cumulativeItems(persistent({ requests: 0, scout_runs: 0 })), null);
  assert.ok(live.emptyItems({ measured, persistent: null, release: { live: null, flight: null } }).length >= 3);
});

test("all real traffic to date names every part it adds up", () => {
  const t = m.allToDate(persistent(), measured);
  assert.match(t.line, /^All real traffic to date: /);
  assert.match(t.line, /19 live requests since 29 Oct 2026/);
  assert.match(t.line, /live smoke run/);
  assert.match(t.line, /recorded Scout runs/);
  assert.equal(t.requests, 19 + measured.smoke.requests + measured.scout.runs.reduce((n, r) => n + r.llm_calls, 0));
  assert.equal(m.allToDate(null, measured), null);
});

test("persistent baseline_usd parses and a bad one is rejected", () => {
  assert.equal(m.parsePersistent(persistent({ baseline_usd: 1.5 })).totals.baseline_usd, 1.5);
  assert.equal(m.parsePersistent(persistent({ baseline_usd: -1 })), null);
});
