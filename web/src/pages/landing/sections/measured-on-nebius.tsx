import { ArrowUpRight, Cpu, Server, Undo2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { RealBadge } from "@/components/ui/real-badge";
import { cn, REPO_URL } from "@/lib/utils";

/** Where the sanitised evidence of the run lives (`evidence/nightschool-nebius/`; `tests/test_nightschool_nebius_evidence.py` holds these numbers to it). */
export const NIGHT_NEBIUS_EVIDENCE = `${REPO_URL}/tree/main/evidence/nightschool-nebius`;

/** The run's key facts, as the evidence folder states them. */
export const NIGHT_NEBIUS = {
  date: "28 Oct 2026",
  vm: "A Nebius CPU VM ran the gateway and the night",
  training: "Spot L40S job: 966 steps, loss about 0.15 to 0.08",
  serving: "The adapter was served from a Nebius Serverless Endpoint",
  verdict: "Rejected: 81.8% precision vs the 95% target",
} as const;

const FACTS = [
  { icon: Server, label: "Gateway", text: NIGHT_NEBIUS.vm },
  { icon: Cpu, label: "Training", text: NIGHT_NEBIUS.training },
  { icon: Server, label: "Serving", text: NIGHT_NEBIUS.serving },
  { icon: Undo2, label: "Golden gate", text: NIGHT_NEBIUS.verdict },
] as const;

/**
 * "Measured on Nebius · 28 Oct 2026": the one end-to-end Night School run, stated with its verdict. It ran once, by hand; the golden
 * gate rejected the student and it was never made live; the live site's own gateway is on Cloudflare and is not connected to that
 * endpoint; the loop has not run nightly on live traffic. No saving is claimed.
 */
export function MeasuredOnNebius({ className }: { className?: string }) {
  return (
    <aside
      aria-label="Night School measured on Nebius"
      className={cn("min-w-0 rounded-2xl border border-line bg-surface p-4 shadow-card sm:p-5", className)}
    >
      <div className="flex flex-wrap items-center gap-2">
        <RealBadge status="real" />
        <p className="text-[13px] font-semibold text-ink">Measured on Nebius · {NIGHT_NEBIUS.date}</p>
        <Badge tone="warn">Rejected by the gate</Badge>
      </div>
      <p className="mt-2 text-pretty text-[13.5px] leading-relaxed text-ink-2">
        Night School ran end to end once on Nebius, by hand. The golden gate rejected the new student, so it was never made live.
      </p>
      <ul className="mt-3 grid min-w-0 gap-2 sm:grid-cols-2">
        {FACTS.map((f) => (
          <li key={f.label} className="flex min-w-0 items-start gap-2.5 rounded-xl border border-line bg-surface-2 px-3 py-2.5">
            <f.icon className="mt-0.5 size-4 shrink-0 text-muted" aria-hidden />
            <span className="min-w-0 text-[13px] leading-snug text-ink-2">
              <span className="block text-[11.5px] font-medium uppercase tracking-[0.08em] text-muted">{f.label}</span>
              {f.text}
            </span>
          </li>
        ))}
      </ul>
      <p className="mt-3 text-pretty text-[12.5px] leading-relaxed text-muted">
        One run, started by hand, on 4,322 rows that already existed. Four job submissions, a re-run of the evaluation with a longer timeout and
        five fixes made on the VM are all in the evidence. The live site's own gateway is still on Cloudflare and is not connected to that
        endpoint; the loop has not run nightly on live traffic. No saving is claimed.{" "}
        <a
          href={NIGHT_NEBIUS_EVIDENCE}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-0.5 font-medium text-ink underline underline-offset-2 hover:text-ink-2"
        >
          Read the evidence <ArrowUpRight className="size-3.5 text-muted" aria-hidden />
        </a>
      </p>
    </aside>
  );
}
