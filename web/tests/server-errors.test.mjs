// The live totals' error tile: failed model calls only (`server_errors`), never the old `errors` total that counted refused keys.
import test from "node:test";
import assert from "node:assert/strict";
import { register } from "node:module";

register("./ts-hooks.mjs", import.meta.url);
const m = await import("../src/lib/measured-model.ts");

const persistent = (over = {}) => ({
  recorded: true,
  since: "2026-10-29",
  updated: "2026-10-29T10:00:00.000Z",
  instances: 2,
  totals: { requests: 12, requests_teacher: 8, requests_split: 2, requests_student: 2, student_tokens_in: 100, student_tokens_out: 50, cost_usd: 0.0421, errors: 1, scout_runs: 3, ...over },
});
const item = (p, label) => m.liveLine(p).items.find((i) => i.label === label);

test("server errors: none is said in words, a count is shown, the old errors total never is", () => {
  assert.equal(m.liveLine(persistent({ errors: 28, server_errors: 0 })).items.some((i) => i.label === "Errors"), false);
  assert.deepEqual(item(persistent({ errors: 28, server_errors: 0 }), "Server errors"), { label: "Server errors", value: "none", hint: "no failed model calls" });
  assert.equal(item(persistent({ server_errors: 3 }), "Server errors").value, "3");
});

test("a Worker that has not started counting server errors shows no tile, not an invented zero", () => {
  assert.equal(item(persistent(), "Server errors"), undefined);
  assert.equal(m.parsePersistent(persistent({ server_errors: 2 })).totals.server_errors, 2);
  assert.equal(m.parsePersistent(persistent({ server_errors: -1 })), null);
});

test("zero counts in the live line say why instead of a bare 0", () => {
  const items = m.liveLine(persistent({ requests_split: 0, requests_student: 0, scout_runs: 0 })).items;
  for (const i of items) assert.notEqual(i.value, "0", i.label);
  assert.equal(items.find((i) => i.label === "Student").value, "none");
});
