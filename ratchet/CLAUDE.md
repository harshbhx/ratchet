# CLAUDE.md: `ratchet/` (core package)

## Request path (`router.py`)

`Ratchet.handle(messages, task, client=None)` (`client` is the API key name; the gateway passes it, internal callers leave it `None`):

0. Pinned task (`tasks.get(name).pinned`) → `_pinned()`: teacher answers, request logged, **no** `add_example`, no student/audit/shadow. Return.
1. `_pick_version()`: canary gets `traffic_pct`% of requests, otherwise live; `None` on cold start → teacher.
2. Student: `n = Settings.samples` samples with `confidence.STUDENT_SAMPLING` (temperature 0.7, logprobs on) → `confidence.score()`. The golden eval (`LiveEvaluator`, `MockFleet`) uses the same constant; change sampling in that constant, never inline. `_sample` goes through the endpoint's breaker first (below): when it is open, `CircuitOpen` → teacher, route `student_breaker`, not counted as a student error.
3. Serve the student if `sc.valid` (at least one sample was a valid answer; a cut-off sample, `finish_reason` `length` or `content_filter`, gets no vote) and `confidence >= threshold(version, model)`. The threshold comes from the version's eval (calibrated); the untuned fallback model uses `Settings.confidence_threshold`.
4. With probability `audit_rate`, the teacher also answers a served request (quality signal + free training data). If that call raises, the student's answer is served anyway: `audited=0`, `audit_errors` + 1, a throttled `audit_error` event (INV-15). A teacher answer that `answer_problem` rejects is not an audit either (`audited=0`, INV-16).
5. Otherwise, or if the student call raised, escalate: teacher answers. Every teacher call goes through `_call_teacher`, which calls `_store_example`, the one caller of `add_example` (INV-2). It stores only a complete, valid answer (INV-16, `answer_problem`: `finish_reason` `stop` or `tool_calls`, not empty after `tasks.clean_answer`, a structured task's answer valid JSON, also after redaction), cleaned with `tasks.clean_answer`, after `redact_messages` / `redact` replace identifiers (when `Settings.redact_pii`), with its license and the placeholder count. Anything else is served but skipped, with a `skip_example` event on the 1st, 10th, 100th and 1000th skip per reason (task and reason only, never the answer). It returns whether it stored (an insert, or a duplicate's `seen` bumped), which goes into `requests.stored`.
6. Shadow: if a version is in `shadow`, it runs the same cascade silently. A would-serve answer that disagrees with a live-student answer is arbitrated by the teacher (`_arbitrate`). Neither can fail the request (INV-15): a failed shadow sample leaves no shadow record, a failed arbitration (or one skipped because the audit already failed) leaves `shadow_agree` NULL, which `Ledger.shadow_quality` counts as a disagreement. So does a teacher reference (escalation, audit or arbitration) that `answer_problem` rejects (INV-16). Both add to `shadow_errors` and write a throttled `shadow_error` event.
7. `ledger.log_request(...)` with `cost_usd`, `baseline_cost_usd` (= the all-teacher cost of this request), `stored` and `route` (`ROUTES`: `student`, `confidence`, `cold_start`, `pinned`, `student_error`, `student_breaker`; `Result.route` too). Every path that logs a request sets a route; a new path adds its value to `ROUTES`, API.md's routes table and `test_every_request_row_has_a_route`.

Keep `handle` free of I/O beyond the providers and the ledger. Anything slow belongs in Night School. `Result.text` is `tasks.clean_answer(task, served.text)` on every path (`_pinned` too): reasoning never reaches a client, and a structured answer is bare JSON.

