// "Nano learned N of 7 lessons from your visit": a lesson is earned by reaching a key section or doing something with the
// characters. Kept per visit (one browser session) and never across days. Pure: tests/companions.test.mjs.

export const LESSONS = [
  { id: "hello", label: "Say hello", hint: "Poke Nano or Ultra" },
  { id: "rent", label: "Every request is rent", hint: "Visit the first chapter" },
  { id: "cascade", label: "How a request is routed", hint: "Visit \"How it turns\" or play the routing picture" },
  { id: "night", label: "Night School", hint: "Visit \"Watch it learn\"" },
  { id: "exam", label: "The exam", hint: "Visit the real results" },
  { id: "gate", label: "The gate says not yet", hint: "Visit \"One way only\"" },
  { id: "adapter", label: "Your adapter", hint: "Visit the Nebius or integration sections, send a live request or watch the story" },
] as const;

export type LessonId = (typeof LESSONS)[number]["id"];
export const LESSON_TOTAL = LESSONS.length;

export interface LessonState {
  day: string;
  ids: LessonId[];
}

const KNOWN = new Set<string>(LESSONS.map((l) => l.id));

/** Local calendar day, YYYY-MM-DD. */
export function dayStamp(d: Date): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

export const emptyLessons = (day: string): LessonState => ({ day, ids: [] });

/** Read what was stored: another day or a malformed value starts over; unknown ids are dropped. */
export function normalizeLessons(raw: unknown, day: string): LessonState {
  if (!raw || typeof raw !== "object") return emptyLessons(day);
  const r = raw as { day?: unknown; ids?: unknown };
  if (r.day !== day || !Array.isArray(r.ids)) return emptyLessons(day);
  const ids = [...new Set(r.ids.filter((x): x is LessonId => typeof x === "string" && KNOWN.has(x)))];
  return { day, ids };
}

export function earn(s: LessonState, id: LessonId): { state: LessonState; newly: boolean } {
  if (s.ids.includes(id)) return { state: s, newly: false };
  return { state: { ...s, ids: [...s.ids, id] }, newly: true };
}

export const lessonCount = (s: LessonState): number => s.ids.length;
export const lessonsComplete = (s: LessonState): boolean => s.ids.length >= LESSON_TOTAL;

/** The lessons a landing section teaches when it comes into view. */
export function lessonsForSection(id: string): LessonId[] {
  switch (id) {
    case "rent":
      return ["rent"];
    case "how":
      return ["cascade"];
    case "learn":
      return ["night"];
    case "real-run":
      return ["exam"];
    case "safety":
      return ["gate"];
    case "stack":
    case "nebius":
    case "integrate":
      return ["adapter"];
    default:
      return [];
  }
}
