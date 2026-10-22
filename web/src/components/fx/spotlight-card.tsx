import { useCallback, useRef, type HTMLAttributes, type PointerEvent, type ReactNode } from "react";
import { cn } from "@/lib/utils";

type Tone = "owned" | "rented" | "ink" | "good" | "bad";

const TONE_VAR: Record<Tone, string> = {
  owned: "var(--owned)",
  rented: "var(--rented)",
  ink: "var(--ink)",
  good: "var(--good)",
  bad: "var(--bad)",
};

/**
 * A card with a soft light that follows the pointer across it, and a border that brightens where the light is (after
 * the idea of React Bits' SpotlightCard; original code). The light is two CSS radial gradients driven by custom
 * properties set from `pointermove`, so nothing re-renders while the pointer moves. Keyboard focus inside the card
 * shows a centred light instead, so the effect is never the only cue. It is decoration: the card reads the same with
 * the light off, and touch screens never see it.
 */
export function SpotlightCard({
  children,
  className,
  tone = "ink",
  as: Tag = "div",
  ...rest
}: HTMLAttributes<HTMLElement> & { children: ReactNode; tone?: Tone; as?: "div" | "li" | "article" }) {
  const ref = useRef<HTMLElement | null>(null);
  const onMove = useCallback((e: PointerEvent<HTMLElement>) => {
    const el = ref.current;
    if (!el || e.pointerType !== "mouse") return;
    const r = el.getBoundingClientRect();
    el.style.setProperty("--sx", `${e.clientX - r.left}px`);
    el.style.setProperty("--sy", `${e.clientY - r.top}px`);
    el.style.setProperty("--so", "1");
  }, []);
  const onLeave = useCallback(() => {
    ref.current?.style.setProperty("--so", "0");
  }, []);
  return (
    <Tag
      // @ts-expect-error -- one ref for the three tags
      ref={ref}
      onPointerMove={onMove}
      onPointerLeave={onLeave}
      className={cn("spotlight-card group/spot relative overflow-hidden rounded-[22px] border border-line bg-surface shadow-card", className)}
      style={{ "--spot": TONE_VAR[tone] } as never}
      {...rest}
    >
      {/* the light on the surface */}
      <span aria-hidden className="spotlight-glow pointer-events-none absolute inset-0 rounded-[inherit]" />
      {/* the brightened border, masked to a ring */}
      <span aria-hidden className="spotlight-ring pointer-events-none absolute inset-0 rounded-[inherit]" />
      <div className="relative z-[1] flex h-full flex-col">{children}</div>
    </Tag>
  );
}
