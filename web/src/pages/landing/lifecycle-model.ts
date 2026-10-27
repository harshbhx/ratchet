/**
 * "Follow one request": the request lifecycle explorer's content, as data. The stages, rules and files are the real
 * ones; the request in each scenario is an illustration, not a live call. Pure (no JSX), so
 * web/tests/lifecycle-model.test.mjs can check it: every file named here exists, every path uses known stages, and the
 * numbers in `LIFECYCLE_NUMBERS` are the defaults in the Python source they come from.
 */

export type Lane = "day" | "night";
export type Tone = "neutral" | "owned" | "rented" | "good" | "bad";
export type StageId =
  | "request"
  | "route"
  | "student"
  | "confidence"
  | "served"
  | "teacher"
  | "ledger"
  | "school"
  | "golden"
  | "shadow"
  | "canary"
  | "live"
  | "rollback";

export interface Stage {
  id: StageId;
  lane: Lane;
  title: string;
  /** A few words under the title on the map. */
  sub: string;
  tone: Tone;
  does: string;
  inputs: string;
  outputs: string;
  safeguards: string[];
  /** Repo paths that implement the stage; each must exist (the test checks). */
  files: string[];
}

/**
 * Defaults the explorer prints, with the source each comes from. The test reads each source file and checks the value.
 * `ratchet/release.py` `ReleasePolicy`, `ratchet/config.py` `Settings`, `ratchet/confidence.py` `score`,
 * `ratchet/nightschool/dataset.py` `FREQUENT`.
 */
export const LIFECYCLE_NUMBERS = {
  min_precision: 0.95,
  min_coverage: 0.25,
  tolerance: 0.02,
  min_shadow: 100,
  min_audits: 20,
  live_floor: 0.92,
  samples: 3,
  audit_rate: 0.05,
  w_prob: 0.4,
  w_agree: 0.6,
  frequent: 3,
} as const;

const N = LIFECYCLE_NUMBERS;
const pct = (x: number) => `${Math.round(x * 100)}%`;
const pts = (x: number) => `${Math.round(x * 100)} points`;

