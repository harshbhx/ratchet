# Ratchet evaluation audit: how model quality is measured and acted on

Audited read-only at /home/user/lanes2/main (HEAD 3c9d063). `pytest tests/test_release.py tests/test_stats.py` passes.
Monte Carlo scripts used for the numbers below are in this folder (`canary_mc.py`, `seq_mc.py`, `golden_mc.py`, `pertask.py`, `pertask2.py`).

## Summary (3 lines)

1. The statistics library is correct (exact binomial tails, Clopper-Pearson, Learn-then-Test, valid mixture e-values, confidence sequences), and the golden eval's even/odd held-out split is clean within a night.
2. The decisions built on it have gaps. Canary promotion runs on a point estimate after 20 audits, checked continuously, so a version below the floor reaches 100% about a third of the time. The signed certificate and the golden gate measure a global threshold the router does not serve when per-task calibration is on, which is the default.
3. The text-to-SQL results page describes execution-match scoring and breakdowns that `scripts/measure_adapter.py` does not implement. Everything else measured so far is agreement with the teacher, on a golden set that leans hard and repeats templates, plus simulation by construction.

## Findings, ranked

### HIGH-1: Canary promotion is a point estimate checked after every tick, so versions below the floor reach 100%

**Where.** `ratchet/release.py:891-931` (Wilson rule: advance when `prec >= live_floor` once `n >= min_audits`, default 20 at `:101`). `ratchet/release.py:965-972` (the sequential rule promotes on the same point estimate unless `promote_evidence=1`). The gateway ticks after every request (mock) or every 5 s (live, `gateway.py:763-787`).

**Problem.** Only rollback carries a guarantee. Promotion is "the first time the running point estimate is at or above 0.92 after 20 audits", repeated for each of three stages, and this peeking is never corrected. The docs give operating characteristics only for false rollback (`docs-site/.../release-gate.mdx:276-284`). They say "A plain point-estimate rule was not used" (`:255`), but that is exactly the rule promotion uses.

**Measured (Monte Carlo, floor 0.92, min_audits 20, one check per audit, three stages):**

| True precision | Default Wilson rule: reaches live | Sequential (the planned live-run profile): reaches live |
|---|---|---|
| 0.85 | 3% | 4% |
| 0.88 | 14% | 16% |
| 0.90 | 34% | 41% |
| 0.92 (at floor) | 64% | 79% |
| 0.95 | 93% (6.6% falsely rolled back) | 99.5% |

Once live, a version is never rolled back automatically. The live alert (e-process at alpha 0.01) needs a median of about 2,360 audits to flag a 0.90 version against 0.92: roughly 47,000 served answers at the 5% audit rate. The 0.88 and 0.85 cases need about 540 and 190 audits.

**Failure scenario.** A candidate passes the golden gate on about 50 held-out rows, but its real precision on live traffic is 0.90. It is promoted to 100% after about 65 audits, in one of three runs. Nothing then alerts within a hackathon-scale window, and SM-4 reports "0 bad deploys".

