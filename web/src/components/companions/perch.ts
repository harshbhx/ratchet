// Where Nano and Ultra stand while the visitor scrolls. A perch is a point on the top edge of a card or a heading (or the floor of the
// viewport) where a character's feet go. Each character picks its own: Nano prefers the right, Ultra the left, both prefer the middle
// of the screen, neither wants to stand on a call to action or on body text, and a current perch is kept unless another is clearly
// better. The component reads the rectangles once per pick; everything here is arithmetic. Pure: tests/companions.test.mjs.
import type { Who } from "./companion-model";
import type { Rect } from "./placement";

export interface Pt {
  x: number;
  y: number;
}
export interface Size {
  w: number;
  h: number;
}
export interface Cand extends Pt {
  id: string;
  kind: "card" | "heading" | "floor" | "peek";
}
export interface Zones {
  /** Calls to action, the story bar: never stood on. */
  cta: Rect[];
  /** Body text: avoided, at a smaller cost. */
  text: Rect[];
}

/** Elements a character can stand on (the top edge), and the text it should stay off. */
export const PERCH_SELECTOR = "[data-companion-perch], main .shadow-card";
export const TEXT_SELECTOR = "main p, main h1, main h2, main h3, main li, main pre, main label, main input, main figcaption, main th, main td, main dd, main blockquote";

/** The rectangle of a character whose feet are at `p`. */
export function charRect(p: Pt, size: Size): Rect {
  return { left: p.x - size.w / 2, right: p.x + size.w / 2, top: p.y - size.h, bottom: p.y };
}

export function overlapArea(a: Rect, b: Rect): number {
  const w = Math.min(a.right, b.right) - Math.max(a.left, b.left);
  const h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
  return w > 0 && h > 0 ? w * h : 0;
}

export interface Item {
  id: string;
  kind: "card" | "heading";
  rect: Rect;
}

/** How far in from a card's corner a character stands. */
export const PERCH_INSET = 32;

/** A point on the top edge of each element: Nano near its right corner, Ultra near its left corner. */
export function candidatesFromItems(items: Item[], who: Who): Cand[] {
  const out: Cand[] = [];
  for (const it of items) {
    const { left, right, top } = it.rect;
    // One consistent offset from the card's corner, so the pair stand on the same grid line down the page.
    const x = who === "nano" ? right - PERCH_INSET : left + PERCH_INSET;
    if (x > left + 20 && x < right - 20) out.push({ id: `${it.id}:0`, kind: it.kind, x, y: top });
  }
  return out;
}

/**
 * Tucked into the gutter: half out of the screen at the edge, only the head and hands in view, leaning in. Always available, so
 * there is always somewhere to be that is not on top of text.
 */
export function peekCandidates(who: Who, vp: { w: number; h: number }): Cand[] {
  const out: Cand[] = [];
  for (let y = 130; y <= vp.h - 10; y += 56) out.push({ id: `peek:${y}`, kind: "peek", x: who === "nano" ? vp.w - 2 : 2, y });
  return out;
}

/** The least-bad edge position when every other perch is taken by text. */
export function bestPeek(who: Who, vp: { w: number; h: number }, size: Size, zones: Zones, prefer?: string): Cand {
  const cands = peekCandidates(who, vp);
  let best = cands[0]!;
  let bestCost = Number.POSITIVE_INFINITY;
  for (const c of cands) {
    const full = charRect(c, size);
    const r = { ...full, left: Math.max(0, full.left), right: Math.min(vp.w, full.right) };
    let cost = Math.abs(c.y - vp.h * 0.6) * 0.05;
    for (const z of zones.cta) cost += overlapArea(r, z);
    for (const z of zones.text) cost += overlapArea(r, z) * 0.2;
    if (c.id === prefer) cost -= 30;
    if (cost < bestCost) {
      bestCost = cost;
      best = c;
    }
  }
  return best;
}

/** Standing on the floor of the viewport, at the side each one prefers. */
export function floorCandidates(who: Who, vp: { w: number; h: number }): Cand[] {
  const y = vp.h - 6;
  const xs = who === "nano" ? [vp.w - 60, vp.w - 150, vp.w * 0.72] : [60, 150, vp.w * 0.28];
  return xs.map((x, i) => ({ id: `floor:${i}`, kind: "floor" as const, x, y }));
}

export interface Ctx {
  vp: { w: number; h: number };
  size: Size;
  zones: Zones;
  /** Where the other character stands (or will), to keep them apart. */
  other?: Pt | null;
  /** The id of the perch held now. */
  current?: string;
  /** The top of the usable viewport (below the header). */
  top?: number;
  /** Where on the screen (0 top, 1 bottom) this one would like to stand; varies by section so the two move about. */
  prefRatio?: number;
}

/**
 * What standing on a candidate costs: distance from the middle of the screen, the wrong side, crowding. A candidate that would put the
 * character on a call to action (more than 2% of its area) or on body text (more than 6%; for a peek, where only the head and hands
 * show, 20%) is not a candidate at all: they never stand on what the visitor is reading.
 */
