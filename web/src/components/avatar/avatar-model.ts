// The avatars' behaviour as pure functions (no React, no DOM), so a test can import them: which expression follows an event,
// and how far the eyes may move toward the pointer.

export type Expression = "idle" | "happy" | "thinking" | "proud" | "sleepy" | "surprised" | "wave";
export type Who = "nano" | "ultra";

export const EXPRESSIONS: readonly Expression[] = ["idle", "happy", "thinking", "proud", "sleepy", "surprised", "wave"];

/** What happens in the tour or on the page that an avatar reacts to. */
export type AvatarEvent =
  | { type: "reset" }
  | { type: "greet" }
  | { type: "send" }
  | { type: "answer" }
  | { type: "limit" }
  | { type: "error" }
  | { type: "scores" };

/** The expression after an event. Every event sets one; `reset` goes back to resting. */
export function nextExpression(_current: Expression, event: AvatarEvent): Expression {
  switch (event.type) {
    case "greet":
      return "wave";
    case "send":
      return "thinking";
    case "answer":
      return "happy";
    case "limit":
      return "sleepy";
    case "error":
      return "surprised";
    case "scores":
      return "proud";
    case "reset":
      return "idle";
  }
}

/**
 * Where the pupils go: from the avatar's centre toward the pointer, at most `max` units away, and less when the pointer is close
 * (so it does not jitter on the avatar itself). Returns whole-unit-free numbers in [-max, max].
 */
export function lookOffset(px: number, py: number, cx: number, cy: number, max: number, soft = 160): { x: number; y: number } {
  const dx = px - cx;
  const dy = py - cy;
  const d = Math.hypot(dx, dy);
  if (d < 1e-6) return { x: 0, y: 0 };
  const k = Math.min(1, d / soft) * max;
  return { x: (dx / d) * k, y: (dy / d) * k };
}

/** What the theme does to the face: a quick squint for the switch, then sunglasses only in the dark theme. */
export function themeLook(dark: boolean, squinting: boolean): { squint: boolean; sunglasses: boolean } {
  return { squint: squinting, sunglasses: dark && !squinting };
}
