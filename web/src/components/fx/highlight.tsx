import type { ReactNode } from "react";
import { motion } from "motion/react";
import { cn } from "@/lib/utils";

/**
 * A marker-pen highlight that draws itself across a phrase when it scrolls into view (ported from Aceternity UI's
 * Hero Highlight's `Highlight`, MIT; adapted: `whileInView`, the story's soft colours, and the full highlight at once
 * under reduced motion through the global MotionConfig).
 */
export function Highlight({ children, className, tone = "owned" }: { children: ReactNode; className?: string; tone?: "owned" | "rented" }) {
  return (
    <motion.span
      initial={{ backgroundSize: "0% 100%" }}
      whileInView={{ backgroundSize: "100% 100%" }}
      viewport={{ once: true, margin: "0px 0px -10% 0px" }}
      transition={{ duration: 1.4, ease: "linear", delay: 0.4 }}
      style={{ backgroundRepeat: "no-repeat", backgroundPosition: "left center", display: "inline" }}
      className={cn(
        "relative rounded-md px-1 pb-0.5",
        tone === "owned" ? "bg-[linear-gradient(var(--owned-soft),var(--owned-soft))]" : "bg-[linear-gradient(var(--rented-soft),var(--rented-soft))]",
        className,
      )}
    >
      {children}
    </motion.span>
  );
}
