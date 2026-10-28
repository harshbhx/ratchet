import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { motion } from "motion/react";
import { EyeOff, Play, RotateCcw, Send, X } from "lucide-react";
import type { Actor } from "./actor";
import { Companion } from "./companion";
import type { Look, Who } from "./companion-model";
import { GAME_STEPS, MAX_QUESTION, cleanQuestion, nextPhase, phaseCaption, phaseMs, thoughtBubbles, type GamePhase } from "./game";
import { LESSONS, LESSON_TOTAL, type LessonState } from "./lessons";
import { Sparkles } from "./sparkles";
import { cn } from "@/lib/utils";

const FOCUSABLE = 'a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])';

/** Progress ring: N of 7. The arc is drawn at its value, not animated. */
export function ProgressRing({ n, size = 36, className }: { n: number; size?: number; className?: string }) {
  const r = 15;
  return (
    <svg aria-hidden viewBox="0 0 36 36" width={size} height={size} className={className}>
      <circle cx="18" cy="18" r={r} fill="var(--surface)" stroke="var(--line-strong)" strokeWidth="3" />
      <circle cx="18" cy="18" r={r} fill="none" stroke="var(--owned)" strokeWidth="3" strokeLinecap="round" pathLength="100" strokeDasharray="100" strokeDashoffset={100 - (100 * n) / LESSON_TOTAL} transform="rotate(-90 18 18)" />
      <text x="18" y="22.2" textAnchor="middle" fill="var(--ink)" style={{ fontSize: 12, fontWeight: 700 }}>
        {n}
      </text>
    </svg>
  );
}

/**
 * The routing picture. A question card goes to Nano, who thinks three times and, unsure, passes it to Ultra. It is labelled as a
 * picture of how Ratchet routes a request: it sends nothing and shows no model output.
 */
