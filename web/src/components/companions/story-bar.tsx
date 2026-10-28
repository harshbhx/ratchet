import { forwardRef } from "react";
import { motion } from "motion/react";
import { ChevronLeft, ChevronRight, Pause, Play, SkipForward, X } from "lucide-react";
import { Character } from "./character";
import type { Look } from "./companion-model";
import type { StoryState, StoryStep } from "./story";
import { cn } from "@/lib/utils";

const BTN = "inline-flex h-9 items-center gap-1.5 rounded-full border border-line-strong bg-surface px-3 text-[13px] font-medium text-ink hover:bg-surface-2";

/**
 * The caption bar for "Watch Nano learn". The caption is in a polite live region. With motion: pause, resume, skip and exit while the
 * page scrolls itself. Under reduced motion nothing scrolls: the same captions are a stepper (previous, next, and a button to go to the
 * section). Esc exits (handled by the stage).
 */
export const StoryBar = forwardRef<
  HTMLDivElement,
  {
    steps: StoryStep[];
    state: StoryState;
    reduce: boolean;
    phone: boolean;
    looks: { nano: Look; ultra: Look };
    onPause: () => void;
    onResume: () => void;
    onNext: () => void;
    onPrev: () => void;
    onGo: () => void;
    onReplay: () => void;
    onExit: () => void;
  }
>(function StoryBar(p, ref) {
  const { steps, state, reduce, phone } = p;
  const done = state.status === "done";
  const step = steps[state.index]!;
  const paused = state.status === "paused";
  return (
    <motion.div
      ref={ref}
      role="region"
      aria-label="Watch Nano learn: captions"
      initial={reduce ? false : { opacity: 0, y: 24 }}
      animate={{ opacity: 1, y: 0 }}
      exit={reduce ? undefined : { opacity: 0, y: 24 }}
      transition={reduce ? { duration: 0 } : { type: "spring", stiffness: 300, damping: 28 }}
      className="fixed inset-x-3 bottom-3 z-[60] mx-auto max-w-3xl rounded-2xl border border-line-strong bg-surface/95 p-3 shadow-card backdrop-blur-md sm:p-3.5"
    >
      <div className="flex items-start gap-3">
        {phone && (
          <div aria-hidden className="flex shrink-0 items-end">
            <Character who="nano" look={{ ...p.looks.nano }} size={46} live={!reduce} />
            <Character who="ultra" look={{ ...p.looks.ultra }} size={54} live={!reduce} className="-ml-2" />
          </div>
        )}
        <div className="min-w-0 flex-1">
          <p className="font-mono text-[11px] uppercase tracking-[0.12em] text-muted">
            Watch Nano learn · {done ? "the end" : `${state.index + 1} of ${steps.length}`}
            {paused && " · paused"}
          </p>
          <p aria-live="polite" className="mt-1 text-pretty text-[14.5px] leading-snug text-ink sm:text-[15px]">
            {done ? "That is the story: rented intelligence, a student, an exam, a gate, and an adapter you keep. Try a real request on the live page." : step.caption}
          </p>
        </div>
        <button type="button" onClick={p.onExit} aria-label="Exit the story" className="inline-flex size-9 shrink-0 items-center justify-center rounded-full text-ink-2 hover:bg-surface-2">
          <X className="size-5" aria-hidden />
        </button>
      </div>
      <div className="mt-2.5 flex flex-wrap items-center gap-2">
        <div aria-hidden className="flex flex-1 items-center gap-1 pr-2">
          {steps.map((s, i) => (
            <span key={s.id} className={cn("h-1 min-w-3 flex-1 rounded-full", i < state.index || done ? "bg-owned" : i === state.index ? "bg-owned/60" : "bg-line-strong")} />
          ))}
        </div>
        {reduce ? (
          <>
            <button type="button" onClick={p.onPrev} disabled={state.index === 0 && !done} className={cn(BTN, "disabled:opacity-50")}>
              <ChevronLeft className="size-4" aria-hidden /> Previous
            </button>
            {!done && (
              <button type="button" onClick={p.onGo} className={BTN}>
                Go to this section
              </button>
            )}
            <button type="button" onClick={done ? p.onReplay : p.onNext} className={BTN}>
              {done ? "Replay" : "Next"} <ChevronRight className="size-4" aria-hidden />
            </button>
          </>
        ) : done ? (
          <button type="button" onClick={p.onReplay} className={BTN}>
            <Play className="size-4" aria-hidden /> Replay
          </button>
        ) : (
          <>
            <button type="button" onClick={paused ? p.onResume : p.onPause} className={BTN}>
              {paused ? <Play className="size-4" aria-hidden /> : <Pause className="size-4" aria-hidden />}
              {paused ? "Resume" : "Pause"}
            </button>
            <button type="button" onClick={p.onNext} className={BTN}>
              <SkipForward className="size-4" aria-hidden /> Skip
            </button>
          </>
        )}
        <button type="button" onClick={p.onExit} className={BTN}>
          Exit
        </button>
      </div>
    </motion.div>
  );
});
