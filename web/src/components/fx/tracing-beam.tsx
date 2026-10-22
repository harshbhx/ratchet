import { useEffect, useRef, useState, type ReactNode } from "react";
import { motion, useReducedMotion, useScroll, useSpring, useTransform } from "motion/react";
import { cn } from "@/lib/utils";

/**
 * A line down the left of a narrative that lights up as far as the reader has scrolled (ported from Aceternity UI's
 * Tracing Beam, MIT; adapted: the story's colours, a ResizeObserver so the line follows the content's height, and the
 * whole line lit under reduced motion). The lit part is an SVG gradient whose ends are motion values on a spring, so
 * it trails the scroll a little.
 */
export function TracingBeam({
  children,
  className,
  lineClassName,
  contentClassName = "pl-10 sm:pl-12",
}: {
  children: ReactNode;
  className?: string;
  lineClassName?: string;
  contentClassName?: string;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  const contentRef = useRef<HTMLDivElement | null>(null);
  const [height, setHeight] = useState(0);
  const reduce = useReducedMotion();
  const { scrollYProgress } = useScroll({ target: ref, offset: ["start 0.6", "end 0.5"] });

  useEffect(() => {
    const el = contentRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setHeight(el.offsetHeight));
    ro.observe(el);
    setHeight(el.offsetHeight);
    return () => ro.disconnect();
  }, []);

  const y1 = useSpring(useTransform(scrollYProgress, [0, 0.85], [24, height]), { stiffness: 420, damping: 80 });
  const y2 = useSpring(useTransform(scrollYProgress, [0, 1], [24, Math.max(24, height - 160)]), { stiffness: 420, damping: 80 });
  const headTop = useTransform(y1, (v) => Math.max(0, Math.min(height, v)) - 6);

  return (
    <div ref={ref} className={cn("relative", className)}>
      <div aria-hidden className={cn("absolute inset-y-0 left-0 w-5", lineClassName)}>
        <svg viewBox={`0 0 20 ${Math.max(height, 1)}`} width="20" height={Math.max(height, 1)} className="block" aria-hidden>
          <path d={`M 10 0 V ${height}`} fill="none" stroke="var(--line-strong)" strokeWidth="1.5" />
          {reduce ? (
            <path d={`M 10 0 V ${height}`} fill="none" stroke="var(--owned)" strokeWidth="2" />
          ) : (
            <>
              <path d={`M 10 0 V ${height}`} fill="none" stroke="url(#tracing-beam-gradient)" strokeWidth="3" strokeLinecap="round" />
              <defs>
                <motion.linearGradient id="tracing-beam-gradient" gradientUnits="userSpaceOnUse" x1="0" x2="0" y1={y1} y2={y2}>
                  <stop stopColor="var(--rented)" stopOpacity="0" />
                  <stop stopColor="var(--rented)" />
                  <stop offset="0.4" stopColor="var(--owned)" />
                  <stop offset="1" stopColor="var(--owned)" stopOpacity="0" />
                </motion.linearGradient>
              </defs>
            </>
          )}
        </svg>
        {!reduce && (
          <motion.span
            className="absolute left-1/2 size-3 -translate-x-1/2 rounded-full border-2 border-owned bg-surface shadow-[0_0_0_4px_var(--owned-soft)]"
            style={{ top: headTop }}
          />
        )}
      </div>
      <div ref={contentRef} className={contentClassName}>
        {children}
      </div>
    </div>
  );
}
