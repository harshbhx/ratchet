// Nano and Ultra, the pure parts: the banter picker and the lines' facts, the lessons, the throw's physics, the shelf's placement
// away from calls to action, the story script, the routing picture and the reduced-motion fallbacks. `npm test`.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { register } from "node:module";
import { test } from "node:test";

register("./ts-hooks.mjs", import.meta.url);
const model = await import("../src/components/companions/companion-model.ts");
const banter = await import("../src/components/companions/banter.ts");
const lessons = await import("../src/components/companions/lessons.ts");
const physics = await import("../src/components/companions/physics.ts");
const placement = await import("../src/components/companions/placement.ts");
const reactions = await import("../src/components/companions/reactions.ts");
const story = await import("../src/components/companions/story.ts");
const game = await import("../src/components/companions/game.ts");
const { parseExams } = await import("../src/lib/exams-model.ts");

const exams = parseExams(readFileSync(new URL("../src/data/exams.json", import.meta.url), "utf8"));
const facts = banter.factsFromExams(exams);
const lines = banter.banterLines(facts);
const everyText = () => [
  ...lines.flatMap((b) => b.lines.map((l) => l.text)),
  ...Object.values(reactions.sectionReactions(facts)).flatMap((r) => r.lines.map((l) => l.text)),
  ...story.storySteps(facts).map((s) => s.caption),
  ...Object.values(reactions.DUO_LINES).flatMap((ls) => ls.map((l) => l.text)),
  ...reactions.EASTER_EGG.map((l) => l.text),
  ...["sent", "answer", "limit", "error"].flatMap((p) => reactions.liveReaction(p).map((l) => l.text)),
];

// ---------- banter ----------
test("the facts are the exams' own numbers", () => {
  assert.deepEqual([facts.r2e1, facts.r2e2, facts.base1, facts.base2], ["39.0%", "33.0%", "12.8%", "12.5%"]);
  assert.equal(facts.r1rows, "821");
  assert.equal(facts.r2rows, "4,322");
});

test("about forty banter exchanges, short lines, both voices, day and night variants", () => {
  assert.ok(lines.length >= 38, `${lines.length} exchanges`);
  assert.equal(new Set(lines.map((b) => b.id)).size, lines.length);
  for (const b of lines) {
    assert.ok(b.lines.length >= 2);
    for (const l of b.lines) assert.ok(l.text.split(/\s+/).length <= 14, l.text);
  }
  assert.ok(lines.some((b) => b.when === "night") && lines.some((b) => b.when === "day"));
  const who = new Set(lines.flatMap((b) => b.lines.map((l) => l.who)));
  assert.deepEqual([...who].sort(), ["nano", "ultra"]);
});

test("no line breaks the honesty rules", () => {
  for (const t of everyText()) {
    assert.doesNotMatch(t, /demo|simulat/i, t);
    assert.doesNotMatch(t, /hosted on nebius|nebius hosts|in production/i, t);
    assert.doesNotMatch(t, /\byou own (a|the) model\b|own the model|your model\b/i, t);
    assert.doesNotMatch(t, /beats? the teacher|better than ultra|outperform/i, t);
    assert.doesNotMatch(t, /—|–/, t);
  }
});

test("every number in a line is one the site publishes", () => {
  const allowed = new Set(["39.0", "33.0", "12.8", "12.5", "821", "4,322", "1.6", "00:00", "1", "2", "3", "7", "9", "16", "40", "25"]);
  for (const t of everyText()) {
    for (const n of t.match(/\d[\d,.:]*\d|\d/g) ?? []) assert.ok(allowed.has(n.replace(/\.$/, "")), `${n} in "${t}"`);
  }
});

test("the student is never said to serve live traffic", () => {
  for (const t of everyText()) assert.doesNotMatch(t, /(I|Nano|student|adapter) (serves?|answers?) (live|every|real)/i, t);
  // Where Night School is mentioned, it is also said to be unproven on live traffic.
  for (const b of lines) {
    const text = b.lines.map((l) => l.text).join(" ");
    if (/night school/i.test(text) && !/night-lamp/.test(b.id)) assert.match(text, /not (yet )?(run|live)|mock/i, text);
  }
});

