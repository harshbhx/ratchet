// The random-split tile (src/lib/real-story-model.ts splitTile): never a bare dash, from the process, then the Worker's totals, then list prices.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { register } from "node:module";

register("./ts-hooks.mjs", import.meta.url);
const m = await import("../src/lib/real-story-model.ts");
const costs = m.parseCosts(readFileSync(new URL("../src/data/real-costs.json", import.meta.url), "utf8"));

const stats = { demo_split: { enabled: true, requests: 4, cost_usd: 0.00013, baseline_usd: 0.004, saved_usd: 0.00387, savings_pct: 0.97 } };
const persistent = (over = {}) => ({
  recorded: true,
  since: "2026-10-29",
  updated: "2026-10-29T10:00:00.000Z",
  instances: 2,
  totals: { requests: 19, requests_teacher: 15, requests_split: 4, requests_student: 0, student_tokens_in: 1, student_tokens_out: 1, cost_usd: 0.0161, errors: 28, server_errors: 0, scout_runs: 1, ...over },
});
const text = (t) => `${t.value} ${t.label} ${t.note}`;

test("the Worker's cumulative totals come first, never this process's own counter", () => {
  const t = m.splitTile(stats, persistent(), costs);
  assert.equal(t.kind, "since");
  assert.equal(t.value, "4");
});

test("this process's own counter is used only when the totals are unreachable", () => {
  const t = m.splitTile(stats, null, costs);
  assert.equal(t.kind, "process");
  assert.match(t.label, /saved by the random split/);
});

test("totals with no split answers say so in a sentence, not as a zero", () => {
  const t = m.splitTile(stats, persistent({ requests_split: 0, requests_teacher: 19 }), costs);
  assert.equal(t.kind, "listed");
  assert.match(t.note, /Ultra answered all of the 19 live requests since 29 Oct 2026; the random split has not sent one/);
});

test("after a restart the Worker's totals say how many the hosted Nano answered, with the date and no invented saving", () => {
  const t = m.splitTile(null, persistent(), costs);
  assert.equal(t.kind, "since");
  assert.equal(t.value, "4");
  assert.match(t.label, /^requests answered by the hosted Nano 30B since 29 Oct 2026/);
  assert.doesNotMatch(text(t), /saved by/);
  assert.match(t.note, /\$0\.0161/);
});

test("with neither, a measured list-price fact stands in", () => {
  for (const p of [null, persistent({ requests_split: 0 })]) {
    const t = m.splitTile({ demo_split: { enabled: true, requests: 0 } }, p, costs);
    assert.equal(t.kind, "listed");
    assert.equal(t.badge, "Measured");
  }
});

test("no case renders a bare dash", () => {
  for (const [s, p] of [[stats, persistent()], [null, persistent()], [null, null], [undefined, undefined], [stats, null]]) {
    const t = m.splitTile(s, p, costs);
    assert.ok(!text(t).includes("–"), JSON.stringify(t));
    assert.notEqual(t.value.trim(), "");
  }
});
