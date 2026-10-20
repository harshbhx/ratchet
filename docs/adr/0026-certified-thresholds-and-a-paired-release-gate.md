# ADR 0026: Certified thresholds and a paired release gate

- Status: Accepted
- Date: 2026-10-20

## Context

Every version's golden eval reports one precision: the agreement with the teacher of the answers it would serve on the held-out half of the golden set (49 to 134 rows in the seed-7 reference run). A point estimate on that many rows cannot tell 0.96 from 0.99. In the reference run the one-sided 95% Clopper–Pearson lower bounds of the versions' training evals range from 0.875 (46 of 48 served answers agree) to 0.964 (129 of 130), while the reported precisions are 95.8% to 99.2% (simulation, mock models).

The golden gate compares unpaired point estimates within ±0.02 (FR-6.1). In the reference run v2 was rejected for coverage 70/74 against the live v1's 74/74: on the same 74 rows that is 4 rows only v1 serves and none only v2 serves, and the exact one-sided McNemar p-value for that difference is 0.0625, not significant at 5%. The two evals are paired (INV-4: the live version is re-scored on the same golden rows the same night), but the gate throws the pairing away.

The statistics were built in [ADR 0024](0024-evidence-foundations.md) (`stats.cp_lower`, `precision_pvalue`, `required_rows`, `mcnemar_one_sided`, `ltt_fixed_sequence`, `CERT_GRID`); this item uses them.

## Decision

### A certificate in every eval, and so in every BOM

`cascade_eval` chooses the threshold on the even rows and measures on the odd rows (INV-6). The odd rows play no part in choosing the threshold, so, given the threshold, the agreement count of the odd rows it would serve is one binomial experiment with the version's precision at that threshold as its rate. One exact test, with no correction for the choice:

- `certificate`: `{method: 'clopper-pearson', calibration, delta, target, threshold, served, errors, precision, lcb, certified}`. `lcb = cp_lower(served − errors, served, delta)`; with probability at least 1 − delta the precision of what the version serves at that threshold is at least `lcb`; `certified` is `lcb ≥ target`. Defaults: target 0.95 (`RATCHET_CERT_TARGET`), delta 0.05 (`RATCHET_CERT_DELTA`).
- `frontier`: the same counts at every threshold of the fixed grid `CERT_GRID` (0.95, 0.90, …, 0.50), each bound at delta / 10. By Bonferroni all ten rows hold at once, so an operator may read off any threshold afterwards and keep the guarantee: a certified risk-coverage curve.
- `per_task`: the certificate's counts per task, each at delta on its own. A diagnostic, not corrected for the number of tasks, that exposes a weak task inside a pooled number (the case per-task calibration proposals start from) without changing routing.
- `test_vector`: one letter per odd row in golden order (`S` served and right, `W` served and wrong, `E` escalated), the input of the paired comparison.

Night School stores the eval before it signs the BOM, so all of this is in the adapter's signed `manifest.eval` (INV-14). `python -m ratchet.stats check bom.json` verifies the signature and recomputes every bound, verdict, frontier row, per-task row and paired p-value from the signed counts, and the counts from the test vector; it exits 1 on any difference above 1e-9. `GET /ratchet/versions/{id}/certificate` serves the signed certificate (`training`), the latest one (`current`: the nightly champion re-score overwrites a live version's eval), the release gate's evidence from the version's audits, and the assumptions in plain words.

The stored threshold is now the exact float the eval measured at. Before, it was rounded to 4 decimals, which can round up past the confidence it was calibrated at: production then served less than the eval measured, and nothing at all when every row had one confidence (a constant-logprob endpoint, `test_stored_threshold_serves_what_the_eval_measured`). With the exact value the certificate, the eval's `coverage` and `precision`, and what the router serves are about the same threshold. No reference run moved.

### Learn-then-Test calibration (opt-in)

