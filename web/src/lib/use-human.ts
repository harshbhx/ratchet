/**
 * The inline human check for one button: `gate()` before a paid call, `report()` after it. It runs the state machine in human-model.ts and asks
 * src/lib/turnstile.ts for a token, drawing the widget inside the element `containerRef` is attached to (the row from components/human-row.tsx,
 * placed right under the button). Each place that uses it has its own state, container and widget.
 *
 *   const human = useHuman(false);
 *   const go = await human.gate();           // { go: true, token } (token null when the visit is already verified or the check is off) or { go: false }
 *   if (!go.go) return;                      // the row says why; the visitor can press Try again
 *   const r = await fetch(..., { headers: tokenHeaders(go.token) });
 *   human.report(failureOf(r.status, code)); // null: all well (the Worker's cookie is now set, so later calls need no check)
 */
import { useCallback, useEffect, useReducer, useRef } from "react";
import { initialHuman, reduceHuman } from "./human-model";
import { isVerified, refreshVerified, siteKey, solve } from "./turnstile";

export type Gate = { go: true; token: string | null } | { go: false };

/** `required`: the call cannot go without the check (Scout); otherwise a check that did not load lets the call go as a plain keyless one. */
export function useHuman(required: boolean) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [state, dispatch] = useReducer(reduceHuman, initialHuman);

  // Ask once on load, so the first click already knows whether any check is needed.
  useEffect(() => {
    void isVerified();
    void siteKey();
  }, []);

  const gate = useCallback(async (): Promise<Gate> => {
    if (await isVerified()) {
      dispatch({ type: "reset" }); // verified for the visit: nothing is shown at all
      return { go: true, token: null };
    }
    if (!(await siteKey())) {
      dispatch({ type: "reset" }); // the check is off
      return { go: true, token: null };
    }
    const el = containerRef.current;
    if (!el) return { go: true, token: null };
    dispatch({ type: "start" });
    const r = await solve(el, { onInteractive: () => dispatch({ type: "interactive" }) });
    if (r.ok) {
      dispatch({ type: "passed" });
      return { go: true, token: r.token };
    }
    if (r.reason === "off") {
      dispatch({ type: "reset" });
      return { go: true, token: null };
    }
    if (r.reason === "unavailable") {
      dispatch({ type: "unavailable", required });
      return required ? { go: false } : { go: true, token: null };
    }
    dispatch({ type: "failed" });
    return { go: false };
  }, [required]);

  /** After the call: the Worker's refusal of the check (`failed`, `blocked`), or null when all went well. */
  const report = useCallback((failure: "failed" | "blocked" | null) => {
    if (failure === "failed") dispatch({ type: "failed" });
    else if (failure === "blocked") dispatch({ type: "blocked" });
    else void refreshVerified();
  }, []);

  const reset = useCallback(() => dispatch({ type: "reset" }), []);

  return { state, containerRef, gate, report, reset, required };
}

export type Human = ReturnType<typeof useHuman>;
