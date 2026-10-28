import { useCallback, useEffect, useMemo, useRef } from "react";
import { animate, useMotionValue, useTransform, type MotionValue } from "motion/react";
import type { Actor } from "./actor";
import type { Look, Who } from "./companion-model";
import { PERCH_SELECTOR, TEXT_SELECTOR, candidatesFromItems, choosePerch, floorCandidates, gatherPair, gathers, needsRepick, overlapArea, charRect, travelPlan, type Item, type Move, type Pt, type Size, type Zones } from "./perch";
import { AVOID_SELECTOR, type Rect } from "./placement";
import { FAST, pushSample, repickEveryMs, shouldAutoJump, velocity, type Sample } from "./scroll";

/** One character's place on the screen: a base point (its perch, following the page) plus an offset that carries it from one perch to the next. */
export interface Roamer {
  bx: MotionValue<number>;
  by: MotionValue<number>;
  ox: MotionValue<number>;
  oy: MotionValue<number>;
  /** Where the feet are, in viewport pixels. */
  px: MotionValue<number>;
  py: MotionValue<number>;
  /** Degrees of lean while travelling. */
  lean: MotionValue<number>;
}

export function useRoamer(): Roamer {
  const bx = useMotionValue(-200);
  const by = useMotionValue(-200);
  const ox = useMotionValue(0);
  const oy = useMotionValue(0);
  const lean = useMotionValue(0);
  const px = useTransform([bx, ox], ([a, b]: number[]) => (a ?? 0) + (b ?? 0));
  const py = useTransform([by, oy], ([a, b]: number[]) => (a ?? 0) + (b ?? 0));
  return useMemo(() => ({ bx, by, ox, oy, px, py, lean }), [bx, by, ox, oy, px, py, lean]);
}

interface Anchor {
  id: string;
  x: number;
  y: number;
  sy0: number;
  follow: boolean;
}

export interface RoamingOpts {
  enabled: boolean;
  phone: boolean;
  /** On a phone only this one walks; the other is in the dock. */
  active: Who;
  sizes: Record<Who, Size>;
  actors: Record<Who, Actor>;
  roamers: Record<Who, Roamer>;
  /** Extra no-go rectangles (the story's caption bar). */
  extraZones: () => Rect[];
  onMove: (who: Who, m: Move | null) => void;
  onLook: (who: Who, patch: Partial<Look>, ms?: number) => void;
  onVisible: (who: Who, v: boolean) => void;
  /** True while something (the sheet, a drag) means they should hold still. */
  hold: () => boolean;
}

const ids = new WeakMap<Element, number>();
let nextId = 1;
const idOf = (el: Element) => {
  let n = ids.get(el);
  if (!n) ids.set(el, (n = nextId++));
  return n;
};

/**
 * Nano and Ultra moving through the page. Each stands on its own perch, a point on the top edge of a card or a heading, which
 * travels up the screen with the content as the visitor scrolls. Every so often (and when the perch slides out of the comfortable
 * band, and when scrolling stops) each looks for a better one and gets there: Nano walks, or jumps when it is far or the page is
 * flying by; Ultra glides. At the very top and at the foot of the page they stand together. The rectangles are read once per pick,
 * scroll is handled once per frame, and everything that moves is a transform.
 */
