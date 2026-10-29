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
const perch = await import("../src/components/companions/perch.ts");
const scroll = await import("../src/components/companions/scroll.ts");
const scenesMod = await import("../src/components/companions/scenes.ts");
const quizMod = await import("../src/components/companions/quiz-model.ts");
const comehere = await import("../src/components/companions/comehere.ts");
const seekMod = await import("../src/components/companions/hideseek.ts");
const { parseExams } = await import("../src/lib/exams-model.ts");

const exams = parseExams(readFileSync(new URL("../src/data/exams.json", import.meta.url), "utf8"));
const facts = banter.factsFromExams(exams);
const lines = banter.banterLines(facts);
const everyText = () => [
  ...lines.flatMap((b) => b.lines.map((l) => l.text)),
  ...Object.values(reactions.sectionReactions(facts)).flatMap((r) => r.lines.map((l) => l.text)),
  ...story.storySteps(facts).map((s) => s.caption),
  ...Object.values(reactions.DUO_LINES).flatMap((ls) => ls.map((l) => l.text)),
  ...quizMod.quizTexts(),
  ...Object.values(scenesMod.scenes(facts)).flatMap((sc) => [sc.nano.text, sc.ultra.text].filter(Boolean)),
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
    assert.ok(b.lines.length >= 1);
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
  const allowed = new Set(["39", "33", "39.0", "33.0", "12.8", "12.5", "821", "4,322", "1.6", "00:00", "1", "2", "3", "7", "9", "16", "40", "25"]);
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
  assert.deepEqual([...reactions.DUO_MOVES].sort(), ["correct", "fistbump", "hide", "highfive", "levelup", "pass", "pat", "sneak"]);
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
  assert.match(steps.find((s) => s.id === "night").caption, /rejected it.*not yet run nightly on live traffic/i);
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
  const files = ["companion-storage.ts", "companion-bus.ts", "companion-host.tsx", "stage.tsx", "sheet.tsx", "companion.tsx", "character.tsx", "actor.ts", "quiz.tsx", "quiz-model.ts", "comehere.ts", "hideseek.ts", "roaming.ts"];
  for (const f of files) {
    const text = readFileSync(new URL(f, src), "utf8");
    const uses = /localStorage|sessionStorage|indexedDB|document\.cookie/.test(text);
    assert.equal(uses, f === "companion-storage.ts", f);
  }
  const text = readFileSync(new URL("companion-storage.ts", src), "utf8");
  assert.deepEqual([...text.matchAll(/_KEY = "([^"]+)"/g)].map((m) => m[1]).sort(), ["ratchet-companions-hidden", "ratchet-companions-session"]);
  assert.equal((text.match(/\btry\b/g) ?? []).length, (text.match(/\bcatch\b/g) ?? []).length);
});

// ---------- perches, following the scroll, the auto-jump ----------
const VP = { w: 1280, h: 800 };
const NANO = { w: 84, h: 91 };
const ULTRA = { w: 108, h: 117 };
const noZones = { cta: [], text: [] };
const card = (id, left, top, right = left + 400, bottom = top + 200) => ({ id, kind: "card", rect: { left, top, right, bottom } });

test("Nano perches on the right end of a card's top edge, Ultra on the left end", () => {
  const items = [card("a", 100, 400, 700)];
  const n = perch.candidatesFromItems(items, "nano");
  const u = perch.candidatesFromItems(items, "ultra");
  assert.deepEqual([n[0].x, n[0].y, u[0].x, u[0].y], [700 - perch.PERCH_INSET, 400, 100 + perch.PERCH_INSET, 400]);
  assert.equal(n.length, 1, "one consistent offset from the corner");
  assert.ok(n.every((c) => c.x > 120 && c.x < 680));
});

test("the nearest good perch wins: the middle of the screen, the right side for Nano, the left for Ultra", () => {
  const items = [card("far-top", 800, 120, 1200), card("mid-right", 700, 520, 1200), card("mid-left", 60, 380, 560), card("low", 700, 740, 1200)];
  const ctx = { vp: VP, size: NANO, zones: noZones };
  const n = perch.choosePerch(perch.candidatesFromItems(items, "nano"), "nano", ctx);
  assert.equal(n.cand.id.split(":")[0], "mid-right");
  const u = perch.choosePerch(perch.candidatesFromItems(items, "ultra"), "ultra", { ...ctx, size: ULTRA });
  assert.equal(u.cand.id.split(":")[0], "mid-left");
});