Failures. Only a teacher call whose answer is needed may fail a request: escalation, cold start, pinned and teacher-only (the gateway maps its `UpstreamError` to 400, 429, 502 or 504 in `_map_upstream`, INV-7). Every other upstream call (the student, the audit, the shadow's samples, the arbitration) is wrapped, counted under `_errors_lock` (`student_errors`, `audit_errors`, `shadow_errors`; `Ratchet.health()` returns them) and logged by `_count_failure` on the 1st, 10th, 100th and 1000th occurrence. These events (`student_error` too) name the exception class, HTTP status and upstream code (`resilience.describe_failure`), never the message, which can hold the endpoint URL or quote the request (vLLM's validation errors do), and `/ratchet/stats` serves events to anyone (INV-12). A new optional call must follow the same pattern ([ADR 0013](../docs/adr/0013-optional-teacher-calls-fail-open.md)).

Upstream calls (`providers.py`, `resilience.py`, [ADR 0014](../docs/adr/0014-upstream-failure-semantics.md)). `OpenAICompatProvider.complete` raises only `UpstreamError` (an `httpx.HTTPError`: status, OpenAI `type`/`code`/`param`, a clipped message, `retry_after`; never the URL or headers). It retries through `retry_call` with the provider's `RetryPolicy`: connection failures and 408/409/429/5xx, a `ReadTimeout` once, never another 4xx; waits are full jitter or the server's `Retry-After` (a wait over `cap_s` returns the error at once, and `_map_upstream` passes it on as the client's `Retry-After` on a 429, 502 or 504); the deadline caps each attempt's timeouts. A 2xx body is parsed after the retries and never retried (`bad_upstream_response`). `_completions` reads per choice the message's `content` only (never `reasoning_content` or `reasoning`), `finish_reason` (`stop` when absent, `unknown` when it is not a short word; `providers.COMPLETE` and `CUT_OFF` name the values the router and `confidence.score` act on), its share of `reasoning_tokens`, and `out_tokens` that always add up to `usage.completion_tokens`, split by the choices' logprob counts when every choice has them, else evenly. Roles get their own `Settings.teacher_timeout()` / `student_timeout()` and `teacher_retry()`: the teacher and judge are retried, the student on the request path is not (the teacher is its retry), the golden eval's student is. New upstream calls take `sleep=` (and `clock=`, `rng=`) as default arguments so tests never wait.

Student breakers (`resilience.Breaker`, [ADR 0015](../docs/adr/0015-student-circuit-breaker-and-adapter-reload.md)). `Ratchet._breaker(role, provider)` makes one per role (`student` for live and canary, `shadow`) and endpoint (the provider's `base_url`, else its class; never `id()`, because a resolver may build a provider per call) on first use, with `Settings.student_breaker()` (`RATCHET_STUDENT_BREAKER_FAILURES`, `RATCHET_STUDENT_BREAKER_COOLDOWN_S`) and `Ratchet(clock=)`. `_sample` calls `allow()` before and `record(ok)` / `release()` after every student call, from the same thread (only the probe's thread decides in half_open): `resilience.endpoint_down(e)` says whether a failure counts (timeouts, connection errors, 5xx, 408, 429), is the endpoint answering (other 4xx: `record(True)`), or says nothing (not an HTTP error: `release()`). A 404 naming the version's adapter becomes `AdapterMissing`; `handle` then calls `on_adapter_missing(version)` (the gateway's `live.AdapterReloader.request` in live mode; never let it fail the request). Transitions write `circuit` events through `_circuit_event`; `health()["breakers"]` has the snapshots. Keep the closed path lock-free: `allow()` and a successful `record()` only read attributes.

`teacher_only(messages, task, client)` is the pinned path (`_pinned`) for a request the student cannot take (a multimodal part, `RATCHET_TEACHER_MODALITIES`): the teacher answers, the request is logged, nothing is stored.

## Release (`release.py`)

States: `candidate → rejected | shadow | canary(10) → canary(50) → live`; `canary → rolled_back`; old versions → `retired`.

