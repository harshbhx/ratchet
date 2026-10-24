# ADR 0083: Success metrics computed from the ledger, the same way in mock and live mode

- Status: Accepted
- Date: 2026-10-21

## Context

PRD §8.1 lists five success metrics (SM-1 to SM-5) and NFR-1 sets a latency target, but nothing computed them. Every row was `Not checked`, and the tests used weaker bars than the targets: savings above 60% on the last day (target 70%) and every day's audit agreement at least 85% (target 95%). The PRD's simulation paragraph said drift recovery took from day 5 to day 9, which is 4 nights against a target of 3, without any code deciding what an episode is. A judge or a customer asking "are you meeting your targets?" got a hand-read table.

The metrics must mean the same thing in the simulator, the mock demo and a live gateway, or a simulation result says nothing about production. They must also keep working on a ledger whose old request rows retention folded into daily sums (ADR 0081), and on the ledger other v0.5 items extend (`requests.wall_ms`, the `failures` table).

## Decision

`ratchet/kpis.py` computes every metric from the ledger with pure functions over plain rows (ledger days, audit counts, release events), and `report(ledger, window_days, min_audits, mode)` reads those rows and assembles them. The gateway serves it at `GET /ratchet/kpis` (access level `dashboard`, like `/ratchet/stats`; `?days=` picks another window) and as a `kpis` block in `/ratchet/stats` (an added key; the dashboard does not read it yet). The simulator puts it in its JSON and prints it labelled as simulation. Nothing in it writes.

Each metric is `{name, value, target, op, unit, met, detail}`. `met` has three values: true, false, and `null` when there is not enough data to say (no traffic in the window, too few audits, too few days to see drift, a drift episode still open within its target). A `null` is never reported as a pass. `detail` holds the counts behind the number, so a reader can recompute it.

**The window.** SM-1 to SM-3 and NFR-1 describe the steady state, so they cover the last `RATCHET_KPI_WINDOW_DAYS` ledger days with traffic (default 3). A day without requests takes no place in the window, so a quiet weekend does not push the working days out. SM-4 and SM-5 read the whole history: a bad deploy or a slow recovery does not stop counting because it is old.

| Metric | Rule |
|---|---|
| SM-1 cost reduction | `1 - (cost + failed cost) / all-teacher baseline` over the window, per token (NFR-11: the GPU-hour view is the cost report's). Failed requests' spend comes from the `failures` table when the ledger has one, so a failure cannot raise the savings |
| SM-2 audited precision | Audits pooled over the window, every version together (canaries included: their answers were served), with a 95% Wilson interval. Met when the point estimate reaches 95% on at least the release gate's own minimum audits (`RATCHET_MIN_AUDITS`); with fewer, `null` |
| SM-3 student share | Student-served requests over all requests in the window |
| SM-4 bad deploys at 100% | Versions that reached 100% (a `live` event names them, or they are live now) and were later found bad: an `alert` names them, or a `rollback` names them after they reached 100%, or they are `rolled_back` now. A canary rolled back before 100% is the gate working, not a bad deploy. `null` until some version has reached 100% |
| SM-5 drift recovery | The longest drift episode in nights (end day minus start day). An episode starts on a day whose escalation rate is at least 5 points above the lowest of the 3 measured days before it, and ends on the first later day at most 2 points above that baseline. `null` while an episode is open; `met` false once any episode, open or closed, took longer than 3 nights; 0 when the rate never rose |
| NFR-1 latency | The nearest-rank median latency of the window's student-served requests, under 1,000 ms: `requests.wall_ms` (the whole cascade) when the ledger records it, else `latency_ms` (the providers' part) |

**SM-5's escalation rate is over the requests the student judged.** A day's rate is `answered_escalated / answered`: of the requests whose student samples came back (they have a confidence), the share it was unsure of. A request the student never saw is left out: a cold start, a pinned or teacher-only task, and above all an outage. The router marks `student_error` and `student_breaker` requests as escalated, so with every request in the denominator an endpoint outage would read as drift, and a day after an outage, with its low baseline, could open an episode. A day counts only when the student judged at least 50 requests, so one quiet day's noise is not read as drift. Folded request rows keep these two sums (`daily_rollup.answered`, `answered_escalated`), so the rate survives retention.

**Targets in one place.** `kpis.SM_TARGETS` holds each target as an operator, a number and a unit. `scripts/reqtrace.py` (rule j) parses the Target column of PRD §8.1 and fails when the two differ, so the table and the code cannot drift apart.

**Statuses from the reference simulation.** `test_reference_simulation_meets_success_metrics` computes the metrics on the pinned reference run (seed 7) and asserts the targets for SM-1 to SM-4 and NFR-1; those rows are `Met in simulation (seed 7)`. SM-5 measures 4 nights there, so its row is `Not met (simulation: 4 nights, seed 7)` with a strict expected failure (`test_sm5_drift_recovery_within_three_nights`) that starts passing, and so fails the suite, once drift handling brings it within 3. This item measures drift; it does not change how Ratchet reacts to it.

**Reads.** New `Ledger` methods, each under the ledger's lock: `kpi_days` (per-day sums, folded rows included), `audit_totals(day_from, day_to)` (pooled audits, folded rows included), `failed_cost`, `latency_quantile` (an `ORDER BY` with an `OFFSET`, no SQLite extension) and `events_of(kinds)`. A report takes the lock once per read, not for the whole report, so requests keep flowing while it is built; its numbers may then differ by the few requests logged in between. A NaN or infinity anywhere becomes `null`, since the gateway's responses refuse them.

## Consequences

- One number per metric, the same code in CI, the demo and production. A live run can be compared with the simulation directly, and every number is labelled `simulated` in mock mode.
- The tests now hold the loop to the PRD's targets instead of weaker ones, and SM-5's gap is visible in the PRD and in CI instead of in a paragraph.
- `/ratchet/stats` does a few more queries per poll (a per-day aggregate, the window's audits and one sort of the window's student latencies, using the `requests(day)` index). At the dashboard's 15-second poll this is small next to what the endpoint already computes.
- SM-5 over the whole history means one slow recovery keeps it unmet for good; the episodes in `detail` show when it happened.
- `requests.wall_ms` and the `failures` table come from other v0.5 items. The reads look for them at run time, so this module works on ledgers with and without them.

## Alternatives considered

- **Plain escalation rate for SM-5** (escalated over all requests, as the dashboard's chart shows). It reads an endpoint outage and a change in the share of pinned traffic as drift, as above. On the reference run both rules give the same episode (days 5 to 9).
- **Calling the spec's `Ledger.audit_counts(day_from)`.** `Ledger.audit_counts(vid, since_id)` already means a version's audits since a ledger id in the release gate's anytime-valid code on the main branch; one name with two meanings would break that code at merge time. The pooled reader is `audit_totals`.
- **SM-2 met on the Wilson lower bound.** Stricter, but the PRD's target is a precision, not a bound, and the release gate already uses bounds for its decisions (ADR 0005). The interval is reported beside the point estimate.
- **The whole history for SM-1 to SM-3.** It mixes the cold start into the steady state the targets describe (the reference run saves 69% over ten days and 84.5% over its last three).
- **Per-day latency percentiles** (as the hot-path timing reports them). The median over the window matches the other windowed metrics and needs no cache.
