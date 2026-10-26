import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { cn } from "@/lib/utils";

/**
 * A box that scrolls sideways when its content is wider than the screen (a code line, a wide table on a phone). The
 * edge with more content behind it fades into `fade` (a colour token's CSS variable, the box's own background), so a
 * reader sees there is more to scroll to. Nothing is drawn when everything fits.
 */
export function ScrollX({
  children,
  className,
  wrapClassName,
  fade = "var(--surface)",
  as: Tag = "div",
}: {
  children: ReactNode;
  /** The scrolling element's classes. */
  className?: string;
  /** The outer box's classes (margins go here, so the fades cover the content only). */
  wrapClassName?: string;
  fade?: string;
  as?: "div" | "pre";
}) {
  const ref = useRef<HTMLDivElement & HTMLPreElement>(null);
  const [edges, setEdges] = useState({ left: false, right: false });
  const measure = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const max = el.scrollWidth - el.clientWidth;
    const left = max > 1 && el.scrollLeft > 1;
    const right = max > 1 && el.scrollLeft < max - 1;
    setEdges((e) => (e.left === left && e.right === right ? e : { left, right }));
  }, []);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    measure();
    const ro = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    ro?.observe(el);
    if (el.firstElementChild) ro?.observe(el.firstElementChild);
    return () => ro?.disconnect();
  }, [measure]);
  const edge = "pointer-events-none absolute inset-y-0 w-8 transition-opacity duration-200";
  return (
    <div className={cn("relative min-w-0", wrapClassName)}>
      <Tag ref={ref} onScroll={measure} className={cn("overflow-x-auto", className)}>
        {children}
      </Tag>
      <span aria-hidden className={cn(edge, "left-0", edges.left ? "opacity-100" : "opacity-0")} style={{ background: `linear-gradient(to right, ${fade}, transparent)` }} />
      <span aria-hidden className={cn(edge, "right-0", edges.right ? "opacity-100" : "opacity-0")} style={{ background: `linear-gradient(to left, ${fade}, transparent)` }} />
    </div>
  );
}
