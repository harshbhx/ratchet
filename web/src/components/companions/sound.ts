// Nano and Ultra's sounds, made with the Web Audio API and nothing else (no audio files, no library). Off by default and opt-in: the
// audio context is only created when the visitor turns sound on (a click, which is the user gesture), or, if they turned it on on an
// earlier visit, at their first click or key press on this one. Muted while the tab is hidden. Quiet by design.
import type { Who } from "./companion-model";
import { CUES, blipPlan, type Cue, type Note } from "./sound-model";

type Ctx = AudioContext;
let ctx: Ctx | null = null;
let on = false;

function make(): Ctx | null {
  if (ctx) return ctx;
  const Ctor = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!Ctor) return null;
  try {
    ctx = new Ctor();
  } catch {
    ctx = null;
  }
  return ctx;
}

/** Turn sound on or off. Turning it on, from a click, creates the audio context. */
export function setSoundOn(next: boolean, fromGesture = false): void {
  on = next;
  if (next && fromGesture) void make()?.resume();
}

/** Sound was on last time: wait for the first user gesture before touching audio, and then create the context. */
export function armSoundForNextGesture(): () => void {
  const go = () => {
    if (on) void make()?.resume();
  };
  window.addEventListener("pointerdown", go, { once: true });
  window.addEventListener("keydown", go, { once: true });
  return () => {
    window.removeEventListener("pointerdown", go);
    window.removeEventListener("keydown", go);
  };
}

function note(c: Ctx, n: Note, at: number): void {
  const osc = c.createOscillator();
  const gain = c.createGain();
  osc.type = n.type;
  osc.frequency.setValueAtTime(n.f, at);
  if (n.to) osc.frequency.exponentialRampToValueAtTime(n.to, at + n.d);
  gain.gain.setValueAtTime(0.0001, at);
  gain.gain.exponentialRampToValueAtTime(n.g, at + Math.min(0.012, n.d / 3));
  gain.gain.exponentialRampToValueAtTime(0.0001, at + n.d);
  osc.connect(gain).connect(c.destination);
  osc.start(at);
  osc.stop(at + n.d + 0.02);
}

function ready(): Ctx | null {
  if (!on || !ctx || document.hidden || ctx.state === "closed") return null;
  if (ctx.state === "suspended") void ctx.resume();
  return ctx;
}

/** A short cue: a pop, a boop, a chime, a jingle or a tick. */
export function play(cue: Cue): void {
  const c = ready();
  if (!c) return;
  const t0 = c.currentTime + 0.01;
  for (const n of CUES[cue]) note(c, n, t0 + n.t);
}

/** Voice blips for a line of speech: one syllable per word, Nano high and quick, Ultra low and slow; one blip under reduced motion. */
export function speak(text: string, who: Who, reduced: boolean): void {
  const c = ready();
  if (!c) return;
  const t0 = c.currentTime + 0.02;
  for (const b of blipPlan(text, who, reduced).blips) {
    note(c, { t: b.t, f: b.f, d: b.d, type: who === "nano" ? "triangle" : "sine", g: 0.035, to: b.f * 0.9 }, t0 + b.t);
  }
}
