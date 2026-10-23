// The guided tour's geometry and step order (src/components/tour/tour-model.ts), and the judges console's pure helpers
// (src/pages/judges/judges-model.ts). Run with `npm test`.
import assert from "node:assert/strict";
import { register } from "node:module";
import { test } from "node:test";

register("./ts-hooks.mjs", import.meta.url);
const t = await import("../src/components/tour/tour-model.ts");
const j = await import("../src/pages/judges/judges-model.ts");
const ch = await import("../src/pages/judges/chapters.ts");

const DESK = { width: 1440, height: 900 };
const PHONE = { width: 390, height: 844 };
const POP = { width: 380, height: 220 };

test("the spotlight is the target plus padding, cut to the screen below the header", () => {
  assert.deepEqual(t.spotlight({ top: 200, left: 100, width: 300, height: 100 }, DESK, 8, 64), { top: 192, left: 92, width: 316, height: 116 });
  const tall = t.spotlight({ top: 40, left: 0, width: 1440, height: 3000 }, DESK, 8, 64, 0);
  assert.equal(tall.top, 68);
  assert.equal(tall.top + tall.height, 896);
  assert.equal(tall.left, 4);
  assert.equal(t.spotlight({ top: 2000, left: 0, width: 100, height: 100 }, DESK), null);
});

test("the popover docks on a phone and never leaves the screen on a desktop", () => {
  assert.deepEqual(t.placePopover({ top: 100, left: 10, width: 300, height: 100 }, POP, PHONE), { mode: "dock" });
  const below = t.placePopover({ top: 100, left: 500, width: 300, height: 100 }, POP, DESK);
  assert.equal(below.side, "below");
  assert.equal(below.top, 100 + 100 + t.GAP);
  const above = t.placePopover({ top: 600, left: 500, width: 300, height: 250 }, POP, DESK);
  assert.equal(above.side, "above");
  const over = t.placePopover({ top: 70, left: 4, width: 1432, height: 826 }, POP, DESK);
  assert.equal(over.side, "over");
  for (const p of [below, above, over]) {
    assert.ok(p.top >= t.GAP && p.top + POP.height <= DESK.height - t.GAP + 0.001, JSON.stringify(p));
    assert.ok(p.left >= t.GAP && p.left + POP.width <= DESK.width - t.GAP + 0.001, JSON.stringify(p));
  }
  const centered = t.placePopover(null, POP, DESK);
  assert.equal(centered.side, "over");
});

test("a target is scrolled to the middle of the free space, a tall one to just under the header", () => {
  const y = t.scrollTarget({ top: 1000, left: 0, width: 100, height: 200 }, 500, DESK, 64, 0);
  assert.equal(y, Math.round(1500 - 64 - (900 - 64 - 200) / 2));
  const tall = t.scrollTarget({ top: 1000, left: 0, width: 100, height: 2000 }, 500, DESK, 64, 0);
  assert.equal(tall, 1500 - 64 - t.SPOT_PAD - 8);
  assert.equal(t.scrollTarget({ top: -500, left: 0, width: 10, height: 10 }, 0, DESK, 64, 0), 0);
  // With a floating popover that fits under it, the target starts under the header so the popover goes below.
  const withPop = t.scrollTarget({ top: 1000, left: 0, width: 100, height: 300 }, 500, DESK, 104, 0, 280);
  assert.equal(withPop, 1500 - 104 - t.SPOT_PAD - 8);
  const after = { top: 104 + t.SPOT_PAD + 8, left: 500, width: 300, height: 300 };
  assert.equal(t.placePopover(t.spotlight(after, DESK, t.SPOT_PAD, 104), { width: 380, height: 280 }, DESK, false, 104).side, "below");
});

test("a popover never goes above the sticky header", () => {
  const p = t.placePopover({ top: 500, left: 0, width: 1440, height: 390 }, POP, DESK, false, 104);
  assert.ok(p.top >= 104 + t.GAP, JSON.stringify(p));
});

test("steps without a target are skipped both ways; the end is null", () => {
  const has = (i) => i !== 1 && i !== 3;
  assert.equal(t.nextStep(5, 0, 1, has), 2);
  assert.equal(t.nextStep(5, 2, -1, has), 0);
  assert.equal(t.nextStep(5, 4, 1, has), null);
  assert.equal(t.nextStep(5, -1, 1, has), 0);
  assert.equal(t.announce(1, 7, "The ledger"), "Step 2 of 7: The ledger");
});

