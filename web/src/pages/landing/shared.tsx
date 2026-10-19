import { useEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { motion } from "motion/react";
import { ArrowUpRight, FlaskConical } from "lucide-react";
import { Eyebrow, H2, Lead } from "@/components/ui/section";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

export const EASE = [0.22, 1, 0.36, 1] as const;

/** The story's chapters, in order. Used by the section ids and the progress rail. */
export const CHAPTERS = [
  { id: "rent", index: "01", label: "The rent" },
  { id: "catch", index: "02", label: "The catch" },
  { id: "how", index: "03", label: "How it turns" },
  { id: "safety", index: "04", label: "One way only" },
  { id: "learn", index: "05", label: "Watch it learn" },
  { id: "scout", index: "06", label: "Agent traffic" },
  { id: "stack", index: "07", label: "Nebius + NVIDIA" },
  { id: "run", index: "08", label: "Run it" },
] as const;

export type ChapterId = (typeof CHAPTERS)[number]["id"];

export const LINKS = {
  openai: "https://openai.com/policies/business-terms/",
  anthropic: "https://www.anthropic.com/legal/commercial-terms",
  ultra: "https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Ultra-550B-A55B-BF16",
  nvidiaSlm: "https://arxiv.org/html/2506.02153v1",
  menlo: "https://menlovc.com/perspective/2025-the-state-of-generative-ai-in-the-enterprise/",
  gemini: "https://ai.google.dev/gemini-api/terms",
};

/** Fade + rise once, when scrolled into view. */
export function Reveal({
  children,
  className,
  delay = 0,
  y = 18,
  as = "div",
}: {
  children: ReactNode;
  className?: string;
  delay?: number;
  y?: number;
  as?: "div" | "li";
}) {
  const props = {
    className,
    initial: { opacity: 0, y },
    whileInView: { opacity: 1, y: 0 },
    viewport: { once: true, margin: "0px 0px -10% 0px" },
    transition: { duration: 0.65, ease: EASE, delay },
  };
  return as === "li" ? <motion.li {...props}>{children}</motion.li> : <motion.div {...props}>{children}</motion.div>;
}

/** One chapter: anchored section with consistent rhythm. */
export function Chapter({ id, children, className }: { id: ChapterId; children: ReactNode; className?: string }) {
  return (
    <section id={id} aria-labelledby={`${id}-title`} className={cn("relative scroll-mt-16 py-20 sm:py-28 lg:py-32", className)}>
      {children}
    </section>
  );
}

export function ChapterHead({
  id,
  title,
  lead,
  className,
  aside,
}: {
  id: ChapterId;
  title: ReactNode;
  lead?: ReactNode;
  className?: string;
  aside?: ReactNode;
}) {
  const ch = CHAPTERS.find((c) => c.id === id)!;
  return (
    <Reveal className={cn("grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-end", className)}>
      <div className="max-w-3xl">
        <Eyebrow index={ch.index}>{ch.label}</Eyebrow>
        <H2 id={`${id}-title`} className="mt-5">
          {title}
        </H2>
        {lead && <div className="mt-5 max-w-[64ch] space-y-4">{lead}</div>}
      </div>
      {aside}
    </Reveal>
  );
}

export { Lead };

export function ExtLink({ href, children, className }: { href: string; children: ReactNode; className?: string }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className={cn(
        "inline-flex items-baseline gap-0.5 font-medium text-ink underline decoration-line-strong decoration-1 underline-offset-[3px] transition-colors hover:decoration-ink",
        className,
      )}
    >
      {children}
      <ArrowUpRight aria-hidden className="size-3 shrink-0 translate-y-[1px] self-center text-muted" />
    </a>
  );
}

export function SimBadge({ children = "Simulation", className }: { children?: ReactNode; className?: string }) {
  return (
    <Badge tone="outline" className={className}>
      <FlaskConical aria-hidden /> {children}
    </Badge>
  );
}

export function ExampleBadge({ className }: { className?: string }) {
  return (
    <Badge tone="outline" className={className}>
      Example
    </Badge>
  );
}

/** Width of an element, kept up to date with a ResizeObserver. */
export function useElementWidth<T extends HTMLElement>() {
  const ref = useRef<T | null>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    setWidth(el.getBoundingClientRect().width);
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width ?? 0;
      setWidth((prev) => (Math.abs(prev - w) > 0.5 ? w : prev));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width] as const;
}

/** True while the element is at least partly on screen and the tab is visible. */
export function useOnScreen<T extends Element>(ref: RefObject<T | null>, rootMargin = "0px") {
  const [on, setOn] = useState(true);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    let inView = true;
    let visible = !document.hidden;
    const sync = () => setOn(inView && visible);
    const io = new IntersectionObserver((e) => {
      inView = e[0]?.isIntersecting ?? true;
      sync();
    }, { rootMargin });
    io.observe(el);
    const onVis = () => {
      visible = !document.hidden;
      sync();
    };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      io.disconnect();
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [ref, rootMargin]);
  return on;
}

/** 95% Wilson score interval, same formula as `ratchet/release.py`. */
export function wilson(k: number, n: number, z = 1.96): [number, number] {
  if (n === 0) return [0, 1];
  const p = k / n;
  const z2 = z * z;
  const denom = 1 + z2 / n;
  const center = (p + z2 / (2 * n)) / denom;
  const margin = (z * Math.sqrt((p * (1 - p)) / n + z2 / (4 * n * n))) / denom;
  return [Math.max(0, center - margin), Math.min(1, center + margin)];
}

/** Small arrowhead path at (x, y) pointing in a direction. */
export function arrowHead(x: number, y: number, dir: "r" | "l" | "u" | "d", s = 5) {
  switch (dir) {
    case "r":
      return `M${x - s} ${y - s * 0.75} L${x} ${y} L${x - s} ${y + s * 0.75}`;
    case "l":
      return `M${x + s} ${y - s * 0.75} L${x} ${y} L${x + s} ${y + s * 0.75}`;
    case "u":
      return `M${x - s * 0.75} ${y + s} L${x} ${y} L${x + s * 0.75} ${y + s}`;
    case "d":
      return `M${x - s * 0.75} ${y - s} L${x} ${y} L${x + s * 0.75} ${y - s}`;
  }
}

/** Sawtooth ratchet wheel path centred on (0,0). */
export function ratchetPath(teeth: number, rOuter: number, rInner: number) {
  const pts: string[] = [];
  const step = (Math.PI * 2) / teeth;
  for (let i = 0; i < teeth; i++) {
    const a0 = i * step - Math.PI / 2;
    const a1 = a0 + step;
    const p = (r: number, a: number) => `${(Math.cos(a) * r).toFixed(2)} ${(Math.sin(a) * r).toFixed(2)}`;
    pts.push(`${i === 0 ? "M" : "L"}${p(rInner, a0)}`);
    pts.push(`L${p(rOuter, a1 - step * 0.06)}`);
    pts.push(`L${p(rInner, a1)}`);
  }
  return pts.join(" ") + " Z";
}
