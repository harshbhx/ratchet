import { motion } from "motion/react";
import { Boxes, Cpu, FileCheck2, ListTree, Lock, ShieldCheck } from "lucide-react";
import type { TaskStats, Version } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { cn, fmtInt, fmtPct, fmtUsd } from "@/lib/utils";
import { studentCopy } from "@/lib/who-model";
import { isPinned } from "./model";
import { DataTable, Empty, Panel, StateChip, td, tdNum, th, thNum } from "./ui";

function BomLink({ v }: { v: Version }) {
  if (!v.bom) return <span className="text-muted">–</span>;
  const signed = v.bom === "signed";
  return (
    <a
      href={`/ratchet/versions/${v.id}/bom`}
      target="_blank"
      rel="noopener noreferrer"
      title={signed ? "Bill of materials, signed with Ed25519" : "Bill of materials with a SHA-256, not signed"}
      className="rounded-full focus-visible:outline-offset-2"
    >
      <Badge tone={signed ? "good" : "outline"} className="transition-colors hover:border-line-strong">
        {signed ? <ShieldCheck aria-hidden /> : <FileCheck2 aria-hidden />} {signed ? "Signed" : "Unsigned"}
        <span className="sr-only">bill of materials for v{v.id} (opens in a new tab)</span>
      </Badge>
    </a>
  );
}

function TrafficBar({ pct, live }: { pct: number; live: boolean }) {
  return (
    <span className="inline-flex items-center justify-end gap-2">
      <span className="tabular text-ink-2">{pct}%</span>
      <span className="relative h-1.5 w-14 overflow-hidden rounded-full bg-owned-soft" aria-hidden>
        <motion.span
          className={cn("absolute inset-y-0 left-0 rounded-full", live ? "bg-owned" : "bg-owned/70")}
          initial={false}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 0.6, ease: [0.22, 1, 0.36, 1] }}
        />
      </span>
    </span>
  );
}