function RoutingGame({
  reduce,
  looks,
  actors,
  live,
  onPhase,
  onPoke,
  onJump,
  onSend,
}: {
  reduce: boolean;
  looks: Record<Who, Look>;
  actors: Record<Who, Actor>;
  live: boolean;
  onPhase: (p: GamePhase, question: string) => void;
  onPoke: (w: Who) => void;
  onJump: (w: Who) => void;
  onSend: (q: string) => void;
}) {
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  const [phase, setPhase] = useState<GamePhase>("idle");
  const running = phase !== "idle" && phase !== "answer";

  useEffect(() => {
    if (phase === "idle") return;
    onPhase(phase, q);
    if (phase === "answer") return;
    const id = window.setTimeout(() => setPhase((p) => nextPhase(p)), phaseMs(phase));
    return () => window.clearTimeout(id);
    // onPhase only reads the latest props through the stage's refs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, q]);

  const start = (e: FormEvent) => {
    e.preventDefault();
    const clean = cleanQuestion(text);
    if (!clean || running) return;
    setQ(clean);
    setPhase("catch");
  };
  const cardX = phase === "catch" || phase.startsWith("try") || phase === "unsure" ? -92 : phase === "throw" || phase === "answer" ? 92 : 0;
  const bubbles = thoughtBubbles(phase);

  return (
    <section aria-labelledby="cp-game-title" className="rounded-2xl border border-line bg-surface-2/60 p-3.5">
      <h3 id="cp-game-title" className="text-[14px] font-semibold tracking-[-0.01em] text-ink">
        How Ratchet routes a request
      </h3>
      <p className="mt-0.5 text-[12.5px] leading-snug text-muted">A picture of the routing, not an answer. Nothing is sent from here.</p>
      <div className="relative mt-3 flex items-end justify-between px-1 pt-14">
        <div className="relative">
          <Companion who="nano" look={looks.nano} size={74} actor={actors.nano} live={live} draggable={false} onPoke={() => onPoke("nano")} onJump={() => onJump("nano")} />
          <div aria-hidden className="absolute left-[4.4rem] top-1 flex items-end gap-1">
            {[0, 1, 2].map((i) => (
              <span key={i} className={cn("size-2 rounded-full border border-line-strong bg-surface transition-opacity", i < bubbles ? "opacity-100" : "opacity-0")} style={{ marginBottom: i * 4 }} />
            ))}
          </div>
        </div>
        <motion.div
          aria-hidden
          animate={{ x: cardX, y: phase === "throw" ? -8 : 0, scale: phase === "idle" ? 0.9 : 1, opacity: phase === "idle" ? 0.35 : 1 }}
          transition={reduce ? { duration: 0 } : { type: "spring", stiffness: 160, damping: 14 }}
          className="absolute left-1/2 top-0 -ml-14 w-28 rounded-lg border border-line-strong bg-surface px-2 py-1.5 text-[11px] leading-tight text-ink-2 shadow-card"
        >
          <span className="font-mono text-[9px] uppercase tracking-wider text-muted">question</span>
          <span className="line-clamp-2 block">{q || "your question"}</span>
        </motion.div>
        <Companion who="ultra" look={looks.ultra} size={92} actor={actors.ultra} live={live} draggable={false} onPoke={() => onPoke("ultra")} onJump={() => onJump("ultra")} />
      </div>
      <div className="mt-2 flex items-center gap-1.5" aria-hidden>
        {GAME_STEPS.map((s) => (
          <span key={s.phase} className={cn("h-1 flex-1 rounded-full", GAME_STEPS.findIndex((x) => x.phase === phase) >= GAME_STEPS.findIndex((x) => x.phase === s.phase) && phase !== "idle" ? "bg-owned" : "bg-line-strong")} />
        ))}
      </div>
      <p role="status" aria-live="polite" className="mt-2 min-h-[1.25rem] text-[13px] text-ink-2">
        {phase === "idle" ? "Type a question and watch where it goes." : phaseCaption(phase)}
      </p>
      <form onSubmit={start} className="mt-2 flex gap-2">
        <label htmlFor="cp-q" className="sr-only">
          A question to route
        </label>
        <input
          id="cp-q"
          value={text}
          onChange={(e) => setText(e.target.value.slice(0, MAX_QUESTION))}
          maxLength={MAX_QUESTION}
          autoComplete="off"
          spellCheck={false}
          placeholder="e.g. Where is my order?"
          className="h-10 min-w-0 flex-1 rounded-xl border border-line-strong bg-bg px-3 text-[16px] text-ink outline-none focus-visible:border-owned sm:text-[14px]"
        />
        <button type="submit" disabled={running || !cleanQuestion(text)} className="inline-flex h-10 items-center gap-1.5 rounded-full bg-ink px-4 text-[13.5px] font-medium text-bg transition-opacity disabled:opacity-50">
          <Send className="size-4" aria-hidden /> Route it
        </button>
      </form>
      {phase === "answer" && (
        <div className="mt-3 flex flex-wrap gap-2">
          <button type="button" onClick={() => onSend(q)} className="inline-flex h-10 items-center gap-1.5 rounded-full bg-owned px-4 text-[13.5px] font-medium text-white dark:text-[#06101f]">
            <Send className="size-4" aria-hidden /> Send it for real
          </button>
          <button type="button" onClick={() => setPhase("catch")} className="inline-flex h-10 items-center gap-1.5 rounded-full border border-line-strong bg-surface px-4 text-[13.5px] font-medium text-ink">
            <RotateCcw className="size-4" aria-hidden /> Again
          </button>
          <p className="w-full text-[12px] leading-snug text-muted">Opens the live page with your question filled in. A real call: the daily limits apply.</p>
        </div>
      )}
    </section>
  );
}

export interface SheetProps {
  phone: boolean;
  reduce: boolean;
  live: boolean;
  looks: Record<Who, Look>;
  actors: Record<Who, Actor>;
  lessons: LessonState;
  line: string;
  celebrate: number;
  onClose: () => void;
  onStory: () => void;
  onHide: () => void;
  onPoke: (w: Who) => void;
  onJump: (w: Who) => void;
  onPhase: (p: GamePhase, q: string) => void;
  onSend: (q: string) => void;
}

/** The companion sheet: a dialog with a focus trap (Tab stays inside, Esc closes, focus returns to what opened it). */
export function Sheet(p: SheetProps) {
  const root = useRef<HTMLDivElement | null>(null);
  const opener = useRef<Element | null>(null);

  useEffect(() => {
    opener.current = document.activeElement;
    const first = root.current?.querySelector<HTMLElement>("[data-cp-first]");
    first?.focus();
    return () => {
      (opener.current as HTMLElement | null)?.focus?.();
    };
  }, []);

  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      p.onClose();
      return;
    }
    if (e.key !== "Tab") return;
    const items = Array.from(root.current?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? []).filter((el) => el.offsetParent !== null || el === document.activeElement);
    if (!items.length) return;
    const first = items[0]!;
    const last = items[items.length - 1]!;
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  };

  const n = p.lessons.ids.length;
  return (
    <div className="fixed inset-0 z-[70] flex items-end justify-center sm:items-end sm:justify-end sm:p-4" onKeyDown={onKey}>
      <div aria-hidden className="absolute inset-0 bg-ink/35 backdrop-blur-[2px]" onClick={p.onClose} />
      <motion.div
        ref={root}
        role="dialog"
        aria-modal="true"
        aria-labelledby="cp-title"
        initial={p.reduce ? false : { opacity: 0, y: 40 }}
        animate={{ opacity: 1, y: 0 }}
        transition={p.reduce ? { duration: 0 } : { type: "spring", stiffness: 300, damping: 28 }}
        className="relative flex max-h-[88dvh] w-full flex-col overflow-hidden rounded-t-3xl border border-line bg-surface shadow-card sm:w-[26rem] sm:rounded-3xl"
      >
        <div className="flex items-center gap-3 border-b border-line px-4 py-3">
          <ProgressRing n={n} size={34} />
          <div className="min-w-0 flex-1">
            <h2 id="cp-title" className="text-[15px] font-semibold tracking-[-0.01em] text-ink">
              Nano &amp; Ultra
            </h2>
            <p className="text-[12.5px] text-muted">Nano learned {n} of {LESSON_TOTAL} lessons from your visit</p>
          </div>
          <button type="button" data-cp-first onClick={p.onClose} aria-label="Close" className="inline-flex size-9 items-center justify-center rounded-full text-ink-2 hover:bg-surface-2">
            <X className="size-5" aria-hidden />
          </button>
        </div>
        <div className="space-y-4 overflow-y-auto px-4 py-4">
          <div className="relative">
            <Sparkles burst={p.celebrate} />
            <p className="min-h-[2.5rem] text-center text-[13px] italic text-ink-2" aria-hidden>
              {p.line || "Poke us. Double-click makes us jump."}
            </p>
          </div>
          <RoutingGame reduce={p.reduce} looks={p.looks} actors={p.actors} live={p.live} onPhase={p.onPhase} onPoke={p.onPoke} onJump={p.onJump} onSend={p.onSend} />
          <button type="button" onClick={p.onStory} className="flex h-11 w-full items-center justify-center gap-2 rounded-full border border-line-strong bg-surface-2 text-[14px] font-medium text-ink hover:bg-line">
            <Play className="size-4" aria-hidden /> Watch Nano learn
            <span className="text-[12px] font-normal text-muted">about 80 seconds</span>
          </button>
          <section aria-labelledby="cp-lessons">
            <h3 id="cp-lessons" className="font-mono text-[11px] uppercase tracking-[0.12em] text-muted">
              Lessons
            </h3>
            <ul className="mt-2 grid gap-1.5">
              {LESSONS.map((l) => {
                const got = p.lessons.ids.includes(l.id);
                return (
                  <li key={l.id} className="flex items-start gap-2 text-[13px]">
                    <span aria-hidden className={cn("mt-0.5 inline-flex size-4 shrink-0 items-center justify-center rounded-full text-[10px] font-bold", got ? "bg-good text-white dark:text-[#06101f]" : "border border-line-strong text-transparent")}>
                      ✓
                    </span>
                    <span className={got ? "text-ink" : "text-muted"}>
                      {l.label}
                      <span className="sr-only">{got ? " (learned)" : ` (not yet: ${l.hint})`}</span>
                      {!got && <span aria-hidden className="block text-[11.5px] text-muted/80">{l.hint}</span>}
                    </span>
                  </li>
                );
              })}
            </ul>
          </section>
        </div>
        <div className="flex items-center justify-between gap-3 border-t border-line px-4 py-3">
          <button type="button" onClick={p.onHide} className="inline-flex items-center gap-1.5 rounded-full px-2 py-1.5 text-[13px] text-muted hover:text-ink">
            <EyeOff className="size-4" aria-hidden /> Hide Nano &amp; Ultra
          </button>
          <span className="text-[12px] text-muted">Remembered on this device</span>
        </div>
      </motion.div>
    </div>
  );
}
