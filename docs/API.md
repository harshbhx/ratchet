# HTTP API

The gateway (`ratchet/gateway.py`, FastAPI app `ratchet.gateway:app`) speaks the OpenAI chat-completions format, so any OpenAI SDK works. It also exposes a small Ratchet API for stats and provenance, mock-only demo controls, three web pages and the Scout demo endpoints.

```bash
RATCHET_MODE=mock uvicorn ratchet.gateway:app --port 8000      # offline demo
RATCHET_MODE=live NEBIUS_API_KEY=... uvicorn ratchet.gateway:app --port 8000
```

FastAPI also serves interactive docs at `/docs` and the schema at `/openapi.json`, in mock mode by default and in live mode only with `RATCHET_OPENAPI=1`.

Errors on `/v1/*` use OpenAI's error envelope, so OpenAI SDKs raise typed exceptions with `code` and `param` ([Errors](#errors)); every other route answers errors as `{"detail": ...}`. Every `POST`, `PUT` and `PATCH` body is limited to `RATCHET_MAX_BODY_BYTES` (2 MiB by default; larger is **413**). Every response, errors included, carries `x-request-id` (the request's id, `req_` and 32 hex digits) and `openai-processing-ms` ([Request ids](#request-ids)).

| Method | Path | Mode | Access | Purpose |
|---|---|---|---|---|
| GET | `/` | both | [public](#access-levels) | Story page: the React app (`ratchet/web/dist/index.html`); falls back to `ratchet/web/landing.html` if the build is missing |
| GET | `/dashboard` | both | [public](#access-levels) | Dashboard (React app; fallback `ratchet/dashboard.html`) |
| GET | `/scout` | both | [public](#access-levels) | Scout demo page (React app; fallback `ratchet/web/scout.html`) |
| GET | `/assets/*` | both | [public](#access-levels) | Built JavaScript, CSS and fonts of the React app |
| GET | `/lite`, `/lite/scout`, `/lite/dashboard` | both | [public](#access-levels) | The hand-written pages (no build step, no JavaScript framework) |
| GET | `/favicon.svg` | both | [public](#access-levels) | The site icon, from the React build (404 without it) |
| GET | `/healthz` | both | [public](#access-levels) | Liveness check: the process answers; always public |
| GET | `/livez` | both | [public](#access-levels) | Liveness under its Kubernetes name, the same answer as `/healthz` |
| GET | `/readyz` | both | [public](#access-levels) | Readiness for load balancers: 200 `ready` or `degraded`, 503 `not_ready`, with the ledger, teacher and student checks ([Readiness](#get-readyz)) |
| GET | `/v1/models` | both | [api](#access-levels) | OpenAI-style model list: `ratchet`, `ratchet/<task>` per task, the model aliases (API key when keys are on) |
| GET | `/v1/models/{id}` | both | [api](#access-levels) | One model of the list, or 404 `model_not_found` (API key when keys are on) |
| POST | `/v1/chat/completions` | both | [api](#access-levels) | The cascade; `stream: true` supported (API key when keys are on) |
| POST | `/openai/deployments/{deployment}/chat/completions` | both | [api](#access-levels) | Azure OpenAI's URL for the same cascade, the deployment naming the task ([Azure-style route](#azure-style-route)) |
| POST | `/v1/completions` | both | [api](#access-levels) | The legacy completions API through the same cascade, one request per prompt ([Completions](#post-v1completions)) |
| POST | `/v1/responses` | both | [api](#access-levels) | The stateless subset of the Responses API through the same cascade ([Responses](#post-v1responses)) |
| POST | `/v1/embeddings` | both | [api](#access-levels) | Embeddings, passed through to `RATCHET_EMBEDDINGS_MODEL`; no cascade, nothing stored ([Embeddings](#post-v1embeddings)) |
| POST | `/v1/moderations` | both | [api](#access-levels) | Not served: 404 `unsupported_endpoint` ([Unsupported endpoints](#unsupported-endpoints)) |
| GET | `/v1/responses/{response_id}` | both | [api](#access-levels) | Retrieve a stored response (or list its input items): not served (no response state), 404 `unsupported_endpoint` |
| POST | `/v1/responses/{response_id}` | both | [api](#access-levels) | Cancel a stored response: not served, 404 `unsupported_endpoint` |
| DELETE | `/v1/responses/{response_id}` | both | [api](#access-levels) | Delete a stored response: not served, 404 `unsupported_endpoint` |
| POST | `/v1/ratchet/no-train` | both | [api](#access-levels) | Register an end user whose requests are never stored as training examples ([End users and storage](#end-users-and-storage)) |
| DELETE | `/v1/ratchet/no-train` | both | [api](#access-levels) | Remove a no-train registration (API key, or admin key) |
| DELETE | `/v1/ratchet/examples` | both | [api](#access-levels) | Erase the stored examples of one of the caller's end users ([Erasure and export](#erasure-and-export)) |
| POST | `/v1/ratchet/feedback` | both | [api](#access-levels) | A verdict on a served answer, optionally with a correction stored as a human label ([Feedback](#feedback)) |
| GET | `/v1/ratchet/requests/{request_id}` | both | [api](#access-levels) | One request this API key sent, by its id: metadata and failures, never text ([Request ids](#request-ids)) |
| GET | `/ratchet/stats` | both | [dashboard](#access-levels) | Everything the dashboard shows |
| GET | `/ratchet/requests/{request_id}` | both | [dashboard](#access-levels) | Any request by its id: metadata and failures, never text ([Request ids](#request-ids)) |
| GET | `/metrics` | both | [metrics](#access-levels) | Prometheus metrics, text format 0.0.4 ([Metrics](#get-metrics)) |
| GET | `/ratchet/bom` | both | [bom](#access-levels) | Bill of materials for the whole ledger (provenance) |
| GET | `/ratchet/versions/{id}/bom` | both | [bom](#access-levels) | One adapter's bill of materials, written at training time, signed when a key is set |
| GET | `/ratchet/versions/{id}/certificate` | both | [dashboard](#access-levels) | What a version's golden evals certify: the signed and the latest certificate, frontier and per-task bounds, the gate's evidence and the assumptions ([Certificates](#get-ratchetversionsidcertificate)) |
| POST | `/ratchet/versions/{id}/proof` | both | [admin](#access-levels) | Whether an example is in a version's committed training (or golden) set, with a membership or non-membership proof against the root its signed BOM carries (admin key; [Committed training sets](#committed-training-sets)) |
| GET | `/ratchet/versions/{id}/heldout` | both | [admin](#access-levels) | The held-out certificate: every golden example's proof of absence from the version's training set (admin key) |
| GET | `/ratchet/drift` | both | [dashboard](#access-levels) | The label-free drift monitor: each (version, task) stream's test martingale on the student's confidence, its alarm and the false-alarm bounds ([Drift](#get-ratchetdrift)) |
| GET | `/ratchet/teacher` | both | [dashboard](#access-levels) | The teacher watch: the check epoch, the teacher's self-agreement on first re-tests, the freeze rule's e-value, the agreement ceiling and the contested labels ([Teacher watch](#teacher-watch)) |
| POST | `/ratchet/teacher/ack` | both | [admin](#access-levels) | Acknowledge the teacher watch: a new check epoch, Night School unfrozen (admin key; open in mock mode without admin keys) |
| GET | `/ratchet/bom/public-key` | both | [public](#access-levels) | Ed25519 public key that verifies adapter BOMs |
| POST | `/ratchet/redact` | both | [dashboard](#access-levels) | Show what the PII redactor would store for a text (stores nothing) |
| GET | `/ratchet/kpis` | both | [dashboard](#access-levels) | PRD §8.1's success metrics and NFR-1's latency target, computed from the ledger ([Success metrics](#get-ratchetkpisdaysn)) |
| GET | `/ratchet/cost` | both | [dashboard](#access-levels) | The cost report: savings per token and by GPU-hours paid, break-even, the hosted-fallback crossover and a recommendation ([Cost](#cost)) |
| GET | `/ratchet/replay` | both | [dashboard](#access-levels) | Replay one version's logged traffic at another threshold or audit rate: coverage, precision, cost, and the calibrated baseline ([Ledger replay](#ledger-replay)) |
| GET | `/ratchet/replay/frontier` | both | [dashboard](#access-levels) | One version's cost-precision frontier over thresholds 0.30 to 1.00, an advisory suggested threshold and the money the current one leaves on the table |
| POST | `/ratchet/cost/endpoint` | both | [admin](#access-levels) | Record the student endpoint going up or down (admin key) |
| GET | `/ratchet/receipts` | both | [api](#access-levels) | The sealed savings receipts a caller may read, by day ([Savings receipts](#savings-receipts)) |
| GET | `/ratchet/receipts/{period}` | both | [api](#access-levels) | One day's signed savings receipt: the gateway's, or a client's (`?client=`; an API key gets its own) |
| GET | `/ratchet/receipts/{period}/proof/{request_id}` | both | [api](#access-levels) | That a request (`X-Ratchet-Request-Id`) is in its day's receipt: its leaf and RFC 9162 audit path |
| GET | `/ratchet/receipts/{period}/leaves` | both | [api](#access-levels) | A receipt's leaves as JSON lines, to recompute it offline |
| GET | `/ratchet/tasks` | both | [dashboard](#access-levels) | Every registered task with its spec, source, spec hash and traffic ([Tasks](#tasks)) |
| GET | `/ratchet/tasks/{name}` | both | [dashboard](#access-levels) | One registered task |
| POST | `/ratchet/tasks/validate` | both | [admin](#access-levels) | Check a task spec or a tasks file without changing anything (admin key) |
| PUT | `/ratchet/tasks/{name}` | both | [admin](#access-levels) | Create or replace an API task (admin key) |
| DELETE | `/ratchet/tasks/{name}` | both | [admin](#access-levels) | Remove an API task; its stored examples stay (admin key) |
| POST | `/ratchet/admin/privacy/erase` | both | [admin](#access-levels) | Erase stored examples by client, end user, hash or text; a dry run by default (admin key; [Erasure and export](#erasure-and-export)) |
| GET | `/ratchet/admin/export/{kind}` | both | [admin](#access-levels) | One table (`examples`, `requests`, `versions`, `events`, `erasures`) as NDJSON (admin key) |
| GET | `/ratchet/admin/versions` | both | [admin](#access-levels) | Every version with its golden eval and its current stage's evidence, and the runtime switches ([Admin](#admin)) |
| GET | `/ratchet/admin/flags` | both | [admin](#access-levels) | The runtime switches and task pins, with who set each one last |
| GET | `/ratchet/admin/audit-log?limit=50` | both | [admin](#access-levels) | The newest admin actions: who, what, why, state before and after |
| POST | `/ratchet/admin/versions/{id}/rollback` | both | [admin](#access-levels) | Roll a canary back |
| POST | `/ratchet/admin/versions/{id}/retire` | both | [admin](#access-levels) | Retire a live, canary or shadow version |
| POST | `/ratchet/admin/versions/{id}/promote` | both | [admin](#access-levels) | Promote a version to a canary stage, or to live over the live version |
| POST | `/ratchet/admin/student/disable`, `/ratchet/admin/student/enable` | both | [admin](#access-levels) | Turn the student off (the teacher answers everything) or back on |
| POST | `/ratchet/admin/releases/freeze`, `/ratchet/admin/releases/unfreeze` | both | [admin](#access-levels) | Hold every promotion (rollbacks and alerts keep running), or lift the hold |
| POST | `/ratchet/admin/training/pause`, `/ratchet/admin/training/resume` | both | [admin](#access-levels) | Skip Night School until resumed |
| PUT | `/ratchet/admin/settings` | both | [admin](#access-levels) | Set the runtime audit rate |
| POST | `/ratchet/admin/tasks/{name}/pin` | both | [admin](#access-levels) | Pin a registered task to the teacher at runtime |
| DELETE | `/ratchet/admin/tasks/{name}/pin` | both | [admin](#access-levels) | Remove a task's runtime pin |
| POST | `/ratchet/admin/tick` | both | [admin](#access-levels) | Run the release gate now, recorded as an admin action |
| POST | `/ratchet/demo/day` | mock only | [mock](#access-levels) | Simulate one day of traffic and one night of training |
| POST | `/ratchet/demo/break-next` | mock only | [mock](#access-levels) | Make the next trained student deploy corrupted |
| POST | `/ratchet/demo/student-outage` | mock only | [mock](#access-levels) | Take the student endpoint down for a while, to show the circuit breaker |
| GET | `/ratchet/demo/sample-example` | mock only | [mock](#access-levels) | A committed example's prompt, as a body for the proof endpoint |
| POST | `/ratchet/demo/teacher-drift` | mock only | [mock](#access-levels) | Make the mock teacher change labels at random, to show the teacher watch |
| POST | `/ratchet/demo/reset` | mock only | [mock](#access-levels) | Start over |
| GET | `/scout/examples` | both | [public](#access-levels) | Example questions and whether free text is allowed |
| GET | `/scout/status` | both | [public](#access-levels) | Budget guard state, the judges' reserve, search and Tavily credits, and which model answers student calls (`fallback_student`, `student_serving`) |
| POST | `/scout/judge` | both | [public](#access-levels) | Open a judge session: the access code in the body, a signed cookie back |
| DELETE | `/scout/judge` | both | [public](#access-levels) | End a judge session (clears the cookie) |
| GET | `/scout/stream?q=` | both | [public](#access-levels) | Run Scout, stream events (Server-Sent Events) |
| POST | `/scout/run` | both | [public](#access-levels) | Run Scout, return all events at the end |
| GET | `/docs`, `/redoc`, `/openapi.json` | both | [public](#access-levels) | FastAPI's interactive docs and schema; they exist only with `RATCHET_OPENAPI=1` (the default in mock mode, not in live mode) |

The three pages are static HTML files with no build step. If a page file is missing, the route returns a small placeholder page with links to the other two.

## OpenAI endpoints

What an OpenAI client pointed at `http://<gateway>/v1` can call ([ADR 0050](adr/0050-adjacent-openai-endpoints.md), FR-1.51). Every route below shares authentication, the body limit, request validation with OpenAI's error envelope, the daily quota and the request log. Error messages for an unsupported endpoint link here.

| Endpoint | Status | What happens |
|---|---|---|
| `POST /v1/chat/completions` | Supported | The cascade: student first, teacher when it is unsure, the teacher's answer stored as training data |
| `POST /openai/deployments/{deployment}/chat/completions` | Supported | The same, for Azure OpenAI clients ([Azure-style route](#azure-style-route)) |
| `GET /v1/models`, `GET /v1/models/{id}` | Supported | The model ids a client may send, the embeddings model among them |
| `POST /v1/completions` | Supported, without `suffix`, `echo`, `best_of` > `n` or token-array prompts | Each prompt is one user message through the chat path, so it is answered, logged and stored exactly as that chat request ([Completions](#post-v1completions)) |
| `POST /v1/responses` | Partial: stateless, not streamed, function tools only | `instructions` and `input` become chat messages, then the chat path ([Responses](#post-v1responses)) |
| `POST /v1/embeddings` | Supported, as a pass-through | One call to `RATCHET_EMBEDDINGS_MODEL`, whatever `model` says; logged and counted, nothing stored ([Embeddings](#post-v1embeddings)) |
| `POST /v1/moderations` | Not supported | 404 `unsupported_endpoint`: the gateway has no moderation model |
| `GET` / `DELETE /v1/responses/{id}`, `POST /v1/responses/{id}/cancel`, `GET /v1/responses/{id}/input_items` | Not supported | 404 `unsupported_endpoint`: Ratchet keeps no response state |
| Anything else under `/v1/` (images, audio, files, uploads, batches, fine-tuning, vector stores, assistants, threads, realtime, ...) | Not supported | 404 `unknown_url` |

## Authentication

Off by default in mock mode, so the demo and local runs need no key. Set `RATCHET_API_KEYS` ([CONFIGURATION.md](CONFIGURATION.md#privacy-and-security)) to require a key on `/v1/*` and `/openai/*`. Live mode refuses to start without them, unless `RATCHET_ALLOW_UNAUTHENTICATED=1` says something in front of the gateway (the demo VM's Caddy guard, a private network) checks callers:

```bash
RATCHET_API_KEYS="acme:$(openssl rand -hex 24),globex:$(openssl rand -hex 24)" uvicorn ratchet.gateway:app
```

- Send the key as `Authorization: Bearer <key>` (what every OpenAI SDK sends as `api_key`), as `X-API-Key: <key>`, or as `api-key: <key>` (what Azure OpenAI SDKs send), checked in that order.
- A missing or wrong key returns **401** `invalid or missing API key` (error code `invalid_api_key`) with `WWW-Authenticate: Bearer`. The key is checked before the body is read.
- The ledger records the key's **name** (`acme`) on every request it sends, never the key. `/ratchet/stats` reports requests, cost and savings per name under `clients` (chat-shaped requests only: embeddings calls count against the quota but not there).
- `RATCHET_API_KEY_DAILY_REQUESTS=N` caps each key at N requests per UTC day: chat completions, responses, embeddings, and completions at one request per prompt (a batch the quota cannot finish is refused whole, before its first prompt runs; a concurrent request from the same key can still use up the quota before a later prompt, which then [stops the batch](#post-v1completions)). Responses carry `X-RateLimit-Limit-Requests`, `X-RateLimit-Remaining-Requests` and `X-RateLimit-Reset-Requests` (time to 00:00 UTC in OpenAI's duration format, rounded up to the second: `3h12m5s`). Past the cap the gateway returns **429** `insufficient_quota` (code `daily_quota_exceeded`) until 00:00 UTC, with `Retry-After` (seconds until then) and `X-Should-Retry: false`, so OpenAI SDKs send it once and do not retry. Only answered requests count: a refused request (400, 413, 429) uses no quota. Concurrent requests can overshoot the cap by the number in flight. `RATCHET_KEY_LIMITS` gives a key its own quota, US dollar budgets, rates, task scope and storage switch ([Limits and headers](#limits-and-headers)).
- **Admin keys** (`RATCHET_ADMIN_KEYS`, same syntax, never the same keys) guard management writes: `PUT` and `DELETE /ratchet/tasks/{name}`, `POST /ratchet/tasks/validate` ([Tasks](#tasks)) and `POST /ratchet/cost/endpoint` ([Cost](#cost)); every route under `/ratchet/admin/`, its reads included ([Admin](#admin)); the training-set proofs, `POST /ratchet/versions/{id}/proof` and `GET /ratchet/versions/{id}/heldout` ([Committed training sets](#committed-training-sets)), which would otherwise tell anyone whether a text they can guess was trained on; and they may register or remove a no-train end user for any client ([End users and storage](#end-users-and-storage)). Send one the same way. A missing or wrong key is **401** `invalid or missing admin key` (`WWW-Authenticate: Bearer`), an API key or a dashboard key **403**. An admin key also reads the dashboard API. Without admin keys these calls are **403** `admin API disabled: set RATCHET_ADMIN_KEYS` in live mode, and open in mock mode (recorded as `mock-admin`). The ledger and the event log record the admin's name, never the key.
- **Savings receipts** (`/ratchet/receipts*`, [Savings receipts](#savings-receipts)): with API keys on, a key reads only its own receipts and proofs; an admin key reads every receipt; any other caller is **401**. In mock mode without keys they are open; in live mode without any keys they are **403** (`receipts need RATCHET_API_KEYS or RATCHET_ADMIN_KEYS in live mode`), because their leaves list every request's time, task and cost.
- **Dashboard keys** (`RATCHET_DASHBOARD_KEYS`, same syntax, never an API or admin key) read the dashboard API and write nothing. Who needs which key is the route's access level ([Access levels](#access-levels)): in live mode the dashboard API needs an admin or dashboard key by default.
- Pages, `/healthz`, `/livez`, `/readyz`, `/ratchet/bom/public-key` and Scout are always public. The mock-only demo controls are covered by the Caddy guard on the demo VM. Scout's agents call the cascade inside the process, so they never need a key; the Scout budget guard protects them.

### Access levels

Every route has one access level (the Access column above; `ratchet/access.py`, [ADR 0061](adr/0061-endpoint-access-levels.md)). A key goes in `Authorization: Bearer <key>` or `X-API-Key: <key>`, whatever the level.

| Level | Who may call it | Without the right key |
|---|---|---|
| `public` | Anyone | (never refused for a key) |
| `api` | With `RATCHET_API_KEYS` set: an API key. Without: anyone (mock mode, or live mode with `RATCHET_ALLOW_UNAUTHENTICATED=1`) | **401** `invalid_api_key` in OpenAI's envelope |
| `dashboard` | With `RATCHET_PUBLIC_DASHBOARD=1` (the default in mock mode): anyone. Else (the default in live mode): an admin or dashboard key | **401** `invalid or missing dashboard key` (`WWW-Authenticate: Bearer`); **403** for an API key; **403** `the dashboard API is private: ...` when neither `RATCHET_ADMIN_KEYS` nor `RATCHET_DASHBOARD_KEYS` is set |
| `bom` | As `dashboard`, and anyone with `RATCHET_PUBLIC_BOM=1`, so auditors can read provenance from a private gateway | As `dashboard` |
| `metrics` | As `dashboard`, and a scraper that sends `RATCHET_METRICS_TOKEN` as its bearer token | As `dashboard`; the 403 for a private dashboard needs `RATCHET_METRICS_TOKEN` unset too |
| `admin` | An admin key. Without `RATCHET_ADMIN_KEYS`: nobody in live mode, anyone in mock mode (as `mock-admin`) | **401** `invalid or missing admin key`; **403** for an API key or a dashboard key; **403** `admin API disabled: set RATCHET_ADMIN_KEYS` in live mode without admin keys |
| `mock` | Anyone, in mock mode only (invariant 8) | **403** `demo controls are only available in mock mode` in live mode |

On a public dashboard a key is optional: an admin or dashboard key gets the full payload, and no key, a wrong key or an API key reads as anonymous (never a 401 or 403). A route that is not in the table is `dashboard` (closed in live mode until someone classifies it). The same table already covers routes added under `/ratchet/admin/` (admin), `/ratchet/requests/`, `/ratchet/versions/`, `/scout/` and `/v1/`, and `/livez`, `/readyz` (public) and `/ratchet/kpis` (dashboard).

**Scrubbed reads.** A dashboard shown to anyone should not show who your clients are or where your endpoints live. By default an anonymous read of a public dashboard in live mode is scrubbed (`RATCHET_SCRUB_STATS=1` scrubs every read, `0` none): in `/ratchet/stats` client names become `client-1`, `client-2`, ... (stable, by sorted name; the same pseudonyms replace the names in task allow-lists from `GET /ratchet/tasks`, in a request lookup and in the `client` labels of `/metrics`), every `http(s)://` URL in an event detail becomes `<url>`, and an error message after an exception's class is cut (`ConnectError: [Errno 111] ...` becomes `ConnectError`). `security.scrubbed` says whether a payload was scrubbed. A reader who sends an admin or dashboard key gets the full payload. Only names and pseudonyms ever appear, never keys (invariant 13).

**Security headers.** Every response carries `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`, and the `Content-Security-Policy` in `RATCHET_CSP` when it is set (none by default, so the pages never break; [CONFIGURATION.md](CONFIGURATION.md#privacy-and-security) has a recommended value).

The demo VM's Caddy config also has an opt-in edge guard for `/v1/*`, `/openai/*` (the [Azure-style route](#azure-style-route) runs the same paid cascade), `/ratchet/demo/*` and `/ratchet/admin/*` ([DEPLOY_NEBIUS_VM.md](DEPLOY_NEBIUS_VM.md#security-notes)). Use either or both. See [THREAT_MODEL.md](THREAT_MODEL.md) T1 and T19.

### Limits and headers

What a key, a client address and the whole gateway may spend and send on `POST /v1/chat/completions` and the other OpenAI endpoints that call a model (the [Azure-style route](#azure-style-route), `/v1/completions`, whose batch needs a request of the quota per prompt, `/v1/responses` and `/v1/embeddings`) ([ADR 0063](adr/0063-spend-and-rate-limits.md); settings in [CONFIGURATION.md](CONFIGURATION.md#spend-and-rate-limits)). Every limit is off by default except the in-flight cap (32) and, in live mode without API keys, the per-IP rate (120 a minute). Limits are per process and keyed by key name, never the key.

```bash
RATCHET_API_KEY_DAILY_REQUESTS=10000 RATCHET_API_KEY_RPM=120 \
RATCHET_KEY_LIMITS="acme:daily_usd=5,monthly_usd=100,concurrency=4,tasks=ticket-triage|invoice-extract;globex:daily_usd=1,store=0" \
RATCHET_DAILY_BUDGET_USD=20 RATCHET_BUDGET_MODE=student_only uvicorn ratchet.gateway:app
```

They are checked in this order, so a refusal calls no model and costs nothing: the key's (or the client address's) requests a minute, before the body is read; then, once the body is in, the in-flight cap and the key's requests in flight; then, after the body is validated, the task scope, the daily request quota, the key's US dollar budgets and the gateway's budget. The rate runs before the body is read, so a request refused later has used its token. The in-flight caps count only requests whose body has arrived, so a client that sends its body slowly holds no slot meant for requests that are being answered.

| Limit | Setting (per key: `RATCHET_KEY_LIMITS` field) | Over it | Headers |
|---|---|---|---|
| Chat requests in flight, all clients | `RATCHET_MAX_INFLIGHT` (32) | **503** `api_error` / `overloaded`: the gateway answers at once instead of queueing the request | `Retry-After: 1` |
| A key's requests in flight | `RATCHET_API_KEY_CONCURRENCY` (`concurrency`) | **429** `rate_limit_error` / `rate_limit_exceeded` (`'acme' has 4 requests in flight, its limit; ...`) | `Retry-After: 1` |
| A key's requests a minute | `RATCHET_API_KEY_RPM` (`rpm`): a token bucket of `max(1, rpm // 6)` requests that refills continuously | **429** `rate_limit_error` / `rate_limit_exceeded` (`rate limit of 60 requests a minute reached for 'acme'; retry after 1 s`) | `Retry-After` (seconds until one more request passes), `X-RateLimit-Limit-Requests` (the rate), `X-RateLimit-Remaining-Requests` (`0`), `X-RateLimit-Reset-Requests` (until the bucket is full) |
| A client address's requests a minute, on a gateway without API keys | `RATCHET_IP_RPM` (live 120, mock 0); the first `X-Forwarded-For` hop with `RATCHET_TRUST_PROXY=1` | As for a key (`... for this client address ...`; the address is not echoed) | As for a key |
| The tasks a key may send | `tasks=a\|b` | **403** `permission_error` / `task_not_allowed` (`task not allowed for this key`), also for a request without a task (`default`) unless the scope lists it; an unknown-task 400 lists only the scope's tasks | None |
| A key's chat completions per UTC day | `RATCHET_API_KEY_DAILY_REQUESTS` (`daily_requests`) | **429** `insufficient_quota` / `daily_quota_exceeded` until 00:00 UTC | `Retry-After`, `X-RateLimit-Limit-Requests`, `X-RateLimit-Remaining-Requests`, `X-RateLimit-Reset-Requests`, `X-Should-Retry: false` |
| A key's US dollars per UTC day and month | `RATCHET_API_KEY_DAILY_USD` (`daily_usd`), `RATCHET_API_KEY_MONTHLY_USD` (`monthly_usd`) | **429** `insufficient_quota` / `budget_exceeded` (`the daily budget of $5.00 is spent for 'acme'; it resets at 00:00 UTC`; a spent monthly budget resets at 00:00 UTC on the 1st, and the message names the cap that resets last) | `Retry-After` and `X-Ratchet-Budget-Reset` (the reset, in seconds and as `3h12m5s`), `X-Ratchet-Spend-Today-USD`, `X-Ratchet-Budget-Remaining-USD: 0.000000`, `X-Should-Retry: false` |
| The gateway's US dollars per UTC day, all keys together | `RATCHET_DAILY_BUDGET_USD`, `RATCHET_BUDGET_MODE` | `block`: **429** `insufficient_quota` / `gateway_budget_exceeded` for every chat request until 00:00 UTC. `student_only`: a confident student answer is still a 200 (no audit, no shadow); a request that needs the teacher (an escalation, a cold start, a pinned or unregistered task, `tools`) gets that 429. The gateway's budget is not named in the message | `Retry-After` (to 00:00 UTC), `X-Should-Retry: false` |
| Training data from a key | `store=0` | Never refused: the key's teacher answers are served and audited but never stored as training examples (`requests.stored = 0`) | None |

On a 200, a key with a daily request quota gets the `X-RateLimit-*-Requests` headers for that quota (as since v0.4); a key with a rate and no daily quota gets them for its bucket. A key with a US dollar budget also gets `X-Ratchet-Spend-Today-USD` (its dollars since 00:00 UTC, this request included, six decimals) and `X-Ratchet-Budget-Remaining-USD` (what it may still spend before its first cap). Dollars are per-token prices, answered and failed requests together (a request that failed after the student was sampled spent those samples); they are simulated in mock mode.

A check reads what was spent before the request, so a request in flight can overshoot a cap by its own cost, and concurrent requests by theirs. Counts live in memory: the gateway reads today's (and with a monthly budget this month's) spend from the ledger at startup, so a restart forgets nothing, and a new UTC day or month starts from zero. Only gateway API requests count: Scout's agents call the cascade inside the process and stay under the Scout budget (invariant 11).

The first time a key or the gateway reaches each `RATCHET_BUDGET_ALERT_PCT` share (50, 80 and 100%) of a dollar cap in its UTC day or month, the release log gets a `budget` event (`acme reached 80% of $5.00 daily budget`); a restart does not repeat one. `/ratchet/stats` shows each key's `spent_today_usd` and `daily_cap_usd` under `clients`, and the gateway's budget under `budget` ([GET /ratchet/stats](#get-ratchetstats)). A scrubbed copy names no key and no cap: the event reads `client-1 reached 80% of its daily budget`, and `daily_cap_usd`, `budget.daily_usd` and `budget.remaining_usd` are `null`.

---

## POST `/v1/chat/completions`

### Request

| Field | Type | Required | Notes |
|---|---|---|---|
| `messages` | array of message objects | yes | 1 to `RATCHET_MAX_MESSAGES` (256) messages ([Messages](#messages)) |
| `model` | string | no | Names the task, never an upstream model: `"ratchet"` (the task comes from the header, metadata or the other rules), `"ratchet/<task>"` (or `"ratchet:<task>"`) for that task, or a model alias (`RATCHET_MODEL_ALIASES`). Any other id is served as `ratchet` with `X-Ratchet-Model-Alias` (`RATCHET_MODEL_POLICY=lenient`, the default) or is a 404 `model_not_found` (`strict`). See [Task resolution](#task-resolution) |
| `stream` | bool | no | `true` returns Server-Sent Events in the OpenAI chunk format ([Streaming](#streaming)) |
| `stream_options` | object | no | With `stream: true`, `{"include_usage": true}` ends the stream with a chunk that carries `usage` ([Streaming](#streaming)). Other keys are ignored, and so is the object without a stream. Never forwarded |
| `metadata` | object | no | Up to 16 string values. `metadata.ratchet_task` is the task name, used if the header is absent. `metadata.ratchet_store` `"false"` or `"0"` keeps the request out of the training set ([End users and storage](#end-users-and-storage)). Echoed in the response, never stored |
| `store` | bool | no | `false` keeps the request's teacher answers out of the training set ([End users and storage](#end-users-and-storage)). Absent or `true` means store: Ratchet's default is the opposite of OpenAI's, because distilling is what it is for. Never forwarded |
| `user`, `safety_identifier` | string | no | The end user: `safety_identifier` if sent, else `user`, any length (hashed whole). Stored only as a keyed hash ([End users and storage](#end-users-and-storage)); never forwarded |
| `n` | integer | no | Number of choices, 1 (default) to `RATCHET_MAX_N` (4); more is a 400 `unsupported_value` ([Choices and logprobs](#choices-and-logprobs)) |
| `max_tokens`, `max_completion_tokens`, `temperature`, `top_p`, `stop`, `seed`, `presence_penalty`, `frequency_penalty`, `logit_bias`, `logprobs`, `top_logprobs`, `response_format`, `tools`, `tool_choice`, `parallel_tool_calls`, `reasoning_effort` | | no | Checked against OpenAI's rules ([Request validation](#request-validation)), then sent to each model as its role allows ([Generation parameters](#generation-parameters)). `max_completion_tokens` wins over `max_tokens`; a string `stop` counts as a one-item list. `logprobs: true` returns each choice's token logprobs ([Choices and logprobs](#choices-and-logprobs)) |
| `functions`, `function_call` | | no | The legacy function-calling fields: checked, then sent to the teacher as `tools` and `tool_choice`, and the answer comes back as `message.function_call` ([Tools and function calling](#tools-and-function-calling)) |
| `user`, `safety_identifier`, `store` | | no | Checked, not forwarded: the end user's identity and the storage choice never go upstream |
| any other field | | no | Ignored on purpose: unknown fields are kept, not refused, so newer OpenAI SDKs and third-party clients that send fields Ratchet does not know (`service_tier`, `prompt_cache_key`, …) keep working. One is forwarded only when `RATCHET_TEACHER_PARAMS` or `RATCHET_STUDENT_PARAMS` names it (vLLM's `top_k`, for example) |

`null` for any optional field means it is not set.

| Header | Meaning |
|---|---|
| `X-Ratchet-Store` | `none`, `0` or `false` (any case) keeps the request's teacher answers out of the training set, like `store: false`; `1` or `true` is the default spelled out, and so is an empty value (a header left unset). Any other value is a 400 `invalid_value` |
| `X-Ratchet-Task` | Task name. Takes precedence over `metadata.ratchet_task`: when both are set, the header wins. A name that is not registered ([Tasks](#tasks)) follows `RATCHET_UNKNOWN_TASKS`: by default it creates a task without fields on the fly, which compares whole answers (see [CONFIGURATION.md](CONFIGURATION.md#tasks)); with `teacher` the teacher answers and nothing is stored; with `reject` it is a 400 `unknown_task`. A task with `clients` answers 403 `task_not_allowed` to any other key. A header that is not a valid name means `default` under `distill`, and is a 400 `unknown_task` under `teacher` and `reject`. Missing means `metadata.ratchet_task`, else the `model` field and the rules below ([Task resolution](#task-resolution)), else `default`. No header, metadata key or other field can pin a task: only an operator's spec does |
| `Idempotency-Key` | Optional; 1 to 255 printable ASCII characters (else a 400 `invalid_idempotency_key`). Makes the request idempotent for this API key: a retry with the same key and body gets the first answer again instead of a second cascade ([Idempotency and retries](#idempotency-and-retries)). Never stored or logged |
| `x-stainless-retry-count` | Sent by OpenAI's SDKs (and other Stainless-built ones) on every request: 0 for the first attempt, then 1, 2. Above 0, a teacher answer the request stores never adds to an existing example's `seen`, and `requests.retry_count` records it. A value that is not a plain count is ignored |

| `Authorization`, `X-API-Key`, `api-key` | The API key, when keys are on ([Authentication](#authentication)) |

#### Task resolution

The task is the first of these that names one ([ADR 0048](adr/0048-model-field-routing.md)); the response says which in `ratchet.task_source`:

| # | Rule | `task_source` |
|---|---|---|
| 1 | `X-Ratchet-Task` | `header` |
| 2 | `metadata.ratchet_task` | `metadata` |
| 3 | `model` of `ratchet/<task>` or `ratchet:<task>`; on the [Azure-style route](#azure-style-route), the deployment in the URL instead (the body's `model` is not read there) | `model`, `deployment` |
| 4 | A model alias (`RATCHET_MODEL_ALIASES`: `gpt-4o-mini=ticket-triage`), for the `model` or a deployment | `alias` |
| 5 | The first registered task whose `match` rules all hold ([CONFIGURATION.md](CONFIGURATION.md#task-options): `system_prefix`, `schema_name`, `tools`), in registry order, among the tasks the key may use | `match` |
| 6 | A `response_format` json_schema: its auto task ([Structured outputs](#structured-outputs)), made on first use (at most `RATCHET_MAX_AUTO_TASKS`, counted together with rule 7's; past it, the unknown-task path) | `response_format` |
| 7 | With `RATCHET_AUTO_TASK=system_prompt`: `auto-sys-` and 10 hex digits of the SHA-256 of the first system message, an auto task made on its first admitted request (at most `RATCHET_MAX_AUTO_TASKS`, counted with rule 6's, and never into the registry's last 50 slots; then `default`) | `auto_system` |
| 8 | `default` | `default` |

A name from rules 1 to 4 that is not registered follows `RATCHET_UNKNOWN_TASKS` (the 400's `param` is `model` when the name came from the model field or an alias). The `model` field never selects an upstream model: the teacher always gets `RATCHET_TEACHER_MODEL` (or the fallback teacher's model) and the student its adapter name, and the response's `model` is the model that served. A client that can set only a model name (LangChain's `ChatOpenAI(model=...)`, a no-code tool) uses `model="ratchet/ticket-triage"` or an alias.

#### Idempotency and retries

A client that retries (after a timeout, a dropped connection, a 5xx) can make the gateway answer the same request twice: two cascades, two bills, and, through the frequent-request rule (an example seen `FREQUENT` = 3 times moves from the golden set to training, INV-3), retries could drain the golden set. Two mechanisms stop that ([ADR 0071](adr/0071-idempotency-keys-and-retry-de-duplication.md)).

**With an `Idempotency-Key`.** Keys are per API key (with keys off, every caller shares one key space). For a key this API key has not used within `RATCHET_IDEMPOTENCY_TTL_S` (600 s):

| Request | Answer |
|---|---|
| The first with the key | Runs as usual. A 200 answer is kept in memory, never on disk, for `RATCHET_IDEMPOTENCY_TTL_S` after it finished (at most `RATCHET_IDEMPOTENCY_CACHE` (10,000) answers and 64 MiB, the least recently used dropped first). An error is never kept, so the client's retry runs again |
| The same key and the same request while the first is still running | Waits for the first, at most `RATCHET_TEACHER_DEADLINE_S` (180 s), and gets its answer with `Idempotent-Replayed: true`. If the first fails, the waiting request runs instead. After the wait, or at once when four requests already wait for the key: **409** `idempotency_key_in_use` with `Retry-After: 1`, which OpenAI's SDKs retry |
| The same key and the same request after the first finished | The same answer again, at once, with `Idempotent-Replayed: true`: the same body (the same completion `id`, `created`, `usage` and `ratchet` block, whose `request_id` names the request that ran), or for a stream the same events. No cascade, no ledger row, no daily quota used, and no `X-RateLimit-*` headers; `x-request-id` is the replay's own id |
| The same key with a different request | **400** `idempotency_key_reused` with `X-Should-Retry: false` (not a 409, which SDKs retry): use a new key for a new request |

"The same request" is the JSON body with keys sorted (so a client that serialises its retry again in another key order still matches) plus the `X-Ratchet-Task` header (and on the [Azure-style route](#azure-style-route) the deployment); headers that differ between attempts do not count. The checks run after the API key (401) and the body's JSON (400) and before validation and the quota, so a replay of an answered request is served even after the key's daily quota is used up. `RATCHET_IDEMPOTENCY_CACHE=0` turns the header off. The cache is per gateway process: with several workers, a retry that reaches another process runs again.

```bash
curl -s localhost:8000/v1/chat/completions -H 'Content-Type: application/json' -H 'Idempotency-Key: order-7-triage' \
  -d '{"messages": [{"role": "user", "content": "Where is my order?"}]}'
# the same command again: the same answer, with Idempotent-Replayed: true and no second cascade
```

```python
client.chat.completions.create(model="ratchet", messages=msgs, extra_headers={"Idempotency-Key": "order-7-triage"})
```

OpenAI's SDKs keep extra headers across their own retries, so one `Idempotency-Key` per logical request covers every retry the SDK makes.

**Without a key.** A retry still does not count as another occurrence of its request. A request with `x-stainless-retry-count` above 0 never adds to the `seen` of the example its teacher answer duplicates, and the same API client's repeat of a stored example within `RATCHET_SEEN_DEDUP_S` (300 s in live mode, off in mock mode) counts once, so a client without an SDK that retries a few times in a row cannot move a golden example into training either. A genuine repeat after the window, or the same request from another API key, counts as before. The request itself is answered (and paid for) as usual: only an `Idempotency-Key` saves the second cascade.

#### Messages

| Role | Content | Notes |
|---|---|---|
| `system`, `user` | string or array of content parts | |
| `developer` | string or array of content parts | OpenAI's newer name for `system`; Ratchet sends and stores it as `system` |
| `assistant` | string, array of parts, or `null` with `tool_calls` or `function_call` | `refusal` parts become text; may carry `refusal`, `tool_calls` and the legacy `function_call` |
| `tool` | string or array of text parts | Needs `tool_call_id` |
| `function` | string | Legacy |

A message keeps `role`, `content` and `name`, plus `refusal`, `tool_calls` and `function_call` in an assistant message and `tool_call_id` in a tool message; a tool call keeps `id`, `type` and `function` (`name`, `arguments`). Any other key (assistant `audio` or `annotations` sent back from a response, a vendor field) is dropped, as unknown top-level fields are ignored: it reaches neither the teacher nor the stored example. Before an example is stored, `name`, `refusal` and tool and function call arguments are redacted like content ([ADR 0009](adr/0009-pattern-based-pii-redaction.md)).

Content parts: `{"type": "text", "text": "..."}` in any message, and `{"type": "refusal", "refusal": "..."}` in assistant messages. A message made only of text parts is turned into one string, the parts joined by newlines, before anything else happens, so it is answered, deduplicated and stored exactly like the same text sent as a string. Messages already in the plain form pass through unchanged. Other part types (`image_url`, `input_audio`, `file`) get **400** `unsupported_content_type`, unless `RATCHET_TEACHER_MODALITIES` lists them ([CONFIGURATION.md](CONFIGURATION.md#request-limits-and-openai-compatibility)): then the request goes to the teacher only, with the parts as sent, and nothing is stored as a training example. `RATCHET_MERGE_SYSTEM=1` merges consecutive leading system messages into one.

The task decides how answers are compared and which answers are valid. `ticket-triage` compares only the JSON fields `category` and `priority`, labels that must be in the task's vocabulary (an answer without them, or with another label, is never served by the student or stored); `default` compares whole answers: canonical JSON when both are JSON, else the normalised text. Pinned tasks (`research-plan`, `research-write`) skip the student and always go to the teacher; their answers are not stored as training examples. The full list is in [CONFIGURATION.md](CONFIGURATION.md#tasks).

#### Generation parameters

Each model call gets the client's parameters as its role allows ([ADR 0017](adr/0017-generation-parameters-by-role.md)). The allowlists and caps are environment variables ([CONFIGURATION.md](CONFIGURATION.md#generation-parameters-and-token-caps)); the table shows the defaults.

| Field | Teacher, serving the answer (escalation, cold start, pinned, teacher-only) | Student (its samples, and a shadow's) | Teacher audit and arbitration |
|---|---|---|---|
| `max_tokens` / `max_completion_tokens` | The client's + `RATCHET_TEACHER_REASONING_HEADROOM` (0), at most `RATCHET_TEACHER_MAX_TOKENS` (8,192); the cap when not sent | The client's, at most `RATCHET_STUDENT_MAX_TOKENS` (1,024); the cap when not sent | As when serving |
| `temperature` | The client's; 0 when not sent (0.7 when `n` > 1, so the choices differ) | 0.7, always | 0 |
| `n` | The client's `n` (1 by default) | `RATCHET_SAMPLES` (3), or the client's `n` when larger | 1 |
| `logprobs` | The client's (sent only when it asks) | On, always | Off |
| `top_logprobs` | The client's | The client's | No |
| `stop`, `response_format` | The client's | The client's | The client's |
| `tools`, `tool_choice`, `parallel_tool_calls` | The client's (only a tool request has them, and the teacher answers it alone: [Tools and function calling](#tools-and-function-calling)) | No | No audit or arbitration runs on a tool request |
| `seed`, `logit_bias` | The client's | The client's | No |
| `top_p`, `presence_penalty`, `frequency_penalty` | The client's | No | No |
| `reasoning_effort` | The client's | No | The client's |
| `model` | `RATCHET_TEACHER_MODEL`, always (invariant 1) | The version's adapter | `RATCHET_TEACHER_MODEL` |
| an unknown field | Only when `RATCHET_TEACHER_PARAMS` names it | Only when `RATCHET_STUDENT_PARAMS` names it | As when serving |

- The student's temperature and logprobs never change, and its confidence is always scored over `RATCHET_SAMPLES` samples: its threshold was calibrated that way. A request for more choices draws more samples, only to fill them ([Choices and logprobs](#choices-and-logprobs)). A sample cut off by its token limit gets no vote, so a tight `max_tokens` can send a request to the teacher.
- `max_tokens` above a cap is capped, not refused.
- When the reasoning headroom gives the teacher more than the client asked for, an answer whose visible part (reasoning removed) is longer than the client's `max_tokens` (about 4 characters a token) comes back whole with `finish_reason: "length"`, and is not stored.
- `stop`, `response_format` (without its `x-ratchet` annotations), `tools` and `tool_choice` are stored with the example, redacted like the messages: the same messages under other conditions are another example (in the same golden or train split), and the nightly golden eval samples the student under them. Temperature, seed, penalties and `max_tokens` are not stored.
- A teacher answer written under a client's `logit_bias`, a non-zero `presence_penalty` or `frequency_penalty`, a `temperature` above 1, or a vendor field is returned as usual but never stored: a client may choose the answer it gets, not the label the student learns.
- `tools`, `tool_choice` and `parallel_tool_calls` reach only the teacher, which answers a tool request alone ([Tools and function calling](#tools-and-function-calling)). A teacher that answers a request **without** tools with a tool call is a **502** `tool_calls_unsupported` (with `X-Should-Retry: false`); a response never has `finish_reason: "tool_calls"` without `message.tool_calls`.

#### Structured outputs

A `response_format` is a contract on every answer of its request (FR-1.40, FR-14.2, [ADR 0042](adr/0042-auto-tasks-from-response-format-schemas.md)):

| `response_format` | An answer meets it when |
|---|---|
| `{"type": "json_object"}` | It is one JSON object |
| `{"type": "json_schema", "json_schema": {"name": ..., "schema": {...}, "strict": true}}` | It is JSON that matches the schema. Checked: `type` (null included), `properties`, `required`, `additionalProperties`, `enum`, `const`, `items`, `prefixItems`, `anyOf`, `oneOf`, `allOf`, local `$ref`, `minItems`/`maxItems`, `minLength`/`maxLength` and the numeric bounds. Other keywords (`format`, `pattern`, ...) are not checked; `additionalProperties: false` is not enforced next to `patternProperties`. A check that cannot finish within its step budget counts as a mismatch |
| `{"type": "json_schema"}` without a `schema` | It is JSON |

- **Served content** is the JSON the answer holds, as the model wrote it: reasoning, a code fence and text around the JSON are removed, nothing is re-serialised. So `json.loads(message.content)` works on every valid answer, and the OpenAI SDK's `client.chat.completions.parse(response_format=SomeModel)` gets bare JSON. A cut-off answer comes back as written with `finish_reason: "length"`, so `.parse()` raises `LengthFinishReasonError`, not a validation error.
- **The student**: a sample that breaks the contract gets no vote; when none meets it, its confidence is 0 and the teacher answers.
- **The teacher**: a complete answer that breaks the contract gets one repair (`RATCHET_SCHEMA_REPAIR=1`, the default): the same teacher is asked again with the same parameters, its own answer as an assistant turn, and a user turn `Your previous answer did not match the schema: <errors>. Reply with JSON only.` (the errors name schema paths and what they expect, never values from the answer). The repaired answer is served, unless it was cut off or is empty (the first answer, which ended by itself, is then kept); when valid, it is stored with the request's own messages, never the repair conversation. Both calls are in `cost_usd`, the unused answer under `usage_breakdown.repair`; `latency_ms` adds both calls, while `usage.prompt_tokens` and the baseline (what the teacher alone would have cost) count the request's own prompt, not the repair conversation. An answer still invalid (or not repaired: cut off, `n` > 1, the repair call failed) is served with `ratchet.schema_valid: false` and never stored, audited or used as a reference.
- **`ratchet.schema_valid`** says whether every choice meets the contract; it is `null` without a `response_format`.
- **A teacher that refuses json_schema** (a 400 whose `param` or message names `response_format` or `json_schema`) is asked again at once with `{"type": "json_object"}` and the schema appended to its leading system message; the answer is still checked against the schema, and the example keeps the client's format. That schema goes that way directly afterwards. A refusal may be about the client's own schema, so after 3 different refused schemas the endpoint is asked once with the smallest strict schema, and only when it refuses that too does every schema go that way (a `json_schema_fallback` event for each step, once per ledger); no client can turn json_schema off for the others. Any other 400 is the client's, as before.
- **Which task** ([CONFIGURATION.md](CONFIGURATION.md#structured-outputs)): an operator's task (built-in, tasks file, admin API) named by `X-Ratchet-Task` or `metadata.ratchet_task` wins, and the schema is only forwarded and checked. Otherwise a json_schema request gets an **auto task**: the unregistered name it gives (under `RATCHET_UNKNOWN_TASKS=distill`; a name starting with `auto-` counts as none), else `auto-<schema name as a slug>-<16 hex digits of SHA-256 over the canonical schema>`, the same for the same schema whatever its key order. Its fields come from the schema: enums, booleans, numbers, dates and identifier strings (`email`, `uuid`, `uri`, `ipv4`, `hostname`) are compared, free text is not. An auto task serves only a schema that derives the same fields: a request that names one with another schema gets its own schema's auto task, so no client can choose the fields another client's answers are compared on. `ratchet.task` names the task a request got. A schema with nothing comparable, or one past `RATCHET_MAX_AUTO_TASKS`, gets an unregistered task of its auto name and follows `RATCHET_UNKNOWN_TASKS` (under `reject`: 400 `unknown_task` with `param` `response_format`). `json_object`, and every schema with `RATCHET_MAX_AUTO_TASKS=0`, keeps the named task, or `default` (canonical JSON compared).
- **`x-ratchet` annotations**: a property may carry `"x-ratchet": {"compare": "text" | "ignore" | "ordered", "abs_tol": 0.01}` to include a free-text field, leave a field out, compare an array in order, or give a number a tolerance. Every key starting with `x-ratchet` is stripped before any model sees the schema and before an example stores it.
- **Redaction** still applies to stored answers ([ADR 0009](adr/0009-pattern-based-pii-redaction.md)). A placeholder replaces an enum value only when the value itself looks like personal data (an email address or a card number, say); such an answer no longer matches its schema after redaction, so it is served but not stored (`invalid after redaction`). A schema whose enum holds such values never becomes an auto task: the ledger's `tasks` table stores rules, never personal data.

#### Tools and function calling

Tool calls pass through to the teacher, and nothing of a tool request is stored (FR-1.41, [ADR 0043](adr/0043-tool-calls-teacher-only-by-default.md)).

- **A tool request** is one with a non-empty `tools` or `functions`, or a message with `tool_calls` or `function_call`, or a message whose role is `tool` or `function` (the next turn of a tool loop, with or without `tools`). `tool_choice: "none"` with tools is still one. An empty `tools` list is not, and `tool_choice` or `parallel_tool_calls` with nothing to call are not sent.
- **The teacher answers it alone**: it gets `tools`, `tool_choice` and `parallel_tool_calls` as sent (under `RATCHET_TEACHER_MODEL`, whatever `model` says); the student, audits and the shadow are never called, and the answer is never stored as a training example, whatever the task (a task's `tools: distill` waits for tool-call distillation). The request is logged with `served_by: teacher` and route `tools` (a pinned task, or an unregistered one under `RATCHET_UNKNOWN_TASKS=teacher`, keeps its own route), counted in `/ratchet/stats` `tasks[].tool_requests`, and `cost_usd` is the teacher's price. When the primary teacher is unavailable, the fallback teacher (`RATCHET_TEACHER_FALLBACK_MODEL`) may answer; a teacher error is mapped as on any teacher path ([Errors](#errors)).
- **The response** carries the calls as the teacher made them: `message.tool_calls` = `[{"id", "type": "function", "function": {"name", "arguments"}}]` with the teacher's ids (a missing one becomes `call_` and 24 hex digits), `arguments` always a JSON string, `message.content` `null` (or the text the model wrote beside the calls) and `finish_reason: "tool_calls"`. A call cut off by a token limit keeps `finish_reason: "length"`. A teacher that answers in prose gets `finish_reason: "stop"` as usual. A tool call the client could not use (not a function, no name, text that is not valid Unicode) makes the response a **502** `teacher_unavailable` (`teacher unavailable: upstream 502 bad_upstream_response`), since serving the other calls would hand the client part of the answer.
- **The legacy fields**: `functions` are sent as `tools` (after the request's own `tools`, a name already there left out) and `function_call` (`"auto"`, `"none"` or `{"name"}`) as `tool_choice`; a legacy history is sent as tool turns (an assistant `function_call` becomes a `tool_calls` entry with a stable id, the `function` message that answers it a `tool` message), since most open-model chat templates know no `function` role. A request that used only the legacy fields gets `message.function_call` = `{"name", "arguments"}` and `finish_reason: "function_call"`; it asks the teacher for one call (`parallel_tool_calls: false` unless set), and if the teacher makes several, the first is returned. A request that also sends `tools` or `tool_choice` gets the current form.
- **`response_format`** applies to the final answer's content, not to a tool call: a call is never repaired, and `ratchet.schema_valid` is `null` when every choice is a call.
- **Streaming**: the role chunk (its `content` `null` when the choice is only calls), any content, then one chunk per call with the whole call, then the finish chunk ([Streaming](#streaming)).

```python
resp = client.chat.completions.create(model="ratchet", messages=msgs, tools=tools)
message = resp.choices[0].message
if message.tool_calls:
    msgs.append(message.model_dump(exclude_none=True))
    for call in message.tool_calls:
        msgs.append({"role": "tool", "tool_call_id": call.id, "content": run(call.function.name, call.function.arguments)})
    final = client.chat.completions.create(model="ratchet", messages=msgs, tools=tools)  # teacher-only too: it has tool turns
```

#### Request validation

Every rule runs before the quota check and before any model call; a refused request is not logged and costs nothing. Each 400 names the first problem: `param` is a JSON path to the field (`messages[2].content`, `response_format.json_schema.name`) and `code` says what is wrong (`invalid_type`, `invalid_value`, `missing_required_parameter`, `unsupported_value`).

| Field | Rule | `code` when broken |
|---|---|---|
| (body) | At most `RATCHET_MAX_BODY_BYTES` (2 MiB) | 413 `request_too_large` |
| (body) | Strict JSON (no `NaN` or `Infinity`), and a JSON object | `invalid_json`; `invalid_type` |
| any string | Valid Unicode: no unpaired UTF-16 surrogate (`"\ud83d"` alone, as code that cuts an emoji in half sends it), escaped or as bytes; `param` names where it is | `invalid_json` |
| `messages` | A non-empty array of at most `RATCHET_MAX_MESSAGES` (256) objects | `missing_required_parameter`, `invalid_type`, `invalid_value` |
| `messages[i].role` | `system`, `developer`, `user`, `assistant`, `tool` or `function` | `invalid_value` |
| `messages[i].content` | A string, a non-empty array of content parts each with a string `type` (`text` parts need a string `text`, `refusal` parts a string `refusal`), or `null` only in an assistant message with `tool_calls` or `function_call` | `invalid_type`, `missing_required_parameter` |
| `messages[i].content[j].type` | `text`, `refusal` (assistant only) or a type in `RATCHET_TEACHER_MODALITIES` | `unsupported_content_type` |
| `messages[i].tool_call_id` | Required, a string, when the role is `tool` | `missing_required_parameter` |
| `messages[i].name`, `.refusal` | Strings (`refusal`: assistant only) | `invalid_type` |
| `messages[i].tool_calls[k]` | In an assistant message: `{"id": string, "type": "function", "function": {"name": string, "arguments": string}}`; other call types are `unsupported_value` | `invalid_type`, `missing_required_parameter`, `unsupported_value` |
| `messages[i].function_call` | In an assistant message: `{"name": string, "arguments": string}` | `invalid_type`, `missing_required_parameter` |
| all message text | At most `RATCHET_MAX_INPUT_CHARS` (120,000) characters: content strings and text and refusal parts, `name`, `refusal`, `tool_call_id`, and each tool call's id, function name and arguments, added up (`param`: `messages`). Non-text parts are bounded by `RATCHET_MAX_BODY_BYTES` only | `context_length_exceeded` |
| `temperature` / `top_p` | A number from 0 to 2 / from 0 to 1 | `invalid_type`, `invalid_value` |
| `n` | An integer from 1 to `RATCHET_MAX_N` (4): below 1 is `invalid_value`, above `unsupported_value` | `invalid_type`, `invalid_value`, `unsupported_value` |
| `max_tokens`, `max_completion_tokens` | An integer of at least 1 | `invalid_type`, `invalid_value` |
| `stop` | A string or an array of at most 4 strings | `invalid_type`, `invalid_value` |
| `presence_penalty`, `frequency_penalty` | A number from -2 to 2 | `invalid_type`, `invalid_value` |
| `seed` | An integer | `invalid_type` |
| `logprobs` / `top_logprobs` | A boolean / an integer from 0 to 20, only with `logprobs: true` | `invalid_type`, `invalid_value` |
| `logit_bias` | An object of integers from -100 to 100 | `invalid_type`, `invalid_value` |
| `metadata` | An object of at most 16 string values; keys up to 64 characters, values up to 512; `ratchet_task` 1 to 64 letters, digits, `_`, `.` or `-`; `ratchet_store` `true`, `false`, `1` or `0` (any case) | `invalid_type`, `invalid_value` |
| `user`, `safety_identifier` | Strings, of any length; the message never quotes the value | `invalid_type` |
| `X-Ratchet-Store` (header) | `none`, `0`, `false`, `1` or `true`, in any case, or empty | `invalid_value` |
| `response_format` | `type` is `text`, `json_object` or `json_schema`. `json_schema` needs a `name` matching `^[A-Za-z0-9_-]{1,64}$`; its `schema`, if sent, must be an object of at most 64 KB and 16 levels | `missing_required_parameter`, `invalid_type`, `invalid_value` |
| `tools` | At most 128 entries of `{"type": "function", "function": {"name": ...}}` (name as above), at most 64 KB in all. Other tool types are `unsupported_value` | `invalid_type`, `invalid_value`, `unsupported_value` |
| `tool_choice` | `none`, `auto`, `required` or `{"type": "function", "function": {"name": ...}}` | `invalid_type`, `invalid_value` |
| `functions`, `function_call` | The legacy forms: functions named as above; `none`, `auto` or `{"name": ...}` | `invalid_type`, `invalid_value` |
| `stream`, `parallel_tool_calls`, `store` | Booleans | `invalid_type` |
| `model`, `reasoning_effort` / `stream_options` | Strings / an object | `invalid_type` |
| `stream_options.include_usage` | A boolean | `invalid_type` |

### Response (200)

A standard `chat.completion`, with every field OpenAI's has, plus a `ratchet` block:

```json
{
  "id": "chatcmpl-3f1c0a9b8e7d6c5b4a3f2e1d0c9b8a79",
  "object": "chat.completion",
  "created": 1792400000,
  "model": "student-v5",
  "system_fingerprint": "fp_6c1e0f3a9b2d",
  "service_tier": "default",
  "choices": [
    {"index": 0,
     "message": {"role": "assistant",
                 "content": "{\"category\": \"billing\", \"priority\": \"medium\", \"reply\": \"Sorry about the billing trouble, I'm checking your charges now.\"}",
                 "refusal": null, "annotations": []},
     "logprobs": null,
     "finish_reason": "stop"}
  ],
  "usage": {
    "prompt_tokens": 104, "completion_tokens": 31, "total_tokens": 135,
    "prompt_tokens_details": {"cached_tokens": 0, "audio_tokens": 0},
    "completion_tokens_details": {"reasoning_tokens": 0, "audio_tokens": 0,
                                  "accepted_prediction_tokens": 0, "rejected_prediction_tokens": 0}
  },
  "ratchet": {
    "served_by": "student",
    "student_version": 5,
    "confidence": 0.951,
    "escalated": false,
    "cost_usd": 0.0000312,
    "all_teacher_cost_usd": 0.00131,
    "latency_ms": 311.4,
    "usage_breakdown": {
      "student_samples": {"in": 104, "out": 93},
      "served": {"in": 104, "out": 31},
      "audit": null, "shadow": null, "arbitration": null, "repair": null
    },
    "degraded": null,
    "stored": false,
    "store_opt_out": null,
    "request_id": "req_3f1c0a9b8e7d6c5b4a3f2e1d0c9b8a79",
    "row_id": 4512,
    "task": "ticket-triage",
    "task_source": "header",
    "schema_valid": null,
    "requested_model": "ratchet"
  }
}
```

| `ratchet` field | Meaning |
|---|---|
| `served_by` | `student` or `teacher` |
| `student_version` | Version that answered; `null` when the teacher answered (including escalations) |
| `confidence` | Student confidence if a student was tried, else `null` |
| `escalated` | `true` if the student was tried and was below its threshold |
| `cost_usd` | Actual cost of this request, including audit, shadow and arbitration calls (a call that failed adds nothing). With `overhead_pending`, only what serving the answer cost ([Off-path checks](#off-path-checks)) |
| `all_teacher_cost_usd` | Estimated cost if the teacher had answered alone (with `overhead_pending` and an audit drawn, the token formula's estimate; the ledger row gets the audit's own price when it ends) |
| `overhead_pending` | Present, and `true`, only when this request's audit or shadow runs after the response ([Off-path checks](#off-path-checks)); absent otherwise |
| `latency_ms` | Student latency plus teacher latency when the teacher answered |
| `degraded` | `null`, or `"teacher_unavailable"` when the teacher (and the fallback teacher, if set) could not answer an escalated request and, with `RATCHET_ON_TEACHER_FAILURE=serve_student`, the student's own answer was served although it was below the version's threshold ([ADR 0019](adr/0019-teacher-breaker-degraded-mode-and-fallback-teacher.md)). Such an answer is never stored, audited or shadowed; `served_by` is `student` and `escalated` is `true` |
| `stored` | `true` when a teacher answer of this request was stored as a training example, or matched one already stored (its repeat count went up); `false` otherwise, including every student answer that was not audited |
| `store_opt_out` | `null`, or why this request's teacher answers could not be stored: `global` (`RATCHET_STORE_EXAMPLES=0`), `task` (a no-store task), `key` (the API key has `store=0`), `request` (`store: false`, `X-Ratchet-Store` or `metadata.ratchet_store`) or `end_user` (a no-train end user) ([End users and storage](#end-users-and-storage)) |
| `request_id` | The request's id, `req_` and 32 hex digits; the response's `id` is `chatcmpl-` and the same digits. It is logged with the request's row (never its text) and names the request in [`POST /v1/ratchet/feedback`](#feedback) |
| `row_id` | The request's row in the ledger (also the `X-Ratchet-Request-Id` header): what its day's savings receipt proves it by (`GET /ratchet/receipts/{day}/proof/{request_id}`, [Savings receipts](#savings-receipts)). Row ids are global and sequential, unlike `request_id`, which is random: feedback names a request by `request_id`, a receipt proof by `row_id` |
| `request_id` | The request's id, as in `x-request-id`; `id` is `chatcmpl-` and the same 32 hex digits ([Request ids](#request-ids)) |
| `task` | The task the request was answered as ([Task resolution](#task-resolution)); also the `X-Ratchet-Task` response header |
| `task_source` | The rule that chose it: `header`, `metadata`, `deployment`, `model`, `alias`, `match`, `auto_system` or `default` |
| `requested_model` | The request's `model` as sent (`null` when it sent none); `model` above is the model that served |
| `usage_breakdown` | Every model call of the request, as `{"in": prompt tokens, "out": completion tokens}` as that model reported them, or `null` for a call that was not made or failed: `student_samples` (one call for all n samples: the prompt once, every sample's output), `served` (the served answer: one of the student's samples, or the teacher's own call; every choice, for `n` > 1), `audit`, `shadow` (the candidate's samples), `arbitration` and `repair` (the teacher answers a schema repair left unused, added up: the first answer, which broke the `response_format`, or a repair answer that was cut off or empty; the answer used is the `served`, `audit` or `arbitration` one) ([Usage](#usage)) |
| `task` | The task the request was resolved to: the registered task it named, an auto task from its `response_format` schema, an unregistered name, or `default` ([Structured outputs](#structured-outputs)) |
| `schema_valid` | `true` when every choice meets the request's `response_format`, `false` when one does not (served anyway, never stored), `null` without a `response_format` |

A request that sent `metadata` gets it back as a top-level `metadata` field, as OpenAI echoes it (in a stream, on the chunk that carries the `ratchet` block). It is never stored.

#### Off-path checks

With `RATCHET_SIDECAR_WORKERS` above 0 (live mode's default, 4), the response does not wait for the teacher's audit of a served student answer, for the shadow candidate's samples or for the teacher's arbitration: the gateway answers as soon as the served answer is ready, and a bounded queue runs them afterwards ([ADR 0072](adr/0072-off-path-audits-and-shadow.md)). The same requests are audited (the draw is made on the request path), the same calls are made in the same order, and the finished ledger row is the one an inline run writes. Such a response carries `"overhead_pending": true` in the `ratchet` block; then `cost_usd` and the cost split are what serving cost, `usage_breakdown.audit`, `.shadow` and `.arbitration` are `null`, and the request's ledger row (`GET /v1/ratchet/requests/{id}`) gets the rest when the checks end: `audited` is 1 with `audit_agree` null while the audit is pending, and `shadow_version` is set with `shadow_serve` null while the shadow is. The release gate counts neither until they end. When the queue is full (its places, or the 64 MiB of request text its jobs may hold), a drawn audit runs before the response instead (never dropped) and the response has no `overhead_pending`; a shadow that finds no place does not run for that request. Mock mode (0 workers by default) keeps every check before the response, as before.

`model` is the model that produced the served answer: the adapter name (`student-vN`), the teacher model ID, or the fallback teacher's (`RATCHET_TEACHER_FALLBACK_MODEL`) when it answered for an unavailable teacher.

`system_fingerprint` names the configuration that produced the answer: `fp_` and the first 12 hex digits of SHA-256 over `student|<adapter>|<version id>|<training-set hash>|<threshold>` for a student answer, or `teacher|<RATCHET_TEACHER_MODEL>` for a teacher answer (`teacher|<RATCHET_TEACHER_FALLBACK_MODEL>` for the fallback teacher's). A degraded answer carries its version's student fingerprint. `<threshold>` is the one the answer was held to: since FR-14.1 each task of a version can have its own, so the fingerprint is the same for every answer of one version on one task, and changes when a promotion or a rollback puts another version in front (during a canary, both versions' fingerprints appear) or when that threshold changes. `service_tier` is always `default`. `choices[i].logprobs` is `null` unless the request sent `logprobs: true` ([Choices and logprobs](#choices-and-logprobs)), `message.refusal` is `null` and `message.annotations` is empty.

`message.content` is the served answer after `tasks.clean_answer`: model reasoning is never returned (a `<think>…</think>` block, anything before a lone `</think>`, an unterminated `<think>` that opens what is left and what follows), and for a structured task such as `ticket-triage` a code fence around the whole JSON answer is removed, so the content is bare JSON. A structured answer that is one JSON object is returned whole, so a tag quoted in its strings stays; a `<think>` after answer text is content too. A free-text answer keeps its code blocks. With a `response_format` the content is the JSON the answer holds ([Structured outputs](#structured-outputs)). A teacher answer that was cut off by its token limit is still returned (and is not stored as training data), with `finish_reason: "length"`.

`finish_reason` is the served answer's: `stop`, `length` (cut off by `max_tokens` or a cap, or over the client's `max_tokens` with the reasoning headroom), `content_filter`, or `tool_calls` (`function_call` for a legacy request) when the message has tool calls ([Tools and function calling](#tools-and-function-calling)); any other value an upstream model reports is returned as `stop`, and `tool_calls` is never sent without `message.tool_calls`. A student answer is always one that ended by itself.

Response headers: `X-Ratchet-Served-By: student|teacher`, `X-Ratchet-Request-Id` (the ledger row, as `ratchet.row_id`), `X-Ratchet-Task: <task>`, `X-Ratchet-Degraded: teacher_unavailable` on a degraded answer (and only then; streamed responses too), `X-Ratchet-Model-Alias: <model>` when `RATCHET_MODEL_POLICY=lenient` served a `model` Ratchet does not know as `ratchet` (UTF-8 percent-encoded except for visible ASCII, so spaces and `%` are encoded too and one percent-decoding gives the id back; at most 256 characters), plus `X-RateLimit-Limit-Requests` and `X-RateLimit-Remaining-Requests` when a daily quota is set, `Idempotent-Replayed: true` on an answer sent again for an `Idempotency-Key` ([Idempotency and retries](#idempotency-and-retries)), and on every response `x-request-id` and `openai-processing-ms` ([Request ids](#request-ids)).

#### Request ids

Every request gets an id from the gateway, `req_` and 32 hex digits (a UUID4), returned as `x-request-id` on every response, errors and streams included, beside `openai-processing-ms` (the gateway's time until the response started, in whole ms), the two headers OpenAI's API sends. OpenAI's SDKs read the header as `response._request_id` and `error.request_id`. The same id is `ratchet.request_id`, `request_id` in every error envelope on `/v1/*`, the ledger's `requests.request_id` and `failures.request_id`, and the request's log line; the completion's `id` is `chatcmpl-` and the same digits. Look a request up with `GET /v1/ratchet/requests/{id}` (an API client, its own requests) or `GET /ratchet/requests/{id}` (the dashboard API); either takes the `req_` id or the `chatcmpl-` id.

Your own id: send `X-Request-Id` (or `X-Client-Request-Id`, which OpenAI's API also takes) with 1 to 128 letters, digits, `.`, `_` or `-`, and it is stored as the request's `client_request_id` and logged beside the gateway's id. Any other value is ignored, never an error. The gateway's id is always its own; yours never replaces it.

Tracing: a valid W3C `traceparent` (version `00`, lower-case hex, no all-zero ids) is accepted, its trace id is logged with the request, and every call to the teacher and the student (each HTTP attempt) carries it with a new span id and the same flags, with `tracestate` passed on when it is printable ASCII of at most 512 characters. A malformed `traceparent` is ignored. A request without one starts no trace ([ADR 0062](adr/0062-request-ids-failures-logs-and-metrics.md)).

#### Usage

`usage` is the served answer's, as if the model that wrote it had answered the request alone ([ADR 0018](adr/0018-usage-reports-the-served-answer.md)):

| Field | Student answer | Teacher answer |
|---|---|---|
| `prompt_tokens` | The student's prompt tokens | The teacher's prompt tokens |
| `completion_tokens` | The chosen sample's tokens (one of the samples; the endpoint's `completion_tokens` split over them by their logprob counts); with `n` > 1, every returned choice's, added up | The teacher's completion tokens, reasoning included, as OpenAI counts them for its reasoning models (every choice's, for `n` > 1) |
| `total_tokens` | `prompt_tokens + completion_tokens` | The same |
| `completion_tokens_details.reasoning_tokens` | The sample's share of the endpoint's reasoning tokens (0 for the tuned student), added up over the choices | What the teacher reported (Token Factory reports them; the mock teacher reports `RATCHET_TEACHER_REASONING_TOKENS`) |
| `prompt_tokens_details.cached_tokens` | What the endpoint reported in `prompt_tokens_details.cached_tokens`, else 0 | The same |
| `audio_tokens`, `accepted_prediction_tokens`, `rejected_prediction_tokens` | 0 | 0 |

When the served model reported no prompt or completion count, Ratchet estimates it at about 4 characters a token (the messages as JSON; the answer as served), so `total_tokens` is never 0. Reasoning and cached tokens never exceed the counts they are part of.

`usage` is **not** what the request cost Ratchet. The other student samples, an audit, a shadow, an arbitration and a schema repair's unused answer are listed in `ratchet.usage_breakdown`, and `ratchet.cost_usd` prices all of them. After a repair, `prompt_tokens` and `cached_tokens` are the request's own prompt (the first call's), not the repair call's, whose prompt also held the rejected answer and the repair note ([Structured outputs](#structured-outputs)). So the same answer reports the same `usage` whether or not it was audited or shadowed, and a client that bills its own users from `usage` charges them for the answer they got.

#### Choices and logprobs

`n` asks for up to `RATCHET_MAX_N` (4) choices; `choices[i].index` numbers them from 0 ([ADR 0020](adr/0020-n-choices-and-token-logprobs.md)).

- **The student** draws `max(RATCHET_SAMPLES, n)` samples in one call. Its confidence is scored on the first `RATCHET_SAMPLES` of them, exactly as for one choice, and it serves only when that confidence reaches the version's threshold **and** at least `n` samples give the majority answer. Then the choices are those samples: choice 0 is the one a request for one choice would get, the others follow by token probability. So all choices of a student answer agree on the task's label fields (`category` and `priority` for `ticket-triage`), and a request for more choices escalates more often than one for a single choice.
- **Otherwise the teacher** writes `n` choices, at temperature 0.7 when the request sets none (at its default of 0 every choice would be the same). The route is `n_choices` when the student was confident but too few of its samples agreed, `confidence` when it was unsure. A teacher endpoint that returns fewer choices than asked gives fewer; Ratchet never pads them with copies.
- **Only choice 0 is ever stored** as a training example, so one request adds or bumps at most one example. Audits, shadow comparisons and arbitrations judge choice 0.
- `usage` adds up every choice's completion (the prompt once), `usage_breakdown.served` lists the same choices, and `cost_usd` prices every call. `message.content` is cleaned per choice as above, and `finish_reason` is each choice's own.

With `"logprobs": true`, each choice has `logprobs: {"content": [...], "refusal": null}`, in OpenAI's format: one entry per token, `{"token", "logprob", "bytes", "top_logprobs"}`, with at most `top_logprobs` (0 to 20; none when not sent) alternatives per token. Without `logprobs`, or with `false`, `logprobs` is `null` as before.

```json
"logprobs": {"content": [
  {"token": "{\"category\"", "logprob": -0.012, "bytes": [123, 34, 99, 97, 116, 101, 103, 111, 114, 121, 34],
   "top_logprobs": [{"token": "{\"category\"", "logprob": -0.012, "bytes": [123, 34, 99, 97, 116, 101, 103, 111, 114, 121, 34]}]},
  …
], "refusal": null}
```

- The tokens always spell `message.content` exactly. When reasoning (`<think>…</think>`), a fence around a structured answer, or whitespace at its edges was removed from the content, their tokens are removed too, and a token is cut only where the part removed is whitespace. Tokens a server lists for reasoning that its content already leaves out are removed the same way, so reasoning never reaches a client through logprobs.
- `content` is `null` when the logprobs cannot be given: the model returned none (an operator can leave `logprobs` out of `RATCHET_TEACHER_PARAMS` for a teacher that refuses it), an entry was malformed, or the answer cannot be told apart from what was removed (a token holding both the end of the reasoning and the start of the answer, reasoning removed from the middle of an answer, or a character split over tokens that do not decode alone). Treat `null` as possible whenever you ask for logprobs.
- A student answer has them whenever its endpoint returns them (the student is sampled with logprobs anyway); a teacher answer has them when the teacher returns them, and the teacher is asked for `logprobs` and `top_logprobs` only when the request sends them. A logprob of `-9999.0` stands for minus infinity, as vLLM reports it (JSON has no `-Infinity`). The mock student returns tokens of about 4 characters with one logprob per sample; the mock teacher returns none (`content: null`).
- Logprobs are the served model's own: a student choice's are the student's, a teacher choice's the teacher's. `exp` of a student choice's mean token logprob is the token probability its confidence score used.

### Streaming

With `"stream": true` the response is `text/event-stream` in the format OpenAI SDKs expect: a first chunk with `delta.role`, content chunks of a few words each, a last chunk with the answer's `finish_reason` (`"stop"`, or `"length"` as above) and the same `ratchet` block as above, then `data: [DONE]`. Every chunk has the response's `id`, `created`, `model`, `system_fingerprint` and `service_tier`, and `logprobs: null` in its choice. With `n` > 1 the choices follow one another, each with its own `index`, role chunk, content chunks and finish chunk; the `ratchet` block rides on the last finish chunk. With `logprobs: true`, each content chunk carries a few tokens and their `logprobs: {"content": [...], "refusal": null}` (so the chunks' logprobs add up to the non-streamed response's; `content: null` when they cannot be given), and the role and finish chunks keep `logprobs: null`.

```text
data: {"id":"chatcmpl-…","object":"chat.completion.chunk","created":1792400000,"model":"student-v5","system_fingerprint":"fp_6c1e0f3a9b2d","service_tier":"default","choices":[{"index":0,"delta":{"role":"assistant","content":"","refusal":null},"logprobs":null,"finish_reason":null}]}

data: {"id":"chatcmpl-…","object":"chat.completion.chunk",…,"choices":[{"index":0,"delta":{"content":"{\"category\": \"billing\", "},"logprobs":null,"finish_reason":null}]}

data: {"id":"chatcmpl-…","object":"chat.completion.chunk",…,"choices":[{"index":0,"delta":{},"logprobs":null,"finish_reason":"stop"}],"ratchet":{"served_by":"student",…}}

data: [DONE]
```

With `"stream_options": {"include_usage": true}`, as OpenAI documents it: every chunk above has `"usage": null`, the finish chunk has no `ratchet` block, and one more chunk comes before `[DONE]`, with no choices, the same `usage` as the non-streamed response and the `ratchet` block:

```text
data: {"id":"chatcmpl-…","object":"chat.completion.chunk",…,"choices":[{"index":0,"delta":{},"logprobs":null,"finish_reason":"stop"}],"usage":null}

data: {"id":"chatcmpl-…","object":"chat.completion.chunk",…,"choices":[],"usage":{"prompt_tokens":104,"completion_tokens":31,"total_tokens":135,…},"ratchet":{"served_by":"student",…}}

data: [DONE]
```

A choice with tool calls ([Tools and function calling](#tools-and-function-calling)) sends, after its role chunk (`"content": null` when it has no text) and any content chunks, one chunk per call with the whole call, then its finish chunk with `"tool_calls"`. This is what the OpenAI SDK's `ChatCompletionStreamState` (behind `client.chat.completions.stream`) and LangChain's chunk accumulators merge by `index`. A legacy request gets one `{"function_call": {"name", "arguments"}}` delta and `"function_call"`.

```text
data: {…,"choices":[{"index":0,"delta":{"role":"assistant","content":null,"refusal":null},"logprobs":null,"finish_reason":null}]}

data: {…,"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_paris01","type":"function","function":{"name":"get_weather","arguments":"{\"city\": \"Paris\"}"}}]},"logprobs":null,"finish_reason":null}]}

data: {…,"choices":[{"index":0,"delta":{},"logprobs":null,"finish_reason":"tool_calls"}],"ratchet":{"served_by":"teacher",…}}

data: [DONE]
```

The cascade has to see the student's whole answer to score its confidence before it decides who serves, so the answer is complete before the first chunk is sent: time to first token equals the full latency. Streaming exists so that clients built for streaming work unchanged. Errors (400, 401, 413, 429, 502, 504) are returned as normal JSON HTTP errors before the stream starts.

```python
for chunk in client.chat.completions.create(model="ratchet", messages=msgs, stream=True, stream_options={"include_usage": True}):
    if chunk.choices:
        print(chunk.choices[0].delta.content or "", end="")
    else:
        print("\n", chunk.usage.total_tokens, "tokens")
```

### Errors

Every error on `/v1/*` (and `/openai/*`) has OpenAI's envelope, which OpenAI SDKs turn into typed exceptions (`openai.BadRequestError`, `AuthenticationError`, `NotFoundError`, `RateLimitError`, `InternalServerError`, …) with `.code`, `.param` and `.type`:

```json
{
  "error": {"message": "Invalid value for 'temperature': expected a number from 0 to 2, but got 3 instead.",
            "type": "invalid_request_error", "param": "temperature", "code": "invalid_value"},
  "detail": "Invalid value for 'temperature': expected a number from 0 to 2, but got 3 instead.",
  "request_id": "req_0b6f3a7c2d1e4f5a8b9c0d1e2f3a4b5c"
}
```

`detail` repeats `error.message` for one release (v0.5), for clients written against earlier versions, then goes away: read `error`. `request_id` (since v0.5) is the request's id, also in the `x-request-id` header; `error` keeps exactly OpenAI's four fields. Routes outside `/v1` (`/ratchet/*`, Scout, pages) keep FastAPI's `{"detail": ...}`, with the id in `x-request-id`.

| Status | `type` | `code` | When | Retried by OpenAI SDKs? |
|---|---|---|---|---|
| 400 | `invalid_request_error` | `invalid_json`, `invalid_type`, `invalid_value`, `missing_required_parameter`, `unsupported_value`, `context_length_exceeded`, `unsupported_content_type` | The request breaks a rule in [Request validation](#request-validation); `param` names the field | No |
| 400 | `invalid_request_error` | `null` | Mock mode with `RATCHET_MOCK_STRICT=1` only: the request is not a synthetic demo ticket or a Scout request from the mock research world (`mock mode only answers demo requests: '...'`). Without it the generic mock world answers every request ([Mock mode](#mock-mode-answers)) | No |
| 400 | `invalid_request_error` | `reserved_task` | The request names the task `embeddings` (header, `metadata.ratchet_task`, `model` or Azure deployment), the name `/v1/embeddings` logs its requests under ([Embeddings](#post-v1embeddings)); `param` says where it came from. Checked before the quota | No |
| 400 | `invalid_request_error` | `unknown_task` | `RATCHET_UNKNOWN_TASKS=reject` and the task is not registered (`unknown task "x"; registered: default, page-read, ...`, at most 20 names, only tasks this key may use); `param` is `metadata.ratchet_task` when the name came from there, and `response_format` for a json_schema request that named no task and whose schema gave no auto task ([Structured outputs](#structured-outputs)) | No |
| 401 | `invalid_request_error` | `invalid_api_key` | API keys are on and the key is missing or wrong (`WWW-Authenticate: Bearer`) | No |
| 403 | `permission_error` | `task_not_allowed` | The task's `clients` do not name this key, or the request has no key, or the key's `tasks` scope does not list the task (`task not allowed for this key`). Checked before the quota, so it uses none | No |
| 404 | `not_found_error` | `unknown_url` | No such route: `Unknown request URL: POST /v1/foo.` | No |
| 404 | `not_found_error` | `unsupported_endpoint` | An OpenAI endpoint this gateway knows but does not serve: `POST /v1/moderations`, a stored response (`/v1/responses/{id}`), or `/v1/embeddings` under `RATCHET_EMBEDDINGS_BASE=off`. The message says why and links [OpenAI endpoints](#openai-endpoints). Checked after the key | No |
| 400 | `invalid_request_error` | `unsupported_parameter` | A field the gateway understands but cannot honour, so serving without it would answer another question: `suffix`, `echo` or `best_of` on `/v1/completions`; `previous_response_id`, `conversation`, `prompt`, `background`, `stream` or a built-in tool on `/v1/responses`. `param` names it | No |
| 404 | `invalid_request_error` | `model_not_found` | `RATCHET_MODEL_POLICY=strict` and the `model` is not `ratchet`, `ratchet/<task>` or an alias (`The model 'gpt-4o' does not exist.`, `param` `model`); under either policy, `ratchet/` with a name that is not a task name; on the Azure-style route, a deployment that is not a task name (`param` null). Checked before the quota, so it uses none and nothing is logged | No |
| 405 | `invalid_request_error` | `null` | A route called with the wrong method (`Allow` names the right one) | No |
| 413 | `invalid_request_error` | `request_too_large` | The body is larger than `RATCHET_MAX_BODY_BYTES` | No |
| 400 | `invalid_request_error` | `invalid_idempotency_key` | The `Idempotency-Key` header is empty, longer than 255 characters or not printable ASCII (`Invalid Idempotency-Key header: ...`); `X-Should-Retry: false` ([Idempotency and retries](#idempotency-and-retries)) | No |
| 400 | `invalid_request_error` | `idempotency_key_reused` | This API key used the `Idempotency-Key` for a different request (another body or `X-Ratchet-Task`) within `RATCHET_IDEMPOTENCY_TTL_S`; `X-Should-Retry: false` | No |
| 409 | `invalid_request_error` | `idempotency_key_in_use` | The first request with this `Idempotency-Key` was still running after `RATCHET_TEACHER_DEADLINE_S`, or four requests were already waiting for it; `Retry-After: 1`. The retry gets its answer once it finished | Yes, after `Retry-After` |
| 400 | `invalid_request_error` | the teacher's (`context_length_exceeded`, ...), `request_too_large` for an upstream 413 without one, or `null` | Live mode: the teacher refused the request with 400, 413 or 422, so it cannot succeed as sent (too long for the model, a content part it cannot read). `message` and `param` are the teacher's; `X-Should-Retry: false` | No |
| 429 | `insufficient_quota` | `daily_quota_exceeded` | The key's daily quota is used up (`daily request quota of N used up for 'acme'; resets at 00:00 UTC`); `Retry-After` (seconds to 00:00 UTC), `X-RateLimit-Reset-Requests` and `X-Should-Retry: false` | No: the response tells them not to |
| 429 | `insufficient_quota` | `budget_exceeded` | The key's daily or monthly US dollar budget is spent (`the daily budget of $5.00 is spent for 'acme'; it resets at 00:00 UTC`); `Retry-After` and `X-Ratchet-Budget-Reset` (until the cap that resets last resets), `X-Ratchet-Budget-Remaining-USD: 0.000000` and `X-Should-Retry: false` ([Limits and headers](#limits-and-headers)) | No: the response tells them not to |
| 429 | `insufficient_quota` | `gateway_budget_exceeded` | The gateway's daily budget (`RATCHET_DAILY_BUDGET_USD`) is spent: every chat request with `RATCHET_BUDGET_MODE=block`, only a request that needs the teacher with `student_only`; `Retry-After` (to 00:00 UTC) and `X-Should-Retry: false` | No: the response tells them not to |
| 429 | `rate_limit_error` | `rate_limit_exceeded` | The key's requests a minute or in flight, or on a gateway without keys the client address's requests a minute, are over their limit; `Retry-After`, and for a rate the `X-RateLimit-*-Requests` headers ([Limits and headers](#limits-and-headers)) | Yes, after `Retry-After` |
| 429 | `rate_limit_error` | `upstream_rate_limited` | Live mode: Token Factory rate-limited the teacher call, after the gateway's own retries (`teacher rate limited: upstream 429; retry after 7 s`). `Retry-After` is the teacher's wait, clamped to 1 to 60 s (1 when it sent none) | Yes, after `Retry-After` |
| 500 | `api_error` | `null` | A bug in the gateway (`The server had an error while processing your request.`). The message never includes request text; the ledger records a `gateway_error` event with the exception's class at the 1st, 10th, 100th, ... occurrence. In live mode this includes a `KeyError` from the cascade, which is a 400 only in strict mock mode (`RATCHET_MOCK_STRICT=1`) | Yes |
| 502 | `api_error` | `teacher_unavailable` | Live mode: a teacher call whose answer was needed failed after its retries: an escalation, a cold start, a pinned task or a teacher-only request (`teacher unavailable: upstream 503`, `teacher unavailable: upstream 502 upstream_unreachable` when Token Factory could not be reached, `... bad_upstream_response` when its answer could not be read). Student failures do not cause errors: they fall back to the teacher. Neither do a failed audit or shadow arbitration: the student's answer is served ([ADR 0013](adr/0013-optional-teacher-calls-fail-open.md)). An upstream redirect or 4xx outside the other rows (402, for example) adds `X-Should-Retry: false`. When Token Factory sent a wait (`Retry-After` on a 503, for example), it is passed on as `Retry-After`, clamped to 1 to 60 s. See [RUNBOOK.md](RUNBOOK.md#teacher-api-down-token-factory-errors-or-timeouts) | Yes (after `Retry-After` when present), unless `X-Should-Retry: false` |
| 502 | `api_error` | `upstream_auth_error` | Live mode: Token Factory refused the gateway's own key (401 or 403): `NEBIUS_API_KEY` is wrong or lacks access. `X-Should-Retry: false` | No |
| 502 | `api_error` | `upstream_not_found` | Live mode: Token Factory answered 404: `RATCHET_TEACHER_MODEL` or `RATCHET_TEACHER_BASE` is wrong. The request's `model` never selects the teacher, so this is the gateway's configuration, not the client's mistake. `X-Should-Retry: false` | No |
| 502 | `api_error` | `tool_calls_unsupported` | Live mode: the teacher answered a request that offers no tools with a tool call (`The teacher answered with a tool call to a request that offers no tools.`). A request with tools gets the calls back ([Tools and function calling](#tools-and-function-calling)). Nothing is stored. `X-Should-Retry: false` | No |
| 503 | `api_error` | `overloaded` | `RATCHET_MAX_INFLIGHT` chat requests are in flight (`the gateway has 32 chat requests in flight, its limit; retry in a second`): answered at once instead of queueing; `Retry-After: 1` | Yes, after `Retry-After` |
| 503 | `api_error` | `teacher_circuit_open` | Live mode: the teacher failed `RATCHET_TEACHER_BREAKER_FAILURES` (5) calls in a row, so its circuit breaker is open and the gateway does not call it until the next probe (`teacher circuit open: recent teacher calls failed, so the gateway is not calling it; retry after 18 s`). Answered at once, for any request that needs the teacher's answer (an escalation, a cold start, a pinned task, a teacher-only request) that no fallback teacher or degraded answer covers. `Retry-After` is the time until the breaker lets a probe through, clamped to 1 to 60 s ([ADR 0019](adr/0019-teacher-breaker-degraded-mode-and-fallback-teacher.md)) | Yes, after `Retry-After` |
| 502, 504 | `api_error` | `upstream_unavailable`, `upstream_timeout` (and `upstream_auth_error`, `upstream_not_found`, `upstream_rate_limited` as above) | `/v1/embeddings` in live mode: the embeddings model failed after the provider's retries. Mapped by cause as a teacher failure is, with these codes, since no teacher was involved; an upstream 400 (an input over the model's length) is a 400 with its message and code | As for the teacher's |
| 504 | `api_error` | `teacher_timeout` | Live mode: the teacher did not answer in time (`RATCHET_TEACHER_TIMEOUT_S`, retried once within `RATCHET_TEACHER_DEADLINE_S`), or Token Factory answered 504 (`teacher timeout: upstream 504 upstream_timeout`). Token Factory's `Retry-After`, if it sent one, is passed on (1 to 60 s) | Yes, after `Retry-After` when present |
| 504 | `api_error` | `batch_deadline_exceeded` | `/v1/completions`: a batch of prompts ran for `RATCHET_TEACHER_DEADLINE_S` before its next prompt, so it stopped. The message names the prompts already answered, logged and billed (not returned); `X-Should-Retry: false`. Any other error of a prompt after the first also carries `X-Should-Retry: false` and that message ([Completions](#post-v1completions)) | No |

OpenAI's Python and Node SDKs retry connection errors, 408, 409, 429 and 5xx responses twice by default, with backoff (or `Retry-After`, up to 60 s), unless the response carries `x-should-retry`. Set `max_retries=0` on the client to turn that off. Each retry of a request the student cannot answer runs the student's samples again, so the gateway marks the errors no retry can fix.

The gateway retries the teacher itself before any of these errors ([ADR 0014](adr/0014-upstream-failure-semantics.md), [CONFIGURATION.md](CONFIGURATION.md#upstream-timeouts-and-retries)): up to `RATCHET_TEACHER_RETRIES` (2) more attempts within `RATCHET_TEACHER_DEADLINE_S` (180 s), with full-jitter backoff or Token Factory's `Retry-After` (up to 8 s; a longer wait comes back to you at once, as the `Retry-After` of the 429, 502 or 504), on connection failures and 408, 409, 429, 500, 502, 503 and 504; a read timeout once at most, because its tokens may already be billed. The student is never retried on the request path: a failing student escalates to the teacher at once. After 5 failed teacher calls in a row the teacher's breaker answers with the 503 above instead of waiting on Token Factory, and lets one request probe it every 30 s (doubling to 300 s while it keeps failing). Two opt-in settings answer instead of an error ([CONFIGURATION.md](CONFIGURATION.md#when-the-teacher-is-down)): a licensed fallback teacher (`RATCHET_TEACHER_FALLBACK_MODEL`, a 200 with its model id) and, for an escalated request, the student's own answer marked as degraded (`RATCHET_ON_TEACHER_FAILURE=serve_student`). Neither covers a 400 about the request or a tool-call answer.

```python
import openai

try:
    client.chat.completions.create(model="ratchet", messages=msgs, temperature=3)
except openai.BadRequestError as e:
    print(e.status_code, e.code, e.param)  # 400 invalid_value temperature
```

### Examples

curl:

```bash
curl -s http://localhost:8000/v1/chat/completions \
  -H 'content-type: application/json' \
  -H 'X-Ratchet-Task: ticket-triage' \
  -d '{"model":"ratchet","messages":[
        {"role":"system","content":"You triage customer support tickets. Reply with JSON only ..."},
        {"role":"user","content":"I was charged twice for my Pro plan this month. Order #48213."}]}'
```

OpenAI Python SDK:

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="unused")  # or your RATCHET_API_KEYS key
resp = client.chat.completions.create(
    model="ratchet",
    messages=[{"role": "user", "content": "I was charged twice this month."}],
    extra_headers={"X-Ratchet-Task": "ticket-triage"},
)
print(resp.choices[0].message.content)
print(resp.model_extra["ratchet"])  # served_by, confidence, cost_usd, ...
```

A client that can set only the model name (LangChain, a no-code tool) names the task there:

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(base_url="http://localhost:8000/v1", api_key="unused", model="ratchet/ticket-triage")
```

OpenAI Node SDK:

```js
import OpenAI from "openai";
const client = new OpenAI({ baseURL: "http://localhost:8000/v1", apiKey: "unused" });
const r = await client.chat.completions.create(
  { model: "ratchet", messages: [{ role: "user", content: "Where is my order #48213?" }] },
  { headers: { "X-Ratchet-Task": "ticket-triage" } },
);
```

### Mock mode answers

In mock mode every request gets an answer from mock models (simulation; nothing here measures a real model). The synthetic tickets generated by `sim/tickets.py` and Scout's requests for the example questions (`sim/research.py`) get their demo answers: use the demo endpoints, take a ticket from `sim.tickets.TicketStream` in Python, or run the Scout CLI (`python -m ratchet.scout "What is LoRA fine-tuning?" --base-url http://localhost:8000/v1`). Any other request gets a deterministic answer from the generic mock world (`sim/generic.py`, [ADR 0041](adr/0041-generic-mock-world.md)), the same every time for the same text:

| The request | The mock teacher's answer |
|---|---|
| Plain text (`hello`) | `[mock teacher] You asked: "hello". ...`: a restatement of the prompt (at most 12 words) and one of four closing sentences |
| `response_format` `json_schema` | JSON that validates against the schema: enum values and booleans drawn, integers and numbers within their bounds, `mock text` for strings (a date, time, UUID or URI for those formats), 1 or 2 array items, nested objects and local `$ref`s. The OpenAI SDK's `client.chat.completions.parse(response_format=SomeModel)` parses it |
| `response_format` `json_object` | `{"answer": "[mock teacher] You asked: ..."}` |
| Ending with a `tool` message | `[mock teacher] The tool returned: "...". ...` |
| `tools` that reach the teacher | A tool call, returned as `message.tool_calls` ([Tools and function calling](#tools-and-function-calling)): `lookup_order(order_id)` or `refund(order_id, amount, reason)` (the mock catalogue, `sim.generic.TOOLS`) with the order number, amount and reason read from the prompt, or a call to your own tool with arguments built from its parameters schema. The call ids are stable, so the same request gets the same call. A request that carries tool turns or offers tools is answered by the mock teacher alone and never stored, as in live mode |

The mock student learns these requests as it learns tickets, per schema name or system prompt (tool requests never reach it). `RATCHET_MOCK_STRICT=1` restores the earlier behaviour: anything but a demo request is a 400 `mock mode only answers demo requests`.

---

## End users and storage

[ADR 0080](adr/0080-storage-opt-outs-and-pseudonymous-end-users.md). Every teacher answer of a request (an escalation, a cold start, an audit, a shadow arbitration) becomes training data unless an opt-out applies. The broadest one that applies is recorded with the request (`requests.store_opt_out`) and returned in `ratchet.store_opt_out`:

| Opt-out | Set by | Reason |
|---|---|---|
| Zero retention | `RATCHET_STORE_EXAMPLES=0` ([CONFIGURATION.md](CONFIGURATION.md#privacy-and-security)); Night School then skips every night | `global` |
| A no-store task | `store: false` in the task's spec, or `RATCHET_NO_STORE_TASKS` | `task` |
| A no-store API key | `store=0` for the key's name in `RATCHET_KEY_LIMITS` | `key` |
| The request | `store: false`, `X-Ratchet-Store: none` (or `0`, `false`), or `metadata.ratchet_store` `"false"` (or `"0"`) | `request` |
| A no-train end user | `POST /v1/ratchet/no-train` (below) | `end_user` |

- An opted-out request is answered exactly as any other: the student may serve, the teacher still audits at the usual rate and still settles shadow disagreements, so release decisions never depend on what is stored. Nothing is inserted into the training set and no stored example's repeat count goes up; `ratchet.stored` is `false`.
- An opt-out only ever removes storage. `store: true`, `X-Ratchet-Store: 1` or `metadata.ratchet_store: "true"` is the default spelled out and cannot undo another opt-out. An absent `store` means store (OpenAI's chat completions default to not storing; Ratchet exists to distill, so it defaults the other way).
- Pinned tasks and teacher-only requests are never stored anyway; their rows still record the opt-out. Examples stored before an opt-out took effect stay until they are erased ([Erasure and export](#erasure-and-export)) or expire (`RATCHET_EXAMPLE_RETENTION_DAYS`).
- **End users.** The request's `safety_identifier`, else its `user`, names its end user (any length, hashed whole; empty means none). Ratchet stores only HMAC-SHA256 of it under the deployment's key (`RATCHET_END_USER_KEY`, or a random key generated once in the ledger), scoped to the API key's name and cut to 16 hex digits, in the request row and in an example it stores. The same identifier sent by two API keys is two people as far as the ledger can tell, and two deployments cannot link their hashes. The raw value is never stored, logged or forwarded upstream. A request that sends both fields is attributed to `safety_identifier`; its `user` is only looked up for a no-train registration.
- **Key names.** The scope is the key's *name* in `RATCHET_API_KEYS`, not the key: rotating a key under the same name keeps every hash, while renaming it starts new ones. A no-train registration scoped to a name that is no longer configured matches nothing (the gateway writes a `no_train_unmatched` event at startup and counts them in `/ratchet/stats` → `privacy.no_train_unmatched`) until the name is back or the end user registers again; an erasure can still name the old client. Keyless registrations match nothing while keys are on, the same way.
- Examples also record the API key name of the request they were first stored from; a later duplicate from another key is recorded too, so a client's data can be found.

### POST `/v1/ratchet/no-train`

```bash
curl -X POST localhost:8000/v1/ratchet/no-train -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' -d '{"end_user": "user-8812"}'
```

```json
{"object": "ratchet.no_train", "end_users": [{"client": "acme", "created": true}]}
```

From then on, requests from that end user are answered as usual but never stored (`store_opt_out` `end_user`), checked once at the start of each request and again just before anything would be stored (an opt-out found then is reported the same way). The value is any one the client sends as `user` or `safety_identifier`: a request is matched on each of the two it carries, so a client that registered `user` values and later also sends `safety_identifier` keeps its registrations. Only the hash is kept, and the response does not return it (the endpoint is not a way to compute hashes). Each entry names the client scope it was registered in (`null` for keyless requests, `"*"` for every client).

- **Who.** Open wherever chat completions are: with `RATCHET_API_KEYS` set it needs an API key and registers that key's own end user; without keys it registers the end user of keyless requests. An admin key (`RATCHET_ADMIN_KEYS`) may add `"client": "<key name>"` to register that client's end user, or leave it out to register the end user for every client (scope `"*"`): every API key, also keys added later, and keyless requests, as for a data subject's request.
- `created` is `false` when the end user was already registered in that scope. It is told only to an API key or an admin key: without API keys anyone may call this endpoint, so a keyless caller gets `null` either way and cannot learn whether an identifier was registered. A registration an admin makes cannot be removed by the client.
- A client's registration is scoped to its key's name: renaming the key in `RATCHET_API_KEYS` unlinks it ([End users](#end-users-and-storage)); an admin's registration for every client does not depend on key names.
- **Errors.** 400 for a body that is not an object, an unknown field (`unknown_parameter`), a missing, non-string or empty `end_user` (never quoted back), or a `client` that names no API key; 401 for a wrong key; 403 when an API key names another client; 409 when new registrations would pass 1,000 in mock mode (the shared demo; a reset clears them; one already registered does not count again). A live gateway has no cap: a cap anyone could fill would block real opt-outs.
- The event log records a count and who registered (`no_train` events), never the end user or its hash.

### DELETE `/v1/ratchet/no-train`

The same JSON body as `POST`, never a query string (access logs keep URLs). Removes the registration, so that end user's requests are stored again. The response lists each client scope with a `status`: `removed`, `kept` (the end user is still no-train there: an admin registered it in that scope, which an API key cannot remove, or an admin's registration for every client still covers it) or `absent`. An API key removes its own end users' registrations; an admin key removes any, for a named `client`, or with none in every scope: each configured key, the keyless scope, every scope a registration is stored in (also a renamed key's) and `"*"`. Without API keys, a live gateway answers 403 unless an admin key is sent, so nobody can switch storage back on for someone else's end users; the mock demo stays open, and its keyless callers get `status: null`, as they get `created: null` from `POST`.

## Feedback

People who use the answers know when one is wrong. `POST /v1/ratchet/feedback` records their verdict on an answer the gateway served and, with a correction, stores the right answer as a training example whose provenance is `human` ([ADR 0091](adr/0091-human-labels-as-a-provenance-source.md)). The student's own text never becomes one (invariant 2): the gateway never kept it, and a `correct` verdict stores nothing.

### POST `/v1/ratchet/feedback`

```bash
# A verdict only: counts toward the task's human agreement and the version's human precision.
curl -X POST localhost:8000/v1/ratchet/feedback -H "Authorization: Bearer $KEY" \
     -H "Content-Type: application/json" \
     -d '{"request_id": "req_3f1c0a9b8e7d6c5b4a3f2e1d0c9b8a79", "verdict": "correct"}'

# A correction: the right answer and the request's messages, exactly as the chat request sent them.
curl -X POST localhost:8000/v1/ratchet/feedback -H "Authorization: Bearer $KEY" \
     -H "Content-Type: application/json" \
     -d '{"request_id": "req_3f1c0a9b8e7d6c5b4a3f2e1d0c9b8a79", "verdict": "incorrect",
          "corrected": "{\"category\": \"refund\", \"priority\": \"low\"}",
          "messages": [{"role": "user", "content": "Where is my refund for order 1234?"}]}'
```

| Field | Meaning |
|---|---|
| `request_id` | Required. The response's `ratchet.request_id` (`req_…`), or its `id` (`chatcmpl-…`) |
| `verdict` | Required. `correct` or `incorrect` |
| `corrected` | The right answer, with `incorrect` only. It is cleaned as an answer is served (`tasks.clean_answer`) and must be valid under the task's spec: JSON that parses, every required field, labels in their vocabulary (else **400** `invalid_correction`); at most `RATCHET_MAX_INPUT_CHARS` characters |
| `messages` | Required with `corrected`, and only with it: the request's messages, because request rows hold no text. They are validated and normalised exactly as the chat route does (`developer` becomes `system`, text parts are joined), text parts only |
| `response_format`, `tools`, `tool_choice`, `stop` | The request's conditions, when it sent any, so the correction has the same example hash as the teacher's answer to that request and replaces it. Without them, a correction of a request that had conditions is another variant of the prompt, in the same split |
| `user`, `safety_identifier` | The end user the request named: every identifier it sent, both when it sent both. Required with a correction when the request named one (**400** `end_user_required`, also when it sent both and the correction sends one), and each must be the same (**400** `end_user_mismatch`): the request row keeps a keyed hash of each, and only with the values can every no-train registration of that end user, made with either identifier or by an admin for every client, keep the correction out. Hashed only, never stored. A request logged before the second hash was recorded needs only the identifier it was attributed to |

The response:

```json
{"object": "ratchet.feedback", "request_id": "req_3f1c…", "verdict": "incorrect", "updated": false,
 "correction": {"stored": true, "not_stored": null, "split": "train", "replaced": "teacher"}}
```

`updated` is `true` when an earlier verdict on the same request was replaced: there is one verdict per request, the latest. `correction` is `null` without one; otherwise `stored`, with `split` (the split the example's hash gives, invariant 3) and `replaced` (`teacher` when the correction replaced the teacher's stored answer to the same request, `human` when it replaced an earlier correction of the same request, else `null`), or `not_stored` saying why not:

| `not_stored` | Why |
|---|---|
| `global`, `task`, `key`, `request`, `end_user` | A storage opt-out: the request's own (as it was recorded), or one in force now (zero retention, a no-store task, the key's `store=0`, an end user registered as no-train since) ([End users and storage](#end-users-and-storage)) |
| `corrections_off` | `RATCHET_FEEDBACK_CORRECTIONS=0`: verdicts are kept, corrections are not |
| `pinned` | The task is pinned: its answers are never training examples (invariant 10) |
| `unknown_task`, `teacher_only` | The request was answered by the teacher alone and is never distilled: an unregistered task under `RATCHET_UNKNOWN_TASKS` `teacher` or `reject`, or a request with a part only the teacher reads |
| `invalid after redaction` | A redaction placeholder breaks the structured answer (as for a teacher answer) |
| `human_label_exists` | The example already has an equal or stronger human label: another client's correction, or an operator's import (`python -m ratchet.importer`). The first client's correction stands, and an import beats any correction |
| `erased` | The example was erased (FR-13.3) and is never stored again |

A correction that is stored becomes a training example like a teacher's, with `teacher_model` `human`, license `customer-provided labels` and `verified` 1, redacted when `RATCHET_REDACT_PII` is on, attributed to the API key (and the end user's hash). It replaces the teacher's answer on the same example hash (a human label beats a teacher's; a later teacher answer only bumps `seen`). The request counts once toward the frequent-request rule: a correction of a request whose teacher answer was already stored adds no occurrence, and when it lands on another variant of the prompt that example starts at `seen` 0 (invariant 3). A request is corrected once: the same messages and conditions again replace that correction (a fix); other messages are **409** `correction_exists`, and so is a `correct` verdict after a stored correction (erase the example to withdraw it).

Rules: an API key reaches its own requests only; another key's request id, an unknown one and one whose row request retention folded are the same **404** `request_not_found`. With API keys off it is open, like chat completions, and reaches requests sent without a key. The volume is bounded by the requests a key may send: one verdict row and at most one stored correction per request. Errors use OpenAI's envelope (400, 401, 404, 409); a refused call records nothing.

Human precision, the share of `correct` verdicts on the answers a version served itself (route `student`), is reported in `/ratchet/stats` → `feedback.by_version` with its 95% Wilson bounds (a fixed-sample interval, for reading at one moment). It is never used to promote or roll back a version, because clients report what they notice; the audited precision stays the release signal. The alert is checked after every verdict, so it uses an anytime-valid rule instead of the Wilson bound: once a version has at least `RATCHET_MIN_AUDITS` verdicts and the mixture e-value against "precision is at least `RATCHET_LIVE_FLOOR`" reaches 40 (`feedback.ALERT_ALPHA` 0.025, ADR 0025), one `human_alert` event says so (see the runbook). The false-alarm rate stays under 2.5% however many verdicts arrive.

## Erasure and export

[ADR 0081](adr/0081-retention-tombstones-erasure-and-export.md), FR-13.3 to FR-13.5. An opt-out keeps new data out; these remove what was stored, and hand it over.

- **What an erasure does.** Each selected example loses its messages, answer, stored conditions and end-user hash for good. The row stays as a tombstone (hash, task, split, seen count, teacher, license, client), so the counts Night School and the golden split rely on stay right, and its hash is never stored again: a later request with the same text is answered as usual and logged `stored` 0 (a `skip_example` event with reason `erased`), from any client. Erasing an end user also clears that end user's hashes from its request rows (both, when the request carried `user` and `safety_identifier` and either one is erased).
- **Shared examples.** When the scope names a client or an end user, `RATCHET_ERASURE_SHARED` decides an example another client also sent: `delete` (default) purges it too; `keep` keeps it for the other client and unlinks the erased party. Erasure by hashes or by text purges whatever it selects.
- **The record.** `erasures` holds who asked, the reason (redacted), the scope (end users as their HMACs, `contains` as its SHA-256), the counts, the versions that may hold the data (`tainted`) and a `status` (`pending` while such versions exist, else `complete`); `erasure_items` lists each purged hash with its task and split. No request text, raw end user or key is written. An `erase` event gives the counts.
- **Trained adapters.** `affected_versions` lists every version that may have been trained on an erased example, with its state and how many: exactly from its committed training set when it has one (FR-16.4), else every version created after the example was stored while it counted as a training row (this errs toward listing). They are reported, never changed: each adapter's signed BOM stays as written (INV-14), and removing what an adapter may have memorised needs a retrain without the rows, which the next training night does (it never sees purged text). Unlearning from an existing adapter is out of scope ([THREAT_MODEL.md](THREAT_MODEL.md)).
- **Retention.** Examples not seen for `RATCHET_EXAMPLE_RETENTION_DAYS` become tombstones the same way, except that a later duplicate that may be stored brings the text back; request rows older than `RATCHET_REQUEST_RETENTION_DAYS` fold into daily sums that every stats figure adds back ([CONFIGURATION.md](CONFIGURATION.md#privacy-and-security)).

### POST `/ratchet/admin/privacy/erase`

Needs an admin key (`RATCHET_ADMIN_KEYS`; 401 without one, 403 for an API key, 403 in live mode without admin keys, open in mock mode as `mock-admin`).

```bash
curl -X POST localhost:8000/ratchet/admin/privacy/erase -H "Authorization: Bearer $ADMIN_KEY" \
  -H 'Content-Type: application/json' -d '{"client": "acme", "reason": "contract ended", "dry_run": false}'
```

```json
{"object": "ratchet.erasure", "id": 3, "dry_run": false, "matched": 412, "purged": 410, "kept": 2,
 "requests_unlinked": 0, "affected_versions": [{"id": 4, "state": "live", "adapter_uri": "student-v4", "examples": 371}],
 "scope": {"client": "acme", "shared": "keep"}}
```

| Field | Meaning |
|---|---|
| `client` | An API key name (also one whose key was since removed): the examples it sent, first or later |
| `end_user` | The value requests are attributed to (`safety_identifier` when a request sent one, else `user`): hashed in `client`'s scope, or with no `client` in every configured API key's scope, every scope the ledger holds end users in (a renamed or removed key's too) and the keyless one; never quoted back |
| `hashes` | Up to 10,000 example hashes (64 lowercase hex characters each, as in an export) |
| `contains` | 1 to 1,000 characters matched with LIKE in the stored messages, answer and conditions (case-insensitive for ASCII letters; `%` and `_` are literal). Stored text is already redacted, so an identifier the redactor replaced is not there to find |
| `reason` | Required, 1 to 500 characters; stored redacted |
| `dry_run` | Default `true`: nothing changes, `id` is `null`, and the counts say what would happen |

Every selector given narrows the scope; at least one is needed. A bad scope is a **400** that never quotes an end user or the text to match. The erasure is logged with the admin key's name as its actor (`python -m ratchet.admin erase --actor` takes a name of the same characters, never free text, because the `erase` event is public in `/ratchet/stats`). `matched` counts the examples selected (ones an earlier erasure took are left out), `purged` those whose text this erasure removed or whose retention tombstone it made final, `kept` the shared ones kept and unlinked, `requests_unlinked` the request rows whose end-user hash was cleared.

### DELETE `/v1/ratchet/examples`

An API key erases the stored examples of one of its own end users, with the same JSON body as `/v1/ratchet/no-train` plus optional `reason` (default `end user request`) and `dry_run` (default `false`, unlike the admin call: a `DELETE` deletes). An admin key may name a `client`, or leave it out for every client. Open wherever chat completions are (it only removes data); keyless without API keys. The end user is read from the body only: a URL with `end_user` is a **400** (`end_user_in_url`), because access logs keep URLs (the item's spec proposed `?end_user=`; see ADR 0081). The response is the admin call's, except that a keyless caller (API keys off) gets `matched`, `purged`, `kept`, `requests_unlinked` and `affected_versions` as `null`, for a dry run too: an open endpoint must not tell anyone whether an identifier had anything stored (as no-train's `created`). Errors use OpenAI's envelope (400, 401, 403 as for no-train).

```bash
curl -X DELETE localhost:8000/v1/ratchet/examples -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' -d '{"end_user": "user-8812"}'
```

### GET `/ratchet/admin/export/{kind}`

One table as NDJSON (`application/x-ndjson`, one JSON object per line, in id order), streamed a page at a time, so the ledger is never locked for the whole export; rows written meanwhile may appear (for a snapshot, back up the file). Needs an admin key, like the other management calls.

| Kind | Each line | Filters |
|---|---|---|
| `examples` | An example that still has its text: `id`, `ts`, `day`, `task`, `hash`, `split`, `seen`, `messages`, `completion`, `params`, `provenance` (`teacher`, `license`, `license_url`, `hash`, `redactions`, `origin`), `client` (first sender), `clients` (every client that sent it), `last_seen_ts` | `task`, `split`, `since`, `client` |
| `requests` | A request row's metadata (`requests` columns), without the end-user hashes (`end_user`, `end_user_secondary`) | `task`, `since`, `client` |
| `versions` | A version with its `eval` and its BOM exactly as stored | `since` |
| `events` | `id`, `ts`, `day`, `kind`, `detail` | `since` |
| `erasures` | An erasure with its `scope` (end users as a count), counts, `affected_versions`, `status` and `items` (`hash`, `task`, `split`) | `since` |

`since` is seconds since the epoch or an ISO 8601 date or date-time (UTC unless it names an offset), compared with the row's `ts` (`created_ts` for versions). A filter the kind does not take, an unknown kind or a bad value is a **400** before the first line. API keys are never stored and end-user hashes are left out, so no line holds either. `python -m ratchet.admin export <kind> [--task] [--split] [--since] [--client] [--out FILE]` writes the same lines.

## GET `/healthz`

Liveness: the process answers. Always public, also when API keys are on. The demo VM's Docker health check uses it. It is a coroutine on the event loop, so it answers while every worker thread is busy (a full thread pool is not a dead process); it asks no dependency, so a restart loop never follows a slow teacher. For "can this gateway serve?", use [`/readyz`](#get-readyz).

```json
{"status": "ok", "version": "0.4.0", "mode": "mock"}
```

## GET `/livez`

The same answer as `/healthz`, under the name Kubernetes probes use. Public.

## GET `/readyz`

Readiness, for load balancers and deploy scripts ([ADR 0070](adr/0070-configuration-checks-and-readiness.md)). Public; the answer names states, statuses and exception classes only, never a URL, a key or an exception's message. `Cache-Control: no-store`. A 503 here is an answer, not a failed request: it writes no `failures` row, however often a load balancer asks.

| Status | `status` | When |
|---|---|---|
| 200 | `ready` | Every check is `ok` |
| 200 | `degraded` | Requests can be answered, but something is down or busy that the gateway works around: the student endpoint (its breaker open, its model list failing or slow, a routed adapter missing), the teacher's model list failing, slow or not listing the teacher model, the teacher's breaker open with its wait over (`probe_due`: the next request is its probe) while its endpoint answers, the teacher's breaker open while a fallback teacher's is closed or the student stands in (`RATCHET_ON_TEACHER_FAILURE=serve_student`, student up), or the ledger busy (another writer held it past 2 s) on one probe |
| 503 | `not_ready` | The ledger cannot be written (closed, read-only, or busy on two probes in a row, 15 s apart), or no teacher can answer: its breaker is open and still waiting with no stand-in, its wait is over but its endpoint does not answer (a connection error, a timeout or a 5xx from `/models`), or its model list answers 401 or 403 (the key is refused) |

What is checked:

- `ledger` (both modes): `SELECT 1`, then `BEGIN IMMEDIATE`, a write of the header's `user_version` to its own value, and `ROLLBACK`. Nothing is committed. `error` is `closed`, `read_only`, `locked`, `lock_timeout` or the exception's class. `locked`, `lock_timeout` and a probe still waiting after 2 s (`timeout`) mean another writer holds the ledger (`python -m ratchet.live night`, a long write): requests wait for it rather than fail, so the first such probe is `degraded` and the second in a row `not_ready` (`busy_runs` counts them).
- `teacher` (live mode): the teacher's breaker (and the fallback teacher's, `fallback_breaker`), and `GET {RATCHET_TEACHER_BASE}/models` with the teacher key (no tokens spent): `status`, `models` (`ok`, `http_<status>`, `unreadable`) or `error` (`timeout`, `ConnectError`, ...), and `model_listed`. While the primary's breaker is not closed, `probe_due` says whether its wait is over (or it is half-open), so the next request that needs the teacher is the breaker's probe. `available`: some teacher can answer: its breaker is closed, or its probe is due and its endpoint answered `/models` below 500 (a fallback teacher on an endpoint of its own, `RATCHET_TEACHER_FALLBACK_BASE`, has no such check: its due probe is enough). Only a request moves an open breaker on, and a gateway a load balancer took out gets none, so a due probe must count, or one teacher blip would keep the gateway out for good. `ok`: the primary's breaker is closed and its model list lists the teacher model.
- `student` (live mode): the student breakers by name (`breakers`; the shadow's is reported, not counted). With `RATCHET_STUDENT_BASE`, `GET {RATCHET_STUDENT_BASE}/models` must list the adapter of every live, canary and shadow version: `missing_adapters` names the ones it does not, and each is handed to the adapter reloader (`reload_queued`; at most once a minute per adapter). Without an endpoint, `model` is the hosted fallback student.
- `config`: the number of startup warnings (in mock mode also their messages; in live mode read them in the log or with `python -m ratchet.config check`).
- `flags`: operational switches that never change the status: `scout_budget_exhausted` (Scout's daily budget is spent).

Mock mode checks the ledger only. The probes run on threads of their own (two for the endpoints, one for the ledger, never the request thread pool), each awaited at most 2 s (a slower one reports `"error": "timeout"` and goes on, and its result is kept), and a result is reused for 15 s, so the teacher sees at most one `/models` call per 15 s however often `/readyz` is asked. The student endpoint's result is reused for 5 minutes while it is `ok` (15 s when it is not, so a reloaded adapter shows soon): it never makes the gateway not ready, and each probe is a request to the endpoint, which on a platform that scales an idle endpoint to zero may count as traffic and keep it running. Breaker states are read fresh on every call. `ms` is how long a probe took.

```json
{
  "status": "degraded",
  "mode": "live",
  "version": "0.4.0",
  "checks": {
    "ledger": {"ok": true, "ms": 1.2},
    "teacher": {"breaker": "closed", "status": 200, "models": "ok", "ms": 84.0, "model_listed": true, "available": true, "ok": true},
    "student": {"endpoint": true, "breakers": {"student": "closed"}, "ok": false, "status": 200, "models": "ok",
                "missing_adapters": ["student-v3"], "reload_queued": ["student-v3"], "ms": 41.5},
    "config": {"ok": true, "warnings": 0}
  },
  "flags": {"scout_budget_exhausted": false}
}
```

## GET `/v1/models`

The model ids a client can send ([Task resolution](#task-resolution)), each a `model` object the OpenAI SDK's `Model` accepts. Needs a key when `RATCHET_API_KEYS` is set.

- `ratchet` first (its task comes from each request);
- `ratchet/<task>` for every registered task the key may use (a task whose `clients` do not name it is left out; with keys on, an auto task only to a key whose own requests used it in the last 30 days, since its name is a digest of a client's system prompt), in registry order;
- unless `RATCHET_UNKNOWN_TASKS=reject`, `ratchet/<name>` for every unregistered name the key's own requests named in the last 30 days (with keys off, any request's), at most 500, the most recently used first;
- every model alias whose task the key may use, with `alias_of`;
- the embeddings model (`RATCHET_EMBEDDINGS_MODEL`) unless `RATCHET_EMBEDDINGS_BASE=off`, with `ratchet.endpoint: "embeddings"` and no task: the id `RATCHET_MODEL_POLICY=strict` accepts on `/v1/embeddings`. `ratchet/embeddings` is never listed for it: `embeddings` is the task name embeddings requests are logged under, not a chat task.

```json
{"object": "list", "data": [
  {"id": "ratchet", "object": "model", "created": 1792400000, "owned_by": "ratchet",
   "ratchet": {"task": null, "pinned": false, "format": null, "compare_keys": [], "live_version": 5}},
  {"id": "ratchet/ticket-triage", "object": "model", "created": 1792310000, "owned_by": "ratchet",
   "ratchet": {"task": "ticket-triage", "pinned": false, "format": "json", "compare_keys": ["category", "priority"], "live_version": 5}},
  {"id": "gpt-4o-mini", "object": "model", "created": 1792310000, "owned_by": "ratchet",
   "ratchet": {"task": "ticket-triage", "pinned": false, "format": "json", "compare_keys": ["category", "priority"], "live_version": 5,
               "alias_of": "ratchet/ticket-triage"}}
]}
```

`created` is when a request (this key's, with keys on) first named the task, else when the gateway process started. In `ratchet`: the task, whether it is pinned, its answer format, the answer fields that must agree, and the live version that serves it (`null` when the student never does: no live version, a pinned task, or an unregistered task under `RATCHET_UNKNOWN_TASKS=teacher`).

## GET `/v1/models/{id}`

One object of the list above (`client.models.retrieve("ratchet/ticket-triage")`; the SDK sends the slash as `%2F`, which is fine; `ratchet:<task>` finds `ratchet/<task>`). It also finds `ratchet/<name>` for an unregistered task name no request used yet, unless `RATCHET_UNKNOWN_TASKS=reject`, because a chat completion with that model would be served (under either `RATCHET_MODEL_POLICY`), so a client that checks its model first gets the request's answer; `created` is then the process start. Any other id is a 404 `model_not_found`: `{"error": {"message": "The model 'gpt-4o' does not exist.", "type": "invalid_request_error", "param": null, "code": "model_not_found"}}`. Needs a key when `RATCHET_API_KEYS` is set.

## Azure-style route

`POST /openai/deployments/{deployment}/chat/completions?api-version=...` runs the same handler as `/v1/chat/completions` (the same validation, checks, quota, envelope, streaming and `ratchet` block) for clients written against Azure OpenAI. The deployment names the task: a model alias's task, `ratchet` (the other rules decide), `ratchet:<task>`, or a task name, which follows `RATCHET_UNKNOWN_TASKS` when it is not registered; anything else is a 404 `model_not_found`. The body's `model` is not read (so `RATCHET_MODEL_POLICY=strict` does not refuse it), the header and `metadata.ratchet_task` still win, and `api-version` is accepted and ignored. The key may come in Azure's `api-key` header.

```python
from openai import AzureOpenAI

client = AzureOpenAI(azure_endpoint="http://localhost:8000", api_key="unused", api_version="2024-10-21")
resp = client.chat.completions.create(model="ticket-triage", messages=[{"role": "user", "content": "I was charged twice."}])
```

## POST `/v1/embeddings`

A pass-through ([ADR 0050](adr/0050-adjacent-openai-endpoints.md)): one call to `RATCHET_EMBEDDINGS_MODEL` at `RATCHET_EMBEDDINGS_BASE` (by default the teacher's Token Factory endpoint, with `NEBIUS_API_KEY`; another base gets `RATCHET_EMBEDDINGS_KEY`, or no key when that is empty, never the Token Factory key). No student, no teacher, no confidence, no audit, and nothing of the request is stored: vectors are not distilled. Mock mode answers locally with deterministic unit vectors (256 values, seeded from SHA-256 of each input). `RATCHET_EMBEDDINGS_BASE=off` turns the route into a 404 `unsupported_endpoint`.

| Field | Type | Required | Notes |
|---|---|---|---|
| `input` | string, array of strings, array of token ids, or array of token-id arrays | yes | 1 to 2,048 items, none empty, all of one kind. Their text counts against `RATCHET_MAX_INPUT_CHARS` (a token as 4 characters) |
| `model` | string | no | Never selects the upstream model: the configured one always answers. Under `RATCHET_MODEL_POLICY=lenient` any id is served (another id than `ratchet` or the configured one is echoed in `X-Ratchet-Model-Alias`); under `strict` only `ratchet` and the configured id, anything else a 404 `model_not_found`, because vectors from another model than an index was built with cannot be compared with it |
| `encoding_format` | `float` or `base64` | no | What the client gets; the upstream is always asked for floats, and the gateway encodes base64 (little-endian float32, what the OpenAI SDKs ask for by default and decode) |
| `dimensions` | integer, 1 to 8,192 | no | Sent upstream as is (the mock keeps the first that many values of the full vector and scales them back to length 1) |
| `user` | string | no | Checked, never forwarded |
| any other field | | no | Ignored, never forwarded |

The response is OpenAI's (`openai.types.CreateEmbeddingResponse`) plus a `ratchet` block; `model` is the upstream's name for the model that served, and `usage` is the upstream's prompt tokens (about 4 characters a token when it reports none):

```json
{"object": "list", "model": "Qwen/Qwen3-Embedding-8B",
 "data": [{"object": "embedding", "index": 0, "embedding": [0.0123, -0.0456, ...]}],
 "usage": {"prompt_tokens": 5, "total_tokens": 5},
 "ratchet": {"served_by": "upstream", "route": "passthrough", "task": "embeddings", "cost_usd": 5e-08,
             "all_teacher_cost_usd": 5e-08, "latency_ms": 41.2, "requested_model": "text-embedding-3-small"}}
```

Each answered call is one request row (task `embeddings`, served_by `upstream`, route `passthrough`, `stored` 0) priced at the configured model's input price (`EMBEDDING_PRICES` in `ratchet/config.py`, unverified until the day-1 checks pass; a model without a price there is priced as the teacher), so it counts against the key's quota and shows in the cost report and as the `embeddings` row of `/ratchet/stats` `tasks`, with its own cost as its all-teacher baseline. The headline figures (`days`, `totals`, `clients`) leave it out: they describe the distillation, which embeddings are not part of, so a client that embeds once per chat request does not halve its student share. Headers: `X-Ratchet-Served-By: upstream`, `X-Ratchet-Task: embeddings`. The task name `embeddings` is reserved for these rows: a chat, completions or responses request that names it (`X-Ratchet-Task`, `metadata.ratchet_task`, `model: "ratchet/embeddings"`, an Azure deployment) is a 400 `reserved_task`, and no task spec or model alias may use it. A failed call logs nothing; its error is mapped by cause ([Errors](#errors): `upstream_*` codes). The shared mock demo returns at most 131,072 values (inputs x dimensions) per request, about 3 MB of JSON.

```python
vectors = client.embeddings.create(model="ratchet", input=["refund my order", "reset my password"])
```

## POST `/v1/completions`

The legacy text API, for clients and tools that still call it. Each prompt becomes one user message, `[{"role": "user", "content": prompt}]`, and runs the chat path: the same validation, task resolution (`X-Ratchet-Task`, `metadata.ratchet_task`, `model: "ratchet/<task>"`, ...), cascade, quota, storage rules and `ratchet` block as a `/v1/chat/completions` request with that message, so a prompt is stored as exactly that chat request's example.

| Field | Notes |
|---|---|
| `prompt` | A string, or up to 16 strings (each its own cascade request, logged and counted against the quota; a batch the quota cannot finish is refused before its first prompt runs). Token arrays are a 400 `unsupported_value`: the gateway works in text |
| `suffix` | A 400 `unsupported_parameter` unless empty: a chat model cannot fill in text before a suffix |
| `echo` | A 400 `unsupported_parameter` when `true` |
| `best_of` | A 400 `unsupported_parameter` unless 1 or `n`: the gateway returns the choices it samples, it does not rank more |
| `logprobs` | 0 to 5: each choice's token logprobs in the legacy shape (`tokens`, `token_logprobs`, `top_logprobs` as `{token: logprob}` per token, `text_offset` counted from the prompt's length), from the chat path's token logprobs with that many alternatives ([Choices and logprobs](#choices-and-logprobs)); `null` when they cannot be given |
| `model`, `max_tokens`, `temperature`, `top_p`, `n`, `stream`, `stream_options`, `stop`, `seed`, `presence_penalty`, `frequency_penalty`, `logit_bias`, `user`, `metadata`, others | The chat field of the same name, checked and sent exactly as on `/v1/chat/completions`. `max_tokens` has no default of 16 here: the per-call caps apply ([Generation parameters](#generation-parameters)) |

```json
{"id": "cmpl-...", "object": "text_completion", "created": 1792400000, "model": "student-v3", "system_fingerprint": "fp_...",
 "choices": [{"text": "{\"category\": \"billing\", ...}", "index": 0, "logprobs": null, "finish_reason": "stop"}],
 "usage": {"prompt_tokens": 18, "completion_tokens": 21, "total_tokens": 39, ...},
 "ratchet": {"served_by": "student", "task": "ticket-triage", ...}}
```

Prompt p's choice j is choice `p * n + j`, as OpenAI numbers them; `usage` adds up every prompt's. With one prompt `ratchet` is the chat path's block; with several it is `{"prompts": [block, ...]}`, and the headers are the last prompt's (`X-Ratchet-Served-By` is `mixed` when the prompts were not all served the same way). With `stream: true` the answer comes as `text_completion` chunks (`choices: [{text, index, logprobs, finish_reason}]`), each choice's text then its finish chunk, a last chunk with `usage` and no choices under `stream_options.include_usage`, and `[DONE]`; the `ratchet` block rides on the last finish chunk or on the usage chunk, as in a chat stream. The prompts run in order. If the first fails, the request returns its error, as a chat request would. If a later one fails (the teacher fails, or a concurrent request from the same key used up the quota the batch was checked against), or the batch has run for `RATCHET_TEACHER_DEADLINE_S` (180 s) before its next prompt, the request returns that prompt's error (or a 504 `batch_deadline_exceeded`) with `X-Should-Retry: false` and no `Retry-After`, and a message naming the prompts already answered (`The batch stopped at prompt[1] of 2: ... prompt[0] was answered, logged and billed, but this error does not return its answer; resend only the prompts from prompt[1] on.`). Those stay logged, billed and stored; their answers are not returned. OpenAI SDKs then do not resend the batch, which would run, bill and store the earlier prompts again (and count their examples as seen again); send only the prompts from the one named on.

```python
resp = client.completions.create(model="ratchet/ticket-triage", prompt="I was charged twice this month.")
print(resp.choices[0].text)
```

## POST `/v1/responses`

The stateless subset of OpenAI's Responses API. The body becomes a chat request, which runs the chat path (validation, task resolution, cascade, quota, storage, `ratchet` block); the answer comes back as a `response` object (`openai.types.responses.Response`). Ratchet keeps no response state: each response is built for its request and forgotten, so a conversation is sent whole every time.

| Field | Becomes |
|---|---|
| `input` | A string: one user message. An array of items: `message` items keep their role (`user`, `assistant`, `system`, `developer`) and content (a string, or `input_text` / `output_text` / `refusal` parts joined as a chat request's text parts are; an `input_image` with an `image_url`, read by the teacher only under `RATCHET_TEACHER_MODALITIES`); consecutive `function_call` items the tool calls of one assistant message (`call_id` the call's id), joined to the assistant `message` item right before them when there is one, as a response's `output` lists its text and then its calls; a `function_call_output` a `tool` message. `reasoning` items are dropped (and split no calls). So a tool loop sent back as input is the chat conversation it would be, and is stored as that request. An `item_reference`, a built-in tool's item, a file, audio or an image by file id is a 400 `unsupported_value` |
| `instructions` | A leading system message |
| `text.format` | `response_format`: `json_schema` (its `name`, `schema`, `strict`, `description`), `json_object`, or `text` (none) |
| `tools`, `tool_choice`, `parallel_tool_calls` | Function tools (`{type: "function", name, description, parameters, strict}`) and a function `tool_choice` become the chat ones and follow their rules ([Generation parameters](#generation-parameters)). A built-in tool (web search, file search, code interpreter, MCP, ...) or a tool choice naming one is a 400 `unsupported_parameter` |
| `max_output_tokens`, `temperature`, `top_p`, `reasoning.effort` | `max_tokens`, `temperature`, `top_p`, `reasoning_effort` |
| `model`, `metadata`, `user`, `safety_identifier` | The chat fields of the same name (`model: "ratchet/<task>"` and `metadata.ratchet_task` name the task) |
| `previous_response_id`, `conversation`, `prompt`, `background: true`, `stream: true` | A 400 `unsupported_parameter`: they need stored state, or a stream of response events, that the gateway does not keep or send. `stream: false` and `background: false` are fine |
| `include`, `truncation`, `service_tier`, `store`, `top_logprobs`, `max_tool_calls`, `prompt_cache_key` | Accepted, no effect: no reasoning items or logprobs are returned, the input is never cut, and nothing is stored (`store` is `false` in the response) |
| a chat-only field (`n`, `stop`, `messages`, ...) | Ignored |
| any other field | An unknown chat field: kept, forwarded only when an allowlist names it ([Request](#request)) |

Errors name the Responses field (`max_output_tokens`, `text.format.name`, `tools[0].name`, `input[2].call_id`).

```json
{"id": "resp_...", "object": "response", "created_at": 1792400000, "status": "completed", "model": "nvidia/Nemotron-3-Ultra-550b-a55b",
 "output": [{"type": "message", "id": "msg_...", "status": "completed", "role": "assistant",
             "content": [{"type": "output_text", "text": "Refunds take 5 to 7 days.", "annotations": []}]}],
 "usage": {"input_tokens": 21, "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
           "output_tokens": 9, "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": 30},
 "instructions": "Be brief.", "store": false, "previous_response_id": null, "tool_choice": "auto", "tools": [], ...,
 "ratchet": {"served_by": "teacher", "task": "default", ...}}
```

`status` is `incomplete` with `incomplete_details.reason` `max_output_tokens` when the answer was cut off (`content_filter` when filtered). Tool calls in an answer become `function_call` items (`call_id`, `name`, `arguments`), which a client answers with a `function_call_output` item carrying the same `call_id`; a teacher answer that is a tool call follows the chat path's rules (`tool_calls_unsupported` until tool calls are returned). The settings the request sent are echoed as sent.

```python
resp = client.responses.create(model="ratchet", instructions="Be brief.", input="How long do refunds take?")
print(resp.output_text)
```

## Unsupported endpoints

`POST /v1/moderations` and the stored-response routes (`GET` and `DELETE /v1/responses/{id}`, `POST /v1/responses/{id}/cancel`, `GET /v1/responses/{id}/input_items`) answer 404 `unsupported_endpoint` after the key check, with a message that says why and links [OpenAI endpoints](#openai-endpoints), so a library that calls them fails with a clear reason (`openai.NotFoundError`, `e.code == "unsupported_endpoint"`). Any other path under `/v1/` is a 404 `unknown_url`.

## GET `/ratchet/stats`

Access level `dashboard` ([Access levels](#access-levels)): open in mock mode; in live mode an admin or dashboard key unless `RATCHET_PUBLIC_DASHBOARD=1`, and then an anonymous reader gets a scrubbed copy (client pseudonyms, also in `budget` events, no URLs or error messages in `events`, and no dollar caps: `clients[].daily_cap_usd`, `budget.daily_usd` and `budget.remaining_usd` are `null`, and a `budget` event names no amount).

```json
{
  "mode": "mock",
  "day": 9,
  "teacher": "nvidia/Nemotron-3-Ultra-550b-a55b",
  "student_base": "nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16",
  "fallback_student": "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
  "student_serving": {"kind": "mock", "model": "student-v5", "version": 5, "threshold": 0.76, "certified": true},
  "days": [
    {"day": 3, "requests": 1500, "student_share": 0.98, "escalation_rate": 0.02,
     "cost_per_1k": 0.16, "baseline_per_1k": 1.093, "savings_pct": 0.85,
     "avg_latency_ms": 352.0, "audit_agreement": 1.0, "degraded": 0, "fallback_teacher": 0,
     "p50_student_ms": 0.41, "p95_ms": 0.93}
  ],
  "versions": [
    {"id": 5, "day": 7, "base_model": "nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16", "adapter_uri": "student-v5",
     "train_examples": 1801, "dataset_hash": "…",
     "eval": {"golden_n": 201, "golden_agreement": 0.94, "threshold": 0.76, "coverage": 0.89,
              "precision": 0.99, "test_n": 100, "rechecked_day": 9},
     "state": "live", "traffic_pct": 100, "stage_since": 12034, "bom": "signed",
     "evidence": {"rule": "wilson", "promote_rule": "point", "alert_rule": "eprocess", "floor": 0.92,
                  "n": 61, "s": 60, "e_rollback": 0.074, "n_stage": 38, "s_stage": 37, "e_promote": 2.0,
                  "e_alert": 0.146, "thresholds": {"rollback": 20.0, "promote": 20.0, "alert": 100.0},
                  "cs": [0.879, 0.999], "wilson": [0.865, 0.995]}}
  ],
  "tasks": [
    {"task": "ticket-triage", "pinned": false, "requests": 12000, "student_share": 0.84, "escalation_rate": 0.06,
     "cost_usd": 3.21, "baseline_usd": 13.1, "savings_pct": 0.75, "avg_latency_ms": 640.0,
     "routes": {"student": 10080, "cold_start": 1100, "confidence": 720, "student_error": 60, "student_breaker": 40},
     "human_feedback": 40, "human_agreement": 0.925},
     "audits": 1008, "audit_agreement": 0.98, "threshold": 0.7412, "precision": 0.98, "coverage": 0.91,
     "calibrated": true, "disabled": null, "compare_keys": ["category", "priority"], "compare_source": "registry",
     "tool_requests": 0, "precision_kind": "exact", "judge_pass_rate": null},
    {"task": "research-plan", "pinned": true, "requests": 3, "student_share": 0.0, "escalation_rate": 0.0,
     "cost_usd": 0.0039, "baseline_usd": 0.0039, "savings_pct": 0.0, "avg_latency_ms": 2400.0,
     "routes": {"pinned": 3}, "human_feedback": 0, "human_agreement": null,
     "audits": 0, "audit_agreement": null, "threshold": null, "precision": null,
     "coverage": null, "calibrated": null, "disabled": null, "compare_keys": [], "compare_source": "registry",
     "tool_requests": 0, "precision_kind": null, "judge_pass_rate": null}
  ],
  "events": [{"id": 57, "ts": 1792400000.0, "day": 9, "kind": "live", "detail": "v5 promoted to 100% (audited precision 94.1%, n=17)"}],
  "totals": {"requests": 13500, "cost_usd": 5.21, "baseline_usd": 14.58, "saved_usd": 9.37, "savings_pct": 0.64,
             "failed_cost_usd": 0.0, "cost_model": "per_token"},
  "bill_of_materials": {"generated_at": 1792400000.0, "examples_sha256": "07ac2c…", "sources": [ … ], "label_sources": [ … ]},
  "clients": [
    {"client": "acme", "requests": 40, "student_share": 0.95, "cost_usd": 0.0044, "baseline_usd": 0.044, "savings_pct": 0.9,
     "end_users": 12, "stored_share": 0.05}
     "spent_today_usd": 0.0011, "daily_cap_usd": 5.0}
  ],
  "privacy": {"redaction": true, "examples": 2742, "redacted_examples": 12, "placeholders": 19,
              "not_stored_requests": 310, "not_stored_by_reason": {"request": 290, "end_user": 20},
              "no_train_end_users": 3, "no_train_unmatched": 0, "store_examples": true, "no_store_tasks": ["hr-notes"],
              "no_store_clients": [], "end_user_key": "env", "example_retention_days": 180,
              "request_retention_days": 400, "erasure_shared": "delete", "purged_examples": 61,
              "purged_by_reason": {"retention": 58, "erasure": 3}, "erasures": 2, "erasure_refusals": 1,
              "rolled_up_requests": 120400, "redaction_style": "label",
              "redaction_task_styles": {"invoice-extract": "numbered"}, "redaction_plugin": false},
  "security": {"api_keys": true, "daily_requests_per_key": 500, "bom_signing": true, "bom_key_id": "6cf6bc1a39c4491d",
               "key_limits": true, "ip_rpm": null, "max_inflight": 32},
  "budget": {"daily_usd": 20.0, "mode": "student_only", "spent_today_usd": 3.41, "remaining_usd": 16.59, "exceeded": false,
             "alert_pct": [50, 80, 100]},
  "cost": {"model": "per_token", "endpoint_hours": 216.0, "gpu_hours_savings_pct": -22.49,
           "break_even_student_requests_per_gpu_hour": 1582.0, "recommendation": "hosted_fallback"},
  "drift": {"enabled": true, "threshold": 100.0, "family_bound": 0.03, "alarms": 1, "streams": 3},
  "teacher_watch": {"enabled": true, "epoch": 1, "frozen": false, "checks": 273, "disagreements": 11,
                    "self_agreement": 0.9597, "ceiling": 0.9886, "e_value": 96.38, "contested": 0}
feedback": {"verdicts": 40, "correct": 37, "incorrect": 3, "corrections_sent": 3, "corrections_stored": 2,
               "human_examples": 152, "golden_only_examples": 120, "corrections": true,
               "by_version": [{"version": 5, "verdicts": 31, "correct": 29, "precision": 0.935, "lower": 0.793, "upper": 0.982}]}
  "errors": {"last_hour": {"student": 3, "auth": 1}},
  "sidecar": {"workers": 4, "max_queue": 1000, "audit_places": 100, "max_bytes": 67108864, "queued": 2, "running": 4,
              "bytes": 41210, "closed": false,
              "submitted": 5130, "done": 5124, "failed": 0, "full": 0, "refused": 0, "abandoned": 0,
              "by_version": [{"version": 5, "audits_inline": 0, "shadows_dropped": 0, "audits_abandoned": 0,
                              "shadows_abandoned": 0}]}
  "controls": {"student_disabled": false, "releases_frozen": false, "training_paused": false, "audit_rate": null,
               "pinned_tasks": []},
  "kpis": {"mode": "mock", "simulated": true, "window_days": 3, "window": {"from": 7, "to": 9, "days": 3, "complete": true},
           "metrics": {"SM-1": {"name": "Cost reduction vs all-teacher, steady state", "value": 0.84, "target": 0.7,
                                "op": ">=", "unit": "fraction", "met": true, "detail": {"…": "…"}}, "…": "…"},
           "summary": {"met": 5, "not_met": 1, "unknown": 0}}
}
```

- `fallback_student` (v0.5): the hosted, untuned model that answers student calls in live mode without a student endpoint (`RATCHET_FALLBACK_STUDENT`). A response whose `ratchet.served_by` is `student` and whose `model` is this id came from it, not from our adapter.
- `student_serving` (v0.5, FR-7.40): which model answers a student call now, so a page can name it. Read from settings and the versions table; it calls no model.

  | Key | Meaning |
  |---|---|
  | `kind` | `mock` (mock mode: the mock fleet serves each version's own adapter); `endpoint` (live, `RATCHET_STUDENT_BASE` set: our adapter on the student endpoint); `hosted_fallback` (live, no student endpoint, a version serves: every student call goes to `fallback_student`); `none` (no live or canary version: the teacher answers everything). Later versions may add kinds; show an unknown one as it is |
  | `model` | The model id student calls are sent to: the version's `adapter_uri`, or `fallback_student` for `hosted_fallback`; `null` without a version |
  | `version` | The live version's id; on a cold start whose first student is in canary, the canary's; `null` for none |
  | `threshold` | The confidence threshold that model is held to (`Ratchet.threshold`): the version's calibrated one, or `RATCHET_CONFIDENCE_THRESHOLD` for the hosted fallback, which was never calibrated; `null` without a version |
  | `certified` | The version's eval carries a certificate that certifies (`certificate.certified`), and our adapter serves. Always `false` for `hosted_fallback`: the certificate was computed for our adapter, never for the hosted model |

  While `kind` is `hosted_fallback`, the pages say "Nano 30B · hosted" for a student answer (with the threshold, and "not covered by the certificate"), stop saying "your model", and hide the live version's golden precision ([SCOUT.md](SCOUT.md#who-answered)).
- `days`: one entry per `requests.day` (see `Ledger.stats_by_day`), including days whose rows retention folded into daily sums (`RATCHET_REQUEST_RETENTION_DAYS`); every figure in `days`, `tasks`, `clients` and `totals` adds those sums back. `/v1/embeddings` calls (route `passthrough`) are left out of `days`, `totals` and `clients`, and counted in `tasks` (the `embeddings` row) and in `cost`. `audit_agreement` is `null` on days without audits. `degraded` and `fallback_teacher` count that day's requests a stand-in answered because the teacher was unavailable (the routes of the same names below). `avg_latency_ms` averages the providers' time (`requests.latency_ms`). `p50_student_ms` (the day's student-served requests) and `p95_ms` (all of them) are nearest-rank percentiles of `requests.wall_ms`, the whole cascade on the gateway's clock (v0.5, FR-12.8): the numbers NFR-1's target (p50 under 1 s for student-served requests) is checked against. Each is `null` for a day without such rows, such as days logged before v0.5. In mock mode the models answer at once, so these are Ratchet's own time, while `avg_latency_ms` is the simulated model latency.
- `tasks`: one entry per task name over all days, busiest first (`Ledger.stats_by_task`). `student_share` and `escalation_rate` are shares of that task's requests; `savings_pct = 1 − cost_usd / baseline_usd`. Pinned tasks always show `student_share` 0 and `savings_pct` 0. `pinned` is true for tasks that always go to the teacher and are never trained on (`research-plan`, `research-write`); the dashboard's **By task** table shows them as "pinned to teacher". `routes` counts the task's requests by how they were served (the ledger's `requests.route`), most frequent first, and adds up to `requests`:

  | Route | Served by | Why |
  |---|---|---|
  | `student` | student | Its confidence reached the version's threshold (and, for `n` > 1 choices, at least `n` samples agreed) |
  | `confidence` | teacher | The student was unsure (below the threshold) |
  | `cold_start` | teacher | No version serves yet (no live version, and the canary was not picked) |
  | `pinned` | teacher | A pinned task (by its spec, or at runtime by an operator: [Admin](#admin)); before v0.5, also a request with a part only the teacher reads (now route `ineligible:content_type`) |
  | `student_error` | teacher | The student call failed (timeout, connection error, an error status, a lost adapter) |
  | `student_breaker` | teacher | The student's circuit breaker was open, so the student was not called ([ARCHITECTURE.md](ARCHITECTURE.md#student-circuit-breaker)) |
  | `degraded` | student | The student was unsure and the teacher unavailable, so its own answer was served (`RATCHET_ON_TEACHER_FAILURE=serve_student`, `ratchet.degraded`); not audited, stored or shadowed ([ARCHITECTURE.md](ARCHITECTURE.md#when-the-teacher-is-down)) |
  | `fallback_teacher` | teacher | The teacher was unavailable (its retries failed or its breaker was open), so the fallback teacher answered (`RATCHET_TEACHER_FALLBACK_MODEL`); an escalation, a cold start, a pinned task or a teacher-only request |
  | `n_choices` | teacher | The student was confident, but fewer than the request's `n` samples gave its answer, so it could not fill `n` choices ([Choices and logprobs](#choices-and-logprobs)) |
  | `unknown_task` | teacher | The task is not registered and `RATCHET_UNKNOWN_TASKS=teacher`: the student was not called and nothing was stored ([Tasks](#tasks)) |
  | `student_disabled` | teacher | An operator turned the student off (`POST /ratchet/admin/student/disable`, [Admin](#admin)): no student or shadow call; the teacher's answer is stored as on a cold start |
  | `tools` | teacher | A tool request (tools offered, or tool calls or results in its messages): the teacher answered alone, with the tools, and nothing was stored ([Tools and function calling](#tools-and-function-calling)) |
  | `multimodal` | teacher | Written only by a caller of `Ratchet.teacher_only` that passes it, for a request with a content part only the teacher reads: the student was not called and nothing was stored. The gateway sends such a request through the cascade, which logs it `ineligible:content_type` (below) |
  | `task_disabled` | teacher | The serving version's per-task record lets the student answer none of this task (the release gate disabled it on that version, or its eval had no held-out row of it), or the task has critical classes the version's record did not measure under the current spec, or the hosted untuned model would answer it (FR-2.50), so the student was not called; never served in degraded mode ([per-task calibration](CONFIGURATION.md#per-task-calibration)) |
  | `rule:<i>` | teacher | The student was confident, but rule `i` (from 0) of the task's `escalate_when` held for its answer (`action: teacher`, or a confidence below the rule's `min_confidence`), so the teacher answered and its answer was stored as for any escalation; never served in degraded mode ([risk-aware routing](CONFIGURATION.md#risk-aware-routing)) |
  | `ineligible:input_tokens` | teacher | The request holds more input than the task's `student_max_input_tokens` (characters of its message text / 4), so no version was picked and the student was not called; stored only with the task's `store_ineligible`; not counted as an escalation |
  | `ineligible:content_type` | teacher | The request has a content part the student does not read (an image or other part admitted by `RATCHET_TEACHER_MODALITIES`; the student reads text only), so the student was not called and nothing was stored; not counted as an escalation |
  | `unrecorded` | either | Requests logged before v0.5, when the ledger had no route column |

  Later versions add routes; a client should show an unknown route as it is. `human_feedback` counts the task's requests that got a verdict ([Feedback](#feedback)), the latest per request, and `human_agreement` is the share of them judged `correct` (`null` without any).

  `tool_requests` counts the task's tool requests (FR-1.41), whatever their route (`tools`, `pinned`, `unknown_task`, `multimodal` or `fallback_teacher`); requests logged before the count existed count as none.

  Since FR-14.1 each entry also says how well the student does the task and how it is held to it ([per-task calibration](CONFIGURATION.md#per-task-calibration)). `audits` and `audit_agreement` cover the teacher's audits of the task's student answers over every version (`audit_agreement` is `null` without audits). The rest come from the live version's eval and are `null` without a live version and for a pinned task: `threshold` is the confidence the live student needs to answer the task, as the router applies it (its per-task threshold, 1.01 when the task is disabled, has no held-out rows, its spec changed since the version was scored or it has critical classes the version's record did not measure (FR-2.50), else the version's global threshold); `precision` and `coverage` are the task's held-out numbers measured when the version was scored, at the threshold it was calibrated to (for a disabled task, or one whose spec changed since, that measurement stays as it was while `threshold` is 1.01 and the student answers none of the task), `calibrated` whether the threshold is the task's own (enough calibration rows) or never below the global one, and `disabled` why the release gate disabled the task on the live version (a string) or `null`. These (all but `threshold`) are `null` when the live version has no per-task record for the task (scored before per-task calibration, or no golden rows of it). Since FR-14.40 two more come from the same record ([free-text tasks](CONFIGURATION.md#free-text-tasks)): `precision_kind` says what `precision` means, `exact` (agreement under comparators that decide it) or `proxy` (a task with a `similarity` field: the share of served answers close enough to the teacher's, not checked correct), and `judge_pass_rate` is the judge's pass rate on the answers the live version would serve on the task's held-out rows (free-text tasks scored with a judge only, else `null`). Both are `null` where the others are, and for a record written before FR-14.40.

  Since FR-14.2 `compare_keys` lists the paths of the fields the task's answers are compared on (empty for a task that compares whole answers, such as `default`), and `compare_source` says where they come from: `schema` for an auto task derived from a `response_format` schema ([Structured outputs](#structured-outputs)), `registry` for a built-in, file or API task, `null` for a name that is not registered.
- `versions`: every version, oldest first. `eval` is the version's latest golden eval (the nightly re-score's for the live version); since v0.5 it also has `certificate`, `frontier`, `per_task`, `test_vector` and, for a candidate trained while a version was live, `paired` ([Certificates](#get-ratchetversionsidcertificate)), and its `threshold` is exact rather than rounded. `eval` is its golden evaluation (since FR-14.1 with one record per task in `eval.tasks`, [per-task calibration](CONFIGURATION.md#per-task-calibration)). A task with critical classes (FR-2.50, [risk-aware routing](CONFIGURATION.md#risk-aware-routing)) has `critical` in its record: one entry per class, in the spec's order, with the condition as written (`field` and `equals` or `in`), `max_miss_rate`, `n` (its held-out rows whose teacher reference is in the class), `misses` (those the student would serve with an answer outside it, at the threshold the record stores and past its rules), `miss_rate` (`null` when `n` is 0) and `upper_bound` (the 95% Wilson upper bound, 1.0 when `n` is 0). `excluded_ineligible`, when present, counts golden rows left out because the task's routing block keeps such a request from the student. For a task with `escalate_when` rules, a record's `precision` and `coverage` (and the pooled ones) count only the answers no rule sends to the teacher. `other_tasks`, present only on an eval scored with `RATCHET_PER_TASK_CALIBRATION=0` that has records (the tasks with critical classes), holds the other tasks' held-out rows (`test_n`) and how many of them the pooled threshold serves (`served`). `bom` is `signed`, `unsigned`, or `null` for versions trained before v0.4. `evidence` (v0.5) is what the release gate reads for the version in `canary` or `live` state, recomputed from its audit counts on every call (`ReleaseGate.evidence`, [ADR 0025](adr/0025-anytime-valid-release-gate.md)); `null` for every other state. Anyone can re-check a gate decision from it:

  | Key | Meaning |
  |---|---|
  | `rule` | The canary rule in force: `wilson` (the default) or `sequential` (`RATCHET_SEQUENTIAL=1`) |
  | `promote_rule` | `point` (point estimate at or above the floor) or `evidence` (`RATCHET_PROMOTE_EVIDENCE=1`) |
  | `alert_rule` | The live alert's rule: `eprocess` (the default) or `wilson` |
  | `floor` | `RATCHET_LIVE_FLOOR` |
  | `n`, `s` | Audits of answers the version served over its whole life, and how many agreed with the teacher |
  | `n_stage`, `s_stage` | The same in its current stage (for a live version: since it went live) |
  | `e_rollback` | e-value against "precision ≥ floor" over its life: the sequential rule rolls back at `thresholds.rollback` or more |
  | `e_alert` | The same over the current stage: the live alert fires at `thresholds.alert` or more |
  | `e_promote` | e-value against "precision ≤ floor" over the current stage: evidence-based promotion needs `thresholds.promote` |
  | `thresholds` | 1/alpha for each: `rollback` (`RATCHET_ALPHA_ROLLBACK`), `promote` (`RATCHET_ALPHA_PROMOTE`), `alert` (`RATCHET_ALPHA_ALERT`) |
  | `cs` | Anytime-valid 95% confidence sequence for precision over its life, on a 0.001 grid |
  | `wilson` | 95% Wilson interval of the current stage (what the Wilson rule reads; valid for one look only) |

  e-values are finite (capped at e^690). With no audits `n` is 0, the e-values are 1 and both intervals are `[0, 1]`.
- `clients`: one entry per API key name that sent requests, busiest first (`Ledger.stats_by_client`). Empty when keys are off. `end_users` is how many distinct end users its requests named (by hash; only rows still kept count, since folded rows keep no end user); `stored_share` the share of its requests whose teacher answer was stored or matched a stored example (`null` when no request recorded it, as before v0.5). In a scrubbed copy `client` is a pseudonym. Since v0.5 each entry also has `spent_today_usd`, the key's chat-request dollars since 00:00 UTC (failed requests' spend included), and `daily_cap_usd`, its daily US dollar budget (`null` when it has none, when the name is no longer a key, and in a scrubbed copy; [Limits and headers](#limits-and-headers)).
- `privacy`: whether redaction is on, how many stored examples had at least one identifier replaced, and how many placeholders were inserted in total (`Ledger.privacy_stats`); how many requests were under a storage opt-out, in all and by reason (`not_stored_requests`, `not_stored_by_reason`; `Ledger.opt_out_stats`), how many end users are registered as no-train (`no_train_end_users`) and how many of those registrations match no request under the current keys (`no_train_unmatched`: scoped to a key name no longer in `RATCHET_API_KEYS`, or keyless while keys are on); and the opt-outs in force: `store_examples` (false under zero retention), `no_store_tasks` (`RATCHET_NO_STORE_TASKS` and tasks whose spec says `store: false`), `no_store_clients` (keys with `store=0`) and where the end-user key comes from (`env` for `RATCHET_END_USER_KEY`, `ledger` for the generated one); the retention windows in force (`example_retention_days`, `request_retention_days`, 0 = forever) and `erasure_shared`; examples whose text was purged, in all and by reason (`purged_examples`, `purged_by_reason`: `retention`, `erasure`), erasures recorded, erased examples offered again and refused (`erasure_refusals`), and request rows folded into daily sums (`rolled_up_requests`; `Ledger.retention_stats`); how identifiers are written in stored examples (`redaction_style`: `label`, `numbered` or `surrogate`, from `RATCHET_REDACT_STYLE`; `redaction_task_styles`: the tasks whose spec overrides it; `redaction_plugin`: whether a detector plugin runs, never its name; [ADR 0082](adr/0082-placeholder-styles-detector-plugin-and-indian-identifiers.md)). `examples` counts the examples that still have their text. Counts and names only: no end-user hash is ever shown.
- `security`: whether API keys are required, the daily quota per key (`null` = none), and whether adapter BOMs are signed (with the key id: the first 16 hex characters of the public key's SHA-256). Since v0.5 also whether admin keys and dashboard keys are set (`admin_keys`, `dashboard_keys`), whether the dashboard API is public (`public_dashboard`), and whether this payload is a scrubbed copy (`scrubbed`: client names are pseudonyms `client-1`, ..., and event details carry no URL or error message; [Access levels](#access-levels)); whether any key has a limit of its own (`key_limits`), the per-IP rate of a gateway without keys (`ip_rpm`, `null` when off) and the in-flight cap (`max_inflight`, `null` when off).
- `budget` (v0.5): the gateway's own daily budget (`daily_usd`, `null` when `RATCHET_DAILY_BUDGET_USD` is 0), what happens past it (`mode`: `block` or `student_only`), what chat requests spent since 00:00 UTC (`spent_today_usd`; Scout is not counted), what is left (`remaining_usd`, `null` without a budget), whether it is spent (`exceeded`) and the shares that write a `budget` event (`alert_pct`). A scrubbed copy leaves the operator's budget out (`daily_usd` and `remaining_usd` are `null`), as the gateway's 429s do.
- `events`: the last 40 events, oldest first. `id` is the ledger row id (stable, use it as a key); `ts` is Unix time.
- `totals.cost_model` is `per_token`: `totals`, `days`, `tasks` and `clients` price the student per token (the hosted Nano price), as every figure since v0.1. The GPU-hour view is in `cost`.
- `feedback`: human verdicts and labels ([Feedback](#feedback), `Ledger.feedback_stats`): verdicts in all, `correct` and `incorrect`, corrections sent and stored, examples that still have their text whose label a person wrote (`human_examples`, `verified` = 1: corrections and human imports) and golden-only examples (`golden_only_examples`), whether corrections may be stored (`corrections`, `RATCHET_FEEDBACK_CORRECTIONS`), and per version the human precision of the answers it served itself (route `student`) with its 95% Wilson bounds (`by_version`). Reported and alerted on (`human_alert`), never used to promote or roll back. Counts only, never a request id.
- `totals.failed_cost_usd` (v0.5): what requests that failed spent without leaving a request row, such as the student's samples of an escalation whose teacher call then failed (NFR-3). It counts against the savings: `saved_usd = baseline_usd − cost_usd − failed_cost_usd` and `savings_pct = 1 − (cost_usd + failed_cost_usd) / baseline_usd`. Without failures both are what they were before. `days`, `tasks` and `clients` leave it out.
- `errors.last_hour` (v0.5): the failures of the last hour by stage (`failures.stage`: `teacher`, `student`, `audit`, `shadow`, `quota`, `auth`, `budget`, `rate`, `upstream`, `gateway`), busiest first; `{}` when nothing failed. A failed student call the teacher covered counts here too, although the client got its answer. [RUNBOOK.md](RUNBOOK.md#failures-and-request-ids) explains each stage.
- `sidecar` (v0.5, [Off-path checks](#off-path-checks)): the queue that runs audits and shadows after the response. `workers` (0: every check runs before the response, mock mode's default), `max_queue`, `audit_places` (the places only a job with an audit may take) and `max_bytes` (the request text queued and running jobs may hold) are the settings; `queued` (places taken, jobs waiting included), `running` and `bytes` (the request text their jobs hold) are now; since start, `submitted`, `done`, `failed` (a job that raised; logged by class), `full` (a place refused because the queue or its byte budget was full), `refused` (a job handed over after shutdown began, which its request ran itself) and `abandoned` (still queued when the shutdown's drain ended: those rows stay pending). `by_version` lists every routed version, and any other version that lost something, with what could not go through the queue: `audits_inline` (drawn audits that ran before the response because the queue was full; an audit is never dropped), `shadows_dropped` (shadows that did not run for that reason), and `audits_abandoned` and `shadows_abandoned` (given up at shutdown). Counts are per process: a restart starts them at 0.
- `controls` (v0.5): the operator's runtime switches ([Admin](#admin)): whether the student is turned off (`student_disabled`), releases are frozen (`releases_frozen`) and training is paused (`training_paused`), the runtime audit rate (`audit_rate`, `null` while `RATCHET_AUDIT_RATE` applies) and the tasks pinned to the teacher at runtime (`pinned_tasks`, sorted; such a task also shows `pinned: true` under `tasks`).
- `drift` (v0.5): the [drift monitor](#get-ratchetdrift)'s summary: whether it is on (`RATCHET_DRIFT_ALARM` above 0), its threshold, the chance that any stream it holds ever alarms on unchanged traffic (`family_bound`, K / threshold), how many streams alarmed (`alarms`) and how many it holds (`streams`, a count; the list is in `GET /ratchet/drift`). Off: `enabled` false, `threshold` and `family_bound` `null`, both counts 0.
- `teacher_watch` (v0.5): the [teacher watch](#teacher-watch)'s summary: whether it acts (`RATCHET_TEACHER_WATCH`), the check epoch, whether Night School is frozen, the epoch's checks and disagreements, the teacher's self-agreement and the agreement ceiling (`null` without checks), the freeze rule's e-value and the contested labels. The same keys as `GET /ratchet/teacher`, without the per-source and per-task rows.
- `cost`: the [cost report](#cost)'s headline numbers for all days: the headline model (`RATCHET_STUDENT_COST_MODEL`), the student endpoint's recorded hours, the savings share by GPU-hours paid (negative when self-hosting does not pay; `null` without a baseline), the break-even volume (`null` until the student has served) and the recommendation.
- `kpis`: the [success metrics](#get-ratchetkpisdaysn) over the last `RATCHET_KPI_WINDOW_DAYS` days with traffic, exactly as `GET /ratchet/kpis` returns them.

Compatibility: the keys the dashboard reads are listed with their JSON types in `tests/fixtures/stats_keys.json`, and `test_stats_contract` checks them on every run. New versions add keys; they do not remove or retype existing ones, so a client written against this page keeps working.

Freshness: in live mode the payload is computed at most once every `RATCHET_STATS_TTL_S` seconds (default 2) by one request at a time, and every other read in between gets that payload; in mock mode (default 0) every read computes it. On a ledger file it is read from a read-only connection, so a dashboard never waits for requests nor slows them ([ARCHITECTURE §11](ARCHITECTURE.md#11-hot-path-and-performance)).
## GET `/ratchet/kpis?days=N`

## Request lookups

What the ledger knows about one request, by the id the gateway gave it ([Request ids](#request-ids)): its `requests` row (metadata only; the table holds no prompt or answer text) and its `failures` rows. Either id form works: `req_<32 hex>` or the completion's `chatcmpl-<32 hex>`.

```json
{
  "request_id": "req_3f1c0a9b8e7d6c5b4a3f2e1d0c9b8a79",
  "request": {"id": 1204, "ts": 1792400000.1, "day": 9, "task": "ticket-triage", "served_by": "teacher",
              "student_version": 5, "confidence": 0.41, "escalated": 1, "audited": 0, "route": "confidence",
              "latency_ms": 2711.0, "wall_ms": 2713.2, "cost_usd": 0.00133, "baseline_cost_usd": 0.00131,
              "client": "acme", "client_request_id": "order-77-retry-1", "request_id": "req_3f1c0a9b8e7d6c5b4a3f2e1d0c9b8a79",
              "...": "every other column of the requests table"},
  "failures": [{"id": 88, "ts": 1792400000.1, "day": 9, "request_id": "req_3f1c0a9b8e7d6c5b4a3f2e1d0c9b8a79",
                "client": "acme", "task": "ticket-triage", "stage": "student", "error": "UpstreamError 504 upstream_timeout",
                "status": 504, "latency_ms": 15002.4, "cost_usd": 0.0}]
}
```

`request` is `null` for a request that failed before the cascade logged it (a teacher error after the student's samples, a quota 429, an unhandled error); then its `failures` row says where it failed, what it spent (`cost_usd`) and the status (`status`: the status of the stage that failed; for an upstream stage the upstream's, such as 504 for a timeout or 503 for an open breaker). `failures` also lists the optional calls that failed around an answered request: a student call the teacher covered, a failed audit, shadow sample or arbitration. `error` is the exception's class, status and code (`UpstreamError 502 upstream_unreachable`, `APIError 429 daily_quota_exceeded`), never a message. A failed request's rows are written before its response starts, so a lookup made as soon as the error arrives finds them; under a flood the gateway keeps at most 120 of its own rows a minute per stage (a row that carries spent money is always kept), so the id of a refusal past that is a 404. A request that is not found, and an id that is not one of the two forms, is a 404.

### GET `/ratchet/requests/{request_id}`

Access level `dashboard`: any request, whoever sent it. A scrubbed read ([Access levels](#access-levels)) gets the `client` names as pseudonyms. 404 `{"detail": "no request has this id"}` when there is nothing to show.

### GET `/v1/ratchet/requests/{request_id}`

Access level `api`: the caller's own requests only. With `RATCHET_API_KEYS` set, `request` and `failures` keep only what this key sent, and an id another key sent is a 404 `not_found_error` with code `request_not_found`, the same answer as for an id that never existed, so a key learns nothing about other clients. With keys off, it finds the requests sent without a key.

```python
import httpx

r = httpx.get(f"{BASE}/v1/ratchet/requests/{completion.id}", headers={"Authorization": f"Bearer {KEY}"})
print(r.json()["request"]["route"])  # student, confidence, cold_start, ...
```

## GET `/metrics`

Prometheus metrics in the text exposition format 0.0.4 (`Content-Type: text/plain; version=0.0.4; charset=utf-8`), from `ratchet/metrics.py` ([ADR 0062](adr/0062-request-ids-failures-logs-and-metrics.md)). Access level `metrics`: an admin or dashboard key, or `RATCHET_METRICS_TOKEN` as the bearer token (Prometheus' `authorization: {credentials: ...}` or `bearer_token`), or anyone when the dashboard is public (an anonymous scrape of a public live dashboard gets client pseudonyms in the `client` labels).

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `ratchet_requests_total` | counter | `task`, `served_by`, `route`, `status` | Requests. The cascade counts each answered one with `status="200"` (chat requests, and Scout's agents, which call it in-process); the gateway counts each chat request that ended in an error, with `served_by="none"`, `route="none"` and its status. A replay by `Idempotency-Key` ran no cascade and is counted in `ratchet_idempotent_requests_total` only |
| `ratchet_request_seconds` | histogram | `served_by` | Wall-clock time per request (`requests.wall_ms` for answered ones; `none`: until the error) |
| `ratchet_upstream_requests_total` | counter | `upstream`, `outcome` | Model calls: `upstream` is `teacher`, `fallback_teacher`, `student`, `shadow`, `audit` or `arbitration`; `outcome` is `ok`, `error`, `timeout` or `circuit_open` (refused by the breaker, no call made) |
| `ratchet_upstream_seconds` | histogram | `upstream` | Time per model call made, the provider's retries included |
| `ratchet_cost_usd_total` | counter | `client` | US dollars spent, failed requests' spend included (per-token prices; simulated in mock mode) |
| `ratchet_failed_cost_usd_total` | counter | `client` | What requests that failed without an answer spent (the student's samples before a teacher failure); also in `ratchet_cost_usd_total` and in `/ratchet/stats`' `totals.failed_cost_usd` |
| `ratchet_baseline_usd_total` | counter | `client` | What the answered requests would have cost with the teacher alone |
| `ratchet_escalations_total` | counter | `task` | Requests the student tried and the teacher answered |
| `ratchet_audits_total` | counter | `result` | Teacher audits of served student answers: `agree`, `disagree`, `invalid` (the teacher's answer measured nothing), `error`, `circuit_open` |
| `ratchet_examples_stored_total` | counter | `task`, `split` | New training examples (`train` or `golden`); a duplicate's `seen` bumped is not one |
| `ratchet_failures_total` | counter | `stage` | Failures by stage (`failures.stage`), every one: the table samples breaker refusals 1 in 10 and throttles the gateway's rows, this counter does not |
| `ratchet_rejections_total` | counter | `reason` | Chat requests refused before the cascade: `auth`, `invalid_request`, `unknown_task`, `task_not_allowed`, `quota`, `budget` (a key's or the gateway's dollar budget), `rate` (a key's or an address's rate or in-flight limit), `overloaded` (the in-flight cap's 503), `too_large`, `disconnected`, `forbidden`, `idempotency` (a malformed or reused `Idempotency-Key`, or the 409 while its first request runs), `other` |
| `ratchet_idempotent_requests_total` | counter | `outcome` | Chat requests with an `Idempotency-Key` ([Idempotency and retries](#idempotency-and-retries)): `first` (ran, answer kept), `uncached` (ran, answer not kept), `failed` (ran and failed; the key is released), `replayed` (sent from the cache), `joined` (waited for the first and got its answer), `reused` (400), `in_flight` (409), `invalid` (400) |
| `ratchet_seen_skipped_total` | counter | `reason` | Repeats of a stored example not counted in its `seen`: `retry` (`x-stainless-retry-count` above 0) or `window` (the same API client within `RATCHET_SEEN_DEDUP_S`) |
| `ratchet_sidecar_jobs_total` | counter | `outcome` | Off-path jobs (a request's audit, shadow samples and arbitration; [Off-path checks](#off-path-checks)): `queued`, `done`, `failed` (the job raised; logged by class), `abandoned` (still queued when the shutdown's drain ended) |
| `ratchet_sidecar_full_total` | counter | `outcome` | Requests whose off-path work found the queue full: `audit_inline` (the drawn audit ran before the response) or `shadow_dropped` (the shadow did not run for this request) |
| `ratchet_sidecar_job_seconds` | histogram | | Time from a job's queueing to its end: how long a row's checks stay pending |
| `ratchet_sidecar_jobs` | gauge | `state` | Off-path jobs `queued` (places taken, waiting jobs included) and `running`, at scrape time |
| `ratchet_circuit_open` | gauge | `breaker` | 1 while a breaker (`student`, `shadow`, `teacher`, `fallback_teacher`, ...) is open or half-open, else 0 |
| `ratchet_version_traffic_pct` | gauge | `version`, `state` | Traffic share of the live, canary and shadow versions (`v5`, `live`, 100) |
| `ratchet_spend_today_usd` | gauge | `client` | US dollars spent since 00:00 UTC, failed requests included |
| `ratchet_inflight_requests` | gauge | | Chat requests in flight at the scrape; `RATCHET_MAX_INFLIGHT` refuses past it with a 503 ([Limits and headers](#limits-and-headers)) |
| `ratchet_info` | gauge | `version`, `mode` | Always 1 |

Label values stay a small set: `task` is a registered task's name, `other` for a name the registry does not hold, or `none` before a request named one; `client` is a `RATCHET_API_KEYS` name, `none` (no key: an open gateway, Scout) or `other`. Counters count from the start of the process (a demo reset keeps them); use `rate()` or `increase()`. Validation 400s are counted here (`ratchet_requests_total{status="400"}`, `ratchet_rejections_total`) and nowhere else. Alert examples are in [RUNBOOK.md](RUNBOOK.md#alerts).

```yaml
scrape_configs:
  - job_name: ratchet
    metrics_path: /metrics
    authorization: {credentials_file: /etc/prometheus/ratchet_token}   # RATCHET_METRICS_TOKEN
    static_configs: [{targets: ["ratchet:8000"]}]
```


PRD §8.1's success metrics (SM-1 to SM-5) and NFR-1's latency target, computed from the ledger by `ratchet/kpis.py` the same way as in the simulator and in CI ([ADR 0083](adr/0083-success-metrics-from-the-ledger.md)). Access level `dashboard`, like `/ratchet/stats`, whose `kpis` block is this report. SM-1 to SM-3 and NFR-1 cover the last `days` ledger days with traffic (1 to 3,650, default `RATCHET_KPI_WINDOW_DAYS`, 3; anything else is **400**, a non-number **422**); SM-4 and SM-5 cover the whole history. An example from the mock demo (simulation numbers from mock models, `simulated: true`):

```json
{
  "mode": "mock",
  "simulated": true,
  "window_days": 3,
  "window": {"from": 8, "to": 10, "days": 3, "complete": true},
  "metrics": {
    "SM-1": {"name": "Cost reduction vs all-teacher, steady state", "value": 0.845, "target": 0.7, "op": ">=",
             "unit": "fraction", "met": true,
             "detail": {"cost_usd": 1.018, "failed_cost_usd": 0.0, "baseline_usd": 6.559, "requests": 6000,
                        "cost_model": "per_token"}},
    "SM-2": {"name": "Audited precision of student-served answers", "value": 0.985, "target": 0.95, "op": ">=",
             "unit": "fraction", "met": true,
             "detail": {"audits": 585, "agree": 576, "lower": 0.971, "upper": 0.992, "min_audits": 20}},
    "SM-3": {"name": "Student share of traffic, steady state", "value": 0.969, "target": 0.8, "op": ">=",
             "unit": "fraction", "met": true, "detail": {"requests": 6000, "student_served": 5814}},
    "SM-4": {"name": "Bad deploys reaching 100% traffic", "value": 0, "target": 0, "op": "==", "unit": "versions",
             "met": true, "detail": {"reached_full_traffic": 4, "bad_versions": []}},
    "SM-5": {"name": "Time to recover from drift (escalations back to baseline)", "value": 4, "target": 3, "op": "<=",
             "unit": "nights", "met": false,
             "detail": {"days_measured": 9,
                        "episodes": [{"start": 5, "end": 9, "nights": 4, "baseline": 0.022, "peak": 0.1405}]}},
    "NFR-1": {"name": "Median latency of student-served requests", "value": 327.1, "target": 1000.0, "op": "<",
              "unit": "ms", "met": true,
              "detail": {"requests": 5814, "column": "latency_ms", "quantile": 0.5, "served_by": "student"}}
  },
  "summary": {"met": 5, "not_met": 1, "unknown": 0}
}
```

- Every metric has `name`, `value`, `target`, `op` (`>=`, `<=`, `==`, `<`), `unit` (`fraction` for a share, else `versions`, `nights` or `ms`), `met` and `detail`. `met` is `null` when there is not enough data to say, with a `detail.reason`: no traffic in the window, fewer audits than `RATCHET_MIN_AUDITS` (SM-2), no version at 100% yet (SM-4), fewer than four days on which the student judged at least 50 requests, or a drift episode still open within its target (SM-5). `value` is `null` when there is nothing to measure, and for SM-5 while an episode is open (`detail.open_nights` counts it so far).
- `simulated` is true in mock mode, where every number comes from mock models. `window` gives the first and last day used and whether the ledger had `window_days` days with traffic (`complete`).
- **SM-1** is `1 - (cost_usd + failed_cost_usd) / baseline_usd`: per token, as `totals`; `failed_cost_usd` is what failed requests spent without leaving a request row (0 on a ledger without a `failures` table). **SM-2** pools every audit in the window, every version together, with a 95% Wilson interval (`lower`, `upper`); it is met on the point estimate. **SM-3** is the student's share of the window's requests.
- **SM-4** counts versions that reached 100% of traffic (a `live` event, or live now) and were later named by an `alert` or a `rollback`, or are `rolled_back` now (`bad_versions`, each with its evidence and day). A canary rolled back before 100% does not count.
- **SM-5** is the longest drift episode in nights over the whole history. A day's escalation rate is the share of the requests the student judged (its samples came back) that it escalated; an episode starts on a day at least 5 points above the lowest of the 3 measured days before it and ends on the first later day at most 2 points above that baseline.
- **NFR-1** is the nearest-rank median latency of the window's student-served requests, from `requests.wall_ms` when the ledger records it, else `latency_ms` (`column`). Folded request rows keep no latencies.
- New metrics may be added under `metrics`; a client should show an unknown one as it is.

## GET `/ratchet/bom`

Access level `bom`: as `/ratchet/stats`, and public with `RATCHET_PUBLIC_BOM=1`.

```json
{
  "generated_at": 1792400000.0,
  "examples_sha256": "07ac2ca35bac78959378cbabd98352540…",
  "sources": [
    {"teacher_model": "nvidia/Nemotron-3-Ultra-550b-a55b", "license": "OpenMDW-1.1",
     "license_url": "https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Ultra-550B-A55B-BF16",
     "split": "train", "n": 2085}
  ],
  "label_sources": [
    {"teacher_model": "human", "license": "customer-provided labels", "license_url": "", "split": "golden", "n": 120}
  ],
  "purged_examples": 61
}
```

`examples_sha256` is a SHA-256 over the hashes of the examples that still have their text, in sorted order, and `sources` counts those written by a teacher; `label_sources` counts those whose label a person wrote (provenance `human`: feedback corrections and imports, [ADR 0091](adr/0091-human-labels-as-a-provenance-source.md)), listed apart because they are not a teacher's; `purged_examples` counts the ones whose text a retention window or an erasure removed ([Erasure and export](#erasure-and-export)), so a purge changes the fingerprint. Two ledgers with the same kept examples give the same fingerprint. Each adapter's own BOM (below) is never changed.

## GET `/ratchet/versions/{id}/bom`

Access level `bom`, like `/ratchet/bom`. The bill of materials of one adapter, written by Night School right after training and evaluation, from the exact rows it was trained and evaluated on. **404** for an unknown id or a version trained before v0.4.

```json
{
  "manifest": {
    "schema": "ratchet.bom/v2",
    "generator": "ratchet 0.4.0",
    "created_at": "2026-10-20T00:15:12Z",
    "adapter": {"version": 1, "name": "student-v1", "base_model": "nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16", "day": 1},
    "training": {"examples": 1029, "dataset_sha256": "478eef…", "tasks": {"ticket-triage": 420, "source-triage": 362, "page-read": 247},
                 "targets": "teacher answers only", "examples_with_pii_redacted": 0, "origins": {"live": 1029},
                 "commitment": {"alg": "ratchet.smt/v1", "root": "e8ab25…", "size": 1029}},
    "golden": {"examples": 110, "sha256": "cbce0e…", "held_out": true, "human_labels": 0,
               "commitment": {"alg": "ratchet.smt/v1", "root": "1f6b2a…", "size": 110}},
    "sources": [{"teacher": "nvidia/Nemotron-3-Ultra-550b-a55b", "license": "OpenMDW-1.1",
                 "license_url": "https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Ultra-550B-A55B-BF16",
                 "license_origin": "builtin", "distill_ok": true, "examples": 1029}],
    "label_sources": [],
    "base_model_license": {"name": "NVIDIA Nemotron Open Model License",
                           "url": "https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-nemotron-open-model-license/"},
    "notices": [{"file": "NOTICE.md", "sha256": "5d0c3a…", "text": "# NOTICE\n\nThis folder holds a LoRA adapter…"}],
    "redactor": {"sha256": "7100b2…", "rules": 11, "redact_pii": true},
    "eval": {"golden_n": 110, "golden_agreement": 0.89, "threshold": 0.65, "coverage": 0.96, "precision": 0.94, "test_n": 55}
  },
  "sha256": "9b1f…",
  "signature": {"alg": "Ed25519", "key_id": "6cf6bc1a39c4491d", "public_key": "IiNlIP8Y…", "value": "k2Hc…"}
}
```

- `sha256` and the signature cover the **canonical** manifest: JSON with sorted keys, no whitespace, UTF-8 (`ratchet.bom.canonical`).
- `signature` is `null` when `RATCHET_BOM_SIGNING_KEY` is not set; the `sha256` still detects edits.
- `training.dataset_sha256` equals the version's `dataset_hash`; `golden.sha256` fingerprints the golden rows the evaluation used. The `eval` block is the evaluation at training time; later re-checks update `/ratchet/stats` but not the signed BOM. Since v0.5 it also carries the certificate, the frontier, the per-task bounds, the per-row outcomes (`test_vector`) and, for a candidate trained while a version was live, the paired comparison with it ([Certificates](#get-ratchetversionsidcertificate)); `python -m ratchet.stats check bom.json` recomputes every one of those numbers from the signed counts.
- Schema `ratchet.bom/v2` ([ADR 0024](adr/0024-evidence-foundations.md)) keeps every v1 field and adds `training.origins` (training rows by where they came from; `live` for rows that did not record it) and `redactor` (a SHA-256 over the redaction rules, the number of rules, and whether redaction was on; `null` when the writer did not say). Later features add optional sections: `teacher`, `backfill_jobs` and `erasures_honored`. Adapters trained before keep their v1 documents, which still verify; any other schema is refused as unknown.
- `training.commitment` and `golden.commitment` (since v0.5, [ADR 0027](adr/0027-committed-training-sets.md)): `{"alg": "ratchet.smt/v1", "root", "size"}`, the root of a sparse Merkle tree over the split's examples (each example's hash mapped to a hash of its label, teacher, license and redaction count) and how many there are. `size` always equals `examples`. A single example can be proved in or out of either set against these roots ([Committed training sets](#committed-training-sets)). In live mode a copy of the document is also written into the adapter's folder (`adapters/<name>/ratchet_bom.json`, with its `NOTICE.md`), so the weights carry their provenance.
- The licenses travel with the adapter (NFR-9, [ADR 0090](adr/0090-licenses-travel-with-the-adapter.md)): `base_model_license` is the license of the base model the adapter runs on (`{name, url}` from `ratchet.licenses.BASE_MODELS`; for a base model Ratchet does not know, `name` and `url` are `null` and `warning` says to find and ship its license); `notices` holds the exact `NOTICE.md` that goes next to the adapter weights (the base model's license, each teacher's license and URL, teacher outputs only, the Ratchet version) with its SHA-256, so the file can be checked against the signed BOM; each source's `license_origin` is `builtin` (Ratchet's registry) or `config` (added by the operator in `RATCHET_TEACHER_LICENSES`). v2 documents written before these fields still verify. `python -m ratchet.live export --version N --out DIR` writes the adapter folder with this BOM and its `NOTICE.md` ([DEPLOYMENT.md](DEPLOYMENT.md#export-an-adapter-with-its-licenses)).
- Human labels are not teachers ([ADR 0091](adr/0091-human-labels-as-a-provenance-source.md)): `sources` lists teachers only, and `label_sources` lists the training rows whose label a person wrote (`[{"source": "human", "license": "customer-provided labels", "note", "distill_ok": true, "examples", "origins": {"feedback": n, "import": n}}]`; empty without any). With human labels among the targets, `training.targets` is `teacher answers and customer-attested human labels` and the NOTICE gets a **Human labels** section; an adapter without them gets the same text as before. `golden.human_labels` counts golden rows whose reference a person wrote (a human-verified golden set). v2 documents written before these fields still verify.

Verify a downloaded BOM against the public key you published:

```bash
curl -s https://<gateway>/ratchet/versions/3/bom > bom.json
python -m ratchet.bom verify bom.json --public-key "<base64 public key>"
# OK: signature valid (Ed25519, key 6cf6bc1a39c4491d, trusted key)
python -m ratchet.bom verify bom.json --public-key "<base64 public key>" --notice NOTICE.md   # also the adapter's NOTICE.md
# OK: NOTICE.md matches the BOM
```

Without `--public-key` the check uses the key embedded in the document, which only proves the document is consistent with itself.

## GET `/ratchet/versions/{id}/certificate`

What a version's golden evals certify about the answers it serves (FR-16.3, [ADR 0026](adr/0026-certified-thresholds-and-a-paired-release-gate.md)). The threshold is chosen on the even half of the golden rows and measured on the odd half (INV-6), so the odd half's counts at that threshold are one exact binomial test: with probability at least 1 − `delta`, the precision of what the version serves at its threshold is at least `lcb`. **404** for an unknown version. Public, like `/ratchet/stats`.

```json
{
  "version": 3,
  "state": "live",
  "status": "certified",
  "training": {
    "certificate": {"method": "clopper-pearson", "calibration": "point", "delta": 0.05, "target": 0.95,
                    "threshold": 0.5, "served": 119, "errors": 1, "precision": 0.9916, "lcb": 0.9608, "certified": true},
    "frontier": [
      {"threshold": 0.95, "coverage": 0.776, "served": 97, "errors": 0, "precision": 1.0, "lcb": 0.9468, "delta": 0.005},
      {"threshold": 0.9, "coverage": 0.824, "served": 103, "errors": 0, "precision": 1.0, "lcb": 0.9499, "delta": 0.005}
    ],
    "per_task": {
      "page-read": {"n_test": 31, "served": 29, "errors": 0, "precision": 1.0, "lcb": 0.9019, "coverage": 0.9355},
      "source-triage": {"n_test": 42, "served": 38, "errors": 0, "precision": 1.0, "lcb": 0.9242, "coverage": 0.9048},
      "ticket-triage": {"n_test": 52, "served": 52, "errors": 1, "precision": 0.9808, "lcb": 0.912, "coverage": 1.0}
    },
    "threshold": 0.5,
    "calibration": "point",
    "paired": {"n": 125, "cov_only_live": 5, "cov_only_cand": 8, "p_cov": 0.8666, "neg_flips": 1, "pos_flips": 3,
               "p_err": 0.9375, "neg_flips_served": 1, "live_served_ok": 113, "nfr": 0.0088,
               "nfr_95": [0.0016, 0.0484], "against": 2}
  },
  "current": {"certificate": {"…": "the same keys"}, "frontier": ["…"], "per_task": {}, "threshold": 0.5,
              "calibration": "point", "paired": null, "rechecked_day": 5},
  "bom": {"sha256": "dd71bf42…", "signed": false},
  "live": {"rule": "wilson", "floor": 0.92, "n": 117, "s": 113, "cs": [0.89, 0.996], "…": "ReleaseGate.evidence"},
  "drift": {"alarm": false, "threshold": 100.0,
            "streams": [{"version": 3, "task": "ticket-triage", "n": 2611, "log10_martingale": -2.1383, "…": "as in GET /ratchet/drift"}]},
  "assumptions": ["Golden rows are assumed exchangeable with live traffic, …", "Precision means agreement with the teacher model …", "Which requests are golden is decided by a hash of the request, …"]
}
```

(Mock gateway after five simulated days of mixed traffic; simulation numbers from mock models, rounded here.)

- `status`: `certified` or `not certified` from the latest certificate (`current`'s when the version was re-scored, else `training`'s); `suspended` while a drift alarm is open for the version (FR-16.6): its own traffic stopped looking like its earlier traffic, so the golden rows no longer stand for what it serves. The certificate is still shown. An alarm stays open for the version's life; the next version gets new streams. `no certificate` for a version evaluated before v0.5 or by an evaluator that writes none (also under an alarm).
- `training`: from the signed BOM's `manifest.eval`, written once at training time (INV-14); `null` without a BOM. `current`: from the version's eval, which the nightly champion re-score overwrites for the live version (with `rechecked_day`, `null` until then). Each holds `certificate`, `frontier`, `per_task`, `threshold`, `calibration` and `paired` (`null` when that eval lacks one).
- `certificate`: the Clopper–Pearson certificate at the version's threshold, on the held-out half: `served` answers, `errors` among them, `precision`, the one-sided lower bound `lcb` at level `delta` (`RATCHET_CERT_DELTA`), and `certified` (`lcb ≥ target`, `RATCHET_CERT_TARGET`). `calibration` is how the threshold was chosen: `point` (v0.4's rule, 97% on the calibration half) or `ltt` (Learn-then-Test over a fixed grid, `RATCHET_CALIBRATION=ltt`), which adds `ltt_trace` (each grid threshold tested on the calibration half, `{threshold, n, k, p_value}`, highest first, stopping at the first that fails).
- `frontier`: the same counts at each threshold of the fixed grid 0.95, 0.90, …, 0.50, each `lcb` at `delta / 10` (Bonferroni), so every row holds at once: an operator may pick any threshold on it afterwards and keep the guarantee.
- `per_task`: the certificate's counts per task, each bound at `delta` on its own. A diagnostic that shows a weak task inside a pooled number; it is not corrected for the number of tasks and never changes routing.
- `paired` (a candidate trained while a version was live): the exact comparison with that version (`against`) on the same held-out rows. `cov_only_live` and `cov_only_cand` are the rows only one of them serves, with `p_cov` the one-sided McNemar p-value for "the candidate serves less"; `neg_flips` are rows the candidate answers wrong where live's answer was right (served right, or escalated to the teacher), `pos_flips` the reverse, with `p_err`; `nfr` is `neg_flips_served / live_served_ok` (answers live served right that the candidate serves wrong), with its 95% Wilson interval `nfr_95`. The opt-in paired gate reads these ([Configuration](CONFIGURATION.md#release-policy-ratchetreleasepy)).
- `bom`: the BOM's `sha256` and whether it is signed; `null` without one. `live`: the release gate's evidence from the version's audits (the same object as `/ratchet/stats`' `evidence`) for a version that serves or once served (`canary`, `live`, `rolled_back`, `retired`), else `null`. `drift`: the drift monitor's view of the version ([Drift](#get-ratchetdrift)): `alarm` (true while any of its streams has alarmed), the `threshold`, and its streams without trajectories; `null` when the monitor is off or the version never logged a confidence (a candidate or shadow).
- `assumptions`: what every certificate assumes, in plain words. Precision is agreement with the teacher, not ground truth; golden rows over-represent hard requests, so a certificate is conservative for easy traffic only to the extent that exchangeability fails in that direction.

Check a downloaded BOM's numbers yourself:

```bash
curl -s https://<gateway>/ratchet/versions/3/bom > bom.json
python -m ratchet.stats check bom.json --public-key "<base64 public key>"
# OK: signature valid (Ed25519, key 6cf6bc1a39c4491d, trusted key)
# OK: certificate: 118 of 119 served answers agree at threshold 0.5 (delta 0.05): lower bound 0.9608 vs target 0.95, certified
# OK: frontier: 10 thresholds, each bound at delta 0.005 (Bonferroni)
# OK: per_task: 3 tasks
# OK: paired against v2: p_cov 0.8666, p_err 0.9375
# check: every number recomputed from the signed counts
```

It exits 1 when the signature or hash fails or when any recomputed number differs from the signed one by more than 1e-9, and 0 for a BOM written before v0.5 (no certificate to check).

## Committed training sets
## GET `/ratchet/drift`

Every adapter's BOM signs one sparse Merkle root per split ([ADR 0027](adr/0027-committed-training-sets.md)): the keys are the example hashes of exactly the rows it was trained on (`training.commitment`) and graded on (`golden.commitment`), each mapped to the SHA-256 of `{completion, teacher, license, redactions}` (the stored teacher answer, who wrote it, its license and the number of PII placeholders in it). The leaves are written once to the ledger (`version_leaves`, append-only) before the BOM is signed. From them the gateway answers whether one example is in a set, with a proof anyone can check offline against the signed root, and gives a certificate that no golden example was trained on (invariants 3 and 6).

Both endpoints need an admin key ([Authentication](#authentication)): for a text an attacker can guess, a yes-or-no answer about the training set is a leak. **404** for an unknown version, and **404** `commitment not available: version N was trained before set commitments (v0.5)` for a version whose BOM has no commitment.

What a proof shows: the operator's binding commitment, at training time, to the dataset the adapter was trained and graded on. It does not show that the weights came from that dataset.

### POST `/ratchet/versions/{id}/proof?split=train`

`split` is `train` (the default) or `golden`; anything else is **400**. The body names the example in one of two ways:

- `{"key": "<64 hex characters>"}`: the example's hash (`python -m ratchet.proof key` computes it).
- The chat request itself: `messages` with its `task` (or `metadata.ratchet_task`; default `default`) and any other `/v1/chat/completions` field. It is validated, normalised and, when `RATCHET_REDACT_PII` is on, redacted exactly as the gateway stores a teacher answer to it, and its `stop`, `response_format`, `tools` and `tool_choice` are part of the key (other sampling fields are not). A request with a content part only the teacher reads is **400**: such requests are never stored, so no adapter was trained on one.

Anything else (both forms, neither, a key that is not 64 hex characters, a request the gateway would refuse) is **400** with the reason; a body that is not JSON is **422**.

```bash
curl -s "localhost:8000/ratchet/versions/3/proof?split=train" -H "Authorization: Bearer $ADMIN_KEY" \
  -H 'content-type: application/json' -d '{"task": "ticket-triage", "messages": [...]}' > proof.json
```

```json
{
  "version": 3, "split": "train", "key": "009caefc0abdc8dd8033a716ed8e7861762d4a29d033afd88573f6af77967df9",
  "alg": "ratchet.smt/v1", "root": "e8ab2527eed2c08e…", "size": 1288, "result": "included",
  "value": "e43228f5b4cc42b4b61ef7fa6128142aa78b7eb2cd67b51e4858397ba615c460",
  "proof": {"siblings": ["92666e28efca7f96…", "c06a2dd0139ee1f1…", "b4715cc6b4fd8ce2…", "64a62adbe07cb66c…", "a5b2f8637ee380a5…", "d61a9c7573c0131a…"],
            "terminal": {"leaf": {"key": "009caefc0abdc8dd…", "value": "e43228f5b4cc42b4…"}}},
  "bom_sha256": "301a48c00a468c60…"
}
```

(Mock gateway after four simulated days; hashes shortened here.)

- `result` is `included` or `absent`. `value` (only when included) is the example's leaf value; anyone holding the example's stored answer, teacher, license and redaction count can recompute it.
- `proof.siblings` are the sibling hashes from the root down (about log2 of `size`); `proof.terminal` is where the key's path ends: `{"leaf": {key, value}}` (the key itself when included; for an absent key, the one example that shares its path, whose key and value hashes the proof therefore discloses) or `{"empty": true}`.
- `root` and `size` are the ones the version's BOM signs, and `bom_sha256` names that BOM.

### GET `/ratchet/versions/{id}/heldout`

The held-out certificate: the golden set's leaves, and for each golden key a non-membership proof against the training root.

```json
{
  "version": 3, "alg": "ratchet.smt/v1",
  "training": {"root": "e8ab2527eed2c08e…", "size": 1288},
  "golden": {"root": "1f6b2a205ce04e6b…", "size": 133, "leaves": [["0001a3…", "5c9e10…"], ["…", "…"]]},
  "proofs": {"0001a3…": {"siblings": ["…"], "terminal": {"leaf": {"key": "…", "value": "…"}}}, "…": {}},
  "held_out": true,
  "bom_sha256": "301a48c00a468c60…"
}
```

About 113 KB for 133 golden rows against 1,288 training leaves (mock gateway after four simulated days). `held_out` is the gateway's own check; a verifier ignores it and checks the proofs.

When the stored leaves no longer rebuild the root and size the BOM signs (the ledger file was edited outside Ratchet: SQLite's triggers refuse any edit through its API), both endpoints answer **500** and the gateway writes one `integrity` event per version and split; it never serves a proof that would fail against the signed root.

### Checking a proof offline

```bash
curl -s https://<gateway>/ratchet/versions/3/bom > bom.json
python -m ratchet.proof verify proof.json --bom bom.json --public-key "<base64 public key>" --expect included
# OK: example 009caefc0abd IS in the training set of student-v3 (1,288 leaves, 6 siblings); BOM signature valid (Ed25519, key 6cf6bc1a39c4491d, trusted key)
curl -s https://<gateway>/ratchet/versions/3/heldout -H "Authorization: Bearer $ADMIN_KEY" > heldout.json
python -m ratchet.proof heldout heldout.json --bom bom.json --public-key "<base64 public key>"
# OK: all 133 golden examples provably absent from the training set of student-v3 (1,288 leaves); BOM signature valid (...)
python -m ratchet.proof key --task ticket-triage --messages request.json   # the key a request is stored under
```

`verify` checks the BOM's hash and signature, that the proof is against the root the BOM signs for that split (never the root, `result` or `value` the file states, which only have to agree), and the proof itself. `heldout` rebuilds the golden root from the certificate's leaves, requires it and the size to match the BOM, and checks that every golden key is absent from the signed training root. Both exit 1 on any failure (a bad signature, a root mismatch, an invalid proof, an unexpected `--expect` result, a file that is not JSON) and 2 on bad input (a missing file). `key` reads a messages list or a request body (`--no-redact` for a gateway with `RATCHET_REDACT_PII=0`) and uses the gateway's settings from the environment. A BOM without commitments (v1, or written before v0.5) has nothing to check against: exit 1, `no set commitment`.
The label-free drift monitor (FR-16.6, [ADR 0032](adr/0032-label-free-drift-alarm.md)). For each version and task, the student's logged confidence is one stream. Each new score gets a conformal p-value (its rank among the stream's earlier scores, small when it is unusually low), and a gambler bets against "traffic has not changed" with those p-values. Its capital is a test martingale: while the traffic a version sees stays exchangeable, the chance that it ever reaches `threshold` is at most 1 / `threshold` (1% at the default 100), however long the stream runs and however often anyone looks. No labels and no teacher calls are needed; the release gate feeds it from the ledger in its tick, so the request path is untouched. Public, like `/ratchet/stats`. `?version=N` narrows `streams` to one version (**422** for a value that is not a whole number); the other numbers stay the monitor's.

```json
{
  "enabled": true,
  "threshold": 100.0,
  "false_alarm_bound": 0.01,
  "family_bound": 0.03,
  "monitored": 3,
  "alarms": 1,
  "streams": [
    {"version": 1, "task": "ticket-triage", "n": 3586, "log10_martingale": 14.7573, "peak_log10": 14.9509,
     "alarm": true, "alarm_at_request": 6129, "alarm_day": 5, "alarm_n": 3335, "last_request": 6600, "active": true,
     "trajectory": [[32, -0.6741], [64, -0.8717], [96, -0.9943], "…", [3584, 14.7458]]},
    {"version": 3, "task": "ticket-triage", "n": 2611, "log10_martingale": -2.1383, "peak_log10": 0.0,
     "alarm": false, "alarm_at_request": null, "alarm_day": null, "alarm_n": null, "last_request": 9000, "active": true,
     "trajectory": [[16, -0.6724], [32, -0.7884], "…", [2608, -2.1378]]}
  ]
}
```

(Mock gateway after six simulated days of tickets with a product launch on day 5; simulation numbers from mock models, one stream left out.)

- `enabled`: false when `RATCHET_DRIFT_ALARM=0`; then `threshold`, `false_alarm_bound` and `family_bound` are `null`, the counts 0 and `streams` empty.
- `false_alarm_bound`: 1 / `threshold`, for one stream. `family_bound`: min(1, K / `threshold`) with K = `monitored`, the chance that any stream the monitor holds ever alarms on unchanged traffic. It grows with every version and task; it is reported, not hidden.
- A version has at most 16 task streams, opened in the order it first sees each task name; the rows of every later name share one more stream whose `task` is `*` (events call it "other tasks"). Task names come from clients (`X-Ratchet-Task`), so this keeps the monitor's memory and K bounded: at most 17 streams per version ([Threat model](THREAT_MODEL.md) T12).
- `alarms`: streams that alarmed. Each stream alarms at most once (the alarm latches) and writes one `drift` event, once across restarts and processes.
- Per stream: `n` confidences seen; `log10_martingale` and `peak_log10`, log10 of the capital now and at its highest (the alarm is at log10 `threshold`, 2 by default); `alarm_at_request` (the request id), `alarm_day` and `alarm_n` of the first crossing, or `null`; `last_request`, the last request id seen; `active`, false for an older version that no longer serves (only its final numbers are kept; should it serve again, its stream is replayed from the ledger first); `alarm_day` is the ledger day of the request that crossed, which is also the `drift` event's `day`, even when the monitor found it later by replaying the ledger; `trajectory`, at most 200 `[n, log10 capital]` points, evenly spaced.
- The test is one-sided: confidence that rises makes the capital fall. An alarm says traffic changed for this version, not that the model got worse; audits measure that ([Runbook](RUNBOOK.md#drift-event)). It suspends the version's certificate ([Certificates](#get-ratchetversionsidcertificate)) and, opt-in, boosts its audits (`RATCHET_DRIFT_AUDIT_BOOST`) and lets Night School train sooner (`RATCHET_DRIFT_EARLY_NIGHT`).

## Teacher watch

Every number Ratchet reports is measured against the teacher, so the teacher is checked too (FR-16.7, [ADR 0033](adr/0033-teacher-watch.md)). A request that repeats is labelled by the teacher again when it escalates or is audited; its first such re-test in a check epoch is compared with the stored label under the task's comparators, at no extra cost. Night School adds a few probes for traffic with few repeats (`RATCHET_TEACHER_PROBES`). Only labels that can be compared count: the same configured teacher at temperature 0, no redaction placeholders on either side, a registered task that names fields and is not pinned. A label a re-test disagreed with is **contested**: it is left out of training and the golden set until Night School's adjudication (two more teacher answers, a strict majority wins) settles it.

When the epoch's first re-tests disagree too often, the release gate freezes Night School with a `teacher_drift` event: the freeze rule's e-value, a mixture over the epoch's days of `stats.e_bernoulli` against "a fresh label disagrees with an earlier one at most `RATCHET_TEACHER_NOISE_MAX` (5%) of the time", has reached 1 / `RATCHET_ALPHA_TEACHER` (100). A teacher within that noise freezes it with probability at most 1% per epoch, however often the gate looks. Training stays paused until an operator acknowledges ([Runbook](RUNBOOK.md#teacher_drift-event)). A change in the model id the teacher's responses report starts a new epoch on its own (`teacher_model_changed`).

### GET `/ratchet/teacher`

Public and read-only, like `/ratchet/stats`.

```json
{
  "enabled": true,
  "epoch": 1,
  "epoch_started_day": 1,
  "frozen": false,
  "model": "nvidia/Nemotron-3-Ultra-550b-a55b",
  "noise_max": 0.05,
  "alpha": 0.01,
  "threshold": 100.0,
  "checks": 273,
  "disagreements": 11,
  "self_agreement": 0.9597,
  "interval": [0.9293, 0.9774],
  "ceiling": 0.9886,
  "e_value": 96.38,
  "strongest": {"from_day": 3, "checks": 48, "disagreements": 11},
  "contested": 0,
  "sources": {"duplicate": {"checks": 193, "disagreements": 1}, "probe": {"checks": 80, "disagreements": 10}},
  "tasks": [
    {"task": "ticket-triage", "checks": 273, "disagreements": 11, "self_agreement": 0.9597, "interval": [0.9293, 0.9774],
     "ceiling": 0.9886, "e_value": 96.38, "contested": 0}
  ]
}
```

(Mock gateway, four days of tickets, the mock teacher changing 15% of labels from day 3 (`POST /ratchet/demo/teacher-drift`); simulation numbers from mock models, rounded here.)

- `enabled`: `RATCHET_TEACHER_WATCH`. When 0 the checks are still recorded and reported, but nothing acts on them.
- `epoch`, `epoch_started_day`: the check epoch (1 until the first ack or model change) and the ledger day it started. Each stored request gives at most one check per epoch: its first re-test. That is what keeps the e-value valid (comparing one stored label with several re-tests makes the checks dependent).
- `frozen`: Night School skips training (`skip` event "teacher labels look unstable") until `POST /ratchet/teacher/ack`.
- `model`: the model id the teacher's responses last reported (`null` before the first teacher call; at most 200 characters).
- `noise_max`, `alpha`, `threshold`: the freeze rule (`RATCHET_TEACHER_NOISE_MAX`, `RATCHET_ALPHA_TEACHER`, 1 / alpha).
- `checks`, `disagreements`: the epoch's first re-tests and how many disagreed. `self_agreement` is 1 − disagreements / checks, `interval` its 95% Wilson interval, and `ceiling` the square root of the interval's upper end: no student can agree with a fresh teacher label more often than that at full coverage (FR-6.43). All three are `null` without checks.
- `e_value`: the freeze rule's e-value (capped at e^690); `strongest`: the stretch of days from `from_day` on that weighs most in it, with its counts (`null` without checks). The `teacher_drift` event quotes these.
- `contested`: stored labels quarantined now, of every epoch.
- `sources`: the checks by where they came from: `duplicate` (a repeated request the teacher labelled again) and `probe` (Night School).
- `tasks`: per task with checks or contested labels, the same numbers. A task's `e_value` is diagnostic only: the freeze tests the pooled checks.

### POST `/ratchet/teacher/ack`

Starts a new check epoch, unfreezes Night School and writes a `teacher_ack` event naming the admin key (its name, never the key). Needs an admin key (`Authorization: Bearer <admin key>` or `X-API-Key`): **401** for a missing or wrong key, **403** for a regular API key, and **403** in live mode without `RATCHET_ADMIN_KEYS`. Open in mock mode without admin keys (the event names `mock-admin`). No body. Check the teacher's model id first ([Runbook](RUNBOOK.md#teacher_drift-event)).

```json
{"epoch": 2, "frozen": false}
```
## GET `/ratchet/bom/public-key`

```json
{"alg": "Ed25519", "public_key": "IiNlIP8YSYKDKqEHNzxBnn+m37imeLeykRmrZEEl5I4=", "key_id": "6cf6bc1a39c4491d"}
```

`public_key` and `key_id` are `null` when signing is off. Publish the key somewhere you control as well (README, website): a key served by the same gateway proves nothing if the gateway itself is compromised.

## POST `/ratchet/redact?style=`

Runs the PII redactor ([ARCHITECTURE.md](ARCHITECTURE.md#pii-redaction)) on a text and returns what would be stored: in the gateway's style (`RATCHET_REDACT_STYLE`) or the one `style` names (`label`, `numbered` or `surrogate`, any case) ([ADR 0082](adr/0082-placeholder-styles-detector-plugin-and-indian-identifiers.md)). Nothing is logged or stored. Backs the dashboard's **Try the redactor** box. Access level `dashboard`; the detector plugin (`RATCHET_REDACT_PLUGIN`), which may be an NER model costing tens of milliseconds a call, runs only for a caller with a valid API or admin key (`Authorization: Bearer <key>` or `X-API-Key`). Anyone else, or a wrong key, gets the patterns alone.

```bash
curl -s localhost:8000/ratchet/redact -H 'content-type: application/json' \
  -d '{"text": "I am bob@example.com, card 4111 1111 1111 1111"}'
```

```json
{"text": "I am [EMAIL], card [CARD]", "found": {"EMAIL": 1, "CARD": 1}, "enabled": true, "style": "label", "plugin": false, "plugin_skipped": false}
```

```bash
curl -s 'localhost:8000/ratchet/redact?style=numbered' -H 'content-type: application/json' \
  -d '{"text": "Sales: ana@x.io. Billing: bo@y.io, cc ana@x.io"}'
```

```json
{"text": "Sales: [EMAIL_1]. Billing: [EMAIL_2], cc [EMAIL_1]", "found": {"EMAIL": 3}, "enabled": true, "style": "numbered", "plugin": false, "plugin_skipped": false}
```

- `found` counts placeholders by label, every occurrence; `style` is the style used; `plugin` says whether the detector plugin ran; `plugin_skipped` is true when one is configured but did not run, because the caller sent no valid key.
- `enabled` says whether this gateway redacts examples (`RATCHET_REDACT_PII`); the preview runs either way. A task whose spec sets `privacy.redact_style` stores in that style; preview it with `?style=`.
- **400** when `text` is missing or longer than 5,000 characters, or `style` is not one of the three (`style must be one of label, numbered, surrogate`). **500** when the detector plugin fails (only callers with a key reach it), with a message that names the problem or the exception's class and never the text (`the redaction plugin raised RuntimeError`).

---

## Cost

Utilization-aware cost accounting ([ADR 0023](adr/0023-utilization-aware-cost-accounting.md), formulas in [ARCHITECTURE.md](ARCHITECTURE.md#7-cost-accounting)). The self-hosted student endpoint and the training jobs bill GPU-hours while they run; the ledger records when they ran (`compute_intervals`) and the report prices them. Every list price is unverified until the day-1 checks pass; the report says so (`prices.label`) until `RATCHET_PRICES_VERIFIED=1`. Numbers from the mock demo are simulation output.

### GET `/ratchet/cost?days=N`

The cost report for every ledger day, or for the last `days` days (1 to 36,500; anything else is **400**, a non-number **422**). Access level `dashboard`, like `/ratchet/stats`. `python -m ratchet.cost report [--days N] [--json]` prints the same report. An example from the mock demo (simulation numbers, nine days of 1,500 requests with the endpoint up 24 hours a day):

```json
{
  "window": {"since_day": null, "until_day": 9, "now": 1792400000.0},
  "model": "per_token",
  "headline": {"model": "per_token", "total_usd": 5.21, "savings_usd": 9.37, "savings_pct": 0.64},
  "prices": {"verified": false, "label": "unverified list prices", "student_gpu_usd_per_hour": 1.55, "student_gpus": 1,
             "student_endpoint_usd_per_hour": 1.55, "train_gpu_usd_per_hour": 1.55, "train_gpus": 1,
             "teacher_model": "nvidia/Nemotron-3-Ultra-550b-a55b", "teacher_usd_per_m_tokens": {"input": 1.0, "output": 3.0},
             "student_usd_per_m_tokens": {"input": 0.06, "output": 0.24},
             "fallback_student_model": "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
             "fallback_student_usd_per_m_tokens": {"input": 0.06, "output": 0.24}},
  "requests": 13500, "student_served": 11300, "teacher_served": 2200,
  "teacher_usd": 4.69, "student_per_token_usd": 0.52, "hosted_student_usd": 0.0, "student_served_off_endpoint": 0,
  "baseline_usd": 14.58, "split_unknown_requests": 0, "split_unknown_usd": 0.0,
  "endpoint_hours": 216.0, "endpoint_usd": 334.8, "endpoint_usd_per_hour": 1.55,
  "idle_hours": 24.0, "idle_share": 0.11, "idle_granularity": "day",
  "training_hours": 1.9, "training_usd": 2.95,
  "views": {
    "per_token": {"total_usd": 5.21, "savings_usd": 9.37, "savings_pct": 0.64},
    "gpu_hours": {"total_usd": 342.44, "savings_usd": -327.86, "savings_pct": -22.49}
  },
  "avoided_teacher_usd_per_student_request": 0.00098,
  "break_even_student_requests_per_gpu_hour": 1582.0,
  "hosted_fallback_usd_per_request": 0.000029,
  "self_host_beats_hosted_above_requests_per_hour": 54070.0,
  "observed_student_requests_per_endpoint_hour": 52.3,
  "utilization_vs_break_even": 0.033,
  "recommendation": "hosted_fallback",
  "explanation": "Self-hosting does not pay at this volume. The endpoint served 52 student requests per hour it ran, below the break-even of 1,582; below 54,070 an hour the hosted fallback is cheaper per answer (1 GPU at $1.55 per GPU-hour; unverified list prices). Use the hosted fallback student on Token Factory (leave RATCHET_STUDENT_BASE empty) or run the endpoint only while traffic arrives. The crossover assumes the hosted fallback serves as many requests as the tuned student; it is untuned, so more of its requests may go to the teacher. Savings by GPU-hours paid: -$327.86 (-2249%); per token: $9.37 (64%).",
  "open_intervals": [],
  "warnings": [],
  "daily": [
    {"day": 9, "requests": 1500, "student_served": 1470, "teacher_usd": 0.12, "student_per_token_usd": 0.06,
     "endpoint_hours": 24.0, "endpoint_usd": 37.2, "training_usd": 0.33, "baseline_usd": 1.64,
     "savings_pct_per_token": 0.89, "savings_pct_gpu_hours": -22.0}
  ],
  "daily_truncated": false
}
```

- `views.per_token` is what every other savings figure uses; `views.gpu_hours` prices the student by the GPU-hours paid: teacher spend + endpoint hours + training hours (each interval at the rate it was recorded at) + the student's per-token spend in hours (live ledger) or days (simulation) with no recorded endpoint uptime (`hosted_student_usd`, `student_served_off_endpoint` answers: the hosted fallback served, or nobody recorded the endpoint). Savings can be negative and are never clamped; `savings_pct` is `null` without a baseline. `headline` is the view `RATCHET_STUDENT_COST_MODEL` picks.
- `avoided_teacher_usd_per_student_request` is the teacher spend a student answer saves on average (its all-teacher baseline less the teacher calls it still made); `break_even_student_requests_per_gpu_hour` is `endpoint_usd_per_hour` divided by it, so it counts student answers per hour of the whole endpoint. `endpoint_usd_per_hour` is the endpoint's GPU cost in the window over its hours (each interval at the rate it was recorded at, per-GPU price × GPUs), the same rate the GPU-hour view bills; the configured rate while no uptime is recorded. Break-even prices the endpoint's hours alone: training jobs can still leave the window at a loss above it, and the explanation then says so. `null` until the student has served, and when its answers save nothing.
- `hosted_fallback_usd_per_request` is what the hosted fallback student (`RATCHET_FALLBACK_STUDENT`, per token) would bill for all the student calls, per student answer; `self_host_beats_hosted_above_requests_per_hour` (`endpoint_usd_per_hour` / that price) is the volume above which the endpoint is cheaper per answer. It compares price per answer only: the hosted model is untuned, so it may serve fewer requests than the tuned student and send more to the teacher. Every `hosted_fallback` explanation says so; compare the student's share of requests over a day on each before switching.
- `observed_student_requests_per_endpoint_hour` counts student answers in buckets when the endpoint had uptime over its hours; `idle_hours` are endpoint hours in buckets when no request reached the student. `idle_granularity` names the bucket: `hour` on a live ledger (requests and uptime share the wall clock; an hour with any uptime is credited with all its answers), `day` on the simulator's synthetic clock (hours idle inside a busy simulated day are not counted).
- `recommendation` is `self_host`, `hosted_fallback`, `scale_to_zero` or `insufficient_data` (rules in [ARCHITECTURE.md](ARCHITECTURE.md#the-report-costpy)), with a plain-English `explanation`. It says separately whether the observed volume covers the endpoint's hours and whether the window lost money by GPU-hours: between break-even and the crossover, `hosted_fallback` comes with "Self-hosting beats the teacher, but the hosted fallback would cost less per answer", and it never says "pays" when `views.gpu_hours.savings_usd` is negative.
- `split_unknown_requests`: rows from before v0.5 have no student/teacher split; their whole cost counts as teacher spend in the GPU-hour view (`split_unknown_usd`), and a warning says so. `warnings` also names a training job open for more than 12 hours, which counts up to now until it is closed.
- `daily`: one row per day with requests or compute, the latest 731 days at most (`daily_truncated`); the totals always cover the whole window.
- The dashboard's **Economics** panel draws this report. The keys it reads are listed with their JSON types in `tests/fixtures/cost_keys.json` and checked by `test_cost_report_contract`; like `/ratchet/stats`, the report adds keys and never removes or retypes one.

### POST `/ratchet/cost/endpoint`

Records the self-hosted student endpoint going up or down, as `python -m ratchet.cost endpoint up|down` does. Needs an admin key (open in mock mode without admin keys, recorded as `mock-admin`; **403** `admin API disabled: set RATCHET_ADMIN_KEYS` in live mode without them).

```bash
curl -s localhost:8000/ratchet/cost/endpoint -H "Authorization: Bearer $ADMIN_KEY" -H 'content-type: application/json' \
  -d '{"state": "up", "usd_per_hour": 1.55, "gpus": 1, "note": "recording the demo video"}'
```

```json
{"state": "up", "changed": true,
 "interval": {"id": 3, "kind": "student_endpoint", "start_ts": 1792400000.0, "end_ts": null, "usd_per_hour": 1.55, "gpus": 1,
              "version": null, "source": "api", "note": "recording the demo video"},
 "summary": "student endpoint up at 2026-10-20 10:13:20 UTC: 1 GPU at $1.55 per GPU-hour (interval 3)"}
```

- `state` is required (`up` or `down`); `usd_per_hour` (per GPU, 0 to 1,000; default `RATCHET_STUDENT_GPU_USD_PER_HOUR`), `gpus` (0 to 1,024; default `RATCHET_STUDENT_GPUS`) and `note` (at most 200 characters, PII redacted) are optional. Any other key, or a bad value, is **400**.
- `up` while the endpoint is up returns its open interval with `changed: false`; `down` while it is down returns `interval: null`, `changed: false`. `down` closes every open endpoint interval. A change writes an `endpoint` event naming the source and the admin's name, never the key.
- In mock mode the time is on the demo's synthetic clock (the current simulated day at the wall clock's time of day), and the shared demo holds at most 200 such records (**409**; a reset clears them).

## Ledger replay

The ledger time machine ([ADR 0028](adr/0028-designed-propensity-replay.md), FR-16.5): what one version's logged traffic would have looked like at another threshold or audit rate. Ratchet designs its own labelling, so every label's probability is logged (`requests.label_p`: 1.0 for a request the student answered and escalated, the audit rate for an answer it served), and precision is estimated at any threshold with weights 1 / `label_p` (Hajek), including thresholds below the live one. Both endpoints are read-only and public, like `/ratchet/stats` (aggregates only, never request text), in both modes. Numbers from the mock demo are simulation output and say so (`label`).

- `version`: one version id in ASCII digits (default: the live version; **400** `no live version: name one with ?version=` without one). A version that does not exist is **404**. `version` twice, a list (`1,2`), a word (`all`) or other digits (`²`) is **400**: confidence scales differ between versions, so a replay never pools them.
- One replay runs at a time: it reads the version's whole history into memory, so a request while another replay runs is **429** with `Retry-After: 1`.
- `since_day`, `until_day`: ledger days, both included (0 to 10,000,000; `since_day` after `until_day` is **400**).
- `audit_rate`: 0 to 1 (**400** otherwise); prices the audits of every request served at the threshold. Default: each served answer's logged rate (a request that escalated in fact at the version's mean logged rate, `logged_audit_rate`). It changes cost only: precision comes from the labels that were logged.
- A query value that is not a number is **422**, as elsewhere.

### GET `/ratchet/replay?version=&threshold=&audit_rate=&since_day=&until_day=`

`threshold` is more than 0 and at most 1.01 (serve nothing; **400** otherwise), default: the version's threshold now (`current_threshold`: the one the router holds its answers to, which for the hosted fallback student, in live mode without a student endpoint, is `RATCHET_CONFIDENCE_THRESHOLD`). An example from the mock demo after four simulated days (simulation, mock models):

```json
{
  "version": 2, "state": "live", "label": "simulation (mock models)", "cost_model": "per_token",
  "n": 1697, "excluded": 0,
  "excluded_by": {"before_logging": 0, "no_student_answer": 0, "pinned_now": 0, "malformed": 0},
  "current_threshold": 0.5, "logged_audit_rate": 0.1,
  "actual": {"threshold": null, "audit_rate": null, "n": 1697, "served": 1653, "coverage": 0.974, "labelled": 172,
             "precision": 0.983, "precision_reason": null, "n_eff": 172.0, "interval": [0.950, 0.994],
             "interval_method": "approximate (Wilson at Kish n_eff)", "served_errors_per_1k": 17.0,
             "cost_usd": 0.282, "baseline_usd": 1.886, "saved_usd": 1.604, "savings": 0.850},
  "counterfactual": {"threshold": 0.4, "audit_rate": 0.05, "n": 1697, "served": 1680, "coverage": 0.990, "labelled": 199,
                     "precision": 0.967, "precision_reason": null, "n_eff": 177.2, "interval": [0.930, 0.985],
                     "interval_method": "approximate (Wilson at Kish n_eff)", "served_errors_per_1k": 32.3,
                     "cost_usd": 0.162, "baseline_usd": 1.886, "saved_usd": 1.724, "savings": 0.914},
  "calibrated_baseline": {"R": 1.0003, "se": 0.00026, "interval": [0.9998, 1.0008], "n": 172, "n_eff": 172.0, "min_n": 30,
                          "status": "ok", "method": "ratio estimator (Cochran), audited served answers weighted by 1/label_p"}
}
```

- `n`: requests the version's student answered with the replay's columns logged. `excluded_by` counts the version's other rows: written before the columns (`before_logging`), without a student answer (`no_student_answer`: the call failed or its breaker was open), of a task pinned now (`pinned_now`), or with out-of-range values (`malformed`, a ledger edited outside Ratchet). A request whose escalation failed outright was never logged, so it is not here.
- `actual`: the cascade's threshold decision as logged (`threshold: null`): each request at the threshold it was logged with, and each audit priced at its logged rate times the request's all-teacher cost (an expected cost, not the audits drawn). Its `served` is exactly the requests the student's confidence served (route `student`); a degraded answer (the student standing in for an unavailable teacher, route `degraded`) is counted as not served and priced as if the teacher had answered it. Arbitration spend is left out, so `cost_usd` can be below the logged cost.
- `counterfactual` at `threshold` and `audit_rate`: `coverage` is exact (no label needed); `precision` is Hajek's weighted mean of `student_agree` over labelled served requests, null with `precision_reason` when nothing is served, when a served request could never be labelled (audit rate 0: "audit rate was 0: precision above the live threshold cannot be identified") or when none has a label; `interval` is approximate (Wilson at Kish's `n_eff`), not a certificate. `cost_usd` = student spend + the all-teacher cost of every request not served + the audit rate times that of every request served; `baseline_usd` = the all-teacher cost of every request; `savings` = 1 − cost / baseline (per token). A request with no valid answer, one that could not fill its `n` choices (route `n_choices`), or one logged at 1.01 while its task's spec is still changed for the version (the router answers that task with the teacher at any threshold until a night re-scores the version) is never served. Not logged, and so counted as servable: a request for `n` > 1 choices that escalated below the threshold (route `confidence`) whose samples also had fewer than `n` in agreement; at a lower threshold it would escalate on route `n_choices`, so coverage below the logged threshold can be slightly high for such traffic. `student_agree` comes from an audit, or from an escalation the teacher answered under an audit's conditions (one choice, temperature 0, none of the client's `top_p`, `seed`, penalties or `logit_bias`); an escalation under the client's sampling parameters, or for `n` > 1 choices, has no label.
- `calibrated_baseline`: the ratio `R` of what the teacher really cost to the token formula's estimate (which assumes `RATCHET_TEACHER_REASONING_TOKENS`), on audited answers, with its standard error and an approximate 95% interval; `insufficient` (R = 1) below 30 audited answers. The replay prices unaudited answers at R times the estimate. The mock teacher writes exactly the assumed tokens, so R is about 1 in the demo.

### GET `/ratchet/replay/frontier?version=&target=&audit_rate=&since_day=&until_day=`

`target` is strictly between 0 and 1 (default 0.97; **400** otherwise).

```json
{
  "version": 2, "state": "live", "label": "simulation (mock models)", "cost_model": "per_token",
  "n": 1697, "excluded": 0, "excluded_by": {"before_logging": 0, "no_student_answer": 0, "pinned_now": 0, "malformed": 0},
  "current_threshold": 0.5, "target": 0.97,
  "rows": [{"threshold": 0.3, "...": "the counterfactual's keys"}, "... 71 rows, 0.30 to 1.00 every 0.01"],
  "suggested": {"threshold": 0.95, "target": 0.97, "lower": 0.974, "precision": 1.0, "coverage": 0.770, "savings": 0.667,
                "advisory": "approximate interval, not a certificate; thresholds change only through Night School",
                "method": "fixed-sequence scan of [0.95, 0.9, 0.85, 0.8, 0.75, 0.7, 0.65, 0.6, 0.55, 0.5], approximate (Wilson at Kish n_eff)"},
  "money_left_on_table": null,
  "calibrated_baseline": {"R": 1.0003, "...": "as above"}
}
```

- `rows`: one counterfactual per threshold; `coverage` never rises with the threshold.
- `suggested`: the scan goes down `stats.CERT_GRID` from 0.95 and stops at the first threshold whose interval's lower bound is below `target`; it returns the last one that passed, or null when 0.95 already fails. It is advice: the interval is approximate and computed on the traffic being looked at, so it is not a certificate, and nothing applies it. Thresholds change only through Night School's held-out calibration ([ADR 0026](adr/0026-certified-thresholds-and-a-paired-release-gate.md)).
- `money_left_on_table`: `{threshold, current_threshold, savings, usd}`, what serving at the suggested threshold would have saved over the current one, when the suggestion is lower; null otherwise (above, the suggestion says the current threshold is not supported at the target by this approximate interval).

`python -m ratchet.replay --db PATH [--version V] [--sweep 0.3:0.95:0.05] [--audit-rate A] [--target 0.97] [--since-day D] [--until-day D] [--json]` prints the frontier report as one table per version (labelled as simulation when the simulator wrote the ledger); exit 2 on bad input. `python -m sim.simulate --db PATH` keeps a simulated run's ledger in a new file for it.

## Savings receipts

Verified savings receipts ([ADR 0029](adr/0029-verified-savings-receipts.md), FR-15.5): for each ledger day, one receipt for the whole gateway and one per API client, sealed once the day is over and never changed (invariant 17). A receipt states what the day cost and saved, with the all-teacher baseline of the answers the student served **calibrated on audited teacher bills** and a one-sided 95% lower bound, and bills only on that bound. It is an RFC 9162 Merkle tree over each request's metadata (never request text; the client by its key's name), signed with the BOM key (`/ratchet/bom/public-key`) and chained to the same client's previous receipt. Every amount is whole nano-USD (1e-9 USD); numbers that are not whole are decimal strings, so the signed JSON has no floats. Who may read what is under [Authentication](#authentication). In the mock demo the numbers are simulation output and say so (`label`).

A day is sealed when it is over and someone asks: the mock demo after each simulated night, any of these endpoints (lazily, every unsealed day up to the one asked for, oldest first), `python -m ratchet.live seal` from cron ([RUNBOOK](RUNBOOK.md#savings-receipts)) or `python -m ratchet.receipt seal --db`. On a live ledger a day is over 5 minutes after it ended. A day that has not ended is **409**; a day on which the client sent no request (nobody did, for the gateway's receipt) is **404**; a `period` outside 0 to 10,000,000 is **400**.

### GET `/ratchet/receipts`

```json
{"receipts": [{"period": 1, "global": true, "clients": ["acme"]}, {"period": 2, "global": true, "clients": ["acme", "globex"]}],
 "sealed_through": 2, "public_key": "YLHq1TAo…", "key_id": "9d2479f21890a44f", "label": "live traffic"}
```

Every sealed receipt the caller may read, by day, after sealing every day that has ended: an admin (or the open mock demo) sees the gateway's (`global`) and every client's; an API key sees only its own (`global: false`). `sealed_through` is the last day that has ended; `public_key` is null when receipts are unsigned.

### GET `/ratchet/receipts/{period}?client=`

The signed document of day `period`: the gateway's, or `client`'s for an admin; an API key always gets its own (`?client=` naming another client is **403**). An example from the mock demo with `RATCHET_MOCK_TEACHER_REASONING_MEAN=420`, after four simulated days (simulation, mock models; shortened):

```json
{
  "manifest": {
    "schema": "ratchet.receipt/v1", "generator": "ratchet 0.4.0", "created_at": "2026-10-21T11:39:44Z",
    "label": "simulation (mock models)", "period": {"day": 3}, "client": null,
    "first_id": 3001, "last_id": 4500, "n": 1500,
    "merkle": {"alg": "rfc9162-sha256", "root": "efd10ccd…", "size": 1500, "leaf_keys": ["id", "ts_ms", "…"]},
    "totals": {"requests": 1500, "teacher_served": 29, "student_served": 1471, "unverified": 0, "audits": 147,
               "cost_nusd": 297406120, "student_cost_nusd": 40212120, "teacher_cost_nusd": 257194000,
               "baseline_recorded_nusd": 1703467000, "baseline_measured_nusd": 33954000,
               "baseline_calibrated_nusd": 2267342206, "missing_amounts": 0},
    "calibration": {"method": "ratio estimator (Cochran, …) per task over audited student answers, weighted 1/label_p; …",
                    "min_audits": 30, "z": "1.645",
                    "tasks": {"ticket-triage": {"status": "ok", "n_served": 1471, "n_audits": 147, "n_eff": "147.0",
                                                "cost_nusd": 262666960, "recorded_nusd": 1669513000, "x_total_nusd": 1606891000,
                                                "ratio": "1.389882", "ratio_se": "0.029841",
                                                "point_nusd": 2233388206, "se_nusd": 45502963}}},
    "savings": {"recorded_nusd": 1406060880, "calibrated_nusd": 1969936086, "stand_in_loss_nusd": 0,
                "billable_nusd": 1969936086, "margin_nusd": 74852375, "billable_lo_nusd": 1895083711, "spend_nusd": 0, "net_billable_lo_nusd": 1895083711},
    "billing": {"share": "0.25", "fee_nusd": 473770927, "basis": "one-sided 95% lower bound",
                "billable": {"requests": 1500, "baseline_nusd": 2267342206, "cost_nusd": 297406120, "tasks": ["ticket-triage"]}},
    "cost_views": {"billed": "per_token",
                   "gpu_hours": {"total_nusd": 37743255111, "endpoint_seconds": 86400, "savings_calibrated_nusd": -35475912905,
                                 "basis": "informational: billing uses the per-token view", "…": "…"},
                   "gpu_hours_note": null},
    "prev_sha256": "6c073996…",
    "units": "amounts in nano-USD (1e-9 USD); numbers that are not whole are decimal strings",
    "scope": "edits after sealing are detectable, …; costs written at request time are not verified: reconcile …"
  },
  "sha256": "64008e19…",
  "signature": {"alg": "Ed25519", "key_id": "9d2479f21890a44f", "public_key": "YLHq1TAo…", "value": "uNVs30b5…"}
}
```

- Requests are **measured** (the primary teacher answered: its bill is the baseline), **served** (the student served on the audit design) or **unverified** (a stand-in answer: route `degraded` or `fallback_teacher`, outside the audit design; counted at its recorded baseline, never billed, though what it lost counts: below). `totals.teacher_served` counts the measured ones.
- `calibration.tasks`: per task with served answers, the ratio `R` of the audited answers' teacher bills to the token formula's estimates (weights 1 / `label_p`), the point estimate of the served answers' all-teacher cost (`point_nusd` = R × `x_total_nusd`) and its standard error (`se_nusd`, Cochran's variance with the finite population correction; normal approximation). `insufficient_audits` below `min_audits` (`RATCHET_RECEIPT_MIN_AUDITS`) and `unlogged` (a served answer written before the formula estimate was logged) have none and are not billed.
- `totals.baseline_calibrated_nusd` = measured bills + each calibrated task's point + the recorded baseline of every other request. `savings.recorded_nusd` is the formula's savings, as `/ratchet/stats` reports them; `calibrated_nusd` uses the calibrated baseline.
- `billing.billable`: the measured requests and the served answers of the calibrated tasks. `savings.stand_in_loss_nusd` (0 or less) = Σ min(0, baseline − cost) over the unverified requests: a stand-in's gain is never billed, but its loss (a fallback teacher's answer after the student's samples were paid for) counts against the bill, like a measured request's. `savings.billable_nusd` = the billable baseline − the billable cost + `stand_in_loss_nusd`, and `billable_lo_nusd` = that − `margin_nusd` (⌈1.645 √Σ se²⌉); `net_billable_lo_nusd` subtracts the day's spend outside requests (`spend_nusd`; a client's share by request count). `billing.fee_nusd` = ⌊`share` × max(0, net)⌋. With no calibrated task, `basis` is `formula` and the fee 0.
- `cost_views.gpu_hours` (the gateway's receipt only; `gpu_hours_note` says why a client's has none): the day's cost by GPU-hours paid (ADR 0023), informational.
- `prev_sha256`: the `sha256` of the same client's previous receipt (64 zeros for its first).

### GET `/ratchet/receipts/{period}/proof/{request_id}?client=`

```json
{"period": 3, "client": null, "request_id": 3006,
 "leaf": {"id": 3006, "ts_ms": 1792582781950, "day": 3, "task": "ticket-triage", "client": null, "served_by": "student",
          "route": "student", "student_version": 1, "escalated": 0, "audited": 0, "label_p_ppb": 100000000,
          "cost_nusd": 27240, "student_cost_nusd": 27240, "teacher_cost_nusd": 0,
          "baseline_nusd": 1093000, "baseline_est_nusd": 1093000},
 "index": 5, "size": 1500, "path": ["273c76f7…", "… 11 hashes"], "root": "efd10ccd…", "alg": "rfc9162-sha256",
 "receipt_sha256": "64008e19…"}
```

The leaf (exactly the receipt's `leaf_keys`) and its RFC 9162 audit path, deepest sibling first, against the root and size the receipt signs. A request of another day or client is **404**. The gateway rebuilds the tree from the ledger and serves a proof only when it rebuilds the signed root; rows changed after sealing get **500** and one `integrity` event per day and client ([RUNBOOK](RUNBOOK.md#savings-receipts)).

### GET `/ratchet/receipts/{period}/leaves?client=`

The receipt's leaves as JSON lines (`application/x-ndjson`), in tree order: anyone holding them can rebuild the root and recompute every total, estimate and the fee. Same checks and errors as the proof.

### Checking a receipt offline

```bash
curl -s localhost:8000/ratchet/bom/public-key                                    # publish this once
curl -s localhost:8000/ratchet/receipts/3 > r3.json; curl -s localhost:8000/ratchet/receipts/2 > r2.json
curl -s localhost:8000/ratchet/receipts/3/proof/3006 > p.json; curl -s localhost:8000/ratchet/receipts/3/leaves > leaves.jsonl
python -m ratchet.receipt verify r3.json --public-key <key> --prev r2.json --proof p.json --rows leaves.jsonl
# OK: signature valid (Ed25519, key 9d2479f21890a44f, trusted key)
# OK: chain: prev_sha256 is the sha256 of day 2's receipt
# OK: proof: request 3006 is leaf 5 of 1500 in the signed tree
# OK: rows: 1500 leaves rebuild the signed root, and every total, estimate and the fee follow
```

Exit 0 when every check passes, 1 when one fails (an edited digit fails the hash), 2 on bad input. `--rows` recomputes amounts that come from the estimator within 2 nano-USD and the decimal strings within 1e-5 (relative), so another implementation's floating point may differ slightly. `python -m ratchet.receipt seal --db PATH [--period N [--client NAME]]` seals a ledger file offline (a simulator's ledger from `python -m sim.simulate --db`: every day it logged) and prints the receipt asked for. A receipt cannot prove the costs written at request time: reconcile `totals.teacher_cost_nusd` over the days with the Token Factory bill for the same API key.

---

## Tasks

The task registry ([ADR 0022](adr/0022-task-registry.md)): the built-in tasks, the tasks of `RATCHET_TASKS_FILE`, the tasks written through these endpoints (API tasks, stored in the ledger and registered again at every start), and auto tasks, derived from a request's `response_format` json_schema and stored the same way ([Structured outputs](#structured-outputs), [ADR 0042](adr/0042-auto-tasks-from-response-format-schemas.md)). The spec format is in [CONFIGURATION.md](CONFIGURATION.md#adding-a-task). Reads have the access level `dashboard` (open in mock mode; in live mode an admin or dashboard key unless `RATCHET_PUBLIC_DASHBOARD=1`, and then an anonymous reader sees `clients` allow-lists as the pseudonyms `/ratchet/stats` uses); writes and `validate` need an admin key ([Access levels](#access-levels)). Errors are `{"detail": ...}`; a bad spec is **422** `{"detail": "invalid task spec", "errors": [...]}`, every problem with its path.

### GET `/ratchet/tasks`

```json
{
  "unknown_tasks": "distill",
  "max_tasks": 500,
  "tasks": [
    {"name": "invoice-extract", "description": "Vendor and total from an invoice", "format": "json",
     "fields": {"vendor": {"type": "text", "strip_suffixes": ["inc", "ltd"]}, "total": {"type": "number", "abs_tol": 0.01}},
     "sql": null, "pinned": false, "store": true, "privacy": {}, "match": {}, "routing": {}, "policy": {}, "tools": "teacher", "clients": ["acme"],
     "source": "api", "spec_hash": "1c7e77bb7041b4d8", "verifiable": true,
     "requests": 420, "student_share": 0.81, "recalibrating": false, "runtime_pinned": false}
  ],
  "unregistered": [{"name": "invoice-extrct", "requests": 3}]
}
```

- `tasks`: every registered task, the built-in ones first, then the file's, then the API and auto tasks. The first twelve keys are the task's spec as a file would hold it (what `PUT` takes back); `sql` is null except for a text-to-SQL task, where it holds every option (`{"fixture": "shop.sql", "dialect": "sqlite", "ordered": "auto", "empty_result": "text", "max_rows": 1000, "max_steps": 200000}`, or `{"dialect": "postgres"}`; [CONFIGURATION.md](CONFIGURATION.md#text-to-sql-tasks)); `source` is `builtin`, `file`, `api` or `auto` (derived from a schema; never pinned); `verifiable` says it names fields that can be checked, or runs its SQL answers on a fixture; `requests` and `student_share` come from the ledger (the same numbers as `/ratchet/stats` `tasks`); `recalibrating` is true when the live version was scored under another spec of the task, so the teacher answers it until a night re-scores the version; `runtime_pinned` (v0.5) is true while an operator has pinned the task to the teacher at runtime ([Admin](#admin)), on top of its spec's `pinned`.
- `unknown_tasks`: the `RATCHET_UNKNOWN_TASKS` policy. `max_tasks`: how many tasks the registry holds at most, built-in ones included.
- `unregistered`: names requests used that are not registered (a typo, or a task to register or pin), with their request counts.
- The dashboard's **Task registry** panel reads this list, `GET /ratchet/tasks/{name}` for its drawer, and the write results below. The keys it reads are listed with their JSON types in `tests/fixtures/tasks_keys.json` and checked by `test_task_registry_contract`; like `/ratchet/stats`, these responses add keys and never remove or retype one. The panel writes API tasks with an admin key the operator types in, sent only as `Authorization: Bearer` and kept in the page's memory ([ADR 0100](adr/0100-dashboard-task-writes-and-derived-breaker-state.md)).

### GET `/ratchet/tasks/{name}`

One entry as above; for an API or auto task also `created_ts`, `updated_ts` and `updated_by` (the admin key's name; `null` for an auto task, which no admin wrote). **404** for a name that is not registered.

### POST `/ratchet/tasks/validate`

A dry run, with no change to the registry, the ledger or the event log. The body is either one task spec (checked as `PUT` would take it, so a built-in or file task's name is an error) or a tasks file document `{"tasks": [...]}` (checked as startup reads `RATCHET_TASKS_FILE`: it may adjust or extend a built-in task and extend its own tasks, but not an API task or a task of the file running now, because startup registers the file before the stored API tasks; and its tasks, with the built-in ones it does not replace, must fit in `max_tasks`). A document that validates starts the gateway. Always **200**:

```json
{"valid": false, "errors": ["tasks[1].fields.total.abs_tol: must be a number >= 0"], "tasks": []}
{"valid": true, "errors": [], "tasks": [{"name": "invoice-extract", "spec_hash": "1c7e77bb7041b4d8", "registered": null}]}
```

`registered` is the source of the task that has the name now (`builtin`, `file`, `api`, `auto`), or null.

### PUT `/ratchet/tasks/{name}`

Create or replace an API task (an auto task of that name becomes an API task). The body is one spec without `name` (or with the same name as the URL); `extends` may name any registered task, the task's own name included (a new version built on the current one).

```bash
curl -X PUT localhost:8000/ratchet/tasks/invoice-extract -H "Authorization: Bearer $ADMIN_KEY" \
  -d '{"description": "Vendor and total from an invoice", "fields": {"total": {"type": "number", "abs_tol": 0.01}}}'
```

| Status | When |
|---|---|
| 200 | `{"task": {...entry...}, "spec_hash": "...", "changed": true, "created": true}`. A spec identical to the stored one is a 200 with `changed: false` and changes nothing; for a sql task the fixture is read again, so the same body after its text changed is a change (a new `spec_hash`) |
| 401, 403 | No admin key, a wrong one or an API key; the admin API is off (live mode without `RATCHET_ADMIN_KEYS`) |
| 403 | The name is a built-in task (`task 'x' is built in; use another name (a spec may extend it)`) or a file task (`task 'x' is defined in RATCHET_TASKS_FILE; edit the file`) |
| 409 | The registry holds `max_tasks` tasks, or (mock mode) 50 API tasks already |
| 422 | The body is not strict JSON (NaN is refused), names another task, or breaks a spec rule; `clients` names a key that is not in `RATCHET_API_KEYS`; a sql task's fixture is outside `RATCHET_FIXTURES_DIR`, missing, over 5 MB, not UTF-8 or does not load (`sql.fixture: fixture '../x.sql' is outside RATCHET_FIXTURES_DIR`), or (Python 3.10) needs Python 3.11 |

The event log records `task_registered` (`name (spec <hash>) by <admin>`) or `task_changed` (`name: spec <old> -> <new> by <admin>`; for a change to `description`, `clients`, `match` or `privacy` only, the hash is kept). A changed spec does not touch stored examples: Night School judges them under the current spec, and the serving version keeps the task on the teacher until it is scored under the new one.

### DELETE `/ratchet/tasks/{name}`

Remove an API or auto task, or an auto task (`RATCHET_AUTO_TASK=system_prompt`), which frees its place under `RATCHET_MAX_AUTO_TASKS` (a request with its system prompt makes it again while there is room): `{"deleted": "invoice-extract", "spec_hash": "...", "examples_kept": 420}` and a `task_deleted` event. Its stored examples stay, for provenance (and stay trainable under the name's adhoc task unless it is registered again or pinned). Requests that name it then follow `RATCHET_UNKNOWN_TASKS`; the next request with an auto task's schema registers it again from that schema. **403** for a built-in or file task, **404** for a name that is not registered.

## Admin

Manual interventions with an audit trail ([ADR 0064](adr/0064-admin-api-compare-and-set-and-audit-trail.md)). Every route under `/ratchet/admin/`, its reads included, needs an admin key (`RATCHET_ADMIN_KEYS`; [Access levels](#access-levels)): **401** without a key or with a wrong one, **403** for an API key or a dashboard key, **403** `admin API disabled: set RATCHET_ADMIN_KEYS` in live mode without admin keys; open in mock mode without them (as `mock-admin`). `python -m ratchet.admin` does the same on the ledger file (see [CLI](#cli)).

Conventions:

- **Body.** A JSON object with only the keys each route names (**400** `unknown keys [...]` otherwise); an empty body is `{}`. Every call that can change something takes `reason`: one line of 3 to 500 characters saying why (**400** without it). It is redacted like a training example (an e-mail address becomes `[EMAIL]`) before it is stored, because it lands in the release log. `POST /ratchet/admin/tick` is the one route where it is optional.
- **Answer.** `{"action", "target", "changed", "before", "after", "action_id"}`: what was acted on (`v7`, a switch, a task, `gate`), its state before and after, and the `admin_actions` row written (`null` when the call changed nothing: a switch already set, a canary already at that share; nothing is then written).
- **Record.** The change, its `admin_actions` row (time, actor `api:<admin key name>` or `cli:<OS user>`, action, target, reason, before, after, and the call's `x-request-id`) and an `admin` event (`v7 rolled back from canary 50% by api:ops: refund labels are wrong`) commit in one transaction. Keys are never stored, only names (invariant 13).
- **Conflicts.** A move the table below does not allow, or a version that another admin or the release gate moved while the call ran (every move compares and sets, FR-12.9), is **409** with what the version is now; nothing is written. **404** for an unknown version or task.
- **Effect.** Every process that shares the ledger sees a change on its next request, tick or night without a restart: the switches and pins are read through a snapshot that any write invalidates, also another process's (`PRAGMA data_version`).

| From | To | How | Needs `force` |
|---|---|---|---|
| `canary` | `rolled_back` | `rollback`: its traffic returns to the live version, or the teacher | No |
| `canary` | `retired` | `retire` | No |
| `canary` | `canary` (another share) | `promote` with `to: canary` | No |
| `canary` | `live` | `promote` with `to: live`: the live version is retired in the same transaction | No |
| `live` | `retired` | `retire`: the teacher answers what the canary (if any) does not | No |
| `shadow` | `retired` | `retire`: cancels the shadow, so tonight's training can run | No |
| `shadow` | `canary` | `promote` with `to: canary`: ends the shadow early | No |
| `rejected`, `rolled_back`, `retired`, `candidate` | `canary` | `promote` with `to: canary`: the golden gate or the canary did not pass it (or never saw it) | Yes |

Nothing becomes live except a canary, a version without a golden eval (no calibrated threshold) is never promoted, force or not, and a move into canary is refused while another version is in shadow or canary (one in flight at a time). With the runtime audit rate at 0, a new canary needs `force` (it would get no audits, so it could neither advance nor be rolled back).

### GET `/ratchet/admin/versions`

```json
{
  "versions": [
    {"id": 7, "day": 12, "state": "canary", "traffic_pct": 50, "adapter": "student-v7", "train_examples": 4210,
     "golden": {"precision": 0.97, "coverage": 0.91, "threshold": 0.74, "golden_n": 412},
     "stage_audits": {"n": 31, "precision": 0.968}},
    {"id": 6, "day": 9, "state": "live", "traffic_pct": 100, "adapter": "student-v6", "train_examples": 3890,
     "golden": {"precision": 0.96, "coverage": 0.9, "threshold": 0.76, "golden_n": 401},
     "stage_audits": {"n": 640, "precision": 0.95}}
  ],
  "controls": {"student_disabled": false, "releases_frozen": false, "training_paused": false, "audit_rate": null,
               "pinned_tasks": []}
}
```

Newest first. `stage_audits` (live and canary) counts the teacher's audits of what the version served in its current stage; a shadow has `shadow` instead (`Ledger.shadow_quality`: would-serve answers, precision, coverage, the live student's coverage, unarbitrated disagreements).

### GET `/ratchet/admin/flags`

`controls` as above, plus `set_by` (each stored switch: its raw value, when and by whom it was last set) and `task_pins` (each runtime pin: task, when, by whom and why).

### GET `/ratchet/admin/audit-log?limit=50`

`{"actions": [...]}`: the newest `limit` (1 to 1000, **400** otherwise) `admin_actions` rows, newest first, with `before` and `after` parsed.

### POST `/ratchet/admin/versions/{id}/rollback` and `.../retire`

Body `{"reason": "..."}`. Roll a canary back, or retire a live, canary or shadow version.

```bash
curl -X POST $GATEWAY/ratchet/admin/versions/7/rollback -H "Authorization: Bearer $ADMIN_KEY" \
  -d '{"reason": "refund labels are wrong since 09:00"}'
```

```json
{"action": "rollback", "target": "v7", "changed": true, "action_id": 12,
 "before": {"v7": {"state": "canary", "traffic_pct": 50}}, "after": {"v7": {"state": "rolled_back", "traffic_pct": 0}}}
```

### POST `/ratchet/admin/versions/{id}/promote`

Body `{"reason", "to", "traffic_pct", "force", "load_adapter"}`:

- `to`: `canary` or `live` (**400** otherwise).
- `traffic_pct`: a canary's share, 1 to 99; by default the next stage (10% from outside canary, then 50%). Leave it out for `live` (100%).
- `force`: allows the moves the table marks, and a canary while the audit rate is 0. The event says `(forced)`.
- `load_adapter`: in live mode, first load the version's LoRA adapter into the student endpoint (`/v1/load_lora_adapter` at `RATCHET_STUDENT_BASE`, path `RATCHET_ADAPTER_ROOT/<adapter>`), after the checks and before the move, outside the ledger's transaction. **409** without `RATCHET_STUDENT_BASE`; a failed load is **502** (`loading adapter student-v5 failed: HTTPStatusError 500; nothing changed`). Ignored in mock mode, whose fleet serves every version.

A promotion to live answers both versions: `before` `{"v7": canary 50, "v6": live 100}`, `after` `{"v7": live 100, "v6": retired 0}`.

### POST `/ratchet/admin/student/disable`, `.../enable`

Body `{"reason"}`. Sets `student_disabled`. While it is on, no request calls the student or the shadow candidate: the teacher answers (route `student_disabled`) and its answers are stored as on a cold start. A kill switch for a misbehaving endpoint or adapter that keeps every version's state as it is.

### POST `/ratchet/admin/releases/freeze`, `.../unfreeze`

Body `{"reason"}`. Sets `releases_frozen`. While it is on the release gate starts no version taking traffic and gives none more: no shadow is promoted to canary, no canary advances or becomes live, and a cold-start candidate shadows the teacher instead of taking 10%. Rollbacks, rejections and the live alert run as usual, so a bad canary is still rolled back. Lifted, the gate goes on from the evidence gathered meanwhile.

### POST `/ratchet/admin/training/pause`, `.../resume`

Body `{"reason"}`. Sets `training_paused`. While it is on, every night writes `skip` (`training paused by an admin; no training tonight`) and trains nothing.

### PUT `/ratchet/admin/settings`

Body `{"audit_rate", "reason", "force"}`. `audit_rate` is required: a number from 0 to 1 that replaces `RATCHET_AUDIT_RATE` (the share of student answers the teacher audits), or `null` to go back to it. 0 while a canary is in flight is **409** unless `force`: with no audits it could neither advance nor be rolled back. With the rate at 0 the gate also starts no new canary.

### POST and DELETE `/ratchet/admin/tasks/{name}/pin`

Body `{"reason"}`. Pin a registered task (built-in, file or API) to the teacher at runtime, or remove that pin. While pinned, its requests go to the teacher alone and nothing of them is stored (invariant 10), and Night School leaves its stored rows out of training and the golden eval from the next night on (`excluded_pinned`), as for a task its spec pins. The spec itself and its `spec_hash` do not change, so removing the pin needs no re-score. **404** for a name that is not registered; a task its spec pins answers `changed: false` to a pin and **409** to an unpin (change the spec instead).

### POST `/ratchet/admin/tick`

Body `{"reason"}`, optional. Runs the release gate now, as the cron tick does, and records it (action `tick`, target `gate`). `before` and `after` are the live, canary and shadow versions around it (a version that left them, such as a rolled-back canary, is in both), `moved` lists the versions whose state changed, and `changed` says whether any did. The gate's own events say why.

### CLI

`python -m ratchet.admin` works on `RATCHET_DB` (or `--db`; a missing file is an error, never created) with the actor `cli:<OS user>`, through the same functions. Exit 0 on success (`nothing changed` on stderr when nothing did), 1 for a refused move (a 409), 2 for a bad input or an unknown version or task.

```bash
python -m ratchet.admin versions [--json]
python -m ratchet.admin rollback 7 --reason "refund labels are wrong since 09:00"
python -m ratchet.admin retire 6 --reason "..."
python -m ratchet.admin promote 5 --to canary [--traffic-pct 10] [--force] --reason "..."
python -m ratchet.admin promote 5 --to live [--load-adapter] --reason "..."
python -m ratchet.admin flag                                            # the switches, who set them, the task pins (JSON)
python -m ratchet.admin flag student_disabled on --reason "..."         # on|off; also releases_frozen, training_paused
python -m ratchet.admin flag audit_rate 0.2 --reason "..."              # or `default`; --force for 0 during a canary
python -m ratchet.admin pin ticket-triage --reason "..."                # and unpin
python -m ratchet.admin audit-log [--limit 50] [--json]
```

## Demo controls (mock mode only)

All five return **403** `demo controls are only available in mock mode` when `RATCHET_MODE=live`.

The mock demo is shared by every visitor, so the controls protect themselves:

- **One demo action at a time.** `day` and `reset` take a lock without waiting. If another `day` or `reset` is still running, the call returns **409** `another demo action is running; try again in a moment`. (`break-next`, `student-outage` and `teacher-drift` do not take the lock.)
- **Reset cooldown.** `reset` returns **429** `the demo was reset less than 30 seconds ago` within 30 seconds (`RESET_COOLDOWN_S`) of the last successful reset.

The dashboard shows these messages under its buttons.

### POST `/ratchet/demo/day?requests=1500&workload=tickets`

Advances `Ledger.day` by one and sends `requests` simulated requests through the cascade, calling `gate.tick()` every `requests/10`. Then it runs one night (`run_night`). `requests` is clamped to 100–5000.

| Query param | Default | Values |
|---|---|---|
| `requests` | `1500` | Requests for the day (clamped to 100–5000) |
| `workload` | `tickets` | `tickets` (support tickets), `research` (Scout's triage and reader calls), `mixed` (half each), `schema` (structured outputs: `response_format` json_schema requests over three schemas under the tasks `review-rating`, `ticket-route` and `meeting-extract`, and from day 5 `warranty-claim`, with nested records) or `tools` (support requests sent with the mock tool catalogue under the task `order-support`). Anything else returns **400** `workload must be one of [...]`; `schema` and `tools` return **400** under `RATCHET_MOCK_STRICT=1`, before the day starts, since only the generic mock world answers them |

With `workload=tickets` the night trains on `ticket-triage` only, as in v0.1. With any other workload it trains on every stored task. Each workload has its own seeded stream, so the streams do not affect each other. The dashboard's **Traffic** selector sets `workload` (default there: `mixed`). Until tool calls pass through the gateway, the default allowlists forward `tools` to neither model, so the `tools` workload is answered, stored and learned as free text. The numbers are simulation output from mock models, and the response says so (`simulation: true`).

```json
{"day": 4, "workload": "tickets", "simulation": true,
 "night": {"trained": false, "version": null, "admitted": false,
           "reason": "release in flight", "eval": null}}
```

`night.reason` is one of `admitted`, `rejected`, `release in flight`, `not enough new data`, `no golden set`.

### POST `/ratchet/demo/break-next`

Marks the next version name (`student-v{count+1}`) to be served corrupted once it reaches canary or live. Its billing, refund and security answers become confidently wrong. This is used to demonstrate automatic rollback. It writes a `fault` event.

```json
{"corrupt": "student-v4"}
```

### POST `/ratchet/demo/student-outage?seconds=60`

Fault injection for the student circuit breaker: for `seconds` (clamped to 5–300, default 60) every call to the production student endpoint, which serves the live and canary versions, fails as an unreachable endpoint does (`httpx.ConnectError`). Shadow candidates run on the mock staging endpoint and are not affected. It writes a `fault` event and returns when the outage ends (Unix time):

```json
{"seconds": 60, "until": 1792400060.0}
```

Send traffic (Scout, or **Run 1 day + night**) and the release log shows the breaker at work: a `student_error` event for the first failure (`v2: ConnectError`; later ones are logged on the 10th, 100th and 1000th), then, after 5 failures in a row, `circuit` `student: closed->open after 5 failures (ConnectError)`; requests go straight to the teacher (route `student_breaker`); after 30 s one probe (`open->half_open`), which fails while the outage lasts and doubles the wait (`half_open->open for 60 s ...`); once the endpoint is back, a probe succeeds and `half_open->closed`. Calling it again starts a new outage of the given length from now. The dashboard's **Resilience** panel runs this drill (30 s, 1 min or 2 min) and reads the breaker's state from the `circuit` events and the outage's end from the `fault` event's `ts` plus its length; the wording of both events is parsed there, and `test_resilience_panel_contract` checks the parser against the gateway's events.

### GET `/ratchet/demo/sample-example?split=train&version=3`

A committed example of `version` (default: the live version; **404** `no live version yet` before there is one) from its `split` set (`train` or `golden`, default `train`; anything else is **400**), as a body for [`POST /ratchet/versions/{id}/proof`](#post-ratchetversionsidproofsplittrain): its task, its stored (redacted) messages and the conditions it was stored under, never its answer. It is the first leaf, in key order, whose example is still stored; **404** for an unknown version or one with no such example. Read-only and lock-free.

```json
{"version": 3, "split": "train", "task": "ticket-triage",
 "messages": [{"role": "system", "content": "You triage customer support tickets. …"}, {"role": "user", "content": "…"}]}
```

Post it back with `split=train` for an `included` proof, or take a `golden` sample and post it with `split=train` for an `absent` one.

### POST `/ratchet/demo/teacher-drift?noise=0.15`

Fault injection for the [teacher watch](#teacher-watch): from now on the mock teacher changes the first label of an answer (a ticket's category) to another value with probability `noise` (0 to 1, default 0.15; **400** outside it). 0 makes it stable again; a reset does too. Each draw is seeded by the request text and a call counter, so a run is reproducible. It writes a `fault` event:

```json
{"noise": 0.15}
```

Then run days: re-tests and Night School's probes start to disagree with the stored labels, contested labels are adjudicated each night (`teacher_watch` events), and once the e-value reaches 100 a `teacher_drift` event freezes Night School (`skip` events) until `POST /ratchet/teacher/ack`. At 0.15 that takes a few days of mock traffic, because a student that serves most requests leaves few re-tests to the teacher (simulation).

### POST `/ratchet/demo/reset`

Rebuilds the runtime: new in-memory ledger, new mock fleet, all three traffic streams reset, new Scout budget guard. If `RATCHET_MOCK_WARMUP_DAYS` is set, the warm-up runs again before the call returns.

```json
{"status": "reset"}
```

---

## Scout endpoints

Scout is the multi-agent research demo. The event contract (every field of `start`, `step`, `done` and `error` events) is in [SCOUT.md](SCOUT.md#event-objects-sse-data-payloads-in-order). This section covers the HTTP side.

Before a run starts, both run endpoints check, in this order:

| Check | `/scout/run` | `/scout/stream` |
|---|---|---|
| Question empty after trimming | 400 `Ask a question.` | `error` event |
| Question longer than 300 characters | 400 | `error` event |
| Mock mode and the question is not one of the example questions (exact match) | 400 | `error` event |
| Live mode without `TAVILY_API_KEY` | 503 | `error` event |
| Budget guard refuses: daily budget used, daily run cap reached, or per-IP hourly limit | 429 (message says which) | `error` event |

A run that passes these checks counts against the per-IP hourly limit and the daily run cap, even if it fails later. Its spend is added to the daily total step by step, so a run that fails halfway still counts what it spent. In live mode the counters are kept in the ledger and restored at startup ([ADR 0120](adr/0120-judge-access-and-a-persistent-scout-guard.md)).

**Judge runs.** A request that carries a valid `ratchet_judge` cookie (from `POST /scout/judge`) is a judge run: it draws on the runs and spend held back for judges (`RATCHET_SCOUT_JUDGE_RUNS_PER_DAY`, `RATCHET_SCOUT_JUDGE_BUDGET_USD`), and on public room that is left, and has its own per-IP hourly window. Only the cookie counts: a `code` query parameter or body field is ignored. When the public share is used up, the public gets 429 `The public demo has reached its run limit for today. ...` or `The daily demo budget is used up. ...`; a judge run past the whole allowance gets `Today's demo allowance, judges' share included, is used up.` None of them mentions a code.

**Search unavailable.** A search that fails (any HTTP or transport error) or that the Tavily credit meter refuses does not end the run: the searcher step carries `detail.unavailable` (the reason), no more queries are made, triage and reader run on whatever earlier queries found, and the writer answers (without sources if nothing was found). The `done` event says so in `search`. The planner, pinned to the teacher, has called Token Factory before any search, so every admitted run makes a runtime Token Factory call.

The client IP is the TCP peer address, or the first `X-Forwarded-For` entry when `RATCHET_TRUST_PROXY=1` ([CONFIGURATION.md](CONFIGURATION.md#scout-the-multi-agent-demo)).

### GET `/scout/examples`

```json
{"mode": "mock", "examples": ["What is LoRA fine-tuning?", "What is knowledge distillation?", "..."], "free_text": false}
```

`examples` is `ratchet.scout.prompts.EXAMPLE_QUESTIONS` (8 questions). `free_text` is `true` only in live mode.

### GET `/scout/status`

```json
{"mode": "live", "budget_usd": 3.0, "spent_today_usd": 0.0412, "runs_today": 3,
 "runs_per_hour_per_ip": 6, "max_runs_per_day": 22, "tavily": true,
 "fallback_student": "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
 "student_serving": {"kind": "hosted_fallback", "model": "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B", "version": 3,
                     "threshold": 0.8, "certified": false}}
 "public_runs_left_today": 13, "public_budget_left_usd": 2.0088, "judge_runs_today": 1, "persistent": true,
 "judge": {"enabled": true, "session": false, "reserved_runs_per_day": 7, "runs_today": 1, "runs_left_today": 19},
 "search": {"provider": "tavily", "available": true, "last_error": null, "errors_today": 0,
            "credits": {"monthly": 1000, "used_this_cycle": 9, "left": 991, "public_left_today": 39,
                        "judge_reserve": 318, "judge_left": 315, "cycle_start": "2026-12-01",
                        "pace_until": "2026-12-15", "exhausted_until": null}}}
```

| Field | Meaning |
|---|---|
| `budget_usd` | `RATCHET_SCOUT_DAILY_BUDGET_USD`; `null` in mock mode (no budget) |
| `spent_today_usd` | Ratchet spend on Scout runs this UTC day (`cost_usd` of every step, including audits) |
| `runs_today` | Runs admitted this UTC day |
| `runs_per_hour_per_ip` | Per-IP limit. Mock mode uses 10 × `RATCHET_SCOUT_RUNS_PER_HOUR` |
| `max_runs_per_day` | `RATCHET_SCOUT_MAX_RUNS_PER_DAY`; `null` in mock mode (no cap) |
| `tavily` | `true` when live search (Tavily) is configured |
| `fallback_student` | v0.5: the hosted fallback's model id, as in `/ratchet/stats`. A `step` whose `served_by` is `student` and whose `model` is this id came from the untuned hosted Nano, not our adapter |
| `student_serving` | v0.5: the same object as in [`/ratchet/stats`](#get-ratchetstats), so the Scout page labels each call without a stats call |
| `public_runs_left_today` | Runs the public can still start today: the run cap minus the judges' reserve, minus public runs and judge runs past the reserve; `null` without a cap (mock mode) |
| `public_budget_left_usd` | The same for spend; `null` without a budget. A public run can be refused a little earlier, while a judge budget is reserved: the largest finished run today is kept as a margin |
| `judge_runs_today` | Judge runs admitted today |
| `persistent` | `true` when the counters are kept in the ledger (live mode) and survive a restart |
| `judge` | `enabled` (judge access is on), `session` (this request carries a valid judge cookie), `reserved_runs_per_day`, `runs_today`, `runs_left_today` (the reserve plus public room left; `null` without a cap) |
| `search` | `provider` (`tavily`, `mock-search` or `null`), `available` (configured, not paused after Tavily's 432 or 433, and the last search did not fail), `last_error` (the last failure's class and status, `null` once a search works again), `errors_today` (failures, not credit refusals) and `credits` (`null` without a meter; otherwise the cycle's credits, used (searches in flight included) and left, the public's allowance left today, the judge reserve and what is left of it, the cycle's first day, `RATCHET_TAVILY_PACE_UNTIL` and when a pause after a 432 or 433 ends; `null` limits when `RATCHET_TAVILY_MONTHLY_CREDITS` is 0) |

Every v0.2 key keeps its name and type; new keys are only added. In live mode the counters are restored from the ledger at startup and reset at 00:00 UTC; in mock mode they live in memory.

### POST `/scout/judge`

Opens a judge session. Body: `{"code": "<RATCHET_SCOUT_JUDGE_CODE>"}`. The code travels only here, in a POST body, because uvicorn and Caddy write URLs and custom headers to their access logs. The body is parsed by hand, so no validation error can echo it.

| Status | When |
|---|---|
| 200 | `{"judge": true, "expires": "2026-12-08T10:00:00+00:00", "runs_left_today": 19}` and `Set-Cookie: ratchet_judge=<signed value>; Path=/scout; Max-Age=<RATCHET_SCOUT_JUDGE_SESSION_S>; HttpOnly; SameSite=Strict` (plus `Secure` in live mode). Writes the event `judge` `judge session opened`, with no IP and no code |
| 400 | `Send {"code": "..."}.`: not a JSON object with a non-empty string `code` |
| 401 | `That code is not valid.`, with no cookie |
| 404 | `Judge access is not enabled on this demo.` (`RATCHET_SCOUT_JUDGE_CODE` is empty) |
| 429 | More than 10 attempts from this IP in a rolling hour (well-formed attempts, the right code included) |

The cookie is `v1.<expires>.<nonce>.<HMAC-SHA256>`, keyed from the code: it survives a restart, and rotating the code ends every session. `SameSite=Strict` keeps another site from starting a judge's runs through `/scout/stream`, which is a GET.

### DELETE `/scout/judge`

204, and clears the cookie (`Max-Age=0`). Idempotent; needs no session.

### GET `/scout/stream?q=<question>`

Server-Sent Events (`text/event-stream`). Each event is one `data:` line holding a JSON object: `start`, then `step` events as agents finish, then `done`. Lines starting with `:` are keep-alive comments, sent every 15 seconds while waiting.

This endpoint **always answers HTTP 200**, because the browser's `EventSource` cannot read status codes. Any failure, before or during the run, arrives as a single `error` event, which ends the stream.

In mock mode the run waits `RATCHET_MOCK_PACE` × each call's simulated latency, so the page shows agents working.

```bash
curl -sN "http://localhost:8000/scout/stream?q=What%20is%20LoRA%20fine-tuning%3F"
```

### POST `/scout/run`

Request body: `{"question": "What is LoRA fine-tuning?"}`. Runs without pacing and returns once the run ends:

```json
{"events": [{"type": "start", "...": "..."}, {"type": "step", "...": "..."}, {"type": "done", "...": "..."}]}
```

| Status | When |
|---|---|
| 200 | Run finished; the last event is `done`. Also when search was unavailable: `done.search.available` is `false` and the answer was written without (some) web sources |
| 400 / 429 / 503 | Pre-run checks above |
| 502 | A model call failed partway (`a model or search call failed: upstream 503`, or `upstream 502 upstream_unreachable`, `upstream 504 upstream_timeout`: the status and code, never the URL), or anything else raised (`run failed: <type>: <message>`). `detail` is the `error` event's message. A failed search is no longer a 502 (v0.5) |
| 422 | Body is not JSON |

---

## Python API (embedding Ratchet without HTTP)

```python
from ratchet.config import Settings
from ratchet.ledger import Ledger
from ratchet.providers import OpenAICompatProvider
from ratchet.release import ReleaseGate
from ratchet.router import Ratchet

s = Settings(mode="live")
ledger = Ledger(s.db_path, sync=s.ledger_sync())  # a file: the WAL journal unless RATCHET_DB_SYNC=FULL (ADR 0060)
# The gateway's timeouts and retries: the teacher retried within its deadline, the student never (the teacher is its retry).
teacher = OpenAICompatProvider(s.teacher_base, s.teacher_api_key, timeout=s.teacher_timeout(), retry=s.teacher_retry())
student = OpenAICompatProvider(s.student_base, s.student_api_key, timeout=s.student_timeout())
r = Ratchet(s, ledger, teacher, lambda v: (student, v.adapter_uri))
gate = ReleaseGate(ledger)

result = r.handle([{"role": "user", "content": "..."}], "ticket-triage")
gate.tick()
print(result.served_by, result.text, result.cost_usd)

# With the client's generation parameters (what the gateway passes) and what else the caller knows:
from ratchet.providers import GenParams
from ratchet.router import RequestMeta

result = r.handle(msgs, "ticket-triage", client="acme", params=GenParams(max_tokens=200, stop=("\n\n",)), meta=RequestMeta())
print(result.finish_reason)  # "stop", or "length" when the answer was cut off
print(result.prompt_tokens, result.completion_tokens, result.total_tokens, result.fingerprint)

# Two choices, with token logprobs:
result = r.handle(msgs, "ticket-triage", params=GenParams(logprobs=True, top_logprobs=2), n=2)
for choice in result.choices:
    print(choice.text, choice.finish_reason, choice.logprobs)  # logprobs: OpenAI's content entries, or None

# Wall-clock latency (requests.wall_ms, the whole cascade) by nearest rank, of one day or all days, and per day:
print(ledger.latency_percentiles(day=None, served_by="student", qs=(0.5, 0.95)))  # {0.5: ms, 0.95: ms}, None if no rows
print([(d["day"], d["p50_student_ms"], d["p95_ms"]) for d in ledger.stats_by_day()])
```

Tasks for an embedded `Ratchet` come from the same registry: `ratchet.taskspec.install(ledger, "tasks.json")` registers the built-in tasks, a tasks file and the API tasks a ledger stored, and `ratchet.tasks.register(taskspec.parse_spec(spec, "api"))` adds one ([ADR 0022](adr/0022-task-registry.md)). `r.task_for(name, client)` applies `RATCHET_UNKNOWN_TASKS` and a task's `clients` as the gateway does (`tasks.UnknownTask`, `tasks.TaskForbidden`); `handle` applies the unknown-task policy only.

`handle(messages, task=None, client=None, params=None, meta=None, n=1)`: `task` is a task name or a `tasks.Task`; `n` is the number of choices ([Choices and logprobs](#choices-and-logprobs)), and `teacher_only` takes it too, with an optional `route` for its row (the gateway passes `multimodal`); a request whose `params.tools` or messages make it a tool request goes to the teacher alone (route `tools`, [Tools and function calling](#tools-and-function-calling)); `params` (`providers.GenParams`, `GenParams.from_request(chat_request)` in the gateway) are sent to each call as `Settings.gen_policy()` allows ([Generation parameters](#generation-parameters)); `meta` (`router.RequestMeta`) carries what else the caller knows: `RequestMeta(storable=False)` keeps the request's teacher answers out of the training set (with `store_opt_out` naming why, `request` by default), and `end_user` is the end user's hash (`privacy.EndUsers.hash`, never the raw value), with `end_user_aliases` the other hashes a no-train registration of that end user may use (`privacy.EndUsers.aliases`; looked up, never stored). `Result.stored` and `Result.store_opt_out` say what happened; zero retention, no-store tasks and no-train end users apply to every caller, Scout included. A provider's `complete(messages, model, n, temperature, logprobs, response_format, params=None, **_)` receives the role's `GenParams`; the explicit arguments win over the same fields in it.

`result.route` says how the request was served (the routes table under [`/ratchet/stats`](#get-ratchetstats)); `result.text` is the answer after `tasks.clean_answer`. `result.choices` are the response's choices (`router.Choice`: `text`, `finish_reason`, `logprobs`, OpenAI's `logprobs.content` entries when `params.logprobs` was set and they could be given, else None, and `tool_calls`, the teacher's tool calls for a tool request, else None); `result.text` and `result.finish_reason` are choice 0's. `result.prompt_tokens`, `completion_tokens`, `reasoning_tokens`, `cached_tokens` and `total_tokens` are the response's [usage](#usage), `result.usage_breakdown` its `ratchet.usage_breakdown` (keys in `router.USAGE_PARTS`), and `result.fingerprint` its `system_fingerprint` (`router.fingerprint(...)`). Each `Completion` a provider returns carries `finish_reason` (`stop` when the server leaves it out or sends an empty string), `reasoning_tokens`, `cached_tokens` (from `usage.prompt_tokens_details`, 0 when absent) and `logprobs_content` (the choice's `logprobs.content` as the server sent it, or None; `providers.served_logprobs` turns it into what a client gets); only a teacher answer with `finish_reason` `stop` or `tool_calls` that is non-empty and valid for its task is stored (invariant 16), and `requests.stored` says whether a request stored one. `result.degraded` is `"teacher_unavailable"` for a degraded answer, else None. `result.task` is the resolved task's name and `result.schema_valid` whether every choice meets the request's `response_format` (None without one); `r.resolve(task, params)` is the task a request with those parameters gets (a registered task, or a schema's auto task, bound to the `response_format`; FR-1.40, FR-14.2), and `r.task_for(task, client, params)` is the same with the gateway's admission checks. `r.health()` returns the failure counters (`student_errors`, `audit_errors`, `shadow_errors`, `repair_errors` for schema repairs that raised), the answers a stand-in wrote for an unavailable teacher (`degraded_answers`, `fallback_answers`) and `breakers`: each breaker's state by name (`student`, `shadow`, `teacher`, `fallback_teacher`), as `{"state": "closed" | "open" | "half_open", "failures": n, "open_until": t, "open_s": s}`, where `open_until` is on the breaker's clock (`time.monotonic()` unless `Ratchet(clock=...)` was given). `Ratchet(..., on_adapter_missing=callback)` is called with the version when vLLM answers 404 for its adapter; the gateway passes `live.AdapterReloader.request`. `Ratchet(..., timer=time.perf_counter)` is the clock `requests.wall_ms` is measured on (tests pass a fake one).

Off-path checks ([ADR 0072](adr/0072-off-path-audits-and-shadow.md)): a `Ratchet` builds its `sidecar` from `Settings.sidecar()` (live mode: 4 workers; mock mode: none), and a call that passes a `RequestMeta` then runs its audit and shadow after it returns: `result.overhead_pending` is True, `result.cost_usd` is the serving cost and `result.audited` False, and the ledger row is finished when the job ends. Call `r.close(drain_s)` before the process exits so queued jobs finish (it returns `r.sidecar_report()`: the queue's counts and, per version, `audits_inline`, `shadows_dropped`, `audits_abandoned`, `shadows_abandoned`). A call without `meta`, or with `RequestMeta(offpath=False)`, or a `Ratchet(..., sidecar=ratchet.sidecar.SideCar(0))`, keeps every check inline, as Scout's agents and the simulator do.

When the teacher's answer was needed and the teacher failed after its retries (and no fallback teacher or degraded answer stood in), `handle` raises `ratchet.providers.UpstreamError` (an `httpx.HTTPError`): `status` (504 for a timeout, 502 when Token Factory could not be reached), and the teacher's `type`, `code`, `param`, `message` and `retry_after` when it sent them; while the teacher's breaker is open, `ratchet.resilience.CircuitOpen` (503, code `teacher_circuit_open`, `retry_after` until the next probe). It never carries the URL or headers. `Ratchet(..., fallback_teacher=provider)` sets the fallback teacher's provider (default: the teacher's); it is used only when `RATCHET_TEACHER_FALLBACK_MODEL` is set.
