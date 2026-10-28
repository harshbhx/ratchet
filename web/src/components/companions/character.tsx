import { forwardRef, useEffect, useImperativeHandle, useRef, type ReactNode } from "react";
import { cn } from "@/lib/utils";
import { lookOffset, type Expr, type Look, type Pose, type Who } from "./companion-model";

/**
 * Nano and Ultra, drawn from simple shapes (original artwork, no external assets). Nano is the student: small, round, blue, with an
 * orange adapter chip on its head. Ultra is the teacher: bigger, orange, with round glasses and a bow tie. Colours are the design
 * tokens, so both read in light and dark; the face plate is the surface colour and the features are the ink colour.
 *
 * Decoration only (`aria-hidden`): whatever they say is text, in a live region. The CSS for blinking, breathing and the small
 * flourishes is in globals.css (`.cp-*`); nothing here animates anything but a transform or an opacity.
 */

interface Geom {
  ex: [number, number];
  ey: number;
  my: number;
  shoulder: [[number, number], [number, number]];
  arm: number;
  armW: number;
  torso: { x: number; y: number; w: number; h: number; r: number };
  feet: [number, number];
  plate: { x: number; y: number; w: number; h: number; r: number };
}

const GEOM: Record<Who, Geom> = {
  nano: {
    ex: [47, 73],
    ey: 54,
    my: 66,
    shoulder: [[46, 94], [74, 94]],
    arm: 22,
    armW: 8,
    torso: { x: 44, y: 84, w: 32, h: 34, r: 14 },
    feet: [49, 71],
    plate: { x: 32, y: 36, w: 56, h: 38, r: 18 },
  },
  ultra: {
    ex: [44, 76],
    ey: 50,
    my: 63,
    shoulder: [[38, 94], [82, 94]],
    arm: 24,
    armW: 9,
    torso: { x: 34, y: 82, w: 52, h: 38, r: 16 },
    feet: [46, 74],
    plate: { x: 27, y: 31, w: 66, h: 40, r: 17 },
  },
};

/** Arm angles in degrees [screen-left arm, screen-right arm]; 0 hangs straight down, positive swings the hand toward screen-left. */
function armAngles(pose: Pose, expr: Expr): [number, number] {
  switch (pose) {
    case "jump":
      return [152, -152];
    case "pointLeft":
      return [92, -6];
    case "pointRight":
      return [6, -92];
    case "carry":
      return [-38, 38];
    case "stop":
      return [96, -6];
    case "hug":
      return [-60, 60];
    case "pat":
      return [138, -6];
    default:
      return expr === "wave" ? [6, -150] : [6, -6];
  }
}

const INK = "var(--ink)";

function Eye({ x, y, kind, k }: { x: number; y: number; kind: "open" | "wide" | "arc" | "flat" | "squint" | "sad" | "tight"; k: string }) {
  const stroke = { stroke: INK, strokeWidth: 2.5, strokeLinecap: "round" as const, fill: "none" };
  if (kind === "arc") return <path key={k} d={`M${x - 4.4} ${y + 1.8}q4.4 -6.6 8.8 0`} {...stroke} />;
  if (kind === "flat") return <path key={k} d={`M${x - 4} ${y}h8`} {...stroke} />;
  if (kind === "squint") return <path key={k} d={`M${x - 4.6} ${y + 0.6}q4.6 -3.2 9.2 0`} {...stroke} strokeWidth={2.8} />;
  const [rx, ry] = kind === "wide" ? [4.6, 5.6] : kind === "tight" ? [3.2, 3] : kind === "sad" ? [3.2, 3.4] : [3.2, 3.9];
  return (
    <g className="cp-blink" key={k}>
      <g style={{ transform: "translate(var(--lx, 0px), var(--ly, 0px))" }}>
        <ellipse cx={x} cy={y} rx={rx} ry={ry} fill={INK} />
        <circle cx={x + 1.2} cy={y - 1.5} r={kind === "wide" ? 1.7 : 1.2} fill="var(--surface)" />
        <circle cx={x - 1.4} cy={y + 1.4} r="0.6" fill="var(--surface)" opacity="0.8" />
      </g>
    </g>
  );
}

function eyeKind(expr: Expr, squint: boolean): "open" | "wide" | "arc" | "flat" | "squint" | "sad" | "tight" {
  if (squint) return "squint";
  switch (expr) {
    case "happy":
    case "proud":
    case "laughing":
      return "arc";
    case "sleepy":
      return "flat";
    case "surprised":
      return "wide";
    case "sad":
      return "sad";
    case "determined":
      return "tight";
    default:
      return "open";
  }
}