- `admit(vid)` runs after training: precision ≥ `min_precision`, coverage ≥ `min_coverage`, no regression vs live beyond `tolerance`. Cold start (no live version) goes straight to canary 10%.
- `tick()` runs periodically: shadow decisions (unarbitrated disagreements count against the shadow; the event says how many), canary advance (needs `min_audits` audits **in the current stage**, tracked via `stage_since`), rollback when the Wilson upper bound < `live_floor`.
- Change thresholds through `ReleasePolicy` (or `RATCHET_<FIELD>` env vars), never inline.

## Ledger (`ledger.py`)

- Every write commits immediately (v0 simplicity; fine for a single gateway process).
- Every public method is wrapped in `@_locked` (one `threading.RLock`), because Scout's agents call `Ratchet.handle` from several threads and share one SQLite connection (`check_same_thread=False`). Add `@_locked` to any new public method. Re-entrant, so locked methods may call each other.
- `stats_by_task()` feeds `tasks` in `/ratchet/stats` and the dashboard's **By task** table.
- `examples.hash` is unique per (task, messages); duplicates increment `seen`.
- Only the functions in `ALLOWED_ADD_EXAMPLE_CALLERS` (`tests/test_nightschool.py`; today `Ratchet._store_example`) may call `add_example`. An AST test fails on any other use under `ratchet/` until the allowlist is changed in a reviewed edit (INV-2, INV-10).
- `split` is deterministic from the hash (`golden_fraction`), so an example never moves between sets, except via the `FREQUENT` rule applied in `nightschool/dataset.py`.
- Schema changes need both `SCHEMA` and `MIGRATIONS`. Indexes on migrated columns go in `INDEXES` (created after migrations), not in `SCHEMA`.

## Privacy, keys and BOMs

- `redact.py`: `redact_messages` covers every message field that can carry user text: content (strings and text parts), `name`, `refusal`, tool and function call arguments. `PATTERNS` is an ordered list of `(label, regex, check)`; earlier patterns win, so put specific formats (with checksums) before broad ones like `PHONE`. Every new pattern needs a positive test **and** must keep `test_redact_leaves_ordinary_text_alone` and `test_redaction_is_a_no_op_on_the_mock_world` passing (mock lookups match exact text).
- `auth.py`: `KeyRing(spec)` raises `ValueError` on a bad spec, so a typo stops the gateway at startup. `identify()` compares SHA-256 digests with `hmac.compare_digest` over every key.
- `bom.py`: `manifest()` from the exact train and golden rows, `document()` wraps it with `sha256` and an optional Ed25519 signature over `canonical()`. `Signer` imports `cryptography` lazily (the `sign` extra). Change the manifest shape only with a new `SCHEMA` version.
- Gateway: on `/v1/chat/completions`, `BodyLimitMiddleware` (413), `_client()` (401), `validate_chat_request` + `normalize_messages` (400), then `_quota()` (429) run before the cascade, so a refused request is never logged or counted; `/healthz` is public. Streaming runs the cascade first, then yields chunks (`_chunks`), so errors are ordinary HTTP errors.

## Licenses (`licenses.py`)

To add a teacher, add it to `REGISTRY` with its real license name and URL. `CLOSED` prefixes are refused. `Ratchet.__init__` calls `require_distillable` on the teacher, so a bad config fails at startup.

## Tasks (`tasks.py`)

`REGISTRY` holds the known tasks. `Task.pinned=True` means always the teacher, never training data (`research-plan`, `research-write`). Distillable tasks with `compare_keys` compare only label fields (`Task.structured`; a later `format` field joins it there). `stats_by_task()` reports `pinned` per task, which the dashboard's **By task** table shows.

