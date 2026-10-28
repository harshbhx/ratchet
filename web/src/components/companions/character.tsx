import { forwardRef, useEffect, useId, useImperativeHandle, useRef, type ReactNode } from "react";
import { cn } from "@/lib/utils";
import { eyeSpec, isSoloFx, lookOffset, type Expr, type Look, type Pose, type Who } from "./companion-model";

/**
 * Nano and Ultra, drawn from simple shapes (original artwork, no external assets). Nano is the student: small, round, blue, with an
 * orange adapter chip and a little antenna on its head. Ultra is the teacher: bigger, orange, round glasses and a bow tie. Bodies and
 * heads have a soft gradient, a rim light and a shaded edge; the face plate is a quiet surface tone with real eyes: sclera, iris, pupil,
 * two highlights, and eyelids that close for blinks and half-close for sleepy, smug or squinting. Colours are the design tokens.
 *
 * Decoration only (`aria-hidden`): whatever they say is text, in a live region. Only transform and opacity animate (the `.cp-*` CSS in
 * companions.css); with `live` off (static portraits, reduced motion) no class that moves is applied.
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
  eye: { rx: number; ry: number };
}

const GEOM: Record<Who, Geom> = {
  nano: {
    ex: [47, 73],
    ey: 54,
    my: 67,
    shoulder: [[46, 94], [74, 94]],
    arm: 22,
    armW: 8,
    torso: { x: 44, y: 84, w: 32, h: 34, r: 14 },
    feet: [49, 71],
    plate: { x: 31, y: 35, w: 58, h: 41, r: 19 },
    eye: { rx: 8, ry: 9 },
  },
  ultra: {
    ex: [44, 76],
    ey: 50,
    my: 64,
    shoulder: [[38, 94], [82, 94]],
    arm: 24,
    armW: 9,
    torso: { x: 34, y: 82, w: 52, h: 38, r: 16 },
    feet: [46, 74],
    plate: { x: 26, y: 30, w: 68, h: 43, r: 18 },
    eye: { rx: 6.2, ry: 7.2 },
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
    case "sip":
    case "watch":
      return [6, -118];
    case "fold":
      return [-74, 74];
    default:
      return expr === "wave" ? [6, -150] : [6, -6];
  }
}

const INK = "var(--ink)";
const PLATE = "var(--surface-2)";

function Eye({ x, y, idx, look, geom, ultra, uid, iris, live }: { x: number; y: number; idx: 0 | 1; look: Look; geom: Geom; ultra: boolean; uid: string; iris: string; live: boolean }) {
  const sp = eyeSpec(look.expr, look.squint, idx, ultra, look.fx === "yawn");
  const rx = geom.eye.rx * sp.scale;
  const ry = geom.eye.ry * sp.scale;
  const clip = `${uid}c${idx}`;
  const ir = sp.pupil + 2.2;
  return (
    <g transform={`translate(${x} ${y})`}>
      <clipPath id={clip}>
        <ellipse rx={rx} ry={ry} />
      </clipPath>
      <ellipse rx={rx} ry={ry} fill="#fbfbf8" stroke={INK} strokeOpacity="0.4" strokeWidth="1.3" />
      <g clipPath={`url(#${clip})`}>
        <g className={cn("cp-pupil", look.micro === "dart" && "cp-dart")} style={{ transform: `translate(calc(var(--lx, 0px) + ${sp.dx}px), calc(var(--ly, 0px) + ${sp.dy}px))` }}>
          <g className={live ? "cp-sacc" : undefined} style={{ animationDelay: `${idx * -0.9}s` }}>
            <circle r={ir} fill={iris} />
            <circle r={ir} fill="none" stroke="#000" strokeOpacity="0.25" strokeWidth="0.8" />
            <circle r={sp.pupil} fill="#0b0b0e" />
          </g>
        </g>
        {/* the highlights stay put on the eye, like light on glass */}
        <circle cx={rx * 0.28} cy={-ry * 0.34} r={sp.wet ? 2.3 : 1.7} fill="#fff" />
        <circle cx={-rx * 0.3} cy={ry * 0.32} r={sp.wet ? 1.2 : 0.9} fill="#fff" opacity="0.85" />
        {/* the lids: the top lid blinks (CSS), the lower lid lifts for a smile */}
        <g className={live ? "cp-lid" : undefined} style={{ ["--rest" as string]: sp.top, transform: `scaleY(${sp.top})`, transformBox: "fill-box", transformOrigin: "50% 0%" }}>
          <rect x={-rx - 1} y={-ry - 1.5} width={rx * 2 + 2} height={ry * 2 + 3} fill={PLATE} />
          <path d={`M${-rx - 1} ${ry + 1.5}H${rx + 1}`} stroke={INK} strokeWidth="2" strokeLinecap="round" />
        </g>
        {sp.bottom > 0 && (
          <g style={{ transform: `scaleY(${sp.bottom})`, transformBox: "fill-box", transformOrigin: "50% 100%" }}>
            <rect x={-rx - 1} y={-ry - 1.5} width={rx * 2 + 2} height={ry * 2 + 3} fill={PLATE} />
          </g>
        )}
      </g>
    </g>
  );
}

