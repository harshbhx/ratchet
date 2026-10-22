/*
  Adapted from React Bits "SpotlightCard" (TS-CSS), https://reactbits.dev/components/spotlight-card
  Copyright (c) 2026 David Haz. MIT + Commons Clause License Condition v1.0: see ../LICENSE.md.

  Changes for the Ratchet docs: the surface, border, shadow, light colour and strength come from theme tokens in
  the CSS (so light and dark both work without a `theme` prop), the light follows keyboard focus as well as the
  pointer (as in React Bits), and the grain layer is left out. The pointer, focus and press logic is React
  Bits' own. Reduced motion: the light jumps instead of gliding.
*/
import { useEffect, useRef, type CSSProperties, type HTMLAttributes } from 'react';
import './SpotlightCard.css';

export type SpotlightCardProps = HTMLAttributes<HTMLDivElement> & {
  /** CSS colour of the light; defaults to the theme's spotlight token (owned blue). */
  spotlightColor?: string;
  spotlightSize?: number;
  softness?: number;
  proximity?: number;
  smoothing?: number;
  flare?: boolean;
  /** Tone of the light: owned (blue, the student), rented (orange, the teacher) or neutral. */
  tone?: 'owned' | 'rented' | 'neutral';
};

const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value));

const falloff = (color: string, softness: number) => {
  const soft = clamp(softness, 0, 1);
  const core = (1 - soft) ** 1.5 * 0.85;
  const stop = (alpha: number, at: number) =>
    `color-mix(in srgb, ${color} ${(alpha * 100).toFixed(2)}%, transparent) ${(at * 100).toFixed(2)}%`;
  const list = [stop(1, 0)];
  for (let i = 0; i <= 10; i++) {
    const t = core + ((1 - core) * i) / 10;
    const g = (t - core) / (1 - core);
    const smooth = 1 - g * g * (3 - 2 * g);
    const glow = Math.exp(-4.6 * g * g) * (1 - g ** 6);
    list.push(stop(smooth + (glow - smooth) * soft, t));
  }
  return list.join(', ');
};

