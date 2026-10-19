import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";
import { CHAPTERS } from "./shared";

/** Desktop-only chapter progress: a quiet column of numbers in the left gutter. */
export function ChapterRail() {
  const [active, setActive] = useState<string | null>(null);

  useEffect(() => {
    const els = CHAPTERS.map((c) => document.getElementById(c.id)).filter((e): e is HTMLElement => !!e);
    if (!els.length) return;
    const onScroll = () => {
      const mid = window.innerHeight * 0.45;
      let cur: string | null = null;
      for (const el of els) {
        const r = el.getBoundingClientRect();
        if (r.top <= mid) cur = el.id;
      }
      // Past the end of the last chapter: hide.
      const last = els[els.length - 1].getBoundingClientRect();
      if (last.bottom < mid) cur = null;
      setActive(cur);
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
      <ol className="space-y-1">
        {CHAPTERS.map((c, i) => {
          const on = c.id === active;
          const past = activeIdx >= 0 && i < activeIdx;
          return (
            <li key={c.id}>
              <a
                href={`#${c.id}`}
                aria-current={on ? "step" : undefined}
                className="group flex h-6 items-center gap-2.5 rounded-md pr-2 font-mono text-[11px] tabular"
              >
                <span className={cn("transition-colors", on ? "text-owned-ink" : past ? "text-ink-2" : "text-muted/70", "group-hover:text-ink")}>
                  {c.index}
                </span>
                <span
                  aria-hidden
                  className={cn("h-px transition-all duration-500", on ? "w-6 bg-owned" : past ? "w-3 bg-ink-2/60" : "w-3 bg-line-strong")}
                />
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
    </nav>
  );
}
