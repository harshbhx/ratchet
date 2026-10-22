import { cn } from "@/lib/utils";

/**
 * The Ratchet mark: a ratchet wheel (the student, owned blue) held by a pawl (the teacher, rented orange). The teeth are
 * a true sawtooth, so the wheel can only turn clockwise: the pawl rides up each slope and drops against the next wall.
 * That is the product: quality moves one way, and the teacher's audits are what stop it slipping back.
 *
 * Geometry on a 32-unit grid, drawn once at module load. `public/favicon.svg` holds the same shapes as fixed colours.
 * Original artwork (design/ASSET_AND_LICENSE_REGISTER.md).
 */
const C = { x: 15.4, y: 18.4 };
const TEETH = 8;
const R_OUT = 10.9;
const R_IN = 8.3;
/** Angle (degrees, clockwise from 3 o'clock) of the notch the pawl rests in. */
const NOTCH = -60;
/** Fraction of a tooth taken by its wall; the rest is the slope. */
const WALL = 0.07;

const rad = (d: number) => (d * Math.PI) / 180;
const at = (r: number, a: number) => [C.x + r * Math.cos(rad(a)), C.y + r * Math.sin(rad(a))] as const;
const f2 = (n: number) => n.toFixed(2);

function wheelPath(): string {
  const step = 360 / TEETH;
  const parts: string[] = [];
  for (let i = 0; i < TEETH; i++) {
    const a0 = NOTCH + i * step;
    const [x0, y0] = at(R_IN, a0); // foot of the wall
    const [x1, y1] = at(R_OUT, a0 + step * WALL); // tip of the tooth; the slope runs from here to the next foot
    parts.push(`${i === 0 ? "M" : "L"}${f2(x0)} ${f2(y0)}L${f2(x1)} ${f2(y1)}`);
  }
  return `${parts.join("")}Z`;
}

const WHEEL = wheelPath();
const TIP = at(R_IN + 0.8, NOTCH - 3);
const PIVOT = at(14.2, -102);
const PAWL = `M${f2(PIVOT[0])} ${f2(PIVOT[1])}L${f2(TIP[0])} ${f2(TIP[1])}`;

/** The pivots the one-shot animation turns around, in the mark's own units (CSS `transform-box: view-box`). */
export const LOGO_ORIGINS = { wheel: `${C.x}px ${C.y}px`, pawl: `${f2(PIVOT[0])}px ${f2(PIVOT[1])}px` };

/**
 * `animate`: play the click once when it mounts (the header's mark, once per page load). It is a CSS animation of
 * under 900 ms that never delays rendering; with reduced motion the mark is drawn at rest.
 */
export function LogoMark({ className, animate = false, title }: { className?: string; animate?: boolean; title?: string }) {
  return (
    <svg
      viewBox="0 0 32 32"
      className={cn("size-7 shrink-0", animate && "logo-click", className)}
      role={title ? "img" : undefined}
      aria-hidden={title ? undefined : true}
      aria-label={title}
    >
      <rect width="32" height="32" rx="8.5" className="fill-ink" />
      <g className="logo-wheel" style={{ transformOrigin: LOGO_ORIGINS.wheel }}>
        <path d={WHEEL} className="fill-owned" />
        <circle cx={C.x} cy={C.y} r="3.1" className="fill-ink" />
      </g>
      <g className="logo-pawl" style={{ transformOrigin: LOGO_ORIGINS.pawl }}>
        {/* A halo in the tile's colour keeps the pawl readable where it lies on the teeth. */}
        <path d={PAWL} strokeWidth="4.2" strokeLinecap="round" className="stroke-ink" />
        <path d={PAWL} strokeWidth="2.6" strokeLinecap="round" className="stroke-rented" />
        <circle cx={PIVOT[0]} cy={PIVOT[1]} r="2.1" className="fill-rented" />
      </g>
    </svg>
  );
}

/** Mark plus wordmark. The word is set in Inter, tight, so the mark carries the identity at every size. */
export function Logo({ className, animate = false, size = "md" }: { className?: string; animate?: boolean; size?: "sm" | "md" }) {
  return (
    <span className={cn("inline-flex items-center", size === "sm" ? "gap-2" : "gap-2.5", className)}>
      <LogoMark animate={animate} className={size === "sm" ? "size-6" : "size-7"} />
      <span className={cn("font-semibold tracking-[-0.03em] text-ink", size === "sm" ? "text-[15px]" : "text-[17.5px]")}>Ratchet</span>
    </span>
  );
}
