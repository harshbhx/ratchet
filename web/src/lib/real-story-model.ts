/**
 * The real numbers the story page prints besides the exam scores: what the teacher's labelling cost, what a call costs
 * at list prices, the one precision-bar fact, and the live gateway's savings counter. The file `src/data/real-costs.json`
 * is held to `evidence/` by `tests/test_story_real_numbers.py`, so a hand-typed number cannot drift. Pure, no JSX:
 * tested by `tests/real-story-model.test.mjs`.
 */
import type { DemoSplitStats, Stats } from "./api";
import type { ExamId, ExamsData, ModelId } from "./exams-model";
import { fmtDay, type Persistent } from "./measured-model";

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

export interface SplitTile {
  /** `process`: this process's own counter; `since`: the Worker's totals kept across restarts; `listed`: list prices, as a last resort. */
  kind: "process" | "since" | "listed";
  value: string;
  label: string;
  note: string;
  /** The badge wording: "Live" for the first two, "Measured" for list prices. */
  badge: "Live" | "Measured";
}

/**
 * The "random split" tile, never a bare dash. First the process's own savings (`/ratchet/stats`); a restart empties that, so
 * next the Worker's totals (`/ratchet/stats/persistent`: the count of requests the hosted Nano answered since the first record,
 * and the cost of all calls; they hold no all-Ultra baseline, so no saving is shown from them); last the per-call list prices.
 */
export function splitTile(stats: Stats | null | undefined, persistent: Persistent | null | undefined, costs: RealCosts): SplitTile {
  const fmt = (v: number, digits: number) => `$${v.toFixed(digits)}`;
  const live = demoSavings(stats);
  if (live) {
    return {
      kind: "process",
      value: fmt(live.savedUsd, live.savedUsd < 1 ? 4 : 2),
      label: `saved by the random split${live.savingsPct != null ? `, ${Math.round(live.savingsPct * 100)}% below all-Ultra` : ""}`,
      note: `${live.requests} requests answered by the hosted Nano, picked at random: ${fmt(live.costUsd, 4)} paid against ${fmt(live.baselineUsd, 4)} all-Ultra.`,
      badge: "Live",
    };
  }
  if (persistent && persistent.totals.requests_split > 0) {
    const t = persistent.totals;
    const n = t.requests_split;
    return {
      kind: "since",
      value: String(n),
      label: `${n === 1 ? "request" : "requests"} answered by the hosted Nano 30B since ${fmtDay(persistent.since ?? "")}`,
      note: `Picked at random, not by confidence. All calls on the live site cost ${fmt(t.cost_usd, 4)} on Token Factory in that time. Kept across restarts; no saving is computed from these totals.`,
      badge: "Live",
    };
  }
  const ratio = callRatio(costs, "triage");
  return {
    kind: "listed",
    value: `${ratio}x`,
    label: "cheaper per triage call: the hosted Nano against Ultra",
    note: "List prices per call. The gateway's own counter of what the random split saved appears here once it has served a request.",
    badge: "Measured",
  };
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
