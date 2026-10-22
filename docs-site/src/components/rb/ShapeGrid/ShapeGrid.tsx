/*
  Adapted from React Bits "ShapeGrid" (formerly "Squares", TS-CSS), https://reactbits.dev/backgrounds/shape-grid
  Copyright (c) 2026 David Haz. MIT + Commons Clause License Condition v1.0: see ../LICENSE.md.

  Changes for the Ratchet docs: square cells only, no drift (the grid stands still unless the reader moves the
  pointer, so nothing moves on its own beyond a 1.4 s intro ripple). The static grid lines are drawn by CSS
  behind the canvas (that is also the fallback); the canvas only paints the lit cells: React Bits' pointer
  trail with its decay and radius, and its intro wave. Colours are read from theme tokens and re-read when the
  theme changes. The canvas is transparent: it never paints a background. It stops when off screen or idle.
*/
import { useEffect, useRef } from 'react';
import './ShapeGrid.css';

export interface ShapeGridProps {
  /** Cell size in px; match the CSS grid behind it. */
  cellSize?: number;
  /** Seconds a lit cell takes to fade. */
  trail?: number;
  /** Radius of the lit area, in cells. */
  radius?: number;
  /** Fill opacity of a lit cell. */
  fillOpacity?: number;
  /** Play a one-time ripple from this point (fractions of the box), or false. */
  intro?: false | { x: number; y: number };
  className?: string;
}

const clamp = (v: number, a: number, b: number) => Math.min(Math.max(v, a), b);
const smoothstep = (e0: number, e1: number, v: number) => {
  const t = clamp((v - e0) / (e1 - e0), 0, 1);
  return t * t * (3 - 2 * t);
};
const key = (i: number, j: number) => `${i},${j}`;

