import { motion } from "motion/react";
import { cn } from "@/lib/utils";
import { EASE } from "@/lib/motion";

export interface SplitSegment {
  text: string;
  className?: string;
}

/**
 * A headline whose words rise into place one after another (after the idea of React Bits' SplitText, written here
 * with `motion`, no GSAP). Each word sits in a clipped box so it rises from below its own baseline. Screen readers get
 * the whole sentence once, from a visually hidden copy; the animated words are decoration. With reduced motion the
 * global MotionConfig drops the transforms and the words simply appear.
 */
export function SplitText({
  segments,
  as: Tag = "h1",
  className,
  delay = 0,
  stagger = 0.05,
  duration = 0.8,
  id,
}: {
  segments: SplitSegment[];
  as?: "h1" | "h2" | "h3" | "p";
  className?: string;
  delay?: number;
  stagger?: number;
  duration?: number;
  id?: string;
}) {
  const words = segments.flatMap((s, si) =>
    s.text
      .split(/(\s+)/)
      .filter((w) => w.length > 0)
      .map((w, wi) => ({ w, className: s.className, key: `${si}-${wi}` })),
  );
  let index = 0;
  return (
    <Tag id={id} className={cn("relative", className)}>
      <span className="sr-only">{segments.map((s) => s.text).join("")}</span>
      <span aria-hidden className="inline">
        {words.map(({ w, className: cls, key }) => {
          if (/^\s+$/.test(w)) return <span key={key}> </span>;
          const i = index++;
          return (
            <span key={key} className={cn("inline-block overflow-hidden align-bottom pb-[0.08em] -mb-[0.08em]", cls)}>
              <motion.span
                className={cn("inline-block will-change-transform", cls)}
                initial={{ y: "110%", opacity: 0 }}
                animate={{ y: "0%", opacity: 1 }}
                transition={{ duration, ease: EASE, delay: delay + i * stagger }}
              >
                {w}
              </motion.span>
            </span>
          );
        })}
      </span>
    </Tag>
  );
}
