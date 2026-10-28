// The only browser storage the companions use, all of it in a try/catch, none of it personal: whether the visitor hid them
// (kept), and this visit's lessons and the sections already commented on (one browser session, and the lessons only for today).
import { SOUND_DEFAULT, soundFromStored } from "./sound-model";
import { LESSON_TOTAL, dayStamp, normalizeLessons, type LessonState } from "./lessons";

const HIDDEN_KEY = "ratchet-companions-hidden";
const SESSION_KEY = "ratchet-companions-session";
const SOUND_KEY = "ratchet-companions-sound";

export interface Session {
  lessons: LessonState;
  /** Sections the characters already commented on this visit. */
  seen: string[];
  graduated: boolean;
}

export function readHidden(): boolean {
  try {
    return localStorage.getItem(HIDDEN_KEY) === "1";
  } catch {
    return false;
  }
}

export function writeHidden(hidden: boolean): void {
  try {
    if (hidden) localStorage.setItem(HIDDEN_KEY, "1");
    else localStorage.removeItem(HIDDEN_KEY);
  } catch {
    /* storage blocked */
  }
}

/** Sound is opt-in: off unless the visitor turned it on (remembered). */
export function readSound(): boolean {
  try {
    return soundFromStored(localStorage.getItem(SOUND_KEY));
  } catch {
    return SOUND_DEFAULT;
  }
}

export function writeSound(on: boolean): void {
  try {
    if (on) localStorage.setItem(SOUND_KEY, "1");
    else localStorage.removeItem(SOUND_KEY);
  } catch {
    /* storage blocked */
  }
}

export function readSession(now = new Date()): Session {
  const day = dayStamp(now);
  try {
    const raw: unknown = JSON.parse(sessionStorage.getItem(SESSION_KEY) ?? "null");
    const r = (raw && typeof raw === "object" ? raw : {}) as { lessons?: unknown; seen?: unknown; graduated?: unknown };
    const lessons = normalizeLessons(r.lessons, day);
    return {
      lessons,
      seen: Array.isArray(r.seen) ? r.seen.filter((x): x is string => typeof x === "string").slice(0, 40) : [],
      graduated: r.graduated === true && lessons.ids.length >= LESSON_TOTAL,
    };
  } catch {
    return { lessons: normalizeLessons(null, day), seen: [], graduated: false };
  }
}

export function writeSession(s: Session): void {
  try {
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(s));
  } catch {
    /* storage blocked */
  }
}
