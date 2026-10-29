// The "This gateway, right now" strip (src/lib/live-strip-model.ts): with an empty ledger it never shows a bare zero or a dash while
// the Worker's totals or the measured snapshot have something real and dated to say. Run with `npm test`.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { register } from "node:module";
import { test } from "node:test";

register("./ts-hooks.mjs", import.meta.url);
const m = await import("../src/lib/live-strip-model.ts");
const mm = await import("../src/lib/measured-model.ts");
const measured = mm.parseMeasured(readFileSync(new URL("../src/data/measured.json", import.meta.url), "utf8"));
const none = { live: null, flight: null };

const persistent = (requests, scout_runs = 0) => ({
  recorded: true,
  since: "2026-10-29",
  updated: "2026-10-29",
  instances: 3,
  totals: { requests, requests_teacher: requests, requests_split: 0, requests_student: 0, student_tokens_in: 0, student_tokens_out: 0, cost_usd: 0.0123, errors: 0, scout_runs },
});

test("an empty ledger with the snapshot shows dated values, never a bare zero or a dash", () => {
  const items = m.emptyItems({ measured, persistent: null, release: none });
  assert.ok(items && items.length >= 3);
  for (const it of items) {
    if (it.id === "version") continue; // "none yet" is true and is kept
    assert.equal(m.isBare(it.value), false, `${it.label}: ${it.value}`);
    assert.ok(it.from && it.from.date, `${it.label} carries its date`);
    assert.ok(it.from.source || it.from.tag === "Live", `${it.label} carries its evidence`);
  }
  for (const it of items) assert.equal(m.isBare(it.sub), false, it.sub);
});

test("the labels and values are the snapshot's own", () => {
  const items = m.emptyItems({ measured, persistent: null, release: none });
  const by = Object.fromEntries(items.map((i) => [i.id, i]));
  assert.equal(by.smoke.value, `${measured.smoke.requests} requests`);
  assert.ok(by.smoke.sub.includes(mm.fmtUsd(measured.smoke.total_usd)));
  assert.match(by["scout-cost"].value, /^\$0\.02\d+ to \$0\.02\d+$/);
  assert.equal(by.endpoint.value, "1.6 s");
  assert.equal(by.endpoint.from.source, measured.endpoint.source);
  assert.equal(by.version.value, "none yet");
  assert.equal(by.version.sub, "Night School's 28 Oct candidate was rejected by the gate (81.8% vs 95%)");
  assert.equal(by.version.from.source, "evidence/nightschool-nebius");
  assert.equal(m.provenanceLabel(by.endpoint.from), "Measured · 27 to 28 Oct 2026");
  assert.equal(m.provenanceLabel(by["scout-cost"].from), "Recorded run · 23 to 24 Oct 2026");
  assert.equal(items.some((i) => /savings|saved vs/i.test(i.label)), false, "no savings figure is made up");
});

test("the Worker's totals come first when they hold traffic, and a store with nothing counted does not", () => {
  const live = m.emptyItems({ measured, persistent: persistent(57, 2), release: none });
  assert.equal(live[0].id, "live-since");
  assert.equal(live[0].value, "57");
  assert.equal(live[0].label, "Requests since 29 Oct 2026");
  assert.equal(m.provenanceLabel(live[0].from), "Live since 29 Oct 2026");
  const thin = m.emptyItems({ measured, persistent: persistent(0, 0), release: none });
  assert.equal(thin[0].id, "scout-cost");
});

test("a version in flight or live keeps its own words", () => {
  const items = m.emptyItems({ measured, persistent: null, release: { live: "v2", flight: "v3 in canary at 10%" } });
  const v = items.find((i) => i.id === "version");
  assert.equal(v.value, "v2");
  assert.equal(v.sub, "v3 in canary at 10%");
});

test("with no snapshot and no totals there is nothing to show but the ledger", () => {
  assert.equal(m.emptyItems({ measured: null, persistent: null, release: none }), null);
});

test("isBare catches what it should", () => {
  for (const v of ["0", "$0", "0%", "–", "-", "", "none", "$0.00"]) assert.equal(m.isBare(v), true, v);
  for (const v of ["57", "1.6 s", "$0.0133", "20 requests", "v2"]) assert.equal(m.isBare(v), false, v);
});
