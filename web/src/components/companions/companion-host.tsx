import { Suspense, lazy, useEffect, useRef, useState } from "react";
import { COMPANION_EVENT, useCompanionsHidden, type CompanionSignal } from "./companion-bus";

// The characters are their own chunk: nothing loads until the page has painted and the browser is idle.
const Stage = lazy(() => import("./stage"));

/**
 * Mounted once in the Layout. Waits for the first paint and an idle moment, then loads the stage; a "Watch Nano learn" click that
 * arrives earlier loads it at once and is handed over as `pending`. Renders nothing while the visitor has hidden Nano and Ultra.
 */
export function CompanionHost() {
  const hidden = useCompanionsHidden();
  const [ready, setReady] = useState(false);
  const pending = useRef<CompanionSignal["type"] | null>(null);

  useEffect(() => {
    let done = false;
    const go = () => {
      if (!done) {
        done = true;
        setReady(true);
      }
    };
    const w = window as Window & { requestIdleCallback?: (cb: () => void, o?: { timeout: number }) => number; cancelIdleCallback?: (id: number) => void };
    let id = 0;
    let timer = 0;
    const arm = () => {
      if (w.requestIdleCallback) id = w.requestIdleCallback(go, { timeout: 3000 });
      else timer = window.setTimeout(go, 1200);
    };
    // After first paint: the load event, then idle.
    if (document.readyState === "complete") arm();
    else window.addEventListener("load", arm, { once: true });
    const early = (e: Event) => {
      const t = (e as CustomEvent<CompanionSignal>).detail?.type;
      if (t === "story" || t === "sheet") {
        pending.current = t;
        go();
      }
    };
    window.addEventListener(COMPANION_EVENT, early);
    return () => {
      done = true;
      window.removeEventListener("load", arm);
      window.removeEventListener(COMPANION_EVENT, early);
      if (id && w.cancelIdleCallback) w.cancelIdleCallback(id);
      window.clearTimeout(timer);
    };
  }, []);

  if (!ready || hidden) return null;
  return (
    <Suspense fallback={null}>
      <Stage
        pending={pending.current}
        onPendingHandled={() => {
          pending.current = null;
        }}
      />
    </Suspense>
  );
}
