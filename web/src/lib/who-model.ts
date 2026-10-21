/**
 * Who answered a call, in one vocabulary on every page (FR-7.40, web/DESIGN.md "Who answered").
 *
 * With the GPU endpoint off, the live gateway sends a trained version's student calls to the untuned Nemotron 3 Nano
 * 30B on Token Factory (RATCHET_FALLBACK_STUDENT). Those calls still come back `served_by: student`; only the call's
 * `model` says which model answered. So a student call is ours only when its model is not the fallback id the gateway
 * reports (`fallback_student` in /ratchet/stats and /scout/status). Pure (no JSX): tested in tests/who-model.test.mjs.
 */
import type { Mode, ServedBy, StudentServing } from "./api";

export type WhoKind = "owned" | "hosted" | "rented" | "tool";

export interface WhoLabel {
  kind: WhoKind;
  /** The badge text. */
  text: string;
  /** The badge tooltip. */
  title: string;
}

export interface WhoInput {
  servedBy: ServedBy;
  /** The model id the call came back with (the response's `model`, a Scout step's `model`). */
  model?: string | null;
  /** The gateway's hosted fallback id (`fallback_student`). Without it every student call reads as ours. */
  fallback?: string | null;
  /** The confidence threshold the hosted fallback is held to (`student_serving.threshold` while it serves). */
  threshold?: number | null;
  /** The gateway serves our adapter in daily endpoint windows (`student_serving.window`, W10), so the hosted tooltip says so. */
  windows?: boolean;
  /** Mock mode: mock models play the real ones, and the tooltip says so. */
  simulated?: boolean;
}

/** The badge texts. The colour tokens keep their names (`owned`, `rented`); only these words are visible. */
export const WHO_TEXT = {
  owned: "Nano · yours",
  hosted: "Nano 30B · hosted",
  rented: "Ultra · Token Factory",
  tavily: "Tavily",
  search: "Search",
} as const;

export const TEACHER_TITLE = "Nemotron 3 Ultra on Nebius Token Factory";
/** What a page says in place of the adapter's precision or certificate while the hosted fallback answers. */
export const NOT_CERTIFIED = "not covered by the certificate";
const SIMULATED = "Simulation: a mock model plays ";

/** The same model id, ignoring case and surrounding spaces (an upstream may echo an id in another case). */
export function sameModel(a?: string | null, b?: string | null): boolean {
  const x = (a ?? "").trim().toLowerCase();
  return x !== "" && x === (b ?? "").trim().toLowerCase();
}

/** A student call the hosted fallback answered. */
export function isHosted(servedBy: ServedBy, model?: string | null, fallback?: string | null): boolean {
  return servedBy === "student" && sameModel(model, fallback);
}

/** The version in a Night School adapter name (`student-v3` is 3), or null for any other id. */
export function adapterVersion(model?: string | null): number | null {
  const m = /(?:^|\/)student-v(\d+)$/i.exec((model ?? "").trim());
  return m ? Number(m[1]) : null;
}

/** A threshold as the tooltips print it: two decimals at least, three when the third is not 0 (0.80, 0.805). */
export function fmtThreshold(t?: number | null): string | null {
  if (typeof t !== "number" || !Number.isFinite(t)) return null;
  const s = t.toFixed(3);
  return s.endsWith("0") ? s.slice(0, -1) : s;
}

export function ownedTitle(version?: number | null): string {
  return `Nemotron 3 Nano 4B with our LoRA adapter${version != null ? ` v${version}` : ""}, on a Nebius Serverless Endpoint`;
}

/**
 * The hosted badge's tooltip. The last sentence only when the gateway reports endpoint windows: without them nothing
 * says when our adapter answers, and the tooltip does not guess.
 */
export function hostedTitle(threshold?: number | null, windows = false): string {
  const t = fmtThreshold(threshold);
  return (
    `Untuned Nemotron 3 Nano 30B on Token Factory${t ? `, held to confidence threshold ${t}` : ""}. ` +
    "Not covered by our adapter's precision certificate." +
    (windows ? " Our adapter answers during the daily endpoint windows." : "")
  );
}

/** The badge for one call. A student call is `hosted` when its model is the fallback id, else `owned`. */
export function whoLabel({ servedBy, model, fallback, threshold, windows, simulated }: WhoInput): WhoLabel {
  const sim = (title: string) => (simulated ? SIMULATED + title : title);
  if (servedBy === "student") {
    if (isHosted(servedBy, model, fallback))
      return { kind: "hosted", text: WHO_TEXT.hosted, title: sim(hostedTitle(threshold, windows)) };
    return { kind: "owned", text: WHO_TEXT.owned, title: sim(ownedTitle(adapterVersion(model))) };
  }
  if (servedBy === "teacher") return { kind: "rented", text: WHO_TEXT.rented, title: sim(TEACHER_TITLE) };
  return { kind: "tool", text: model === "tavily" ? WHO_TEXT.tavily : WHO_TEXT.search, title: model ?? "" };
}

