// Nano and Ultra as pure data and functions (no React, no DOM), so `npm test` can import them: the looks, the time-of-day and
// theme rules, and what reduced motion switches off.

export type Who = "nano" | "ultra";
export type Expr = "idle" | "happy" | "thinking" | "proud" | "sleepy" | "surprised" | "laughing" | "determined" | "sad" | "wave";
export type Pose = "stand" | "jump" | "pointLeft" | "pointRight" | "carry" | "stop" | "hug" | "pat" | "sip";
export type Held = "none" | "book" | "ball" | "card" | "papers" | "mug";

export const EXPRESSIONS: readonly Expr[] = ["idle", "happy", "thinking", "proud", "sleepy", "surprised", "laughing", "determined", "sad", "wave"];
export const POSES: readonly Pose[] = ["stand", "jump", "pointLeft", "pointRight", "carry", "stop", "hug", "pat", "sip"];

/** Everything that changes how one character is drawn. */
export interface Look {
  expr: Expr;
  pose: Pose;
  held: Held;
  sunglasses: boolean;
  squint: boolean;
  lamp: boolean;
  cap: boolean;
  blush: boolean;
  sweat: boolean;
  /** A small pop-up over the head. */
  pop: "none" | "!" | "?" | "sparkle" | "heart";
  /** Ultra's glasses adjust (one short animation). */
  adjust: boolean;
  /** A quick one-off: eyes dart, the smile twitches, the head shakes. */
  micro: "none" | "dart" | "twitch" | "shake";
}

export const REST: Look = { expr: "idle", pose: "stand", held: "none", sunglasses: false, squint: false, lamp: false, cap: false, blush: false, sweat: false, pop: "none", adjust: false, micro: "none" };

/** Local night is Night School: 20:00 to 05:59. */
export function isNight(d: Date): boolean {
  const h = d.getHours();
  return h >= 20 || h < 6;
}

export interface Context {
  night: boolean;
  dark: boolean;
  graduated: boolean;
  /** Nano squints for a moment after the page turns light. */
  squinting: boolean;
}

/** How each character looks at rest. Nano: sunglasses in the dark theme, a squint in the light one, a lamp and a book at night. */
export function baseLook(who: Who, c: Context): Look {
  if (who === "nano") {
    return {
      ...REST,
      sunglasses: c.dark,
      squint: !c.dark && c.squinting,
      lamp: c.night,
      cap: c.graduated,
      held: c.night ? "book" : "none",
      pose: c.night ? "carry" : "stand",
      expr: c.graduated ? "proud" : "idle",
    };
  }
  return { ...REST, held: c.night ? "papers" : "mug", pose: c.night ? "carry" : "stand", expr: c.graduated ? "happy" : "idle" };
}

export interface MotionPlan {
  /** Blinking, breathing and eyes that follow the pointer. */
  ambient: boolean;
  /** Small moves and banter on a timer. */
  autonomous: boolean;
  /** Drag, throw and spring physics. */
  physics: boolean;
  /** The story scrolls the page by itself. */
  autoScroll: boolean;
  /** The story shows its captions as a stepper the reader advances. */
  stepper: boolean;
}

export function motionPlan(reduced: boolean): MotionPlan {
  return { ambient: !reduced, autonomous: !reduced, physics: !reduced, autoScroll: !reduced, stepper: reduced };
}

/** Where the pupils go: toward the pointer, at most `max` units, and less when it is close. */
export function lookOffset(px: number, py: number, cx: number, cy: number, max: number, soft = 160): { x: number; y: number } {
  const dx = px - cx;
  const dy = py - cy;
  const d = Math.hypot(dx, dy);
  if (d < 1e-6) return { x: 0, y: 0 };
  const k = Math.min(1, d / soft) * max;
  return { x: (dx / d) * k, y: (dy / d) * k };
}

/** True while the reader is typing: banter and reactions wait. */
export function isTyping(el: { tagName?: string; isContentEditable?: boolean } | null | undefined): boolean {
  if (!el) return false;
  const t = (el.tagName ?? "").toUpperCase();
  return t === "INPUT" || t === "TEXTAREA" || t === "SELECT" || !!el.isContentEditable;
}

export const PHONE_MAX = 639;
export function isPhone(width: number): boolean {
  return width <= PHONE_MAX;
}