export const STAGES: Stage[] = [
  {
    id: "request",
    lane: "day",
    title: "Request",
    sub: "your app → /v1",
    tone: "neutral",
    does:
      "Takes an OpenAI chat completion request, checks it, and finds its task: the X-Ratchet-Task header, a model of the form ratchet/<task>, or the task's match rules.",
    inputs: "An OpenAI chat request and an API key",
    outputs: "A checked request, its task and its storage settings",
    safeguards: [
      "A client mistake is a 400 in OpenAI's error envelope and costs nothing",
      "API keys are compared by digest; only their names are logged",
      "Quotas and budgets are checked before any model is called",
      "End-user ids are kept only as keyed hashes",
    ],
    files: ["ratchet/gateway.py", "ratchet/openai_compat.py", "ratchet/auth.py"],
  },
  {
    id: "route",
    lane: "day",
    title: "Route",
    sub: "student allowed?",
    tone: "neutral",
    does:
      "Decides whether the student may answer at all. Pinned tasks, tool calls and inputs only the teacher reads go straight to the teacher; a task's routing rules can send more there.",
    inputs: "The task's spec and the request's shape",
    outputs: "The student path, or the teacher-only path",
    safeguards: [
      "Only an operator's task spec pins a task, never a request",
      "Pinned tasks never reach the student and are never stored",
      "Tool requests are answered by the teacher alone and never stored",
    ],
    files: ["ratchet/router.py", "ratchet/routing.py", "ratchet/tasks.py"],
  },
  {
    id: "student",
    lane: "day",
    title: "Student",
    sub: "Nano 4B + LoRA",
    tone: "owned",
    does: `Nemotron 3 Nano 4B with the live (or canary) LoRA adapter answers ${N.samples} times, with token probabilities.`,
    inputs: "The messages and the version's adapter",
    outputs: `${N.samples} sampled answers`,
    safeguards: [
      "A student failure falls back to the teacher; it never fails the request",
      "A circuit breaker skips an endpoint that keeps failing",
      "A canary version only gets its share of traffic",
    ],
    files: ["ratchet/router.py", "ratchet/providers.py", "ratchet/resilience.py"],
  },
  {
    id: "confidence",
    lane: "day",
    title: "Confidence",
    sub: "above threshold?",
    tone: "owned",
    does: `Scores the samples: ${pct(N.w_prob)} token probability and ${pct(N.w_agree)} agreement between the samples; an answer the task's format refuses gets no vote. The score is compared with a threshold calibrated for this version and task.`,
    inputs: `${N.samples} sampled answers`,
    outputs: "Serve the student's answer, or escalate",
    safeguards: [
      "Thresholds are calibrated on one half of the golden set and measured on the other",
      "A task whose spec changed goes to the teacher until a night re-scores it",
    ],
    files: ["ratchet/confidence.py", "ratchet/calibration.py"],
  },
  {
    id: "served",
    lane: "day",
    title: "Student serves",
    sub: "answer served",
    tone: "owned",
    does: `Returns the student's answer. A random ${pct(N.audit_rate)} of served answers (the default rate) are also answered by the teacher as an audit, which is how live precision is measured.`,
    inputs: "A confident answer",
    outputs: "The response, with served_by, cost and confidence",
    safeguards: [
      "A failed audit never fails a request the student answered",
      "Audits can run after the response, off the request path",
      "Audit results drive the canary and rollback rules",
    ],
    files: ["ratchet/router.py", "ratchet/sidecar.py"],
  },
  {
    id: "teacher",
    lane: "day",
    title: "Teacher answers",
    sub: "Nemotron 3 Ultra",
    tone: "rented",
    does: "Nemotron 3 Ultra on Nebius Token Factory answers, and the client receives the teacher's answer.",
    inputs: "The messages",
    outputs: "An answer, with its teacher's license on record",
    safeguards: [
      "Only teachers whose license allows distillation; a closed model stops startup",
      "Retries with backoff and a circuit breaker; a failure becomes a mapped HTTP status, never a crash",
      "An optional licensed fallback teacher",
    ],
    files: ["ratchet/router.py", "ratchet/licenses.py", "ratchet/resilience.py"],
  },
  {
    id: "ledger",
    lane: "day",
    title: "Ledger",
    sub: "provenance",
    tone: "neutral",
    does:
      "Logs every request as metadata (route, cost, confidence; never its text). A complete, valid teacher answer becomes a training example, redacted first, with its teacher and license.",
    inputs: "The request and the teacher's answer, if any",
    outputs: "A request row, and maybe an example with its hash, teacher, license and redaction count",
    safeguards: [
      "Personal data is redacted before anything is stored (on by default)",
      "Student answers are never stored as training targets",
      "store: false, no-store tasks and no-train end users only ever reduce storage",
      "An erased example is tombstoned and never stored again",
    ],
    files: ["ratchet/ledger.py", "ratchet/redact.py", "ratchet/privacy.py"],
  },
  {
    id: "school",
    lane: "night",
    title: "Night School",
    sub: "train from base",
    tone: "neutral",
    does:
      "Each night Night School fine-tunes a new LoRA adapter from the base model on every stored training example, then writes the adapter's bill of materials. Built to run as a Nebius Serverless Job: one such job has run by hand (27 Oct 2026); the nightly job is scripted and tested offline, ready to run.",
    inputs: "Teacher answers and attested human labels",
    outputs: "A candidate adapter and its BOM",
    safeguards: [
      `Golden examples are never trained on (except requests seen ${N.frequent} or more times)`,
      "No training while a release is in shadow or canary",
      "Rows from a closed teacher are dropped when the dataset is built",
      "The BOM is written once, and signed (Ed25519) when a signing key is set",
    ],
    files: ["ratchet/nightschool/orchestrate.py", "ratchet/nightschool/dataset.py", "ratchet/nightschool/train.py", "ratchet/bom.py"],
  },
  {
    id: "golden",
    lane: "night",
    title: "Golden gate",
    sub: "held-out test",
    tone: "neutral",
    does:
      "Tests the candidate on the held-out golden set: calibrate on one half, measure on the other, and compare with the live version re-scored on the same rows the same night.",
    inputs: "The candidate and the golden set",
    outputs: "Admitted to shadow, or rejected (with no live version yet, straight to a 10% canary)",
    safeguards: [
      `Precision of at least ${pct(N.min_precision)} on the held-out half`,
      `Coverage of at least ${pct(N.min_coverage)}`,
      `No regression against the live version beyond ${pts(N.tolerance)}`,
      "Per-task floors can switch off one task or reject the candidate",
    ],
    files: ["ratchet/nightschool/evaluate.py", "ratchet/release.py"],
  },
  {
    id: "shadow",
    lane: "night",
    title: "Shadow",
    sub: "silent, real traffic",
    tone: "neutral",
    does: "The candidate answers live traffic silently next to the live student. When the two disagree, the teacher arbitrates.",
    inputs: "Live traffic",
    outputs: "Promoted to canary, or rejected",
    safeguards: [
      "Never serves a client",
      `Needs at least ${N.min_shadow} answers it would have served`,
      "A disagreement the teacher could not arbitrate counts against it",
    ],
    files: ["ratchet/release.py", "ratchet/router.py"],
  },
  {
    id: "canary",
    lane: "night",
    title: "Canary",
    sub: "10% → 50% → 100%",
    tone: "neutral",
    does: "Serves 10%, then 50%, then 100% of traffic, audited by the teacher at every stage.",
    inputs: "Its share of live traffic and the teacher's audits",
    outputs: "Promoted to live, or rolled back",
    safeguards: [
      `At least ${N.min_audits} audits in a stage before it moves up`,
      "Every move is compare-and-set, so each decision is made once",
      "An operator can freeze releases; rollbacks still run",
    ],
    files: ["ratchet/release.py", "ratchet/stats.py"],
  },
  {
    id: "live",
    lane: "night",
    title: "Live",
    sub: "new version serves",
    tone: "good",
    does: "Serves every request it is confident about, until a better version replaces it.",
    inputs: "All traffic",
    outputs: "Student answers, still audited",
    safeguards: ["Audits keep measuring it, and an alert fires if its audited precision drops", "A label-free drift alarm watches its confidence"],
    files: ["ratchet/release.py", "ratchet/drift.py"],
  },
  {
    id: "rollback",
    lane: "night",
    title: "Rolled back",
    sub: "last good version",
    tone: "bad",
    does: `When the 95% Wilson upper bound of the canary's audited precision is below ${pct(N.live_floor)}, its traffic returns to the last good version and the candidate is marked rolled back.`,
    inputs: "The canary's audits",
    outputs: "Traffic back on the last good version",
    safeguards: [
      "Automatic: nobody has to be awake",
      "An opt-in anytime-valid rule (RATCHET_SEQUENTIAL=1) can decide sooner",
      "Operators can also roll back by hand, with an audit trail (python -m ratchet.admin)",
    ],
    files: ["ratchet/release.py", "ratchet/admin.py"],
  },
];

