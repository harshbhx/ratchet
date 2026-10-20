/**
 * Typed client for the Ratchet gateway. Shapes mirror docs/API.md and docs/SCOUT.md.
 * All paths are absolute so the SPA works at /, /scout and /dashboard.
 */

export type Mode = "mock" | "live";

export interface DayStats {
  day: number;
  requests: number;
  student_share: number;
  escalation_rate: number;
  cost_per_1k: number;
  baseline_per_1k: number;
  savings_pct: number;
  avg_latency_ms: number;
  audit_agreement: number | null;
}

export interface TaskStats {
  task: string;
  pinned: boolean;
  requests: number;
  student_share: number;
  escalation_rate: number;
  cost_usd: number;
  baseline_usd: number;
  savings_pct: number;
  avg_latency_ms: number;
}

export type VersionState = "candidate" | "rejected" | "shadow" | "canary" | "live" | "retired" | "rolled_back";

export interface VersionEval {
  golden_n?: number;
  golden_agreement?: number;
  threshold?: number;
  coverage?: number;
  precision?: number;
  test_n?: number;
  judge_pass_rate?: number;
  rechecked_day?: number;
}

export interface Version {
  id: number;
  day: number;
  base_model: string;
  adapter_uri: string;
  train_examples: number;
  dataset_hash: string;
  eval: VersionEval;
  state: VersionState;
  traffic_pct: number;
  stage_since: number;
  /** This adapter's bill of materials (GET /ratchet/versions/{id}/bom); null for versions trained before v0.4. */
  bom?: "signed" | "unsigned" | null;
}

export interface LedgerEvent {
  id?: number;
  ts?: number;
  day: number;
  kind: string;
  detail: string;
}

export interface BomSource {
  teacher_model: string;
  license: string;
  license_url: string;
  split: string;
  n: number;
}

export interface ClientStats {
  client: string;
  requests: number;
  student_share: number;
  cost_usd: number;
  baseline_usd: number;
  savings_pct: number;
}

export interface PrivacyStats {
  redaction: boolean;
  examples: number;
  redacted_examples: number;
  placeholders: number;
}

export interface SecurityStats {
  api_keys: boolean;
  daily_requests_per_key: number | null;
  bom_signing: boolean;
  bom_key_id: string | null;
}

export interface RedactPreview {
  text: string;
  found: Record<string, number>;
  enabled: boolean;
}

export interface Stats {
  mode: Mode;
  day: number;
  teacher: string;
  student_base: string;
  days: DayStats[];
  tasks: TaskStats[];
  versions: Version[];
  events: LedgerEvent[];
  totals: { requests: number; cost_usd: number; baseline_usd: number; saved_usd: number; savings_pct: number };
  bill_of_materials: { generated_at: number; examples_sha256: string; sources: BomSource[] };
  // v0.4: optional so the UI still renders against an older gateway.
  clients?: ClientStats[];
  privacy?: PrivacyStats;
  security?: SecurityStats;
}

export type Workload = "tickets" | "research" | "mixed";

export interface NightReport {
  trained: boolean;
  version: number | null;
  admitted: boolean;
  reason: string;
  eval: VersionEval | null;
}

// ---------- Scout ----------
export type Agent = "planner" | "searcher" | "triage" | "reader" | "writer";
export type ServedBy = "student" | "teacher" | "tool";

export interface SearchResult {
  id: number;
  title: string;
  url: string;
  snippet: string;
}
export interface Citation {
  n: number;
  title: string;
  url: string;
}

export interface StepBase {
  type: "step";
  agent: Agent;
  task: string | null;
  served_by: ServedBy;
  model: string;
  confidence: number | null;
  escalated: boolean;
  cost_usd: number;
  baseline_usd: number;
  latency_ms: number;
}
export type PlannerStep = StepBase & { agent: "planner"; detail: { queries: string[] } };
export type SearcherStep = StepBase & { agent: "searcher"; detail: { query: string; results: SearchResult[] } };
export type TriageStep = StepBase & {
  agent: "triage";
  detail: { id: number; url: string; title: string; relevant: boolean; source_type: string; reason: string };
};
export type ReaderStep = StepBase & {
  agent: "reader";
  detail: { id: number; url: string; answers_question: boolean; fact_type: string; key_fact: string };
};
export type WriterStep = StepBase & { agent: "writer"; detail: { answer: string; citations: Citation[] } };
export type ScoutStep = PlannerStep | SearcherStep | TriageStep | ReaderStep | WriterStep;

