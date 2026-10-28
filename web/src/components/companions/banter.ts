// What Nano and Ultra say on their own. Every line is held to the site's facts: the numbers come from exams.json through
// `factsFromExams`, the rest is wording the site and AGENTS.md already use. Nano is the eager, cheeky student; Ultra is the calm,
// dry teacher. Pure: tests/companions.test.mjs.
import { pct, type ExamsData } from "@/lib/exams-model";
import type { Expr, Who } from "./companion-model";

export interface Facts {
  r2e1: string;
  r2e2: string;
  base1: string;
  base2: string;
  r1rows: string;
  r2rows: string;
}

/** The exam numbers, as the page prints them. */
export function factsFromExams(d: ExamsData): Facts {
  const rows = (id: string) => /\(([\d,]+) rows\)/.exec(d.models.find((m) => m.id === id)?.label ?? "")?.[1] ?? "";
  return {
    r2e1: pct(d.scores.exam1.r2.rate),
    r2e2: pct(d.scores.exam2.r2.rate),
    base1: pct(d.scores.exam1.base.rate),
    base2: pct(d.scores.exam2.base.rate),
    r1rows: rows("r1"),
    r2rows: rows("r2"),
  };
}

export interface Line {
  who: Who;
  text: string;
  /** The speaker's face while the line is up. */
  expr?: Expr;
}
export interface Banter {
  id: string;
  lines: Line[];
  when?: "night" | "day";
}

const N = (text: string, expr?: Expr): Line => ({ who: "nano", text, expr });
const U = (text: string, expr?: Expr): Line => ({ who: "ultra", text, expr });

