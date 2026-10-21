import { createContext, useContext } from "react";
import { Cloud, Cpu, Globe, Sparkles, type LucideIcon } from "lucide-react";
import type { ServedBy } from "@/lib/api";
import { whoLabel, type WhoContextValue, type WhoKind } from "@/lib/who-model";
import { Badge } from "./badge";

/** What a page knows about the gateway's student (`whoContext`), for every badge under it. A badge's own props win. */
export type { WhoContextValue };

const WhoContext = createContext<WhoContextValue>({});
export const WhoProvider = WhoContext.Provider;
export function useWho(): WhoContextValue {
  return useContext(WhoContext);
}

const TONE: Record<WhoKind, "owned" | "outline" | "rented" | "neutral"> = { owned: "owned", hosted: "outline", rented: "rented", tool: "neutral" };
const ICON: Record<WhoKind, LucideIcon> = { owned: Cpu, hosted: Cloud, rented: Sparkles, tool: Globe };

/**
 * Who answered a call: our adapter, the hosted fallback, the teacher, or a tool (`whoLabel`). One vocabulary
 * everywhere; pass the call's `model` whenever it is known, since only the model id tells our adapter from the fallback.
 */
export function WhoBadge({
  servedBy,
  model,
  fallback,
  threshold,
  windows,
  simulated,
  className,
}: {
  servedBy: ServedBy;
  model?: string | null;
  fallback?: string | null;
  threshold?: number | null;
  windows?: boolean;
  simulated?: boolean;
  className?: string;
}) {
  const ctx = useWho();
  const w = whoLabel({
    servedBy,
    model,
    fallback: fallback ?? ctx.fallback,
    threshold: threshold ?? ctx.threshold,
    windows: windows ?? ctx.windows,
    simulated: simulated ?? ctx.simulated,
  });
  const Icon = ICON[w.kind];
  return (
    <Badge tone={TONE[w.kind]} className={className} title={w.title || undefined}>
      <Icon aria-hidden /> {w.text}
    </Badge>
  );
}
