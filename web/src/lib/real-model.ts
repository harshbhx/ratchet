/**
 * "What's real": one data file (`src/data/real.json`) says, for every claim the site makes about Ratchet, whether it is
 * real, measured, simulation, pending or ready to run, what it rests on and how to check it. The judges page's table and
 * every `RealBadge` read it through these helpers, so a status changes in one place. Pure, no JSX: tested by
 * `tests/real-model.test.mjs`, and `tests/test_judges_page.py` holds the file to its rules (evidence and a way to check on
 * every row, a value on every measured number, no number on a pending one).
 *
 * The `exam-result` and `toolcall-round3` entries are measured: each of their `metrics` holds its value as it prints. Change
 * them only from `evidence/` (`python scripts/text2sql/exam_numbers.py --check`, `python scripts/toolcall_results.py --check`).
 */

/** The status vocabulary, in the order the legend shows it. Keep on one line: the Python test reads it. */
export const REAL_STATUSES = ["real", "measured", "simulation", "pending", "not-run"] as const;

export type RealStatus = (typeof REAL_STATUSES)[number];

export interface RealCheck {
  label: string;
  /** A command to paste into a shell at the repository root. */
  command?: string;
  /** A page that shows it. */
  href?: string;
}

export interface RealFact {
  label: string;
  value: string;
}

export interface RealMetric {
  id: string;
  label: string;
  /** null until measured: never a placeholder. */
  value: string | null;
}

export interface RealEntry {
  id: string;
  claim: string;
  status: RealStatus;
  evidence: string;
  check: RealCheck;
  /** Repository files the entry rests on (the Python test checks that they exist). */
  paths: string[];
  /** When the source was last confirmed, YYYY-MM-DD. */
  date: string;
  facts: RealFact[];
  metrics: RealMetric[];
}

export interface RealLinks {
  repo: string;
  docs: string;
  demo: string;
  /** The demo video (public on YouTube since 26 Oct 2026); null hides every link to it. */
  video: string | null;
  /**
   * The video's counting link (video.ratchet.dualmindlab.tech): it counts the `from` tag and redirects to `video`. The page's own
   * links use it through `videoHref`; null (or anything but an https address) links straight to `video`.
   */
  videoShort: string | null;
}

/**
 * Where a "watch the video" link on the site points: the counting link tagged with where the click came from (`?from=site`,
 * `?from=judges`), else the video itself; null when there is no video, which hides the link.
 */
export function videoHref(links: Pick<RealLinks, "video" | "videoShort">, from: string): string | null {
  if (!links.video) return null;
  if (!links.videoShort) return links.video;
  try {
    const u = new URL(links.videoShort);
    if (u.protocol !== "https:") return links.video;
    u.searchParams.set("from", from);
    return u.href;
  } catch {
    return links.video;
  }
}

export interface RealData {
  updated: string;
  links: RealLinks;
  entries: RealEntry[];
}

/** The badge's words and its longer meaning (the legend and the badge's accessible label). */
export const STATUS_TEXT: Record<RealStatus, { label: string; meaning: string }> = {
  real: { label: "Real", meaning: "Happened or runs as described, and you can check it" },
  measured: { label: "Measured", meaning: "A number measured on a real model or a live gateway" },
  simulation: { label: "Simulation", meaning: "Mock models; no real model was measured" },
  pending: { label: "Pending", meaning: "Planned or in progress; not available yet" },
  "not-run": { label: "Ready to run", meaning: "Built and tested offline, ready to run for real" },
};

/** Badge tone per status: status colours only, always with the word (DESIGN.md). */
export const STATUS_TONE: Record<RealStatus, "good" | "owned" | "outline" | "warn" | "neutral"> = {
  real: "good",
  measured: "owned",
  simulation: "outline",
  pending: "warn",
  "not-run": "neutral",
};

export function isStatus(s: unknown): s is RealStatus {
  return typeof s === "string" && (REAL_STATUSES as readonly string[]).includes(s);
}

function str(v: unknown): string {
  return typeof v === "string" ? v.trim() : "";
}

