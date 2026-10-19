/**
 * Scout run state: a reducer over the SSE event stream (docs/SCOUT.md) plus pure selectors.
 * Parallel agents (triage, reader) may arrive in any order; everything is keyed by result id.
 */
import type {
  Citation,
  Mode,
  PlannerStep,
  ReaderStep,
  ScoutEvent,
  ScoutStep,
  ScoutTotals,
  SearcherStep,
  ServedBy,
  TriageStep,
  WriterStep,
} from "@/lib/api";

/** Server-side cap on reader calls per question (`Scout.max_reads`, docs/SCOUT.md). */
export const READ_LIMIT = 6;
/** Results per query we draw as placeholders before search returns (Tavily `max_results` default). */
export const PLACEHOLDER_PER_QUERY = 4;
export const PLACEHOLDER_QUERIES = 3;

export type RunStatus = "idle" | "running" | "done" | "error" | "stopped";

export interface ResultInfo {
  id: number;
  title: string;
  url: string;
  snippet: string;
  query: string | null;
  qi: number; // query index, -1 when unknown
}

export interface RunState {
  status: RunStatus;
  question: string;
  mode: Mode | null;
  runId: string | null;
  startedAt: number | null;
  endedAt: number | null;
  steps: ScoutStep[];
  planner: PlannerStep | null;
  queries: string[];
  searches: Record<string, SearcherStep>;
  results: Record<number, ResultInfo>;
  triage: Record<number, TriageStep>;
  reader: Record<number, ReaderStep>;
  writer: WriterStep | null;
  done: { answer: string; citations: Citation[]; totals: ScoutTotals } | null;
  error: string | null;
}

export const initialRun: RunState = {
  status: "idle",
  question: "",
  mode: null,
  runId: null,
  startedAt: null,
  endedAt: null,
  steps: [],
  planner: null,
  queries: [],
  searches: {},
  results: {},
  triage: {},
  reader: {},
  writer: null,
  done: null,
  error: null,
};

export type RunAction =
  | { type: "begin"; question: string; at: number }
  | { type: "event"; ev: ScoutEvent; at: number }
  | { type: "stop"; at: number }
  | { type: "fail"; message: string; at: number };

const num = (x: unknown) => (typeof x === "number" && Number.isFinite(x) ? x : 0);
const str = (x: unknown) => (x == null ? "" : String(x));

function addResult(results: Record<number, ResultInfo>, r: Partial<ResultInfo> & { id: number }): Record<number, ResultInfo> {
  const prev = results[r.id];
  if (prev && prev.qi >= 0) {
    // Keep the first query a result appeared under; fill gaps only.
    return { ...results, [r.id]: { ...prev, title: prev.title || str(r.title), url: prev.url || str(r.url) } };
  }
  return {
    ...results,
    [r.id]: {
      id: r.id,
      title: str(r.title ?? prev?.title),
      url: str(r.url ?? prev?.url),
      snippet: str(r.snippet ?? prev?.snippet),
      query: r.query ?? prev?.query ?? null,
      qi: r.qi ?? prev?.qi ?? -1,
    },
  };
}

function onStep(s: RunState, st: ScoutStep): RunState {
  const steps = [...s.steps, st];
  switch (st.agent) {
    case "planner": {
      const qs = Array.isArray(st.detail?.queries) ? st.detail.queries.map(str) : [];
      const merged = [...qs, ...s.queries.filter((q) => !qs.includes(q))];
      return { ...s, steps, planner: st, queries: merged };
    }
    case "searcher": {
      const query = str(st.detail?.query);
      const queries = s.queries.includes(query) ? s.queries : [...s.queries, query];
      const qi = queries.indexOf(query);
      let results = s.results;
      for (const r of Array.isArray(st.detail?.results) ? st.detail.results : []) {
        results = addResult(results, { id: num(r.id), title: r.title, url: r.url, snippet: r.snippet, query, qi });
      }
      return { ...s, steps, queries, searches: { ...s.searches, [query]: st }, results };
    }
    case "triage": {
      const id = num(st.detail?.id);
      const results = s.results[id] ? s.results : addResult(s.results, { id, title: st.detail?.title, url: st.detail?.url });
      return { ...s, steps, results, triage: { ...s.triage, [id]: st } };
    }
    case "reader": {
      const id = num(st.detail?.id);
      const results = s.results[id] ? s.results : addResult(s.results, { id, url: st.detail?.url });
      return { ...s, steps, results, reader: { ...s.reader, [id]: st } };
    }
    case "writer":
      return { ...s, steps, writer: st };
    default:
      return { ...s, steps };
  }
}

