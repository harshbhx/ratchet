import { useRef } from "react";
import { motion, useInView } from "motion/react";
import { RealBadge } from "@/components/ui/real-badge";
import { TOOLCALL } from "@/components/charts/toolcall-block";
import { EXAMS } from "@/components/charts/exam-charts";
import { pct, type ExamId } from "@/lib/exams-model";
import { LEARN_STAGES, labellingTotal, teacherOtherColumns } from "@/lib/real-story-model";
import { TOOL_EXAMS, TOOL_MODEL_LABEL, pct as toolPct, scoreText as toolScoreText, type ToolModelId } from "@/lib/toolcall-model";
import { cn, fmtInt, fmtUsd } from "@/lib/utils";
import { EASE } from "../shared";
import { COSTS } from "./real-numbers";

interface Row {
  key: string;
  label: string;
  rate: number;
  text: string;
  tone: "base" | "owned" | "rented";
  strong?: boolean;
  note?: string;
}

const FILL: Record<Row["tone"], string> = { base: "bg-muted/50", owned: "bg-owned", rented: "bg-rented" };

function Bars({ rows, inView }: { rows: Row[]; inView: boolean }) {
  return (
    <ul className="mt-3 space-y-3">
      {rows.map((r, i) => (
        <li key={r.key} className="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-3 gap-y-1.5">
          <span className={cn("min-w-0 text-[13px]", r.strong ? "font-semibold text-ink" : "text-ink-2")}>
            {r.label}
            {r.note ? <span className="ml-1.5 text-[11.5px] font-normal text-muted">{r.note}</span> : null}
          </span>
          <span className={cn("font-mono text-[13px] tabular", r.strong ? "font-semibold text-ink" : "text-ink-2")}>{r.text}</span>
          <span className="col-span-2 h-2 overflow-hidden rounded-full bg-bg-sunken" aria-hidden>
            <motion.span
              className={cn("block h-full rounded-full", FILL[r.tone])}
              initial={{ width: 0 }}
              animate={inView ? { width: `${r.rate * 100}%` } : undefined}
              transition={{ duration: 0.9, delay: 0.1 + i * 0.12, ease: EASE }}
            />
          </span>
        </li>
      ))}
    </ul>
  );
}

function sqlRows(exam: ExamId): Row[] {
  const s = EXAMS.scores[exam];
  const stages = LEARN_STAGES.map<Row>((st) => ({
    key: st.id,
    label: st.label,
    rate: s[st.id].rate,
    text: `${pct(s[st.id].rate)} · ${s[st.id].agree}/${s[st.id].n}`,
    tone: st.id === "base" ? "base" : "owned",
    strong: st.id === "r2",
  }));
  stages.push({ key: "teacher", label: "Nemotron 3 Ultra", note: "reference", rate: s.teacher.rate, text: `${pct(s.teacher.rate)} · ${s.teacher.agree}/${s.teacher.n}`, tone: "rented" });
  return stages;
}

function toolRows(exam: "hard" | "bfcl"): Row[] {
  const d = TOOLCALL;
  if (!d) return [];
  const order: ToolModelId[] = ["base", "adapter", "ultra"];
  return order.map<Row>((m) => ({
    key: m,
    label: TOOL_MODEL_LABEL[m],
    rate: d.exams[exam].scores[m].rate,
    text: `${toolPct(d.exams[exam].scores[m].rate)} · ${d.exams[exam].scores[m].agree}/${d.exams[exam].scores[m].n}`,
    tone: m === "base" ? "base" : m === "adapter" ? "owned" : "rented",
    strong: m === "adapter",
  }));
}

/**
 * "Watch it learn", the measured part: the same Nano 4B untrained, after round 1 and after round 2 on the two text-to-SQL exams,
 * with Ultra beside it as a reference, then the tool-calling adapter. Every figure is read from `exams.json` and `toolcall.json`
 * (`evidence/` holds the answers; tests fail if a file drifts) or `real-costs.json`. The ten-day simulation that follows is a
 * different thing, and stays labelled Simulation.
 */