test("a perch under a call to action or on body text loses to a clear one; one outside the viewport is never chosen", () => {
  const items = [card("a", 700, 500, 1200), card("b", 700, 600, 1200)];
  const cands = perch.candidatesFromItems(items, "nano");
  const best = perch.choosePerch(cands, "nano", { vp: VP, size: NANO, zones: noZones }).cand;
  const blocked = { cta: [{ left: best.x - 50, right: best.x + 50, top: best.y - 100, bottom: best.y - 5 }], text: [] };
  const next = perch.choosePerch(cands, "nano", { vp: VP, size: NANO, zones: blocked }).cand;
  assert.notEqual(next.id, best.id);
  const text = { cta: [], text: [{ left: best.x - 50, right: best.x + 50, top: best.y - 100, bottom: best.y - 5 }] };
  assert.notEqual(perch.choosePerch(cands, "nano", { vp: VP, size: NANO, zones: text }).cand.id, best.id);
  const off = [{ id: "x", kind: "floor", x: 10, y: 20 }, { id: "y", kind: "floor", x: 1300, y: 790 }, { id: "z", kind: "floor", x: 640, y: 30 }];
  assert.equal(perch.choosePerch(off, "nano", { vp: VP, size: NANO, zones: noZones }), null, "under the header, off the side");
  assert.ok(perch.perchCost({ id: "w", kind: "floor", x: 640, y: 794 }, "nano", { vp: VP, size: NANO, zones: noZones }) < Infinity);
});

test("the current perch is kept unless another is clearly better, and the two keep apart", () => {
  const items = [card("a", 700, 500, 1200), card("b", 300, 520, 900)];
  const cands = perch.candidatesFromItems(items, "nano");
  const first = perch.choosePerch(cands, "nano", { vp: VP, size: NANO, zones: noZones }).cand;
  const other = cands.find((c) => c.id !== first.id);
  const kept = perch.choosePerch(cands, "nano", { vp: VP, size: NANO, zones: noZones, current: other.id }).cand;
  assert.equal(kept.id, other.id, "hysteresis: a small gain does not move him");
  const crowded = perch.choosePerch(cands, "nano", { vp: VP, size: NANO, zones: noZones, other: { x: first.x, y: first.y } }).cand;
  assert.notEqual(crowded.id, first.id);
  assert.ok(perch.choosePerch(cands, "nano", { vp: VP, size: NANO, zones: noZones }, 0.99).cand);
  assert.equal(perch.choosePerch([], "nano", { vp: VP, size: NANO, zones: noZones }), null);
});

test("floor perches exist at each side; at the very top and the foot of the page they gather", () => {
  const n = perch.floorCandidates("nano", VP);
  const u = perch.floorCandidates("ultra", VP);
  assert.ok(n.every((c) => c.x > VP.w / 2) && u.every((c) => c.x < VP.w / 2));
  assert.deepEqual([perch.gathers(0, 800, 9000), perch.gathers(79, 800, 9000), perch.gathers(80, 800, 9000), perch.gathers(8200, 800, 9000), perch.gathers(8159, 800, 9000)], [true, true, false, true, false]);
  const pair = perch.gatherPair(VP, { nano: NANO, ultra: ULTRA }, noZones);
  assert.ok(Math.abs(pair.nano.x - pair.ultra.x) < 140 && pair.nano.y === pair.ultra.y);
  const cta = { left: 1000, right: 1280, top: 600, bottom: 800 };
  const away = perch.gatherPair(VP, { nano: NANO, ultra: ULTRA }, { cta: [cta], text: [] });
  assert.ok(away.nano.x < 1000 - 40, "they gather away from a call to action");
});

test("a perch follows the content as the page scrolls, and is replaced when it slides out of the band", () => {
  assert.equal(perch.followY(500, 0, 120), 380);
  assert.equal(perch.followY(500, 300, 300), 500);
  assert.equal(perch.followY(500, 300, 100), 700, "scrolling back up moves it down");
  assert.equal(perch.needsRepick(380, 800), false);
  assert.equal(perch.needsRepick(60, 800), true, "under the header");
  assert.equal(perch.needsRepick(795, 800), true, "off the bottom");
});

