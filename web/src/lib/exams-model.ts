/**
 * The two text-to-SQL exams as the pages show them: who wrote each, how big it is and what every model scored.
 * One data file (`src/data/exams.json`) feeds the landing card, the judges' "The exams" chapter and the charts. It is generated from the
 * saved answers by `scripts/text2sql/exam_numbers.py`, and `tests/test_exam_numbers.py` fails if it drifts from them.
 * Pure, no JSX: tested by `tests/exams-model.test.mjs`.
 */

export type ModelId = "base" | "teacher" | "r1" | "r2";
export type ExamId = "exam1" | "exam2";
export type Difficulty = "simple" | "join" | "aggregate" | "hard";

export const MODEL_IDS: ModelId[] = ["base", "teacher", "r1", "r2"];
export const DIFFICULTIES: Difficulty[] = ["simple", "join", "aggregate", "hard"];

export interface Score {
  agree: number;
  n: number;
  rate: number;
  lo: number;
  hi: number;
}

export interface ExamInfo {
  id: ExamId;
  name: string;
  rows: number;
  built: string;
  writer: string;
  writer_note: string;
  industries: string;
  checked: string;
  fingerprint: string;
  evidence: string;
}

export interface ExamsData {
  updated: string;
  models: Array<{ id: ModelId; label: string; short: string }>;
  exams: ExamInfo[];
  scores: Record<ExamId, Record<ModelId, Score>>;
  difficulty: Record<ExamId, Record<Difficulty, { n: number } & Record<ModelId, number>>>;
  r1_to_r2: Record<ExamId, { wrong_to_right: number; right_to_wrong: number; both_right: number; both_wrong: number; p: number }>;
  teacher_shape: Record<
    ExamId,
    { exact: number; same_rows_other_columns: number; same_count_other_values: number; other_row_count: number; does_not_run: number }
  >;
}

/** Read exams.json's text. Throws on a file whose rates do not follow from its counts: a chart must never print a number the counts do not back. */
export function parseExams(text: string): ExamsData {
  const d = JSON.parse(text) as ExamsData;
  for (const exam of ["exam1", "exam2"] as ExamId[]) {
    for (const model of MODEL_IDS) {
      const s = d.scores[exam][model];
      if (!(s.n > 0) || Math.abs(s.agree / s.n - s.rate) > 0.0001 || !(s.lo <= s.rate && s.rate <= s.hi)) {
        throw new Error(`exams.json: ${exam} ${model} is inconsistent`);
      }
    }
  }
  return d;
}

/** 0.3895 -> "39.0%". */
export function pct(rate: number): string {
  return `${(rate * 100).toFixed(1)}%`;
}

/** "26.9 to 39.8%" for a 95% range. */
export function rangeText(s: Score): string {
  return `${(s.lo * 100).toFixed(1)} to ${(s.hi * 100).toFixed(1)}%`;
}

/** "66 of 200 · 33.0%". */
export function scoreText(s: Score): string {
  return `${s.agree} of ${s.n} · ${pct(s.rate)}`;
}

/** A value on a 0..max axis as a fraction of `width`, clamped to the axis. */
export function xOf(value: number, max: number, width: number): number {
  if (!(max > 0)) return 0;
  return Math.max(0, Math.min(1, value / max)) * width;
}

/** Multiples such as "2.6": the round 2 adapter's count over the base model's. */
export function timesBase(d: ExamsData, exam: ExamId): number {
  const s = d.scores[exam];
  return s.r2.agree / s.base.agree;
}

/** What each model is, for a chart: its row label and which design token colours it. */
export const MODEL_STYLE: Record<ModelId, { color: string; opacity: number }> = {
  base: { color: "var(--muted)", opacity: 0.55 },
  teacher: { color: "var(--rented)", opacity: 1 },
  r1: { color: "var(--owned)", opacity: 0.55 },
  r2: { color: "var(--owned)", opacity: 1 },
};
