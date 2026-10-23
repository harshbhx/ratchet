// The exams' numbers as the pages show them (src/lib/exams-model.ts, src/data/exams.json): the parser refuses a file whose rates do not follow
// from its counts, and the helpers print what the counts say. Run with `npm test`.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { register } from "node:module";
import { test } from "node:test";

register("./ts-hooks.mjs", import.meta.url);
const m = await import("../src/lib/exams-model.ts");
const text = readFileSync(new URL("../src/data/exams.json", import.meta.url), "utf8");

test("exams.json parses, with every model scored on both exams", () => {
  const d = m.parseExams(text);
  for (const exam of ["exam1", "exam2"]) {
    for (const id of m.MODEL_IDS) {
      const s = d.scores[exam][id];
      assert.ok(s.n > 0 && s.agree >= 0 && s.agree <= s.n, `${exam} ${id}`);
      assert.ok(s.lo <= s.rate && s.rate <= s.hi, `${exam} ${id} range`);
    }
  }
  assert.equal(d.exams.length, 2);
  assert.equal(d.scores.exam1.r2.n, 172);
  assert.equal(d.scores.exam2.r2.n, 200);
});

test("a rate that its counts do not give is refused", () => {
  const d = JSON.parse(text);
  d.scores.exam2.r2.rate = 0.5;
  assert.throws(() => m.parseExams(JSON.stringify(d)), /inconsistent/);
});

test("the difficulty rows add up to the exam and the changes add up to the adapters", () => {
  const d = m.parseExams(text);
  for (const exam of ["exam1", "exam2"]) {
    const n = m.DIFFICULTIES.reduce((a, k) => a + d.difficulty[exam][k].n, 0);
    assert.equal(n, d.scores[exam].r2.n, `${exam} questions`);
    for (const id of ["base", "teacher", "r1", "r2"]) {
      const total = m.DIFFICULTIES.reduce((a, k) => a + d.difficulty[exam][k][id], 0);
      assert.equal(total, d.scores[exam][id].agree, `${exam} ${id}`);
    }
    const f = d.r1_to_r2[exam];
    assert.equal(f.wrong_to_right + f.right_to_wrong + f.both_right + f.both_wrong, d.scores[exam].r2.n);
    assert.equal(f.both_right + f.wrong_to_right, d.scores[exam].r2.agree);
    assert.equal(f.both_right + f.right_to_wrong, d.scores[exam].r1.agree);
  }
});

test("each exam names its writer and carries a SHA-256 fingerprint", () => {
  const d = m.parseExams(text);
  for (const e of d.exams) {
    assert.ok(e.writer.length > 10 && e.writer_note.length > 20, e.id);
    assert.match(e.fingerprint, /^[0-9a-f]{64}$/, e.id);
  }
  assert.match(d.exams[0].writer, /Ultra/);
  assert.match(d.exams[1].writer, /Claude/);
  assert.match(d.exams[1].writer, /evaluation only/);
});

test("the helpers print what the counts say", () => {
  assert.equal(m.pct(0.33), "33.0%");
  assert.equal(m.pct(0.1279), "12.8%");
  assert.equal(m.scoreText({ agree: 66, n: 200, rate: 0.33, lo: 0.2688, hi: 0.3978 }), "66 of 200 · 33.0%");
  assert.equal(m.rangeText({ agree: 66, n: 200, rate: 0.33, lo: 0.2688, hi: 0.3978 }), "26.9 to 39.8%");
  assert.equal(m.xOf(0.3, 0.6, 100), 50);
  assert.equal(m.xOf(2, 0.6, 100), 100);
  assert.equal(m.xOf(-1, 0.6, 100), 0);
  assert.equal(m.xOf(1, 0, 100), 0);
  const d = m.parseExams(text);
  assert.equal(m.timesBase(d, "exam2").toFixed(1), "2.6");
});
