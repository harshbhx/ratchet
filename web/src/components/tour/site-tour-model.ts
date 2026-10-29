// The 60-second tour's steps and what an avatar says, as data and pure functions (no DOM, no JSX): `tests/site-tour-model.test.mjs`.
// The numbers in the exam step come from exams.json through the caller; nothing is typed here. Wording is held to the site's own:
// Ratchet's own adapter is not connected to the live gateway (a random 40% of eligible requests go to the hosted Nano 30B), and training on live traffic has not run.
import type { Expression } from "@/components/avatar/avatar-model";

export type StepPath = "/" | "/live" | "any";

export interface SiteStep {
  id: string;
  /** The page the step lives on; `any` steps use elements every page has (header, footer). */
  path: StepPath;
  /** The element to highlight; a step whose element is missing or hidden at this width is skipped. Empty: a centred card. */
  target: string;
  title: string;
  /** Nano's expression and, where it speaks, the line (also in the popover text). */
  mood: Expression;
}

export const SITE_STEPS: readonly SiteStep[] = [
  { id: "hero", path: "/", target: "#hero-title", title: "Ratchet in one line", mood: "wave" },
  { id: "try-live", path: "/", target: '[data-tour="try-live"]', title: "Try it live", mood: "happy" },
  { id: "send", path: "/live", target: '[data-tour="live-request"]', title: "Try a real request", mood: "idle" },
  { id: "answer", path: "/live", target: '[data-tour="live-who"]', title: "Who answered", mood: "happy" },
  { id: "split", path: "/live", target: '[data-tour="live-strip"]', title: "What “Random split” means", mood: "idle" },
  { id: "scores", path: "any", target: '[data-tour="check-numbers"]', title: "The real results", mood: "proud" },
  { id: "nebius", path: "any", target: '[data-tour="built-on"]', title: "What runs on Nebius", mood: "idle" },
  { id: "theme", path: "any", target: '[data-tour="theme"]', title: "Light or dark", mood: "idle" },
  { id: "done", path: "any", target: "", title: "That is the tour", mood: "wave" },
];

export const SEND_INDEX = 2;
export const ANSWER_INDEX = 3;
export const SPLIT_INDEX = 4;

export interface ExamNumbers {
  exam1: { adapter: string; base: string };
  exam2: { adapter: string; base: string };
}

/** The body of a step. `exams` are the printed scores from exams.json; `who` is the badge text the visitor's own answer carries. */
export function stepBody(id: string, ctx: { exams: ExamNumbers; who?: string | null }): string {
  switch (id) {
    case "hero":
      return "An OpenAI-compatible gateway. A Nemotron adapter you own (LoRA on NVIDIA's open Nano base) answers what it is sure of; Nemotron 3 Ultra answers the rest and teaches the next adapter.";
    case "try-live":
      return "Press it, or press Next. It opens the live gateway in this same tab, and the tour comes with you.";
    case "send":
      return "Keep the question or type your own, then press Send once. The tour waits for you.";
    case "answer":
      return ctx.who
        ? `The badge says who answered your call: “${ctx.who}”. The reply also carries a ratchet block with its cost, and the dashboard adds those up.`
        : "The badge says who answered a call, and the reply carries a ratchet block with its cost.";
    case "split":
      return "A call can be labelled “Random split”: it was picked at random for the hosted Nano, not by the confidence gate. The label shows on badges, in Scout's totals and in the dashboard, so a sampled answer is never taken for a chosen one.";
    case "scores": {
      const { exam1, exam2 } = ctx.exams;
      return `Exam scores for the round 2 adapter: ${exam1.adapter} and ${exam2.adapter} on the two text-to-SQL exams; the untrained base scores ${exam1.base} and ${exam2.base}. These are exam results, not live traffic: the student does not serve this site's calls yet. The adapter, the data and every answer are public.`;
    }
    case "nebius":
      return "Built on NVIDIA Nemotron and Nebius: every model call on the live site runs on Nebius Token Factory, and the adapter was retrained on Nebius AI Cloud.";
    case "theme":
      return "The new theme spreads from the button. Try it; the tour waits.";
    default:
      return "";
  }
}

/** What each avatar says. `null`: it stays quiet. */
export interface Lines {
  nano: string | null;
  ultra: string | null;
}

export type SendOutcome = "waiting" | "sending" | "answer" | "limit" | "error";

/** Nano's and Ultra's lines while the visitor sends a request. Ultra appears only once an answer has arrived from the teacher. */
export function sendLines(outcome: SendOutcome, servedBy: string | null, live: boolean): Lines {
  switch (outcome) {
    case "waiting":
      return { nano: "Your turn. Send it when you are ready.", ultra: null };
    case "sending":
      return { nano: "Thinking... the call is on its way.", ultra: null };
    case "answer":
      if (servedBy !== "teacher") return { nano: "An answer came back.", ultra: null };
      // Mock models make no Token Factory call, so Ultra says only what is true of them.
      return live
        ? { nano: "I am taking notes for the next adapter. Training on live traffic has not run yet.", ultra: "I answered this one, from Nebius Token Factory." }
        : { nano: "These are mock models, so nothing left your machine.", ultra: "I answered this one, as a mock." };
    case "limit":
      return { nano: "Back tomorrow at 00:00 UTC.", ultra: null };
    case "error":
      return { nano: "That one did not go through. It happens; carry on, nothing else needs it.", ultra: null };
  }
}

/** Nano's expression for each outcome. */
export function sendMood(outcome: SendOutcome): Expression {
  return outcome === "sending" ? "thinking" : outcome === "answer" ? "happy" : outcome === "limit" ? "sleepy" : outcome === "error" ? "surprised" : "idle";
}

/** The next step to show from `from` going `dir`, over steps for which `available` is true; null past either end. */
export function nextAvailable(count: number, from: number, dir: 1 | -1, available: (i: number) => boolean): number | null {
  for (let i = from + dir; i >= 0 && i < count; i += dir) if (available(i)) return i;
  return null;
}

/** "3 / 9". */
export function progressText(index: number, total: number): string {
  return `${index + 1} / ${total}`;
}

/** Does this query ask for the site tour (`?tour=1`)? Only on the pages the site tour covers. */
export function wantsSiteTour(pathname: string, search: string): boolean {
  const p = pathname.replace(/\/+$/, "") || "/";
  if (p !== "/" && p !== "/live") return false;
  const q = search.startsWith("?") ? search.slice(1) : search;
  return q.split("&").some((x) => x === "tour=1" || x === "tour=true");
}
