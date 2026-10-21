# HTTP API

The gateway (`ratchet/gateway.py`, FastAPI app `ratchet.gateway:app`) speaks the OpenAI chat-completions format, so any OpenAI SDK works. It also exposes a small Ratchet API for stats and provenance, mock-only demo controls, three web pages and the Scout demo endpoints.

```bash
RATCHET_MODE=mock uvicorn ratchet.gateway:app --port 8000      # offline demo
RATCHET_MODE=live NEBIUS_API_KEY=... uvicorn ratchet.gateway:app --port 8000
```

FastAPI also serves interactive docs at `/docs` and the schema at `/openapi.json`.

Errors on `/v1/*` use OpenAI's error envelope, so OpenAI SDKs raise typed exceptions with `code` and `param` ([Errors](#errors)); every other route answers errors as `{"detail": ...}`. Every `POST`, `PUT` and `PATCH` body is limited to `RATCHET_MAX_BODY_BYTES` (2 MiB by default; larger is **413**).

| Method | Path | Mode | Purpose |
|---|---|---|---|
| GET | `/` | both | Story page: the React app (`ratchet/web/dist/index.html`); falls back to `ratchet/web/landing.html` if the build is missing |
| GET | `/dashboard` | both | Dashboard (React app; fallback `ratchet/dashboard.html`) |
| GET | `/scout` | both | Scout demo page (React app; fallback `ratchet/web/scout.html`) |
| GET | `/assets/*` | both | Built JavaScript, CSS and fonts of the React app |
| GET | `/lite`, `/lite/scout`, `/lite/dashboard` | both | The hand-written pages (no build step, no JavaScript framework) |
| GET | `/healthz` | both | Liveness check; always public |
| GET | `/v1/models` | both | OpenAI-style model list: `ratchet`, `ratchet/<task>` per task, the model aliases (API key when keys are on) |
| GET | `/v1/models/{id}` | both | One model of the list, or 404 `model_not_found` (API key when keys are on) |
| POST | `/v1/chat/completions` | both | The cascade; `stream: true` supported (API key when keys are on) |
| POST | `/openai/deployments/{deployment}/chat/completions` | both | Azure OpenAI's URL for the same cascade, the deployment naming the task ([Azure-style route](#azure-style-route)) |
| GET | `/ratchet/stats` | both | Everything the dashboard shows |
| GET | `/ratchet/bom` | both | Bill of materials for the whole ledger (provenance) |
| GET | `/ratchet/versions/{id}/bom` | both | One adapter's bill of materials, written at training time, signed when a key is set |
| GET | `/ratchet/bom/public-key` | both | Ed25519 public key that verifies adapter BOMs |
| POST | `/ratchet/redact` | both | Show what the PII redactor would store for a text (stores nothing) |
| GET | `/ratchet/cost` | both | The cost report: savings per token and by GPU-hours paid, break-even, the hosted-fallback crossover and a recommendation ([Cost](#cost)) |
| POST | `/ratchet/cost/endpoint` | both | Record the student endpoint going up or down (admin key) |
| GET | `/ratchet/tasks` | both | Every registered task with its spec, source, spec hash and traffic ([Tasks](#tasks)) |
| GET | `/ratchet/tasks/{name}` | both | One registered task |
| POST | `/ratchet/tasks/validate` | both | Check a task spec or a tasks file without changing anything (admin key) |
| PUT | `/ratchet/tasks/{name}` | both | Create or replace an API task (admin key) |
| DELETE | `/ratchet/tasks/{name}` | both | Remove an API task; its stored examples stay (admin key) |
| POST | `/ratchet/demo/day` | mock only | Simulate one day of traffic and one night of training |
| POST | `/ratchet/demo/break-next` | mock only | Make the next trained student deploy corrupted |
| POST | `/ratchet/demo/student-outage` | mock only | Take the student endpoint down for a while, to show the circuit breaker |
| POST | `/ratchet/demo/reset` | mock only | Start over |
| GET | `/scout/examples` | both | Example questions and whether free text is allowed |
| GET | `/scout/status` | both | Budget guard state |
| GET | `/scout/stream?q=` | both | Run Scout, stream events (Server-Sent Events) |
| POST | `/scout/run` | both | Run Scout, return all events at the end |

The three pages are static HTML files with no build step. If a page file is missing, the route returns a small placeholder page with links to the other two.

## Authentication

Off by default, so the mock demo and local runs need no key. Set `RATCHET_API_KEYS` ([CONFIGURATION.md](CONFIGURATION.md#privacy-and-security)) to require a key on `/v1/*` and `/openai/*`:

```bash
RATCHET_API_KEYS="acme:$(openssl rand -hex 24),globex:$(openssl rand -hex 24)" uvicorn ratchet.gateway:app
```

- Send the key as `Authorization: Bearer <key>` (what every OpenAI SDK sends as `api_key`), as `X-API-Key: <key>`, or as `api-key: <key>` (what Azure OpenAI SDKs send), checked in that order.
- A missing or wrong key returns **401** `invalid or missing API key` (error code `invalid_api_key`) with `WWW-Authenticate: Bearer`. The key is checked before the body is read.
- The ledger records the key's **name** (`acme`) on every request it sends, never the key. `/ratchet/stats` reports requests, cost and savings per name under `clients`.
- `RATCHET_API_KEY_DAILY_REQUESTS=N` caps each key at N chat completions per UTC day. Responses carry `X-RateLimit-Limit-Requests`, `X-RateLimit-Remaining-Requests` and `X-RateLimit-Reset-Requests` (time to 00:00 UTC in OpenAI's duration format, rounded up to the second: `3h12m5s`). Past the cap the gateway returns **429** `insufficient_quota` (code `daily_quota_exceeded`) until 00:00 UTC, with `Retry-After` (seconds until then) and `X-Should-Retry: false`, so OpenAI SDKs send it once and do not retry. Only answered requests count: a refused request (400, 413, 429) uses no quota. Concurrent requests can overshoot the cap by the number in flight.
- **Admin keys** (`RATCHET_ADMIN_KEYS`, same syntax, never the same keys) guard management writes: `PUT` and `DELETE /ratchet/tasks/{name}`, `POST /ratchet/tasks/validate` ([Tasks](#tasks)) and `POST /ratchet/cost/endpoint` ([Cost](#cost)). Send one the same way. A missing or wrong key is **401** `invalid or missing admin key` (`WWW-Authenticate: Bearer`), an API key **403**. Without admin keys these calls are **403** `admin API disabled: set RATCHET_ADMIN_KEYS` in live mode, and open in mock mode (recorded as `mock-admin`). The ledger and the event log record the admin's name, never the key.
- Pages, `/healthz`, the other `/ratchet/*` routes (reads included: `GET /ratchet/tasks`) and Scout stay public. The read-only `/ratchet/stats` and `/ratchet/bom` are what the dashboard reads; the mock-only demo controls are covered by the Caddy guard on the demo VM. Scout's agents call the cascade inside the process, so they never need a key; the Scout budget guard protects them.

The demo VM's Caddy config also has an opt-in edge guard for `/v1/*`, `/openai/*` (the [Azure-style route](#azure-style-route) runs the same paid cascade) and `/ratchet/demo/*` ([DEPLOY_NEBIUS_VM.md](DEPLOY_NEBIUS_VM.md#security-notes)). Use either or both. See [THREAT_MODEL.md](THREAT_MODEL.md) T1 and T19.

---

## POST `/v1/chat/completions`

### Request

| Field | Type | Required | Notes |
|---|---|---|---|
| `messages` | array of message objects | yes | 1 to `RATCHET_MAX_MESSAGES` (256) messages ([Messages](#messages)) |
| `model` | string | no | Names the task, never an upstream model: `"ratchet"` (the task comes from the header, metadata or the other rules), `"ratchet/<task>"` (or `"ratchet:<task>"`) for that task, or a model alias (`RATCHET_MODEL_ALIASES`). Any other id is served as `ratchet` with `X-Ratchet-Model-Alias` (`RATCHET_MODEL_POLICY=lenient`, the default) or is a 404 `model_not_found` (`strict`). See [Task resolution](#task-resolution) |
| `stream` | bool | no | `true` returns Server-Sent Events in the OpenAI chunk format ([Streaming](#streaming)) |
| `stream_options` | object | no | With `stream: true`, `{"include_usage": true}` ends the stream with a chunk that carries `usage` ([Streaming](#streaming)). Other keys are ignored, and so is the object without a stream. Never forwarded |
| `metadata` | object | no | Up to 16 string values. `metadata.ratchet_task` is the task name, used if the header is absent |
| `n` | integer | no | Number of choices, 1 (default) to `RATCHET_MAX_N` (4); more is a 400 `unsupported_value` ([Choices and logprobs](#choices-and-logprobs)) |
| `max_tokens`, `max_completion_tokens`, `temperature`, `top_p`, `stop`, `seed`, `presence_penalty`, `frequency_penalty`, `logit_bias`, `logprobs`, `top_logprobs`, `response_format`, `tools`, `tool_choice`, `parallel_tool_calls`, `reasoning_effort` | | no | Checked against OpenAI's rules ([Request validation](#request-validation)), then sent to each model as its role allows ([Generation parameters](#generation-parameters)). `max_completion_tokens` wins over `max_tokens`; a string `stop` counts as a one-item list. `logprobs: true` returns each choice's token logprobs ([Choices and logprobs](#choices-and-logprobs)) |
| `functions`, `function_call`, `user`, `safety_identifier`, `store` | | no | Checked, not forwarded: the end user's identity and the storage choice never go upstream, and the legacy function fields are not mapped to `tools` yet |
| any other field | | no | Ignored on purpose: unknown fields are kept, not refused, so newer OpenAI SDKs and third-party clients that send fields Ratchet does not know (`service_tier`, `prompt_cache_key`, …) keep working. One is forwarded only when `RATCHET_TEACHER_PARAMS` or `RATCHET_STUDENT_PARAMS` names it (vLLM's `top_k`, for example) |

`null` for any optional field means it is not set.

| Header | Meaning |
|---|---|
| `X-Ratchet-Task` | Task name. Takes precedence over `metadata.ratchet_task`: when both are set, the header wins. A name that is not registered ([Tasks](#tasks)) follows `RATCHET_UNKNOWN_TASKS`: by default it creates a task without fields on the fly, which compares whole answers (see [CONFIGURATION.md](CONFIGURATION.md#tasks)); with `teacher` the teacher answers and nothing is stored; with `reject` it is a 400 `unknown_task`. A task with `clients` answers 403 `task_not_allowed` to any other key. A header that is not a valid name means `default` under `distill`, and is a 400 `unknown_task` under `teacher` and `reject`. Missing means `metadata.ratchet_task`, else the `model` field and the rules below ([Task resolution](#task-resolution)), else `default`. No header, metadata key or other field can pin a task: only an operator's spec does |
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
| 6 | (Reserved for an auto task from a `response_format` json_schema, a later item) | |
| 7 | With `RATCHET_AUTO_TASK=system_prompt`: `auto-sys-` and 10 hex digits of the SHA-256 of the first system message, an auto task made on its first admitted request (at most `RATCHET_MAX_AUTO_TASKS`, and never into the registry's last 50 slots; then `default`) | `auto_system` |
| 8 | `default` | `default` |

A name from rules 1 to 4 that is not registered follows `RATCHET_UNKNOWN_TASKS` (the 400's `param` is `model` when the name came from the model field or an alias). The `model` field never selects an upstream model: the teacher always gets `RATCHET_TEACHER_MODEL` (or the fallback teacher's model) and the student its adapter name, and the response's `model` is the model that served. A client that can set only a model name (LangChain's `ChatOpenAI(model=...)`, a no-code tool) uses `model="ratchet/ticket-triage"` or an alias.

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
| `tools`, `tool_choice`, `parallel_tool_calls` | No (tool calls are not returned yet) | No | As when serving |
| `seed`, `logit_bias` | The client's | The client's | No |
| `top_p`, `presence_penalty`, `frequency_penalty` | The client's | No | No |
| `reasoning_effort` | The client's | No | The client's |
| `model` | `RATCHET_TEACHER_MODEL`, always (invariant 1) | The version's adapter | `RATCHET_TEACHER_MODEL` |
| an unknown field | Only when `RATCHET_TEACHER_PARAMS` names it | Only when `RATCHET_STUDENT_PARAMS` names it | As when serving |

- The student's temperature and logprobs never change, and its confidence is always scored over `RATCHET_SAMPLES` samples: its threshold was calibrated that way. A request for more choices draws more samples, only to fill them ([Choices and logprobs](#choices-and-logprobs)). A sample cut off by its token limit gets no vote, so a tight `max_tokens` can send a request to the teacher.
- `max_tokens` above a cap is capped, not refused.
- When the reasoning headroom gives the teacher more than the client asked for, an answer whose visible part (reasoning removed) is longer than the client's `max_tokens` (about 4 characters a token) comes back whole with `finish_reason: "length"`, and is not stored.
- `stop`, `response_format`, `tools` and `tool_choice` are stored with the example, redacted like the messages: the same messages under other conditions are another example (in the same golden or train split), and the nightly golden eval samples the student under them. Temperature, seed, penalties and `max_tokens` are not stored.
- A teacher answer written under a client's `logit_bias`, a non-zero `presence_penalty` or `frequency_penalty`, a `temperature` above 1, or a vendor field is returned as usual but never stored: a client may choose the answer it gets, not the label the student learns.
- `tools`, `tool_choice` and `parallel_tool_calls` reach no model by default, so a request with tools is answered in prose, as before v0.5. When an operator allows them for the teacher and it answers with a tool call, the response is **502** `tool_calls_unsupported` (with `X-Should-Retry: false`) until tool calls are passed through; a response never has `finish_reason: "tool_calls"` without them.

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
| `metadata` | An object of at most 16 string values; keys up to 64 characters, values up to 512; `ratchet_task` 1 to 64 letters, digits, `_`, `.` or `-` | `invalid_type`, `invalid_value` |
| `response_format` | `type` is `text`, `json_object` or `json_schema`. `json_schema` needs a `name` matching `^[A-Za-z0-9_-]{1,64}$`; its `schema`, if sent, must be an object of at most 64 KB and 16 levels | `missing_required_parameter`, `invalid_type`, `invalid_value` |
| `tools` | At most 128 entries of `{"type": "function", "function": {"name": ...}}` (name as above), at most 64 KB in all. Other tool types are `unsupported_value` | `invalid_type`, `invalid_value`, `unsupported_value` |
| `tool_choice` | `none`, `auto`, `required` or `{"type": "function", "function": {"name": ...}}` | `invalid_type`, `invalid_value` |
| `functions`, `function_call` | The legacy forms: functions named as above; `none`, `auto` or `{"name": ...}` | `invalid_type`, `invalid_value` |
| `stream`, `parallel_tool_calls`, `store` | Booleans | `invalid_type` |
| `model`, `user`, `safety_identifier`, `reasoning_effort` / `stream_options` | Strings / an object | `invalid_type` |
| `stream_options.include_usage` | A boolean | `invalid_type` |

### Response (200)

A standard `chat.completion`, with every field OpenAI's has, plus a `ratchet` block:

```json
{
  "id": "chatcmpl-3f1c0a9b8e7d6c5b4a3f2e1d",
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
      "audit": null, "shadow": null, "arbitration": null
    },
    "degraded": null,
    "task": "ticket-triage",
    "task_source": "header",
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
| `cost_usd` | Actual cost of this request, including audit, shadow and arbitration calls (a call that failed adds nothing) |
| `all_teacher_cost_usd` | Estimated cost if the teacher had answered alone |
| `latency_ms` | Student latency plus teacher latency when the teacher answered |
| `degraded` | `null`, or `"teacher_unavailable"` when the teacher (and the fallback teacher, if set) could not answer an escalated request and, with `RATCHET_ON_TEACHER_FAILURE=serve_student`, the student's own answer was served although it was below the version's threshold ([ADR 0019](adr/0019-teacher-breaker-degraded-mode-and-fallback-teacher.md)). Such an answer is never stored, audited or shadowed; `served_by` is `student` and `escalated` is `true` |
| `task` | The task the request was answered as ([Task resolution](#task-resolution)); also the `X-Ratchet-Task` response header |
| `task_source` | The rule that chose it: `header`, `metadata`, `deployment`, `model`, `alias`, `match`, `auto_system` or `default` |
| `requested_model` | The request's `model` as sent (`null` when it sent none); `model` above is the model that served |
| `usage_breakdown` | Every model call of the request, as `{"in": prompt tokens, "out": completion tokens}` as that model reported them, or `null` for a call that was not made or failed: `student_samples` (one call for all n samples: the prompt once, every sample's output), `served` (the served answer: one of the student's samples, or the teacher's own call; every choice, for `n` > 1), `audit`, `shadow` (the candidate's samples) and `arbitration` ([Usage](#usage)) |

`model` is the model that produced the served answer: the adapter name (`student-vN`), the teacher model ID, or the fallback teacher's (`RATCHET_TEACHER_FALLBACK_MODEL`) when it answered for an unavailable teacher.

`system_fingerprint` names the configuration that produced the answer: `fp_` and the first 12 hex digits of SHA-256 over `student|<adapter>|<version id>|<training-set hash>|<threshold>` for a student answer, or `teacher|<RATCHET_TEACHER_MODEL>` for a teacher answer (`teacher|<RATCHET_TEACHER_FALLBACK_MODEL>` for the fallback teacher's). A degraded answer carries its version's student fingerprint. `<threshold>` is the one the answer was held to: since FR-14.1 each task of a version can have its own, so the fingerprint is the same for every answer of one version on one task, and changes when a promotion or a rollback puts another version in front (during a canary, both versions' fingerprints appear) or when that threshold changes. `service_tier` is always `default`. `choices[i].logprobs` is `null` unless the request sent `logprobs: true` ([Choices and logprobs](#choices-and-logprobs)), `message.refusal` is `null` and `message.annotations` is empty.

`message.content` is the served answer after `tasks.clean_answer`: model reasoning is never returned (a `<think>…</think>` block, anything before a lone `</think>`, an unterminated `<think>` that opens what is left and what follows), and for a structured task such as `ticket-triage` a code fence around the whole JSON answer is removed, so the content is bare JSON. A structured answer that is one JSON object is returned whole, so a tag quoted in its strings stays; a `<think>` after answer text is content too. A free-text answer keeps its code blocks. A teacher answer that was cut off by its token limit is still returned (and is not stored as training data), with `finish_reason: "length"`.

`finish_reason` is the served answer's: `stop`, `length` (cut off by `max_tokens` or a cap, or over the client's `max_tokens` with the reasoning headroom) or `content_filter`; any other value an upstream model reports is returned as `stop`. It is never `tool_calls`, because `message.tool_calls` is not returned yet ([Generation parameters](#generation-parameters)). A student answer is always one that ended by itself.

Response headers: `X-Ratchet-Served-By: student|teacher`, `X-Ratchet-Task: <task>`, `X-Ratchet-Degraded: teacher_unavailable` on a degraded answer (and only then), `X-Ratchet-Model-Alias: <model>` when `RATCHET_MODEL_POLICY=lenient` served a `model` Ratchet does not know as `ratchet` (UTF-8 percent-encoded except for visible ASCII, so spaces and `%` are encoded too and one percent-decoding gives the id back; at most 256 characters), plus `X-RateLimit-Limit-Requests` and `X-RateLimit-Remaining-Requests` when a daily quota is set. Streamed responses carry them too.

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

`usage` is **not** what the request cost Ratchet. The other student samples, an audit, a shadow and an arbitration are listed in `ratchet.usage_breakdown`, and `ratchet.cost_usd` prices all of them. So the same answer reports the same `usage` whether or not it was audited or shadowed, and a client that bills its own users from `usage` charges them for the answer they got.

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
  "detail": "Invalid value for 'temperature': expected a number from 0 to 2, but got 3 instead."
}
```

`detail` repeats `error.message` for one release (v0.5), for clients written against earlier versions, then goes away: read `error`. Routes outside `/v1` (`/ratchet/*`, Scout, pages) keep FastAPI's `{"detail": ...}`.

| Status | `type` | `code` | When | Retried by OpenAI SDKs? |
|---|---|---|---|---|
| 400 | `invalid_request_error` | `invalid_json`, `invalid_type`, `invalid_value`, `missing_required_parameter`, `unsupported_value`, `context_length_exceeded`, `unsupported_content_type` | The request breaks a rule in [Request validation](#request-validation); `param` names the field | No |
| 400 | `invalid_request_error` | `null` | Mock mode with `RATCHET_MOCK_STRICT=1` only: the request is not a synthetic demo ticket or a Scout request from the mock research world (`mock mode only answers demo requests: '...'`). Without it the generic mock world answers every request ([Mock mode](#mock-mode-answers)) | No |
| 400 | `invalid_request_error` | `unknown_task` | `RATCHET_UNKNOWN_TASKS=reject` and the task is not registered (`unknown task "x"; registered: default, page-read, ...`, at most 20 names, only tasks this key may use); `param` is `metadata.ratchet_task` when the name came from there | No |
| 401 | `invalid_request_error` | `invalid_api_key` | API keys are on and the key is missing or wrong (`WWW-Authenticate: Bearer`) | No |
| 403 | `permission_error` | `task_not_allowed` | The task's `clients` do not name this key, or the request has no key (`task not allowed for this key`). Checked before the quota, so it uses none | No |
| 404 | `not_found_error` | `unknown_url` | No such route: `Unknown request URL: POST /v1/foo.` | No |
| 404 | `invalid_request_error` | `model_not_found` | `RATCHET_MODEL_POLICY=strict` and the `model` is not `ratchet`, `ratchet/<task>` or an alias (`The model 'gpt-4o' does not exist.`, `param` `model`); under either policy, `ratchet/` with a name that is not a task name; on the Azure-style route, a deployment that is not a task name (`param` null). Checked before the quota, so it uses none and nothing is logged | No |
| 405 | `invalid_request_error` | `null` | A route called with the wrong method (`Allow` names the right one) | No |
| 413 | `invalid_request_error` | `request_too_large` | The body is larger than `RATCHET_MAX_BODY_BYTES` | No |
| 400 | `invalid_request_error` | the teacher's (`context_length_exceeded`, ...), `request_too_large` for an upstream 413 without one, or `null` | Live mode: the teacher refused the request with 400, 413 or 422, so it cannot succeed as sent (too long for the model, a content part it cannot read). `message` and `param` are the teacher's; `X-Should-Retry: false` | No |
| 429 | `insufficient_quota` | `daily_quota_exceeded` | The key's daily quota is used up (`daily request quota of N used up for 'acme'; resets at 00:00 UTC`); `Retry-After` (seconds to 00:00 UTC), `X-RateLimit-Reset-Requests` and `X-Should-Retry: false` | No: the response tells them not to |
| 429 | `rate_limit_error` | `upstream_rate_limited` | Live mode: Token Factory rate-limited the teacher call, after the gateway's own retries (`teacher rate limited: upstream 429; retry after 7 s`). `Retry-After` is the teacher's wait, clamped to 1 to 60 s (1 when it sent none) | Yes, after `Retry-After` |
| 500 | `api_error` | `null` | A bug in the gateway (`The server had an error while processing your request.`). The message never includes request text; the ledger records a `gateway_error` event with the exception's class at the 1st, 10th, 100th, ... occurrence. In live mode this includes a `KeyError` from the cascade, which is a 400 only in strict mock mode (`RATCHET_MOCK_STRICT=1`) | Yes |
| 502 | `api_error` | `teacher_unavailable` | Live mode: a teacher call whose answer was needed failed after its retries: an escalation, a cold start, a pinned task or a teacher-only request (`teacher unavailable: upstream 503`, `teacher unavailable: upstream 502 upstream_unreachable` when Token Factory could not be reached, `... bad_upstream_response` when its answer could not be read). Student failures do not cause errors: they fall back to the teacher. Neither do a failed audit or shadow arbitration: the student's answer is served ([ADR 0013](adr/0013-optional-teacher-calls-fail-open.md)). An upstream redirect or 4xx outside the other rows (402, for example) adds `X-Should-Retry: false`. When Token Factory sent a wait (`Retry-After` on a 503, for example), it is passed on as `Retry-After`, clamped to 1 to 60 s. See [RUNBOOK.md](RUNBOOK.md#teacher-api-down-token-factory-errors-or-timeouts) | Yes (after `Retry-After` when present), unless `X-Should-Retry: false` |
| 502 | `api_error` | `upstream_auth_error` | Live mode: Token Factory refused the gateway's own key (401 or 403): `NEBIUS_API_KEY` is wrong or lacks access. `X-Should-Retry: false` | No |
| 502 | `api_error` | `upstream_not_found` | Live mode: Token Factory answered 404: `RATCHET_TEACHER_MODEL` or `RATCHET_TEACHER_BASE` is wrong. The request's `model` never selects the teacher, so this is the gateway's configuration, not the client's mistake. `X-Should-Retry: false` | No |
| 502 | `api_error` | `tool_calls_unsupported` | Live mode, only when `RATCHET_TEACHER_PARAMS` allows `tools`: the teacher answered with a tool call, which the gateway cannot return yet (`The teacher answered with a tool call, which this gateway cannot return yet; send the request without tools.`). Nothing is stored. `X-Should-Retry: false` | No |
| 503 | `api_error` | `teacher_circuit_open` | Live mode: the teacher failed `RATCHET_TEACHER_BREAKER_FAILURES` (5) calls in a row, so its circuit breaker is open and the gateway does not call it until the next probe (`teacher circuit open: recent teacher calls failed, so the gateway is not calling it; retry after 18 s`). Answered at once, for any request that needs the teacher's answer (an escalation, a cold start, a pinned task, a teacher-only request) that no fallback teacher or degraded answer covers. `Retry-After` is the time until the breaker lets a probe through, clamped to 1 to 60 s ([ADR 0019](adr/0019-teacher-breaker-degraded-mode-and-fallback-teacher.md)) | Yes, after `Retry-After` |
| 504 | `api_error` | `teacher_timeout` | Live mode: the teacher did not answer in time (`RATCHET_TEACHER_TIMEOUT_S`, retried once within `RATCHET_TEACHER_DEADLINE_S`), or Token Factory answered 504 (`teacher timeout: upstream 504 upstream_timeout`). Token Factory's `Retry-After`, if it sent one, is passed on (1 to 60 s) | Yes, after `Retry-After` when present |

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
| `tools` that reach the teacher | A tool call: `lookup_order(order_id)` or `refund(order_id, amount, reason)` (the mock catalogue, `sim.generic.TOOLS`) with the order number, amount and reason read from the prompt, or a call to your own tool with arguments built from its parameters schema. The default `RATCHET_TEACHER_PARAMS` does not forward `tools` yet, so by default the teacher answers in prose; with `tools` allowed, the call is refused as a real one would be (502 `tool_calls_unsupported`) |

The mock student learns these requests as it learns tickets, per schema name, tool set or system prompt. `RATCHET_MOCK_STRICT=1` restores the earlier behaviour: anything but a demo request is a 400 `mock mode only answers demo requests`.

---

## GET `/healthz`

Always public, also when API keys are on. The demo VM's Docker health check uses it.

```json
{"status": "ok", "version": "0.4.0", "mode": "mock"}
```

## GET `/v1/models`

The model ids a client can send ([Task resolution](#task-resolution)), each a `model` object the OpenAI SDK's `Model` accepts. Needs a key when `RATCHET_API_KEYS` is set.

- `ratchet` first (its task comes from each request);
- `ratchet/<task>` for every registered task the key may use (a task whose `clients` do not name it is left out; with keys on, an auto task only to a key whose own requests used it in the last 30 days, since its name is a digest of a client's system prompt), in registry order;
- unless `RATCHET_UNKNOWN_TASKS=reject`, `ratchet/<name>` for every unregistered name the key's own requests named in the last 30 days (with keys off, any request's), at most 500, the most recently used first;
- every model alias whose task the key may use, with `alias_of`.

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
     "avg_latency_ms": 352.0, "audit_agreement": 1.0, "degraded": 0, "fallback_teacher": 0}
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
     "cost_usd": 3.21, "baseline_usd": 13.1, "savings_pct": 0.75, "avg_latency_ms": 640.0,
     "routes": {"student": 10080, "cold_start": 1100, "confidence": 720, "student_error": 60, "student_breaker": 40},
     "audits": 1008, "audit_agreement": 0.98, "threshold": 0.7412, "precision": 0.98, "coverage": 0.91,
     "calibrated": true, "disabled": null},
    {"task": "research-plan", "pinned": true, "requests": 3, "student_share": 0.0, "escalation_rate": 0.0,
     "cost_usd": 0.0039, "baseline_usd": 0.0039, "savings_pct": 0.0, "avg_latency_ms": 2400.0,
     "routes": {"pinned": 3}, "audits": 0, "audit_agreement": null, "threshold": null, "precision": null,
     "coverage": null, "calibrated": null, "disabled": null}
  ],
  "events": [{"id": 57, "ts": 1792400000.0, "day": 9, "kind": "live", "detail": "v5 promoted to 100% (audited precision 94.1%, n=17)"}],
  "totals": {"requests": 13500, "cost_usd": 5.21, "baseline_usd": 14.58, "saved_usd": 9.37, "savings_pct": 0.64,
             "cost_model": "per_token"},
  "bill_of_materials": {"generated_at": 1792400000.0, "examples_sha256": "07ac2c…", "sources": [ … ]},
  "clients": [
    {"client": "acme", "requests": 40, "student_share": 0.95, "cost_usd": 0.0044, "baseline_usd": 0.044, "savings_pct": 0.9}
  ],
  "privacy": {"redaction": true, "examples": 2742, "redacted_examples": 12, "placeholders": 19},
  "security": {"api_keys": true, "daily_requests_per_key": 500, "bom_signing": true, "bom_key_id": "6cf6bc1a39c4491d"},
  "cost": {"model": "per_token", "endpoint_hours": 216.0, "gpu_hours_savings_pct": -22.49,
           "break_even_student_requests_per_gpu_hour": 1582.0, "recommendation": "hosted_fallback"}
}
```

- `days`: one entry per `requests.day` (see `Ledger.stats_by_day`). `audit_agreement` is `null` on days without audits. `degraded` and `fallback_teacher` count that day's requests a stand-in answered because the teacher was unavailable (the routes of the same names below).
- `tasks`: one entry per task name over all days, busiest first (`Ledger.stats_by_task`). `student_share` and `escalation_rate` are shares of that task's requests; `savings_pct = 1 − cost_usd / baseline_usd`. Pinned tasks always show `student_share` 0 and `savings_pct` 0. `pinned` is true for tasks that always go to the teacher and are never trained on (`research-plan`, `research-write`); the dashboard's **By task** table shows them as "pinned to teacher". `routes` counts the task's requests by how they were served (the ledger's `requests.route`), most frequent first, and adds up to `requests`:

  | Route | Served by | Why |
  |---|---|---|
  | `student` | student | Its confidence reached the version's threshold (and, for `n` > 1 choices, at least `n` samples agreed) |
  | `confidence` | teacher | The student was unsure (below the threshold) |
  | `cold_start` | teacher | No version serves yet (no live version, and the canary was not picked) |
  | `pinned` | teacher | A pinned task |
  | `student_error` | teacher | The student call failed (timeout, connection error, an error status, a lost adapter) |
  | `student_breaker` | teacher | The student's circuit breaker was open, so the student was not called ([ARCHITECTURE.md](ARCHITECTURE.md#student-circuit-breaker)) |
  | `degraded` | student | The student was unsure and the teacher unavailable, so its own answer was served (`RATCHET_ON_TEACHER_FAILURE=serve_student`, `ratchet.degraded`); not audited, stored or shadowed ([ARCHITECTURE.md](ARCHITECTURE.md#when-the-teacher-is-down)) |
  | `fallback_teacher` | teacher | The teacher was unavailable (its retries failed or its breaker was open), so the fallback teacher answered (`RATCHET_TEACHER_FALLBACK_MODEL`); an escalation, a cold start, a pinned task or a teacher-only request |
  | `n_choices` | teacher | The student was confident, but fewer than the request's `n` samples gave its answer, so it could not fill `n` choices ([Choices and logprobs](#choices-and-logprobs)) |
  | `unknown_task` | teacher | The task is not registered and `RATCHET_UNKNOWN_TASKS=teacher`: the student was not called and nothing was stored ([Tasks](#tasks)) |
  | `task_disabled` | teacher | The serving version's per-task record lets the student answer none of this task (the release gate disabled it on that version, or its eval had no held-out row of it), or the task has critical classes the version's record did not measure under the current spec, or the hosted untuned model would answer it (FR-2.50), so the student was not called; never served in degraded mode ([per-task calibration](CONFIGURATION.md#per-task-calibration)) |
  | `rule:<i>` | teacher | The student was confident, but rule `i` (from 0) of the task's `escalate_when` held for its answer (`action: teacher`, or a confidence below the rule's `min_confidence`), so the teacher answered and its answer was stored as for any escalation; never served in degraded mode ([risk-aware routing](CONFIGURATION.md#risk-aware-routing)) |
  | `ineligible:input_tokens` | teacher | The request holds more input than the task's `student_max_input_tokens` (characters of its message text / 4), so no version was picked and the student was not called; stored only with the task's `store_ineligible`; not counted as an escalation |
  | `ineligible:content_type` | teacher | The request has a content part the student does not read (an image or other part admitted by `RATCHET_TEACHER_MODALITIES`; the student reads text only), so the student was not called and nothing was stored; not counted as an escalation |
  | `unrecorded` | either | Requests logged before v0.5, when the ledger had no route column |

  Later versions add routes; a client should show an unknown route as it is.

  Since FR-14.1 each entry also says how well the student does the task and how it is held to it ([per-task calibration](CONFIGURATION.md#per-task-calibration)). `audits` and `audit_agreement` cover the teacher's audits of the task's student answers over every version (`audit_agreement` is `null` without audits). The rest come from the live version's eval and are `null` without a live version and for a pinned task: `threshold` is the confidence the live student needs to answer the task, as the router applies it (its per-task threshold, 1.01 when the task is disabled, has no held-out rows, its spec changed since the version was scored or it has critical classes the version's record did not measure (FR-2.50), else the version's global threshold); `precision` and `coverage` are the task's held-out numbers measured when the version was scored, at the threshold it was calibrated to (for a disabled task, or one whose spec changed since, that measurement stays as it was while `threshold` is 1.01 and the student answers none of the task), `calibrated` whether the threshold is the task's own (enough calibration rows) or never below the global one, and `disabled` why the release gate disabled the task on the live version (a string) or `null`. The last four are `null` when the live version has no per-task record for the task (scored before per-task calibration, or no golden rows of it).
- `versions`: every version, oldest first. `eval` is its golden evaluation (since FR-14.1 with one record per task in `eval.tasks`, [per-task calibration](CONFIGURATION.md#per-task-calibration)). A task with critical classes (FR-2.50, [risk-aware routing](CONFIGURATION.md#risk-aware-routing)) has `critical` in its record: one entry per class, in the spec's order, with the condition as written (`field` and `equals` or `in`), `max_miss_rate`, `n` (its held-out rows whose teacher reference is in the class), `misses` (those the student would serve with an answer outside it, at the threshold the record stores and past its rules), `miss_rate` (`null` when `n` is 0) and `upper_bound` (the 95% Wilson upper bound, 1.0 when `n` is 0). `excluded_ineligible`, when present, counts golden rows left out because the task's routing block keeps such a request from the student. For a task with `escalate_when` rules, a record's `precision` and `coverage` (and the pooled ones) count only the answers no rule sends to the teacher. `other_tasks`, present only on an eval scored with `RATCHET_PER_TASK_CALIBRATION=0` that has records (the tasks with critical classes), holds the other tasks' held-out rows (`test_n`) and how many of them the pooled threshold serves (`served`). `bom` is `signed`, `unsigned`, or `null` for versions trained before v0.4.
- `clients`: one entry per API key name that sent requests, busiest first (`Ledger.stats_by_client`). Empty when keys are off.
- `privacy`: whether redaction is on, how many stored examples had at least one identifier replaced, and how many placeholders were inserted in total (`Ledger.privacy_stats`).
- `security`: whether API keys are required, the daily quota per key (`null` = none), and whether adapter BOMs are signed (with the key id: the first 16 hex characters of the public key's SHA-256).
- `events`: the last 40 events, oldest first. `id` is the ledger row id (stable, use it as a key); `ts` is Unix time.
- `totals.cost_model` is `per_token`: `totals`, `days`, `tasks` and `clients` price the student per token (the hosted Nano price), as every figure since v0.1. The GPU-hour view is in `cost`.
- `cost`: the [cost report](#cost)'s headline numbers for all days: the headline model (`RATCHET_STUDENT_COST_MODEL`), the student endpoint's recorded hours, the savings share by GPU-hours paid (negative when self-hosting does not pay; `null` without a baseline), the break-even volume (`null` until the student has served) and the recommendation.

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
    "schema": "ratchet.bom/v2",
    "generator": "ratchet 0.4.0",
    "created_at": "2026-10-20T00:15:12Z",
    "adapter": {"version": 1, "name": "student-v1", "base_model": "nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16", "day": 1},
    "training": {"examples": 1029, "dataset_sha256": "478eef…", "tasks": {"ticket-triage": 420, "source-triage": 362, "page-read": 247},
                 "targets": "teacher answers only", "examples_with_pii_redacted": 0, "origins": {"live": 1029}},
    "golden": {"examples": 110, "sha256": "cbce0e…", "held_out": true},
    "sources": [{"teacher": "nvidia/Nemotron-3-Ultra-550b-a55b", "license": "OpenMDW-1.1",
                 "license_url": "https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Ultra-550B-A55B-BF16",
                 "distill_ok": true, "examples": 1029}],
    "redactor": {"sha256": "e85726…", "rules": 9, "redact_pii": true},
    "eval": {"golden_n": 110, "golden_agreement": 0.89, "threshold": 0.65, "coverage": 0.96, "precision": 0.94, "test_n": 55}
  },
  "sha256": "9b1f…",
  "signature": {"alg": "Ed25519", "key_id": "6cf6bc1a39c4491d", "public_key": "IiNlIP8Y…", "value": "k2Hc…"}
}
```

- `sha256` and the signature cover the **canonical** manifest: JSON with sorted keys, no whitespace, UTF-8 (`ratchet.bom.canonical`).
- `signature` is `null` when `RATCHET_BOM_SIGNING_KEY` is not set; the `sha256` still detects edits.
- `training.dataset_sha256` equals the version's `dataset_hash`; `golden.sha256` fingerprints the golden rows the evaluation used. The `eval` block is the evaluation at training time; later re-checks update `/ratchet/stats` but not the signed BOM.
- Schema `ratchet.bom/v2` ([ADR 0024](adr/0024-evidence-foundations.md)) keeps every v1 field and adds `training.origins` (training rows by where they came from; `live` for rows that did not record it) and `redactor` (a SHA-256 over the redaction rules, the number of rules, and whether redaction was on; `null` when the writer did not say). Later features add optional sections: `training.commitment` and `golden.commitment` (`{"alg": "ratchet.smt/v1", "root", "size"}`), `teacher`, `backfill_jobs` and `erasures_honored`. Adapters trained before keep their v1 documents, which still verify; any other schema is refused as unknown.

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

## Cost

Utilization-aware cost accounting ([ADR 0023](adr/0023-utilization-aware-cost-accounting.md), formulas in [ARCHITECTURE.md](ARCHITECTURE.md#7-cost-accounting)). The self-hosted student endpoint and the training jobs bill GPU-hours while they run; the ledger records when they ran (`compute_intervals`) and the report prices them. Every list price is unverified until the day-1 checks pass; the report says so (`prices.label`) until `RATCHET_PRICES_VERIFIED=1`. Numbers from the mock demo are simulation output.

### GET `/ratchet/cost?days=N`

The cost report for every ledger day, or for the last `days` days (1 to 36,500; anything else is **400**, a non-number **422**). Public, like `/ratchet/stats`. `python -m ratchet.cost report [--days N] [--json]` prints the same report. An example from the mock demo (simulation numbers, nine days of 1,500 requests with the endpoint up 24 hours a day):

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
  "endpoint_hours": 216.0, "endpoint_usd": 334.8, "idle_hours": 24.0, "idle_share": 0.11,
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
  "explanation": "Self-hosting does not pay at this volume. The endpoint served 52 student requests per hour it ran, below the break-even of 1,582; below 54,070 an hour the hosted fallback is cheaper (1 GPU at $1.55 per GPU-hour; unverified list prices). Use the hosted fallback student on Token Factory (leave RATCHET_STUDENT_BASE empty) or run the endpoint only while traffic arrives. Savings by GPU-hours paid: -$327.86 (-2249%); per token: $9.37 (64%).",
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

- `views.per_token` is what every other savings figure uses; `views.gpu_hours` prices the student by the GPU-hours paid: teacher spend + endpoint hours + training hours (each interval at the rate it was recorded at) + the student's per-token spend on days with no recorded endpoint uptime (`hosted_student_usd`, `student_served_off_endpoint` answers: the hosted fallback served, or nobody recorded the endpoint). Savings can be negative and are never clamped; `savings_pct` is `null` without a baseline. `headline` is the view `RATCHET_STUDENT_COST_MODEL` picks.
- `avoided_teacher_usd_per_student_request` is the teacher spend a student answer saves on average (its all-teacher baseline less the teacher calls it still made); `break_even_student_requests_per_gpu_hour` is the endpoint's USD per hour (per-GPU price × GPUs) divided by it, so it counts student answers per hour of the whole endpoint. `null` until the student has served, and when its answers save nothing.
- `hosted_fallback_usd_per_request` is what the hosted fallback student (`RATCHET_FALLBACK_STUDENT`, per token) would bill for all the student calls, per student answer; `self_host_beats_hosted_above_requests_per_hour` is the volume above which the endpoint is cheaper. The hosted model is untuned, so it may serve fewer requests than the tuned student.
- `observed_student_requests_per_endpoint_hour` counts student answers on days the endpoint had uptime over its hours; `idle_hours` are endpoint hours on days when no request reached the student (whole days: hours idle inside a busy day are not counted).
- `recommendation` is `self_host`, `hosted_fallback`, `scale_to_zero` or `insufficient_data` (rules in [ARCHITECTURE.md](ARCHITECTURE.md#the-report-costpy)), with a plain-English `explanation`.
- `split_unknown_requests`: rows from before v0.5 have no student/teacher split; their whole cost counts as teacher spend in the GPU-hour view (`split_unknown_usd`), and a warning says so. `warnings` also names a training job open for more than 12 hours, which counts up to now until it is closed.
- `daily`: one row per day with requests or compute, the latest 731 days at most (`daily_truncated`); the totals always cover the whole window.

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

- `state` is required (`up` or `down`); `usd_per_hour` (per GPU, 0 or more; default `RATCHET_STUDENT_GPU_USD_PER_HOUR`), `gpus` (0 to 1,024; default `RATCHET_STUDENT_GPUS`) and `note` (at most 200 characters, PII redacted) are optional. Any other key, or a bad value, is **400**.
- `up` while the endpoint is up returns its open interval with `changed: false`; `down` while it is down returns `interval: null`, `changed: false`. `down` closes every open endpoint interval. A change writes an `endpoint` event naming the source and the admin's name, never the key.
- In mock mode the time is on the demo's synthetic clock (the current simulated day at the wall clock's time of day), and the shared demo holds at most 200 such records (**409**; a reset clears them).

---

## Tasks

The task registry ([ADR 0022](adr/0022-task-registry.md)): the built-in tasks, the tasks of `RATCHET_TASKS_FILE`, and the tasks written through these endpoints (API tasks, stored in the ledger and registered again at every start). The spec format is in [CONFIGURATION.md](CONFIGURATION.md#adding-a-task). Reads are public; writes and `validate` need an admin key ([Authentication](#authentication)). Errors are `{"detail": ...}`; a bad spec is **422** `{"detail": "invalid task spec", "errors": [...]}`, every problem with its path.

### GET `/ratchet/tasks`

```json
{
  "unknown_tasks": "distill",
  "max_tasks": 500,
  "tasks": [
    {"name": "invoice-extract", "description": "Vendor and total from an invoice", "format": "json",
     "fields": {"vendor": {"type": "text", "strip_suffixes": ["inc", "ltd"]}, "total": {"type": "number", "abs_tol": 0.01}},
     "pinned": false, "store": true, "match": {}, "routing": {}, "policy": {}, "tools": "teacher", "clients": ["acme"],
     "source": "api", "spec_hash": "1c7e77bb7041b4d8", "verifiable": true,
     "requests": 420, "student_share": 0.81, "recalibrating": false}
  ],
  "unregistered": [{"name": "invoice-extrct", "requests": 3}]
}
```

- `tasks`: every registered task, the built-in ones first, then the file's, then the API's. The first eleven keys are the task's spec as a file would hold it (what `PUT` takes back); `source` is `builtin`, `file` or `api`; `verifiable` says it names fields that can be checked; `requests` and `student_share` come from the ledger (the same numbers as `/ratchet/stats` `tasks`); `recalibrating` is true when the live version was scored under another spec of the task, so the teacher answers it until a night re-scores the version.
- `unknown_tasks`: the `RATCHET_UNKNOWN_TASKS` policy. `max_tasks`: how many tasks the registry holds at most, built-in ones included.
- `unregistered`: names requests used that are not registered (a typo, or a task to register or pin), with their request counts.

### GET `/ratchet/tasks/{name}`

One entry as above; for an API task also `created_ts`, `updated_ts` and `updated_by` (the admin key's name). **404** for a name that is not registered.

### POST `/ratchet/tasks/validate`

A dry run, with no change to the registry, the ledger or the event log. The body is either one task spec (checked as `PUT` would take it, so a built-in or file task's name is an error) or a tasks file document `{"tasks": [...]}` (checked as startup reads `RATCHET_TASKS_FILE`: it may adjust or extend a built-in task and extend its own tasks, but not an API task or a task of the file running now, because startup registers the file before the stored API tasks; and its tasks, with the built-in ones it does not replace, must fit in `max_tasks`). A document that validates starts the gateway. Always **200**:

```json
{"valid": false, "errors": ["tasks[1].fields.total.abs_tol: must be a number >= 0"], "tasks": []}
{"valid": true, "errors": [], "tasks": [{"name": "invoice-extract", "spec_hash": "1c7e77bb7041b4d8", "registered": null}]}
```

`registered` is the source of the task that has the name now (`builtin`, `file`, `api`), or null.

### PUT `/ratchet/tasks/{name}`

Create or replace an API task. The body is one spec without `name` (or with the same name as the URL); `extends` may name any registered task, the task's own name included (a new version built on the current one).

```bash
curl -X PUT localhost:8000/ratchet/tasks/invoice-extract -H "Authorization: Bearer $ADMIN_KEY" \
  -d '{"description": "Vendor and total from an invoice", "fields": {"total": {"type": "number", "abs_tol": 0.01}}}'
```

| Status | When |
|---|---|
| 200 | `{"task": {...entry...}, "spec_hash": "...", "changed": true, "created": true}`. A spec identical to the stored one is a 200 with `changed: false` and changes nothing |
| 401, 403 | No admin key, a wrong one or an API key; the admin API is off (live mode without `RATCHET_ADMIN_KEYS`) |
| 403 | The name is a built-in task (`task 'x' is built in; use another name (a spec may extend it)`) or a file task (`task 'x' is defined in RATCHET_TASKS_FILE; edit the file`) |
| 409 | The registry holds `max_tasks` tasks, or (mock mode) 50 API tasks already |
| 422 | The body is not strict JSON (NaN is refused), names another task, or breaks a spec rule; `clients` names a key that is not in `RATCHET_API_KEYS` |

The event log records `task_registered` (`name (spec <hash>) by <admin>`) or `task_changed` (`name: spec <old> -> <new> by <admin>`; for a change to `description`, `clients` or `match` only, the hash is kept). A changed spec does not touch stored examples: Night School judges them under the current spec, and the serving version keeps the task on the teacher until it is scored under the new one.

### DELETE `/ratchet/tasks/{name}`

Remove an API task, or an auto task (`RATCHET_AUTO_TASK=system_prompt`), which frees its place under `RATCHET_MAX_AUTO_TASKS` (a request with its system prompt makes it again while there is room): `{"deleted": "invoice-extract", "spec_hash": "...", "examples_kept": 420}` and a `task_deleted` event. Its stored examples stay, for provenance (and stay trainable under the name's adhoc task unless it is registered again or pinned). Requests that name it then follow `RATCHET_UNKNOWN_TASKS`. **403** for a built-in or file task, **404** for a name that is not registered.

## Demo controls (mock mode only)

All four return **403** `demo controls are only available in mock mode` when `RATCHET_MODE=live`.

The mock demo is shared by every visitor, so the controls protect themselves:

- **One demo action at a time.** `day` and `reset` take a lock without waiting. If another `day` or `reset` is still running, the call returns **409** `another demo action is running; try again in a moment`. (`break-next` and `student-outage` do not take the lock.)
- **Reset cooldown.** `reset` returns **429** `the demo was reset less than 30 seconds ago` within 30 seconds (`RESET_COOLDOWN_S`) of the last successful reset.

The dashboard shows these messages under its buttons.

### POST `/ratchet/demo/day?requests=1500&workload=tickets`

Advances `Ledger.day` by one and sends `requests` simulated requests through the cascade, calling `gate.tick()` every `requests/10`. Then it runs one night (`run_night`). `requests` is clamped to 100–5000.

| Query param | Default | Values |
|---|---|---|
| `requests` | `1500` | Requests for the day (clamped to 100–5000) |
| `workload` | `tickets` | `tickets` (support tickets), `research` (Scout's triage and reader calls), `mixed` (half each), `schema` (structured outputs: `response_format` json_schema requests over three schemas under the tasks `review-rating`, `ticket-route` and `meeting-extract`, and from day 5 `warranty-claim`, with nested records) or `tools` (support requests sent with the mock tool catalogue under the task `order-support`). Anything else returns **400** `workload must be one of [...]` |

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

Send traffic (Scout, or **Run 1 day + night**) and the release log shows the breaker at work: a `student_error` event for the first failure (`v2: ConnectError`; later ones are logged on the 10th, 100th and 1000th), then, after 5 failures in a row, `circuit` `student: closed->open after 5 failures (ConnectError)`; requests go straight to the teacher (route `student_breaker`); after 30 s one probe (`open->half_open`), which fails while the outage lasts and doubles the wait (`half_open->open for 60 s ...`); once the endpoint is back, a probe succeeds and `half_open->closed`. Calling it again starts a new outage of the given length from now.

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
| 502 | The run failed partway: a model or search call failed (`a model or search call failed: upstream 503`, or `upstream 502 upstream_unreachable`, `upstream 504 upstream_timeout`: the status and code, never the URL), or anything else raised (`run failed: <type>: <message>`). `detail` is the `error` event's message |
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
```

Tasks for an embedded `Ratchet` come from the same registry: `ratchet.taskspec.install(ledger, "tasks.json")` registers the built-in tasks, a tasks file and the API tasks a ledger stored, and `ratchet.tasks.register(taskspec.parse_spec(spec, "api"))` adds one ([ADR 0022](adr/0022-task-registry.md)). `r.task_for(name, client)` applies `RATCHET_UNKNOWN_TASKS` and a task's `clients` as the gateway does (`tasks.UnknownTask`, `tasks.TaskForbidden`); `handle` applies the unknown-task policy only.

`handle(messages, task=None, client=None, params=None, meta=None, n=1)`: `task` is a task name or a `tasks.Task`; `n` is the number of choices ([Choices and logprobs](#choices-and-logprobs)), and `teacher_only` takes it too; `params` (`providers.GenParams`, `GenParams.from_request(chat_request)` in the gateway) are sent to each call as `Settings.gen_policy()` allows ([Generation parameters](#generation-parameters)); `meta` (`router.RequestMeta`) carries what else the caller knows, and `RequestMeta(storable=False)` keeps the request's teacher answers out of the training set. A provider's `complete(messages, model, n, temperature, logprobs, response_format, params=None, **_)` receives the role's `GenParams`; the explicit arguments win over the same fields in it.

`result.route` says how the request was served (the routes table under [`/ratchet/stats`](#get-ratchetstats)); `result.text` is the answer after `tasks.clean_answer`. `result.choices` are the response's choices (`router.Choice`: `text`, `finish_reason`, and `logprobs`, OpenAI's `logprobs.content` entries when `params.logprobs` was set and they could be given, else None); `result.text` and `result.finish_reason` are choice 0's. `result.prompt_tokens`, `completion_tokens`, `reasoning_tokens`, `cached_tokens` and `total_tokens` are the response's [usage](#usage), `result.usage_breakdown` its `ratchet.usage_breakdown` (keys in `router.USAGE_PARTS`), and `result.fingerprint` its `system_fingerprint` (`router.fingerprint(...)`). Each `Completion` a provider returns carries `finish_reason` (`stop` when the server leaves it out or sends an empty string), `reasoning_tokens`, `cached_tokens` (from `usage.prompt_tokens_details`, 0 when absent) and `logprobs_content` (the choice's `logprobs.content` as the server sent it, or None; `providers.served_logprobs` turns it into what a client gets); only a teacher answer with `finish_reason` `stop` or `tool_calls` that is non-empty and valid for its task is stored (invariant 16), and `requests.stored` says whether a request stored one. `result.degraded` is `"teacher_unavailable"` for a degraded answer, else None. `r.health()` returns the failure counters (`student_errors`, `audit_errors`, `shadow_errors`), the answers a stand-in wrote for an unavailable teacher (`degraded_answers`, `fallback_answers`) and `breakers`: each breaker's state by name (`student`, `shadow`, `teacher`, `fallback_teacher`), as `{"state": "closed" | "open" | "half_open", "failures": n, "open_until": t, "open_s": s}`, where `open_until` is on the breaker's clock (`time.monotonic()` unless `Ratchet(clock=...)` was given). `Ratchet(..., on_adapter_missing=callback)` is called with the version when vLLM answers 404 for its adapter; the gateway passes `live.AdapterReloader.request`.

When the teacher's answer was needed and the teacher failed after its retries (and no fallback teacher or degraded answer stood in), `handle` raises `ratchet.providers.UpstreamError` (an `httpx.HTTPError`): `status` (504 for a timeout, 502 when Token Factory could not be reached), and the teacher's `type`, `code`, `param`, `message` and `retry_after` when it sent them; while the teacher's breaker is open, `ratchet.resilience.CircuitOpen` (503, code `teacher_circuit_open`, `retry_after` until the next probe). It never carries the URL or headers. `Ratchet(..., fallback_teacher=provider)` sets the fallback teacher's provider (default: the teacher's); it is used only when `RATCHET_TEACHER_FALLBACK_MODEL` is set.