**Fix.**
- Make evidence-based promotion the default with the sequential rule (`promote_evidence=1`, 56 agreeing audits per stage), or promote only when an anytime-valid lower bound (the confidence sequence's low end) clears `live_floor - margin`.
- Publish the false-promotion operating characteristic next to the false-rollback one.
- At minimum, require the promotion check to pass on the stage's audit count at a single pre-planned look, not continuously.

### HIGH-2: The certificate, the golden gate's precision and coverage, and the paired vector use the global threshold, while the router serves per-task thresholds

**Where.**
- `ratchet/nightschool/evaluate.py:344-345` and `:401-418`: `precision`, `coverage`, `certificate`, `frontier` and `test_vector` are all computed at the global `thr`.
- `evaluate.py:159-169`: a task with at least `min_task_n` (20) calibration rows gets its *own* threshold, which can be lower than the global one.
- `ratchet/calibration.py` `threshold()`: the router serves at the per-task record's threshold.
- `ratchet/release.py:377`: `admit` checks the global `precision` and `coverage`.
- `RATCHET_PER_TASK_CALIBRATION` defaults on.

**Problem.** Per-task calibration (ADR 0040) landed after the certificate (ADR 0026). The certificate was never moved to the deployed policy. The docstring says the certificate is "at ... the one stored and served at", and the README says "the answers this version serves at its threshold". Neither holds for a multi-task adapter. The paired McNemar gate likewise compares two policies that are not served.

**Demonstrated** (`pertask2.py`, seed 3, two tasks with different confidence scales):

| | Served | Errors | Precision | CP lower bound |
|---|---|---|---|---|
| Signed certificate (global threshold 0.96) | 104 | 0 | 1.000 | 0.972, certified |
| Same held-out rows at the per-task thresholds the router uses | 189 | 8 | 0.958 | 0.925, not certifiable at 95% |

The golden gate passed on the global 1.000.

**Failure scenario.** A BOM is signed "certified >= 95%" (and the admission event says so) while the adapter serves an answer set whose bound is 92.5%. `python -m ratchet.stats check` recomputes and confirms the wrong certificate, because its counts are internally consistent.

**Fix.** In `cascade_eval`, compute the outcome of each held-out row at its task's record threshold (after `escalate_when`). Build `precision`, `coverage`, `certificate`, `frontier` (or label it "global threshold only"), `per_task` and `test_vector` from those outcomes. Alternatively, certify the union with a Bonferroni split across tasks. Update the README sentence.

### HIGH-3: The text-to-SQL results page describes scoring that `measure_adapter.py` does not do

**Where.** `docs-site/src/content/docs/guides/results.mdx` makes these claims:
- "Match with the teacher is execution match: the student's query and the teacher's query return the same rows".
- There are breakdowns "by difficulty and by industry".
- `verify` needs "the exam databases".

`docs-site/src/components/SqlResults.astro` requires `mode === "sql"` and `breakdown_answering_every_row`, and `proof.mdx:35` calls this "the one check that has been run on a real model". In fact, `scripts/measure_adapter.py:30` and `:58-63` compare the ticket-triage fields `category` and `priority`, else whitespace-normalised text. No database is read, no `mode` or breakdown is written, and no remote branch has an SQL mode.

On top of that, the script's confidence is not production's (`:98-120`, `:149-156`):
- greedy decoding with one sample (production: three samples at temperature 0.7);
- an arithmetic mean of token probabilities (production: `exp(mean logprob)` weighted 0.4 with agreement 0.6);
- 4-bit by default.

So its coverage and precision describe a different gate.

**Failure scenario.** The published file shows `adapter_agreement` as exact SQL-text equality, which undercounts every equivalent query, while the page labels it execution match. Otherwise the page stays "pending" until someone adds `mode` and breakdowns by hand, which breaks "no number typed by hand".

**Fix.**
- Score with `ratchet/sqlcheck.Sandbox` (execution match with the same normalisation as the gateway's `sql` comparator).
- Write `mode: "sql"` and the difficulty and domain breakdowns.
- Reproduce production confidence, or label coverage and precision as "single greedy sample, mean token probability".
- Add the student's spend on escalated rows to the savings estimate (see LOW-6).

### MEDIUM-1: Shadow "precision" is mostly agreement with the live student

**Where.** `ratchet/router.py:1481-1496` grades the shadow against the served student answer whenever the request has no teacher answer, and arbitrates only *disagreements*. `ledger.SHADOW_QUALITY_SQL` averages the result as `prec`. `release.py:847-871` promotes when it is at least `min_precision` (0.95) on 100 answers.

**Problem.** At 95% student share and a 5% audit rate, about 90% of would-serve shadow rows have no teacher reference. A candidate that repeats the live student's mistakes scores 1.0 on them. Errors are likely correlated, since both models share a base and most training data.

**Failure scenario.** The live student is confidently wrong on a template slice, and the candidate inherits the error. The shadow reads about 0.99 and promotes. The canary's 20 audits then have to catch it, which runs into HIGH-1.

**Fix.** Report the metric as "agreement with live". Compute shadow precision only on teacher-referenced rows (escalations and audits, weighted by `1/label_p`), or arbitrate a random sample of agreements too.

### MEDIUM-2: The golden set is selected by the live model's own escalations, which biases champion versus challenger and coverage

**Where.**
- Golden rows are stored teacher answers to escalations, audits and arbitrations; the split is 10% by hash (`ledger._split`, `nightschool/dataset.py:102-190`).
- Frequent prompts leave golden (`dataset.py:139-145`).
- Champion re-score: `orchestrate.py:240-243`. Gate: `release.py:399-405`, plus the paired `p_cov`.
- `certificate.ASSUMPTIONS` admits the rows "over-represent requests the student found hard".

**Problem.** Escalated rows were chosen *because the live version's confidence was low*. Re-scoring live on them shows regression to the mean, which depresses live's coverage relative to any independent candidate, so the "no regression vs live" check and McNemar `p_cov` lean toward the challenger. Coverage and precision on golden also describe the tail rather than the traffic mix, because the head (FREQUENT prompts) is moved to training.

**Failure scenario.** A candidate that is no better than live clears the regression check on the strength of the selection artefact alone. The reported coverage under-states (or the precision misstates) production.

**Fix.** `label_p` is already logged per request. Store it with each example and weight golden rows by `1/label_p` (audit rows by 1/rate, escalations by 1). Alternatively, keep an audit-only golden subset (a uniform sample of served traffic) for the certificate and the champion comparison.

### MEDIUM-3: Template workloads leak across the train and golden split

**Where.** `sim/tickets.py` has about 20 templates with slot fills. `ledger._split` hashes the exact messages, so filled variants of the same template sit in both halves. The live-run harness uses the same streams (`liverun.make_workload`) and reports "template-label accuracy" (`liverun.py:460-512`).

**Problem.** For a real model, golden precision and live-run accuracy measure the template-to-label mapping, i.e. memorisation, not generalisation. The caveats say the labels are the template author's, but not that every golden row has training siblings from the same template.

**Fix.** For synthetic workloads, split by template id: hold out whole templates, as the text-to-SQL exam holds out whole databases. Report held-out-template accuracy separately from in-template accuracy.

### MEDIUM-4: SM-4 ("bad deploys reaching 100%") can only count what the gate itself detects

**Where.** `ratchet/kpis.py:169-210`: a version counts as "bad" only through an `alert` or `rollback` event.

**Problem.** Live versions are never rolled back automatically. The alert needs thousands of audits for a moderate drop (HIGH-1), so SM-4 = 0 is met by non-detection. In simulation the ground truth is known but unused, and the corrupted deploy drops precision to about 41%, which is trivially caught.

**Fix.**
- In the simulator, define "bad" against mock truth: true served precision below the floor.
- In live mode, report each live version's confidence-sequence upper bound and the smallest drop detectable in the window, rather than a bare count.

### MEDIUM-5: Pooled audit precision mixes sampling rates

**Where.**
- `liverun.window_stats` (`liverun.py:789-846`) and `ledger_values` (`:2684-2696`) pool canary audits (rate 0.5 under the run profile) with live audits (0.05) unweighted. The pooled number is published as `live.final.audited_precision_pct` and `..._lcb_pct`; it says "pooled" but carries a CP bound.
- Per-task `policy.audit_rate` (`router.py:1973-1978`, `:2434`) gives the version-level live alert and SM-2 a mix of rates.

**Problem.** A canary audited ten times as often dominates the pooled number. The CP bound then covers an audit-weighted mixture, not what clients received.

**Fix.** Use a Hajek estimate with `1/label_p` weights (already logged) and Kish n_eff, as the replay does, or report each version separately.

### LOW

1. **Noisy default golden gate.** Unpaired point estimates within 0.02 on about 50-75 held-out rows (`release.py:399-405`). Monte Carlo (`golden_mc.py`): a model with true served precision 0.969 passes the golden gate 46% of the time at golden n = 100, 68% at 300 and 89% at 1,000. The live-run plan's own output says round 1 is short of a certifiable size (1,686 stored answers needed, 1,500 sent). The paired gate and LTT are opt-in.
2. **Small tasks skip their own gate.** A task with fewer than 10 held-out rows is never checked on its own (`release.py:545`). It serves at `max(global, own)`, where `own` comes from a handful of calibration rows, and the global threshold was calibrated on other tasks' confidence scale. Fix: SERVE_NOTHING below N held-out rows, or require a per-task CP bound.
3. **Halves follow row position, not a hash.** `evaluate.py:319` alternates by position over the scored rows. A row changes half when an earlier row is excluded (frequent, contested, purged, pinned). A periodic row order can put a whole task in one half: with tasks alternating, one task landed entirely in calibration and the other entirely in test, giving SERVE_NOTHING and precision 0. Fix: split on a hash of the example key.
4. **The live re-score can include the live version's own training rows.** Same prompt under another task, via `golden_anywhere` (`dataset.py:126-145`). `rescore` does not filter by the version's committed train leaves, so the "current" certificate can be contaminated. Fix: drop golden keys found in `version_leaves(live, 'train')`.
5. **Invalid or failed teacher audits are dropped** (`router.py:1428-1430`, INV-15/16). If those cluster on hard requests, audited precision is biased upward. Count them and report them next to the precision.
6. **`measure_adapter.py` savings are overstated.** They leave out the student's spend on escalated rows and the three samples, and price the teacher on the adapter's token counts (no reasoning tokens).
7. **SM-1 uses the formula baseline.** In `/ratchet/kpis`, student-served rows are priced with the token formula, not a measured cost. Only the live-run control arm, and receipts with enough audits, correct it.
8. **Stale README lines.** "Per task ... does not change routing": per-task calibration now does. "serves at its threshold": see HIGH-2.

## How the simulation can flatter results (by construction, labelled as simulation)

- **The teacher is right by construction.** The mock teacher is always correct unless noise is injected, so "agreement with the teacher" equals correctness in simulation and nowhere else.
- **Confidence is separated by construction.** A known request gets token probability 0.80-0.97 and agreeing samples. An unknown one gets random labels (low agreement) 85% of the time and is "confidently wrong" only 15% of the time (`sim/mock_models.py:246-268`). Calibration works because the mock is built that way.
- **Skill is learned per template** (`skill()`): golden rows from the same template inherit the training effect (see MEDIUM-3).
- **The corrupted deploy is extreme.** Every fragile item becomes confidently wrong, giving about 41% audited precision, which a 17-audit Wilson rule catches. No scenario in the 0.85-0.91 band, where the gate is weak (HIGH-1), is simulated.
- **The golden gate cannot catch the corruption.** The golden eval always grades the clean staging copy, so the scenario shows rollback by design.

## Verified sound

- **`ratchet/stats.py`:**
  - The binomial tails, `cp_lower` and `cp_upper` (including the `s == n` closed form), `required_rows` and the one-sided McNemar test are correct.
  - The LTT fixed-sequence test uses a data-independent grid and stops at the first non-rejection, so the family-wise error stays controlled.
  - The mixture e-value's supermartingale argument is correct: the alternatives lie strictly on the H1 side, and the weights are fixed and sum to 1.
  - The confidence sequence splits alpha/2 per side.
- **`cascade_eval`:**
  - The threshold is chosen on the even half only and measured on the odd half, and the per-task thresholds also come from calibration rows only.
  - Refused rows count as escalations, and invalid samples get no vote.
  - The threshold is stored exactly, without rounding, and ties in `calibrate_threshold` are handled.
- **Certificate arithmetic:**
  - `certificate`, `frontier` (Bonferroni over a fixed grid) and `paired` are correct, and `recheck` recomputes every number from the counts.
  - The certificate states its assumptions honestly: agreement with the teacher, not correctness, and a golden set that leans hard.
- **Audits:**
  - Audits are one uniform draw per served answer.
  - Only the version's own adapter's audits count (`OWN_STUDENT`), and pending off-path audits are excluded from both counts.
- **Sequential rollback and the live alert:**
  - Sequential rollback runs on the whole-life e-process, never restarted per stage.
  - The live alert's e-process is valid under continuous monitoring, and every decision can be recomputed from two integers.
- **INV-4:** the champion is re-scored on the same golden rows the same night, so the paired vectors line up.
- **Replay:** IPW/Hajek with a positivity check. Missing labels are treated as missing at random, and its intervals are labelled approximate (Wilson at Kish n_eff).
- **Live-run control arm:**
  - The control sample is a seeded Bernoulli draw by index, independent of routing, and the ratio estimator for the measured baseline is sound.
  - Live-run caveats are explicit: template labels, a single run, list prices.

## What the evaluation honestly proves today

Ratchet's statistics code is careful and correct. On a given night, the golden eval gives an unbiased held-out estimate, and an exact lower bound, of how often answers at *the global threshold* agree with Nemotron 3 Ultra on a golden set. That golden set is built from escalations and audits and drawn from the same templates the model trained on.

It does not yet prove three things. That the answers actually served, which use per-task thresholds, meet the certified bound. That this agreement equals correctness. Or that it carries over to the head of real traffic or to new phrasings. The release gate reliably stops grossly broken versions (the 41%-precision corrupted deploy is rolled back in 7-17 audits). It does not reliably stop versions a few points below the 0.92 floor: they reach 100% a third of the time, and the live alert may not notice for tens of thousands of answers. The one real-model measurement (text-to-SQL) is still pending, and its script does not yet compute the execution-match metric the docs promise. All loop-level numbers (savings, share, rollbacks) are simulation, where the teacher is correct and confidence is calibrated by construction.
