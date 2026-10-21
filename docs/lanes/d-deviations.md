# Lane D: deviations from the specs

Where lane D built something other than its spec asked, and why, so the integrator knows what the merged code does.

## OPS-OBSERVABILITY: the retirement event is `live_retired`, not `retired`

**Spec.** Item (5) asks for a `retired` event when `ReleaseGate.tick` retires the old live version on a promotion.

**Built.** `ReleaseGate._tick_canary` writes kind `live_retired` (`v4 retired: v5 replaces it as live`) just before the `live` event. `scripts/reference_check.py` lists `live_retired` in `IGNORED_KINDS`.

**Why.** v0.4 already writes `retired` for a candidate replaced at admission, and those events are part of the pinned event logs that `make reference` compares by kind. Writing the new event as `retired` would change the three pinned workloads; adding `retired` to the ignored kinds would hide v0.4's own events from the comparison. A kind of its own keeps the pinned logs byte-identical and still gives every version transition an event (`tests/test_observability.py::test_every_version_transition_writes_an_event`).

**Where it shows.** `ratchet/release.py` (`_tick_canary`), `scripts/reference_check.py` (`IGNORED_KINDS`), docs/RUNBOOK.md (event kinds), docs/ARCHITECTURE.md, ADR 0062, PRD FR-12.4. A dashboard or alert that looks for the retirement must match `live_retired`.

## OPS-COST-RATE-LIMITS

**`student_only` is a per-request flag, not `Ratchet.flags.student_only`.** The spec has the Runtime set a global flag on the router once the gateway's budget is spent. Scout's agents call the same `Ratchet`: its pinned planner and writer would then fail whenever `/v1` spent its budget, against invariant 11 and the spec's own "Scout stays under its Guard"; the simulator would be affected too, and a global flag flipped by one request races with others. The gateway sets `RequestMeta.student_only` on each `/v1` request instead (`ratchet/gateway.py` `_budget`, `ratchet/router.py` `RequestMeta`, `Ratchet._budget_refused`).

**No shadow in `student_only` mode.** The spec only drops the audit. A shadow disagreement needs the teacher's arbitration, and an unarbitrated one counts against the candidate (ADR 0013), so a spent budget would bias the release gate; the shadow is skipped for those requests, like a stand-in's (ADR 0019).

**A new UTC day starts from zero instead of re-reading the ledger.** The spec seeds "at startup and on rollover". Re-reading on rollover can count twice a request logged before the read and added after it. One process sees every gateway request it serves, so zero is exact; the ledger is read at startup and when the clock moves back to an earlier day (memory holds nothing for it). A request is counted on the day its add runs.

**The seed query scans the `(client, ts)` index.** The spec says it uses "the existing index"; a `ts` range across clients cannot seek on an index led by `client`, so SQLite reads the whole index once (`EXPLAIN QUERY PLAN`: `SCAN requests USING INDEX requests_client_ts`). It runs at startup only (and on a clock moved back), so no new index on `requests` was added; the month is read only when a monthly cap is set. The alert de-duplication reads events through a new index `events_kind_ts`.

