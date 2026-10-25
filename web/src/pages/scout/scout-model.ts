// Pure helpers for the Scout page's example chips and its "runs left today" counter. No DOM, no storage, no network.
import type { ScoutExamples, ScoutStatus } from "@/lib/api";

/** Research questions for the chips when the gateway answers free text (live mode). Each needs a few web sources. */
export const LIVE_CHIPS = [
  "What is LoRA fine-tuning?",
  "How does knowledge distillation work?",
  "What is PagedAttention in vLLM?",
  "Why does model collapse happen?",
];

/**
 * The chips to show. Live mode (free text) gets the four curated questions. A simulated gateway answers only its own
 * examples, so it shows what it sent. No answer yet: nothing, so no chip can start a run the gateway would refuse.
 */
export function chipQuestions(examples: ScoutExamples | null): string[] {
  if (!examples) return [];
  return examples.free_text ? LIVE_CHIPS : examples.examples;
}

export interface RunsLeft {
  left: number;
  max: number;
  used: number;
}

/** Runs left today from the fields /scout/status already sends. null (so the counter hides) when either one is missing. */
export function runsLeftToday(status: Pick<ScoutStatus, "runs_today" | "max_runs_per_day"> | null | undefined): RunsLeft | null {
  if (!status) return null;
  const max = status.max_runs_per_day;
  const used = status.runs_today;
  if (typeof max !== "number" || !Number.isFinite(max) || max <= 0) return null;
  if (typeof used !== "number" || !Number.isFinite(used) || used < 0) return null;
  return { left: Math.max(0, Math.floor(max - used)), max: Math.floor(max), used: Math.floor(used) };
}

export function runsLeftText(r: RunsLeft): string {
  if (r.left === 0) return "No demo runs left today. The count resets at 00:00 UTC.";
  return `${r.left} ${r.left === 1 ? "run" : "runs"} left today`;
}
