import { useState, type ReactNode } from "react";
import { Check, Copy } from "lucide-react";
import { cn } from "@/lib/utils";

/** Monospace block with a copy button. `children` may contain highlighted spans; `copy` is the plain text. */
export function CodeBlock({ title, copy, children, className }: { title?: string; copy: string; children: ReactNode; className?: string }) {
  const [done, setDone] = useState(false);
  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(copy);
      setDone(true);
      window.setTimeout(() => setDone(false), 1600);
    } catch {
      /* clipboard blocked */
    }
  };
  return (
    <div className={cn("overflow-hidden rounded-2xl border border-line bg-bg-sunken", className)}>
      <div className="flex items-center justify-between border-b border-line px-4 py-2">
        <span className="font-mono text-[12px] text-muted">{title}</span>
        <button
          type="button"
          onClick={onCopy}
          className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[12px] text-muted transition-colors hover:bg-surface-2 hover:text-ink"
          aria-label="Copy code"
        >
          {done ? <Check className="size-3.5 text-good" /> : <Copy className="size-3.5" />}
          {done ? "Copied" : "Copy"}
        </button>
      </div>
      <pre className="overflow-x-auto px-4 py-4 font-mono text-[13px] leading-6 text-ink-2">
        <code>{children}</code>
      </pre>
    </div>
  );
}

/** Minimal token colouring for code samples. */
export const tok = {
  k: (s: string) => <span className="text-owned-ink">{s}</span>,
  s: (s: string) => <span className="text-good-ink">{s}</span>,
  c: (s: string) => <span className="text-muted">{s}</span>,
  h: (s: string) => <span className="rounded bg-owned-soft px-1 text-owned-ink">{s}</span>,
};
