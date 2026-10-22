import type { ReactNode } from "react";
import { motion } from "motion/react";
import { cn } from "@/lib/utils";
import { EASE } from "@/lib/motion";

/**
 * A bar of light at the top of a section, with two cones of glow spreading down from it that open as the section
 * scrolls in (ported from Aceternity UI's Lamp, MIT; adapted: the page's own background instead of a fixed dark
 * one, so it works in both themes, a smaller height, the glow in the story's blue, and the cones at their final
 * width under reduced motion through the global MotionConfig). Used once, for the chapter about the gates: the lamp
 * shows what the gates examine.
 */
export function Lamp({ children, className }: { children: ReactNode; className?: string }) {
  const open = {
    initial: { opacity: 0.4, width: "14rem" },
    whileInView: { opacity: 1, width: "30rem" },
    viewport: { once: true, margin: "0px 0px -20% 0px" },
    transition: { delay: 0.2, duration: 0.9, ease: EASE },
  };
  return (
    <div className={cn("relative isolate overflow-hidden", className)}>
      <div aria-hidden className="pointer-events-none absolute inset-x-0 top-0 flex h-[22rem] items-start justify-center">
        {/* left cone */}
        <motion.div
          {...open}
          style={{ backgroundImage: "conic-gradient(from 70deg at center top, var(--owned), transparent, transparent)" }}
          className="absolute right-1/2 top-0 h-56 w-[30rem] opacity-60 [mask-image:linear-gradient(to_bottom,black,transparent_85%)] dark:opacity-80"
        />
        {/* right cone */}
        <motion.div
          {...open}
          style={{ backgroundImage: "conic-gradient(from 290deg at center top, transparent, transparent, var(--owned))" }}
          className="absolute left-1/2 top-0 h-56 w-[30rem] opacity-60 [mask-image:linear-gradient(to_bottom,black,transparent_85%)] dark:opacity-80"
        />
        {/* the soft pool of light under the bar */}
        <div className="absolute top-6 h-32 w-[26rem] rounded-full bg-owned opacity-25 blur-3xl dark:opacity-35" />
        <motion.div
          initial={{ width: "8rem", opacity: 0 }}
          whileInView={{ width: "16rem", opacity: 1 }}
          viewport={{ once: true, margin: "0px 0px -20% 0px" }}
          transition={{ delay: 0.2, duration: 0.9, ease: EASE }}
          className="absolute top-0 h-24 rounded-full bg-owned opacity-40 blur-2xl"
        />
        {/* the bar */}
        <motion.div
          initial={{ width: "12rem" }}
          whileInView={{ width: "26rem" }}
          viewport={{ once: true, margin: "0px 0px -20% 0px" }}
          transition={{ delay: 0.2, duration: 0.9, ease: EASE }}
          className="absolute top-0 h-[3px] max-w-[90vw] rounded-full bg-owned shadow-[0_0_24px_4px_var(--owned)]"
        />
        {/* fade the whole thing into the page below */}
        <div className="absolute inset-x-0 bottom-0 h-40 bg-[linear-gradient(to_bottom,transparent,var(--bg))]" />
      </div>
      <div className="relative">{children}</div>
    </div>
  );
}
