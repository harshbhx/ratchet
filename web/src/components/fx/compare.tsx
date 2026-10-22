import { useId, useState, type ReactNode } from "react";
import { cn } from "@/lib/utils";

/**
 * Two panels under one slider: drag the handle to reveal more of one or the other (ported from Aceternity UI's
 * Compare, MIT; adapted: any content instead of two images, no sparkles or icon library, and one real range input
 * over the whole card, so a mouse drag, a finger and the arrow keys all move the same control and a screen reader
 * hears a slider with a value). Both panels are always in the accessibility tree; the clip is visual only.
 */
export function Compare({
  before,
  after,
  beforeLabel,
  afterLabel,
  initial = 50,
  className,
  ariaLabel,
}: {
  before: ReactNode;
  after: ReactNode;
  beforeLabel: string;
  afterLabel: string;
  initial?: number;
  className?: string;
  ariaLabel: string;
}) {
  const [pct, setPct] = useState(initial);
  const id = useId();
  return (
    <div className={cn("group/cmp relative isolate overflow-hidden rounded-[22px] border border-line bg-surface shadow-card", className)}>
      {/* the full "after" panel underneath */}
      <div className="relative">{after}</div>
      {/* the "before" panel on top, clipped to the slider */}
      <div aria-hidden className="absolute inset-0" style={{ clipPath: `inset(0 ${100 - pct}% 0 0)` }}>
        {before}
      </div>
      {/* the divider and handle */}
      <div aria-hidden className="pointer-events-none absolute inset-y-0 z-20 w-px bg-ink/70" style={{ left: `${pct}%` }}>
        <span className="absolute left-1/2 top-1/2 flex h-9 w-7 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-lg border border-line-strong bg-surface shadow-card transition-transform group-focus-within/cmp:ring-2 group-focus-within/cmp:ring-[var(--focus)] group-focus-within/cmp:ring-offset-2 group-focus-within/cmp:ring-offset-surface">
          <span className="block h-4 w-px bg-ink-2" />
          <span className="ml-1 block h-4 w-px bg-ink-2" />
        </span>
      </div>
      <span aria-hidden className="pointer-events-none absolute left-3 top-3 z-20 rounded-full border border-line bg-surface/90 px-2 py-0.5 font-mono text-[11px] text-ink-2 backdrop-blur">
        {beforeLabel}
      </span>
      <span aria-hidden className="pointer-events-none absolute right-3 top-3 z-20 rounded-full border border-line bg-surface/90 px-2 py-0.5 font-mono text-[11px] text-ink-2 backdrop-blur">
        {afterLabel}
      </span>
      <label htmlFor={id} className="sr-only">
        {ariaLabel}
      </label>
      <input
        id={id}
        type="range"
        min={0}
        max={100}
        step={1}
        value={pct}
        onChange={(e) => setPct(Number(e.target.value))}
        aria-valuetext={`${pct}% ${beforeLabel}, ${100 - pct}% ${afterLabel}`}
        className="absolute inset-0 z-30 m-0 h-full w-full cursor-col-resize opacity-0"
      />
    </div>
  );
}
