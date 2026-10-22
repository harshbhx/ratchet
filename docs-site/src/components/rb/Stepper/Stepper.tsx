/*
  Adapted from React Bits "Stepper" (TS-CSS), https://reactbits.dev/components/stepper
  Copyright (c) 2026 David Haz. MIT + Commons Clause License Condition v1.0: see ../LICENSE.md.

  Changes for the Ratchet docs: steps come as data ({ title, html }) so an MDX page can pass them as props
  through an Astro wrapper; numbered indicators only; inline SVG icons instead of an icon package; colours from
  theme tokens. Kept from React Bits: the gliding highlight under the current step, the pane that slides in the
  direction of travel and the height that follows the content (Web Animations API, no motion library), Back and
  Continue, clickable indicators, `aria-current="step"`. Every step is also rendered as a plain ordered list,
  shown instead of the stepper in print and without script, so no content is hidden from those readers.
*/
import { useLayoutEffect, useEffect, useRef, useState, type CSSProperties } from 'react';
import './Stepper.css';

export interface StepData {
  title: string;
  /** Trusted HTML made by the Astro wrapper from the page's own text. */
  html: string;
  tone?: 'owned' | 'rented' | 'neutral';
}

export interface StepperProps {
  steps: StepData[];
  initialStep?: number;
  label?: string;
  backLabel?: string;
  nextLabel?: string;
  restartLabel?: string;
  className?: string;
}

const useIsoLayoutEffect = typeof window === 'undefined' ? useEffect : useLayoutEffect;
const SPRING = 'cubic-bezier(0.34, 1.3, 0.64, 1)';
const SETTLE = 'cubic-bezier(0.22, 1, 0.36, 1)';

function Check() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" focusable="false">
      <path d="M5 12.5l4.2 4.2L19 7" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
