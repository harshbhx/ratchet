import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { onRecovered } from "@/lib/waking-store";
import { api, streamScout, type ScoutExamples, type ScoutStatus, type ScoutTokens } from "@/lib/api";
import { failureOfMessage } from "@/lib/human-model";
import { hostedServing, whoContext } from "@/lib/who-model";
import { derive, initialRun, runReducer } from "./state";

const now = () => (typeof performance !== "undefined" ? performance.now() : Date.now());

/** The inline human check, as a run uses it: its tokens, and where to say that the Worker refused it (or that all went well, with null). */
export interface ScoutHuman {
  tokens: ScoutTokens;
  report: (failure: "failed" | "blocked" | null) => void;
}

/** Page-level controller: examples, budget status, and one run at a time. */
export function useScout() {
  const [run, dispatch] = useReducer(runReducer, initialRun);
  const [examples, setExamples] = useState<ScoutExamples | null>(null);
  const [examplesError, setExamplesError] = useState<string | null>(null);
  const [status, setStatus] = useState<ScoutStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const cancelRef = useRef<(() => void) | null>(null);

  const loadExamples = useCallback(async () => {
    try {
      setExamples(await api.scoutExamples());
      setExamplesError(null);
    } catch {
      setExamplesError("Could not load the example questions. Is the Ratchet gateway running?");
    }
  }, []);

  const loadStatus = useCallback(async () => {
    try {
      setStatus(await api.scoutStatus());
    } catch {
      /* status is optional */
    } finally {
      setStatusLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadExamples();
    void loadStatus();
    // The live gateway woke up: read what the first calls could not (a Scout run is never retried by itself).
    const off = onRecovered(() => {
      void loadExamples();
      void loadStatus();
      });
    return () => {
      off();
      cancelRef.current?.();
    };
  }, [loadExamples, loadStatus]);

  const start = useCallback(
    (question: string, human?: ScoutHuman) => {
      const q = question.trim();
      if (!q) return;
      cancelRef.current?.();
      dispatch({ type: "begin", question: q, at: now() });
      let ended = false;
      const cancel = streamScout(
        q,
        (ev) => {
          // The Worker refused the human check: the inline row under the Ask button says so, so the run just stops here (no second, raw error).
          const refused = ev.type === "error" && human ? failureOfMessage(ev.message) : null;
          if (refused && human) {
            human.report(refused);
            dispatch({ type: "stop", at: now() });
            ended = true;
            return;
          }
          if (ev.type === "done" && human) human.report(null);
          dispatch({ type: "event", ev, at: now() });
          if (!ended && (ev.type === "done" || ev.type === "error")) {
            ended = true;
            void loadStatus();
          }
        },
        human?.tokens,
      );
      cancelRef.current = cancel;
    },
    [loadStatus],
  );

  const stop = useCallback(() => {
    cancelRef.current?.();
    cancelRef.current = null;
    dispatch({ type: "stop", at: now() });
    void loadStatus();
  }, [loadStatus]);

  const fallback = status?.fallback_student ?? null;
  const servingHosted = hostedServing(status?.student_serving);
  const derived = useMemo(() => derive(run, fallback, servingHosted), [run, fallback, servingHosted]);
  const mode = run.mode ?? examples?.mode ?? status?.mode ?? null;
  // Every badge on the page tells our adapter from the hosted fallback by the call's model id (FR-7.40).
  const who = useMemo(() => ({ ...whoContext(status), simulated: mode === "mock" }), [status, mode]);

  return { run, derived, examples, examplesError, status, statusLoading, mode, who, start, stop, reloadExamples: loadExamples, refreshStatus: loadStatus };
}
