/**
 * The motion system, in one place: one ease, three durations and the reveal variants every section uses, so the page
 * moves as one thing. Every animation here is either a one-time reveal, a response to the reader, or an ambient
 * loop that pauses off screen; `MotionConfig reducedMotion="user"` (main.tsx) and the CSS rule in globals.css hold
 * all of it to the final state under reduced motion.
 */
export const EASE = [0.22, 1, 0.36, 1] as const;

export const DUR = { fast: 0.25, base: 0.55, slow: 0.85 } as const;

/** A fade and a short rise, for anything that enters when scrolled to. */
export const rise = (delay = 0, y = 18) => ({
  initial: { opacity: 0, y },
  whileInView: { opacity: 1, y: 0 },
  viewport: { once: true, margin: "0px 0px -10% 0px" },
  transition: { duration: DUR.base + 0.1, ease: EASE, delay },
});

/** The same, but on mount (the hero, which is on screen before anything scrolls). */
export const enter = (delay = 0, y = 16) => ({
  initial: { opacity: 0, y },
  animate: { opacity: 1, y: 0 },
  transition: { duration: DUR.slow, ease: EASE, delay },
});

/** Parent and child variants for a staggered list. */
export const list = {
  parent: { hidden: {}, show: { transition: { staggerChildren: 0.07, delayChildren: 0.05 } } },
  child: { hidden: { opacity: 0, y: 14 }, show: { opacity: 1, y: 0, transition: { duration: DUR.base, ease: EASE } } },
} as const;