/** How many of these calls the hosted fallback answered. */
export function hostedCalls(steps: readonly { served_by: ServedBy; model?: string | null }[], fallback?: string | null): number {
  return steps.filter((s) => isHosted(s.served_by, s.model, fallback)).length;
}

export interface StudentCopy {
  /** A metric's label: "Answered by your model". */
  label: string;
  /** The same on a phone. */
  short: string;
  /** After "N of M calls answered". */
  by: string;
  /** The same in a short progress line. */
  byShort: string;
  /** The subject of "... tried first". */
  tried: string;
  /** A table column. */
  column: string;
  /** In a compact progress line ("3 yours"). */
  compact: string;
  /** In a sentence or a chart's tooltip: "your model 84%". */
  name: string;
}

const OWNED_COPY: StudentCopy = {
  label: "Answered by your model",
  short: "Your model",
  by: "by your model",
  byShort: "by your model",
  tried: "your model",
  column: "Yours",
  compact: "yours",
  name: "your model",
};
const HOSTED_COPY: StudentCopy = {
  label: "Answered by the student",
  short: "Student",
  by: "by the hosted Nano (untuned)",
  byShort: "by the student",
  tried: "the hosted Nano",
  column: "Student",
  compact: "student",
  name: "the student",
};

/** The words for student answers on a run or a page: "your model" only while no answer came from the hosted fallback. */
export function studentCopy(hosted: boolean): StudentCopy {
  return hosted ? HOSTED_COPY : OWNED_COPY;
}

/** Whether the hosted fallback answers the student's calls now (`student_serving.kind`). */
export function hostedServing(serving?: StudentServing | null): boolean {
  return serving?.kind === "hosted_fallback";
}

/** Whether a page may show the serving version's precision or certificate: never while the hosted fallback answers. */
export function precisionShown(serving?: StudentServing | null): boolean {
  return !hostedServing(serving);
}

/** The threshold a hosted badge names: the serving threshold while the hosted fallback serves, else unknown. */
export function hostedThreshold(serving?: StudentServing | null): number | null {
  return hostedServing(serving) && typeof serving?.threshold === "number" ? serving.threshold : null;
}

/** Whether the gateway reports daily endpoint windows (`student_serving.window`, null or absent without them). */
export function hasWindows(serving?: StudentServing | null): boolean {
  return serving?.window != null && typeof serving.window === "object";
}

export interface ServingLine {
  kind: "owned" | "hosted" | "none";
  text: string;
  /** A second, quieter part: the threshold and certificate note, or what happens without a student. */
  detail: string | null;
  title: string;
}

/**
 * The dashboard header's student line, from `student_serving`. Null keeps the base model line: mock mode, and a
 * gateway older than this field.
 */
export function servingLine(serving?: StudentServing | null): ServingLine | null {
  if (!serving) return null;
  if (serving.kind === "endpoint") {
    const v = serving.version ?? adapterVersion(serving.model);
    return {
      kind: "owned",
      text: `Nano 4B + LoRA${v != null ? ` v${v}` : ""} · Nebius Serverless Endpoint`,
      detail: null,
      title: ownedTitle(v),
    };
  }
  if (serving.kind === "hosted_fallback") {
    const t = fmtThreshold(serving.threshold);
    return {
      kind: "hosted",
      text: "Nano 30B · hosted on Token Factory (endpoint off)",
      detail: `${t ? `threshold ${t} · ` : ""}${NOT_CERTIFIED}`,
      title: hostedTitle(serving.threshold, hasWindows(serving)),
    };
  }
  if (serving.kind === "none") return { kind: "none", text: "No student yet", detail: "the teacher answers every request", title: TEACHER_TITLE };
  return null;
}

/** The landing strip's student-share item: its label, and a sub-line in place of the day's while the hosted fallback answers. */
export function studentShareCopy(serving?: StudentServing | null): { label: string; sub: string | null } {
  return hostedServing(serving)
    ? { label: HOSTED_COPY.label, sub: "hosted Nano 30B while our GPU endpoint is off" }
    : { label: OWNED_COPY.label, sub: null };
}

/**
 * What a page knows about the gateway's student, for every badge under it (`WhoProvider`): the hosted fallback id, the
 * threshold it is held to, whether it serves now, whether the gateway reports endpoint windows, whether mock models play
 * the real ones, and `serving`, the model student calls go to now (a badge for "who answers this kind of call" passes it
 * as its `model`).
 */
export interface WhoContextValue {
  fallback?: string | null;
  threshold?: number | null;
  hosted?: boolean;
  windows?: boolean;
  simulated?: boolean;
  serving?: string | null;
}

/** The badge context from `/ratchet/stats` or `/scout/status` (both carry these keys); empty before either loads. */
export function whoContext(src?: { mode?: Mode | null; fallback_student?: string; student_serving?: StudentServing } | null): WhoContextValue {
  if (!src) return {};
  const serving = src.student_serving;
  return {
    fallback: src.fallback_student ?? null,
    threshold: hostedThreshold(serving),
    hosted: hostedServing(serving),
    windows: hasWindows(serving),
    simulated: src.mode === "mock",
    serving: serving?.model ?? null,
  };
}