`RATCHET_CALIBRATION=ltt` chooses the threshold by fixed-sequence testing over `CERT_GRID` on the calibration rows: from 0.95 down, test H0 "precision at or above this threshold ≤ target" with the exact binomial p-value, keep going while it is rejected at delta and stop at the first that is not; serve at the lowest rejected threshold, or serve nothing (1.01) when the first already fails. Fixed-sequence testing controls the family-wise error at delta with no correction, so the chosen threshold has precision above the target with probability at least 1 − delta, and the held-out certificate then checks it independently.

**The grid must be fixed in advance.** A grid built from the observed confidences puts one point in each group at the top, the first test runs on one row, cannot reject, and the sequence certifies nothing; and a grid chosen after looking at the data breaks the guarantee. This is the flaw of the task-sliced gate proposals that tested the observed confidences.

The point rule stays the default, because Learn-then-Test is more conservative early in a deployment: with few calibration rows it serves nothing (ten calibration rows cannot certify 95%, since 0.95^10 = 0.60 > 0.05). In the seed-7 reference configuration it serves nothing from v1 and v3 and saves 55.4% instead of 69.2% (simulation, one seed).

### Why conformal risk control does not apply

Conformal risk control gives distribution-free guarantees for a loss that is monotone in the threshold. Selective precision is not: raising the threshold removes answers, right or wrong, so precision can fall as the threshold rises. One confidently wrong answer at 0.97 and ten right ones at 0.91 give precision 0 at 0.95 and 10/11 at 0.90; the mock student's confidently wrong samples (`sim/mock_models.py`) are exactly this case. Learn-then-Test needs no monotonicity: it tests each threshold, and the fixed order is what spends the error budget once.

### The paired gate (opt-in)

`run_night` compares the candidate's test vector with the re-scored live version's (same golden rows, same order, the same night) and stores `paired` (with `against`, the live id) in the candidate's eval before the BOM is signed:

