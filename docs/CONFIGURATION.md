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
| `RATCHET_AUDIT_RATE` | `0.05` | Probability that a student-served answer is also sent to the teacher. Mock mode raises it to at least `0.10` |
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
| `RATCHET_API_KEY_DAILY_REQUESTS` | `0` | Chat completions per key per UTC day; past it a 429 `insufficient_quota` (code `daily_quota_exceeded`) with `Retry-After` until 00:00 UTC and `X-Should-Retry: false` ([API.md](API.md#authentication)). `0` = unlimited. Counted from the ledger, so it survives restarts in live mode |
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

## Demo VM: Caddy (`deploy/vm/`)

Read only by Docker Compose and Caddy on the demo VM ([DEPLOY_NEBIUS_VM.md](DEPLOY_NEBIUS_VM.md)), from `deploy/vm/.env`. The gateway does not read them.

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_DOMAIN` | required | Site name Caddy serves and gets a certificate for, e.g. `ratchet.dualmindlab.tech` or `<public-ip>.sslip.io`. First boot fills in the sslip.io name if it is still the placeholder |
| `RATCHET_API_GUARD` | `open` | `open`: every path is public. `guarded`: `/v1/*` and `/ratchet/demo/*` return 403 unless the request has a matching `X-Ratchet-Admin-Token` header. Pages, `/scout*`, `/ratchet/stats` and `/ratchet/bom` stay public. In mock mode, `guarded` also disables the dashboard's demo buttons |
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
| `RATCHET_STUDENT_KEY` | `student_endpoint.sh` | required | Token clients must send to the endpoint |
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
| `student` | 0.06 | 0.24 | Per-token **proxy** for the self-hosted student; real cost is GPU-hours |

`Settings.price(model)` checks for an exact key first, then uses `student` for any name starting with `student`. Anything else gets the teacher's price (or 1.00 / 3.00). To change prices, pass `Settings(prices={...})` in code; there are no price environment variables. A fallback teacher's answers (`RATCHET_TEACHER_FALLBACK_MODEL`) are priced with its own entry (the teacher's price when it has none); the all-teacher baseline of such a request is the teacher's price for the same tokens.

## Release policy (`ratchet/release.py`)

`ReleasePolicy` defaults. In live mode (gateway and `ratchet.live`) each field can be overridden with the variable in the second column (`ReleasePolicy.from_env()` reads `RATCHET_<FIELD>` for every field). Integers stay integers, the rest are floats. Mock mode ignores them and uses `min_audits=15`.

| Field | Variable | Default | Used in |
|---|---|---|---|
| `min_precision` | `RATCHET_MIN_PRECISION` | 0.95 | Golden gate and shadow: minimum precision of answers the candidate would serve |
| `min_coverage` | `RATCHET_MIN_COVERAGE` | 0.25 | Golden gate: minimum share of golden traffic it would serve |
| `tolerance` | `RATCHET_TOLERANCE` | 0.02 | Max allowed drop vs live (precision and coverage) in the golden gate, and coverage vs live in shadow |
| `min_shadow` | `RATCHET_MIN_SHADOW` | 100 | Would-serve shadow answers needed before a shadow decision |
| `min_audits` | `RATCHET_MIN_AUDITS` | 20 | Teacher audits needed (per stage) before a canary decision or a live alert |
| `live_floor` | `RATCHET_LIVE_FLOOR` | 0.92 | Audited precision floor; rollback when the Wilson upper bound is below it |

A new `ReleasePolicy` field needs a row here: `python scripts/reqtrace.py --check` derives the variable names from the dataclass and fails on an undocumented one.

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

## Tasks

A task tells Ratchet when two answers "agree" and which answers are valid (`ratchet/tasks.py`, [ADR 0021](adr/0021-field-comparators-and-task-specs.md)). Its `fields` list the parts of the answer that must match, each a path and a typed comparator (`ratchet/compare.py`); anything else in the answer, such as a free-text `reply`, is ignored.

Tasks come from three places ([ADR 0022](adr/0022-task-registry.md)): the built-in ones below (`ratchet/tasks.py`), a tasks file, and the admin API ([API.md](API.md#tasks)). Read in `ratchet/config.py`; used by `ratchet/taskspec.py`, `ratchet/router.py` and `ratchet/gateway.py`:

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_TASKS_FILE` | empty | A file of task specs ([Adding a task](#adding-a-task)), JSON, or YAML for a `.yaml` or `.yml` file (needs PyYAML: `pip install pyyaml`, or the `yaml` extra). Read when the gateway starts (and on a mock demo reset) and by `python -m ratchet.live`. A file task may replace a built-in one. A bad file stops startup with every error and its path (`tasks[2].fields.total.abs_tol: must be a number >= 0`); a YAML file without PyYAML is an error that says so |
| `RATCHET_UNKNOWN_TASKS` | `distill` | What a request naming a task that is not registered gets. `distill` (v0.4): a task without fields made up for the name, distilled like any other. `teacher`: the teacher answers (route `unknown_task`), the student is not called and nothing is stored. `reject`: 400 `unknown_task`, listing the registered tasks the key may use. Case-insensitive; anything else stops startup |
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
| `extends` | none | Spec files and the API only: start from another task's spec (a task of the same file, before or after, or a registered task; the task's own name extends the registered task of that name). `fields`, `policy` and `match` merge by key (`null` removes the base's entry); every other key replaces the base's; `name` and `description` are not inherited. Resolved when the task is defined, so later changes to the base do not reach it |

`Task.spec_hash` is 16 hex digits of SHA-256 over the task's rules (`Task.spec()`: `format`, `fields` in their order, `pinned`, `store`, `policy`, `routing`, `tools`), so a changed vocabulary or tolerance changes it and a renamed task keeps it. `description`, `clients` and `match` are left out: they say who uses a task, not how its answers compare. Night School records each golden task's hash in every eval it writes, and a task whose hash changed since the serving version was scored goes to the teacher (threshold 1.01) until a night scores the version under the new spec; `GET /ratchet/tasks` shows it as `recalibrating`.

**Pinned tasks** (`Task.pinned = True`) skip the student entirely. `Ratchet.handle` sends them to the teacher, logs the request (`served_by=teacher`, cost equal to baseline) and does **not** store the answer as a training example. Use `pinned` for open-ended work (planning, long writing) that a small model should not take over. Every other task is **distillable**: its teacher answers become training data. Only an operator's spec pins a task (built-in, tasks file, admin API): no header, metadata key, `model` value or `response_format` can. Pinning a task that already has stored examples keeps them out of training and out of the golden eval from the next night on (`excluded_pinned` in the dataset manifest); the ledger keeps them, so unpinning brings them back.

Every answer is cleaned the same way before it is compared, served or stored (`tasks.clean_answer`): reasoning is removed (`<think>` blocks, anything before a lone `</think>`, an unterminated `<think>` that opens what is left and what follows), and for a JSON task (any task with fields, unless its format is `text`) a code fence around the whole answer is unwrapped when its body is JSON; an answer that is one JSON object is kept whole, tags quoted in its strings included. A teacher answer is stored as a training example only when it is complete (`finish_reason` `stop` or `tool_calls`), not empty, and valid under the task (invariant 16); otherwise it is served and logged as a `skip_example` event. So a JSON task's system prompt should ask for JSON only, and list the allowed values of each label.

Clients pick a task with the `X-Ratchet-Task` header or `metadata.ratchet_task`. A name that is not registered follows `RATCHET_UNKNOWN_TASKS`: by default it gets a temporary task without fields with that name (`source` `adhoc`, not added to the registry, never pinned), so its data stays separate. Names must match `[A-Za-z0-9_.-]{1,64}`; a header that does not becomes `default` (a bad `metadata.ratchet_task` is a 400). At most 500 tasks are registered, built-in ones included.

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
