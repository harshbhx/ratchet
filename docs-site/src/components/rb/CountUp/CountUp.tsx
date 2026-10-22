/*
  Adapted from React Bits "CountUp" (TS-CSS), https://reactbits.dev/text-animations/count-up
  Copyright (c) 2026 David Haz. MIT + Commons Clause License Condition v1.0: see ../LICENSE.md.

  Changes for the Ratchet docs: the server renders the final number, so the page is right before hydration and
  without script; a hidden copy of the final text holds the width, so counting never shifts the layout. After
  hydration the number resets to `from` while it is off screen and counts once when it scrolls into view, with
  React Bits' easeOut curve, on requestAnimationFrame (no motion dependency). Screen readers get the final text
  only. Reduced motion keeps the final number. Count mode only (the roll mode is left out).
*/
import { useEffect, useLayoutEffect, useRef, type CSSProperties } from 'react';
import '../shared.css';

export interface CountUpProps {
  to: number;
  from?: number;
  /** Seconds. */
  duration?: number;
  /** Seconds before counting starts once visible. */
  delay?: number;
  /** Thousands separator, e.g. ",". */
  separator?: string;
  decimals?: number;
  prefix?: string;
  suffix?: string;
  className?: string;
  style?: CSSProperties;
}

const useIsoLayoutEffect = typeof window !== 'undefined' ? useLayoutEffect : useEffect;

/* React Bits' default easing, cubic-bezier(0.33, 1, 0.68, 1), is the ease-out cubic curve. */
const easeOut = (t: number) => 1 - Math.pow(1 - t, 3);

const places = (n: number) => {
  const s = String(n);
  return s.includes('.') ? s.split('.')[1].length : 0;
};

export default function CountUp({
  to,
  from = 0,
  duration = 1.6,
  delay = 0,
  separator = ',',
  decimals,
  prefix = '',
  suffix = '',
  className = '',
  style,
}: CountUpProps) {
  const rootRef = useRef<HTMLSpanElement>(null);
  const liveRef = useRef<HTMLSpanElement>(null);
  const digits = decimals ?? Math.max(places(from), places(to));

  const format = (value: number) => {
    const fixed = Math.abs(value).toFixed(digits);
    const [int, frac] = fixed.split('.');
    const grouped = separator ? int.replace(/\B(?=(\d{3})+(?!\d))/g, separator) : int;
    const sign = value < 0 && Number(fixed) !== 0 ? '-' : '';
    return `${prefix}${sign}${grouped}${frac ? `.${frac}` : ''}${suffix}`;
  };
  const finalText = format(to);

  useIsoLayoutEffect(() => {
    const root = rootRef.current;
    const live = liveRef.current;
    if (!root || !live) return undefined;
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return undefined;
    if (typeof IntersectionObserver === 'undefined') return undefined;

    let raf = 0;
    let timer = 0;
    const run = () => {
      const start = performance.now();
      const total = Math.max(0.05, duration) * 1000;
      const tick = (now: number) => {
        const t = Math.min(1, (now - start) / total);
        live.textContent = format(from + (to - from) * easeOut(t));
        if (t < 1) raf = requestAnimationFrame(tick);
        else live.textContent = finalText;
      };
      raf = requestAnimationFrame(tick);
    };

    const rect = root.getBoundingClientRect();
    const onScreen = rect.top < window.innerHeight && rect.bottom > 0;
    // Already on screen at hydration: count from `from` right away, without waiting for the observer.
    live.textContent = format(from);
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (!entry?.isIntersecting) return;
        observer.disconnect();
        timer = window.setTimeout(run, Math.max(0, delay) * 1000);
      },
      { threshold: onScreen ? 0 : 0.4 },
    );
    observer.observe(root);
    return () => {
      observer.disconnect();
      cancelAnimationFrame(raf);
      window.clearTimeout(timer);
      live.textContent = finalText;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [to, from, duration, delay, finalText]);

  return (
    <span ref={rootRef} className={`rb-count-up ${className}`.trim()} style={{ display: 'inline-grid', ...style }}>
      <span aria-hidden="true" style={{ gridArea: '1 / 1', visibility: 'hidden', fontVariantNumeric: 'tabular-nums' }}>
        {finalText}
      </span>
      <span
        ref={liveRef}
        aria-hidden="true"
        style={{ gridArea: '1 / 1', fontVariantNumeric: 'tabular-nums', textAlign: 'inherit' }}
      >
        {finalText}
      </span>
      <span className="rb-sr-only">{finalText}</span>
    </span>
  );
}