test("travel: Nano walks short steps and jumps far or when the page is flying; Ultra always glides", () => {
  const a = { x: 900, y: 500 };
  const near = perch.travelPlan(a, { x: 960, y: 520 }, "nano", false);
  assert.equal(near.mode, "walk");
  assert.ok(near.ms >= 500 && near.arc === 0);
  assert.equal(perch.travelPlan(a, { x: 960, y: 700 }, "nano", false).mode, "jump");
  assert.equal(perch.travelPlan(a, { x: 300, y: 500 }, "nano", false).mode, "jump");
  const fast = perch.travelPlan(a, { x: 960, y: 520 }, "nano", true);
  assert.equal(fast.mode, "jump");
  assert.ok(fast.arc >= 40 && fast.arc <= 140 && fast.ms >= 420 && fast.ms <= 900);
  const g = perch.travelPlan(a, { x: 100, y: 100 }, "ultra", true);
  assert.deepEqual([g.mode, g.arc], ["glide", 0]);
  assert.ok(g.ms >= 650 && g.ms <= 1700);
});

test("scroll speed from samples, and when Nano jumps to keep up", () => {
  let s = [];
  assert.equal(scroll.velocity(s), 0);
  s = scroll.pushSample(s, 0, 0);
  assert.equal(scroll.velocity(s), 0, "one sample says nothing");
  s = scroll.pushSample(s, 50, 100);
  assert.equal(Math.round(scroll.velocity(s)), 2000);
  s = scroll.pushSample(s, 400, 110);
  assert.ok(s.length < 3, "old samples fall out of the window");
  assert.deepEqual([scroll.scrollMode(10), scroll.scrollMode(500), scroll.scrollMode(2500)], ["still", "slow", "fast"]);
  assert.equal(scroll.shouldAutoJump(2500, 1000), true);
  assert.equal(scroll.shouldAutoJump(2500, 100), false, "he must land first");
  assert.equal(scroll.shouldAutoJump(900, 5000), false, "a slow scroll does not trigger it");
  assert.ok(scroll.repickEveryMs("nano", 3000) < scroll.repickEveryMs("ultra", 3000), "Nano keeps up, Ultra rarely moves");
  assert.ok(scroll.repickEveryMs("nano", 100) >= 600);
});

test("a micro-expression field exists at rest", () => {
  assert.equal(model.REST.micro, "none");
  assert.ok(model.POSES.includes("sip"));
});

// ---------- eyes, scenes, peeking ----------
test("Nano's and Ultra's eyes are open at rest, in every expression but sleepy", () => {
  for (const ultra of [false, true]) {
    for (const idx of [0, 1]) {
      const rest = model.eyeSpec("idle", false, idx, ultra);
      assert.ok(rest.top <= 0.2 && rest.bottom === 0, `idle ${ultra ? "ultra" : "nano"} ${idx}`);
      for (const e of model.EXPRESSIONS) {
        if (e === "sleepy") continue;
        const sp = model.eyeSpec(e, false, idx, ultra);
        assert.ok(sp.top <= model.OPEN_MAX, `${e} top ${sp.top}`);
        assert.ok(sp.bottom <= 0.35, `${e} bottom ${sp.bottom}`);
        assert.ok(sp.scale >= 1 && sp.pupil > 2, e);
      }
    }
  }
  assert.equal(model.eyeSpec("idle", false, 0, false).top, 0, "Nano's default lid is wide open");
  assert.ok(model.eyeSpec("sleepy", false, 0, false).top > 0.5, "sleepy is the one that closes");
  assert.ok(model.eyeSpec("idle", true, 0, false).top <= 0.3 + 1e-9, "a squint narrows, it is not a slit");
  assert.ok(model.eyeSpec("idle", false, 0, false, true).top > 0.5, "a yawn shuts them");
});

test("at rest and after a theme change Nano's eyes stay open; the sunglasses sit on his head", () => {
  const c = { night: true, dark: true, graduated: false, squinting: false };
  const dark = model.baseLook("nano", c);
  assert.equal(dark.sunglasses, true);
  assert.equal(dark.shadesDown, false, "pushed up, not over the eyes");
  assert.equal(model.baseLook("nano", { ...c, shadesDown: true }).shadesDown, true);
  assert.equal(model.baseLook("nano", { ...c, dark: false, shadesDown: true }).shadesDown, false);
  for (const night of [true, false]) for (const d of [true, false]) assert.equal(model.baseLook("nano", { night, dark: d, graduated: false, squinting: false }).expr, "idle");
});

