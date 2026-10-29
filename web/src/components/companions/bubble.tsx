import { motion } from "motion/react";
import type { Who } from "./companion-model";

/**
 * A speech bubble with a springy tail. Decoration for sighted visitors (`aria-hidden`): the same words go to the stage's polite
 * live region. Under reduced motion it simply appears.
 */
export function Bubble({ who, text, reduce, align, className = "", action }: { who: Who; text: string; reduce: boolean; align?: "left" | "right"; className?: string; action?: { label: string; onClick: () => void } }) {
  const nano = align ? align === "left" : who === "nano";
  const isNano = who === "nano";
  return (
    <motion.div
      aria-hidden
      initial={reduce ? false : { opacity: 0, scale: 0.6, y: 8 }}
      animate={{ opacity: 1, scale: 1, y: 0 }}
      exit={reduce ? undefined : { opacity: 0, scale: 0.8, y: 4 }}
      transition={reduce ? { duration: 0 } : { type: "spring", stiffness: 420, damping: 20 }}
      style={{ transformOrigin: nano ? "20% 100%" : "80% 100%" }}
      className={`pointer-events-none absolute bottom-full z-10 mb-2 w-max max-w-[min(15rem,70vw)] ${nano ? "left-0" : "right-0"} ${className}`}
    >
      <div className="relative rounded-2xl border border-line bg-surface px-3.5 py-2.5 text-[13px] leading-snug text-ink shadow-card">
        <span className="mb-0.5 flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.1em] text-muted">
          <span className={`size-1.5 rounded-full ${isNano ? "bg-owned" : "bg-rented"}`} />
          {isNano ? "Nano" : "Ultra"}
        </span>
        {text}
        {action && (
          <button type="button" tabIndex={-1} onClick={action.onClick} className="pointer-events-auto mt-1.5 block rounded-full border border-line-strong bg-surface-2 px-3 py-1 text-[12px] font-medium text-ink hover:bg-line">
            {action.label}
          </button>
        )}
        <span
          className={`absolute -bottom-[5px] size-2.5 rotate-45 border-b border-r border-line bg-surface ${nano ? "left-8" : "right-10"}`}
        />
      </div>
    </motion.div>
  );
}
