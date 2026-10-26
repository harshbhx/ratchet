/**
 * Typed client for the Ratchet gateway. Shapes mirror docs/API.md and docs/SCOUT.md.
 * All paths are absolute so the SPA works at /, /scout and /dashboard.
 */

import type { DemoInfo, Links, Upstream } from "./surface-model";
import { gatewayFetch } from "./waking-store";

export type { DemoInfo, Links, Upstream, UpstreamCall } from "./surface-model";

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
  /** v0.5: escalations a stand-in answered because the teacher was unavailable (ADR 0019). */
  degraded?: number;
  fallback_teacher?: number;
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
  /** v0.5: how many of the task's requests took each route (`student`, `student_breaker`, ...; router.ROUTES). */
  routes?: Record<string, number>;
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

/** v0.5 (FR-7.40): which model answers a student call now. `string` too: a newer gateway may add a kind. */
export type StudentServingKind = "mock" | "endpoint" | "hosted_fallback" | "none" | (string & {});

export interface StudentServing {
  /** `mock` models; our adapter on the student `endpoint`; the untuned `hosted_fallback` (endpoint off); `none` yet. */
  kind: StudentServingKind;
  /** The served model id: the version's adapter, or the fallback id. null without a version. */
  model: string | null;
  /** The live version, or the first student's canary on a cold start. */
  version: number | null;
  /** The confidence threshold the serving model is held to (the global one for the hosted fallback). */
  threshold: number | null;
  /** A certificate covers the serving model: never for the hosted fallback. */
  certified: boolean;
  /** The daily endpoint windows (W10, RATCHET_STUDENT_WINDOWS); null or absent when none are set. */
  window?: { open: boolean; next_start_utc: string | null } | null;
}

export interface Stats {
  mode: Mode;
  day: number;
  /** v0.5: the other surface's URLs, the shared simulation's limits and the last real upstream call per role. Absent on an older gateway. */
  links?: Links;
  demo?: DemoInfo;
  upstream?: Upstream;
  teacher: string;
  student_base: string;
  /** v0.5: the hosted, untuned model that answers student calls while the GPU endpoint is off (RATCHET_FALLBACK_STUDENT). */
  fallback_student?: string;
  student_serving?: StudentServing;
  days: DayStats[];
  tasks: TaskStats[];
  versions: Version[];
  events: LedgerEvent[];
  /** `cost_model` (v0.5) is always `per_token`: these totals price the student per token. */
  totals: { requests: number; cost_usd: number; baseline_usd: number; saved_usd: number; savings_pct: number; cost_model?: CostModel };
  bill_of_materials: { generated_at: number; examples_sha256: string; sources: BomSource[] };
  // v0.4: optional so the UI still renders against an older gateway.
  clients?: ClientStats[];
  privacy?: PrivacyStats;
  security?: SecurityStats;
  // v0.5: the cost report's headline numbers (the full report is GET /ratchet/cost).
  cost?: CostSummary;
}

// ---------- cost accounting (GET /ratchet/cost, docs/API.md#cost, ADR 0023) ----------
export type CostModel = "per_token" | "gpu_hours";
/** `string` too: a newer gateway may add a recommendation this build does not know yet. */
export type Recommendation = "self_host" | "hosted_fallback" | "scale_to_zero" | "insufficient_data" | (string & {});

/** One way of pricing the student. `savings_usd` is negative when Ratchet cost more than all-teacher (never clamped). */
export interface CostView {
  total_usd: number;
  savings_usd: number;
  /** null without an all-teacher baseline (no requests in the window). */
  savings_pct: number | null;
}

export interface TokenPrice {
  input: number;
  output: number;
}

