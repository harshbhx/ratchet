# HTTP API

The gateway (`ratchet/gateway.py`, FastAPI app `ratchet.gateway:app`) speaks the OpenAI chat-completions format, so any OpenAI SDK works. It also exposes a small Ratchet API for stats and provenance, mock-only demo controls, three web pages and the Scout demo endpoints.

```bash
RATCHET_MODE=mock uvicorn ratchet.gateway:app --port 8000      # offline demo
RATCHET_MODE=live NEBIUS_API_KEY=... uvicorn ratchet.gateway:app --port 8000
```

FastAPI also serves interactive docs at `/docs` and the schema at `/openapi.json`.

| Method | Path | Mode | Purpose |
|---|---|---|---|
| GET | `/` | both | Story page: the React app (`ratchet/web/dist/index.html`); falls back to `ratchet/web/landing.html` if the build is missing |
| GET | `/dashboard` | both | Dashboard (React app; fallback `ratchet/dashboard.html`) |
| GET | `/scout` | both | Scout demo page (React app; fallback `ratchet/web/scout.html`) |
| GET | `/assets/*` | both | Built JavaScript, CSS and fonts of the React app |
| GET | `/lite`, `/lite/scout`, `/lite/dashboard` | both | The hand-written pages (no build step, no JavaScript framework) |
| GET | `/healthz` | both | Liveness check; always public |
| GET | `/v1/models` | both | OpenAI-style model list (API key when keys are on) |
| POST | `/v1/chat/completions` | both | The cascade; `stream: true` supported (API key when keys are on) |
| GET | `/ratchet/stats` | both | Everything the dashboard shows |
| GET | `/ratchet/bom` | both | Bill of materials for the whole ledger (provenance) |
| GET | `/ratchet/versions/{id}/bom` | both | One adapter's bill of materials, written at training time, signed when a key is set |
| GET | `/ratchet/bom/public-key` | both | Ed25519 public key that verifies adapter BOMs |
| POST | `/ratchet/redact` | both | Show what the PII redactor would store for a text (stores nothing) |
| POST | `/ratchet/demo/day` | mock only | Simulate one day of traffic and one night of training |
| POST | `/ratchet/demo/break-next` | mock only | Make the next trained student deploy corrupted |
| POST | `/ratchet/demo/reset` | mock only | Start over |
| GET | `/scout/examples` | both | Example questions and whether free text is allowed |
| GET | `/scout/status` | both | Budget guard state |
| GET | `/scout/stream?q=` | both | Run Scout, stream events (Server-Sent Events) |
| POST | `/scout/run` | both | Run Scout, return all events at the end |

The three pages are static HTML files with no build step. If a page file is missing, the route returns a small placeholder page with links to the other two.

## Authentication