- coverage: `cov_only_live` (b) and `cov_only_cand` (c), `p_cov` = McNemar's exact one-sided p-value P(X ≥ b), X ~ Binomial(b + c, 1/2), for "the candidate serves less";
- wrong answers as a client sees them (an escalated row gets the teacher's answer, right by definition): `neg_flips` (the candidate serves a wrong answer where live served a right one or escalated) and `pos_flips` (live served a wrong answer and the candidate does not), `p_err` likewise; (neg − pos) / n is the change in wrong answers per request;
- the negative flip rate: answers live served right that the candidate serves wrong, over the answers live served right, with its 95% Wilson interval.

With `RATCHET_PAIRED_GATE=1` the golden gate's regression check reads this instead of the two point estimates. It rejects only a regression that is both large and significant: more than `tolerance` of the rows lost from coverage with `p_cov < RATCHET_REGRESSION_ALPHA`; more than `tolerance` of the rows newly answered wrong with `p_err` below it; or, with `RATCHET_MAX_NFR` < 1, a negative flip rate whose 95% lower bound exceeds it. The gate recomputes the p-values from the counts. Without a paired record against the current live version (an evaluator that writes no vectors, vectors of different lengths) it falls back to the unpaired rule. The shadow event appends `serves +c−b of n vs live vL (McNemar p=…); nf negative flips, pf positive flips`.

`RATCHET_MIN_CERTIFIED_PRECISION` (opt-in) rejects a candidate whose certified lower bound, recomputed from its certificate's counts, is below the floor, or that has no certificate. The shadow and cold-start canary events of a certified version append `certified >= 95% (lcb 96.4%, delta 0.05)`.

In the seed-7 reference configuration the paired gate admits v2 (`serves -4 of 74 vs live v1 (McNemar p=0.06)`), and the run then takes another path: the corrupted v3 is rejected in shadow (shadow precision 91.7%) instead of reaching the canary, so no answer of it is served (145 under the default gate), and the run saves 69.6% against 69.2%. With `RATCHET_MIN_CERTIFIED_PRECISION=0.9`, v1 is rejected on night 1 (lower bound 87.5%) and the run saves 61.6%. One seed each; simulation, mock models.

**Why the error flips count escalated rows.** McNemar's test is exact for a binary outcome per row. Taking "the client got a wrong answer" (`W`) against everything else makes the test exact and its effect size meaningful. Counting only live's served-right rows as possible negative flips (the specification's first form) would leave rows live escalated and the candidate now serves wrong out of the test, which makes it conservative and its (neg − pos) / n no particular quantity. The flip rate keeps the narrower, classic definition (Yan et al., positive-congruent training): of the answers live served right, how many the candidate gets wrong.

### Sample sizes

`python -m ratchet.stats plan --target T --delta D` prints the served held-out rows a target needs with 0, 1, 2 or 3 wrong answers (`stats.required_rows`):

| Target (delta 0.05) | 0 errors | 1 | 2 | 3 |
|---|---|---|---|---|
| 0.90 | 29 | 46 | 61 | 76 |
| 0.95 | 59 | 93 | 124 | 153 |
| 0.97 | 99 | 157 | 208 | 257 |

Only the held-out half counts and only served rows count, so certifying 95% with one error takes about 2 × 93 / coverage golden rows. A 10% golden fraction reaches that after about 2,000 stored teacher answers at 90% coverage.

### What a certificate assumes

Served with every certificate (`certificate.ASSUMPTIONS`):

1. Golden rows are exchangeable with live traffic. They are not quite: they are teacher answers to escalations, audits and arbitrations, so they over-represent requests the student found hard. The bias is in the conservative direction only insofar as hard requests are where the student errs.
2. Precision means agreement with the teacher under the task's comparators, not correctness.
3. The golden split is a hash of the request, which acts like a coin flip: it does not look at difficulty or at any answer (INV-3).

The guarantee is about the population the golden rows are drawn from, at the time they were drawn. Drift (FR-16.6) breaks exchangeability; the endpoint's `suspended` status is reserved for an open drift alarm.

## Consequences

- Every eval and BOM gains `certificate`, `frontier`, `per_task` and `test_vector` (about 2 KB at today's golden sizes; the per-task block grows with the number of tasks). `Ledger.versions(states)` now filters in SQL, so the request path parses only the live and canary evals instead of every version's: the reference simulation runs as fast as before (with the larger evals it had slowed from about 13 s to 22 s on the development machine).
- Defaults keep v0.4's decisions: point calibration, no certified floor, no paired gate. The certificate code draws no random number; the reference simulation and the three reference workloads are unchanged (`test_certificates_move_no_simulation_number` checks the stats with the certificate code taken out).
- The stored threshold is exact, so `/ratchet/stats` and the BOM show thresholds such as 0.9804917698 where they showed 0.9805. The dashboard rounds them for display.
- `/ratchet/stats` gains no key (the certificate rides inside `versions[].eval`).
- Learn-then-Test and a certified floor slow the first deployments down; the planner says by how much.

## Alternatives

- **Wilson or normal intervals for the certificate.** Approximate: their coverage can fall below the stated level at small counts and extreme rates, which is where certificates live (59 of 59 is the smallest 95% certificate). Clopper–Pearson is exact, and a signed certificate must be re-checkable to the digit.
- **Calibrate and certify on the same rows.** The threshold is chosen to look good on its rows, so a bound on them is biased upward; the held-out half avoids any correction.
- **A Bonferroni frontier over the observed confidences.** The number of thresholds would depend on the data, and the grid would be chosen after looking.
- **Per-task thresholds now.** Per-task certificates need about 60 to 100 held-out rows per task; the per-task block shows when a task has them, and per-task calibration (a planned item) can build on it.
- **Bootstrap paired intervals.** Approximate and random; McNemar is exact, deterministic and recomputable from four counts.