export default function SpotlightCard({
  children,
  className = '',
  style,
  spotlightColor = 'var(--rb-spot)',
  spotlightSize = 320,
  softness = 0.75,
  proximity = 64,
  smoothing = 0.3,
  flare = true,
  tone = 'owned',
  ...rest
}: SpotlightCardProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const lightRef = useRef<HTMLSpanElement>(null);
  const edgeRef = useRef<HTMLSpanElement>(null);
  const settings = {
    fill: falloff(spotlightColor, softness),
    edge: falloff(spotlightColor, 1),
    size: Math.max(20, spotlightSize),
    proximity: Math.max(0, proximity),
    smoothing: clamp(smoothing, 0, 1),
    flare,
  };
  const settingsRef = useRef(settings);
  settingsRef.current = settings;

  useEffect(() => {
    const root = rootRef.current;
    const light = lightRef.current;
    const edge = edgeRef.current;
    if (!root || !light || !edge) return undefined;

    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
    const state = { x: 0, y: 0, presence: 0, flare: 0, pressed: false };
    const pointer = { x: 0, y: 0, active: false };
    const drawn = { light: '', edge: '' };
    let focus: Element | null = null;
    let raf = 0;
    let last = 0;
    let alive = true;

    const paint = () => {
      const s = settingsRef.current;
      const size = s.size * (1 + 0.2 * state.flare);
      const fill = `radial-gradient(circle ${size.toFixed(1)}px at ${state.x.toFixed(1)}px ${state.y.toFixed(1)}px, ${s.fill})`;
      const rim = `radial-gradient(circle ${(size * 0.6).toFixed(1)}px at ${state.x.toFixed(1)}px ${state.y.toFixed(1)}px, ${s.edge})`;
      if (fill !== drawn.light) {
        drawn.light = fill;
        light.style.background = fill;
      }
      if (rim !== drawn.edge) {
        drawn.edge = rim;
        edge.style.background = rim;
      }
      const boost = 1 + 0.6 * state.flare;
      root.style.setProperty('--rb-spot-presence', String(Math.min(1, state.presence * boost)));
    };

    const tick = (now: number) => {
      raf = 0;
      if (!alive) return;
      const s = settingsRef.current;
      const dt = Math.min(0.05, Math.max(0.001, (now - last) / 1000));
      last = now;
      const rect = root.getBoundingClientRect();
      let tx = state.x;
      let ty = state.y;
      let target = 0;
      if (focus && root.contains(focus)) {
        const box = focus.getBoundingClientRect();
        tx = box.left + box.width / 2 - rect.left;
        ty = box.top + box.height / 2 - rect.top;
        target = 1;
      } else if (pointer.active) {
        tx = pointer.x - rect.left;
        ty = pointer.y - rect.top;
        const gap = Math.hypot(Math.max(0, -tx, tx - rect.width), Math.max(0, -ty, ty - rect.height));
        if (gap === 0) target = 1;
        else if (s.proximity > 0) target = Math.max(0, 1 - gap / s.proximity) ** 2;
      }
      if (state.presence < 0.02 && target > 0) {
        state.x = tx;
        state.y = ty;
      }
      const follow = reduce || s.smoothing === 0 ? 1 : 1 - Math.exp(-dt / (0.015 + s.smoothing * 0.22));
      state.x += (tx - state.x) * follow;
      state.y += (ty - state.y) * follow;
      const fade = reduce ? 1 : 1 - Math.exp(-dt / (target > state.presence ? 0.1 : 0.32));
      state.presence += (target - state.presence) * fade;
      if (Math.abs(target - state.presence) < 0.002) state.presence = target;
      const press = state.pressed && s.flare && !reduce ? 1 : 0;
      state.flare += (press - state.flare) * (1 - Math.exp(-dt / (press ? 0.05 : 0.28)));
      if (Math.abs(press - state.flare) < 0.002) state.flare = press;
      paint();
      const moving = Math.abs(tx - state.x) > 0.1 || Math.abs(ty - state.y) > 0.1;
      const settled = !moving && state.presence === target && state.flare === press;
      if (!settled) raf = requestAnimationFrame(tick);
    };

    const wake = () => {
      if (raf || !alive) return;
      last = performance.now();
      raf = requestAnimationFrame(tick);
    };

    const onMove = (event: PointerEvent) => {
      pointer.x = event.clientX;
      pointer.y = event.clientY;
      pointer.active = true;
      if (state.presence > 0 || root.contains(event.target as Node | null)) wake();
      else {
        const rect = root.getBoundingClientRect();
        const near =
          event.clientX > rect.left - settingsRef.current.proximity &&
          event.clientX < rect.right + settingsRef.current.proximity &&
          event.clientY > rect.top - settingsRef.current.proximity &&
          event.clientY < rect.bottom + settingsRef.current.proximity;
        if (near) wake();
      }
    };
    const onOut = (event: PointerEvent) => {
      if (event.relatedTarget) return;
      pointer.active = false;
      wake();
    };
    const onDown = (event: PointerEvent) => {
      pointer.x = event.clientX;
      pointer.y = event.clientY;
      pointer.active = true;
      state.pressed = true;
      wake();
    };
    const onUp = (event: PointerEvent) => {
      if (event.pointerType !== 'mouse') pointer.active = false;
      if (!state.pressed && event.pointerType === 'mouse') return;
      state.pressed = false;
      wake();
    };
    const onFocusIn = (event: FocusEvent) => {
      if (!(event.target instanceof Element) || !event.target.matches(':focus-visible')) return;
      focus = event.target;
      wake();
    };
    const onFocusOut = (event: FocusEvent) => {
      if (root.contains(event.relatedTarget as Node | null)) return;
      focus = null;
      wake();
    };

    window.addEventListener('pointermove', onMove, { passive: true });
    window.addEventListener('pointerup', onUp);
    window.addEventListener('pointercancel', onUp);
    document.addEventListener('pointerout', onOut);
    root.addEventListener('pointerdown', onDown);
    root.addEventListener('focusin', onFocusIn);
    root.addEventListener('focusout', onFocusOut);

    return () => {
      alive = false;
      cancelAnimationFrame(raf);
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      window.removeEventListener('pointercancel', onUp);
      document.removeEventListener('pointerout', onOut);
      root.removeEventListener('pointerdown', onDown);
      root.removeEventListener('focusin', onFocusIn);
      root.removeEventListener('focusout', onFocusOut);
    };
  }, []);

  return (
    <div
      ref={rootRef}
      className={`rb-spotlight-card ${className}`.trim()}
      data-tone={tone}
      style={style as CSSProperties}
      {...rest}
    >
      <span ref={lightRef} className="rb-spotlight-card__light" aria-hidden="true" />
      {children}
      <span className="rb-spotlight-card__border" aria-hidden="true" />
      <span ref={edgeRef} className="rb-spotlight-card__edge" aria-hidden="true" />
    </div>
  );
}
