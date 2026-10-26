// Who answered a call (src/lib/who-model.ts, FR-7.40): our adapter, the hosted fallback, the teacher or a tool, with the
// exact badge words and tooltips; the copy a run or a page switches to once the hosted fallback answers, and no precision
// figure while it serves. Also the Scout reducer's count of hosted calls (src/pages/scout/state.ts). Run with `npm test`.
import assert from "node:assert/strict";
import { register } from "node:module";
import { test } from "node:test";

register("./ts-hooks.mjs", import.meta.url);
const m = await import("../src/lib/who-model.ts");
const scout = await import("../src/pages/scout/state.ts");

const FALLBACK = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B";
const ULTRA = "nvidia/Nemotron-3-Ultra-550b-a55b";
const HOSTED_TITLE_080 =
  "Untuned Nemotron 3 Nano 30B on Token Factory, held to confidence threshold 0.80. Not covered by our adapter's precision certificate.";
// The label table's full tooltip, once the gateway reports daily endpoint windows (W10).
const WINDOWS = " Our adapter answers during the daily endpoint windows.";

test("the badge words and tooltips are the label table's", () => {
  assert.deepEqual(m.WHO_TEXT, {
    owned: "Nano + your adapter",
    hosted: "Nano 30B · hosted",
    demoSplit: "Nano 30B · demo split",
    rented: "Ultra · Token Factory",
    tavily: "Tavily",
    search: "Search",
  });
  assert.equal(m.TEACHER_TITLE, "Nemotron 3 Ultra on Nebius Token Factory");
  assert.equal(m.ownedTitle(3), "Nemotron 3 Nano 4B with our LoRA adapter v3, built to run on a Nebius Serverless Endpoint");
  assert.equal(m.ownedTitle(null), "Nemotron 3 Nano 4B with our LoRA adapter, built to run on a Nebius Serverless Endpoint");
  assert.equal(m.hostedTitle(0.8), HOSTED_TITLE_080);
  assert.equal(
    m.hostedTitle(0.8, true),
    "Untuned Nemotron 3 Nano 30B on Token Factory, held to confidence threshold 0.80. Not covered by our adapter's precision certificate. " +
      "Our adapter answers during the daily endpoint windows.",
  );
  assert.equal(m.hostedTitle(null), "Untuned Nemotron 3 Nano 30B on Token Factory. Not covered by our adapter's precision certificate.");
  assert.equal(m.NOT_CERTIFIED, "not covered by the certificate");
});

test("our adapter's answer is owned, the fallback's is hosted", () => {
  assert.deepEqual(m.whoLabel({ servedBy: "student", model: "student-v3", fallback: FALLBACK }), {
    kind: "owned",
    text: "Nano + your adapter",
    title: "Nemotron 3 Nano 4B with our LoRA adapter v3, built to run on a Nebius Serverless Endpoint",
  });
  const hosted = m.whoLabel({ servedBy: "student", model: FALLBACK, fallback: FALLBACK, threshold: 0.8 });
  assert.deepEqual(hosted, { kind: "hosted", text: "Nano 30B · hosted", title: HOSTED_TITLE_080 });
  assert.match(hosted.title, /threshold 0\.80/);
  assert.match(hosted.title, /Not covered/);
  // The window sentence only when the gateway reports windows: without them nothing says when our adapter answers.
  assert.doesNotMatch(hosted.title, /window/);
  assert.equal(m.whoLabel({ servedBy: "student", model: FALLBACK, fallback: FALLBACK, threshold: 0.8, windows: true }).title, HOSTED_TITLE_080 + WINDOWS);
  assert.equal(m.whoLabel({ servedBy: "student", model: "student-v3", fallback: FALLBACK, windows: true }).title, m.ownedTitle(3));
  // An upstream that echoes the id in another case or with spaces is still the fallback.
  assert.equal(m.whoLabel({ servedBy: "student", model: ` ${FALLBACK.toLowerCase()} `, fallback: FALLBACK }).kind, "hosted");
  // Without the fallback id (an older gateway), or for any other model, a student answer reads as ours.
  assert.equal(m.whoLabel({ servedBy: "student", model: FALLBACK }).kind, "owned");
  assert.equal(m.whoLabel({ servedBy: "student", model: "", fallback: "" }).kind, "owned");
  assert.equal(m.whoLabel({ servedBy: "student" }).title, m.ownedTitle(null));
  // A teacher answer is never hosted, even if its model were the fallback id.
  assert.equal(m.whoLabel({ servedBy: "teacher", model: FALLBACK, fallback: FALLBACK }).kind, "rented");
});