test("the picker deals every item once before any repeat, and a new bag never opens with the last item", () => {
  let seed = 7;
  const rng = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
  const items = Array.from({ length: 12 }, (_, i) => i);
  const p = banter.createPicker(items, rng);
  let prev;
  for (let round = 0; round < 6; round++) {
    const got = Array.from({ length: items.length }, () => p.next());
    assert.deepEqual([...got].sort((a, b) => a - b), items, `round ${round}`);
    if (prev !== undefined) assert.notEqual(got[0], prev);
    prev = got[got.length - 1];
  }
  assert.equal(banter.createPicker([], rng).next(), undefined);
  assert.equal(banter.createPicker(["only"], rng).next(), "only");
});

test("banter waits 25 to 40 seconds and a bubble stays long enough to read", () => {
  assert.equal(banter.nextDelayMs(() => 0), 25000);
  assert.ok(banter.nextDelayMs(() => 0.999999) < 40000);
  assert.ok(banter.readMs("Short.") >= 2000);
  assert.ok(banter.readMs("word ".repeat(40)) <= 7000);
});

// ---------- lessons ----------
test("seven lessons, earned once, kept for the day only", () => {
  assert.equal(lessons.LESSON_TOTAL, 7);
  let s = lessons.emptyLessons("2026-10-28");
  for (const l of lessons.LESSONS) {
    const r = lessons.earn(s, l.id);
    assert.equal(r.newly, true);
    assert.equal(lessons.earn(r.state, l.id).newly, false);
    s = r.state;
  }
  assert.equal(lessons.lessonCount(s), 7);
  assert.equal(lessons.lessonsComplete(s), true);
  assert.equal(lessons.lessonsComplete(lessons.emptyLessons("x")), false);
  assert.equal(lessons.normalizeLessons(s, "2026-10-28").ids.length, 7);
  assert.equal(lessons.normalizeLessons(s, "2026-10-29").ids.length, 0, "another day starts over");
  assert.equal(lessons.normalizeLessons({ day: "d", ids: ["rent", "nope", "rent"] }, "d").ids.length, 1);
  assert.equal(lessons.normalizeLessons("garbage", "d").ids.length, 0);
  assert.equal(lessons.dayStamp(new Date(2026, 9, 8)), "2026-10-08");
});

test("key sections teach their lessons, and every lesson can be earned from the page", () => {
  const ids = ["rent", "how", "learn", "real-run", "safety", "stack", "nebius", "integrate", "bill", "trust"];
  const earned = new Set(ids.flatMap((i) => lessons.lessonsForSection(i)));
  for (const l of lessons.LESSONS) if (l.id !== "hello") assert.ok(earned.has(l.id), l.id);
  assert.deepEqual(lessons.lessonsForSection("hero"), []);
});

// ---------- physics ----------
test("a throw settles on the shelf, bouncing when it is hard", () => {
  for (const [x0, v0] of [[0, 0], [-240, 1800], [160, -900], [-30, 3000], [0, 2500]]) {
    const s = physics.springRun(x0, v0);
    assert.ok(s.settled, `${x0},${v0}`);
    assert.ok(s.settleS < 4, `${s.settleS}s`);
  }
  const hard = physics.springRun(-200, 2400);
  assert.ok(hard.crossings >= 1 && hard.peak > 5, "an underdamped spring overshoots");
  assert.ok(physics.SPRING.damping < 2 * Math.sqrt(physics.SPRING.stiffness), "the fall is underdamped: it bounces");
});

test("drag bounds keep a character inside the viewport; squash grows with speed", () => {
  const b = physics.dragBounds({ left: 900, right: 1000, top: 500, bottom: 650 }, 1280, 720);
  assert.deepEqual(b, { left: -892, right: 272, top: -492, bottom: 62 });
  assert.deepEqual(physics.clampTo(-5000, 5000, b), { x: -892, y: 62 });
  const soft = physics.landSquash(100);
  const hard = physics.landSquash(5000);
  assert.ok(hard.sy < soft.sy && hard.sx > soft.sx);
  assert.ok(hard.sy >= 0.7 - 1e-9);
  assert.ok(physics.airStretch(2000).sy > 1);
});

