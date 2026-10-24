// What the dashboard says after a day whose night skipped training (src/pages/dashboard/night-model.ts): it quotes the
// gateway's own numbers, explains once, and then only restates them. Run with `npm test` (Node 22.6 or later).
import assert from "node:assert/strict";
import { register } from "node:module";
import { test } from "node:test";

register("./ts-hooks.mjs", import.meta.url);
const m = await import("../src/pages/dashboard/night-model.ts");

const SKIP = { kind: "skip", detail: "only 12 new examples (< 40); no training tonight" };

test("the numbers come from the skip event, and only from a skip event", () => {
  assert.deepEqual(m.skipInfo([{ kind: "live", detail: "v2 is live" }, SKIP]), { fresh: 12, need: 40 });
  assert.equal(m.skipInfo([{ kind: "skip", detail: "v3 still in canary; no training tonight (saves GPU time)" }]), null);
  assert.equal(m.skipInfo([]), null);
});

test("the first skip explains, with the share, and points to what to do", () => {
  const s = m.skipSentence({ fresh: 12, need: 40 }, 0.96, 1);
  assert.match(s, /^No training tonight: 12 new examples, 40 needed\./);
  assert.match(s, /answered 96% of today's requests itself/);
  assert.match(s, /Run a different workload/);
});

test("a low student share is not blamed on the student", () => {
  assert.doesNotMatch(m.skipSentence({ fresh: 3, need: 40 }, 0.2, 1), /little new for it to learn/);
});

test("a repeat only restates the numbers", () => {
  assert.equal(m.skipSentence({ fresh: 12, need: 40 }, 0.96, 2), "Still no training: 12 of 40 new examples.");
});

test("one example is singular, and an unknown reason keeps the old words", () => {
  assert.match(m.skipSentence({ fresh: 1, need: 40 }, null, 1), /1 new example, 40 needed/);
  assert.equal(m.skipSentence(null, 0.9, 1), "Skipped training: not enough new data.");
});
