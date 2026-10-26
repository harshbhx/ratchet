import { useState, type FormEvent } from "react";
import { LoaderCircle, Radio, Send, Video } from "lucide-react";
import type { Mode } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { WhoBadge } from "@/components/ui/who";
import { cn } from "@/lib/utils";
import { gatewayFetch, useWaking } from "@/lib/waking-store";
import { RUN_WAKING, isWaking } from "@/lib/waking-model";
import {
  DAILY_LIMIT_TEXT,
  DAILY_NOTE,
  DEFAULT_PROMPT,
  EMPTY_TEXT,
  MAX_PROMPT_CHARS,
  RECORDED_HEADING,
  buildBody,
  errorText,
  isDailyLimit,
  parseAnswer,
  recordedAnswer,
  recordedLabel,
  resetsIn,
  retryAfterSeconds,
  type Answer,
} from "../try-request-model";
import { RECORDED_TRIES } from "../recorded-tries";

type State =
  | { kind: "idle" }
  | { kind: "sending" }
  | { kind: "done"; answer: Answer }
  | { kind: "error"; message: string }
  /** The edge's daily limit: the seconds to 00:00 UTC when it sent them. Recorded answers are offered instead of a live call. */
  | { kind: "limit"; waitSec: number | null };

/**
 * "Try a real request": one short chat request through this gateway's keyless /v1 path (the live Worker adds the demo key).
 * It is sent once and never retried, and it waits while the demo wakes up. `mode` comes from the page's stats poll: on the
 * simulation host the same box sends a request to mock models and says so.
 */
