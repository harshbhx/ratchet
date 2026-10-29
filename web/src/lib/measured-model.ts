/**
 * The measured snapshot (`src/data/measured.json`, written from `evidence/` by `scripts/measured_numbers.py` and held to it by
 * `tests/test_measured_numbers.py`) and the live totals the Worker keeps across restarts (`GET /ratchet/stats/persistent`).
 * Both feed the judges page and the dashboard, so neither shows an empty "0 / 0" after the gateway restarts:
 *
 *   - Live since <date>: the Worker's running totals, when it has recorded traffic;
 *   - otherwise the measured snapshot, every value dated and linked to the folder it was read from.
 *
 * Pure, no JSX: `tests/measured-model.test.mjs` runs it. Nothing here makes up a number: a tile that has no data is left out.
 */

export const REPO_TREE = "https://github.com/harshbhx/ratchet/tree/main/";

/** "2026-10-27" as "27 Oct 2026" (UTC, so the label does not move with the reader's time zone). */
export function fmtDay(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return iso;
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const name = months[Number(m[2]) - 1];
  return name ? `${Number(m[3])} ${name} ${m[1]}` : iso;
}

/** "27 to 28 Oct 2026" or "27 Oct 2026". */
export function fmtRange(from: string, to?: string | null): string {
  if (!to || to === from) return fmtDay(from);
  const a = fmtDay(from).split(" ");
  const b = fmtDay(to).split(" ");
  if (a[1] === b[1] && a[2] === b[2]) return `${a[0]} to ${fmtDay(to)}`;
  return `${fmtDay(from)} to ${fmtDay(to)}`;
}

/** The label every measured value carries: "Measured · 27 Oct 2026". The source goes beside it as a link (`sourceHref`). */
export function measuredLabel(date: string, dateEnd?: string | null): string {
  return `Measured · ${fmtRange(date, dateEnd)}`;
}

export const sourceHref = (path: string): string => `${REPO_TREE}${path}`;

export const fmtInt = (n: number): string => Math.round(n).toLocaleString("en-US");

/** Dollars: four places under a cent's tenth, two otherwise, so a $0.0133 smoke run does not read "$0.01" as a rounding of nothing. */
export function fmtUsd(n: number): string {
  if (n === 0) return "$0";
  return n < 0.1 ? `$${n.toFixed(4)}` : `$${n.toFixed(2)}`;
}

export const fmtPct = (rate: number, digits = 1): string => `${(rate * 100).toFixed(digits)}%`;

export function fmtSeconds(s: number): string {
  if (s >= 120) return `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`;
  return `${s >= 10 ? Math.round(s) : s.toFixed(1)} s`;
}

// ---------------------------------------------------------------- the snapshot

export interface Score {
  model: string;
  label?: string;
  agree: number;
  n: number;
  rate: number;
}

export interface MeasuredData {
  exams: { date: string; source: string; what: string; exams: { id: string; name: string; rows: number; built: string; evidence: string; scores: Score[] }[] };
  toolcall: { date: string; source: string; what: string; exams: { id: string; label: string; rows: number; scores: Score[] }[] };
  retrain: {
    date: string;
    source: string;
    what: string;
    exams: { id: string; nebius: Score2; original: Score2 }[];
    machine_hours: number;
    cost_usd: number;
    jobs: number;
  };
  endpoint: {
    date: string;
    date_end: string;
    source: string;
    what: string;
    gpu_usd_per_hour: number;
    cold_start_s: number;
    restart_s: number;
    adapter_load_s: number;
    bench: { adapter: string; concurrency: number; requests: number; ok: number; errors: number; lat_p50_s: number; lat_p95_s: number; tokens_per_s_total: number; requests_per_s: number; usd_per_1000: number }[];
  };
  scout: {
    date: string;
    date_end: string;
    source: string;
    what: string;
    runs: { file: string; recorded_at: string; question: string; duration_s: number; calls: number; llm_calls: number; teacher_calls: number; student_calls: number; cost_usd: number; citations: number }[];
  };
  smoke: { date: string; source: string; what: string; run_id: string; requests: number; total_usd: number; teacher_p50_wall_ms: number; ultra_p50_client_ms: number; wall_s: number; student_share_pct: number };
  labelling: { date: string; source: string; what: string; rounds: { id: string; date: string; usd: number; calls: number; rows: number; source: string }[] };
  hosted_nano: { date: string; source: string; what: string; rows: Record<string, number>; spend_usd: number; student_model: string; teacher_model: string };
  nightschool: {
    present: boolean;
    source: string;
    date?: string | null;
    what?: string | null;
    verdict?: string | null;
    adapter_name?: string | null;
    numbers?: { golden_agreement?: string; coverage?: string; precision?: string; target?: string; training_rows?: string; golden_rows?: string; rows_imported?: string; steps?: string; loss_start?: string; loss_end?: string; gate_right?: string; gate_answered?: string; gate_questions?: string };
    has_readme?: boolean;
    files?: string[];
  };
}