export function banterLines(f: Facts): Banter[] {
  return [
    { id: "notes", lines: [U("I'll answer. You take notes."), N("I always take notes. That's literally my job.", "happy")] },
    { id: "roles", lines: [N("I'm the adapter you'd own.", "idle"), U("I'm the teacher you rent. We balance out.")] },
    { id: "base", lines: [N("My base is NVIDIA's open Nemotron Nano.", "idle"), U("The adapter is a small LoRA on top. Modest, like you.")] },
    { id: "scores", lines: [N(`Exams: ${f.r2e1} and ${f.r2e2}. Untrained base: ${f.base1}, ${f.base2}.`, "proud"), U("Text-to-SQL only. Don't frame it yet.")] },
    { id: "sqlonly", lines: [U("Those exams are SQL. Only SQL."), N("Then I'll be the best at SQL. Specifically.", "determined")] },
    { id: "exam2", lines: [N("Exam 2 was written after I trained.", "surprised"), U("By a different lab's model. Hard to peek at a test that didn't exist.")] },
    { id: "rows", lines: [N(`Round one: ${f.r1rows} examples. Round two: ${f.r2rows}.`, "thinking"), U("More homework, better grades. Education works.")] },
    { id: "gpu", lines: [N("I first trained on a borrowed GPU.", "idle"), U("Returned in good condition, I hope.")] },
    { id: "nebius-train", lines: [N("Then I retrained on Nebius AI Cloud!", "happy"), U("By hand, on one spot L40S. Very rustic.")] },
    { id: "gate", lines: [U("No release skips the gate."), N("Shadow, canary, rollback. I get it. I just resent it.", "sad")] },
    { id: "gate2", lines: [N("Can I serve yet?", "happy"), U("The gate says not yet. The gate is strict. That's its job.")] },
    { id: "lineage", lines: [N("My training data comes with a signed record.", "proud"), U("Receipts. We like receipts here.")] },
    { id: "license", lines: [U("Closed models are refused as teachers."), N("And you're open. Lucky for you.", "happy")] },
    { id: "live", lines: [N("Right now Ultra answers every live request.", "sad"), U("Yes. You're still in training. It suits you.")] },
    { id: "nightschool", lines: [N("Night School turns Ultra's answers into lessons.", "happy"), U("Built and tested on mock models. Not run on live traffic yet.")] },
    { id: "oneline", lines: [N("One line changes: base_url.", "determined"), U("Even you could not get that wrong.")] },
    { id: "try", lines: [U("Three real tries a day on the live page."), N("No key, no account. Go on, poke the gateway!", "happy")] },
    { id: "stored", lines: [N("Your text in the Try box isn't stored.", "idle"), U("Which keeps our notes very short.")] },
    { id: "cloudflare", lines: [U("This site sits on Cloudflare."), N("And Ultra's brain runs on Nebius. Division of labour!", "proud")] },
    { id: "tokenfactory", lines: [N("Ultra runs on Nebius Token Factory.", "idle"), U("It's a nice factory. Very punctual.")] },
    { id: "endpoint", lines: [N("They served me from a Nebius endpoint once.", "surprised"), U("By hand, for speed tests. Then it was deleted.")] },
    { id: "cold", lines: [U("Cold start on that endpoint: about seven minutes."), N("I was stretching. Warm-up is important.", "happy")] },
    { id: "speed", lines: [N("Sixteen users at once: 1.6 s median for SQL!", "proud"), U("Speed, not accuracy. We didn't score it there.")] },
    { id: "synthetic", lines: [U("All the training data is synthetic."), N("So I've never met a real customer. Hello, you!", "wave")] },
    { id: "adapter", lines: [N("You'd keep an adapter and its signed lineage.", "idle"), U("Not a model. Read the small print. I did.")] },
    { id: "scout", lines: [N("Scout lets you watch each call get routed.", "thinking"), U("Like watching traffic, but educational.")] },
    { id: "dashboard", lines: [U("The dashboard shows releases, costs, rollbacks."), N("Rollbacks. My least favourite word.", "sad")] },
    { id: "numbers", lines: [N("Every number here is live, measured or linked.", "proud"), U("No invented figures. We checked. Twice.")] },
    { id: "cheeky", lines: [N("Ask me anything!", "happy"), U("Ask him something small. He's still learning.")] },
    { id: "poke", lines: [N("Poke me. I dare you.", "happy"), U("He means it. I've counted.")] },
    { id: "drag", lines: [U("You can pick us up, you know."), N("Gently, though. I'm a student, not luggage!", "surprised")] },
    { id: "glasses", lines: [N("Ultra, why the round glasses?", "thinking"), U("They make the answers look rounder.")] },
    { id: "mug", lines: [U("Coffee is a teaching aid."), N("Is it? Can I have some?", "happy"), U("No.")] },
    { id: "chip", lines: [N("This orange chip is my adapter.", "proud"), U("It's a good chip. Don't lose it.")] },
    { id: "famous", lines: [N("Are we famous yet?", "happy"), U("We're accurate yet. Start with that.")] },
    { id: "night-lamp", when: "night", lines: [N("Lamp on, book open. Night School!", "determined"), U("I'm marking papers. Quietly.")] },
    { id: "night-red", when: "night", lines: [U("Red pen is out. Nobody panic."), N("Panicking a little.", "surprised")] },
    { id: "night-sleep", when: "night", lines: [N("Is it bedtime yet?", "sleepy"), U("It's study time. Bedtime is a reward.")] },
    { id: "day-bright", when: "day", lines: [N("So bright out! I need shades.", "surprised"), U("Dark mode has a pair for you.")] },
    { id: "day-nap", when: "day", lines: [N("Daytime is for traffic. Night is for homework.", "thinking"), U("That's the design. Built and tested, not live yet.")] },
  ];
}

/** A shuffled bag: every item once before any repeats, and a new bag never starts with the item that ended the last. */
export function createPicker<T>(items: readonly T[], rng: () => number = Math.random): { next(): T | undefined; remaining(): number } {
  let bag: T[] = [];
  let last: T | undefined;
  const fill = () => {
    bag = items.slice();
    for (let i = bag.length - 1; i > 0; i--) {
      const j = Math.floor(rng() * (i + 1));
      [bag[i], bag[j]] = [bag[j]!, bag[i]!];
    }
    // next() pops from the end: make sure that is not the item that ended the previous bag.
    if (bag.length > 1 && bag[bag.length - 1] === last) [bag[0], bag[bag.length - 1]] = [bag[bag.length - 1]!, bag[0]!];
  };
  return {
    next() {
      if (!items.length) return undefined;
      if (!bag.length) fill();
      last = bag.pop();
      return last;
    },
    remaining: () => bag.length,
  };
}

/** The wait before the next exchange: 25 to 40 seconds. */
export function nextDelayMs(rng: () => number = Math.random): number {
  return 25_000 + Math.floor(rng() * 15_000);
}

/** How long a bubble stays up: a floor, plus a little per word, so a line is readable. */
export function readMs(text: string): number {
  const words = text.trim().split(/\s+/).length;
  return Math.min(7000, 1800 + words * 330);
}
