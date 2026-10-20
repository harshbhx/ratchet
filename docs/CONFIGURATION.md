# Configuration

All settings come from environment variables. Defaults live in `ratchet/config.py` (`Settings`), `ratchet/live.py`, the `deploy/` scripts and `deploy/vm/`. Model IDs and prices follow the Nebius Token Factory catalog as of October 2026. Check them on day 1 with `python scripts/day1_check.py` (see [DAY1.md](DAY1.md)).

## Gateway and cascade (`ratchet/config.py`)

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_MODE` | `mock` | `mock`: offline demo with mock models and an in-memory ledger. `live`: real Token Factory teacher and a SQLite file |
| `RATCHET_DB` | `ratchet.db` | SQLite ledger path (live mode only; mock mode always uses `:memory:`) |
| `RATCHET_TEACHER_BASE` | `https://api.tokenfactory.nebius.com/v1` | OpenAI-compatible base URL for the teacher, judge and fallback student |
| `NEBIUS_API_KEY` | empty | Token Factory API key (sent as `Authorization: Bearer …`) |
| `RATCHET_TEACHER_MODEL` | `nvidia/Nemotron-3-Ultra-550b-a55b` | Teacher model. Must be in the license registry and distillable, or startup fails with `LicenseError` |
| `RATCHET_JUDGE_MODEL` | `nvidia/nemotron-3-super-120b-a12b` | Judge for free-text golden evaluation (`LiveEvaluator`) |
| `RATCHET_FALLBACK_STUDENT` | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` | Hosted, untuned model used for student calls when `RATCHET_STUDENT_BASE` is empty |
| `RATCHET_STUDENT_BASE` | empty | Base URL of your vLLM endpoint, e.g. `https://<endpoint>/v1` |
| `RATCHET_STUDENT_KEY` | empty | Token for the student endpoint |
| `RATCHET_STUDENT_BASE_MODEL` | `nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16` | Base model that adapters are trained on (also used by the deploy scripts) |
| `RATCHET_CONFIDENCE_THRESHOLD` | `0.80` | Fallback threshold, used only when a version has no calibrated `threshold` in its eval |
| `RATCHET_SAMPLES` | `3` | Student samples per request (self-consistency) and per golden row |
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
| `RATCHET_MERGE_SYSTEM` | `0` | `1`: merge consecutive leading `system` messages (after `developer` becomes `system`) into one, joined by a blank line, for models whose chat template takes a single system message. It changes the stored form of such requests, so set it before collecting training data, not halfway |
| `RATCHET_TEACHER_MODALITIES` | `text` | Content part types the teacher accepts besides text, comma-separated, e.g. `text,image_url` (also `input_audio`, `file`). A request with such a part goes to the teacher only: no student call, no audit, nothing stored as a training example. Any other non-text part is a 400 `unsupported_content_type`. Set it only when the teacher model reads those parts; otherwise the teacher's refusal comes back as a 400 with its own message ([API.md](API.md#errors)) |

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
| `RATCHET_STUDENT_BREAKER_COOLDOWN_S` | `30` | Seconds an open student breaker waits before one request probes the endpoint again. A failed probe doubles the wait, up to 300 s (or this value, if larger); a successful probe closes the breaker and resets it |

## Privacy and security

