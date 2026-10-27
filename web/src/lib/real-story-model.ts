/**
 * The real numbers the story page prints besides the exam scores: what the teacher's labelling cost, what a call costs
 * at list prices, the one precision-bar fact, and the live gateway's savings counter. The file `src/data/real-costs.json`
 * is held to `evidence/` by `tests/test_story_real_numbers.py`, so a hand-typed number cannot drift. Pure, no JSX:
 * tested by `tests/real-story-model.test.mjs`.
 */
import type { DemoSplitStats, Stats } from "./api";
import type { ExamId, ExamsData, ModelId } from "./exams-model";

export interface RealCosts {
  labelling: {
    round1: { usd: number; calls: number; rows: number; source: string };
    round2: { usd: number; calls: number; rows_total: number; source: string };
    ultra_usd_per_m_tokens: { input: number; output: number };
  };
  per_call: { note: string; ultra_triage: number; ultra_reader: number; nano_triage: number; nano_reader: number; source: string };
  precision_bar: { answered: number; of: number; target_pct: number; source: string };
}

export function parseCosts(text: string): RealCosts {
  const d = JSON.parse(text) as RealCosts;
  const nums = [d.labelling.round1.usd, d.labelling.round2.usd, d.per_call.ultra_triage, d.per_call.nano_triage, d.per_call.ultra_reader, d.per_call.nano_reader];
  if (!nums.every((n) => Number.isFinite(n) && n > 0)) throw new Error("real-costs.json: a price is missing");
  return d;
}

/** Both labelling rounds together. */
export function labellingTotal(c: RealCosts): number {
  return c.labelling.round1.usd + c.labelling.round2.usd;
}

/** How many times cheaper the Nano's call is than Ultra's, per kind of call, rounded to a whole number. */
export function callRatio(c: RealCosts, kind: "triage" | "reader"): number {
  const p = c.per_call;
  return Math.round(kind === "triage" ? p.ultra_triage / p.nano_triage : p.ultra_reader / p.nano_reader);
}

export interface DemoSavings {
  requests: number;
  costUsd: number;
  baselineUsd: number;
  savedUsd: number;
  savingsPct: number | null;
}

/** The random split counter from `/ratchet/stats`, or null when the stats are missing, the split is off or it has served nothing. */
export function demoSavings(stats: Stats | null | undefined): DemoSavings | null {
  const s: DemoSplitStats | undefined = stats?.demo_split;
  if (!s || !s.enabled || !(s.requests > 0)) return null;
  return { requests: s.requests, costUsd: s.cost_usd, baselineUsd: s.baseline_usd, savedUsd: s.saved_usd, savingsPct: s.savings_pct };
}

export const LEARN_STAGES: { id: ModelId; label: string; short: string }[] = [
  { id: "base", label: "Untrained Nano 4B", short: "Untrained" },
  { id: "r1", label: "Round 1 adapter", short: "Round 1" },
  { id: "r2", label: "Round 2 adapter", short: "Round 2" },
];

/** Exam rate of a stage, as the exams file says. */
export function stageRate(d: ExamsData, exam: ExamId, id: ModelId): number {
  return d.scores[exam][id].rate;
}

/** How many of the teacher's answers returned the right rows with other columns: why a strict match undercounts it. */
export function teacherOtherColumns(d: ExamsData, exam: ExamId): { n: number; of: number } {
  return { n: d.teacher_shape[exam].same_rows_other_columns, of: d.scores[exam].teacher.n };
}
