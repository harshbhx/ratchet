import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { api, streamScout, type ScoutExamples, type ScoutStatus } from "@/lib/api";
import { derive, initialRun, runReducer } from "./state";

const now = () => (typeof performance !== "undefined" ? performance.now() : Date.now());

/** Page-level controller: examples, budget status, and one run at a time. */
export function useScout() {
  const [run, dispatch] = useReducer(runReducer, initialRun);
  const [examples, setExamples] = useState<ScoutExamples | null>(null);
  const [examplesError, setExamplesError] = useState<string | null>(null);
  const [status, setStatus] = useState<ScoutStatus | null>(null);
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
    }
  }, []);

  useEffect(() => {
    void loadExamples();
    void loadStatus();
    return () => cancelRef.current?.();
  }, [loadExamples, loadStatus]);

  const start = useCallback(
    (question: string) => {
      const q = question.trim();
      if (!q) return;
      cancelRef.current?.();
      dispatch({ type: "begin", question: q, at: now() });
      let ended = false;
      const cancel = streamScout(q, (ev) => {
        dispatch({ type: "event", ev, at: now() });
        if (!ended && (ev.type === "done" || ev.type === "error")) {
          ended = true;
          void loadStatus();
        }
      });
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

  const derived = useMemo(() => derive(run), [run]);
  const mode = run.mode ?? examples?.mode ?? status?.mode ?? null;

  return { run, derived, examples, examplesError, status, mode, start, stop, reloadExamples: loadExamples };
}
