// Which numbers the dashboard headline shows (src/pages/dashboard/cumulative-model.ts): the cumulative totals beat this process's ledger.
import test from "node:test";
import assert from "node:assert/strict";
import { register } from "node:module";

register("./ts-hooks.mjs", import.meta.url);
const d = await import("../src/pages/dashboard/cumulative-model.ts");

const persistent = (over = {}) => ({
  recorded: true,
  since: "2026-10-29",
  updated: "2026-10-29T10:00:00.000Z",
  instances: 2,
  totals: { requests: 19, requests_teacher: 15, requests_split: 4, requests_student: 0, student_tokens_in: 1, student_tokens_out: 1, cost_usd: 0.0161, errors: 28, server_errors: 0, baseline_usd: 0, scout_runs: 1, ...over },
});
const withDays = { days: [{ day: 1 }] };
const empty = { days: [] };

test("persistent beats this process's ledger, even when the process has rows", () => {
  const h = d.headline({ mock: false, persistent: persistent(), stats: withDays });
  assert.equal(h.kind, "cumulative");
  assert.equal(h.items.find((i) => i.id === "requests").value, "19");
  const h1 = d.headline({ mock: false, persistent: persistent(), stats: empty });
  assert.equal(h1.kind, "cumulative", "an empty process ledger does not hide the totals");
});

test("while the totals are being read nothing is shown, so this process's numbers never flash", () => {
  assert.equal(d.headline({ mock: false, persistent: undefined, stats: withDays }).kind, "loading");
});

test("unreachable totals: this process's ledger when it has rows, the measured tiles when it has none", () => {
  assert.equal(d.headline({ mock: false, persistent: null, stats: withDays }).kind, "process");
  assert.equal(d.headline({ mock: false, persistent: null, stats: empty }).kind, "measured");
  assert.equal(d.headline({ mock: false, persistent: persistent({ requests: 0, scout_runs: 0 }), stats: empty }).kind, "measured");
});

test("mock mode has no Worker and shows its own ledger", () => {
  assert.equal(d.headline({ mock: true, persistent: undefined, stats: withDays }).kind, "process");
});

test("the fallback is labelled with when this server instance started", () => {
  assert.equal(d.instanceLabel(1793260327.7), "this server instance since 29 Oct 2026, 07:52 UTC");
  assert.equal(d.instanceLabel(null), "this server instance since its last start");
});

test("an empty chart is one plain sentence that points at the all-time tiles, with no zeros", () => {
  const s = d.emptyChartSentence(1793260327.7);
  assert.match(s, /^Nothing to plot for this server instance since 29 Oct 2026/);
  assert.match(s, /all-time totals are in the Overview tiles/);
  assert.doesNotMatch(s, /\b0%|\$0/);
  assert.match(d.chartCaption(null), /headline totals above are cumulative/);
});
