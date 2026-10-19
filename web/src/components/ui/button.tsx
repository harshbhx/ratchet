import { forwardRef, type ButtonHTMLAttributes, type AnchorHTMLAttributes } from "react";
import { Link, type LinkProps } from "react-router";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

export const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-full font-medium transition-[background,color,box-shadow,transform] duration-200 disabled:pointer-events-none disabled:opacity-50 active:scale-[0.98] [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        primary: "bg-ink text-bg hover:bg-ink-2 shadow-card",
        owned: "bg-owned text-white hover:brightness-110 shadow-card dark:text-[#06101f]",
        outline: "border border-line-strong bg-surface text-ink hover:bg-surface-2",
        ghost: "text-ink-2 hover:bg-surface-2 hover:text-ink",
        subtle: "bg-surface-2 text-ink hover:bg-line",
        danger: "border border-bad/40 bg-bad-soft text-bad hover:border-bad",
      },
      size: {
        sm: "h-8 px-3 text-[13px]",
        md: "h-10 px-4 text-sm",
        lg: "h-12 px-6 text-[15px]",
        icon: "size-9",
      },
    },
    defaultVariants: { variant: "primary", size: "md" },
  },
);

type Variants = VariantProps<typeof buttonVariants>;

export const Button = forwardRef<HTMLButtonElement, ButtonHTMLAttributes<HTMLButtonElement> & Variants>(
  ({ className, variant, size, ...props }, ref) => (
    <button ref={ref} className={cn(buttonVariants({ variant, size }), className)} {...props} />
  ),
);
Button.displayName = "Button";

export function ButtonLink({ className, variant, size, ...props }: LinkProps & Variants) {
  return <Link className={cn(buttonVariants({ variant, size }), className)} {...props} />;
}

export function ButtonA({ className, variant, size, ...props }: AnchorHTMLAttributes<HTMLAnchorElement> & Variants) {
  return <a className={cn(buttonVariants({ variant, size }), className)} {...props} />;
}
