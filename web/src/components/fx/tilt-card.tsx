import { useRef, type PointerEvent, type ReactNode } from "react";
import { motion, useMotionValue, useReducedMotion, useSpring } from "motion/react";
import { cn } from "@/lib/utils";

/**
 * A card that tilts a few degrees toward the pointer, in perspective, and settles back on a spring (after the idea
 * of React Bits' TiltedCard; original code). Mouse only, at most `max` degrees, nothing under reduced motion, and
 * the content is unchanged: it is the same card, seen from a slightly different angle.
 */
export function TiltCard({ children, className, max = 6, scale = 1.012 }: { children: ReactNode; className?: string; max?: number; scale?: number }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const reduce = useReducedMotion();
  const rx = useMotionValue(0);
  const ry = useMotionValue(0);
  const sc = useMotionValue(1);
  const spring = { stiffness: 180, damping: 22, mass: 0.8 };
  const srx = useSpring(rx, spring);
  const sry = useSpring(ry, spring);
  const ssc = useSpring(sc, spring);

  const onMove = (e: PointerEvent<HTMLDivElement>) => {
    if (reduce || e.pointerType !== "mouse" || !ref.current) return;
    const r = ref.current.getBoundingClientRect();
    const px = (e.clientX - r.left) / r.width - 0.5;
    const py = (e.clientY - r.top) / r.height - 0.5;
    ry.set(px * 2 * max);
    rx.set(-py * 2 * max);
    sc.set(scale);
  };
  const reset = () => {
    rx.set(0);
    ry.set(0);
    sc.set(1);
  };
  return (
    <div ref={ref} onPointerMove={onMove} onPointerLeave={reset} className={cn("[perspective:1100px]", className)}>
      <motion.div style={{ rotateX: srx, rotateY: sry, scale: ssc, transformStyle: "preserve-3d" }} className="h-full will-change-transform">
        {children}
      </motion.div>
    </div>
  );
}