test("five pokes inside three seconds is the easter egg, and the count starts over", () => {
  const b = physics.createBurst(5, 3000);
  assert.deepEqual([0, 400, 900, 1500].map((t) => b.push(t)), [false, false, false, false]);
  assert.equal(b.push(2000), true);
  assert.equal(b.push(2100), false);
  const slow = physics.createBurst(5, 3000);
  assert.deepEqual([0, 1000, 2000, 3000, 4000, 5000].map((t) => slow.push(t)), [false, false, false, false, false, false]);
});

// ---------- placement ----------
test("the shelf stands bottom right, steps aside for a call to action and keeps its place while that stays clear", () => {
  const vp = { w: 1280, h: 720 };
  const size = { w: 212, h: 156 };
  assert.deepEqual(placement.chooseSlot(vp, size, []), { side: "right", lift: 0, clear: true });
  const rect = placement.slotRect(vp, size, { side: "right", lift: 0 });
  assert.deepEqual(rect, { left: 1056, right: 1268, bottom: 708, top: 552 });
  const cta = { left: 1000, top: 600, right: 1200, bottom: 650 };
  const moved = placement.chooseSlot(vp, size, [cta]);
  assert.deepEqual([moved.side, moved.lift, moved.clear], ["left", 0, true]);
  const both = [cta, { left: 20, top: 600, right: 300, bottom: 650 }];
  const lifted = placement.chooseSlot(vp, size, both);
  assert.ok(lifted.lift > 0 && lifted.clear);
  assert.ok(!placement.intersects(placement.slotRect(vp, size, lifted), cta, 10));
  // Hysteresis: a slot that is still clear is kept even though bottom right would be clear too.
  const keep = placement.chooseSlot(vp, size, [], { side: "left", lift: 72, clear: true });
  assert.deepEqual([keep.side, keep.lift], ["left", 72]);
  // Nothing clear: it stays where it began and says so.
  const wall = { left: 0, top: 0, right: 1280, bottom: 720 };
  assert.equal(placement.chooseSlot(vp, size, [wall]).clear, false);
});

test("the dock on a phone avoids a full-width button too", () => {
  const vp = { w: 390, h: 800 };
  const dock = { w: 64, h: 64 };
  const button = { left: 16, top: 690, right: 374, bottom: 738 };
  const s = placement.chooseSlot(vp, dock, [button]);
  assert.ok(s.lift > 0);
  assert.ok(!placement.intersects(placement.slotRect(vp, dock, s), button, 10));
});

// ---------- model: time of day, theme, reduced motion ----------
test("Night School is 20:00 to 05:59 local", () => {
  const at = (h, m = 0) => new Date(2026, 9, 28, h, m);
  assert.deepEqual([19, 20, 23, 0, 5, 6, 12].map((h) => model.isNight(at(h))), [false, true, true, true, true, false, false]);
  assert.equal(model.isNight(at(19, 59)), false);
  assert.equal(model.isNight(at(5, 59)), true);
});

test("Nano wears sunglasses in dark, squints in light, and has a lamp and a book at night", () => {
  const ctx = { night: false, dark: false, graduated: false, squinting: false };
  assert.equal(model.baseLook("nano", { ...ctx, dark: true }).sunglasses, true);
  assert.equal(model.baseLook("nano", ctx).sunglasses, false);
  assert.equal(model.baseLook("nano", { ...ctx, squinting: true }).squint, true);
  assert.equal(model.baseLook("nano", { ...ctx, dark: true, squinting: true }).squint, false);
  const night = model.baseLook("nano", { ...ctx, night: true });
  assert.deepEqual([night.lamp, night.held], [true, "book"]);
  assert.equal(model.baseLook("nano", ctx).lamp, false);
  assert.equal(model.baseLook("ultra", { ...ctx, night: true }).held, "papers");
  assert.equal(model.baseLook("ultra", { ...ctx, dark: true }).sunglasses, false);
  assert.equal(model.baseLook("nano", { ...ctx, graduated: true }).cap, true);
});

test("the ten expressions and the poses exist", () => {
  assert.equal(model.EXPRESSIONS.length, 10);
  for (const e of ["idle", "happy", "thinking", "proud", "sleepy", "surprised", "laughing", "determined", "sad", "wave"]) assert.ok(model.EXPRESSIONS.includes(e), e);
  for (const p of ["jump", "pointLeft", "pointRight", "carry"]) assert.ok(model.POSES.includes(p), p);
});