export function runReducer(s: RunState, a: RunAction): RunState {
  switch (a.type) {
    case "begin":
      return { ...initialRun, status: "running", question: a.question, mode: s.mode, startedAt: a.at };
    case "stop":
      return s.status === "running" ? { ...s, status: "stopped", endedAt: a.at } : s;
    case "fail":
      return { ...s, status: "error", error: a.message, endedAt: a.at };
    case "event": {
      if (s.status !== "running") return s;
      const ev = a.ev;
      if (!ev || typeof ev !== "object") return s;
      if (ev.type === "start") return { ...s, runId: ev.run_id, mode: ev.mode ?? s.mode, question: ev.question || s.question };
      if (ev.type === "step") return onStep(s, ev);
      if (ev.type === "done")
        return {
          ...s,
          status: "done",
          endedAt: a.at,
          done: { answer: str(ev.answer), citations: Array.isArray(ev.citations) ? ev.citations : [], totals: ev.totals },
        };
      if (ev.type === "error") return { ...s, status: "error", error: str(ev.message) || "Scout stopped.", endedAt: a.at };
      return s;
    }
  }
}

// ---------------------------------------------------------------- selectors

export type Phase = "idle" | "planning" | "searching" | "triage" | "reading" | "writing" | "done" | "error" | "stopped";
export type NodeState = "idle" | "queued" | "working" | "done" | "skipped";

export interface QueryGroup {
  query: string | null;
  qi: number;
  step: SearcherStep | null;
  ids: number[] | null; // null until search returns
}

export interface LiveTotals {
  llm: number;
  student: number;
  teacher: number;
  escalated: number;
  cost: number;
  baseline: number;
  saved: number; // 0..1
  final: boolean;
}

export interface AgentRow {
  agent: "planner" | "searcher" | "triage" | "reader" | "writer";
  calls: number;
  student: number;
  escalated: number;
  cost: number;
  baseline: number;
  latency: number; // mean ms
}

export interface Derived {
  phase: Phase;
  groups: QueryGroup[];
  order: number[];
  searchesDone: boolean;
  triageDone: boolean;
  relevant: number[]; // sorted by id
  readQueue: number[]; // the ones the reader will (or did) read
  notRead: number[]; // relevant but beyond the reader limit (known only once done)
  readerDone: boolean;
  totals: LiveTotals;
  perAgent: AgentRow[];
  citedByUrl: Record<string, number>;
}

export function isLlm(servedBy: ServedBy) {
  return servedBy === "student" || servedBy === "teacher";
}

export function derive(s: RunState): Derived {
  const groups: QueryGroup[] = s.queries.map((query, qi) => {
    const step = s.searches[query] ?? null;
    return {
      query,
      qi,
      step,
      ids: step
        ? Object.values(s.results)
            .filter((r) => r.qi === qi)
            .map((r) => r.id)
        : null,
    };
  });
  // Keep server order inside a group.
  for (const g of groups) {
    if (!g.step || !g.ids) continue;
    const pos = new Map((g.step.detail?.results ?? []).map((r, i) => [num(r.id), i]));
    g.ids.sort((a, b) => (pos.get(a) ?? 99) - (pos.get(b) ?? 99));
  }
  const grouped = new Set(groups.flatMap((g) => g.ids ?? []));
  const orphans = Object.values(s.results)
    .filter((r) => !grouped.has(r.id))
    .map((r) => r.id)
    .sort((a, b) => a - b);
  const order = [...groups.flatMap((g) => g.ids ?? []), ...orphans];

  const searchesDone = !!s.planner && groups.length > 0 && groups.every((g) => g.step);
  const triageDone = searchesDone && order.length > 0 && order.every((id) => s.triage[id]);
  const relevant = order.filter((id) => s.triage[id]?.detail?.relevant === true).sort((a, b) => a - b);
  const readIds = Object.keys(s.reader).map(Number);
  const queue = new Set([...relevant.slice(0, READ_LIMIT), ...readIds]);
  const readQueue = [...queue].sort((a, b) => a - b);
  const finished = s.status === "done";
  const notRead = finished ? relevant.filter((id) => !s.reader[id]) : relevant.filter((id) => !queue.has(id));
  const readerDone = triageDone && readQueue.every((id) => s.reader[id]);

  let phase: Phase;
  if (s.status === "idle") phase = "idle";
  else if (s.status === "done") phase = "done";
  else if (s.status === "error") phase = "error";
  else if (s.status === "stopped") phase = "stopped";
  else if (!s.planner) phase = "planning";
  else if (!searchesDone) phase = "searching";
  else if (!triageDone) phase = "triage";
  else if (!readerDone) phase = "reading";
  else phase = "writing";

  const llm = s.steps.filter((st) => isLlm(st.served_by));
  const student = llm.filter((st) => st.served_by === "student").length;
  const cost = llm.reduce((a, st) => a + num(st.cost_usd), 0);
  const baseline = llm.reduce((a, st) => a + num(st.baseline_usd), 0);
  const t = s.done?.totals;
  const totals: LiveTotals = t
    ? {
        llm: num(t.llm_calls),
        student: num(t.student_calls),
        teacher: num(t.teacher_calls),
        escalated: llm.filter((st) => st.escalated).length,
        cost: num(t.cost_usd),
        baseline: num(t.baseline_usd),
        saved: Math.max(0, num(t.savings_pct)),
        final: true,
      }
    : {
        llm: llm.length,
        student,
        teacher: llm.length - student,
        escalated: llm.filter((st) => st.escalated).length,
        cost,
        baseline,
        saved: baseline > 0 ? Math.max(0, 1 - cost / baseline) : 0,
        final: false,
      };

  const agents: AgentRow["agent"][] = ["planner", "searcher", "triage", "reader", "writer"];
  const perAgent = agents.map((agent) => {
    const xs = s.steps.filter((st) => st.agent === agent);
    return {
      agent,
      calls: xs.length,
      student: xs.filter((st) => st.served_by === "student").length,
      escalated: xs.filter((st) => st.escalated).length,
      cost: xs.reduce((a, st) => a + num(st.cost_usd), 0),
      baseline: xs.reduce((a, st) => a + num(st.baseline_usd), 0),
      latency: xs.length ? xs.reduce((a, st) => a + num(st.latency_ms), 0) / xs.length : 0,
    };
  });

  const citations = s.done?.citations ?? s.writer?.detail?.citations ?? [];
  const citedByUrl: Record<string, number> = {};
  for (const c of citations) if (c?.url) citedByUrl[c.url] = num(c.n);

  return { phase, groups, order, searchesDone, triageDone, relevant, readQueue, notRead, readerDone, totals, perAgent, citedByUrl };
}

