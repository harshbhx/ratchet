import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { Pause, Play } from "lucide-react";
import { SIM_DAYS } from "@/lib/sim";
import { cn, fmtPct, fmtUsd } from "@/lib/utils";
import { WHO_TEXT } from "@/lib/who-model";
import { EASE, SimBadge, ratchetPath, useElementWidth, useOnScreen } from "../shared";

const NIGHTS = SIM_DAYS.length;
const NIGHT_MS = 2600;
const HOLD_MS = 4200;
const SPAWN_MS = 150;
const POOL = 48;

type Route = "nano" | "ultra";
interface Dot {
  born: number;
  route: Route;
}

function geometry(W: number) {
  const narrow = W < 460;
  const H = narrow ? 236 : 288;
  const cy = H / 2;
  const appW = narrow ? 58 : 78;
  const appH = narrow ? 44 : 54;
  const app = { x: (narrow ? 8 : 16) + appW / 2, y: cy, w: appW, h: appH };
  const gw = { x: W * (narrow ? 0.45 : 0.46), y: cy, r: narrow ? 27 : 36 };
  const outR = narrow ? 19 : 24;
  const outX = W - (narrow ? 10 : 18) - outR;
  const nano = { x: outX, y: H * 0.23, r: outR };
  const ultra = { x: outX, y: H * 0.77, r: outR };
  const leg1 = `M${app.x + appW / 2 + 6} ${cy} L${gw.x - gw.r - 8} ${cy}`;
  const branch = (to: { x: number; y: number; r: number }) => {
    const sx = gw.x + gw.r + 8;
    const ex = to.x - to.r - 8;
    const mx = sx + (ex - sx) * 0.55;
    return `M${sx} ${cy} C${mx} ${cy} ${mx} ${to.y} ${ex} ${to.y}`;
  };
  return { narrow, H, app, gw, nano, ultra, leg1, legNano: branch(nano), legUltra: branch(ultra) };
}

/**
 * The hero's one ambient loop: requests flow from your app into the Ratchet gateway and are
 * answered by Nano (yours) or Ultra (rented). Each "night" the wheel clicks one tooth forward and the
 * share routed to Nano follows the reference simulation (SIM_DAYS).
 */
