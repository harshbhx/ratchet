// "Why it is different" (src/pages/landing/why-model.ts) names files and invariants. This test holds it to the repo:
// every file exists, and every cited invariant in CLAUDE.md begins with the words the card attributes to it.
// Run with `npm test`.
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { register } from "node:module";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

register("./ts-hooks.mjs", import.meta.url);
const m = await import("../src/pages/landing/why-model.ts");
const REPO = fileURLToPath(new URL("../../", import.meta.url));

test("every file a card names exists in the repo", () => {
  const missing = m.WHY.flatMap((w) => w.files.filter((f) => !existsSync(REPO + f)).map((f) => `${w.id}: ${f}`));
  assert.deepEqual(missing, []);
  assert.ok(m.WHY.every((w) => w.files.length > 0 && w.title && w.body));
});

test("every invariant a card cites says what the card says", () => {
  const claude = readFileSync(REPO + "CLAUDE.md", "utf-8");
  const invariants = new Map();
  for (const line of claude.split("\n")) {
    const hit = /^(\d+)\. \*\*(.+?)\*\*/.exec(line);
    if (hit) invariants.set(Number(hit[1]), hit[2]);
  }
  assert.ok(invariants.size >= 20, "CLAUDE.md lists the invariants");
  for (const w of m.WHY) {
    if (!w.invariant) continue;
    const text = invariants.get(w.invariant.n);
    assert.ok(text, `${w.id}: invariant ${w.invariant.n} exists`);
    assert.ok(text.includes(w.invariant.says), `${w.id}: invariant ${w.invariant.n} says "${w.invariant.says}", not "${text}"`);
  }
});

test("the ids are distinct and the grid has two wide cards", () => {
  assert.equal(new Set(m.WHY.map((w) => w.id)).size, m.WHY.length);
  assert.equal(m.WHY.filter((w) => w.wide).length, 2);
});
