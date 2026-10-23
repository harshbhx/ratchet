/**
 * The guided tour's geometry and step order, pure (no DOM, no JSX): tested by `tests/tour-model.test.mjs`.
 * The tour itself (`tour.tsx`) measures the page and hands the numbers here.
 */

export interface Box {
  top: number;
  left: number;
  width: number;
  height: number;
}

export interface Size {
  width: number;
  height: number;
}

/** One stop: the first selector that matches an element is the target; a step with none is skipped. */
export interface TourStep {
  targets: string[];
  title: string;
  body: string;
}

/** Below this viewport width the popover docks to the bottom of the screen. */
export const NARROW_PX = 640;
/** Space between the target and its outline. */
export const SPOT_PAD = 8;
/** Space kept between the popover and the screen's edges and the spotlight. */
export const GAP = 12;

/**
 * The spotlight's box: the target grown by `pad`, cut to the part of the viewport below `topInset` (the sticky header)
 * and above `bottomInset` (a docked popover). null when no part of the target is on screen.
 */
export function spotlight(target: Box, view: Size, pad = SPOT_PAD, topInset = 0, bottomInset = 0): Box | null {
  const top = Math.max(target.top - pad, topInset + 4);
  const left = Math.max(target.left - pad, 4);
  const bottom = Math.min(target.top + target.height + pad, view.height - bottomInset - 4);
  const right = Math.min(target.left + target.width + pad, view.width - 4);
  if (bottom - top < 8 || right - left < 8) return null;
  return { top, left, width: right - left, height: bottom - top };
}

export type Placement =
  | { mode: "dock" }
  | { mode: "float"; top: number; left: number; side: "below" | "above" | "right" | "left" | "over" };

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(Math.max(v, lo), Math.max(lo, hi));
}

/**
 * Where the popover goes: docked to the bottom on a narrow screen; else below the spotlight, above it, to its right or
 * left, whichever fits first; else over the spotlight's lower right corner, inside the screen.
 */
export function placePopover(spot: Box | null, pop: Size, view: Size, narrow = view.width < NARROW_PX, topInset = 0): Placement {
  if (narrow) return { mode: "dock" };
  const minTop = topInset + GAP;
  const maxLeft = view.width - pop.width - GAP;
  const maxTop = view.height - pop.height - GAP;
  if (!spot) return { mode: "float", top: clamp((view.height - pop.height) / 2, minTop, maxTop), left: clamp((view.width - pop.width) / 2, GAP, maxLeft), side: "over" };
  const centerLeft = clamp(spot.left + spot.width / 2 - pop.width / 2, GAP, maxLeft);
  const centerTop = clamp(spot.top + spot.height / 2 - pop.height / 2, minTop, maxTop);
  const below = spot.top + spot.height + GAP;
  if (below + pop.height <= view.height - GAP) return { mode: "float", top: below, left: centerLeft, side: "below" };
  const above = spot.top - GAP - pop.height;
  if (above >= minTop) return { mode: "float", top: above, left: centerLeft, side: "above" };
  const right = spot.left + spot.width + GAP;
  if (right + pop.width <= view.width - GAP) return { mode: "float", top: centerTop, left: right, side: "right" };
  const left = spot.left - GAP - pop.width;
  if (left >= GAP) return { mode: "float", top: centerTop, left, side: "left" };
  return { mode: "float", top: maxTop, left: clamp(spot.left + spot.width - pop.width - GAP, GAP, maxLeft), side: "over" };
}

/**
 * The scroll position that brings a target into view. With `popHeight` (a floating popover): a target that leaves room
 * for the popover below it starts just under the header, so the popover sits under it. Otherwise a target that fits is
 * centred in the space between the header and the docked popover (or the bottom); a taller one starts under the header.
 */
export function scrollTarget(target: Box, scrollY: number, view: Size, topInset: number, bottomInset: number, popHeight = 0): number {
  const room = view.height - topInset - bottomInset;
  const docTop = scrollY + target.top;
  const under = docTop - topInset - SPOT_PAD - 8;
  let y: number;
  if (popHeight > 0 && target.height + 2 * SPOT_PAD + 2 * GAP + popHeight <= room) y = under;
  else if (target.height + 2 * SPOT_PAD <= room) y = docTop - topInset - (room - target.height) / 2;
  else y = under;
  return Math.max(0, Math.round(y));
}

/**
 * The next step to show from `from`, moving by `dir`, skipping steps `exists` says have no target. null when none is
 * left that way.
 */
export function nextStep(count: number, from: number, dir: 1 | -1, exists: (i: number) => boolean): number | null {
  for (let i = from + dir; i >= 0 && i < count; i += dir) if (exists(i)) return i;
  return null;
}

/** "Step 2 of 7: The ledger", for the live region. */
export function announce(index: number, count: number, title: string): string {
  return `Step ${index + 1} of ${count}: ${title}`;
}

/** Whether `search` (a location's query string) asks for the tour. */
export function wantsTour(search: string): boolean {
  const q = search.startsWith("?") ? search.slice(1) : search;
  return q.split("&").some((p) => p === "tour=1" || p === "tour=true" || p === "tour");
}