**Only gateway rows are seeded.** The seed counts rows with a gateway request id (`request_id IS NOT NULL`), so Scout's and the simulator's rows (and the mock warm-up's) never count toward `/v1` limits. Rows from before request ids existed are not counted (only the upgrade day can notice).

**Failed spend counts.** The spec's seed reads `requests` only. A request that failed after the student was sampled spent money no `requests` row holds (ADR 0062), so the meter also reads `failures.cost_usd` and the gateway adds `RequestContext.spent_usd` (new) on any failure; otherwise a teacher outage would be free against a budget.

**Block mode refuses chat completions, not every `/v1` route.** `/v1/models` and `/v1/ratchet/requests/{id}` cost nothing, and refusing them would break SDK health checks for no saving. The 429 code is `gateway_budget_exceeded` (the spec names none), so a client can tell it from its own key's `budget_exceeded`, and its message does not name the operator's budget.

**The request rate runs before the body is read; the in-flight caps after.** The spec places the caps (quota, dollars, scope) after validation and task resolution, which they are. It does not place the rate limits. The token buckets run on the event loop before the body is read, so a flood is refused without a worker thread or a read, as OpenAI counts requests; a request refused later (in-flight, 400, 403, quota) has still used its token. The in-flight cap and a key's concurrency are taken once the body is in, just around the worker thread, because they protect the thread pool: taken earlier (as first built, until review), a client that trickled its body held a slot no worker used, and 32 stalled uploads refused every chat request. The slots are given back when the response is built (the cascade is done by then; a later streaming item that streams the teacher live should hold them until the stream ends).

**A scrubbed read shows no dollar cap.** The spec adds `clients[].daily_cap_usd` and the gateway budget to `/ratchet/stats` without saying what a public reader sees. An anonymous read of a public live dashboard gets them as null, and `budget` events under the key's pseudonym with no amount, as the gateway's own 429s never name its budget (found in review: the events named the key).

**Headers.** On a 200 the daily quota keeps the `X-RateLimit-*-Requests` headers it has had since v0.4; a key with a rate and no daily quota gets them for its bucket; a rate 429 describes the bucket. A concurrency 429 sends `Retry-After: 1` only (the request-rate headers would describe a different limit). A budget 429 also sends `X-Ratchet-Budget-Reset` (the spec's "reset time") in OpenAI's duration format, and `Retry-After` names the cap that resets last.

**Monthly caps alert too**, de-duplicated per UTC month, and a cap lowered at a restart writes the shares already passed at once.

**`store` is "may become training data".** The spec lists `store=1` without saying what it does. It is read like OpenAI's `store` parameter: `store=0` makes every request of the key `RequestMeta.storable=False` (served, audited and logged, never stored).

**A negative `RATCHET_API_KEY_DAILY_REQUESTS` now stops startup.** It used to mean no quota; every limit default is checked like the other settings (`KeyLimits.check`).

## OPS-ADMIN-API

**Requirement ids, ADR number and the invariant.** The spec's FR-6.9 (compare-and-set transitions) is FR-12.9 in section 6.12: lane D's new rows must use FR-12.x or FR-1.6x, and FR-12.1 to FR-12.8 were taken or reserved (FR-12.5 config readiness, FR-12.6 Night School leases). The spec's ADR 0015 exists (the student breaker); this is ADR 0064, the next free number in lane D's range. INV-18 is not added to CLAUDE.md's numbered list (numbers would collide across lanes); it is proposed in docs/lanes/d-invariants.md with two companions (every manual change is recorded with it; holds never stop a rollback). The tests carry FR-12.1, FR-12.9, INV-5, INV-8, INV-10 and INV-13 markers; the integrator adds the new invariant's number.

**`promote_live` takes the old live version.** The spec's signature is `promote_live(vid, from_state, from_pct, event)`. To compare the retirement too, the caller names the live version it saw (`old_live=`, None for none) and the retirement's event (`retired_event=`, the gate's `live_retired`); nothing changes unless the versions live now are exactly that one. `transition` refuses a move to live (ValueError), so `promote_live` is the only way to make a version live.

**`admit` is one transaction and acts only on a candidate.** The spec only says admit uses `transition`. If an admin promoted the candidate while it was scored, the old admit would have retired it as an "older" in-flight version; now it is left alone with a `conflict` event, and the checks, the retirement of an older shadow or canary and the placement commit together. The tick's steps keep their reads outside a transaction and rely on compare-and-set, so the write lock is not held while audits are counted.

**More moves than the spec's table.** The spec allows rejected or rolled-back to canary with force and refuses a candidate without an eval. Also allowed with force: retired to canary (restoring an earlier version, which the old runbook did with `set_version`) and a candidate with an eval to canary (the old runbook's "force a candidate to canary"). A version without a golden eval (no numeric `threshold`) is refused for every move, force or not.

**Holds cover admission and an audit rate of 0.** The spec's freeze skips shadow-to-canary and canary advance or promotion. A cold-start candidate would otherwise go straight to canary 10% while frozen, so it shadows the teacher instead and is promoted on its evidence once unfrozen. An audit rate of 0 holds new canaries the same way (admission, shadow to canary, and an admin's promotion to canary without `force`): such a canary could neither advance nor be rolled back. Rollback, rejections and the live alert are never held.

**`student_disabled` is a route of its own and stops the shadow.** The spec has `_pick_version` return None and the teacher answer. Requests are logged with route `student_disabled` (new in `router.ROUTES`) rather than `cold_start`, so the dashboard tells an operator's kill switch from a cold start; and the shadow candidate is not sampled either (the switch stops every student call).

**Runtime pins.** Only a registered operator task (built-in, file or API) can be pinned (404 for an unknown name, 409 for an auto task); a pinned copy keeps the spec's own `spec_hash`, so an eval's record stays valid and unpinning needs no re-score; Night School leaves a runtime-pinned task's rows out of training and the golden eval, as INV-10 requires for any task pinned now. A pin row outlives a deleted task spec and applies again if the name is registered again (`unpin` removes it). `GET /ratchet/tasks` entries gain `runtime_pinned`.

**Endpoints and CLI beyond the spec.** `GET /ratchet/admin/flags` (who set each switch, and the pins with their reasons). `POST /ratchet/admin/tick` is recorded as an admin action (`tick`, its reason optional) and answers the routed versions before and after and `moved`. The CLI has `retire` and `unpin` too, and no `tick` (`python -m ratchet.live tick` exists); an unknown version or task exits 2 like a bad input, a refused move 1. A promotion's `load_adapter` is ignored in mock mode, 409 in live mode without `RATCHET_STUDENT_BASE`, and runs only when the promotion changes something.

**No tamper-evident ledger yet.** `admin_actions` rows are written directly; when the other lane's tamper-evident ledger lands, they should be appended through it.
