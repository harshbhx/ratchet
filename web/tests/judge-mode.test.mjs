// Judge mode on /judges (src/pages/judges/judge-mode-model.ts, judge-mode.tsx). Run with `npm test`.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const m = await import("../src/pages/judges/judge-mode-model.ts");

test("the panel is shown only on a live gateway with a key; without one, the invitation line", () => {
  assert.equal(m.panelState("live", "judge-key-aaaaaaaa"), "panel");
  assert.equal(m.panelState("live", ""), "invite");
  assert.equal(m.panelState("live", "  "), "invite");
  assert.equal(m.panelState("mock", "judge-key-aaaaaaaa"), "none");
  assert.equal(m.panelState(null, ""), "none");
  assert.equal(m.NO_KEY_LINE, "Judges: paste the key from your dossier to switch on Judge mode.");
  assert.equal(m.MODE_BADGE, "Judge mode · key active");
  assert.equal(m.DOSSIER_LINE, "Your key and the Nebius account details are in your judges' dossier.");
});

test("the budget line is the gateway's count when it reported one, else the key's limits as text", () => {
  assert.equal(m.budgetLine(287), "287 left today");
  assert.equal(m.budgetLine(0), "0 left today");
  assert.equal(m.budgetLine(null), "Limits for a judges' key: 300 requests a day, $1 a day, 30 a minute.");
});

test("a result shows the answer, who answered and what it cost", () => {
  const r = m.parseJudgeResult({
    model: "nemotron-ultra",
    choices: [{ message: { content: " SELECT 1; " } }],
    ratchet: { served_by: "teacher", route: "teacher", cost_usd: 0.000314 },
  });
  assert.deepEqual(r, { text: "SELECT 1;", servedBy: "teacher", model: "nemotron-ultra", route: "teacher", costUsd: 0.000314 });
  assert.equal(m.costLine(0.000314), "cost $0.00031");
  assert.equal(m.costLine(0.0125), "cost $0.013");
  assert.equal(m.costLine(null), "cost not reported");
  const split = m.parseJudgeResult({ choices: [{ message: { content: "x" } }], ratchet: { served_by: "student", route: "demo_split" } });
  assert.equal(split.servedBy, "student");
  assert.equal(split.route, "demo_split");
  assert.equal(split.costUsd, null);
});

test("a tool call reads as name(arguments); an empty reply is no result", () => {
  const r = m.parseJudgeResult({ choices: [{ message: { content: null, tool_calls: [{ function: { name: "get_weather", arguments: '{"city":"Pune"}' } }] } }], ratchet: { served_by: "teacher", cost_usd: 0.0002 } });
  assert.equal(r.text, 'get_weather({"city":"Pune"})');
  assert.equal(m.parseJudgeResult({ choices: [{ message: { content: "" } }] }), null);
  assert.equal(m.parseJudgeResult(null), null);
});

test("the tests are real, short, unstored requests; the tool test offers a tool", () => {
  assert.deepEqual(m.TESTS.map((t) => t.id), ["sql", "tools"]);
  for (const t of m.TESTS) {
    assert.equal(t.body.store, false);
    assert.ok(t.body.max_tokens <= 160);
  }
  assert.equal(m.TESTS[1].body.tools[0].function.name, "get_weather");
  assert.equal(m.TESTS[0].body.tools, undefined);
});

test("the panel sends the key as a header only, and the key is never in an address or a log", () => {
  const ui = readFileSync(new URL("../src/pages/judges/judge-mode.tsx", import.meta.url), "utf8");
  assert.match(ui, /\.\.\.keyHeaders\(key\)/);
  assert.match(ui, /to="\/scout"/, "Scout takes no client key, so it links to /scout");
  for (const f of ["../src/pages/judges/judge-mode.tsx", "../src/pages/judges/judge-mode-model.ts"]) {
    const src = readFileSync(new URL(f, import.meta.url), "utf8");
    assert.doesNotMatch(src, /console\.|localStorage|sessionStorage|URLSearchParams|location\.(search|href|hash)/, f);
  }
  const page = readFileSync(new URL("../src/pages/judges/index.tsx", import.meta.url), "utf8");
  assert.match(page, /<JudgeMode mode=\{stats\?\.mode \?\? null\} \/>/);
});
