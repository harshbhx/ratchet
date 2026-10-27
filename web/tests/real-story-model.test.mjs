// The story page's real-number helpers (src/lib/real-story-model.ts): the costs file parses, the Nano/Ultra ratio and the labelling
// total follow from it, and the live demo counter shows a number only when the stats carry one. Run with `npm test`.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { register } from "node:module";
import { test } from "node:test";

register("./ts-hooks.mjs", import.meta.url);
const m = await import("../src/lib/real-story-model.ts");
const costs = m.parseCosts(readFileSync(new URL("../src/data/real-costs.json", import.meta.url), "utf8"));

test("both labelling rounds add up and the per-call ratio is a whole number", () => {
  assert.equal(Math.round(m.labellingTotal(costs) * 100) / 100, 12.05);
  assert.equal(m.callRatio(costs, "triage"), 18);
  assert.equal(m.callRatio(costs, "reader"), 39);
});

test("a costs file with a missing price is refused", () => {
  const bad = JSON.parse(JSON.stringify(costs));
  bad.per_call.nano_triage = 0;
  assert.throws(() => m.parseCosts(JSON.stringify(bad)));
});

const split = (over) => ({
  demo_split: { enabled: true, rate: 0.4, audit_rate: 0.1, requests: 12, of_requests: 30, share: 0.4, audits: 2, agreed: 2, agreement: 1, cost_usd: 0.01, baseline_usd: 0.05, saved_usd: 0.04, savings_pct: 0.8, ...over },
});

test("the live counter is null without stats, without a split or before it served anything", () => {
  assert.equal(m.demoSavings(null), null);
  assert.equal(m.demoSavings({}), null);
  assert.equal(m.demoSavings(split({ enabled: false })), null);
  assert.equal(m.demoSavings(split({ requests: 0 })), null);
});

test("the live counter reads the stats' own figures", () => {
  assert.deepEqual(m.demoSavings(split({})), { requests: 12, costUsd: 0.01, baselineUsd: 0.05, savedUsd: 0.04, savingsPct: 0.8 });
});