function Arrow({ dir }: { dir: 'left' | 'right' }) {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" focusable="false">
      <path
        d={dir === 'left' ? 'M15 18l-6-6 6-6' : 'M9 18l6-6-6-6'}
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
function Restart() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" focusable="false">
      <path d="M4 12a8 8 0 1 0 2.3-5.6M4 4v4h4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export default function Stepper({
  steps,
  initialStep = 1,
  label = 'Steps',
  backLabel = 'Back',
  nextLabel = 'Next',
  restartLabel = 'Start again',
  className = '',
}: StepperProps) {
  const total = steps.length;
  const [current, setCurrent] = useState(Math.min(Math.max(1, initialStep), Math.max(1, total)));
  const [direction, setDirection] = useState(1);
  const [ready, setReady] = useState(false);
  const trackRef = useRef<HTMLDivElement>(null);
  const gliderRef = useRef<HTMLSpanElement>(null);
  const markerRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const viewportRef = useRef<HTMLDivElement>(null);
  const paneRef = useRef<HTMLDivElement>(null);
  const heightRef = useRef(0);
  const firstRef = useRef(true);

  useEffect(() => setReady(true), []);

  const go = (target: number) => {
    const next = Math.min(Math.max(1, target), total);
    if (next === current) return;
    if (viewportRef.current) heightRef.current = viewportRef.current.offsetHeight;
    setDirection(next > current ? 1 : -1);
    setCurrent(next);
  };

  // Glider under the current indicator.
  useIsoLayoutEffect(() => {
    const track = trackRef.current;
    const glider = gliderRef.current;
    if (!track || !glider) return undefined;
    const place = (animate: boolean) => {
      const segment = markerRefs.current[current - 1];
      if (!segment) return;
      const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      glider.style.transition =
        animate && !reduce ? `transform 480ms ${SPRING}, width 480ms ${SPRING}, opacity 200ms ease` : 'opacity 200ms ease';
      glider.style.transform = `translate3d(${segment.offsetLeft}px, ${segment.offsetTop}px, 0)`;
      glider.style.width = `${segment.offsetWidth}px`;
      glider.style.height = `${segment.offsetHeight}px`;
      glider.style.opacity = '1';
    };
    place(!firstRef.current);
    const observer = new ResizeObserver(() => place(false));
    observer.observe(track);
    return () => observer.disconnect();
  }, [current]);

  // Pane slide and height follow, as in React Bits.
  useIsoLayoutEffect(() => {
    if (firstRef.current) {
      firstRef.current = false;
      return;
    }
    const viewport = viewportRef.current;
    const pane = paneRef.current;
    if (!viewport || !pane || typeof viewport.animate !== 'function') return;
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const from = heightRef.current;
    const to = viewport.offsetHeight;
    if (!reduce && from && Math.abs(from - to) > 0.5) {
      viewport.animate([{ height: `${from}px` }, { height: `${to}px` }], { duration: 420, easing: SETTLE });
    }
    if (!reduce) {
      pane.animate([{ transform: `translateX(${direction * 28}px)` }, { transform: 'none' }], {
        duration: 460,
        easing: SETTLE,
      });
    }
    pane.animate([{ opacity: 0 }, { opacity: 1 }], { duration: reduce ? 120 : 320, easing: 'ease-out' });
  }, [current]);

  const step = steps[current - 1];
  const isLast = current === total;

  return (
    <div className={`rb-stepper ${className}`.trim()} data-ready={ready ? '' : undefined}>
      <div className="rb-stepper__ui" role="group" aria-label={label}>
        <div className="rb-stepper__header">
          <div ref={trackRef} className="rb-stepper__track">
            <span ref={gliderRef} className="rb-stepper__glider" aria-hidden="true" />
            {steps.map((entry, index) => {
              const number = index + 1;
              const status = number < current ? 'complete' : number === current ? 'active' : 'upcoming';
              return (
                <button
                  key={number}
                  ref={(el) => {
                    markerRefs.current[index] = el;
                  }}
                  type="button"
                  className="rb-stepper__segment"
                  data-status={status}
                  data-tone={entry.tone ?? 'neutral'}
                  aria-current={number === current ? 'step' : undefined}
                  aria-label={`Step ${number} of ${total}: ${entry.title}`}
                  onClick={() => go(number)}
                >
                  <span className="rb-stepper__badge" data-status={status}>
                    {status === 'complete' ? <Check /> : number}
                  </span>
                  <span className="rb-stepper__title">{entry.title}</span>
                </button>
              );
            })}
          </div>
        </div>
        <div ref={viewportRef} className="rb-stepper__viewport" aria-live="polite">
          <div key={current} ref={paneRef} className="rb-stepper__pane" data-tone={step?.tone ?? 'neutral'}>
            <p className="rb-stepper__count">
              Step {current} of {total}
            </p>
            <p className="rb-stepper__pane-title">{step?.title}</p>
            <div className="rb-stepper__body" dangerouslySetInnerHTML={{ __html: step?.html ?? '' }} />
          </div>
        </div>
        <div className="rb-stepper__footer">
          <button
            type="button"
            className="rb-stepper__button rb-stepper__button--back"
            data-hidden={current <= 1 ? '' : undefined}
            disabled={current <= 1}
            onClick={() => go(current - 1)}
          >
            <Arrow dir="left" />
            <span>{backLabel}</span>
          </button>
          {isLast ? (
            <button type="button" className="rb-stepper__button rb-stepper__button--next" onClick={() => go(1)}>
              <Restart />
              <span>{restartLabel}</span>
            </button>
          ) : (
            <button type="button" className="rb-stepper__button rb-stepper__button--next" onClick={() => go(current + 1)}>
              <span>{nextLabel}</span>
              <Arrow dir="right" />
            </button>
          )}
        </div>
      </div>
      <ol className="rb-stepper__all" style={{ '--rb-total': total } as CSSProperties}>
        {steps.map((entry, index) => (
          <li key={index} data-tone={entry.tone ?? 'neutral'}>
            <p className="rb-stepper__pane-title">{entry.title}</p>
            <div className="rb-stepper__body" dangerouslySetInnerHTML={{ __html: entry.html }} />
          </li>
        ))}
      </ol>
    </div>
  );
}