export default function ShapeGrid({
  cellSize = 44,
  trail = 0.9,
  radius = 1.6,
  fillOpacity = 0.16,
  intro = { x: 0.72, y: 0.42 },
  className = '',
}: ShapeGridProps) {
  const boxRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const box = boxRef.current;
    const canvas = canvasRef.current;
    if (!box || !canvas) return undefined;
    const ctx = canvas.getContext('2d');
    if (!ctx) return undefined;
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
    if (reduce) return undefined;

    const colors = { line: 'rgba(0,0,0,0)', hover: '#3987e5', fill: '#3987e5' };
    const readColors = () => {
      const cs = getComputedStyle(box);
      colors.hover = cs.getPropertyValue('--rb-grid-hover').trim() || colors.hover;
      colors.fill = cs.getPropertyValue('--rb-grid-fill').trim() || colors.hover;
    };

    const state = {
      alive: true,
      raf: 0,
      last: 0,
      time: 0,
      width: 1,
      height: 1,
      ratio: 1,
      inside: false,
      px: 0,
      py: 0,
      sx: 0,
      sy: 0,
      vx: 0,
      vy: 0,
      power: 0,
      visible: true,
      introStart: intro ? 0 : -1,
      lit: new Map<string, number>(),
    };

    const resize = () => {
      const w = Math.max(1, box.clientWidth);
      const h = Math.max(1, box.clientHeight);
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      state.width = w;
      state.height = h;
      state.ratio = ratio;
      const pw = Math.round(w * ratio);
      const ph = Math.round(h * ratio);
      if (canvas.width !== pw || canvas.height !== ph) {
        canvas.width = pw;
        canvas.height = ph;
        canvas.style.width = `${w}px`;
        canvas.style.height = `${h}px`;
      }
    };

    const illuminate = (dt: number) => {
      const decay = Math.exp(-dt / Math.max(0.05, trail));
      for (const [k, v] of state.lit) {
        const next = v * decay;
        if (next < 0.004) state.lit.delete(k);
        else state.lit.set(k, next);
      }
      if (state.power < 0.01) return;
      const reach = Math.max(radius, 0.5) * cellSize;
      const hi = Math.floor(state.sx / cellSize);
      const hj = Math.floor(state.sy / cellSize);
      const span = Math.ceil(radius) + 1;
      for (let i = hi - span; i <= hi + span; i++) {
        for (let j = hj - span; j <= hj + span; j++) {
          const cx = (i + 0.5) * cellSize;
          const cy = (j + 0.5) * cellSize;
          let value = smoothstep(reach, reach * 0.15, Math.hypot(cx - state.sx, cy - state.sy));
          if (i === hi && j === hj) value = Math.max(value, 1);
          value *= state.power;
          if (value <= 0.004) continue;
          const k = key(i, j);
          if (value > (state.lit.get(k) ?? 0)) state.lit.set(k, value);
        }
      }
    };

    const draw = () => {
      const { ratio, width, height } = state;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      const cells = new Map(state.lit);
      if (state.introStart >= 0 && intro) {
        const t = state.time - state.introStart;
        const ox = intro.x * width;
        const oy = intro.y * height;
        const front = t * Math.hypot(width, height) * 0.6;
        const cols = Math.ceil(width / cellSize);
        const rows = Math.ceil(height / cellSize);
        for (let i = 0; i < cols; i++) {
          for (let j = 0; j < rows; j++) {
            const d = Math.hypot((i + 0.5) * cellSize - ox, (j + 0.5) * cellSize - oy);
            const wave = Math.exp(-(((front - d) / (cellSize * 1.6)) ** 2)) * 0.55 * Math.max(0, 1 - t / 1.4);
            if (wave > 0.01) {
              const k = key(i, j);
              cells.set(k, Math.max(cells.get(k) ?? 0, wave));
            }
          }
        }
        if (t > 1.4) state.introStart = -1;
      }
      for (const [k, value] of cells) {
        const [i, j] = k.split(',').map(Number);
        const x = i * cellSize;
        const y = j * cellSize;
        ctx.globalAlpha = Math.min(1, value) * fillOpacity;
        ctx.fillStyle = colors.fill;
        ctx.fillRect(x + 0.5, y + 0.5, cellSize - 1, cellSize - 1);
        ctx.globalAlpha = Math.min(1, value) * 0.7;
        ctx.strokeStyle = colors.hover;
        ctx.lineWidth = 1;
        ctx.strokeRect(x + 0.5, y + 0.5, cellSize - 1, cellSize - 1);
      }
      ctx.globalAlpha = 1;
    };

    const frame = (now: number) => {
      state.raf = 0;
      if (!state.alive) return;
      const dt = state.last ? Math.min(0.05, (now - state.last) / 1000) : 1 / 60;
      state.last = now;
      state.time += dt;
      const want = state.inside ? 1 : 0;
      state.power += (want - state.power) * (1 - Math.exp(-(want ? 10 : 5) * dt));
      const k = 220;
      const damp = 2 * Math.sqrt(k) * 0.85;
      state.vx += (k * (state.px - state.sx) - damp * state.vx) * dt;
      state.vy += (k * (state.py - state.sy) - damp * state.vy) * dt;
      state.sx += state.vx * dt;
      state.sy += state.vy * dt;
      illuminate(dt);
      draw();
      const idle = state.lit.size === 0 && state.power < 0.005 && state.introStart < 0;
      if (!idle && state.visible) state.raf = requestAnimationFrame(frame);
      else state.last = 0;
    };
    const wake = () => {
      if (!state.raf && state.alive && state.visible) state.raf = requestAnimationFrame(frame);
    };

    const onMove = (event: PointerEvent) => {
      if (event.pointerType !== 'mouse') return;
      const rect = box.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
      const inside = x >= 0 && y >= 0 && x <= rect.width && y <= rect.height;
      if (inside && !state.inside && state.power < 0.02) {
        state.sx = x;
        state.sy = y;
        state.vx = 0;
        state.vy = 0;
      }
      state.inside = inside;
      if (inside) {
        state.px = x;
        state.py = y;
        wake();
      }
    };
    const onLeave = () => {
      state.inside = false;
      wake();
    };

    readColors();
    resize();
    const resizeObserver = new ResizeObserver(() => {
      resize();
      draw();
    });
    resizeObserver.observe(box);
    const visibility = new IntersectionObserver((entries) => {
      state.visible = entries.some((e) => e.isIntersecting);
      if (state.visible) wake();
    });
    visibility.observe(box);
    const themeObserver = new MutationObserver(() => {
      readColors();
      draw();
    });
    themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    window.addEventListener('pointermove', onMove, { passive: true });
    document.documentElement.addEventListener('pointerleave', onLeave);
    window.addEventListener('blur', onLeave);
    box.setAttribute('data-live', '');
    wake();

    return () => {
      state.alive = false;
      cancelAnimationFrame(state.raf);
      resizeObserver.disconnect();
      visibility.disconnect();
      themeObserver.disconnect();
      window.removeEventListener('pointermove', onMove);
      document.documentElement.removeEventListener('pointerleave', onLeave);
      window.removeEventListener('blur', onLeave);
    };
  }, [cellSize, trail, radius, fillOpacity, intro]);

  return (
    <div ref={boxRef} className={`rb-shape-grid ${className}`.trim()} aria-hidden="true">
      <canvas ref={canvasRef} className="rb-shape-grid__canvas" />
    </div>
  );
}