function Mouth({ expr, x, y, ultra }: { expr: Expr; x: number; y: number; ultra: boolean }) {
  const line = { stroke: INK, strokeWidth: 2.3, strokeLinecap: "round" as const, fill: "none" };
  switch (expr) {
    case "happy":
    case "wave":
      return <path d={`M${x - 8} ${y - 2}q8 11 16 0z`} fill={INK} />;
    case "laughing":
      return (
        <g>
          <path d={`M${x - 10} ${y - 3}q10 17 20 0z`} fill={INK} />
          <path d={`M${x - 5} ${y + 5}q5 -4 10 0q-5 4 -10 0z`} fill="var(--rented)" opacity="0.8" />
        </g>
      );
    case "proud":
      return <path d={`M${x - 7} ${y}q7 7 14 0`} {...line} />;
    case "sleepy":
      return <ellipse cx={x} cy={y + 2} rx="2.6" ry="2" fill={INK} opacity="0.85" />;
    case "surprised":
      return <ellipse cx={x} cy={y + 1} rx="3.3" ry="4.2" fill={INK} />;
    case "thinking":
      return <path d={`M${x - 6} ${y + 1}q3 -3 6 0t6 0`} {...line} />;
    case "determined":
      return <path d={`M${x - 6} ${y}h12`} {...line} />;
    case "sad":
      return <path d={`M${x - 6} ${y + 4}q6 -6 12 0`} {...line} />;
    default:
      return ultra ? <path d={`M${x - 6} ${y + 1}q6 3 13 -2`} {...line} /> : <path d={`M${x - 6} ${y - 1}q6 5 12 0`} {...line} />;
  }
}

function Brows({ expr, ex, ey, ultra, squint }: { expr: Expr; ex: [number, number]; ey: number; ultra: boolean; squint: boolean }) {
  const by = ey - 11;
  const st = { stroke: INK, strokeWidth: 2.2, strokeLinecap: "round" as const, fill: "none", opacity: 0.8 };
  const slope = (x: number, dir: 1 | -1, outerDy: number, innerDy: number) => (
    <path d={`M${x - dir * 6} ${by + outerDy}L${x + dir * 6} ${by + innerDy}`} {...st} />
  );
  if (expr === "determined" || squint) return <g>{slope(ex[0], 1, -2.5, 2.5)}{slope(ex[1], -1, -2.5, 2.5)}</g>;
  if (expr === "sad") return <g>{slope(ex[0], 1, 2, -3)}{slope(ex[1], -1, 2, -3)}</g>;
  if (expr === "surprised")
    return <g><path d={`M${ex[0] - 5} ${by - 2}q5 -4 10 0`} {...st} /><path d={`M${ex[1] - 5} ${by - 2}q5 -4 10 0`} {...st} /></g>;
  if (expr === "thinking") return <g><path d={`M${ex[0] - 5} ${by}h10`} {...st} /><path d={`M${ex[1] - 5} ${by - 3}q5 -3 10 0`} {...st} /></g>;
  if (!ultra) return null;
  // Ultra's brows: level, with one a little higher. Dry.
  return <g><path d={`M${ex[0] - 6} ${by}h12`} {...st} /><path d={`M${ex[1] - 6} ${by - 2}h12`} {...st} /></g>;
}

