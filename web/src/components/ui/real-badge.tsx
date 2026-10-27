import { CircleCheck, CircleDashed, Gauge, Hourglass, type LucideIcon } from "lucide-react";
import { STATUS_TEXT, STATUS_TONE, statusLabel, type RealStatus } from "@/lib/real-model";
import { Badge } from "./badge";

const ICON: Record<RealStatus, LucideIcon> = {
  real: CircleCheck,
  measured: Gauge,
  pending: Hourglass,
  "not-run": CircleDashed,
};

/**
 * What a claim or a number rests on: Real, Measured, Pending or Ready to run (`src/lib/real-model.ts`). The word
 * is always shown; the icon and colour only repeat it. Its accessible name says what the status means.
 */
export function RealBadge({ status, className, label }: { status: RealStatus; className?: string; label?: string }) {
  const Icon = ICON[status];
  const text = STATUS_TEXT[status];
  return (
    <Badge tone={STATUS_TONE[status]} className={className} title={text.meaning} aria-label={statusLabel(status)} role="img">
      <Icon aria-hidden /> {label ?? text.label}
    </Badge>
  );
}
