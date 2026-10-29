import { Check, LoaderCircle, TriangleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { CHECKING_BRAND, canRetry, humanText, rowOpen, widgetShown } from "@/lib/human-model";
import type { Human } from "@/lib/use-human";

/**
 * The inline human check, right under the button that asked for it. It takes no room while nothing is happening (a visitor already verified for the
 * visit never sees it), opens smoothly to one slim line while Cloudflare checks (usually a moment, no click), and shows Cloudflare's widget in this same
 * spot only if a click is needed. When it passes it settles into a muted "Verified for this visit" and the request that was waiting goes on by itself.
 * It never moves focus, never floats (no fixed or absolute position anywhere), and keeps the Nano and Ultra companions off it (data-companion-avoid).
 * The height animates with the grid-rows trick, which is instant under prefers-reduced-motion.
 */
export function HumanRow({ human, onRetry, className }: { human: Human; onRetry: () => void; className?: string }) {
  const { state, containerRef, required } = human;
  const open = rowOpen(state);
  const text = humanText(state, required);
  const bad = state.phase === "error";
  return (
    <div
      data-companion-avoid
      data-human={state.phase}
      aria-live="polite"
      className={cn(
        "grid transition-[grid-template-rows,opacity] duration-300 ease-out motion-reduce:transition-none",
        open ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0",
        className,
      )}
    >
      <div className="min-h-0 overflow-hidden">
        <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5 pt-2.5 text-[12.5px] leading-snug">
          {state.phase === "checking" && <LoaderCircle className="size-3.5 shrink-0 animate-spin text-muted motion-reduce:animate-none" aria-hidden />}
          {state.phase === "verified" && <Check className="size-3.5 shrink-0 text-good-ink" aria-hidden />}
          {(bad || state.phase === "skipped") && <TriangleAlert className={cn("size-3.5 shrink-0", bad ? "text-warn" : "text-muted")} aria-hidden />}
          {text && (
            <span className={cn(bad ? "text-ink-2" : "text-muted")}>
              {text}
              {state.phase === "checking" && <span className="text-muted"> · {CHECKING_BRAND}</span>}
            </span>
          )}
          {canRetry(state) && (
            <Button type="button" variant="outline" size="sm" className="h-7 px-2.5 text-[12.5px]" onClick={onRetry}>
              Try again
            </Button>
          )}
        </div>
        {/* Cloudflare draws its widget in here; it is empty and closed unless a click is needed. */}
        <div ref={containerRef} className={cn("w-full max-w-[22rem]", widgetShown(state) ? "mt-2.5" : "h-0 overflow-hidden")} />
      </div>
    </div>
  );
}
