/*
  Adapted from React Bits "ShinyText" (TS-CSS), https://reactbits.dev/text-animations/shiny-text
  Copyright (c) 2026 David Haz. MIT + Commons Clause License Condition v1.0: see ../LICENSE.md.

  Changes for the Ratchet docs: the shine is a CSS gradient on the text, so its colours are theme tokens and
  follow the light/dark switch without re-parsing colours in JavaScript. It sweeps once when the text first
  scrolls into view and again on hover or focus; it never loops (no motion that cannot be stopped). Without
  script, or with reduced motion, the text is plain.
*/
import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import './ShinyText.css';

export interface ShinyTextProps {
  text?: string;
  children?: ReactNode;
  /** Base text colour (any CSS colour or var()). */
  color?: string;
  /** Colour of the moving band. */
  shineColor?: string;
  /** Seconds for one sweep. */
  speed?: number;
  /** Delay before the first sweep, in seconds. */
  delay?: number;
  /** Angle of the band, in degrees. */
  angle?: number;
  /** 'view' sweeps when it first scrolls into view; 'load' sweeps on first paint and needs no hydration. */
  trigger?: 'view' | 'load';
  className?: string;
}

export default function ShinyText({
  text,
  children,
  color = 'var(--rt-ink-2)',
  shineColor = 'var(--rt-ink)',
  speed = 1.6,
  delay = 0.2,
  angle = 115,
  trigger = 'view',
  className = '',
}: ShinyTextProps) {
  const ref = useRef<HTMLSpanElement>(null);
  const [run, setRun] = useState(trigger === 'load' ? 1 : 0);

  useEffect(() => {
    if (trigger === 'load') return undefined;
    const el = ref.current;
    if (!el || typeof IntersectionObserver === 'undefined') return undefined;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting) {
          setRun((n) => (n === 0 ? 1 : n));
          observer.disconnect();
        }
      },
      { threshold: 0.6 },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const replay = () => setRun((n) => n + 1);

  return (
    <span
      ref={ref}
      className={`rb-shiny-text ${className}`.trim()}
      data-run={run > 0 ? (run % 2 ? 'a' : 'b') : undefined}
      onPointerEnter={run > 0 ? replay : undefined}
      onFocus={run > 0 ? replay : undefined}
      style={
        {
          '--rb-shiny-base': color,
          '--rb-shiny-shine': shineColor,
          '--rb-shiny-speed': `${Math.max(0.2, speed)}s`,
          '--rb-shiny-delay': `${run > 1 ? 0 : Math.max(0, delay)}s`,
          '--rb-shiny-angle': `${angle}deg`,
        } as CSSProperties
      }
    >
      {text ?? children}
    </span>
  );
}
