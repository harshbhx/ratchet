// Hide and seek. Now and then, after the reader has scrolled a long way, Nano ducks behind the top edge of a card with only his antenna
// and the top of his head showing. Finding him (a click) is a lesson. At most twice a visit, with a long gap between, and never while
// something else is going on. The scheduler and the choice of a hiding place are arithmetic; the page does the rest. Pure.
import { charRect, overlapArea, type Cand, type Item, type Size, type Zones } from "./perch";

export const MAX_HIDES = 2;
export const MIN_GAP_MS = 90_000;
/** How far the reader must have scrolled (px, either way) since the last hide before another can start. */
export const FAR_PX = 2800;
/** He comes out by himself after this long. */
export const HIDE_MS = 25_000;
/** The share of his height that is behind the card; the rest (the antenna and the top of his head) shows. */
export const HIDE_CLIP = 0.66;

export interface SeekState {
  hides: number;
  /** Scrolling since the last hide, in px. */
  far: number;
  lastEnd: number;
  hidingUntil: number;
}

export const newSeek = (): SeekState => ({ hides: 0, far: 0, lastEnd: -Infinity, hidingUntil: 0 });

export function addScroll(s: SeekState, dy: number): SeekState {
  return { ...s, far: s.far + Math.abs(dy) };
}

export interface Ctx {
  /** Something else is going on: talking, the sheet, the story, a drag, typing, a fast scroll. */
  busy: boolean;
  /** Roaming is on (not reduced motion) and it is a desktop: on a phone there is no room to hide. */
  roaming: boolean;
}

export function canHide(s: SeekState, now: number, c: Ctx): boolean {
  return c.roaming && !c.busy && s.hides < MAX_HIDES && s.far >= FAR_PX && now - s.lastEnd >= MIN_GAP_MS && now >= s.hidingUntil;
}

export function startHide(s: SeekState, now: number): SeekState {
  return { ...s, hides: s.hides + 1, far: 0, hidingUntil: now + HIDE_MS };
}

export function endHide(s: SeekState, now: number): SeekState {
  return { ...s, lastEnd: now, hidingUntil: 0 };
}

/**
 * Where to hide: behind the top edge of a card in view, a little in from its corners. His feet are `HIDE_CLIP` of his height below the
 * card's top edge, so the part that shows is above it. The place must be clear of text and calls to action. `rng` picks among the fits.
 */
export function hideSpot(items: Item[], vp: { w: number; h: number }, size: Size, zones: Zones, rng: () => number = Math.random): Cand | null {
  const fits: Cand[] = [];
  for (const it of items) {
    const { left, right, top } = it.rect;
    if (top < 150 || top > vp.h - 110 || right - left < size.w + 100) continue;
    const x = left + 50 + rng() * (right - left - 100);
    const c: Cand = { id: `hide:${it.id}`, kind: "card", x, y: top + size.h * HIDE_CLIP };
    // Only the part above the card shows; that part must not stand on text or a call to action.
    const shown = { ...charRect(c, size), bottom: top };
    const area = Math.max(1, (shown.right - shown.left) * (shown.bottom - shown.top));
    const cta = zones.cta.reduce((n, z) => n + overlapArea(shown, z), 0);
    const txt = zones.text.reduce((n, z) => n + overlapArea(shown, z), 0);
    if (cta / area <= 0.02 && txt / area <= 0.06) fits.push(c);
  }
  return fits.length ? fits[Math.floor(rng() * fits.length)]! : null;
}
