# Architecture

Ratchet is a gateway that sits between your app and a large "teacher" model. A small "student" model you own answers each request first. If it is unsure, the request goes to the teacher. Every teacher answer is stored with its license, and every night a new student is trained on those answers and released through a series of safety gates.

This page describes v0.1.0 exactly as implemented in `ratchet/`. Product context is in [PRD.md](PRD.md); the reasoning behind each choice is in the [ADRs](adr/README.md).

## 1. Components

```
                         ┌──────────────────────────────── Nebius Token Factory ─────────┐
                         │  Teacher: nvidia/Nemotron-3-Ultra-550b-a55b                   │
                         │  Judge:   nvidia/nemotron-3-super-120b-a12b (free-text eval)  │
                         │  Fallback student: nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B      │
                         └──────────────▲──────────────────────────────────────────────┘
                                        │ OpenAI-compatible HTTPS
┌──────────┐   /v1/chat/completions   ┌─┴──────────────────────────────┐
│ Your app │ ───────────────────────▶ │ Gateway  (ratchet/gateway.py)  │──── SQLite ledger (ratchet/ledger.py)
└──────────┘ ◀─────────────────────── │  Ratchet router (router.py)    │      requests · examples · versions · events
                                      │  ReleaseGate.tick (release.py) │
                                      │  Dashboard (dashboard.html)    │
                                      └─┬──────────────────────────────┘
                                        │ OpenAI-compatible HTTPS (model = adapter name)
                         ┌──────────────▼──── Nebius AI Cloud ─────────────────────────────┐
                         │ Serverless Endpoint: vLLM + nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16 │
                         │   LoRA adapters loaded at runtime (/v1/load_lora_adapter)         │
                         │ Serverless Job (nightly): ratchet.nightschool.train (Unsloth)     │
                         │ Object Storage bucket mounted at /data: night/train.jsonl,        │
                         │   adapters/<version>/                                             │
                         └───────────────────────────────────────────────────────────────────┘
          Night School driver: python -m ratchet.live night | tick   (ratchet/live.py, cron)
```

| Module | Responsibility |
|---|---|
| `ratchet/config.py` | `Settings` (read from environment variables) and the `Price` table |
| `ratchet/providers.py` | `Completion`, the `Provider` protocol, and `OpenAICompatProvider` (httpx client for Token Factory and vLLM) |
| `ratchet/tasks.py` | `Task` definitions: which answer fields must match for two answers to "agree" |
| `ratchet/confidence.py` | `score()` (confidence from samples) and `calibrate_threshold()` |
| `ratchet/router.py` | `Ratchet.handle()`: the per-request cascade, audits, shadow, cost accounting |
| `ratchet/release.py` | `ReleaseGate` (golden gate, shadow, canary, rollback) and `wilson()` |
| `ratchet/ledger.py` | `Ledger`: SQLite storage, provenance, stats |
| `ratchet/licenses.py` | Teacher license registry; refuses teachers that forbid distillation |
| `ratchet/nightschool/dataset.py` | Builds train and golden sets from the ledger |
| `ratchet/nightschool/train.py` | LoRA fine-tune script, run inside the Serverless Job |
| `ratchet/nightschool/evaluate.py` | `cascade_eval()` and `LiveEvaluator`: golden-set precision, coverage and threshold |
| `ratchet/nightschool/orchestrate.py` | `run_night()`: one night of training and release |
| `ratchet/live.py` | Live Night School on Nebius (`NebiusTrainer`, `load_adapter`, CLI) |
| `ratchet/gateway.py` | FastAPI app: OpenAI-compatible API, stats, dashboard, mock-only demo controls |
| `sim/` | Synthetic tickets and mock teacher/student for the offline simulation and tests |

There are two runtime modes, chosen by `RATCHET_MODE`:

- **mock** (default): in-memory ledger, mock teacher and students from `sim/`, demo controls enabled. No network or keys.
- **live**: SQLite file at `RATCHET_DB`, real teacher on Token Factory, student on your vLLM endpoint (or the hosted fallback model when `RATCHET_STUDENT_BASE` is empty).

## 2. Request path

`Ratchet.handle(messages, task_name)` in `router.py`:

