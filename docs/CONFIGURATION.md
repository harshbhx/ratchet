# Configuration

All settings come from environment variables. Defaults live in `ratchet/config.py` (`Settings`), `ratchet/live.py`, the `deploy/` scripts and `deploy/vm/`. Model IDs and prices were read from the Nebius Token Factory catalog in October 2026 and are unverified until `python scripts/day1_check.py` passes with a real key (INV-9).

Terms (shadow, canary, Wilson interval, e-value) are in [GLOSSARY.md](GLOSSARY.md); FR-, NFR- and INV- ids are requirement and invariant ids ([REQUIREMENTS.md](REQUIREMENTS.md)). A one-page overview is in [README.md](../README.md).

## Gateway and cascade (`ratchet/config.py`)

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_MODE` | `mock` | `mock`: offline demo with mock models and an in-memory ledger. `live`: real Token Factory teacher and a SQLite file |
| `RATCHET_DB` | `ratchet.db` | SQLite ledger path (live mode only; mock mode always uses `:memory:`) |
| `RATCHET_TEACHER_BASE` | `https://api.tokenfactory.nebius.com/v1` | OpenAI-compatible base URL for the teacher, judge and fallback student (and the fallback teacher, unless `RATCHET_TEACHER_FALLBACK_BASE` is set) |
| `NEBIUS_API_KEY` | empty | Token Factory API key (sent as `Authorization: Bearer …`) |
| `RATCHET_TEACHER_MODEL` | `nvidia/Nemotron-3-Ultra-550b-a55b` | Teacher model. Must be in the license registry and distillable, or startup fails with `LicenseError` |
| `RATCHET_JUDGE_MODEL` | `nvidia/nemotron-3-super-120b-a12b` | Judge for free-text golden evaluation (`LiveEvaluator`): the answers the student would serve on free-text tasks' held-out golden rows, read by `RATCHET_MIN_JUDGE_PASS` ([free-text tasks](#free-text-tasks)) |
| `RATCHET_FALLBACK_STUDENT` | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` | Hosted, untuned model used for student calls when `RATCHET_STUDENT_BASE` is empty and a trained version exists (on a fresh deployment the teacher answers). It answers under `RATCHET_CONFIDENCE_THRESHOLD`, and its answers count toward no version's release: a canary or shadow holds while it serves ([ADR 0073](adr/0073-fallback-attribution.md)). Reported as `fallback_student` in `/ratchet/stats` and `/scout/status`; the pages label a student answer with this `model` "Nano 30B · hosted", never as our adapter (FR-7.40) |
| `RATCHET_STUDENT_BASE` | empty | Base URL of your vLLM endpoint, e.g. `https://<endpoint>/v1` |
| `RATCHET_STUDENT_KEY` | empty | Token for the student endpoint |
| `RATCHET_STUDENT_BASE_MODEL` | `nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16` | Base model that adapters are trained on (also used by the deploy scripts) |
| `RATCHET_CONFIDENCE_THRESHOLD` | `0.80` | Fallback threshold, used only when a version has no calibrated `threshold` in its eval (and for the hosted, untuned fallback student) |
| `RATCHET_SAMPLES` | `3` | Student samples per request (self-consistency) and per golden row, for a task whose spec sets no `policy.samples` (a task's own count wins, FR-2.55). A request for more choices (`n`) draws `n` samples, but its confidence is still scored on the first `RATCHET_SAMPLES` (or the task's count) of them |
| `RATCHET_AUDIT_RATE` | `0.05` | Probability that a student-served answer is also sent to the teacher. Mock mode raises it to at least `0.10`. A task's `policy.audit_rate` replaces it for that task ([risk-aware routing](#risk-aware-routing)), and a canary's rate below replaces both. An admin can replace them all at runtime, without a restart (`PUT /ratchet/admin/settings` or `python -m ratchet.admin flag audit_rate 0.2`; [API.md](API.md#admin)); `controls.audit_rate` in `/ratchet/stats` shows the override, `null` while this value applies |
| `RATCHET_CANARY_AUDIT_RATE` | unset | Audit focusing ([ADR 0025](adr/0025-anytime-valid-release-gate.md)): the audit probability for answers a **canary** serves, so its release evidence arrives sooner; live answers keep `RATCHET_AUDIT_RATE`. More than 0 and at most 1 (0 would leave a canary without audits, so it could never be promoted, rolled back or retired); anything else stops startup. Unset or empty: the canary is audited at `RATCHET_AUDIT_RATE`. Still one random draw per served answer; the rate used is logged as `requests.label_p` |
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

Each answered embeddings call is one request row (task `embeddings`, served_by `upstream`, route `passthrough`, nothing stored) and counts against the key's daily quota, budgets, rate and in-flight caps ([Spend and rate limits](#spend-and-rate-limits)). The task name `embeddings` is reserved for these rows: a task spec (tasks file, admin API) or a `RATCHET_MODEL_ALIASES` entry that names it is refused, and a chat request that names it is a 400 `reserved_task`. Its calls get the teacher's timeouts and retries (`RATCHET_TEACHER_TIMEOUT_S`, `RATCHET_TEACHER_RETRIES`, `RATCHET_TEACHER_DEADLINE_S`) and go through no breaker. `/v1/embeddings` also takes `RATCHET_MAX_INPUT_CHARS` (a token id counts as 4 characters) and at most 2,048 inputs.

## Retries and idempotency

A client's retry neither pays for a second cascade nor counts as a second occurrence of its request ([ADR 0071](adr/0071-idempotency-keys-and-retry-de-duplication.md), [API.md](API.md#idempotency-and-retries)). Read in `ratchet/config.py` (`Settings.idempotency()`, `Settings.seen_dedup()`); used by `ratchet/idem.py`, `ratchet/gateway.py` and `ratchet/ledger.py`. Both structures live in memory, in each gateway process (with several workers, a retry that reaches another process runs again), and a mock demo reset clears them. A value out of range stops startup, naming the variable.

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_IDEMPOTENCY_CACHE` | `10000` | Most answers kept by `Idempotency-Key`, 0 to 1,000,000; the least recently used goes first, and the answers kept never exceed 64 MiB together, each counted as its body plus 2 KiB for the entry that holds it (so at most about 32,000 small answers, whatever this says). `0` turns idempotency off: the header is ignored (not even checked) and every request runs |
| `RATCHET_IDEMPOTENCY_TTL_S` | `600` | Seconds an answer is kept after it finished, 0 to 86,400; a request with the same key after that runs again. `0` keeps none: only a request that arrives while the first is still running shares its answer. Answers hold request-derived text, so they are never written to disk (INV-12) |
| `RATCHET_SEEN_DEDUP_S` | unset: `300` in live mode, `0` in mock mode | Seconds within which the same API client's repeat of a stored example counts once toward the frequent-request rule (`seen`, `FREQUENT` = 3, INV-3), 0 to 86,400; a repeat after the window counts again. Without API keys every caller is one client. Up to 10,000 (example, client) pairs are remembered, the least recently counted forgotten first. A request whose `x-stainless-retry-count` is above 0 never counts, whatever this says. `0`: every repeat counts, as in v0.4; mock mode's default, because the simulator's and the demo's repeats are traffic, not retries (internal calls, such as Scout's, the simulator's and the warm-up's, count every repeat in any mode). The demo VM's [`.env.vm.example`](../deploy/vm/.env.vm.example) sets `300`, so the public mock demo counts a visitor's quick repeats once, as live mode does |

## Generation parameters and token caps

Read in `ratchet/config.py` (`Settings.gen_policy()`); used by `ratchet/router.py`, the golden eval (`ratchet/nightschool/evaluate.py`, `sim/mock_models.py`) and `ratchet/providers.py` ([API.md](API.md#generation-parameters), [ADR 0017](adr/0017-generation-parameters-by-role.md)). A bad value (a field that cannot be forwarded, a name that is not an identifier, a negative number) stops the gateway, `python -m ratchet.live night` and the simulator at startup with `ValueError`.

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_TEACHER_PARAMS` | empty (every standard field) | Request fields the teacher gets when its answer is served (escalation, cold start, pinned, teacher-only), comma-separated, in any case: `max_tokens` (also for `max_completion_tokens`), `temperature`, `top_p`, `stop`, `seed`, `presence_penalty`, `frequency_penalty`, `logit_bias`, `response_format`, `tools`, `tool_choice`, `parallel_tool_calls`, `logprobs`, `top_logprobs`, `reasoning_effort`. Empty means the default: every standard field. `tools`, `tool_choice` and `parallel_tool_calls` are in it since v0.5 (FR-1.41): only a tool request has them, the teacher answers it alone and its tool calls come back to the client ([API.md](API.md#tools-and-function-calling)); leave them out (name the other fields) and the teacher answers tool requests in prose, still teacher-only and never stored. `logprobs` and `top_logprobs` reach the teacher only when a client sends them, and its token logprobs come back in the response (v0.5, FR-1.14); leave them out if the teacher refuses `logprobs` (`scripts/day1_check.py` reports whether it returns them), and teacher-served choices then carry `logprobs.content: null`. `none` forwards nothing; `all` stands for the whole list, and `all,top_k` adds a vendor field the client sends (otherwise unknown fields are never forwarded). Startup stops on `model`, `messages`, `n`, `stream`, `stream_options`, `user`, `safety_identifier`, `store`, `metadata`, `service_tier`, `functions` or `function_call`, on a name that is not an identifier, and on a near miss of a standard field (`max_token`, `stops`), which is a typo; any other name is taken as a vendor field. Audits and arbitrations take the same fields, minus `top_p`, `seed`, the penalties, `logit_bias` and logprobs, at temperature 0. A served answer written under a client's `logit_bias`, a non-zero penalty, a temperature above 1 or a vendor field is not stored |
| `RATCHET_STUDENT_PARAMS` | empty (`max_tokens,stop,seed,response_format,top_logprobs`) | Request fields the student gets, same syntax. Its `n` (`RATCHET_SAMPLES`), temperature (0.7) and logprobs are fixed by the cascade whatever this says, so its confidence stays calibrated. `logit_bias` is not in the default on purpose: a client's bias would steer the student's answer while the audit runs without it, and OpenAI token ids mean other tokens to Nemotron; name it here to forward it. A request with tools never reaches the student (the teacher answers it alone, FR-1.41), so `tools` and `tool_choice` here only matter for a golden row stored with them; add them only when the student's vLLM runs with `--enable-auto-tool-choice` and a `--tool-call-parser` (`deploy/student_endpoint.sh` does not), since without them vLLM answers 400. A golden row the student refuses this way scores as not served in the nightly eval |
| `RATCHET_TEACHER_MAX_TOKENS` | `8192` | Most output tokens per teacher call (reasoning included, as Token Factory counts them). A client's larger `max_tokens` is capped to this; with none, this is sent. `0` = no cap |
| `RATCHET_STUDENT_MAX_TOKENS` | `1024` | Most output tokens per student sample, the same way. `0` = no cap |
| `RATCHET_TEACHER_REASONING_HEADROOM` | `0` | Tokens added to a client's `max_tokens` for the teacher (still at most `RATCHET_TEACHER_MAX_TOKENS`), for reasoning it writes before the answer: without it a reasoning teacher can spend a tight limit on its reasoning and be cut off. When the teacher gets more than the client asked for, a visible answer longer than the client's `max_tokens` (about 4 characters a token) is returned with `finish_reason: "length"` and not stored. Set it near `RATCHET_TEACHER_REASONING_TOKENS` for a reasoning teacher whose clients send small limits |

## Reasoning (Nemotron thinking)

Read in `ratchet/config.py` (`Settings.reasoning_policy()`, [ADR 0130](adr/0130-reasoning-by-role.md)); sent as the chat template's `chat_template_kwargs`. A bad value stops the gateway, the simulator and `python -m ratchet.live` at startup. `default` sends nothing.

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_STUDENT_REASONING` | `off` | The trained student on the vLLM endpoint (production, canary, shadow, both golden evals): `off` sends `{"enable_thinking": false}`, `default` nothing. `on` and `medium` stop startup: the student is trained with thinking off |
| `RATCHET_FALLBACK_STUDENT_REASONING` | `off` | The hosted untuned student (`RATCHET_FALLBACK_STUDENT`) used while `RATCHET_STUDENT_BASE` is empty: `off`, `on` or `default`. Set `default` if Token Factory refuses the field (the student then fails fast and requests escalate, route `student_error`) |
| `RATCHET_TEACHER_REASONING` | `default` | The teacher whose answer is served (escalation, cold start, pinned, teacher-only) and the fallback teacher: `default`, `off`, `on` or `medium` (Ultra's `{"enable_thinking": true, "medium_effort": true}`). Keep `default` until day 1 shows Token Factory accepts the field. With `off`, `RATCHET_TEACHER_REASONING_HEADROOM` is not needed |
| `RATCHET_CHECK_REASONING` | empty (follows `RATCHET_TEACHER_REASONING`) | Audits and arbitrations, same values |
| `RATCHET_TEACHER_REASONING_FIELD` | `chat_template_kwargs` | Where Token Factory-bound calls (teacher, fallback teacher, hosted fallback student) carry the kwargs: top level, or `extra_body` (nested as `extra_body.chat_template_kwargs`). **Unverified until day 1** (`scripts/day1_check.py`). The vLLM endpoint always gets the top-level field |

A client's `chat_template_kwargs` and `extra_body` are accepted and ignored, and `RATCHET_TEACHER_PARAMS` and `RATCHET_STUDENT_PARAMS` refuse them. The kwargs are never stored or hashed.

## Upstream timeouts and retries

Read in `ratchet/config.py`; used by `ratchet/providers.py` and `ratchet/resilience.py` ([ADR 0014](adr/0014-upstream-failure-semantics.md)). Connect, write and pool timeouts are fixed at 5, 10 and 5 seconds (the student's connect timeout has its own variable). A bad value (zero, negative, not a number) stops live mode at startup with `ValueError`.

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_TEACHER_TIMEOUT_S` | `120` | Read timeout for teacher and judge calls on Token Factory, in seconds: how long the model may take to start answering |
| `RATCHET_TEACHER_RETRIES` | `2` | Extra attempts for a failed teacher or judge call (`0` = no retries). Retried: connection failures and HTTP 408, 409, 429, 500, 502, 503, 504, with full-jitter backoff (0.5 s base, 8 s cap) or the server's `Retry-After`. A read timeout is retried once at most, because the tokens may already be billed; any other 4xx, and a 2xx body that cannot be read, never. A server wait over 8 s is not waited for: the 429 goes back to the client with it |
| `RATCHET_TEACHER_DEADLINE_S` | `180` | Upper bound on one teacher call, attempts and waits together, in seconds. A retry whose wait would end past it is not started, and later attempts get only the time that is left as their timeout. A `/v1/completions` batch starts no further prompt once it has run this long (504 `batch_deadline_exceeded`, [API.md](API.md#post-v1completions)) |
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

Read in `ratchet/config.py`; used by `ratchet/router.py`, `ratchet/auth.py`, `ratchet/privacy.py`, `ratchet/bom.py`, `ratchet/live.py`, `ratchet/admin.py` and `ratchet/gateway.py`. Background in [ARCHITECTURE.md](ARCHITECTURE.md#pii-redaction), [API.md](API.md#authentication), [API.md](API.md#end-users-and-storage) (opt-outs and end users), [API.md](API.md#erasure-and-export) and [THREAT_MODEL.md](THREAT_MODEL.md).

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_REDACT_PII` | `1` | Replace emails, phone numbers, card numbers (Luhn-checked), IBANs (mod-97), Aadhaar (Verhoeff), US SSNs, Indian PANs and GSTINs, UPI ids, IPv4 addresses and API secrets with placeholders before a request and the teacher's answer are stored as a training example. The teacher still receives the original. `0` stores examples verbatim. Names and street addresses are found only by a detector plugin (`RATCHET_REDACT_PLUGIN`) |
| `RATCHET_REDACT_STYLE` | `label` | How identifiers are written in stored examples ([ADR 0082](adr/0082-placeholder-styles-detector-plugin-and-indian-identifiers.md), FR-13.7): `label` (`[EMAIL]`, v0.4's output), `numbered` (`[EMAIL_1]`, `[EMAIL_2]`: the same value gets the same number in the prompt, the stored conditions and the answer, so an extraction task's answer still says which of two addresses it names) or `surrogate` (reserved values of the same shape: `person1@example.com`, `+12025550100`, a 16-digit card number that fails Luhn, a `ZZ` IBAN, `192.0.2.1`; the numbered placeholder for other identifiers). A task spec's `privacy.redact_style` overrides it for that task ([Tasks](#task-options)). Numbers start again in every example, and nothing original is kept. Changing it changes the stored text, so a request stored before and after is two examples (old rows are never rewritten); an erased example stays erased (each row stored otherwise than in `label` records its `label` key, which an erasure tombstones too). Case and spaces are ignored; empty means `label`; anything else stops startup |
| `RATCHET_REDACT_PLUGIN` | empty | A detector for what the patterns cannot find, such as names and addresses: `module:function` (a dotted attribute path after the colon), importable by the gateway. The function takes a text and returns `(start, end, label)` spans (`0 <= start < end <= len(text)`, a label of 1 to 32 letters, digits and `_`); they are merged with the patterns' and written in the active style. Imported and called once on an empty text at startup; a missing module or attribute, or a detector that fails there, stops startup. It runs whenever an example is stored, on the request's thread, so its latency adds to escalated and audited requests, and it must be thread-safe. When it fails at store time the answer is served and nothing is stored (`skip_example` `redaction failed`). `POST /ratchet/redact` runs it only for callers with a valid API or admin key. ADR 0082 has a Presidio example; the package bundles no NER model |
| `RATCHET_STORE_EXAMPLES` | `1` | `0` is zero retention ([ADR 0080](adr/0080-storage-opt-outs-and-pseudonymous-end-users.md), FR-13.1): every request is answered, audited and logged as usual, but no teacher answer is stored as a training example, and Night School skips every night (`storage disabled`), so examples kept from before are not trained on. A task whose spec changed is still re-scored on the stored golden rows when no release is in flight (that writes no example), so it does not stay on the teacher for good. Requests are logged with `store_opt_out` `global`. Accepts `1`/`0`, `true`/`false`, `yes`/`no`, `on`/`off` in any case; anything else stops startup, so a typo cannot mean "store" |
| `RATCHET_NO_STORE_TASKS` | empty | Tasks whose teacher answers are never stored, comma-separated (`invoice-extract,hr-notes`): the same as `store: false` in their specs ([Tasks](#tasks)). Requests are logged with `store_opt_out` `task`. A name need not be registered, but one that is not a task name (a space-separated list) stops startup. Examples stored before stay (FR-13.1) |
| `RATCHET_FEEDBACK_CORRECTIONS` | `1` | Whether a correction sent with an `incorrect` verdict (`POST /v1/ratchet/feedback`, FR-13.30, [ADR 0091](adr/0091-human-labels-as-a-provenance-source.md)) may be stored as a human-labelled training example. `0` keeps every verdict (task agreement, human precision, the `human_alert` event) and stores no correction (`not_stored: corrections_off`), for deployments whose API clients should not write labels. The storage opt-outs above and pinned tasks apply either way. Accepts `1`/`0`, `true`/`false`, `yes`/`no`, `on`/`off` |
| `RATCHET_END_USER_KEY` | empty | The HMAC key end users are hashed with (FR-13.2), at least 16 characters (`openssl rand -hex 32`). The request's `safety_identifier`, else its `user`, is stored only as HMAC-SHA256 under this key, scoped to the API key name, cut to 16 hex digits. Empty: 32 random bytes are generated on first start and kept in the ledger's `meta` table, so hashes survive restarts, but anyone with a copy of the database file then has the key too; set this from a secrets manager to keep it out of the file and its backups. Changing it unlinks every earlier hash: no-train registrations stop matching (an `end_user_key` event says how many), so those end users must register again. A shorter value stops startup |
| `RATCHET_EXAMPLE_RETENTION_DAYS` | `0` | Whole days a stored example is kept after it was last stored or seen again (FR-13.4, [ADR 0081](adr/0081-retention-tombstones-erasure-and-export.md)); `0` keeps it forever. Past the window its messages, answer and conditions are removed (golden examples too) and the row stays as a tombstone with its hash, so Night School's counts and an erasure still see it; a later duplicate that may be stored brings the text back with its own teacher and license. Applied hourly by the live gateway, at the start of every live night and by `python -m ratchet.live purge`. 0 to 36500; anything else stops startup |
| `RATCHET_REQUEST_RETENTION_DAYS` | `0` | Whole days a request row (metadata only) is kept (FR-13.4); `0` keeps it forever. Older rows are folded into the `daily_rollup` table (per day, task, client, route and opt-out: the sums every stats reader and the cost report add back; no end user) and deleted, except rows logged since a release stage in flight began and the rows a version in shadow or canary served or shadowed in an earlier stage, and the rows of a day whose savings receipt is not sealed yet (a day that ended less than 5 minutes ago keeps every row until the next purge seals it). Distinct end users per client then count only the rows still kept. Use at least 1, so today's rows (the daily quota counts them) stay. 0 to 36500; anything else stops startup |
| `RATCHET_ERASURE_SHARED` | `delete` | What erasing a client or an end user (FR-13.3) does with an example another client also sent: `delete` purges it too (the text is the same, so the erased party's data was in it); `keep` keeps it for the other client and unlinks the erased party (the other client becomes its first sender, without an end user). Erasure by hashes or by text purges whatever it selects. Anything else stops startup |
| `RATCHET_API_KEYS` | empty | `name:key,name:key`. When set, `/v1/*` and `/openai/*` need `Authorization: Bearer <key>`, `X-API-Key: <key>` or `api-key: <key>`; anything else gets 401. Names: letters, digits, `_`, `.`, `-` (up to 64). Keys: at least 16 characters; generate with `openssl rand -hex 24`. A bare key without `name:` is named `keyN`. The ledger stores the name per request, never the key. End-user hashes and no-train registrations are scoped to the name ([API.md](API.md#end-users-and-storage)): rotate a key under the same name; renaming one unlinks its end users' earlier hashes, and its clients' no-train registrations match nothing until the name is back (a `no_train_unmatched` event at startup; an admin's registration for every client is not affected). A key also reads its own savings receipts and their proofs, never another client's ([API.md](API.md#savings-receipts)). A malformed value stops the gateway at startup |
| `RATCHET_API_KEY_DAILY_REQUESTS` | `0` | Requests per key per UTC day (chat completions, responses, embeddings, and completions at one per prompt); past it a 429 `insufficient_quota` (code `daily_quota_exceeded`) with `Retry-After` until 00:00 UTC and `X-Should-Retry: false` ([API.md](API.md#authentication)). `0` = unlimited. `RATCHET_KEY_LIMITS` overrides it per key ([Spend and rate limits](#spend-and-rate-limits)). Counted by the spend meter, which reads today's requests from the ledger at startup, so it survives restarts in live mode |
| `RATCHET_ADMIN_KEYS` | empty | Admin keys for management calls (`PUT` and `DELETE /ratchet/tasks/{name}`, `POST /ratchet/tasks/validate`, [API.md](API.md#tasks); `POST /ratchet/cost/endpoint`; every route of the admin API under `/ratchet/admin/`, reads included, [API.md](API.md#admin); and the training-set proofs, `POST /ratchet/versions/{id}/proof` and `GET /ratchet/versions/{id}/heldout`, [API.md](API.md#committed-training-sets), which are a membership oracle for the training data; and every savings receipt, the gateway's and each client's, [API.md](API.md#savings-receipts)), in the `RATCHET_API_KEYS` syntax: `name:key,name:key`, keys at least 16 characters. Sent as `Authorization: Bearer <key>` or `X-API-Key`; a missing or wrong key gets 401, an API key 403. The ledger and the event log record the name, never the key. A key may not be in both variables (startup error). Empty: the admin API is off in live mode (403 `admin API disabled: set RATCHET_ADMIN_KEYS`) and open in mock mode (the admin is `mock-admin`). Not the Caddy guard's `RATCHET_ADMIN_TOKEN` |
| `RATCHET_DASHBOARD_KEYS` | empty | Read-only keys for the dashboard API ([API.md](API.md#access-levels)), in the `RATCHET_API_KEYS` syntax. A dashboard key reads everything an admin key reads and writes nothing. A key may not also be an API key or an admin key (startup error), so each key has one role. Give one to whoever watches a live gateway's dashboard |
| `RATCHET_PUBLIC_DASHBOARD` | `1` in mock mode, `0` in live mode | `1`: the dashboard API (`GET /ratchet/stats`, `GET /ratchet/cost`, `POST /ratchet/redact`, `GET /ratchet/tasks` and `/ratchet/tasks/{name}`, the BOM endpoints and the other reads [API.md](API.md#access-levels) lists) needs no key. `0`: it needs an admin or dashboard key, as `Authorization: Bearer <key>` or `X-API-Key`; without one it is 401, with an API key 403, and with neither `RATCHET_ADMIN_KEYS` nor `RATCHET_DASHBOARD_KEYS` set every read is 403. The pages, `/healthz`, `/ratchet/bom/public-key` and Scout are always public. The dashboard page sends no key yet, so with `0` it shows no data: read the API with a key, or set `1` (the demo VM does) |
| `RATCHET_PUBLIC_BOM` | `0` | `1`: `GET /ratchet/bom` and `GET /ratchet/versions/{id}/bom` stay public while the dashboard API is private, so auditors can check an adapter's provenance without a key |
| `RATCHET_SCRUB_STATS` | unset | What a reader of the dashboard API sees. Unset: an anonymous read of a public dashboard in live mode is scrubbed: client names in `/ratchet/stats` and in task allow-lists (`GET /ratchet/tasks`) become `client-1`, `client-2`, ... (by sorted name, the same in both), event details lose every `http(s)://` URL (`<url>`), and an error message after an exception's class is cut (`ConnectError: ...` becomes `ConnectError`; v0.4 ledgers hold such events). A reader with an admin or dashboard key sees everything. `1`: every read is scrubbed, with a key or without, in either mode. `0`: none is. `security.scrubbed` in `/ratchet/stats` says which a reader got |
| `RATCHET_OPENAPI` | `1` in mock mode, `0` in live mode | `1`: FastAPI serves `/docs`, `/redoc` and `/openapi.json`. `0`: they do not exist (404), so a live gateway does not hand anyone a map of its routes |
| `RATCHET_ALLOW_UNAUTHENTICATED` | `0` | Live mode refuses to start without `RATCHET_API_KEYS`, because `/v1/*` would answer anyone, on your teacher credits. `1` lets it start for a gateway something else guards, but only with the guard configured: `RATCHET_API_GUARD=guarded` and a `RATCHET_ADMIN_TOKEN` of at least 32 characters (the demo VM's Caddy checks both), so one variable alone never opens the paid API. Otherwise startup and `python -m ratchet.config check` refuse, naming both variables and never the token. Mock mode ignores it |
| `RATCHET_CSP` | empty | A `Content-Security-Policy` for every response. Empty sends none, so no page can break by default. Printable ASCII only (anything else stops startup). Recommended, which the React build and the lite pages work with: `default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'` (the pages load nothing from other origins; `'unsafe-inline'` is for the theme script in the React page and the lite pages' inline scripts). It also blocks `/docs`, whose Swagger UI comes from a CDN |
| `RATCHET_BOM_SIGNING_KEY` | empty | Ed25519 private key as the base64 of its 32-byte seed. Signs each adapter's bill of materials and every savings receipt (same key, same public key). Generate with `python -m ratchet.bom keygen`, which also prints the public key to publish. Needs the `sign` extra (`pip install "ratchet-distill[sign]"`, included in the gateway image). An invalid value stops the gateway at startup. Empty = BOMs and receipts are stored with a SHA-256 but unsigned |

The switches above take `0` or `1`; any other value stops startup, and unset or empty takes the default. Every response also carries `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer` ([ADR 0061](adr/0061-endpoint-access-levels.md)).

Keep `RATCHET_API_KEYS`, `RATCHET_ADMIN_KEYS`, `RATCHET_END_USER_KEY`, `RATCHET_DASHBOARD_KEYS`, `RATCHET_METRICS_TOKEN` and `RATCHET_BOM_SIGNING_KEY` out of git, like every other key. Rotating the signing key does not invalidate old BOMs: each one embeds the public key and key id it was signed with, so keep a list of the public keys you have published.

## Spend and rate limits

What each API key and the whole gateway may spend and send ([ADR 0063](adr/0063-spend-and-rate-limits.md), [API.md](API.md#limits-and-headers), [RUNBOOK.md](RUNBOOK.md#cost-spike)). Read in `ratchet/config.py` (`Settings.limit_policy`); `RATCHET_KEY_LIMITS` is parsed by `ratchet/auth.py` against the key names; used by `ratchet/spend.py`, `ratchet/ratelimit.py` and `ratchet/gateway.py`. A bad value, or `RATCHET_KEY_LIMITS` naming a key that is not in `RATCHET_API_KEYS`, stops startup with a `ValueError` naming the variable. Dollars are per-token prices ([Prices](#prices-default_prices)), simulated in mock mode; a key's spend counts answered requests and what failed requests spent (the student's samples before a teacher failure), for gateway API requests only: Scout's agents stay under the Scout budget (invariant 11). Limits are kept per process (one worker, ADR 0006) and keyed by key name, never the key.

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_KEY_LIMITS` | empty | Per-key overrides of the defaults below, by key name: `acme:daily_requests=10000,daily_usd=5,monthly_usd=100,rpm=60,concurrency=4,tasks=ticket-triage\|invoice-extract,store=1;globex:daily_usd=1`. Entries are separated by `;`, fields by `,`. `daily_requests`, `rpm` and `concurrency` are whole numbers, `daily_usd` and `monthly_usd` plain decimals (US dollars, at most 1,000,000,000), `0` turns a limit off for that key. `tasks` lists the task names the key may send, separated by `\|` (any other task, `default` included, is a 403 `task_not_allowed`; without `tasks` every task its own `clients` allow). `store=0` keeps the key's requests out of the training set: they are served, audited and logged, never stored (`store=1`, the default, stores as usual). A name that is not in `RATCHET_API_KEYS`, an unknown field, a field or a key given twice, or a bad value stops startup; the message lists the key names and never prints what may be a key pasted by mistake (`acme:<key>`, in the `RATCHET_API_KEYS` syntax): an unknown name is not repeated, and an item, field or value is quoted only when it is shorter than any key (16 characters) |
| `RATCHET_API_KEY_DAILY_USD` | `0` | Every key's US dollar budget per UTC day (`0` = none; at most 1,000,000,000). Past it the key gets 429 `insufficient_quota` (code `budget_exceeded`) until 00:00 UTC, with `X-Ratchet-Budget-Remaining-USD: 0`, `Retry-After`, `X-Ratchet-Budget-Reset` and `X-Should-Retry: false`. A request in flight can overshoot by its own cost, because the check reads what was spent before it. A key with a dollar budget gets `X-Ratchet-Spend-Today-USD` and `X-Ratchet-Budget-Remaining-USD` on every answer |
| `RATCHET_API_KEY_MONTHLY_USD` | `0` | Every key's US dollar budget per UTC month (`0` = none), refused the same way until 00:00 UTC on the 1st. With a monthly budget set, the gateway reads this month's spend from the ledger at startup (a pass over the requests index; with none, only today's) |
| `RATCHET_API_KEY_RPM` | `0` | Every key's requests a minute (`0` = no limit): a token bucket that holds `max(1, rpm // 6)` requests (ten seconds' worth) and refills continuously. Past it a 429 `rate_limit_error` (code `rate_limit_exceeded`) with `Retry-After` (seconds until one more request passes, which OpenAI SDKs honour) and `X-RateLimit-Limit-Requests`, `X-RateLimit-Remaining-Requests` and `X-RateLimit-Reset-Requests`. Checked before the body is read, so a request refused later (400, 403, 429) has still used its token |
| `RATCHET_API_KEY_CONCURRENCY` | `0` | Every key's requests in flight (`0` = no limit), counted once a request's body has arrived. One more is refused at once (429 `rate_limit_exceeded`, `Retry-After: 1`), never queued |
| `RATCHET_DAILY_BUDGET_USD` | `0` | The gateway's own US dollar budget per UTC day for `/v1` chat requests, all keys (and requests without a key) together (`0` = none; at most 1,000,000,000). A scrubbed `/ratchet/stats` read does not show it. What happens past it is `RATCHET_BUDGET_MODE`. Scout is not counted: it has `RATCHET_SCOUT_DAILY_BUDGET_USD` |
| `RATCHET_BUDGET_MODE` | `block` | `block`: past the gateway's budget every chat request gets 429 `insufficient_quota` (code `gateway_budget_exceeded`) until 00:00 UTC (`/v1/models` and request lookups cost nothing and still answer). `student_only`: confident student answers are still served, with no audit and no shadow; a request that needs the teacher (an escalation, a cold start, a pinned or unregistered task, a request with `tools`) gets that 429 without a teacher call, and what its student samples cost is recorded. Without audits, canary promotion pauses; a rollback still acts on the audits already recorded. Any case; empty means `block` |
| `RATCHET_BUDGET_ALERT_PCT` | `50,80,100` | The shares of a dollar cap (a key's daily or monthly budget, the gateway's daily budget) that write a `budget` event the first time spend reaches them in the cap's UTC day or month (`acme reached 80% of $5.00 daily budget`, `the gateway reached 100% of $20.00 daily budget`). Whole percentages from 1 to 100, comma-separated; empty = no alerts. The gateway reads the period's `budget` events at startup, so a restart does not write one again; the events go to the log like every other (`ledger_event`), where an alert rule can catch them |
| `RATCHET_IP_RPM` | unset: `0` in mock mode, `120` in live mode | Requests a minute per client IP on `/v1/chat/completions` of a gateway without `RATCHET_API_KEYS` (with keys, each key has its own limit and this is off), as a token bucket like `RATCHET_API_KEY_RPM`. The IP is the connection's, or the first `X-Forwarded-For` hop with `RATCHET_TRUST_PROXY=1` (on the demo VM uvicorn already takes it from Caddy). At most 10,000 addresses are remembered; past that the least recently seen is forgotten. `0` = no limit |
| `RATCHET_MAX_INFLIGHT` | `32` | Chat requests in flight across the gateway before the next gets a 503 `overloaded` with `Retry-After: 1` instead of waiting for a worker thread (the pool has 40, so the dashboard and health checks keep answering under a burst). A request counts once its body has arrived, so slow uploads hold no slot. `0` = no limit |

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
| `RATCHET_SCOUT_RECORDINGS_DIR` | empty | A folder of recorded Scout runs (`python -m ratchet.scout record`) that `GET /scout/recorded` lists and serves, read-only, so a judge can watch a real run when live runs are refused or failing (FR-10.40, [ADR 0140](adr/0140-recorded-scout-run.md)). Empty turns it off. Files are read at request time; a file over 2 MB or with another schema is ignored. The VM's compose file mounts `evidence/scout` there |
| `RATCHET_TRUST_PROXY` | `0` | `1`: take the client IP for the per-IP limit from the first `X-Forwarded-For` entry. Only set it when a proxy you control overwrites that header; otherwise clients can fake their IP. Not needed on the demo VM, where uvicorn runs with `--proxy-headers` |

The budget counts the `cost_usd` of every Scout step, including audits and shadow calls. It does not cover `/v1/chat/completions`. In live mode the daily counters are kept in the ledger (`scout_run` rows in `spend`, `usd` 0) and restored at startup, so a restart or `update.sh` does not reset them ([ADR 0120](adr/0120-judge-access-and-a-persistent-scout-guard.md)). They reset at 00:00 UTC. The per-IP windows stay in memory and reset on restart, so no IP is stored. On the first restart after upgrading, runs from earlier that day have no rows and are not counted, once.

### Judge access and Tavily credits

Read with `Settings.scout_policy()` in `Runtime.reset`; a bad value stops startup with a `ValueError` naming the variable, and no message ever shows the judge code ([ADR 0120](adr/0120-judge-access-and-a-persistent-scout-guard.md)).

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_SCOUT_JUDGE_CODE` | empty | The access code for hackathon judges; empty turns judge access off. 16 to 256 printable ASCII characters with no spaces: use `openssl rand -hex 16`. Set it if and only if `RATCHET_SCOUT_JUDGE_RUNS_PER_DAY` is above 0. Judges send it once (`POST /scout/judge`) and get a signed cookie; never put it in a URL, the project page or the video |
| `RATCHET_SCOUT_JUDGE_RUNS_PER_DAY` | `0` | Runs per UTC day held back for judges, 0 to `RATCHET_SCOUT_MAX_RUNS_PER_DAY`. The public gets the run cap minus this; judges get their share plus whatever public room is left. Equal to the run cap: judges only, and the public gets 429 (allowed) |
| `RATCHET_SCOUT_JUDGE_BUDGET_USD` | unset | USD per UTC day held back for judges, 0 to `RATCHET_SCOUT_DAILY_BUDGET_USD`. Unset: the daily budget × judge runs / run cap, rounded down to the cent (7 of 22 runs of $3.00: $0.95). Above 0 only with judge access on |
| `RATCHET_SCOUT_JUDGE_SESSION_S` | `604800` | How long a judge cookie lasts, 60 s to 30 days (7 days: a judge who comes back does not need the code again). Rotating the code ends every session |
| `RATCHET_TAVILY_MONTHLY_CREDITS` | `0` | Tavily credits per cycle (the free plan gives 1,000 a month; the Nebius Builders Program may add more, unverified). `0` counts credits without a cap. A basic search costs 1 credit, an advanced one 2; the meter charges what Tavily reports in `usage.credits` |
| `RATCHET_TAVILY_JUDGE_CREDITS` | unset | Credits per cycle held back for judge runs, at most the monthly credits, above 0 only with judge access on. Unset: monthly × judge runs / run cap, rounded down (1,000 × 7 / 22 = 318) |
| `RATCHET_TAVILY_CYCLE_DAY` | `1` | Day of the month (1 to 28) the credit cycle starts, at 00:00 UTC. Tavily's own reset day is not documented (unverified): set the day your dashboard shows |
| `RATCHET_TAVILY_PACE_UNTIL` | unset | A date (`YYYY-MM-DD`, UTC). While it falls in the current cycle and is today or later, the public's daily credit allowance spreads what is left until that date instead of the cycle's end; unused credits roll forward. Recommended: the last day of judging (the plan assumes `2026-12-15`; unverified, see [ADR 0120](adr/0120-judge-access-and-a-persistent-scout-guard.md)) |

A public run whose search credits for today are used up still runs: the planner (pinned to the teacher) has already called Token Factory, and the writer answers without web sources, labelled "search unavailable" (FR-10.22). Credits never refuse a run; the run and USD caps bound the cost.

## Mock-mode demo

Read in `ratchet/config.py`. Ignored in live mode.

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_MOCK_WARMUP_DAYS` | `0` | Simulated days of `mixed` traffic, each followed by a night, run at startup and on every demo reset, so the demo opens with a trained student. `4` takes about 2 s and leaves a live student that answers about 80–90% of Scout's LLM calls (simulation). Writes a `warmup` event |
| `RATCHET_MOCK_WARMUP_REQUESTS` | `1500` | Requests per warm-up day |
| `RATCHET_MOCK_PACE` | `0.25` | `/scout/stream` waits this share of each call's simulated latency (mock Ultra 2.4 s → 0.6 s), so the page shows agents working. `0` = no waiting. `/scout/run` never waits |
| `RATCHET_MOCK_STRICT` | `0` | `1`: the mock models answer only the demo's own requests (synthetic tickets and Scout's research calls), and anything else gets 400 `mock mode only answers demo requests`, as before the generic mock world. `0`: any other request gets a deterministic answer from the generic world (`sim/generic.py`, [ADR 0041](adr/0041-generic-mock-world.md)): free text, an instance of its `response_format` schema, or a tool call to the mock teacher. Night School's mock trainer and golden eval read stored rows either way. With `1`, `POST /ratchet/demo/day` refuses the `schema` and `tools` workloads (400), which only the generic world answers |
| `RATCHET_MOCK_ENDPOINT_HOURS` | `24` | Hours a day the simulated student endpoint runs, 0 to 24: each simulated day (the demo, the warm-up, `python -m sim.simulate`) records one endpoint interval of this length from the start of the day, on the synthetic clock, for the cost report ([Cost accounting](#cost-accounting)). `0` = no endpoint (as with the hosted fallback). Anything outside 0 to 24 stops startup. Simulation assumption, not a measurement |
| `RATCHET_MOCK_TEACHER_REASONING_MEAN` | unset | The mock teacher's mean billed reasoning tokens. Unset, it bills exactly `RATCHET_TEACHER_REASONING_TOKENS` (300), the formula's assumption, so every number is as before. Set (more than 0, at most 100,000; anything else stops the gateway and `python -m ratchet.live` at startup, in live mode too, where nothing reads it), each request bills round(mean × exp(0.3 z − 0.045)) reasoning tokens, z a standard normal drawn from the request's text (the same request always costs the same), so the savings receipts have a gap between the formula and the bills to measure: with `420`, `ticket-triage`'s calibrated ratio is about 1.3 to 1.4 (simulation; [Savings receipts](#savings-receipts)). The simulator takes `--teacher-reasoning-mean` instead |

## Demo hosting: the simulation host (FR-11.30)

The demo VM can run a second gateway in mock mode beside the live one ([DEPLOY_NEBIUS_VM.md](DEPLOY_NEBIUS_VM.md), [ADR 0133](adr/0133-live-and-simulation-hosts.md)). Read in `ratchet/config.py`; a bad value stops startup, naming the variable. The four `RATCHET_DEMO_*` settings act in mock mode only and only when set (a warning in live mode); the compose file sets them on `ratchet-sim`.

| Variable | Default | Mode | Meaning |
|---|---|---|---|
| `RATCHET_LIVE_URL` | empty | both | The live gateway's public URL, for the pages' links to the live gateway (`/ratchet/stats` `links.live`). Empty, or `https://` with a host, at most 300 characters, no user info, no whitespace |
| `RATCHET_SIM_URL` | empty | both | The simulation's public URL (`links.sim`). Same rule. On the VM the compose file derives it from `RATCHET_SIM_DOMAIN` unless `.env` sets it (empty: no link) |
| `RATCHET_DEMO_RPM` | `0` (off) | mock | Demo actions (non-GET calls to `/ratchet/demo/*`) a minute per client IP, 0 to 600. Over it: 429 with `Retry-After`. The burst is `rpm // 6`, at least 1 |
| `RATCHET_DEMO_MAX_DAYS` | `0` (off) | mock | Reset the shared simulation before the day after this many simulated days. 0, or above `RATCHET_MOCK_WARMUP_DAYS`, at most 3650. Enforced when a visitor clicks `POST /ratchet/demo/day` (`auto_reset: "max_days"`) |
| `RATCHET_DEMO_MAX_REQUESTS` | `0` (off) | mock | Reset past this many ledger requests, 0, or above warm-up days x `RATCHET_MOCK_WARMUP_REQUESTS`, at most 10,000,000. Checked on a click and by a background pass every minute |
| `RATCHET_DEMO_IDLE_RESET_S` | `0` (off) | mock | Reset after this many seconds with no demo action and no request while the state differs from the last reset. 0 or 300 to 604,800 |

The warm-up rules exist so a cap can never reset the simulation right after its own warm-up. An automatic reset runs the warm-up again, writes a `demo_reset` event into the new ledger and clears everything visitors added.

## Demo VM: Caddy (`deploy/vm/`)

Read only by Docker Compose and Caddy on the demo VM ([DEPLOY_NEBIUS_VM.md](DEPLOY_NEBIUS_VM.md)), from `deploy/vm/.env`. The gateway does not read them.

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_DOMAIN` | required | Site name Caddy serves and gets a certificate for, e.g. `ratchet.dualmindlab.tech` or `<public-ip>.sslip.io`. First boot fills in the sslip.io name if it is still the placeholder |
| `RATCHET_API_GUARD` | `open` | `open`: every path is public. `guarded`: `/v1/*`, `/openai/*`, `/ratchet/demo/*` and `/ratchet/admin/*` return 403 unless the request has a matching `X-Ratchet-Admin-Token` header (the admin API also needs an admin key, `RATCHET_ADMIN_KEYS`, in live mode). Pages, `/scout*`, `/ratchet/stats` and `/ratchet/bom` stay public at the edge; whether the gateway serves the dashboard API without a key is `RATCHET_PUBLIC_DASHBOARD`. In mock mode, `guarded` also disables the dashboard's demo buttons. In live mode without `RATCHET_API_KEYS`, the gateway starts only with `RATCHET_ALLOW_UNAUTHENTICATED=1` |
| `RATCHET_ADMIN_TOKEN` | empty | Token for `guarded`. Hex, at least 32 characters (`openssl rand -hex 32`). Shorter or empty blocks the guarded paths for everyone. Caddy strips the header before it reaches the gateway |
| `RATCHET_SIM` | `on` | `on`: Caddy serves the `ratchet-sim` container at `RATCHET_SIM_DOMAIN`. `off`: no simulation host (the container still runs, unreachable); then also set an empty `RATCHET_SIM_URL=` so the live pages link nothing. `update.sh` refuses anything else |
| `RATCHET_SIM_DOMAIN` | `sim.<RATCHET_DOMAIN>` | The simulation host's name. It needs DNS: `sim.<ip>.sslip.io` resolves by itself; with your own domain add an A record (or a wildcard) first. Must differ from `RATCHET_DOMAIN` (`update.sh` refuses it before it builds anything) |
| `RATCHET_SIM_BOM_SIGNING_KEY` | empty | Optional key that signs the simulation's BOMs, handed to `ratchet-sim` as its `RATCHET_BOM_SIGNING_KEY`. Never the same as the live `RATCHET_BOM_SIGNING_KEY` (`update.sh` refuses it): a simulated BOM must not verify against the live public key |

`deploy/vm/create_vm.sh` and `deploy/vm/backup.sh` have their own settings, listed at the top of each script and in [DEPLOY_NEBIUS_VM.md](DEPLOY_NEBIUS_VM.md). Like the variables above they are in `config.DEPLOY_ENV`, so the gateway's startup check does not call them unknown:

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_VM_NAME` | `ratchet-demo` | The VM's name (`create_vm.sh` refuses a name that exists) |
| `RATCHET_VM_PLATFORM` | `cpu-e2` | Nebius platform; `cpu-e2` exists only in `eu-north1`, elsewhere use `cpu-d3` |
| `RATCHET_VM_PRESET` | `2vcpu-8gb` | Nebius preset, for example `4vcpu-16gb` |
| `RATCHET_VM_DISK_GB` | `30` | Boot disk size in GB |
| `RATCHET_SSH_USER` | `ubuntu` | The login user cloud-init creates (Nebius reserves `root` and `admin`) |
| `RATCHET_REPO_URL` | `https://github.com/harshbhx/ratchet.git` | What the VM clones into `/opt/ratchet` |
| `RATCHET_BACKUP_DIR` | `/var/backups/ratchet` | `backup.sh`: the host folder for ledger backups |
| `RATCHET_BACKUP_KEEP` | `14` | `backup.sh`: local copies to keep |
| `RATCHET_BACKUP_TAG` | empty | `backup.sh`: 1 to 32 letters, digits or `-`, added to the file name (`ratchet-<stamp>-<tag>.db.gz`); `update.sh` sets `pre-update`. Anything else exits 1 |

## Demo watchdog (`ratchet/watch.py`)

Read on the VM host by `python3 -m ratchet.watch` (standard library only), from `deploy/vm/.env` with the process environment winning, and by the gateway's `python -m ratchet.config check`, which refuses a bad value before `update.sh` replaces the container ([ADR 0132](adr/0132-demo-watchdog-and-rollback-first-updates.md), [DEPLOY_NEBIUS_VM.md](DEPLOY_NEBIUS_VM.md#watchdog)). A bad value names its variable and never echoes a secret. Variables marked secret are never printed, logged or sent.

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_WATCH` | `on` | `on` or `off`. `off`: every command but `status` prints one line and exits 0 |
| `RATCHET_WATCH_CLIENT` | `watchdog` | The `RATCHET_API_KEYS` name the probe uses; the host takes that name's key from `RATCHET_API_KEYS` itself. The pinned task's `clients` list in `deploy/vm/config/tasks.json` must name it |
| `RATCHET_WATCH_FAILURES` | `3` | Failed liveness checks, or failed ledger checks, in a row before a restart (2 to 20, one check every 2 minutes) |
| `RATCHET_WATCH_RESTART_COOLDOWN_S` | `600` | Seconds after a restart before another (120 to 86,400) |
| `RATCHET_WATCH_MAX_RESTARTS_PER_HOUR` | `2` | Restarts in a rolling hour; past it one alert and no restart (1 to 10) |
| `RATCHET_WATCH_START_GRACE_S` | `240` | No check counts while the container started less than this ago (0 to 3,600) |
| `RATCHET_WATCH_PROBE_TIMEOUT_S` | `200` | The probe's HTTP timeout, above the gateway's own teacher deadline (180 s) so its retries finish first (10 to 900) |
| `RATCHET_WATCH_REALERT_S` | `21600` | While a check keeps failing, repeat its alert this often (600 to 604,800) |
| `RATCHET_WATCH_CONNECT` | `public` | `public`: the probe resolves `RATCHET_DOMAIN` as anyone would. `local`: it connects to `127.0.0.1:443` with the domain as SNI and `Host` (certificates still checked), for a VM that cannot reach its own public IP. Whether Nebius VMs can (hairpin NAT) is unverified |
| `RATCHET_WATCH_HEARTBEAT_URL` | empty | Secret. An `https://` URL the probe GETs after a healthy probe that follows a healthy tick (healthchecks.io or any dead-man's switch: it alerts when the VM, cron or the watchdog is dead, which nothing on the VM can report) |
| `RATCHET_WATCH_STATE_DIR` | `/var/lib/ratchet` | State files and locks (`watch-tick.json`, `watch-probe.json`, `watch.paused`, `update.lock`), mode 0600 |
| `RATCHET_WATCH_FLAG_MAX_AGE_S` | `10800` | Gateway: a banner flag older than this reads `stale` and shows nothing (600 to 604,800) |
| `RATCHET_ALERT_WEBHOOK_URL` | empty | Secret. `https://` only. Alerts are POSTed here as JSON with a `text` field (Slack's incoming-webhook shape; whether Slack ignores the other keys, and Discord's `/slack` suffix, are unverified: `python3 -m ratchet.watch test-alert` is the check). Empty: alerts go to `/var/log/ratchet/watch.log` only |
| `RATCHET_ALERT_WEBHOOK_SECRET` | empty | Secret. When set, each alert carries `X-Ratchet-Signature: sha256=<hex HMAC-SHA256 of the body>` |
| `RATCHET_ALERT_KINDS` | `alert,rollback,train_failed,circuit,budget,admin,task_disabled,probe,watchdog_restart` | Kinds to send; an unknown kind is an error naming it. The watchdog sends only `probe` and `watchdog_restart` |
| `RATCHET_INSTANCE_NAME` | the host name | `gateway` in the alert body |
| `RATCHET_WATCH_KEY` | empty | Secret. `scripts/demo_check.py --chat` only: the `watchdog` key for its one live completion (from the environment, never a flag) |
| `RATCHET_DEMO_CHECK_KEY` | empty | Secret. `scripts/demo_check.py` only: the key for its free `GET /v1/models` check (the judge key's), unless `--key-env` names another variable |

The probe also reads `RATCHET_MODE`, `RATCHET_DOMAIN`, `RATCHET_API_GUARD`, `RATCHET_ADMIN_TOKEN`, `RATCHET_API_KEYS` and `RATCHET_TEACHER_FALLBACK_MODEL` from the same file.

## Live Night School (`ratchet/live.py`)

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_DATA_DIR` | `data` | Local folder synced to the bucket, laid out as the training job sees the bucket at `/data`: `night/train.jsonl`, `boms/student-vN.json`, and `adapters/<name>/ratchet_bom.json` and `NOTICE.md` (written after a night that trained, next to where the job wrote the weights; an adapter name that is not one folder name gets the `boms/` copy only). `python -m ratchet.live export` copies `adapters/<name>/` from here when the weights are here too (a bucket mount, or synced down) |
| `RATCHET_SYNC_CMD` | empty | Command that uploads `RATCHET_DATA_DIR` to the bucket, e.g. `aws s3 sync ./data s3://my-bucket --endpoint-url https://storage.eu-north1.nebius.cloud` (skipped if empty). Run before the training job and again after a night that trained, so the adapter's BOM and NOTICE.md reach its bucket folder; a failure of the second run is a `sync_failed` event and a non-zero exit. It must only upload: an option that deletes remote files missing locally (`--delete`) would delete the adapter weights |
| `RATCHET_TRAIN_CMD` | `bash deploy/train_job.sh {version}` | Command that runs training and **waits** for it. `{version}` and `{data}` are substituted |
| `RATCHET_ADAPTER_ROOT` | `/data/adapters` | Adapter folder as the vLLM endpoint sees it (`lora_path` = `<root>/<version>`). Also read by the gateway in live mode, which reloads an adapter from there when vLLM answers 404 for it |

Commands are split with `shlex.split` and run without a shell, so pipes and `&&` do not work. Wrap them in a script.

## Live-run harness

`python -m ratchet.liverun` ([ADR 0121](adr/0121-live-measurement-method.md), [RUNBOOK](RUNBOOK.md#live-measurement-run)) runs on the gateway's host with the gateway's `.env` (it reads the same `RATCHET_DB`, task registry, audit rates, release policy and prices), plus these variables, which only the harness reads. Its budget is a flag (`--max-usd`), never a variable.

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_LIVERUN_KEY` | empty (required) | The harness's gateway API key: the key of the `liverun:<key>` entry in `RATCHET_API_KEYS` (`--client` names another entry). Export it in the harness's shell only; never a flag, so it stays out of shell history and the process list, and it is never written or printed (results carry the name) |
| `NEBIUS_API_KEY_LIVERUN` | empty (`NEBIUS_API_KEY`) | A separate Token Factory key for the harness's own arms (the control arm and the untuned-Nano arm), so a runaway run cannot drain the key the gateway and the judge demo serve with. The gateway's own teacher calls keep `NEBIUS_API_KEY`. Results record which variable was used, never its value |
| `RATCHET_GIT_SHA` | empty | The code's commit when the image has no `.git` (`git rev-parse HEAD` comes first). 40 or 64 hex characters; anything else is ignored. Results carry a SHA-256 over the `ratchet` and `sim` sources either way |

The run's planned gate profile is set on the gateway, not here: `RATCHET_SEQUENTIAL=1` and `RATCHET_CANARY_AUDIT_RATE=0.5` ([Release policy](#release-policy-ratchetreleasepy)). `python -m ratchet.liverun run` refuses to start without them unless `--gate-profile as-set` is passed.

## Deploy scripts (`deploy/`)

Read only by `deploy/train_job.sh`, `deploy/student_endpoint.sh` and the one-command script `deploy/nebius/nebius.sh` ([WHERE_IT_RUNS.md](WHERE_IT_RUNS.md#one-command-to-host-it-all-on-nebius)) on the machine that runs the `nebius` CLI, never by the gateway (`config.DEPLOY_ENV` lists them so the traceability check holds each to a row here). The Nebius commands follow the CLI reference (auto generated on 19-Oct-2026, read Oct 21, 2026); [DEPLOYMENT.md](DEPLOYMENT.md#step-4-student-endpoint-vllm-with-runtime-lora) has the actions.

| Variable | Used by | Default | Meaning |
|---|---|---|---|
| `DRY_RUN` | both | `0` | `1`: `train_job.sh` and `student_endpoint.sh create` pass `--dry-run` (nebius validates the request and creates nothing); `start` and `stop`, which have no `--dry-run`, print the command instead of running it; the read-only `nebius ai endpoint list` still runs, and no uptime is recorded. Any value but `0` or `1` is refused (exit 2), so `DRY_RUN=true` never starts a paid GPU |
| `RATCHET_BUCKET_ID` | both | required (job; endpoint `create`) | Nebius Object Storage bucket ID, mounted at `/data` |
| `RATCHET_SUBNET_ID` | both | empty (the project's default subnet) | `--subnet-id` for the job and for `create` |
| `RATCHET_TRAIN_IMAGE` | `train_job.sh` | required | Training image built from `deploy/Dockerfile.train` |
| `RATCHET_TRAIN_PLATFORM` | `train_job.sh` | `gpu-l40s-a` | Nebius GPU platform |
| `RATCHET_TRAIN_PRESET` | `train_job.sh` | `1gpu-8vcpu-32gb` | Nebius preset |
| `RATCHET_REGISTRY_SECRET` | `train_job.sh` | empty | `--registry-secret`: a MysteryBox secret with `REGISTRY_USERNAME` and `REGISTRY_PASSWORD`, for a private training image (Nebius Container Registry) |
| `RATCHET_BUCKET_S3` | `train_job.sh` | empty | `s3://bucket[/prefix]`. When set, the script polls for `adapters/<version>/adapter_model.safetensors` (every 30 s, up to 2 h, the job's `--timeout`) so it can be used as `RATCHET_TRAIN_CMD` |
| `RATCHET_S3_ENDPOINT` | `train_job.sh` | empty | S3 endpoint URL for Nebius Object Storage |
| `RATCHET_STUDENT_KEY` | `student_endpoint.sh` | required (`create`) | Token clients must send to the endpoint. Passed to `--token`, never printed (`<RATCHET_STUDENT_KEY>` in the echoed command) |
| `RATCHET_ENDPOINT_NAME` | `student_endpoint.sh` | `ratchet-student` | Name `create` gives the endpoint, and the name `start`, `stop` and `status` look up in `nebius ai endpoint list --format json` to find its id |
| `RATCHET_ENDPOINT_ID` | `student_endpoint.sh` | empty | The endpoint's id (`metadata.id`). When set, `start` and `stop` use it without the list lookup, and `status` looks this id up instead of the name. Set it when the lookup cannot decide (two endpoints with the name, or a list with more pages) |
| `RATCHET_PYTHON` | `student_endpoint.sh` | `python` | Python 3 that reads the endpoint list (standard library only, `-I`) and runs `python -m ratchet.cost endpoint up\|down` after `create`, `start` and `stop` to record the endpoint's uptime in the ledger at `RATCHET_DB` (best effort: when recording fails, the deploy still succeeds and prints the command to run where the ledger is) |
| `RATCHET_SERVE_PLATFORM` | `student_endpoint.sh` | `gpu-l40s-a` | GPU platform for vLLM |
| `RATCHET_SERVE_PRESET` | `student_endpoint.sh` | `1gpu-8vcpu-32gb` | Preset for vLLM |
| `RATCHET_VLLM_IMAGE` | `student_endpoint.sh` | `vllm/vllm-openai:v0.18.0-cu130` | vLLM image. `vllm/vllm-openai:v0.18.0` is the CUDA 12 build of the same release (both on Docker Hub, pushed Mar 20, 2026), for a host driver too old for CUDA 13 |
| `RATCHET_SERVE_MAX_LEN` | `student_endpoint.sh` | `8192` | vLLM `--max-model-len`. The model card's example uses 262,144 tokens; ticket prompts need far less, and a longer prompt gets vLLM's 400 and escalates to the teacher |
| `RATCHET_SERVE_MAX_SEQS` | `student_endpoint.sh` | `64` | vLLM `--max-num-seqs` (concurrent sequences) |
| `RATCHET_SERVE_TEMPLATE_KWARGS` | `student_endpoint.sh` | `{"enable_thinking":false}` | vLLM `--default-chat-template-kwargs` (a JSON object, checked before `create`; no single quote), so the student answers without reasoning unless a request asks. Set but empty to leave the flag out (requests then carry their own `chat_template_kwargs`) |
| `RATCHET_REASONING_PARSER_PLUGIN` | `student_endpoint.sh` | empty | Absolute path in the container of the model card's `nano_v3_reasoning_parser.py` (for example `/data/nano_v3_reasoning_parser.py`, uploaded to the bucket). When set, vLLM gets `--reasoning-parser-plugin <file> --reasoning-parser nano_v3` |
| `RATCHET_STUDENT_BASE_MODEL` | both | `nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16` | Base model |
| `RATCHET_I_UNDERSTAND_THIS_COSTS_MONEY` | `nebius.sh` | unset | Must be `1`, together with `--yes`, for `up` or `down` to create or delete anything; without both, those commands are a dry run that calls no Nebius API. `plan` and `status` ignore it |
| `RATCHET_TRAIN_VERSION` | `nebius.sh` | `student-v1` | Version name of the training job `up --with-training` submits through `train_job.sh` |
| `RATCHET_SMOKE_CMD` | `nebius.sh` | empty (`scripts/demo_check.py`) | One executable called as `<cmd> https://<domain> --mode mock\|live` for the smoke check after `up` (tests set a stub) |

## Prices (`DEFAULT_PRICES`)

USD per 1M tokens (input / output). These are Token Factory list prices as read in October 2026 and unverified until `scripts/day1_check.py` passes (INV-9); verify them in the console.

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

Read in `ratchet/config.py` (`Settings.cost_policy()`); used by `ratchet/cost.py`, the gateway (`GET /ratchet/cost`, the `cost` block of `/ratchet/stats`, `POST /ratchet/cost/endpoint`), `python -m ratchet.cost`, `python -m ratchet.live night` and the simulator ([ARCHITECTURE.md](ARCHITECTURE.md#7-cost-accounting), [ADR 0023](adr/0023-utilization-aware-cost-accounting.md)). A bad value (negative, not a number, above $1,000 per GPU-hour or 1,024 GPUs, not a whole number of GPUs, an unknown model) stops startup with `ValueError` naming the variable; the CLI, `POST /ratchet/cost/endpoint` and the ledger refuse the same values. Each interval stores the rate it was recorded at, so changing a rate later prices new intervals only; the break-even and the hosted-fallback crossover use the rate the window's intervals recorded (`endpoint_usd_per_hour`), and the configured student rate only while no uptime is recorded. The crossover compares price per answer with the hosted fallback (`RATCHET_FALLBACK_STUDENT`) and assumes that untuned model serves as many requests as the tuned student; the report says so.

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_STUDENT_COST_MODEL` | `per_token` | Which savings the cost report's `headline` (and `/ratchet/stats` `cost.model`) shows: `per_token` (the student priced per token at the hosted Nano price, as every figure since v0.1) or `gpu_hours` (the GPU-hours the student endpoint and the training jobs ran). The report always has both views, and each request's `cost_usd` stays per token in both, so history never moves. Case and spaces are ignored; empty means `per_token` |
| `RATCHET_STUDENT_GPU_USD_PER_HOUR` | `1.55` | USD per GPU-hour of the student endpoint (default 1.55, the list price this setting was first written against; Nebius's pricing page lists an L40S at $1.35 on demand, [docs.nebius.com/compute/resources/pricing](https://docs.nebius.com/compute/resources/pricing), read Oct 22, 2026, so set 1.35 to match it; **unverified** until the day-1 checks pass; 0 to 1,000). The default for `python -m ratchet.cost endpoint up` and `POST /ratchet/cost/endpoint` when they name no price, for the simulator's intervals, and the price in the break-even |
| `RATCHET_STUDENT_GPUS` | `1` | GPUs the student endpoint runs on (0 to 1,024) (`1gpu-8vcpu-32gb` in `deploy/student_endpoint.sh`). The endpoint costs this many times the per-GPU price per hour |
| `RATCHET_TRAIN_GPU_USD_PER_HOUR` | `1.55` | USD per GPU-hour of a training job (the same L40S price: default 1.55, $1.35 on Nebius's pricing page; unverified). Prices the training interval `python -m ratchet.live night` records around each training run, and the simulator's |
| `RATCHET_TRAIN_GPUS` | `1` | GPUs a training job runs on (`RATCHET_TRAIN_PRESET`) |
| `RATCHET_PRICES_VERIFIED` | `0` | `1` once `scripts/day1_check.py` passed with a real key and the GPU rates above match your Nebius bill. Until then every report labels its prices "unverified list prices" (invariant 9); with `1` it says "prices confirmed by the operator". Ratchet never sets it by itself |

The simulated endpoint's hours a day are `RATCHET_MOCK_ENDPOINT_HOURS` ([Mock-mode demo](#mock-mode-demo)).

## Savings receipts

Read in `ratchet/config.py` (`Settings.receipt_policy()`); used by `ratchet/receipts.py`, the gateway's receipt endpoints and its demo days, `python -m ratchet.live seal`, `python -m ratchet.receipt seal` and `python -m sim.simulate --receipts` ([ADR 0029](adr/0029-verified-savings-receipts.md), [API](API.md#savings-receipts)). A bad value stops startup with `ValueError` naming the variable, because a sealed receipt can never be corrected (invariant 17). Receipts are signed with `RATCHET_BOM_SIGNING_KEY` ([Privacy and security](#privacy-and-security)) when it is set; without it they carry their sha256 only.

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_TAKE_RATE` | `0.25` | The fee's share of a receipt's billable lower bound (after spend outside requests), from 0 to 1. Stated in every receipt as a decimal string (`billing.share`). The fee is rounded down to a nano-USD and is 0 when the bound is negative or no task is calibrated |
| `RATCHET_RECEIPT_MIN_AUDITS` | `30` | Audited answers a task needs, in one receipt, before its baseline is calibrated and its served answers are billed; a whole number from 2 to 1,000,000. Below it the task is `insufficient_audits`: its savings are reported at the formula and not billed. At a 10% audit rate, 30 audits take about 300 student-served answers of the task in a day (per client, for a client's receipt) |

A day is sealed once it is over: on a live ledger 5 minutes after it ended (`receipts.SEAL_GRACE_S`; a live ledger's day 1 starts at its first request's time, not at 00:00 UTC, so run `python -m ratchet.live seal` hourly), on a simulated one when the next day starts. The mock teacher's reasoning (`RATCHET_MOCK_TEACHER_REASONING_MEAN`) is under [Mock-mode demo](#mock-mode-demo).

## Certified precision (`Settings.cert_policy()`)

How Night School's golden eval picks each version's threshold and what its certificate claims (FR-16.3, [ADR 0026](adr/0026-certified-thresholds-and-a-paired-release-gate.md)). The gateway (mock mode's golden eval) and `python -m ratchet.live night` read them; a bad value stops startup with a `ValueError` naming the variable. The simulator takes `--calibration`, `--cert-target` and `--cert-delta` instead. The gate's opt-in rules that read certificates are in the [release policy](#release-policy-ratchetreleasepy).

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_CALIBRATION` | `point` | How the threshold is chosen on the calibration half of the golden rows. `point`: the lowest threshold whose calibration answers reach 97% precision (v0.4's rule; routing and the reference runs do not move). `ltt`: Learn-then-Test over the fixed grid 0.95, 0.90, …, 0.50, keeping the lowest threshold whose precision is above `RATCHET_CERT_TARGET` with probability at least 1 − `RATCHET_CERT_DELTA` (1.01, serve nothing, when the highest fails). Case is ignored; empty means `point` |
| `RATCHET_CERT_TARGET` | `0.95` | The precision a certificate must show (`certified` when the lower bound reaches it), and Learn-then-Test's target. Strictly between 0 and 1 |
| `RATCHET_CERT_DELTA` | `0.05` | The certificate's error rate: the one-sided Clopper–Pearson bound holds with probability at least 1 − delta (the frontier's rows at delta / 10 each). Strictly between 0 and 1 |

Golden rows a target needs (with 0, 1, 2 or 3 wrong answers among the served held-out rows; `python -m ratchet.stats plan --target T --delta D`): 95% at delta 0.05 needs 59, 93, 124 or 153 served rows; 97% needs 99, 157, 208 or 257; 90% needs 29, 46, 61 or 76. Only the held-out half counts, so the golden set must be about twice that, divided by the coverage.

## Ledger and hot path

How the ledger file is written and how often the request path does periodic work ([ADR 0060](adr/0060-hot-path-caches-and-throttled-tick.md), [ARCHITECTURE §11](ARCHITECTURE.md#11-hot-path-and-performance)). A bad value stops startup with a `ValueError` naming the variable (the gateway, `python -m ratchet.live` and `python -m ratchet.cost`).

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_DB_SYNC` | `NORMAL` | How a ledger file is journaled, `NORMAL` or `FULL` (any case; empty means `NORMAL`). `NORMAL`: the WAL journal with `synchronous=NORMAL`. Readers never wait for the writer, the dashboard reads on a second, read-only connection, and a commit does not wait for the disk: a power cut or an operating-system crash (not a crash of the gateway process) can lose the last commits, never corrupt the file. `FULL`: the rollback journal with `synchronous=FULL`, v0.4's behaviour, for a ledger on a filesystem without shared memory (a network or bucket mount, where WAL does not work) or when every commit must reach the disk before the client gets its answer. Give the gateway, the cron jobs and the cost CLI the same value: `FULL` cannot take a file out of WAL while another process has it open, and stops startup instead. Mock mode's in-memory ledger has no journal to set (a bad value still stops startup) |
| `RATCHET_TICK_INTERVAL_S` | unset: `5` in live mode, `0` in mock mode | Seconds between two release-gate ticks run from the request path (0 to 3600). A tick less than this after the previous one, or while another thread is ticking, is skipped, so requests pay for a tick at most once per interval and never wait for one. A rollback can wait this long; the cron `python -m ratchet.live tick` runs regardless. `0` ticks after every request (mock mode, so simulations and tests behave as before); the mock demo's simulated days always tick |
| `RATCHET_STATS_TTL_S` | unset: `2` in live mode, `0` in mock mode | Seconds `/ratchet/stats` serves one computed payload (0 to 3600). However many dashboards poll, it is computed at most once per TTL, by one caller at a time; the others get the previous payload. `0` computes it on every read |

## Off-path quality checks

Where a request's teacher audit, shadow samples and arbitration run ([ADR 0072](adr/0072-off-path-audits-and-shadow.md), [ARCHITECTURE §15](ARCHITECTURE.md#15-off-path-quality-checks)). With workers, a request the gateway serves returns as soon as its answer is ready, and a bounded sidecar finishes its ledger row afterwards; Scout's agents, the simulator and the mock warm-up keep every check on the request path in any case. Read in `ratchet/config.py` (`Settings.sidecar`); a bad value stops startup naming the variable.

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_SIDECAR_WORKERS` | unset: `4` in live mode, `0` in mock mode | Threads that run requests' audits and shadows after the answer is sent, 0 to 64. `0`: every check stays on the request path, as before (mock mode's default, so simulations, the reference runs and the demo are unchanged). Each worker holds at most one teacher call at a time (an audit, or an arbitration bounded to 60 s), so more workers spend the teacher's rate limit faster, never more in total |
| `RATCHET_SIDECAR_QUEUE` | `1000` | Jobs that may wait for a worker, 1 to 100,000. A tenth of the places (at least one, once there are two) is kept for jobs that carry an audit. Each job holds its request's messages until it ends, so the request text queued and running jobs hold is bounded too, at 64 MiB (`sidecar.MAX_BYTES`, a tenth of it also kept for audits; at `RATCHET_MAX_INPUT_CHARS`' default a job holds at most about 0.5 MB): past it the queue counts as full. A drawn audit is never dropped: with every place taken it runs on the request path instead (`audits_inline`). A shadow without a place does not run for that request (`shadows_dropped`), counted per version in `/ratchet/stats` `sidecar` and in `ratchet_sidecar_full_total` |
| `RATCHET_SIDECAR_DRAIN_S` | `10` | Seconds the gateway's shutdown waits for queued jobs, 0 to 3600, after the server stopped taking requests. Jobs still queued then never run: their rows stay pending (`audited` 1 with `audit_agree` NULL, `shadow_version` with `shadow_serve` NULL), which the release gate ignores, and one `sidecar_dropped` event counts them per version. Keep it under the process manager's stop timeout: Docker sends SIGKILL 10 s after SIGTERM by default; the demo VM's Compose file gives the gateway 20 s (`stop_grace_period`), so raise both together |

While a request's checks are pending, its row holds what serving cost (`cost_usd` and its split), the token formula's baseline and `audited` 1 with `audit_agree` NULL; the job adds the audit's, the shadow's and the arbitration's cost call by call, writes the verdicts and, after a teacher audit, the baseline the audit prices, so the finished row is the one an inline run writes. The response says so with `ratchet.overhead_pending: true` ([API.md](API.md#off-path-checks)).

## Observability

Logs, metrics and request ids ([ADR 0062](adr/0062-request-ids-failures-logs-and-metrics.md), [API.md](API.md#request-ids), [RUNBOOK.md](RUNBOOK.md#failures-and-request-ids)). Read in `ratchet/config.py`; used by `ratchet/obs.py` and `ratchet/gateway.py`. A bad value stops startup with a `ValueError` naming the variable.

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_LOG_FORMAT` | unset: `text` in mock mode, `json` in live mode | How the `ratchet` logger writes to stderr: `json` (one object per line: `ts`, `level`, `logger`, `event`, then the fields) or `text` (`<ts> <LEVEL> <event> key=value ...`), any case. Lines carry ids, names, numbers and statuses, never prompt or answer text, URLs or keys |
| `RATCHET_LOG_LEVEL` | unset: `WARNING` in mock mode, `INFO` in live mode | `DEBUG`, `INFO`, `WARNING` or `ERROR`, any case. `INFO` writes one `chat` line per chat request (request id, client request id, trace id, client name, task, served_by, route, version, confidence, escalated, audited, latency_ms, wall_ms, cost_usd, status; `retry_count` on a retry, `idempotent_replay` and `replay_of` on an answer sent again for an `Idempotency-Key`, whose `cost_usd` is 0, and `overhead_pending` when its checks run after the answer), one `offpath` line when those checks end (row, audited, audit_agree, shadow_version, shadow_serve, shadow_agree, stored, the `cost_usd` they added, `pending_ms`; the same request id, so the two lines' costs add up to the row's) and every release-log event (`ledger_event`, scrubbed of URLs and error messages); `WARNING` keeps the `upstream_error` lines (role, exception class, status), so mock mode and the tests stay quiet |
| `RATCHET_METRICS_TOKEN` | empty | A bearer token that reads `GET /metrics` (and nothing else), for a Prometheus scraper, besides the admin and dashboard keys. At least 16 characters; it may not also be an API, admin or dashboard key (startup error). Compared as a constant-time digest, never stored or logged. Empty: only admin and dashboard keys read a private `/metrics`. A public dashboard (`RATCHET_PUBLIC_DASHBOARD=1`) makes `/metrics` public too |

Keep `RATCHET_METRICS_TOKEN` out of git, like the keys.

## Configuration checks

The gateway checks every setting before it opens the ledger or calls anything ([ADR 0070](adr/0070-configuration-checks-and-readiness.md)): `Runtime` stops with one `ConfigError` (a `ValueError`, also a `LicenseError` when a teacher is refused) that lists **every** problem, each naming its variable, never a key's value. `python -m ratchet.config check` runs the same check without starting anything (plus `RATCHET_TASKS_FILE`, which the gateway checks itself when it builds the registry), so it exits 2 exactly when the gateway would not start, and exits **0** when clean, **1** with warnings only, **2** with errors (or with warnings under `RATCHET_STRICT_CONFIG=1`); `--json` prints `{"mode", "strict", "exit", "issues": [{"level", "var", "message"}]}`. `deploy/vm/update.sh` runs it in the new image before it replaces the running container, and CI runs it on the defaults.

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_STRICT_CONFIG` | unset (off) | `1`: a warning stops startup like an error, and `python -m ratchet.config check` exits 2 on it. `0` or unset: warnings start the gateway; each is kept in `Runtime.config_warnings`, logged once as a `config_warning` line (the variable and the message, never the value) and counted under `checks.config` in `/readyz`. Anything but 0 or 1 is an error |

Errors (the gateway does not start):

| Rule | Variables |
|---|---|
| A number, whole number or 0/1 switch that does not parse (`5%`, `three`, `yes`) | every numeric setting and `RATCHET_PUBLIC_DASHBOARD`, `RATCHET_PUBLIC_BOM`, `RATCHET_SCRUB_STATS`, `RATCHET_OPENAPI`, `RATCHET_ALLOW_UNAUTHENTICATED`, `RATCHET_STRICT_CONFIG` |
| `mock` or `live` (an empty or misspelled mode used to start live mode) | `RATCHET_MODE` |
| From 0 to 1 | `RATCHET_AUDIT_RATE`, `RATCHET_CONFIDENCE_THRESHOLD` |
| A whole number from 1 to 16 | `RATCHET_SAMPLES` |
| More than 0 and at most 0.5 (more would hold most examples out of training) | `RATCHET_GOLDEN_FRACTION` |
| 0 or more | `RATCHET_API_KEY_DAILY_REQUESTS`, `RATCHET_MAX_BODY_BYTES`, `RATCHET_TEACHER_REASONING_TOKENS`, the Scout limits and budget, `RATCHET_MOCK_WARMUP_DAYS`, `RATCHET_MOCK_WARMUP_REQUESTS`, `RATCHET_MOCK_PACE` |
| A whole number from 0 to 1,000,000; seconds from 0 to 86,400 ([Retries and idempotency](#retries-and-idempotency)) | `RATCHET_IDEMPOTENCY_CACHE`; `RATCHET_IDEMPOTENCY_TTL_S`, `RATCHET_SEEN_DEDUP_S` |
| A whole number from 0 to 64; from 1 to 100,000; seconds from 0 to 3600 ([Off-path quality checks](#off-path-quality-checks)) | `RATCHET_SIDECAR_WORKERS`; `RATCHET_SIDECAR_QUEUE`; `RATCHET_SIDECAR_DRAIN_S` |
| An `http://` or `https://` URL with a host (the message never repeats the URL, which may hold a password) | `RATCHET_TEACHER_BASE` (required), `RATCHET_STUDENT_BASE`, `RATCHET_TEACHER_FALLBACK_BASE` (empty is fine) |
| Distillable under the license registry (INV-1) | `RATCHET_TEACHER_MODEL`, `RATCHET_JUDGE_MODEL`, `RATCHET_TEACHER_FALLBACK_MODEL` |
| Each setting's own rules, as before, now listed together (a rule that covers several variables, such as the cost settings, reports each bad one) | timeouts, retries, breakers, `RATCHET_ON_TEACHER_FAILURE`, `RATCHET_UNKNOWN_TASKS`, the request limits, the parameter allowlists and token caps, the cost settings, `RATCHET_DB_SYNC`, the intervals, the log settings, the access switches and `RATCHET_CSP`, the key rings and `RATCHET_METRICS_TOKEN`, the [reserved settings](#reserved-settings) |
| The base64 of a 32-byte Ed25519 seed, with the `sign` extra installed, when set | `RATCHET_BOM_SIGNING_KEY` |
| Live mode: a key for a Token Factory teacher (a teacher on another host may run without one: a warning) | `NEBIUS_API_KEY` |
| Live mode: API keys, unless something in front checks callers | `RATCHET_API_KEYS` (or `RATCHET_ALLOW_UNAUTHENTICATED=1`) |
| Live mode: a ledger file that can be written (not a directory, its directory exists and is writable) | `RATCHET_DB` |
| Live mode: every `RATCHET_<FIELD>` parses and is in its range, each reported on its own (`ReleasePolicy.env_issues`); then 0 < `RATCHET_LIVE_FLOOR` <= `RATCHET_MIN_PRECISION` <= 1; `RATCHET_MIN_AUDITS` and `RATCHET_MIN_SHADOW` at least 1; `RATCHET_TOLERANCE` from 0 to 0.2 (`ReleasePolicy.validate`) | the [release policy](#release-policy-ratchetreleasepy) |

Warnings (the gateway starts, unless `RATCHET_STRICT_CONFIG=1`):

| Rule | Variables |
|---|---|
| A `RATCHET_*` variable Ratchet does not read, with the closest known name (`RATCHET_AUDIT_RAT`: did you mean `RATCHET_AUDIT_RATE`?). The deploy scripts' and the demo VM's own variables (`RATCHET_DOMAIN`, `RATCHET_API_GUARD`, `RATCHET_ADMIN_TOKEN`, ...) are known, because Docker Compose hands the VM's whole `.env` to the gateway | any |
| A model without a price in `DEFAULT_PRICES`: its calls are costed as another model's | `RATCHET_TEACHER_MODEL`, `RATCHET_JUDGE_MODEL`, `RATCHET_TEACHER_FALLBACK_MODEL`, and `RATCHET_FALLBACK_STUDENT` in live mode without a student endpoint |
| Live mode: a student endpoint without a token | `RATCHET_STUDENT_KEY` |
| Live mode: an audit rate under 1%, at which a canary may never collect `RATCHET_MIN_AUDITS` audits | `RATCHET_AUDIT_RATE` |
| Live mode: a variable only mock mode reads, set to anything but its default | `RATCHET_MOCK_WARMUP_DAYS`, `RATCHET_MOCK_WARMUP_REQUESTS`, `RATCHET_MOCK_PACE`, `RATCHET_MOCK_ENDPOINT_HOURS`, `RATCHET_MOCK_TEACHER_REASONING_MEAN` |
| A 0/1 switch spelled otherwise (`true` reads as off, `false` as on for redaction) | `RATCHET_TRUST_PROXY`, `RATCHET_MERGE_SYSTEM`, `RATCHET_STORE_FALLBACK_TEACHER`, `RATCHET_PRICES_VERIFIED`, `RATCHET_REDACT_PII` |

`config.KNOWN_ENV` lists every variable the package reads; `python scripts/reqtrace.py --check` fails when code reads one that is not listed (or lists one nothing reads), so a new variable goes in `KNOWN_ENV` and in this file. Variables the deploy scripts read go in `config.DEPLOY_ENV`.


## Success metrics

Read in `ratchet/config.py` (`Settings.kpi_window()`); used by `ratchet/kpis.py` through `GET /ratchet/kpis`, the `kpis` block of `/ratchet/stats` and the simulator ([ADR 0083](adr/0083-success-metrics-from-the-ledger.md)). SM-2 needs the release gate's `RATCHET_MIN_AUDITS` audits in the window before it reports met or not met.

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_KPI_WINDOW_DAYS` | `3` | The steady state the success metrics describe: SM-1 to SM-3 and NFR-1 cover the last this many ledger days with traffic (days without requests are skipped). A whole number from 1 to 3,650; anything else stops startup with `ValueError` naming the variable. `GET /ratchet/kpis?days=N` overrides it for one report |

## Teacher watch (`Settings.teacher_watch_limits()`)

How much paid teacher work Night School spends each night checking the teacher (FR-5.40, FR-5.41, [ADR 0033](adr/0033-teacher-watch.md)). The checks that come free with traffic (a stored request's first re-test in each check epoch) need no setting. Read by the gateway (mock nights), the simulator and `python -m ratchet.live night`; a value that is not a whole number from 0 to 10,000 stops startup with a `ValueError` naming the variable. The freeze rule and the switch are [release policy](#release-policy-ratchetreleasepy) fields (`teacher_noise_max`, `alpha_teacher`, `teacher_watch`).

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_TEACHER_PROBES` | `20` | Stored labels Night School asks the teacher about again each night (one call each, as an audit asks: temperature 0, under the example's stored conditions), so traffic with few repeats is checked too. Only labels that can be checked: the configured teacher's, no redaction placeholders, a task that names fields and is not pinned, not contested, not re-tested yet in the current epoch; in hash order. Each one is that request's first re-test of the epoch. 0: none |
| `RATCHET_ADJUDICATE_MAX` | `50` | Contested labels Night School adjudicates each night, two more teacher answers each: a strict majority of the three becomes the label and ends the quarantine; without one the label stays quarantined and is tried again after the labels never adjudicated. 0: none (contested labels stay out of training) |

At the defaults a night makes at most 120 teacher calls (20 probes, 50 adjudications of two calls each); at the teacher's list price (unverified) and about 200 tokens in and 300 out a call, that is about $0.13. The night's `teacher_watch` event states what it spent when anything happened; the cost report does not count it yet (ADR 0033).

## Release policy (`ratchet/release.py`)

`ReleasePolicy` defaults. The gateway (both modes) and `ratchet.live` read each field from the variable in the second column (`ReleasePolicy.from_env()` reads `RATCHET_<FIELD>` for every field). Integers stay integers, the rest are floats. Mock mode uses `min_audits=15` unless `RATCHET_MIN_AUDITS` is set, so the demo can show the anytime-valid gate with `RATCHET_SEQUENTIAL=1`. The simulator ignores them and takes its own flags (`--min-audits`, `--release`, `--promote-evidence`, `--canary-audit-rate`, `--paired-gate`, `--min-certified`, `--drift-alarm`, `--drift-boost`, `--drift-early-night`, `--teacher-watch`; `--teacher-noise` and `--teacher-noise-day` make its mock teacher unstable).

| Field | Variable | Default | Used in |
|---|---|---|---|
| `min_precision` | `RATCHET_MIN_PRECISION` | 0.95 | Golden gate and shadow: minimum precision of answers the candidate would serve |
| `min_coverage` | `RATCHET_MIN_COVERAGE` | 0.25 | Golden gate: minimum share of golden traffic it would serve |
| `tolerance` | `RATCHET_TOLERANCE` | 0.02 | Max allowed drop vs live (precision and coverage) in the golden gate, and coverage vs live in shadow |
| `min_shadow` | `RATCHET_MIN_SHADOW` | 100 | Would-serve shadow answers needed before a shadow decision |
| `min_audits` | `RATCHET_MIN_AUDITS` | 20 | Teacher audits needed in a canary stage before it is promoted, and before the Wilson rules roll back or alert. The anytime-valid rules have no minimum for a rollback or an alert |
| `live_floor` | `RATCHET_LIVE_FLOOR` | 0.92 | Audited precision floor. The Wilson rule rolls back when the Wilson upper bound is below it; the e-processes test "precision at or above the floor". Strictly between 0 and 1 |
| `min_task_test_n` | `RATCHET_MIN_TASK_TEST_N` | 10 | Held-out (odd-half) golden rows a task needs before the golden gate checks its own numbers ([per-task calibration](#per-task-calibration), FR-14.1). A whole number, 0 or more |
| `task_mode` | `RATCHET_TASK_MODE` | `disable` | What the golden gate does with a task that fails its own check: `disable` it on the candidate (its threshold becomes 1.01, a `task_disabled` event; the version ships for its other tasks) or `reject` the candidate. Case and surrounding spaces are ignored; empty means the default |
| `sequential` | `RATCHET_SEQUENTIAL` | 0 | 1 switches the canary to the anytime-valid rule ([ADR 0025](adr/0025-anytime-valid-release-gate.md)): rollback when the e-value over every audit the canary has had reaches 1/`alpha_rollback`, with no minimum count; promotion on this stage's audits (point estimate, or evidence with `promote_evidence`); retirement at `max_stage_audits`. 0: the Wilson rule above. 0 or 1 |
| `alpha_rollback` | `RATCHET_ALPHA_ROLLBACK` | 0.05 | Sequential rule: the chance of ever rolling back a version whose precision stays at or above the floor (threshold 1/alpha: 20). Strictly between 0 and 1 |
| `promote_evidence` | `RATCHET_PROMOTE_EVIDENCE` | 0 | 1 promotes a canary stage only when the e-value that precision is above the floor reaches 1/`alpha_promote` (56 audits that all agree at a 0.92 floor and 5%), instead of on the point estimate. Needs `sequential=1`. 0 or 1 |
| `alpha_promote` | `RATCHET_ALPHA_PROMOTE` | 0.05 | Sequential rule with `promote_evidence`: the chance that a version at or below the floor reaches live. Strictly between 0 and 1 |
| `max_stage_audits` | `RATCHET_MAX_STAGE_AUDITS` | 400 | Sequential rule: a canary stage with this many audits and no decision is retired (event `inconclusive`); 0 = never. It counts audits, not time. Must be at least `min_audits`, and with `promote_evidence` at least the audits a promotion needs (startup says how many) |
| `alert_eprocess` | `RATCHET_ALERT_EPROCESS` | 1 | 1: the live alert fires when the e-value against "precision at or above the floor" over the audits since the version went live reaches 1/`alpha_alert`, with no minimum count. 0: the Wilson rule (at least `min_audits` audits and the upper bound below the floor). 0 or 1 |
| `alpha_alert` | `RATCHET_ALPHA_ALERT` | 0.01 | The live alert's error rate: a live version at or above the floor is alerted on with probability at most this (threshold 1/alpha: 100). Strictly between 0 and 1 |
| `min_certified_precision` | `RATCHET_MIN_CERTIFIED_PRECISION` | 0.0 | Golden gate ([ADR 0026](adr/0026-certified-thresholds-and-a-paired-release-gate.md)): reject a candidate whose certified lower bound on precision (recomputed from its certificate's counts) is below this, or that has no certificate. 0: off. From 0 to 1 |
| `paired_gate` | `RATCHET_PAIRED_GATE` | 0 | 1: the golden gate's regression check compares the candidate with the re-scored live version row by row on the same held-out rows (the candidate's `paired` record), instead of the two point estimates within `tolerance`. It rejects only a regression that is both larger than `tolerance` (as a share of the rows) and significant (exact McNemar, `regression_alpha`), on coverage or on wrong answers, or a negative flip rate above `max_nfr`. Without a paired record against the live version it falls back to the unpaired rule. 0 or 1 |
| `regression_alpha` | `RATCHET_REGRESSION_ALPHA` | 0.05 | The paired gate's error rate for each McNemar test: a candidate no worse than live is rejected with probability at most this. Strictly between 0 and 1 |
| `max_nfr` | `RATCHET_MAX_NFR` | 1.0 | The paired gate: reject when the 95% Wilson lower bound of the negative flip rate (answers live served right that the candidate serves wrong) is above this. 1: off. More than 0 and at most 1 |
| `min_judge_pass` | `RATCHET_MIN_JUDGE_PASS` | 0 (off) | Golden gate on free-text tasks ([free-text tasks](#free-text-tasks), FR-14.40): the judge's pass rate the answers a candidate would serve need. Below it overall, the candidate is rejected; below it on a task with `min_task_test_n` held-out rows, that task fails as under `task_mode`. With it on, a free-text task the candidate would answer that no judge graded fails too. From 0 to 1 |
| `drift_alarm` | `RATCHET_DRIFT_ALARM` | 100 | The label-free drift alarm ([ADR 0032](adr/0032-label-free-drift-alarm.md)): the alarm threshold of each (version, task) stream's test martingale on the student's confidence. A stream that sees no traffic change alarms with probability at most 1/threshold over its whole life (1% at 100); with K streams, at most K/threshold for any of them (`GET /ratchet/drift` reports both). A version holds at most 17 streams: 16 task streams and one for every later task name. An alarm writes a `drift` event and suspends the version's certificate; it decides no release. On by default; it reads the ledger in the gate's tick and never touches the request path. 0 turns it off; otherwise more than 1 |
| `drift_audit_boost` | `RATCHET_DRIFT_AUDIT_BOOST` | 1.0 | After a drift alarm, the alarmed version's audit rate is multiplied by this while it serves (canary or live), up to 0.5, and never lowered (FR-6.40). The boosted rate is logged as `label_p`; a newer version going live clears it. 1: off. 1 or more, and needs the monitor on |
| `drift_early_night` | `RATCHET_DRIFT_EARLY_NIGHT` | 0 | 1: when a drift alarm came from a request logged after the last `trained` event, Night School needs half the usual new examples (`max(1, min_new_examples // 2)`; FR-6.41). Never while a release is in flight. 0 or 1, and needs the monitor on |
| `teacher_noise_max` | `RATCHET_TEACHER_NOISE_MAX` | 0.05 | The teacher watch ([ADR 0033](adr/0033-teacher-watch.md)): the rate at which a fresh teacher label of a request may disagree with an independent earlier label of it before the watch calls the teacher unstable (H0 of the freeze rule). Strictly between 0 and 1. Raise it on purpose for a teacher you know is noisier (a sampling teacher, a free-text-heavy task mix); the runbook says when |
| `alpha_teacher` | `RATCHET_ALPHA_TEACHER` | 0.01 | The freeze rule's error rate: a teacher within `teacher_noise_max` freezes Night School with probability at most this per check epoch, however often the gate looks (threshold 1/alpha: 100). Strictly between 0 and 1 |
| `teacher_watch` | `RATCHET_TEACHER_WATCH` | 1 | 1: the teacher watch acts: contested labels are left out of training and the golden set, the freeze rule pauses Night School (`teacher_drift`, FR-6.42), the golden gate warns about the agreement ceiling (FR-6.43), and Night School probes and adjudicates. 0: none of that; the free checks are still recorded (one indexed lookup per stored duplicate) and shown by `GET /ratchet/teacher`, so turning it on later has history. 0 or 1 |

`ReleasePolicy` checks its values when it is built (`from_env` included), so a typo stops startup with a `ValueError` naming the variable: whole numbers for the integer fields, `min_precision`, `min_coverage`, `tolerance`, `min_judge_pass` and `min_certified_precision` from 0 to 1, `live_floor` and the alphas strictly between 0 and 1, `max_nfr` above 0 and at most 1, `drift_alarm` 0 or more than 1, `drift_audit_boost` 1 or more, `teacher_noise_max` and `alpha_teacher` strictly between 0 and 1, the switches 0 or 1, `task_mode` `disable` or `reject`. It also refuses settings that would decide nothing: `promote_evidence=1` without `sequential=1`, with `sequential=1` a `max_stage_audits` below `min_audits` or too small for `promote_evidence` to ever pass (the message names the audits needed), and a drift action (`drift_audit_boost` above 1 or `drift_early_night=1`) with `drift_alarm=0`. The live gateway's startup check (and `python -m ratchet.config check`) also applies the rules across fields, which building a policy does not, so tests and simulations may still use any value: 0 < `live_floor` <= `min_precision` <= 1 (a rollback floor above the golden gate's bar would roll back versions the gate admitted), `min_audits` and `min_shadow` at least 1, `tolerance` at most 0.2 ([Configuration checks](#configuration-checks)).

A new `ReleasePolicy` field needs a row here: `python scripts/reqtrace.py --check` derives the variable names from the dataclass and fails on an undocumented one. No policy field is reserved today ([Reserved settings](#reserved-settings)).

Other constants:

| Constant | Where | Value |
|---|---|---|
| `CANARY_STEPS` | `release.py` | `[10, 50]` then 100 |
| `FREQUENT` | `nightschool/dataset.py` | 3: requests seen this often always go to training |
| `min_new_examples` | `run_night()` | 50 new train rows needed to train |
| `target_precision` | `cascade_eval()` / `LiveEvaluator` | 0.97 for threshold calibration (the `point` rule) (`calibration.DEFAULT_TARGET_PRECISION`); a task's `policy.target_precision` overrides it for that task |
| `CERT_GRID` | `stats.py` | 0.95, 0.90, …, 0.50: the thresholds Learn-then-Test and the frontier use (fixed in advance, never from the data) |
| `floor` | `cascade_eval()` | 0.5: lowest allowed threshold |
| Drift monitor | `drift.py` | `BINS` 10,000 (a confidence's rank is taken to 4 decimals); `BETS` 0.05, 0.15, 0.3, 0.6, 1.0 (the power betting functions); switching rate 0.01 for a stream's first 100 confidences, then 1/n; `MAX_POINTS` 200 trajectory points per stream; seed 7 for the tie-breaking draws (never the router's generator) |
| Confidence weights | `confidence.score()` | 0.4 × token probability + 0.6 × agreement |
| Student sampling | `router._sample` | `temperature=0.7`, `logprobs=True` |
| Teacher temperature for `n` > 1 | `providers.CHOICES_TEMPERATURE` | 0.7, when the client sets no temperature (at 0 every choice would be the same) |

## Reserved settings

The evidence features (PRD §6.16) are built one at a time. Their settings are declared already, with defaults that keep today's behaviour ([ADR 0024](adr/0024-evidence-foundations.md)), so later items only fill in logic. **Until a feature is built, any value but its default stops startup** with a `ValueError` that names the variable and the feature: a setting that does nothing yet must not look like it protects anything. The gateway, the simulator and `python -m ratchet.live` all check (`Settings.check_reserved()` in `Ratchet`, and `ReleasePolicy` when it is built). Unset, empty, or the default spelled another way (`0.50`, ` Operations `) is fine. Each feature's item removes its rows from this section and documents them where they belong.

`Settings` (`config.RESERVED_SETTINGS`):

| Variable | Default | Feature that will read it |
|---|---|---|
| `RATCHET_BATCH_API` | `operations` | Clean-room backfill (FR-16.8): the Token Factory batch API (`operations` or `openai`) |
| `RATCHET_BATCH_DISCOUNT` | `0.5` | Clean-room backfill (FR-16.8): the batch price discount (unverified, like every price) |
| `RATCHET_BACKFILL_MAX_TOKENS` | `2048` | Clean-room backfill (FR-16.8): the output cap the worst-case budget is computed from |
| `RATCHET_GATEWAY_LEASE` | `warn` | One gateway process per live ledger (NFR-60, waiting for the leases table): `warn` will write a `gateway_lease` event when a second process serves the same ledger and keep serving, `exclusive` will refuse to start, `off` will do nothing. Any other value stops startup now, also through `Settings.scout_policy()` |

`ReleasePolicy` (`release.RESERVED_POLICY`): none today. Every field declared for the evidence features is built; the check stays for the next feature that declares a field before it is built.

## Tasks

A task tells Ratchet when two answers "agree" and which answers are valid (`ratchet/tasks.py`, [ADR 0021](adr/0021-field-comparators-and-task-specs.md)). Its `fields` list the parts of the answer that must match, each a path and a typed comparator (`ratchet/compare.py`); anything else in the answer, such as a free-text `reply`, is ignored.

Tasks come from four places ([ADR 0022](adr/0022-task-registry.md)): the built-in ones below (`ratchet/tasks.py`), a tasks file, the admin API ([API.md](API.md#tasks)), and auto tasks derived from a request's `response_format` json_schema ([Structured outputs](#structured-outputs), [ADR 0042](adr/0042-auto-tasks-from-response-format-schemas.md)). The name `embeddings` is reserved for the `/v1/embeddings` pass-through's request rows and cannot be a task's ([Other OpenAI endpoints](#other-openai-endpoints)). Read in `ratchet/config.py`; used by `ratchet/taskspec.py`, `ratchet/router.py` and `ratchet/gateway.py`:

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_TASKS_FILE` | empty | A file of task specs ([Adding a task](#adding-a-task)), JSON, or YAML for a `.yaml` or `.yml` file (needs PyYAML: `pip install pyyaml`, or the `yaml` extra). Read when the gateway starts (and on a mock demo reset) and by `python -m ratchet.live`. A file task may replace a built-in one. A bad file stops startup with every error and its path (`tasks[2].fields.total.abs_tol: must be a number >= 0`); a YAML file without PyYAML is an error that says so |
| `RATCHET_UNKNOWN_TASKS` | `distill` | What a request naming a task that is not registered gets. `distill` (v0.4): a task without fields made up for the name, distilled like any other. `teacher`: the teacher answers (route `unknown_task`), the student is not called and nothing is stored. `reject`: 400 `unknown_task`, listing the registered tasks the key may use. Under `teacher` and `reject` an `X-Ratchet-Task` header that is not a valid task name is a 400 `unknown_task` too (under `distill` it means `default`, as in v0.4). Case-insensitive; anything else stops startup |
| `RATCHET_SCHEMA_REPAIR` | `1` | `0`: no repair call. Otherwise a complete teacher answer that breaks its request's `response_format` gets one more call to the same teacher, shown its answer and the schema errors; the repaired answer is served (unless it was cut off or is empty: the first answer is then kept) and, when valid, stored with the request's own messages. Both calls are paid for in `cost_usd`; the baseline and the client's `usage.prompt_tokens` count the request's own prompt, and `latency_ms` both calls. Read in `ratchet/config.py`, used by `ratchet/router.py` (`Ratchet._repair`) |
| `RATCHET_MODEL_POLICY` | `lenient` | What a request whose `model` Ratchet does not know gets ([task resolution](#task-resolution), [ADR 0048](adr/0048-model-field-routing.md)). `lenient`: it is served as `ratchet` (its task from the other rules) and the response carries `X-Ratchet-Model-Alias: <the id>` (UTF-8 percent-encoded except for visible ASCII), so clients with a hard-coded model name work unchanged. `strict`: a 404 `model_not_found` ("The model 'gpt-4o' does not exist"), before anything is logged or billed, also when a header names the task. Under both, a missing or empty `model`, `ratchet`, `ratchet/<task>` and the aliases are accepted (`ratchet/<task>` for an unregistered task then follows `RATCHET_UNKNOWN_TASKS`, and `GET /v1/models/{id}` gives the same answer), and `ratchet/` with a name that is not a task name is a 404. Case-insensitive; anything else stops startup |
| `RATCHET_MODEL_ALIASES` | empty | Model ids that name a task, as `model=task` pairs: `gpt-4o-mini=ticket-triage,openai/gpt-4o=default`. For clients that send a fixed model name. An alias is 1 to 256 characters without spaces, `,` or `=`, and must not be `ratchet` or start with `ratchet/` or `ratchet:`; at most 256. The task need not be registered yet: until it is, requests for the alias follow `RATCHET_UNKNOWN_TASKS`. Aliases are listed in `GET /v1/models` (with `alias_of`). A bad entry stops startup |
| `RATCHET_AUTO_TASK` | `off` | `system_prompt`: a request no header, metadata, model, alias or match rule places gets an auto task of its own per first system message, `auto-sys-` and the first 10 hex digits of the message's SHA-256 (the name holds a digest, never the prompt). A new auto task compares whole answers like `default`, is never pinned, and is registered and stored in the ledger (so a restart and Night School find it) once its first request has passed the quota. A request without a system message gets `default`. An auto task is a registered task, so `RATCHET_UNKNOWN_TASKS` does not apply to it; the admin API may replace it with an API task. Case-insensitive; anything else stops startup |
| `RATCHET_MAX_AUTO_TASKS` | `200` | The most auto tasks that may exist (at most 50 in mock mode, whose shared demo a reset clears). Past it, or when the registry has only its last 50 slots free (kept for file and API tasks, so request traffic cannot lock the admin API out), a request with a new system prompt gets `default`, and one `auto_task_cap` event is logged once such a request is admitted. `0`: none is ever made. An admin can delete an auto task (`DELETE /ratchet/tasks/{name}`) to free its place. A whole number from 0 to 250 (half the registry); anything else stops startup |
| `RATCHET_FIXTURES_DIR` | `fixtures` | Where fixture files a task spec names must live (`taskspec.resolve_fixture`: a path that resolves outside it, through `..` or a symlink, is refused): the SQL scripts that [text-to-SQL tasks](#text-to-sql-tasks) run their answers on. Relative to the working directory; the gateway and `python -m ratchet.live` both read it |

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
| `number` | a JSON number, or a string with currency symbols (`$`, `€`, `₹`, any Unicode currency sign), an ISO 4217 code in capitals before or after (`USD 12`, `12.50 EUR`), thousands separators that fit `decimal` (`1,234.50`; `1.234,50` with `decimal=","`; spaces, apostrophes, Indian grouping `1,23,456.50`), `(12.00)` for a negative, a trailing `%`; Indian amounts: `Rs.`, `Rs` or `Re.` before or after, `rupees` after, a closing `/-`, and `lakh` (`lakhs`, `lac`, `lacs`), `crore` (`crores`) or `lakh crore` after (`Rs. 1.5 lakh` is 150000, scaled exactly; `Cr` and `L` are not read) | a float; NaN, infinities, ambiguous groupings (`1,5` under `decimal="."`) and strings over 100 characters are invalid | `abs(a − b) <= max(abs_tol, rel_tol × max(abs(a), abs(b)))`; exact with both tolerances 0 (`1` equals `1.0`) | `abs_tol`, `rel_tol`, `decimal` (`.` or `,`), `percent` (`number`: `12%` is 12; `fraction`: 0.12) |
| `ordinal` | a label in `order` | its position | positions at most `tolerance` apart | `order` (required, lowest first), `tolerance`, `aliases` |
| `text` | a string (or an integer) | `exact`: case-folded, whitespace collapsed; `loose` (default): NFKC, case-folded, punctuation read as a space between words (`Acme,Inc.` is `acme inc`, `hello-world` is `hello world`) except apostrophes and the dots of initials, which join (`O'Brien` is `obrien`, `U.S.A.` is `usa`), whitespace collapsed, trailing `strip_suffixes` tokens dropped while another token is left; `token_set`: the set of loose tokens | equal | `mode`, `strip_suffixes` (`("inc", "ltd", "llc", "gmbh", "pvt ltd")`) |
| `date` | ISO date or datetime (`2026-10-03`, `2026-10-03T12:00:00Z`, offsets), `2026/10/03`, `3 Oct 2026`, `3 October 2026`, `Oct 3 2026`, `October 3rd, 2026`, `3.10.2026`, `10/03/2026` (month first) | the ISO date; with `granularity="datetime"`, the ISO datetime in UTC (naive times read as UTC) | equal | `day_first` (`03/10/2026` is 3 October), `granularity` (`date`: the day as written, even with a time zone; `datetime`) |
| `language` | an ISO 639-1, 639-2 or 639-3 code, a locale tag (`en-US`, `pt_BR`, `zh-Hant-TW`, `es-419`: the whole value, subtags joined by `-` or `_`, led by a 639-1 code or a 3-letter individual language such as `cmn`), or an English or native name (`English`, `Deutsch`, `日本語`) of about 80 languages | the 639-1 code | equal; an unknown language, one outside `values`, or a sentence (`It is English`, `no idea`) is invalid | `values` |
| `set` | a list, or a string split on `split` | the set of normalised elements, duplicates and `empty_values` removed | Jaccard overlap at least `min_jaccard` (default 1.0: equal); two empty sets agree | `item` (element spec or type name; default a label over the set's `values` and `aliases`), `min_jaccard`, `split`, `empty_values` (`("none", "n/a")`) |
| `records` | a list of objects | each object's sub-`fields` | same length, and each record matches its partner: paired by the `key` fields, by position (`ordered=True`), or else in any order (a maximum matching, so the listed order never decides; quadratic in the records when sub-fields have a tolerance, so name a `key` when records have one) | `fields` (required), `key`, `ordered` |
| `ranking` | a list of ids | the normalised ids; an id ranked twice is invalid | `prefix` (default): the first `k` in order; `set`: the first `k` as a set (`k=0`: all) | `k`, `mode`, `item` |
| `similarity` | a string (free text: a summary, a short translation, a reply) | NFKC, case-folded, whitespace collapsed; an empty string is invalid | the longer is at most `max_len_ratio` times the shorter (in characters) and the `metric`'s score reaches `threshold`; equal texts always agree. A proxy: close to the other answer, not checked correct ([free-text tasks](#free-text-tasks)) | `metric` (`chrf`, the default; `token_f1`; `rouge_l`), `threshold` (default 0.7; more than 0, at most 1), `max_len_ratio` (default 2.0; 1 or more). The task needs `policy.samples` of at least 2. Not inside `records`, not a set element |

`tool_calls` is a reserved type name for a later release, as is the format `tool_calls`; a spec that uses them is refused. The format `sql` takes a `sql` object instead of fields ([text-to-SQL tasks](#text-to-sql-tasks)). A set's elements and a record's `key` fields compare by their normalised value only, so an element type with a tolerance is refused (use `records`).

Limits: 64 fields per task (counting the fields inside records), records nested at most 4 deep, at most 2,000 `values`, `aliases`, `order` entries, suffixes or empty values per field, at most 200 records in one answer (at any depth) and 2,000 elements in one set or ranking. A spec that breaks a rule raises `ValueError` when the task is defined, so a broken spec stops the gateway at startup; that includes an option the field's type does not use (`tolerance` on a `number`), which would otherwise do nothing.

### Task options

| Option | Default | Meaning |
|---|---|---|
| `fields` | none | The `FieldSpec`s above. A task without fields compares whole answers (the `default` rule) |
| `compare_keys` | none | The v0.4 shortcut: each key becomes `FieldSpec(key, "exact", required=False)`, so answers compare, validate and canonicalise exactly as in v0.4. Give `compare_keys` or `fields`, not both |
| `format` | `json` with fields, `sql` with a `sql` object, else `text` | `json`: fields are paths into the JSON answer, and a code fence around the whole answer is unwrapped; with a `$` field the answer may be any JSON value (a list, a number). `text`: the answer is plain text and its one field is `$`, the text without trailing `.!,;:` (`Positive.` is the label `positive`). `sql`: the answer is one SQL query, compared by what it returns on a fixture ([text-to-SQL tasks](#text-to-sql-tasks)) |
| `sql` | none | A sql task's options instead of `fields`: `fixture`, `dialect`, `ordered`, `empty_result`, `max_rows`, `max_steps` ([text-to-SQL tasks](#text-to-sql-tasks)). Only a task with format `sql` takes it, and such a task needs it |
| `pinned` | false | Always the teacher, never training data (below) |
| `store` | true | False: the task's teacher answers are served but never stored as training examples (requests are logged with `store_opt_out` `task`; `RATCHET_NO_STORE_TASKS` does the same without editing the spec, FR-13.1) |
| `privacy` | `{}` | `redact_style`: `label`, `numbered`, `surrogate` or null (the default, `RATCHET_REDACT_STYLE`), how identifiers in this task's stored examples are written (FR-13.7). Use `numbered` or `surrogate` for extraction tasks whose answers repeat an identifier from the request. Not part of the spec's hash: it changes how new examples are stored, not how answers compare |
| `policy` | `{}` | `samples` (1 to 16), `audit_rate`, `target_precision`, `min_precision` (0 to 1): part of the spec and its hash. `target_precision` is the precision the task's own threshold is calibrated to (default 0.97) and `min_precision` its floor in the golden gate (default `RATCHET_MIN_PRECISION`) ([per-task calibration](#per-task-calibration)); `audit_rate` replaces `RATCHET_AUDIT_RATE` for the task's served student answers ([risk-aware routing](#risk-aware-routing)); `samples` is how many student samples the task's confidence score rests on, in the router (the shadow too) and in the golden eval, instead of `RATCHET_SAMPLES` (FR-2.55); a task with a `similarity` field must set it to 2 or more |
| `routing` | `{}` | Eligibility, escalation rules and critical classes ([risk-aware routing](#risk-aware-routing)); part of the spec and its hash |
| `tools` | `teacher` | `teacher` or `distill` (opt-in tool-call distillation, a later release). A tool request is answered by the teacher alone and never stored under both values until then ([API.md](API.md#tools-and-function-calling)) |
| `description` | empty | What the task is for, up to 1,000 characters. Shown by `GET /ratchet/tasks` |
| `clients` | none | API key names (`RATCHET_API_KEYS`) that may use the task. Any other key gets 403 `task_not_allowed`, and so does a request without a key (with `RATCHET_API_KEYS` off, every request), before any quota or model call. A name must be a configured key when the spec is loaded or written. Empty or absent: every key |
| `match` | `{}` | How a request that names no task finds this one ([task resolution](#task-resolution)): `system_prefix` (its first system or developer message starts with it), `schema_name` (its `response_format.json_schema.name` is it), `tools` (the names of its `tools` and legacy `functions` are exactly these). Every rule a task gives must hold; the first such task in registry order wins, among the tasks the key may use. Plain comparisons, never patterns |
| `extends` | none | Spec files and the API only: start from another task's spec. In a tasks file, a built-in task or another task of the same file (before or after it; a task's own name extends the built-in task of that name), never an API task, because startup reads the file before it registers the stored API tasks. Through the API, any registered task (the task's own name extends its current version). `fields`, `sql`, `policy`, `match`, `privacy` and `routing` merge by key (`null` removes the base's entry; a routing block's `escalate_when` and `critical` lists are replaced whole); every other key replaces the base's; `name` and `description` are not inherited. Resolved when the task is defined, so later changes to the base do not reach it |

`Task.spec_hash` is 16 hex digits of SHA-256 over the task's rules (`Task.spec()`: `format`, `fields` in their order, `pinned`, `store`, `policy`, `routing`, `tools`, and for a sql task its `sql` options and the SHA-256 of its fixture's text, not its path), so a changed vocabulary or tolerance changes it and a renamed task keeps it. `description`, `clients`, `match` and `privacy` are left out: they say who uses a task and how its examples are stored, not how its answers compare. Night School records each golden task's hash in every eval it writes (in the task's per-task record, and in `spec_hashes`), and a task whose hash changed since the serving version was scored goes to the teacher (threshold 1.01) until a night scores the version under the new spec; `GET /ratchet/tasks` shows it as `recalibrating`. Because `policy` is part of the hash, changing a task's `target_precision` or `min_precision` also sends it to the teacher until the next night calibrates it under the new target.

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

### Free-text tasks

Summaries, translations and replies are rarely word for word the same, so the exact types call every pair of good answers a disagreement and the student never earns a confident score (FR-2.55, FR-14.40, [ADR 0054](adr/0054-free-text-similarity-and-a-judge-gate.md)). A `similarity` field compares them by a score instead:

| `metric` | What it compares | Use for |
|---|---|---|
| `chrf` (default) | Character n-grams of 1 to 6 of the text without whitespace; precision and recall averaged over the orders, F-beta with beta 2; the lower of the two directions, so it is symmetric | Any language, Chinese, Japanese and Thai included; short summaries and translations |
| `token_f1` | SQuAD's token F1: punctuation and the English articles dropped, the overlap of the two bags of words | Short English answers where word order does not matter |
| `rouge_l` | ROUGE-L F1: the longest common subsequence of word tokens, at most 512 tokens of each answer | Answers where word order matters; languages that separate words with spaces |

```json
{"tasks": [
  {"name": "short-summary", "format": "text",
   "fields": {"$": {"type": "similarity", "metric": "chrf", "threshold": 0.7}},
   "policy": {"samples": 3}},
  {"name": "ticket-summary",
   "fields": {"category": {"type": "label", "values": ["billing", "bug", "account"]},
              "summary":  {"type": "similarity", "metric": "rouge_l", "threshold": 0.6}},
   "policy": {"samples": 3}},
  {"name": "long-form-writing", "pinned": true}
]}
```

What changes for such a task:

- **Samples.** The confidence score counts how many samples agree, so a task with a similarity field needs `policy.samples` of at least 2 (with one sample it always agrees with itself); the spec is refused otherwise. The router and the golden eval both use the task's `samples`.
- **The answer served** is the medoid: the sample that agrees with the most others, then the most probable among those. Similarity is not transitive (A close to B and B to C, A far from C), and the medoid is never an answer at the edge.
- **Precision is a proxy.** On a similarity task, the golden eval's precision is the share of served answers close enough to the teacher's, labelled `precision_kind: proxy` in the task's record (and globally when such a task was scored). Audits and shadow comparisons use the same rule. Similar is not right: "the refund was approved" and "the refund was not approved" score 0.74 under chrF.
- **The judge.** In live mode `LiveEvaluator` asks `RATCHET_JUDGE_MODEL` (JSON mode) whether each answer the student would serve on the held-out half of a free-text task's golden rows (no fields, or a similarity field) is as good as the teacher's: `judge_pass_rate` and `judge_n` in the eval and in each such task's record. The candidate is the answer chosen from the samples the eval already drew, so the judge grades what production would serve. Labels are never judged. The judge runs at night, never on the request path. The mock fleet's judge knows the stored truth, so the gate works offline.
- **The judge gate.** `RATCHET_MIN_JUDGE_PASS` (default 0, off): a candidate whose overall `judge_pass_rate` is below it is rejected; a free-text task with at least `RATCHET_MIN_TASK_TEST_N` held-out rows whose own rate is below it fails like a task below its precision floor (`RATCHET_TASK_MODE`: disabled on the candidate, or the candidate rejected). With the gate on, a free-text task the candidate would answer that no judge graded fails it too.

Recommended defaults. Pin long-form work (summaries of long documents, translations of whole pages, chat replies, planning, long writing): there the big model earns its price and a score cannot tell good from bad. Distill short free text (a one-paragraph summary, a short translation) with a similarity field, and turn on `RATCHET_MIN_JUDGE_PASS` (0.9 is a reasonable start). Set `RATCHET_UNKNOWN_TASKS=teacher` so that free text no operator registered is answered by the teacher and never stored, rather than distilled under the whole-answer rule.

**Pinned tasks** (`Task.pinned = True`) skip the student entirely. `Ratchet.handle` sends them to the teacher, logs the request (`served_by=teacher`, cost equal to baseline) and does **not** store the answer as a training example. Use `pinned` for open-ended work (planning, long writing) that a small model should not take over. Every other task is **distillable**: its teacher answers become training data. Only an operator's spec pins a task (built-in, tasks file, admin API): no header, metadata key, `model` value or `response_format` can. Pinning a task that already has stored examples keeps them out of training and out of the golden eval from the next night on (`excluded_pinned` in the dataset manifest); the ledger keeps them, so unpinning brings them back.

Every answer is cleaned the same way before it is compared, served or stored (`tasks.clean_answer`): reasoning is removed (`<think>` blocks, anything before a lone `</think>`, an unterminated `<think>` that opens what is left and what follows), and for a JSON task (any task with fields, unless its format is `text`) a code fence around the whole answer is unwrapped when its body is JSON; an answer that is one JSON object is kept whole, tags quoted in its strings included. A sql task's answer loses one code fence around the whole of it (```` ```sql ... ``` ````). A teacher answer is stored as a training example only when it is complete (`finish_reason` `stop` or `tool_calls`), not empty, and valid under the task (invariant 16); otherwise it is served and logged as a `skip_example` event. So a JSON task's system prompt should ask for JSON only, and list the allowed values of each label.

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
| 6 | A `response_format` json_schema: its auto task ([Structured outputs](#structured-outputs)), made on first use (at most `RATCHET_MAX_AUTO_TASKS`) | `response_format` |
| 7 | With `RATCHET_AUTO_TASK=system_prompt`, the auto task of the first system message | `auto_system` |
| 8 | `default` | `default` |

The `model` field only ever names a task: the teacher always gets `RATCHET_TEACHER_MODEL` (or the fallback teacher's model) and the student its adapter name, whatever a request sends, and the response's `model` is the model that served. A `model` Ratchet does not know follows `RATCHET_MODEL_POLICY`. With the default settings, a request that sends none of rules 1 to 4 is `default`, as before: the built-in tasks have no match rules and auto tasks are off.

Clients pick a task with the `X-Ratchet-Task` header or `metadata.ratchet_task` (or the rules above). A name that is not registered follows `RATCHET_UNKNOWN_TASKS`: by default it gets a temporary task without fields with that name (`source` `adhoc`, not added to the registry, never pinned), so its data stays separate. Names must match `[A-Za-z0-9_.-]{1,64}`; a header that does not becomes `default` under `distill` (v0.4) and is a 400 `unknown_task` under `teacher` and `reject`, so it never becomes `default` training data there (a bad `metadata.ratchet_task` is always a 400). At most 500 tasks are registered, built-in ones included; a tasks file whose tasks, with the built-in ones it does not replace, come to more is a startup error.

### Structured outputs

A request's `response_format` is a contract on every answer of that request (FR-1.40, FR-14.2, [ADR 0042](adr/0042-auto-tasks-from-response-format-schemas.md), [API.md](API.md#structured-outputs)): `json_object` asks for one JSON object, `json_schema` for JSON that matches the schema (`ratchet/jsonschema_lite.py`). An answer that breaks it is invalid under the request's task, as an answer that breaks the task's spec is: no vote for a student sample, never stored or used as a reference for a teacher answer (one repair first, `RATCHET_SCHEMA_REPAIR`).

Which task a json_schema request gets:

| The request names | It gets |
|---|---|
| An operator's task (built-in, tasks file or admin API; header or `metadata.ratchet_task`) | That task; the schema is forwarded and checked on top of its fields |
| An auto task's name | That auto task when the request's schema derives the same fields; otherwise the request's own auto task (an `auto_task_collision` event) |
| An unregistered name | Under `distill`, an auto task of that name derived from the schema; under `teacher` and `reject`, the unknown-task path for the name. A name starting with `auto-` counts as no name |
| No name | The auto task `auto-<schema name as a slug>-<16 hex digits of SHA-256 over the canonical schema>`, registered on first use |

An auto task stands for the fields its schema derives, so one client cannot choose what another client's answers are compared on by naming that client's auto task first with a weaker schema. If an auto name is held by an auto task of other fields (two schemas with one 64-bit digest, or one derived differently by another release), the request gets an unregistered task of that name that stores nothing, which `RATCHET_UNKNOWN_TASKS` decides about; delete the stale auto task (`DELETE /ratchet/tasks/<name>`) to let the schema derive it again.

An auto task's fields come from the schema (`ratchet/schema_tasks.py`, `derive`):

| Schema | Field |
|---|---|
| `enum` | `label` over its values (`number`, exact, when they are all numbers, since the schema takes `2.0` for `2`; `exact` when they collide once normalised, are not all strings or integers, or are more than a label takes) |
| `boolean` | `bool` |
| `integer` | `number`, exact |
| `number` | `number`, relative tolerance 1e-6 |
| `string` with `format` `date` / `date-time` | `date` (day / datetime granularity) |
| `string` with `format` `email`, `uuid`, `uri`, `ipv4`, `hostname` | `text`, exact |
| any other `string`, a `const` | not compared (free text) |
| `array` of enums, booleans or those strings | `set` (`ranking` with `x-ratchet: {"compare": "ordered"}`); other numbers in an array are not compared |
| `array` of objects | `records` over their comparable properties (skipped when there is none) |
| nested `object` | its properties as dot paths, at most 4 levels deep; 64 fields in all |

Local `$ref`s and `$defs` are resolved; `"type": ["x", "null"]` or an `anyOf` with `{"type": "null"}` makes a field nullable; a field is required when the schema requires it and every object above it. A property's `x-ratchet` annotation changes its field: `{"compare": "text"}` compares a free-text string (normalised text), `{"compare": "ignore"}` leaves a field out, `{"compare": "ordered"}` makes an array a `ranking` (or ordered `records`), and `{"abs_tol": n}` gives a number a tolerance. Every key starting with `x-ratchet` is stripped from the schema before any model sees it or an example stores it, and the auto task's name hashes the schema with its annotations, since they change the fields.

Auto tasks have source `auto`, are never pinned and are stored in the ledger's `tasks` table, so they are registered again at every start and by `python -m ratchet.live`. An admin may replace one with an API spec (`PUT`) or delete it (its examples stay). A schema with no comparable field, one whose derived spec holds personal data (an enum of email addresses), or one past `RATCHET_MAX_AUTO_TASKS` gets an unregistered task of its auto name, which `RATCHET_UNKNOWN_TASKS` decides about. `json_object` without a schema, and every schema with `RATCHET_MAX_AUTO_TASKS=0`, keeps the named task, or `default`, which compares canonical JSON.

A teacher that answers a json_schema request with a 400 about the format is asked again with `json_object` and the schema in its system message, and that schema goes that way from then on. A refusal may be about the client's schema (a keyword the endpoint's grammar engine cannot compile), so after 3 refused schemas the endpoint is asked once with the smallest strict schema, and only when it refuses that too does every schema go as `json_object` (`json_schema_fallback` events).

### Text-to-SQL tasks

Two correct queries are rarely the same string (`COUNT(*)` and `COUNT(id)`, a JOIN and an IN subquery, aliases, case), and two queries that differ by one word can mean opposite things (`city = 'Pune'` against `city = 'Oslo'`). A task with `format: "sql"` compares answers by what they return on a fixture: a small database the operator writes as a SQL script inside `RATCHET_FIXTURES_DIR` (FR-2.56, [ADR 0055](adr/0055-sql-tasks-compared-by-execution.md), `ratchet/sqlcheck.py`).

```json
{"tasks": [
  {"name": "shop-sql",
   "description": "Questions about the shop's customers and orders, answered with one SQLite query",
   "format": "sql",
   "sql": {"fixture": "shop.sql", "ordered": "auto", "empty_result": "text", "max_rows": 1000, "max_steps": 200000}},
  {"name": "warehouse-sql", "format": "sql", "sql": {"dialect": "postgres"}}
]}
```

| `sql` option | Default | Meaning |
|---|---|---|
| `fixture` | required (sqlite) | A SQL script, relative to `RATCHET_FIXTURES_DIR` (a path that resolves outside it, through `..` or a symlink, is refused): a regular file of at most 5 MB of UTF-8. It is read and loaded when the spec is parsed (startup, the admin API, `validate`), so a missing, oversized, broken or refused fixture is a spec error with its path (`tasks[0].sql.fixture: fixture 'shop.sql' cannot be loaded: near ";": syntax error`) |
| `dialect` | `sqlite` | `postgres`: nothing runs; two answers agree when their normalised texts are equal (below). Takes no other option |
| `ordered` | `auto` | `auto`: rows compare in order when the outermost query has ORDER BY (not one in a subquery, a CTE or a window), else as a multiset, and an ordered result of two rows or more never equals an unordered one. `always`: every result in order. `never`: always as a multiset |
| `empty_result` | `text` | Two empty results say little (two wrong filters both return nothing), so under `text` they agree only when the normalised queries are equal: comments and a trailing `;` dropped, unquoted words lower-cased, one space between tokens. `agree`: two empty results with the same column count agree |
| `max_rows` | 1000 | 1 to 10,000. A query that returns more rows is invalid |
| `max_steps` | 200000 | 1,000 to 10,000,000 SQLite VM steps. A query that needs more is invalid (a runaway recursive CTE stops in milliseconds) |

**What agrees.** Two results agree when they have the same number of columns and the same rows: column names are ignored, floats are rounded to 9 significant digits and a whole float equals the integer (`SUM(qty)` and `TOTAL(qty)` agree), but `1` is not `'1'`. A duplicated row counts.

**Valid answers.** One statement that starts with SELECT, WITH or VALUES, bare or in one code fence, with reasoning before it as for any task; a `;` may end it and comments may sit anywhere. Anything else is invalid, with a reason that never quotes the answer (so it can go into a `skip_example` event): `not one query` (a second statement, prose around the query, EXPLAIN, PRAGMA, a write, an unclosed string), `reads the clock` (the fixture is fixed, so the answer should be too: CURRENT_DATE, CURRENT_TIME, CURRENT_TIMESTAMP; a date function without a time value, such as `date()`, `julianday()` or `strftime('%s')`; `'now'`, `'localtime'` or `'utc'` among a date function's arguments, also double-quoted; and, when the query runs, any date function given `now`, `subsec` or a `localtime`/`utc` modifier, however the value was built; write fixed dates such as `'2026-10-01'` instead), `sql error`, `sql denied` (an operation the sandbox refuses), `sql steps exceeded`, `sql time exceeded` (1 s), `sql memory exceeded` (128 MB, Linux), `sql too many rows`, `sql result too large` (8 MB). As for any task, an invalid student sample gets no vote and an invalid teacher answer is served but never stored. So the task's system prompt should give the schema and ask for one SQLite query and nothing else.

**The fixture.** Write it as you would a test database, to tell the likely mistakes apart: NULLs, ties, duplicates, empty groups, rows on each side of every boundary a question asks about. Two queries that differ only on data the fixture lacks agree. `sqlite3 shop.db .dump > fixtures/shop.sql` makes one from a database (a `.dump`'s `PRAGMA foreign_keys`, BEGIN and COMMIT are fine). It must create a table or a view; it may set only the PRAGMAs `foreign_keys`, `defer_foreign_keys`, `recursive_triggers`, `user_version` and `application_id`, and may not ATTACH a database, call `load_extension`, `random`, `randomblob` or the `current_*` functions, read the clock while it loads (`date('now')`, `unixepoch()`, or a `DEFAULT CURRENT_TIMESTAMP` that a row takes; a `.dump` gives every value, so its defaults never run), define a view that reads the clock, hold a value over 100 KB, take more than 30 s or grow past 64 MB: its database must be the same every time it loads. `spec_hash` covers the SHA-256 of its text, so editing it sends the task to the teacher until a night re-scores the version (the stored API tasks read it again at every start, and a `PUT` of the same body after an edit is a change); moving it keeps the hash. Tasks with the same fixture text share one loaded copy. `RATCHET_FIXTURES_DIR` must be readable by the gateway and by `python -m ratchet.live`, which re-checks stored answers and runs the golden eval on the fixture.

**Security.** The gateway never connects to a customer database: the fixture is the only data it runs SQL on, and the query comes from a model a client's prompt can steer, so it is run as hostile input ([THREAT_MODEL.md](THREAT_MODEL.md), T23):

- in a private in-memory SQLite database per fixture text, never a file, with `temp_store = MEMORY`;
- with `PRAGMA query_only = ON` and an authorizer that allows only reading the fixture's own tables and views, the schema and `json_each`/`json_tree` (not `sqlite_stmt`, which lists the statements other requests ran, nor `dbstat`), recursive CTEs and the deterministic functions in `sqlcheck.FUNCTIONS`, and denies writes, schema changes, ATTACH, PRAGMA (the `pragma_*` table-valued functions too), transactions, `random()`, `randomblob()`, `changes()`, `last_insert_rowid()`, `load_extension()`, `json_patch()`, `unhex()` and every function not on the list;
- under a progress handler, every 100 VM steps, that stops a query after `max_steps`, 1 s or 128 MB of resident-memory growth (Linux), with results read 100 rows at a time up to `max_rows` and 8 MB;
- with the work of one step and one row bounded, since SQLite calls the handler only where a loop jumps back: a value is at most 100 KB and a LIKE or GLOB pattern at most 100 bytes; `instr`, `replace` and the two-argument `trim`, `ltrim` and `rtrim`, which SQLite runs in quadratic time, are linear versions with the same results (one `trim` call took 15 s with 60,000 characters in each, measured); and LIKE, GLOB (up to about 25 ms a call), the date functions and those linear versions check the query's time on every call (a row whose WHERE called LIKE 200 times took 3.4 s before, measured);
- one query at a time per fixture (Scout calls the gateway from several threads); a typical query on a small fixture takes about 0.1 ms (a call of LIKE or a date function adds a few microseconds), and a hostile one costs at most about 1.3 s and 128 MB before it is stopped.

Capping a value's size needs `Connection.setlimit`, new in Python 3.11; without it one query can copy values of up to 1 GB in steps the progress handler never sees (3 GB and 18 s for a query that doubles a string, measured on 3.10). So on Python 3.10 a sqlite fixture is a spec error that says so; `dialect: postgres` still works. The Docker image runs Python 3.12. (A process-wide `PRAGMA hard_heap_limit` would also cap the ledger's SQLite and does not cap LIKE patterns; see ADR 0055.)

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
4. Optional: for free-text tasks (no fields, or a `similarity` field), `LiveEvaluator` asks the judge model about the answers the student would serve (`judge_pass_rate`, `judge_n`), and `RATCHET_MIN_JUDGE_PASS` makes the release gate hold candidates to it ([free-text tasks](#free-text-tasks)).

A built-in task is a `Task` in `tasks.BUILTIN` (`ratchet/tasks.py`), with `FieldSpec`s from `ratchet/compare.py`; put shared vocabularies in `ratchet/vocab.py`. Use that only for tasks the code itself depends on, such as Scout's.

Choose fields that are verifiable (labels, IDs, numbers, dates). Typed comparison on these fields is what keeps evaluation free of judge bias; pick the loosest type that is still right (a `number` with a tolerance for amounts, a `set` for tags), because a field that is too strict escalates good answers and lowers measured precision. Changing a task's rules later is safe: the serving version keeps the task on the teacher until a night has scored it under the new spec.

## Teacher licenses

`ratchet/licenses.py` holds the built-in registry (`BUILTIN`: known distillable teachers) and `CLOSED`, the ID prefixes that are always refused (`gpt`, `o1`, `o3`, `o4`, `claude`, `gemini`). Unknown teachers are refused until registered. See [ADR 0001](adr/0001-open-teacher-only.md).

An open teacher can also be added without a code change ([ADR 0090](adr/0090-licenses-travel-with-the-adapter.md)):

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_TEACHER_LICENSES` | empty | A JSON file: a list of `{"model", "license", "url", "note"}` objects (`note` optional, at most 100 entries). Read at startup by the gateway (and on a mock demo reset), the simulator, `python -m ratchet.live` and `python -m ratchet.admin`, and merged into a copy of the built-in registry. The configured teachers can then be `RATCHET_TEACHER_MODEL` or `RATCHET_TEACHER_FALLBACK_MODEL`, Night School trains on their stored answers, and each adapter's BOM and NOTICE.md name them with `license_origin` `config`. A bad file stops startup with every error and its place (`[2].license: 'CC-BY-NC-4.0' is not an allowed license; ...`). Empty = the built-in registry only |

Each entry is checked conservatively, so configuration cannot relabel a closed model Ratchet knows of (invariant 1):

- `license` must be one of the **allowed licenses**: `OpenMDW-1.1`, `Apache-2.0`, `MIT`, `NVIDIA Nemotron Open Model License` (any case; stored in this spelling). Licenses with use restrictions or custom terms (a community license, a non-commercial one) need a reviewed change to `ratchet/licenses.py`.
- `model` must not belong to a closed model family (`closed_family`). The id is split at every character that is not a letter or digit; it is refused when a part starts with a `CLOSED` prefix (`gpt`, `o1`, `o3`, `o4`, `claude`, `gemini`) or with `o` and a digit (`o5-mini`), a part contains `gpt`, `claude` or `gemini` anywhere (`chatgpt-4o-latest`), a closed model's name (`davinci`, `babbage`, `codex`; `CLOSED_NAMES`) or a closed vendor's name (`openai`, `anthropic`; `CLOSED_VENDORS`). `gpt-4o`, `azure-gpt-4o`, `chatgpt-4o-latest`, `davinci-002`, `openai/davinci-002` or `anthropic/claude-3-5-sonnet` claimed as `Apache-2.0` are refused. The rule errs toward refusing, so it also refuses open models with such a name (`openai/gpt-oss-120b`, `EleutherAI/gpt-j-6b`): add those in code, after reading their license.
- `model` must not be a router alias: a part containing a router's name (`openrouter`; `ROUTER_VENDORS`) or a last path segment `auto` (`openrouter/auto`). The model that answers it is chosen per request and may be closed, so no license claim covers it; name the model itself.
- `model` must not be a built-in teacher, in any case (configuration never relabels one), or appear twice; `url` must be an `https://` URL; no other keys.
- `model` must not name a provenance source that is not a teacher (`licenses.SOURCES`: `human`, customer-provided labels, [ADR 0091](adr/0091-human-labels-as-a-provenance-source.md)), in any case. `RATCHET_TEACHER_MODEL=human` stops startup too: `require_distillable` never reads `SOURCES`.

The model check knows only the closed families, model names, vendors and routers listed above. Any other id is admitted on your license claim alone: a closed model from another vendor under its own name (`x-ai/grok-4`, say) passes the check, so claiming an open license for it is on you. Every adapter trained on a configured teacher says so: its BOM records `license_origin` `config` and its NOTICE.md marks the license "(configured)", so whoever receives the adapter can tell your claim from Ratchet's reviewed registry.

```json
[
  {"model": "Qwen/Qwen3-235B-A22B-Instruct-2507", "license": "Apache-2.0",
   "url": "https://huggingface.co/Qwen/Qwen3-235B-A22B-Instruct-2507", "note": "Checked on the model card, Oct 2026."}
]
```

A configured teacher has no entry in `DEFAULT_PRICES`, so it is priced as described in [Prices](#prices-default_prices). Removing an entry later makes the teacher unknown again: the gateway refuses it at startup and Night School leaves its stored rows out of training (they keep the license they were stored with). Not legal advice: check each license yourself.

`BASE_MODELS` in the same file maps the student base models Ratchet knows (Nemotron 3 Nano 4B and 30B-A3B) to their license, the NVIDIA Nemotron Open Model License, which every adapter's BOM (`base_model_license`) and NOTICE.md carry. For another `RATCHET_STUDENT_BASE_MODEL` they carry a warning instead (never an error): find its license and ship it with the adapter, or add the model to `BASE_MODELS`.