/** The map's columns per lane; a pair is a fork where the request goes one way or the other. */
export const LAYOUT: Record<Lane, (StageId | [StageId, StageId])[]> = {
  day: ["request", "route", "student", "confidence", ["served", "teacher"], "ledger"],
  night: ["school", "golden", "shadow", "canary", ["live", "rollback"]],
};

export type ScenarioId = "student" | "escalated" | "pinned" | "rollback";

export interface Scenario {
  id: ScenarioId;
  label: string;
  /** One line under the scenario picker. */
  summary: string;
  path: StageId[];
  /** What happens at each stage on the path, in this scenario. */
  notes: Partial<Record<StageId, string>>;
  outcome: { tone: Tone; text: string };
}

export const SCENARIOS: Scenario[] = [
  {
    id: "student",
    label: "Student serves",
    summary: "A routine support ticket the adapter has seen many like.",
    path: ["request", "route", "student", "confidence", "served", "ledger"],
    notes: {
      request: "A ticket-triage request, named by its X-Ratchet-Task header.",
      route: "A registered task, not pinned, no tools: the student may try.",
      student: `The live adapter answers ${N.samples} times.`,
      confidence: "The samples agree and the score clears this version's threshold for the task.",
      served: "The client gets the student's answer. This one was not drawn for an audit.",
      ledger: "One metadata row: route student, cost, confidence. No training example, because no teacher answered.",
    },
    outcome: { tone: "owned", text: "Answered by the adapter you own, at the student's cost." },
  },
  {
    id: "escalated",
    label: "Escalated",
    summary: "An unusual ticket the student is unsure about.",
    path: ["request", "route", "student", "confidence", "teacher", "ledger", "school", "golden", "shadow", "canary", "live"],
    notes: {
      request: "A ticket-triage request, as before.",
      route: "Eligible: the student tries first.",
      student: `The live adapter answers ${N.samples} times.`,
      confidence: "The samples disagree, so the score falls below the threshold and the request escalates.",
      teacher: "Nemotron 3 Ultra answers, and that is the answer the client gets.",
      ledger: "The answer is complete and valid, so it is redacted and stored with its teacher and license (OpenMDW-1.1).",
      school:
        "Tonight's adapter is trained on every stored answer, this one included, unless its hash put it in the golden set, which is only ever used to test.",
      golden: "The candidate clears the gate on the held-out half.",
      shadow: `It answers live traffic silently until ${N.min_shadow} would-serve answers are scored.`,
      canary: `10%, then 50%, then 100% of traffic, with at least ${N.min_audits} audits at each stage.`,
      live: "It becomes the live version. Requests like this one now have a better chance of staying with the student.",
    },
    outcome: { tone: "rented", text: "Answered by the teacher today; the answer becomes training data for the next adapter." },
  },
  {
    id: "pinned",
    label: "Pinned task",
    summary: "A Scout planner call: its task's spec pins it to the teacher.",
    path: ["request", "route", "teacher", "ledger"],
    notes: {
      request: "A Scout planner call, task research-plan.",
      route:
        "The task is pinned, so the student, audits and shadow are skipped. Tool requests and inputs only the teacher reads take the same path.",
      teacher: "Ultra answers, and the client gets its answer.",
      ledger:
        "A metadata row only (route pinned). Nothing becomes a training example, and Night School leaves the task's rows out. A storage opt-out (store: false) also stores nothing, though the cascade runs as usual.",
    },
    outcome: { tone: "rented", text: "Answered by the teacher. Never training data." },
  },
  {
    id: "rollback",
    label: "Bad candidate",
    summary: "A night produces an adapter that is worse on live traffic than it looked.",
    path: ["school", "golden", "shadow", "canary", "rollback"],
    notes: {
      school: "A night trains a candidate that is worse on live traffic.",
      golden: `It could have stopped here: below ${pct(N.min_precision)} precision on the held-out half, a candidate is rejected and never serves. This one passes.`,
      shadow: "It gets through shadow too.",
      canary: "At 10% of traffic, the teacher's audits start to disagree with it.",
      rollback: `The upper bound of its audited precision drops below ${pct(N.live_floor)}: rolled back. The last good version kept serving the other 90% and now serves everything again.`,
    },
    outcome: { tone: "bad", text: "Rolled back automatically. The last good version never stopped serving." },
  },
];

