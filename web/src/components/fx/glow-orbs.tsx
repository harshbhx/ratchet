import { useRef } from "react";
import { cn } from "@/lib/utils";
import { useOnScreen } from "@/lib/visibility";

type Tone = "owned" | "rented" | "good";

/**
 * Two or three large, soft pools of colour that drift very slowly behind a section (an original take on the
 * animated backgrounds in React Bits and Aceternity UI, done with CSS transforms, no blur filter and no canvas, so
 * the compositor does all the work). They drift only while the section is on screen and the tab is visible, and
 * stand still under reduced motion. Decoration: every section reads the same without them.
 */
export function GlowOrbs({ tones = ["owned", "rented"], className }: { tones?: Tone[]; className?: string }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const on = useOnScreen(ref);
  const spots = ["left-[-8%] top-[-10%] size-[42rem]", "right-[-10%] top-[30%] size-[38rem]", "left-[30%] bottom-[-20%] size-[34rem]"];
  return (
    <div ref={ref} aria-hidden className={cn("pointer-events-none absolute inset-0 overflow-hidden", on && "orbs-on", className)}>
      {tones.map((t, i) => (
        <span key={`${t}-${i}`} className={cn("orb absolute rounded-full", `orb-${i}`, spots[i % spots.length])} style={{ "--orb": `var(--${t})` } as never} />
      ))}
    </div>
  );
}
