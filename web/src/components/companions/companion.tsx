import { useRef, useState } from "react";
import { animate, motion } from "motion/react";
import type { Actor } from "./actor";
import { Character } from "./character";
import type { Look, Who } from "./companion-model";
import { SPRING, SLIDE, airStretch, dragBounds, type Bounds } from "./physics";

const NAMES: Record<Who, string> = { nano: "Nano, the student", ultra: "Ultra, the teacher" };

/**
 * One character as a focusable button: Enter or a click pokes, a double click jumps. With `draggable` (not under reduced motion) it
 * can be picked up and thrown: it falls back to its place on a spring and lands with a squash.
 */
export function Companion({
  who,
  look,
  size,
  actor,
  live,
  draggable,
  onPoke,
  onJump,
  onGrab,
  onThrow,
}: {
  who: Who;
  look: Look;
  size: number;
  actor: Actor;
  live: boolean;
  draggable: boolean;
  onPoke: () => void;
  onJump: () => void;
  onGrab?: () => void;
  onThrow?: () => void;
}) {
  const wrap = useRef<HTMLDivElement | null>(null);
  const moved = useRef(false);
  const [bounds, setBounds] = useState<Bounds | undefined>(undefined);
  const [grabbed, setGrabbed] = useState(false);

  const measure = () => {
    const el = wrap.current;
    if (!el) return;
    // Measured at rest: remove the current offset so the bounds are relative to the place on the shelf.
    const r = el.getBoundingClientRect();
    const rest = { left: r.left - actor.x.get(), right: r.right - actor.x.get(), top: r.top - actor.y.get(), bottom: r.bottom - actor.y.get() };
    setBounds(dragBounds(rest, window.innerWidth, window.innerHeight));
  };

  return (
    <motion.div
      ref={wrap}
      drag={draggable}
      dragMomentum={false}
      dragElastic={0.1}
      dragConstraints={bounds}
      onPointerDownCapture={draggable ? measure : undefined}
      onDragStart={() => {
        moved.current = true;
        actor.stop();
        setGrabbed(true);
        onGrab?.();
      }}
      onDragEnd={(_, info) => {
        setGrabbed(false);
        const v = info.velocity;
        const speed = Math.hypot(v.x, v.y);
        window.setTimeout(() => (moved.current = false), 80);
        // Thrown: it flies on with the release speed and springs back to the shelf, bouncing; the first touch of the shelf squashes.
        let prev = actor.y.get();
        let landed = prev >= 0;
        const off = actor.y.on("change", (val) => {
          if (!landed && prev < 0 && val >= 0) {
            landed = true;
            void actor.squash(Math.abs(actor.y.getVelocity()) + 300);
          }
          prev = val;
        });
        const sx = animate(actor.x, 0, { type: "spring", ...SLIDE, velocity: v.x });
        const sy = animate(actor.y, 0, { type: "spring", ...SPRING, velocity: v.y });
        void Promise.allSettled([sx, sy]).then(() => off());
        const st = airStretch(speed);
        actor.sx.set(st.sx);
        actor.sy.set(st.sy);
        window.setTimeout(() => {
          animate(actor.sx, 1, { duration: 0.2 });
          animate(actor.sy, 1, { duration: 0.2 });
        }, 120);
        onThrow?.();
      }}
      style={{ x: actor.x, y: actor.y, touchAction: draggable ? "none" : undefined }}
      className="pointer-events-auto relative"
    >
      <motion.button
        type="button"
        aria-label={`${NAMES[who]}. Press Enter to poke.`}
        onClick={() => {
          if (moved.current) return;
          onPoke();
        }}
        onDoubleClick={() => {
          if (!moved.current) onJump();
        }}
        style={{ scaleX: actor.sx, scaleY: actor.sy, rotate: actor.rot, originX: 0.5, originY: 1 }}
        className={`block select-none rounded-2xl ${draggable ? (grabbed ? "cursor-grabbing" : "cursor-grab") : "cursor-pointer"}`}
      >
        <Character who={who} look={look} size={size} live={live} />
      </motion.button>
    </motion.div>
  );
}
