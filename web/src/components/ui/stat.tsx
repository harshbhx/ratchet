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
