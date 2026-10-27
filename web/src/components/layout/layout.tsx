import { useEffect } from "react";
import { Outlet, useLocation } from "react-router";
import { motion, useReducedMotion } from "motion/react";
import { SiteHeader } from "./site-header";
import { SiteFooter } from "./site-footer";
import { WakingBanner } from "./waking-banner";
import { useStats } from "@/lib/hooks";
import { isWaking } from "@/lib/waking-model";
import { useWaking } from "@/lib/waking-store";

const TITLES: Record<string, string> = {
  "/": "Ratchet: own the adapter your traffic trains",
  "/live": "Live gateway · Ratchet",
  "/scout": "Scout · Ratchet",
  "/dashboard": "Dashboard · Ratchet",
};

/** Give up on a `#hash` whose element has not appeared after this long. */
const HASH_WAIT_MS = 8000;
/** After scrolling to it, keep the element in place this long while panels above it finish loading. */
const HASH_SETTLE_MS = 3000;

/**
 * Scroll to the element a `#hash` names, waiting for it to render: the dashboard draws its panels only after the first
 * stats call, so `/dashboard#redactor` has nothing to scroll to on the first paint, and panels that load later push it
 * down. Once the scroll has come to rest, a drifted element is put back, for a few seconds. Stops as soon as the reader
 * scrolls, types or taps, so it never pulls the page away from them. Returns the cleanup.
 */
function scrollToHash(hash: string): () => void {
  let id: string;
  try {
    id = decodeURIComponent(hash.slice(1));
  } catch {
    return () => {};
  }
  if (!id) return () => {};
  let el: HTMLElement | null = null;
  let raf = 0;
  let until = 0;
  let lastY = Number.NaN;
  let still = 0;
  const events = ["wheel", "touchstart", "keydown", "mousedown"] as const;
  const observer = new MutationObserver(() => find());
  const timer = window.setTimeout(() => {
    if (!el) stop();
  }, HASH_WAIT_MS);
  function stop() {
    observer.disconnect();
    window.clearTimeout(timer);
    window.cancelAnimationFrame(raf);
    for (const e of events) window.removeEventListener(e, stop);
  }
  function settle() {
    if (!el || performance.now() > until) return stop();
    const y = window.scrollY;
    still = y === lastY ? still + 1 : 0;
    lastY = y;
    if (still >= 3) {
      const off = el.getBoundingClientRect().top - (parseFloat(getComputedStyle(el).scrollMarginTop) || 0);
      const atBottom = window.innerHeight + y >= document.documentElement.scrollHeight - 1;
      if (Math.abs(off) > 2 && !(off > 0 && atBottom)) el.scrollIntoView({ block: "start", behavior: "instant" });
    }
    raf = window.requestAnimationFrame(settle);
  }
  function find() {
    if (el) return;
    el = document.getElementById(id);
    if (!el) return;
    observer.disconnect();
    el.scrollIntoView({ block: "start" });
    until = performance.now() + HASH_SETTLE_MS;
    raf = window.requestAnimationFrame(settle);
  }
  for (const e of events) window.addEventListener(e, stop, { passive: true, once: true });
  observer.observe(document.body, { childList: true, subtree: true });
  find();
  return stop;
}

export function Layout() {
  const { pathname, hash } = useLocation();
  // One stats call per page load for the footer's status line, not live numbers.
  const { stats, error } = useStats(0);
  const reduce = useReducedMotion();
  const waking = isWaking(useWaking());
  const gateway = waking ? "waking" : stats ? "up" : error ? "down" : "unknown";
  useEffect(() => {
    document.title = TITLES[pathname] ?? "Ratchet";
    if (!hash) {
      window.scrollTo({ top: 0 });
      return;
    }
    return scrollToHash(hash);
  }, [pathname, hash]);
  return (
    <div className="flex min-h-dvh flex-col bg-bg text-ink">
      <SiteHeader gateway={gateway} />
      <WakingBanner />
      <main id="main" className="flex-1">
        {/* A page fades in on a route change. Opacity only: a transform here would break the dashboard's sticky jump bar. */}
        <motion.div key={pathname} initial={reduce ? false : { opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.35, ease: "easeOut" }}>
          <Outlet />
        </motion.div>
      </main>
      <SiteFooter stats={stats} error={error} />
    </div>
  );
}
