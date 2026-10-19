import { cn } from "@/lib/utils";

/** Ratchet mark: an asymmetric-toothed wheel with a pawl. Original artwork. */
export function LogoMark({ className }: { className?: string }) {
  const teeth = Array.from({ length: 10 }, (_, i) => i * 36);
  return (
    <svg viewBox="0 0 40 40" className={cn("size-7", className)} aria-hidden>
      <rect width="40" height="40" rx="10" className="fill-ink" />
      <g transform="translate(20 21)">
        {teeth.map((a) => (
          <path key={a} d="M0 -12.5 L3.4 -9.2 L0 -8.6 Z" transform={`rotate(${a})`} className="fill-owned" />
        ))}
        <circle r="9" className="fill-owned" />
        <circle r="3.4" className="fill-ink" />
      </g>
      <path d="M31.5 6.5 L25 14.4" strokeWidth="2.6" strokeLinecap="round" className="stroke-rented" />
      <circle cx="31.5" cy="6.5" r="2" className="fill-rented" />
    </svg>
  );
}

export function Logo({ className }: { className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-2.5", className)}>
      <LogoMark />
      <span className="text-[17px] font-semibold tracking-[-0.02em] text-ink">Ratchet</span>
    </span>
  );
}