test("reduced motion: static portraits, no autonomy, no physics, no auto-scroll, a stepper", () => {
  assert.deepEqual(model.motionPlan(true), { ambient: false, autonomous: false, physics: false, autoScroll: false, stepper: true });
  assert.deepEqual(model.motionPlan(false), { ambient: true, autonomous: true, physics: true, autoScroll: true, stepper: false });
});

test("the pupils follow the pointer within a limit, and rest when it is on top of them", () => {
  assert.deepEqual(model.lookOffset(10, 10, 10, 10, 2), { x: 0, y: 0 });
  const far = model.lookOffset(1000, 0, 0, 0, 2);
  assert.ok(Math.abs(far.x - 2) < 1e-9 && far.y === 0);
  const near = model.lookOffset(20, 0, 0, 0, 2);
  assert.ok(near.x > 0 && near.x < 1);
});

test("typing and phone width are recognised", () => {
  assert.equal(model.isTyping({ tagName: "INPUT" }), true);
  assert.equal(model.isTyping({ tagName: "textarea" }), true);
  assert.equal(model.isTyping({ tagName: "DIV", isContentEditable: true }), true);
  assert.equal(model.isTyping({ tagName: "BUTTON" }), false);
  assert.equal(model.isTyping(null), false);
  assert.deepEqual([390, 639, 640, 1280].map(model.isPhone), [true, true, false, false]);
});

// ---------- reactions ----------
test("pokes: a random reaction, never twice the same kind, glasses only for Ultra", () => {
  for (let i = 0; i < 40; i++) {
    const r = Math.random;
    const a = reactions.pokeReaction("nano", r);
    const b = reactions.pokeReaction("nano", r, a.kind);
    assert.notEqual(a.kind, b.kind);
    assert.notEqual(a.kind, "glasses");
    assert.ok(a.say.length > 0);
  }
  const kinds = new Set(Array.from({ length: 80 }, () => reactions.pokeReaction("ultra").kind));
  assert.ok(kinds.has("glasses"));
  const nanoKinds = new Set(Array.from({ length: 200 }, () => reactions.pokeReaction("nano").kind));
  assert.ok(nanoKinds.size >= 3, "3 or 4 variants per kind of reaction");
});

test("the easter egg: Nano spins, then Ultra asks for focus", () => {
  assert.deepEqual(reactions.EASTER_EGG.map((l) => l.who), ["nano", "ultra"]);
  assert.match(reactions.EASTER_EGG[1].text, /focus/i);
});

test("sections: each is remarked on once per visit; the exam line carries the real numbers; the gate says not yet", () => {
  assert.equal(reactions.shouldReact([], "safety"), true);
  assert.equal(reactions.shouldReact(["safety"], "safety"), false);
  const r = reactions.sectionReactions(facts);
  const exam = r["real-run"].lines.map((l) => l.text).join(" ");
  for (const n of ["39.0%", "33.0%", "12.8%", "12.5%"]) assert.ok(exam.includes(n), n);
  assert.equal(r.safety.lines[0].who, "ultra");
  assert.match(r.safety.lines[0].text, /not yet/i);
  assert.equal(r.safety.ultra.pose, "stop");
  assert.match(r.stack.lines[0].text, /Token Factory/);
  assert.match(r.stack.lines[0].text, /Nebius AI Cloud/);
  assert.deepEqual([r.footer.nano.expr, r.footer.ultra.expr], ["wave", "wave"]);
  for (const id of reactions.WATCHED_SECTIONS) assert.ok(r[id], id);
});

test("the two move with each other: high five, a pat, a passed card and hiding", () => {
  assert.deepEqual([...reactions.DUO_MOVES].sort(), ["hide", "highfive", "pass", "pat"]);
  for (const m of reactions.DUO_MOVES) assert.ok(reactions.DUO_LINES[m].length >= 2);
});