test("the teacher is Ultra on Token Factory, a tool is a search", () => {
  assert.deepEqual(m.whoLabel({ servedBy: "teacher", model: ULTRA }), {
    kind: "rented",
    text: "Ultra · Token Factory",
    title: "Nemotron 3 Ultra on Nebius Token Factory",
  });
  assert.deepEqual(m.whoLabel({ servedBy: "tool", model: "tavily" }), { kind: "tool", text: "Tavily", title: "tavily" });
  assert.deepEqual(m.whoLabel({ servedBy: "tool", model: "mock-search" }), { kind: "tool", text: "Search", title: "mock-search" });
  assert.equal(m.whoLabel({ servedBy: "tool" }).title, "");
});

test("in mock mode the tooltip says a mock model plays the real one; the words stay", () => {
  const owned = m.whoLabel({ servedBy: "student", model: "student-v2", simulated: true });
  assert.equal(owned.text, "Nano + your adapter");
  assert.equal(owned.title, "Simulation: a mock model plays Nemotron 3 Nano 4B with our LoRA adapter v2, built to run on a Nebius Serverless Endpoint");
  assert.equal(m.whoLabel({ servedBy: "teacher", simulated: true }).title, "Simulation: a mock model plays Nemotron 3 Ultra on Nebius Token Factory");
  assert.equal(m.whoLabel({ servedBy: "tool", model: "tavily", simulated: true }).title, "tavily");
});

test("thresholds, adapter versions and model ids", () => {
  const cases = [
    [0.8, "0.80"],
    [0.42, "0.42"],
    [0.805, "0.805"],
    [0.8137, "0.814"],
    [1, "1.00"],
    [0, "0.00"],
    [null, null],
    [undefined, null],
    [Number.NaN, null],
  ];
  for (const [t, want] of cases) assert.equal(m.fmtThreshold(t), want, `threshold ${t}`);
  assert.equal(m.adapterVersion("student-v3"), 3);
  assert.equal(m.adapterVersion("adapters/student-v12"), 12);
  assert.equal(m.adapterVersion("STUDENT-V4"), 4);
  assert.equal(m.adapterVersion(FALLBACK), null);
  assert.equal(m.adapterVersion("student-v3-merged"), null);
  assert.equal(m.adapterVersion(null), null);
  assert.equal(m.sameModel(FALLBACK, FALLBACK.toUpperCase()), true);
  assert.equal(m.sameModel("", ""), false);
  assert.equal(m.sameModel(null, undefined), false);
});

test("a run that the hosted fallback answered stops saying 'your adapter'", () => {
  const steps = [
    { served_by: "teacher", model: ULTRA },
    { served_by: "student", model: FALLBACK },
    { served_by: "student", model: "student-v3" },
    { served_by: "tool", model: "tavily" },
  ];
  assert.equal(m.hostedCalls(steps, FALLBACK), 1);
  assert.equal(m.hostedCalls(steps, null), 0);
  assert.deepEqual(m.studentCopy(false), {
    label: "Answered by your adapter",
    short: "Your adapter",
    by: "by your adapter",
    byShort: "by your adapter",
    tried: "your adapter",
    column: "Adapter",
    compact: "adapter",
    name: "your adapter",
  });
  const hosted = m.studentCopy(true);
  assert.equal(hosted.label, "Answered by the student");
  assert.equal(hosted.by, "by the hosted Nano (untuned)");
  for (const text of Object.values(hosted)) assert.doesNotMatch(text, /your|yours/i);
});