function Mouth({ look, x, y, ultra, talking }: { look: Look; x: number; y: number; ultra: boolean; talking: boolean }) {
  const expr = look.expr;
  const line = { stroke: INK, strokeWidth: 2.4, strokeLinecap: "round" as const, fill: "none" };
  if (talking) {
    // Three mouth shapes, one shown at a time (CSS, stepped): shut, wide, round.
    return (
      <g>
        <path className="cp-v" style={{ animationDelay: "0s" }} d={`M${x - 5} ${y}h10`} {...line} />
        <g className="cp-v" style={{ animationDelay: "-0.2s" }}>
          <path d={`M${x - 7} ${y - 3}q7 12 14 0z`} fill={INK} />
          <path d={`M${x - 3.5} ${y + 3.4}q3.5 -3 7 0q-3.5 2.4 -7 0z`} fill="var(--rented)" opacity="0.85" />
        </g>
        <ellipse className="cp-v" style={{ animationDelay: "-0.4s" }} cx={x} cy={y + 1.5} rx="3.2" ry="3.8" fill={INK} />
      </g>
    );
  }
  if (look.fx === "yawn") return <ellipse className="cp-yawn" cx={x} cy={y + 2} rx="5" ry="6.5" fill={INK} />;
  const cls = look.micro === "twitch" ? "cp-twitch" : undefined;
  const g = (n: ReactNode) => <g className={cls}>{n}</g>;
  switch (expr) {
    case "happy":
    case "wave":
      return g(
        <g>
          <path d={`M${x - 8} ${y - 2}q8 12 16 0z`} fill={INK} />
          <path d={`M${x - 4} ${y + 4.4}q4 -3.4 8 0q-4 2.6 -8 0z`} fill="var(--rented)" opacity="0.85" />
        </g>,
      );
    case "laughing":
      return g(
        <g>
          <path d={`M${x - 10} ${y - 3}q10 18 20 0z`} fill={INK} />
          <path d={`M${x - 5} ${y + 6}q5 -4.4 10 0q-5 3.4 -10 0z`} fill="var(--rented)" opacity="0.85" />
        </g>,
      );
    case "proud":
      return g(<path d={`M${x - 7} ${y}q7 7 14 0`} {...line} />);
    case "sleepy":
      return g(<ellipse cx={x} cy={y + 2} rx="2.6" ry="2" fill={INK} opacity="0.85" />);
    case "surprised":
      return g(<ellipse cx={x} cy={y + 1} rx="3.3" ry="4.4" fill={INK} />);
    case "thinking":
      return g(<path d={`M${x - 6} ${y + 1}q3 -3 6 0t6 0`} {...line} />);
    case "determined":
      return g(<path d={`M${x - 6} ${y}h12`} {...line} />);
    case "sad":
      return g(<path d={`M${x - 6} ${y + 4}q6 -7 12 0`} {...line} />);
    default:
      return g(ultra ? <path d={`M${x - 6} ${y + 1}q6 3 13 -2`} {...line} /> : <path d={`M${x - 6} ${y - 1}q6 5 12 0`} {...line} />);
  }
}