test("a perch on text is never chosen; when nothing is clear they tuck into the gutter, head and hands in view", () => {
  const vp = { w: 1280, h: 800 };
  const wall = { cta: [], text: [{ left: 0, top: 0, right: 1280, bottom: 800 }] };
  const cands = [...perch.candidatesFromItems([card("a", 700, 500, 1200)], "nano"), ...perch.floorCandidates("nano", vp)];
  assert.equal(perch.choosePerch(cands, "nano", { vp, size: NANO, zones: wall }), null, "every perch is on text");
  const peek = perch.bestPeek("nano", vp, NANO, wall);
  assert.equal(peek.kind, "peek");
  assert.ok(peek.x > vp.w - 10, "Nano peeks from the right edge");
  assert.ok(perch.bestPeek("ultra", vp, ULTRA, wall).x < 10, "Ultra from the left");
  // A gutter beside the text is a fine place: only the narrow strip at the edge matters.
  const gutter = { cta: [], text: [{ left: 40, top: 0, right: 1240, bottom: 800 }] };
  const best = perch.choosePerch(perch.peekCandidates("nano", vp), "nano", { vp, size: NANO, zones: gutter });
  assert.ok(best && best.cand.kind === "peek", "peeking into a 40px gutter is allowed");
  // A call to action is never stood on, even by a peek.
  const cta = { cta: [{ left: 1100, top: 0, right: 1280, bottom: 800 }], text: [] };
  assert.equal(perch.choosePerch(perch.peekCandidates("nano", vp), "nano", { vp, size: NANO, zones: cta }), null);
  assert.equal(perch.gatherPair(vp, { nano: NANO, ultra: ULTRA }, wall), null, "no room to gather on text");
});

test("every landing section and every page has a scene, and the exam scene holds the real score card", () => {
  const sc = scenesMod.scenes(facts);
  for (const id of [...scenesMod.SCENE_SECTIONS, ...scenesMod.ROUTE_SCENES]) assert.ok(sc[id], id);
  assert.equal(sc["real-run"].nano.text, "39%");
  assert.equal(sc["real-run"].ultra.fx, "pen");
  assert.deepEqual([sc.safety.ultra.pose, sc.safety.ultra.fx, sc.safety.once], ["stop", "door", "sneak"]);
  assert.equal(sc.how.once, "pass");
  assert.match(sc.stack.nano.text, /Nebius AI Cloud/);
  assert.match(sc.stack.ultra.text, /Token Factory/);
  assert.deepEqual([sc["closing-cta"].nano.pose, sc["closing-cta"].ultra.pose], ["jump", "jump"]);
  assert.deepEqual([sc.footer.nano.expr, sc.footer.ultra.expr], ["wave", "wave"]);
  assert.deepEqual([scenesMod.routeScene("/"), scenesMod.routeScene("/live"), scenesMod.routeScene("/judges"), scenesMod.routeScene("/scout"), scenesMod.routeScene("/dashboard")], [null, "live", "judges", "scout-page", "dashboard"]);
  for (const t of Object.values(sc)) for (const side of [t.nano, t.ultra]) assert.ok(!side.text || side.text.length <= 16, side.text);
});

test("the section the reader is at: the one across the line a little over a third down, the footer at the foot", () => {
  const tops = [{ id: "rent", top: -900, bottom: -100 }, { id: "catch", top: -100, bottom: 700 }, { id: "how", top: 700, bottom: 1500 }];
  assert.equal(scenesMod.currentSection(tops, 800, false), "catch");
  assert.equal(scenesMod.currentSection([{ id: "rent", top: 500, bottom: 1200 }], 800, false), "hero", "nothing across the line yet: still the top");
  assert.equal(scenesMod.currentSection(tops, 800, true), "footer");
});

test("the new duo moves have their lines: a sneaked answer is blocked, a lesson card levels Nano up", () => {
  assert.match(reactions.DUO_LINES.sneak[1].text, /not yet/i);
  assert.equal(reactions.DUO_LINES.sneak[1].who, "ultra");
  assert.match(reactions.DUO_LINES.levelup[1].text, /level up/i);
  assert.ok(reactions.DUO_LINES.fistbump.length >= 2);
});

// ---------- Nano is learning ----------
test("the chip is the learning meter: Nano shows how many of the seven lessons he has learned", () => {
  const c = { night: false, dark: false, graduated: false, squinting: false };
  assert.equal(model.baseLook("nano", c).learn, 0);
  assert.equal(model.baseLook("nano", { ...c, learn: 3 }).learn, 3);
  assert.equal(model.baseLook("nano", { ...c, learn: 7 }).learn, 7);
  assert.equal(model.REST.learn, 0);
  assert.equal(lessons.LESSON_TOTAL, 7, "one segment per lesson");
});

