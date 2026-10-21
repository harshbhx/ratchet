// Recorded live Scout runs (FR-10.40, docs/SCOUT.md#recorded-runs): the schedule a recording plays at, "Skip to the end",
// and the words that say it is a recording. Pure: the timers are passed in, so tests run without waiting.
import type { Mode, ScoutEvent } from "@/lib/api";

export interface RecordingSummary {
  id: string;
  recorded_at: string;
  question: string;
  duration_ms: number;
  mode: Mode;
}
export interface Recording extends RecordingSummary {
  events: (ScoutEvent & { t_ms: number })[];
}

/** The newest recording of a live run: the one the "Watch a recorded live run" button plays. */
export function pickLive(list: RecordingSummary[] | null | undefined): RecordingSummary | null {
  return (list ?? []).find((r) => r.mode === "live") ?? null;
}

/** The events with the delay (ms) from the start of playback at which each is delivered: the recorded offsets. */
export function schedule(rec: Pick<Recording, "events">): { at: number; ev: ScoutEvent }[] {
  let last = 0;
  return rec.events.map((e) => {
    const { t_ms, ...ev } = e;
    last = Math.max(last, Number.isFinite(t_ms) ? Math.max(0, t_ms) : last);
    return { at: last, ev: ev as ScoutEvent };
  });
}

/** "Recorded live run · 21 Oct 2026 10:00 UTC", or "Recorded simulation · ..." for a mock-mode recording. */
export function recordingLabel(rec: Pick<RecordingSummary, "mode" | "recorded_at">): string {
  const d = new Date(rec.recorded_at);
  const when = Number.isNaN(d.getTime())
    ? rec.recorded_at
    : `${d.getUTCDate()} ${["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][d.getUTCMonth()]} ${d.getUTCFullYear()} ${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")} UTC`;
  return `${rec.mode === "mock" ? "Recorded simulation" : "Recorded live run"} · ${when}`;
}

/** "Watch a recorded live run (42 s)". */
export function watchLabel(rec: Pick<RecordingSummary, "mode" | "duration_ms">): string {
  const s = Math.max(1, Math.round(rec.duration_ms / 1000));
  return `${rec.mode === "mock" ? "Watch a recorded simulation" : "Watch a recorded live run"} (${s} s)`;
}

export interface Timers {
  set: (fn: () => void, ms: number) => unknown;
  clear: (h: unknown) => void;
}

export interface Playback {
  /** Deliver every event not yet delivered, in order, now. */
  skip: () => void;
  cancel: () => void;
}

/** Deliver each event at its recorded offset. `onEnd` runs once, after the last event, also after `skip`. */
export function play(rec: Pick<Recording, "events">, deliver: (ev: ScoutEvent) => void, timers: Timers, onEnd?: () => void): Playback {
  const plan = schedule(rec);
  let next = 0;
  let live = true;
  const handles: unknown[] = [];
  const step = () => {
    if (!live || next >= plan.length) return;
    deliver(plan[next++].ev);
    if (next >= plan.length) {
      live = false;
      onEnd?.();
    }
  };
  for (const p of plan) handles.push(timers.set(step, p.at));
  return {
    skip: () => {
      handles.forEach((h) => timers.clear(h));
      while (live && next < plan.length) step();
    },
    cancel: () => {
      live = false;
      handles.forEach((h) => timers.clear(h));
    },
  };
}
