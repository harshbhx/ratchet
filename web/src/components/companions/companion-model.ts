// Nano and Ultra as pure data and functions (no React, no DOM), so `npm test` can import them: the looks, the time-of-day and
// theme rules, and what reduced motion switches off.

export type Who = "nano" | "ultra";
export type Expr = "idle" | "happy" | "thinking" | "proud" | "sleepy" | "surprised" | "laughing" | "determined" | "sad" | "wave";
export type Pose = "stand" | "jump" | "pointLeft" | "pointRight" | "carry" | "stop" | "hug" | "pat" | "sip" | "fold" | "watch";
export type Held = "none" | "book" | "ball" | "card" | "papers" | "mug" | "score" | "tag";
export type Fx = "none" | "juggle" | "read" | "pen" | "watch" | "door" | "yawn";

export const EXPRESSIONS: readonly Expr[] = ["idle", "happy", "thinking", "proud", "sleepy", "surprised", "laughing", "determined", "sad", "wave"];
export const POSES: readonly Pose[] = ["stand", "jump", "pointLeft", "pointRight", "carry", "stop", "hug", "pat", "sip", "fold", "watch"];

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
  pop: "none" | "!" | "?" | "+1" | "sparkle" | "heart";
  /** Ultra's glasses adjust (one short animation). */
  adjust: boolean;
  /** A quick one-off: eyes dart, the smile twitches, the head shakes. */
  micro: "none" | "dart" | "twitch" | "shake";
  /** A little scene prop or activity: juggling SQL tokens, turning book pages, marking a paper, a pocket watch, a door, a yawn. */
  fx: Fx;
  /** Text on a held score card or tag. */
  text: string;
  /** Ultra raises an eyebrow. */
  brow: "none" | "raise";
  /** The adapter chip glows (a level up). */
  glow: boolean;
  /** How many of the seven lessons Nano has learned: the segments lit on his adapter chip. */
  learn: number;
  /** Sunglasses are worn pushed up on the head; down means over the eyes (for a moment, when the theme turns dark). */
  shadesDown: boolean;
}

export const REST: Look = { expr: "idle", pose: "stand", held: "none", sunglasses: false, squint: false, lamp: false, cap: false, blush: false, sweat: false, pop: "none", adjust: false, micro: "none", fx: "none", text: "", brow: "none", glow: false, shadesDown: false, learn: 0 };

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
  /** The sunglasses are down over the eyes for a moment, after the page turns dark. */
  shadesDown?: boolean;
  /** Lessons learned so far (0 to 7). */
  learn?: number;
}

/** How each character looks at rest. Nano: sunglasses in the dark theme, a squint in the light one, a lamp and a book at night. */
export function baseLook(who: Who, c: Context): Look {
  if (who === "nano") {
    return {
      ...REST,
      sunglasses: c.dark,
      shadesDown: c.dark && !!c.shadesDown,
      learn: c.learn ?? 0,
      squint: !c.dark && c.squinting,
      lamp: c.night,
      cap: c.graduated,
      held: c.night ? "book" : "none",
      pose: c.night ? "carry" : "stand",
      expr: c.graduated ? "proud" : "idle",
    };
  }
  return { ...REST, held: c.night ? "papers" : "none", pose: c.night ? "carry" : "stand", expr: c.graduated ? "happy" : "idle" };
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

/** An activity that is its own prop (juggling tokens, a pocket watch, a door) and so replaces whatever is held. */
export function isSoloFx(fx: Fx): boolean {
  return fx === "juggle" || fx === "watch" || fx === "door";
}

/** How many props are in view for a look: never more than one. Pen and page-turning are the held papers and book in use, not extra props. */
export function propCount(look: Pick<Look, "held" | "fx">): number {
  if (isSoloFx(look.fx)) return 1;
  return look.held !== "none" ? 1 : 0;
}

/** The most closed a lid may rest for an expression that is not sleepy: 0 is wide open, 1 is shut. Eyes must read as open. */
export const OPEN_MAX = 0.3;

export interface EyeSpec {
  /** How far the top lid rests down. */
  top: number;
  /** How far the lower lid lifts (a smile). */
  bottom: number;
  pupil: number;
  scale: number;
  dx: number;
  dy: number;
  wet: boolean;
}

/**
 * What the lids and pupils do for each expression. Every expression except sleepy (and the yawn) keeps the eyes open: a top lid at
 * most OPEN_MAX down. Eyes are never drawn as closed arcs, a squint is a brief narrowing and not a slit, and the default is wide open.
 */
export function eyeSpec(expr: Expr, squint: boolean, idx: 0 | 1, ultra: boolean, yawn = false): EyeSpec {
  let s: EyeSpec = { top: 0, bottom: 0, pupil: 2.7, scale: 1, dx: 0, dy: 0, wet: false };
  switch (expr) {
    case "happy":
    case "wave":
      s = { ...s, top: 0.04, bottom: 0.2, pupil: 3.5 };
      break;
    case "proud":
      s = { ...s, top: 0.26, bottom: 0.14, pupil: 3.2 };
      break;
    case "laughing":
      s = { ...s, top: 0.18, bottom: 0.3, pupil: 3.4 };
      break;
    case "sleepy":
      s = { ...s, top: 0.62, dy: 1.2 };
      break;
    case "surprised":
      s = { ...s, scale: 1.14, pupil: 3.7 };
      break;
    case "thinking":
      s = { ...s, top: idx === 1 ? 0.26 : 0.1, dx: 1.8, dy: -1.8 };
      break;
    case "determined":
      s = { ...s, top: 0.24, pupil: 2.5 };
      break;
    case "sad":
      s = { ...s, top: 0.22, dy: 1.2, wet: true };
      break;
    default:
      // Ultra's dry look: the right eye a little skeptical, still clearly open.
      if (ultra && idx === 1) s = { ...s, top: 0.2 };
  }
  if (squint) s = { ...s, top: Math.max(s.top, 0.3), bottom: Math.max(s.bottom, 0.26) };
  if (yawn) s = { ...s, top: 0.7 };
  return s;
}