```mermaid
sequenceDiagram
    participant App
    participant GW as Gateway
    participant S as Student (vLLM)
    participant T as Teacher (Ultra)
    participant SH as Shadow candidate
    participant L as Ledger
    App->>GW: POST /v1/chat/completions (X-Ratchet-Task)
    GW->>GW: pick version: canary with prob traffic_pct%, else live, else none
    alt a version was picked
        GW->>S: n=3 samples, temperature 0.7, logprobs
        GW->>GW: score() -> confidence
        alt confidence >= version threshold
            GW-->>GW: serve student answer
            opt with probability audit_rate
                GW->>T: audit (answer stored as training example)
            end
        else below threshold
            GW->>T: escalate (answer stored as training example)
        end
    else cold start (no live, canary not picked)
        GW->>T: teacher answers (stored)
    end
    opt a version is in shadow
        GW->>SH: n=3 samples, score, would it serve?
        opt would serve AND no teacher answer yet AND disagrees with served answer
            GW->>T: arbitrate (stored)
        end
    end
    GW->>L: log_request(...)
    GW->>GW: ReleaseGate.tick()
    GW-->>App: chat.completion + "ratchet" block, X-Ratchet-Served-By
```

Details that matter:

1. **Version choice** (`_pick_version`): if a version is in `canary`, it is chosen with probability `traffic_pct / 100`; otherwise the `live` version is used. With no live version (cold start) and the canary not picked, the request goes straight to the teacher.
2. **Student sampling** (`_sample`): one call with `n = Settings.samples` (default 3), `temperature=0.7`, `logprobs=True`. The model name sent is whatever the resolver returns. In live mode that is `version.adapter_uri`, which is the LoRA adapter name.
3. **Threshold**: `Ratchet.threshold(version)` returns `version.eval["threshold"]` (calibrated at training time), falling back to `Settings.confidence_threshold` (default 0.80).
4. **Audits**: when the student serves, the teacher is also called with probability `audit_rate` (default 0.05; mock mode raises it to at least 0.10). The audit result (`audit_agree`) is the live quality signal for canary and live versions, and the teacher's answer also becomes training data.
5. **Teacher calls** (`_call_teacher`): the teacher answer is stripped of `<think>…</think>` and stored with `Ledger.add_example(task, messages, completion, teacher_model, license_name, license_url)`. The license comes from `licenses.require_distillable(settings.teacher_model)`, checked once when `Ratchet` is constructed. A closed teacher raises `LicenseError` at startup.
6. **Shadow**: see [state machine](#5-version-state-machine). The shadow candidate never serves.
7. **Logging**: one row in `requests` per call (columns below).
8. **Gate tick**: the gateway calls `rt.gate.tick()` after every request. In live mode, `python -m ratchet.live tick` can also be run from cron.

The response text is the served answer with reasoning tags removed.

## 3. Nightly loop (Night School)

`run_night(ledger, trainer, evaluator, gate, base_model, min_new_examples=50, task=None)` in `nightschool/orchestrate.py`:

```
 ┌─ release in flight? (any version in shadow or canary) ── yes ──▶ event "skip", stop
 │ no
 ├─ dataset.build(): train rows + golden rows (license-checked)
 ├─ new = train_examples − max(train_examples of all previous versions)
 │     new < min_new_examples (50) ───────────────────────────────▶ event "skip", stop
 │     no golden rows ────────────────────────────────────────────▶ event "skip", stop
 ├─ champion re-check: evaluate the LIVE version on today's golden set, overwrite its eval
 ├─ trainer.train(train_rows, base_model, name="student-v{N+1}") → adapter_uri
 ├─ ledger.create_version(state="candidate")
 ├─ evaluator.evaluate(adapter_uri, golden_rows) → {precision, coverage, threshold, ...}
 ├─ event "trained"
 └─ gate.admit(version) → rejected | canary 10% (cold start) | shadow
```

- **Mock mode**: `MockFleet` (in `sim/mock_models.py`) is both trainer and evaluator.
- **Live mode**: `NebiusTrainer` (`ratchet/live.py`) does four things. It writes `RATCHET_DATA_DIR/night/train.jsonl`, runs `RATCHET_SYNC_CMD` (if set), runs `RATCHET_TRAIN_CMD` (default `bash deploy/train_job.sh {version}`), then hot-loads the adapter into vLLM with `POST {root}/v1/load_lora_adapter`. `LiveEvaluator` then scores it.

### Datasets (`nightschool/dataset.py`)

- Every example gets a fixed split when first stored: `golden` if `int(sha256[:8], 16) / 0xFFFFFFFF < golden_fraction` (default 0.10), else `train`. The split never changes, so evaluation data cannot leak into training through re-splitting.
- **Frequent requests:** an example whose `seen` count is at least `FREQUENT = 3` is treated as `train`, even if its stored split is `golden`. Requests that keep repeating are too valuable to hold out ([ADR 0007](adr/0007-golden-set-split-and-frequent-requests.md)).
- Rows whose teacher is unknown or not distillable (per `licenses.lookup`) are dropped.
- Training is cumulative: each night trains from the **base model** on every train row so far ([ADR 0004](adr/0004-retrain-from-base-cumulative.md)).
- `build()` also writes `manifest.json`: train and golden counts, `dataset_sha256` (a hash of the train rows' example hashes), and the bill of materials.

## 4. Data model (SQLite)

Created by `SCHEMA` in `ledger.py` (`CREATE TABLE IF NOT EXISTS`).

### `requests`: one row per gateway call

| Column | Type | Meaning |
|---|---|---|
| `id` | INTEGER PK | Auto-increment. Also used as the "since" marker for stages |
| `ts` | REAL | Unix time |
| `day` | INTEGER | `Ledger.day` at the time (set by the simulator or demo; see note) |
| `task` | TEXT | Task name |
| `served_by` | TEXT | `student` or `teacher` |
| `student_version` | INTEGER | Version that was tried first; NULL on cold start |
| `confidence` | REAL | Student confidence; NULL if no student was tried |
| `escalated` | INTEGER | 1 if the student was below threshold |
| `audited` | INTEGER | 1 if the teacher double-checked a student answer |
| `audit_agree` | INTEGER | 1/0 for audited rows, NULL otherwise |
| `shadow_version` | INTEGER | Shadow candidate that ran, if any |
| `shadow_serve` | INTEGER | 1 if the shadow would have served |
| `shadow_agree` | INTEGER | 1 if the shadow agreed with the best available reference |
| `latency_ms` | REAL | Student latency (max over samples) plus teacher latency if called |
| `cost_usd` | REAL | Actual cost of this request (student + teacher + audit + shadow + arbitration) |
| `baseline_cost_usd` | REAL | Estimated cost if the teacher had answered alone |

### `examples`: teacher-written training data with provenance

| Column | Type | Meaning |
|---|---|---|
| `id` | INTEGER PK | |
| `ts`, `day`, `task` | | When and for which task it was first stored |
| `hash` | TEXT UNIQUE | `sha256(json({"task","messages"}, sort_keys))`; deduplicates identical requests |
| `messages` | TEXT | JSON request messages |
| `completion` | TEXT | Teacher answer, reasoning removed |
| `teacher_model` | TEXT | Model ID of the teacher |
| `license`, `license_url` | TEXT | License that permits training on this output |
| `split` | TEXT | `train` or `golden` (fixed from the hash) |
| `seen` | INTEGER | Times this exact request was answered by the teacher (starts at 1, +1 per duplicate) |

### `versions`: student models

| Column | Type | Meaning |
|---|---|---|
| `id` | INTEGER PK | Version number (`v{id}` in the UI) |
| `created_ts`, `day` | | Creation time |
| `base_model` | TEXT | Base model ID |
| `adapter_uri` | TEXT | Adapter name, e.g. `student-v3` (also the vLLM model name) |
| `train_examples` | INTEGER | Size of the train set used |
| `dataset_hash` | TEXT | `dataset_sha256` from the manifest |
| `eval` | TEXT (JSON) | `golden_n`, `golden_agreement`, `threshold`, `coverage`, `precision`, `test_n`, optional `judge_pass_rate`, `rechecked_day` |
| `state` | TEXT | `candidate`, `rejected`, `shadow`, `canary`, `live`, `retired`, `rolled_back` |
| `traffic_pct` | INTEGER | 10 or 50 in canary, 100 when live, 0 otherwise |
| `stage_since` | INTEGER | Max `requests.id` when the current stage began; audits are counted only after it |

### `events`: release log

`id`, `ts`, `day`, `kind`, `detail`. The kinds written by the code are `trained`, `skip`, `rejected`, `shadow`, `canary`, `live`, `retired`, `rollback`, `alert` and `fault` (demo only). Their meanings are in [RUNBOOK.md](RUNBOOK.md#event-kinds).

> Note on `day`: the simulator and the mock demo endpoint set `Ledger.day` themselves. In live mode the ledger is opened with `auto_day=True`: day 1 starts at the first logged request and a new day begins every 24 hours. Existing database files are migrated on open (`Ledger._migrate()` adds columns introduced after v0.1).

## 5. Version state machine

Thresholds are the `ReleasePolicy` defaults in `release.py`: `min_precision=0.95`, `min_coverage=0.25`, `tolerance=0.02`, `min_shadow=100`, `min_audits=20`, `live_floor=0.92`. Canary steps are `CANARY_STEPS = [10, 50]`, then 100.

```mermaid
stateDiagram-v2
    [*] --> candidate: run_night trains
    candidate --> rejected: golden precision < 0.95\nor coverage < 0.25\nor regresses vs live (>0.02)
    candidate --> canary: admit, no live version (cold start)\ntraffic 10%
    candidate --> shadow: admit, live version exists
    shadow --> rejected: ≥100 would-serve answers and\nprecision < 0.95 or coverage < live − 0.02
    shadow --> canary: ≥100 would-serve answers,\nprecision ≥ 0.95, coverage ≥ live − 0.02\ntraffic 10%
    canary --> canary: ≥20 audits this stage,\naudited precision ≥ 0.92 → 10% → 50%
    canary --> live: at 50%, ≥20 audits, precision ≥ 0.92\n(old live → retired)
    canary --> rolled_back: ≥20 audits and Wilson upper bound < 0.92
    shadow --> retired: newer candidate admitted
    canary --> retired: newer candidate admitted
    live --> retired: a canary is promoted
```

### Golden gate: `ReleaseGate.admit(vid)`

Using the candidate's `eval` (calibrated on half of the golden set, measured on the other half):

1. Reject if `precision < 0.95`.
2. Reject if `coverage < 0.25`.
3. If a live version exists, reject if `precision < live.precision − 0.02` **or** `coverage < live.coverage − 0.02`. The live numbers were re-measured on the same golden set the same night ([§3](#3-nightly-loop-night-school)).
4. Otherwise every version in `shadow` or `canary` is set to `retired` (event `retired`). Then:
   - no live version → `canary` at 10% (event `canary`, "first student");
   - live version exists → `shadow` (event `shadow`).

In practice step 4's retire branch rarely runs, because `run_night` skips training while a shadow or canary exists.

### Ticks: `ReleaseGate.tick()`

Run after every gateway request and by `python -m ratchet.live tick`.

1. **Live watch:** take the audits of the live version since it became live. If there are at least `min_audits` and the Wilson upper bound of precision is below `live_floor`, write an `alert` event. No automatic action is taken on the live version.
2. **Shadow:** `shadow_quality(id)` returns `served` (would-serve count), `precision` (mean `shadow_agree` where `shadow_serve=1`), `coverage = served / n`, and `live_coverage` (share of the same requests the live student actually served). Once `served ≥ min_shadow` (100):
   - precision < 0.95 → `rejected`;
   - coverage < live_coverage − 0.02 → `rejected`;
   - otherwise → `canary` at 10%.
3. **Canary:** `version_live_quality(id)` counts audits of answers this version served since `stage_since`. With `n ≥ min_audits` (20) and audited precision `p`:
   - Wilson upper bound of `p` below `live_floor` (0.92) → `rolled_back` (event `rollback`). Traffic returns to the live version, or to the teacher on cold start.
   - else if `p < 0.92` → hold (no change; keep auditing).
   - else if traffic is 10% → 50% (event `canary`); the stage counter resets because `set_version` updates `stage_since`.
   - else → old live set to `retired`, canary set to `live` at 100% (event `live`).

### Shadow reference and arbitration

For each request while a shadow exists, the shadow candidate is sampled and scored like a real student. `shadow_serve = confidence ≥ shadow threshold`. The reference for agreement is chosen in this order:

1. The teacher's answer, if the teacher was called for this request (escalation or audit).
2. If the shadow would serve and disagrees with the served answer, the teacher is called to **arbitrate** (stored as training data) and its answer is the reference.
3. Otherwise the served answer (the live student's).

So disagreements are always settled by the teacher, while agreements with the live student are accepted as-is.

## 6. Confidence and calibration

### Score (`confidence.score`)

Given `n` samples and a `Task`:

1. Each sample is canonicalised by `Task.canonical()`. For tasks with `compare_keys` (e.g. `ticket-triage` → `category`, `priority`), the JSON is parsed and only those fields are kept, lower-cased and trimmed. Otherwise the whole text is used, with reasoning removed and whitespace normalised. Unparseable JSON becomes `<invalid>`.
2. If every sample is invalid, confidence is 0.
3. `agreement = votes for the majority answer / n`.
4. The best sample is the majority answer with the highest mean token probability, `p = exp(mean(token logprobs))`.
5. `confidence = 0.4 · p + 0.6 · agreement`, or `agreement` alone if no logprobs were returned.

### Threshold calibration (`calibrate_threshold`, `cascade_eval`)

`cascade_eval(sample, golden_rows, samples=3, target_precision=0.97, floor=0.5)`:

1. Score each golden row with the candidate. Even-indexed rows go to a calibration set, odd-indexed rows to a test set; each point is `(confidence, agrees_with_teacher)`.
2. `calibrate_threshold(calib, 0.97)` sorts points by confidence (descending) and walks down **in groups of equal confidence** (ties included before checking). It returns the lowest confidence at which cumulative precision is still ≥ 0.97, or `inf` if none qualifies.
3. `threshold = min(1.01, max(0.5, calibrated))`. The value 1.01 means "serve nothing".
4. On the **test** half: `coverage = share with confidence ≥ threshold`, and `precision = agreement rate among those`.

Returned keys: `golden_n`, `golden_agreement` (majority-answer agreement over all rows), `threshold`, `coverage`, `precision`, `test_n`, plus `judge_pass_rate` from `LiveEvaluator` when free-text tasks exist.

### Wilson score interval (`release.wilson`)

For `s` successes out of `n` and `z = 1.96`:

```
p      = s / n
denom  = 1 + z² / n
centre = (p + z² / (2n)) / denom
half   = z · sqrt( p(1−p)/n + z²/(4n²) ) / denom
bounds = [max(0, centre − half), min(1, centre + half)]
```

Rollback requires the **upper** bound to be below the floor, which means strong evidence the version is worse. With 20 audits and a true precision of 96%, the chance of a false rollback is negligible. With a raw "point estimate < 92%" rule it would have been about 19%. Example: 19/20 gives roughly [0.76, 0.99], so no rollback; 10/20 gives roughly [0.30, 0.70], so rollback.

## 7. Cost accounting

`Price(input, output)` is in USD per 1M tokens (`config.DEFAULT_PRICES`). Per request:

- **Student:** `price("student").cost(in_tokens of first sample, sum of out_tokens over samples)`. The prompt is counted once and every sample's output is counted.
- **Teacher** (escalation, audit or arbitration): `price(teacher_model).cost(in, out)`. The `out` count includes reasoning tokens, as Token Factory reports them in usage; the mock teacher adds `teacher_reasoning_tokens`.
- **Shadow:** the candidate's sampling cost, plus any arbitration, is added to `cost_usd`. Shadowing is not free.
- **Baseline:** if the teacher was called, the baseline is that call's cost. Otherwise it is estimated as `teacher price × (student in_tokens, student out_tokens + teacher_reasoning_tokens)` (default 300 reasoning tokens).

`stats_by_day()` derives `cost_per_1k`, `baseline_per_1k` and `savings_pct = 1 − cost/baseline`; `totals()` gives `saved_usd`. The student price is a **per-token proxy** set to the hosted Nano price. Real self-hosted cost is GPU-hours, so live savings should be computed from the Nebius bill too.

## 8. Provenance

- `GET /ratchet/bom` returns `Ledger.bill_of_materials()`: counts grouped by teacher, license, license URL and split, plus `examples_sha256` (a SHA-256 over all example hashes in sorted order).
- `train.py` writes `ratchet_manifest.json` next to each adapter, listing the base model, example count and the (teacher, license) pairs.
- `licenses.require_distillable()` refuses unknown teachers and any model whose ID (after the last `/`) starts with `gpt`, `o1`, `o3`, `o4`, `claude` or `gemini`.

## 9. Design decisions

| ADR | Decision |
|---|---|
| [0001](adr/0001-open-teacher-only.md) | Only teachers whose license allows training on outputs |
| [0002](adr/0002-student-on-nebius-serverless-not-token-factory.md) | Train and serve the student on Nebius AI Cloud, not Token Factory |
| [0003](adr/0003-per-request-cascade-with-calibrated-confidence.md) | Per-request cascade with a calibrated, per-version threshold |
| [0004](adr/0004-retrain-from-base-cumulative.md) | Retrain from the base model on the cumulative dataset nightly |
| [0005](adr/0005-shadow-canary-rollback-with-wilson-bounds.md) | Shadow → canary → live with Wilson-bound rollback |
| [0006](adr/0006-sqlite-ledger-for-v0.md) | SQLite ledger for v0 |
| [0007](adr/0007-golden-set-split-and-frequent-requests.md) | Hash-based golden split; frequent requests always train |
