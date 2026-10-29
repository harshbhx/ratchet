import { useEffect, useRef } from "react";
import { useReducedMotion } from "motion/react";
import { cn } from "@/lib/utils";
import { lookOffset, type Expression, type Who } from "./avatar-model";

/**
 * Nano and Ultra, drawn here from simple shapes (original artwork, no external assets). Nano is the student: a small round bot with
 * the brand blue, a face plate and a little adapter chip on its head. Ultra is the teacher: larger and calmer, a rounded square in
 * the brand orange with round glasses. Both use the design tokens, so they read in light and dark.
 *
 * The SVG is decoration (`aria-hidden`); whatever an avatar "says" is text elsewhere, in a live region. Motion: the eyes blink every
 * few seconds, the body breathes, and the eyes follow the pointer within a few units. Under reduced motion all of that stops and
 * only the expression remains. The CSS for the loops is in globals.css (`.av-*`).
 */
export function Avatar({
  who,
  expression = "idle",
  size = 72,
  sunglasses = false,
  squint = false,
  follow = true,
  className,
}: {
  who: Who;
  expression?: Expression;
  size?: number;
  sunglasses?: boolean;
  squint?: boolean;
  /** Eyes follow the pointer (not under reduced motion). */
  follow?: boolean;
  className?: string;
}) {
  const reduce = useReducedMotion();
  const ref = useRef<SVGSVGElement | null>(null);
  const live = !reduce;

  useEffect(() => {
    const svg = ref.current;
    if (!svg || !follow || !live || expression === "thinking" || expression === "sleepy") return;
    let raf = 0;
    const on = (e: PointerEvent) => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        const r = svg.getBoundingClientRect();
        const o = lookOffset(e.clientX, e.clientY, r.left + r.width / 2, r.top + r.height / 2, 2.2);
        svg.style.setProperty("--lx", `${o.x.toFixed(2)}px`);
        svg.style.setProperty("--ly", `${o.y.toFixed(2)}px`);
      });
    };
    window.addEventListener("pointermove", on, { passive: true });
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("pointermove", on);
    };
  }, [follow, live, expression]);

  const ultra = who === "ultra";
  const body = ultra ? "var(--rented)" : "var(--owned)";
  const ex = ultra ? 36 : 38;
  const ex2 = ultra ? 64 : 62;
  const ey = ultra ? 50 : 52;
  const open = expression === "surprised" ? { rx: 4.4, ry: 5.4 } : { rx: 3.1, ry: 3.8 };
  const arcEyes = expression === "happy" || expression === "proud" || expression === "wave";
  const flatEyes = squint || expression === "sleepy";

  const eye = (x: number) => {
    if (sunglasses) return null;
    if (flatEyes) return <path d={`M${x - 4} ${ey}h8`} stroke="var(--ink)" strokeWidth="2.4" strokeLinecap="round" fill="none" />;
    if (arcEyes) return <path d={`M${x - 4} ${ey + 1.5}q4 -6 8 0`} stroke="var(--ink)" strokeWidth="2.4" strokeLinecap="round" fill="none" />;
    const up = expression === "thinking" ? { x: 1.6, y: -2.4 } : { x: 0, y: 0 };
    return (
      <g className="av-blink">
        <g style={{ transform: `translate(calc(var(--lx, 0px) + ${up.x}px), calc(var(--ly, 0px) + ${up.y}px))` }}>
          <ellipse cx={x} cy={ey} rx={open.rx} ry={open.ry} fill="var(--ink)" />
          <circle cx={x + 1} cy={ey - 1.2} r="1" fill="var(--surface)" />
        </g>
      </g>
    );
  };

  const mouthY = ultra ? 66 : 67;
  const mouth =
    expression === "surprised" ? (
      <ellipse cx="50" cy={mouthY + 1} rx="3.2" ry="4" fill="var(--ink)" />
    ) : expression === "happy" || expression === "wave" ? (
      <path d={`M42 ${mouthY - 2}q8 10 16 0z`} fill="var(--ink)" />
    ) : expression === "proud" ? (
      <path d={`M43 ${mouthY}q7 7 14 0`} stroke="var(--ink)" strokeWidth="2.4" strokeLinecap="round" fill="none" />
    ) : expression === "sleepy" ? (
      <path d={`M45 ${mouthY + 1}h10`} stroke="var(--ink)" strokeWidth="2.2" strokeLinecap="round" fill="none" />
    ) : expression === "thinking" ? (
      <path d={`M45 ${mouthY + 1}q5 -2 10 0`} stroke="var(--ink)" strokeWidth="2.2" strokeLinecap="round" fill="none" />
    ) : (
      <path d={`M44 ${mouthY - 1}q6 5 12 0`} stroke="var(--ink)" strokeWidth="2.2" strokeLinecap="round" fill="none" />
    );

  return (
    <svg
      ref={ref}
      viewBox="0 0 100 100"
      width={size}
      height={size}
      aria-hidden
      focusable="false"
      className={cn("shrink-0 overflow-visible", className)}
    >
      <g className={cn(live && "av-bob")} style={{ transformOrigin: "50px 90px" }}>
        {/* body */}
        <rect x={ultra ? 26 : 32} y={ultra ? 78 : 80} width={ultra ? 48 : 36} height="16" rx="8" fill={body} />
        {expression === "wave" && (
          <g className={cn(live && "av-wave")} style={{ transformOrigin: ultra ? "78px 84px" : "70px 86px" }}>
            <rect x={ultra ? 74 : 66} y={ultra ? 66 : 70} width="8" height="18" rx="4" fill={body} />
          </g>
        )}
        {/* head */}
        {ultra ? (
          <rect x="16" y="14" width="68" height="64" rx="24" fill={body} />
        ) : (
          <>
            <circle cx="50" cy="52" r="36" fill={body} />
            <rect x="42" y="8" width="16" height="9" rx="3" fill="var(--rented)" />
            <rect x="48.4" y="14" width="3.2" height="6" fill={body} />
          </>
        )}
        {/* face plate */}
        <rect x={ultra ? 24 : 24} y={ultra ? 28 : 34} width="52" height={ultra ? 44 : 38} rx={ultra ? 16 : 18} fill="var(--surface)" />
        {/* eyebrows: Ultra's stay level and calm; Nano's lift when surprised */}
        {ultra && <path d={`M${ex - 6} ${ey - 11}h12M${ex2 - 6} ${ey - 11}h12`} stroke="var(--ink)" strokeWidth="2" strokeLinecap="round" opacity="0.7" />}
        {!ultra && expression === "surprised" && (
          <path d={`M${ex - 5} ${ey - 10}q5 -3 10 0M${ex2 - 5} ${ey - 10}q5 -3 10 0`} stroke="var(--ink)" strokeWidth="2" strokeLinecap="round" fill="none" />
        )}
        {eye(ex)}
        {eye(ex2)}
        {/* glasses: Ultra's own, and sunglasses for either in the dark theme */}
        {ultra && !sunglasses && (
          <g fill="none" stroke="var(--ink)" strokeWidth="2.2">
            <circle cx={ex} cy={ey} r="8.2" />
            <circle cx={ex2} cy={ey} r="8.2" />
            <path d={`M${ex + 8.2} ${ey}h${ex2 - ex - 16.4}`} />
          </g>
        )}
        {sunglasses && (
          <g fill="var(--ink)">
            <rect x={ex - 9} y={ey - 6.5} width="18" height="13" rx="5" />
            <rect x={ex2 - 9} y={ey - 6.5} width="18" height="13" rx="5" />
            <rect x={ex + 8} y={ey - 2} width={ex2 - ex - 16} height="2.6" />
          </g>
        )}
        {(expression === "happy" || expression === "proud" || expression === "wave") && (
          <g fill="var(--rented)" opacity="0.28">
            <circle cx={ex - 7} cy={ey + 9} r="4" />
            <circle cx={ex2 + 7} cy={ey + 9} r="4" />
          </g>
        )}
        {mouth}
        {/* what the expression adds around the head */}
        {expression === "thinking" && (
          <g fill="var(--ink-2)">
            <circle className={cn(live && "av-dot")} cx="76" cy="14" r="2.6" />
            <circle className={cn(live && "av-dot")} cx="85" cy="9" r="2.6" style={{ animationDelay: "0.25s" }} />
            <circle className={cn(live && "av-dot")} cx="94" cy="4" r="2.6" style={{ animationDelay: "0.5s" }} />
          </g>
        )}
        {expression === "sleepy" && (
          <text x="76" y="16" className={cn("fill-muted font-mono", live && "av-z")} style={{ fontSize: 13, fontWeight: 600 }}>
            z
          </text>
        )}
        {expression === "proud" && (
          <path d="M84 12l2.2 5 5.2.6-3.9 3.5 1.1 5.2-4.6-2.7-4.6 2.7 1.1-5.2-3.9-3.5 5.2-.6z" fill="var(--rented)" className={cn(live && "av-bob")} />
        )}
      </g>
    </svg>
  );
}
