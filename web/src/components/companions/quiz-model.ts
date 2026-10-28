// "Quiz Nano": real questions from the two published text-to-SQL exams, with the answers that were recorded and the marks the published
// scoring gave them. Nothing here calls a model, and nothing is re-judged: a row is right or wrong exactly as `quiz.json` says
// (scripts/text2sql/quiz_sample.py recomputes it from the saved answers, and tests/test_quiz_sample.py holds the file to them).
// Pure: tests/companions.test.mjs.
import { createPicker } from "./banter";

export interface Answer {
  sql: string;
  right: boolean;
}
export interface QuizRow {
  exam: "exam1" | "exam2";
  i: number;
  domain: string;
  difficulty: string;
  question: string;
  reference: string;
  nano: Answer;
  ultra: Answer;
  base: Answer;
}
export interface Quiz {
  source: string;
  scoring: string;
  rows: QuizRow[];
}

/** What every quiz card says about where the answers come from. */
export const PROVENANCE = "Recorded answers from the published exam, harshbhx/ratchet-text2sql-exams. No model was called here: nothing on this card is a live answer.";
export const MARK_NOTE = "Marked the way the published exam was: both queries run on the same database and the rows are compared.";
export const NANO_LABEL = "Nano's recorded answer";
export const ULTRA_LABEL = "Ultra's recorded answer";
export const REFERENCE_LABEL = "Reference query (the exam's own)";
export const BASE_LABEL = "The untrained model said";

/** Who wrote each exam, from the evidence: exam 2 is another lab's model, kept for evaluation only. */
export function examNote(exam: QuizRow["exam"]): string {
  return exam === "exam1"
    ? "Exam 1 was written by the teacher, Nemotron 3 Ultra."
    : "Exam 2 was written by a different lab's model, for evaluation only.";
}

export function verdictLine(right: boolean): string {
  return right ? "Marked right: the rows match the reference." : "Marked wrong: the rows differ from the reference.";
}

/** What the two say while a quiz question is up. Short and true: Nano's answer is a recording, so nothing claims it is live. */
export const QUIZ_LINES = {
  ask: { nano: "Okay. Here is what I answered on the exam.", ultra: "Real question. Recorded answer." },
  right: { nano: "Got it. The rows match.", ultra: "Marked right." },
  wrong: { nano: "Oops. That one missed.", ultra: "Marked wrong. The reference is below." },
} as const;

/** The text the quiz adds to the page, for the honesty test. */
export function quizTexts(): string[] {
  return [
    PROVENANCE,
    MARK_NOTE,
    NANO_LABEL,
    ULTRA_LABEL,
    REFERENCE_LABEL,
    BASE_LABEL,
    examNote("exam1"),
    examNote("exam2"),
    verdictLine(true),
    verdictLine(false),
    ...Object.values(QUIZ_LINES).flatMap((l) => [l.nano, l.ultra]),
  ];
}

/** Read quiz.json's text. Throws on a file that is not a plain list of marked rows: a quiz card must never show an unmarked answer. */
export function parseQuiz(text: string): Quiz {
  const d = JSON.parse(text) as Quiz;
  if (!d || !Array.isArray(d.rows) || d.rows.length === 0) throw new Error("quiz.json: no rows");
  for (const r of d.rows) {
    if (!r.question || !r.reference) throw new Error("quiz.json: a row has no question or reference");
    for (const k of ["nano", "ultra", "base"] as const) {
      if (typeof r[k]?.sql !== "string" || typeof r[k]?.right !== "boolean") throw new Error(`quiz.json: ${r.exam} ${r.i} has no marked ${k} answer`);
    }
  }
  return d;
}

/** The marks, exactly as recorded. */
export function markOf(row: QuizRow): { nano: boolean; ultra: boolean; base: boolean } {
  return { nano: row.nano.right, ultra: row.ultra.right, base: row.base.right };
}

/** What shows after Ultra marks: Nano's answer always; when it was wrong, Ultra's own recorded answer and the reference too. */
export function reveal(row: QuizRow): Array<{ label: string; sql: string; right?: boolean }> {
  const out: Array<{ label: string; sql: string; right?: boolean }> = [{ label: NANO_LABEL, sql: row.nano.sql, right: row.nano.right }];
  if (!row.nano.right) {
    out.push({ label: ULTRA_LABEL, sql: row.ultra.sql, right: row.ultra.right });
    out.push({ label: REFERENCE_LABEL, sql: row.reference });
  }
  return out;
}

/** A shuffled bag of the rows: every question once before any repeats. */
export function quizPicker(rows: readonly QuizRow[], rng: () => number = Math.random) {
  return createPicker(rows, rng);
}
