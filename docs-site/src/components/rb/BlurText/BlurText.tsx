/*
  Adapted from React Bits "BlurText" (TS-CSS), https://reactbits.dev/text-animations/blur-text
  Copyright (c) 2026 David Haz. MIT + Commons Clause License Condition v1.0: see ../LICENSE.md.

  Changes for the Ratchet docs: the reveal is a CSS keyframe animation, so a `trigger="load"` heading is
  rendered on the server already animating and needs no JavaScript at all. A `trigger="view"` heading is
  rendered visible ("shown"); after hydration, if it is still below the fold, it is hidden before the next paint
  and revealed when it scrolls in. So text is never lost when a script is slow, blocked or off, and a heading
  already on screen at hydration does not blink. Words keep normal spaces between them so headings wrap and
  balance like any text. One effect (blur, with a short rise), no loops, no motion dependency. Reduced motion
  shows the final state.
*/
import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type ElementType } from 'react';

const useIsoLayoutEffect = typeof window !== 'undefined' ? useLayoutEffect : useEffect;
import '../shared.css';
import './BlurText.css';

export interface BlurTextProps {
  /** The text. A newline starts a new line (a block), e.g. "Rent intelligence today.\nOwn it tomorrow." */
  text: string;
  as?: ElementType;
  id?: string;
  className?: string;
  /** One class per line, in order (a line without one gets none). */
  lineClassNames?: string[];
  animateBy?: 'words' | 'letters';
  /** 'load' animates on first paint (no hydration needed); 'view' when it scrolls into view. */
  trigger?: 'load' | 'view';
  /** Delay between words (or letters), in ms. */
  delay?: number;
  /** Duration of one word's reveal, in seconds. */
  duration?: number;
  /** Starting blur, in px. */
  blur?: number;
  /** Starting offset, in px; the direction sets its sign. */
  distance?: number;
  direction?: 'top' | 'bottom';
  /** Delay before the first word, in ms. */
  startDelay?: number;
  threshold?: number;
  /** Extra classes for given words, e.g. { Own: 'mark-owned' } (words mode). */
  marks?: Record<string, string>;
}

export default function BlurText({
  text,
  as: Tag = 'p',
  id,
  className = '',
  lineClassNames = [],
  animateBy = 'words',
  trigger = 'view',
  delay = 70,
  duration = 0.7,
  blur = 10,
  distance = 14,
  direction = 'top',
  startDelay = 0,
  threshold = 0.2,
  marks = {},
}: BlurTextProps) {
  const ref = useRef<HTMLElement>(null);
  const [state, setState] = useState<'shown' | 'from' | 'in'>(trigger === 'load' ? 'in' : 'shown');

  // Below the fold at hydration: hide (before paint) so it can be revealed on scroll.
  useIsoLayoutEffect(() => {
    if (trigger !== 'view' || state !== 'shown') return;
    const el = ref.current;
    if (!el || typeof IntersectionObserver === 'undefined') return;
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return;
    const rect = el.getBoundingClientRect();
    if (rect.top > window.innerHeight || rect.bottom < 0) setState('from');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (state !== 'from') return undefined;
    const el = ref.current;
    if (!el) return undefined;
    if (typeof IntersectionObserver === 'undefined') {
      setState('in');
      return undefined;
    }
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting) {
          setState('in');
          observer.disconnect();
        }
      },
      { threshold },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [state, threshold]);

  let rank = 0;
  const lines = text.split('\n');
  const style = {
    '--rb-blur': `${Math.max(0, blur)}px`,
    '--rb-from-y': `${(direction === 'bottom' ? 1 : -1) * distance}px`,
    '--rb-dur': `${Math.max(0.05, duration)}s`,
    '--rb-stagger': `${Math.max(0, delay)}ms`,
    '--rb-start': `${Math.max(0, startDelay)}ms`,
  } as CSSProperties;

  return (
    <Tag ref={ref} id={id} className={`rb-blur-text ${className}`.trim()} data-state={state} style={style}>
      <span className="rb-sr-only">{lines.join(' ')}</span>
      {lines.map((line, l) => {
        const words = line.split(' ').filter(Boolean);
        return (
          <span key={l} className={`rb-blur-text__line ${lineClassNames[l] ?? ''}`.trim()} aria-hidden="true">
            {words.map((word, w) => {
              const space = w < words.length - 1 ? ' ' : '';
              if (animateBy === 'letters') {
                return (
                  <span key={w}>
                    <span className="rb-blur-text__word">
                      {Array.from(word).map((char, c) => (
                        <span key={c} className="rb-blur-text__piece" style={{ '--i': rank++ } as CSSProperties}>
                          {char}
                        </span>
                      ))}
                    </span>
                    {space}
                  </span>
                );
              }
              return (
                <span key={w}>
                  <span
                    className={`rb-blur-text__piece ${marks[word] ?? ''}`.trim()}
                    style={{ '--i': rank++ } as CSSProperties}
                  >
                    {word}
                  </span>
                  {space}
                </span>
              );
            })}
          </span>
        );
      })}
    </Tag>
  );
}
