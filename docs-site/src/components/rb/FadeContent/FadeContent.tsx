/*
  Adapted from React Bits "FadeContent" (TS-CSS), https://reactbits.dev/animations/fade-content
  Copyright (c) 2026 David Haz. MIT + Commons Clause License Condition v1.0: see ../LICENSE.md.

  Changes for the Ratchet docs: GSAP and ScrollTrigger are replaced by one IntersectionObserver and CSS
  transitions, which keeps the page light. Kept from React Bits: the fade with an optional blur, the duration,
  delay, threshold, initial opacity and a stagger over direct children; added a small rise (from React Bits'
  AnimatedContent). It plays once. The block is rendered visible; after hydration, if it is still below the fold,
  it is hidden before the next paint and revealed when it scrolls in, so content is never lost when a script is
  slow, blocked or off. Reduced motion and print show it as is.
*/
import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import './FadeContent.css';

const useIsoLayoutEffect = typeof window !== 'undefined' ? useLayoutEffect : useEffect;

export interface FadeContentProps {
  children?: ReactNode;
  /** true for 8px, or a number of px. */
  blur?: boolean | number;
  /** Seconds. */
  duration?: number;
  /** Seconds. */
  delay?: number;
  threshold?: number;
  initialOpacity?: number;
  /** Rise in px (0 for a plain fade). */
  distance?: number;
  /** Seconds between direct children; 0 animates the block as one. */
  stagger?: number;
  className?: string;
  as?: 'div' | 'section';
}

export default function FadeContent({
  children,
  blur = false,
  duration = 0.7,
  delay = 0,
  threshold = 0.12,
  initialOpacity = 0,
  distance = 14,
  stagger = 0,
  className = '',
  as: Tag = 'div',
}: FadeContentProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [state, setState] = useState<'shown' | 'from' | 'in'>('shown');

  // Below the fold at hydration: number the children for the stagger and hide them before paint.
  useIsoLayoutEffect(() => {
    const el = ref.current;
    if (!el || typeof IntersectionObserver === 'undefined') return;
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return;
    if (stagger > 0) {
      const items = el.querySelectorAll<HTMLElement>(':scope > astro-slot > *, :scope > *:not(astro-slot)');
      items.forEach((item, i) => item.style.setProperty('--rb-fade-i', String(i)));
    }
    const rect = el.getBoundingClientRect();
    if (rect.top > window.innerHeight || rect.bottom < 0) setState('from');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (state !== 'from') return undefined;
    const el = ref.current;
    if (!el) return undefined;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting) {
          setState('in');
          observer.disconnect();
        }
      },
      { threshold, rootMargin: '0px 0px -6% 0px' },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [state, threshold]);

  const px = blur === true ? 8 : Math.max(0, Number(blur) || 0);
  return (
    <Tag
      ref={ref}
      className={`rb-fade-content ${className}`.trim()}
      data-state={state}
      data-stagger={stagger > 0 ? '' : undefined}
      style={
        {
          '--rb-fade-blur': `${px}px`,
          '--rb-fade-from': String(Math.min(1, Math.max(0, initialOpacity))),
          '--rb-fade-y': `${distance}px`,
          '--rb-fade-dur': `${Math.max(0.05, duration)}s`,
          '--rb-fade-delay': `${Math.max(0, delay)}s`,
          '--rb-fade-stagger': `${Math.max(0, stagger)}s`,
        } as CSSProperties
      }
    >
      {children}
    </Tag>
  );
}