/** The list prices the report used. Unverified until the day-1 checks pass (`verified`, RATCHET_PRICES_VERIFIED). */
export interface CostPrices {
  verified: boolean;
  label?: string;
  student_gpu_usd_per_hour?: number;
  student_gpus?: number;
  student_endpoint_usd_per_hour?: number;
  train_gpu_usd_per_hour?: number;
  train_gpus?: number;
  teacher_model?: string;
  teacher_usd_per_m_tokens?: TokenPrice;
  student_usd_per_m_tokens?: TokenPrice;
  fallback_student_model?: string;
  fallback_student_usd_per_m_tokens?: TokenPrice;
}

/** One ledger day with requests or compute. The savings shares are null on a day without requests. */
export interface CostDay {
  day: number;
  requests: number;
  student_served: number;
  teacher_usd: number;
  student_per_token_usd: number;
  endpoint_hours: number;
  endpoint_usd: number;
  training_usd: number;
  baseline_usd: number;
  savings_pct_per_token: number | null;
  savings_pct_gpu_hours: number | null;
}

export interface CostOpenInterval {
  id: number;
  kind: string;
  start_ts: number;
  hours: number;
}

/**
 * GET /ratchet/cost. `views` and `recommendation` are what the dashboard needs; everything else is optional so a
 * gateway that sends less still renders.
 */
export interface CostReport {
  window?: { since_day: number | null; until_day: number; now: number };
  model?: CostModel;
  headline?: CostView & { model: CostModel };
  prices?: CostPrices;
  requests?: number;
  student_served?: number;
  teacher_served?: number;
  teacher_usd?: number;
  student_per_token_usd?: number;
  hosted_student_usd?: number;
  student_served_off_endpoint?: number;
  baseline_usd?: number;
  split_unknown_requests?: number;
  split_unknown_usd?: number;
  endpoint_hours?: number;
  endpoint_usd?: number;
  /** The endpoint price break-even and the crossover divide: the rate the window's intervals recorded (else the configured one). */
  endpoint_usd_per_hour?: number;
  idle_hours?: number;
  idle_share?: number | null;
  /** How idle time, the observed volume and off-endpoint answers were counted: `hour` on a live ledger, `day` on the synthetic clock. */
  idle_granularity?: string;
  training_hours?: number;
  training_usd?: number;
  views: { per_token: CostView; gpu_hours: CostView };
  avoided_teacher_usd_per_student_request?: number | null;
  break_even_student_requests_per_gpu_hour?: number | null;
  hosted_fallback_usd_per_request?: number | null;
  self_host_beats_hosted_above_requests_per_hour?: number | null;
  observed_student_requests_per_endpoint_hour?: number | null;
  utilization_vs_break_even?: number | null;
  recommendation: Recommendation;
  explanation?: string;
  open_intervals?: CostOpenInterval[];
  warnings?: string[];
  daily?: CostDay[];
  daily_truncated?: boolean;
}

/** The `cost` block of /ratchet/stats: the report's headline numbers for all days. */
export interface CostSummary {
  model: CostModel;
  endpoint_hours: number;
  gpu_hours_savings_pct: number | null;
  break_even_student_requests_per_gpu_hour: number | null;
  recommendation: Recommendation;
}

// ---------- task registry (GET /ratchet/tasks, docs/API.md#tasks, ADR 0022) ----------
/** `string` too: a newer gateway may add a source this build does not know yet. */
export type TaskSource = "builtin" | "file" | "api" | "auto" | (string & {});
export type UnknownTaskPolicy = "distill" | "teacher" | "reject" | (string & {});

/**
 * One field of a task spec as `taskspec.document` writes it: its comparator `type` and every option that is not its
 * default (`compare.FieldSpec`). `item` is a type name or a nested spec; `fields` holds a record's sub-fields.
 */
export interface FieldDoc {
  type: string;
  required?: boolean;
  nullable?: boolean;
  values?: string[];
  aliases?: Record<string, string>;
  abs_tol?: number;
  rel_tol?: number;
  decimal?: string;
  percent?: string;
  order?: string[];
  tolerance?: number;
  mode?: string;
  strip_suffixes?: string[];
  day_first?: boolean;
  granularity?: string;
  item?: string | FieldDoc;
  min_jaccard?: number;
  split?: string;
  empty_values?: (string | number | boolean | null)[];
  fields?: Record<string, FieldDoc>;
  key?: string[];
  ordered?: boolean;
  k?: number;
  [option: string]: unknown;
}

