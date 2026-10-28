// "Come here": a click on an empty part of the page, or a press and hold, calls Nano over. Empty means the page itself: not a link, a
// button, a field, a card, text, a picture or a call to action. The page's own clicks are never touched (the listener only watches);
// this decides whether a press counts, and whether Nano may stand where it points. Pure: tests/companions.test.mjs.
import { charRect, overlapArea, type Size, type Zones } from "./perch";

/** Anything the page uses: a press on one of these is the page's, not a call to Nano. */
export const CONTENT_SELECTOR =
  "a,button,input,textarea,select,label,summary,option,[role='button'],[role='link'],[role='dialog'],[contenteditable],[data-companion-avoid],p,h1,h2,h3,h4,h5,h6,li,pre,code,td,th,dt,dd,figure,figcaption,blockquote,svg,img,video,canvas,iframe,nav,header,footer,.shadow-card,.cp-root";

export interface ElLike {
  tagName: string;
  closest(selector: string): unknown;
  childNodes?: ArrayLike<{ nodeType: number; textContent: string | null }>;
}

/** True when the pressed element is just the page: it is not content and holds no text of its own. */
export function isEmptyTarget(el: ElLike | null | undefined): boolean {
  if (!el) return false;
  if (el.closest(CONTENT_SELECTOR)) return false;
  const nodes = el.childNodes ?? [];
  for (let i = 0; i < nodes.length; i++) {
    const n = nodes[i]!;
    if (n.nodeType === 3 && (n.textContent ?? "").trim()) return false;
  }
  return true;
}

export const TAP_MAX_MS = 300;
export const TAP_MAX_MOVE = 8;
export const HOLD_MS = 380;
export const TOUCH_HOLD_MS = 520;

export type Press = "tap" | "hold" | "none";

/**
 * What a press is, from how long it lasted and how far it moved. A mouse: a quick click walks Nano over, a press held a moment makes
 * him follow. A finger: only a long press counts, so scrolling and ordinary taps are never taken.
 */
export function pressKind(pointerType: string, downMs: number, movedPx: number): Press {
  if (pointerType === "touch" || pointerType === "pen") return downMs >= TOUCH_HOLD_MS && movedPx < 24 ? "hold" : "none";
  if (downMs >= HOLD_MS) return "hold";
  return downMs <= TAP_MAX_MS && movedPx <= TAP_MAX_MOVE ? "tap" : "none";
}

/** Whether the hold timer should start for this press (a finger needs time to tell a scroll from a long press). */
export function holdDelay(pointerType: string): number {
  return pointerType === "touch" || pointerType === "pen" ? TOUCH_HOLD_MS : HOLD_MS;
}

/** May Nano stand with his feet at `p`? Inside the screen below the header, off every call to action and off body text. */
export function validDestination(p: { x: number; y: number }, size: Size, zones: Zones, vp: { w: number; h: number }, top = 72): boolean {
  const r = charRect(p, size);
  if (r.top < top || r.bottom > vp.h || r.left < 4 || r.right > vp.w - 4) return false;
  const area = size.w * size.h;
  let cta = 0;
  let txt = 0;
  for (const z of zones.cta) cta += overlapArea(r, z);
  for (const z of zones.text) txt += overlapArea(r, z);
  return cta / area <= 0.02 && txt / area <= 0.06;
}

/** Where he stands while following: beside the pointer like a pet at heel (left of it, else right, else just below), else where he was. */
export function followPoint(p: { x: number; y: number }, last: { x: number; y: number }, size: Size, zones: Zones, vp: { w: number; h: number }) {
  const only = { cta: zones.cta, text: [] };
  for (const at of [
    { x: p.x - size.w * 0.7, y: p.y + size.h * 0.55 },
    { x: p.x + size.w * 0.7, y: p.y + size.h * 0.55 },
    { x: p.x, y: p.y + size.h * 0.95 },
  ]) {
    if (validDestination(at, size, only, vp)) return at;
  }
  return last;
}
