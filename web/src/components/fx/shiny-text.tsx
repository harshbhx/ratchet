import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/**
 * Text with a soft band of light that sweeps across it now and then (after the idea of React Bits' ShinyText). Pure
 * CSS: a clipped gradient and the `shine` keyframes in globals.css, which reduced motion turns off, leaving the text
 * in its base colour. Used for one eyebrow in the hero, nowhere else: it is an accent, not a style.
 */
export function ShinyText({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={cn("shiny-text", className)}>{children}</span>;
}