interface Score2 {
  agree: number;
  n: number;
  rate: number;
}

/** The snapshot's JSON text, or null when it is not one (the panel then shows nothing rather than half a snapshot). */
export function parseMeasured(text: string): MeasuredData | null {
  try {
    const d = JSON.parse(text) as Partial<MeasuredData>;
    for (const k of ["exams", "toolcall", "retrain", "endpoint", "scout", "smoke", "labelling", "hosted_nano", "nightschool"] as const) {
      if (typeof d[k] !== "object" || d[k] === null) return null;
    }
    return d as MeasuredData;
  } catch {
    return null;
  }
}

/** One measured card: what it says, when it was measured, where it was read from, and its values. */
export interface Tile {
  id: string;
  title: string;
  /** A one-line claim in words. */
  headline: string;
  date: string;
  dateEnd?: string | null;
  source: string;
  /** What kind of run it was, in the site's words: "Measured" for a number read from evidence, "Recorded run" for a replayable recording. */
  tag: "Measured" | "Recorded run";
  facts: { label: string; value: string }[];
  /** Bars drawn from the facts: a value from 0 to 1 and its text. */
  bars?: { group?: string; label: string; value: number; text: string }[];
}

const scoreBars = (group: string, scores: Score[], names: Record<string, string>) =>
  scores.map((s) => ({ group, label: names[s.model] ?? s.label ?? s.model, value: s.rate, text: `${fmtPct(s.rate)} (${s.agree} of ${s.n})` }));

