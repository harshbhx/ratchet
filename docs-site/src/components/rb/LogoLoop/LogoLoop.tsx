/*
  Adapted from React Bits "LogoLoop" (TS-CSS), https://reactbits.dev/animations/logo-loop
  Copyright (c) 2026 David Haz. MIT + Commons Clause License Condition v1.0: see ../LICENSE.md.

  Changes for the Ratchet docs: text items only (we draw no company logos), a CSS animation instead of the
  requestAnimationFrame engine, and a visible pause button (WCAG 2.2.2), on top of React Bits' pause on hover and
  focus and its faded edges. The second copy of the list is hidden from screen readers. With reduced motion,
  without script and in print it is a static, wrapping row.
*/
import { useEffect, useRef, useState, type CSSProperties } from 'react';
import '../shared.css';
import './LogoLoop.css';

export interface LoopItem {
  label: string;
  href?: string;
  /** Small muted text after the label, e.g. the role. */
  note?: string;
}

export interface LogoLoopProps {
  items: LoopItem[];
  /** Seconds for one full loop. */
  duration?: number;
  /** Gap between items, in px. */
  gap?: number;
  ariaLabel?: string;
  className?: string;
}

export default function LogoLoop({ items, duration = 48, gap = 40, ariaLabel = 'Built with', className = '' }: LogoLoopProps) {
  const [paused, setPaused] = useState(false);
  const [ready, setReady] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const [offscreen, setOffscreen] = useState(false);

  useEffect(() => {
    setReady(true);
    const el = rootRef.current;
    if (!el || typeof IntersectionObserver === 'undefined') return undefined;
    const observer = new IntersectionObserver(([entry]) => setOffscreen(!entry?.isIntersecting));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const list = (copy: number) => (
    <ul className="rb-logoloop__list" aria-hidden={copy > 0 ? true : undefined}>
      {items.map((item, i) => (
        <li key={`${copy}-${i}`} className="rb-logoloop__item">
          {item.href ? (
            <a className="rb-logoloop__content" href={item.href} tabIndex={copy > 0 ? -1 : undefined} target="_blank" rel="noopener noreferrer">
              <span className="rb-logoloop__label">{item.label}</span>
              {item.note && <span className="rb-logoloop__note">{item.note}</span>}
            </a>
          ) : (
            <span className="rb-logoloop__content">
              <span className="rb-logoloop__label">{item.label}</span>
              {item.note && <span className="rb-logoloop__note">{item.note}</span>}
            </span>
          )}
        </li>
      ))}
    </ul>
  );

  return (
    <div
      ref={rootRef}
      className={`rb-logoloop ${className}`.trim()}
      role="region"
      aria-label={ariaLabel}
      data-ready={ready ? '' : undefined}
      data-paused={paused || offscreen ? '' : undefined}
      style={{ '--rb-loop-duration': `${Math.max(8, duration)}s`, '--rb-loop-gap': `${gap}px` } as CSSProperties}
    >
      <div className="rb-logoloop__viewport">
        <div className="rb-logoloop__track">
          {list(0)}
          {list(1)}
        </div>
      </div>
      <button
        type="button"
        className="rb-logoloop__toggle"
        aria-pressed={paused}
        onClick={() => setPaused((p) => !p)}
        title={paused ? 'Play the scrolling list' : 'Pause the scrolling list'}
      >
        {paused ? (
          <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" focusable="false">
            <path d="M8 5.5v13l10-6.5-10-6.5z" fill="currentColor" />
          </svg>
        ) : (
          <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" focusable="false">
            <path d="M8 5h3v14H8zM13 5h3v14h-3z" fill="currentColor" />
          </svg>
        )}
        <span className="rb-sr-only">{paused ? 'Play the scrolling list' : 'Pause the scrolling list'}</span>
      </button>
    </div>
  );
}