export const STAGE_BY_ID = Object.fromEntries(STAGES.map((s) => [s.id, s])) as Record<StageId, Stage>;
export const SCENARIO_BY_ID = Object.fromEntries(SCENARIOS.map((s) => [s.id, s])) as Record<ScenarioId, Scenario>;

/** Every stage in map order: the day lane, then the night lane, a fork's first branch first. Arrow keys follow it. */
export const MAP_ORDER: StageId[] = (["day", "night"] as const).flatMap((lane) => LAYOUT[lane].flat());

/** Where a stage sits on a scenario's path: its 1-based step, or 0 when the path does not go through it. */
export function stepOf(scenario: Scenario, id: StageId): number {
  return scenario.path.indexOf(id) + 1;
}

/** The stage after (+1) or before (-1) `id` on the scenario's path, staying at either end. */
export function stepFrom(scenario: Scenario, id: StageId, delta: 1 | -1): StageId {
  const i = scenario.path.indexOf(id);
  if (i < 0) return scenario.path[0];
  return scenario.path[Math.max(0, Math.min(scenario.path.length - 1, i + delta))];
}

/** Roving focus over the map: arrow keys move through `MAP_ORDER`, Home and End jump to its ends. */
export function moveFocus(id: StageId, key: string): StageId | null {
  const i = MAP_ORDER.indexOf(id);
  switch (key) {
    case "ArrowRight":
    case "ArrowDown":
      return MAP_ORDER[Math.min(MAP_ORDER.length - 1, i + 1)];
    case "ArrowLeft":
    case "ArrowUp":
      return MAP_ORDER[Math.max(0, i - 1)];
    case "Home":
      return MAP_ORDER[0];
    case "End":
      return MAP_ORDER[MAP_ORDER.length - 1];
    default:
      return null;
  }
}

/** "Request → Route → Teacher answers → Ledger": a scenario's path in words. */
export function pathText(scenario: Scenario): string {
  return scenario.path.map((id) => STAGE_BY_ID[id].title).join(" → ");
}

/** A GitHub link for a repo path. */
export function sourceUrl(repo: string, path: string): string {
  return `${repo}/blob/main/${path}`;
}
