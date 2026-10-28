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
  kind: "card" | "heading" | "floor";
}
export interface Zones {
  /** Calls to action, the story bar: never stood on. */
  cta: Rect[];
  /** Body text: avoided, at a smaller cost. */
  text: Rect[];
}

/** Elements a character can stand on (the top edge), and the text it should stay off. */
export const PERCH_SELECTOR = "[data-companion-perch], main section h2, main .shadow-card";
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

/** Points on the top edge of each element: Nano near its right end, Ultra near its left end (two each, for wide elements). */
export function candidatesFromItems(items: Item[], who: Who): Cand[] {
  const out: Cand[] = [];
  for (const it of items) {
    const { left, right, top } = it.rect;
    const xs = who === "nano" ? [right - 36, right - 190] : [left + 40, left + 200];
    xs.forEach((x, i) => {
      if (x > left + 20 && x < right - 20) out.push({ id: `${it.id}:${i}`, kind: it.kind, x, y: top });
    });
  }
  return out;
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
}

/** What standing on a candidate costs: overlap with CTAs and text, distance from the middle of the screen, the wrong side, crowding. */
export function perchCost(c: Cand, who: Who, ctx: Ctx): number {
  const { vp, size, zones } = ctx;
  const r = charRect(c, size);
  const top = ctx.top ?? 72;
  if (r.top < top || r.bottom > vp.h || r.left < 4 || r.right > vp.w - 4) return Number.POSITIVE_INFINITY;
  let cost = 0;
  for (const z of zones.cta) cost += overlapArea(r, z) * 0.5;
  for (const z of zones.text) cost += overlapArea(r, z) * 0.02;
  const prefY = vp.h * (who === "nano" ? 0.64 : 0.46);
  cost += Math.abs(c.y - prefY) * 0.3;
  cost += who === "nano" ? Math.max(0, vp.w * 0.55 - c.x) * 0.1 : Math.max(0, c.x - vp.w * 0.45) * 0.1;
  if (ctx.other && Math.abs(c.x - ctx.other.x) < size.w * 1.1 && Math.abs(c.y - ctx.other.y) < size.h) cost += 60;
  if (c.kind === "floor") cost += 25;
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

/** The pair of floor points for gathering: Nano nearer the edge, Ultra beside him, on the clearest side. */
export function gatherPair(vp: { w: number; h: number }, size: { nano: Size; ultra: Size }, zones: Zones): { nano: Pt; ultra: Pt } {
  const y = vp.h - 6;
  const options = [
    { nano: { x: vp.w - 56, y }, ultra: { x: vp.w - 56 - 100, y } },
    { nano: { x: 56 + size.ultra.w, y }, ultra: { x: 56, y } },
    { nano: { x: vp.w * 0.5 + 50, y }, ultra: { x: vp.w * 0.5 - 50, y } },
  ];
  let best = options[0]!;
  let bestCost = Number.POSITIVE_INFINITY;
  for (const o of options) {
    const rs = [charRect(o.nano, size.nano), charRect(o.ultra, size.ultra)];
    let cost = 0;
    for (const r of rs) {
      for (const z of zones.cta) cost += overlapArea(r, z) * 0.5;
      for (const z of zones.text) cost += overlapArea(r, z) * 0.02;
    }
    if (cost < bestCost - 1) {
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
