// Round 3's page model (src/lib/toolcall-model.ts): no data file is needed, the tests build their own. Run with `npm test`.
import assert from "node:assert/strict";
import { register } from "node:module";
import { test } from "node:test";

register("./ts-hooks.mjs", import.meta.url);
const m = await import("../src/lib/toolcall-model.ts");

const sc = (agree, n, lo, hi) => ({ agree, n, rate: Math.round((agree / n) * 10000) / 10000, lo, hi });
const exam = (adapter, base) => ({
  label: "x",
  rows: 100,
  scores: { base, ultra: sc(90, 100, 0.82, 0.94), adapter },
  verdicts: { adapter_vs_base: "about_same", adapter_vs_ultra: "below" },
  sentence: "s",
  paired: { fixed: 1, broke: 1, p: 1 },
});
const file = (variant, a, b) =>
  JSON.stringify({ variant, variant_reason: "", date: "", caveat: "", setting: "", exams: { hard: exam(a, b), bfcl: exam(a, b) } });

test("no file, no block", () => {
  assert.equal(m.parseToolcall(undefined), null);
  assert.equal(m.blockPlace(null), "none");
});

test("ranges that do not overlap put tool calling first, overlapping ranges keep text-to-SQL first", () => {
  const far = m.parseToolcall(file("B", sc(70, 100, 0.6, 0.78), sc(30, 100, 0.22, 0.4)));
  assert.equal(m.blockPlace(far), "top");
  const near = m.parseToolcall(file("A", sc(40, 100, 0.31, 0.5), sc(30, 100, 0.22, 0.4)));
  assert.equal(m.blockPlace(near), "bottom");
});

test("a file whose variant or counts do not follow is refused", () => {
  assert.throws(() => m.parseToolcall(file("B", sc(40, 100, 0.31, 0.5), sc(30, 100, 0.22, 0.4))), /variant/);
  assert.throws(() => m.parseToolcall(file("A", { ...sc(40, 100, 0.31, 0.5), rate: 0.9 }, sc(30, 100, 0.22, 0.4))), /inconsistent/);
});
