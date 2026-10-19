import { AnimatePresence, LayoutGroup, motion } from "motion/react";
import { Archive, Bug, ChevronRight, CheckCircle2, Undo2, XCircle, type LucideIcon } from "lucide-react";
import type { Version } from "@/lib/api";
import { cn, fmtPct } from "@/lib/utils";
import { POLICY, stateLabel } from "./model";
import { Panel } from "./ui";

type StageId = "candidate" | "shadow" | "canary10" | "canary50" | "live";

const STAGES: { id: StageId; label: string; rule: string }[] = [
  { id: "candidate", label: "Candidate", rule: `Golden gate: precision ≥ ${fmtPct(POLICY.minPrecision)}, coverage ≥ ${fmtPct(POLICY.minCoverage)}` },
  { id: "shadow", label: "Shadow", rule: `Runs silently until ≥ ${POLICY.shadowMin} would-serve answers` },
  { id: "canary10", label: "Canary 10%", rule: `≥ ${POLICY.auditsPerStage} audits to advance` },
  { id: "canary50", label: "Canary 50%", rule: `≥ ${POLICY.auditsPerStage} audits to advance` },
  { id: "live", label: "Live", rule: "Serves every request it is sure about" },
];

function stageOf(v: Version): StageId | null {
  if (v.state === "candidate") return "candidate";
  if (v.state === "shadow") return "shadow";
  if (v.state === "canary") return v.traffic_pct > 10 ? "canary50" : "canary10";
  if (v.state === "live") return "live";
  return null;
}

const EASE = [0.22, 1, 0.36, 1] as const;

function VersionChip({ v, exit, faulty }: { v: Version; exit?: boolean; faulty?: boolean }) {
  const live = v.state === "live";
  return (
    <motion.span
      layout
      layoutId={`pipe-v${v.id}`}
      initial={{ opacity: 0, scale: 0.85 }}
      animate={{ opacity: 1, scale: 1 }}
      exit={{ opacity: 0, scale: 0.85 }}
      transition={{ duration: 0.6, ease: EASE }}
      title={`v${v.id}: ${stateLabel(v)}${v.eval.precision != null ? `, golden precision ${fmtPct(v.eval.precision)}` : ""}, trained day ${v.day}${faulty ? " (demo fault injected)" : ""}`}
      className={cn(
        "inline-flex h-7 items-center gap-1.5 rounded-full border px-2.5 font-mono text-[12.5px] font-semibold",
        exit
          ? "border-line bg-surface text-muted"
          : live
            ? "border-owned bg-owned text-white shadow-card dark:text-[#06101f]"
            : "border-owned/35 bg-owned-soft text-owned-ink",
      )}
    >
      {live && <CheckCircle2 className="size-3.5" aria-hidden />}v{v.id}
      {faulty && (
        <>
          <Bug className="size-3.5 text-warn" aria-hidden />
          <span className="sr-only">(fault injected)</span>
        </>
      )}
      {v.state === "canary" && <span className="font-sans text-[11px] font-medium opacity-80">{v.traffic_pct}%</span>}
    </motion.span>
  );
}

function ExitBin({ icon: Icon, label, rule, items, tone, className }: { icon: LucideIcon; label: string; rule: string; items: Version[]; tone: "bad" | "muted"; className?: string }) {
  const shown = items.slice(-4).reverse();
  const more = items.length - shown.length;
  return (
    <div className={cn("flex min-w-0 flex-col gap-2 rounded-xl border border-line bg-bg-sunken/70 px-3 py-2.5", className)}>
      <div className="flex items-start gap-1.5 text-[12.5px] font-medium leading-4 text-ink-2 sm:items-center">
        <Icon className={cn("size-3.5 shrink-0", tone === "bad" ? "text-bad" : "text-muted")} aria-hidden />
        {label}
        <span className="ml-auto text-[11.5px] font-normal text-muted tabular">{items.length}</span>
      </div>
      <p className="hidden text-[11.5px] leading-4 text-muted sm:block">{rule}</p>
      <div className="flex min-h-7 flex-wrap items-center gap-1.5">
        <AnimatePresence mode="popLayout">
          {shown.map((v) => (
            <VersionChip key={v.id} v={v} exit />
          ))}
        </AnimatePresence>
        {more > 0 && <span className="text-[12px] text-muted">+{more} older</span>}
        {items.length === 0 && <span className="text-[12px] text-muted">none</span>}
      </div>
    </div>
  );
}