function Held({ held, ultra }: { held: Look["held"]; ultra: boolean }) {
  if (held === "none") return null;
  const s = ultra ? 1.15 : 1;
  return (
    <g transform={`translate(60 ${ultra ? 104 : 102}) scale(${s})`}>
      {held === "book" && (
        <g>
          <rect x="-14" y="-9" width="28" height="19" rx="2.5" fill="var(--surface)" stroke={INK} strokeWidth="1.6" />
          <path d="M0 -9v19" stroke={INK} strokeWidth="1.4" />
          <path d="M-10 -3h7M-10 1h7M3 -3h7M3 1h7" stroke="var(--muted)" strokeWidth="1.2" strokeLinecap="round" />
          <path d="M7 -9v7l2.5 -2l2.5 2v-7z" fill="var(--rented)" />
        </g>
      )}
      {held === "ball" && (
        <g>
          <circle r="10" fill="var(--rented)" stroke={INK} strokeWidth="1.6" />
          <path d="M-10 0q10 5 20 0M0 -10q-5 10 0 20" stroke={INK} strokeWidth="1.2" fill="none" />
        </g>
      )}
      {held === "card" && (
        <g>
          <rect x="-13" y="-9" width="26" height="18" rx="3" fill="var(--surface)" stroke={INK} strokeWidth="1.6" />
          <text x="0" y="4.5" textAnchor="middle" fill="var(--owned-ink)" style={{ fontSize: 14, fontWeight: 700 }}>?</text>
        </g>
      )}
      {held === "papers" && (
        <g>
          <rect x="-12" y="-10" width="24" height="19" rx="2" fill="var(--surface-2)" stroke={INK} strokeWidth="1.2" transform="rotate(-6)" />
          <rect x="-12" y="-9" width="24" height="19" rx="2" fill="var(--surface)" stroke={INK} strokeWidth="1.4" />
          <path d="M-8 -4h10M-8 0h14M-8 4h8" stroke="var(--muted)" strokeWidth="1.2" strokeLinecap="round" />
          <path d="M3 3l3 3l6 -7" stroke="var(--bad)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" fill="none" />
          <rect x="9" y="-14" width="3" height="14" rx="1.5" fill="var(--bad)" transform="rotate(24 10 -7)" />
        </g>
      )}
      {held === "mug" && (
        <g>
          <path d="M-9 -8h16v13a6 6 0 0 1 -6 6h-4a6 6 0 0 1 -6 -6z" fill="var(--surface)" stroke={INK} strokeWidth="1.6" />
          <path d="M7 -3h3a4 4 0 0 1 0 8h-3" fill="none" stroke={INK} strokeWidth="1.6" />
          <path className="cp-steam" d="M-4 -12q-2 -3 0 -6M1 -12q-2 -3 0 -6" stroke="var(--muted)" strokeWidth="1.3" strokeLinecap="round" fill="none" />
        </g>
      )}
    </g>
  );
}

export interface CharacterProps {
  who: Who;
  look: Look;
  size?: number;
  /** Blinking, breathing and the pupils following the pointer. Off for static portraits and under reduced motion. */
  live?: boolean;
  className?: string;
  children?: ReactNode;
}