Read in `ratchet/config.py`; used by `ratchet/router.py`, `ratchet/auth.py`, `ratchet/bom.py` and `ratchet/gateway.py`. Background in [ARCHITECTURE.md](ARCHITECTURE.md#pii-redaction), [API.md](API.md#authentication) and [THREAT_MODEL.md](THREAT_MODEL.md).

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_REDACT_PII` | `1` | Replace emails, phone numbers, card numbers (Luhn-checked), IBANs (mod-97), Aadhaar (Verhoeff), US SSNs, Indian PANs, IPv4 addresses and API secrets with `[LABEL]` placeholders before a request and the teacher's answer are stored as a training example. The teacher still receives the original. `0` stores examples verbatim. Names and street addresses are not detected |
| `RATCHET_API_KEYS` | empty | `name:key,name:key`. When set, `/v1/*` needs `Authorization: Bearer <key>` or `X-API-Key: <key>`; anything else gets 401. Names: letters, digits, `_`, `.`, `-` (up to 64). Keys: at least 16 characters; generate with `openssl rand -hex 24`. A bare key without `name:` is named `keyN`. The ledger stores the name per request, never the key. A malformed value stops the gateway at startup |
| `RATCHET_API_KEY_DAILY_REQUESTS` | `0` | Chat completions per key per UTC day; past it a 429 `insufficient_quota` (code `daily_quota_exceeded`) with `Retry-After` until 00:00 UTC and `X-Should-Retry: false` ([API.md](API.md#authentication)). `0` = unlimited. Counted from the ledger, so it survives restarts in live mode |
| `RATCHET_BOM_SIGNING_KEY` | empty | Ed25519 private key as the base64 of its 32-byte seed. Signs each adapter's bill of materials. Generate with `python -m ratchet.bom keygen`, which also prints the public key to publish. Needs the `sign` extra (`pip install "ratchet-distill[sign]"`, included in the gateway image). An invalid value stops the gateway at startup. Empty = BOMs are stored with a SHA-256 but unsigned |

Keep `RATCHET_API_KEYS` and `RATCHET_BOM_SIGNING_KEY` out of git, like every other key. Rotating the signing key does not invalidate old BOMs: each one embeds the public key and key id it was signed with, so keep a list of the public keys you have published.

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

`Settings.price(model)` checks for an exact key first, then uses `student` for any name starting with `student`. Anything else gets the teacher's price (or 1.00 / 3.00). To change prices, pass `Settings(prices={...})` in code; there are no price environment variables.

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

## Tasks

A task tells Ratchet when two answers "agree" (`ratchet/tasks.py`):

| Task | `compare_keys` | `pinned` | Agreement rule |
|---|---|---|---|
| `default` | none | no | Whole text equal after removing `<think>` blocks, lower-casing and collapsing whitespace |
| `ticket-triage` | `category`, `priority` | no | Parse JSON (tolerates code fences and surrounding text), compare only those fields, case-insensitive. Free-text fields such as `reply` are ignored |
| `source-triage` | `relevant`, `source_type` | no | Scout's triage agent. Same JSON rule; `reason` is ignored |
| `page-read` | `answers_question`, `fact_type` | no | Scout's reader agent. Same JSON rule; `key_fact` is ignored |
| `research-plan` | none | **yes** | Scout's planner. Always answered by the teacher |
| `research-write` | none | **yes** | Scout's writer. Always answered by the teacher |

**Pinned tasks** (`Task.pinned = True`) skip the student entirely. `Ratchet.handle` sends them to the teacher, logs the request (`served_by=teacher`, cost equal to baseline) and does **not** store the answer as a training example. Use `pinned` for open-ended work (planning, long writing) that a small model should not take over. Every other task is **distillable**: its teacher answers become training data.

Clients pick a task with the `X-Ratchet-Task` header or `metadata.ratchet_task`. An unknown name gets a temporary `default`-style task with that name (not added to the registry), so its data stays separate. Names must match `[A-Za-z0-9_.-]{1,64}`; anything else becomes `default`.

### Adding a task

1. Register it in `ratchet/tasks.py`:

   ```python
   REGISTRY["invoice-extract"] = Task("invoice-extract", compare_keys=["vendor", "total", "currency", "due_date"])
   # or, for a task that must always stay on the teacher:
   REGISTRY["contract-summary"] = Task("contract-summary", pinned=True)
   ```

2. Make the system prompt ask for JSON with those keys.
3. Send traffic with `X-Ratchet-Task: invoice-extract`.
4. Optional: for free-text tasks (no `compare_keys`), `LiveEvaluator` adds a judge score (`judge_pass_rate`) using the judge model. The release gate does not use that score yet.

Choose keys that are verifiable (labels, IDs, numbers). Exact comparison on these fields is what keeps evaluation free of judge bias.

## Teacher licenses

Edit `ratchet/licenses.py` to add a teacher. `REGISTRY` holds known distillable teachers; `CLOSED` holds ID prefixes that are always refused (`gpt`, `o1`, `o3`, `o4`, `claude`, `gemini`). Unknown teachers are refused until registered. See [ADR 0001](adr/0001-open-teacher-only.md).