test("no precision figure while the hosted fallback serves", () => {
  const hosted = { kind: "hosted_fallback", model: FALLBACK, version: 3, threshold: 0.8, certified: false };
  const endpoint = { kind: "endpoint", model: "student-v3", version: 3, threshold: 0.42, certified: true };
  assert.equal(m.precisionShown(hosted), false);
  for (const s of [endpoint, { ...endpoint, kind: "mock" }, { kind: "none", model: null, version: null, threshold: null, certified: false }, null, undefined])
    assert.equal(m.precisionShown(s), true, JSON.stringify(s));
  assert.equal(m.hostedThreshold(hosted), 0.8);
  assert.equal(m.hostedThreshold(endpoint), null);
  assert.equal(m.hostedThreshold(undefined), null);
  // `window` is W10's: null or absent means no windows are set.
  assert.equal(m.hasWindows(hosted), false);
  assert.equal(m.hasWindows({ ...hosted, window: null }), false);
  assert.equal(m.hasWindows({ ...hosted, window: { open: false, next_start_utc: "2026-12-01T14:00:00Z" } }), true);
  assert.equal(m.hasWindows(undefined), false);
});

test("the dashboard header names the serving student", () => {
  assert.deepEqual(m.servingLine({ kind: "endpoint", model: "student-v3", version: 3, threshold: 0.42, certified: true }), {
    kind: "owned",
    text: "Nano 4B + LoRA v3 · Nebius Serverless Endpoint",
    detail: null,
    title: "Nemotron 3 Nano 4B with our LoRA adapter v3, built to run on a Nebius Serverless Endpoint",
  });
  assert.deepEqual(m.servingLine({ kind: "hosted_fallback", model: FALLBACK, version: 3, threshold: 0.8, certified: false }), {
    kind: "hosted",
    text: "Nano 30B · hosted on Token Factory (endpoint off)",
    detail: "threshold 0.80 · not covered by the certificate",
    title: HOSTED_TITLE_080,
  });
  const windowed = { kind: "hosted_fallback", model: FALLBACK, version: 3, threshold: 0.8, certified: false, window: { open: false, next_start_utc: null } };
  assert.equal(m.servingLine(windowed).title, HOSTED_TITLE_080 + WINDOWS);
  assert.equal(m.servingLine({ kind: "none", model: null, version: null, threshold: null, certified: false }).text, "No student yet");
  // Mock mode, an unknown kind and an older gateway keep the base model line.
  assert.equal(m.servingLine({ kind: "mock", model: "student-v1", version: 1, threshold: 0.5, certified: true }), null);
  assert.equal(m.servingLine({ kind: "a-newer-kind", model: null, version: null, threshold: null, certified: false }), null);
  assert.equal(m.servingLine(undefined), null);
});

test("the badge context from /ratchet/stats or /scout/status", () => {
  assert.deepEqual(m.whoContext(null), {});
  assert.deepEqual(m.whoContext(undefined), {});
  const hosted = { kind: "hosted_fallback", model: FALLBACK, version: 3, threshold: 0.8, certified: false };
  assert.deepEqual(m.whoContext({ mode: "live", fallback_student: FALLBACK, student_serving: hosted }), {
    fallback: FALLBACK,
    threshold: 0.8,
    hosted: true,
    windows: false,
    simulated: false,
    serving: FALLBACK,
  });
  // A badge for "who answers this agent's calls" passes `serving`: hosted now, ours behind the endpoint.
  const ctx = m.whoContext({ mode: "live", fallback_student: FALLBACK, student_serving: hosted });
  assert.equal(m.whoLabel({ servedBy: "student", model: ctx.serving, fallback: ctx.fallback, threshold: ctx.threshold }).text, "Nano 30B · hosted");
  const endpoint = { kind: "endpoint", model: "student-v3", version: 3, threshold: 0.42, certified: true };
  assert.deepEqual(m.whoContext({ mode: "live", fallback_student: FALLBACK, student_serving: endpoint }), {
    fallback: FALLBACK,
    threshold: null,
    hosted: false,
    windows: false,
    simulated: false,
    serving: "student-v3",
  });
  const mock = m.whoContext({ mode: "mock", fallback_student: FALLBACK, student_serving: { ...endpoint, kind: "mock" } });
  assert.equal(mock.simulated, true);
  assert.equal(mock.hosted, false);
  // An older gateway without the v0.5 keys: every student call reads as ours, as before.
  assert.deepEqual(m.whoContext({ mode: "live" }), { fallback: null, threshold: null, hosted: false, windows: false, simulated: false, serving: null });
});

