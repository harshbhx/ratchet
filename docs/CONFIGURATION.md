# Configuration

All settings come from environment variables. Defaults live in `ratchet/config.py` (`Settings`), `ratchet/live.py`, the `deploy/` scripts and `deploy/vm/`. Model IDs and prices follow the Nebius Token Factory catalog as of October 2026. Check them on day 1 with `python scripts/day1_check.py` (see [DAY1.md](DAY1.md)).

## Gateway and cascade (`ratchet/config.py`)

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_MODE` | `mock` | `mock`: offline demo with mock models and an in-memory ledger. `live`: real Token Factory teacher and a SQLite file |
| `RATCHET_DB` | `ratchet.db` | SQLite ledger path (live mode only; mock mode always uses `:memory:`) |
| `RATCHET_TEACHER_BASE` | `https://api.tokenfactory.nebius.com/v1` | OpenAI-compatible base URL for the teacher, judge and fallback student (and the fallback teacher, unless `RATCHET_TEACHER_FALLBACK_BASE` is set) |
| `NEBIUS_API_KEY` | empty | Token Factory API key (sent as `Authorization: Bearer …`) |
| `RATCHET_TEACHER_MODEL` | `nvidia/Nemotron-3-Ultra-550b-a55b` | Teacher model. Must be in the license registry and distillable, or startup fails with `LicenseError` |
| `RATCHET_JUDGE_MODEL` | `nvidia/nemotron-3-super-120b-a12b` | Judge for free-text golden evaluation (`LiveEvaluator`) |
| `RATCHET_FALLBACK_STUDENT` | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` | Hosted, untuned model used for student calls when `RATCHET_STUDENT_BASE` is empty |
| `RATCHET_STUDENT_BASE` | empty | Base URL of your vLLM endpoint, e.g. `https://<endpoint>/v1` |
| `RATCHET_STUDENT_KEY` | empty | Token for the student endpoint |
| `RATCHET_STUDENT_BASE_MODEL` | `nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16` | Base model that adapters are trained on (also used by the deploy scripts) |
| `RATCHET_CONFIDENCE_THRESHOLD` | `0.80` | Fallback threshold, used only when a version has no calibrated `threshold` in its eval (and for the hosted, untuned fallback student) |
| `RATCHET_SAMPLES` | `3` | Student samples per request (self-consistency) and per golden row. A request for more choices (`n`) draws `n` samples, but its confidence is still scored on the first `RATCHET_SAMPLES` of them |
| `RATCHET_AUDIT_RATE` | `0.05` | Probability that a student-served answer is also sent to the teacher. Mock mode raises it to at least `0.10`. A task's `policy.audit_rate` replaces it for that task ([risk-aware routing](#risk-aware-routing)) |
| `RATCHET_GOLDEN_FRACTION` | `0.10` | Share of new examples assigned to the golden (held-out) split |
| `RATCHET_MIN_TASK_GOLDEN` | `20` | Calibration rows (the even half of a task's golden rows) a task needs before its own threshold may go below the version's global one ([per-task calibration](#per-task-calibration), FR-14.1). With fewer it gets the higher of the two and `calibrated: false`. A whole number, 1 or more; anything else stops startup (gateway, simulator, `python -m ratchet.live`) |
| `RATCHET_PER_TASK_CALIBRATION` | `1` | `0` switches [per-task calibration](#per-task-calibration) off (FR-14.1, ADR 0040): Night School writes no per-task records, so each new version keeps one pooled threshold and the golden gate checks only the pooled numbers, as in v0.4; only a task with critical classes still gets its record (its own threshold, checked by the per-task gate and the [critical gate](#risk-aware-routing)), and the eval's `other_tasks` gives the other tasks' held-out rows and how many the pooled threshold serves, so disabling the critical task leaves the version the coverage of its other tasks. Versions already scored with records keep them until a night re-scores them. Any other value leaves it on. The reference workloads (`make reference`) run with it off, so they reproduce v0.4.0 exactly |
| `RATCHET_TEACHER_REASONING_TOKENS` | `300` | Extra output tokens assumed for the teacher in baseline estimates (and added by the mock teacher) |

## Request limits and OpenAI compatibility

Read in `ratchet/config.py`; used by `ratchet/gateway.py` and `ratchet/openai_compat.py`. Every check runs before any model call, and a refused request is neither logged nor counted against a quota ([API.md](API.md#request-validation), [ADR 0012](adr/0012-openai-request-validation-and-error-envelope.md)).

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_MAX_BODY_BYTES` | `2097152` | Largest request body, in bytes (2 MiB), on every `POST`, `PUT` and `PATCH`. A larger body gets **413** `request_too_large`: at once when `Content-Length` says so, or as soon as a chunked body passes the limit. `0` = no limit |
| `RATCHET_MAX_MESSAGES` | `256` | Most messages in one `/v1/chat/completions` request; more is a 400 with `param: messages`. `0` = no limit |
| `RATCHET_MAX_INPUT_CHARS` | `120000` | Most characters of message text in one request: content strings, text and refusal parts, `name`, `refusal`, `tool_call_id`, and tool call ids, function names and arguments, added up (everything forwarded to the teacher except non-text parts, which only `RATCHET_MAX_BODY_BYTES` bounds); more is a 400 with code `context_length_exceeded`. About 30,000 tokens of English. `0` = no limit |
| `RATCHET_MAX_N` | `4` | Most choices (`n`) one request may ask for, 1 to 128; more is a 400 `unsupported_value` with `param: n` ([API.md](API.md#choices-and-logprobs), [ADR 0020](adr/0020-n-choices-and-token-logprobs.md)). Each choice past `RATCHET_SAMPLES` is one more student sample on every such request, and an escalated one asks the teacher for `n` choices, which it bills as `n` answers. `1` restores the v0.4 behaviour of one choice per request. A value outside 1 to 128 stops the gateway at startup with `ValueError` |
| `RATCHET_MERGE_SYSTEM` | `0` | `1`: merge consecutive leading `system` messages (after `developer` becomes `system`) into one, joined by a blank line, for models whose chat template takes a single system message. It changes the stored form of such requests, so set it before collecting training data, not halfway |
| `RATCHET_TEACHER_MODALITIES` | `text` | Content part types the teacher accepts besides text, comma-separated, e.g. `text,image_url` (also `input_audio`, `file`). A request with such a part goes to the teacher only (route `ineligible:content_type`, [risk-aware routing](#risk-aware-routing)): no student call, no audit, nothing stored as a training example. Any other non-text part is a 400 `unsupported_content_type`. Set it only when the teacher model reads those parts; otherwise the teacher's refusal comes back as a 400 with its own message ([API.md](API.md#errors)) |

### Other OpenAI endpoints

`/v1/completions` and `/v1/responses` run the chat path, so every setting above and below applies to them as to `/v1/chat/completions` (a completion's prompt counts against `RATCHET_MAX_INPUT_CHARS` on its own). `/v1/embeddings` is a pass-through to one model ([API.md](API.md#post-v1embeddings), [ADR 0050](adr/0050-adjacent-openai-endpoints.md)):

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_EMBEDDINGS_BASE` | (empty) | OpenAI-compatible base URL that `/v1/embeddings` calls. Empty: `RATCHET_TEACHER_BASE` (Token Factory). `off` (any case): the route answers 404 `unsupported_endpoint` and `/v1/models` does not list the embeddings model. Anything that is not an `http(s)` URL stops startup. Mock mode embeds locally unless it is `off` |
| `RATCHET_EMBEDDINGS_MODEL` | `Qwen/Qwen3-Embedding-8B` | The one model `/v1/embeddings` asks for, whatever `model` a client sends; listed in `/v1/models`, and the id `RATCHET_MODEL_POLICY=strict` accepts there besides `ratchet`. **Unverified until `scripts/day1_check.py` passes** (INV-9), which checks it. Priced from `EMBEDDING_PRICES` ([Prices](#prices-default_prices)); a model without a price there is priced as the teacher. An empty id, one with spaces around it, a Ratchet model id or one that is also a model alias stops startup |
| `RATCHET_EMBEDDINGS_KEY` | (empty) | API key for `RATCHET_EMBEDDINGS_BASE`. Empty: `NEBIUS_API_KEY` when the base is the teacher's (`RATCHET_TEACHER_BASE`), and no key at all for any other base, so the Token Factory key is never sent to another provider. Set it for a provider that needs one; a local server without auth needs none |

Each answered embeddings call is one request row (task `embeddings`, served_by `upstream`, route `passthrough`, nothing stored) and counts against `RATCHET_API_KEY_DAILY_REQUESTS`. The task name `embeddings` is reserved for these rows: a task spec (tasks file, admin API) or a `RATCHET_MODEL_ALIASES` entry that names it is refused, and a chat request that names it is a 400 `reserved_task`. Its calls get the teacher's timeouts and retries (`RATCHET_TEACHER_TIMEOUT_S`, `RATCHET_TEACHER_RETRIES`, `RATCHET_TEACHER_DEADLINE_S`) and go through no breaker. `/v1/embeddings` also takes `RATCHET_MAX_INPUT_CHARS` (a token id counts as 4 characters) and at most 2,048 inputs.

## Generation parameters and token caps

Read in `ratchet/config.py` (`Settings.gen_policy()`); used by `ratchet/router.py`, the golden eval (`ratchet/nightschool/evaluate.py`, `sim/mock_models.py`) and `ratchet/providers.py` ([API.md](API.md#generation-parameters), [ADR 0017](adr/0017-generation-parameters-by-role.md)). A bad value (a field that cannot be forwarded, a name that is not an identifier, a negative number) stops the gateway, `python -m ratchet.live night` and the simulator at startup with `ValueError`.

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_TEACHER_PARAMS` | empty (every standard field but `tools`, `tool_choice` and `parallel_tool_calls`) | Request fields the teacher gets when its answer is served (escalation, cold start, pinned, teacher-only), comma-separated, in any case: `max_tokens` (also for `max_completion_tokens`), `temperature`, `top_p`, `stop`, `seed`, `presence_penalty`, `frequency_penalty`, `logit_bias`, `response_format`, `tools`, `tool_choice`, `parallel_tool_calls`, `logprobs`, `top_logprobs`, `reasoning_effort`. Empty means the default: the gateway does not return tool calls yet, so those fields are left out (an operator who adds `tools` gets a 502 `tool_calls_unsupported` whenever the teacher calls one). `logprobs` and `top_logprobs` reach the teacher only when a client sends them, and its token logprobs come back in the response (v0.5, FR-1.14); leave them out if the teacher refuses `logprobs` (`scripts/day1_check.py` reports whether it returns them), and teacher-served choices then carry `logprobs.content: null`. `none` forwards nothing; `all` stands for the whole list, and `all,top_k` adds a vendor field the client sends (otherwise unknown fields are never forwarded). Startup stops on `model`, `messages`, `n`, `stream`, `stream_options`, `user`, `safety_identifier`, `store`, `metadata`, `service_tier`, `functions` or `function_call`, on a name that is not an identifier, and on a near miss of a standard field (`max_token`, `stops`), which is a typo; any other name is taken as a vendor field. Audits and arbitrations take the same fields, minus `top_p`, `seed`, the penalties, `logit_bias` and logprobs, at temperature 0. A served answer written under a client's `logit_bias`, a non-zero penalty, a temperature above 1 or a vendor field is not stored |
| `RATCHET_STUDENT_PARAMS` | empty (`max_tokens,stop,seed,response_format,logit_bias,top_logprobs`) | Request fields the student gets, same syntax. Its `n` (`RATCHET_SAMPLES`), temperature (0.7) and logprobs are fixed by the cascade whatever this says, so its confidence stays calibrated. Remove `logit_bias` when clients send OpenAI token ids, which mean other tokens to Nemotron. Add `tools` and `tool_choice` only when the student's vLLM runs with `--enable-auto-tool-choice` and a `--tool-call-parser` (`deploy/student_endpoint.sh` does not); without them vLLM answers 400 to every request with tools, and the request escalates. A golden row the student refuses this way scores as not served in the nightly eval |
| `RATCHET_TEACHER_MAX_TOKENS` | `8192` | Most output tokens per teacher call (reasoning included, as Token Factory counts them). A client's larger `max_tokens` is capped to this; with none, this is sent. `0` = no cap |
| `RATCHET_STUDENT_MAX_TOKENS` | `1024` | Most output tokens per student sample, the same way. `0` = no cap |
| `RATCHET_TEACHER_REASONING_HEADROOM` | `0` | Tokens added to a client's `max_tokens` for the teacher (still at most `RATCHET_TEACHER_MAX_TOKENS`), for reasoning it writes before the answer: without it a reasoning teacher can spend a tight limit on its reasoning and be cut off. When the teacher gets more than the client asked for, a visible answer longer than the client's `max_tokens` (about 4 characters a token) is returned with `finish_reason: "length"` and not stored. Set it near `RATCHET_TEACHER_REASONING_TOKENS` for a reasoning teacher whose clients send small limits |

## Upstream timeouts and retries

Read in `ratchet/config.py`; used by `ratchet/providers.py` and `ratchet/resilience.py` ([ADR 0014](adr/0014-upstream-failure-semantics.md)). Connect, write and pool timeouts are fixed at 5, 10 and 5 seconds (the student's connect timeout has its own variable). A bad value (zero, negative, not a number) stops live mode at startup with `ValueError`.

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_TEACHER_TIMEOUT_S` | `120` | Read timeout for teacher and judge calls on Token Factory, in seconds: how long the model may take to start answering |
| `RATCHET_TEACHER_RETRIES` | `2` | Extra attempts for a failed teacher or judge call (`0` = no retries). Retried: connection failures and HTTP 408, 409, 429, 500, 502, 503, 504, with full-jitter backoff (0.5 s base, 8 s cap) or the server's `Retry-After`. A read timeout is retried once at most, because the tokens may already be billed; any other 4xx, and a 2xx body that cannot be read, never. A server wait over 8 s is not waited for: the 429 goes back to the client with it |
| `RATCHET_TEACHER_DEADLINE_S` | `180` | Upper bound on one teacher call, attempts and waits together, in seconds. A retry whose wait would end past it is not started, and later attempts get only the time that is left as their timeout |
| `RATCHET_STUDENT_TIMEOUT_S` | `15` | Read timeout for student calls (the vLLM endpoint, or the hosted fallback when `RATCHET_STUDENT_BASE` is empty), in seconds. On the request path a student call is never retried: a failure escalates to the teacher at once. Night School's golden eval retries it like the teacher, since no teacher stands behind it there |
| `RATCHET_STUDENT_CONNECT_TIMEOUT_S` | `2` | Connect timeout for student calls, in seconds, so an endpoint that is down costs a request two seconds rather than the teacher's five |
| `RATCHET_STUDENT_BREAKER_FAILURES` | `5` | Student circuit breaker: endpoint failures in a row (a timeout, a connection error, an unreadable or 5xx answer, 408, 429; another 4xx is the endpoint answering and resets the count) after which student calls are skipped and requests go straight to the teacher (route `student_breaker`), so they stop waiting on a dead endpoint. Live and canary on one endpoint share a breaker; shadow calls have their own. `0` = no breaker ([ARCHITECTURE.md](ARCHITECTURE.md#student-circuit-breaker), [ADR 0015](adr/0015-student-circuit-breaker-and-adapter-reload.md)) |
| `RATCHET_STUDENT_BREAKER_COOLDOWN_S` | `30` | Seconds an open student breaker waits before one request probes the endpoint again. A failed probe doubles the wait, up to 300 s (or this value, if larger); a successful probe closes the breaker and resets it. It may be shorter than the student's timeouts: another request probes only once the probe has been out for the longer of the wait and the student call's longest time (connect, write, read and pool timeouts plus 5 s: 2 + 10 + `RATCHET_STUDENT_TIMEOUT_S` + 5 + 5 = 37 s by default), and a probe that fails after that still reopens the breaker and doubles the wait |

## When the teacher is down

Read in `ratchet/config.py`; used by `ratchet/router.py` and `ratchet/gateway.py` ([ADR 0019](adr/0019-teacher-breaker-degraded-mode-and-fallback-teacher.md), [RUNBOOK.md](RUNBOOK.md#teacher-api-down-token-factory-errors-or-timeouts)). Every teacher call goes through a circuit breaker; the other two features are off by default. On an escalation the order is: the teacher, then the fallback teacher (if set), then the student's own answer (with `serve_student`), then the error.

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_TEACHER_BREAKER_FAILURES` | `5` | Teacher circuit breaker: failed teacher calls in a row, each counted after its retries (a timeout, a connection error, an unreadable or 5xx answer, 408, 429; a 400 such as `context_length_exceeded`, a 401 or a tool-call answer is the teacher answering and resets the count), after which the teacher is not called. While it is open, a request that needs the teacher's answer gets **503** `teacher_circuit_open` at once, with the time until the next probe as `Retry-After` (1 to 60 s), and audits and arbitrations are skipped. The fallback teacher has a breaker of its own with the same settings. `0` = no breaker. A negative or non-integer value stops the gateway at startup |
| `RATCHET_TEACHER_BREAKER_COOLDOWN_S` | `30` | Seconds an open teacher breaker waits before one request probes the teacher again. A failed probe doubles the wait, up to 300 s (or this value, if larger); a successful one closes the breaker. Must be a positive number |
| `RATCHET_ON_TEACHER_FAILURE` | `error` | What an escalated request gets when the teacher (and the fallback teacher, if set) is unavailable. `error`: the mapped error (502, 503 or 504). `serve_student`: the student's own answer, below its threshold, when it gave at least one valid sample, with route `degraded`, `ratchet.degraded: "teacher_unavailable"` and `X-Ratchet-Degraded: teacher_unavailable`; never stored, audited or shadowed, so it never counts toward a release. Never for a 400, 413 or 422 about the request, a tool-call answer, a request with `tools`, a pinned or teacher-only request, a cold start, or a request whose student call failed. Case and spaces are ignored, empty means `error`, anything else stops the gateway at startup |
| `RATCHET_TEACHER_FALLBACK_MODEL` | empty | A second teacher that answers a call whose answer is served (escalation, cold start, pinned, teacher-only) when the teacher is unavailable: its retries failed or its breaker is open. Never for audits or arbitrations, a 400 about the request or a tool-call answer. Must be in the license registry and distillable, or startup fails with `LicenseError` (invariant 1). Its answers are served with route `fallback_teacher` and its own model id, priced with its own price (`Settings.price`). When it fails too, the client gets the teacher's error. Empty = none |
| `RATCHET_TEACHER_FALLBACK_BASE` | empty (`RATCHET_TEACHER_BASE`) | OpenAI-compatible base URL for the fallback teacher. It always uses `NEBIUS_API_KEY` and the teacher's timeouts and retries. Empty or equal to `RATCHET_TEACHER_BASE`: the teacher's client is used |
| `RATCHET_STORE_FALLBACK_TEACHER` | `0` | `1`: store the fallback teacher's answers as training examples (under the usual rules), each with the fallback model and its own license in its provenance and the BOM. `0`: serve them, store nothing, so golden labels stay the teacher's |

## Privacy and security

Read in `ratchet/config.py`; used by `ratchet/router.py`, `ratchet/auth.py`, `ratchet/bom.py` and `ratchet/gateway.py`. Background in [ARCHITECTURE.md](ARCHITECTURE.md#pii-redaction), [API.md](API.md#authentication) and [THREAT_MODEL.md](THREAT_MODEL.md).

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_REDACT_PII` | `1` | Replace emails, phone numbers, card numbers (Luhn-checked), IBANs (mod-97), Aadhaar (Verhoeff), US SSNs, Indian PANs, IPv4 addresses and API secrets with `[LABEL]` placeholders before a request and the teacher's answer are stored as a training example. The teacher still receives the original. `0` stores examples verbatim. Names and street addresses are not detected |
| `RATCHET_API_KEYS` | empty | `name:key,name:key`. When set, `/v1/*` and `/openai/*` need `Authorization: Bearer <key>`, `X-API-Key: <key>` or `api-key: <key>`; anything else gets 401. Names: letters, digits, `_`, `.`, `-` (up to 64). Keys: at least 16 characters; generate with `openssl rand -hex 24`. A bare key without `name:` is named `keyN`. The ledger stores the name per request, never the key. A malformed value stops the gateway at startup |
| `RATCHET_API_KEY_DAILY_REQUESTS` | `0` | Requests per key per UTC day (chat completions, responses, embeddings, and completions at one per prompt); past it a 429 `insufficient_quota` (code `daily_quota_exceeded`) with `Retry-After` until 00:00 UTC and `X-Should-Retry: false` ([API.md](API.md#authentication)). `0` = unlimited. Counted from the ledger, so it survives restarts in live mode |
| `RATCHET_ADMIN_KEYS` | empty | Admin keys for management writes (`PUT` and `DELETE /ratchet/tasks/{name}`, `POST /ratchet/tasks/validate`; [API.md](API.md#tasks)), in the `RATCHET_API_KEYS` syntax: `name:key,name:key`, keys at least 16 characters. Sent as `Authorization: Bearer <key>` or `X-API-Key`; a missing or wrong key gets 401, an API key 403. The ledger and the event log record the name, never the key. A key may not be in both variables (startup error). Empty: the admin API is off in live mode (403 `admin API disabled: set RATCHET_ADMIN_KEYS`) and open in mock mode (the admin is `mock-admin`). Not the Caddy guard's `RATCHET_ADMIN_TOKEN` |
| `RATCHET_BOM_SIGNING_KEY` | empty | Ed25519 private key as the base64 of its 32-byte seed. Signs each adapter's bill of materials. Generate with `python -m ratchet.bom keygen`, which also prints the public key to publish. Needs the `sign` extra (`pip install "ratchet-distill[sign]"`, included in the gateway image). An invalid value stops the gateway at startup. Empty = BOMs are stored with a SHA-256 but unsigned |

Keep `RATCHET_API_KEYS`, `RATCHET_ADMIN_KEYS` and `RATCHET_BOM_SIGNING_KEY` out of git, like every other key. Rotating the signing key does not invalidate old BOMs: each one embeds the public key and key id it was signed with, so keep a list of the public keys you have published.

## Scout, the multi-agent demo

Read in `ratchet/config.py`; used by `ratchet/gateway.py` and `ratchet/scout/guard.py`. Details in [SCOUT.md](SCOUT.md).

| Variable | Default | Meaning |
|---|---|---|
| `TAVILY_API_KEY` | empty | Tavily Search key for Scout's searcher. In live mode without it, Scout returns 503. The Scout CLI (`python -m ratchet.scout`) also reads it; without it the CLI uses simulated results |
| `RATCHET_API_KEY` | `unused` | Scout CLI only (`python -m ratchet.scout`): the gateway API key it sends as `Authorization: Bearer`, for a gateway with `RATCHET_API_KEYS` set. `--api-key` overrides it. The gateway itself never reads this variable |
| `RATCHET_SCOUT_DAILY_BUDGET_USD` | `3.00` | Live mode: most Ratchet spend on Scout runs per UTC day. Once reached, new runs get 429. Mock mode has no budget |
| `RATCHET_SCOUT_RUNS_PER_HOUR` | `6` | Runs per client IP per rolling hour. Mock mode allows 10 × this value |
| `RATCHET_SCOUT_MAX_RUNS_PER_DAY` | `30` | Live mode: most runs per UTC day, for all clients together. Caps Tavily credits (about 3 searches per run, so 90 credits a day). Mock mode has no cap |
| `RATCHET_SCOUT_MAX_RESULTS` | `4` | Tavily results per query. With 3 queries, at most 12 results are triaged |
| `RATCHET_TRUST_PROXY` | `0` | `1`: take the client IP for the per-IP limit from the first `X-Forwarded-For` entry. Only set it when a proxy you control overwrites that header; otherwise clients can fake their IP. Not needed on the demo VM, where uvicorn runs with `--proxy-headers` |

The budget counts the `cost_usd` of every Scout step, including audits and shadow calls. It does not cover `/v1/chat/completions`. Counters are in memory and reset at 00:00 UTC and on restart.

## Mock-mode demo

Read in `ratchet/config.py`. Ignored in live mode.

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_MOCK_WARMUP_DAYS` | `0` | Simulated days of `mixed` traffic, each followed by a night, run at startup and on every demo reset, so the demo opens with a trained student. `4` takes about 2 s and leaves a live student that answers about 80–90% of Scout's LLM calls (simulation). Writes a `warmup` event |
| `RATCHET_MOCK_WARMUP_REQUESTS` | `1500` | Requests per warm-up day |
| `RATCHET_MOCK_PACE` | `0.25` | `/scout/stream` waits this share of each call's simulated latency (mock Ultra 2.4 s → 0.6 s), so the page shows agents working. `0` = no waiting. `/scout/run` never waits |
| `RATCHET_MOCK_STRICT` | `0` | `1`: the mock models answer only the demo's own requests (synthetic tickets and Scout's research calls), and anything else gets 400 `mock mode only answers demo requests`, as before the generic mock world. `0`: any other request gets a deterministic answer from the generic world (`sim/generic.py`, [ADR 0041](adr/0041-generic-mock-world.md)): free text, an instance of its `response_format` schema, or a tool call to the mock teacher. Night School's mock trainer and golden eval read stored rows either way |
| `RATCHET_MOCK_ENDPOINT_HOURS` | `24` | Hours a day the simulated student endpoint runs, 0 to 24: each simulated day (the demo, the warm-up, `python -m sim.simulate`) records one endpoint interval of this length from the start of the day, on the synthetic clock, for the cost report ([Cost accounting](#cost-accounting)). `0` = no endpoint (as with the hosted fallback). Anything outside 0 to 24 stops startup. Simulation assumption, not a measurement |

## Demo VM: Caddy (`deploy/vm/`)

Read only by Docker Compose and Caddy on the demo VM ([DEPLOY_NEBIUS_VM.md](DEPLOY_NEBIUS_VM.md)), from `deploy/vm/.env`. The gateway does not read them.

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_DOMAIN` | required | Site name Caddy serves and gets a certificate for, e.g. `ratchet.dualmindlab.tech` or `<public-ip>.sslip.io`. First boot fills in the sslip.io name if it is still the placeholder |
| `RATCHET_API_GUARD` | `open` | `open`: every path is public. `guarded`: `/v1/*`, `/openai/*` and `/ratchet/demo/*` return 403 unless the request has a matching `X-Ratchet-Admin-Token` header. Pages, `/scout*`, `/ratchet/stats` and `/ratchet/bom` stay public. In mock mode, `guarded` also disables the dashboard's demo buttons |
| `RATCHET_ADMIN_TOKEN` | empty | Token for `guarded`. Hex, at least 32 characters (`openssl rand -hex 32`). Shorter or empty blocks the guarded paths for everyone. Caddy strips the header before it reaches the gateway |

`deploy/vm/create_vm.sh` and `deploy/vm/backup.sh` have their own settings (VM name, platform, preset, disk size, SSH user, repo URL, backup folder and retention). They are listed at the top of each script and in [DEPLOY_NEBIUS_VM.md](DEPLOY_NEBIUS_VM.md).

## Live Night School (`ratchet/live.py`)

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_DATA_DIR` | `data` | Local folder for `night/train.jsonl`, synced to the bucket |
| `RATCHET_SYNC_CMD` | empty | Command that uploads `RATCHET_DATA_DIR` to the bucket, e.g. `aws s3 sync ./data s3://my-bucket --endpoint-url https://storage.eu-north1.nebius.cloud` (skipped if empty) |
| `RATCHET_TRAIN_CMD` | `bash deploy/train_job.sh {version}` | Command that runs training and **waits** for it. `{version}` and `{data}` are substituted |
| `RATCHET_ADAPTER_ROOT` | `/data/adapters` | Adapter folder as the vLLM endpoint sees it (`lora_path` = `<root>/<version>`). Also read by the gateway in live mode, which reloads an adapter from there when vLLM answers 404 for it |

Commands are split with `shlex.split` and run without a shell, so pipes and `&&` do not work. Wrap them in a script.

## Deploy scripts (`deploy/`)

| Variable | Used by | Default | Meaning |
|---|---|---|---|
| `RATCHET_BUCKET_ID` | both | required | Nebius Object Storage bucket ID, mounted at `/data` |
| `RATCHET_TRAIN_IMAGE` | `train_job.sh` | required | Training image built from `deploy/Dockerfile.train` |
| `RATCHET_TRAIN_PLATFORM` | `train_job.sh` | `gpu-l40s-a` | Nebius GPU platform |
| `RATCHET_TRAIN_PRESET` | `train_job.sh` | `1gpu-8vcpu-32gb` | Nebius preset |
| `RATCHET_BUCKET_S3` | `train_job.sh` | empty | `s3://bucket[/prefix]`. When set, the script polls for `adapters/<version>/adapter_model.safetensors` (every 30 s, up to 2 h) so it can be used as `RATCHET_TRAIN_CMD` |
| `RATCHET_S3_ENDPOINT` | `train_job.sh` | empty | S3 endpoint URL for Nebius Object Storage |
| `RATCHET_STUDENT_KEY` | `student_endpoint.sh` | required (start) | Token clients must send to the endpoint |
| `RATCHET_ENDPOINT_NAME` | `student_endpoint.sh` | `ratchet-student` | Name of the Serverless Endpoint that `start` creates and `stop` stops |
| `RATCHET_PYTHON` | `student_endpoint.sh` | `python` | Python that runs `python -m ratchet.cost endpoint up\|down` after `start` and `stop` to record the endpoint's uptime in the ledger at `RATCHET_DB` (best effort: when it fails, the deploy still succeeds and prints the command to run where the ledger is) |
| `RATCHET_SERVE_PLATFORM` | `student_endpoint.sh` | `gpu-l40s-a` | GPU platform for vLLM |
| `RATCHET_SERVE_PRESET` | `student_endpoint.sh` | `1gpu-8vcpu-32gb` | Preset for vLLM |
| `RATCHET_STUDENT_BASE_MODEL` | both | `nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16` | Base model |

## Prices (`DEFAULT_PRICES`)

USD per 1M tokens (input / output). These are Token Factory list prices as of October 2026; verify them in the console.

| Key | Input | Output | Note |
|---|---|---|---|
| `nvidia/Nemotron-3-Ultra-550b-a55b` | 1.00 | 3.00 | Teacher |
| `nvidia/nemotron-3-super-120b-a12b` | 0.30 | 0.90 | Judge |
| `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` | 0.06 | 0.24 | Fallback student |
| `nvidia/Nemotron-3_5-Lightning` | 0.06 | 0.24 | |
| `student` | 0.06 | 0.24 | Per-token **proxy** for the self-hosted student; real cost is GPU-hours ([Cost accounting](#cost-accounting)) |

Embeddings models (`EMBEDDING_PRICES`, kept apart because they are never teachers and write no training data; merged into `Settings.prices`):

| Key | Input | Output | Note |
|---|---|---|---|
| `Qwen/Qwen3-Embedding-8B` | 0.01 | 0 | `RATCHET_EMBEDDINGS_MODEL`'s default ([Other OpenAI endpoints](#other-openai-endpoints)) |

`Settings.price(model)` checks for an exact key first, then uses `student` for any name starting with `student`. Anything else gets the teacher's price (or 1.00 / 3.00). To change prices, pass `Settings(prices={...})` in code; there are no price environment variables. A fallback teacher's answers (`RATCHET_TEACHER_FALLBACK_MODEL`) are priced with its own entry (the teacher's price when it has none); the all-teacher baseline of such a request is the teacher's price for the same tokens.

## Cost accounting

Read in `ratchet/config.py` (`Settings.cost_policy()`); used by `ratchet/cost.py`, the gateway (`GET /ratchet/cost`, the `cost` block of `/ratchet/stats`, `POST /ratchet/cost/endpoint`), `python -m ratchet.cost`, `python -m ratchet.live night` and the simulator ([ARCHITECTURE.md](ARCHITECTURE.md#7-cost-accounting), [ADR 0023](adr/0023-utilization-aware-cost-accounting.md)). A bad value (negative, not a number, not a whole number of GPUs, an unknown model) stops startup with `ValueError` naming the variable. Each interval stores the rate it was recorded at, so changing a rate later prices new intervals only; the break-even and the hosted-fallback crossover use the current student rate.

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_STUDENT_COST_MODEL` | `per_token` | Which savings the cost report's `headline` (and `/ratchet/stats` `cost.model`) shows: `per_token` (the student priced per token at the hosted Nano price, as every figure since v0.1) or `gpu_hours` (the GPU-hours the student endpoint and the training jobs ran). The report always has both views, and each request's `cost_usd` stays per token in both, so history never moves. Case and spaces are ignored; empty means `per_token` |
| `RATCHET_STUDENT_GPU_USD_PER_HOUR` | `1.55` | USD per GPU-hour of the student endpoint (an L40S on-demand list price, [nebius.com/prices](https://nebius.com/prices), Oct 2026; **unverified** until the day-1 checks pass). The default for `python -m ratchet.cost endpoint up` and `POST /ratchet/cost/endpoint` when they name no price, for the simulator's intervals, and the price in the break-even |
| `RATCHET_STUDENT_GPUS` | `1` | GPUs the student endpoint runs on (`1gpu-8vcpu-32gb` in `deploy/student_endpoint.sh`). The endpoint costs this many times the per-GPU price per hour |
| `RATCHET_TRAIN_GPU_USD_PER_HOUR` | `1.55` | USD per GPU-hour of a training job (the same L40S list price, unverified). Prices the training interval `python -m ratchet.live night` records around each training run, and the simulator's |
| `RATCHET_TRAIN_GPUS` | `1` | GPUs a training job runs on (`RATCHET_TRAIN_PRESET`) |
| `RATCHET_PRICES_VERIFIED` | `0` | `1` once `scripts/day1_check.py` passed with a real key and the GPU rates above match your Nebius bill. Until then every report labels its prices "unverified list prices" (invariant 9); with `1` it says "prices confirmed by the operator". Ratchet never sets it by itself |

The simulated endpoint's hours a day are `RATCHET_MOCK_ENDPOINT_HOURS` ([Mock-mode demo](#mock-mode-demo)).

## Release policy (`ratchet/release.py`)

`ReleasePolicy` defaults. In live mode (gateway and `ratchet.live`) each field can be overridden with the variable in the second column (`ReleasePolicy.from_env()` reads `RATCHET_<FIELD>` for every field). Integers stay integers, the rest are floats. Mock mode ignores them and uses `min_audits=15`.

| Field | Variable | Default | Used in |
|---|---|---|---|
| `min_precision` | `RATCHET_MIN_PRECISION` | 0.95 | Golden gate and shadow: minimum precision of answers the candidate would serve |
| `min_coverage` | `RATCHET_MIN_COVERAGE` | 0.25 | Golden gate: minimum share of golden traffic it would serve |
| `tolerance` | `RATCHET_TOLERANCE` | 0.02 | Max allowed drop vs live (precision and coverage) in the golden gate, and coverage vs live in shadow |
| `min_shadow` | `RATCHET_MIN_SHADOW` | 100 | Would-serve shadow answers needed before a shadow decision |
| `min_audits` | `RATCHET_MIN_AUDITS` | 20 | Teacher audits needed (per stage) before a canary decision or a live alert |
| `live_floor` | `RATCHET_LIVE_FLOOR` | 0.92 | Audited precision floor; rollback when the Wilson upper bound is below it. Strictly between 0 and 1 |
| `min_task_test_n` | `RATCHET_MIN_TASK_TEST_N` | 10 | Held-out (odd-half) golden rows a task needs before the golden gate checks its own numbers ([per-task calibration](#per-task-calibration), FR-14.1). A whole number, 0 or more |
| `task_mode` | `RATCHET_TASK_MODE` | `disable` | What the golden gate does with a task that fails its own check: `disable` it on the candidate (its threshold becomes 1.01, a `task_disabled` event; the version ships for its other tasks) or `reject` the candidate. Case and surrounding spaces are ignored; empty means the default |

`ReleasePolicy` checks its values when it is built (`from_env` included), so a typo stops startup with a `ValueError` naming the variable: whole numbers for the integer fields, `min_precision`, `min_coverage` and `tolerance` from 0 to 1, `live_floor` strictly between 0 and 1, `task_mode` `disable` or `reject`.

A new `ReleasePolicy` field needs a row here: `python scripts/reqtrace.py --check` derives the variable names from the dataclass and fails on an undocumented one. The fields of the evidence features are in [Reserved settings](#reserved-settings).

Other constants:

| Constant | Where | Value |
|---|---|---|
| `CANARY_STEPS` | `release.py` | `[10, 50]` then 100 |
| `FREQUENT` | `nightschool/dataset.py` | 3: requests seen this often always go to training |
| `min_new_examples` | `run_night()` | 50 new train rows needed to train |
| `target_precision` | `cascade_eval()` / `LiveEvaluator` | 0.97 for threshold calibration (`calibration.DEFAULT_TARGET_PRECISION`); a task's `policy.target_precision` overrides it for that task |
| `floor` | `cascade_eval()` | 0.5: lowest allowed threshold |
| Confidence weights | `confidence.score()` | 0.4 × token probability + 0.6 × agreement |
| Student sampling | `router._sample` | `temperature=0.7`, `logprobs=True` |
| Teacher temperature for `n` > 1 | `providers.CHOICES_TEMPERATURE` | 0.7, when the client sets no temperature (at 0 every choice would be the same) |

## Reserved settings

The evidence features (PRD §6.16 and FR-15.5) are built one at a time. Their settings are declared already, with defaults that keep today's behaviour ([ADR 0024](adr/0024-evidence-foundations.md)), so later items only fill in logic. **Until a feature is built, any value but its default stops startup** with a `ValueError` that names the variable and the feature: a setting that does nothing yet must not look like it protects anything. The gateway, the simulator and `python -m ratchet.live` all check (`Settings.check_reserved()` in `Ratchet`, and `ReleasePolicy` when it is built). Unset, empty, or the default spelled another way (`0.950`, ` Point `) is fine. Each feature's item removes its rows from this section and documents them where they belong.

`Settings` (`config.RESERVED_SETTINGS`):

| Variable | Default | Feature that will read it |
|---|---|---|
| `RATCHET_CANARY_AUDIT_RATE` | unset (the audit rate) | Anytime-valid release gate (FR-16.1): the canary's own audit rate |
| `RATCHET_CALIBRATION` | `point` | Certified precision (FR-16.3): `point` (today's threshold rule) or `ltt` (Learn-then-Test) |
| `RATCHET_CERT_TARGET` | `0.95` | Certified precision (FR-16.3): the precision a certificate tests for |
| `RATCHET_CERT_DELTA` | `0.05` | Certified precision (FR-16.3): the certificate's error rate |
| `RATCHET_TEACHER_PROBES` | `20` | Teacher watch (FR-16.7): stored labels re-asked per night |
| `RATCHET_ADJUDICATE_MAX` | `50` | Teacher watch (FR-16.7): contested labels adjudicated per night |
| `RATCHET_TAKE_RATE` | `0.25` | Savings receipts (FR-15.5): the share of the billable lower bound that is the fee |
| `RATCHET_RECEIPT_MIN_AUDITS` | `30` | Savings receipts (FR-15.5): audits a task needs before its baseline is calibrated |
| `RATCHET_MOCK_TEACHER_REASONING_MEAN` | unset | Savings receipts (FR-15.5), mock mode only: the mock teacher's mean billed reasoning tokens (unset: the fixed `RATCHET_TEACHER_REASONING_TOKENS`) |
| `RATCHET_BATCH_API` | `operations` | Clean-room backfill (FR-16.8): the Token Factory batch API (`operations` or `openai`) |
| `RATCHET_BATCH_DISCOUNT` | `0.5` | Clean-room backfill (FR-16.8): the batch price discount (unverified, like every price) |
| `RATCHET_BACKFILL_MAX_TOKENS` | `2048` | Clean-room backfill (FR-16.8): the output cap the worst-case budget is computed from |
| `RATCHET_DB_SYNC` | `FULL` | Live readiness: `NORMAL` will switch a file ledger to the WAL journal. `FULL` is today's rollback journal |

`ReleasePolicy` (`release.RESERVED_POLICY`, read by `from_env` as `RATCHET_<FIELD>` in live mode like the fields above):

| Field | Variable | Default | Feature that will read it |
|---|---|---|---|
| `sequential` | `RATCHET_SEQUENTIAL` | 0 | Anytime-valid release gate (FR-16.1): 1 rolls a canary back by an e-process, with no minimum audit count |
| `alpha_rollback` | `RATCHET_ALPHA_ROLLBACK` | 0.05 | FR-16.1: the chance of ever rolling back a version at or above the floor |
| `promote_evidence` | `RATCHET_PROMOTE_EVIDENCE` | 0 | FR-16.1: 1 promotes a canary stage only on evidence that precision is above the floor |
| `alpha_promote` | `RATCHET_ALPHA_PROMOTE` | 0.05 | FR-16.1: the promotion test's error rate |
| `max_stage_audits` | `RATCHET_MAX_STAGE_AUDITS` | 400 | FR-16.1: audits after which an undecided canary stage is retired |
| `alert_eprocess` | `RATCHET_ALERT_EPROCESS` | 0 | Anytime-valid live alert (FR-16.2): 1 alerts on the live version by the e-process instead of Wilson's bound |
| `alpha_alert` | `RATCHET_ALPHA_ALERT` | 0.01 | FR-16.2: the live alert's error rate |
| `min_certified_precision` | `RATCHET_MIN_CERTIFIED_PRECISION` | 0.0 | Certified precision (FR-16.3): a floor on the certified lower bound (0: off) |
| `paired_gate` | `RATCHET_PAIRED_GATE` | 0 | FR-16.3: 1 compares candidate and live version on the same golden rows (McNemar) |
| `regression_alpha` | `RATCHET_REGRESSION_ALPHA` | 0.05 | FR-16.3: the paired gate's error rate |
| `max_nfr` | `RATCHET_MAX_NFR` | 1.0 | FR-16.3: the highest negative-flip rate the paired gate accepts (1: off) |
| `drift_alarm` | `RATCHET_DRIFT_ALARM` | 100 | Drift alarm (FR-16.6): the test martingale's alarm threshold (false-alarm probability at most 1/threshold per stream) |
| `drift_audit_boost` | `RATCHET_DRIFT_AUDIT_BOOST` | 1.0 | FR-16.6: the audit-rate multiplier after an alarm (1: off) |
| `drift_early_night` | `RATCHET_DRIFT_EARLY_NIGHT` | 0 | FR-16.6: 1 halves the new examples Night School needs after an alarm |
| `teacher_noise_max` | `RATCHET_TEACHER_NOISE_MAX` | 0.05 | Teacher watch (FR-16.7): the teacher disagreement rate it tolerates |
| `alpha_teacher` | `RATCHET_ALPHA_TEACHER` | 0.01 | FR-16.7: the teacher watch's error rate |

## Tasks

A task tells Ratchet when two answers "agree" and which answers are valid (`ratchet/tasks.py`, [ADR 0021](adr/0021-field-comparators-and-task-specs.md)). Its `fields` list the parts of the answer that must match, each a path and a typed comparator (`ratchet/compare.py`); anything else in the answer, such as a free-text `reply`, is ignored.

Tasks come from three places ([ADR 0022](adr/0022-task-registry.md)): the built-in ones below (`ratchet/tasks.py`), a tasks file, and the admin API ([API.md](API.md#tasks)). The name `embeddings` is reserved for the `/v1/embeddings` pass-through's request rows and cannot be a task's ([Other OpenAI endpoints](#other-openai-endpoints)). Read in `ratchet/config.py`; used by `ratchet/taskspec.py`, `ratchet/router.py` and `ratchet/gateway.py`:

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_TASKS_FILE` | empty | A file of task specs ([Adding a task](#adding-a-task)), JSON, or YAML for a `.yaml` or `.yml` file (needs PyYAML: `pip install pyyaml`, or the `yaml` extra). Read when the gateway starts (and on a mock demo reset) and by `python -m ratchet.live`. A file task may replace a built-in one. A bad file stops startup with every error and its path (`tasks[2].fields.total.abs_tol: must be a number >= 0`); a YAML file without PyYAML is an error that says so |
| `RATCHET_UNKNOWN_TASKS` | `distill` | What a request naming a task that is not registered gets. `distill` (v0.4): a task without fields made up for the name, distilled like any other. `teacher`: the teacher answers (route `unknown_task`), the student is not called and nothing is stored. `reject`: 400 `unknown_task`, listing the registered tasks the key may use. Under `teacher` and `reject` an `X-Ratchet-Task` header that is not a valid task name is a 400 `unknown_task` too (under `distill` it means `default`, as in v0.4). Case-insensitive; anything else stops startup |
| `RATCHET_MODEL_POLICY` | `lenient` | What a request whose `model` Ratchet does not know gets ([task resolution](#task-resolution), [ADR 0048](adr/0048-model-field-routing.md)). `lenient`: it is served as `ratchet` (its task from the other rules) and the response carries `X-Ratchet-Model-Alias: <the id>` (UTF-8 percent-encoded except for visible ASCII), so clients with a hard-coded model name work unchanged. `strict`: a 404 `model_not_found` ("The model 'gpt-4o' does not exist"), before anything is logged or billed, also when a header names the task. Under both, a missing or empty `model`, `ratchet`, `ratchet/<task>` and the aliases are accepted (`ratchet/<task>` for an unregistered task then follows `RATCHET_UNKNOWN_TASKS`, and `GET /v1/models/{id}` gives the same answer), and `ratchet/` with a name that is not a task name is a 404. Case-insensitive; anything else stops startup |
| `RATCHET_MODEL_ALIASES` | empty | Model ids that name a task, as `model=task` pairs: `gpt-4o-mini=ticket-triage,openai/gpt-4o=default`. For clients that send a fixed model name. An alias is 1 to 256 characters without spaces, `,` or `=`, and must not be `ratchet` or start with `ratchet/` or `ratchet:`; at most 256. The task need not be registered yet: until it is, requests for the alias follow `RATCHET_UNKNOWN_TASKS`. Aliases are listed in `GET /v1/models` (with `alias_of`). A bad entry stops startup |
| `RATCHET_AUTO_TASK` | `off` | `system_prompt`: a request no header, metadata, model, alias or match rule places gets an auto task of its own per first system message, `auto-sys-` and the first 10 hex digits of the message's SHA-256 (the name holds a digest, never the prompt). A new auto task compares whole answers like `default`, is never pinned, and is registered and stored in the ledger (so a restart and Night School find it) once its first request has passed the quota. A request without a system message gets `default`. An auto task is a registered task, so `RATCHET_UNKNOWN_TASKS` does not apply to it; the admin API may replace it with an API task. Case-insensitive; anything else stops startup |
| `RATCHET_MAX_AUTO_TASKS` | `200` | The most auto tasks that may exist (at most 50 in mock mode, whose shared demo a reset clears). Past it, or when the registry has only its last 50 slots free (kept for file and API tasks, so request traffic cannot lock the admin API out), a request with a new system prompt gets `default`, and one `auto_task_cap` event is logged once such a request is admitted. `0`: none is ever made. An admin can delete an auto task (`DELETE /ratchet/tasks/{name}`) to free its place. A whole number from 0 to 250 (half the registry); anything else stops startup |
| `RATCHET_FIXTURES_DIR` | `fixtures` | Where fixture files a task spec names must live (`taskspec.resolve_fixture`: a path that resolves outside it, through `..` or a symlink, is refused). For the reserved `sql` format |

| Task | Fields | `pinned` | Agreement rule |
|---|---|---|---|
| `default` | none | no | Whole answer: when both are a JSON object or array, canonical JSON (sorted keys; strings, keys included, NFKC-normalised, case-folded, whitespace collapsed; numbers as floats); otherwise the text after removing reasoning (`tasks.strip_reasoning`), lower-cased with whitespace collapsed. An empty answer never agrees |
| `ticket-triage` | `category` (label over `billing`, `technical`, `account`, `shipping`, `refund`, `security`), `priority` (label over `low`, `medium`, `high`, `urgent`) | no | Parse JSON (tolerates reasoning, code fences and surrounding text) and compare both labels. Both are required: `{}`, a missing `priority` or a category outside the list is invalid. `reply` is ignored |
| `source-triage` | `relevant` (bool), `source_type` (label over `docs`, `paper`, `blog`, `forum`, `news`, `other`) | no | Scout's triage agent. Same JSON rule; `reason` is ignored |
| `page-read` | `answers_question` (bool), `fact_type` (label over `definition`, `number`, `date`, `name`, `none`) | no | Scout's reader agent. Same JSON rule; `key_fact` is ignored |
| `research-plan` | none | **yes** | Scout's planner. Always answered by the teacher |
| `research-write` | none | **yes** | Scout's writer. Always answered by the teacher |

The built-in vocabularies are in `ratchet/vocab.py`; the prompts that ask for these labels (`sim/tickets.py`, `ratchet/scout/prompts.py`) import the same lists.

**Valid answers.** Every field is required unless it says `required=False`, and may be null only when `nullable=True`. An answer is invalid when it is empty after cleaning, does not parse in the task's format, lacks a required field, or has a value its type cannot normalise (a label outside `values`, a date that is not one). An invalid answer never counts: a student sample gets no vote (all invalid: confidence 0, never served), a teacher answer is served but not stored (`skip_example` `invalid field <path>`) and is not an audit, a golden reference is skipped (`golden_invalid_refs` in the eval), and a stored train row the current spec refuses is left out of training (`skipped_invalid` in the dataset manifest). An optional field that is missing or null agrees only with missing or null.

**Agreement is pairwise.** `confidence.score` compares every pair of samples with the task's comparators, so a tolerance works: under `abs_tol=1`, samples 0, 0.6 and 1.2 agree through 0.6 (agreement 1.0), and 0.6 is the answer served ([ARCHITECTURE.md](ARCHITECTURE.md#score-confidencescore)).

### Field types

`FieldSpec(path, type, ...)`. `path` is `$` (the whole answer) or a dot path into nested objects (`customer.address.city`); a missing path is a missing field.

| Type | Accepts | Normalised to | Agree when | Options |
|---|---|---|---|---|
| `exact` | any JSON value | strings stripped and lower-cased, anything else as it is (v0.4's rule) | equal as JSON text: `1` and `1.0` differ, so do `true` and `1` | none |
| `label` | a string (or an integer) | NFKC, case-folded, trailing `.`, `,`, `;`, `:`, `!`, `?` dropped, runs of spaces, `_` and `-` as one `_` (`Very High.` is `very_high`), then `aliases` | equal; a label outside `values` is invalid | `values`, `aliases` (`{"P1": "urgent"}`; each alias must name a value and must not be one) |
| `bool` | `true`/`false`, `yes`/`no`, `y`/`n`, `1`/`0`, as JSON or as a string | true or false | equal | none |
| `number` | a JSON number, or a string with currency symbols (`$`, `€`, `₹`, any Unicode currency sign), an ISO 4217 code in capitals before or after (`USD 12`, `12.50 EUR`), thousands separators that fit `decimal` (`1,234.50`; `1.234,50` with `decimal=","`; spaces, apostrophes, Indian grouping), `(12.00)` for a negative, a trailing `%` | a float; NaN, infinities, ambiguous groupings (`1,5` under `decimal="."`) and strings over 100 characters are invalid | `abs(a − b) <= max(abs_tol, rel_tol × max(abs(a), abs(b)))`; exact with both tolerances 0 (`1` equals `1.0`) | `abs_tol`, `rel_tol`, `decimal` (`.` or `,`), `percent` (`number`: `12%` is 12; `fraction`: 0.12) |
| `ordinal` | a label in `order` | its position | positions at most `tolerance` apart | `order` (required, lowest first), `tolerance`, `aliases` |
| `text` | a string (or an integer) | `exact`: case-folded, whitespace collapsed; `loose` (default): NFKC, case-folded, punctuation read as a space between words (`Acme,Inc.` is `acme inc`, `hello-world` is `hello world`) except apostrophes and the dots of initials, which join (`O'Brien` is `obrien`, `U.S.A.` is `usa`), whitespace collapsed, trailing `strip_suffixes` tokens dropped while another token is left; `token_set`: the set of loose tokens | equal | `mode`, `strip_suffixes` (`("inc", "ltd", "llc", "gmbh", "pvt ltd")`) |
| `date` | ISO date or datetime (`2026-10-03`, `2026-10-03T12:00:00Z`, offsets), `2026/10/03`, `3 Oct 2026`, `3 October 2026`, `Oct 3 2026`, `October 3rd, 2026`, `3.10.2026`, `10/03/2026` (month first) | the ISO date; with `granularity="datetime"`, the ISO datetime in UTC (naive times read as UTC) | equal | `day_first` (`03/10/2026` is 3 October), `granularity` (`date`: the day as written, even with a time zone; `datetime`) |
| `language` | an ISO 639-1, 639-2 or 639-3 code, a locale tag (`en-US`, `pt_BR`, `zh-Hant-TW`, `es-419`: the whole value, subtags joined by `-` or `_`, led by a 639-1 code or a 3-letter individual language such as `cmn`), or an English or native name (`English`, `Deutsch`, `日本語`) of about 80 languages | the 639-1 code | equal; an unknown language, one outside `values`, or a sentence (`It is English`, `no idea`) is invalid | `values` |
| `set` | a list, or a string split on `split` | the set of normalised elements, duplicates and `empty_values` removed | Jaccard overlap at least `min_jaccard` (default 1.0: equal); two empty sets agree | `item` (element spec or type name; default a label over the set's `values` and `aliases`), `min_jaccard`, `split`, `empty_values` (`("none", "n/a")`) |
| `records` | a list of objects | each object's sub-`fields` | same length, and each record matches its partner: paired by the `key` fields, by position (`ordered=True`), or else in any order (a maximum matching, so the listed order never decides; quadratic in the records when sub-fields have a tolerance, so name a `key` when records have one) | `fields` (required), `key`, `ordered` |
| `ranking` | a list of ids | the normalised ids; an id ranked twice is invalid | `prefix` (default): the first `k` in order; `set`: the first `k` as a set (`k=0`: all) | `k`, `mode`, `item` |

`similarity` (free text) and `tool_calls` are reserved type names for later releases, as are the formats `tool_calls` and `sql`; a spec that uses them is refused. A set's elements and a record's `key` fields compare by their normalised value only, so an element type with a tolerance is refused (use `records`).

Limits: 64 fields per task (counting the fields inside records), records nested at most 4 deep, at most 2,000 `values`, `aliases`, `order` entries, suffixes or empty values per field, at most 200 records in one answer (at any depth) and 2,000 elements in one set or ranking. A spec that breaks a rule raises `ValueError` when the task is defined, so a broken spec stops the gateway at startup; that includes an option the field's type does not use (`tolerance` on a `number`), which would otherwise do nothing.

### Task options

| Option | Default | Meaning |
|---|---|---|
| `fields` | none | The `FieldSpec`s above. A task without fields compares whole answers (the `default` rule) |
| `compare_keys` | none | The v0.4 shortcut: each key becomes `FieldSpec(key, "exact", required=False)`, so answers compare, validate and canonicalise exactly as in v0.4. Give `compare_keys` or `fields`, not both |
| `format` | `json` with fields, else `text` | `json`: fields are paths into the JSON answer, and a code fence around the whole answer is unwrapped; with a `$` field the answer may be any JSON value (a list, a number). `text`: the answer is plain text and its one field is `$`, the text without trailing `.!,;:` (`Positive.` is the label `positive`) |
| `pinned` | false | Always the teacher, never training data (below) |
| `store` | true | False: the task's teacher answers are served but never stored as training examples |
| `policy` | `{}` | `samples` (1 to 16), `audit_rate`, `target_precision`, `min_precision` (0 to 1): part of the spec and its hash. `target_precision` is the precision the task's own threshold is calibrated to (default 0.97) and `min_precision` its floor in the golden gate (default `RATCHET_MIN_PRECISION`) ([per-task calibration](#per-task-calibration)); `audit_rate` replaces `RATCHET_AUDIT_RATE` for the task's served student answers ([risk-aware routing](#risk-aware-routing)); `samples` is read by a later release |
| `routing` | `{}` | Eligibility, escalation rules and critical classes ([risk-aware routing](#risk-aware-routing)); part of the spec and its hash |
| `tools` | `teacher` | `teacher` or `distill` (opt-in tool-call distillation, a later release) |
| `description` | empty | What the task is for, up to 1,000 characters. Shown by `GET /ratchet/tasks` |
| `clients` | none | API key names (`RATCHET_API_KEYS`) that may use the task. Any other key gets 403 `task_not_allowed`, and so does a request without a key (with `RATCHET_API_KEYS` off, every request), before any quota or model call. A name must be a configured key when the spec is loaded or written. Empty or absent: every key |
| `match` | `{}` | How a request that names no task finds this one ([task resolution](#task-resolution)): `system_prefix` (its first system or developer message starts with it), `schema_name` (its `response_format.json_schema.name` is it), `tools` (the names of its `tools` and legacy `functions` are exactly these). Every rule a task gives must hold; the first such task in registry order wins, among the tasks the key may use. Plain comparisons, never patterns |
| `extends` | none | Spec files and the API only: start from another task's spec. In a tasks file, a built-in task or another task of the same file (before or after it; a task's own name extends the built-in task of that name), never an API task, because startup reads the file before it registers the stored API tasks. Through the API, any registered task (the task's own name extends its current version). `fields`, `policy`, `match` and `routing` merge by key (`null` removes the base's entry; a routing block's `escalate_when` and `critical` lists are replaced whole); every other key replaces the base's; `name` and `description` are not inherited. Resolved when the task is defined, so later changes to the base do not reach it |

`Task.spec_hash` is 16 hex digits of SHA-256 over the task's rules (`Task.spec()`: `format`, `fields` in their order, `pinned`, `store`, `policy`, `routing`, `tools`), so a changed vocabulary or tolerance changes it and a renamed task keeps it. `description`, `clients` and `match` are left out: they say who uses a task, not how its answers compare. Night School records each golden task's hash in every eval it writes (in the task's per-task record, and in `spec_hashes`), and a task whose hash changed since the serving version was scored goes to the teacher (threshold 1.01) until a night scores the version under the new spec; `GET /ratchet/tasks` shows it as `recalibrating`. Because `policy` is part of the hash, changing a task's `target_precision` or `min_precision` also sends it to the teacher until the next night calibrates it under the new target.

### Per-task calibration

When several tasks share one adapter, each gets its own threshold (FR-14.1, [ADR 0040](adr/0040-per-task-calibration.md)). The golden eval scores every row once and splits the rows into the calibration and held-out halves by their global position, as before, then writes one record per task into the version's eval (`eval['tasks'][name]`): `golden_n`, `test_n`, `agreement`, `threshold`, `precision`, `coverage`, `spec_hash`, `target_precision`, `calibrated`, and `disabled` once the release gate disables the task. With one task the record holds exactly the global numbers.

| Rule | Value |
|---|---|
| A task's threshold | Calibrated on its own calibration rows to its `policy.target_precision` (default 0.97), never below 0.5 |
| Fewer than `RATCHET_MIN_TASK_GOLDEN` (20) calibration rows | The higher of its own threshold and the version's global one (`calibrated: false`); the global one when it has none |
| No held-out row | 1.01: the teacher answers it until a night measures it |
| No golden row at all | No record: the version's global threshold, as before per-task calibration |
| A record that serves nothing (disabled, or no held-out row) | The student is not called for the task (route `task_disabled`), so it costs no student samples and is never served in degraded mode (`RATCHET_ON_TEACHER_FAILURE=serve_student`); the shadow still runs |
| Spec changed since the version was scored | 1.01 until a night re-scores the version |
| Golden gate, per task | Checked once it has `RATCHET_MIN_TASK_TEST_N` (10) held-out rows: it fails when its held-out precision (over the answers it would serve) is below its floor (`policy.min_precision`, else `RATCHET_MIN_PRECISION`), or below the live version's precision on it less `RATCHET_TOLERANCE`: point estimates, as for the global numbers. A task that answers none of its held-out rows fails too. `RATCHET_TASK_MODE=disable` disables it on the candidate; `reject` rejects the candidate. A candidate left answering under `RATCHET_MIN_COVERAGE` of its held-out rows at its per-task thresholds is rejected |
| A disabled task | Stays disabled for that version's life, through every re-score |
| Live alert, per task | One `alert` per (version, task) when its audited precision's Wilson upper bound is below `RATCHET_LIVE_FLOOR` with at least `RATCHET_MIN_AUDITS` audits, for the live and canary versions; a version whose audits are all one task's has only its own alert |

A version scored before per-task calibration, or with `RATCHET_PER_TASK_CALIBRATION=0`, has no records and keeps its one threshold (with the switch off, a task with critical classes still gets its record: [risk-aware routing](#risk-aware-routing)). `/ratchet/stats` lists each task's threshold, precision and coverage from the live version ([API](API.md#get-ratchetstats)).

**Pinned tasks** (`Task.pinned = True`) skip the student entirely. `Ratchet.handle` sends them to the teacher, logs the request (`served_by=teacher`, cost equal to baseline) and does **not** store the answer as a training example. Use `pinned` for open-ended work (planning, long writing) that a small model should not take over. Every other task is **distillable**: its teacher answers become training data. Only an operator's spec pins a task (built-in, tasks file, admin API): no header, metadata key, `model` value or `response_format` can. Pinning a task that already has stored examples keeps them out of training and out of the golden eval from the next night on (`excluded_pinned` in the dataset manifest); the ledger keeps them, so unpinning brings them back.

Every answer is cleaned the same way before it is compared, served or stored (`tasks.clean_answer`): reasoning is removed (`<think>` blocks, anything before a lone `</think>`, an unterminated `<think>` that opens what is left and what follows), and for a JSON task (any task with fields, unless its format is `text`) a code fence around the whole answer is unwrapped when its body is JSON; an answer that is one JSON object is kept whole, tags quoted in its strings included. A teacher answer is stored as a training example only when it is complete (`finish_reason` `stop` or `tool_calls`), not empty, and valid under the task (invariant 16); otherwise it is served and logged as a `skip_example` event. So a JSON task's system prompt should ask for JSON only, and list the allowed values of each label.

### Risk-aware routing

A task's `routing` block says which requests the student may take, which of its answers still go to the teacher, and which misses the release gate must rule out (FR-2.50, [ADR 0049](adr/0049-risk-aware-routing.md), `ratchet/routing.py`). It is checked when the task is defined, every error with its path (`tasks[0].routing.escalate_when[1].field: 'flaged' is not a field of this task; its fields: flagged, severity`):

```json
{
  "name": "moderation",
  "fields": {"flagged": {"type": "bool"}, "severity": {"type": "label", "values": ["low", "medium", "high"]}},
  "policy": {"audit_rate": 0.2},
  "routing": {
    "student_max_input_tokens": 1800,
    "escalate_when": [
      {"field": "flagged", "equals": true, "action": "teacher"},
      {"field": "severity", "in": ["high"], "min_confidence": 0.97}
    ],
    "critical": [{"field": "flagged", "equals": true, "max_miss_rate": 0.1}]
  }
}
```

| Key | Default | Meaning |
|---|---|---|
| `student_max_input_tokens` | none | A request whose message text holds more than this many tokens (characters / 4, as the gateway's input limit counts characters: content strings and text parts, names, refusals, tool call ids, names and arguments) goes to the teacher without a version being picked or the student called (route `ineligible:input_tokens`). 1 to 10,000,000. Set it at or below the student's training length (`train.py --max-seq`, 2048 tokens by default) less its answer: the student never learned longer prompts |
| `student_content_types` | `["text"]` | Content part types the student reads. Only `text` today (`image_url`, `input_audio` and `file` are reserved for a student that reads them), so a request with another part, which the gateway admits only when `RATCHET_TEACHER_MODALITIES` lists it, goes to the teacher (route `ineligible:content_type`) |
| `store_ineligible` | `false` | `true`: the teacher's answer to an over-length request is stored as a training example (under the usual rules: complete, valid, redacted). Off by default because `train.py` cuts every example at `--max-seq`, so a long prompt trains on its start only. A request with a part the student does not read is never stored |
| `escalate_when` | `[]` | Rules checked in order, after the threshold, on the answer a confident student would serve: `{field, equals \| in, action: "teacher"}` sends an answer in the class to the teacher; `{field, equals \| in, min_confidence: x}` does unless the student's confidence is at least `x`. The first rule that holds names the route (`rule:<i>`, from 0). The teacher's answer is served and stored as for any escalation; the request counts as escalated. At most 32 rules |
| `critical` | `[]` | Classes whose misses are costly: `{field, equals \| in, max_miss_rate}` with `max_miss_rate` above 0 and below 1. The golden eval measures them and the release gate enforces them (below). At most 32 |

A **condition** names one of the task's fields of a scalar type (`exact`, `label`, `bool`, `number`, `ordinal`, `text`, `date`, `language`) and holds when the field's comparator matches one of its values: `equals` (one value) or `in` (1 to 256 values). Values are normalised by the field when the task is defined, so `"HIGH"` holds for `high`, a label alias for its value, and a `number` condition keeps the field's tolerance; a value the field refuses (a label outside its vocabulary) is a spec error. On the request path the rules compare the normalised answer the cascade parsed already, so they cost microseconds, and the task's answer is never written into an event or a gate reason (only the spec's values are).

| Rule | What happens |
|---|---|
| Order | pinned or teacher-only → eligibility → pick a version → sample → the task's threshold → `escalate_when` → serve, or escalate |
| Ineligible request | The teacher answers it (or the fallback teacher, route `fallback_teacher`, when the primary is unavailable); no audit, no shadow, no degraded answer (the student never saw it); not counted as an escalation. The golden eval leaves such rows out (`excluded_ineligible`). It checks the stored prompt, which was redacted (invariant 12): a placeholder such as `[EMAIL]` is shorter than what it replaced, so a request stored with `store_ineligible` just over the limit can come back just under it and be scored. Only rows near the limit, and only with `store_ineligible` |
| Rule escalation | Stored like any escalation. An answer a rule holds for never stands in for an unavailable teacher (`RATCHET_ON_TEACHER_FAILURE=serve_student`), also when the student was below the threshold. A candidate in shadow runs the same rules, so an answer a rule sends to the teacher does not count as one it would serve. The golden eval does the same: such an answer neither calibrates the task's threshold nor counts as served in the precision and coverage the gate checks (the task's record and the pooled numbers), though its row still counts in `test_n` and coverage's denominator |
| Critical classes, eval | On the task's held-out rows: `n`, the rows whose teacher reference is in the class, and `misses`, those whose answer the student would serve (confidence at the threshold the record stores, the one the router applies, and no rule sends it to the teacher) while it is not in the class, with `miss_rate` and its 95% Wilson `upper_bound` (`eval['tasks'][task]['critical']`, [API](API.md#get-ratchetstats)). Unsure, refused and rule-escalated rows are answered by the teacher, so they are not misses |
| Critical classes, gate | A class passes when it has at least `RATCHET_MIN_TASK_TEST_N` (10) such rows and the upper bound is at most `max_miss_rate`. Otherwise the task fails at admit like any failing task (`RATCHET_TASK_MODE`: `disable` takes it off the candidate with a `task_disabled` event, `reject` rejects the candidate), and a night's re-score of the live version disables it in the same write (`task_disabled`, "re-scored"). With no misses, n rows show at most 3.84 / (n + 3.84): 10 rows 28%, 35 rows 10%, 73 rows 5%, 381 rows 1% |
| Critical classes, router | The student answers such a task only on a version whose record measured its classes under the current spec (and passed the gate); without one (no golden rows of it yet, or scored before the classes were added) the task goes to the teacher (route `task_disabled`). The hosted untuned model (`RATCHET_FALLBACK_STUDENT`) never answers it, and is not sampled for it (route `task_disabled`) |
| Per-task audit rate | `policy.audit_rate` (0 to 1) replaces `RATCHET_AUDIT_RATE` for the task's served student answers, with one random draw either way, so other tasks are audited on the same requests as before. The version-level release statistics (the canary's rollback and promotion, the live alert) pool audited answers without weights, so each task counts in them by its volume times its audit rate: a task audited at 0.2 weighs four times as much per request as one at 0.05, and a task at 0 is not in them at all (its answers are still served). The per-task alerts are per task, so unaffected |

**Limits.** Rules catch costly *positive* predictions: they see the answer the student gives, never the one it should have given, so a student that answers `flagged: false` for a post that should be flagged is not caught by `flagged = true → teacher`. Such misses are what `critical` and the audits are for: the critical gate keeps a task on the teacher until the held-out rows show the miss rate is low enough, and audits measure the live answers. Choose `max_miss_rate` with the table above in mind: a strict limit needs many golden rows in the class before the student may answer the task at all. A routing block is part of the spec and its hash, so changing it sends the task to the teacher until a night re-scores the version.

### Task resolution

A request's task is the first of these that names one (`openai_compat.resolve_task`, FR-1.50, [ADR 0048](adr/0048-model-field-routing.md)); the response says which (`ratchet.task`, `ratchet.task_source`, `X-Ratchet-Task`):

| # | Rule | `task_source` |
|---|---|---|
| 1 | The `X-Ratchet-Task` header | `header` |
| 2 | `metadata.ratchet_task` | `metadata` |
| 3 | A `model` of `ratchet/<task>` or `ratchet:<task>`; on the Azure-style route, the deployment in the URL instead (the body's `model` is not read there) | `model`, `deployment` |
| 4 | A model alias (`RATCHET_MODEL_ALIASES`), for the `model` or a deployment | `alias` |
| 5 | The first registered task whose `match` rules all hold, among those the key may use | `match` |
| 6 | (Reserved for an auto task from a `response_format` json_schema, a later item) | |
| 7 | With `RATCHET_AUTO_TASK=system_prompt`, the auto task of the first system message | `auto_system` |
| 8 | `default` | `default` |

The `model` field only ever names a task: the teacher always gets `RATCHET_TEACHER_MODEL` (or the fallback teacher's model) and the student its adapter name, whatever a request sends, and the response's `model` is the model that served. A `model` Ratchet does not know follows `RATCHET_MODEL_POLICY`. With the default settings, a request that sends none of rules 1 to 4 is `default`, as before: the built-in tasks have no match rules and auto tasks are off.

Clients pick a task with the `X-Ratchet-Task` header or `metadata.ratchet_task` (or the rules above). A name that is not registered follows `RATCHET_UNKNOWN_TASKS`: by default it gets a temporary task without fields with that name (`source` `adhoc`, not added to the registry, never pinned), so its data stays separate. Names must match `[A-Za-z0-9_.-]{1,64}`; a header that does not becomes `default` under `distill` (v0.4) and is a 400 `unknown_task` under `teacher` and `reject`, so it never becomes `default` training data there (a bad `metadata.ratchet_task` is always a 400). At most 500 tasks are registered, built-in ones included; a tasks file whose tasks, with the built-in ones it does not replace, come to more is a startup error.

### Adding a task

Define tasks as data, in a tasks file or through the admin API; both take the same spec. A spec has the options above, with `fields` written as `{path: {type, ...options}}` (paths are `$` or dot-separated names of letters, digits and `_`). Unknown keys and options, an option the field's type does not use, a value of the wrong type or out of range are all errors, and every error is reported with its path, so one check finds them all.

1. Write the spec. In a file (`tasks.json`; or `tasks.yaml` with PyYAML):

   ```json
   {"tasks": [
     {"name": "invoice-extract",
      "description": "Vendor, total, currency, due date and lines from an invoice",
      "fields": {
        "vendor":   {"type": "text", "strip_suffixes": ["inc", "ltd", "llc", "gmbh"]},
        "total":    {"type": "number", "abs_tol": 0.01},
        "currency": {"type": "label", "values": ["USD", "EUR", "INR"], "aliases": {"$": "USD", "€": "EUR"}},
        "due_date": {"type": "date", "nullable": true},
        "lines":    {"type": "records", "key": ["sku"], "required": false,
                     "fields": {"sku": {"type": "label"}, "qty": {"type": "number"}}}
      }},
     {"name": "invoice-extract-eu", "extends": "invoice-extract",
      "fields": {"currency": {"type": "label", "values": ["EUR"]}}},
     {"name": "contract-summary", "pinned": true}
   ]}
   ```

   ```yaml
   tasks:
     - name: invoice-extract
       fields:
         total: {type: number, abs_tol: 0.01}
         currency: {type: label, values: [USD, EUR, INR]}
   ```

   Then start the gateway with `RATCHET_TASKS_FILE=tasks.json`. Quote YAML strings that look like booleans or dates (`"yes"`, `"2026-10-03"`).

   Or, without a restart, through the admin API (`RATCHET_ADMIN_KEYS` set; the body is one spec, its name in the URL):

   ```bash
   curl -X POST localhost:8000/ratchet/tasks/validate -H "Authorization: Bearer $ADMIN_KEY" -d @invoice.json   # a dry run
   curl -X POST localhost:8000/ratchet/tasks/validate -H "Authorization: Bearer $ADMIN_KEY" -d @tasks.json    # a whole tasks file, before a restart
   curl -X PUT localhost:8000/ratchet/tasks/invoice-extract -H "Authorization: Bearer $ADMIN_KEY" -d @invoice.json
   ```

   API tasks are stored in the ledger and registered again at every start. The API cannot change a built-in or file task (403): edit the file, or define a task under another name that `extends` it. A file task replaces a built-in one of the same name (`{"name": "ticket-triage", "extends": "ticket-triage", "policy": {"samples": 5}}` adjusts the built-in triage).
2. Make the system prompt ask for JSON with those keys, and list each label's allowed values.
3. Send traffic with `X-Ratchet-Task: invoice-extract`. `GET /ratchet/tasks` shows every task with its source, spec hash and traffic, and the unregistered names requests used.
4. Optional: for free-text tasks (no fields), `LiveEvaluator` adds a judge score (`judge_pass_rate`) using the judge model. The release gate does not use that score yet.

A built-in task is a `Task` in `tasks.BUILTIN` (`ratchet/tasks.py`), with `FieldSpec`s from `ratchet/compare.py`; put shared vocabularies in `ratchet/vocab.py`. Use that only for tasks the code itself depends on, such as Scout's.

Choose fields that are verifiable (labels, IDs, numbers, dates). Typed comparison on these fields is what keeps evaluation free of judge bias; pick the loosest type that is still right (a `number` with a tolerance for amounts, a `set` for tags), because a field that is too strict escalates good answers and lowers measured precision. Changing a task's rules later is safe: the serving version keeps the task on the teacher until a night has scored it under the new spec.

## Teacher licenses

Edit `ratchet/licenses.py` to add a teacher. `REGISTRY` holds known distillable teachers; `CLOSED` holds ID prefixes that are always refused (`gpt`, `o1`, `o3`, `o4`, `claude`, `gemini`). Unknown teachers are refused until registered. See [ADR 0001](adr/0001-open-teacher-only.md).