/** `live`: Night School does not run on the live gateway, so its empty state says so instead of promising a first night. */
export function VersionsTable({ versions, live = false }: { versions: Version[]; live?: boolean }) {
  const rows = versions.slice().reverse();
  return (
    <Panel
      id="versions"
      title="Model versions"
      description="Every student Night School trained, newest first. Golden precision is measured on held-out golden examples."
      actions={versions.length > 0 && <span className="text-[12px] text-muted tabular">{versions.length} total</span>}
    >
      {rows.length === 0 ? (
        <Empty icon={Boxes} title="No versions yet">
          {live
            ? "Night School does not run on this live demo, so no student is trained here and the teacher answers every request."
            : "Day 1 is all teacher (cold start). Night School trains v1 on the teacher's answers that night."}
        </Empty>
      ) : (
        <DataTable caption="Model versions" minWidth={860}>
          <thead>
            <tr>
              <th className={th}>Version</th>
              <th className={th}>Trained</th>
              <th className={thNum}>Examples</th>
              <th className={thNum}>Golden precision</th>
              <th className={thNum}>Coverage</th>
              <th className={thNum}>Threshold</th>
              <th className={th}>State</th>
              <th className={thNum}>Traffic</th>
              <th className={th}>BOM</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((v) => {
              const active = v.state === "live" || v.state === "canary" || v.state === "shadow";
              return (
                <tr key={v.id} className={cn("transition-colors hover:bg-surface-2/70", active && "bg-owned-soft/25")}>
                  <td className={td}>
                    <span className="font-mono text-[13px] font-semibold text-ink">v{v.id}</span>
                    <span className="ml-2 font-mono text-[11.5px] text-muted">{v.adapter_uri}</span>
                  </td>
                  <td className={td}>Day {v.day}</td>
                  <td className={tdNum}>{fmtInt(v.train_examples)}</td>
                  <td className={tdNum}>
                    <span className="font-medium text-ink">{fmtPct(v.eval.precision, 1)}</span>
                    {v.eval.test_n != null && <span className="ml-1.5 text-[11.5px] text-muted">n={v.eval.test_n}</span>}
                  </td>
                  <td className={tdNum}>{fmtPct(v.eval.coverage, 1)}</td>
                  <td className={cn(tdNum, "font-mono text-[12.5px]")}>{v.eval.threshold == null ? "–" : v.eval.threshold.toFixed(2)}</td>
                  <td className={td}>
                    <StateChip state={v.state} traffic={v.traffic_pct} />
                  </td>
                  <td className={tdNum}>
                    <TrafficBar pct={v.traffic_pct} live={v.state === "live"} />
                  </td>
                  <td className={td}>
                    <BomLink v={v} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </DataTable>
      )}
    </Panel>
  );
}

export function ShareBar({ share, esc }: { share: number; esc: number }) {
  return (
    <span className="inline-flex items-center justify-end gap-2.5">
      <span className="w-9 text-right font-medium text-ink tabular">{fmtPct(share)}</span>
      <span className="flex h-1.5 w-20 overflow-hidden rounded-full bg-line" aria-hidden>
        <span className="h-full bg-owned" style={{ width: `${share * 100}%` }} />
        {esc > 0 && <span className="h-full border-l-2 border-surface bg-rented" style={{ width: `calc(${esc * 100}% + 2px)` }} />}
      </span>
    </span>
  );
}

/** `hosted`: the hosted fallback serves the student now, so the share column says "the student" (FR-7.40). */
export function TasksTable({ tasks, hosted = false }: { tasks: TaskStats[]; hosted?: boolean }) {
  const hasPinned = tasks.some(isPinned);
  return (
    <Panel id="tasks" title="By task" description="All days. Each task is distilled into your adapter unless it is pinned to the teacher.">
      {tasks.length === 0 ? (
        <Empty icon={ListTree} title="No traffic yet">
          Tasks appear here as requests arrive with an <span className="font-mono">X-Ratchet-Task</span> header.
        </Empty>
      ) : (
        <>
          <DataTable caption="Traffic by task" minWidth={640}>
            <thead>
              <tr>
                <th className={th}>Task</th>
                <th className={thNum}>Requests</th>
                <th className={thNum}>{studentCopy(hosted).label}</th>
                <th className={thNum}>Escalated</th>
                <th className={thNum}>Cost reduction</th>
                <th className={th}>Route</th>
              </tr>
            </thead>
            <tbody>
              {tasks.map((t) => {
                const pinned = isPinned(t);
                return (
                  <tr key={t.task} className="transition-colors hover:bg-surface-2/70">
                    <td className={td}>
                      <span className="font-mono text-[12.5px] font-medium text-ink">{t.task}</span>
                    </td>
                    <td className={tdNum}>{fmtInt(t.requests)}</td>
                    <td className={tdNum}>{pinned ? <span className="text-muted">–</span> : <ShareBar share={t.student_share} esc={t.escalation_rate} />}</td>
                    <td className={tdNum}>{pinned ? <span className="text-muted">–</span> : fmtPct(t.escalation_rate)}</td>
                    <td className={tdNum} title={`${fmtUsd(t.cost_usd)} spent vs ${fmtUsd(t.baseline_usd)} all-teacher`}>
                      <span className="text-ink-2">{fmtPct(t.savings_pct)}</span>
                      <span className="ml-1.5 text-[11.5px] text-muted">{fmtUsd(t.cost_usd)}</span>
                    </td>
                    <td className={td}>
                      {pinned ? (
                        <Badge tone="rented">
                          <Lock aria-hidden /> Pinned to teacher
                        </Badge>
                      ) : (
                        <Badge tone="owned">
                          <Cpu aria-hidden /> Distilled
                        </Badge>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </DataTable>
          <p className="mt-3 text-pretty text-[12.5px] leading-5 text-muted">
            {hasPinned ? "Pinned tasks" : "Pinned tasks, when they appear,"} (Scout's open-ended planning and writing) always go to the teacher and are never used as
            training data. The small number next to cost reduction is what the task cost in total.
          </p>
        </>
      )}
    </Panel>
  );
}
