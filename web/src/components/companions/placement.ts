// Where the shelf (or, on a phone, the dock) stands so it never covers a primary call to action. The component measures the
// elements marked as avoid zones and asks `chooseSlot`; the shelf then moves by transform only. Pure: tests/companions.test.mjs.

export interface Rect {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

/** Elements the characters keep clear of: the hero buttons, the live request form, the closing call to action. */
export const AVOID_SELECTOR = "[data-companion-avoid], #closing-cta a, #closing-cta button, a.btn-shine, button[aria-label=\"Copy the changed line\"]";

export function intersects(a: Rect, b: Rect, pad = 0): boolean {
  return a.left < b.right + pad && a.right > b.left - pad && a.top < b.bottom + pad && a.bottom > b.top - pad;
}

export interface Slot {
  side: "right" | "left";
  /** Pixels the shelf is raised from the bottom edge. */
  lift: number;
  /** False when no candidate was clear (the shelf stays at its first place). */
  clear: boolean;
}

export interface Viewport {
  w: number;
  h: number;
}

/** The rectangle a slot occupies. `edge` is the gap to the viewport's side and bottom. */
export function slotRect(vp: Viewport, size: { w: number; h: number }, slot: Pick<Slot, "side" | "lift">, edge = 12): Rect {
  const left = slot.side === "right" ? vp.w - edge - size.w : edge;
  const bottom = vp.h - edge - slot.lift;
  return { left, right: left + size.w, bottom, top: bottom - size.h };
}

/**
 * The first slot that is clear of every zone: bottom right, bottom left, then both raised in steps. `prefer` (the current slot)
 * is kept while it is still clear, so the shelf does not jump back and forth while the page scrolls.
 */
export function chooseSlot(vp: Viewport, size: { w: number; h: number }, zones: Rect[], prefer?: Slot, pad = 10, step = 72, maxLift = 288): Slot {
  const clear = (s: Pick<Slot, "side" | "lift">) => {
    const r = slotRect(vp, size, s);
    return r.top >= 0 && !zones.some((z) => intersects(r, z, pad));
  };
  if (prefer && clear(prefer)) return { ...prefer, clear: true };
  for (let lift = 0; lift <= maxLift; lift += step) {
    for (const side of ["right", "left"] as const) if (clear({ side, lift })) return { side, lift, clear: true };
  }
  return { side: "right", lift: 0, clear: false };
}