Answers are cleaned in one place (FR-2.7): `strip_reasoning` removes complete `<think>` blocks (any case), everything up to a lone `</think>` and an unterminated `<think>` with what follows; `clean_answer(task, text)` adds, for structured tasks only, unwrapping one code fence that is the whole answer when its body is JSON. `Task.canonical` calls `clean_answer`, and an empty answer is `INVALID` for every task. Compare, store and serve through these; never strip tags or fences by hand. `parse_json` tolerates reasoning, fences and surrounding text, and returns None (never raises) for JSON nested too deeply for the parser. All of these run on every sample on the request path, so keep them linear in the answer's length: no regex that can rescan from every position (`.*?` pairs, `\s*` before a literal), which a model's output can make take seconds (`test_answer_cleaning_is_linear_on_adversarial_answers`).

## Gateway (`gateway.py`)

`Runtime` wires everything; `Runtime.reset()` rebuilds it (mock reset endpoint) and runs the mock warm-up (`RATCHET_MOCK_WARMUP_DAYS`) if set. Mock mode imports from `sim/` and keeps three seeded streams (`tickets`, `research`, `mixed`). Live mode uses `Ledger(auto_day=True)` and `ReleasePolicy.from_env()`. `rt.tick()` runs `gate.tick()` under `gate_lock`.

`Runtime(settings, transports={"teacher": ..., "student": ...}, sleep=..., clock=...)` puts an `httpx.BaseTransport` under the live providers (the student's also carries adapter reloads), so tests (and later items) run live mode offline, and `sleep` replaces the teacher's waits between retries; any other transport key raises, and mock mode ignores both. `clock` drives the student breakers, `rt.reloader` (`live.AdapterReloader`, live mode with a student endpoint only, else None; tests wait for it with `join(timeout)`) and the mock fleet's demo outage. With no student endpoint, the hosted fallback student gets its own client (on the teacher's transport) with the student's timeouts and no retries. The other live entry points take their network and commands the same way, as default arguments that production code never passes: `OpenAICompatProvider(transport=, sleep=, clock=, rng=)`, `live.load_adapter(client=)`, `NebiusTrainer(run=, http=)`, `live.reload_adapters(http=)`, `live.main(argv, provider_factory=, run=, http=)`, `scout.agents.http_caller(client=)` and the Scout CLI's `main(argv, client)`. `tests/test_live.py` uses them; keep new live code testable the same way.

Pages: the React build in `web/dist` (source in `/web`, see [web/CLAUDE.md](../web/CLAUDE.md)) at `/`, `/scout` and `/dashboard`. The hand-written static pages (`web/landing.html`, `web/scout.html`, `dashboard.html`) are served at `/lite*` and as a fallback when the build is missing. Both are package data (`pyproject.toml`).

**OpenAI compatibility (`openai_compat.py`, [ADR 0012](../docs/adr/0012-openai-request-validation-and-error-envelope.md)).** Standard library only. `chat` is `async`: it checks the key, reads the body, and runs `_complete` (parse, validate, normalise, quota, cascade, response) in one worker thread with `anyio.to_thread.run_sync`, on the default pool sync routes use.

