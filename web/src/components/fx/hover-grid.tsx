import { useState, type ReactNode } from "react";
import { AnimatePresence, motion } from "motion/react";
import { cn } from "@/lib/utils";

/**
 * A grid whose hover background slides from cell to cell instead of fading in and out on each (ported from
 * Aceternity UI's Card Hover Effect, MIT; adapted: any cell content, a list element, the story's tokens, and the
 * slide replaced by a plain fade under reduced motion through the global MotionConfig). Keyboard focus inside a cell
 * moves the background too.
 */
export function HoverGrid<T>({
  items,
  render,
  keyOf,
  className,
  cellClassName,
}: {
  items: T[];
  render: (item: T, index: number) => ReactNode;
  keyOf: (item: T) => string;
  className?: string;
  cellClassName?: string;
}) {
  const [hovered, setHovered] = useState<number | null>(null);
  return (
    <ul className={cn("grid grid-cols-1 gap-1 sm:grid-cols-2 lg:grid-cols-4", className)} onMouseLeave={() => setHovered(null)}>
      {items.map((it, i) => (
        <li
          key={keyOf(it)}
          className={cn("group/cell relative", cellClassName)}
          onMouseEnter={() => setHovered(i)}
          onFocus={() => setHovered(i)}
          onBlur={() => setHovered((h) => (h === i ? null : h))}
        >
          <AnimatePresence>
            {hovered === i && (
              <motion.span
                aria-hidden
                layoutId="hover-grid-bg"
                className="absolute inset-0 block rounded-2xl bg-surface-2"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1, transition: { duration: 0.15 } }}
                exit={{ opacity: 0, transition: { duration: 0.15, delay: 0.2 } }}
              />
            )}
          </AnimatePresence>
          <div className="relative z-10 h-full">{render(it, i)}</div>
        </li>
      ))}
    </ul>
  );
}