test("?tour=1 opens the tour; other parameters do not", () => {
  assert.equal(t.wantsTour("?tour=1"), true);
  assert.equal(t.wantsTour("?a=b&tour=1"), true);
  assert.equal(t.wantsTour("?tour=0"), false);
  assert.equal(t.wantsTour("?detour=1"), false);
  assert.equal(t.wantsTour(""), false);
});

test("every judges chapter has a tour stop, a unique id and a group", () => {
  const ids = ch.JUDGE_CHAPTERS.map((c) => c.id);
  assert.equal(new Set(ids).size, ids.length);
  for (const c of ch.JUDGE_CHAPTERS) {
    assert.ok(c.tourTitle && c.tour.length > 20 && ch.GROUP_LABEL[c.group], c.id);
    assert.ok(c.tour.split(/(?<=\.)\s/).length <= 2, `${c.id}: one or two sentences`);
  }
  assert.ok(ch.JUDGE_CHAPTERS.some((c) => c.pinned && c.id === "real"));
});

test("a failed read is said in words, never as a number", () => {
  assert.equal(j.unavailable("/ratchet/drift", 404, "").kind, "missing");
  assert.match(j.unavailable("/ratchet/drift", 404, "").text, /Not available in this build/);
  assert.equal(j.unavailable("/ratchet/stats", 401, "invalid or missing dashboard key").kind, "locked");
  assert.equal(j.unavailable("/ratchet/demo/day", 403, "demo controls are only available in mock mode").kind, "mock-only");
  assert.equal(j.unavailable("/ratchet/stats", 0, "").kind, "offline");
  assert.match(j.unavailable("/ratchet/receipts/3", 404, "no requests in scope").text, /^Nothing here yet/);
  assert.equal(j.errorMessage({ detail: "x" }), "x");
  assert.equal(j.errorMessage({ error: { message: "y" } }), "y");
  assert.equal(j.errorMessage({ detail: [{ msg: "z" }] }), "");
});

test("routes, receipts and helpers", () => {
  assert.equal(j.routeWords("student"), "student answered");
  assert.equal(j.routeWords("rule:2"), "escalated by rule 2");
  assert.equal(j.routeWords("ineligible:too_long"), "teacher: not eligible (too_long)");
  assert.equal(j.routeWords("brand_new"), "brand_new");
  assert.equal(j.routeWords(null), "not recorded");
  assert.deepEqual(
    j.routeTotals([{ routes: { student: 3, confidence: 1 } }, { routes: { student: 2, cold_start: 0 } }, {}]),
    [
      { route: "student", n: 5 },
      { route: "confidence", n: 1 },
    ],
  );
  assert.equal(j.latestGlobalPeriod({ receipts: [{ period: 1, global: true }, { period: 3, global: true }, { period: 4, global: false }] }), 3);
  assert.equal(j.latestGlobalPeriod({ receipts: [] }), null);
  assert.equal(j.nusdToUsd(1_500_000_000), 1.5);
  assert.equal(j.nusdToUsd(null), null);
  assert.equal(j.shortHash("0123456789abcdef0123456789abcdef"), "0123456789…abcdef");
  assert.equal(j.parseLeaves('{"id":1}\nnot json\n{"id":2}\n').length, 2);
  assert.equal(j.alarmProgress(1, 100), 0.5);
  assert.equal(j.alarmProgress(-1, 100), 0);
  assert.equal(j.alarmProgress(5, 100), 1);
  assert.equal(j.stageOf("canary", 10), "canary10");
  assert.equal(j.stageOf("canary", 50), "canary50");
  assert.equal(j.stageOf("rolled_back", 0), null);
  const vs = [
    { id: 1, state: "rejected", bom: "unsigned" },
    { id: 2, state: "live", bom: "unsigned" },
    { id: 3, state: "canary", bom: "unsigned" },
  ];
  assert.equal(j.focusVersion(vs).id, 2);
  assert.equal(j.focusVersion([{ id: 1, state: "rejected", bom: null }]).id, 1);
  assert.equal(j.focusVersion([]), null);
  assert.equal(j.baseUrl("https://demo.example.com/"), "https://demo.example.com/v1");
  assert.equal(j.baseUrl("file://"), "http://localhost:8000/v1");
});
