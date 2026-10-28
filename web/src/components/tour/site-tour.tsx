import { useCallback, useEffect, useRef, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { useLocation, useNavigate } from "react-router";
import { AnimatePresence, motion } from "motion/react";
import { X } from "lucide-react";
import type { Config, Driver, DriveStep, PopoverDOM } from "driver.js";
import { Avatar } from "@/components/avatar/avatar";
import { themeLook, type Expression } from "@/components/avatar/avatar-model";
import examsText from "@/data/exams.json?raw";
import { parseExams, pct } from "@/lib/exams-model";
import { videoHref } from "@/lib/real-model";
import { DAILY_LIMIT_TEXT } from "@/pages/landing/try-request-model";
import { REAL } from "@/pages/judges/real";
import { DOCS_URL } from "@/lib/utils";
import { buttonHidden, clearTourState, dismissHint, hintDismissed, readTourState, setButtonHidden, writeTourState } from "./site-tour-storage";
import {
  ANSWER_INDEX,
  SEND_INDEX,
  SITE_STEPS,
  nextAvailable,
  progressText,
  sendLines,
  sendMood,
  stepBody,
  wantsSiteTour,
  type ExamNumbers,
  type Lines,
  type SendOutcome,
} from "./site-tour-model";

/** Anything on the site can start the tour by firing this (the hero button, the phone menu). */
export const TOUR_EVENT = "ratchet-start-tour";
export const startSiteTour = () => window.dispatchEvent(new Event(TOUR_EVENT));

const EXAMS = parseExams(examsText);
const EXAM_NUMBERS: ExamNumbers = {
  exam1: { adapter: pct(EXAMS.scores.exam1.r2.rate), base: pct(EXAMS.scores.exam1.base.rate) },
  exam2: { adapter: pct(EXAMS.scores.exam2.r2.rate), base: pct(EXAMS.scores.exam2.base.rate) },
};
const N = SITE_STEPS.length;

/** What the popover's avatars show now. */
interface Ui {
  mood: Expression;
  ultra: boolean;
  lines: Lines;
  sunglasses: boolean;
  squint: boolean;
}

function TourAvatars({ ui }: { ui: Ui }) {
  return (
    <>
      <div className="flex items-end gap-2">
        <Avatar who="nano" expression={ui.mood} size={64} sunglasses={ui.sunglasses} squint={ui.squint} />
        <AnimatePresence>
          {ui.ultra && (
            <motion.div initial={{ opacity: 0, scale: 0.6, y: 10 }} animate={{ opacity: 1, scale: 1, y: 0 }} exit={{ opacity: 0, scale: 0.7 }} transition={{ type: "spring", stiffness: 380, damping: 22 }}>
              <Avatar who="ultra" expression="proud" size={76} sunglasses={ui.sunglasses} squint={ui.squint} />
            </motion.div>
          )}
        </AnimatePresence>
      </div>
      <div aria-live="polite" className="flex min-w-0 flex-col gap-1.5">
        {ui.lines.ultra && <p className="rt-say">{ui.lines.ultra}</p>}
        {ui.lines.nano && <p className="rt-say">{ui.lines.nano}</p>}
      </div>
    </>
  );
}

interface Run {
  driver: Driver;
  create: () => Driver;
  swapping: boolean;
  idx: number;
  ui: Ui;
  outcome: SendOutcome;
  servedBy: string | null;
  /** The gateway answered with real model calls (not mock models). */
  live: boolean;
  sun: boolean;
  navigating: boolean;
  cleanups: (() => void)[];
  roots: Map<HTMLElement, Root>;
  pop: PopoverDOM | null;
  body: string;
}

/** One Driver per step: reusing an active one leaves the previous popover on the page. */
function swapDriver(run: Run): void {
  if (run.driver.isActive()) {
    run.swapping = true;
    run.driver.destroy();
    run.swapping = false;
  }
  run.driver = run.create();
}

const visible = (sel: string): Element | null => {
  if (!sel) return null;
  try {
    const el = document.querySelector(sel);
    return el && el.getClientRects().length > 0 ? el : null;
  } catch {
    return null;
  }
};

function waitFor(sel: string, ms: number): Promise<Element | null> {
  return new Promise((resolve) => {
    const now = visible(sel);
    if (now) return resolve(now);
    const t = window.setTimeout(() => {
      mo.disconnect();
      resolve(visible(sel));
    }, ms);
    const mo = new MutationObserver(() => {
      const el = visible(sel);
      if (el) {
        window.clearTimeout(t);
        mo.disconnect();
        resolve(el);
      }
    });
    mo.observe(document.body, { childList: true, subtree: true });
  });
}

/**
 * The tour's host: lives in App, starts the tour from a button, from `?tour=1` or from where it left off across the page change
 * to /live, shows the one-time hint and the small corner button, and drives Driver.js (loaded only when a tour starts).
 */
export function SiteTourHost() {
  const loc = useLocation();
  const navigate = useNavigate();
  const pathRef = useRef(loc.pathname);
  pathRef.current = loc.pathname;
  const navRef = useRef(navigate);
  navRef.current = navigate;
  const runRef = useRef<Run | null>(null);
  const busy = useRef(false);
  const gen = useRef(0);
  const [touring, setTouring] = useState(false);
  const [hint, setHint] = useState(() => !hintDismissed());
  const [hidden, setHidden] = useState(() => buttonHidden());

  const available = useCallback((i: number): boolean => {
    const s = SITE_STEPS[i];
    const run = runRef.current;
    if (i === ANSWER_INDEX) return run?.outcome === "answer";
    if (!s.target) return true;
    if (s.path !== "any" && s.path !== pathRef.current) return true;
    return visible(s.target) !== null;
  }, []);

  const render = (run: Run) => {
    const pop = run.pop;
    if (!pop) return;
    let box = pop.wrapper.querySelector<HTMLElement>(".rt-avatars");
    if (!box) {
      box = document.createElement("div");
      box.className = "rt-avatars";
      pop.wrapper.insertBefore(box, pop.wrapper.firstChild);
    }
    let root = run.roots.get(box);
    if (!root) {
      root = createRoot(box);
      run.roots.set(box, root);
    }
    root.render(<TourAvatars ui={run.ui} />);
  };

  const setBody = (run: Run, text: string) => {
    run.body = text;
    if (run.pop) run.pop.description.textContent = text;
  };

  const teardown = (run: Run) => {
    run.cleanups.splice(0).forEach((f) => f());
    // Unmount after the current frame: the popover may still be rendering.
    const roots = [...run.roots.values()];
    run.roots.clear();
    window.setTimeout(() => roots.forEach((r) => r.unmount()), 0);
  };

  const finish = useCallback(() => {
    gen.current++;
    const run = runRef.current;
    clearTourState();
    runRef.current = null;
    busy.current = false;
    setTouring(false);
    if (run) {
      run.navigating = true;
      teardown(run);
      try {
        run.driver.destroy();
      } catch {
        /* already gone */
      }
    }
  }, []);

  const leaveTo = useCallback((idx: number, path: string) => {
    const run = runRef.current;
    if (!run) return;
    writeTourState(idx);
    runRef.current = null;
    busy.current = false;
    run.navigating = true;
    teardown(run);
    try {
      run.driver.destroy();
    } catch {
      /* already gone */
    }
    navRef.current(path);
  }, []);

  const show = useCallback(
    async (idx: number, dir: 1 | -1): Promise<void> => {
      const run = runRef.current;
      if (!run) return;
      run.cleanups.splice(0).forEach((f) => f());
      const s = SITE_STEPS[idx];
      if (s.path !== "any" && s.path !== pathRef.current) return leaveTo(idx, s.path);
      if (s.target) {
        const el = await waitFor(s.target, idx === SEND_INDEX ? 5000 : 1200);
        if (runRef.current !== run) return;
        if (!el) {
          const n = nextAvailable(N, idx, dir, available);
          return n == null ? finish() : show(n, dir);
        }
      }
      run.idx = idx;
      const last = idx === N - 1;
      const label = last ? "Finish" : idx === SEND_INDEX ? "Skip this step" : "Next";
      run.ui.mood = s.mood;
      run.ui.ultra = false;
      run.ui.lines = { nano: null, ultra: null };
      run.ui.squint = false;
      run.ui.sunglasses = run.sun;
      const el = s.target ? visible(s.target) : null;

      // What this step reacts to.
      if (idx === 1 && el) {
        const a = el.closest("a") ?? el;
        const go = () => {
          writeTourState(SEND_INDEX);
          const r = runRef.current;
          if (!r) return;
          runRef.current = null;
          busy.current = false;
          r.navigating = true;
          teardown(r);
          r.driver.destroy();
        };
        a.addEventListener("click", go, { once: true });
        run.cleanups.push(() => a.removeEventListener("click", go));
      }
      if (idx === SEND_INDEX && el) {
        run.outcome = "waiting";
        run.ui.lines = sendLines("waiting", null, true);
        const form = el.querySelector("form");
        const onSubmit = () => {
          run.outcome = "sending";
          run.ui.mood = sendMood("sending");
          run.ui.lines = sendLines("sending", null, true);
          setBody(run, "Sending your request. The answer takes a moment.");
          render(run);
        };
        form?.addEventListener("submit", onSubmit, { capture: true });
        const look = () => {
          if (run.outcome === "answer" || run.outcome === "limit" || run.outcome === "error") return;
          const who = el.querySelector<HTMLElement>('[data-tour="live-who"]');
          const limit = el.querySelector('[data-tour="live-limit"]');
          const err = el.querySelector('[data-tour="live-error"]');
          if (who) {
            run.outcome = "answer";
            run.servedBy = who.dataset.servedBy ?? null;
            run.live = (el as HTMLElement).dataset.live === "true";
            run.ui.mood = sendMood("answer");
            run.ui.lines = sendLines("answer", run.servedBy, run.live);
            render(run);
            const t = window.setTimeout(() => runRef.current === run && advance(1), 700);
            run.cleanups.push(() => window.clearTimeout(t));
          } else if (limit || err) {
            run.outcome = limit ? "limit" : "error";
            run.ui.mood = sendMood(run.outcome);
            run.ui.lines = sendLines(run.outcome, null, true);
            setBody(run, limit ? `${DAILY_LIMIT_TEXT} The tour carries on.` : "That request did not go through. The tour carries on without it.");
            if (run.pop) run.pop.nextButton.textContent = "Next";
            render(run);
          }
        };
        const mo = new MutationObserver(look);
        mo.observe(el, { childList: true, subtree: true });
        run.cleanups.push(() => {
          form?.removeEventListener("submit", onSubmit, { capture: true });
          mo.disconnect();
        });
        look();
      }
      let who: string | null = null;
      if (idx === ANSWER_INDEX && el) {
        who = (el.textContent ?? "").replace(/^\s*Answered by\s*/i, "").trim() || null;
        run.ui.ultra = run.servedBy === "teacher";
        run.ui.lines = sendLines("answer", run.servedBy, run.live);
        run.ui.mood = "happy";
      }
      if (idx === N - 2 && el) {
        const on = () => {
          run.ui.squint = true;
          render(run);
          const t = window.setTimeout(() => {
            const look = themeLook(document.documentElement.classList.contains("dark"), false);
            run.sun = look.sunglasses;
            run.ui.squint = look.squint;
            run.ui.sunglasses = look.sunglasses;
            render(run);
          }, 650);
          run.cleanups.push(() => window.clearTimeout(t));
        };
        window.addEventListener("ratchet-theme", on);
        run.cleanups.push(() => window.removeEventListener("ratchet-theme", on));
      }

      run.body = idx === SEND_INDEX && run.outcome !== "waiting" ? run.body : stepBody(s.id, { exams: EXAM_NUMBERS, who });
      const step: DriveStep = {
        element: s.target ? (el as Element) : undefined,
        popover: {
          title: s.title,
          description: "",
          align: "center",
          showButtons: ["next", "close"],
          nextBtnText: label,
          doneBtnText: label,
          onPopoverRender: (pop) => {
            run.pop = pop;
            decorate(run, pop, idx, last);
          },
        },
      };
      swapDriver(run);
      run.driver.setSteps([step]);
      run.driver.drive(0);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const advance = useCallback(
    (dir: 1 | -1) => {
      const run = runRef.current;
      if (!run) return;
      const n = nextAvailable(N, run.idx, dir, available);
      if (n == null) {
        if (dir === 1) finish();
        return;
      }
      void show(n, dir);
    },
    [available, finish, show],
  );

  /** Fill the popover Driver drew: text, progress dots, Back, the avatars, and a live region. */
  const decorate = (run: Run, pop: PopoverDOM, idx: number, last: boolean) => {
    pop.wrapper.classList.add("ratchet-tour");
    pop.wrapper.setAttribute("role", "dialog");
    pop.wrapper.setAttribute("aria-label", "Guided tour");
    pop.description.setAttribute("aria-live", "polite");
    pop.description.textContent = run.body;
    pop.description.style.display = run.body || last ? "block" : "none";
    pop.progress.style.display = "flex";
    if (last) {
      const p = document.createElement("p");
      p.className = "mt-2";
      const link = (href: string, text: string) => {
        const a = document.createElement("a");
        a.href = href;
        a.textContent = text;
        if (/^https?:/.test(href)) {
          a.target = "_blank";
          a.rel = "noopener noreferrer";
        }
        return a;
      };
      p.append(link(DOCS_URL, "Read the docs"), " · ", link("/judges", "For judges"));
      const v = videoHref(REAL.links, "tour");
      if (v) p.append(" · ", link(v, "Watch the video"));
      pop.description.append(p);
    }
    pop.progress.replaceChildren();
    const text = document.createElement("span");
    text.textContent = progressText(idx, N);
    const dots = document.createElement("span");
    dots.className = "rt-dots";
    dots.setAttribute("aria-hidden", "true");
    for (let i = 0; i < N; i++) {
      const d = document.createElement("i");
      if (i <= idx) d.className = "on";
      dots.append(d);
    }
    pop.progress.append(text, dots);
    pop.footerButtons.querySelector(".rt-back")?.remove();
    if (nextAvailable(N, idx, -1, available) != null) {
      const back = document.createElement("button");
      back.type = "button";
      back.className = "rt-back";
      back.textContent = "Back";
      back.addEventListener("click", () => advance(-1));
      pop.footerButtons.insertBefore(back, pop.nextButton);
    }
    render(run);
  };

  const begin = useCallback(
    async (idx: number) => {
      if (busy.current) return;
      busy.current = true;
      const mine = ++gen.current;
      clearTourState();
      setTouring(true);
      try {
        const [{ driver: createDriver }] = await Promise.all([import("driver.js"), import("driver.js/dist/driver.css")]);
        if (mine !== gen.current || runRef.current) return;
        const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        const config: Config = {
          animate: !reduce,
          smoothScroll: !reduce,
          overlayColor: "#05070d",
          overlayOpacity: 0.55,
          stagePadding: 8,
          stageRadius: 14,
          allowKeyboardControl: true,
          allowClose: true,
          overlayClickBehavior: "none",
          disableActiveInteraction: false,
          popoverClass: "ratchet-tour",
          showProgress: true,
          onNextClick: () => advance(1),
          onDoneClick: () => advance(1),
          onPrevClick: () => advance(-1),
          onCloseClick: () => finish(),
          onDestroyed: () => {
            const r = runRef.current;
            if (r && !r.navigating && !r.swapping) finish();
          },
        };
        const driver = createDriver(config);
        runRef.current = {
          driver,
          create: () => createDriver(config),
          swapping: false,
          idx,
          ui: { mood: "idle", ultra: false, lines: { nano: null, ultra: null }, sunglasses: false, squint: false },
          outcome: "waiting",
          servedBy: null,
          live: false,
          sun: false,
          navigating: false,
          cleanups: [],
          roots: new Map(),
          pop: null,
          body: "",
        };
        await show(idx, 1);
      } catch {
        finish();
      }
    },
    [advance, finish, show],
  );

  // A button elsewhere asks for the tour: from the story page it starts here, from another page it goes there first.
  useEffect(() => {
    const on = () => {
      setHint(false);
      dismissHint();
      if (runRef.current) finish();
      if (pathRef.current === "/") void begin(0);
      else {
        writeTourState(0);
        navRef.current("/");
      }
    };
    window.addEventListener(TOUR_EVENT, on);
    return () => window.removeEventListener(TOUR_EVENT, on);
  }, [begin, finish]);

  // `?tour=1`, or the tour's place after a page change.
  useEffect(() => {
    if (wantsSiteTour(loc.pathname, loc.search)) {
      const params = new URLSearchParams(loc.search);
      params.delete("tour");
      params.delete("tour=true");
      const rest = params.toString();
      navRef.current({ pathname: loc.pathname, search: rest ? `?${rest}` : "", hash: loc.hash }, { replace: true });
      setHint(false);
      void begin(loc.pathname === "/live" ? SEND_INDEX : 0);
      return;
    }
    const saved = readTourState();
    if (saved && !runRef.current) void begin(saved.step);
  }, [loc.pathname, loc.search, loc.hash, begin]);

  const closeHint = () => {
    dismissHint();
    setHint(false);
  };
  const showHint = hint && !touring && loc.pathname === "/";
  const showButton = !hint && !hidden && !touring;

  return (
    <>
      <AnimatePresence>
        {showHint && (
          <motion.aside
            key="hint"
            aria-label="Guided tour"
            initial={{ opacity: 0, y: 16, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 12, transition: { duration: 0.2, delay: 0 } }}
            transition={{ duration: 0.4, ease: [0.22, 1, 0.36, 1], delay: 1.2 }}
            className="fixed bottom-4 right-4 z-40 flex max-w-[calc(100vw-2rem)] items-end gap-3 rounded-3xl border border-line-strong bg-surface/90 p-3 pr-4 shadow-[0_20px_50px_-20px_rgb(0_0_0/0.45)] backdrop-blur-xl sm:bottom-6 sm:right-6"
          >
            <Avatar who="nano" expression="wave" size={64} />
            <div className="min-w-0 pb-1">
              <p role="status" className="pr-6 text-[14px] font-medium leading-snug text-ink">
                First time? Want a 60-second tour?
              </p>
              <div className="mt-2 flex items-center gap-2">
                <button
                  type="button"
                  onClick={startSiteTour}
                  className="rounded-full bg-ink px-3.5 py-1.5 text-[13px] font-medium text-bg transition-opacity hover:opacity-90"
                >
                  Start the tour
                </button>
                <button type="button" onClick={closeHint} className="rounded-full px-2.5 py-1.5 text-[13px] text-muted transition-colors hover:text-ink">
                  Not now
                </button>
              </div>
            </div>
            <button
              type="button"
              onClick={closeHint}
              aria-label="Dismiss the tour hint"
              className="absolute right-2 top-2 inline-flex size-7 items-center justify-center rounded-full text-muted transition-colors hover:bg-surface-2 hover:text-ink"
            >
              <X className="size-4" aria-hidden />
            </button>
          </motion.aside>
        )}
      </AnimatePresence>
      {showButton && (
        <div className="group fixed bottom-4 right-4 z-30 sm:bottom-6 sm:right-6">
          <button
            type="button"
            onClick={startSiteTour}
            aria-label="Take the 60-second tour"
            title="Take the 60-second tour"
            className="inline-flex size-12 items-center justify-center rounded-full border border-line-strong bg-surface/90 shadow-card backdrop-blur-md transition-transform hover:-translate-y-0.5 motion-reduce:transition-none"
          >
            <Avatar who="nano" expression="idle" size={34} />
          </button>
          <button
            type="button"
            onClick={() => {
              setButtonHidden(true);
              setHidden(true);
            }}
            aria-label="Hide the tour button"
            className="absolute -right-1 -top-1 inline-flex size-5 items-center justify-center rounded-full border border-line bg-surface text-muted opacity-0 transition-opacity hover:text-ink focus-visible:opacity-100 group-hover:opacity-100 group-focus-within:opacity-100"
          >
            <X className="size-3" aria-hidden />
          </button>
        </div>
      )}
    </>
  );
}
