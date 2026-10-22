import { memo, useRef } from "react";
import { motion, useReducedMotion } from "motion/react";
import { cn } from "@/lib/utils";
import { useOnScreen } from "@/lib/visibility";

/**
 * A fan of thin curved lines with light running along them (ported from Aceternity UI's Background Beams, MIT;
 * adapted: 18 lines instead of 50, a fixed set of timings instead of random ones so the build is reproducible, the
 * story's colours, the lights only while the section is on screen and the tab is visible, and still lines under
 * reduced motion). It sits behind the closing call to action.
 */
const COUNT = 18;
const PATHS = Array.from({ length: COUNT }, (_, i) => {
  const o = i * 14;
  return `M${-380 + o} ${-189 - o * 0.6}C${-380 + o} ${-189 - o * 0.6} ${-312 + o} ${216 - o * 0.6} ${152 + o} ${343 - o * 0.6}C${616 + o} ${470 - o * 0.6} ${684 + o} ${875 - o * 0.6} ${684 + o} ${875 - o * 0.6}`;
});
// Deterministic "random" timings: golden-ratio steps, so neighbours never move together.
const TIMING = Array.from({ length: COUNT }, (_, i) => ({
  duration: 12 + ((i * 0.618) % 1) * 9,
  delay: ((i * 0.382) % 1) * 9,
  end: 93 + ((i * 0.236) % 1) * 8,
}));

export const BackgroundBeams = memo(function BackgroundBeams({ className }: { className?: string }) {
  const ref = useRef<SVGSVGElement | null>(null);
  const on = useOnScreen(ref);
  const reduce = useReducedMotion();
  const live = on && !reduce;
  return (
    <div aria-hidden className={cn("pointer-events-none absolute inset-0 overflow-hidden", className)}>
      <svg ref={ref} className="absolute inset-0 size-full" viewBox="0 0 696 316" preserveAspectRatio="xMidYMid slice" fill="none">
        {PATHS.map((d, i) => (
          <path key={`p-${i}`} d={d} stroke="var(--line-strong)" strokeOpacity="0.5" strokeWidth="0.6" />
        ))}
        {live &&
          PATHS.map((d, i) => <motion.path key={`l-${i}`} d={d} stroke={`url(#beam-${i})`} strokeOpacity="0.8" strokeWidth="0.9" />)}
        <defs>
          {live &&
            PATHS.map((_, i) => (
              <motion.linearGradient
                id={`beam-${i}`}
                key={`g-${i}`}
                initial={{ x1: "0%", x2: "0%", y1: "0%", y2: "0%" }}
                animate={{ x1: ["0%", "100%"], x2: ["0%", "95%"], y1: ["0%", "100%"], y2: ["0%", `${TIMING[i].end}%`] }}
                transition={{ duration: TIMING[i].duration, ease: "easeInOut", repeat: Infinity, delay: TIMING[i].delay }}
              >
                <stop stopColor="var(--owned)" stopOpacity="0" />
                <stop stopColor="var(--owned)" />
                <stop offset="32.5%" stopColor="var(--rented)" />
                <stop offset="100%" stopColor="var(--rented)" stopOpacity="0" />
              </motion.linearGradient>
            ))}
        </defs>
      </svg>
    </div>
  );
});