/** The tiles, in the order a judge reads them. A section whose evidence is missing is left out, never filled. */
export function measuredTiles(d: MeasuredData): Tile[] {
  const tiles: Tile[] = [];
  const names: Record<string, string> = { base: "Untrained model", teacher: "Teacher (Ultra)", r1: "Adapter, round 1", r2: "Adapter, round 2", adapter: "Adapter", ultra: "Teacher (Ultra)" };

  const x1 = d.exams.exams.find((e) => e.id === "exam1");
  const x2 = d.exams.exams.find((e) => e.id === "exam2");
  if (x1 && x2) {
    const r2 = (x: typeof x1) => x.scores.find((s) => s.model === "r2");
    const base = (x: typeof x1) => x.scores.find((s) => s.model === "base");
    const a = r2(x1);
    const b = base(x1);
    tiles.push({
      id: "exams",
      title: "Text-to-SQL exams",
      headline: a && b ? `The round-2 adapter gets ${fmtPct(a.rate)} on exam 1, the untrained model ${fmtPct(b.rate)}` : d.exams.what,
      date: d.exams.date,
      source: d.exams.source,
      tag: "Measured",
      facts: [
        { label: "Exam 1", value: `${x1.rows} questions` },
        { label: "Exam 2", value: `${x2.rows} questions` },
      ],
      bars: [...scoreBars("Exam 1", x1.scores, names), ...scoreBars("Exam 2", x2.scores, names)],
    });
  }

  if (d.toolcall.exams.length) {
    tiles.push({
      id: "toolcall",
      title: "Tool calling",
      headline: d.toolcall.what,
      date: d.toolcall.date,
      source: d.toolcall.source,
      tag: "Measured",
      facts: d.toolcall.exams.map((e) => ({ label: e.label, value: `${e.rows} questions` })),
      bars: d.toolcall.exams.flatMap((e) => scoreBars(e.label, e.scores, names)),
    });
  }

  const e1 = d.retrain.exams.find((e) => e.id === "exam1");
  const e2 = d.retrain.exams.find((e) => e.id === "exam2");
  if (e1 && e2) {
    tiles.push({
      id: "retrain",
      title: "Round-2 retrain on Nebius",
      headline: `Retrained on one preemptible L40S in ${d.retrain.machine_hours} machine-hours for about ${fmtUsd(d.retrain.cost_usd)}: ${fmtPct(e1.nebius.rate)} on exam 1, ${fmtPct(e2.nebius.rate)} on exam 2`,
      date: d.retrain.date,
      source: d.retrain.source,
      tag: "Measured",
      facts: [
        { label: "Machine time", value: `${d.retrain.machine_hours} h across ${d.retrain.jobs} jobs` },
        { label: "Cost at list price", value: fmtUsd(d.retrain.cost_usd) },
      ],
      bars: [
        { group: "Exam 1", label: "Retrained on Nebius", value: e1.nebius.rate, text: `${fmtPct(e1.nebius.rate)} (${e1.nebius.agree} of ${e1.nebius.n})` },
        { group: "Exam 1", label: "Original adapter", value: e1.original.rate, text: `${fmtPct(e1.original.rate)} (${e1.original.agree} of ${e1.original.n})` },
        { group: "Exam 2", label: "Retrained on Nebius", value: e2.nebius.rate, text: `${fmtPct(e2.nebius.rate)} (${e2.nebius.agree} of ${e2.nebius.n})` },
        { group: "Exam 2", label: "Original adapter", value: e2.original.rate, text: `${fmtPct(e2.original.rate)} (${e2.original.agree} of ${e2.original.n})` },
      ],
    });
  }

  const ep = d.endpoint;
  const sql = ep.bench.filter((r) => r.adapter === "sql-r2");
  const slowest = Math.max(1, ...ep.bench.map((r) => r.lat_p50_s));
  if (ep.bench.length) {
    const one = ep.bench.find((r) => r.adapter === "toolcall" && r.concurrency === 1) ?? sql[0];
    tiles.push({
      id: "endpoint",
      title: "Student endpoint speed",
      headline: `Served by hand on a Nebius endpoint: ${fmtSeconds(ep.cold_start_s)} from a cold start, ${fmtSeconds(ep.restart_s)} after a restart, about ${one ? one.lat_p50_s.toFixed(1) : "1"} s per answer`,
      date: ep.date,
      dateEnd: ep.date_end,
      source: ep.source,
      tag: "Measured",
      facts: [
        { label: "Cold start to first answer", value: fmtSeconds(ep.cold_start_s) },
        { label: "Restart to first answer", value: fmtSeconds(ep.restart_s) },
        { label: "Adapter load", value: fmtSeconds(ep.adapter_load_s) },
        { label: "GPU price", value: `$${ep.gpu_usd_per_hour.toFixed(2)} an hour` },
        ...(sql.find((r) => r.concurrency === 16) ? [{ label: "SQL, 16 at a time", value: `${fmtUsd(sql.find((r) => r.concurrency === 16)!.usd_per_1000)} per 1,000 requests` }] : []),
        ...(sql.find((r) => r.concurrency === 16) ? [{ label: "Throughput, 16 at a time", value: `${Math.round(sql.find((r) => r.concurrency === 16)!.tokens_per_s_total)} tokens a second` }] : []),
      ],
      bars: ep.bench.map((r) => ({ group: `${r.adapter}, median latency`, label: `${r.concurrency} at a time`, value: r.lat_p50_s / slowest, text: `${r.lat_p50_s.toFixed(2)} s` })),
    });
  }

  if (d.scout.runs.length) {
    const runs = d.scout.runs;
    const costliest = Math.max(...runs.map((r) => r.cost_usd));
    tiles.push({
      id: "scout",
      title: "Recorded Scout runs",
      headline: `${runs.length} recorded runs on Token Factory with live search; the teacher answered every model call`,
      date: d.scout.date,
      dateEnd: d.scout.date_end,
      source: d.scout.source,
      tag: "Recorded run",
      facts: [
        { label: "Duration", value: `${Math.min(...runs.map((r) => r.duration_s))} to ${Math.max(...runs.map((r) => r.duration_s))} s` },
        { label: "Cost per run", value: `${fmtUsd(Math.min(...runs.map((r) => r.cost_usd)))} to ${fmtUsd(costliest)}` },
        { label: "Model calls per run", value: `${Math.min(...runs.map((r) => r.llm_calls))} to ${Math.max(...runs.map((r) => r.llm_calls))}` },
      ],
      bars: runs.map((r, i) => ({ group: "Cost of each run", label: `Run ${i + 1}`, value: costliest ? r.cost_usd / costliest : 0, text: `${fmtUsd(r.cost_usd)} · ${r.duration_s} s` })),
    });
  }

  tiles.push({
    id: "smoke",
    title: "Live smoke run",
    headline: `${d.smoke.requests} requests answered by the teacher through the live path in ${d.smoke.wall_s} s`,
    date: d.smoke.date,
    source: d.smoke.source,
    tag: "Recorded run",
    facts: [
      { label: "Spend", value: fmtUsd(d.smoke.total_usd) },
      { label: "Teacher, median", value: `${Math.round(d.smoke.teacher_p50_wall_ms)} ms` },
      { label: "Student share", value: `${d.smoke.student_share_pct.toFixed(0)}% (no student endpoint)` },
    ],
  });

  const lab = d.labelling.rounds;
  if (lab.length) {
    const top = Math.max(...lab.map((r) => r.usd));
    tiles.push({
      id: "labelling",
      title: "What the labels cost",
      headline: `The teacher wrote ${fmtInt(lab.reduce((n, r) => n + r.rows, 0))} text-to-SQL rows for ${fmtUsd(lab.reduce((n, r) => n + r.usd, 0))} on Token Factory`,
      date: d.labelling.date,
      source: d.labelling.source,
      tag: "Measured",
      facts: lab.map((r) => ({ label: `Round ${r.id.replace("round", "")} (${fmtDay(r.date)})`, value: `${fmtUsd(r.usd)} · ${r.calls} calls` })),
      bars: lab.map((r) => ({ label: `Round ${r.id.replace("round", "")}`, value: top ? r.usd / top : 0, text: `${fmtUsd(r.usd)}` })),
    });
  }

  const hn = d.hosted_nano;
  tiles.push({
    id: "hosted-nano",
    title: "Hosted Nano as a stand-in student",
    headline: "The untuned hosted Nano was scored against the teacher's labels on Scout's two distillable calls",
    date: hn.date,
    source: hn.source,
    tag: "Measured",
    facts: [
      ...Object.entries(hn.rows).map(([k, v]) => ({ label: `${k} rows`, value: String(v) })),
      { label: "Spend", value: fmtUsd(hn.spend_usd) },
    ],
  });

  return tiles;
}

