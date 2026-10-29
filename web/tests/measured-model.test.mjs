// The measured snapshot and the live totals the pages show (src/lib/measured-model.ts): labels, tiles, the Night School gate, no bare zeros.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { register } from "node:module";

register("./ts-hooks.mjs", import.meta.url);
const m = await import("../src/lib/measured-model.ts");

const text = readFileSync(new URL("../src/data/measured.json", import.meta.url), "utf8");
const data = m.parseMeasured(text);
const persistent = (over = {}) => ({
  recorded: true,
  since: "2026-10-29",
  updated: "2026-10-29T10:00:00.000Z",
  instances: 2,
  totals: { requests: 12, requests_teacher: 8, requests_split: 2, requests_student: 2, student_tokens_in: 100, student_tokens_out: 50, cost_usd: 0.0421, errors: 1, scout_runs: 3, ...over },
});

test("dates read as the site writes them, and a range shares its month", () => {
  assert.equal(m.fmtDay("2026-10-27"), "27 Oct 2026");
  assert.equal(m.fmtRange("2026-10-27", "2026-10-28"), "27 to 28 Oct 2026");
  assert.equal(m.fmtRange("2026-10-31", "2026-11-01"), "31 Oct 2026 to 1 Nov 2026");
  assert.equal(m.measuredLabel("2026-10-27"), "Measured · 27 Oct 2026");
  assert.equal(m.sourceHref("evidence/scout"), "https://github.com/harshbhx/ratchet/tree/main/evidence/scout");
});

test("the snapshot parses and every tile has a date, a source folder and no placeholder", () => {
  assert.ok(data);
  const tiles = m.measuredTiles(data);
  assert.ok(tiles.length >= 8);
  for (const t of tiles) {
    assert.match(t.date, /^\d{4}-\d{2}-\d{2}$/, t.id);
    assert.ok(t.source.startsWith("evidence/"), t.id);
    assert.ok(t.headline.length > 20, t.id);
    assert.ok(["Measured", "Recorded run"].includes(t.tag), t.id);
    const shown = JSON.stringify(t);
    assert.ok(!/NaN|undefined|null|Infinity/.test(shown), `${t.id} prints a placeholder`);
    for (const b of t.bars ?? []) assert.ok(b.value >= 0 && b.value <= 1, `${t.id} bar ${b.label}`);
  }
  for (const id of ["exams", "toolcall", "retrain", "endpoint", "scout", "smoke", "labelling", "hosted-nano"]) assert.ok(tiles.some((t) => t.id === id), id);
});

test("the headline numbers are the ones in the data", () => {
  const tiles = Object.fromEntries(m.measuredTiles(data).map((t) => [t.id, t]));
  assert.match(tiles.retrain.headline, /40\.7%/);
  assert.match(tiles.retrain.headline, /34\.5%/);
  assert.match(tiles.exams.headline, /39\.0%/);
  assert.equal(tiles.scout.tag, "Recorded run");
  assert.equal(tiles.exams.tag, "Measured");
  assert.match(tiles.endpoint.headline, /7 min 38 s/);
});

test("the Night School tile is hidden until its evidence folder exists, and carries no number of its own", () => {
  assert.equal(m.nightSchoolTile({ ...data, nightschool: { present: false, source: "evidence/nightschool-nebius" } }), null);
  const t = m.nightSchoolTile({ ...data, nightschool: { present: true, source: "evidence/nightschool-nebius", date: "2026-10-28", what: "the folder's own words", files: ["a", "b"] } });
  assert.equal(t.headline, "the folder's own words");
  assert.equal(t.source, "evidence/nightschool-nebius");
  assert.deepEqual(t.facts, [{ label: "Evidence files", value: "2" }]);
  // Without the evidence the wording stays as before; with it the page says the run is recorded and no version served.
  assert.match(m.nightSchoolWording({ ...data, nightschool: { present: false, source: "x" } }).versions, /has not run on live traffic/);
  assert.match(m.nightSchoolWording({ ...data, nightschool: { present: true, source: "x" } }).versions, /recorded below/);
});

test("the live line: Live since the first record, requests, dollars, Scout runs", () => {
  const l = m.liveLine(persistent());
  assert.equal(l.headline, "Live since 29 Oct 2026: 12 requests, $0.0421 Token Factory, 3 Scout runs");
  assert.ok(l.items.some((i) => i.label === "Student tokens" && i.value === "150"));
  assert.equal(l.note, "Kept across 1 restart of the gateway");
  assert.equal(m.liveLine(persistent({ student_tokens_in: 0, student_tokens_out: 0 })).items.some((i) => i.label === "Student tokens"), false);
});

test("nothing recorded, or nothing counted yet, is not live traffic: the snapshot is shown, never a zero", () => {
  assert.equal(m.hasLiveTraffic(null), false);
  assert.equal(m.hasLiveTraffic(m.parsePersistent({ recorded: false, since: null, totals: null })), false);
  assert.equal(m.hasLiveTraffic(persistent({ requests: 0, scout_runs: 0 })), false);
  assert.equal(m.hasLiveTraffic(persistent()), true);
  assert.equal(m.hasLiveTraffic(persistent({ requests: 0, scout_runs: 2 })), true);
});

test("parsePersistent accepts the Worker's answer and rejects anything else", () => {
  assert.ok(m.parsePersistent(persistent()));
  assert.equal(m.parsePersistent({ detail: "Not Found" }), null);
  assert.equal(m.parsePersistent({ ...persistent(), totals: { ...persistent().totals, requests: "5" } }), null);
  assert.equal(m.parsePersistent({ ...persistent(), totals: { ...persistent().totals, cost_usd: -1 } }), null);
  assert.equal(m.parsePersistent("<html>"), null);
});

test("dollars keep four places when small and the wording has no demo or simulation", () => {
  assert.equal(m.fmtUsd(0.0133), "$0.0133");
  assert.equal(m.fmtUsd(2.16), "$2.16");
  assert.equal(m.fmtUsd(0), "$0");
  const all = JSON.stringify(m.measuredTiles(data)) + JSON.stringify(m.nightSchoolWording(data)) + JSON.stringify(m.liveLine(persistent()));
  assert.ok(!/demo|simulat/i.test(all));
});

test("recorded runs to date are sums of the evidence values", () => {
  const r = m.recordedToDate(data);
  const calls = data.scout.runs.reduce((n, x) => n + x.llm_calls, 0);
  assert.ok(r.headline.startsWith(`Recorded runs to date: ${calls} model calls across ${data.scout.runs.length} Scout runs`));
  assert.equal(r.source, "evidence/scout");
  assert.equal(r.date, "2026-10-23");
  assert.equal(r.dateEnd, "2026-10-24");
  assert.equal(m.recordedToDate({ ...data, scout: { ...data.scout, runs: [] } }), null);
  assert.ok(!/demo|simulat|sample/i.test(r.headline));
});