export function perchCost(c: Cand, who: Who, ctx: Ctx): number {
  const { vp, size, zones } = ctx;
  const full = charRect(c, size);
  const peek = c.kind === "peek";
  const top = ctx.top ?? 72;
  if (full.top < top || full.bottom > vp.h) return Number.POSITIVE_INFINITY;
  if (!peek && (full.left < 4 || full.right > vp.w - 4)) return Number.POSITIVE_INFINITY;
  const r = { ...full, left: Math.max(0, full.left), right: Math.min(vp.w, full.right) };
  const area = Math.max(1, (r.right - r.left) * (r.bottom - r.top));
  let cta = 0;
  let txt = 0;
  for (const z of zones.cta) cta += overlapArea(r, z);
  for (const z of zones.text) txt += overlapArea(r, z);
  if (cta / area > 0.02 || txt / area > (peek ? 0.2 : 0.06)) return Number.POSITIVE_INFINITY;
  let cost = cta * 0.5 + txt * 0.2;
  const prefY = vp.h * (ctx.prefRatio ?? (who === "nano" ? 0.64 : 0.46));
  cost += Math.abs(c.y - prefY) * 0.3;
  cost += who === "nano" ? Math.max(0, vp.w * 0.55 - c.x) * 0.1 : Math.max(0, c.x - vp.w * 0.45) * 0.1;
  if (ctx.other && Math.abs(c.x - ctx.other.x) < size.w * 1.1 && Math.abs(c.y - ctx.other.y) < size.h) cost += 60;
  if (c.kind === "floor") cost += 25;
  if (peek) cost += 35;
  if (ctx.current && c.id === ctx.current) cost -= 40;
  return cost;
}

/** The cheapest candidate, or null when none fits the viewport. `pick` (0 to 1) takes a later one of the best three, for wandering. */
export function choosePerch(cands: Cand[], who: Who, ctx: Ctx, pick = 0): { cand: Cand; cost: number; textOverlap: number } | null {
  const scored = cands
    .map((c) => ({ cand: c, cost: perchCost(c, who, ctx) }))
    .filter((s) => Number.isFinite(s.cost))
    .sort((a, b) => a.cost - b.cost);
  if (!scored.length) return null;
  const best = scored[Math.min(scored.length - 1, Math.floor(pick * Math.min(3, scored.length)))]!;
  const r = charRect(best.cand, ctx.size);
  const textOverlap = ctx.zones.text.reduce((n, z) => n + overlapArea(r, z), 0) + ctx.zones.cta.reduce((n, z) => n + overlapArea(r, z), 0);
  return { ...best, textOverlap };
}

/** At the very top and at the foot of the page the two stand together, to greet and to wave. */
export function gathers(scrollY: number, vh: number, docH: number): boolean {
  return scrollY < 80 || scrollY + vh >= docH - 40;
}

/** The pair of floor points for gathering: Nano nearer the edge, Ultra beside him, on the clearest side; null when every side would stand on text. */
export function gatherPair(vp: { w: number; h: number }, size: { nano: Size; ultra: Size }, zones: Zones): { nano: Pt; ultra: Pt } | null {
  const y = vp.h - 6;
  const options = [
    { nano: { x: vp.w - 56, y }, ultra: { x: vp.w - 56 - 100, y } },
    { nano: { x: 56 + size.ultra.w, y }, ultra: { x: 56, y } },
    { nano: { x: vp.w * 0.5 + 50, y }, ultra: { x: vp.w * 0.5 - 50, y } },
  ];
  let best: { nano: Pt; ultra: Pt } | null = null;
  let bestCost = Number.POSITIVE_INFINITY;
  for (const o of options) {
    let cost = 0;
    let ok = true;
    for (const r of [charRect(o.nano, size.nano), charRect(o.ultra, size.ultra)]) {
      if (r.left < 4 || r.right > vp.w - 4) ok = false;
      const area = (r.right - r.left) * (r.bottom - r.top);
      let cta = 0;
      let txt = 0;
      for (const z of zones.cta) cta += overlapArea(r, z);
      for (const z of zones.text) txt += overlapArea(r, z);
      if (cta / area > 0.02 || txt / area > 0.06) ok = false;
      cost += cta * 0.5 + txt * 0.2;
    }
    if (ok && cost < bestCost - 1) {
      bestCost = cost;
      best = o;
    }
  }
  return best;
}

/** Following the content: a perch fixed to the page moves up the screen by exactly the distance scrolled. */
export function followY(y0: number, scroll0: number, scrollNow: number): number {
  return y0 - (scrollNow - scroll0);
}

/** A perch that has slid out of the comfortable band (under the header, or off the bottom) needs a new one. */
export function needsRepick(y: number, vh: number, top = 90, bottom = 12): boolean {
  return y < top || y > vh - bottom;
}

export type Move = "walk" | "jump" | "glide";
export interface TravelPlan {
  mode: Move;
  ms: number;
  arc: number;
}

/** How to get from one perch to the next: Nano walks short steps and jumps far or when the page is flying by; Ultra glides, calmly. */
export function travelPlan(from: Pt, to: Pt, who: Who, fast: boolean): TravelPlan {
  const dist = Math.hypot(to.x - from.x, to.y - from.y);
  const dy = Math.abs(to.y - from.y);
  const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
  if (who === "ultra") return { mode: "glide", ms: clamp((dist / 240) * 1000, 650, 1700), arc: 0 };
  if (fast || dy > 120 || dist > 360) return { mode: "jump", ms: clamp(380 + dist * 0.5, 420, 900), arc: clamp(dist * 0.25 + 30, 40, 140) };
  return { mode: "walk", ms: clamp((dist / 150) * 1000, 500, 2200), arc: 0 };
}

/** A different preferred height for each section, so the pair do not stand in the same place all the way down the page. */
export function prefRatio(who: Who, sceneIndex: number): number {
  const frac = (v: number) => v - Math.floor(v);
  return who === "nano" ? 0.3 + 0.45 * frac(sceneIndex * 0.381966) : 0.72 - 0.4 * frac(sceneIndex * 0.618034);
}