test("Ultra teaches each key section's one idea on a small card of two to four words", () => {
  const sc = scenesMod.scenes(facts);
  for (const id of ["rent", "how", "learn", "integrate"]) {
    const t = sc[id].ultra;
    assert.equal(t.held, "tag", id);
    const words = t.text.split(/\s+/).length;
    assert.ok(words >= 2 && words <= 4, `${id}: ${t.text}`);
  }
  assert.equal(sc.learn.once, "correct", "Night School shows a guess, a correction and a second try");
  assert.deepEqual([sc.safety.ultra.pose, sc.safety.nano.held ?? "none"], ["stop", "none"], "the gate: Ultra blocks, Nano does not go through");
  assert.equal(sc["real-run"].nano.text, "39%", "the exam score is read from exams.json");
});

test("a guess, a correction and a right second try, kept honest", () => {
  const l = reactions.DUO_LINES.correct;
  assert.deepEqual(l.map((x) => x.who), ["nano", "ultra", "nano", "ultra"]);
  assert.match(l[1].text, /not quite/i);
  assert.match(l[3].text, /matches/i);
  assert.ok(!reactions.CALM_DUOS.includes("sneak") && !reactions.CALM_DUOS.includes("hide"), "the unprompted moments are the calm ones");
  assert.ok(reactions.CALM_DUOS.includes("correct") && reactions.CALM_DUOS.includes("levelup"));
});

test("never more than one prop in view, and a pen or a page turn always has its paper or book", () => {
  const sc = scenesMod.scenes(facts);
  for (const [id, t] of Object.entries(sc)) {
    for (const [who, side] of [["nano", t.nano], ["ultra", t.ultra]]) {
      const look = { ...model.REST, ...side };
      assert.ok(model.propCount(look) <= 1, `${id} ${who}`);
      if (side.fx === "pen") assert.equal(side.held, "papers", `${id} ${who}: a pen needs the paper`);
      if (side.fx === "read") assert.equal(side.held, "book", `${id} ${who}: page turns need the book`);
    }
  }
  assert.equal(model.propCount({ held: "card", fx: "juggle" }), 1, "a solo activity replaces what is held");
  assert.equal(model.propCount({ held: "none", fx: "none" }), 0);
  assert.equal(model.baseLook("ultra", { night: false, dark: false, graduated: false, squinting: false }).held, "none", "no mug at rest by day");
});

test("the finale: the full meter earns a cap, and the words are about the page, not the real student", () => {
  const c = { night: false, dark: false, graduated: true, squinting: false, learn: 7 };
  const look = model.baseLook("nano", c);
  assert.deepEqual([look.cap, look.learn, look.expr], [true, 7, "proud"]);
  assert.equal(model.baseLook("nano", { ...c, graduated: false }).cap, false, "the cap is brief: it comes off");
  const src = readFileSync(new URL("../src/components/companions/stage.tsx", import.meta.url), "utf8");
  assert.match(src, /Learned from this page\. The real student still needs the gate\./);
  assert.doesNotMatch(src, /promoted|now serves|is serving/i);
});

// ---------- Quiz Nano: real exam rows, recorded answers, recorded marks ----------
const quizText = readFileSync(new URL("../src/data/quiz.json", import.meta.url), "utf8");

test("the quiz sample loads, is balanced, and every answer carries its recorded mark", () => {
  const q = quizMod.parseQuiz(quizText);
  assert.equal(q.source, "harshbhx/ratchet-text2sql-exams");
  assert.ok(q.rows.length >= 40 && q.rows.length <= 60, `${q.rows.length} rows`);
  for (const exam of ["exam1", "exam2"]) {
    const mine = q.rows.filter((r) => r.exam === exam);
    assert.ok(mine.length >= 20, exam);
    const right = mine.filter((r) => r.nano.right).length;
    assert.ok(right >= 8 && mine.length - right >= 8, `${exam}: a real mix of right and wrong (${right} of ${mine.length})`);
  }
  assert.equal(new Set(q.rows.map((r) => `${r.exam}:${r.i}`)).size, q.rows.length, "no row twice");
  assert.throws(() => quizMod.parseQuiz('{"rows":[]}'));
  assert.throws(() => quizMod.parseQuiz(JSON.stringify({ rows: [{ exam: "exam1", i: 0, question: "q", reference: "r", nano: { sql: "x" }, ultra: { sql: "x", right: true }, base: { sql: "x", right: true } }] })), "an answer without a recorded mark is refused");
});

