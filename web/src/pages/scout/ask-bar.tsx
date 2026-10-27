import { useEffect, useRef, useState, type FormEvent } from "react";
import { ArrowRight, CornerDownLeft, Search, Square } from "lucide-react";
import type { ScoutExamples } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useWaking } from "@/lib/waking-store";
import { RUN_WAKING, isWaking } from "@/lib/waking-model";
import { chipQuestions } from "./scout-model";

const MAX = 300;

/** Command-bar question input plus example chips. Mock mode: read-only input, chips start runs. */
export function AskBar({
  examples,
  examplesError,
  running,
  current,
  onRun,
  onStop,
  onRetryExamples,
  hint,
}: {
  examples: ScoutExamples | null;
  examplesError: string | null;
  running: boolean;
  current: string;
  onRun: (q: string) => void;
  onStop: () => void;
  onRetryExamples: () => void;
  hint: (msg: string | null) => void;
}) {
  const freeText = examples?.free_text === true;
  // A run is never started or retried for the visitor while the live gateway starts: Run waits for /healthz.
  const waking = isWaking(useWaking());
  const [value, setValue] = useState("");
  const inputRef = useRef<HTMLInputElement | null>(null);

  // Keep the input in sync with the question being run (chip clicks, retries).
  useEffect(() => {
    if (current) setValue(current);
  }, [current]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (running || waking) return;
    const q = value.trim();
    if (!q) {
      hint(freeText ? "Type a question first, or pick one of the examples." : "Pick one of the example questions below.");
      return;
    }
    if (!freeText && examples && !examples.examples.includes(q)) {
      hint("This gateway runs mock models, so it answers the example questions only.");
      return;
    }
    hint(null);
    onRun(q.slice(0, MAX));
  };

  const pick = (q: string) => {
    if (running || waking) return;
    setValue(q);
    hint(null);
    onRun(q);
  };

  const placeholder = !examples ? "Loading questions…" : freeText ? "Ask a question about a topic on the web" : "Pick a question below";

  return (
    <div>
      <form onSubmit={submit} role="search" aria-label="Ask Scout">
        <div
          className={cn(
            "group flex items-center gap-2 rounded-[20px] border border-line-strong bg-surface p-2 pl-4 shadow-card transition-[border-color,box-shadow] duration-200 sm:gap-3 sm:pl-5",
            "focus-within:border-owned focus-within:shadow-[0_0_0_4px_var(--owned-soft)]",
            running && "border-line",
          )}
        >
          <Search className="size-5 shrink-0 text-muted transition-colors group-focus-within:text-owned-ink" aria-hidden />
          <label htmlFor="scout-q" className="sr-only">
            Question for Scout
          </label>
          <input
            ref={inputRef}
            id="scout-q"
            name="q"
            type="text"
            value={value}
            onChange={(e) => freeText && setValue(e.target.value.slice(0, MAX))}
            readOnly={!freeText}
            disabled={running && freeText}
            maxLength={MAX}
            autoComplete="off"
            spellCheck={false}
            placeholder={placeholder}
            aria-describedby="scout-q-note"
            className={cn(
              "h-12 min-w-0 flex-1 bg-transparent text-[16px] text-ink outline-none placeholder:text-muted focus-visible:outline-none sm:h-14 sm:text-[18px]",
              !freeText && "cursor-default",
            )}
          />
          {freeText && !running && value.length > MAX * 0.8 && (
            <span className="hidden font-mono text-[11.5px] text-muted tabular sm:inline">
              {value.length}/{MAX}
            </span>
          )}
          {!running && (
            <kbd
              className="hidden items-center gap-1 rounded-md border border-line bg-surface-2 px-1.5 py-0.5 font-mono text-[11px] text-muted md:inline-flex"
              aria-hidden
            >
              <CornerDownLeft className="size-3" /> Enter
            </kbd>
          )}
          {running ? (
            <Button type="button" variant="outline" size="lg" onClick={onStop} className="h-11 px-4 sm:h-12 sm:px-5">
              <Square className="fill-current !size-3" aria-hidden /> Stop
            </Button>
          ) : (
            <Button type="submit" size="lg" className="h-11 px-4 sm:h-12 sm:px-6" disabled={!examples || waking}>
              {waking ? RUN_WAKING : <>Ask <ArrowRight aria-hidden /></>}
            </Button>
          )}
        </div>
      </form>

      <div
        className="-mx-4 mt-4 grid auto-cols-max grid-flow-col grid-rows-2 gap-2 overflow-x-auto px-4 pb-1 [mask-image:linear-gradient(to_right,#000_72%,transparent)] [scrollbar-width:none] sm:mx-0 sm:flex sm:flex-wrap sm:items-center sm:justify-center sm:overflow-visible sm:px-0 sm:pb-0 sm:[mask-image:none] [&::-webkit-scrollbar]:hidden"
        role="group"
        aria-label="Example questions"
      >
        <span className="mr-1 hidden shrink-0 font-mono text-[11.5px] uppercase tracking-[0.14em] text-muted sm:inline">Try</span>
        {chipQuestions(examples).map((q) => {
          const active = q === current;
          return (
            <button
              key={q}
              type="button"
              onClick={() => pick(q)}
              disabled={running || waking}
              className={cn(
                "shrink-0 whitespace-nowrap rounded-full border px-3 py-1.5 text-left text-[13.5px] leading-snug transition-[background,border-color,color,opacity] duration-200 sm:whitespace-normal",
                "disabled:cursor-not-allowed disabled:opacity-50",
                active
                  ? "border-owned/40 bg-owned-soft text-owned-ink"
                  : "border-line bg-surface text-ink-2 hover:border-line-strong hover:bg-surface-2 hover:text-ink",
              )}
            >
              {q}
            </button>
          );
        })}
        {!examples && !examplesError && (
          <span className="row-span-2 flex gap-2" aria-hidden>
            {[180, 150, 210, 170].map((w) => (
              <span key={w} className="h-8 animate-pulse rounded-full bg-surface-2" style={{ width: w }} />
            ))}
          </span>
        )}
        {examplesError && !waking && (
          <span className="text-[13.5px] text-bad">
            {examplesError}{" "}
            <button type="button" className="font-medium underline underline-offset-2" onClick={onRetryExamples}>
              Try again
            </button>
          </span>
        )}
      </div>
      <p id="scout-q-note" className="mt-3 text-[13px] text-muted sm:text-center">
        {examples && !freeText
          ? "With mock models, Scout answers the example questions only."
          : examples && freeText
            ? `Up to ${MAX} characters. Each run makes three web searches and at most 20 model calls.`
            : " "}
      </p>
    </div>
  );
}
