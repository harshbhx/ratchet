// The story page's pure helpers: the honest bill (src/pages/landing/bill-model.ts) and the trust chapter's claims
// (src/pages/landing/trust-model.ts). Every figure the bill prints comes from a cost report, missing fields stay
// unknown rather than zero, and the reference run renders through the same code as a live report. Run with `npm test`.
import assert from "node:assert/strict";
import { register } from "node:module";
import { test } from "node:test";

register("./ts-hooks.mjs", import.meta.url);
const m = await import("../src/pages/landing/bill-model.ts");
const t = await import("../src/pages/landing/trust-model.ts");
const { FIXTURE_COST_REPORT } = await import("./fixtures/cost-report.mjs");
const MINIMAL = {
  recommendation: "insufficient_data",
  views: { per_token: { total_usd: 0, savings_usd: 0, savings_pct: null }, gpu_hours: { total_usd: 0, savings_usd: 0, savings_pct: null } },
};

test("the reference report renders through billOf with every figure the chapter prints", () => {
  const b = m.billOf(FIXTURE_COST_REPORT);
  assert.equal(b.baseline, 21.86);
  assert.deepEqual(b.perToken, { total: 6.74, savings: 15.12, pct: 0.692 });
  assert.deepEqual(b.gpuHours, { total: 380.34, savings: -358.48, pct: -16.398 });
  assert.equal(b.endpointPerHour, 1.55);
  assert.equal(b.breakEven, 1575);
  assert.equal(b.crossover, 51528);
  assert.equal(b.recommendation, "hosted_fallback");
  assert.equal(b.verified, false);
  assert.equal(m.baselineName(b.teacherModel), "all-Ultra");
  // Its parts add up to its totals, as the chapter's breakdown shows them (each rounded to cents).
  assert.ok(Math.abs(b.teacherUsd + b.endpointUsd + b.trainingUsd + b.hostedStudentUsd - b.gpuHours.total) < 0.006);
  assert.ok(Math.abs(b.teacherUsd + b.studentTokensUsd - b.perToken.total) < 0.006);
  assert.ok(Math.abs(b.baseline - b.perToken.total - b.perToken.savings) < 0.006);
  assert.ok(Math.abs(b.baseline - b.gpuHours.total - b.gpuHours.savings) < 0.006);
  // The formulas the chapter prints hold for the rounded numbers it prints them with (ADR 0023).
  assert.ok(Math.abs(b.endpointPerHour / b.avoided - b.breakEven) / b.breakEven < 0.001);
  assert.ok(Math.abs(b.endpointPerHour / b.hostedPerAnswer - b.crossover) / b.crossover < 0.002);
  assert.ok(Math.abs(b.observed / b.breakEven - b.utilization) < 0.0006);
  // As printed: "$1.55 ÷ $0.000984" beside "1,575", and the hosted Nano's "$0.0000301" beside "51,528".
  assert.equal(m.fmtSmallUsd(b.endpointPerHour), "$1.55");
  assert.equal(m.fmtSmallUsd(b.avoided, 3), "$0.000984");
  assert.equal(m.fmtSmallUsd(b.hostedPerAnswer, 3), "$0.0000301");
  const printed = (s) => Number(s.replace(/[$,]/g, ""));
  assert.ok(Math.abs(printed(m.fmtSmallUsd(b.endpointPerHour)) / printed(m.fmtSmallUsd(b.avoided, 3)) - b.breakEven) < 1);
});

test("billOf keeps a field the gateway did not send as unknown, never zero", () => {
  const b = m.billOf(MINIMAL);
  for (const k of ["teacherUsd", "endpointUsd", "endpointHours", "endpointPerHour", "avoided", "breakEven", "crossover", "observed", "utilization", "idleShare"]) {
    assert.equal(b[k], null, k);
  }
  assert.equal(b.explanation, null);
  assert.equal(b.teacherModel, null);
  assert.equal(m.baselineName(b.teacherModel), "all-teacher");
  // The bars fall back to one part per bill when the report has no breakdown.
  const rows = m.barRows({ ...b, baseline: 10, perToken: { total: 4, savings: 6, pct: 0.6 }, gpuHours: { total: 30, savings: -20, pct: -2 } }, "all-teacher");
  assert.deepEqual(rows.map((r) => r.parts.length), [1, 1, 1]);
  assert.deepEqual(rows.map((r) => r.parts[0].usd), [10, 4, 30]);
  assert.equal(rows[0].label, "All-teacher");
  // An endpoint rate is the per-GPU price times GPUs when the whole-endpoint figure is missing.
  const rate = m.billOf({ ...MINIMAL, prices: { verified: false, student_gpu_usd_per_hour: 2, student_gpus: 3 } });
  assert.equal(rate.endpointPerHour, 6);
  assert.equal(m.billOf({ ...MINIMAL, explanation: "   " }).explanation, null);
});