test("the marks are the recorded ones: nothing is re-judged", () => {
  const q = quizMod.parseQuiz(quizText);
  for (const r of q.rows) {
    const m = quizMod.markOf(r);
    assert.deepEqual([m.nano, m.ultra, m.base], [r.nano.right, r.ultra.right, r.base.right]);
  }
  const wrong = q.rows.find((r) => !r.nano.right);
  const right = q.rows.find((r) => r.nano.right);
  const w = quizMod.reveal(wrong);
  assert.deepEqual(w.map((b) => b.label), [quizMod.NANO_LABEL, quizMod.ULTRA_LABEL, quizMod.REFERENCE_LABEL]);
  assert.equal(w[1].right, wrong.ultra.right, "Ultra's own recorded answer, with its own recorded mark");
  assert.equal(w[2].sql, wrong.reference);
  assert.equal(quizMod.reveal(right).length, 1, "when Nano was right only his answer shows");
  assert.match(quizMod.verdictLine(true), /right/i);
  assert.match(quizMod.verdictLine(false), /wrong/i);
});

test("the quiz deals every question once before it repeats", () => {
  const q = quizMod.parseQuiz(quizText);
  const p2 = quizMod.quizPicker(q.rows);
  const first = new Set(Array.from({ length: q.rows.length }, () => { const r = p2.next(); return `${r.exam}:${r.i}`; }));
  assert.equal(first.size, q.rows.length);
});

test("the quiz says where its answers come from, and never calls them live", () => {
  assert.match(quizMod.PROVENANCE, /harshbhx\/ratchet-text2sql-exams/);
  assert.match(quizMod.PROVENANCE, /recorded/i);
  assert.match(quizMod.PROVENANCE, /no model was called/i);
  assert.match(quizMod.examNote("exam2"), /evaluation only/);
  assert.match(quizMod.examNote("exam2"), /different lab/);
  assert.match(quizMod.examNote("exam1"), /teacher/);
  assert.match(quizMod.MARK_NOTE, /rows are compared/);
  for (const t of quizMod.quizTexts()) {
    assert.doesNotMatch(t, /demo|simulat/i, t);
    assert.doesNotMatch(t, /answers? (it|him) (gave )?live|just now|in real time|live model/i, t);
  }
});

// ---------- come here ----------
const el = (opts = {}) => ({ tagName: "DIV", closest: () => null, childNodes: [], ...opts });

test("only an empty part of the page calls Nano: never content, a link, a field, text or a call to action", () => {
  assert.equal(comehere.isEmptyTarget(el()), true);
  assert.equal(comehere.isEmptyTarget(null), false);
  assert.equal(comehere.isEmptyTarget(el({ closest: () => ({}) })), false, "inside a link, card or paragraph");
  assert.equal(comehere.isEmptyTarget(el({ childNodes: [{ nodeType: 3, textContent: "  hello " }] })), false, "holds text of its own");
  assert.equal(comehere.isEmptyTarget(el({ childNodes: [{ nodeType: 3, textContent: "  \n " }, { nodeType: 1, textContent: "x" }] })), true, "whitespace and child elements do not count");
  for (const sel of ["a", "button", "input", "textarea", "p", "h2", "li", ".shadow-card", "[data-companion-avoid]", "svg", "img"]) {
    assert.ok(comehere.CONTENT_SELECTOR.includes(sel), sel);
  }
});

test("a click walks him over, a hold makes him follow; a finger needs a long press", () => {
  assert.equal(comehere.pressKind("mouse", 120, 2), "tap");
  assert.equal(comehere.pressKind("mouse", 120, 40), "none", "a drag is not a click");
  assert.equal(comehere.pressKind("mouse", 600, 3), "hold");
  assert.equal(comehere.pressKind("touch", 150, 2), "none", "an ordinary tap is not taken");
  assert.equal(comehere.pressKind("touch", 700, 5), "hold");
  assert.equal(comehere.pressKind("touch", 700, 60), "none", "a scroll is not a long press");
  assert.ok(comehere.holdDelay("touch") > comehere.holdDelay("mouse"));
});

