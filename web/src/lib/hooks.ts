import { useCallback, useEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { animate, useInView, useReducedMotion } from "motion/react";
import { api, type Stats } from "./api";
import { onRecovered } from "./waking-store";
import { switchWithReveal, type RevealDeps } from "./theme-transition";

/** Poll /ratchet/stats. `stats` stays null if the gateway is unreachable (static preview). */
export function useStats(intervalMs = 15000) {
  const [stats, setStats] = useState<Stats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const refresh = useCallback(async () => {
    try {
      setStats(await api.stats());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "unreachable");
    }
  }, []);
  useEffect(() => {
    void refresh();
    const off = onRecovered(() => void refresh());
    if (!intervalMs) return off;
    const id = window.setInterval(() => void refresh(), intervalMs);
    return () => {
      window.clearInterval(id);
      off();
    };
  }, [refresh, intervalMs]);
  return { stats, error, refresh };
}

const THEME_EVENT = "ratchet-theme";

export type Theme = "light" | "dark";

export function useTheme() {
  const [theme, setThemeState] = useState<Theme>(() =>
    typeof document !== "undefined" && document.documentElement.classList.contains("dark") ? "dark" : "light",
  );
  // Several toggles live on one page (header, menu, footer): each follows a change made by another.
  useEffect(() => {
    const on = () => setThemeState(document.documentElement.classList.contains("dark") ? "dark" : "light");
    window.addEventListener(THEME_EVENT, on);
    return () => window.removeEventListener(THEME_EVENT, on);
  }, []);
  const setTheme = useCallback((t: Theme) => {
    document.documentElement.classList.toggle("dark", t === "dark");
    try {
      localStorage.setItem("ratchet-theme", t);
    } catch {
      /* storage blocked */
    }
    setThemeState(t);
    window.dispatchEvent(new Event(THEME_EVENT));
  }, []);
  /** Switch theme. With `origin` (the toggle button) the new theme spreads from its centre as a circle; see lib/theme-transition.ts. */
  const toggle = (origin?: Element | null) => {
    const next: Theme = theme === "dark" ? "light" : "dark";
    const r = origin?.getBoundingClientRect();
    const x = r ? r.left + r.width / 2 : window.innerWidth / 2;
    const y = r ? r.top + r.height / 2 : window.innerHeight / 2;
    switchWithReveal(() => flushSync(() => setTheme(next)), x, y, {
      doc: document as unknown as RevealDeps["doc"],
      reducedMotion: window.matchMedia("(prefers-reduced-motion: reduce)").matches,
      width: window.innerWidth,
      height: window.innerHeight,
    });
  };
  return { theme, setTheme, toggle };
}

/** Animate a number toward `value` once it scrolls into view. Respects reduced motion. */
export function useCountUp(value: number, duration = 1.1) {
  const ref = useRef<HTMLSpanElement | null>(null);
  const inView = useInView(ref, { once: true, margin: "-10% 0px" });
  const reduce = useReducedMotion();
  const [display, setDisplay] = useState(reduce ? value : 0);
  const from = useRef(0);
  useEffect(() => {
    if (!inView) return;
    if (reduce) {
      setDisplay(value);
      return;
    }
    const controls = animate(from.current, value, {
      duration,
      ease: [0.22, 1, 0.36, 1],
      onUpdate: (v) => setDisplay(v),
    });
    from.current = value;
    return () => controls.stop();
  }, [value, inView, reduce, duration]);
  return { ref, display };
}

export function useInterval(cb: () => void, ms: number | null) {
  const saved = useRef(cb);
  useEffect(() => {
    saved.current = cb;
  }, [cb]);
  useEffect(() => {
    if (ms == null) return;
    const id = window.setInterval(() => saved.current(), ms);
    return () => window.clearInterval(id);
  }, [ms]);
}
