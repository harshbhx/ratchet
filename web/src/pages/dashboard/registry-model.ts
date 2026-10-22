/**
 * Pure helpers for the Task registry panel (GET /ratchet/tasks, ADR 0022): source and policy wording, comparator
 * summaries, field options as text, and the editor's spec handling. Everything works from what the gateway sent.
 */
import { ApiError, type FieldDoc, type TaskEntry, type TaskSource, type UnknownTaskPolicy } from "@/lib/api";

export interface SourceMeta {
  label: string;
  /** Where the spec lives and how to change it. */
  hint: string;
}

const SOURCES: Record<string, SourceMeta> = {
  builtin: { label: "Built in", hint: "Defined in the code (ratchet/tasks.py). Another task may extend it." },
  file: { label: "Tasks file", hint: "Defined in RATCHET_TASKS_FILE, read at startup. Edit the file to change it." },
  api: { label: "Admin API", hint: "Written with PUT /ratchet/tasks/{name} and an admin key. It can be changed or deleted here." },
  auto: { label: "Auto", hint: "Registered by the gateway itself." },
};

export const SOURCE_ORDER = ["builtin", "file", "api", "auto"];

export function sourceMeta(source: TaskSource): SourceMeta {
  return SOURCES[source] ?? { label: source, hint: "A source this dashboard does not know yet." };
}

/** Only API tasks can be changed or deleted through the admin API (gateway._not_yours). */
export function isEditable(t: Pick<TaskEntry, "source">): boolean {
  return t.source === "api";
}

/** The registered sources in display order, with how many tasks each has. */
export function sourceCounts(tasks: Pick<TaskEntry, "source">[]): { source: string; n: number }[] {
  const counts = new Map<string, number>();
  for (const t of tasks) counts.set(t.source, (counts.get(t.source) ?? 0) + 1);
  const known = SOURCE_ORDER.filter((s) => counts.has(s));
  const other = [...counts.keys()].filter((s) => !SOURCE_ORDER.includes(s)).sort();
  return [...known, ...other].map((source) => ({ source, n: counts.get(source) ?? 0 }));
}

export const POLICY_TEXT: Record<string, { short: string; long: string }> = {
  distill: {
    short: "distilled",
    long: "A request naming a task that is not registered gets a task without fields under that name, distilled like any other (RATCHET_UNKNOWN_TASKS=distill).",
  },
  teacher: {
    short: "teacher only",
    long: "A request naming a task that is not registered is answered by the teacher; the student is not called and nothing is stored (RATCHET_UNKNOWN_TASKS=teacher).",
  },
  reject: {
    short: "refused",
    long: "A request naming a task that is not registered gets a 400 unknown_task (RATCHET_UNKNOWN_TASKS=reject).",
  },
};

export function policyText(p: UnknownTaskPolicy): { short: string; long: string } {
  return POLICY_TEXT[p] ?? { short: p, long: `RATCHET_UNKNOWN_TASKS=${p}.` };
}

/** Top-level fields by comparator type, most common first: `label ×2`, `bool`. */
export function typeSummary(fields: Record<string, FieldDoc>): { type: string; n: number }[] {
  const counts = new Map<string, number>();
  for (const f of Object.values(fields)) {
    const t = typeof f?.type === "string" ? f.type : "exact";
    counts.set(t, (counts.get(t) ?? 0) + 1);
  }
  return [...counts.entries()].map(([type, n]) => ({ type, n })).sort((a, b) => b.n - a.n || a.type.localeCompare(b.type));
}

/** Fields counted as the registry's 64-field limit counts them: a record's sub-fields too. */
export function countFields(fields: Record<string, FieldDoc> | undefined): number {
  if (!fields) return 0;
  let n = 0;
  for (const f of Object.values(fields)) n += 1 + countFields(f?.fields);
  return n;
}

/** What each comparator type does, in a few words (docs/CONFIGURATION.md#tasks). */
export const TYPE_HINT: Record<string, string> = {
  exact: "equal after trimming",
  label: "one label from a vocabulary",
  bool: "true or false, also yes/no",
  number: "a number, within a tolerance",
  ordinal: "a step on an ordered scale",
  text: "text, compared loosely",
  date: "a date or time",
  language: "a language code or name",
  set: "a set of values",
  records: "a list of records",
  ranking: "an ordered list of ids",
};

const OPTION_LABEL: Record<string, string> = {
  values: "Values",
  aliases: "Aliases",
  abs_tol: "Absolute tolerance",
  rel_tol: "Relative tolerance",
  decimal: "Decimal mark",
  percent: "Percent as",
  order: "Order, lowest first",
  tolerance: "Steps apart allowed",
  mode: "Mode",
  strip_suffixes: "Suffixes dropped",
  day_first: "Day first",
  granularity: "Granularity",
  item: "Each element",
  min_jaccard: "Minimum overlap",
  split: "Split on",
  empty_values: "Dropped elements",
  key: "Paired by",
  ordered: "Paired by position",
  k: "Leading ids compared",
};

/** A JSON value as one short line of text. */
export function fmtValue(v: unknown): string {
  if (v === null) return "null";
  if (typeof v === "string") return v === "" ? '""' : v;
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v === "number") return Number.isFinite(v) ? String(v) : "–";
  if (Array.isArray(v)) return v.length === 0 ? "none" : v.map((x) => (typeof x === "string" ? x : JSON.stringify(x))).join(", ");
  if (typeof v === "object") {
    const entries = Object.entries(v as Record<string, unknown>);
    return entries.length === 0 ? "none" : entries.map(([a, b]) => `${a} → ${typeof b === "string" ? b : JSON.stringify(b)}`).join(", ");
  }
  return String(v);
}