test("isCostReport and tellsTheStory refuse what cannot carry the chapter", () => {
  assert.equal(m.isCostReport(null), false);
  assert.equal(m.isCostReport({}), false);
  assert.equal(m.isCostReport({ recommendation: "self_host", views: { per_token: { total_usd: 1, savings_usd: 0 } } }), false);
  assert.equal(m.isCostReport({ recommendation: 3, views: MINIMAL.views }), false);
  assert.equal(m.isCostReport({ ...MINIMAL, views: { ...MINIMAL.views, gpu_hours: { total_usd: "1", savings_usd: 0 } } }), false);
  assert.equal(m.isCostReport(MINIMAL), true);
  assert.equal(m.isCostReport(FIXTURE_COST_REPORT), true);
  assert.equal(m.tellsTheStory(FIXTURE_COST_REPORT), true);
  assert.equal(m.tellsTheStory(null), false);
  assert.equal(m.tellsTheStory(MINIMAL), false);
  assert.equal(m.tellsTheStory({ ...FIXTURE_COST_REPORT, endpoint_hours: 0 }), false); // no uptime recorded
  assert.equal(m.tellsTheStory({ ...FIXTURE_COST_REPORT, requests: 0 }), false);
  assert.equal(m.tellsTheStory({ ...FIXTURE_COST_REPORT, break_even_student_requests_per_gpu_hour: null }), false);
});

test("verdict says less, more or the same against the baseline, in words", () => {
  const b = m.billOf(FIXTURE_COST_REPORT);
  assert.deepEqual(m.verdict(b.perToken, b.baseline, "all-Ultra"), { kind: "less", text: "69% less than all-Ultra" });
  assert.deepEqual(m.verdict(b.gpuHours, b.baseline, "all-Ultra"), { kind: "more", text: "17× the all-Ultra bill" });
  assert.equal(m.verdict({ total: 5, savings: 0, pct: 0 }, 5, "all-teacher").kind, "same");
  assert.equal(m.verdict({ total: 5, savings: -5, pct: null }, 0, "all-teacher").kind, "unknown");
  // Without a share, the share is worked out from the dollars.
  assert.equal(m.verdict({ total: 2.5, savings: 7.5, pct: null }, 10, "all-teacher").text, "75% less than all-teacher");
});

test("number formats", () => {
  assert.deepEqual([17.4, 1.43, 2.0, 0.5, 1234.6, -3.25, null, NaN].map(m.fmtTimes), ["17×", "1.4×", "2×", "0.5×", "1,235×", "−3.3×", "–", "–"]);
  assert.deepEqual([0.000984, 0.0000301, 1.55, 0.5, 0, 12.345, -0.0042, null].map((v) => m.fmtSmallUsd(v)), [
    "$0.00098",
    "$0.000030",
    "$1.55",
    "$0.50",
    "$0",
    "$12.35",
    "−$0.0042",
    "–",
  ]);
  assert.deepEqual([37800, 37506, 1240, 12, 0, 999999, null].map(m.fmtAbout), ["37,800", "37,500", "1,240", "12", "0", "1,000,000", "–"]);
  assert.deepEqual([0.000984, 0.0000301, 0.5, 12.345].map((v) => m.fmtSmallUsd(v, 3)), ["$0.000984", "$0.0000301", "$0.500", "$12.35"]);
});

