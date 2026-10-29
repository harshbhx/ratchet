// The Night School card on the story page (src/lib/night-run-model.ts, src/pages/landing/sections/measured-on-nebius.tsx): its numbers are
// the measured snapshot's (which the evidence folder holds), the meter's geometry is right, and the page renders the card exactly once.
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { register } from "node:module";
import { test } from "node:test";

register("./ts-hooks.mjs", import.meta.url);
const m = await import("../src/lib/night-run-model.ts");
const mm = await import("../src/lib/measured-model.ts");
const measured = mm.parseMeasured(readFileSync(new URL("../src/data/measured.json", import.meta.url), "utf8"));

test("the run's numbers are the snapshot's, with the rows stated both ways", () => {
  const r = m.nightRun(measured);
  assert.ok(r);
  const x = measured.nightschool.numbers;
  assert.equal(r.date, "28 Oct 2026");
  assert.equal(r.precisionText, x.precision);
  assert.equal(r.targetText, x.target);
  assert.equal(r.right, "9");
  assert.equal(r.answered, "11");
  assert.deepEqual(r.rows, { imported: "4,322", training: "3,864", golden: "458" });
  assert.deepEqual(
    r.steps.map((s) => s.id),
    ["gateway", "training", "serving", "gate"],
  );
  assert.equal(r.steps[1].detail, "966 steps · loss 0.15 → 0.08");
  assert.equal(r.steps.filter((s) => s.stops).length, 1);
  assert.equal(r.steps[3].stops, true);
});

test("without the evidence, or with a number missing, there is no card", () => {
  assert.equal(m.nightRun(null), null);
  const gone = structuredClone(measured);
  gone.nightschool.present = false;
  assert.equal(m.nightRun(gone), null);
  const thin = structuredClone(measured);
  delete thin.nightschool.numbers.gate_right;
  assert.equal(m.nightRun(thin), null);
  const accepted = structuredClone(measured);
  accepted.nightschool.verdict = "accepted";
  assert.equal(m.nightRun(accepted), null);
});

test("the meter fills to the precision, shades the gap and marks the target", () => {
  const g = m.meter({ precision: 0.818, target: 0.95 });
  assert.equal(g.fill, 81.8);
  assert.equal(g.marker, 95);
  assert.ok(Math.abs(g.gapFrom - 81.8) < 1e-9 && Math.abs(g.gapWidth - 13.2) < 1e-9);
  const over = m.meter({ precision: 0.97, target: 0.95 });
  assert.equal(over.gapWidth, 0);
  assert.equal(m.meter({ precision: 2, target: 0.95 }).fill, 100);
});

test("the story page renders the card exactly once", () => {
  const dir = new URL("../src/pages/landing/", import.meta.url);
  let uses = 0;
  const walk = (d) => {
    for (const e of readdirSync(d, { withFileTypes: true })) {
      if (e.isDirectory()) walk(new URL(`${e.name}/`, d));
      else if (/\.tsx$/.test(e.name) && e.name !== "measured-on-nebius.tsx") uses += (readFileSync(new URL(e.name, d), "utf8").match(/<MeasuredOnNebius\b/g) ?? []).length;
    }
  };
  walk(dir);
  assert.equal(uses, 1);
});
