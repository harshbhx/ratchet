# ADR 0005: Release through shadow and canary, roll back on Wilson upper bound

- **Status:** Accepted
- **Date:** 2026-10-19

## Context

A student that passes offline evaluation can still fail in production, from drift, a wrong adapter file, or serving and training mismatches. No competitor page we read documents shadow mode, canary rollout and automatic rollback together. Audits are expensive (each is a teacher call), so decisions must work with tens of audits, not thousands.

At n=20 audits, a healthy canary at 96% true precision has about a 19% chance of showing a point estimate below 92% (2 or more errors), so a point-estimate rule would cause frequent false rollbacks.

## Decision

The full state machine is in [ARCHITECTURE.md §5](../ARCHITECTURE.md#5-version-state-machine). In short:

- **Golden gate (`admit`):** precision ≥ 0.95, coverage ≥ 0.25, and no more than 0.02 worse than live on either. Live is re-scored on the same golden set the same night.
- **Shadow:** the candidate runs the full cascade silently on real traffic. After ≥ 100 would-serve answers, promote to canary 10% if precision ≥ 0.95 and coverage ≥ live coverage − 0.02. Disagreements with the live student are arbitrated by the teacher.
- **Canary 10% → 50% → live:** each step needs ≥ 20 audits *in the current stage* (`stage_since`) with audited precision ≥ 0.92.
- **Rollback:** when the 95% Wilson **upper** bound of audited precision is below 0.92, the version is set to `rolled_back` and traffic returns to the previous live version (or the teacher on cold start).
- **Hold:** with a point estimate below 0.92 but an upper bound above it, wait for more audits.
- **Cold start:** with no live version, a candidate that passes the golden gate goes straight to canary 10%.
- **Live watch:** an `alert` event when the live version's upper bound falls below the floor (no automatic action on live).

## Consequences

- Positive: rollbacks happen on strong evidence. The simulation rolled back a corrupted deploy at 41% audited precision within the same day, with no false rollbacks across seeds.
- Positive: shadow adds a real-traffic check before any user sees the candidate.
- Negative: promotions are slow at low traffic. At a 5% audit rate and 10% canary, 20 audits take about 4,000+ requests.
- Negative: shadow and audits cost money (extra student samples, arbitration and audit teacher calls).
- Negative: live versions are never rolled back automatically, only alerted on.

## Alternatives considered

- **Point-estimate thresholds:** too many false rollbacks at small n.
- **Sequential probability ratio tests:** better sample efficiency; a candidate for v0.2.
- **Fixed-time canaries** (e.g. 24 h): ignore how much evidence has accumulated.