export interface OptionRow {
  key: string;
  label: string;
  text: string;
}

/**
 * A field's options other than its type, requiredness and sub-fields, labelled and as text, in the order the gateway
 * sent them. An option this dashboard does not know is shown under its own name, never dropped.
 */
export function fieldOptions(f: FieldDoc): OptionRow[] {
  const rows: OptionRow[] = [];
  for (const [key, value] of Object.entries(f)) {
    if (key === "type" || key === "required" || key === "nullable" || key === "fields") continue;
    if (key === "item" && value && typeof value === "object") {
      const item = value as FieldDoc;
      const opts = fieldOptions(item).map((o) => `${o.label.toLowerCase()} ${o.text}`);
      rows.push({ key, label: OPTION_LABEL.item, text: [item.type, ...opts].join("; ") });
      continue;
    }
    rows.push({ key, label: OPTION_LABEL[key] ?? key, text: fmtValue(value) });
  }
  return rows;
}

/** `required`, `optional`, and `nullable` when it is: the defaults are required and not nullable. */
export function presence(f: FieldDoc): string {
  const req = f.required === false ? "optional" : "required";
  return f.nullable ? `${req}, may be null` : req;
}

/** How a task's training data is kept: pinned tasks never become examples (invariant 10), whatever `store` says. */
export function storage(t: Pick<TaskEntry, "pinned" | "store">): "pinned" | "stored" | "not_stored" {
  if (t.pinned) return "pinned";
  return t.store ? "stored" : "not_stored";
}

/** The keys of a spec as a file holds it and PUT takes it back (taskspec.SPEC_KEYS), without `name`. */
export const SPEC_KEYS = ["description", "format", "fields", "sql", "pinned", "store", "privacy", "match", "routing", "policy", "tools", "clients"] as const;

/** A registered task's spec as an editable document: what PUT /ratchet/tasks/{name} takes back. */
export function specDocument(t: TaskEntry, withName = false): Record<string, unknown> {
  const out: Record<string, unknown> = withName ? { name: t.name } : {};
  for (const k of SPEC_KEYS) out[k] = t[k];
  return out;
}

export function specText(doc: Record<string, unknown>): string {
  return JSON.stringify(doc, null, 2);
}

/** Task names, as tasks.valid_name checks them. */
export const NAME_RE = /^[A-Za-z0-9_.-]{1,64}$/;

/** A new task's starting point: the example from docs/CONFIGURATION.md#adding-a-task. */
export const NEW_TASK = {
  name: "invoice-extract",
  spec: {
    description: "Vendor, total and due date from an invoice",
    fields: {
      vendor: { type: "text", strip_suffixes: ["inc", "ltd"] },
      total: { type: "number", abs_tol: 0.01 },
      due_date: { type: "date", nullable: true },
    },
  } as Record<string, unknown>,
};

/** A new API task that starts from a built-in or file task (`extends` copies its spec when the task is defined). */
export function extendDraft(t: TaskEntry, taken: { has(name: string): boolean }): { name: string; spec: Record<string, unknown> } {
  const base = t.name.slice(0, 57);
  let name = `${base}-custom`;
  for (let i = 2; taken.has(name) && i < 100; i++) name = `${base}-custom-${i}`;
  return { name, spec: { extends: t.name, description: t.description ? `${t.description} (custom)` : "" } };
}

export type Parsed = { ok: true; value: Record<string, unknown> } | { ok: false; error: string };

/** The editor's text as one spec object, or why not. The gateway checks it again, strictly (NaN refused). */
export function parseSpec(text: string): Parsed {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch (e) {
    return { ok: false, error: `Not valid JSON: ${e instanceof Error ? e.message : "it does not parse"}.` };
  }
  if (value == null || typeof value !== "object" || Array.isArray(value)) return { ok: false, error: "The spec must be one JSON object." };
  return { ok: true, value: value as Record<string, unknown> };
}

/** The errors a refused admin call carries: a 422's `errors`, else its message. */
export function errorsOf(e: unknown): string[] {
  if (e instanceof ApiError) {
    const body = e.body as { errors?: unknown } | undefined;
    if (e.status === 422 && Array.isArray(body?.errors) && body.errors.length) return body.errors.map((x) => String(x));
    return [adminMessage(e)];
  }
  return ["The gateway did not answer."];
}

/** What a refused management call means, in the gateway's words where it gave them. */
export function adminMessage(e: ApiError): string {
  const msg = e.message && !/^Request failed \(\d+\)$/.test(e.message) ? e.message : "";
  switch (e.status) {
    case 401:
      return "The admin key is missing or wrong. Type a key from RATCHET_ADMIN_KEYS.";
    case 403:
      return msg ? sentence(msg) : "Not allowed.";
    case 404:
      return msg ? sentence(msg) : "That task is not registered.";
    case 409:
      return msg ? sentence(msg) : "The registry is full.";
    case 413:
      return "The spec is larger than the gateway accepts.";
    default:
      return msg ? sentence(msg) : `The gateway answered ${e.status}.`;
  }
}

function sentence(msg: string): string {
  const t = msg.trim();
  if (!t) return t;
  return t.charAt(0).toUpperCase() + t.slice(1) + (/[.!?]$/.test(t) ? "" : ".");
}

/** A gateway timestamp (epoch seconds) as a local date and time. */
export function fmtDateTime(ts: number): string {
  return new Date(ts * 1000).toLocaleString("en-US", { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** Matches a task by name or description, case-insensitively. */
export function matches(t: Pick<TaskEntry, "name" | "description">, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return t.name.toLowerCase().includes(q) || (t.description ?? "").toLowerCase().includes(q);
}
