import { useEffect, useRef, useState, type RefObject } from "react";

/** Width of an element, kept up to date with a ResizeObserver. */
export function useElementWidth<T extends HTMLElement>() {
  const ref = useRef<T | null>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    setWidth(el.getBoundingClientRect().width);
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width ?? 0;
      setWidth((prev) => (Math.abs(prev - w) > 0.5 ? w : prev));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width] as const;
}

/**
 * True while the element is at least partly on screen and the tab is visible. Every ambient loop on the page asks
 * this before it draws a frame, so nothing animates behind another tab or below the fold.
 */
export function useOnScreen<T extends Element>(ref: RefObject<T | null>, rootMargin = "0px") {
  const [on, setOn] = useState(true);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    let inView = true;
    let visible = !document.hidden;
    const sync = () => setOn(inView && visible);
    const io = new IntersectionObserver(
      (e) => {
        inView = e[0]?.isIntersecting ?? true;
        sync();
      },
      { rootMargin },
    );
    io.observe(el);
    const onVis = () => {
      visible = !document.hidden;
      sync();
    };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      io.disconnect();
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [ref, rootMargin]);
  return on;
}
