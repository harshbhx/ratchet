// "Watch Nano learn": the story as data and a small state machine. Nine steps, about 80 seconds; the page scrolls to each section and
// the two act it out, with the caption in a bar. Every caption is held to the site's facts (the numbers come from exams.json).
// Pure: tests/companions.test.mjs.
import type { Facts } from "./banter";
import type { Look } from "./companion-model";
import type { LessonId } from "./lessons";

export interface StoryStep {
  id: string;
  /** The element to scroll to (`null`: the top of the page). */
  target: string | null;
  ms: number;
  caption: string;
  nano: Partial<Look>;
  ultra: Partial<Look>;
  lesson?: LessonId;
}

export function storySteps(f: Facts): StoryStep[] {
  return [
    {
      id: "hello",
      target: null,
      ms: 6000,
      caption: "Meet Nano, the student, and Ultra, the teacher. Pull up a chair.",
      nano: { expr: "wave" },
      ultra: { expr: "happy" },
    },
    {
      id: "rent",
      target: "rent",
      ms: 9000,
      caption: "Most AI features rent a big model: every request billed per token, for as long as you use it.",
      nano: { expr: "thinking", pop: "?" },
      ultra: { expr: "idle", pose: "pointLeft" },
      lesson: "rent",
    },
    {
      id: "cascade",
      target: "how",
      ms: 10000,
      caption: "Ratchet changes the order. The small adapter looks first; when it is unsure, the big teacher answers.",
      nano: { expr: "determined", held: "card", pose: "carry" },
      ultra: { expr: "idle", pose: "pointLeft" },
      lesson: "cascade",
    },
    {
      id: "night",
      target: "learn",
      ms: 10000,
      caption: "Night School is the plan: the teacher's answers become the student's lessons. Built and tested, not yet run on live traffic.",
      nano: { expr: "happy", held: "book", pose: "carry", lamp: true },
      ultra: { expr: "idle", held: "papers", pose: "carry" },
      lesson: "night",
    },
    {
      id: "exam",
      target: "real-run",
      ms: 10000,
      caption: `Then the exam: ${f.r2e1} and ${f.r2e2} on two text-to-SQL tests. The untrained base managed ${f.base1} and ${f.base2}.`,
      nano: { expr: "proud", pop: "sparkle", blush: true },
      ultra: { expr: "happy" },
      lesson: "exam",
    },
    {
      id: "gate",
      target: "safety",
      ms: 9000,
      caption: "Good scores are not a ticket. A new adapter must pass the gate first: shadow, canary, rollback. For now: not yet.",
      nano: { expr: "sad", sweat: true },
      ultra: { expr: "determined", pose: "stop" },
      lesson: "gate",
    },
    {
      id: "retrain",
      target: "stack",
      ms: 9000,
      caption: "So Nano retrained on Nebius AI Cloud: by hand, one spot L40S, the same recipe.",
      nano: { expr: "determined", pose: "pointLeft" },
      ultra: { expr: "idle" },
    },
    {
      id: "endpoint",
      target: "nebius",
      ms: 9000,
      caption: "Then it was served from a Nebius endpoint, by hand, for speed tests, and deleted. Live requests still go to Ultra.",
      nano: { expr: "happy" },
      ultra: { expr: "idle", pose: "pointLeft" },
    },
    {
      id: "adapter",
      target: "integrate",
      ms: 9000,
      caption: "What you keep is an adapter and its signed lineage. Change one line, base_url, and you are in.",
      nano: { expr: "wave", pop: "sparkle" },
      ultra: { expr: "wave" },
      lesson: "adapter",
    },
  ];
}

export const storyDurationMs = (steps: readonly StoryStep[]): number => steps.reduce((n, s) => n + s.ms, 0);

export interface StoryState {
  status: "idle" | "playing" | "paused" | "done";
  index: number;
}
export type StoryAction = { type: "start" } | { type: "pause" } | { type: "resume" } | { type: "next" } | { type: "prev" } | { type: "exit" };

export const STORY_IDLE: StoryState = { status: "idle", index: 0 };

export function storyReducer(s: StoryState, a: StoryAction, length: number): StoryState {
  switch (a.type) {
    case "start":
      return { status: "playing", index: 0 };
    case "pause":
      return s.status === "playing" ? { ...s, status: "paused" } : s;
    case "resume":
      return s.status === "paused" ? { ...s, status: "playing" } : s;
    case "next":
      if (s.status === "idle") return s;
      return s.index >= length - 1 ? { status: "done", index: length - 1 } : { ...s, index: s.index + 1, status: s.status === "done" ? "done" : s.status };
    case "prev":
      return s.status === "idle" ? s : { status: s.status === "done" ? "playing" : s.status, index: Math.max(0, s.index - 1) };
    case "exit":
      return STORY_IDLE;
  }
}