/** The version the golden gate rejected in the Night School run ("v1"), or null while there is no such evidence or it did not say so. */
export function rejectedVersion(d: MeasuredData): string | null {
  const n = d.nightschool;
  if (!n.present || n.verdict !== "rejected") return null;
  const m = /(v\d+)$/.exec(n.adapter_name ?? "");
  return m ? m[1] : null;
}

/** The Night School tile exists only once its evidence folder does; every value in it is read from that folder (its manifest and the run's README tables). */
export function nightSchoolTile(d: MeasuredData): Tile | null {
  const n = d.nightschool;
  if (!n.present) return null;
  const v = rejectedVersion(d);
  const x = n.numbers ?? {};
  const facts: Tile["facts"] = [];
  if (v) facts.push({ label: "Verdict", value: `Rejected at the golden gate; ${v} was never made live` });
  if (x.precision && x.target) facts.push({ label: "Precision at the gate's threshold", value: `${x.precision} against the ${x.target} target` });
  if (x.coverage) facts.push({ label: "Coverage at that threshold", value: x.coverage });
  if (x.golden_agreement) facts.push({ label: "Golden agreement", value: x.golden_agreement });
  if (x.training_rows && x.golden_rows) facts.push({ label: "Rows", value: `${x.training_rows} training, ${x.golden_rows} golden` });
  if (facts.length === 0) facts.push({ label: "Evidence files", value: String(n.files?.length ?? 0) });
  return {
    id: "nightschool",
    title: "Night School on Nebius",
    headline: v
      ? "Night School ran end to end once on Nebius, by hand: trained on a spot L40S, the adapter served from a Nebius endpoint, and the golden gate rejected it"
      : (n.what ?? "A Night School run on Nebius; the evidence folder has the details"),
    date: n.date ?? "",
    source: n.source,
    tag: "Recorded run",
    facts,
  };
}