test("the landing strip's share item while the hosted fallback answers", () => {
  assert.deepEqual(m.studentShareCopy({ kind: "hosted_fallback", model: FALLBACK, version: 1, threshold: 0.8, certified: false }), {
    label: "Answered by the student",
    sub: "hosted Nano 30B while our GPU endpoint is off",
  });
  assert.deepEqual(m.studentShareCopy({ kind: "endpoint", model: "student-v1", version: 1, threshold: 0.5, certified: false }), {
    label: "Answered by your adapter",
    sub: null,
  });
  assert.deepEqual(m.studentShareCopy(undefined), { label: "Answered by your adapter", sub: null });
});

test("Scout counts hosted calls and its done text names the hosted Nano", () => {
  const step = (served_by, model, extra = {}) => ({
    type: "step",
    agent: "triage",
    task: "source-triage",
    served_by,
    model,
    confidence: 0.9,
    escalated: false,
    cost_usd: 0.0001,
    baseline_usd: 0.001,
    latency_ms: 10,
    detail: { id: 1, url: "https://example.org", title: "t", relevant: true, source_type: "docs", reason: "" },
    ...extra,
  });
  let run = scout.runReducer(scout.initialRun, { type: "begin", question: "q", at: 0 });
  for (const ev of [step("student", FALLBACK), step("teacher", ULTRA, { agent: "planner", detail: { queries: [] } })])
    run = scout.runReducer(run, { type: "event", ev, at: 1 });
  const totals = { calls: 2, llm_calls: 2, student_calls: 1, teacher_calls: 1, cost_usd: 0.001, baseline_usd: 0.002, savings_pct: 0.5, wall_ms: 5 };
  run = scout.runReducer(run, { type: "event", ev: { type: "done", run_id: "r", answer: "a", citations: [], totals }, at: 2 });

  const hosted = scout.derive(run, FALLBACK);
  assert.equal(hosted.totals.hosted, 1);
  assert.equal(hosted.hosted, true);
  assert.equal(scout.phaseText(run, hosted), "Done. 1 of 2 model calls answered by the hosted Nano (untuned).");
  assert.match(scout.announceText(run, hosted), /answered by the hosted Nano \(untuned\), 50% cheaper/);

  const ours = scout.derive(run);
  assert.equal(ours.totals.hosted, 0);
  assert.equal(ours.hosted, false);
  assert.equal(scout.phaseText(run, ours), "Done. 1 of 2 model calls answered by your adapter.");
  // The run's own answers decide: our adapter answered its student call, so a later switch to hosted does not relabel it.
  assert.equal(scout.derive(run, "another-model", true).hosted, false);

  // Serving hosted now and no student answer in the run (every student call escalated, or no run yet): "the student".
  let escalated = scout.runReducer(scout.initialRun, { type: "begin", question: "q", at: 0 });
  escalated = scout.runReducer(escalated, { type: "event", ev: step("teacher", ULTRA, { escalated: true, confidence: 0.3 }), at: 1 });
  assert.equal(scout.derive(escalated, FALLBACK, true).hosted, true);
  assert.equal(scout.derive(escalated, FALLBACK, false).hosted, false);
  assert.equal(scout.derive(scout.initialRun, FALLBACK, true).hosted, true);
  assert.equal(scout.derive(scout.initialRun, FALLBACK).hosted, false);
});

