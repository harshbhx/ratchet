// The animated theme switch: the circle covers the viewport, and without the View Transitions API or with reduced motion the
// theme changes at once. `npm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { REVEAL_CLASS, revealKeyframes, revealRadius, switchWithReveal } from "../src/lib/theme-transition.ts";

function fakeDoc(withApi) {
  const classes = new Set();
  const animations = [];
  const doc = {
    documentElement: {
      classList: { add: (c) => classes.add(c), remove: (c) => classes.delete(c) },
      animate: (k, o) => animations.push({ k, o }),
    },
  };
  if (withApi) {
    doc.startViewTransition = (cb) => {
      cb();
      return { ready: Promise.resolve(), finished: Promise.resolve() };
    };
  }
  return { doc, classes, animations };
}

test("the radius reaches the farthest corner", () => {
  assert.equal(revealRadius(0, 0, 300, 400), 500);
  assert.equal(revealRadius(300, 400, 300, 400), 500);
  assert.equal(revealRadius(150, 200, 300, 400), 250);
  for (const [x, y] of [[10, 10], [290, 30], [150, 390], [0, 400]]) {
    const r = revealRadius(x, y, 300, 400);
    for (const [cx, cy] of [[0, 0], [300, 0], [0, 400], [300, 400]]) assert.ok(Math.hypot(cx - x, cy - y) <= r + 1e-9);
  }
});

test("the keyframes grow a circle from the toggle's centre", () => {
  const k = revealKeyframes(100, 50, 1000, 800);
  assert.equal(k.clipPath[0], "circle(0px at 100px 50px)");
  assert.match(k.clipPath[1], /^circle\([\d.]+px at 100px 50px\)$/);
});

test("without startViewTransition the theme changes at once", async () => {
  const { doc, classes } = fakeDoc(false);
  let n = 0;
  assert.equal(switchWithReveal(() => n++, 10, 10, { doc, reducedMotion: false, width: 300, height: 400 }), false);
  assert.equal(n, 1);
  assert.equal(classes.size, 0);
});

test("with reduced motion the theme changes at once even when the API exists", () => {
  const { doc, animations } = fakeDoc(true);
  let n = 0;
  assert.equal(switchWithReveal(() => n++, 10, 10, { doc, reducedMotion: true, width: 300, height: 400 }), false);
  assert.equal(n, 1);
  assert.equal(animations.length, 0);
});

test("with the API the change runs once inside the transition and the circle animates", async () => {
  const { doc, classes, animations } = fakeDoc(true);
  let n = 0;
  assert.equal(switchWithReveal(() => n++, 10, 20, { doc, reducedMotion: false, width: 300, height: 400 }), true);
  assert.equal(n, 1);
  assert.ok(classes.has(REVEAL_CLASS));
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(animations.length, 1);
  assert.equal(animations[0].o.pseudoElement, "::view-transition-new(root)");
  assert.equal(animations[0].o.duration, 500);
  assert.equal(classes.has(REVEAL_CLASS), false);
});
