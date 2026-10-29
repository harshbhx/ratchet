import { useState, type FormEvent } from "react";
import { useLocation } from "react-router";
import { LoaderCircle, Radio, Send } from "lucide-react";
import { signal } from "@/components/companions/companion-bus";
import type { Mode } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { WhoBadge } from "@/components/ui/who";
import { cn } from "@/lib/utils";
import { gatewayFetch, useWaking } from "@/lib/waking-store";
import { RUN_WAKING, isWaking } from "@/lib/waking-model";
import { HumanRow } from "@/components/human-row";
import { JudgeKey } from "@/components/judge-key";
import { keyErrorText, keyHeaders, remainingFromHeaders } from "@/lib/judge-key-model";
import { useJudgeKey } from "@/lib/use-judge-key";
import { failureOf } from "@/lib/human-model";
import { useHuman } from "@/lib/use-human";
import { tokenHeaders } from "@/lib/turnstile-model";
import {
  DAILY_LIMIT_TEXT,
  DAILY_NOTE,
  DEFAULT_PROMPT,
  EMPTY_TEXT,
  MAX_PROMPT_CHARS,
  buildBody,
  errorText,
  isDailyLimit,
  parseAnswer,
  type Answer,
} from "../try-request-model";

type State =
  | { kind: "idle" }
  | { kind: "sending" }
  | { kind: "done"; answer: Answer }
  | { kind: "error"; message: string }
  /** The edge's daily limit: a plain message, no live call. */
  | { kind: "limit" };

/**
 * "Try a real request": one short chat request through this gateway's keyless /v1 path (the live Worker adds the gateway key).
 * It is sent once and never retried, and it waits while the gateway wakes up. `mode` comes from the page's stats poll: on a mock gateway the same box sends a request to mock models and says so.
 */
export function LiveRequest({ mode, className }: { mode: Mode | null; className?: string }) {
  const waking = isWaking(useWaking());
  // Nano and Ultra's "Send it for real" hands the visitor's own text over in the navigation state (never in the URL).
  const handed = (useLocation().state as { companionPrefill?: unknown } | null)?.companionPrefill;
  const [prompt, setPrompt] = useState(typeof handed === "string" && handed.trim() ? handed.slice(0, MAX_PROMPT_CHARS) : DEFAULT_PROMPT);
  const [state, setState] = useState<State>({ kind: "idle" });
  const sending = state.kind === "sending";
  const live = mode === "live";
  const limited = state.kind === "limit";

  // The human check, drawn inline under the button. Once per visit: after one pass the Worker's cookie covers every later send and the row stays closed.
  const human = useHuman(false);
  // A judge's own key: calls carry it as Authorization and skip the human check. How many calls it has left comes from the gateway's own header.
  const { key } = useJudgeKey();
  const [left, setLeft] = useState<number | null>(null);
  const checking = human.state.phase === "checking" || human.state.phase === "interactive";

  const send = async (e?: FormEvent) => {
    e?.preventDefault();
    if (sending || checking || waking || limited || !mode || !prompt.trim()) return;
    // First the check (nothing happens here when it is off or already passed); the send then goes on by itself, the visitor does not click again.
    // A token is single-use and a send is never repeated, so a token is never reused; Try again asks for a new one.
    const gate = key ? { go: true as const, token: null } : await human.gate();
    if (!gate.go) return;
    setState({ kind: "sending" });
    signal({ type: "live", phase: "sent" });
    try {
      const r = await gatewayFetch("/v1/chat/completions", {
        method: "POST",
        headers: { "content-type": "application/json", ...(key ? keyHeaders(key) : tokenHeaders(gate.token)) },
        body: JSON.stringify(buildBody(prompt)),
      });
      if (key) setLeft(remainingFromHeaders(r.headers));
      if (!r.ok) {
        const mine = key && (r.status === 401 || r.status === 429) ? keyErrorText(r.status, r.status === 429 ? await r.json().catch(() => null) : null) : null;
        if (mine) {
          setState({ kind: "error", message: mine });
          signal({ type: "live", phase: "error" });
          return;
        }
        // The body is read once. Only the edge's own daily-limit code is the daily limit; any other 429 keeps the generic message.
        const body = r.status === 429 || r.status === 403 ? await r.json().catch(() => null) : null;
        const refused = failureOf(r.status, (body as { error?: { code?: unknown } } | null)?.error?.code);
        if (refused) {
          // The Worker refused the check itself: the row under the button says so, with Try again.
          human.report(refused);
          setState({ kind: "idle" });
          return;
        }
        if (isDailyLimit(r.status, body)) {
          setState({ kind: "limit" });
          signal({ type: "live", phase: "limit" });
        } else {
          setState({ kind: "error", message: errorText(r.status) });
          signal({ type: "live", phase: "error" });
        }
        return;
      }
      if (!key) human.report(null);
      const answer = parseAnswer(await r.json());
      setState(answer && answer.text ? { kind: "done", answer } : { kind: "error", message: EMPTY_TEXT });
      signal({ type: "live", phase: answer && answer.text ? "answer" : "error" });
    } catch {
      setState({ kind: "error", message: errorText(0) });
      signal({ type: "live", phase: "error" });
    }
  };

  return (
    <div data-tour="live-request" data-live={live ? "true" : "false"} className={cn("mt-6 rounded-2xl border border-line bg-surface p-5 shadow-card sm:p-6", className)} aria-labelledby="live-req-title">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <h3 id="live-req-title" className="text-[16px] font-semibold tracking-[-0.01em] text-ink">
          Try a real request
        </h3>
        {mode && (
          <Badge tone={live ? "good" : "outline"}>
            <Radio aria-hidden /> {live ? "Live call" : "Mock models"}
          </Badge>
        )}
      </div>
      <p className="mt-1.5 max-w-[66ch] text-[13.5px] leading-relaxed text-muted">
        {live
          ? "This sends one short request to the live gateway. It is a real model call and costs a small amount, so each visitor gets a few a day. Your text is not stored."
          : "This sends one short request to mock models. The answer is not from a real model."}
      </p>
      <form onSubmit={send} data-companion-avoid className="mt-4 flex flex-col gap-2.5 sm:flex-row">
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
        <Button type="submit" size="md" className="h-11 sm:px-5" disabled={sending || (checking && !key) || waking || limited || !mode || !prompt.trim()}>
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
      <HumanRow human={human} onRetry={() => void send()} />
      {live && !limited && !key && <p className="mt-2 text-[12.5px] text-muted">{DAILY_NOTE}</p>}
      {live && <JudgeKey left={left} />}
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
            <p data-tour="live-who" data-served-by={state.answer.servedBy ?? ""} className="mt-2.5 flex flex-wrap items-center gap-2 text-[12.5px] text-muted">
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
        {state.kind === "error" && (
          <p data-tour="live-error" className="text-[14px] text-ink-2">
            {state.message}
          </p>
        )}
        {state.kind === "limit" && (
          <p data-tour="live-limit" className="text-[14.5px] leading-relaxed text-ink">
            {DAILY_LIMIT_TEXT}
          </p>
        )}
      </div>
    </div>
  );
}