/** Per-agent node state for the graph and lane headers. */
export function agentState(s: RunState, d: Derived, agent: AgentRow["agent"]): NodeState {
  const running = s.status === "running";
  const halted = s.status === "error" || s.status === "stopped";
  const done = (() => {
    switch (agent) {
      case "planner":
        return !!s.planner;
      case "searcher":
        return d.searchesDone;
      case "triage":
        return d.triageDone;
      case "reader":
        return d.readerDone || (s.status === "done" && d.triageDone);
      case "writer":
        return !!s.writer || !!s.done;
    }
  })();
  if (done) return "done";
  if (s.status === "idle") return "idle";
  const active = (() => {
    switch (agent) {
      case "planner":
        return true;
      case "searcher":
        return !!s.planner;
      case "triage":
        return d.order.length > 0;
      case "reader":
        return d.triageDone;
      case "writer":
        return d.readerDone;
    }
  })();
  if (halted) return "skipped";
  if (running && active) return "working";
  return "queued";
}

/** One-line progress text for the aria-live status and the visible status line. */
export function phaseText(s: RunState, d: Derived): string {
  const tri = Object.keys(s.triage).length;
  const rd = Object.keys(s.reader).length;
  switch (d.phase) {
    case "idle":
      return "";
    case "planning":
      return "Planner is writing search queries";
    case "searching": {
      const n = d.groups.filter((g) => g.step).length;
      return `Searching the web: ${n} of ${d.groups.length} queries`;
    }
    case "triage":
      return `Triage is checking sources: ${tri} of ${d.order.length}`;
    case "reading":
      return `Reader is pulling key facts: ${rd} of ${d.readQueue.length}`;
    case "writing":
      return "Writer is drafting the answer";
    case "done":
      return `Done. ${d.totals.student} of ${d.totals.llm} model calls answered by your model.`;
    case "error":
      return "Scout stopped.";
    case "stopped":
      return "Run stopped.";
  }
}

/** Screen-reader announcement: changes once per phase (not per call), plus the final result. */
export function announceText(s: RunState, d: Derived): string {
  switch (d.phase) {
    case "idle":
      return "";
    case "planning":
      return `Running Scout on: ${s.question}. Planner is writing search queries.`;
    case "searching":
      return `Searching the web with ${d.groups.length} queries.`;
    case "triage":
      return `Triage is checking ${d.order.length} sources.`;
    case "reading":
      return `Reader is reading ${d.readQueue.length} relevant sources.`;
    case "writing":
      return "Writer is drafting the answer.";
    case "done":
      return `Done. ${d.totals.student} of ${d.totals.llm} model calls answered by your model, ${Math.round(d.totals.saved * 100)}% cheaper than all-Ultra. The answer is below.`;
    case "error":
      return "Scout stopped.";
    case "stopped":
      return "Run stopped.";
  }
}
