// How fast the visitor is scrolling, from a few recent samples. Pure: tests/companions.test.mjs.

export interface Sample {
  t: number;
  y: number;
}

/** Scroll speed above which Nano jumps to keep up (px per second). */
export const FAST = 1600;
/** Scroll speed under which the page counts as still. */
export const STILL = 40;

/** Keep the samples of the last `windowMs`. */
export function pushSample(samples: Sample[], t: number, y: number, windowMs = 140): Sample[] {
  const next = samples.filter((s) => t - s.t <= windowMs);
  next.push({ t, y });
  return next;
}

/** Px per second between the oldest and the newest sample (absolute). */
export function velocity(samples: Sample[]): number {
  if (samples.length < 2) return 0;
  const a = samples[0]!;
  const b = samples[samples.length - 1]!;
  const dt = b.t - a.t;
  return dt > 0 ? (Math.abs(b.y - a.y) / dt) * 1000 : 0;
}

export type ScrollMode = "still" | "slow" | "fast";
export function scrollMode(v: number): ScrollMode {
  return v >= FAST ? "fast" : v <= STILL ? "still" : "slow";
}

/** Nano jumps again only when the page is flying by and he has had time to land (at least `gapMs` since the last jump). */
export function shouldAutoJump(v: number, sinceLastJumpMs: number, gapMs = 450): boolean {
  return v >= FAST && sinceLastJumpMs >= gapMs;
}

/** How often to look for a new perch: Nano keeps up fast, Ultra rarely. */
export function repickEveryMs(who: "nano" | "ultra", v: number): number {
  if (v >= FAST) return who === "nano" ? 380 : 900;
  return who === "nano" ? 700 : 1100;
}