export function useRoaming(o: RoamingOpts) {
  const O = useRef(o);
  O.current = o;
  const anchors = useRef<Record<Who, Anchor | null>>({ nano: null, ultra: null });
  const travel = useRef<Record<Who, { stop(): void } | null>>({ nano: null, ultra: null });
  const lastPick = useRef<Record<Who, number>>({ nano: 0, ultra: 0 });
  const lastJump = useRef(0);
  const meeting = useRef(false);
  const token = useRef<Record<Who, number>>({ nano: 0, ultra: 0 });

  const setBase = useCallback((who: Who, sy: number) => {
    const a = anchors.current[who];
    if (!a) return;
    const r = O.current.roamers[who];
    r.bx.set(a.x);
    // Following the page, but never off the screen: in a fast scroll he rides the edge until he jumps to a perch that is in view.
    r.by.set(a.follow ? Math.min(window.innerHeight - 8, Math.max(100, a.y - (sy - a.sy0))) : a.y);
  }, []);

  const apply = useCallback(
    (who: Who, cand: { id: string; x: number; y: number; kind: string }, fast: boolean, sy: number, vp: { w: number; h: number }) => {
      const opt = O.current;
      const r = opt.roamers[who];
      const prev = anchors.current[who];
      const next: Anchor = { id: cand.id, x: cand.x, y: cand.y, sy0: sy, follow: cand.kind !== "floor" };
      if (prev && prev.id === cand.id) {
        // The same perch, re-measured: keep it where it is, no walk.
        const ox0 = r.bx.get();
        const oy0 = r.by.get();
        anchors.current[who] = next;
        setBase(who, sy);
        r.ox.set(r.ox.get() + (ox0 - r.bx.get()));
        r.oy.set(r.oy.get() + (oy0 - r.by.get()));
        return;
      }
      const first = !prev;
      const from: Pt = first ? { x: who === "nano" ? vp.w + 70 : -70, y: vp.h - 6 } : { x: r.px.get(), y: r.py.get() };
      anchors.current[who] = next;
      setBase(who, sy);
      const to: Pt = { x: r.bx.get(), y: r.by.get() };
      const dx = from.x - to.x;
      const dy = from.y - to.y;
      travel.current[who]?.stop();
      const tok = ++token.current[who];
      meeting.current = who === "ultra" ? false : meeting.current;
      const plan = travelPlan(from, to, who, fast);
      r.ox.set(dx);
      r.oy.set(dy);
      const act = opt.actors[who];
      opt.onMove(who, plan.mode);
      const dir = -Math.sign(dx) || 1;
      const go = async () => {
        if (plan.mode === "jump") {
          // Anticipation: a crouch, then up.
          opt.onLook(who, fast ? { pose: "jump", expr: "surprised", sweat: true } : { pose: "jump", expr: "happy" }, plan.ms + 200);
          await Promise.all([animate(act.sy, 0.8, { duration: 0.12 }), animate(act.sx, 1.12, { duration: 0.12 })]);
          animate(act.sy, 1.12, { duration: 0.2 });
          animate(act.sx, 0.93, { duration: 0.2 });
        }
        r.lean.set(plan.mode === "jump" ? 0 : dir * 6);
        await new Promise<void>((done) => {
          travel.current[who] = animate(0, 1, {
            duration: plan.ms / 1000,
            ease: plan.mode === "glide" ? [0.45, 0, 0.25, 1] : "easeInOut",
            onUpdate: (t: number) => {
              r.ox.set(dx * (1 - t));
              r.oy.set(dy * (1 - t) - plan.arc * 4 * t * (1 - t));
              if (plan.mode === "jump") r.lean.set(dir * 10 * Math.sin(Math.PI * t));
            },
            onComplete: () => done(),
          });
          // when stopped by a newer travel, `done` is never called by motion: resolve on a timer too.
          window.setTimeout(done, plan.ms + 80);
        });
        if (token.current[who] !== tok) return;
        r.ox.set(0);
        r.oy.set(0);
        animate(r.lean, 0, { type: "spring", stiffness: 260, damping: 14 });
        opt.onMove(who, null);
        if (plan.mode === "jump") await act.squash(fast ? 1500 : 900);
      };
      void go();
    },
    [setBase],
  );

  const pick = useCallback(
    (whos: Who[], fast: boolean, wander?: Who) => {
      const opt = O.current;
      if (!opt.enabled || opt.hold() || document.hidden) return;
      const vp = { w: window.innerWidth, h: window.innerHeight };
      const sy = window.scrollY;
      const now = performance.now();
      // Read every rectangle once.
      const items: Item[] = [];
      for (const el of Array.from(document.querySelectorAll(PERCH_SELECTOR)).slice(0, 300)) {
        const r = el.getBoundingClientRect();
        if (r.bottom < 0 || r.top > vp.h || r.width < 110 || r.height < 20) continue;
        items.push({ id: `p${idOf(el)}`, kind: el.tagName === "H2" ? "heading" : "card", rect: { left: r.left, right: r.right, top: r.top, bottom: r.bottom } });
      }
      const cta: Rect[] = Array.from(document.querySelectorAll(AVOID_SELECTOR))
        .map((e) => e.getBoundingClientRect())
        .filter((r) => r.width > 0 && r.bottom > 0 && r.top < vp.h)
        .map((r) => ({ left: r.left, right: r.right, top: r.top, bottom: r.bottom }));
      cta.push(...opt.extraZones());
      const text: Rect[] = [];
      for (const el of Array.from(document.querySelectorAll(TEXT_SELECTOR)).slice(0, 500)) {
        const r = el.getBoundingClientRect();
        if (r.bottom < 0 || r.top > vp.h || r.width < 8 || r.height > 420) continue;
        text.push({ left: r.left, right: r.right, top: r.top, bottom: r.bottom });
      }
      const zones: Zones = { cta, text };
      const docH = document.documentElement.scrollHeight;
      for (const w of whos) lastPick.current[w] = now;

      if (!opt.phone && gathers(sy, vp.h, docH)) {
        const pair = gatherPair(vp, { nano: opt.sizes.nano, ultra: opt.sizes.ultra }, zones);
        for (const w of whos) apply(w, { id: `gather:${w}:${Math.round(pair[w].x)}`, x: pair[w].x, y: pair[w].y, kind: "floor" }, fast, sy, vp);
        for (const w of whos) opt.onVisible(w, true);
        return;
      }
      const order: Who[] = whos.includes("ultra") ? ["ultra", "nano"] : ["nano", "ultra"];
      const placed: Partial<Record<Who, Pt>> = {};
      for (const w of order) {
        if (!whos.includes(w)) continue;
        const other = w === "nano" ? "ultra" : "nano";
        const otherPt = placed[other] ?? (anchors.current[other] ? { x: opt.roamers[other].px.get(), y: opt.roamers[other].py.get() } : null);
        const cands = [...candidatesFromItems(items, w), ...floorCandidates(w, vp)];
        const best = choosePerch(cands, w, { vp, size: opt.sizes[w], zones, other: opt.phone ? null : otherPt, current: anchors.current[w]?.id }, wander === w ? 0.9 : 0);
        if (!best) {
          opt.onVisible(w, false);
          continue;
        }
        // On a phone nothing may stand on text: if the best place still overlaps it, they stay in the dock.
        const r = charRect(best.cand, opt.sizes[w]);
        const overlap = text.reduce((n, z) => n + overlapArea(r, z), 0);
        const hide = opt.phone && overlap > opt.sizes[w].w * opt.sizes[w].h * 0.12;
        opt.onVisible(w, !hide);
        if (hide) continue;
        placed[w] = best.cand;
        apply(w, best.cand, fast && w === "nano", sy, vp);
      }
    },
    [apply],
  );

  // Scroll, resize and a slow heartbeat.
  useEffect(() => {
    if (!o.enabled) return;
    let raf = 0;
    let settle = 0;
    let samples: Sample[] = [];
    const active = (): Who[] => (O.current.phone ? [O.current.active] : ["nano", "ultra"]);
    const frame = () => {
      raf = 0;
      const sy = window.scrollY;
      const now = performance.now();
      samples = pushSample(samples, now, sy);
      const v = velocity(samples);
      const vh = window.innerHeight;
      const whos: Who[] = [];
      for (const w of active()) {
        const a = anchors.current[w];
        if (a) setBase(w, sy);
        const stale = !a || (a.follow && needsRepick(a.y - (sy - a.sy0), vh));
        if (stale || now - lastPick.current[w] >= repickEveryMs(w, v)) whos.push(w);
      }
      if (meeting.current && v > 120) release();
      // Nano jumps to keep up with a fast scroll, but only once he has landed from the last jump.
      const keepUp = whos.includes("nano") && v >= FAST;
      const list = keepUp && !shouldAutoJump(v, now - lastJump.current) ? whos.filter((w) => w !== "nano") : whos;
      if (keepUp && list.includes("nano")) lastJump.current = now;
      if (list.length) pick(list, v >= FAST);
      window.clearTimeout(settle);
      settle = window.setTimeout(() => {
        pick(active(), false);
        // Scrolling stopped: they look around.
        for (const w of active()) if (Math.random() < 0.7) O.current.onLook(w, { micro: "dart" }, 900);
      }, 280);
    };
    const onScroll = () => {
      if (!raf) raf = requestAnimationFrame(frame);
    };
    const onResize = () => {
      window.clearTimeout(settle);
      settle = window.setTimeout(() => pick(active(), false), 250);
    };
    const beat = window.setInterval(() => pick(active(), false), 2500);
    const first = window.setTimeout(() => pick(active(), false), 500);
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onResize);
    document.addEventListener("visibilitychange", onResize);
    return () => {
      cancelAnimationFrame(raf);
      window.clearTimeout(settle);
      window.clearTimeout(first);
      window.clearInterval(beat);
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onResize);
      document.removeEventListener("visibilitychange", onResize);
    };
    // release is stable and defined below
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [o.enabled, o.phone, o.active, pick, setBase]);

  /** Ultra walks over to stand beside Nano to talk. */
  const meet = useCallback(() => {
    const opt = O.current;
    if (!opt.enabled || opt.phone || opt.hold()) return;
    const n = opt.roamers.nano;
    const u = opt.roamers.ultra;
    if (!anchors.current.nano || !anchors.current.ultra) return;
    const side = n.px.get() > window.innerWidth / 2 ? -1 : 1;
    const tx = n.px.get() + side * (opt.sizes.nano.w / 2 + opt.sizes.ultra.w / 2 + 6) - u.bx.get();
    const ty = n.py.get() - u.by.get();
    const ox0 = u.ox.get();
    const oy0 = u.oy.get();
    meeting.current = true;
    token.current.ultra++;
    travel.current.ultra?.stop();
    opt.onMove("ultra", "glide");
    travel.current.ultra = animate(0, 1, {
      duration: 1,
      ease: [0.45, 0, 0.25, 1],
      onUpdate: (t: number) => {
        u.ox.set(ox0 + (tx - ox0) * t);
        u.oy.set(oy0 + (ty - oy0) * t);
      },
      onComplete: () => O.current.onMove("ultra", null),
    });
  }, []);

  /** ...and back to its own perch. */
  const release = useCallback(() => {
    if (!meeting.current) return;
    meeting.current = false;
    const u = O.current.roamers.ultra;
    const ox0 = u.ox.get();
    const oy0 = u.oy.get();
    token.current.ultra++;
    travel.current.ultra?.stop();
    O.current.onMove("ultra", "glide");
    travel.current.ultra = animate(0, 1, {
      duration: 0.9,
      ease: [0.45, 0, 0.25, 1],
      onUpdate: (t: number) => {
        u.ox.set(ox0 * (1 - t));
        u.oy.set(oy0 * (1 - t));
      },
      onComplete: () => O.current.onMove("ultra", null),
    });
  }, []);

  const repick = useCallback(() => pick(O.current.phone ? [O.current.active] : ["nano", "ultra"], false), [pick]);
  const wander = useCallback((who: Who) => pick([who], false, who), [pick]);
  return { meet, release, repick, wander };
}
