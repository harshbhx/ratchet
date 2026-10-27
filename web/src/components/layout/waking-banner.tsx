import { useReducedMotion } from "motion/react";
import { LoaderCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { retryWaking, useWaking } from "@/lib/waking-store";
import { bannerText } from "@/lib/waking-model";

/**
 * The live gateway sleeps when idle; while it starts, this says so under the header. A polite status, not an alert: the page
 * updates by itself when /healthz answers. After about 90 s it says it is taking longer and offers a Retry.
 */
export function WakingBanner() {
  const state = useWaking();
  const reduce = useReducedMotion();
  const text = bannerText(state);
  return (
    <div role="status" aria-live="polite" aria-atomic="true">
      {text && (
        <div className="border-b border-line bg-surface-2">
          <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-3 gap-y-2 px-4 py-2.5 text-[13.5px] text-ink-2 sm:px-6 lg:px-8">
            {state.phase === "waking" && <LoaderCircle aria-hidden className={reduce ? "size-4 text-owned" : "size-4 animate-spin text-owned"} />}
            <span className="min-w-0 flex-1 text-pretty">{text}</span>
            {state.phase === "slow" && (
              <Button size="sm" variant="outline" className="h-7" onClick={retryWaking}>
                Retry
              </Button>
            )}
          </div>
          {state.phase === "waking" && (
            <div aria-hidden className="relative h-0.5 w-full overflow-hidden bg-line">
              <span className={reduce ? "absolute inset-y-0 left-0 w-full bg-owned/60" : "waking-bar absolute inset-y-0 left-0 w-1/3 bg-owned"} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
