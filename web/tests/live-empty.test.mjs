// The dashboard and story models against a recorded, empty live gateway (day 0, no requests, no versions): nothing breaks, nothing is NaN.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { register } from "node:module";

register("./ts-hooks.mjs", import.meta.url);
const { isEmptyReport, fmtMoney, fmtSignedPct } = await import("../src/pages/dashboard/economics-model.ts");
const { isCostReport, tellsTheStory } = await import("../src/pages/landing/bill-model.ts");
const { breakerViews, fallbackTotals, latestStandIns, resilienceEvents } = await import("../src/pages/dashboard/resilience-model.ts");
const { liveVersion, inFlight, cumulativeSaved, armedFault } = await import("../src/pages/dashboard/model.ts");
const { gatewayStatus } = await import("../src/lib/real-model.ts");
const { servingLine, hostedServing } = await import("../src/lib/who-model.ts");
const { lastCall } = await import("../src/lib/surface-model.ts");
const { routeTotals } = await import("../src/pages/judges/judges-model.ts");

const rec = (n) => JSON.parse(readFileSync(new URL(`./fixtures/live-empty/${n}.json`, import.meta.url), "utf8"));
const stats = rec("stats");
const cost = rec("cost");

test("the recorded gateway is live and empty", () => {
  assert.equal(stats.mode, "live");
  assert.equal(stats.totals.requests, 0);
  assert.equal(stats.days.length, 0);
  assert.equal(stats.versions.length, 0);
  assert.equal(rec("kpis").simulated, false);
});

test("mode drives the badges: live is measured, mock has no badge", () => {
  assert.equal(gatewayStatus(stats.mode), "measured");
  assert.equal(gatewayStatus("mock"), null);
  assert.equal(gatewayStatus(undefined), null);
});

test("an empty live gateway has no upstream calls and no student serving", () => {
  assert.equal(lastCall(stats.upstream, "token_factory"), null);
  assert.equal(lastCall(stats.upstream, "tavily"), null);
  assert.equal(hostedServing(stats.student_serving), false);
  assert.ok(servingLine(stats.student_serving) === null || typeof servingLine(stats.student_serving).text === "string");
});

test("empty cost report: the economics panel shows its empty state; the story falls back to the labelled reference", () => {
  assert.equal(isEmptyReport(cost), true);
  assert.equal(tellsTheStory(cost), false);
  assert.equal(isCostReport(cost), true);
  for (const s of [fmtMoney(cost.headline.total_usd), fmtSignedPct(cost.headline.savings_pct)]) assert.ok(!/NaN|undefined/.test(s), s);
});

test("the dashboard's pure helpers take empty arrays", () => {
  assert.equal(liveVersion(stats.versions), undefined);
  assert.deepEqual(inFlight(stats.versions), []);
  assert.deepEqual(cumulativeSaved(stats.days), []);
  assert.equal(armedFault(stats), null);
  assert.ok(breakerViews(stats.events).every((b) => b.state === "closed" || b.state == null || typeof b.state === "string"));
  assert.deepEqual(resilienceEvents(stats.events), []);
  assert.equal(latestStandIns(stats.days), null);
  assert.equal(fallbackTotals(stats.tasks).degraded ?? 0, 0);
  assert.deepEqual(routeTotals(stats.tasks), []);
});