export function HeroFlow() {
  const reduce = useReducedMotion() ?? false;
  const [wrapRef, W] = useElementWidth<HTMLDivElement>();
  const cardRef = useRef<HTMLDivElement | null>(null);
  const onScreen = useOnScreen(cardRef);
  const [paused, setPaused] = useState(false);
  const [night, setNight] = useState(reduce ? NIGHTS : 1);
  const [ticks, setTicks] = useState(reduce ? NIGHTS - 1 : 0);

  const g = useMemo(() => (W > 0 ? geometry(W) : null), [W]);
  const leg1Ref = useRef<SVGPathElement | null>(null);
  const nanoRef = useRef<SVGPathElement | null>(null);
  const ultraRef = useRef<SVGPathElement | null>(null);
  const dotRefs = useRef<(SVGCircleElement | null)[]>([]);
  const running = !reduce && !paused && onScreen;
  const runningRef = useRef(running);
  runningRef.current = running;
  const nightRef = useRef(night);
  nightRef.current = night;

  // Reduced motion: final night, dots drawn once in a static spread.
  useEffect(() => {
    if (reduce) {
      setNight(NIGHTS);
      setTicks(NIGHTS - 1);
    }
  }, [reduce]);

  useEffect(() => {
    if (!g || !leg1Ref.current || !nanoRef.current || !ultraRef.current) return;
    const leg1 = leg1Ref.current;
    const legs: Record<Route, SVGPathElement> = { nano: nanoRef.current, ultra: ultraRef.current };
    const len1 = leg1.getTotalLength();
    const len2: Record<Route, number> = { nano: legs.nano.getTotalLength(), ultra: legs.ultra.getTotalLength() };
    const speed = Math.max(0.11, W / 3600); // px per ms
    const color = (r: Route) => (r === "nano" ? "var(--owned)" : "var(--rented)");

    const place = (el: SVGCircleElement, s: number, route: Route) => {
      const total = len1 + len2[route];
      let pt: DOMPoint;
      let fill = "var(--muted)";
      if (s <= len1) pt = leg1.getPointAtLength(s);
      else {
        pt = legs[route].getPointAtLength(Math.min(len2[route], s - len1));
        fill = color(route);
      }
      const fadeIn = Math.min(1, s / 24);
      const fadeOut = Math.min(1, (total - s) / 18);
      el.setAttribute("cx", pt.x.toFixed(1));
      el.setAttribute("cy", pt.y.toFixed(1));
      el.style.fill = fill;
      el.style.opacity = String(Math.max(0, Math.min(fadeIn, fadeOut)));
    };

    const els = dotRefs.current;
    if (reduce) {
      // Static end state: a calm spread along each path, 98% to Nano.
      const statics: [number, Route][] = [
        [0.12, "nano"], [0.32, "nano"], [0.52, "nano"], [0.72, "nano"], [0.9, "nano"],
        [1.12, "nano"], [1.3, "nano"], [1.5, "nano"], [1.7, "nano"], [1.42, "ultra"],
      ];
      els.forEach((el) => el && (el.style.opacity = "0"));
      statics.forEach(([f, route], i) => {
        const el = els[i];
        if (!el) return;
        const s = f <= 1 ? f * len1 : len1 + (f - 1) * len2[route];
        place(el, s, route);
        el.style.opacity = "1";
      });
      return;
    }

    const dots: (Dot | null)[] = Array(POOL).fill(null);
    let clock = 0;
    let lastSpawn = -SPAWN_MS;
    let acc = 0.5;
    let nightIdx = nightRef.current - 1;
    let nightT = 0;
    let raf = 0;
    let last = performance.now();

    const frame = (now: number) => {
      const dt = Math.min(64, now - last);
      last = now;
      if (runningRef.current) {
        clock += dt;
        nightT += dt;
        const limit = nightIdx === NIGHTS - 1 ? HOLD_MS : NIGHT_MS;
        if (nightT >= limit) {
          nightT = 0;
          nightIdx = (nightIdx + 1) % NIGHTS;
          setNight(nightIdx + 1);
          setTicks((t) => t + 1);
        }
        if (clock - lastSpawn >= SPAWN_MS) {
          lastSpawn = clock;
          const free = dots.findIndex((d) => d === null);
          if (free >= 0) {
            acc += SIM_DAYS[nightIdx].student;
            let route: Route = "ultra";
            if (acc >= 1) {
              route = "nano";
              acc -= 1;
            }
            dots[free] = { born: clock, route };
          }
        }
        for (let i = 0; i < POOL; i++) {
          const d = dots[i];
          const el = els[i];
          if (!el) continue;
          if (!d) {
            el.style.opacity = "0";
            continue;
          }
          const s = (clock - d.born) * speed;
          if (s >= len1 + len2[d.route]) {
            dots[i] = null;
            el.style.opacity = "0";
            continue;
          }
          place(el, s, d.route);
        }
      }
      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, [g, W, reduce]);

  const day = SIM_DAYS[night - 1];
  const share = day.student;
  const label = `Animated illustration, simulation: requests flow from your app through the Ratchet gateway to Nano (with the adapter you own) or Ultra (rented). Over ${NIGHTS} nights the share answered by Nano rises from ${fmtPct(SIM_DAYS[0].student)} to ${fmtPct(SIM_DAYS[NIGHTS - 1].student)}, and the cost per 1,000 requests falls from ${fmtUsd(SIM_DAYS[0].costPer1k, 3)} to ${fmtUsd(SIM_DAYS[NIGHTS - 1].costPer1k, 3)}.`;

  return (
    <div ref={cardRef} className="relative overflow-hidden rounded-[22px] border border-line bg-surface/90 shadow-card backdrop-blur-sm">
      <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-2.5 sm:px-5">
        <div className="flex min-w-0 items-center gap-2">
          <SimBadge />
          <span className="truncate text-[12.5px] text-muted">Ten nights of support tickets</span>
        </div>
        {!reduce && (
          <button
            type="button"
            onClick={() => setPaused((p) => !p)}
            aria-pressed={paused}
            aria-label={paused ? "Play animation" : "Pause animation"}
            className="inline-flex size-8 shrink-0 items-center justify-center rounded-full border border-line text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink"
          >
            {paused ? <Play className="size-3.5" /> : <Pause className="size-3.5" />}
          </button>
        )}
      </div>

      <div ref={wrapRef} className="relative px-0">
        {g && (
          <svg width={W} height={g.H} viewBox={`0 0 ${W} ${g.H}`} role="img" aria-label={label} className="block">
            {/* paths */}
            <path ref={leg1Ref} d={g.leg1} fill="none" className="stroke-line-strong" strokeWidth={1.5} strokeLinecap="round" />
            <path
              ref={nanoRef}
              d={g.legNano}
              fill="none"
              stroke="var(--owned)"
              strokeWidth={1.5 + share * 1.5}
              strokeLinecap="round"
              style={{ opacity: 0.18 + share * 0.5, transition: "opacity .6s, stroke-width .6s" }}
            />
            <path
              ref={ultraRef}
              d={g.legUltra}
              fill="none"
              stroke="var(--rented)"
              strokeWidth={1.5 + (1 - share) * 1.5}
              strokeLinecap="round"
              style={{ opacity: 0.18 + (1 - share) * 0.5, transition: "opacity .6s, stroke-width .6s" }}
            />

            {/* app */}
            <g>
              <rect
                x={g.app.x - g.app.w / 2}
                y={g.app.y - g.app.h / 2}
                width={g.app.w}
                height={g.app.h}
                rx={10}
                className="fill-surface-2 stroke-line-strong"
                strokeWidth={1}
              />
              {[0, 1, 2].map((i) => (
                <circle key={i} cx={g.app.x - g.app.w / 2 + 9 + i * 6} cy={g.app.y - g.app.h / 2 + 9} r={1.7} className="fill-line-strong" />
              ))}
              <rect x={g.app.x - g.app.w / 2 + 8} y={g.app.y - 3} width={g.app.w * 0.55} height={3} rx={1.5} className="fill-line-strong" />
              <rect x={g.app.x - g.app.w / 2 + 8} y={g.app.y + 5} width={g.app.w * 0.35} height={3} rx={1.5} className="fill-line-strong" />
              <text x={g.app.x} y={g.app.y + g.app.h / 2 + 20} textAnchor="middle" className="fill-ink-2 text-[12px] font-medium">
                Your app
              </text>
            </g>

            {/* gateway: the ratchet wheel clicks one tooth per night */}
            <g>
              <circle cx={g.gw.x} cy={g.gw.y} r={g.gw.r + 10} className="fill-bg-sunken stroke-line" strokeWidth={1} />
              <g
                style={{
                  transform: `rotate(${ticks * 36}deg)`,
                  transformOrigin: `${g.gw.x}px ${g.gw.y}px`,
                  transition: reduce ? "none" : "transform 520ms cubic-bezier(0.22, 1, 0.36, 1)",
                }}
              >
                <path d={ratchetPath(10, g.gw.r, g.gw.r * 0.74)} transform={`translate(${g.gw.x} ${g.gw.y})`} className="fill-ink" />
                <circle cx={g.gw.x} cy={g.gw.y} r={g.gw.r * 0.32} className="fill-bg-sunken" />
                <circle cx={g.gw.x} cy={g.gw.y - g.gw.r * 0.52} r={g.gw.r * 0.07} className="fill-bg-sunken" />
              </g>
              {/* pawl */}
              <path
                d={`M${g.gw.x + g.gw.r * 0.95} ${g.gw.y - g.gw.r * 1.18} L${g.gw.x + g.gw.r * 0.46} ${g.gw.y - g.gw.r * 0.7}`}
                stroke="var(--rented)"
                strokeWidth={g.narrow ? 2.6 : 3.2}
                strokeLinecap="round"
              />
              <circle cx={g.gw.x + g.gw.r * 0.95} cy={g.gw.y - g.gw.r * 1.18} r={g.narrow ? 2.6 : 3.2} fill="var(--rented)" />
              <text x={g.gw.x} y={g.gw.y + g.gw.r + 30} textAnchor="middle" className="fill-ink text-[12px] font-semibold">
                Ratchet
              </text>
              {!g.narrow && (
                <text x={g.gw.x} y={g.gw.y + g.gw.r + 45} textAnchor="middle" className="fill-muted font-mono text-[10.5px]">
                  gateway
                </text>
              )}
            </g>

            {/* outputs */}
            {(
              [
                { k: "nano", n: g.nano, label: WHO_TEXT.owned, sub: "adapter you keep", above: true },
                { k: "ultra", n: g.ultra, label: WHO_TEXT.rented, sub: "billed per token", above: false },
              ] as const
            ).map(({ k, n, label: l, sub, above }) => {
              const owned = k === "nano";
              const ly = above ? n.y - n.r - (g.narrow ? 10 : 24) : n.y + n.r + 20;
              return (
                <g key={k}>
                  <circle
                    cx={n.x}
                    cy={n.y}
                    r={n.r}
                    fill={owned ? "var(--owned-soft)" : "var(--rented-soft)"}
                    stroke={owned ? "var(--owned)" : "var(--rented)"}
                    strokeWidth={1.5}
                  />
                  {owned ? (
                    // chip glyph
                    <g stroke="var(--owned-ink)" strokeWidth={1.4} fill="none" strokeLinecap="round">
                      <rect x={n.x - n.r * 0.36} y={n.y - n.r * 0.36} width={n.r * 0.72} height={n.r * 0.72} rx={2} />
                      {[-0.18, 0.18].map((o) => (
                        <g key={o}>
                          <path d={`M${n.x + o * n.r} ${n.y - n.r * 0.36} v${-n.r * 0.16}`} />
                          <path d={`M${n.x + o * n.r} ${n.y + n.r * 0.36} v${n.r * 0.16}`} />
                          <path d={`M${n.x - n.r * 0.36} ${n.y + o * n.r} h${-n.r * 0.16}`} />
                          <path d={`M${n.x + n.r * 0.36} ${n.y + o * n.r} h${n.r * 0.16}`} />
                        </g>
                      ))}
                    </g>
                  ) : (
                    // spark glyph
                    <path
                      d={`M${n.x} ${n.y - n.r * 0.48} Q${n.x + n.r * 0.08} ${n.y - n.r * 0.08} ${n.x + n.r * 0.48} ${n.y} Q${n.x + n.r * 0.08} ${n.y + n.r * 0.08} ${n.x} ${n.y + n.r * 0.48} Q${n.x - n.r * 0.08} ${n.y + n.r * 0.08} ${n.x - n.r * 0.48} ${n.y} Q${n.x - n.r * 0.08} ${n.y - n.r * 0.08} ${n.x} ${n.y - n.r * 0.48} Z`}
                      fill="var(--rented-ink)"
                    />
                  )}
                  <text x={n.x + n.r} y={ly} textAnchor="end" className="fill-ink text-[12px] font-semibold">
                    {l}
                  </text>
                  {!g.narrow && (
                    <text x={n.x + n.r} y={ly + 15} textAnchor="end" className="fill-muted text-[11px]">
                      {sub}
                    </text>
                  )}
                </g>
              );
            })}

            {/* request dots (positioned imperatively) */}
            <g aria-hidden>
              {Array.from({ length: POOL }, (_, i) => (
                <circle
                  key={i}
                  ref={(el) => {
                    dotRefs.current[i] = el;
                  }}
                  r={g.narrow ? 3 : 3.6}
                  style={{ opacity: 0 }}
                />
              ))}
            </g>
          </svg>
        )}
        {!g && <div style={{ height: 288 }} />}
      </div>

      {/* counters */}
      <div className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-end gap-x-5 gap-y-2 border-t border-line bg-surface-2/60 px-4 py-3.5 sm:gap-x-8 sm:px-5 sm:py-4">
        <div>
          <div className="text-[11.5px] text-muted">Night</div>
          <div className="mt-1 flex items-baseline gap-1 font-mono text-[22px] font-medium leading-none tracking-[-0.02em] text-ink tabular sm:text-[26px]">
            <span className="relative inline-flex h-[1.05em] w-[1.25em] overflow-hidden">
              <AnimatePresence initial={false} mode="popLayout">
                <motion.span
                  key={night}
                  initial={{ y: "-90%", opacity: 0 }}
                  animate={{ y: 0, opacity: 1 }}
                  exit={{ y: "90%", opacity: 0 }}
                  transition={{ duration: 0.45, ease: EASE }}
                  className="absolute inset-0"
                >
                  {String(night).padStart(2, "0")}
                </motion.span>
              </AnimatePresence>
            </span>
            <span className="text-[13px] text-muted">/{NIGHTS}</span>
          </div>
          <div className="mt-2 flex gap-[3px]" aria-hidden>
            {SIM_DAYS.map((d) => (
              <span
                key={d.day}
                className={cn("h-2 w-[5px] rounded-[1.5px] transition-colors duration-300", d.day <= night ? "bg-ink" : "bg-line-strong")}
                style={{ clipPath: "polygon(0 100%, 0 0, 100% 100%)" }}
              />
            ))}
          </div>
        </div>
        <div className="min-w-0">
          <div className="flex items-baseline justify-between gap-2 text-[11.5px] text-muted">
            <span className="truncate"><span className="min-[420px]:hidden">By Nano</span><span className="hidden min-[420px]:inline">Answered by Nano</span></span>
            <span className="font-mono text-[13px] font-medium text-ink tabular">{fmtPct(share)}</span>
          </div>
          <div className="mt-2 flex h-2 overflow-hidden rounded-full bg-rented/70" aria-hidden>
            <div className="h-full rounded-full bg-owned transition-[width] duration-700 ease-out" style={{ width: `${share * 100}%` }} />
          </div>
          <div className="mt-1.5 flex justify-between text-[11px] text-muted">
            <span>yours</span>
            <span>rented</span>
          </div>
        </div>
        <div className="text-right">
          <div className="text-[11.5px] text-muted">Cost / 1k requests</div>
          <div className="mt-1 font-mono text-[22px] font-medium leading-none tracking-[-0.02em] text-ink tabular sm:text-[26px]">
            {fmtUsd(day.costPer1k, 3)}
          </div>
          <div className="mt-2 text-[11px] text-muted tabular">all-Ultra {fmtUsd(day.baselinePer1k, 3)}</div>
        </div>
      </div>
    </div>
  );
}