/** One registered task: its spec (the first eleven keys, what PUT takes back), where it came from and its traffic. */
export interface TaskEntry {
  name: string;
  description: string;
  format: string;
  fields: Record<string, FieldDoc>;
  /** The SQL checker spec (format "sql"), else null. */
  sql: Record<string, unknown> | null;
  pinned: boolean;
  store: boolean;
  /** Per-task privacy settings (taskspec `privacy`), an empty object when none. */
  privacy: Record<string, unknown>;
  match: Record<string, unknown>;
  routing: Record<string, unknown>;
  policy: Record<string, unknown>;
  tools: string;
  clients: string[];
  source: TaskSource;
  spec_hash: string;
  verifiable: boolean;
  requests: number;
  student_share: number;
  recalibrating: boolean;
  /** GET /ratchet/tasks/{name} only, for an API task: when it was written and by which admin (the key's name). */
  created_ts?: number;
  updated_ts?: number;
  updated_by?: string;
}

export interface TaskRegistry {
  unknown_tasks: UnknownTaskPolicy;
  max_tasks: number;
  tasks: TaskEntry[];
  /** Names requests used that are not registered, with their request counts. */
  unregistered: { name: string; requests: number }[];
}

/** POST /ratchet/tasks/validate: always 200; `registered` is the source of the task that has the name now. */
export interface TaskValidation {
  valid: boolean;
  errors: string[];
  tasks: { name: string; spec_hash: string; registered: TaskSource | null }[];
}

export interface TaskSaved {
  task: TaskEntry;
  spec_hash: string;
  changed: boolean;
  created: boolean;
}

export interface TaskDeleted {
  deleted: string;
  spec_hash: string;
  examples_kept: number;
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

import type { Recording, RecordingSummary } from "@/lib/recording-model";

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
  /** v0.5: a step whose `model` is this id came from the hosted fallback, not our adapter (FR-7.40). */
  fallback_student?: string;
  student_serving?: StudentServing;
  /** ADR 0120: what the public can still use today, the judges' reserve excluded; null without a cap or budget (mock mode). */
  public_runs_left_today?: number | null;
  public_budget_left_usd?: number | null;
  /** ADR 0120: judge access. Read through `parseJudge` (src/pages/scout/judge-model.ts), which treats a missing block as off. */
  judge?: { enabled?: boolean; session?: boolean; reserved_runs_per_day?: number | null; runs_today?: number; runs_left_today?: number | null };
}

// ---------- fetch helpers ----------
export class ApiError extends Error {
  status: number;
  /** The parsed error body, when it was JSON (a 422 from PUT /ratchet/tasks/{name} carries `errors`). */
  body: unknown;
  constructor(status: number, message: string, body?: unknown) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await gatewayFetch(path, { ...init, headers: { "content-type": "application/json", ...(init?.headers || {}) } });
  if (!r.ok) {
    let msg = `Request failed (${r.status})`;
    let body: unknown;
    try {
      body = await r.json();
      const b = body as { detail?: unknown; message?: unknown } | null;
      // Only a string becomes the message: FastAPI's own 422 sends `detail` as a list of objects.
      if (typeof b?.detail === "string" && b.detail) msg = b.detail;
      else if (typeof b?.message === "string" && b.message) msg = b.message;
    } catch {
      /* not JSON */
    }
    throw new ApiError(r.status, msg, body);
  }
  return r.json() as Promise<T>;
}

/**
 * The header for a management call. The admin key is passed in by the caller, which holds it in memory only; it is
 * never written to storage, a cookie or a URL. An empty key sends no header (mock mode without RATCHET_ADMIN_KEYS).
 */
