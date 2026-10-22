/*
  Adapted from React Bits "GradientText" (TS-CSS), https://reactbits.dev/text-animations/gradient-text
  Copyright (c) 2026 David Haz. MIT + Commons Clause License Condition v1.0: see ../LICENSE.md.

  Changes for the Ratchet docs: a linear gradient only, drawn by CSS from theme tokens so it follows the
  light/dark switch. Instead of an endless loop it glides once across the text when it first scrolls into view,
  then rests (no motion that cannot be stopped). Without script, or with reduced motion, it shows the resting
  gradient. The optional border is kept.
*/
import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import './GradientText.css';

export interface GradientTextProps {
  text?: string;
  children?: ReactNode;
  /** Two or more CSS colours (var() works). */
  colors?: string[];
  angle?: number;
  /** Seconds for the one glide. */
  duration?: number;
  showBorder?: boolean;
  className?: string;
}

export default function GradientText({
  text,
  children,
  colors = ['var(--rt-owned-ink)', 'var(--rt-owned)', 'var(--rt-ink)'],
  angle = 90,
  duration = 2.2,
  showBorder = false,
  className = '',
}: GradientTextProps) {
  const ref = useRef<HTMLSpanElement>(null);
  const [run, setRun] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el || typeof IntersectionObserver === 'undefined') return undefined;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting) {
          setRun(true);
          observer.disconnect();
        }
      },
      { threshold: 0.5 },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const list = colors.length > 1 ? colors : [colors[0] ?? 'currentColor', colors[0] ?? 'currentColor'];
  const loop = [...list, list[0]];
  return (
    <span
      ref={ref}
      className={`rb-gradient-text${showBorder ? ' rb-gradient-text--border' : ''} ${className}`.trim()}
      data-run={run ? '' : undefined}
      style={
        {
          '--rb-gt-gradient': `linear-gradient(${angle}deg, ${loop.join(', ')})`,
          '--rb-gt-duration': `${Math.max(0.3, duration)}s`,
        } as CSSProperties
      }
    >
      <span className="rb-gradient-text__content">{text ?? children}</span>
    </span>
  );
}
