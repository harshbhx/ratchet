import { useMemo, useRef } from "react";
import { animate, useMotionValue, type MotionValue } from "motion/react";
import { SLIDE, SPRING, landSquash } from "./physics";

/**
 * One character's body in motion: position, squash and stretch, and a spin, as motion values (transforms only). The moves follow
 * the usual rules of animation: a crouch before a jump (anticipation), a stretch in the air, a squash on landing and a small
 * settle after it (follow-through).
 */
export interface Actor {
  x: MotionValue<number>;
  y: MotionValue<number>;
  sx: MotionValue<number>;
  sy: MotionValue<number>;
  rot: MotionValue<number>;
  stop(): void;
  squash(speed: number): Promise<void>;
  hop(height?: number): Promise<void>;
  slide(dx: number): Promise<void>;
  home(): Promise<void>;
  shake(): Promise<void>;
  spin(): Promise<void>;
  nod(): Promise<void>;
}

type Thenable = { then(onResolve: () => void, onReject?: () => void): unknown };

export function useActor(): Actor {
  const x = useMotionValue(0);
  const y = useMotionValue(0);
  const sx = useMotionValue(1);
  const sy = useMotionValue(1);
  const rot = useMotionValue(0);
  const live = useRef<Array<{ stop(): void }>>([]);
  return useMemo<Actor>(() => {
    const track = <T extends { stop(): void }>(c: T): T => {
      live.current.push(c);
      return c;
    };
    const done = async (...c: Thenable[]) => {
      await Promise.all(c.map((t) => new Promise<void>((res) => t.then(() => res(), () => res()))));
    };
    const squash = async (speed: number) => {
      const s = landSquash(speed);
      await done(
        track(animate(sx, [s.sx, 0.97, 1], { duration: 0.42, ease: "easeOut" })),
        track(animate(sy, [s.sy, 1.05, 1], { duration: 0.42, ease: "easeOut" })),
      );
    };
    const a: Actor = {
      x,
      y,
      sx,
      sy,
      rot,
      stop() {
        live.current.forEach((c) => c.stop());
        live.current = [];
        sx.set(1);
        sy.set(1);
        rot.set(0);
      },
      squash,
      async hop(height = 38) {
        await done(track(animate(sy, 0.82, { duration: 0.13, ease: "easeOut" })), track(animate(sx, 1.12, { duration: 0.13, ease: "easeOut" })));
        await done(
          track(animate(y, -height, { duration: 0.28, ease: "easeOut" })),
          track(animate(sy, 1.14, { duration: 0.22 })),
          track(animate(sx, 0.92, { duration: 0.22 })),
        );
        await done(track(animate(y, 0, { duration: 0.24, ease: "easeIn" })), track(animate(sy, 1, { duration: 0.2 })), track(animate(sx, 1, { duration: 0.2 })));
        await squash(1000);
      },
      async slide(dx) {
        await done(track(animate(x, dx, { type: "spring", ...SLIDE })));
      },
      async home() {
        await done(track(animate(x, 0, { type: "spring", ...SLIDE })), track(animate(y, 0, { type: "spring", ...SPRING })));
      },
      async shake() {
        await done(track(animate(x, [x.get(), x.get() - 4, x.get() + 4, x.get() - 3, x.get() + 3, x.get()], { duration: 0.4 })));
      },
      async spin() {
        await done(track(animate(rot, [0, 360], { duration: 0.75, ease: "easeInOut" })));
        rot.set(0);
      },
      async nod() {
        await done(track(animate(y, [0, 4, 0, 4, 0], { duration: 0.6, ease: "easeInOut" })));
      },
    };
    return a;
  }, [x, y, sx, sy, rot]);
}
