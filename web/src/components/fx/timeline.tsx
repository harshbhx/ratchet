import { useEffect, useRef, useState, type ReactNode } from "react";
import { motion, useReducedMotion, useScroll, useTransform } from "motion/react";
import { cn } from "@/lib/utils";

export interface TimelineItem {
  /** The big label that sticks while its entry scrolls by. */
  title: string;
  /** A short line under the title. */
  sub?: string;
  content: ReactNode;
  tone?: "neutral" | "owned" | "good" | "bad";
}

const DOT: Record<NonNullable<TimelineItem["tone"]>, string> = {
  neutral: "border-line-strong bg-surface",
  owned: "border-owned bg-owned-soft",
  good: "border-good bg-good-soft",
  bad: "border-bad bg-bad-soft",
};

/**
 * Stages down a vertical line, each title sticking while its content scrolls, and the line filling as the reader
 * goes (ported from Aceternity UI's Timeline, MIT; adapted: the story's tokens, a tone per stage, no page chrome, a
 * ResizeObserver for the line's height, and the line drawn in full under reduced motion).
 */
export function Timeline({ items, className }: { items: TimelineItem[]; className?: string }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [height, setHeight] = useState(0);
  const reduce = useReducedMotion();
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setHeight(el.getBoundingClientRect().height));
    ro.observe(el);
    setHeight(el.getBoundingClientRect().height);
    return () => ro.disconnect();
  }, []);
  const { scrollYProgress } = useScroll({ target: ref, offset: ["start 0.7", "end 0.6"] });
  const fill = useTransform(scrollYProgress, [0, 1], [0, height]);
  const fillOpacity = useTransform(scrollYProgress, [0, 0.08], [0, 1]);

  return (
    <div ref={ref} className={cn("relative", className)}>
      <ol>
        {items.map((it, i) => (
          <li key={it.title} className="relative flex gap-4 pb-12 last:pb-0 sm:gap-8 md:gap-10">
            <div className="sticky top-24 z-10 flex h-fit w-9 shrink-0 items-start justify-center self-start md:w-56 md:justify-start lg:w-64">
              <span className="relative flex size-9 items-center justify-center rounded-full bg-bg">
                <span className={cn("size-4 rounded-full border-2", DOT[it.tone ?? "neutral"])} aria-hidden />
                <span className="sr-only">{`Stage ${i + 1}: `}</span>
              </span>
              <div className="hidden pl-4 md:block">
                <p className="text-[22px] font-semibold leading-tight tracking-[-0.025em] text-ink lg:text-[26px]">{it.title}</p>
                {it.sub && <p className="mt-1 font-mono text-[12px] text-muted">{it.sub}</p>}
              </div>
            </div>
            <div className="min-w-0 flex-1 pt-1">
              <p className="text-[19px] font-semibold leading-tight tracking-[-0.02em] text-ink md:hidden">{it.title}</p>
              {it.sub && <p className="mt-0.5 font-mono text-[12px] text-muted md:hidden">{it.sub}</p>}
              <div className="md:-mt-1">{it.content}</div>
            </div>
          </li>
        ))}
      </ol>
      {/* the line, behind the dots */}
      <div
        aria-hidden
        style={{ height }}
        className="absolute left-[17px] top-0 w-[2px] overflow-hidden bg-[linear-gradient(to_bottom,transparent,var(--line-strong)_8%,var(--line-strong)_92%,transparent)]"
      >
        {reduce ? (
          <span className="absolute inset-x-0 top-0 h-full bg-[linear-gradient(to_bottom,var(--rented),var(--owned))]" />
        ) : (
          <motion.span
            style={{ height: fill, opacity: fillOpacity }}
            className="absolute inset-x-0 top-0 rounded-full bg-[linear-gradient(to_bottom,var(--rented)_0%,var(--owned)_24%,var(--owned)_100%)]"
          />
        )}
      </div>
    </div>
  );
}
