import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/**
 * A band of light that sweeps across a card once when the pointer enters it, or when something inside it takes
 * keyboard focus (after the idea of React Bits' GlareHover; original code, pure CSS: `.glare` in globals.css). It
 * never moves on its own, so it has nothing to pause; reduced motion turns the transition off.
 */
export function GlareHover({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn("glare relative", className)}>
      {children}
      <span aria-hidden className="glare-band pointer-events-none absolute inset-0 overflow-hidden rounded-[inherit]">
        <span className="glare-light absolute inset-y-[-40%] left-0 w-[45%]" />
      </span>
    </div>
  );
}
