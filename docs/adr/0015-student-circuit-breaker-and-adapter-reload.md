# ADR 0015: A circuit breaker per student endpoint, automatic adapter reloads, and a route on every request

- Status: Accepted
- Date: 2026-10-20

## Context

ADR 0014 gave the student its own short timeouts (2 s to connect, 15 s to answer) and no retries, because the teacher is its retry. That bounds what one request pays for a broken endpoint, but every request still pays it:

- **A hung endpoint costs every request its timeout.** When the vLLM endpoint accepts connections and never answers, each request waits 15 s before the teacher steps in, for as long as the outage lasts. `student_errors` only throttled the event log; it never changed what the next request did.
- **A restarted endpoint loses its adapters.** vLLM forgets LoRA adapters loaded at runtime when it restarts, and answers 404 ``The model `student-v3` does not exist.`` for each. Every request then escalated until someone ran `python -m ratchet.live reload` by hand.
- **The ledger could not say why the teacher answered.** `served_by='teacher'` with `escalated=1` meant "unsure", "student down" and "no version yet" alike, so neither the dashboard nor an operator could tell an outage from drift.

## Decision

- **`resilience.Breaker(name, failure_threshold=5, open_s=30, max_open_s=300, clock, on_change)`**, three states:
  - closed: calls go through; `failure_threshold` endpoint failures in a row open it, and a success resets the count.
  - open: `allow()` is False for `open_s` seconds; the caller raises `CircuitOpen` (an `UpstreamError`, 503 `circuit_open`) without calling.
  - half_open: exactly one caller gets `allow()` True (the probe); concurrent callers see it as open. A successful probe closes the breaker and resets `open_s`; a failed one opens it again for twice as long, up to `max_open_s`.
  - Guarded by one `threading.Lock`, taken only while counting failures or while not closed: when closed, `allow()` and a successful `record()` read attributes only, so the hot path takes no lock.
  - Only the probe's own thread decides in half_open, so a late failure from a call that began before the breaker opened cannot be taken for the probe's. `release()` hands a probe back undecided, and a probe that has not reported back within one wait is presumed lost, so the breaker can never stay half-open forever.
  - `on_change` runs after each transition, outside the lock, and an exception from it is swallowed: a failing ledger write must not fail the request the breaker guards.
- **What counts** (`resilience.endpoint_down`): a timeout, a connection or protocol error, an unreadable answer, a 5xx, 408 or 429 means the endpoint looks down. Any other 4xx is the endpoint answering about this request (a prompt too long for a 4B model's context, a missing adapter), so it counts as healthy: five long prompts must not take the student away from every short one. An error that is not an HTTP failure (a bug in a provider, the mock world not knowing a request) counts neither way.
- **One breaker per failure domain.** `Ratchet` makes breakers on first use, keyed by role and endpoint. The endpoint is the provider's `base_url` (every `OpenAICompatProvider`, and the mock fleet's students), or its class when it has none. Live and canary on one endpoint share the `student` breaker; shadow calls use a separate `shadow` breaker, so a bad staging adapter cannot open the breaker live traffic depends on. Another endpoint in the same role is `student-2`, and so on.
- **The router.** `_sample` checks `allow()` before calling and records each outcome after. `handle` catches `CircuitOpen` and escalates to the teacher with `escalated=1`, `confidence` NULL and route `student_breaker`, without counting another `student_error` (the breaker already counted). For the shadow, an open breaker means no shadow record, as when its samples fail. Settings: `RATCHET_STUDENT_BREAKER_FAILURES` (5; 0 turns the breaker off) and `RATCHET_STUDENT_BREAKER_COOLDOWN_S` (30); a bad value stops startup. Each transition writes a `circuit` event, `student: closed->open after 5 failures (ReadTimeout)`, naming the failure's class and status (`resilience.describe_failure`), never its message. `Ratchet.health()` adds `breakers`: `{name: {state, failures, open_until, open_s}}`.
- **Lost adapters.** A 404 whose message names the version's adapter, when the model sent is the adapter (not the hosted fallback model), becomes `AdapterMissing` (404 `adapter_missing`). It does not count against the breaker. The request escalates (route `student_error`, counted in `student_errors`) and `Ratchet.on_adapter_missing(version)` is called. In live mode with an endpoint, the gateway wires that to `live.AdapterReloader.request`: one daemon thread, started on first use, calls `live.load_adapter(student_base, key, name, f"{RATCHET_ADAPTER_ROOT}/{name}")`, at most once per adapter per 60 s however many requests hit the 404, and writes `adapter_reloaded` or `adapter_reload_failed`. `request` never waits. `python -m ratchet.live reload` stays as the manual fallback. The name match is whole-word, so a 404 for `student-v30` never reloads `student-v3`.
- **A route on every request.** New column `requests.route` (in `SCHEMA` and `MIGRATIONS`): `student`, `confidence`, `cold_start`, `pinned`, `student_error`, `student_breaker`. Later items add more (for example `degraded`, `multimodal`, `rule:<i>`). `Ledger.stats_by_task` adds `routes: {route: count}`, most frequent first, with rows from before the column counted as `unrecorded`; `/ratchet/stats` gains the key (it only grows). `Result.route` carries it to embedders.
- **A demo.** The mock-only `POST /ratchet/demo/student-outage?seconds=60` (clamped to 5 to 300) makes `MockFleet.resolve` return a student on the mock production endpoint that raises `httpx.ConnectError` until the deadline, so the release log shows the breaker open, the teacher answer, a probe, and the breaker close. `Runtime(clock=...)` drives the breakers, reloads and outage from one clock, so tests run the whole demo on a fake clock.

