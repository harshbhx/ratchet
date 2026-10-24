/**
 * Round 3, tool calling, as the pages show it. One data file (`src/data/toolcall.json`) is written by `scripts/toolcall_results.py --render --web`
 * from `evidence/toolcall/results.json`; the file does not exist until the exam scores do, and then no page shows anything.
 * The script, not the page, decides the headline variant and the wording. Pure, no JSX: tested by `tests/toolcall-model.test.mjs`.
 */

export type ToolExamId = "hard" | "bfcl";
export type ToolModelId = "base" | "ultra" | "adapter";
export type Verdict = "above" | "about_same" | "below" | "higher" | "lower";

export interface ToolScore {
  agree: number;
  n: number;
  rate: number;
  lo: number;
  hi: number;
}

export interface ToolExam {
  label: string;
  rows: number;
  scores: Record<ToolModelId, ToolScore>;
  verdicts: { adapter_vs_base: Verdict; adapter_vs_ultra: Verdict };
  sentence: string;
  paired: { fixed: number; broke: number; p: number };
}

export interface ToolcallData {
  /** "B": tool calling leads (above the untrained model on both exams, ranges apart). "A": text-to-SQL stays the headline. */
  variant: "A" | "B";
  variant_reason: string;
  date: string;
  exams: Record<ToolExamId, ToolExam>;
  caveat: string;
  setting: string;
}

export const TOOL_EXAMS: ToolExamId[] = ["hard", "bfcl"];
export const TOOL_MODELS: ToolModelId[] = ["base", "ultra", "adapter"];
export const TOOL_MODEL_LABEL: Record<ToolModelId, string> = {
  base: "Untrained Nano 4B",
  ultra: "Teacher (Nemotron 3 Ultra)",
  adapter: "Tool-calling adapter",
};

/** B only when the adapter's range is above the untrained model's on both exams (no overlap); otherwise A. */
export function headlineVariant(d: ToolcallData): "A" | "B" {
  const above = TOOL_EXAMS.every((e) => d.exams[e].scores.adapter.lo > d.exams[e].scores.base.hi);
  return above ? "B" : "A";
}

/** Read toolcall.json's text, or null when there is none yet. Throws on a file whose rates or variant do not follow from its counts and ranges. */
export function parseToolcall(text: string | null | undefined): ToolcallData | null {
  if (!text) return null;
  const d = JSON.parse(text) as ToolcallData;
  for (const e of TOOL_EXAMS) {
    for (const m of TOOL_MODELS) {
      const s = d.exams[e].scores[m];
      if (!(s.n > 0) || Math.abs(s.agree / s.n - s.rate) > 0.0001 || !(s.lo <= s.rate && s.rate <= s.hi)) {
        throw new Error(`toolcall.json: ${e} ${m} is inconsistent`);
      }
    }
  }
  if (d.variant !== headlineVariant(d)) throw new Error("toolcall.json: the variant does not follow from the ranges");
  return d;
}

/** Where the tool-calling block sits on a page: before the text-to-SQL results (B), after them (A), or nowhere without data. */
export function blockPlace(d: ToolcallData | null): "top" | "bottom" | "none" {
  if (!d) return "none";
  return d.variant === "B" ? "top" : "bottom";
}

export const pct = (rate: number): string => `${(rate * 100).toFixed(1)}%`;
export const rangeText = (s: ToolScore): string => `${(s.lo * 100).toFixed(1)} to ${(s.hi * 100).toFixed(1)}%`;
export const scoreText = (s: ToolScore): string => `${s.agree} of ${s.n} · ${pct(s.rate)}`;