export interface ScoutTotals {
  calls: number;
  llm_calls: number;
  student_calls: number;
  teacher_calls: number;
  cost_usd: number;
  baseline_usd: number;
  savings_pct: number;
  wall_ms: number;
}

export type ScoutEvent =
  | { type: "start"; run_id: string; question: string; mode: Mode }
  | ScoutStep
  | { type: "done"; run_id: string; answer: string; citations: Citation[]; totals: ScoutTotals }
  | { type: "error"; message: string };

export interface ScoutExamples {
  mode: Mode;
  examples: string[];
  free_text: boolean;
}
export interface ScoutStatus {
  mode: Mode;
  budget_usd: number | null;
  spent_today_usd: number;
  runs_today: number;
  runs_per_hour_per_ip: number;
  max_runs_per_day: number | null;
  tavily: boolean;
}

// ---------- fetch helpers ----------
export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, { ...init, headers: { "content-type": "application/json", ...(init?.headers || {}) } });
  if (!r.ok) {
    let msg = `Request failed (${r.status})`;
    try {
      const body = await r.json();
      msg = body.detail || body.message || msg;
    } catch {
      /* not JSON */
    }
    throw new ApiError(r.status, msg);
  }
  return r.json() as Promise<T>;
}

export const api = {
  stats: () => json<Stats>("/ratchet/stats"),
  demoDay: (workload: Workload, requests = 1500) =>
    json<{ day: number; workload: Workload; night: NightReport }>(
      `/ratchet/demo/day?workload=${encodeURIComponent(workload)}&requests=${requests}`,
      { method: "POST" },
    ),
  breakNext: () => json<{ corrupt: string }>("/ratchet/demo/break-next", { method: "POST" }),
  reset: () => json<{ status: string }>("/ratchet/demo/reset", { method: "POST" }),
  redact: (text: string) => json<RedactPreview>("/ratchet/redact", { method: "POST", body: JSON.stringify({ text }) }),
  scoutExamples: () => json<ScoutExamples>("/scout/examples"),
  scoutStatus: () => json<ScoutStatus>("/scout/status"),
  scoutRun: (question: string) =>
    json<{ events: ScoutEvent[] }>("/scout/run", { method: "POST", body: JSON.stringify({ question }) }),
};

/**
 * Stream a Scout run over Server-Sent Events. Falls back to POST /scout/run (replayed with
 * short gaps) when EventSource is unavailable or fails before the first event.
 * Returns a cancel function.
 */
export function streamScout(question: string, onEvent: (e: ScoutEvent) => void): () => void {
  let closed = false;
  let started = false;
  let es: EventSource | null = null;

  const finish = () => {
    closed = true;
    es?.close();
  };

  const fallback = async () => {
    try {
      const { events } = await api.scoutRun(question);
      for (const ev of events) {
        if (closed) return;
        onEvent(ev);
        await new Promise((r) => setTimeout(r, ev.type === "step" ? 90 : 0));
      }
    } catch (e) {
      if (!closed) onEvent({ type: "error", message: e instanceof Error ? e.message : "Scout failed." });
    } finally {
      finish();
    }
  };

  if (typeof EventSource === "undefined") {
    void fallback();
    return finish;
  }
  es = new EventSource(`/scout/stream?q=${encodeURIComponent(question)}`);
  es.onmessage = (m) => {
    if (closed) return;
    let ev: ScoutEvent;
    try {
      ev = JSON.parse(m.data);
    } catch {
      return;
    }
    started = true;
    onEvent(ev);
    if (ev.type === "done" || ev.type === "error") finish();
  };
  es.onerror = () => {
    if (closed) return;
    es?.close();
    if (!started) void fallback();
    else {
      onEvent({ type: "error", message: "The connection to Scout was lost." });
      finish();
    }
  };
  return finish;
}
