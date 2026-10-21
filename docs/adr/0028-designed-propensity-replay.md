# ADR 0028: Designed-propensity replay: a ledger time machine for thresholds and audit rates, and a calibrated baseline

- Status: Accepted
- Date: 2026-10-21

## Context

Operators and judges ask questions the ledger could answer but did not:

- What would precision, coverage and cost have been last week at threshold t, or at audit rate a?
- How much money does the calibrated threshold leave on the table?
- How far off is the baseline's assumption that the teacher writes 300 reasoning tokens (`RATCHET_TEACHER_REASONING_TOKENS`)? Nobody measures it.

Audits alone cannot answer the first question below the live threshold: an audit only ever sees answers the student served. But Ratchet designs its own labelling, so it knows the probability of every label it sees:

- a request the student answered and escalated is labelled by the teacher with probability 1;
- an answer the student served is labelled with the audit rate it was drawn at (one `rng.random()` per served answer, ADR 0025);
- on every escalation both the student's majority answer and the teacher's answer already exist, so the student's correctness on that request costs one `task.agree` call, which was thrown away.

Known labelling probabilities that are above 0 (positivity) are the textbook condition for unbiased inverse-probability estimates (Horvitz-Thompson, Hajek). The labelling depends only on the confidence, the logged threshold and an independent random draw, never on whether the student was right, so the missing labels are missing by design.

[ADR 0024](0024-evidence-foundations.md) added the columns (`threshold`, `label_p`, `student_agree`, `baseline_est_usd`) and the estimators (`stats.hajek_mean`, `kish_n_eff`, `ratio_estimate`); ADR 0025 started writing `label_p`.

## Decision

### Logging only on the request path

`Ratchet.handle` writes, on every row where the student answered:

