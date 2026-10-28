// The throw, in numbers. The component hands the same stiffness and damping to motion's spring; `springRun` runs the same equation
// step by step so a test can check that every throw settles, and that a hard throw overshoots (the bounce) before it rests.
// Pure: tests/companions.test.mjs.

/** Spring constants for the fall back to the shelf and the slide home (mass 1). */
export const SPRING = { stiffness: 170, damping: 11, mass: 1 } as const;
export const SLIDE = { stiffness: 80, damping: 17, mass: 1 } as const;

export interface Sim {
  /** Seconds until the position stays within `eps` of the target. */
  settleS: number;
  /** How many times it crossed the target. */
  crossings: number;
  /** The largest distance past the target. */
  peak: number;
  settled: boolean;
}

/** A damped spring from `x0` with velocity `v0` toward 0, in fixed steps (semi-implicit Euler). */
export function springRun(x0: number, v0: number, k = SPRING.stiffness, c = SPRING.damping, dt = 1 / 240, eps = 0.5, maxS = 8): Sim {
  let x = x0;
  let v = v0;
  let t = 0;
  let crossings = 0;
  let peak = 0;
  let lastOutside = 0;
  let prevSign = Math.sign(x0) || 1;
  while (t < maxS) {
    v += (-k * x - c * v) * dt;
    x += v * dt;
    t += dt;
    const s = Math.sign(x);
    if (s !== 0 && s !== prevSign) {
      crossings++;
      prevSign = s;
    }
    if (Math.sign(x) !== Math.sign(x0 || 1)) peak = Math.max(peak, Math.abs(x));
    if (Math.abs(x) > eps || Math.abs(v) > eps * 4) lastOutside = t;
  }
  return { settleS: lastOutside, crossings, peak, settled: lastOutside < maxS - 0.5 };
}

export interface Bounds {
  left: number;
  right: number;
  top: number;
  bottom: number;
}

/** How far a dragged character may go from its place: it stays inside the viewport with a margin. */
export function dragBounds(rect: { left: number; right: number; top: number; bottom: number }, vw: number, vh: number, margin = 8): Bounds {
  return { left: margin - rect.left, right: vw - margin - rect.right, top: margin - rect.top, bottom: Math.max(0, vh - margin - rect.bottom) };
}

/** Clamp a point to bounds. */
export function clampTo(x: number, y: number, b: Bounds): { x: number; y: number } {
  return { x: Math.min(b.right, Math.max(b.left, x)), y: Math.min(b.bottom, Math.max(b.top, y)) };
}

/** Squash and stretch for a landing at `speed` px/s: harder landings flatten more, never past 0.7. */
export function landSquash(speed: number): { sx: number; sy: number } {
  const k = Math.min(1, Math.max(0, speed) / 1800);
  const sy = 1 - 0.3 * k;
  return { sx: 1 + 0.32 * k, sy };
}

/** Stretch while in the air: faster means taller and thinner. */
export function airStretch(speed: number): { sx: number; sy: number } {
  const k = Math.min(1, Math.abs(speed) / 2200);
  return { sx: 1 - 0.12 * k, sy: 1 + 0.18 * k };
}

/** Five pokes within the window is a lot of pokes. */
export function createBurst(count = 5, windowMs = 3000): { push(t: number): boolean } {
  let times: number[] = [];
  return {
    push(t) {
      times = times.filter((x) => t - x <= windowMs);
      times.push(t);
      if (times.length >= count) {
        times = [];
        return true;
      }
      return false;
    },
  };
}
