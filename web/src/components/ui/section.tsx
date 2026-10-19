import type { HTMLAttributes, ReactNode } from "react";
import { cn } from "@/lib/utils";

export function Container({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("mx-auto w-full max-w-6xl px-4 sm:px-6 lg:px-8", className)} {...props} />;
}

export function Eyebrow({ className, children, index }: { className?: string; children: ReactNode; index?: string }) {
  return (
    <p className={cn("flex items-center gap-3 font-mono text-[12px] uppercase tracking-[0.14em] text-muted", className)}>
      {index && <span className="text-owned-ink">{index}</span>}
      {index && <span aria-hidden className="h-px w-8 bg-line-strong" />}
      <span>{children}</span>
    </p>
  );
}

export function H2({ className, ...props }: HTMLAttributes<HTMLHeadingElement>) {
  return (
    <h2
      className={cn("text-balance text-3xl font-semibold tracking-[-0.025em] text-ink sm:text-4xl lg:text-[2.75rem] lg:leading-[1.08]", className)}
      {...props}
    />
  );
}

export function Lead({ className, ...props }: HTMLAttributes<HTMLParagraphElement>) {
  return <p className={cn("text-pretty text-[17px] leading-relaxed text-ink-2 sm:text-lg", className)} {...props} />;
}