/**
 * Read real.json's text into typed data, keeping only well-formed entries, so a bad edit hides one row instead of the
 * page. A measured entry whose metrics lack a value, or a pending one that has a value, is shown as "pending" with its
 * numbers withheld: the page never prints a number its status does not back.
 */
export function parseReal(text: string): RealData {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    raw = {};
  }
  const o = (raw ?? {}) as Record<string, unknown>;
  const l = (o.links ?? {}) as Record<string, unknown>;
  const entries: RealEntry[] = [];
  for (const e of Array.isArray(o.entries) ? o.entries : []) {
    const r = (e ?? {}) as Record<string, unknown>;
    const c = (r.check ?? {}) as Record<string, unknown>;
    const check: RealCheck = { label: str(c.label) };
    if (str(c.command)) check.command = str(c.command);
    if (str(c.href)) check.href = str(c.href);
    const entry: RealEntry = {
      id: str(r.id),
      claim: str(r.claim),
      status: isStatus(r.status) ? r.status : "pending",
      evidence: str(r.evidence),
      check,
      paths: Array.isArray(r.paths) ? r.paths.map(str).filter(Boolean) : [],
      date: str(r.date),
      facts: (Array.isArray(r.facts) ? r.facts : [])
        .map((f) => ({ label: str((f as RealFact)?.label), value: str((f as RealFact)?.value) }))
        .filter((f) => f.label && f.value),
      metrics: (Array.isArray(r.metrics) ? r.metrics : []).map((m) => {
        const x = (m ?? {}) as Record<string, unknown>;
        return { id: str(x.id), label: str(x.label), value: str(x.value) || null };
      }),
    };
    if (!entry.id || !entry.claim || !entry.evidence || !entry.check.label || !(entry.check.command || entry.check.href)) continue;
    entries.push(withheld(entry));
  }
  return {
    updated: str(o.updated),
    links: { repo: str(l.repo), docs: str(l.docs), demo: str(l.demo), video: str(l.video) || null, videoShort: str(l.videoShort) || null },
    entries,
  };
}

/** An entry whose numbers its status does not back, made safe: shown as pending with no value. */
function withheld(e: RealEntry): RealEntry {
  const filled = e.metrics.filter((m) => m.value != null).length;
  if (e.status === "measured" && (e.metrics.length === 0 || filled < e.metrics.length)) {
    return { ...e, status: "pending", metrics: e.metrics.map((m) => ({ ...m, value: null })) };
  }
  if (e.status !== "measured" && filled > 0) return { ...e, metrics: e.metrics.map((m) => ({ ...m, value: null })) };
  return e;
}

export function entryById(data: RealData, id: string): RealEntry | undefined {
  return data.entries.find((e) => e.id === id);
}

/** How many entries have each status, in legend order (the table's summary line). */
export function statusCounts(data: RealData): { status: RealStatus; n: number }[] {
  return REAL_STATUSES.map((status) => ({ status, n: data.entries.filter((e) => e.status === status).length })).filter((c) => c.n > 0);
}

/** "Real: happened or runs as described, ..." for a badge's accessible label. */
export function statusLabel(s: RealStatus): string {
  return `${STATUS_TEXT[s].label}: ${STATUS_TEXT[s].meaning.charAt(0).toLowerCase()}${STATUS_TEXT[s].meaning.slice(1)}`;
}

/**
 * The status of a number this page reads from the gateway it is served by: simulation on a mock gateway (the demo),
 * measured on a live one, and nothing while the mode is unknown (no badge rather than a wrong one).
 */
export function gatewayStatus(mode: string | null | undefined): RealStatus | null {
  if (mode === "mock") return "simulation";
  if (mode === "live") return "measured";
  return null;
}

/** "22 Oct 2026" from "2026-10-22"; the input when it is not a date. */
export function fmtDate(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!m) return iso;
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const mi = Number(m[2]) - 1;
  if (mi < 0 || mi > 11) return iso;
  return `${Number(m[3])} ${months[mi]} ${m[1]}`;
}