function adminHeaders(key: string | undefined): Record<string, string> {
  const k = key?.trim();
  return k ? { authorization: `Bearer ${k}` } : {};
}

/**
 * The judge code goes out once, in the JSON body of POST /scout/judge, never in a URL or header (access logs record both).
 * The caller holds it in an input's memory and clears it after the call; the gateway answers with an HttpOnly cookie that
 * this code cannot read. Resolves to the HTTP status (0 when the request got no answer), never to the code.
 */
async function judgeCall(method: "POST" | "DELETE", body?: string): Promise<number> {
  try {
    const r = await fetch("/scout/judge", {
      method,
      credentials: "same-origin",
      headers: body === undefined ? {} : { "content-type": "application/json" },
      body,
    });
    return r.status;
  } catch {
    return 0;
  }
}

const taskPath = (name: string) => `/ratchet/tasks/${encodeURIComponent(name)}`;

export const api = {
  stats: () => json<Stats>("/ratchet/stats"),
  /** 404 on a gateway older than v0.5 (the dashboard then hides its Economics panel). */
  cost: (days?: number) => json<CostReport>(days == null ? "/ratchet/cost" : `/ratchet/cost?days=${encodeURIComponent(days)}`),
  demoDay: (workload: Workload, requests = 1500) =>
    json<{ day: number; workload: Workload; night: NightReport; auto_reset?: string | null }>(
      `/ratchet/demo/day?workload=${encodeURIComponent(workload)}&requests=${requests}`,
      { method: "POST" },
    ),
  breakNext: () => json<{ corrupt: string }>("/ratchet/demo/break-next", { method: "POST" }),
  /** Mock only: the student endpoint refuses connections for `seconds` (the gateway clamps it to 5..300). */
  studentOutage: (seconds: number) =>
    json<{ seconds: number; until: number }>(`/ratchet/demo/student-outage?seconds=${encodeURIComponent(seconds)}`, { method: "POST" }),
  /** 404 on a gateway older than v0.5 (the dashboard then hides its Task registry panel). */
  tasks: () => json<TaskRegistry>("/ratchet/tasks"),
  task: (name: string) => json<TaskEntry>(taskPath(name)),
  /** A dry run (always 200 with a valid gateway); needs an admin key unless the gateway is in mock mode without them. */
  validateTask: (spec: Record<string, unknown>, adminKey?: string) =>
    json<TaskValidation>("/ratchet/tasks/validate", { method: "POST", body: JSON.stringify(spec), headers: adminHeaders(adminKey) }),
  putTask: (name: string, spec: Record<string, unknown>, adminKey?: string) =>
    json<TaskSaved>(taskPath(name), { method: "PUT", body: JSON.stringify(spec), headers: adminHeaders(adminKey) }),
  deleteTask: (name: string, adminKey?: string) => json<TaskDeleted>(taskPath(name), { method: "DELETE", headers: adminHeaders(adminKey) }),
  reset: () => json<{ status: string }>("/ratchet/demo/reset", { method: "POST" }),
  redact: (text: string) => json<RedactPreview>("/ratchet/redact", { method: "POST", body: JSON.stringify({ text }) }),
  scoutExamples: () => json<ScoutExamples>("/scout/examples"),
  scoutStatus: () => json<ScoutStatus>("/scout/status", { credentials: "same-origin" }),
  /** Unlock the judges' reserve (ADR 0120). Resolves to the HTTP status: 200 ok, 400, 401, 404, 429, or 0 with no answer. */
  judgeUnlock: (code: string) => judgeCall("POST", JSON.stringify({ code })),
  /** End the judge session (clears the cookie). Resolves to the HTTP status. */
  judgeSignOut: () => judgeCall("DELETE"),
  /** Recorded live runs, newest first (FR-10.40): read-only, no model call, no budget. */
  scoutRecordings: () => json<{ recordings: RecordingSummary[] }>("/scout/recorded"),
  scoutRecording: (id: string) => json<Recording>(`/scout/recorded/${encodeURIComponent(id)}`),
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