- `threshold`: the threshold the answer was compared with (`Ratchet.threshold(version, model, task)`). NULL when the student did not answer (no version, a failed call, an open breaker, pinned): nothing was compared.
- `label_p`: the audit rate the answer was drawn at when it was served (the canary's own rate when set), 1.0 when the request escalated. Unchanged from ADR 0025; a later drift boost of the audit rate changes the same variable, so it is logged too.
- `student_agree`: `int(task.agree(student answer, teacher answer))` when the teacher's answer came from this request's escalation or random audit, the student's samples held a valid answer, and the teacher's answer was valid (INV-16). On audited rows it equals `audit_agree`. Never from a shadow arbitration (not a random draw: it runs only when the shadow disagrees), from the fallback teacher or a degraded answer (stand-ins, ADR 0019: a label is the primary teacher's or nothing, as for audits), or from a cut-off or invalid teacher answer.
- One label, one teacher. An audit is the teacher's deterministic answer: temperature 0, one answer, without `CHECK_DROPS` (ADR 0017). An escalation's answer is the serving call, which takes the client's sampling: a temperature, a seed, `logit_bias`, penalties, and `CHOICES_TEMPERATURE` (0.7) for n > 1 choices. Comparing the student with such an answer below the threshold, and with the deterministic answer above it, would put two different estimands in one weighted mean (and a `logit_bias` client could choose the labels, threat model T2). So an escalation sets `student_agree` only when the serving call ran under the audit's conditions (`router._labels_like_audit`: one choice, and the serving parameters equal the audit's once the client's `logprobs` and `top_logprobs` are set aside, because they report on the answer and never change it). Otherwise `student_agree` is NULL: a designed label that was not obtained, like a failed teacher call. The labelling probability stays 1.0; the miss depends on what the client sent, not on the student's answer.
- `baseline_est_usd`: the token formula's all-teacher estimate (`Ratchet._formula_baseline`) for every student answer, also when the teacher was called. For a served answer it is the baseline itself, computed by the same expression, so `baseline_cost_usd` does not change by a bit.

No `reason` column: the `route` column already says why a request was not served (ADR 0024). `student_cost_usd` is used as ADR 0023 defined it (the live samples and any shadow's).

Request-path cost: at most one `task.agree` and one `Task.check` per escalation (string work, microseconds) and three more values in the existing INSERT. No model call, no random draw, no extra SQL statement. The reference simulation and the three reference workloads do not move.

### The replay (`ratchet/replay.py`)

`load(ledger, version, since_day, until_day)` reads one version's rows (`Ledger.replay_rows`): requests its student answered with `confidence`, `label_p` and `threshold` written. Left out, and counted by reason: rows written before these columns (`before_logging`), rows without a student answer (`no_student_answer`), rows of a task that is pinned now (`pinned_now`: the student will never answer it again; INV-10 at read time, as Night School reads it) and rows with out-of-range values from a ledger edited outside Ratchet (`malformed`).

For a threshold t (default: each request at its logged threshold, which reproduces what happened even though a live version's threshold moves at every re-score):

- **Coverage** = (1/N) Σ 1[c ≥ t]: exact, no label needed. A request with no valid answer scores 0 and is never served at any t > 0; one that escalated only because fewer than the client's n samples agreed (route `n_choices`) is never served at any t either, because that does not depend on the threshold. Nor is a row logged at `SERVE_NOTHING` while its task's spec is still changed for the version (`Ratchet.spec_changed`, FR-2.15): the router holds that task to the teacher at any threshold until a night re-scores the version. One case is not logged and counts as servable: a request for n > 1 choices whose confidence was below the threshold and whose samples also had fewer than n in agreement escalated on route `confidence`, and at a lower threshold it would escalate on route `n_choices` instead. Such requests are rare (n > 1 and a split vote) and only make coverage below the logged threshold optimistic; telling them apart would need a column, which this item does not add.
- **Precision** (Hajek) = Σ y/π over labelled served rows / Σ 1/π over the same rows, with π = `label_p` and y = `student_agree`. A designed label that was not obtained (the teacher failed or answered invalidly) is missing; Hajek's self-normalisation treats such misses as random within the row's weight.
- **Positivity**: if any row served at t has π = 0 (audit rate 0), precision is null with the reason "audit rate was 0: precision above the live threshold cannot be identified". Nothing is extrapolated.
- **Interval**: `stats.wilson(precision × n_eff, n_eff)` at Kish's n_eff = (Σw)² / Σw². Labelled "approximate (Wilson at Kish n_eff)": it is a normal-theory approximation for a weighted sample, not an exact or anytime-valid bound.
- **Cost** at threshold t and audit rate a = Σ s_i + Σ over c < t of T_i + a × Σ over c ≥ t of T_i, where s_i is the logged student spend and T_i the request's all-teacher cost: `baseline_cost_usd` when the teacher answered the escalation or an audit, else `baseline_est_usd` × R. a defaults to each served row's logged rate (a request that escalated in fact is priced at the version's mean logged rate). An audit is priced as the request's T_i, exact for one choice. Baseline = Σ T_i; savings = 1 − cost / baseline; `served_errors_per_1k` = 1000 × coverage × (1 − precision). The audit rate changes cost only: precision comes from the labels that were logged.

`frontier(rows, 0.30, 1.00, 0.01)` is `at()` over the grid in one pass over the rows sorted by confidence (O(N log N + G)); each row equals `at(rows, t)` to the bit.

### The calibrated baseline

`ratio(rows)` is Cochran's ratio estimator on audited answers the student served (a random sample of them): R = Σ w·y / Σ w·x with x = `baseline_est_usd`, y = `baseline_cost_usd` (what the audit actually cost the teacher) and w = 1 / `label_p`, with its linearised standard error (`stats.ratio_estimate`, shared with the receipts). Below 30 audited answers R = 1 and the status is `insufficient`. R prices the unaudited served rows in the replay, and says how far the 300-token assumption is from what the teacher writes. In the mock models the teacher writes exactly the assumed reasoning tokens, so R is 1.000 there (simulation); the receipts item adds a mock teacher whose reasoning varies.

### Suggestion, not a certificate

`suggest(rows, target=0.97)` scans `stats.CERT_GRID` from 0.95 down and stops at the first threshold whose interval's lower bound is below the target; it returns the last one that passed, or None. The earlier proposal called this Learn-then-Test; it is not: Learn-then-Test needs a valid p-value per hypothesis (ADR 0026), and the replay's interval is approximate and computed on the same traffic the operator looks at. So the result carries `advisory: "approximate interval, not a certificate; thresholds change only through Night School"` and nothing applies it. `money_left_on_table` is the savings difference (and dollars) between the suggested threshold and the current one, when the suggestion is lower.

### Surfaces

- `GET /ratchet/replay?version=&threshold=&audit_rate=&since_day=&until_day=` and `GET /ratchet/replay/frontier?version=&target=&audit_rate=&since_day=&until_day=`: read-only, public like `/ratchet/stats` (aggregates only), both modes, the live version by default, 400 without one. One version only: `version` twice, a list or a word is a 400, because confidence scales differ between versions and pooling them would mix incomparable numbers. Labelled `simulation (mock models)` in mock mode, `per_token` as the cost model (NFR-11). The current threshold is the one the router holds the version's answers to (the hosted fallback student's configured default in live mode without a student endpoint). A replay reads the version's whole history into memory, so the gateway runs one at a time (another request meanwhile is a 429 with `Retry-After: 1`), and `Ledger.replay_rows` reads in chunks of 20,000 rows on a `(student_version, id)` index (`requests_version_id`), each under the ledger lock, up to the last request id when it began, so the request path's `log_request` never waits for a whole history.
- `python -m ratchet.replay --db PATH [--version V] [--sweep 0.3:0.95:0.05] [--audit-rate A] [--target 0.97] [--json]`: one table per version, labelled as simulation when the simulator wrote the ledger (its compute intervals say `simulation`).
- `python -m sim.simulate --db PATH` keeps a run's ledger in a new file for it.

## Consequences

- Precision at thresholds below the live one becomes measurable from data Ratchet already pays for. In the seed-7 bad-deploy run (6 days, 1,500 requests a day; simulation, mock models), the live v1 replays 6,208 requests: 97.7% coverage and 99.2% precision (approximate 95% interval 98.1% to 99.7%) at its threshold 0.5, and 100% coverage at 97.0% precision at 0.30; the corrupted v3's 123 requests replay at 40% precision (19.8% to 64.3%).
- The replay reproduces each version's logged coverage exactly and its audited precision exactly at the logged thresholds (`test_replay_reproduces_each_versions_logged_coverage_and_audits`), and the estimator is unbiased by seeded Monte Carlo (`test_replay_precision_is_unbiased_under_the_designed_labelling`), while the unweighted mean of the labels is off by several points.
- Validity rests on the design being what the log says: one random draw per served answer at the logged rate, and labels only from escalations and random audits, each the teacher's answer under the audit's conditions. The proposed lane-F invariant "Replay labels come only from designed draws" records that, and the existing "Every audit decision is one logged draw" covers the draw.
- Escalations under a client's sampling parameters, or for n > 1 choices, give no label, while audits of the same clients' served answers still do (an audit always runs under its own conditions). Precision stays an estimate of agreement with the teacher's deterministic answer at every threshold, but below the threshold it rests on the other clients' escalations: if the clients that send sampling parameters send different traffic, it describes theirs less well.
- A request whose escalation failed outright (the teacher down, no degraded answer) raises before it is logged, so the replay does not see it. Outages are rare and unrelated to the student's correctness; the docs say so.
- Rows logged before this change are left out and counted, never guessed.
- The interval is approximate. It is fine for an operator's question and wrong for a release decision; those stay with the golden certificate (ADR 0026) and the anytime-valid gate (ADR 0025).

## Alternatives

- **Audit-only replay.** Unbiased above the live threshold, blind below it, and much less data.
- **Log arbitration labels too.** More labels, but arbitration runs only when the shadow disagrees with the served answer, so its labelling probability is unknown and depends on correctness. It would bias every estimate.
- **Horvitz-Thompson instead of Hajek.** Exactly unbiased for a total, but its variance is larger and it is not bounded to [0, 1] for a mean; Hajek's O(1/n) bias is negligible at the replay's sizes.
- **Apply the suggested threshold automatically.** That would let an approximate interval on reused data move production. Thresholds stay with Night School's held-out calibration and certificate.
- **A separate `reason` column.** `route` already holds it.
- **Pool versions.** Their confidence scales differ (each adapter is calibrated on its own), so a pooled threshold means nothing.
