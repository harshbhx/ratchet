import { motion, useReducedMotion } from "motion/react";
import { cn } from "@/lib/utils";
import { EASE } from "@/lib/motion";

/**
 * A heading whose words come into focus one after another as it scrolls into view (after the idea of React Bits'
 * BlurText; original code on `motion` variants). Every chapter title on the story uses it, so the scroll has one
 * rhythm. Screen readers get the sentence once; with reduced motion the blur is dropped and the words simply appear.
 */
export function BlurText({
  text,
  as: Tag = "h2",
  className,
  id,
  delay = 0,
  stagger = 0.07,
}: {
  text: string;
  as?: "h1" | "h2" | "h3" | "p";
  className?: string;
  id?: string;
  delay?: number;
  stagger?: number;
}) {
  const reduce = useReducedMotion();
  const words = text.split(" ");
  return (
    <Tag id={id} className={cn("relative", className)}>
      <span className="sr-only">{text}</span>
      <motion.span
        aria-hidden
        initial="hidden"
        whileInView="show"
        viewport={{ once: true, margin: "0px 0px -10% 0px" }}
        variants={{ hidden: {}, show: { transition: { staggerChildren: stagger, delayChildren: delay } } }}
      >
        {words.map((w, i) => (
          <motion.span
            key={`${w}-${i}`}
            className="inline-block will-change-[filter,transform]"
            variants={{
              hidden: { opacity: 0, y: reduce ? 0 : 14, filter: reduce ? "none" : "blur(10px)" },
              show: { opacity: 1, y: 0, filter: "blur(0px)", transition: { duration: 0.7, ease: EASE } },
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
