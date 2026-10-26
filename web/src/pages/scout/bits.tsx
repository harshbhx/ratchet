import { useEffect, useRef, useState, type ReactNode } from "react";
import { animate, useReducedMotion } from "motion/react";
import { ArrowUpRight, Check, CornerRightUp, Pin, Sprout, Wrench, X } from "lucide-react";
import type { ScoutStep } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { WhoBadge, useWho } from "@/components/ui/who";
import { cn, domainOf, fmtMs, fmtUsd, safeHref } from "@/lib/utils";
import type { AgentRow, NodeState } from "./state";

/** A number that tweens from its previous value. Final value immediately under reduced motion. */
export function AnimatedNumber({ value, format, className }: { value: number; format: (v: number) => string; className?: string }) {
  const reduce = useReducedMotion();
  const [display, setDisplay] = useState(value);
  const from = useRef(value);
  useEffect(() => {
    if (reduce) {
      from.current = value;
      setDisplay(value);
      return;
    }
    const controls = animate(from.current, value, {
      duration: 0.45,
      ease: [0.22, 1, 0.36, 1],
      onUpdate: (v) => {
        from.current = v;
        setDisplay(v);
      },
    });
    return () => controls.stop();
  }, [value, reduce]);
  return <span className={cn("tabular", className)}>{format(display)}</span>;
}

/** Cost with sensible precision for sub-cent per-call amounts. */
export function usd(v: number): string {
  if (!v) return "$0";
  return fmtUsd(v, Math.abs(v) < 0.001 ? 5 : Math.abs(v) < 1 ? 4 : 2);
}

export const AGENTS: Record<
  AgentRow["agent"],
  { n: number; name: string; task: string | null; route: "pinned" | "distillable" | "tool"; does: string }
> = {
  planner: { n: 1, name: "Planner", task: "research-plan", route: "pinned", does: "Writes three web search queries for the question." },
  searcher: { n: 2, name: "Searcher", task: null, route: "tool", does: "Runs each query on the web. A tool call, not a model call." },
  triage: {
    n: 3,
    name: "Triage",
    task: "source-triage",
    route: "distillable",
    does: "One call per result: is it relevant, and what kind of source is it?",
  },
  reader: {
    n: 4,
    name: "Reader",
    task: "page-read",
    route: "distillable",
    does: "One call per relevant result: does it answer the question, and what is the key fact?",
  },
  writer: { n: 5, name: "Writer", task: "research-write", route: "pinned", does: "Writes the answer from the key facts, with numbered citations." },
};

export function RouteNote({ route, className }: { route: "pinned" | "distillable" | "tool"; className?: string }) {
  if (route === "pinned")
    return (
      <span className={cn("inline-flex items-center gap-1 text-[12px] font-medium text-rented-ink", className)}>
        <Pin className="size-3" aria-hidden /> Pinned to Ultra: open-ended
      </span>
    );
  if (route === "distillable")
    return (
      <span className={cn("inline-flex items-center gap-1 text-[12px] font-medium text-owned-ink", className)}>
        <Sprout className="size-3" aria-hidden /> Distillable: moves to your adapter
      </span>
    );
  return (
    <span className={cn("inline-flex items-center gap-1 text-[12px] font-medium text-muted", className)}>
      <Wrench className="size-3" aria-hidden /> Tool call, not a model call
    </span>
  );
}

const STATE_LABEL: Record<NodeState, string> = { idle: "waiting", queued: "queued", working: "working", done: "done", skipped: "not run" };

/** Numbered disc for an agent, colored by its state. */
export function AgentDisc({ n, state, className }: { n: number; state: NodeState; className?: string }) {
  return (
    <span
      aria-hidden
      className={cn(
        "relative inline-flex size-6 shrink-0 items-center justify-center rounded-full border font-mono text-[11px] font-semibold tabular transition-colors duration-300",
        state === "done" && "border-ink bg-ink text-bg",
        state === "working" && "border-owned bg-owned-soft text-owned-ink",
        (state === "idle" || state === "queued") && "border-line-strong bg-surface text-muted",
        state === "skipped" && "border-dashed border-line-strong bg-surface text-muted",
        className,
      )}
    >
      {state === "done" ? <Check className="size-3.5" strokeWidth={3} /> : n}
      {state === "working" && <span className="absolute inset-[-4px] animate-ping rounded-full border border-owned/50 motion-reduce:hidden" />}
    </span>
  );
}

