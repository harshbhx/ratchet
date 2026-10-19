# ADR 0004: Retrain from the base model on the cumulative dataset every night

- **Status:** Accepted
- **Date:** 2026-10-19

## Context

Training on each night's new examples on top of yesterday's adapter risks catastrophic forgetting. LoRA forgets less than full fine-tuning but also learns less (Biderman et al. 2024), and replay of earlier data is the standard mitigation (Ibrahim et al. 2024). Training on a model's own outputs causes collapse (Shumailov et al. 2023).

## Decision

- Each night `run_night` trains a **new** adapter from the **base model** on **all** train rows so far (`dataset.rows(ledger, "train")`). Replay is built in.
- Training targets are **only teacher answers** (escalations, audits, arbitrations, cold start). Student answers are never stored as examples.
- Training is skipped when fewer than `min_new_examples=50` new rows exist, when there is no golden set, or while a release is in flight.

## Consequences

- Positive: no compounding drift across adapters; any version can be reproduced from its `dataset_hash`.
- Positive: simple to reason about and to audit (one adapter = one dataset snapshot).
- Negative: training time grows with the dataset. At hackathon scale (a few thousand rows) it is minutes. Later, cap the set with deduplication, stratified sampling, or recency-weighted replay.
- Negative: the dataset only grows from requests the student could not answer or that were audited, so it over-represents hard cases. That is acceptable, because the golden split and calibration measure on the same distribution.

## Alternatives considered

- **Continual training of one adapter:** cheaper, but has forgetting risk and a harder rollback story.
- **Training on student answers the teacher agreed with:** free data, but it is self-training and risks collapse. Rejected.