export function RealCurve() {
  const ref = useRef<HTMLDivElement | null>(null);
  const inView = useInView(ref, { once: true, amount: 0.25 });
  const e1 = teacherOtherColumns(EXAMS, "exam1");
  const e2 = teacherOtherColumns(EXAMS, "exam2");
  const b1 = EXAMS.scores.exam1;
  const b2 = EXAMS.scores.exam2;
  return (
    <div ref={ref} className="overflow-hidden rounded-[24px] border border-line bg-surface shadow-card">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-line px-4 py-3.5 sm:px-6">
        <RealBadge status="measured" />
        <span className="text-[13px] text-ink-2">Real models, exact match against one reference query. Not the simulation.</span>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2">
        {(["exam1", "exam2"] as const).map((exam, i) => (
          <div key={exam} className={cn("p-4 sm:p-6", i === 0 && "border-b border-line lg:border-b-0 lg:border-r")}>
            <p className="text-[13px] font-semibold text-ink">
              {exam === "exam1" ? "Exam 1" : "Exam 2"} · {EXAMS.scores[exam].base.n} questions
            </p>
            <p className="mt-0.5 text-[12px] text-muted">
              {exam === "exam1" ? "Written by Nemotron 3 Ultra." : "Written by a different model, for testing only."} Untrained to round 2: {pct(EXAMS.scores[exam].base.rate)} to{" "}
              {pct(EXAMS.scores[exam].r2.rate)}.
            </p>
            <Bars rows={sqlRows(exam)} inView={inView} />
          </div>
        ))}
      </div>

      <dl className="grid grid-cols-1 gap-px border-t border-line bg-line min-[560px]:grid-cols-3">
        <div className="bg-surface-2/60 px-4 py-3.5 sm:px-6">
          <dt className="text-[12px] text-muted">Round 1: trained on</dt>
          <dd className="mt-1 font-mono text-[15px] text-ink tabular">
            {COSTS.labelling.round1.rows} rows · {fmtUsd(COSTS.labelling.round1.usd, 2)} of labels
          </dd>
        </div>
        <div className="bg-surface-2/60 px-4 py-3.5 sm:px-6">
          <dt className="text-[12px] text-muted">Round 2: trained on</dt>
          <dd className="mt-1 font-mono text-[15px] text-ink tabular">
            {fmtInt(COSTS.labelling.round2.rows_total)} rows · {fmtUsd(labellingTotal(COSTS), 2)} of labels in all
          </dd>
        </div>
        <div className="bg-surface-2/60 px-4 py-3.5 sm:px-6">
          <dt className="text-[12px] text-muted">Round 1 to round 2, exam 1 and 2</dt>
          <dd className="mt-1 font-mono text-[15px] text-ink tabular">
            +{b1.r2.agree - b1.r1.agree} and +{b2.r2.agree - b2.r1.agree} questions right
          </dd>
        </div>
      </dl>

      <p className="border-t border-line px-4 py-4 text-[13.5px] leading-relaxed text-ink-2 sm:px-6">
        <span className="font-medium text-ink">How to read it.</span> The small model learned the job; that does not make it smarter than Ultra. The strict score counts a
        query only if it returns exactly the reference rows, and Ultra loses most of its points by returning the right rows with other columns: {e1.n} of {e1.of} answers on
        exam 1 and {e2.n} of {e2.of} on exam 2. The gain from round 1 to round 2 is small enough that part of it may be chance, and the round 2 adapter still answers only {COSTS.precision_bar.answered} of{" "}
        {COSTS.precision_bar.of} measured questions at the {COSTS.precision_bar.target_pct}% precision bar.
      </p>

      {TOOLCALL ? (
        <div className="border-t border-line">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 pt-4 sm:px-6">
            <RealBadge status="measured" />
            <span className="text-[13px] font-semibold text-ink">Tool calling: a second adapter, same untrained Nano 4B</span>
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-2">
            {TOOL_EXAMS.map((e, i) => (
              <div key={e} className={cn("p-4 sm:p-6", i === 0 && "border-b border-line lg:border-b-0 lg:border-r")}>
                <p className="text-[13px] font-semibold text-ink">
                  {TOOLCALL?.exams[e].label.replace(/\s*\d+$/, "")} · {TOOLCALL?.exams[e].rows} questions
                </p>
                <Bars rows={toolRows(e)} inView={inView} />
              </div>
            ))}
          </div>
          <p className="border-t border-line px-4 py-3.5 text-[12.5px] leading-relaxed text-muted sm:px-6">
            On the hard exam the adapter is {toolScoreText(TOOLCALL.exams.hard.scores.adapter)} against Ultra's {toolScoreText(TOOLCALL.exams.hard.scores.ultra)}; on the BFCL
            slice it scores {toolPct(TOOLCALL.exams.bfcl.scores.adapter.rate)}, lower than Ultra's {toolPct(TOOLCALL.exams.bfcl.scores.ultra.rate)}.
          </p>
        </div>
      ) : null}
    </div>
  );
}

