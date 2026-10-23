/**
 * The judges' console, chapter by chapter: the section ids, the rail's groups and labels, and what the guided tour says
 * at each stop. Pure data, no JSX (the tour's step list is built from it).
 */

export type ChapterGroup = "start" | "outside" | "inside" | "check";

export interface JudgeChapter {
  id: string;
  group: ChapterGroup;
  /** The rail's label. */
  label: string;
  /** The guided tour's title and one or two plain sentences at this stop. */
  tourTitle: string;
  tour: string;
  /** Kept at the top of the rail. */
  pinned?: boolean;
}

export const GROUP_LABEL: Record<ChapterGroup, string> = {
  start: "Start",
  outside: "Use it",
  inside: "Inside the gateway",
  check: "Check it",
};

export const JUDGE_CHAPTERS: JudgeChapter[] = [
  {
    id: "start",
    group: "start",
    label: "The 90-second path",
    tourTitle: "Start here",
    tour: "Three clicks show the whole idea: send a request, run a day with a bad deploy, then read what is real.",
  },
  {
    id: "real",
    group: "start",
    label: "What's real",
    pinned: true,
    tourTitle: "What is real, and what is not",
    tour: "Every claim has one status: real, measured, simulation, pending or not run. The exam results are measured and can be recomputed from the saved answers.",
  },
  {
    id: "exams",
    group: "start",
    label: "The exams",
    tourTitle: "Two exams, two authors",
    tour: "Exam 1 was written by the teacher and exam 2 by a different model, for testing only. Both adapters, the teacher and the untrained model took both; the charts show every score with its range.",
  },
  {
    id: "client",
    group: "outside",
    label: "Send a request",
    tourTitle: "A request arrives",
    tour: "Your app keeps its OpenAI client and changes one line: the base URL. Send one here and see who answered it.",
  },
  {
    id: "api",
    group: "outside",
    label: "API surface",
    tourTitle: "The API surface",
    tour: "The OpenAI routes, Ratchet's own read routes and who may call each one. Errors use OpenAI's envelope.",
  },
  {
    id: "features",
    group: "outside",
    label: "Tasks, schemas, tools",
    tourTitle: "Tasks, structured outputs and tools",
    tour: "A request names its task, a JSON schema is checked on every answer, and tool calls go to the teacher alone.",
  },
  {
    id: "architecture",
    group: "inside",
    label: "The loop in one picture",
    tourTitle: "The loop",
    tour: "Blue is the small student you own; orange is the teacher you rent per token. The ledger and the gates connect them.",
  },
  {
    id: "cascade",
    group: "inside",
    label: "The cascade",
    tourTitle: "The student answers first",
    tour: "The small student answers first; when it is unsure, the request escalates to the teacher. Each request records its route.",
  },
  {
    id: "ledger",
    group: "inside",
    label: "The ledger",
    tourTitle: "Every teacher answer is logged",
    tour: "Each stored teacher answer keeps the teacher's name and its license, after PII redaction.",
  },
  {
    id: "release",
    group: "inside",
    label: "Releases",
    tourTitle: "Night School and the release gate",
    tour: "Each night a new student trains on teacher answers only and is tested on a held-out golden set. The gate rolls it out, or back.",
  },
  {
    id: "certificate",
    group: "inside",
    label: "Certificate and BOM",
    tourTitle: "A certificate and a bill of materials",
    tour: "Each version carries an exact precision bound and a bill of materials that names its data, teacher and licenses.",
  },
  {
    id: "proof",
    group: "inside",
    label: "Training-set proof",
    tourTitle: "Prove what was trained on",
    tour: "Ask whether an example is in a version's training set and get a proof against the root its bill of materials holds.",
  },
  {
    id: "receipts",
    group: "inside",
    label: "Savings receipts",
    tourTitle: "The bill: savings receipts",
    tour: "Each day gets a receipt over every request's cost, chained to the day before. In the demo these are simulation numbers.",
  },
  {
    id: "watch",
    group: "inside",
    label: "Drift and teacher watch",
    tourTitle: "Watching the student and the teacher",
    tour: "An alarm watches the student's confidence without labels, and a second check watches whether the teacher changed.",
  },
  {
    id: "privacy",
    group: "inside",
    label: "Privacy and access",
    tourTitle: "Privacy and access",
    tour: "Text is redacted before it is stored, clients can opt out, and every route has one access level.",
  },
  {
    id: "resilience",
    group: "inside",
    label: "Resilience",
    tourTitle: "When a model fails",
    tour: "Breakers stop calling a failing model and the teacher answers meanwhile. The outage drill shows it in the demo.",
  },
  {
    id: "cost",
    group: "inside",
    label: "Cost",
    tourTitle: "The honest bill",
    tour: "The same traffic priced per token and by GPU-hours paid. At demo volume a dedicated GPU does not pay, and the report says so.",
  },
  {
    id: "check",
    group: "check",
    label: "Check it yourself",
    tourTitle: "Check it yourself",
    tour: "Commands to install, run the tests and verify a bill of materials, each with a copy button.",
  },
  {
    id: "sponsors",
    group: "check",
    label: "NVIDIA, Nebius, Tavily",
    tourTitle: "What we used",
    tour: "Which sponsor technology each part uses, and which parts have not run live yet.",
  },
  {
    id: "limits",
    group: "check",
    label: "Limits and links",
    tourTitle: "Honest limits",
    tour: "What this is not yet, and where to find the code, the docs and the demo.",
  },
];
