import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { useCountUp } from "@/lib/hooks";

/** Big headline number that counts up when scrolled into view. */
export function CountUp({ value, format, className }: { value: number; format: (v: number) => string; className?: string }) {
  const { ref, display } = useCountUp(value);
  return (
    <span ref={ref} className={cn("tabular", className)}>
      {format(display)}
    </span>
  );
}

export function Stat({ label, value, hint, className }: { label: ReactNode; value: ReactNode; hint?: ReactNode; className?: string }) {
  return (
    <div className={cn("rounded-2xl border border-line bg-surface p-4 shadow-card sm:p-5", className)}>
      <div className="text-[28px] font-semibold leading-none tracking-[-0.03em] text-ink tabular sm:text-[32px]">{value}</div>
      <div className="mt-2 text-[13px] text-muted">{label}</div>
      {hint && <div className="mt-1 text-[12px] text-muted">{hint}</div>}
    </div>
  );
}
