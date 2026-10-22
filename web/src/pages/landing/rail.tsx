import { useEffect, useState } from "react";
import { motion, useReducedMotion } from "motion/react";
import { cn } from "@/lib/utils";
import { EASE } from "@/lib/motion";
import { CHAPTERS } from "./shared";

/**
 * Desktop-only chapter progress in the left gutter: a track with one dot per chapter and a fill that grows as the
 * reader moves through the story. The ids are the chapters' section ids, so the links work without JavaScript too.
 */
export function ChapterRail() {
  const [active, setActive] = useState<string | null>(null);
  const [progress, setProgress] = useState(0);
  const reduce = useReducedMotion();

  useEffect(() => {
    const els = CHAPTERS.map((c) => document.getElementById(c.id)).filter((e): e is HTMLElement => !!e);
    if (!els.length) return;
    const onScroll = () => {
      const mid = window.innerHeight * 0.45;
      let cur: string | null = null;
      let idx = -1;
      els.forEach((el, i) => {
        if (el.getBoundingClientRect().top <= mid) {
          cur = el.id;
          idx = i;
        }
      });
      // Past the end of the last chapter: hide.
      const last = els[els.length - 1].getBoundingClientRect();
      if (last.bottom < mid) cur = null;
      setActive(cur);
      // How far through the active chapter the middle of the screen is, as a fraction of the whole rail.
      if (idx >= 0) {
        const r = els[idx].getBoundingClientRect();
        const within = Math.max(0, Math.min(1, (mid - r.top) / Math.max(1, r.height)));
        setProgress((idx + within) / els.length);
      } else setProgress(0);
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, []);

  const activeIdx = CHAPTERS.findIndex((c) => c.id === active);

  return (
    <nav
      aria-label="Chapters"
      className={cn(
        "fixed left-5 top-1/2 z-30 hidden -translate-y-1/2 transition-opacity duration-500 xl:block 2xl:left-8",
        active ? "opacity-100" : "pointer-events-none opacity-0",
      )}
    >
      <div className="relative">
        {/* the track and its fill */}
        <span aria-hidden className="absolute left-[7px] top-3 bottom-3 w-px bg-line-strong" />
        <motion.span
          aria-hidden
          className="absolute left-[7px] top-3 w-px origin-top bg-[linear-gradient(to_bottom,var(--rented),var(--owned))]"
          style={{ height: "calc(100% - 24px)" }}
          animate={{ scaleY: progress }}
          transition={reduce ? { duration: 0 } : { duration: 0.4, ease: EASE }}
        />
        <ol className="relative space-y-1">
          {CHAPTERS.map((c, i) => {
            const on = c.id === active;
            const past = activeIdx >= 0 && i < activeIdx;
            return (
              <li key={c.id}>
                <a
                  href={`#${c.id}`}
                  aria-current={on ? "step" : undefined}
                  className="group flex h-6 items-center gap-3 rounded-md pr-2 font-mono text-[11px] tabular"
                >
                  <span
                    aria-hidden
                    className={cn(
                      "relative flex size-[15px] items-center justify-center rounded-full border bg-bg transition-colors duration-300",
                      on ? "border-owned" : past ? "border-ink-2/60" : "border-line-strong",
                      "group-hover:border-ink",
                    )}
                  >
                    <span className={cn("size-[5px] rounded-full transition-colors duration-300", on ? "bg-owned" : past ? "bg-ink-2/60" : "bg-transparent")} />
                  </span>
                  <span className={cn("transition-colors", on ? "text-owned-ink" : past ? "text-ink-2" : "text-muted/70", "group-hover:text-ink")}>
                    {c.index}
                  </span>
                  <span
                    className={cn(
                      "whitespace-nowrap text-[11px] uppercase tracking-[0.1em] transition-opacity duration-300",
                      on ? "text-ink-2 opacity-100" : "text-muted opacity-0 group-hover:opacity-100",
                      "max-[1599px]:sr-only",
                    )}
                  >
                    {c.label}
                  </span>
                </a>
              </li>
            );
          })}
        </ol>
      </div>
    </nav>
  );
}
