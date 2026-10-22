import { motion, useReducedMotion } from "motion/react";
import { cn } from "@/lib/utils";
import { EASE } from "@/lib/motion";

/**
 * A sentence whose words sharpen into view one by one when scrolled to (ported from Aceternity UI's Text Generate
 * Effect, MIT; adapted: variants on `whileInView` instead of an effect on mount, the blur dropped under reduced
 * motion, and the sentence read whole by screen readers).
 */
export function TextGenerate({ text, className, as: Tag = "p", delay = 0 }: { text: string; className?: string; as?: "p" | "h2" | "h3" | "span"; delay?: number }) {
  const reduce = useReducedMotion();
  const words = text.split(" ");
  return (
    <Tag className={cn("relative", className)}>
      <span className="sr-only">{text}</span>
      <motion.span
        aria-hidden
        initial="hidden"
        whileInView="show"
        viewport={{ once: true, margin: "0px 0px -12% 0px" }}
        variants={{ hidden: {}, show: { transition: { staggerChildren: 0.06, delayChildren: delay } } }}
      >
        {words.map((w, i) => (
          <motion.span
            key={`${w}-${i}`}
            className="inline-block"
            variants={{
              hidden: { opacity: 0, filter: reduce ? "none" : "blur(8px)", y: reduce ? 0 : 4 },
              show: { opacity: 1, filter: "blur(0px)", y: 0, transition: { duration: 0.55, ease: EASE } },
            }}
          >
            {w}
            {i < words.length - 1 ? " " : ""}
          </motion.span>
        ))}
      </motion.span>
    </Tag>
  );
}
