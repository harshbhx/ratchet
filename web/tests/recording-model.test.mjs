// Recorded Scout runs (src/lib/recording-model.ts, FR-10.40): the play schedule is the recorded offsets, "Skip to the end"
// delivers every remaining event in order once, cancel delivers nothing more, and the labels say recording. `npm test`.
import assert from "node:assert/strict";
import { register } from "node:module";
import { test } from "node:test";

register("./ts-hooks.mjs", import.meta.url);
const m = await import("../src/lib/recording-model.ts");

const rec = {
  id: "20261021T100000Z-q",
  recorded_at: "2026-10-21T10:05:00Z",
  question: "Q",
  duration_ms: 4200,
  mode: "live",
  events: [
    { type: "start", run_id: "a", question: "Q", mode: "live", t_ms: 0 },
    { type: "step", agent: "planner", t_ms: 900 },
    { type: "step", agent: "searcher", t_ms: 900 },
    { type: "done", run_id: "a", answer: "A", citations: [], totals: {}, t_ms: 4200 },
  ],
};

function fakeTimers() {
  const q = [];
  return {
    q,
    set: (fn, ms) => {
      const h = { fn, ms, on: true };
      q.push(h);
      return h;
    },
    clear: (h) => {
      h.on = false;
    },
    fire: () => q.filter((h) => h.on).sort((a, b) => a.ms - b.ms).forEach((h) => ((h.on = false), h.fn())),
  };
}

test("the schedule is the recorded offsets, in order, without t_ms in the events", () => {
  const s = m.schedule(rec);
  assert.deepEqual(s.map((x) => x.at), [0, 900, 900, 4200]);
  assert.deepEqual(s.map((x) => x.ev.type), ["start", "step", "step", "done"]);
  assert.ok(s.every((x) => !("t_ms" in x.ev)));
  // an offset that goes backwards never schedules an event before its predecessor
  assert.deepEqual(m.schedule({ events: [{ type: "start", t_ms: 500 }, { type: "done", t_ms: 100 }] }).map((x) => x.at), [500, 500]);
});

test("play sets one timer per event at its offset and delivers them in order", () => {
  const t = fakeTimers();
  const got = [];
  let ended = 0;
  m.play(rec, (ev) => got.push(ev.type), t, () => ended++);
  assert.deepEqual(t.q.map((h) => h.ms), [0, 900, 900, 4200]);
  t.fire();
  assert.deepEqual(got, ["start", "step", "step", "done"]);
  assert.equal(ended, 1);
});

test("Skip to the end delivers every remaining event in order, once", () => {
  const t = fakeTimers();
  const got = [];
  let ended = 0;
  const p = m.play(rec, (ev) => got.push(ev.type), t, () => ended++);
  t.q[0].fn();
  t.q[0].on = false;
  assert.deepEqual(got, ["start"]);
  p.skip();
  assert.deepEqual(got, ["start", "step", "step", "done"]);
  p.skip();
  t.fire();
  assert.deepEqual(got, ["start", "step", "step", "done"]);
  assert.equal(ended, 1);
});

test("cancel delivers nothing more", () => {
  const t = fakeTimers();
  const got = [];
  const p = m.play(rec, (ev) => got.push(ev.type), t);
  p.cancel();
  t.q.forEach((h) => h.fn());
  p.skip();
  assert.deepEqual(got, []);
});

test("labels: a live recording and a mock one", () => {
  assert.equal(m.recordingLabel(rec), "Recorded live run · 21 Oct 2026 10:05 UTC");
  assert.equal(m.recordingLabel({ ...rec, mode: "mock" }), "Recorded simulation · 21 Oct 2026 10:05 UTC");
  assert.equal(m.watchLabel(rec), "Watch a recorded live run (4 s)");
  assert.equal(m.watchLabel({ ...rec, mode: "mock" }), "Watch a recorded simulation (4 s)");
});

test("the button plays the newest live recording, never a simulation", () => {
  const list = [{ ...rec, id: "c", mode: "mock" }, { ...rec, id: "b" }, { ...rec, id: "a" }];
  assert.equal(m.pickLive(list).id, "b");
  assert.equal(m.pickLive([{ ...rec, mode: "mock" }]), null);
  assert.equal(m.pickLive(null), null);
});
