// The animated theme switch: the new theme spreads from the toggle as a growing circle (View Transitions API, same document only).
// No JSX and no React here, so a test can import it. Without `document.startViewTransition`, or with reduced motion, the theme
// switches at once. This is not a cross-document `@view-transition`: page navigation is untouched.

/** How long the circle takes to cover the page. */
export const REVEAL_MS = 500;
/** Set on <html> while a switch runs; the CSS that disables the default cross-fade is scoped to it. */
export const REVEAL_CLASS = "theme-reveal";

/** Radius of the smallest circle centred on (x, y) that covers a w by h viewport: the distance to the farthest corner. */
export function revealRadius(x: number, y: number, w: number, h: number): number {
  return Math.hypot(Math.max(x, w - x), Math.max(y, h - y));
}

/** The circle's keyframes, from nothing to the whole viewport. */
export function revealKeyframes(x: number, y: number, w: number, h: number): { clipPath: [string, string] } {
  const r = revealRadius(x, y, w, h);
  return { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${r}px at ${x}px ${y}px)`] };
}

export interface RevealDeps {
  doc: {
    documentElement: { classList: { add(c: string): void; remove(c: string): void }; animate?: (k: unknown, o: unknown) => unknown };
    startViewTransition?: (cb: () => void) => { ready: Promise<unknown>; finished: Promise<unknown> };
  };
  reducedMotion: boolean;
  width: number;
  height: number;
}

/**
 * Run `apply` (which must change the theme synchronously). Returns true when the circle animation runs, false when the change was
 * made at once (no API, or reduced motion). `x` and `y` are the toggle's centre in viewport pixels.
 */
export function switchWithReveal(apply: () => void, x: number, y: number, deps: RevealDeps): boolean {
  const { doc, reducedMotion, width, height } = deps;
  if (reducedMotion || typeof doc.startViewTransition !== "function") {
    apply();
    return false;
  }
  const root = doc.documentElement;
  root.classList.add(REVEAL_CLASS);
  const t = doc.startViewTransition(apply);
  void t.ready
    .then(() => {
      root.animate?.(revealKeyframes(x, y, width, height), {
        duration: REVEAL_MS,
        easing: "cubic-bezier(0.22, 1, 0.36, 1)",
        pseudoElement: "::view-transition-new(root)",
      });
    })
    .catch(() => {
      /* the transition was skipped: the theme has changed anyway */
    });
  void t.finished.then(
    () => root.classList.remove(REVEAL_CLASS),
    () => root.classList.remove(REVEAL_CLASS),
  );
  return true;
}
