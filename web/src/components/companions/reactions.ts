// What the characters do when poked, when a section comes into view, and when they play with each other. Fresh wording, held to the
// site's facts (see banter.ts). Pure: tests/companions.test.mjs.
import type { Expr, Look, Pose, Who } from "./companion-model";
import type { Facts, Line } from "./banter";

export interface Poke {
  kind: "giggle" | "blush" | "startle" | "think" | "glasses" | "deadpan" | "sip";
  say: string;
  look: Partial<Look>;
}

const NANO_POKES: Poke[] = [
  { kind: "giggle", say: "Hehe! That tickles.", look: { expr: "laughing" } },
  { kind: "giggle", say: "Again, again!", look: { expr: "laughing", pop: "sparkle" } },
  { kind: "blush", say: "Oh! Hi. Hello.", look: { expr: "happy", blush: true, pop: "heart" } },
  { kind: "blush", say: "Stop, I'm shy!", look: { expr: "happy", blush: true } },
  { kind: "startle", say: "Eep! Who goes there?", look: { expr: "surprised", pop: "!", sweat: true } },
  { kind: "think", say: "Is this on the exam?", look: { expr: "thinking", pop: "?" } },
];

const ULTRA_POKES: Poke[] = [
  { kind: "glasses", say: "Noted.", look: { expr: "idle", adjust: true } },
  { kind: "glasses", say: "Yes? I'm teaching.", look: { expr: "determined", adjust: true } },
  { kind: "deadpan", say: "I felt that. Emotionally.", look: { expr: "sad" } },
  { kind: "sip", say: "Not now. Coffee.", look: { expr: "sleepy", pop: "none" } },
  { kind: "blush", say: "Unnecessary. Mildly pleasant.", look: { expr: "happy", blush: true } },
];

/** A random reaction for one character, never the same kind twice in a row. */
export function pokeReaction(who: Who, rng: () => number = Math.random, lastKind?: Poke["kind"]): Poke {
  const pool = who === "nano" ? NANO_POKES : ULTRA_POKES;
  const options = pool.filter((p) => p.kind !== lastKind);
  return (options.length ? options : pool)[Math.floor(rng() * (options.length ? options.length : pool.length))]!;
}

/** Five quick pokes: Nano spins, then Ultra asks for focus. */
export const EASTER_EGG: Line[] = [
  { who: "nano", text: "Wheeeee!", expr: "laughing" },
  { who: "ultra", text: "Focus.", expr: "determined" },
];

export type DuoMove = "highfive" | "pat" | "pass" | "hide" | "sneak" | "levelup" | "fistbump" | "correct";
export const DUO_MOVES: DuoMove[] = ["highfive", "pat", "pass", "hide", "sneak", "levelup", "fistbump", "correct"];
/** The calm ones, for the occasional unprompted moment. */
export const CALM_DUOS: DuoMove[] = ["highfive", "fistbump", "pat", "pass", "levelup", "correct"];

/** Moves the two make with each other, and what they say. */
export const DUO_LINES: Record<DuoMove, Line[]> = {
  highfive: [
    { who: "nano", text: "High five!", expr: "happy" },
    { who: "ultra", text: "Fine. Once.", expr: "happy" },
  ],
  pat: [
    { who: "ultra", text: "Good work.", expr: "happy" },
    { who: "nano", text: "Did he just pat my head? Best day ever.", expr: "proud" },
  ],
  pass: [
    { who: "ultra", text: "Here's a question.", expr: "idle" },
    { who: "nano", text: "Got it. Thinking...", expr: "thinking" },
  ],
  hide: [
    { who: "nano", text: "Too hard! Hiding!", expr: "surprised" },
    { who: "ultra", text: "Stay behind me.", expr: "determined" },
  ],
  sneak: [
    { who: "nano", text: "Psst. Just a peek at the answer?", expr: "happy" },
    { who: "ultra", text: "Not yet.", expr: "determined" },
  ],
  levelup: [
    { who: "ultra", text: "Lesson complete. Here is your card.", expr: "happy" },
    { who: "nano", text: "Level up! Did my chip just glow?", expr: "proud" },
  ],
  // A guess, a correction, a second try: how a student learns from a teacher, kept light.
  correct: [
    { who: "nano", text: "Is it a LEFT JOIN?", expr: "happy" },
    { who: "ultra", text: "Not quite. Check the rows.", expr: "determined" },
    { who: "nano", text: "INNER JOIN, then?", expr: "thinking" },
    { who: "ultra", text: "That matches. Noted.", expr: "happy" },
  ],
  fistbump: [
    { who: "nano", text: "Fist bump!", expr: "happy" },
    { who: "ultra", text: "Mild enthusiasm. Granted.", expr: "happy" },
  ],
};

