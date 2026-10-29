import type { CSSProperties } from "react";

const COLORS = ["var(--rented)", "var(--owned)", "var(--warn)", "var(--good)"];

/** A light burst of little stars, CSS only (transform and opacity). `burst` is a key: a new number plays it again; 0 draws nothing. */
export function Sparkles({ burst, className = "" }: { burst: number; className?: string }) {
  if (!burst) return null;
  return (
    <span key={burst} aria-hidden className={`pointer-events-none absolute left-1/2 top-1/3 ${className}`}>
      {Array.from({ length: 14 }, (_, i) => {
        const a = (i / 14) * Math.PI * 2;
        const d = 40 + (i % 3) * 16;
        return (
          <span
            key={i}
            className="cp-spark absolute block size-1.5 rounded-sm"
            style={
              {
                background: COLORS[i % COLORS.length],
                "--tx": `${Math.cos(a) * d}px`,
                "--ty": `${Math.sin(a) * d - 12}px`,
                animationDelay: `${(i % 4) * 40}ms`,
                rotate: `${i * 25}deg`,
              } as CSSProperties
            }
          />
        );
      })}
    </span>
  );
}