export const Character = forwardRef<SVGSVGElement, CharacterProps>(function Character({ who, look, size = 96, live = true, className }, ref) {
  const inner = useRef<SVGSVGElement | null>(null);
  useImperativeHandle(ref, () => inner.current as SVGSVGElement);
  const ultra = who === "ultra";
  const g = GEOM[who];
  const body = ultra ? "var(--rented)" : "var(--owned)";
  const accent = ultra ? "var(--owned)" : "var(--rented)";

  // The pupils follow the pointer.
  useEffect(() => {
    const svg = inner.current;
    if (!svg || !live || look.expr === "sleepy" || look.sunglasses) return;
    let raf = 0;
    const on = (e: PointerEvent) => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        const r = svg.getBoundingClientRect();
        const o = lookOffset(e.clientX, e.clientY, r.left + r.width / 2, r.top + r.height * 0.4, 2.3);
        svg.style.setProperty("--lx", `${o.x.toFixed(2)}px`);
        svg.style.setProperty("--ly", `${o.y.toFixed(2)}px`);
      });
    };
    window.addEventListener("pointermove", on, { passive: true });
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("pointermove", on);
    };
  }, [live, look.expr, look.sunglasses]);

  const pose: Pose = look.pose;
  const [aL, aR] = armAngles(pose, look.expr);
  const jump = pose === "jump";
  const [ex0, ex1] = g.ex;
  const kind = eyeKind(look.expr, look.squint);
  const happyBlush = look.blush || look.expr === "happy" || look.expr === "proud" || look.expr === "laughing" || look.expr === "wave";
  const holding = look.held !== "none" && (pose === "carry" || pose === "hug");

  const arm = (side: 0 | 1, angle: number, wave: boolean) => {
    const [sx, sy] = g.shoulder[side];
    const hand = pose === "stop" && side === 0;
    return (
      <g transform={`rotate(${angle} ${sx} ${sy})`}>
        <g className={wave ? "cp-wave" : undefined} style={{ transformOrigin: `${sx}px ${sy}px` }}>
          <rect x={sx - g.armW / 2} y={sy - 2} width={g.armW} height={g.arm} rx={g.armW / 2} fill={body} />
          <circle cx={sx} cy={sy + g.arm - 1} r={g.armW * 0.72} fill={body} />
          {hand && <rect x={sx - 6} y={sy + g.arm - 7} width="12" height="9" rx="4" fill={body} />}
        </g>
      </g>
    );
  };

  return (
    <svg ref={inner} viewBox="0 0 120 130" width={size} height={size * (130 / 120)} aria-hidden focusable="false" className={cn("shrink-0 overflow-visible", className)}>
      {/* a gentle shadow on the shelf */}
      <ellipse cx="60" cy="126" rx={ultra ? 34 : 28} ry="4.2" fill="var(--ink)" opacity={jump ? 0.08 : 0.16} />
      <g className={cn(live && "cp-bob")} style={{ transformOrigin: "60px 124px" }}>
        {/* feet */}
        <ellipse cx={g.feet[0]} cy={jump ? 116 : 121} rx={ultra ? 10 : 9} ry="4.4" fill={body} />
        <ellipse cx={g.feet[1]} cy={jump ? 116 : 121} rx={ultra ? 10 : 9} ry="4.4" fill={body} />
        {/* torso */}
        <rect x={g.torso.x} y={g.torso.y} width={g.torso.w} height={g.torso.h} rx={g.torso.r} fill={body} />
        <ellipse cx="60" cy={g.torso.y + 20} rx={g.torso.w * 0.26} ry="8" fill="var(--surface)" opacity="0.22" />
        {ultra && (
          <g fill={accent}>
            <path d="M60 90l-9 -5v10z" />
            <path d="M60 90l9 -5v10z" />
            <circle cx="60" cy="90" r="2.6" fill="var(--ink)" opacity="0.7" />
          </g>
        )}
        {/* arms */}
        {arm(0, aL, false)}
        {arm(1, aR, look.expr === "wave" && pose === "stand")}
        {holding && <Held held={look.held} ultra={ultra} />}
        {look.held !== "none" && !holding && pose !== "jump" && look.held === "mug" && (
          <g transform={`translate(${ultra ? 96 : 84} 104) scale(0.7)`}>
            <Held held="mug" ultra={false} />
          </g>
        )}
        {/* head */}
        {ultra ? (
          <ellipse cx="60" cy="48" rx="41" ry="37" fill={body} />
        ) : (
          <>
            <circle cx="60" cy="50" r="36" fill={body} />
            {!look.cap && (
              <g>
                <rect x="55.4" y="13" width="9.2" height="7" fill={body} />
                <rect x="49" y="4" width="22" height="12" rx="3.2" fill="var(--rented)" />
                <path d="M54 16v3M60 16v3M66 16v3" stroke="var(--rented-ink)" strokeWidth="1.4" strokeLinecap="round" opacity="0.7" />
                <circle cx="54" cy="9" r="1.5" fill="var(--surface)" opacity="0.85" />
              </g>
            )}
          </>
        )}
        <ellipse cx={ultra ? 40 : 42} cy={ultra ? 24 : 30} rx={ultra ? 14 : 11} ry="6" fill="var(--surface)" opacity="0.2" transform={`rotate(-24 ${ultra ? 40 : 42} ${ultra ? 24 : 30})`} />
        {/* face plate */}
        <rect x={g.plate.x} y={g.plate.y} width={g.plate.w} height={g.plate.h} rx={g.plate.r} fill="var(--surface)" />
        <Brows expr={look.expr} ex={g.ex} ey={g.ey} ultra={ultra} squint={look.squint} />
        {!look.sunglasses && (
          <>
            <Eye x={ex0} y={g.ey} kind={kind} k={`l${kind}`} />
            <Eye x={ex1} y={g.ey} kind={kind} k={`r${kind}`} />
          </>
        )}
        {ultra && !look.sunglasses && (
          <g fill="none" stroke={INK} strokeWidth="2.2" className={cn(look.adjust && "cp-adjust")}>
            <circle cx={ex0} cy={g.ey} r="9.6" />
            <circle cx={ex1} cy={g.ey} r="9.6" />
            <path d={`M${ex0 + 9.6} ${g.ey - 1}q${(ex1 - ex0 - 19.2) / 2} -3 ${ex1 - ex0 - 19.2} 0`} />
            <path d={`M${ex0 - 6} ${g.ey - 6}q3 -2 6 -1`} stroke="var(--surface)" strokeWidth="1.6" opacity="0.0" />
          </g>
        )}
        {look.sunglasses && (
          <g fill="#15151a">
            <rect x={ex0 - 10} y={g.ey - 7} width="20" height="14" rx="5.5" />
            <rect x={ex1 - 10} y={g.ey - 7} width="20" height="14" rx="5.5" />
            <rect x={ex0 + 9} y={g.ey - 3} width={ex1 - ex0 - 18} height="2.8" />
            <path d={`M${ex0 - 6} ${g.ey - 4}l5 -1M${ex1 - 6} ${g.ey - 4}l5 -1`} stroke="#fff" strokeWidth="1.6" strokeLinecap="round" opacity="0.55" />
          </g>
        )}
        {happyBlush && (
          <g fill={accent} opacity="0.3">
            <ellipse cx={ex0 - 8} cy={g.ey + 9} rx="4.6" ry="3" />
            <ellipse cx={ex1 + 8} cy={g.ey + 9} rx="4.6" ry="3" />
          </g>
        )}
        <Mouth expr={look.expr} x={60} y={g.my} ultra={ultra} />
        {look.expr === "laughing" && <path className="cp-tear" d={`M${ex1 + 6} ${g.ey + 3}q3 4 0 7q-3 -3 0 -7z`} fill="var(--owned)" opacity="0.7" />}
        {/* graduation cap */}
        {!ultra && look.cap && (
          <g>
            <path d="M42 22h36l-3 10q-15 6 -30 0z" fill="#17203a" />
            <path d="M28 22l32 -13l32 13l-32 13z" fill="#202b4d" stroke="#0e1426" strokeWidth="1" />
            <circle cx="60" cy="22" r="2" fill="var(--rented)" />
            <path d="M60 22l28 4v12" stroke="var(--rented)" strokeWidth="1.8" fill="none" strokeLinecap="round" />
            <circle cx="88" cy="40" r="2.6" fill="var(--rented)" />
          </g>
        )}
        {/* the night lamp: a small headlamp and a soft cone of light */}
        {!ultra && look.lamp && (
          <g>
            <path d="M26 44l-16 40l30 -8z" fill="var(--warn)" opacity="0.16" className="cp-glow" />
            <path d="M28 40q-4 -10 6 -22" fill="none" stroke="var(--ink-2)" strokeWidth="1.8" strokeLinecap="round" />
            <circle cx="26" cy="42" r="12" fill="var(--warn)" opacity="0.18" className="cp-glow" />
            <rect x="20" y="37" width="11" height="9" rx="3.5" fill="var(--warn)" stroke={INK} strokeWidth="1.4" />
          </g>
        )}
        {/* sweat drop, pop-ups and the rest of what an expression adds around the head */}
        {look.sweat && <path className="cp-drop" d={`M${ultra ? 96 : 90} 26q-4 6 0 9q4 -3 0 -9z`} fill="var(--owned)" opacity="0.75" />}
        {look.expr === "thinking" && (
          <g fill="var(--ink-2)">
            <circle className={cn(live && "cp-dot")} cx="94" cy="20" r="2.4" />
            <circle className={cn(live && "cp-dot")} cx="103" cy="12" r="3" style={{ animationDelay: "0.25s" }} />
            <circle className={cn(live && "cp-dot")} cx="113" cy="2" r="3.6" style={{ animationDelay: "0.5s" }} />
          </g>
        )}
        {look.expr === "sleepy" && (
          <text x="90" y="20" className={cn("fill-muted font-mono", live && "cp-z")} style={{ fontSize: 14, fontWeight: 700 }}>
            z
          </text>
        )}
        {look.pop !== "none" && (
          <g className={cn(live && "cp-pop")} style={{ transformOrigin: "98px 20px" }}>
            {look.pop === "sparkle" && <path d="M98 6l2.6 7.4L108 16l-7.4 2.6L98 26l-2.6 -7.4L88 16l7.4 -2.6z" fill="var(--warn)" stroke={INK} strokeWidth="0.8" />}
            {look.pop === "heart" && <path d="M98 24c-9 -6 -9 -14 -3 -14c3 0 3 3 3 3s0 -3 3 -3c6 0 6 8 -3 14z" fill="var(--bad)" />}
            {(look.pop === "!" || look.pop === "?") && (
              <g>
                <circle cx="98" cy="14" r="9" fill="var(--surface)" stroke={INK} strokeWidth="1.6" />
                <text x="98" y="19.5" textAnchor="middle" fill={INK} style={{ fontSize: 15, fontWeight: 800 }}>
                  {look.pop}
                </text>
              </g>
            )}
          </g>
        )}
      </g>
    </svg>
  );
});