export interface SectionReaction {
  lines: Line[];
  nano?: Partial<Look>;
  ultra?: Partial<Look>;
}

const pose = (p: Pose): Partial<Look> => ({ pose: p });
const face = (e: Expr, extra: Partial<Look> = {}): Partial<Look> => ({ expr: e, ...extra });

/** What the two do when a landing section scrolls into view, at most once per section per visit. */
export function sectionReactions(f: Facts): Record<string, SectionReaction> {
  return {
    rent: {
      lines: [
        { who: "nano", text: "Pay per token, forever? Pricey.", expr: "thinking" },
        { who: "ultra", text: "It's called rent. Landlords love it." },
      ],
      nano: face("thinking", { pop: "?" }),
    },
    how: {
      lines: [
        { who: "nano", text: "I take the first look.", expr: "determined" },
        { who: "ultra", text: "If he's unsure, I step in." },
      ],
      nano: { ...face("determined"), held: "card", pose: "carry" },
    },
    learn: {
      lines: [
        { who: "nano", text: "Night School. Homework!", expr: "happy" },
        { who: "ultra", text: "Ran once by hand on Nebius; the gate rejected it. Not run nightly on live traffic." },
      ],
      nano: { ...face("happy"), held: "book", pose: "carry" },
      ultra: { held: "papers", pose: "carry" },
    },
    "real-run": {
      lines: [
        { who: "nano", text: `${f.r2e1} and ${f.r2e2} on the exams. The untrained base: ${f.base1} and ${f.base2}.`, expr: "proud" },
        { who: "ultra", text: "Text-to-SQL. Don't get carried away.", expr: "idle" },
      ],
      nano: face("proud", { pop: "sparkle", blush: true }),
    },
    safety: {
      lines: [
        { who: "ultra", text: "Not yet.", expr: "determined" },
        { who: "nano", text: "He said it again.", expr: "sad" },
      ],
      nano: face("sad", { sweat: true }),
      ultra: { ...pose("stop"), expr: "determined" },
    },
    stack: {
      lines: [
        { who: "nano", text: "Ultra answers via Token Factory. I trained on Nebius AI Cloud.", expr: "proud" },
        { who: "ultra", text: "A fair split of labour." },
      ],
      nano: pose("pointLeft"),
    },
    nebius: {
      lines: [
        { who: "nano", text: "My endpoint cold-started in about seven minutes.", expr: "surprised" },
        { who: "ultra", text: "Plan your coffee accordingly." },
      ],
    },
    bill: {
      lines: [
        { who: "ultra", text: "The honest bill says when this doesn't pay.", expr: "idle" },
        { who: "nano", text: "Brave of it.", expr: "surprised" },
      ],
    },
    trust: {
      lines: [
        { who: "ultra", text: "Signed lineage, checkable offline." },
        { who: "nano", text: "Show your work. Always.", expr: "determined" },
      ],
    },
    integrate: {
      lines: [
        { who: "nano", text: "One line changes. I counted.", expr: "wave" },
        { who: "ultra", text: "He counted twice." },
      ],
      nano: face("wave"),
    },
    footer: {
      lines: [
        { who: "nano", text: "Thanks for reading!", expr: "wave" },
        { who: "ultra", text: "Come back at night. We study.", expr: "wave" },
      ],
      nano: face("wave"),
      ultra: face("wave"),
    },
  };
}

/** Sections the page watches, in the order they are checked. */
export const WATCHED_SECTIONS = ["rent", "how", "learn", "real-run", "safety", "stack", "nebius", "bill", "trust", "integrate"] as const;

/** At most once per section per visit. */
export function shouldReact(seen: readonly string[], id: string): boolean {
  return !seen.includes(id);
}

/** Lines for the live request flow on /live. */
export function liveReaction(phase: "sent" | "answer" | "limit" | "error"): Line[] {
  switch (phase) {
    case "sent":
      return [{ who: "nano", text: "Off it goes. Fingers crossed.", expr: "thinking" }];
    case "answer":
      return [
        { who: "ultra", text: "Answered. You're welcome.", expr: "proud" },
        { who: "nano", text: "I took notes.", expr: "happy" },
      ];
    case "limit":
      return [{ who: "nano", text: "That's today's free tries. Back at 00:00 UTC.", expr: "sleepy" }];
    case "error":
      return [{ who: "nano", text: "That one didn't go through.", expr: "surprised" }];
  }
}
