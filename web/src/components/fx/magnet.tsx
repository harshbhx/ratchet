import { useRef, type PointerEvent, type ReactNode } from "react";
import { motion, useMotionValue, useReducedMotion, useSpring } from "motion/react";
import { cn } from "@/lib/utils";

/**
 * A wrapper that leans a little toward the pointer when it comes near, and springs back when it leaves (after the
 * idea of React Bits' Magnet; original code on `motion` springs). A few pixels at most, mouse only, and nothing
 * under reduced motion: the control underneath is the same button either way.
 */
export function Magnet({ children, className, strength = 0.35, range = 90 }: { children: ReactNode; className?: string; strength?: number; range?: number }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const reduce = useReducedMotion();
  const x = useMotionValue(0);
  const y = useMotionValue(0);
  const sx = useSpring(x, { stiffness: 220, damping: 18, mass: 0.6 });
  const sy = useSpring(y, { stiffness: 220, damping: 18, mass: 0.6 });

  const onMove = (e: PointerEvent<HTMLDivElement>) => {
    if (reduce || e.pointerType !== "mouse" || !ref.current) return;
    const r = ref.current.getBoundingClientRect();
    const dx = e.clientX - (r.left + r.width / 2);
    const dy = e.clientY - (r.top + r.height / 2);
    const d = Math.hypot(dx, dy);
    const pull = d < range ? strength : 0;
    x.set(dx * pull);
    y.set(dy * pull);
  };
  const reset = () => {
    x.set(0);
    y.set(0);
  };
  return (
    <div ref={ref} onPointerMove={onMove} onPointerLeave={reset} className={cn("inline-block", className)}>
      <motion.div style={{ x: sx, y: sy }}>{children}</motion.div>
    </div>
  );
}