function Brows({ look, ex, ey, ultra }: { look: Look; ex: [number, number]; ey: number; ultra: boolean }) {
  const expr = look.expr;
  const by = ey - 12.5;
  const st = { stroke: INK, strokeWidth: 2.5, strokeLinecap: "round" as const, fill: "none", opacity: 0.85 };
  const slope = (x: number, dir: 1 | -1, outerDy: number, innerDy: number) => <path d={`M${x - dir * 6.5} ${by + outerDy}Q${x} ${by + (outerDy + innerDy) / 2 - 1} ${x + dir * 6.5} ${by + innerDy}`} {...st} />;
  if (expr === "determined" || look.squint) return <g>{slope(ex[0], 1, -2.5, 3)}{slope(ex[1], -1, -2.5, 3)}</g>;
  if (expr === "sad") return <g>{slope(ex[0], 1, 3, -3.5)}{slope(ex[1], -1, 3, -3.5)}</g>;
  if (expr === "surprised")
    return <g><path d={`M${ex[0] - 6} ${by - 1}q6 -6 12 0`} {...st} /><path d={`M${ex[1] - 6} ${by - 1}q6 -6 12 0`} {...st} /></g>;
  if (expr === "thinking") return <g><path d={`M${ex[0] - 6} ${by + 1}h12`} {...st} /><path d={`M${ex[1] - 6} ${by - 1}q6 -5 12 0`} {...st} /></g>;
  if (expr === "happy" || expr === "wave" || expr === "proud") return <g><path d={`M${ex[0] - 6} ${by}q6 -3 12 0`} {...st} /><path d={`M${ex[1] - 6} ${by}q6 -3 12 0`} {...st} /></g>;
  if (!ultra) return null;
  if (look.brow === "raise") return <g><path d={`M${ex[0] - 6.5} ${by + 0.5}h13`} {...st} /><path d={`M${ex[1] - 6.5} ${by - 5}q6.5 -4 13 0`} {...st} /></g>;
  // Ultra's brows: level, the right one a little higher. Dry.
  return <g><path d={`M${ex[0] - 6.5} ${by + 0.5}h13`} {...st} /><path d={`M${ex[1] - 6.5} ${by - 2.2}q6.5 -2 13 0`} {...st} /></g>;
}