test("he only comes to a place he may stand: on screen, off call-to-action, off text", () => {
  const vp = { w: 1280, h: 800 };
  const none = { cta: [], text: [] };
  assert.equal(comehere.validDestination({ x: 640, y: 500 }, NANO, none, vp), true);
  assert.equal(comehere.validDestination({ x: 640, y: 60 }, NANO, none, vp), false, "under the header");
  assert.equal(comehere.validDestination({ x: 1275, y: 500 }, NANO, none, vp), false, "off the side");
  assert.equal(comehere.validDestination({ x: 640, y: 500 }, NANO, { cta: [{ left: 600, right: 700, top: 420, bottom: 520 }], text: [] }, vp), false);
  assert.equal(comehere.validDestination({ x: 640, y: 500 }, NANO, { cta: [], text: [{ left: 560, right: 720, top: 400, bottom: 510 }] }, vp), false);
  const last = { x: 300, y: 500 };
  assert.deepEqual(comehere.followPoint({ x: 640, y: 500 }, last, NANO, { cta: [{ left: 0, right: 1280, top: 0, bottom: 800 }], text: [] }, vp), last, "a call to action in the way: he stays");
});

// ---------- hide and seek ----------
test("hide and seek: only after a long scroll, once a visit at most, never while busy", () => {
  const ok = { busy: false, roaming: true };
  let st = seekMod.newSeek();
  assert.equal(seekMod.canHide(st, 1e6, ok), false, "not before the reader has scrolled far");
  st = seekMod.addScroll(st, 2000);
  st = seekMod.addScroll(st, -900);
  assert.equal(st.far, 2900);
  assert.equal(seekMod.canHide(st, 1e6, ok), true);
  assert.equal(seekMod.canHide(st, 1e6, { ...ok, busy: true }), false);
  assert.equal(seekMod.canHide(st, 1e6, { ...ok, roaming: false }), false, "reduced motion and phones: no hiding");
  st = seekMod.startHide(st, 1e6);
  assert.deepEqual([st.hides, st.far], [1, 0]);
  assert.equal(seekMod.canHide(seekMod.addScroll(st, 5000), 1e6 + 1000, ok), false, "he is hiding");
  st = seekMod.endHide(st, 1e6 + 30000);
  st = seekMod.addScroll(st, 5000);
  assert.equal(seekMod.canHide(st, 1e6 + 40000, ok), false, "a long gap between hides");
  assert.equal(seekMod.MAX_HIDES, 1);
  assert.equal(st.hides, 1);
  assert.equal(seekMod.canHide(st, 9e6, ok), false, "once is the most: rarer, so it reads as a game");
});

test("the hiding place is behind a card's top edge, with room, clear of text and calls to action", () => {
  const vp = { w: 1280, h: 800 };
  const cards = [card("top", 100, 90, 600), card("narrow", 100, 400, 200), card("a", 100, 400, 1100), card("low", 100, 740, 1100)];
  const rng = () => 0.5;
  const spot = seekMod.hideSpot(cards, vp, NANO, noZones, rng);
  assert.ok(spot && spot.id === "hide:a", "a card with room, well inside the screen");
  assert.ok(spot.x > 150 && spot.x < 1050);
  assert.equal(Math.round(spot.y), Math.round(400 + NANO.h * seekMod.HIDE_CLIP), "his feet are behind the card: only the top of him shows");
  const text = { cta: [], text: [{ left: 0, right: 1280, top: 300, bottom: 400 }] };
  assert.equal(seekMod.hideSpot(cards, vp, NANO, text, rng), null, "text just above the card: nowhere to peek");
  assert.equal(seekMod.hideSpot([], vp, NANO, noZones, rng), null);
  assert.ok(seekMod.HIDE_CLIP > 0.5 && seekMod.HIDE_CLIP < 0.8);
  // Never right under a section's title.
  const underTitle = [card("t", 100, 300, 1100)];
  assert.equal(seekMod.hideSpot(underTitle, vp, NANO, noZones, rng, [240]), null, "a card just under a title");
  assert.ok(seekMod.hideSpot(underTitle, vp, NANO, noZones, rng, [20]), "a title far above does not matter");
});

