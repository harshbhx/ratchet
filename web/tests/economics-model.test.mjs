// The Economics panel's pure helpers (src/pages/dashboard/economics-model.ts): every number a gridline prints is the
// value it sits at, both ends of the break-even meter carry a tick, and the empty state and window label say only
// what the report says. Run with `npm test` (Node 22.6 or later).
import assert from "node:assert/strict";
import { register } from "node:module";
import { test } from "node:test";

register("./ts-hooks.mjs", import.meta.url);
const m = await import("../src/pages/dashboard/economics-model.ts");

const num = (label) => Number(label.replace(/[−]/g, "-").replace(/[$,]/g, ""));
const ZERO_REPORT = {
  requests: 0,
  endpoint_hours: 0,
  training_hours: 0,
  daily: [],
  recommendation: "insufficient_data",
  views: { per_token: { total_usd: 0, savings_usd: 0, savings_pct: null }, gpu_hours: { total_usd: 0, savings_usd: 0, savings_pct: null } },
};

test("stepDigits gives the decimals a step's multiples need", () => {
  const cases = [
    [10, 0],
    [1, 0],
    [5, 0],
    [2.5, 1],
    [0.5, 1],
    [0.1, 1],
    [0.30000000000000004, 1],
    [0.25, 2],
    [0.025, 3],
    [1e-7, 7],
    [2.5e-7, 8],
    [0, 0],
    [Number.NaN, 0],
  ];
  for (const [step, digits] of cases) assert.equal(m.stepDigits(step), digits, `step ${step}`);
});

test("break-even meter: a 2.5 step labels 2.5 and 7.5, not 3 and 8", () => {
  const s = m.meterScale([5, 8]);
  assert.equal(s.log, false);
  assert.deepEqual(
    s.ticks.map((t) => t.label),
    ["0.0", "2.5", "5.0", "7.5", "10.0"],
  );
  assert.equal(s.at(8), 0.8);
  assert.equal(s.ticks.find((t) => t.label === "7.5").at, 0.75);
});

test("break-even meter: every linear tick label is the value it sits at, and the last one ends the axis", () => {
  for (let i = -30; i <= 40; i++) {
    const v = Math.round(Math.pow(10, i / 10) * 1000) / 1000;
    if (!(v > 0)) continue;
    for (const values of [[v], [v, v * 1.6], [v * 0.2, v]]) {
      const s = m.meterScale(values);
      if (s.log) continue;
      for (const t of s.ticks) assert.equal(num(t.label), t.value, `${values}: ${t.label} at ${t.value}`);
      assert.equal(s.ticks[0].at, 0);
      assert.equal(s.ticks[s.ticks.length - 1].at, 1, `${values}`);
      assert.equal(s.ratio, 1);
    }
  }
});

test("break-even meter: a log scale over more than 6 decades says 100x and ends on a tick", () => {
  const s = m.meterScale([0.05, 1563, 51779]);
  assert.equal(s.log, true);
  assert.equal(s.ratio, 100);
  assert.deepEqual(
    s.ticks.map((t) => t.label),
    ["0.01", "1", "100", "10k", "1M"],
  );
  assert.equal(s.ticks[0].at, 0);
  assert.equal(s.ticks[s.ticks.length - 1].at, 1);
  assert.ok(s.at(51779) < 1);
});

test("break-even meter: a log scale up to 6 decades has a gridline per decade, both ends ticked", () => {
  for (const values of [[3, 600], [0.4, 1563, 40], [12, 9_000_000]]) {
    const s = m.meterScale(values);
    assert.equal(s.log, true, `${values}`);
    assert.equal(s.ratio, 10);
    assert.equal(s.ticks[0].at, 0);
    assert.equal(s.ticks[s.ticks.length - 1].at, 1);
    for (let i = 1; i < s.ticks.length; i++) assert.equal(Math.round(s.ticks[i].value / s.ticks[i - 1].value), 10);
  }
});

test("daily chart: money axis labels are the value of their gridline, with one precision for all", () => {
  // Every day's endpoint costs $11.00 (about 7 h at $1.55): a 2.5 step from −12.5.
  const ticks = m.signedTicks(-11, 0.9);
  const step = ticks[1] - ticks[0];
  assert.equal(+step.toPrecision(12), 2.5);
  const labels = ticks.map((t) => m.fmtAxisMoney(t, step));
  assert.deepEqual(labels, ["−$12.5", "−$10.0", "−$7.5", "−$5.0", "−$2.5", "$0", "$2.5"]);
  for (const [lo, hi] of [[-11, 0.9], [-0.11, 0.02], [-0.0011, 0.0004], [-147.9, 1.7], [-3, 26], [0, 0.07]]) {
    const ts = m.signedTicks(lo, hi);
    const st = ts[1] - ts[0];
    for (const t of ts) assert.equal(num(m.fmtAxisMoney(t, st)), t, `${lo}..${hi}: ${t}`);
  }
});

test("empty state only when the report says it is empty", () => {
  assert.equal(m.isEmptyReport(ZERO_REPORT), true);
  const partial = { recommendation: "self_host", views: { per_token: { total_usd: 1, savings_usd: 3, savings_pct: 0.75 }, gpu_hours: { total_usd: 2, savings_usd: 2, savings_pct: 0.5 } } };
  assert.equal(m.isEmptyReport(partial), false);
  assert.equal(m.isEmptyReport({ ...ZERO_REPORT, requests: 3 }), false);
  assert.equal(m.isEmptyReport({ ...ZERO_REPORT, endpoint_hours: 0.5 }), false);
  assert.equal(m.isEmptyReport({ ...ZERO_REPORT, daily: [{ day: 1 }] }), false);
});

test("window label: a truncated daily list does not stand in for the window's first day", () => {
  const days = (from, to) => Array.from({ length: to - from + 1 }, (_, i) => ({ day: from + i }));
  const w = (since, until) => ({ since_day: since, until_day: until, now: 0 });
  assert.equal(m.windowLabel({ ...ZERO_REPORT, window: w(null, 1730), daily: days(1000, 1730), daily_truncated: true }), "Through day 1730");
  assert.equal(m.windowLabel({ ...ZERO_REPORT, window: w(990, 1730), daily: days(1000, 1730), daily_truncated: true }), "Days 990–1730");
  assert.equal(m.windowLabel({ ...ZERO_REPORT, window: w(null, 4), daily: days(1, 4), daily_truncated: false }), "Days 1–4");
  assert.equal(m.windowLabel({ ...ZERO_REPORT, window: w(null, 4), daily: days(4, 4) }), "Day 4");
  assert.equal(m.windowLabel({ ...ZERO_REPORT }), null);
});
