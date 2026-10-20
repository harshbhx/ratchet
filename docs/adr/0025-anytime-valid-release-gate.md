# ADR 0025: Anytime-valid release gate: e-process rollback, evidence-based promotion and confidence sequences from two integers per version

- Status: Accepted
- Date: 2026-10-20

## Context

The canary rule of [ADR 0005](0005-shadow-canary-rollback-with-wilson-bounds.md) rolls back when the 95% Wilson upper bound of audited precision falls below the floor (0.92), once a stage has `min_audits` audits. The gate runs that test again after every gateway request (and every 200 requests in the simulator). A fixed-sample interval looked at again and again does not keep its error rate:

- When true precision sits exactly on the floor, checking Wilson after every audit falsely rolls back about 22% of such versions within 400 audits, against a nominal 2.5% (simulation, counts only: 2,000 seeded runs, two seeds gave 22.6% and 22.1%; `tests/test_stats.py` keeps a quick version as a regression guard).
- The `min_audits` floor that limits this does the opposite harm to a broken deploy: it keeps serving until it has 15 to 20 audits. In the seed-7 reference run, the corrupted v3 had 41.2% audited precision and was rolled back only at n=17, after serving 145 answers.

The live alert has the same problem in a milder form.

## Decision

A mixture e-process (`stats.log_e_bernoulli`, built in [ADR 0024](0024-evidence-foundations.md)) decides instead. It is computed from two integers per version, audits and agreements (`Ledger.audit_counts`), and stays valid however often `tick()` runs.

- **Why it is valid.** Against H0 "precision ≥ p0" the e-value mixes likelihood ratios over a fixed grid of alternatives q_j < p0 (half the weight 0.01 to 0.10 below p0, half spread over the rest). One audit multiplies component j by a factor with conditional mean q·q_j/p0 + (1−q)(1−q_j)/(1−p0) = 1 + (q−p0)(q_j/p0 − (1−q_j)/(1−p0)), which is at most 1 whenever the audit's conditional agreement probability q is at least p0, because q_j < p0. No independence or identical distribution is needed. So each component, and any fixed mixture, is a nonnegative supermartingale starting at 1, and Ville's inequality gives P(the e-value ever reaches 1/α) ≤ α. The side "above" (H0 "precision ≤ p0") is the mirror image. Both are checked exactly in `tests/test_stats.py`.
- **Sequential canary (opt-in: `RATCHET_SEQUENTIAL=1`).** `_tick_canary` hands over to `_sequential_canary`, in this order:
  1. **Rollback:** the e-value against "precision ≥ floor" over *every* audit the canary has had reaches 1/`alpha_rollback` (20 at 5%). There is no minimum count: 0 of 2 audits agreeing gives 29.8. A canary has only ever served as a canary (a shadow never serves), so its whole life is its canary life.
  2. **Promotion,** on the current stage's audits once there are `min_audits`: the point estimate at or above the floor (today's rule), or with `RATCHET_PROMOTE_EVIDENCE=1` the e-value against "precision ≤ floor" reaching 1/`alpha_promote`. Advancing and promoting are unchanged; the event text appends the evidence.
  3. **Futility:** a stage with `max_stage_audits` (400) audits and no decision retires the canary (event `inconclusive`). No new state: `retired` exists, and Night School can train again (INV-5). It counts audits, not time, so a canary without audits holds, as today.
- **Live alert (default: `RATCHET_ALERT_EPROCESS=1`).** The alert fires once per live version when the e-value against "precision ≥ floor" over the audits since it went live reaches 1/`alpha_alert` (100 at 1%), with no minimum count. `RATCHET_ALERT_EPROCESS=0` keeps the Wilson rule.
- **Evidence everyone can re-check.** Every rollback, promotion, alert and inconclusive event carries the counts, the e-value and its threshold, and an anytime-valid 95% confidence sequence (`stats.confidence_sequence`, on a 0.001 grid). `ReleaseGate.evidence(v)` recomputes the same numbers from the counts alone (no stored state), and `/ratchet/stats` serves it for the canary and the live version.
- **Audit focusing (opt-in: `RATCHET_CANARY_AUDIT_RATE`).** A canary's answers are audited at their own rate, so its evidence arrives sooner. There is still exactly one random draw per served answer, and the rate it was drawn at is logged as `requests.label_p`, so the ledger replay (FR-16.5) and the receipts stay unbiased.
- **Policy checks.** `ReleasePolicy` refuses settings that would decide nothing: evidence-based promotion without the sequential rule, a futility cap below `min_audits`, and a cap too small for evidence-based promotion to ever pass (at a floor of 0.92 and α 5% a stage needs 56 audits that all agree; at 0.99 it needs 450, more than the default cap).
- **Defaults.** The sequential canary is opt-in, so the reference workloads (`scripts/reference_check.py`) and the reference simulation stay byte-identical. The live alert's e-process is the default: in the three reference workloads it raises no alert, and across seeds 1, 2, 3, 7 and 11 of the tickets, mixed and research workloads (8 days of 1,500 requests) the highest e-value any live version reached was 0.62, far from 100 (simulation).