// ---------------------------------------------------------------- the live totals

export interface Persistent {
  recorded: boolean;
  since: string | null;
  updated: string | null;
  instances: number;
  totals: {
    requests: number;
    requests_teacher: number;
    requests_split: number;
    requests_student: number;
    student_tokens_in: number;
    student_tokens_out: number;
    cost_usd: number;
    /** Every failures row, a refused key included. Kept for compatibility; the pages show `server_errors`. */
    errors: number;
    /** Failed model calls and the gateway's own 5xx only. Absent from a Worker that has not started counting it. */
    server_errors?: number;
    scout_runs: number;
  };
}

const TOTAL_KEYS = ["requests", "requests_teacher", "requests_split", "requests_student", "student_tokens_in", "student_tokens_out", "cost_usd", "errors", "scout_runs"] as const;

/** The Worker's answer, or null when it is not one (a gateway without the route answers 404, a warming-up one answers its own page). */
export function parsePersistent(raw: unknown): Persistent | null {
  if (typeof raw !== "object" || raw === null) return null;
  const r = raw as Record<string, unknown>;
  if (r.recorded !== true || typeof r.since !== "string" || typeof r.totals !== "object" || r.totals === null) return null;
  const t = r.totals as Record<string, unknown>;
  const totals = {} as Persistent["totals"];
  if (t.server_errors !== undefined) {
    if (typeof t.server_errors !== "number" || !Number.isFinite(t.server_errors) || t.server_errors < 0) return null;
    totals.server_errors = t.server_errors;
  }
  for (const k of TOTAL_KEYS) {
    const v = t[k];
    if (typeof v !== "number" || !Number.isFinite(v) || v < 0) return null;
    totals[k] = v;
  }
  return { recorded: true, since: r.since, updated: typeof r.updated === "string" ? r.updated : null, instances: typeof r.instances === "number" ? r.instances : 1, totals };
}

/** Does the live total have something to say? A recorded store with nothing counted yet is no better than an empty one: show the snapshot. */
export function hasLiveTraffic(p: Persistent | null): p is Persistent {
  return p !== null && (p.totals.requests > 0 || p.totals.scout_runs > 0);
}

export interface LiveLine {
  headline: string;
  items: { label: string; value: string; hint?: string }[];
  note: string | null;
}

