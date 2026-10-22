# ADR 0073: Fallback attribution

- Status: Accepted
- Date: 2026-10-21

## Context

Without `RATCHET_STUDENT_BASE`, the live gateway's student resolver returns the hosted, untuned fallback model on Token Factory (`RATCHET_FALLBACK_STUDENT`, Nemotron Nano 30B) for every version: live, canary and shadow alike. That mode is not rare. It is the emergency step when the student endpoint is down (RUNBOOK), and it is what the cost report recommends below the hosted-fallback crossover, which covers demo volume: run the endpoint only for the night job, and let the fallback serve the student role per token during the day.

A code reading found that in this mode:

- Every row still logged `student_version` = the routed version, so `Ledger.version_live_quality` counted the fallback's audited answers as the version's. The canary could advance a stage, be promoted to live or be rolled back on another model's answers, and the live alert could fire on them. With traffic during a judging window and a canary in flight, a gateway on the fallback could roll back a good adapter it never served.
- The shadow ran on the fallback too, so its verdicts compared the fallback with itself (the live student was the same model), and the teacher was paid to arbitrate between them.
- The response said `ratchet.student_version: N` for an answer version N's adapter did not write, and its `system_fingerprint` named that version.

The release gate's statistics (NFR-8) are only meaningful over answers the version's own adapter wrote.

## Decision

- **An answer is a version's only when its own adapter wrote it.** The rule is `model == version.adapter_uri`, where `model` is what the resolver named for the call (`Ratchet._own`). Ratchet's own resolvers (the vLLM endpoint and the mock fleet) name the adapter; the hosted fallback names its model id. The rule is positive on purpose: a resolver that names anything else (a misconfiguration, a future routing scheme) gets no attribution either, and the operator sees it, instead of release decisions quietly resting on another model.
- **The row records the model.** New columns `requests.student_model` (the student's samples, set when they came back) and `requests.shadow_model` (set exactly when `shadow_version` is: the model behind the verdict), in `SCHEMA` and `MIGRATIONS`. `shadow_model` joins the shadow verdict columns `Ledger.update_request` writes once, so an off-path shadow whose samples fail clears its model with its verdict, as an inline run logs it. `student_version` keeps naming the version the request was routed to, so routing and canary traffic stay visible.
- **The gate's queries filter on it.** `AUDIT_TALLY_SQL`, `AUDIT_PENDING_SQL` and `SHADOW_QUALITY_SQL` add `OWN_STUDENT` / `OWN_SHADOW`: `student_model IS NULL OR student_model = (SELECT adapter_uri FROM versions WHERE id=?1)`. The adapter is read once per query by primary key, the requests indexes are still searched as before (the plans are tested), and the callers' parameters do not change. A NULL model (every row logged before the column) counts as it always did. `student_model` is written with the row and never changed, so the incremental tally (ADR 0060) never has to forget a row, and a pending off-path audit (ADR 0072) of the fallback's answer never enters the tally's pending set.
- **A shadow under the fallback does not run.** `Ratchet._shadow_student` resolves the shadow once per request, before a sidecar place is taken; if the model is not the candidate's adapter, there are no samples, no arbitration and no place. A resolver that raises for the shadow is a failed shadow sample (counted, a failures row), never the request's failure (INV-15); resolving there instead of inside `_sample` keeps the number and order of resolver calls the same, so the reference runs do not move.
- **The response names the model, not a version.** For another model's answer `ratchet.student_version` is null, `model` is the configured fallback id (not whatever the server echoed), and `system_fingerprint` is `student|<model>|<threshold>`, the same whichever version the request was routed to. The threshold stays as before: the global `RATCHET_CONFIDENCE_THRESHOLD` for the fallback, which was never calibrated (`Ratchet.threshold`).
- **It stays visible.** A throttled `unattributed` event per version (1st, 10th, 100th, 1000th) names the version, its state and the model, and says the shadow waits or the answer does not count. `/ratchet/stats` `days[].fallback_served` counts the student-served requests whose model is not their version's adapter (in `student_share`, toward no release).
- **Audits still run as drawn.** The audit draw stays on the request path in the same order; a fallback answer is audited like any other. The audit's teacher answer is still training data (INV-2), and the day's `audit_agreement` shows the fallback's quality. Only the attribution changes.

So while the fallback serves, the canary, the shadow and the live alert see no evidence and every stage holds. On a fresh deployment there is no version, so the teacher answers and the fallback is never called.

## Alternatives considered

- **Log `student_version` NULL for fallback answers.** One change instead of a column, but the row would lose which version the request was routed to (canary traffic, replays of a version's traffic, receipts), and a NULL version means "cold start" everywhere else.
- **Compare with `RATCHET_FALLBACK_STUDENT` instead of the adapter.** The ledger does not know the settings, so the filter would need the id passed into every gate query, and any other non-adapter model would still count. The positive rule needs nothing but the versions table.
- **Do not route canary traffic while the fallback serves.** It would change the canary pick, a random draw the reference runs and the off-path parity depend on, and hide the canary from traffic the operator expects to see. Routing stays; attribution decides.
- **Stop auditing fallback answers.** It would save teacher calls but change the audit draws, lose free training data and the only quality signal for the fallback itself.

## Consequences

- With a canary or shadow in flight, a gateway on the fallback holds it, and Night School skips while a release is in flight (INV-5). To move on, start the endpoint so the candidate's adapter serves (also for part of the day), or retire or roll back the candidate by hand (RUNBOOK). The `unattributed` events and `days[].fallback_served` say this is happening.
- A version's audited precision (and the canary's, the live alert's) covers its own adapter's answers only, also across a switch to the fallback and back within one stage.
- Code that adds a resolver must name the version's `adapter_uri` for the version's own answers, or they will not count.
- Rows logged before this change have no model and count as before; the reference simulation and the three pinned workloads are unchanged (the mock fleet names the adapter).
