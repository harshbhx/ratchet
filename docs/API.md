# HTTP API

The gateway (`ratchet/gateway.py`, FastAPI app `ratchet.gateway:app`) speaks the OpenAI chat-completions format, so any OpenAI SDK works. It also exposes a small Ratchet API for stats and provenance, and mock-only demo controls.

```bash
RATCHET_MODE=mock uvicorn ratchet.gateway:app --port 8000      # offline demo
RATCHET_MODE=live NEBIUS_API_KEY=... uvicorn ratchet.gateway:app --port 8000
```

FastAPI also serves interactive docs at `/docs` and the schema at `/openapi.json`.

| Method | Path | Mode | Purpose |
|---|---|---|---|
| GET | `/` | both | Dashboard (HTML) |
| GET | `/v1/models` | both | OpenAI-style model list |
| POST | `/v1/chat/completions` | both | The cascade |
| GET | `/ratchet/stats` | both | Everything the dashboard shows |
| GET | `/ratchet/bom` | both | Model bill of materials (provenance) |
| POST | `/ratchet/demo/day` | mock only | Simulate one day of traffic and one night of training |
| POST | `/ratchet/demo/break-next` | mock only | Make the next trained student deploy corrupted |
| POST | `/ratchet/demo/reset` | mock only | Start over |

> **No authentication in v0.1.** The gateway does not check any API key. Put it behind a private network or an authenticating proxy before exposing it (see [THREAT_MODEL.md](THREAT_MODEL.md)).

---

## POST `/v1/chat/completions`

### Request

| Field | Type | Required | Notes |
|---|---|---|---|
| `messages` | array of `{role, content}` | yes | Must be a non-empty list, or the response is 400 |
| `model` | string | no | Ignored; Ratchet chooses the model. Use `"ratchet"` |
| `stream` | bool | no | `true` returns 400 (streaming not supported yet) |
| `metadata.ratchet_task` | string | no | Task name, used if the header is absent |
| any other field | | no | Ignored (`temperature`, `max_tokens`, `tools`, … are not forwarded in v0.1) |

| Header | Meaning |
|---|---|
| `X-Ratchet-Task` | Task name. Takes precedence over `metadata.ratchet_task`. Unknown names create a plain-text task on the fly (see [CONFIGURATION.md](CONFIGURATION.md#tasks)). Missing means `default` |

The task decides how answers are compared. `ticket-triage` compares only the JSON fields `category` and `priority`; `default` compares the whole normalised text.

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

`model` is the model that produced the served answer: the adapter name (`student-vN`) or the teacher model ID. `usage` is not returned in v0.1.

Response header: `X-Ratchet-Served-By: student|teacher`.

### Errors

| Status | When | Body `detail` |
|---|---|---|
| 400 | `stream: true` | `streaming is not supported yet` |
| 400 | `messages` missing, not a list, or empty | `messages is required` |
| 400 | Mock mode, message is not one of the synthetic demo tickets | `mock mode only answers demo tickets: '...'` |
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

client = OpenAI(base_url="http://localhost:8000/v1", api_key="unused")
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

In mock mode, only the synthetic tickets generated by `sim/tickets.py` can be answered. Use the demo endpoints, or take a ticket from `sim.tickets.TicketStream` in Python.

---

## GET `/v1/models`

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
  "events": [{"day": 9, "kind": "live", "detail": "v5 promoted to 100% (audited precision 94.1%, n=17)"}],
  "totals": {"requests": 13500, "cost_usd": 5.21, "baseline_usd": 14.58, "saved_usd": 9.37, "savings_pct": 0.64},
  "bill_of_materials": {"generated_at": 1792400000.0, "examples_sha256": "07ac2c…", "sources": [ … ]}
}
```

- `days`: one entry per `requests.day` (see `Ledger.stats_by_day`). `audit_agreement` is `null` on days without audits.
- `versions`: every version, oldest first.
- `events`: the last 40 events, oldest first.

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

---

## Demo controls (mock mode only)

All three return **403** `demo controls are only available in mock mode` when `RATCHET_MODE=live`.

### POST `/ratchet/demo/day?requests=1500`

Advances `Ledger.day` by one and sends `requests` synthetic tickets through the cascade, calling `gate.tick()` every `requests/10`. Then it runs one night (`run_night`). `requests` is clamped to 100–5000.

```json
{"day": 4, "night": {"trained": false, "version": null, "admitted": false,
                      "reason": "release in flight", "eval": null}}
```

`night.reason` is one of `admitted`, `rejected`, `release in flight`, `not enough new data`, `no golden set`.

### POST `/ratchet/demo/break-next`

Marks the next version name (`student-v{count+1}`) to be served corrupted once it reaches canary or live. Its billing, refund and security answers become confidently wrong. This is used to demonstrate automatic rollback. It writes a `fault` event.

```json
{"corrupt": "student-v4"}
```

### POST `/ratchet/demo/reset`

Rebuilds the runtime: new in-memory ledger, new mock fleet, ticket stream reset.

```json
{"status": "reset"}
```

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