export function Pipeline({ versions, faultId }: { versions: Version[]; faultId?: number | null }) {
  const byStage = new Map<StageId, Version[]>(STAGES.map((s) => [s.id, []]));
  for (const v of versions) {
    const s = stageOf(v);
    if (s) byStage.get(s)!.push(v);
  }
  const rejected = versions.filter((v) => v.state === "rejected");
  const rolled = versions.filter((v) => v.state === "rolled_back");
  const retired = versions.filter((v) => v.state === "retired");
  const active = versions.filter((v) => stageOf(v) != null).sort((a, b) => b.id - a.id);
  const now = active.map((v) => `v${v.id} ${stateLabel(v).toLowerCase()}`).join(" · ");

  return (
    <Panel
      id="pipeline"
      title="Release pipeline"
      description="A version only moves forward through the gates. If it slips, the gate catches it: a bad canary is rolled back automatically."
      actions={
        <span className="hidden max-w-[22rem] truncate rounded-full border border-line bg-surface-2 px-2.5 py-1 text-[12px] text-ink-2 md:inline" aria-live="polite">
          {now ? <>Now: {now}</> : "No versions yet"}
        </span>
      }
    >
      <p className="mb-3 text-[12.5px] text-ink-2 md:hidden" aria-hidden>
        {now ? <>Now: {now}</> : "No versions yet"}
      </p>
      <LayoutGroup id="pipeline">
        <ol className="grid grid-cols-1 sm:grid-cols-5" aria-label="Release stages">
          {STAGES.map((s, i) => {
            const here = byStage.get(s.id)!;
            const occupied = here.length > 0;
            return (
              <li key={s.id} className="grid grid-cols-[28px_1fr] gap-x-3 sm:grid-cols-1 sm:gap-x-0">
                {/* track: vertical on phones, horizontal from sm */}
                <div className="relative flex flex-col items-center sm:flex-row" aria-hidden>
                  <span className={cn("h-1 w-0.5 sm:h-0.5 sm:w-auto sm:flex-1", i === 0 ? "bg-transparent" : "bg-line-strong")} />
                  <span
                    className={cn(
                      "relative z-[1] grid size-6 shrink-0 place-items-center rounded-full border-2 text-[11px] font-semibold transition-colors duration-500",
                      occupied ? "border-owned bg-owned text-white dark:text-[#06101f]" : "border-line-strong bg-surface text-muted",
                    )}
                  >
                    {i + 1}
                  </span>
                  <span className={cn("relative w-0.5 flex-1 sm:h-0.5 sm:w-auto", i === STAGES.length - 1 ? "bg-transparent" : "bg-line-strong")}>
                    {i < STAGES.length - 1 && (
                      <ChevronRight
                        className={cn(
                          "absolute left-1/2 top-1/2 hidden size-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-surface sm:block",
                          "text-muted",
                        )}
                        strokeWidth={3}
                      />
                    )}
                  </span>
                </div>
                <div className="min-w-0 pb-4 pt-1 sm:px-2 sm:pb-0 sm:pt-3 sm:text-center">
                  <div className="text-[13.5px] font-semibold text-ink">{s.label}</div>
                  <p className="mt-0.5 text-pretty text-[11.5px] leading-4 text-muted sm:mx-auto sm:min-h-8 sm:max-w-[13rem]">{s.rule}</p>
                  <div
                    className={cn(
                      "mt-2 flex min-h-11 flex-wrap items-center gap-1.5 rounded-xl border px-2 py-2 transition-colors duration-500 sm:justify-center",
                      occupied ? "border-owned/30 bg-owned-soft/50" : "border-dashed border-line bg-transparent",
                    )}
                  >
                    <AnimatePresence mode="popLayout">
                      {here.map((v) => (
                        <VersionChip key={v.id} v={v} faulty={v.id === faultId} />
                      ))}
                    </AnimatePresence>
                    {!occupied && <span className="text-[12px] text-muted">empty</span>}
                  </div>
                </div>
              </li>
            );
          })}
        </ol>
        <div className="mt-1 grid grid-cols-3 gap-2 border-t border-dashed border-line pt-4 sm:mt-4 sm:grid-cols-5 sm:gap-3">
          <ExitBin className="sm:col-span-2" icon={XCircle} tone="bad" label="Rejected" rule="Failed the golden gate or shadow" items={rejected} />
          <ExitBin
            className="sm:col-span-2"
            icon={Undo2}
            tone="bad"
            label="Rolled back"
            rule={`Canary audits clearly under ${fmtPct(POLICY.liveFloor)}; traffic returns to live`}
            items={rolled}
          />
          <ExitBin icon={Archive} tone="muted" label="Retired" rule="Replaced by a newer version" items={retired} />
        </div>
      </LayoutGroup>
    </Panel>
  );
}