Off by default, so the mock demo and local runs need no key. Set `RATCHET_API_KEYS` ([CONFIGURATION.md](CONFIGURATION.md#privacy-and-security)) to require a key on `/v1/*`:

```bash
RATCHET_API_KEYS="acme:$(openssl rand -hex 24),globex:$(openssl rand -hex 24)" uvicorn ratchet.gateway:app
```

- Send the key as `Authorization: Bearer <key>` (what every OpenAI SDK sends as `api_key`) or as `X-API-Key: <key>`.
- A missing or wrong key returns **401** `invalid or missing API key` with `WWW-Authenticate: Bearer`.
- The ledger records the key's **name** (`acme`) on every request it sends, never the key. `/ratchet/stats` reports requests, cost and savings per name under `clients`.
- `RATCHET_API_KEY_DAILY_REQUESTS=N` caps each key at N chat completions per UTC day. Responses carry `X-RateLimit-Limit-Requests` and `X-RateLimit-Remaining-Requests`; past the cap the gateway returns **429** until 00:00 UTC. Concurrent requests can overshoot the cap by the number in flight.
- Pages, `/healthz`, `/ratchet/*` and Scout stay public. The read-only `/ratchet/stats` and `/ratchet/bom` are what the dashboard reads; the mock-only demo controls are covered by the Caddy guard on the demo VM. Scout's agents call the cascade inside the process, so they never need a key; the Scout budget guard protects them.

The demo VM's Caddy config also has an opt-in edge guard for `/v1/*` and `/ratchet/demo/*` ([DEPLOY_NEBIUS_VM.md](DEPLOY_NEBIUS_VM.md#security-notes)). Use either or both. See [THREAT_MODEL.md](THREAT_MODEL.md) T1 and T19.

---

## POST `/v1/chat/completions`

### Request

| Field | Type | Required | Notes |
|---|---|---|---|
| `messages` | array of `{role, content}` | yes | Must be a non-empty list, or the response is 400 |
| `model` | string | no | Ignored; Ratchet chooses the model. Use `"ratchet"` |
| `stream` | bool | no | `true` returns Server-Sent Events in the OpenAI chunk format ([Streaming](#streaming)) |
| `metadata.ratchet_task` | string | no | Task name, used if the header is absent |
| any other field | | no | Ignored (`temperature`, `max_tokens`, `tools`, … are not forwarded yet) |

| Header | Meaning |
|---|---|
| `X-Ratchet-Task` | Task name. Takes precedence over `metadata.ratchet_task`. Unknown names create a plain-text task on the fly (see [CONFIGURATION.md](CONFIGURATION.md#tasks)). Missing means `default` |

The task decides how answers are compared. `ticket-triage` compares only the JSON fields `category` and `priority`; `default` compares the whole normalised text. Pinned tasks (`research-plan`, `research-write`) skip the student and always go to the teacher; their answers are not stored as training examples. The full list is in [CONFIGURATION.md](CONFIGURATION.md#tasks).

### Response (200)

Standard `chat.completion` plus a `ratchet` block:

```json
{
  "id": "chatcmpl-3f1c0a9b8e7d6c5b4a3f2e1d",
  "object": "chat.completion",
  "created": 1792400000,
  "model": "student-v5",
  "choices": [
    {"index": 0, "finish_reason": "stop",
     "message": {"role": "assistant",
                 "content": "{\"category\": \"billing\", \"priority\": \"medium\", \"reply\": \"Sorry about the billing trouble, I'm checking your charges now.\"}"}}
  ],
  "ratchet": {
    "served_by": "student",
    "student_version": 5,
    "confidence": 0.951,
    "escalated": false,
    "cost_usd": 0.0000312,
    "all_teacher_cost_usd": 0.00131,
    "latency_ms": 311.4
  }
}
```

| `ratchet` field | Meaning |
|---|---|
| `served_by` | `student` or `teacher` |
| `student_version` | Version that answered; `null` when the teacher answered (including escalations) |
| `confidence` | Student confidence if a student was tried, else `null` |
| `escalated` | `true` if the student was tried and was below its threshold |
| `cost_usd` | Actual cost of this request, including audit, shadow and arbitration calls |
| `all_teacher_cost_usd` | Estimated cost if the teacher had answered alone |
| `latency_ms` | Student latency plus teacher latency when the teacher answered |

`model` is the model that produced the served answer: the adapter name (`student-vN`) or the teacher model ID. `usage` is not returned yet.

Response headers: `X-Ratchet-Served-By: student|teacher`, plus `X-RateLimit-Limit-Requests` and `X-RateLimit-Remaining-Requests` when a daily quota is set.

### Streaming

With `"stream": true` the response is `text/event-stream` in the format OpenAI SDKs expect: a first chunk with `delta.role`, content chunks of a few words each, a last chunk with `finish_reason: "stop"` and the same `ratchet` block as above, then `data: [DONE]`.

```text
data: {"id":"chatcmpl-…","object":"chat.completion.chunk","created":1792400000,"model":"student-v5","choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}]}

data: {"id":"chatcmpl-…","object":"chat.completion.chunk",…,"choices":[{"index":0,"delta":{"content":"{\"category\": \"billing\", "},"finish_reason":null}]}

data: {"id":"chatcmpl-…","object":"chat.completion.chunk",…,"choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"ratchet":{"served_by":"student",…}}

data: [DONE]
```

The cascade has to see the student's whole answer to score its confidence before it decides who serves, so the answer is complete before the first chunk is sent: time to first token equals the full latency. Streaming exists so that clients built for streaming work unchanged. Errors (400, 401, 429, 502) are returned as normal HTTP errors before the stream starts.

```python
for chunk in client.chat.completions.create(model="ratchet", messages=msgs, stream=True):
    print(chunk.choices[0].delta.content or "", end="")
```

### Errors

| Status | When | Body `detail` |
|---|---|---|
| 400 | `messages` missing, not a list, or empty | `messages is required` |
| 401 | API keys are on and the key is missing or wrong | `invalid or missing API key` |
| 429 | The key's daily quota is used up | `daily request quota of N used up for 'acme'; resets at 00:00 UTC` |
| 400 | Mock mode, the last user message is not a synthetic demo ticket or a Scout request from the mock research world | `mock mode only answers demo tickets: '...'` |
| 422 | Body is not JSON | FastAPI validation error |
| 502 | Live mode: the teacher call failed (`httpx` error) | `{"detail": "teacher unavailable: ConnectError"}`. Student failures do not cause errors: they fall back to the teacher. See [RUNBOOK.md](RUNBOOK.md#playbooks) |

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

OpenAI Node SDK:

```js
import OpenAI from "openai";
const client = new OpenAI({ baseURL: "http://localhost:8000/v1", apiKey: "unused" });
const r = await client.chat.completions.create(
  { model: "ratchet", messages: [{ role: "user", content: "Where is my order #48213?" }] },
  { headers: { "X-Ratchet-Task": "ticket-triage" } },
);
```

In mock mode, only the synthetic tickets generated by `sim/tickets.py` and Scout's requests for the example questions (`sim/research.py`) can be answered. Use the demo endpoints, take a ticket from `sim.tickets.TicketStream` in Python, or run the Scout CLI (`python -m ratchet.scout "What is LoRA fine-tuning?" --base-url http://localhost:8000/v1`).

---

## GET `/healthz`

Always public, also when API keys are on. The demo VM's Docker health check uses it.

```json
{"status": "ok", "version": "0.4.0", "mode": "mock"}
```

## GET `/v1/models`

Needs a key when `RATCHET_API_KEYS` is set.

```json
{"object": "list", "data": [{"id": "ratchet", "object": "model", "owned_by": "you"}]}
```

## GET `/ratchet/stats`

```json
{
  "mode": "mock",
  "day": 9,
  "teacher": "nvidia/Nemotron-3-Ultra-550b-a55b",
  "student_base": "nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16",
  "days": [
    {"day": 3, "requests": 1500, "student_share": 0.98, "escalation_rate": 0.02,
     "cost_per_1k": 0.16, "baseline_per_1k": 1.093, "savings_pct": 0.85,
     "avg_latency_ms": 352.0, "audit_agreement": 1.0}
  ],
  "versions": [
    {"id": 5, "day": 7, "base_model": "nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16", "adapter_uri": "student-v5",
     "train_examples": 1801, "dataset_hash": "…",
     "eval": {"golden_n": 201, "golden_agreement": 0.94, "threshold": 0.76, "coverage": 0.89,
              "precision": 0.99, "test_n": 100, "rechecked_day": 9},
     "state": "live", "traffic_pct": 100, "stage_since": 12034}
  ],
  "tasks": [
    {"task": "ticket-triage", "pinned": false, "requests": 12000, "student_share": 0.84, "escalation_rate": 0.06,
     "cost_usd": 3.21, "baseline_usd": 13.1, "savings_pct": 0.75, "avg_latency_ms": 640.0},
    {"task": "research-plan", "pinned": true, "requests": 3, "student_share": 0.0, "escalation_rate": 0.0,
     "cost_usd": 0.0039, "baseline_usd": 0.0039, "savings_pct": 0.0, "avg_latency_ms": 2400.0}
  ],
  "events": [{"id": 57, "ts": 1792400000.0, "day": 9, "kind": "live", "detail": "v5 promoted to 100% (audited precision 94.1%, n=17)"}],
  "totals": {"requests": 13500, "cost_usd": 5.21, "baseline_usd": 14.58, "saved_usd": 9.37, "savings_pct": 0.64},
  "bill_of_materials": {"generated_at": 1792400000.0, "examples_sha256": "07ac2c…", "sources": [ … ]},
  "clients": [
    {"client": "acme", "requests": 40, "student_share": 0.95, "cost_usd": 0.0044, "baseline_usd": 0.044, "savings_pct": 0.9}
  ],
  "privacy": {"redaction": true, "examples": 2742, "redacted_examples": 12, "placeholders": 19},
  "security": {"api_keys": true, "daily_requests_per_key": 500, "bom_signing": true, "bom_key_id": "6cf6bc1a39c4491d"}
}
```

- `days`: one entry per `requests.day` (see `Ledger.stats_by_day`). `audit_agreement` is `null` on days without audits.
- `tasks`: one entry per task name over all days, busiest first (`Ledger.stats_by_task`). `student_share` and `escalation_rate` are shares of that task's requests; `savings_pct = 1 − cost_usd / baseline_usd`. Pinned tasks always show `student_share` 0 and `savings_pct` 0. `pinned` is true for tasks that always go to the teacher and are never trained on (`research-plan`, `research-write`); the dashboard's **By task** table shows them as "pinned to teacher".
- `versions`: every version, oldest first. `bom` is `signed`, `unsigned`, or `null` for versions trained before v0.4.
- `clients`: one entry per API key name that sent requests, busiest first (`Ledger.stats_by_client`). Empty when keys are off.
- `privacy`: whether redaction is on, how many stored examples had at least one identifier replaced, and how many placeholders were inserted in total (`Ledger.privacy_stats`).
- `security`: whether API keys are required, the daily quota per key (`null` = none), and whether adapter BOMs are signed (with the key id: the first 16 hex characters of the public key's SHA-256).
- `events`: the last 40 events, oldest first. `id` is the ledger row id (stable, use it as a key); `ts` is Unix time.

Compatibility: the keys the dashboard reads are listed with their JSON types in `tests/fixtures/stats_keys.json`, and `test_stats_contract` checks them on every run. New versions add keys; they do not remove or retype existing ones, so a client written against this page keeps working.

## GET `/ratchet/bom`

```json
{
  "generated_at": 1792400000.0,
  "examples_sha256": "07ac2ca35bac78959378cbabd98352540…",
  "sources": [
    {"teacher_model": "nvidia/Nemotron-3-Ultra-550b-a55b", "license": "OpenMDW-1.1",
     "license_url": "https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Ultra-550B-A55B-BF16",
     "split": "train", "n": 2085}
  ]
}
```

`examples_sha256` is a SHA-256 over all example hashes in sorted order. Two ledgers with the same examples give the same fingerprint.

## GET `/ratchet/versions/{id}/bom`

The bill of materials of one adapter, written by Night School right after training and evaluation, from the exact rows it was trained and evaluated on. **404** for an unknown id or a version trained before v0.4.

```json
{
  "manifest": {
    "schema": "ratchet.bom/v1",
    "generator": "ratchet 0.4.0",
    "created_at": "2026-10-20T00:15:12Z",
    "adapter": {"version": 1, "name": "student-v1", "base_model": "nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16", "day": 1},
    "training": {"examples": 1029, "dataset_sha256": "478eef…", "tasks": {"ticket-triage": 420, "source-triage": 362, "page-read": 247},
                 "targets": "teacher answers only", "examples_with_pii_redacted": 0},
    "golden": {"examples": 110, "sha256": "cbce0e…", "held_out": true},
    "sources": [{"teacher": "nvidia/Nemotron-3-Ultra-550b-a55b", "license": "OpenMDW-1.1",
                 "license_url": "https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Ultra-550B-A55B-BF16",
                 "distill_ok": true, "examples": 1029}],
    "eval": {"golden_n": 110, "golden_agreement": 0.89, "threshold": 0.65, "coverage": 0.96, "precision": 0.94, "test_n": 55}
  },
  "sha256": "9b1f…",
  "signature": {"alg": "Ed25519", "key_id": "6cf6bc1a39c4491d", "public_key": "IiNlIP8Y…", "value": "k2Hc…"}
}
```

- `sha256` and the signature cover the **canonical** manifest: JSON with sorted keys, no whitespace, UTF-8 (`ratchet.bom.canonical`).
- `signature` is `null` when `RATCHET_BOM_SIGNING_KEY` is not set; the `sha256` still detects edits.
- `training.dataset_sha256` equals the version's `dataset_hash`; `golden.sha256` fingerprints the golden rows the evaluation used. The `eval` block is the evaluation at training time; later re-checks update `/ratchet/stats` but not the signed BOM.

Verify a downloaded BOM against the public key you published:

```bash
curl -s https://<gateway>/ratchet/versions/3/bom > bom.json
python -m ratchet.bom verify bom.json --public-key "<base64 public key>"
# OK: signature valid (Ed25519, key 6cf6bc1a39c4491d, trusted key)
```

Without `--public-key` the check uses the key embedded in the document, which only proves the document is consistent with itself.

## GET `/ratchet/bom/public-key`

```json
{"alg": "Ed25519", "public_key": "IiNlIP8YSYKDKqEHNzxBnn+m37imeLeykRmrZEEl5I4=", "key_id": "6cf6bc1a39c4491d"}
```

`public_key` and `key_id` are `null` when signing is off. Publish the key somewhere you control as well (README, website): a key served by the same gateway proves nothing if the gateway itself is compromised.

## POST `/ratchet/redact`

Runs the PII redactor ([ARCHITECTURE.md](ARCHITECTURE.md#pii-redaction)) on a text and returns what would be stored. Nothing is logged or stored. Backs the dashboard's **Try the redactor** box.

```bash
curl -s localhost:8000/ratchet/redact -H 'content-type: application/json' \
  -d '{"text": "I am bob@example.com, card 4111 1111 1111 1111"}'
```

```json
{"text": "I am [EMAIL], card [CARD]", "found": {"EMAIL": 1, "CARD": 1}, "enabled": true}
```

`enabled` says whether this gateway redacts examples (`RATCHET_REDACT_PII`); the preview runs either way. **400** when `text` is missing or longer than 5,000 characters.

---

## Demo controls (mock mode only)

All three return **403** `demo controls are only available in mock mode` when `RATCHET_MODE=live`.

The mock demo is shared by every visitor, so the controls protect themselves:

- **One demo action at a time.** `day` and `reset` take a lock without waiting. If another `day` or `reset` is still running, the call returns **409** `another demo action is running; try again in a moment`. (`break-next` does not take the lock.)
- **Reset cooldown.** `reset` returns **429** `the demo was reset less than 30 seconds ago` within 30 seconds (`RESET_COOLDOWN_S`) of the last successful reset.

The dashboard shows these messages under its buttons.

### POST `/ratchet/demo/day?requests=1500&workload=tickets`

Advances `Ledger.day` by one and sends `requests` simulated requests through the cascade, calling `gate.tick()` every `requests/10`. Then it runs one night (`run_night`). `requests` is clamped to 100–5000.

| Query param | Default | Values |
|---|---|---|
| `requests` | `1500` | Requests for the day (clamped to 100–5000) |
| `workload` | `tickets` | `tickets` (support tickets), `research` (Scout's triage and reader calls), `mixed` (half each). Anything else returns **400** `workload must be one of [...]` |

With `workload=tickets` the night trains on `ticket-triage` only, as in v0.1. With `research` or `mixed` it trains on every stored task. Each workload has its own seeded stream, so the three streams do not affect each other. The dashboard's **Traffic** selector sets `workload` (default there: `mixed`).

```json
{"day": 4, "workload": "tickets",
 "night": {"trained": false, "version": null, "admitted": false,
           "reason": "release in flight", "eval": null}}
```

`night.reason` is one of `admitted`, `rejected`, `release in flight`, `not enough new data`, `no golden set`.

### POST `/ratchet/demo/break-next`

Marks the next version name (`student-v{count+1}`) to be served corrupted once it reaches canary or live. Its billing, refund and security answers become confidently wrong. This is used to demonstrate automatic rollback. It writes a `fault` event.

```json
{"corrupt": "student-v4"}
```

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

A run that passes these checks counts against the per-IP hourly limit and the daily run cap, even if it fails later. Its spend is added to the daily total step by step, so a run that fails halfway still counts what it spent.

The client IP is the TCP peer address, or the first `X-Forwarded-For` entry when `RATCHET_TRUST_PROXY=1` ([CONFIGURATION.md](CONFIGURATION.md#scout-the-multi-agent-demo)).

### GET `/scout/examples`

```json
{"mode": "mock", "examples": ["What is LoRA fine-tuning?", "What is knowledge distillation?", "..."], "free_text": false}
```

`examples` is `ratchet.scout.prompts.EXAMPLE_QUESTIONS` (8 questions). `free_text` is `true` only in live mode.

### GET `/scout/status`

```json
{"mode": "live", "budget_usd": 3.0, "spent_today_usd": 0.0412, "runs_today": 3,
 "runs_per_hour_per_ip": 6, "max_runs_per_day": 30, "tavily": true}
```

| Field | Meaning |
|---|---|
| `budget_usd` | `RATCHET_SCOUT_DAILY_BUDGET_USD`; `null` in mock mode (no budget) |
| `spent_today_usd` | Ratchet spend on Scout runs this UTC day (`cost_usd` of every step, including audits) |
| `runs_today` | Runs admitted this UTC day |
| `runs_per_hour_per_ip` | Per-IP limit. Mock mode uses 10 × `RATCHET_SCOUT_RUNS_PER_HOUR` |
| `max_runs_per_day` | `RATCHET_SCOUT_MAX_RUNS_PER_DAY`; `null` in mock mode (no cap) |
| `tavily` | `true` when live search (Tavily) is configured |

The counters live in memory and reset at 00:00 UTC and when the process restarts.

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
| 200 | Run finished; the last event is `done` |
| 400 / 429 / 503 | Pre-run checks above |
| 502 | The run failed partway: a model or search call raised an `httpx` error (`a model or search call failed: ConnectError`), or anything else raised (`run failed: <type>: <message>`). `detail` is the `error` event's message |
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
ledger = Ledger(s.db_path)
teacher = OpenAICompatProvider(s.teacher_base, s.teacher_api_key)
student = OpenAICompatProvider(s.student_base, s.student_api_key)
r = Ratchet(s, ledger, teacher, lambda v: (student, v.adapter_uri))
gate = ReleaseGate(ledger)

result = r.handle([{"role": "user", "content": "..."}], "ticket-triage")
gate.tick()
print(result.served_by, result.text, result.cost_usd)
```
