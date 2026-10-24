# ADR 0013: Optional teacher calls fail open for the request and count against the shadow in the release gate

- Status: Accepted
- **Update:** the off-path retry was later built in [ADR 0072](0072-off-path-audits-and-shadow.md).
- Date: 2026-10-20

## Context

`Ratchet.handle` makes up to three teacher calls a request does not need. With probability `audit_rate` the teacher audits an answer the student already served. While a version is in shadow, the teacher arbitrates when the shadow would serve an answer that disagrees with the served one. And the shadow itself is sampled on every request.

Up to now, a failed shadow sample was skipped silently, but a failed audit or arbitration raised out of `handle`. The gateway turned that into a 502 although the student had a confident answer ready: with `audit_rate=1` and a teacher timing out, every request failed. The runbook listed a fix as planned since v0.3. A failed call also left no trace beyond the 502: no counter and no event.

Two questions had to be settled with the fix. What the request gets, which is easy: the student's answer. And what the release gate sees, which is not, because the gate decides promotions from these calls:

- Audits are sampled at random. Dropping a failed one shrinks the sample without changing what it measures.
- Arbitration runs only when the shadow disagrees with the served answer. Dropping those rows removes exactly the cases where the shadow may be wrong, so during a teacher outage a candidate that disagrees often would look better the more it disagreed. Before this change that bias already existed in a different form: the failing request raised before it was logged, so its row never reached `shadow_quality` either.

## Decision

- **Fail open for the request.** The audit, the shadow's samples and the arbitration are each wrapped. On a failure the student's answer is served, nothing is billed for the failed call, and the request is logged. The teacher call on the escalation, cold-start, pinned and teacher-only paths still raises, and the gateway still answers 502 (INV-7): there is no answer to serve.
- **Count and log every failure, without leaking.** `student_errors`, `audit_errors` and `shadow_errors` are per-process counters, updated under a lock because Scout calls `handle` from several threads, and returned by `Ratchet.health()` for the planned `/readyz` and `/metrics`. Each kind writes an event (`audit_error`, `shadow_error` with `sampling` or `arbitration`) on its 1st, 10th, 100th and 1000th failure, like `student_error`. The detail names the task or version, the exception class and, for an HTTP error, the status; never the message, because an httpx message carries the endpoint URL and other messages can carry request text.
- **A failed audit is not an audit.** The row gets `audited=0` and `audit_agree` NULL, so canary promotion and rollback (`version_live_quality`) just see fewer audits.
- **A failed arbitration is a disagreement for the gate.** The row keeps what is known (`shadow_version`, `shadow_serve=1`) and leaves what is not (`shadow_agree` NULL). `Ledger.shadow_quality` counts it in `n`, `served` (the `min_shadow` evidence) and coverage, and as 0 in precision, and reports the count as `unarbitrated`; the gate's event says how many were counted that way. Precision is therefore a lower bound when the teacher was flaky, and exactly the old number when it was not.
- **Don't wait on the same outage twice.** If the audit failed, the arbitration for the same request is not attempted; the disagreement is recorded as unarbitrated.
- **A row without a shadow verdict counts toward nothing.** `shadow_quality` filters `shadow_serve IS NOT NULL`, so a later off-path design can log a pending row first and fill the verdict in afterwards.

## Consequences

- Invariant 15: optional teacher calls never fail a request the student already answered. PRD FR-2.10.
- A teacher outage now fails only the requests that need the teacher's answer. Audited requests still wait for the teacher's timeout before the student's answer is served; `RATCHET_AUDIT_RATE=0` removes that wait during a long outage (RUNBOOK).
- A shadow running through a long teacher outage collects unarbitrated disagreements and is likely to be rejected, with the count in the reason. That loses one night's candidate, which the next night replaces (ADR 0004). It never promotes a candidate on missing evidence.
- With no failures nothing changes: the pinned reference simulation is identical.
- Counters reset with the process; the events are the durable record.

## Alternatives considered

- **Leave unarbitrated rows out of every number (`shadow_serve` and `shadow_agree` NULL).** Simple, and it stops a failure from diluting coverage, but it keeps the selection bias above: the candidate's precision is computed only on the disagreements the teacher happened to settle, plus every agreement.
- **Hold the shadow while unarbitrated rows could change the decision** (promote only if the worst case passes, reject only if the best case fails). The most precise rule, but a long outage can leave a shadow stuck for days, and a shadow in flight blocks Night School (INV-5). Rejecting on the worst case frees the pipeline and errs on the safe side.
- **Retry the arbitration later, off the request path.** Better for latency and evidence, and planned with the off-path quality-control sidecar; it needs a job queue and request updates. This decision is the failure semantics that sidecar keeps.