// ---------- room beside the content ----------
test("the gutter beside the content: Nano and Ultra stand whole in it, drawn a little smaller if it is narrow", () => {
  const vp = { w: 1280, h: 800 };
  const content = [{ left: 96, right: 1184, top: 100, bottom: 700 }];
  const g = perch.gutterOf(vp, content);
  assert.deepEqual(g, { left: 96, right: 96 });
  const n = perch.gutterCandidates("nano", vp, NANO, g);
  assert.ok(n.length > 5 && n.every((c) => c.kind === "gutter" && c.scale >= perch.MIN_FIT));
  assert.ok(n[0].x + (NANO.w * n[0].scale) / 2 <= vp.w - 4, "never clipped by the edge");
  assert.ok(n[0].x - (NANO.w * n[0].scale) / 2 >= 1184, "and clear of the content");
  const u = perch.gutterCandidates("ultra", vp, ULTRA, g);
  assert.ok(u.length > 5 && u[0].scale < 1 && u[0].scale >= perch.MIN_FIT, "Ultra is drawn smaller to fit");
  assert.ok(u[0].x - (ULTRA.w * u[0].scale) / 2 >= 4);
  assert.deepEqual(perch.gutterCandidates("ultra", vp, ULTRA, { left: 40, right: 40 }), [], "a 40px gutter is too narrow: elsewhere");
  assert.deepEqual(perch.gutterOf(vp, []), { left: 0, right: 0 });
  // The gutter is chosen when nothing else is clear, and a gutter perch passes the text test.
  const full = { cta: [], text: [{ left: 96, right: 1184, top: 0, bottom: 800 }] };
  const pick = perch.choosePerch([...n, ...perch.floorCandidates("nano", vp)], "nano", { vp, size: NANO, zones: full });
  assert.equal(pick.cand.kind, "gutter");
});

test("the gaps between blocks of text, and the peek beside a section title", () => {
  const vp = { w: 1280, h: 800 };
  const text = [{ left: 100, right: 700, top: 100, bottom: 300 }, { left: 100, right: 700, top: 420, bottom: 600 }, { left: 100, right: 700, top: 610, bottom: 700 }];
  const gaps = perch.gapCandidates("nano", vp, NANO, text);
  assert.equal(gaps.length, 1, "only the 120px gap is tall enough");
  assert.equal(gaps[0].y, 412);
  assert.ok(gaps[0].kind === "gap" && perch.perchCost(gaps[0], "nano", { vp, size: NANO, zones: { cta: [], text } }) < Infinity);
  const peeks = perch.peekCandidates("nano", { w: 390, h: 844 }, [300, 5]);
  assert.ok(peeks.some((c) => c.id === "peekh:300") && !peeks.some((c) => c.id === "peekh:5"), "beside the title, in view");
  const title = peeks.find((c) => c.id === "peekh:300");
  const plain = { ...title, id: "peek:300" };
  const ctx = { vp: { w: 390, h: 844 }, size: { w: 48, h: 52 }, zones: noZones };
  assert.ok(perch.perchCost(title, "nano", ctx) < perch.perchCost(plain, "nano", ctx), "alongside a title is preferred");
});

// ---------- standing on something, under the header ----------
test("in the gutter he stands at the level of a real edge on the page, not on an arbitrary grid line", () => {
  const vp = { w: 1280, h: 800 };
  const g = { left: 96, right: 96 };
  const edges = [300, 305, 520, 90, 790];
  const c = perch.gutterCandidates("nano", vp, NANO, g, edges);
  assert.deepEqual(c.map((x) => x.y), [300, 520, 790], "near-duplicate edges merge; edges under the header line or off the screen are dropped");
  assert.ok(c.every((x) => x.kind === "gutter"));
  assert.ok(perch.gutterCandidates("nano", vp, NANO, g).length > 5, "with no edge in view he stands on the grid");
});

test("nothing stands above the line under the header: perches and destinations obey the header's real bottom", () => {
  const vp = { w: 1280, h: 800 };
  const cand = { id: "x", kind: "gutter", x: 1232, y: 200 };
  const ctx = { vp, size: NANO, zones: noZones };
  assert.ok(perch.perchCost(cand, "nano", ctx) < Infinity, "default line: 72px");
  assert.equal(perch.perchCost(cand, "nano", { ...ctx, top: 120 }), Infinity, "a taller header pushes the line down: his head would touch it");
  assert.ok(perch.perchCost({ ...cand, y: 240 }, "nano", { ...ctx, top: 120 }) < Infinity);
  assert.equal(comehere.validDestination({ x: 640, y: 150 }, NANO, noZones, vp, 120), false);
  assert.equal(comehere.validDestination({ x: 640, y: 300 }, NANO, noZones, vp, 120), true);
});
