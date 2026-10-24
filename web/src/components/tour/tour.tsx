import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useLocation, useNavigate } from "react-router";
import { useReducedMotion } from "motion/react";
import { ArrowLeft, ArrowRight, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { stepsFor } from "./steps";
import { announce, GAP, NARROW_PX, nextStep, placePopover, scrollTarget, spotlight, wantsTour, type Box, type Size, type TourStep } from "./tour-model";

/** The first element a step's selectors find, or null. A bad selector counts as no match. */
function findTarget(step: TourStep): HTMLElement | null {
  for (const sel of step.targets) {
    try {
      const el = document.querySelector<HTMLElement>(sel);
      if (el) return el;
    } catch {
      /* not a valid selector */
    }
  }
  return null;
}

/** The bottom edge of what stays stuck to the top of the screen: the site header and anything marked `data-tour-inset`. */
function topInset(): number {
  let bottom = 0;
  const els = [document.querySelector("header"), ...Array.from(document.querySelectorAll("[data-tour-inset]"))];
  for (const el of els) {
    if (!el) continue;
    const r = el.getBoundingClientRect();
    const pos = getComputedStyle(el).position;
    if ((pos === "sticky" || pos === "fixed") && r.top <= 1 + bottom && r.height > 0) bottom = Math.max(bottom, r.bottom);
  }
  return bottom;
}

function viewSize(): Size {
  return { width: window.innerWidth, height: window.innerHeight };
}

function same(a: Box | null, b: Box | null): boolean {
  if (!a || !b) return a === b;
  return Math.abs(a.top - b.top) < 0.5 && Math.abs(a.left - b.left) < 0.5 && Math.abs(a.width - b.width) < 0.5 && Math.abs(a.height - b.height) < 0.5;
}

const FOCUSABLE = 'button:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])';

/**
 * A guided tour over the page: the screen dims except a cut-out around each step's target, and a dialog says what it
 * is, with Back, Next and Skip. Esc closes it, the arrow keys move, focus stays inside and goes back where it was on
 * close, and each step is announced. It keeps nothing anywhere: it is opened by `?tour=1` or a button, every time.
 */
export function Tour({ steps, onClose }: { steps: TourStep[]; onClose: () => void }) {
  const reduce = useReducedMotion() ?? false;
  const titleId = useId();
  const bodyId = useId();
  const [index, setIndex] = useState<number | null>(null);
  const [target, setTarget] = useState<Box | null>(null);
  const [view, setView] = useState<Size>(viewSize);
  const [pop, setPop] = useState<Size>({ width: 380, height: 220 });
  const [inset, setInset] = useState(0);
  const popRef = useRef<HTMLDivElement | null>(null);
  const opener = useRef<Element | null>(null);
  const exists = useCallback((i: number) => findTarget(steps[i]) !== null, [steps]);
  const narrow = view.width < NARROW_PX;
  const bottomInset = narrow ? pop.height + 2 * GAP : 0;

  // Open on the first step that has a target; give focus back to the opener on close.
  useEffect(() => {
    opener.current = document.activeElement;
    const first = nextStep(steps.length, -1, 1, exists);
    if (first == null) onClose();
    else setIndex(first);
    return () => {
      const el = opener.current as HTMLElement | null;
      if (el && document.contains(el)) el.focus({ preventScroll: true });
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Bring the step's target into view.
  useEffect(() => {
    if (index == null) return;
    const el = findTarget(steps[index]);
    if (!el) {
      const n = nextStep(steps.length, index, 1, exists) ?? nextStep(steps.length, index, -1, exists);
      if (n == null) onClose();
      else setIndex(n);
      return;
    }
    // A page that folds its sections (the judges' chapters) opens the one this step lights.
    el.dispatchEvent(new CustomEvent("tour-focus", { bubbles: true }));
    const r = el.getBoundingClientRect();
    const v = viewSize();
    const top = topInset();
    const docked = v.width < NARROW_PX;
    const y = scrollTarget({ top: r.top, left: r.left, width: r.width, height: r.height }, window.scrollY, v, top, docked ? pop.height + 2 * GAP : 0, docked ? 0 : pop.height);
    window.scrollTo({ top: y, behavior: reduce ? "auto" : "smooth" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [index]);

  // Follow the target while the page scrolls, resizes or loads more above it.
  useEffect(() => {
    if (index == null) return;
    let raf = 0;
    let last: Box | null = null;
    let lastInset = -1;
    const frame = () => {
      const el = findTarget(steps[index]);
      const r = el?.getBoundingClientRect();
      const box = r ? { top: r.top, left: r.left, width: r.width, height: r.height } : null;
      if (!same(box, last)) {
        last = box;
        setTarget(box);
      }
      const t = topInset();
      if (Math.abs(t - lastInset) > 0.5) {
        lastInset = t;
        setInset(t);
      }
      raf = window.requestAnimationFrame(frame);
    };
    raf = window.requestAnimationFrame(frame);
    const onResize = () => setView(viewSize());
    window.addEventListener("resize", onResize);
    return () => {
      window.cancelAnimationFrame(raf);
      window.removeEventListener("resize", onResize);
    };
  }, [index, steps]);

  // The popover's own size, for placing it.
  useLayoutEffect(() => {
    const el = popRef.current;
    if (!el) return;
    const measure = () => {
      const r = el.getBoundingClientRect();
      setPop((p) => (Math.abs(p.width - r.width) > 0.5 || Math.abs(p.height - r.height) > 0.5 ? { width: r.width, height: r.height } : p));
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [index]);

  // Focus into the dialog when it opens.
  useEffect(() => {
    if (index != null && popRef.current && !popRef.current.contains(document.activeElement)) popRef.current.focus({ preventScroll: true });
  }, [index]);

  const go = useCallback(
    (dir: 1 | -1) => {
      if (index == null) return;
      const n = nextStep(steps.length, index, dir, exists);
      if (n != null) setIndex(n);
      else if (dir === 1) onClose();
    },
    [index, steps.length, exists, onClose],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      } else if (e.key === "ArrowRight" || e.key === "ArrowDown") {
        e.preventDefault();
        go(1);
      } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
        e.preventDefault();
        go(-1);
      } else if (e.key === "Tab" && popRef.current) {
        const items = Array.from(popRef.current.querySelectorAll<HTMLElement>(FOCUSABLE));
        if (items.length === 0) return;
        const first = items[0];
        const lastItem = items[items.length - 1];
        const active = document.activeElement;
        if (e.shiftKey && (active === first || !popRef.current.contains(active))) {
          e.preventDefault();
          lastItem.focus();
        } else if (!e.shiftKey && (active === lastItem || !popRef.current.contains(active))) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [go, onClose]);

  if (index == null) return null;
  const step = steps[index];
  const available = steps.map((_, i) => i).filter(exists);
  const position = Math.max(0, available.indexOf(index));
  const total = Math.max(available.length, 1);
  const isLast = nextStep(steps.length, index, 1, exists) == null;
  const isFirst = nextStep(steps.length, index, -1, exists) == null;
  const spot = target ? spotlight(target, view, undefined, inset, bottomInset) : null;
  const place = placePopover(spot, pop, view, narrow, inset);
  const move = reduce ? "" : "transition-[top,left,width,height] duration-300 ease-[cubic-bezier(0.22,1,0.36,1)]";

  return createPortal(
    <div className="fixed inset-0 z-[70]">
      {/* Clicks outside the dialog do nothing: the tour ends only by a button or Esc. */}
      <div className="absolute inset-0" aria-hidden />
      {spot ? (
        <div
          aria-hidden
          className={cn("pointer-events-none absolute rounded-xl ring-2 ring-white/80", move)}
          style={{ top: spot.top, left: spot.left, width: spot.width, height: spot.height, boxShadow: "0 0 0 200vmax rgb(6 8 12 / 0.6)" }}
        />
      ) : (
        <div aria-hidden className="absolute inset-0 bg-[rgb(6_8_12/0.6)]" />
      )}
      <div
        ref={popRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={bodyId}
        tabIndex={-1}
        className={cn(
          "absolute rounded-2xl border border-line bg-surface p-5 text-ink shadow-[0_24px_60px_-20px_rgb(0_0_0/0.45),0_2px_6px_rgb(0_0_0/0.12)] outline-none",
          place.mode === "dock" ? "inset-x-3 bottom-3 pb-[max(1.25rem,env(safe-area-inset-bottom))]" : "w-[min(380px,calc(100vw-24px))]",
          place.mode === "float" && move,
        )}
        style={place.mode === "float" ? { top: place.top, left: place.left } : undefined}
      >
        <div className="flex items-start justify-between gap-3">
          <p className="font-mono text-[12px] text-muted tabular-nums">
            Step {position + 1} of {total}
          </p>
          <button
            type="button"
            onClick={onClose}
            className="-mr-1.5 -mt-1.5 inline-flex size-8 items-center justify-center rounded-full text-muted transition-colors hover:bg-surface-2 hover:text-ink"
            aria-label="Close the tour"
          >
            <X className="size-4" aria-hidden />
          </button>
        </div>
        <h2 id={titleId} className="mt-1.5 text-balance text-[18px] font-semibold leading-snug tracking-[-0.015em]">
          {step.title}
        </h2>
        <p id={bodyId} className="mt-2 text-pretty text-[14.5px] leading-relaxed text-ink-2">
          {step.body}
        </p>
        <div className="mt-4 flex gap-1" aria-hidden>
          {available.map((i) => (
            <span key={i} className={cn("h-1 flex-1 rounded-full", i === index ? "bg-ink" : i < index ? "bg-line-strong" : "bg-line")} />
          ))}
        </div>
        <div className="mt-4 flex items-center justify-between gap-2">
          <Button variant="ghost" size="sm" onClick={onClose} className="-ml-2">
            Skip tour
          </Button>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => go(-1)} disabled={isFirst} aria-label="Previous step">
              <ArrowLeft aria-hidden /> Back
            </Button>
            <Button size="sm" onClick={() => go(1)} aria-label={isLast ? "Finish the tour" : "Next step"}>
              {isLast ? "Finish" : "Next"} {!isLast && <ArrowRight aria-hidden />}
            </Button>
          </div>
        </div>
        <p className="mt-3 hidden text-[12px] text-muted sm:block">Arrow keys move, Esc closes.</p>
      </div>
      <div className="sr-only" aria-live="polite" aria-atomic="true">
        {announce(position, total, step.title)}
      </div>
    </div>,
    document.body,
  );
}

/**
 * Opens the tour for the page when the URL has `?tour=1`, once that page's targets have rendered (the dashboard draws
 * its panels after its first stats call), and takes the parameter off the URL when the tour closes.
 */
export function TourHost() {
  const loc = useLocation();
  const navigate = useNavigate();
  const steps = stepsFor(loc.pathname);
  const want = steps.length > 0 && wantsTour(loc.search);
  const [ready, setReady] = useState(false);

  const close = useCallback(() => {
    const params = new URLSearchParams(loc.search);
    params.delete("tour");
    const search = params.toString();
    navigate({ pathname: loc.pathname, search: search ? `?${search}` : "", hash: loc.hash }, { replace: true });
  }, [loc.pathname, loc.search, loc.hash, navigate]);

  useEffect(() => {
    if (!want) {
      setReady(false);
      return;
    }
    let tries = 0;
    const check = () => steps.some((s) => findTarget(s) !== null);
    if (check()) {
      setReady(true);
      return;
    }
    const id = window.setInterval(() => {
      if (check()) {
        window.clearInterval(id);
        setReady(true);
      } else if (++tries > 60) {
        window.clearInterval(id);
        close();
      }
    }, 200);
    return () => window.clearInterval(id);
  }, [want, steps, close]);

  return want && ready ? <Tour key={loc.pathname} steps={steps} onClose={close} /> : null;
}