export function stateLabel(s: NodeState) {
  return STATE_LABEL[s];
}

/** Shorter display name for model ids. Full id stays in the title attribute. */
export function shortModel(model: string): string {
  if (!model) return "";
  const m = model.split("/").pop() ?? model;
  if (/ultra/i.test(m)) return "Nemotron 3 Ultra";
  return m;
}

/** Who answered, confidence, escalation, cost vs Ultra, latency. One row, wraps on small screens. */
export function StepMeta({ step, className }: { step: ScoutStep; className?: string }) {
  const who = useWho();
  const tool = step.served_by === "tool";
  const same = Math.abs(step.cost_usd - step.baseline_usd) <= Math.max(1e-9, step.baseline_usd * 0.01);
  // Cost includes any audit or shadow call Ratchet made for this step (docs/SCOUT.md), so a
  // student answer that costs about as much as Ultra was checked by Ultra too.
  const checked = step.served_by === "student" && step.baseline_usd > 0 && step.cost_usd > step.baseline_usd * 0.5;
  return (
    <div className={cn("flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[12px] text-muted tabular", className)}>
      <WhoBadge servedBy={step.served_by} model={step.model} route={"route" in step.detail ? step.detail.route : undefined} />
      {!tool && (
        <span className="font-mono text-[11.5px]" title={step.model}>
          {shortModel(step.model)}
        </span>
      )}
      {step.served_by === "student" && step.confidence != null && <span>conf {step.confidence.toFixed(2)}</span>}
      {step.escalated && (
        <Badge
          tone="rented"
          title={`${who.hosted ? "The hosted Nano (untuned)" : "The student (Nano with your adapter)"} tried first, was below its confidence threshold, and handed the call to Ultra.`}
        >
          <CornerRightUp aria-hidden /> escalated
          {step.confidence != null && <span className="font-normal opacity-80">· conf {step.confidence.toFixed(2)}</span>}
        </Badge>
      )}
      {!tool && (
        <span>
          <span className="text-ink-2">{usd(step.cost_usd)}</span>
          {!same && <span> vs {usd(step.baseline_usd)} on Ultra</span>}
        </span>
      )}
      {checked && (
        <span
          className="underline decoration-dotted underline-offset-2"
          title="Ratchet also asked Ultra to check this answer (a quality audit or shadow call). The cost includes that check."
        >
          incl. Ultra check
        </span>
      )}
      <span>{step.latency_ms < 1 ? "<1 ms" : fmtMs(step.latency_ms)}</span>
    </div>
  );
}

/** External link for web content: only http(s), always noopener. Falls back to plain text. */
export function WebLink({ url, children, className }: { url: string; children: ReactNode; className?: string }) {
  const href = safeHref(url);
  if (!href) return <span className={className}>{children}</span>;
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className={cn("group/link underline-offset-2 hover:underline", className)}>
      {children}
      <ArrowUpRight aria-hidden className="ml-0.5 inline size-3 -translate-y-px opacity-0 transition-opacity group-hover/link:opacity-60" />
      <span className="sr-only"> (opens in a new tab)</span>
    </a>
  );
}

export function Domain({ url, className }: { url: string; className?: string }) {
  return <span className={cn("font-mono text-[11.5px] text-muted [overflow-wrap:anywhere]", className)}>{domainOf(url)}</span>;
}

export function YesNo({ yes, yesLabel, noLabel }: { yes: boolean; yesLabel: string; noLabel: string }) {
  return yes ? (
    <span className="inline-flex items-center gap-1 text-[12.5px] font-semibold text-good">
      <span className="inline-flex size-4 items-center justify-center rounded-full bg-good-soft">
        <Check className="size-3" strokeWidth={3} aria-hidden />
      </span>
      {yesLabel}
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 text-[12.5px] font-semibold text-muted">
      <span className="inline-flex size-4 items-center justify-center rounded-full bg-surface-2">
        <X className="size-3" strokeWidth={3} aria-hidden />
      </span>
      {noLabel}
    </span>
  );
}