export function LiveRequest({ mode, className }: { mode: Mode | null; className?: string }) {
  const waking = isWaking(useWaking());
  const [prompt, setPrompt] = useState(DEFAULT_PROMPT);
  const [state, setState] = useState<State>({ kind: "idle" });
  const sending = state.kind === "sending";
  const [picked, setPicked] = useState<number | null>(null);
  const live = mode === "live";
  const limited = state.kind === "limit";

  const send = async (e: FormEvent) => {
    e.preventDefault();
    if (sending || waking || limited || !mode || !prompt.trim()) return;
    setState({ kind: "sending" });
    try {
      const r = await gatewayFetch("/v1/chat/completions", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(buildBody(prompt)),
      });
      if (!r.ok) {
        // The body is read once. Only the edge's own daily-limit code is the daily limit; any other 429 keeps the generic message.
        const body = r.status === 429 ? await r.json().catch(() => null) : null;
        if (isDailyLimit(r.status, body)) {
          setPicked(null);
          setState({ kind: "limit", waitSec: retryAfterSeconds(r.headers.get("retry-after")) });
        } else setState({ kind: "error", message: errorText(r.status) });
        return;
      }
      const answer = parseAnswer(await r.json());
      setState(answer && answer.text ? { kind: "done", answer } : { kind: "error", message: EMPTY_TEXT });
    } catch {
      setState({ kind: "error", message: errorText(0) });
    }
  };

  return (
    <div className={cn("mt-6 rounded-2xl border border-line bg-surface p-5 shadow-card sm:p-6", className)} aria-labelledby="live-req-title">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <h3 id="live-req-title" className="text-[16px] font-semibold tracking-[-0.01em] text-ink">
          Try a real request
        </h3>
        {mode && (
          <Badge tone={live ? "good" : "outline"}>
            <Radio aria-hidden /> {live ? "Live call" : "Simulation"}
          </Badge>
        )}
      </div>
      <p className="mt-1.5 max-w-[66ch] text-[13.5px] leading-relaxed text-muted">
        {live
          ? "This sends one short request to the live gateway. It is a real model call and costs a small amount, so each visitor gets a few a day. Your text is not stored."
          : "This sends one short request to mock models. The answer is simulated."}
      </p>
      <form onSubmit={send} className="mt-4 flex flex-col gap-2.5 sm:flex-row">
        <label htmlFor="live-req" className="sr-only">
          Your question
        </label>
        <input
          id="live-req"
          type="text"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value.slice(0, MAX_PROMPT_CHARS))}
          maxLength={MAX_PROMPT_CHARS}
          autoComplete="off"
          spellCheck={false}
          className="h-11 w-full min-w-0 shrink-0 rounded-xl border border-line-strong bg-bg px-3.5 text-[16px] text-ink outline-none focus-visible:border-owned focus-visible:shadow-[0_0_0_4px_var(--owned-soft)] sm:w-auto sm:flex-1 sm:text-[15px]"
        />
        <Button type="submit" size="md" className="h-11 sm:px-5" disabled={sending || waking || limited || !mode || !prompt.trim()}>
          {waking ? (
            RUN_WAKING
          ) : sending ? (
            <>
              <LoaderCircle className="animate-spin motion-reduce:animate-none" aria-hidden /> Sending
            </>
          ) : (
            <>
              <Send aria-hidden /> Send once
            </>
          )}
        </Button>
      </form>
      {live && !limited && <p className="mt-2 text-[12.5px] text-muted">{DAILY_NOTE}</p>}
      <div role="status" aria-live="polite" className="mt-4 min-h-[3.25rem]">
        {sending && (
          <div aria-busy="true" aria-label="Waiting for the answer" className="space-y-2">
            <div className="h-4 w-3/4 animate-pulse rounded bg-surface-2" />
            <div className="h-4 w-1/2 animate-pulse rounded bg-surface-2" />
          </div>
        )}
        {state.kind === "done" && (
          <div>
            <p className="whitespace-pre-wrap text-[15px] leading-relaxed text-ink">{state.answer.text}</p>
            <p className="mt-2.5 flex flex-wrap items-center gap-2 text-[12.5px] text-muted">
              {state.answer.servedBy ? (
                <>
                  Answered by <WhoBadge servedBy={state.answer.servedBy} model={state.answer.model} route={state.answer.route} simulated={!live} />
                </>
              ) : (
                "The reply did not say which model answered."
              )}
            </p>
          </div>
        )}
        {state.kind === "error" && <p className="text-[14px] text-ink-2">{state.message}</p>}
        {state.kind === "limit" && (
          <p className="text-[14.5px] leading-relaxed text-ink">
            {DAILY_LIMIT_TEXT}
            {resetsIn(state.waitSec) && <span className="text-ink-2"> {resetsIn(state.waitSec)}</span>}
          </p>
        )}
      </div>
      {state.kind === "limit" && RECORDED_TRIES.length > 0 && (
        <div className="mt-4 border-t border-line pt-4">
          <p className="text-[13.5px] font-medium text-ink-2" id="recorded-tries-heading">
            {RECORDED_HEADING}
          </p>
          <div role="group" aria-labelledby="recorded-tries-heading" className="mt-2.5 flex flex-wrap gap-2">
            {RECORDED_TRIES.map((t, i) => (
              <button
                key={t.prompt}
                type="button"
                aria-pressed={picked === i}
                onClick={() => setPicked(i)}
                className={cn(
                  "rounded-xl border px-3 py-2 text-left text-[13.5px] leading-snug transition-colors hover:border-line-strong hover:text-ink",
                  picked === i ? "border-owned bg-owned-soft text-ink" : "border-line bg-bg text-ink-2",
                )}
              >
                {t.prompt}
              </button>
            ))}
          </div>
          <div aria-live="polite" className="mt-4">
            {picked !== null && RECORDED_TRIES[picked] && <RecordedAnswer index={picked} />}
          </div>
        </div>
      )}
    </div>
  );
}

/** One recorded answer in the live answer's layout, with its badge, model and route and the words that say it is not live. */
function RecordedAnswer({ index }: { index: number }) {
  const t = RECORDED_TRIES[index];
  const a = recordedAnswer(t);
  return (
    <div>
      <Badge tone="outline">
        <Video aria-hidden /> {recordedLabel(t.recorded)}
      </Badge>
      <p className="mt-2.5 whitespace-pre-wrap text-[15px] leading-relaxed text-ink">{a.text}</p>
      <p className="mt-2.5 flex flex-wrap items-center gap-x-2 gap-y-1.5 text-[12.5px] text-muted">
        Answered by {a.servedBy && <WhoBadge servedBy={a.servedBy} model={a.model} route={a.route} simulated={false} />}
        <span className="font-mono text-[12px]">{a.model}</span>
        <span className="font-mono text-[12px]">route {a.route}</span>
      </p>
    </div>
  );
}