### Guarantees

- **Rollback** is one e-process over the canary's whole life, never restarted per stage. So P(ever rolling back a version whose precision stays at or above the floor) ≤ `alpha_rollback`, with no union bound over stages.
- **Promotion with evidence** restarts in each stage, and a version reaches live only by passing the last stage's own test. So a version whose precision is at or below the floor reaches live with probability at most `alpha_promote`. (A union over stages, as in "2α", is not needed: passing the last stage is necessary.)
- **Live alert:** a live version whose precision stays at or above the floor is alerted on with probability at most `alpha_alert`, however long it serves.
- **Confidence sequence:** the true precision stays inside the reported 95% interval at every audit count at once, with probability at least 95% (Ville on each side, 2.5% each).

### Operating characteristics

Counts only, checked after every audit, floor 0.92, α 5%, horizon 400 audits; 2,000 seeded runs at the floor and 1,000 per row below (simulation, not measurements; two seeds agree within the noise).

| True precision | e-process (sequential rule) | Wilson after every audit (n ≥ 20) |
|---|---|---|
| 0.92 (on the floor): false rollback | 2.9% to 3.5% | 22.1% to 22.6% (nominal 2.5%) |
| 0.41: median audits to rollback | 4 | 20 |
| 0.50: median audits to rollback | 5 | 20 |
| 0.70: median audits to rollback | 18 | 20 |
| 0.85: median audits to rollback (detected within 400) | about 115 (97%) | about 35 (100%) |
| 0.88: median audits to rollback (detected within 400) | about 300 to 350 (57% to 62%) | about 80 to 90 (92%) |

Promotion with evidence needs 56 agreeing audits per stage at a 0.92 floor (55 give 19.996, just under 20), against 20 with the point rule.

In the seed-7 reference configuration, `python -m sim.simulate --bad-deploy-version 3 --compare-release` rolls the corrupted v3 back on the same day under both rules: under the sequential rule after 7 audits (3 agreeing, e-value 64.9) and 48 served answers, against 17 audits and 145 answers under Wilson (simulation).

## Consequences

- A catastrophic deploy stops after a handful of audits instead of `min_audits`, and a good version on the floor is rolled back about 3% of the time instead of about 22%.
- The price is power against small shifts: a version at 0.85 to 0.88 is caught later than by the Wilson rule (which catches it sooner only because it also rolls back good versions eight times as often). `max_stage_audits` bounds how long such a canary can hold.
- A long good stage dilutes later failures, because rollback counts the whole life: 40 agreeing audits at 10% followed by failures at 50% need 11 failures, where 2 would do on a fresh count. That is the cost of not paying a union bound over stages.
- Evidence-based promotion is slower (56 audits per stage instead of 20) and stays opt-in. With audit focusing the wait is shorter in requests.
- The live alert's text changes (`live v4: 0/3 audits agree; e-value 268 >= 100 ...`). Nothing parses it; RUNBOOK explains how to read it.
- A confidence sequence costs about 20 ms (a scan of 999 grid points); results are cached by counts, so `/ratchet/stats` recomputes only after a new audit.
- `/ratchet/stats` gains a key (`versions[].evidence`), as the stats contract allows.

## Alternatives

- **Keep Wilson with a larger `min_audits`.** Fewer looks lowers the inflation but does not remove it, and makes bad deploys serve longer.
- **Wilson with a Bonferroni or alpha-spending correction over looks.** Needs a planned number of looks; the gate's looks depend on traffic.
- **A plain SPRT against one alternative.** Fast when the alternative is right, slow or wrong when it is not; the mixture covers small and large drops at once (ADR 0005 named SPRT as the candidate; the mixture is its robust form).
- **Restart the rollback e-process per stage.** Restores power after a long good stage, but needs a union bound (α per stage, 2α in all) and state for each stage.
- **Bet adaptively (predictable plug-in e-processes).** Somewhat more powerful, but the e-value then depends on the order of audits, so it cannot be recomputed from two integers.
