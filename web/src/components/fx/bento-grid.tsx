import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/**
 * A grid of cards of mixed widths (ported from Aceternity UI's Bento Grid, MIT; adapted: the story's tokens, a list
 * element, and the card itself left to the caller, usually a SpotlightCard).
 */
export function BentoGrid({ children, className }: { children: ReactNode; className?: string }) {
  return <ul className={cn("grid grid-cols-1 gap-4 md:grid-cols-6 md:auto-rows-[minmax(13rem,auto)]", className)}>{children}</ul>;
}

/** A span helper: `wide` takes two thirds of a row on desktop, the rest one third. */
export const bentoSpan = (wide: boolean) => (wide ? "md:col-span-4" : "md:col-span-2");
