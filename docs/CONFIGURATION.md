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
| `RATCHET_CONFIDENCE_THRESHOLD` | `0.80` | Fallback threshold, used only when a version has no calibrated `threshold` in its eval |
| `RATCHET_SAMPLES` | `3` | Student samples per request (self-consistency) and per golden row. A request for more choices (`n`) draws `n` samples, but its confidence is still scored on the first `RATCHET_SAMPLES` of them |
| `RATCHET_AUDIT_RATE` | `0.05` | Probability that a student-served answer is also sent to the teacher. Mock mode raises it to at least `0.10`. An admin can replace it at runtime, without a restart (`PUT /ratchet/admin/settings` or `python -m ratchet.admin flag audit_rate 0.2`; [API.md](API.md#admin)); `controls.audit_rate` in `/ratchet/stats` shows the override, `null` while this value applies |
| `RATCHET_GOLDEN_FRACTION` | `0.10` | Share of new examples assigned to the golden (held-out) split |
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
| `RATCHET_TEACHER_MODALITIES` | `text` | Content part types the teacher accepts besides text, comma-separated, e.g. `text,image_url` (also `input_audio`, `file`). A request with such a part goes to the teacher only: no student call, no audit, nothing stored as a training example. Any other non-text part is a 400 `unsupported_content_type`. Set it only when the teacher model reads those parts; otherwise the teacher's refusal comes back as a 400 with its own message ([API.md](API.md#errors)) |

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
| `RATCHET_API_KEYS` | empty | `name:key,name:key`. When set, `/v1/*` needs `Authorization: Bearer <key>` or `X-API-Key: <key>`; anything else gets 401. Names: letters, digits, `_`, `.`, `-` (up to 64). Keys: at least 16 characters; generate with `openssl rand -hex 24`. A bare key without `name:` is named `keyN`. The ledger stores the name per request, never the key. A malformed value stops the gateway at startup |
| `RATCHET_API_KEY_DAILY_REQUESTS` | `0` | Chat completions per key per UTC day; past it a 429 `insufficient_quota` (code `daily_quota_exceeded`) with `Retry-After` until 00:00 UTC and `X-Should-Retry: false` ([API.md](API.md#authentication)). `0` = unlimited. `RATCHET_KEY_LIMITS` overrides it per key ([Spend and rate limits](#spend-and-rate-limits)). Counted by the spend meter, which reads today's requests from the ledger at startup, so it survives restarts in live mode |
| `RATCHET_ADMIN_KEYS` | empty | Admin keys for management writes (`PUT` and `DELETE /ratchet/tasks/{name}`, `POST /ratchet/tasks/validate`, `POST /ratchet/cost/endpoint`, and every route of the admin API under `/ratchet/admin/`, reads included; [API.md](API.md#admin)), in the `RATCHET_API_KEYS` syntax: `name:key,name:key`, keys at least 16 characters. Sent as `Authorization: Bearer <key>` or `X-API-Key`; a missing or wrong key gets 401, an API key 403. The ledger and the event log record the name, never the key. A key may not be in both variables (startup error). Empty: the admin API is off in live mode (403 `admin API disabled: set RATCHET_ADMIN_KEYS`) and open in mock mode (the admin is `mock-admin`). Not the Caddy guard's `RATCHET_ADMIN_TOKEN` |
| `RATCHET_DASHBOARD_KEYS` | empty | Read-only keys for the dashboard API ([API.md](API.md#access-levels)), in the `RATCHET_API_KEYS` syntax. A dashboard key reads everything an admin key reads and writes nothing. A key may not also be an API key or an admin key (startup error), so each key has one role. Give one to whoever watches a live gateway's dashboard |
| `RATCHET_PUBLIC_DASHBOARD` | `1` in mock mode, `0` in live mode | `1`: the dashboard API (`GET /ratchet/stats`, `GET /ratchet/cost`, `POST /ratchet/redact`, `GET /ratchet/tasks` and `/ratchet/tasks/{name}`, the BOM endpoints and the other reads [API.md](API.md#access-levels) lists) needs no key. `0`: it needs an admin or dashboard key, as `Authorization: Bearer <key>` or `X-API-Key`; without one it is 401, with an API key 403, and with neither `RATCHET_ADMIN_KEYS` nor `RATCHET_DASHBOARD_KEYS` set every read is 403. The pages, `/healthz`, `/ratchet/bom/public-key` and Scout are always public. The dashboard page sends no key yet, so with `0` it shows no data: read the API with a key, or set `1` (the demo VM does) |
| `RATCHET_PUBLIC_BOM` | `0` | `1`: `GET /ratchet/bom` and `GET /ratchet/versions/{id}/bom` stay public while the dashboard API is private, so auditors can check an adapter's provenance without a key |
| `RATCHET_SCRUB_STATS` | unset | What a reader of the dashboard API sees. Unset: an anonymous read of a public dashboard in live mode is scrubbed: client names in `/ratchet/stats` and in task allow-lists (`GET /ratchet/tasks`) become `client-1`, `client-2`, ... (by sorted name, the same in both), event details lose every `http(s)://` URL (`<url>`), and an error message after an exception's class is cut (`ConnectError: ...` becomes `ConnectError`; v0.4 ledgers hold such events). A reader with an admin or dashboard key sees everything. `1`: every read is scrubbed, with a key or without, in either mode. `0`: none is. `security.scrubbed` in `/ratchet/stats` says which a reader got |
| `RATCHET_OPENAPI` | `1` in mock mode, `0` in live mode | `1`: FastAPI serves `/docs`, `/redoc` and `/openapi.json`. `0`: they do not exist (404), so a live gateway does not hand anyone a map of its routes |
| `RATCHET_ALLOW_UNAUTHENTICATED` | `0` | Live mode refuses to start without `RATCHET_API_KEYS`, because `/v1/*` would answer anyone, on your teacher credits. `1` lets it start, for a gateway something else guards: the demo VM's Caddy guard (`RATCHET_API_GUARD=guarded`) or a private network. Mock mode ignores it |
| `RATCHET_CSP` | empty | A `Content-Security-Policy` for every response. Empty sends none, so no page can break by default. Printable ASCII only (anything else stops startup). Recommended, which the React build and the lite pages work with: `default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'` (the pages load nothing from other origins; `'unsafe-inline'` is for the theme script in the React page and the lite pages' inline scripts). It also blocks `/docs`, whose Swagger UI comes from a CDN |
| `RATCHET_BOM_SIGNING_KEY` | empty | Ed25519 private key as the base64 of its 32-byte seed. Signs each adapter's bill of materials. Generate with `python -m ratchet.bom keygen`, which also prints the public key to publish. Needs the `sign` extra (`pip install "ratchet-distill[sign]"`, included in the gateway image). An invalid value stops the gateway at startup. Empty = BOMs are stored with a SHA-256 but unsigned |

The switches above take `0` or `1`; any other value stops startup, and unset or empty takes the default. Every response also carries `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer` ([ADR 0061](adr/0061-endpoint-access-levels.md)).

Keep `RATCHET_API_KEYS`, `RATCHET_ADMIN_KEYS`, `RATCHET_DASHBOARD_KEYS`, `RATCHET_METRICS_TOKEN` and `RATCHET_BOM_SIGNING_KEY` out of git, like every other key. Rotating the signing key does not invalidate old BOMs: each one embeds the public key and key id it was signed with, so keep a list of the public keys you have published.

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
| `RATCHET_TRUST_PROXY` | `0` | `1`: take the client IP for the per-IP limit from the first `X-Forwarded-For` entry. Only set it when a proxy you control overwrites that header; otherwise clients can fake their IP. Not needed on the demo VM, where uvicorn runs with `--proxy-headers` |

The budget counts the `cost_usd` of every Scout step, including audits and shadow calls. It does not cover `/v1/chat/completions`. Counters are in memory and reset at 00:00 UTC and on restart.

## Mock-mode demo

Read in `ratchet/config.py`. Ignored in live mode.

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_MOCK_WARMUP_DAYS` | `0` | Simulated days of `mixed` traffic, each followed by a night, run at startup and on every demo reset, so the demo opens with a trained student. `4` takes about 2 s and leaves a live student that answers about 80–90% of Scout's LLM calls (simulation). Writes a `warmup` event |
| `RATCHET_MOCK_WARMUP_REQUESTS` | `1500` | Requests per warm-up day |
| `RATCHET_MOCK_PACE` | `0.25` | `/scout/stream` waits this share of each call's simulated latency (mock Ultra 2.4 s → 0.6 s), so the page shows agents working. `0` = no waiting. `/scout/run` never waits |
| `RATCHET_MOCK_ENDPOINT_HOURS` | `24` | Hours a day the simulated student endpoint runs, 0 to 24: each simulated day (the demo, the warm-up, `python -m sim.simulate`) records one endpoint interval of this length from the start of the day, on the synthetic clock, for the cost report ([Cost accounting](#cost-accounting)). `0` = no endpoint (as with the hosted fallback). Anything outside 0 to 24 stops startup. Simulation assumption, not a measurement |

## Demo VM: Caddy (`deploy/vm/`)

Read only by Docker Compose and Caddy on the demo VM ([DEPLOY_NEBIUS_VM.md](DEPLOY_NEBIUS_VM.md)), from `deploy/vm/.env`. The gateway does not read them.

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_DOMAIN` | required | Site name Caddy serves and gets a certificate for, e.g. `ratchet.dualmindlab.tech` or `<public-ip>.sslip.io`. First boot fills in the sslip.io name if it is still the placeholder |
| `RATCHET_API_GUARD` | `open` | `open`: every path is public. `guarded`: `/v1/*` and `/ratchet/demo/*` return 403 unless the request has a matching `X-Ratchet-Admin-Token` header. Pages, `/scout*`, `/ratchet/stats` and `/ratchet/bom` stay public at the edge; whether the gateway serves the dashboard API without a key is `RATCHET_PUBLIC_DASHBOARD`. In mock mode, `guarded` also disables the dashboard's demo buttons. In live mode without `RATCHET_API_KEYS`, the gateway starts only with `RATCHET_ALLOW_UNAUTHENTICATED=1` |
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

## Ledger and hot path

How the ledger file is written and how often the request path does periodic work ([ADR 0060](adr/0060-hot-path-caches-and-throttled-tick.md), [ARCHITECTURE §11](ARCHITECTURE.md#11-hot-path-and-performance)). A bad value stops startup with a `ValueError` naming the variable (the gateway, `python -m ratchet.live` and `python -m ratchet.cost`).

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_DB_SYNC` | `NORMAL` | How a ledger file is journaled, `NORMAL` or `FULL` (any case; empty means `NORMAL`). `NORMAL`: the WAL journal with `synchronous=NORMAL`. Readers never wait for the writer, the dashboard reads on a second, read-only connection, and a commit does not wait for the disk: a power cut or an operating-system crash (not a crash of the gateway process) can lose the last commits, never corrupt the file. `FULL`: the rollback journal with `synchronous=FULL`, v0.4's behaviour, for a ledger on a filesystem without shared memory (a network or bucket mount, where WAL does not work) or when every commit must reach the disk before the client gets its answer. Give the gateway, the cron jobs and the cost CLI the same value: `FULL` cannot take a file out of WAL while another process has it open, and stops startup instead. Mock mode's in-memory ledger has no journal to set (a bad value still stops startup) |
| `RATCHET_TICK_INTERVAL_S` | unset: `5` in live mode, `0` in mock mode | Seconds between two release-gate ticks run from the request path (0 to 3600). A tick less than this after the previous one, or while another thread is ticking, is skipped, so requests pay for a tick at most once per interval and never wait for one. A rollback can wait this long; the cron `python -m ratchet.live tick` runs regardless. `0` ticks after every request (mock mode, so simulations and tests behave as before); the mock demo's simulated days always tick |
| `RATCHET_STATS_TTL_S` | unset: `2` in live mode, `0` in mock mode | Seconds `/ratchet/stats` serves one computed payload (0 to 3600). However many dashboards poll, it is computed at most once per TTL, by one caller at a time; the others get the previous payload. `0` computes it on every read |

## Observability

Logs, metrics and request ids ([ADR 0062](adr/0062-request-ids-failures-logs-and-metrics.md), [API.md](API.md#request-ids), [RUNBOOK.md](RUNBOOK.md#failures-and-request-ids)). Read in `ratchet/config.py`; used by `ratchet/obs.py` and `ratchet/gateway.py`. A bad value stops startup with a `ValueError` naming the variable.

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_LOG_FORMAT` | unset: `text` in mock mode, `json` in live mode | How the `ratchet` logger writes to stderr: `json` (one object per line: `ts`, `level`, `logger`, `event`, then the fields) or `text` (`<ts> <LEVEL> <event> key=value ...`), any case. Lines carry ids, names, numbers and statuses, never prompt or answer text, URLs or keys |
| `RATCHET_LOG_LEVEL` | unset: `WARNING` in mock mode, `INFO` in live mode | `DEBUG`, `INFO`, `WARNING` or `ERROR`, any case. `INFO` writes one `chat` line per chat request (request id, client request id, trace id, client name, task, served_by, route, version, confidence, escalated, audited, latency_ms, wall_ms, cost_usd, status) and every release-log event (`ledger_event`, scrubbed of URLs and error messages); `WARNING` keeps the `upstream_error` lines (role, exception class, status), so mock mode and the tests stay quiet |
| `RATCHET_METRICS_TOKEN` | empty | A bearer token that reads `GET /metrics` (and nothing else), for a Prometheus scraper, besides the admin and dashboard keys. At least 16 characters; it may not also be an API, admin or dashboard key (startup error). Compared as a constant-time digest, never stored or logged. Empty: only admin and dashboard keys read a private `/metrics`. A public dashboard (`RATCHET_PUBLIC_DASHBOARD=1`) makes `/metrics` public too |

Keep `RATCHET_METRICS_TOKEN` out of git, like the keys.

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

`ReleasePolicy` checks its values when it is built (`from_env` included), so a typo stops startup with a `ValueError` naming the variable: whole numbers for the integer fields, `min_precision`, `min_coverage` and `tolerance` from 0 to 1, `live_floor` strictly between 0 and 1.

A new `ReleasePolicy` field needs a row here: `python scripts/reqtrace.py --check` derives the variable names from the dataclass and fails on an undocumented one. The fields of the evidence features are in [Reserved settings](#reserved-settings).

Other constants:

| Constant | Where | Value |
|---|---|---|
| `CANARY_STEPS` | `release.py` | `[10, 50]` then 100 |
| `FREQUENT` | `nightschool/dataset.py` | 3: requests seen this often always go to training |
| `min_new_examples` | `run_night()` | 50 new train rows needed to train |
| `target_precision` | `cascade_eval()` / `LiveEvaluator` | 0.97 for threshold calibration |
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

`RATCHET_DB_SYNC` was reserved here for the WAL journal; it is built now ([Ledger and hot path](#ledger-and-hot-path)).

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

Tasks come from three places ([ADR 0022](adr/0022-task-registry.md)): the built-in ones below (`ratchet/tasks.py`), a tasks file, and the admin API ([API.md](API.md#tasks)). Read in `ratchet/config.py`; used by `ratchet/taskspec.py`, `ratchet/router.py` and `ratchet/gateway.py`:

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_TASKS_FILE` | empty | A file of task specs ([Adding a task](#adding-a-task)), JSON, or YAML for a `.yaml` or `.yml` file (needs PyYAML: `pip install pyyaml`, or the `yaml` extra). Read when the gateway starts (and on a mock demo reset) and by `python -m ratchet.live`. A file task may replace a built-in one. A bad file stops startup with every error and its path (`tasks[2].fields.total.abs_tol: must be a number >= 0`); a YAML file without PyYAML is an error that says so |
| `RATCHET_UNKNOWN_TASKS` | `distill` | What a request naming a task that is not registered gets. `distill` (v0.4): a task without fields made up for the name, distilled like any other. `teacher`: the teacher answers (route `unknown_task`), the student is not called and nothing is stored. `reject`: 400 `unknown_task`, listing the registered tasks the key may use. Under `teacher` and `reject` an `X-Ratchet-Task` header that is not a valid task name is a 400 `unknown_task` too (under `distill` it means `default`, as in v0.4). Case-insensitive; anything else stops startup |
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
| `policy` | `{}` | `samples` (1 to 16), `audit_rate`, `target_precision`, `min_precision` (0 to 1): part of the spec and its hash, read by later releases (per-task sampling, audits and precision targets) |
| `routing` | `{}` | Routing rules for a later release; part of the spec and its hash |
| `tools` | `teacher` | `teacher` or `distill` (opt-in tool-call distillation, a later release) |
| `description` | empty | What the task is for, up to 1,000 characters. Shown by `GET /ratchet/tasks` |
| `clients` | none | API key names (`RATCHET_API_KEYS`) that may use the task. Any other key gets 403 `task_not_allowed`, and so does a request without a key (with `RATCHET_API_KEYS` off, every request), before any quota or model call. A name must be a configured key when the spec is loaded or written. Empty or absent: every key |
| `match` | `{}` | How a request without a task name may find this task, for a later release: `system_prefix` (the system message starts with it), `schema_name` (`response_format.json_schema.name`), `tools` (exactly these tool names). Plain comparisons, never patterns |
| `extends` | none | Spec files and the API only: start from another task's spec. In a tasks file, a built-in task or another task of the same file (before or after it; a task's own name extends the built-in task of that name), never an API task, because startup reads the file before it registers the stored API tasks. Through the API, any registered task (the task's own name extends its current version). `fields`, `policy` and `match` merge by key (`null` removes the base's entry); every other key replaces the base's; `name` and `description` are not inherited. Resolved when the task is defined, so later changes to the base do not reach it |

`Task.spec_hash` is 16 hex digits of SHA-256 over the task's rules (`Task.spec()`: `format`, `fields` in their order, `pinned`, `store`, `policy`, `routing`, `tools`), so a changed vocabulary or tolerance changes it and a renamed task keeps it. `description`, `clients` and `match` are left out: they say who uses a task, not how its answers compare. Night School records each golden task's hash in every eval it writes, and a task whose hash changed since the serving version was scored goes to the teacher (threshold 1.01) until a night scores the version under the new spec; `GET /ratchet/tasks` shows it as `recalibrating`.

**Pinned tasks** (`Task.pinned = True`) skip the student entirely. `Ratchet.handle` sends them to the teacher, logs the request (`served_by=teacher`, cost equal to baseline) and does **not** store the answer as a training example. Use `pinned` for open-ended work (planning, long writing) that a small model should not take over. Every other task is **distillable**: its teacher answers become training data. Only an operator's spec pins a task (built-in, tasks file, admin API): no header, metadata key, `model` value or `response_format` can. Pinning a task that already has stored examples keeps them out of training and out of the golden eval from the next night on (`excluded_pinned` in the dataset manifest); the ledger keeps them, so unpinning brings them back.

Every answer is cleaned the same way before it is compared, served or stored (`tasks.clean_answer`): reasoning is removed (`<think>` blocks, anything before a lone `</think>`, an unterminated `<think>` that opens what is left and what follows), and for a JSON task (any task with fields, unless its format is `text`) a code fence around the whole answer is unwrapped when its body is JSON; an answer that is one JSON object is kept whole, tags quoted in its strings included. A teacher answer is stored as a training example only when it is complete (`finish_reason` `stop` or `tool_calls`), not empty, and valid under the task (invariant 16); otherwise it is served and logged as a `skip_example` event. So a JSON task's system prompt should ask for JSON only, and list the allowed values of each label.

Clients pick a task with the `X-Ratchet-Task` header or `metadata.ratchet_task`. A name that is not registered follows `RATCHET_UNKNOWN_TASKS`: by default it gets a temporary task without fields with that name (`source` `adhoc`, not added to the registry, never pinned), so its data stays separate. Names must match `[A-Za-z0-9_.-]{1,64}`; a header that does not becomes `default` under `distill` (v0.4) and is a 400 `unknown_task` under `teacher` and `reject`, so it never becomes `default` training data there (a bad `metadata.ratchet_task` is always a 400). At most 500 tasks are registered, built-in ones included; a tasks file whose tasks, with the built-in ones it does not replace, come to more is a startup error.

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
