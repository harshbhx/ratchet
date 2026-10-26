import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { onRecovered } from "@/lib/waking-store";
import { api, streamScout, type ScoutExamples, type ScoutStatus } from "@/lib/api";
import { pickLive, play as playRecording, type Playback, type RecordingSummary } from "@/lib/recording-model";
import { hostedServing, whoContext } from "@/lib/who-model";
import { derive, initialRun, runReducer } from "./state";

const now = () => (typeof performance !== "undefined" ? performance.now() : Date.now());

/** Page-level controller: examples, budget status, and one run at a time. */
export function useScout() {
  const [run, dispatch] = useReducer(runReducer, initialRun);
  const [examples, setExamples] = useState<ScoutExamples | null>(null);
  const [examplesError, setExamplesError] = useState<string | null>(null);
  const [status, setStatus] = useState<ScoutStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const cancelRef = useRef<(() => void) | null>(null);
  const playbackRef = useRef<Playback | null>(null);
  const [recordings, setRecordings] = useState<RecordingSummary[]>([]);
  /** The recording being played or just played; null for a live run. */
  const [recording, setRecording] = useState<RecordingSummary | null>(null);

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

  const loadRecordings = useCallback(async () => {
    try {
      setRecordings((await api.scoutRecordings()).recordings);
    } catch {
      /* an older gateway has none: the button stays hidden */
    }
  }, []);

  useEffect(() => {
    void loadExamples();
    void loadStatus();
    void loadRecordings();
    // The live demo woke up: read what the first calls could not (a Scout run is never retried by itself).
    const off = onRecovered(() => {
      void loadExamples();
      void loadStatus();
      void loadRecordings();
    });
    return () => {
      off();
      cancelRef.current?.();
      playbackRef.current?.cancel();
    };
  }, [loadExamples, loadStatus, loadRecordings]);

  const start = useCallback(
    (question: string) => {
      const q = question.trim();
      if (!q) return;
      cancelRef.current?.();
      playbackRef.current?.cancel();
      playbackRef.current = null;
      setRecording(null);
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

  /** Play a recorded run at its real pace through the same reducer as a live one; it costs nothing and cannot be refused. */
  const play = useCallback(async (summary: RecordingSummary) => {
    cancelRef.current?.();
    cancelRef.current = null;
    playbackRef.current?.cancel();
    try {
      const rec = await api.scoutRecording(summary.id);
      setRecording(summary);
      dispatch({ type: "begin", question: rec.question, at: now() });
      playbackRef.current = playRecording(
        rec,
        (ev) => dispatch({ type: "event", ev, at: now() }),
        { set: (fn, ms) => setTimeout(fn, ms), clear: (h) => clearTimeout(h as ReturnType<typeof setTimeout>) },
        () => {
          playbackRef.current = null;
        },
      );
    } catch {
      dispatch({ type: "fail", message: "Could not load the recorded run.", at: now() });
    }
  }, []);

  const skip = useCallback(() => playbackRef.current?.skip(), []);

  const stop = useCallback(() => {
    cancelRef.current?.();
    cancelRef.current = null;
    playbackRef.current?.cancel();
    playbackRef.current = null;
    dispatch({ type: "stop", at: now() });
    void loadStatus();
  }, [loadStatus]);

  const fallback = status?.fallback_student ?? null;
  const servingHosted = hostedServing(status?.student_serving);
  const derived = useMemo(() => derive(run, fallback, servingHosted), [run, fallback, servingHosted]);
  const mode = run.mode ?? examples?.mode ?? status?.mode ?? null;
  // Every badge on the page tells our adapter from the hosted fallback by the call's model id (FR-7.40).
  const who = useMemo(() => ({ ...whoContext(status), simulated: mode === "mock" }), [status, mode]);

  const live = useMemo(() => pickLive(recordings), [recordings]);
  const playing = recording !== null && run.status === "running";

  return { run, derived, examples, examplesError, status, statusLoading, mode, who, start, stop, reloadExamples: loadExamples, refreshStatus: loadStatus, recording, playing, recordings, liveRecording: live, play, skip };
}
