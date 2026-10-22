import { useEffect, useRef, type ReactNode } from "react";
import { Link } from "react-router";
import { motion, useMotionTemplate, useMotionValue, useReducedMotion, useTransform } from "motion/react";
import { cn } from "@/lib/utils";
import { useOnScreen } from "@/lib/visibility";

/**
 * A button whose border carries one point of light around it (ported from Aceternity UI's Moving Border, MIT;
 * adapted: a router link, the story's two colours, an animation frame only while the button is on screen and the tab
 * is visible, and a still gradient ring under reduced motion). The light rides an invisible SVG rect's path, so it
 * follows the rounded corners exactly.
 */
function Light({ duration = 3600, rx, ry }: { duration?: number; rx: string; ry: string }) {
  const pathRef = useRef<SVGRectElement | null>(null);
  const hostRef = useRef<SVGSVGElement | null>(null);
  const on = useOnScreen(hostRef);
  const reduce = useReducedMotion();
  const progress = useMotionValue(0);
  // Our own frame loop, started only while the button is on screen (motion's `useAnimationFrame` would keep the
  // page's frame loop alive for good). The light picks up where it stopped.
  useEffect(() => {
    if (!on || reduce) return;
    let raf = 0;
    let start = 0;
    const from = progress.get();
    const tick = (time: number) => {
      if (!start) start = time;
      const length = pathRef.current?.getTotalLength();
      if (length) progress.set((from + ((time - start) * length) / duration) % length);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [on, reduce, duration, progress]);
  const x = useTransform(progress, (v) => pathRef.current?.getPointAtLength(v).x ?? 0);
  const y = useTransform(progress, (v) => pathRef.current?.getPointAtLength(v).y ?? 0);
  const transform = useMotionTemplate`translateX(${x}px) translateY(${y}px) translateX(-50%) translateY(-50%)`;
  return (
    <>
      <svg ref={hostRef} preserveAspectRatio="none" className="absolute size-full" width="100%" height="100%" aria-hidden>
        <rect ref={pathRef} fill="none" width="100%" height="100%" rx={rx} ry={ry} />
      </svg>
      {!reduce && (
        <motion.span aria-hidden className="absolute left-0 top-0 inline-block" style={{ transform }}>
          <span className="block size-20 rounded-full bg-[radial-gradient(var(--owned)_30%,transparent_62%)] opacity-90 dark:opacity-80" />
        </motion.span>
      )}
    </>
  );
}

export function MovingBorderLink({
  to,
  children,
  className,
  containerClassName,
  duration,
}: {
  to: string;
  children: ReactNode;
  className?: string;
  containerClassName?: string;
  duration?: number;
}) {
  return (
    <Link
      to={to}
      className={cn(
        "group/mb relative inline-flex h-12 overflow-hidden rounded-full bg-transparent p-px transition-transform duration-200 active:scale-[0.98]",
        containerClassName,
      )}
    >
      {/* the ring: a still gradient (the whole ring under reduced motion), with the light on top */}
      <span aria-hidden className="absolute inset-0 rounded-full bg-[linear-gradient(135deg,var(--owned),var(--rented))] opacity-50 motion-reduce:opacity-80" />
      <span aria-hidden className="absolute inset-0 rounded-full">
        <Light duration={duration} rx="50%" ry="50%" />
      </span>
      <span
        className={cn(
          "relative inline-flex h-full w-full items-center justify-center gap-2 whitespace-nowrap rounded-full bg-ink px-6 text-[15px] font-medium text-bg transition-colors group-hover/mb:bg-ink-2 [&_svg]:size-4 [&_svg]:shrink-0",
          className,
        )}
      >
        {children}
      </span>
    </Link>
  );
}