## Consequences

- PRD FR-2.11 (new) and NFR-2 reworded. During an outage requests cost the teacher's latency instead of the teacher's plus the student's timeout; after one wait, a single request per wait pays the timeout again as the probe.
- An endpoint restart heals itself within one request per adapter, plus the time vLLM takes to load it. The requests in between escalate, at teacher cost.
- Live and canary share a failure domain: a canary adapter that makes vLLM fail with 5xx takes live traffic to the teacher too, until the breaker's probe finds the endpoint healthy. A canary that merely answers badly is the release gate's job (rollback), and a missing canary adapter is a 404, which never counts.
- While the shadow's breaker is open the shadow collects no evidence, so its release decision waits; it can never be promoted on fewer answers.
- `student_errors` now counts only calls that were made. An outage shows as a few `student_error`s and then `student_breaker` routes; dashboards that infer outages from `student_errors` alone should read the routes.
- The reference simulation is unchanged: mock students never fail in it, the breaker draws no random numbers, and `route` adds a column without changing any other value.
- The mock fleet's students name their endpoint (`base_url` `mock://production` or `mock://staging`); `MockStudent` objects are still built per call, so the sampling seeds are unchanged.

## Alternatives considered

- **Key breakers by provider object (`id(provider)`).** It works in live mode, where one provider object serves the endpoint, but a resolver that builds a provider per call (the mock fleet does) would get a new breaker per request, never open, and leak memory; a reused `id` could even attach a stale breaker to a new object. The endpoint URL is the failure domain.
- **One breaker for live, canary and shadow.** Simpler, but a broken staging adapter would push all live traffic to the teacher.
- **Count every failure, 4xx included.** It would open the breaker for prompts the student cannot take and for lost adapters, which the endpoint answers quickly and correctly.
- **A rolling error rate instead of failures in a row.** Better for a flaky endpoint under heavy traffic, but it needs a window and a minimum volume to mean anything; consecutive failures are enough to stop waiting on a dead endpoint, and the half-open probe handles recovery.
- **Reload every active adapter on the first 404.** One restart loses them all, but each adapter's own traffic reports its 404 within seconds (the shadow sees every request), and reloading only what was asked for keeps the reloader simple and its events precise.
- **Reload synchronously, inside the request.** Loading an adapter can take tens of seconds; the request would wait for it, and a burst of requests would load it many times.
- **A breaker library (pybreaker).** A new dependency, without the per-thread probe ownership or the 4xx rule, for about 150 lines.
