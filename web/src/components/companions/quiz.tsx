import { useCallback, useEffect, useRef, useState } from "react";
import { Check, HelpCircle, X } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  BASE_LABEL,
  MARK_NOTE,
  PROVENANCE,
  examNote,
  parseQuiz,
  quizPicker,
  reveal,
  verdictLine,
  type Quiz,
  type QuizRow,
} from "./quiz-model";

export type QuizPhase = "ask" | "right" | "wrong";

function Mark({ right }: { right: boolean }) {
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11.5px] font-medium", right ? "bg-good-soft text-good-ink" : "bg-bad-soft text-bad")}>
      {right ? <Check className="size-3.5" aria-hidden /> : <X className="size-3.5" aria-hidden />}
      {right ? "Right" : "Wrong"}
    </span>
  );
}

function Sql({ label, sql, right }: { label: string; sql: string; right?: boolean }) {
  return (
    <div>
      <p className="mb-1 flex items-center gap-2 text-[12px] font-medium text-ink-2">
        {label}
        {right !== undefined && <Mark right={right} />}
      </p>
      <pre className="max-h-28 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-line bg-bg p-2 font-mono text-[11.5px] leading-relaxed text-ink">{sql}</pre>
    </div>
  );
}

/**
 * "Quiz Nano": a real question from the published exams, Nano's recorded answer, and Ultra's mark: right or wrong exactly as the
 * published scoring gave it. When Nano was wrong, Ultra's own recorded answer and the reference query follow. The questions load
 * from their own small file the first time; no model is called, and the card says the answers are recorded.
 */
export function QuizCard({ reduce, autoStart, onPhase }: { reduce: boolean; autoStart: number; onPhase: (p: QuizPhase, row: QuizRow) => void }) {
  const quiz = useRef<Quiz | null>(null);
  const picker = useRef<ReturnType<typeof quizPicker> | null>(null);
  const [row, setRow] = useState<QuizRow | null>(null);
  const [phase, setPhase] = useState<"idle" | "loading" | "ask" | "marked" | "error">("idle");
  const timer = useRef(0);

  const next = useCallback(async () => {
    window.clearTimeout(timer.current);
    try {
      if (!quiz.current) {
        setPhase("loading");
        quiz.current = parseQuiz((await import("@/data/quiz.json?raw")).default);
        picker.current = quizPicker(quiz.current.rows);
      }
      const r = picker.current!.next()!;
      setRow(r);
      setPhase("ask");
      onPhase("ask", r);
      timer.current = window.setTimeout(
        () => {
          setPhase("marked");
          onPhase(r.nano.right ? "right" : "wrong", r);
        },
        reduce ? 0 : 1600,
      );
    } catch {
      setPhase("error");
    }
  }, [onPhase, reduce]);

  useEffect(() => () => window.clearTimeout(timer.current), []);
  // "Quiz me" from Nano's bubble opens the sheet and starts a question.
  useEffect(() => {
    if (autoStart > 0) void next();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoStart]);

  return (
    <section aria-labelledby="cp-quiz-title" className="rounded-2xl border border-line bg-surface-2/60 p-3.5">
      <h3 id="cp-quiz-title" className="flex items-center gap-1.5 text-[14px] font-semibold tracking-[-0.01em] text-ink">
        <HelpCircle className="size-4 text-owned-ink" aria-hidden /> Quiz Nano
      </h3>
      <p className="mt-0.5 text-[12.5px] leading-snug text-muted">Real questions from the published exams, with the answers Nano gave. They are recorded, not live.</p>
      {phase === "idle" || phase === "error" ? (
        <div className="mt-3">
          <button type="button" onClick={() => void next()} className="inline-flex h-10 items-center rounded-full bg-ink px-4 text-[13.5px] font-medium text-bg">
            Quiz Nano
          </button>
          {phase === "error" && <p className="mt-2 text-[12.5px] text-ink-2">The questions did not load. Try again.</p>}
        </div>
      ) : phase === "loading" || !row ? (
        <p className="mt-3 text-[13px] text-muted" role="status">Loading the questions.</p>
      ) : (
        <div className="mt-3 space-y-3">
          <div>
            <p className="font-mono text-[11px] text-muted">
              {row.exam === "exam1" ? "Exam 1" : "Exam 2"} · {row.domain} · {row.difficulty}
            </p>
            <p className="mt-1 text-[14px] font-medium leading-snug text-ink">{row.question}</p>
          </div>
          {phase === "ask" ? (
            <p role="status" aria-live="polite" className="text-[13px] text-ink-2">
              Nano is looking at the answer he gave.
            </p>
          ) : (
            <>
              <p role="status" aria-live="polite" className={cn("flex items-center gap-1.5 text-[13.5px] font-medium", row.nano.right ? "text-good-ink" : "text-bad")}>
                {row.nano.right ? <Check className="size-4" aria-hidden /> : <X className="size-4" aria-hidden />}
                {verdictLine(row.nano.right)}
              </p>
              {reveal(row).map((b) => (
                <Sql key={b.label} {...b} />
              ))}
              <details>
                <summary className="cursor-pointer text-[12.5px] text-ink-2">{BASE_LABEL}</summary>
                <div className="mt-1.5">
                  <Sql label="Recorded on the same exam" sql={row.base.sql} right={row.base.right} />
                </div>
              </details>
              <button type="button" onClick={() => void next()} className="inline-flex h-9 items-center rounded-full border border-line-strong bg-surface px-4 text-[13px] font-medium text-ink">
                Next question
              </button>
            </>
          )}
          <p className="text-[11.5px] leading-snug text-muted">
            {MARK_NOTE} {examNote(row.exam)} {PROVENANCE}
          </p>
        </div>
      )}
    </section>
  );
}
