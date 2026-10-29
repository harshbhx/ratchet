// The judges pack (src/pages/judges/pack-model.ts, src/data/pack.json): the parser, the link kinds, and the numbers on its cards
// against the measured snapshot they are read from. Run with `npm test`.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { register } from "node:module";
import { test } from "node:test";

register("./ts-hooks.mjs", import.meta.url);
const m = await import("../src/pages/judges/pack-model.ts");
const text = readFileSync(new URL("../src/data/pack.json", import.meta.url), "utf8");
const measured = JSON.parse(readFileSync(new URL("../src/data/measured.json", import.meta.url), "utf8"));
const pct = (r) => `${(r * 100).toFixed(1)}%`;

test("pack.json parses whole and a broken one is refused", () => {
  const pack = m.parsePack(text);
  assert.ok(pack);
  assert.equal(pack.proof.length, 8);
  assert.equal(m.parsePack("{"), null);
  assert.equal(m.parsePack(JSON.stringify({ ...pack, proof: [] })), null);
});

test("the headline is the approved sentence", () => {
  const pack = m.parsePack(text);
  assert.equal(
    pack.headline,
    "Built on NVIDIA Nemotron and Nebius: every model call on the live site runs on Nebius Token Factory, the adapter was retrained on Nebius AI Cloud, and the student was served from a Nebius Serverless Endpoint in a measured test.",
  );
});

test("link kinds: this site, this page, elsewhere", () => {
  assert.equal(m.hrefKind("/live"), "page");
  assert.equal(m.hrefKind("#proof"), "anchor");
  assert.equal(m.hrefKind("https://huggingface.co/collections/harshbhx/ratchet"), "external");
});

test("every link is a plain https address, a page of this site or an anchor", () => {
  const pack = m.parsePack(text);
  const links = m.allLinks(pack);
  assert.ok(links.length > 40);
  for (const l of links) assert.match(l.href, /^(https:\/\/|\/|#)/, l.href);
});

test("the numbers on the cards are the measured snapshot's", () => {
  const pack = m.parsePack(text);
  const card = (id) => pack.proof.find((c) => c.id === id);
  const score = (exam, model) => measured.exams.exams.find((e) => e.id === exam).scores.find((s) => s.model === model).rate;
  assert.equal(card("text2sql").number, `${pct(score("exam1", "r2"))} / ${pct(score("exam2", "r2"))}`);
  for (const [exam, model] of [
    ["exam1", "base"],
    ["exam2", "base"],
    ["exam1", "teacher"],
    ["exam2", "teacher"],
  ])
    assert.ok(card("text2sql").note.includes(pct(score(exam, model))), `${exam} ${model}`);
  const tc = (exam, model) => measured.toolcall.exams.find((e) => e.id === exam).scores.find((s) => s.model === model).rate;
  assert.equal(card("toolcall").number, pct(tc("hard", "adapter")));
  assert.ok(card("toolcall").note.includes(pct(tc("hard", "ultra"))) && card("toolcall").note.includes(pct(tc("bfcl", "adapter"))));
  const r = measured.retrain;
  assert.equal(card("retrain").number, `${pct(r.exams[0].nebius.rate)} / ${pct(r.exams[1].nebius.rate)}`);
  assert.ok(card("retrain").note.includes(`$${r.cost_usd.toFixed(2)}`));
  assert.ok(card("endpoint").note.includes(`${(measured.endpoint.restart_s / 60).toFixed(1)} min`));
  const ns = measured.nightschool.numbers;
  assert.equal(card("nightschool").number, `${ns.precision} vs ${ns.target}`);
});
