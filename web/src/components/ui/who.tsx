import { Cpu, Globe, Sparkles } from "lucide-react";
import type { ServedBy } from "@/lib/api";
import { Badge } from "./badge";

/** Who answered a call: the owned student, the rented teacher, or a tool. One vocabulary everywhere. */
export function WhoBadge({ servedBy, model, className }: { servedBy: ServedBy; model?: string; className?: string }) {
  if (servedBy === "student")
    return (
      <Badge tone="owned" className={className} title={model}>
        <Cpu aria-hidden /> Nano · yours
      </Badge>
    );
  if (servedBy === "teacher")
    return (
      <Badge tone="rented" className={className} title={model}>
        <Sparkles aria-hidden /> Ultra · rented
      </Badge>
    );
  return (
    <Badge tone="neutral" className={className} title={model}>
      <Globe aria-hidden /> {model === "tavily" ? "Tavily" : "Search"}
    </Badge>
  );
}