test("a demo-split answer is labelled as a random pick, whatever its confidence (ADR 0147)", () => {
  const split = m.whoLabel({ servedBy: "student", model: FALLBACK, fallback: FALLBACK, route: "demo_split" });
  assert.deepEqual(split, {
    kind: "hosted",
    text: "Nano 30B · demo split",
    title:
      "Nano 30B (hosted, untuned) · demo split: picked at random, not by the confidence gate. Not covered by our adapter's precision certificate.",
  });
  // Even with no fallback id known to the page, the route alone says what it was.
  assert.equal(m.whoLabel({ servedBy: "student", route: "demo_split" }).text, "Nano 30B · demo split");
  // Any other route keeps the ordinary badge; a teacher answer is never labelled a split.
  assert.equal(m.whoLabel({ servedBy: "student", model: FALLBACK, fallback: FALLBACK, route: "student" }).text, "Nano 30B · hosted");
  assert.equal(m.whoLabel({ servedBy: "teacher", model: ULTRA, route: "demo_split" }).text, "Ultra · Token Factory");
  assert.equal(m.isDemoSplit("demo_split"), true);
  assert.equal(m.isDemoSplit("student"), false);
  assert.equal(m.isDemoSplit(null), false);
});

test("the dashboard's split stat says what it is and what it did", () => {
  const base = {
    enabled: true,
    rate: 0.4,
    audit_rate: 0.25,
    requests: 8,
    of_requests: 20,
    share: 0.4,
    audits: 2,
    agreed: 1,
    agreement: 0.5,
    cost_usd: 0.001,
    baseline_usd: 0.01,
    saved_usd: 0.009,
    savings_pct: 0.9,
  };
  const s = m.demoSplitStat(base);
  assert.equal(s.label, "Demo split");
  assert.equal(s.value, "40%");
  assert.deepEqual(s.lines, [
    "A random share of calls goes to the hosted Nano 30B (untuned), picked at random, not by the confidence gate.",
    "8 of 20 requests of those tasks so far (40%).",
    "Ultra audited 2: the Nano agreed on 1 (50%).",
    "Cost 90% below all-Ultra on these calls.",
  ]);
  const none = m.demoSplitStat({ ...base, requests: 0, of_requests: 0, share: null, audits: 0, agreement: null, savings_pct: null });
  assert.equal(none.lines[1], "No request has been picked yet.");
  const noAudit = m.demoSplitStat({ ...base, audits: 0, agreed: 0, agreement: null });
  assert.equal(noAudit.lines[2], "No audit has come back yet (25% of these answers are audited).");
  // Off and never used, or an older gateway: no stat at all. Off with history: shown as off.
  assert.equal(m.demoSplitStat(undefined), null);
  assert.equal(m.demoSplitStat({ ...base, enabled: false, requests: 0 }), null);
  assert.equal(m.demoSplitStat({ ...base, enabled: false }).value, "off");
});

test("a Scout run counts and names its demo-split calls", () => {
  const steps = [
    { served_by: "student", detail: { route: "demo_split" } },
    { served_by: "student", detail: { route: "demo_split" } },
    { served_by: "student", detail: {} },
    { served_by: "teacher", detail: { route: "demo_split" } },
    { served_by: "tool", detail: null },
  ];
  assert.equal(m.demoSplitCalls(steps), 2);
  assert.equal(
    m.demoSplitNote(2, 3),
    "Demo split: 2 of 3 student calls were picked at random for the hosted Nano, not by the confidence gate.",
  );
  assert.equal(m.demoSplitNote(1, 1), "Demo split: 1 of 1 student call was picked at random for the hosted Nano, not by the confidence gate.");
  assert.equal(m.demoSplitNote(0, 4), null);
});

test("the serving line names the split while the hosted Nano serves", () => {
  const line = m.servingLine({ kind: "hosted_fallback", model: FALLBACK, version: 1, threshold: null, certified: false, tasks: {}, demo_split: 0.4 });
  assert.match(line.detail, /^demo split 40% picked at random, not by the confidence gate · not covered by the certificate$/);
});
