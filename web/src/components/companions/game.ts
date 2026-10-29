// The routing picture in the companion sheet: a question card goes to Nano, who thinks (three little thought bubbles) and, if unsure,
// passes it to Ultra. It is a picture of how Ratchet routes a request. It shows no model output, and it makes no call. Pure.

export type GamePhase = "idle" | "catch" | "try1" | "try2" | "try3" | "unsure" | "throw" | "answer";

export const GAME_STEPS: ReadonlyArray<{ phase: Exclude<GamePhase, "idle">; ms: number; caption: string }> = [
  { phase: "catch", ms: 1100, caption: "Nano catches the question." },
  { phase: "try1", ms: 1000, caption: "Nano thinks. Try 1 of 3." },
  { phase: "try2", ms: 1000, caption: "Nano thinks again. Try 2 of 3." },
  { phase: "try3", ms: 1000, caption: "One last time. Try 3 of 3." },
  { phase: "unsure", ms: 1100, caption: "Still not sure." },
  { phase: "throw", ms: 1100, caption: "Nano passes the card to Ultra." },
  { phase: "answer", ms: 0, caption: "Ultra takes it from here." },
];

export function nextPhase(p: GamePhase): GamePhase {
  if (p === "idle") return GAME_STEPS[0]!.phase;
  const i = GAME_STEPS.findIndex((s) => s.phase === p);
  return GAME_STEPS[Math.min(GAME_STEPS.length - 1, i + 1)]!.phase;
}

export const phaseMs = (p: GamePhase): number => GAME_STEPS.find((s) => s.phase === p)?.ms ?? 0;
export const phaseCaption = (p: GamePhase): string => GAME_STEPS.find((s) => s.phase === p)?.caption ?? "";
export const thoughtBubbles = (p: GamePhase): number => (p === "try1" ? 1 : p === "try2" ? 2 : p === "try3" || p === "unsure" ? 3 : 0);

export const MAX_QUESTION = 120;

/** The visitor's text, tidied: one line, trimmed, at most MAX_QUESTION characters. */
export function cleanQuestion(raw: string): string {
  return raw.replace(/\s+/g, " ").trim().slice(0, MAX_QUESTION);
}
