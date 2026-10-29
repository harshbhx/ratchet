/**
 * The glossary: the single source of truth for the Glossary page and for every <Term> tooltip on the site.
 *
 * Each term has a stable `slug` (the page anchor, `/guides/glossary/#slug`: never rename one), its `name`, the `aliases` the
 * prose uses for it, a one-sentence `short` definition (at most 25 words, shown in the tooltip), an optional `long` text (the
 * fuller definition the glossary page shows instead of `short`; it never contradicts `short`), the `page` that explains it
 * and an optional external `learn` link (paper or official docs; every one is checked to resolve).
 * Strings may use `code`, **bold** and [links](/path/).
 */
export interface GlossaryTerm {
  slug: string;
  name: string;
  short: string;
  long?: string;
  aliases?: string[];
  page?: [label: string, href: string];
  learn?: [label: string, href: string];
}

export const GLOSSARY: GlossaryTerm[] = [
  {
    "slug": "adapter",
    "name": "Adapter",
    "short": "The small set of LoRA weights one student version adds on top of the Nano base.",
    "long": "The small set of LoRA weights one student version adds on top of the Nano base; it is the part you own, together with its signed training lineage.",
    "aliases": [
      "LoRA adapter",
      "adapters"
    ],
    "page": [
      "Night School",
      "/guides/night-school/"
    ]
  },
  {
    "slug": "admin-key",
    "name": "Admin key",
    "short": "A key from `RATCHET_ADMIN_KEYS` for management calls (the admin API, task writes, training-set proofs).",
    "long": "A key from `RATCHET_ADMIN_KEYS` for management calls (the admin API, task writes, training-set proofs); recorded by name only, never stored.",
    "page": [
      "Security",
      "/guides/security/"
    ]
  },
  {
    "slug": "all-teacher-baseline",
    "name": "All-teacher baseline",
    "short": "What a request would have cost if the teacher had answered it alone (`all_teacher_cost_usd` in each response).",
    "long": "What a request would have cost if the teacher had answered it alone (`all_teacher_cost_usd` in each response); savings are measured against it.",
    "page": [
      "Cost and savings",
      "/guides/cost-and-savings/"
    ]
  },
  {
    "slug": "anytime-valid",
    "name": "Anytime-valid",
    "short": "A statistical guarantee that holds however often you look at the data.",
    "long": "A statistical guarantee that holds however often you look at the data; the live alert uses it by default, and the canary gate with `RATCHET_SEQUENTIAL=1`.",
    "page": [
      "Release gate",
      "/guides/release-gate/"
    ],
    "learn": [
      "Sequential analysis",
      "https://en.wikipedia.org/wiki/Sequential_analysis"
    ]
  },
  {
    "slug": "api-client",
    "name": "API client",
    "short": "The name of a gateway API key (`RATCHET_API_KEYS=name:key`).",
    "long": "The name of a gateway API key (`RATCHET_API_KEYS=name:key`); requests, cost, savings and receipts are reported per name, never per key.",
    "page": [
      "Security",
      "/guides/security/"
    ]
  },
  {
    "slug": "arbitration",
    "name": "Arbitration",
    "short": "When a shadow candidate disagrees with the live student's served answer, the teacher decides who was right, and its answer becomes training data.",
    "page": [
      "Release gate",
      "/guides/release-gate/"
    ]
  },
  {
    "slug": "audit",
    "name": "Audit",
    "short": "The teacher answers again a random share of the requests the student served, to measure live quality.",
    "long": "The teacher answers again a random share of the requests the student served, to measure live quality; a failed audit never fails the request.",
    "aliases": [
      "audits",
      "audited"
    ],
    "page": [
      "Escalation and audits",
      "/guides/escalation-and-audits/"
    ]
  },
  {
    "slug": "audit-rate",
    "name": "Audit rate",
    "short": "The share of student answers that are audited, `RATCHET_AUDIT_RATE`, 0.05 by default.",
    "long": "The share of student answers that are audited, `RATCHET_AUDIT_RATE`, 0.05 by default; each audit is a paid teacher call.",
    "page": [
      "Escalation and audits",
      "/guides/escalation-and-audits/"
    ]
  },
  {
    "slug": "auto-task",
    "name": "Auto task",
    "short": "A task Ratchet derives from a request's `response_format` JSON schema when the request names no registered task.",
    "page": [
      "Training on your data",
      "/guides/training-on-your-data/"
    ]
  },
  {
    "slug": "base-model",
    "name": "Base model",
    "short": "The pretrained model an adapter sits on. For the student it is NVIDIA Nemotron 3 Nano 4B, which stays frozen.",
    "aliases": [
      "base model",
      "Nano base"
    ],
    "page": [
      "The big picture",
      "/guides/big-picture/"
    ]
  },
  {
    "slug": "bfcl",
    "name": "BFCL",
    "short": "The Berkeley Function-Calling Leaderboard, a public benchmark that scores how exactly models call tools.",
    "aliases": [
      "BFCL"
    ],
    "learn": [
      "BFCL leaderboard",
      "https://gorilla.cs.berkeley.edu/leaderboard.html"
    ],
    "page": [
      "Results",
      "/guides/results/"
    ]
  },
  {
    "slug": "bom",
    "name": "Bill of materials (BOM)",
    "short": "The record of every training source and its license.",
    "long": "The record of every training source and its license; `GET /ratchet/bom` covers the whole ledger, and each adapter gets its own, written once at training time and signed with Ed25519 when a key is set.",
    "aliases": [
      "BOM",
      "bill of materials",
      "BOMs"
    ],
    "page": [
      "Evidence",
      "/guides/evidence/"
    ]
  },
  {
    "slug": "break-even",
    "name": "Break-even",
    "short": "The number of student answers per endpoint hour at which a GPU endpoint pays for itself.",
    "long": "The number of student answers per endpoint hour at which a GPU endpoint pays for itself: the GPU price per hour divided by the teacher spend each student answer avoids.",
    "page": [
      "Cost and savings",
      "/guides/cost-and-savings/"
    ]
  },
  {
    "slug": "budget-guard",
    "name": "Budget guard",
    "short": "Scout's live-mode limits: a daily spend cap, a daily run cap and a per-IP hourly limit, kept across restarts.",
    "long": "Scout's limits in live mode (a daily spend cap, a daily run cap and a per-IP hourly limit), kept in the ledger across restarts and always on in live mode.",
    "page": [
      "Scout",
      "/guides/scout/"
    ]
  },
  {
    "slug": "calibrated-baseline",
    "name": "Calibrated baseline",
    "short": "The ratio of what the teacher really cost on audited answers to the token formula's estimate, used to correct the savings in receipts.",
    "page": [
      "Cost and savings",
      "/guides/cost-and-savings/"
    ]
  },
  {
    "slug": "calibration",
    "name": "Calibration",
    "short": "Choosing a version's threshold: the lowest confidence at which its served answers reach 97% precision on the calibration half of the golden set.",
    "page": [
      "Confidence",
      "/guides/confidence/"
    ]
  },
  {
    "slug": "canary",
    "name": "Canary",
    "short": "A new version serving a slice of real traffic, 10% and then 50%, while audits check it, before it goes live.",
    "aliases": [
      "canary stage"
    ],
    "page": [
      "Release gate",
      "/guides/release-gate/"
    ],
    "learn": [
      "Canary release",
      "https://en.wikipedia.org/wiki/Canary_release"
    ]
  },
  {
    "slug": "cascade",
    "name": "Cascade",
    "short": "Try the cheap model first and send the request to the expensive one only when needed.",
    "aliases": [
      "cascades"
    ],
    "page": [
      "The request journey",
      "/guides/request-journey/"
    ]
  },
  {
    "slug": "certificate",
    "name": "Certificate",
    "short": "An exact lower bound on how often a version's served answers agree with the teacher, computed on held-out golden rows and signed in its BOM.",
    "long": "An exact lower bound (one-sided Clopper–Pearson) on how often a version's served answers agree with the teacher, computed on held-out golden rows and signed in its BOM.",
    "aliases": [
      "certificates"
    ],
    "page": [
      "Evidence",
      "/guides/evidence/"
    ]
  },
  {
    "slug": "champion-challenger",
    "name": "Champion and challenger",
    "short": "The live version (champion) is scored again on the same golden set as the new candidate (challenger) before the two are compared.",
    "page": [
      "Night School",
      "/guides/night-school/"
    ]
  },
  {
    "slug": "checkpoint",
    "name": "Checkpoint",
    "short": "A saved snapshot of training progress, so an interrupted job can resume instead of starting over.",
    "aliases": [
      "checkpoints"
    ]
  },
  {
    "slug": "circuit-breaker",
    "name": "Circuit breaker",
    "short": "After 5 failed calls in a row the gateway pauses calls to the student endpoint (or teacher), and student traffic goes to the teacher.",
    "long": "After 5 failed calls in a row the gateway stops calling the student endpoint (or the teacher) for 30 seconds, doubling up to 300 seconds while probes fail; student traffic then goes to the teacher.",
    "aliases": [
      "circuit breakers",
      "breaker"
    ],
    "page": [
      "The request journey",
      "/guides/request-journey/"
    ],
    "learn": [
      "Circuit breaker pattern",
      "https://en.wikipedia.org/wiki/Circuit_breaker_design_pattern"
    ]
  },
  {
    "slug": "clopper-pearson",
    "name": "Clopper–Pearson",
    "short": "An exact method for a confidence bound on a rate measured on a sample; it errs on the side of caution.",
    "aliases": [
      "Clopper–Pearson",
      "Clopper-Pearson"
    ],
    "learn": [
      "Binomial proportion confidence interval",
      "https://en.wikipedia.org/wiki/Binomial_proportion_confidence_interval"
    ],
    "page": [
      "Evidence",
      "/guides/evidence/"
    ]
  },
  {
    "slug": "cold-start",
    "name": "Cold start",
    "short": "The time before any student version exists, when the teacher answers every request.",
    "aliases": [
      "cold-start"
    ],
    "page": [
      "The request journey",
      "/guides/request-journey/"
    ]
  },
  {
    "slug": "comparator",
    "name": "Comparator (field comparator)",
    "short": "How one field of a task's answer is normalised and compared (`label`, `number`, `date`, `set`, `records`, ...).",
    "long": "How one field of a task's answer is normalised and compared (`label`, `number`, `date`, `set`, `records`, ...); a missing required field makes the whole answer invalid.",
    "page": [
      "Confidence",
      "/guides/confidence/"
    ]
  },
  {
    "slug": "confidence",
    "name": "Confidence",
    "short": "A score from 0 to 1: 0.4 × the token probability of the best majority sample plus 0.6 × the agreement among the student's samples.",
    "long": "A score from 0 to 1: 0.4 × the token probability of the best majority sample plus 0.6 × the agreement among the student's samples; an invalid sample gets no vote.",
    "aliases": [
      "confidence score"
    ],
    "page": [
      "Confidence",
      "/guides/confidence/"
    ]
  },
  {
    "slug": "confidence-interval",
    "name": "Confidence interval",
    "short": "A range that probably contains the true value of a rate measured on a sample.",
    "aliases": [
      "confidence interval",
      "confidence bound",
      "lower bound",
      "upper bound"
    ],
    "learn": [
      "Binomial proportion confidence interval",
      "https://en.wikipedia.org/wiki/Binomial_proportion_confidence_interval"
    ]
  },
  {
    "slug": "confidence-sequence",
    "name": "Confidence sequence",
    "short": "A range for a rate that holds at every sample size at once, so it can be checked after every audit.",
    "long": "A range for a rate that holds at every sample size at once, so it can be checked after every audit; release events show a 95% one.",
    "aliases": [
      "confidence sequences"
    ],
    "page": [
      "Release gate",
      "/guides/release-gate/"
    ]
  },
  {
    "slug": "container",
    "name": "Container",
    "short": "A program packaged with everything it needs, so it runs the same anywhere. Nebius jobs and endpoints each start from one.",
    "aliases": [
      "container",
      "Docker image",
      "container image"
    ]
  },
  {
    "slug": "contested-label",
    "name": "Contested label",
    "short": "A stored teacher answer that the teacher contradicted when it saw the same request again.",
    "long": "A stored teacher answer that the teacher contradicted when it saw the same request again. It is kept out of training and out of the golden set until Night School asks the teacher again and two of three answers agree.",
    "page": [
      "Evidence",
      "/guides/evidence/"
    ]
  },
  {
    "slug": "coverage",
    "name": "Coverage",
    "short": "The share of requests a student serves, or would serve.",
    "long": "The share of requests a student serves, or would serve; more coverage means more savings.",
    "page": [
      "Release gate",
      "/guides/release-gate/"
    ]
  },
  {
    "slug": "cron",
    "name": "Cron",
    "short": "A scheduler that runs a command at set times, such as every night or every hour.",
    "aliases": [
      "cron"
    ]
  },
  {
    "slug": "daily-limit",
    "name": "Daily limit",
    "short": "How many requests one API key may send per day. Each key also has a spending limit.",
    "aliases": [
      "daily limit"
    ],
    "page": [
      "Security",
      "/guides/security/"
    ]
  },
  {
    "slug": "dashboard-key",
    "name": "Dashboard key",
    "short": "A read-only key from `RATCHET_DASHBOARD_KEYS` that reads the dashboard API in live mode and writes nothing.",
    "page": [
      "Security",
      "/guides/security/"
    ]
  },
  {
    "slug": "day-1-checks",
    "name": "Day-1 checks",
    "short": "The checks to run with a real Token Factory key before trusting live mode ([`scripts/day1_check.py`](https://github.com/harshbhx/ratchet/blob/main/scripts/day1_check.py)).",
    "long": "The checks to run with a real Token Factory key before trusting live mode ([`scripts/day1_check.py`](https://github.com/harshbhx/ratchet/blob/main/scripts/day1_check.py)); model IDs and prices stay unverified until they pass.",
    "page": [
      "Host it on Nebius",
      "/guides/host-on-nebius/"
    ]
  },
  {
    "slug": "degraded-mode",
    "name": "Degraded mode",
    "short": "An opt-in (`RATCHET_ON_TEACHER_FAILURE=serve_student`): when the teacher cannot answer an escalation, the student's own below-threshold answer is served, marked `degraded`, and never stored.",
    "page": [
      "Escalation and audits",
      "/guides/escalation-and-audits/"
    ]
  },
  {
    "slug": "distillation",
    "name": "Distillation",
    "short": "Training a small model to copy a larger model's answers.",
    "aliases": [
      "distill",
      "distilling",
      "distilled"
    ],
    "page": [
      "What is Ratchet?",
      "/guides/what-is-ratchet/"
    ],
    "learn": [
      "Distilling the Knowledge in a Neural Network (Hinton et al.)",
      "https://arxiv.org/abs/1503.02531"
    ]
  },
  {
    "slug": "drift",
    "name": "Drift",
    "short": "New kinds of requests the student has never seen, for example after a product launch.",
    "long": "New kinds of requests the student has never seen, for example after a product launch; escalations rise, then fall as Night School learns them.",
    "page": [
      "Night School",
      "/guides/night-school/"
    ]
  },
  {
    "slug": "drift-alarm",
    "name": "Drift alarm",
    "short": "A label-free alarm on each version's confidence scores.",
    "long": "A label-free alarm on each version's confidence scores. If the scores of a version's answers shift away from how they looked before, it raises one `drift` event and suspends the version's certificate. It needs no audits, and an unchanged stream triggers it with probability at most 1 in 100 over its whole life. `GET /ratchet/drift`.",
    "page": [
      "Evidence",
      "/guides/evidence/"
    ]
  },
  {
    "slug": "ed25519",
    "name": "Ed25519 signature",
    "short": "A compact digital signature scheme. Ratchet signs BOMs, proofs and receipts with it so anyone can check them with a public key.",
    "aliases": [
      "Ed25519",
      "Ed25519 signature",
      "signed with Ed25519"
    ],
    "learn": [
      "Ed25519: high-speed high-security signatures",
      "https://ed25519.cr.yp.to/"
    ],
    "page": [
      "Evidence",
      "/guides/evidence/"
    ]
  },
  {
    "slug": "edge-guard",
    "name": "Edge guard",
    "short": "Caddy's opt-in `RATCHET_API_GUARD=guarded` in the Nebius VM kit, which blocks `/v1/*`, the mock-only controls and the admin API without the `X-Ratchet-Admin-Token` header.",
    "page": [
      "Host it on Nebius",
      "/guides/host-on-nebius/"
    ]
  },
  {
    "slug": "e-value",
    "name": "E-process and e-value",
    "short": "A running measure of evidence against \"precision is at least the floor\".",
    "long": "A running measure of evidence against \"precision is at least the floor\"; the sequential canary rolls back at an e-value of 20 and the live alert fires at 100, however often they look.",
    "aliases": [
      "e-values",
      "e-value",
      "e-process"
    ],
    "page": [
      "Release gate",
      "/guides/release-gate/"
    ]
  },
  {
    "slug": "erasure",
    "name": "Erasure",
    "short": "Removing stored examples for good, by client, end user, hash or text.",
    "long": "Removing stored examples for good, by client, end user, hash or text; the example's hash stays behind as a tombstone so it is never stored again.",
    "page": [
      "Security",
      "/guides/security/"
    ]
  },
  {
    "slug": "escalation",
    "name": "Escalation",
    "short": "A request the student was not confident about, sent to the teacher.",
    "long": "A request the student was not confident about, sent to the teacher; the teacher's answer is served and, if valid, stored as training data.",
    "aliases": [
      "escalate",
      "escalated",
      "escalations",
      "escalates"
    ],
    "page": [
      "Escalation and audits",
      "/guides/escalation-and-audits/"
    ]
  },
  {
    "slug": "exam",
    "name": "Exam",
    "short": "A fixed set of questions with known answers, used to score a model before and after training.",
    "aliases": [
      "exam",
      "exams"
    ],
    "page": [
      "Results",
      "/guides/results/"
    ]
  },
  {
    "slug": "fallback-student",
    "name": "Fallback student",
    "short": "Another name for the hosted fallback (`RATCHET_FALLBACK_STUDENT`).",
    "page": [
      "Host it on Nebius",
      "/guides/host-on-nebius/"
    ]
  },
  {
    "slug": "fallback-teacher",
    "name": "Fallback teacher",
    "short": "An opt-in second licensed teacher (`RATCHET_TEACHER_FALLBACK_MODEL`) that answers when the primary teacher is unavailable.",
    "long": "An opt-in second licensed teacher (`RATCHET_TEACHER_FALLBACK_MODEL`) that answers when the primary teacher is unavailable; its answers are stored only with `RATCHET_STORE_FALLBACK_TEACHER=1`.",
    "page": [
      "Escalation and audits",
      "/guides/escalation-and-audits/"
    ]
  },
  {
    "slug": "fine-tuning",
    "name": "Fine-tuning",
    "short": "Training an already-trained model a little more, on your own examples, so it does one job better.",
    "aliases": [
      "fine-tune",
      "fine-tuned",
      "fine-tunes",
      "fine-tuning"
    ],
    "learn": [
      "Fine-tuning (deep learning)",
      "https://en.wikipedia.org/wiki/Fine-tuning_(deep_learning)"
    ]
  },
  {
    "slug": "freeze",
    "name": "Freeze",
    "short": "An operator's switch (`POST /ratchet/admin/releases/freeze`) that stops promotions and new stages.",
    "long": "An operator's switch (`POST /ratchet/admin/releases/freeze`) that stops promotions and new stages. Rollbacks and alerts keep running. Also what the teacher watch does to training when the teacher's answers drift.",
    "page": [
      "Release gate",
      "/guides/release-gate/"
    ]
  },
  {
    "slug": "frequent-rule",
    "name": "FREQUENT rule",
    "short": "A request seen 3 or more times leaves the golden set and goes to training, so frequent questions are never held out forever.",
    "page": [
      "Night School",
      "/guides/night-school/"
    ]
  },
  {
    "slug": "frontier",
    "name": "Frontier",
    "short": "The certificate at every threshold from 0.95 down to 0.50, corrected so all of them hold at once: the certified trade-off between precision and coverage.",
    "page": [
      "Evidence",
      "/guides/evidence/"
    ]
  },
  {
    "slug": "gateway",
    "name": "Gateway",
    "short": "Ratchet’s server: it receives each request, asks the student first, escalates when needed and records the result.",
    "aliases": [
      "gateway"
    ],
    "page": [
      "The big picture",
      "/guides/big-picture/"
    ]
  },
  {
    "slug": "golden-gate",
    "name": "Golden gate",
    "short": "The first release check: at least 95% precision and 25% coverage on held-out golden rows, with no real drop against the live version.",
    "long": "The first release check: at least 95% precision and 25% coverage on held-out golden rows, and no drop of more than 2 points against the live version.",
    "page": [
      "Release gate",
      "/guides/release-gate/"
    ]
  },
  {
    "slug": "golden-set",
    "name": "Golden set",
    "short": "About 10% of unique requests (`RATCHET_GOLDEN_FRACTION`), chosen by hash, held out from training and used only to test versions.",
    "aliases": [
      "golden rows",
      "golden examples",
      "held-out exam",
      "golden eval"
    ],
    "page": [
      "Night School",
      "/guides/night-school/"
    ]
  },
  {
    "slug": "gpu",
    "name": "GPU",
    "short": "A graphics processor, used to train and run models much faster than an ordinary processor could.",
    "aliases": [
      "GPU",
      "GPUs"
    ]
  },
  {
    "slug": "ground-truth",
    "name": "Ground truth",
    "short": "The known-correct answer. Ratchet usually measures agreement with the teacher, which is not the same thing.",
    "aliases": [
      "ground truth"
    ]
  },
  {
    "slug": "hash",
    "name": "Hash",
    "short": "A fixed-length fingerprint computed from some data. The same data always gives the same hash.",
    "aliases": [
      "hash",
      "hashes"
    ]
  },
  {
    "slug": "held-out",
    "name": "Held-out",
    "short": "Data a version never trained on.",
    "long": "Data a version never trained on; the even-numbered golden rows set its threshold and the odd-numbered rows measure its precision.",
    "aliases": [
      "held out",
      "held-out rows"
    ],
    "page": [
      "Night School",
      "/guides/night-school/"
    ]
  },
  {
    "slug": "held-out-certificate",
    "name": "Held-out certificate",
    "short": "Every golden example's proof of absence from an adapter's committed training set, checkable from the BOM alone.",
    "page": [
      "Evidence",
      "/guides/evidence/"
    ]
  },
  {
    "slug": "hosted-fallback",
    "name": "Hosted fallback",
    "short": "The untuned Nemotron 3 Nano 30B on Token Factory that answers student calls while no student endpoint is set.",
    "long": "The untuned Nemotron 3 Nano 30B on Token Factory that answers student calls while no student endpoint is set; paid per token, held to the default threshold of 0.80, and covered by no certificate.",
    "aliases": [
      "hosted student"
    ],
    "page": [
      "Host it on Nebius",
      "/guides/host-on-nebius/"
    ]
  },
  {
    "slug": "hot-load",
    "name": "Hot-load",
    "short": "Loading a new adapter into a running vLLM server without restarting it.",
    "aliases": [
      "hot-loads",
      "hot-loaded",
      "hot-loading",
      "hot-load"
    ],
    "page": [
      "Host it on Nebius",
      "/guides/host-on-nebius/"
    ]
  },
  {
    "slug": "human-label",
    "name": "Human label",
    "short": "A customer's correction (`POST /v1/ratchet/feedback`) or an attested import, stored as a training example with the provenance `human`.",
    "long": "A customer's correction (`POST /v1/ratchet/feedback`) or an attested import, stored as a training example with the provenance `human`; a verdict on its own stores nothing.",
    "page": [
      "Training on your data",
      "/guides/training-on-your-data/"
    ]
  },
  {
    "slug": "human-precision",
    "name": "Human precision",
    "short": "The share of `correct` verdicts on the answers a version served itself, reported next to the audited precision.",
    "long": "The share of `correct` verdicts on the answers a version served itself, reported next to the audited precision. It is never used to promote or roll back a version, because people report what they notice.",
    "page": [
      "Training on your data",
      "/guides/training-on-your-data/"
    ]
  },
  {
    "slug": "idempotency-key",
    "name": "Idempotency key",
    "short": "An `Idempotency-Key` header that makes a retried request get the first answer back without running the cascade again.",
    "page": [
      "The request journey",
      "/guides/request-journey/"
    ],
    "learn": [
      "Idempotence",
      "https://en.wikipedia.org/wiki/Idempotence"
    ]
  },
  {
    "slug": "inconclusive",
    "name": "Inconclusive",
    "short": "Under the sequential rule, a canary stage that reached 400 audits without enough evidence either way.",
    "long": "Under the sequential rule, a canary stage that reached 400 audits without enough evidence either way; the version is retired, not rolled back.",
    "page": [
      "Release gate",
      "/guides/release-gate/"
    ]
  },
  {
    "slug": "invariant",
    "name": "Invariant",
    "short": "One of the rules the code must never break, such as \"never train on student outputs\".",
    "long": "One of the rules the code must never break, such as \"never train on student outputs\"; each one is mapped to code and tests.",
    "page": [
      "Security",
      "/guides/security/#the-safety-rules-the-loop-never-breaks"
    ]
  },
  {
    "slug": "judge",
    "name": "Judge",
    "short": "Nemotron 3 Super on Token Factory, which scores free-text answers in the golden eval.",
    "long": "Nemotron 3 Super on Token Factory, which scores free-text answers in the golden eval; tasks with typed fields are compared field by field instead, with no judge.",
    "aliases": [
      "judge model"
    ],
    "page": [
      "Night School",
      "/guides/night-school/"
    ]
  },
  {
    "slug": "judges-reserve",
    "name": "Judges' reserve",
    "short": "Scout runs, spend and Tavily credits held back each day for evaluators, unlocked by a code sent once to `POST /scout/judge`.",
    "page": [
      "Scout",
      "/guides/scout/"
    ]
  },
  {
    "slug": "l40s",
    "name": "L40S",
    "short": "An NVIDIA data-centre GPU with 48 GB of memory. Ratchet trains and serves the student on one.",
    "aliases": [
      "L40S"
    ],
    "page": [
      "Host it on Nebius",
      "/guides/host-on-nebius/"
    ]
  },
  {
    "slug": "label-p",
    "name": "label_p (labelling probability)",
    "short": "The designed chance that the teacher labels a student answer, logged with every request so the replay can weight each label.",
    "long": "The designed chance that the teacher labels a student answer (1 for an escalation, the audit rate for a served answer), logged with every request so the replay can weight each label.",
    "page": [
      "Evidence",
      "/guides/evidence/"
    ]
  },
  {
    "slug": "learn-then-test",
    "name": "Learn-then-Test",
    "short": "An opt-in calibration (`RATCHET_CALIBRATION=ltt`) that picks the threshold from a fixed grid so its precision is certified.",
    "page": [
      "Evidence",
      "/guides/evidence/"
    ]
  },
  {
    "slug": "ledger",
    "name": "Ledger",
    "short": "The SQLite file that records every request's metadata, every stored training example with its provenance, every version and every event.",
    "page": [
      "The ledger",
      "/guides/ledger/"
    ]
  },
  {
    "slug": "license-clean-distillation",
    "name": "License-clean distillation",
    "short": "Distilling only from teachers whose licenses allow training on their outputs.",
    "long": "Distilling only from teachers whose licenses allow training on their outputs; closed models whose terms forbid it are refused in code.",
    "aliases": [
      "license-clean"
    ],
    "page": [
      "Licenses",
      "/guides/licenses/"
    ]
  },
  {
    "slug": "live-mode",
    "name": "Live mode",
    "short": "Running against real Token Factory, a real student endpoint and real web search.",
    "long": "Running against real Token Factory, a real student endpoint and real web search; every teacher call costs money.",
    "page": [
      "Host it on Nebius",
      "/guides/host-on-nebius/"
    ]
  },
  {
    "slug": "live-run-harness",
    "name": "Live-run harness",
    "short": "A command, `python -m ratchet.liverun`, that sends rounds of traffic through a live gateway under a spending cap and writes the results file.",
    "long": "`python -m ratchet.liverun`, which sends rounds of traffic through a live gateway under a spending cap and writes the results file every live number must come from; its smoke mode has run once (20 requests on Oct 23, about $0.01, 0 students and 0 audits, so no savings number), the measured `run` mode has not.",
    "page": [
      "Evidence",
      "/guides/evidence/"
    ]
  },
  {
    "slug": "live-version",
    "name": "Live version",
    "short": "The version that serves all student traffic after its canary passed.",
    "long": "The version that serves all student traffic after its canary passed; it is alerted on, never rolled back automatically.",
    "page": [
      "Release gate",
      "/guides/release-gate/"
    ]
  },
  {
    "slug": "llms-txt",
    "name": "llms.txt",
    "short": "A plain-text file at /llms.txt that lists a site's pages for language models to read. These docs publish one.",
    "aliases": [
      "llms.txt"
    ],
    "learn": [
      "The llms.txt proposal",
      "https://llmstxt.org"
    ]
  },
  {
    "slug": "lora",
    "name": "LoRA",
    "short": "Low-Rank Adaptation: training a small set of extra weights (an adapter) on top of a frozen base model, cheap to train and to swap.",
    "aliases": [
      "Low-Rank Adaptation"
    ],
    "page": [
      "Night School",
      "/guides/night-school/"
    ],
    "learn": [
      "LoRA: Low-Rank Adaptation of Large Language Models",
      "https://arxiv.org/abs/2106.09685"
    ]
  },
  {
    "slug": "mamba",
    "name": "Mamba (hybrid Mamba)",
    "short": "A model design that mixes attention layers with Mamba state-space layers, which handle long text cheaply. Nemotron Nano is built this way.",
    "aliases": [
      "Mamba",
      "hybrid Mamba",
      "Mamba-Transformer"
    ],
    "learn": [
      "Mamba: Linear-Time Sequence Modeling",
      "https://arxiv.org/abs/2312.00752"
    ],
    "page": [
      "Night School",
      "/guides/night-school/"
    ]
  },
  {
    "slug": "mcnemar",
    "name": "McNemar test",
    "short": "A test that compares two models on the same rows by looking only at the rows where they disagree.",
    "aliases": [
      "McNemar",
      "McNemar tests",
      "McNemar test"
    ],
    "learn": [
      "McNemar's test",
      "https://en.wikipedia.org/wiki/McNemar%27s_test"
    ],
    "page": [
      "Release gate",
      "/guides/release-gate/"
    ]
  },
  {
    "slug": "merkle-root",
    "name": "Merkle root",
    "short": "One hash that commits to a whole set.",
    "long": "One hash that commits to a whole set; an adapter's BOM signs one over its training set and one over its golden set, and a savings receipt signs one over a day's request metadata.",
    "aliases": [
      "Merkle tree",
      "Merkle",
      "Merkle roots"
    ],
    "page": [
      "Evidence",
      "/guides/evidence/"
    ],
    "learn": [
      "Merkle tree",
      "https://en.wikipedia.org/wiki/Merkle_tree"
    ]
  },
  {
    "slug": "mock-mode",
    "name": "Mock mode",
    "short": "Running with mock models and search, with no keys and no GPU, for tests and development.",
    "long": "Running with mock models and search, with no keys and no GPU, for tests and development; its numbers say nothing about how good a real model is.",
    "page": [
      "Quickstart",
      "/guides/quickstart/"
    ]
  },
  {
    "slug": "nebius-ai-cloud",
    "name": "Nebius AI Cloud",
    "short": "The cloud whose Token Factory, Serverless Jobs and Object Storage Ratchet uses for model calls and for training.",
    "aliases": [
      "Nebius AI Cloud"
    ],
    "learn": [
      "Nebius",
      "https://nebius.com/"
    ],
    "page": [
      "Host it on Nebius",
      "/guides/host-on-nebius/"
    ]
  },
  {
    "slug": "nemotron",
    "name": "Nemotron",
    "short": "NVIDIA's family of open language models: Nano 4B is the student, Super the judge and Ultra the teacher.",
    "aliases": [
      "Nemotron"
    ],
    "learn": [
      "NVIDIA Nemotron",
      "https://developer.nvidia.com/nemotron"
    ],
    "page": [
      "The big picture",
      "/guides/big-picture/"
    ]
  },
  {
    "slug": "nemotron-license",
    "name": "Nemotron Open Model License",
    "short": "The license on NVIDIA's Nemotron base models. The base stays under it; the adapter you train is yours.",
    "aliases": [
      "NVIDIA Nemotron Open Model License",
      "Nemotron Open Model License"
    ],
    "page": [
      "Licenses",
      "/guides/licenses/"
    ]
  },
  {
    "slug": "night-school",
    "name": "Night School",
    "short": "The nightly job: build the dataset, retrain the adapter, test it on the golden set and hand it to the release gate.",
    "long": "The nightly job: build the dataset, retrain the adapter from the base on all teacher answers so far, evaluate it on the golden set, and hand it to the release gate.",
    "page": [
      "Night School",
      "/guides/night-school/"
    ]
  },
  {
    "slug": "notice",
    "name": "NOTICE.md",
    "short": "The file next to each adapter's weights that names the base model's license and each teacher's license and says the targets were teacher outputs only.",
    "long": "The file next to each adapter's weights that names the base model's license and each teacher's license and says the targets were teacher outputs only; its SHA-256 is in the signed BOM.",
    "aliases": [
      "NOTICE"
    ],
    "page": [
      "Licenses",
      "/guides/licenses/"
    ]
  },
  {
    "slug": "no-train-end-user",
    "name": "No-train end user",
    "short": "An end user registered with `POST /v1/ratchet/no-train`, whose requests are never stored as training examples.",
    "page": [
      "Security",
      "/guides/security/"
    ]
  },
  {
    "slug": "nvidia-slm-paper",
    "name": "NVIDIA small-model paper",
    "short": "NVIDIA Research’s paper arguing that small language models suit agent work, with six steps for moving from a large model to a small one.",
    "aliases": [
      "NVIDIA paper",
      "SLM paper",
      "small language models paper"
    ],
    "learn": [
      "Small Language Models are the Future of Agentic AI (arXiv 2506.02153)",
      "https://arxiv.org/abs/2506.02153"
    ],
    "page": [
      "Why it matters",
      "/guides/why-it-matters/"
    ]
  },
  {
    "slug": "object-storage",
    "name": "Object Storage",
    "short": "Nebius's S3-compatible storage.",
    "long": "Nebius's S3-compatible storage; one bucket is mounted at `/data` in both the training job and the student endpoint.",
    "aliases": [
      "bucket",
      "buckets"
    ],
    "page": [
      "Host it on Nebius",
      "/guides/host-on-nebius/"
    ],
    "learn": [
      "Nebius Object Storage docs",
      "https://docs.nebius.com/object-storage"
    ]
  },
  {
    "slug": "off-path-checks",
    "name": "Off-path checks",
    "short": "In live mode, audits and shadow checks run after the answer is sent, so the client never waits for the teacher.",
    "long": "In live mode, audits and shadow checks run after the answer is sent (`RATCHET_SIDECAR_WORKERS`, 4 by default there), so the client does not wait for the teacher.",
    "page": [
      "Escalation and audits",
      "/guides/escalation-and-audits/"
    ]
  },
  {
    "slug": "openai-compatible",
    "name": "OpenAI-compatible API",
    "short": "An API that takes the same requests and returns the same responses as OpenAI’s chat endpoint, so existing client code works after a URL change.",
    "aliases": [
      "OpenAI-compatible"
    ],
    "page": [
      "What is Ratchet?",
      "/guides/what-is-ratchet/"
    ]
  },
  {
    "slug": "openmdw",
    "name": "OpenMDW-1.1",
    "short": "The license of Nemotron 3 Ultra.",
    "long": "The license of Nemotron 3 Ultra; it places no restrictions on outputs, so its answers may be used to train a student.",
    "aliases": [
      "OpenMDW"
    ],
    "page": [
      "Licenses",
      "/guides/licenses/"
    ],
    "learn": [
      "The OpenMDW-1.1 license",
      "https://openmdw.ai/license/1-1/"
    ]
  },
  {
    "slug": "opt-out",
    "name": "Opt-out",
    "short": "A way to keep a request's teacher answers out of training (`store: false`, `X-Ratchet-Store: none`, a key's `store=0`, no-store tasks, no-train end users).",
    "long": "A way to keep a request's teacher answers out of training (`store: false`, `X-Ratchet-Store: none`, a key's `store=0`, no-store tasks, no-train end users); opt-outs only ever reduce storage.",
    "page": [
      "Security",
      "/guides/security/"
    ]
  },
  {
    "slug": "paired-gate",
    "name": "Paired gate",
    "short": "An opt-in golden-gate rule (`RATCHET_PAIRED_GATE=1`) that compares the candidate and the live version row by row on the same golden rows with exact McNemar tests.",
    "page": [
      "Release gate",
      "/guides/release-gate/"
    ],
    "learn": [
      "McNemar's test",
      "https://en.wikipedia.org/wiki/McNemar%27s_test"
    ]
  },
  {
    "slug": "peft",
    "name": "PEFT",
    "short": "Parameter-efficient fine-tuning: training a small set of extra weights instead of the whole model. Also the name of Hugging Face’s library for it.",
    "aliases": [
      "PEFT"
    ],
    "learn": [
      "PEFT on GitHub",
      "https://github.com/huggingface/peft"
    ],
    "page": [
      "Host it on Nebius",
      "/guides/host-on-nebius/"
    ]
  },
  {
    "slug": "per-task-calibration",
    "name": "Per-task calibration",
    "short": "When several tasks share one adapter, each gets its own threshold and held-out numbers.",
    "long": "When several tasks share one adapter, each gets its own threshold and held-out numbers, and a task that fails its golden check is disabled on that version.",
    "page": [
      "Confidence",
      "/guides/confidence/"
    ]
  },
  {
    "slug": "pii-redaction",
    "name": "PII redaction",
    "short": "Replacing emails, phone numbers, card numbers, IBANs, national IDs, IP addresses and secrets with placeholders such as `[EMAIL]` before anything becomes a training example.",
    "long": "Replacing emails, phone numbers, card numbers, IBANs, national IDs, IP addresses and secrets with placeholders such as `[EMAIL]` before anything becomes a training example; names and street addresses are not caught yet.",
    "aliases": [
      "PII",
      "redaction"
    ],
    "page": [
      "Security",
      "/guides/security/"
    ],
    "learn": [
      "Personal data",
      "https://en.wikipedia.org/wiki/Personal_data"
    ]
  },
  {
    "slug": "pinned-task",
    "name": "Pinned task",
    "short": "A task that always goes to the teacher and is never stored as training data, such as Scout's `research-plan` and `research-write`.",
    "long": "A task that always goes to the teacher and is never stored as training data, such as Scout's `research-plan` and `research-write`; only an operator's spec pins a task.",
    "page": [
      "Scout",
      "/guides/scout/"
    ]
  },
  {
    "slug": "precision",
    "name": "Precision",
    "short": "Of the answers a student serves (or would serve), the share that agree with the teacher.",
    "long": "Of the answers a student serves (or would serve), the share that agree with the teacher; agreement with Nemotron 3 Ultra, not ground truth.",
    "page": [
      "Release gate",
      "/guides/release-gate/"
    ],
    "learn": [
      "Precision and recall",
      "https://en.wikipedia.org/wiki/Precision_and_recall"
    ]
  },
  {
    "slug": "preemptible-gpu",
    "name": "Preemptible (spot) GPU",
    "short": "A GPU rented at a discount on the condition that the provider can take it back at short notice, so training must resume.",
    "aliases": [
      "preemptible",
      "spot",
      "spot GPU",
      "preemptible (spot)",
      "preemptible GPU"
    ],
    "page": [
      "Host it on Nebius",
      "/guides/host-on-nebius/"
    ]
  },
  {
    "slug": "provenance",
    "name": "Provenance",
    "short": "Where a training example came from: which teacher wrote it and under which license, recorded when it is stored.",
    "page": [
      "The ledger",
      "/guides/ledger/"
    ]
  },
  {
    "slug": "qlora",
    "name": "QLoRA",
    "short": "LoRA training on a base model squeezed to 4-bit numbers, so a larger model fits on a small GPU.",
    "aliases": [
      "QLoRA"
    ],
    "learn": [
      "QLoRA: Efficient Finetuning of Quantized LLMs",
      "https://arxiv.org/abs/2305.14314"
    ],
    "page": [
      "Training on your data",
      "/guides/training-on-your-data/"
    ]
  },
  {
    "slug": "random-split",
    "name": "Random split",
    "short": "A share of eligible live calls sent on purpose to the hosted Nano 30B, labelled as such, so its answers can be measured.",
    "aliases": [
      "random split"
    ],
    "page": [
      "FAQ",
      "/guides/faq/"
    ]
  },
  {
    "slug": "readiness-liveness",
    "name": "Readiness and liveness",
    "short": "`/readyz` says whether the gateway can answer requests (for load balancers and uptime monitors).",
    "long": "`/readyz` says whether the gateway can answer requests (for load balancers and uptime monitors); `/healthz` says only that the process is up (for restarts).",
    "page": [
      "Host it on Nebius",
      "/guides/host-on-nebius/"
    ]
  },
  {
    "slug": "receipt",
    "name": "Receipt (savings receipt)",
    "short": "A signed record per ledger day of what was saved, with a Merkle tree over the request metadata and the fee taken.",
    "long": "A signed record per ledger day, for the gateway and for each API client, of what was saved, with a Merkle tree over the day's request metadata and the fee taken on the 95% lower bound of the savings.",
    "page": [
      "Cost and savings",
      "/guides/cost-and-savings/"
    ]
  },
  {
    "slug": "release-gate",
    "name": "Release gate",
    "short": "The checks a new version must pass before it serves traffic: golden gate, shadow, canary at 10% and 50%, then live.",
    "long": "The checks a new version must pass before it serves traffic: the golden gate, shadow, canary at 10% and 50%, then live, with automatic rollback of a bad canary.",
    "aliases": [
      "release gates"
    ],
    "page": [
      "Release gate",
      "/guides/release-gate/"
    ]
  },
  {
    "slug": "replay",
    "name": "Replay (ledger replay)",
    "short": "Recomputing one version's coverage, precision and cost from its logged traffic at another threshold or audit rate, with no new model call.",
    "page": [
      "Evidence",
      "/guides/evidence/"
    ]
  },
  {
    "slug": "request-id",
    "name": "Request id",
    "short": "`req_` and 32 hex digits, sent in every response (`x-request-id`, `ratchet.request_id`).",
    "long": "`req_` and 32 hex digits, sent in every response (`x-request-id`, `ratchet.request_id`). It names the request in feedback calls and request lookups; it never holds text.",
    "page": [
      "The request journey",
      "/guides/request-journey/"
    ]
  },
  {
    "slug": "retention",
    "name": "Retention",
    "short": "How long stored examples and request rows are kept (`RATCHET_EXAMPLE_RETENTION_DAYS`, `RATCHET_REQUEST_RETENTION_DAYS`.",
    "long": "How long stored examples and request rows are kept (`RATCHET_EXAMPLE_RETENTION_DAYS`, `RATCHET_REQUEST_RETENTION_DAYS`; 0 keeps them forever).",
    "page": [
      "Security",
      "/guides/security/"
    ]
  },
  {
    "slug": "rollback",
    "name": "Rollback",
    "short": "Pulling a canary automatically when the upper bound of its audited precision falls below 92%.",
    "long": "Pulling a canary automatically when the 95% upper bound of its audited precision falls below 92% (or, with the sequential rule, when its e-value reaches 20).",
    "aliases": [
      "roll back",
      "rolled back",
      "rollbacks"
    ],
    "page": [
      "Release gate",
      "/guides/release-gate/"
    ]
  },
  {
    "slug": "route",
    "name": "Route",
    "short": "The recorded reason a request was answered the way it was: `student`, `confidence` (escalated), `cold_start`, `pinned`, `tools`, `student_breaker` and others.",
    "page": [
      "The request journey",
      "/guides/request-journey/"
    ]
  },
  {
    "slug": "scout",
    "name": "Scout",
    "short": "The multi-agent research tool: a planner and writer pinned to the teacher, a web searcher, and many small student calls.",
    "long": "The multi-agent research tool: a planner and a writer pinned to the teacher, a web searcher, and many small triage and reader calls the student learns.",
    "page": [
      "Scout",
      "/guides/scout/"
    ]
  },
  {
    "slug": "self-consistency",
    "name": "Self-consistency (agreement)",
    "short": "How many of the student's samples (3 by default) give the same answer, compared with the task's comparators.",
    "page": [
      "Confidence",
      "/guides/confidence/"
    ]
  },
  {
    "slug": "serverless-endpoint",
    "name": "Serverless Endpoint",
    "short": "A Nebius service that serves a container at a URL.",
    "long": "A Nebius service that serves a container at a URL; Ratchet runs vLLM with the student there on one L40S, billed while it runs.",
    "aliases": [
      "Serverless Endpoints",
      "student endpoint"
    ],
    "page": [
      "Host it on Nebius",
      "/guides/host-on-nebius/"
    ],
    "learn": [
      "Nebius Serverless docs",
      "https://docs.nebius.com/serverless/overview"
    ]
  },
  {
    "slug": "serverless-job",
    "name": "Serverless Job",
    "short": "A Nebius service that runs a container to completion.",
    "long": "A Nebius service that runs a container to completion; Night School trains each adapter in one, on one L40S.",
    "aliases": [
      "Serverless Jobs"
    ],
    "page": [
      "Host it on Nebius",
      "/guides/host-on-nebius/"
    ],
    "learn": [
      "Nebius Serverless docs",
      "https://docs.nebius.com/serverless/overview"
    ]
  },
  {
    "slug": "set-commitment",
    "name": "Set commitment",
    "short": "The Merkle root in an adapter's BOM that fixes its exact training (or golden) set, so one example can be proved in or out.",
    "long": "The Merkle root in an adapter's BOM that fixes its exact training (or golden) set, so a single example can be proved in or out of it.",
    "page": [
      "Evidence",
      "/guides/evidence/"
    ]
  },
  {
    "slug": "sha-256",
    "name": "SHA-256",
    "short": "A hash function that turns any file into a 64-character fingerprint. Change one byte and the fingerprint changes.",
    "aliases": [
      "SHA-256"
    ],
    "learn": [
      "SHA-2",
      "https://en.wikipedia.org/wiki/SHA-2"
    ]
  },
  {
    "slug": "shadow",
    "name": "Shadow",
    "short": "A candidate version answering real traffic silently next to the live student.",
    "long": "A candidate version answering real traffic silently next to the live student; nothing it says is served, and disagreements go to the teacher.",
    "aliases": [
      "shadow stage",
      "shadow check",
      "shadow checks"
    ],
    "page": [
      "Release gate",
      "/guides/release-gate/"
    ]
  },
  {
    "slug": "spec-hash",
    "name": "Spec hash",
    "short": "A hash of the rules in a task's spec that decide agreement.",
    "long": "A hash of the rules in a task's spec that decide agreement; when it changes, the task goes to the teacher until a night scores the version again.",
    "page": [
      "Training on your data",
      "/guides/training-on-your-data/"
    ]
  },
  {
    "slug": "sqlite",
    "name": "SQLite",
    "short": "A database kept in a single file. The ledger is a SQLite file.",
    "aliases": [
      "SQLite"
    ],
    "learn": [
      "SQLite",
      "https://www.sqlite.org"
    ],
    "page": [
      "The ledger",
      "/guides/ledger/"
    ]
  },
  {
    "slug": "student",
    "name": "Student",
    "short": "The small model that answers first: NVIDIA Nemotron 3 Nano 4B with the adapter you own, served by vLLM.",
    "page": [
      "The big picture",
      "/guides/big-picture/"
    ]
  },
  {
    "slug": "take-rate",
    "name": "Take rate",
    "short": "The share of the savings' billable lower bound that is the fee in a receipt (`RATCHET_TAKE_RATE`, 25% by default).",
    "page": [
      "Cost and savings",
      "/guides/cost-and-savings/"
    ]
  },
  {
    "slug": "task",
    "name": "Task",
    "short": "A named kind of request, such as `ticket-triage`, that says which answer fields must match, which answers are valid and whether it is pinned.",
    "long": "A named kind of request, such as `ticket-triage`, that says which answer fields must match, which answers are valid and whether it is pinned; set with the `X-Ratchet-Task` header.",
    "aliases": [
      "tasks"
    ],
    "page": [
      "Training on your data",
      "/guides/training-on-your-data/"
    ]
  },
  {
    "slug": "task-registry",
    "name": "Task registry",
    "short": "The tasks a request can name: the built-in ones, those in `RATCHET_TASKS_FILE` and those written through the admin API.",
    "page": [
      "Training on your data",
      "/guides/training-on-your-data/"
    ]
  },
  {
    "slug": "tavily",
    "name": "Tavily",
    "short": "A web-search API built for AI agents. Scout uses it to find sources.",
    "aliases": [
      "Tavily"
    ],
    "learn": [
      "Tavily",
      "https://tavily.com"
    ],
    "page": [
      "Scout",
      "/guides/scout/"
    ]
  },
  {
    "slug": "teacher",
    "name": "Teacher",
    "short": "The large model that answers what the student cannot: NVIDIA Nemotron 3 Ultra on Token Factory.",
    "long": "The large model that answers what the student cannot: NVIDIA Nemotron 3 Ultra on Token Factory; only its answers and attested human labels become training data.",
    "page": [
      "The big picture",
      "/guides/big-picture/"
    ]
  },
  {
    "slug": "teacher-watch",
    "name": "Teacher watch",
    "short": "A check that the teacher still gives the same labels.",
    "long": "A check that the teacher still gives the same labels. A repeated request, or one of Night School's probes, is compared with its stored label, once per request per check epoch. Enough disagreement freezes training until an operator acknowledges it (`POST /ratchet/teacher/ack`). A new model id in the teacher's responses starts a new epoch. `GET /ratchet/teacher`.",
    "page": [
      "Evidence",
      "/guides/evidence/"
    ]
  },
  {
    "slug": "text-to-sql",
    "name": "Text-to-SQL",
    "short": "Turning a plain-English question into a SQL query. It is the task the published adapter is trained on.",
    "aliases": [
      "Text-to-SQL",
      "text2sql"
    ],
    "page": [
      "Text-to-SQL adapter",
      "/guides/text-to-sql/"
    ]
  },
  {
    "slug": "threshold",
    "name": "Threshold",
    "short": "The confidence at or above which a student answer is served, calibrated per version and per task.",
    "aliases": [
      "thresholds",
      "threshold (τ*)",
      "τ*"
    ],
    "page": [
      "Confidence",
      "/guides/confidence/"
    ]
  },
  {
    "slug": "token",
    "name": "Token",
    "short": "A small piece of text, roughly a short word or part of one, that models read and that Token Factory bills by.",
    "aliases": [
      "tokens"
    ]
  },
  {
    "slug": "token-factory",
    "name": "Token Factory",
    "short": "Nebius's OpenAI-compatible inference service for open models, billed per token.",
    "long": "Nebius's OpenAI-compatible inference service for open models, billed per token; Ratchet's teacher, judge and hosted fallback run there.",
    "page": [
      "Host it on Nebius",
      "/guides/host-on-nebius/"
    ],
    "learn": [
      "Nebius Token Factory",
      "https://nebius.com/token-factory"
    ]
  },
  {
    "slug": "tombstone",
    "name": "Tombstone",
    "short": "The hash an erased example leaves behind, so the same example is refused from then on, from any client.",
    "page": [
      "Security",
      "/guides/security/"
    ]
  },
  {
    "slug": "tool-calling",
    "name": "Tool calling",
    "short": "A model replying with a structured call to a named function instead of prose, which code then runs.",
    "aliases": [
      "tool calls",
      "tool call",
      "function calling"
    ],
    "page": [
      "Results",
      "/guides/results/"
    ]
  },
  {
    "slug": "training-example",
    "name": "Training example",
    "short": "One request with the teacher’s answer to it, stored with its provenance and used to teach the student.",
    "aliases": [
      "training examples",
      "training data"
    ],
    "page": [
      "The ledger",
      "/guides/ledger/"
    ]
  },
  {
    "slug": "version",
    "name": "Version",
    "short": "One trained student adapter (`student-v1`, `student-v2`, ...) with its own eval, threshold and state: `candidate`, `rejected`, `shadow`, `canary`, `live`, `retired` or `rolled_back`.",
    "page": [
      "Release gate",
      "/guides/release-gate/"
    ]
  },
  {
    "slug": "vllm",
    "name": "vLLM",
    "short": "The open-source model server that runs the student on the endpoint and loads each new LoRA adapter without a restart.",
    "page": [
      "Host it on Nebius",
      "/guides/host-on-nebius/"
    ],
    "learn": [
      "vLLM on GitHub",
      "https://github.com/vllm-project/vllm"
    ]
  },
  {
    "slug": "weights",
    "name": "Weights",
    "short": "The numbers a model learns in training. An adapter is a small set of extra weights.",
    "aliases": [
      "weights"
    ]
  },
  {
    "slug": "wilson-bound",
    "name": "Wilson bound",
    "short": "An interval for a proportion that behaves well on small samples.",
    "long": "An interval for a proportion that behaves well on small samples; the default canary rollback uses its upper bound, and it is valid for one look at a fixed sample.",
    "aliases": [
      "Wilson interval",
      "Wilson",
      "Wilson score"
    ],
    "page": [
      "Release gate",
      "/guides/release-gate/"
    ],
    "learn": [
      "Binomial proportion confidence interval (Wilson)",
      "https://en.wikipedia.org/wiki/Binomial_proportion_confidence_interval"
    ]
  }
];
