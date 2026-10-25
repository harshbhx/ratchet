import { useState, type FormEvent } from "react";
import { LoaderCircle, Radio, Send } from "lucide-react";
import type { Mode } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { WhoBadge } from "@/components/ui/who";
import { gatewayFetch, useWaking } from "@/lib/waking-store";
import { RUN_WAKING, isWaking } from "@/lib/waking-model";
import { DEFAULT_PROMPT, EMPTY_TEXT, MAX_PROMPT_CHARS, buildBody, errorText, parseAnswer, type Answer } from "../try-request-model";

type State = { kind: "idle" } | { kind: "sending" } | { kind: "done"; answer: Answer } | { kind: "error"; message: string };

/**
 * "Try a real request": one short chat request through this gateway's keyless /v1 path (the live Worker adds the demo key).
 * It is sent once and never retried, and it waits while the demo wakes up. `mode` comes from the page's stats poll: on the
 * simulation host the same box sends a request to mock models and says so.
 */
export function LiveRequest({ mode }: { mode: Mode | null }) {
  const waking = isWaking(useWaking());
  const [prompt, setPrompt] = useState(DEFAULT_PROMPT);
  const [state, setState] = useState<State>({ kind: "idle" });
  const sending = state.kind === "sending";
  const live = mode === "live";

  const send = async (e: FormEvent) => {
    e.preventDefault();
    if (sending || waking || !mode || !prompt.trim()) return;
    setState({ kind: "sending" });
    try {
      const r = await gatewayFetch("/v1/chat/completions", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(buildBody(prompt)),
      });
      if (!r.ok) {
        setState({ kind: "error", message: errorText(r.status) });
        return;
      }
      const answer = parseAnswer(await r.json());
      setState(answer && answer.text ? { kind: "done", answer } : { kind: "error", message: EMPTY_TEXT });
    } catch {
      setState({ kind: "error", message: errorText(0) });
    }
  };

  return (
    <div className="mt-6 rounded-2xl border border-line bg-surface p-5 shadow-card sm:p-6" aria-labelledby="live-req-title">
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
          ? "This sends one short request to the live gateway. It is a real model call and costs a small amount, so please send a few at most. Your text is not stored."
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
          className="h-11 min-w-0 flex-1 rounded-xl border border-line-strong bg-bg px-3.5 text-[16px] text-ink outline-none focus-visible:border-owned focus-visible:shadow-[0_0_0_4px_var(--owned-soft)] sm:text-[15px]"
        />
        <Button type="submit" size="md" className="h-11 sm:px-5" disabled={sending || waking || !mode || !prompt.trim()}>
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
                  Answered by <WhoBadge servedBy={state.answer.servedBy} model={state.answer.model} simulated={!live} />
                </>
              ) : (
                "The reply did not say which model answered."
              )}
            </p>
          </div>
        )}
        {state.kind === "error" && <p className="text-[14px] text-ink-2">{state.message}</p>}
      </div>
    </div>
  );
}
