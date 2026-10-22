# ADR 0007: Hash-based golden split; frequent requests always train

- **Status:** Accepted
- **Date:** 2026-10-19
- **Amended:** [ADR 0131](0131-golden-prompts-are-golden-in-every-task.md) groups a prompt across tasks: golden under one task, golden under all; occurrences add up across tasks.

## Context

Ratchet needs held-out data to evaluate each candidate, with no leakage into training and a stable membership over time. Identical requests are deduplicated (`examples.hash` is unique; `seen` counts repeats).

Simulation showed two problems:

1. Requests that repeat often but were assigned to the golden split were **never** trained on. The student kept escalating the most common questions.
2. Because of deduplication, the golden set over-represented rare phrasings. Calibrating and measuring on the same rows reported 94% coverage where real traffic got 80%.

## Decision

- **Split by hash:** an example is `golden` if `int(sha256[:8], 16) / 0xFFFFFFFF < golden_fraction` (default 0.10), else `train`. It is fixed forever.
- **Frequent requests train:** an example with `seen ≥ FREQUENT` (3) is treated as `train` and leaves the golden set (`dataset.rows`).
- **Calibrate on half, measure on the other half:** in `cascade_eval`, even-indexed golden rows calibrate the threshold and odd-indexed rows give the reported precision and coverage.
- **Champion vs challenger:** the live version is re-evaluated on the current golden set before a candidate is compared with it.

## Consequences

- Positive: no leakage from re-splitting, common requests get learned, and the reported numbers are not graded on calibration data.
- Positive: the golden set grows with traffic and drifts with it, since it is drawn from the same teacher-labelled stream.
- Negative: the golden set is drawn from teacher-labelled traffic, which over-represents escalations (hard cases). Its coverage is a conservative estimate of live coverage.
- Negative: halving the golden set doubles the noise. With about 200 golden rows, precision is only known to a few points. Shadow and canary exist partly for this reason.

## Amendment (2026-10-21): retries are not repeats

`seen` must count how often traffic asks a request, not how often one client retried it: three retries of one escalated request used to reach `FREQUENT` and move a golden example into training. Since [ADR 0071](0071-idempotency-keys-and-retry-de-duplication.md) a request whose `x-stainless-retry-count` is above 0 never adds to a duplicate's `seen` (`Ledger.add_example(count_seen=False)`), and the same API client's repeat of a stored example within `RATCHET_SEEN_DEDUP_S` (300 s in live mode, off in mock mode) counts once. A repeat from another client, or minutes later, still counts, and internal traffic (Scout, the simulator, the warm-up) counts every repeat, so the rule above and every simulation number are unchanged. With an `Idempotency-Key` a retry does not reach the cascade at all.

## Alternatives considered

- **Time-based holdout** (yesterday's data): tracks drift better, but frequent requests leak between train and test.
- **Weighting golden rows by `seen`:** tried; it biased the set toward never-trainable frequent rows, and was replaced by the frequent-request rule.
- **A hand-curated golden set:** the gold standard for regulated customers; planned as an optional addition.