- Validate everything Ratchet reads or will forward, with an OpenAI `code` and a JSON-path `param`; keep unknown top-level keys in `ChatRequest.extra` (never refuse them). `parse_body` also refuses a string with an unpaired UTF-16 surrogate (it cannot be written as UTF-8, so the teacher call or the ledger would crash later). New request fields go into `ChatRequest` and `validate_chat_request` together, with a case in `VALIDATION_CASES` (`tests/test_openai_compat.py`) and a row in API.md's validation table.
- Message keys outside `ROLE_KEYS` (by role; `MESSAGE_KEYS` otherwise) and unknown keys inside tool calls are dropped by `normalize_messages`, so they are neither forwarded nor stored. A new message field Ratchet forwards goes into `ROLE_KEYS`, is checked and counted toward `max_input_chars` in `_message`, and is redacted in `redact_messages` if it can carry user text.
- `normalize_messages` must return a plain message as the **same object** (and the same list when nothing changes), or example hashes, golden splits, mock lookups and the reference simulation move. `test_normalize_is_identity_on_the_mock_world` checks every mock request.
- Errors on `/v1/*` and `/openai/*` use the envelope (`APIError`, `_http_error`, `_validation_error`, `_unhandled_error`); every other route keeps `{"detail": ...}`, which the dashboard reads. Raise `APIError` for a client mistake; a plain `HTTPException` is mapped by status (`openai_compat.STATUS_TYPES`). Never let a client mistake reach `_unhandled_error`: that is a 500 and a `gateway_error` event.
- A `KeyError` from the cascade is a 400 only in mock mode (`sim.world.lookup` does not know the request); in live mode it is a bug (500).
- A teacher failure is never a plain `HTTPException`: `_map_upstream` turns its `UpstreamError` into an `APIError` with a code (`context_length_exceeded` and the teacher's other 400 codes, `upstream_rate_limited`, `upstream_auth_error`, `upstream_not_found`, `teacher_timeout`, `teacher_unavailable`) and `X-Should-Retry: false` where a retry cannot help. The daily quota's 429 is `insufficient_quota` / `daily_quota_exceeded` with `Retry-After` and `X-RateLimit-Reset-Requests` (`_duration`).
- A content part listed in `RATCHET_TEACHER_MODALITIES` sends the request to `Ratchet.teacher_only` (the pinned path: no student, nothing stored).

Demo controls are shared by every visitor: `day` and `reset` take `_demo_lock` without blocking (busy → 409), and `reset` has a 30 s cooldown (429). `break-next` and `student-outage` (the mock production endpoint down for 5 to 300 s, `MockFleet.student_outage`) only set fault state and write a `fault` event. Every demo control calls `_mock_only()` first (INV-8).

`/ratchet/stats` only gains keys: every key the dashboard reads is listed with its JSON type in `tests/fixtures/stats_keys.json` and checked by `test_stats_contract`. Add new keys there too; never remove or retype one. The other client-facing behaviour (status codes, task selection, sampling parameters, the confidence formula, the threshold boundary, audits, cost to the token) is pinned in `tests/test_contract.py`.

## Scout (`scout/`)

Multi-agent research demo ([docs/SCOUT.md](../docs/SCOUT.md)).

| File | What |
|---|---|
| `agents.py` | `Scout.run(question, emit)`: planner → searcher → triage → reader → writer; emits `start`/`step`/`done` events. `Call` (what an agent learns back), `http_caller` (any gateway over HTTP) |
| `prompts.py` | System prompts, message builders (`plan_messages`, `triage_messages`, `read_messages`, `write_messages`), `EXAMPLE_QUESTIONS` |
| `search.py` | `TavilySearch` (live), `MockSearch` (simulated results from `sim/research.py`) |
| `guard.py` | `Guard`: daily budget, daily run cap, per-IP hourly limit; in-memory, resets at 00:00 UTC |
| `__main__.py` | CLI: `python -m ratchet.scout "<question>" --base-url ... [--api-key] [--max-results]` |

Rules:

- **`prompts.py` is shared with the simulator.** `sim/research.py` builds its mock requests with the same message builders, and the mock teacher recognises the planner and writer by their system prompts. The mock models look a request up by its exact user text, so if you change a prompt or a builder, mock lookups still work only because both sides call the same function. Never build Scout messages anywhere else.
- **Threads.** Triage and reader calls run in a `ThreadPoolExecutor` (6 workers). `Scout.run` serialises `emit` with its own lock; the ledger has its lock. `Ratchet.rng` is shared across threads, so the order of random draws (canary pick, audits) is not reproducible inside a Scout run. Tests on Scout must not depend on exact routing.
- **Budget.** The gateway runs `_scout_precheck` (which calls `Guard.admit`) before every run and `Guard.add_spend` in a `finally` after it, summing each finished step's `cost_usd`. Keep both on every run path in live mode.
- `/scout/stream` always returns HTTP 200 and reports failures as an `error` event; `/scout/run` uses status codes (400/429/502/503).
