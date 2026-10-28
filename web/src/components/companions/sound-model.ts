// The sounds, as plans (no Web Audio here, so a test can import them): the cues, and the voice blips for a line of speech. Sound is
// opt-in and off by default; the plans are small on purpose (quiet, short, capped), and under reduced motion a line gets one blip.
// Pure: tests/companions.test.mjs.
import type { Who } from "./companion-model";

export const SOUND_DEFAULT = false;
/** Only the stored value "1" means on. Anything else, or nothing, is off. */
export function soundFromStored(v: string | null | undefined): boolean {
  return v === "1";
}

export type Cue = "pop" | "boop" | "chime" | "jingle" | "tick";
export interface Note {
  /** Seconds after the cue starts. */
  t: number;
  /** Hertz. */
  f: number;
  /** Seconds. */
  d: number;
  type: "sine" | "triangle" | "square";
  /** Peak gain: low everywhere. */
  g: number;
  /** Hertz the pitch glides to by the end, if it does. */
  to?: number;
}

export const CUES: Record<Cue, Note[]> = {
  pop: [{ t: 0, f: 560, d: 0.07, type: "sine", g: 0.07, to: 320 }],
  boop: [{ t: 0, f: 230, d: 0.11, type: "triangle", g: 0.07, to: 340 }],
  chime: [
    { t: 0, f: 880, d: 0.14, type: "sine", g: 0.05 },
    { t: 0.1, f: 1318, d: 0.2, type: "sine", g: 0.05 },
  ],
  jingle: [
    { t: 0, f: 523, d: 0.12, type: "triangle", g: 0.05 },
    { t: 0.12, f: 659, d: 0.12, type: "triangle", g: 0.05 },
    { t: 0.24, f: 784, d: 0.12, type: "triangle", g: 0.05 },
    { t: 0.36, f: 1047, d: 0.28, type: "triangle", g: 0.055 },
  ],
  tick: [{ t: 0, f: 1800, d: 0.035, type: "square", g: 0.025 }],
};

/** The longest a line's blips may last, in seconds. */
export const BLIP_MAX_S = 1.3;
export const BLIP_MAX_COUNT = 9;

export interface Blip {
  t: number;
  f: number;
  d: number;
}

/**
 * One short pitched syllable per word, so the blips follow the mouth: Nano is higher and quicker, Ultra lower and slower. The count and
 * the total length are capped, and with `reduced` a whole line gets one blip.
 */
export function blipPlan(text: string, who: Who, reduced: boolean): { blips: Blip[]; total: number } {
  const words = text.trim().split(/\s+/).filter(Boolean);
  const base = who === "nano" ? 760 : 230;
  const dur = who === "nano" ? 0.06 : 0.1;
  const gap = who === "nano" ? 0.1 : 0.17;
  const n = reduced ? Math.min(1, words.length) : Math.min(words.length, BLIP_MAX_COUNT, Math.floor(BLIP_MAX_S / gap));
  const blips: Blip[] = [];
  for (let i = 0; i < n; i++) {
    const w = words[i]!;
    // The pitch follows the word: longer words dip, a final word rises. Deterministic, so a line always sounds the same.
    const seed = w.charCodeAt(0) + w.length * 7;
    const f = base * (1 + ((seed % 7) - 3) * 0.045) * (i === n - 1 && /[?]$/.test(text.trim()) ? 1.18 : 1);
    blips.push({ t: i * gap, f: Math.round(f), d: dur });
  }
  const total = blips.length ? blips[blips.length - 1]!.t + dur : 0;
  return { blips, total };
}