function Held({ held, ultra, sip, text = "" }: { held: Look["held"]; ultra: boolean; sip?: boolean; text?: string }) {
  if (held === "none") return null;
  const s = ultra ? 1.15 : 1;
  return (
    <g transform={sip ? "translate(101 80) rotate(-24) scale(0.78)" : `translate(60 ${ultra ? 104 : 102}) scale(${s})`}>
      {held === "book" && (
        <g>
          <rect x="-14" y="-9" width="28" height="19" rx="2.5" fill="var(--surface)" stroke={INK} strokeWidth="1.6" />
          <path d="M0 -9v19" stroke={INK} strokeWidth="1.4" />
          <path d="M-10 -3h7M-10 1h7M3 -3h7M3 1h7" stroke="var(--muted)" strokeWidth="1.2" strokeLinecap="round" />
          <path d="M7 -9v7l2.5 -2l2.5 2v-7z" fill="var(--rented)" />
          <path className="cp-page" d="M0 -8h13v17h-13z" fill="var(--surface-2)" stroke={INK} strokeWidth="1" />
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
      {held === "score" && (
        <g>
          <rect x="-18" y="-12" width="36" height="25" rx="4" fill="var(--surface)" stroke={INK} strokeWidth="1.6" />
          <text x="0" y="5.5" textAnchor="middle" fill="var(--owned-ink)" style={{ fontSize: 13, fontWeight: 800 }}>{text}</text>
        </g>
      )}
      {held === "tag" && (
        <g>
          <rect x="-31" y="-8.5" width="62" height="17" rx="8.5" fill="var(--surface)" stroke={INK} strokeWidth="1.5" />
          <text x="0" y="3.2" textAnchor="middle" fill="var(--ink)" style={{ fontSize: 6.4, fontWeight: 700 }}>{text}</text>
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
  /** How it is travelling: a walk cycle, a jump or a calm glide. */
  moving?: "walk" | "jump" | "glide" | null;
  /** A speech bubble is up: the mouth moves. */
  talking?: boolean;
  /** Look toward a point (a unit-ish vector from this character), e.g. at the one who is speaking. */
  gaze?: { x: number; y: number } | null;
  className?: string;
}

export const Character = forwardRef<SVGSVGElement, CharacterProps>(function Character({ who, look, size = 96, live = true, moving = null, talking = false, gaze = null, className }, ref) {
  const inner = useRef<SVGSVGElement | null>(null);
  useImperativeHandle(ref, () => inner.current as SVGSVGElement);
  const uid = useId().replace(/[^a-zA-Z0-9]/g, "");
  const ultra = who === "ultra";
  const g = GEOM[who];
  const body = ultra ? "var(--rented)" : "var(--owned)";
  const accent = ultra ? "var(--owned)" : "var(--rented)";
  const iris = ultra ? "#8a4b1f" : "var(--owned)";
  const dark = `color-mix(in srgb, ${body} 78%, black)`;

  // The pupils and the head follow the pointer, or look toward `gaze`.
  useEffect(() => {
    const svg = inner.current;
    if (!svg || !live) return;
    const set = (x: number, y: number) => {
      svg.style.setProperty("--lx", `${(x * 2.3).toFixed(2)}px`);
      svg.style.setProperty("--ly", `${(y * 2.3).toFixed(2)}px`);
      svg.style.setProperty("--hx", `${(x * 1.4).toFixed(2)}px`);
      svg.style.setProperty("--tilt", `${(x * 3).toFixed(2)}deg`);
    };
    if (gaze) {
      set(gaze.x, gaze.y);
      return;
    }
    let raf = 0;
    const on = (e: PointerEvent) => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        const r = svg.getBoundingClientRect();
        const o = lookOffset(e.clientX, e.clientY, r.left + r.width / 2, r.top + r.height * 0.4, 1);
        set(o.x, o.y);
      });
    };
    window.addEventListener("pointermove", on, { passive: true });
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("pointermove", on);
    };
  }, [live, gaze]);

  const pose: Pose = look.pose;
  const [aL, aR] = armAngles(pose, look.expr);
  const jump = pose === "jump" || moving === "jump";
  const [ex0, ex1] = g.ex;
  const blushOn = look.blush || look.expr === "happy" || look.expr === "proud" || look.expr === "laughing" || look.expr === "wave";
  // One prop at a time: an activity that is its own prop (juggling, a pocket watch, a door) replaces whatever is held.
  const solo = isSoloFx(look.fx);
  const holding = !solo && look.held !== "none" && (pose === "carry" || pose === "hug");
  const sipping = !solo && pose === "sip" && look.held === "mug";
  const shadesOver = look.sunglasses && look.shadesDown;
  const gb = `${uid}b`;
  const gs = `${uid}s`;
  const footY = jump ? 116 : 121;

  const arm = (side: 0 | 1, angle: number, wave: boolean) => {
    const [sx, sy] = g.shoulder[side];
    const flail = moving === "jump" && live;
    return (
      <g transform={`rotate(${angle} ${sx} ${sy})`}>
        <g className={cn(wave && live && "cp-wave", flail && (side === 0 ? "cp-flail-a" : "cp-flail-b"))} style={{ transformOrigin: `${sx}px ${sy}px` }}>
          <rect x={sx - g.armW / 2} y={sy - 2} width={g.armW} height={g.arm} rx={g.armW / 2} fill={body} />
          <circle cx={sx} cy={sy + g.arm - 1} r={g.armW * 0.74} fill={body} />
          {pose === "stop" && side === 0 && <rect x={sx - 6} y={sy + g.arm - 7} width="12" height="9" rx="4" fill={body} />}
        </g>
      </g>
    );
  };

  // The adapter chip: a learning meter, one segment lit for each lesson learned. It stays visible under the graduation cap.
  const chip = (
    <>
      {look.glow && <circle cx="60" cy="14" r="17" fill="var(--warn)" className={cn(live && "cp-levelup")} opacity="0.35" />}
                  <g className={cn(live && "cp-chip")} style={{ transformOrigin: "60px 17px" }}>
                    <rect x="48" y="9" width="24" height="11.5" rx="3.4" fill={look.glow ? "#ffd27a" : "var(--rented)"} />
                    <rect x="48" y="9" width="24" height="5" rx="3" fill="#fff" opacity="0.2" />
                    {/* the chip is a learning meter: one segment lights for each lesson learned */}
                    {Array.from({ length: 7 }, (_, i) => (
                      <rect key={i} x={49.2 + i * 3.2} y="10.6" width="2.6" height="4.6" rx="0.8" fill={i < look.learn ? "#fff" : "var(--rented-ink)"} opacity={i < look.learn ? 0.96 : 0.4} className={live && look.learn === i + 1 ? "cp-seg" : undefined} />
                    ))}
                    <path d="M53 17.8v2.6M60 17.8v2.6M67 17.8v2.6" stroke="var(--rented-ink)" strokeWidth="1.2" strokeLinecap="round" opacity="0.6" />
                  </g>
    </>
  );

  return (
    <svg
      ref={inner}
      viewBox="0 0 120 130"
      width={size}
      height={size * (130 / 120)}
      aria-hidden
      focusable="false"
      className={cn("shrink-0 overflow-visible", live && moving === "walk" && "cp-walking", live && moving === "glide" && "cp-gliding", className)}
    >
      <defs>
        <linearGradient id={gb} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" style={{ stopColor: `color-mix(in srgb, ${body} 80%, white)` }} />
          <stop offset="0.55" style={{ stopColor: body }} />
          <stop offset="1" style={{ stopColor: `color-mix(in srgb, ${body} 82%, black)` }} />
        </linearGradient>
        <radialGradient id={gs} cx="0.36" cy="0.28" r="0.85">
          <stop offset="0" stopColor="#fff" stopOpacity="0.24" />
          <stop offset="0.5" stopColor="#fff" stopOpacity="0" />
          <stop offset="1" stopColor="#000" stopOpacity="0.24" />
        </radialGradient>
      </defs>
      {/* a soft shadow on whatever it stands on */}
      <ellipse cx="60" cy="126" rx={ultra ? 34 : 28} ry="4.2" fill="var(--ink)" opacity={jump ? 0.07 : 0.16} />
      <g className={cn(live && "cp-shift")} style={{ transformOrigin: "60px 124px" }}>
      <g className={cn(live && "cp-bob")} style={{ transformOrigin: "60px 124px" }}>
        <g className="cp-foot-a">
          <ellipse cx={g.feet[0]} cy={footY} rx={ultra ? 10 : 9} ry="4.6" fill={dark} />
        </g>
        <g className="cp-foot-b">
          <ellipse cx={g.feet[1]} cy={footY} rx={ultra ? 10 : 9} ry="4.6" fill={dark} />
        </g>
        {/* torso */}
        <rect x={g.torso.x} y={g.torso.y} width={g.torso.w} height={g.torso.h} rx={g.torso.r} fill={`url(#${gb})`} />
        <rect x={g.torso.x} y={g.torso.y} width={g.torso.w} height={g.torso.h} rx={g.torso.r} fill={`url(#${gs})`} />
        <ellipse cx="60" cy={g.torso.y + 21} rx={g.torso.w * 0.24} ry="7.5" fill={PLATE} opacity="0.3" />
        {ultra && (
          <g>
            <path d="M60 90l-9.5 -5.5v11z" fill={accent} />
            <path d="M60 90l9.5 -5.5v11z" fill={accent} />
            <circle cx="60" cy="90" r="2.8" fill={INK} opacity="0.75" />
          </g>
        )}
        {look.fx === "door" && (
          <g transform="translate(-4 24) scale(0.78)">
            <rect x="-14" y="34" width="36" height="92" rx="3" fill="var(--surface-2)" stroke={INK} strokeWidth="2" />
            <rect x="-8" y="42" width="24" height="30" rx="2" fill="none" stroke={INK} strokeOpacity="0.35" strokeWidth="1.4" />
            <rect x="-8" y="80" width="24" height="36" rx="2" fill="none" stroke={INK} strokeOpacity="0.35" strokeWidth="1.4" />
            <circle cx="16" cy="84" r="2.6" fill="var(--warn)" stroke={INK} strokeWidth="1" />
          </g>
        )}
        {arm(0, aL, false)}
        {arm(1, aR, look.expr === "wave" && pose === "stand")}
        {holding && <Held held={look.held} ultra={ultra} text={look.text} />}
        {look.fx === "juggle" && (
          <g aria-hidden>
            {["AS", "ON", "*"].map((t, i) => (
              <g key={t} className={cn(live && "cp-jug")} style={{ animationDelay: `${i * -0.4}s` }}>
                <rect x="-8" y="-4.5" width="16" height="9" rx="3" transform="translate(60 98)" fill="var(--surface)" stroke={INK} strokeWidth="1.1" />
                <text x="60" y="101.2" textAnchor="middle" fill="var(--rented-ink)" style={{ fontSize: 6.6, fontWeight: 800 }}>{t}</text>
              </g>
            ))}
          </g>
        )}
        {look.fx === "pen" && <path className={cn(live && "cp-mark")} d="M52 104l3 -3l3 3l3 -3l3 3" stroke="var(--bad)" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" fill="none" />}
        {look.fx === "watch" && (
          <g transform="translate(103 80)">
            <path d="M0 4q-18 18 -28 20" stroke={INK} strokeOpacity="0.5" strokeWidth="1.2" fill="none" />
            <circle r="7" fill="var(--surface)" stroke={INK} strokeWidth="1.6" />
            <path className={cn(live && "cp-tick")} d="M0 0V-4.4" stroke={INK} strokeWidth="1.4" strokeLinecap="round" style={{ transformOrigin: "0 0" }} />
            <path d="M0 0l3.2 1.6" stroke={INK} strokeWidth="1.4" strokeLinecap="round" />
            <circle cy="-8.4" r="1.6" fill={INK} />
          </g>
        )}
        {sipping && <Held held="mug" ultra sip />}

        {/* the head turns a little toward what it is looking at */}
        <g className={cn("cp-head", look.micro === "shake" && "cp-hshake")} style={{ transformOrigin: "60px 84px", transform: live ? "translate(var(--hx, 0px), 0) rotate(var(--tilt, 0deg))" : undefined }}>
          {ultra ? (
            <>
              <ellipse className={cn(live && "cp-ear")} cx="19.5" cy="50" rx="4.4" ry="7" fill={dark} style={{ transformOrigin: "22px 50px" }} />
              <ellipse className={cn(live && "cp-ear")} cx="100.5" cy="50" rx="4.4" ry="7" fill={dark} style={{ transformOrigin: "98px 50px", animationDelay: "-2s" }} />
              <ellipse cx="60" cy="48" rx="41" ry="37" fill={`url(#${gb})`} />
              <ellipse cx="60" cy="48" rx="41" ry="37" fill={`url(#${gs})`} />
              <path d="M21.5 35.3A41 37 0 0 1 70.6 12.3" stroke="#fff" strokeOpacity="0.42" strokeWidth="2.4" strokeLinecap="round" fill="none" />
            </>
          ) : (
            <>
              <circle cx="60" cy="50" r="36" fill={`url(#${gb})`} />
              <circle cx="60" cy="50" r="36" fill={`url(#${gs})`} />
              <path d="M26.2 37.7A36 36 0 0 1 69.3 15.2" stroke="#fff" strokeOpacity="0.42" strokeWidth="2.4" strokeLinecap="round" fill="none" />
              {!look.cap && (
                <g>
                  {/* the antenna and the adapter chip wobble a little */}
                  <g className={cn(live && "cp-antenna")} style={{ transformOrigin: "60px 14px" }}>
                    <path d="M60 14V3" stroke={dark} strokeWidth="2" strokeLinecap="round" />
                    <circle cx="60" cy="2.6" r="2.8" fill={accent} stroke={INK} strokeOpacity="0.25" strokeWidth="0.8" />
                  </g>
                  {chip}
                </g>
              )}
            </>
          )}
          {/* sunglasses worn pushed up on the head */}
          {look.sunglasses && !look.shadesDown && !ultra && (
            <g fill="#15151a" opacity="0.95">
              <rect x="39" y="24" width="18" height="10" rx="4.5" />
              <rect x="63" y="24" width="18" height="10" rx="4.5" />
              <rect x="56" y="27" width="8" height="2.4" />
              <path d="M42 27l5 -1.2M66 27l5 -1.2" stroke="#fff" strokeWidth="1.5" strokeLinecap="round" opacity="0.5" />
            </g>
          )}
          {/* face plate, with a little depth at its edge */}
          <rect x={g.plate.x} y={g.plate.y} width={g.plate.w} height={g.plate.h} rx={g.plate.r} fill={PLATE} />
          <rect x={g.plate.x + 1} y={g.plate.y + 1} width={g.plate.w - 2} height={g.plate.h - 2} rx={g.plate.r - 1} fill="none" stroke={INK} strokeOpacity="0.1" strokeWidth="2" />
          <g key={`b${look.expr}${look.brow}${look.squint}`} className={live ? "cp-in" : undefined}>
            <Brows look={look} ex={g.ex} ey={g.ey} ultra={ultra} />
          </g>
          {!shadesOver && (
            <>
              <Eye x={ex0} y={g.ey} idx={0} look={look} geom={g} ultra={ultra} uid={uid} iris={iris} live={live} />
              <Eye x={ex1} y={g.ey} idx={1} look={look} geom={g} ultra={ultra} uid={uid} iris={iris} live={live} />
            </>
          )}
          {ultra && !shadesOver && (
            <g fill="none" stroke={INK} strokeWidth="2.3" className={cn(look.adjust && "cp-adjust")}>
              <circle cx={ex0} cy={g.ey} r="10.2" />
              <circle cx={ex1} cy={g.ey} r="10.2" />
              <path d={`M${ex0 + 10.2} ${g.ey - 1}q${(ex1 - ex0 - 20.4) / 2} -3 ${ex1 - ex0 - 20.4} 0`} />
              <path className={cn(live && "cp-glint")} d={`M${ex0 - 6} ${g.ey - 5}l5 -3`} stroke="#fff" strokeWidth="2.2" strokeLinecap="round" opacity="0.8" />
            </g>
          )}
          {shadesOver && (
            <g>
              <g fill="#15151a">
                <rect x={ex0 - 10.5} y={g.ey - 7.5} width="21" height="15" rx="6" />
                <rect x={ex1 - 10.5} y={g.ey - 7.5} width="21" height="15" rx="6" />
                <rect x={ex0 + 10} y={g.ey - 3.5} width={ex1 - ex0 - 20} height="3" />
              </g>
              <path d={`M${ex0 - 7} ${g.ey - 4}l6 -1.6M${ex1 - 7} ${g.ey - 4}l6 -1.6`} stroke="#fff" strokeWidth="1.8" strokeLinecap="round" opacity="0.5" />
            </g>
          )}
          <g fill={accent} style={{ opacity: blushOn ? 0.36 : 0, transition: "opacity 0.4s" }}>
            <ellipse cx={ex0 - 9} cy={g.ey + 11} rx="4.8" ry="3.1" />
            <ellipse cx={ex1 + 9} cy={g.ey + 11} rx="4.8" ry="3.1" />
          </g>
          <g key={`m${look.expr}${look.fx}${talking}`} className={live ? "cp-in" : undefined}>
            <Mouth look={look} x={60} y={g.my} ultra={ultra} talking={talking && live} />
          </g>
          {look.expr === "laughing" && <path className="cp-tear" d={`M${ex1 + 7} ${g.ey + 4}q3 4 0 7q-3 -3 0 -7z`} fill="var(--owned)" opacity="0.7" />}
          {!ultra && look.cap && (
            <g>
              <path d="M42 22h36l-3 10q-15 6 -30 0z" fill="#17203a" />
              <path d="M28 22l32 -13l32 13l-32 13z" fill="#202b4d" stroke="#0e1426" strokeWidth="1" />
              <g transform="translate(60 30) scale(0.8) translate(-60 -15)">{chip}</g>
              <path d="M60 22l28 4v12" stroke="var(--rented)" strokeWidth="1.8" fill="none" strokeLinecap="round" />
              <circle cx="88" cy="40" r="2.6" fill="var(--rented)" />
            </g>
          )}
          {!ultra && look.lamp && (
            <g>
              <path d="M26 44l-16 40l30 -8z" fill="var(--warn)" opacity="0.16" className={cn(live && "cp-glow")} />
              <path d="M28 40q-4 -10 6 -22" fill="none" stroke="var(--ink-2)" strokeWidth="1.8" strokeLinecap="round" />
              <circle cx="26" cy="42" r="12" fill="var(--warn)" opacity="0.18" className={cn(live && "cp-glow")} />
              <rect x="20" y="37" width="11" height="9" rx="3.5" fill="var(--warn)" stroke={INK} strokeWidth="1.4" />
            </g>
          )}
        </g>
        {look.sweat && <path className={cn(live && "cp-drop")} d={`M${ultra ? 98 : 92} 26q-4 6 0 9q4 -3 0 -9z`} fill="var(--owned)" opacity="0.75" />}
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
            {look.pop === "sparkle" && <path d="M98 6l2.6 7.4L108 16l-7.4 2.6L98 26l-2.6 -7.4L88 16l7.4 -2.6z" fill="var(--rented)" stroke={INK} strokeWidth="0.8" />}
            {look.pop === "heart" && <path d="M98 24c-9 -6 -9 -14 -3 -14c3 0 3 3 3 3s0 -3 3 -3c6 0 6 8 -3 14z" fill="var(--bad)" />}
            {(look.pop === "!" || look.pop === "?" || look.pop === "+1") && (
              <g>
                <circle cx="98" cy="14" r="9" fill="var(--surface)" stroke={INK} strokeWidth="1.6" />
                <text x="98" y="19" textAnchor="middle" fill={look.pop === "+1" ? "var(--owned-ink)" : INK} style={{ fontSize: look.pop === "+1" ? 10.5 : 15, fontWeight: 800 }}>
                  {look.pop}
                </text>
              </g>
            )}
          </g>
        )}
      </g>
      </g>
    </svg>
  );
});
