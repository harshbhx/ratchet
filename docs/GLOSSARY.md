# Glossary

| Term | Meaning in Ratchet |
|---|---|
| **Teacher** | The large model that answers requests the student can't handle. Default: NVIDIA Nemotron 3 Ultra on Nebius Token Factory. Its answers become training data. |
| **Student** | The small model that answers first. Default: NVIDIA Nemotron 3 Nano 4B plus a LoRA adapter, served by vLLM. The customer owns the adapter; the base model keeps its own license. |
| **Version** | One trained student adapter (`student-v1`, `student-v2`, ...). Each has its own eval numbers, threshold and state. |
| **Cascade** | Try the cheap model first; send the request to the expensive model only when needed. |
| **Escalation** | A request the student was not confident about, sent to the teacher. |
| **Confidence** | A score from 0 to 1: 0.4 × token probability of the best majority sample + 0.6 × agreement among samples. Invalid-format answers score 0. |
| **Threshold** | The confidence at or above which the student's answer is served. Calibrated per version, and per task when several tasks share the adapter. |
| **Per-task calibration** | When several tasks share one adapter, each gets its own threshold and held-out precision and coverage in the version's eval (`eval['tasks']`), and the release gate checks each one; a task that fails is disabled on that version (the teacher answers it) or rejects the version ([ADR 0040](adr/0040-per-task-calibration.md)). |
| **Calibration** | Picking the lowest threshold whose served answers reach a target precision (97%) on held-out data. |
| **Self-consistency / agreement** | How many of the student's n samples (default 3) give the same answer, compared pairwise with the task's field comparators. |
| **Logprobs** | The model's log-probabilities for each generated token. Used to estimate how sure it is. |
| **Task** | A named kind of call (for example `ticket-triage`) that defines which answer fields must match for two answers to agree, which answers are valid, and whether it is pinned. Chosen with the `X-Ratchet-Task` header. |
| **Field comparator** | How one field of a task's answer is normalised and compared (`label` over a vocabulary, `number` with a tolerance, `date`, `set`, `records`, ...). A required field that is missing, or a value that does not normalise, makes the answer invalid. |
| **Distillable task** | A task whose teacher answers become training data, so the student can take it over. Example: `source-triage`. Every task is distillable unless it is pinned. |
| **Pinned task** | A task that always goes to the teacher and is never stored as training data (`Task.pinned`). Used for open-ended work such as planning and writing. Examples: `research-plan`, `research-write`. Only an operator's spec pins a task, never a request. |
| **Task registry** | The tasks a request can name: the built-in ones, the tasks of `RATCHET_TASKS_FILE` and the ones written through the admin API (`/ratchet/tasks`). A name that is not registered follows `RATCHET_UNKNOWN_TASKS` ([ADR 0022](adr/0022-task-registry.md)). |
| **Task spec** | A task written as data (`name`, `fields`, `pinned`, `policy`, `clients`, ...), in a tasks file or an admin API body. Its `spec_hash` covers the rules that decide agreement; a change sends the task to the teacher until a night recalibrates it. |
| **Audit** | The teacher re-answers a random share of student-served requests (`audit_rate`) to measure live quality. |
| **Precision** | Of the answers a student serves (or would serve), the share that agree with the teacher. |
| **Coverage** | The share of requests a student serves (or would serve). Higher coverage = more savings. |
| **Golden set** | About 10% of unique requests, held out from training and used to evaluate new versions. Requests seen 3+ times leave the golden set and go to training. |
| **Golden gate** | The first release check: precision ≥ 95%, coverage ≥ 25%, and no regression against the live version on the same golden set. |
| **Champion vs challenger** | The live version (champion) is re-scored on the same golden set as the new candidate (challenger) before comparing them. |
| **Shadow mode** | A candidate runs silently on real traffic. Its answers are compared but never shown to users. |
| **Arbitration** | When a shadow candidate disagrees with the live student's served answer, the teacher decides who was right. |
| **Canary** | A candidate serves a slice of real traffic (10%, then 50%) while audits measure its quality. |
| **Rollback** | Pulling a canary version when the 95% upper bound of its audited precision falls below 92% (or, with the sequential rule, when its e-value reaches 20). Traffic returns to the previous live version, or to the teacher. |
| **Wilson interval** | A statistical range for a proportion that behaves well on small samples. Used for rollback decisions by default. Valid for one look at a fixed sample, not for a gate that looks after every request. |
| **Inconclusive** | Under the sequential rule, a canary stage that reached `RATCHET_MAX_STAGE_AUDITS` audits without enough evidence either way. The canary is retired, not rolled back. |
| **e-value** | A number that measures evidence against a claim (here "precision is at least the floor"): 1 means none, and the chance that it ever reaches 1/alpha while the claim holds is at most alpha, however often it is checked (anytime-valid). The anytime-valid release gate rolls back at 20 and the live alert fires at 100 ([ADR 0025](adr/0025-anytime-valid-release-gate.md)). |
| **Confidence sequence** | A range for a rate that holds at every sample size at once with the stated probability, so it can be checked after every audit. Release events report a 95% one as `95% CS [lo, hi]`. |
| **Drift alarm** | A `drift` event for one version and task: the student's confidence on recent traffic no longer matches its earlier traffic. A test martingale (a gambler's capital betting against "traffic has not changed") reached `RATCHET_DRIFT_ALARM` (100), which an unchanged stream does with probability at most 1% over its life. It needs no labels, says nothing about quality by itself, and suspends the version's certificate ([ADR 0032](adr/0032-label-free-drift-alarm.md)). |
| **Teacher watch, check epoch, contested label** | The teacher watch compares each stored request's first re-test in a check epoch (the teacher labelling a repeated request again, or a Night School probe) with its stored label. A label a re-test disagreed with is contested: left out of training and the golden set until two more teacher answers settle it by majority. A check epoch starts with the ledger, an operator's ack or a new model id in the teacher's responses; each request gives at most one check per epoch. When the epoch's disagreements pass `RATCHET_TEACHER_NOISE_MAX` (5%) by enough, a `teacher_drift` event freezes Night School ([ADR 0033](adr/0033-teacher-watch.md)). |
| **Certificate** | A version's exact lower bound on precision (one-sided Clopper–Pearson) from the held-out golden rows its threshold was not tuned on: "certified >= 95% (lcb 96.4%, delta 0.05)" means the answers it serves agree with the teacher at least 96.4% of the time, with probability at least 95%. Signed in the adapter's BOM ([ADR 0026](adr/0026-certified-thresholds-and-a-paired-release-gate.md)). |
| **Frontier** | The certificate at every threshold from 0.95 to 0.50, each corrected so all hold at once: the certified trade-off between precision and coverage. |
| **Learn-then-Test** | Choosing the threshold by testing a fixed list of thresholds from the highest down and stopping at the first that cannot be certified (`RATCHET_CALIBRATION=ltt`). |
| **Labelling probability (`label_p`)** | The designed chance that the teacher labels a student answer: 1 when the request escalated, the audit rate when the student served. Logged with every request, so the replay can weight each label by 1 / `label_p` ([ADR 0028](adr/0028-designed-propensity-replay.md)). |
| **Ledger replay** | Recomputing a version's coverage, precision and cost from its logged traffic at another threshold or audit rate (`GET /ratchet/replay`, `python -m ratchet.replay`). Precision uses Hajek's estimator (labels weighted by 1 / `label_p`); its interval is approximate. One version at a time. |
| **Positivity** | Every answer the replay would count has some chance of being labelled. With an audit rate of 0, served answers are never labelled, so precision above the live threshold cannot be estimated and the replay reports it as null. |
| **Calibrated baseline** | The ratio of what the teacher really cost on audited answers to the token formula's estimate (which assumes `RATCHET_TEACHER_REASONING_TOKENS`), with its standard error. |
| **Negative flip** | A golden row the live version answered right that a candidate answers wrong. The paired gate compares negative and positive flips on the same rows with McNemar's exact test. |
| **Live** | The version serving 100% of student traffic. |
| **States** | `candidate`, `rejected`, `shadow`, `canary`, `live`, `retired`, `rolled_back`. |
| **Cold start** | The period before any student exists. The teacher answers everything. |
| **Drift** | New kinds of requests the student has never seen, for example after a product launch. Escalations rise, then fall as Night School learns them. |
| **Night School** | The nightly job: build dataset, train, evaluate, run the release gate. |
| **Distillation** | Training a small model to copy a larger model's answers. |
| **LoRA** | Low-Rank Adaptation. Trains a small set of extra weights (an adapter) on top of a frozen base model. Cheap to train and to swap. |
| **Adapter** | The LoRA weights for one version, stored in the customer's bucket. |
| **Provenance ledger** | The record of every training example: teacher, license, license URL, hash, split, times seen. |
| **Model bill of materials (BOM)** | A summary of the provenance ledger: every teacher and license behind the training data, with counts and a SHA-256 fingerprint of the dataset. `GET /ratchet/bom`. |
| **Adapter BOM** | The bill of materials of one adapter, written at training time from the exact rows it used, with the golden-set hash and its evaluation. Signed with Ed25519 when a signing key is set. `GET /ratchet/versions/{id}/bom`; check it with `python -m ratchet.bom verify`. |
| **Key id** | The first 16 hex characters of the SHA-256 of an Ed25519 public key. Names which key signed a BOM. |
| **Set commitment** | A 32-byte root in an adapter's BOM that fixes its exact training (or golden) set: the root of a sparse Merkle tree over the examples' hashes, each mapped to a hash of its label, teacher, license and redaction count. One example can then be proved in or out of the set without revealing the others ([ADR 0027](adr/0027-committed-training-sets.md)). |
| **Membership proof / non-membership proof** | The sibling hashes along one example's path in a set commitment's tree, which fold to the signed root. It shows the example was in the set (with its leaf value) or was not; `python -m ratchet.proof verify` checks one offline. |
| **Held-out certificate** | Every golden example's non-membership proof against an adapter's training root, with the golden leaves: shows from the BOM alone that no golden example was trained on (`GET /ratchet/versions/{id}/heldout`, `python -m ratchet.proof heldout`). |
| **PII redaction** | Replacing identifiers (emails, phones, cards, IBANs, national IDs, IPs, secrets) with placeholders such as `[EMAIL]` before a request becomes a training example (`ratchet/redact.py`). |
| **API client** | The name attached to a gateway API key (`RATCHET_API_KEYS=name:key`). Requests, cost and savings are reported per client. |
| **License-clean distillation** | Distilling only from teachers whose licenses allow training on their outputs. Closed models whose terms forbid it are refused. |
| **OpenMDW-1.1** | The license of Nemotron 3 Ultra. It places no restrictions on outputs. |
| **Baseline cost** | What a request would have cost if the teacher answered it alone. Used for savings receipts. |
| **Mock mode** | Running with simulated models, synthetic tickets and simulated search results. No keys or GPUs needed. Numbers show loop behavior, not real model quality. |
| **Workload** | The kind of simulated traffic in mock mode and the simulator: `tickets` (support tickets), `research` (Scout's triage and reader calls) or `mixed` (half each). Set with `--workload` or `?workload=`. |
| **Warm-up** | In mock mode, simulated days and nights run at startup (`RATCHET_MOCK_WARMUP_DAYS`), so the demo opens with a trained student instead of a cold start. |
| **Scout** | The multi-agent research demo. Five agents (planner, searcher, triage, reader, writer) answer a question from the web, and every LLM call goes through Ratchet. Triage and reader calls are distilled; planner and writer calls are pinned. See [SCOUT.md](SCOUT.md). |
| **Budget guard** | The limits that protect the public Scout demo in live mode: a daily spend cap, a daily run cap and a per-IP hourly run limit (`ratchet/scout/guard.py`), with a share held back for judges; kept in the ledger and restored at startup (ADR 0120). |
| **Judges' reserve** | Scout runs, spend and Tavily credits per day that the public cannot use, unlocked by a judge code sent once to `POST /scout/judge` and then carried by a signed cookie (ADR 0120). |
| **Tavily** | The web search API Scout's searcher uses in live mode. |
| **Live mode** | Running against real Token Factory and Nebius AI Cloud. |
| **Token Factory** | Nebius's OpenAI-compatible inference service for open models. |
| **Serverless Jobs / Endpoints** | Nebius AI Cloud services that run a container to completion (jobs) or serve it at a URL (endpoints), billed per second. |
