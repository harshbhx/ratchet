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

type RunsFields = Pick<ScoutStatus, "runs_today" | "max_runs_per_day" | "public_runs_left_today" | "judge">;

const count = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) && v >= 0 ? Math.floor(v) : null);

/**
 * Runs a visitor can still start today. A gateway that sends `public_runs_left_today` (ADR 0120) says it directly: the
 * judges' reserve is excluded, so the count never promises runs a visitor cannot have; `max` is then the cap minus that
 * reserve. An older gateway: the cap minus the runs so far. null (so the counter hides) without a cap.
 */
export function runsLeftToday(status: Partial<RunsFields> | null | undefined): RunsLeft | null {
  if (!status) return null;
  const cap = count(status.max_runs_per_day);
  if (cap == null || cap <= 0) return null;
  const pub = count(status.public_runs_left_today);
  if (pub != null) {
    const reserve = count(status.judge?.enabled === false ? 0 : status.judge?.reserved_runs_per_day) ?? 0;
    const max = Math.max(pub, cap - reserve);
    return { left: pub, max, used: max - pub };
  }
  const used = count(status.runs_today);
  if (used == null) return null;
  return { left: Math.max(0, cap - used), max: cap, used };
}

export interface BudgetLeft {
  /** US dollars a visitor's runs can still spend today. */
  left: number;
  /** The whole daily budget, judges' reserve included. */
  total: number;
  /** left / total, between 0 and 1. */
  share: number;
}

/**
 * The live demo's budget a visitor can still use today: `public_budget_left_usd` when the gateway sends it (judges'
 * reserve excluded), else the budget minus today's spend. null in mock mode or without a budget.
 */
export function budgetLeft(
  status: Partial<Pick<ScoutStatus, "mode" | "budget_usd" | "spent_today_usd" | "public_budget_left_usd">> | null | undefined,
): BudgetLeft | null {
  if (!status || status.mode !== "live") return null;
  const total = status.budget_usd;
  if (typeof total !== "number" || !Number.isFinite(total) || total <= 0) return null;
  const pub = status.public_budget_left_usd;
  const spent = typeof status.spent_today_usd === "number" && Number.isFinite(status.spent_today_usd) ? status.spent_today_usd : 0;
  const raw = typeof pub === "number" && Number.isFinite(pub) ? pub : total - spent;
  const left = Math.min(total, Math.max(0, raw));
  return { left, total, share: left / total };
}

export function runsLeftText(r: RunsLeft): string {
  if (r.left === 0) return "No demo runs left today. The count resets at 00:00 UTC.";
  return `${r.left} ${r.left === 1 ? "run" : "runs"} left today`;
}

// ---------- the edge's daily limit per visitor, and the recorded runs offered instead

/** The edge refuses a fourth Scout run of a day with `{"detail": "Daily limit reached: 3 Scout runs per visitor per day. ..."}`. */
export function isScoutDailyLimit(message: string | null | undefined): boolean {
  return typeof message === "string" && message.trimStart().startsWith("Daily limit reached");
}

export const SCOUT_LIMIT_TITLE = "You've used your 3 free Scout runs today.";
export const SCOUT_LIMIT_TEXT = "They reset at 00:00 UTC. Meanwhile, watch a recorded run:";

/** The recordings to offer: live ones first, each group in the gateway's order (newest first). */
export function recordingChoices<T extends { mode: string }>(list: readonly T[] | null | undefined): T[] {
  const all = list ?? [];
  return [...all.filter((r) => r.mode === "live"), ...all.filter((r) => r.mode !== "live")];
}