/** "Live since 29 Oct 2026: N requests, $X Token Factory, ..." as a headline and its parts. */
export function liveLine(p: Persistent): LiveLine {
  const t = p.totals;
  const items = [
    { label: "Requests", value: fmtInt(t.requests) },
    { label: "Teacher", value: fmtInt(t.requests_teacher) },
    t.requests_split > 0 ? { label: "Random split", value: fmtInt(t.requests_split), hint: "sent to the hosted Nano 30B" } : { label: "Random split", value: "none so far", hint: "no requests sent to the hosted Nano 30B yet" },
    t.requests_student > 0 ? { label: "Student", value: fmtInt(t.requests_student) } : { label: "Student", value: "none", hint: "Ratchet's own adapter is not connected to the live site" },
    { label: "Token Factory cost", value: fmtUsd(t.cost_usd) },
    t.scout_runs > 0 ? { label: "Scout runs", value: fmtInt(t.scout_runs) } : { label: "Scout runs", value: "none so far" },
  ];
  // Server errors: failed model calls and gateway 5xx. A refused key or a limit is not one, so `errors` is never shown.
  if (t.server_errors !== undefined) {
    items.push(t.server_errors === 0 ? { label: "Server errors", value: "none", hint: "no failed model calls" } : { label: "Server errors", value: fmtInt(t.server_errors), hint: "failed model calls" });
  }
  const tokens = t.student_tokens_in + t.student_tokens_out;
  if (tokens > 0) items.splice(5, 0, { label: "Student tokens", value: fmtInt(tokens) });
  return {
    headline: `Live since ${fmtDay(p.since ?? "")}: ${fmtInt(t.requests)} requests, ${fmtUsd(t.cost_usd)} Token Factory, ${fmtInt(t.scout_runs)} Scout runs`,
    items,
    note: p.instances > 1 ? `Kept across ${p.instances - 1} restart${p.instances === 2 ? "" : "s"} of the gateway` : null,
  };
}

/** The wording of the Night School and release pages: honest before and after the Nebius run's evidence is on the site. */
export function nightSchoolWording(d: MeasuredData): { versions: string; pipeline: string } {
  const v = rejectedVersion(d);
  if (d.nightschool.present && v) {
    const ran = `Night School ran end to end once on Nebius, by hand (${fmtDay(d.nightschool.date ?? "")}). The golden gate rejected ${v}, so it was never made live. The loop has not run nightly on live traffic.`;
    return {
      versions: `No version of Ratchet's own adapter has served on this live gateway; Ultra teaches, and a random 40% of eligible requests are answered by the hosted Nano 30B. ${ran}`,
      pipeline: ran,
    };
  }
  return {
    versions: "No version on this live gateway: the nightly loop is built and tested but has not run on live traffic. Ratchet's own adapter is not connected to it; a random 40% of eligible requests are answered by the hosted Nano 30B.",
    pipeline: "The nightly loop is built and tested but has not run on live traffic.",
  };
}

/** Live totals this small are shown beside the recorded runs instead of alone. */
export const THIN_LIVE_REQUESTS = 100;

export interface RecordedToDate {
  headline: string;
  source: string;
  date: string;
  dateEnd: string;
}

/**
 * "Recorded runs to date": what the saved runs add up to, summed from the snapshot (the recorded Scout runs and the live smoke run), for a
 * page whose live totals are thin. Every figure is a sum of values the evidence files hold; nothing is estimated.
 */
export function recordedToDate(d: MeasuredData): RecordedToDate | null {
  const runs = d.scout.runs;
  if (runs.length === 0) return null;
  const calls = runs.reduce((n, r) => n + r.llm_calls, 0);
  const usd = runs.reduce((n, r) => n + r.cost_usd, 0);
  return {
    headline: `Recorded runs to date: ${fmtInt(calls)} model calls across ${runs.length} Scout runs, ${fmtUsd(usd)}; ${fmtInt(d.smoke.requests)} requests in the live smoke run, ${fmtUsd(d.smoke.total_usd)}`,
    source: d.scout.source,
    date: d.scout.date,
    dateEnd: d.smoke.date > d.scout.date_end ? d.smoke.date : d.scout.date_end,
  };
}
