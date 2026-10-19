import type { HTMLAttributes } from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

export const badgeVariants = cva(
  "inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-[12px] font-medium leading-5 [&_svg]:size-3.5",
  {
    variants: {
      tone: {
        neutral: "border-line bg-surface-2 text-ink-2",
        owned: "border-owned/30 bg-owned-soft text-owned-ink",
        rented: "border-rented/30 bg-rented-soft text-rented-ink",
        good: "border-good/30 bg-good-soft text-good",
        warn: "border-warn/30 bg-warn-soft text-warn",
        bad: "border-bad/30 bg-bad-soft text-bad",
        outline: "border-line-strong text-muted",
      },
    },
    defaultVariants: { tone: "neutral" },
  },
);

export function Badge({ className, tone, ...props }: HTMLAttributes<HTMLSpanElement> & VariantProps<typeof badgeVariants>) {
  return <span className={cn(badgeVariants({ tone }), className)} {...props} />;
}