test("partWidths keeps a sliver for a tiny part and nothing for an empty one", () => {
  const parts = [
    { key: "a", label: "a", usd: 0.01, fill: "rented" },
    { key: "b", label: "b", usd: 0, fill: "owned" },
    { key: "c", label: "c", usd: 50, fill: "owned" },
  ];
  assert.deepEqual(m.partWidths(parts, 100), [0.6, 0, 50]);
  assert.deepEqual(m.partWidths(parts, 0), [0, 0, 0]);
  // The three bills share one scale, so the GPU-hour bill fills it in the reference run.
  const rows = m.barRows(m.billOf(FIXTURE_COST_REPORT), "all-Ultra");
  const max = Math.max(...rows.map((r) => r.total));
  assert.equal(max, 380.34);
  const sum = (r) => m.partWidths(r.parts, max).reduce((a, w) => a + w, 0);
  assert.ok(Math.abs(sum(rows[2]) - 100) < 1);
  assert.ok(sum(rows[1]) < 3);
  assert.deepEqual(rows[2].parts.map((p) => p.key), ["teacher", "endpoint", "training"]); // no hosted tokens: left out
});

test("the meter puts this traffic, break-even and the crossover on one log scale", () => {
  const { rows, scale } = m.meter(m.billOf(FIXTURE_COST_REPORT));
  assert.deepEqual(rows.map((r) => [r.key, r.value, r.mark]), [
    ["observed", 66.03, "dot"],
    ["break_even", 1575, "tick"],
    ["crossover", 51528, "ring"],
  ]);
  assert.equal(scale.log, true);
  assert.ok(scale.at(66.03) < scale.at(1575) && scale.at(1575) < scale.at(51528));
  assert.match(m.meterSummary(rows, scale.log), /^Break-even meter on a log scale\. This traffic: .*: 66 an hour; .*: 1,575 an hour; .*: 51,528 an hour\.$/);
  const none = m.meter(m.billOf(MINIMAL));
  assert.equal(none.scale, null);
  assert.deepEqual(none.rows.map((r) => r.key), ["observed", "break_even"]); // no crossover row without one
});

test("outcomes lists the four recommendations and marks the report's", () => {
  const o = m.outcomes("hosted_fallback");
  assert.deepEqual(o.map((x) => x.key), ["self_host", "hosted_fallback", "scale_to_zero", "insufficient_data"]);
  assert.deepEqual(o.filter((x) => x.current).map((x) => x.key), ["hosted_fallback"]);
  assert.ok(o.every((x) => x.label && x.when.endsWith(".")));
  assert.equal(m.outcomes("something_new").filter((x) => x.current).length, 0);
});

test("rowText reads a bill and its parts for screen readers", () => {
  const rows = m.barRows(m.billOf(FIXTURE_COST_REPORT), "all-Ultra");
  assert.equal(m.rowText(rows[0]), "$21.86");
  assert.equal(m.rowText(rows[2]), "$380.34 (teacher calls $6.26, endpoint hours $372.00, training $2.08)");
});

test("trust chapter: the redaction list reads as English and event text is clipped", () => {
  assert.equal(t.listWords(["EMAIL"]), "emails");
  assert.equal(t.listWords(["EMAIL", "PHONE"]), "emails and phone numbers");
  assert.equal(t.listWords(["EMAIL", "IP", "NEWLABEL"]), "emails, IPv4 addresses and newlabel");
  assert.equal(t.listWords([]), "");
  assert.ok(t.TRUST_REDACTS.every((l) => t.REDACT_WORDS[l]), "every redaction label has words");
  assert.equal(t.clip("  v3  audited\nprecision  "), "v3 audited precision");
  const long = "x".repeat(200);
  assert.equal(t.clip(long, 20), "x".repeat(19) + "…");
  assert.equal(t.clip(long, 20).length, 20);
  assert.match(t.BOM_VERIFY, /^python -m ratchet\.bom verify bom\.json --public-key /);
});

test("the break-even formula divides the endpoint rate the report recorded, not the configured price", () => {
  // An interval recorded at $3.10 an hour while the settings say $1.55: the report's break-even divides $3.10.
  const recorded = { ...FIXTURE_COST_REPORT, endpoint_usd_per_hour: 3.1, break_even_student_requests_per_gpu_hour: 3.1 / 0.000984 };
  const b = m.billOf(recorded);
  assert.equal(b.endpointPerHour, 3.1);
  assert.ok(Math.abs(b.endpointPerHour / b.avoided - b.breakEven) / b.breakEven < 0.001);
  assert.equal(m.billOf(FIXTURE_COST_REPORT).idleBuckets, "on days");
  assert.equal(m.billOf({ ...FIXTURE_COST_REPORT, idle_granularity: "hour" }).idleBuckets, "in hours");
});