// ---------- story ----------
test("the story is nine steps of 60 to 90 seconds, in the order of the brief, with the real exam numbers", () => {
  const steps = story.storySteps(facts);
  const total = story.storyDurationMs(steps);
  assert.ok(total >= 60_000 && total <= 90_000, `${total} ms`);
  assert.deepEqual(
    steps.map((s) => s.id),
    ["hello", "rent", "cascade", "night", "exam", "gate", "retrain", "endpoint", "adapter"],
  );
  const exam = steps.find((s) => s.id === "exam").caption;
  for (const n of ["39.0%", "33.0%", "12.8%", "12.5%"]) assert.ok(exam.includes(n), n);
  assert.match(steps.find((s) => s.id === "night").caption, /not yet run on live traffic/i);
  assert.match(steps.find((s) => s.id === "retrain").caption, /Nebius AI Cloud/);
  assert.match(steps.find((s) => s.id === "endpoint").caption, /Ultra/);
  assert.match(steps.find((s) => s.id === "gate").caption, /not yet/i);
  for (const s of steps) assert.ok(s.caption.length < 170, s.caption);
});

test("the story's player: play, pause, resume, skip, back, finish, exit", () => {
  const n = 9;
  const run = (s, a) => story.storyReducer(s, a, n);
  let s = run(story.STORY_IDLE, { type: "start" });
  assert.deepEqual(s, { status: "playing", index: 0 });
  s = run(s, { type: "pause" });
  assert.equal(s.status, "paused");
  assert.equal(run(s, { type: "next" }).index, 1, "skip while paused moves on");
  s = run(s, { type: "resume" });
  assert.equal(s.status, "playing");
  s = run(s, { type: "prev" });
  assert.equal(s.index, 0, "never below the first step");
  for (let i = 0; i < n - 1; i++) s = run(s, { type: "next" });
  assert.deepEqual(s, { status: "playing", index: 8 });
  s = run(s, { type: "next" });
  assert.equal(s.status, "done");
  assert.equal(run(s, { type: "prev" }).status, "playing");
  assert.deepEqual(run(s, { type: "exit" }), story.STORY_IDLE);
  assert.deepEqual(run(story.STORY_IDLE, { type: "next" }), story.STORY_IDLE);
  assert.deepEqual(run(story.STORY_IDLE, { type: "pause" }), story.STORY_IDLE);
});

// ---------- the routing picture ----------
test("the routing picture: catch, three tries, unsure, pass to Ultra, no model output", () => {
  assert.deepEqual(
    game.GAME_STEPS.map((s) => s.phase),
    ["catch", "try1", "try2", "try3", "unsure", "throw", "answer"],
  );
  let p = "idle";
  const seen = [];
  for (let i = 0; i < 9; i++) {
    p = game.nextPhase(p);
    seen.push(p);
  }
  assert.equal(seen.at(-1), "answer", "it ends at Ultra and stays there");
  assert.deepEqual(["idle", "catch", "try1", "try2", "try3", "unsure", "throw"].map(game.thoughtBubbles), [0, 0, 1, 2, 3, 3, 0]);
  for (const s of game.GAME_STEPS) assert.doesNotMatch(s.caption, /answer(ed)?:|the answer is/i);
  assert.equal(game.cleanQuestion("  where   is\n my   order?  "), "where is my order?");
  assert.equal(game.cleanQuestion("x".repeat(500)).length, game.MAX_QUESTION);
});

// ---------- storage ----------
test("the companions' browser storage lives in one file, behind try/catch, with the registered keys", () => {
  const src = new URL("../src/components/companions/", import.meta.url);
  const files = ["companion-storage.ts", "companion-bus.ts", "companion-host.tsx", "stage.tsx", "sheet.tsx", "companion.tsx", "character.tsx", "actor.ts"];
  for (const f of files) {
    const text = readFileSync(new URL(f, src), "utf8");
    const uses = /localStorage|sessionStorage|indexedDB|document\.cookie/.test(text);
    assert.equal(uses, f === "companion-storage.ts", f);
  }
  const text = readFileSync(new URL("companion-storage.ts", src), "utf8");
  assert.deepEqual([...text.matchAll(/_KEY = "([^"]+)"/g)].map((m) => m[1]).sort(), ["ratchet-companions-hidden", "ratchet-companions-session"]);
  assert.equal((text.match(/\btry\b/g) ?? []).length, (text.match(/\bcatch\b/g) ?? []).length);
});
