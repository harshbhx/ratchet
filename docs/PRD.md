# Ratchet: Product Requirements Document

| | |
|---|---|
| Owner | Harsh Bhanushali (DualMind Labs) |
| Status | v0.4.0 built (hackathon MVP, Scout demo, React web app, demo VM kit, privacy and security hardening); live Nebius run pending |
| Last updated | 2026-10-20 |
| Related | [Requirements matrix](REQUIREMENTS.md) · [Architecture](ARCHITECTURE.md) · [API](API.md) · [Deployment](DEPLOYMENT.md) · [Research summary](RESEARCH_SUMMARY.md) · [Roadmap](ROADMAP.md) |

## 1. Overview

**One-liner:** Ratchet is an OpenAI-compatible gateway that turns a company's own AI traffic into a cheaper model the company owns, using only teachers whose licenses allow it.

**Tagline:** Rent intelligence today. Own it tomorrow.

An app changes one line, its API base URL. From then on:

1. A small model the customer owns (the **student**) tries each request first.
2. If the student is not confident, the request goes to a large open model (the **teacher**, NVIDIA Nemotron 3 Ultra on Nebius Token Factory).
3. Every teacher answer is stored with its **provenance**: which teacher wrote it and which license allows training on it.
4. Every night, **Night School** trains a new student on that data, tests it on a held-out **golden set**, and releases it step by step (shadow, canary 10%, 50%, 100%), with automatic rollback.

The bill goes down over time. At the end, the customer owns the model weights.

## 2. Problem

### 2.1 What hurts

- Companies rent frontier models for tasks a small model could handle. Many requests are narrow and repetitive: classification, extraction, ticket triage, sub-steps inside agents.
- Inference spend is large and growing. Enterprise generative AI spend reached $37B in 2025, 3.2x the year before, with $12.5B on model APIs ([Menlo Ventures](https://menlovc.com/perspective/2025-the-state-of-generative-ai-in-the-enterprise/)). Gartner puts inference at 55% ($23.3B) of AI-optimized cloud spend in 2026 ([Gartner](https://www.gartner.com/en/newsroom/press-releases/2026-08-10-gartner-forecasts-worldwide-artificial-intelligence-optimized-iaas-spending-to-grow-96-percent-in-2026)).
- AI-native startups feel it in their margins. Some AI coding startups report neutral or negative gross margins ([TechCrunch](https://techcrunch.com/2025/08/07/the-high-costs-and-thin-margins-threatening-ai-coding-startups/)).
- Distillation works, but doing it by hand is slow and risky. Teams must log traffic, clean data, fine-tune, evaluate, deploy, and watch for regressions. Most tools cover only one of those steps.
- Distilling from closed models breaks their terms. OpenAI's [Business Terms 3.3(e)](https://openai.com/policies/business-terms/) and Anthropic's [Commercial Terms D.4(a)](https://www.anthropic.com/legal/commercial-terms) forbid using outputs to train competing models. A tool that learns from GPT or Claude traffic puts its users at risk.
- OpenAI is ending self-serve fine-tuning: no new jobs after Jan 6, 2027 ([OpenAI deprecations](https://developers.openai.com/api/docs/deprecations)). Teams with fine-tunes there need a new home.

### 2.2 Evidence the approach works

- Gartner expects small task-specific models to be used 3x more than general LLMs by 2027 ([Gartner](https://www.gartner.com/en/newsroom/press-releases/2025-04-09-gartner-predicts-by-2027-organizations-will-use-small-task-specific-ai-models-three-times-more-than-general-purpose-large-language-models)).
- NVIDIA Research estimates 40–70% of LLM calls in agent systems could go to small models, which are 10–30x cheaper to serve ([arXiv 2506.02153](https://arxiv.org/html/2506.02153v1)).
- Checkr replaced GPT-4 with a fine-tuned 8B model at 5x lower cost ([Rubrik blog](https://www.rubrik.com/blog/ai/24/how-checkr-streamlines-background-checks-with-fine-tuned-small-language)). Convirza cut cost 10x with LoRA adapters ([ZenML](https://zenml.io/llmops-database/optimizing-call-center-analytics-with-small-language-models-and-multi-adapter-serving)).
- Cascades: FrugalGPT matched GPT-4 at up to 98% lower cost ([arXiv](https://arxiv.org/abs/2305.05176)). Online Cascade Learning reported up to 90% lower cost ([arXiv](https://arxiv.org/html/2402.04513v2)).

### 2.3 Buyer caution

Buyers rank proving ROI (43%) far above price (7%) as their top challenge ([State of Tokenomics 2026](https://www.nops.io/blog/state-of-tokenomics-2026/)). Ratchet must show savings with receipts and show that quality held, not just promise cheaper tokens.

## 3. Goals and non-goals

### 3.1 Goals

| ID | Goal |
|---|---|
| G1 | Cut inference cost for narrow, high-volume tasks without users noticing a quality drop |
| G2 | Make distillation automatic: no manual dataset curation, training runs, or deploys |
| G3 | Make every release safe: nothing reaches users unless it beats or matches what is live |
| G4 | Make training data license-clean and provable (provenance ledger, model bill of materials) |
| G5 | Give the customer ownership of the trained weights |
| G6 | Run end to end on open models and Nebius infrastructure (Token Factory + AI Cloud) |
| G7 | Win or place in the Nebius x NVIDIA Global AI Hackathon (Best Apps and Agents track) |

### 3.2 Non-goals (v0.1)

- Streaming the student's tokens as they are generated (v0.4 streams the decided answer; see FR-1.6).
- Multi-tenant SaaS with billing, auth, and per-customer isolation.
- Open-ended generation tasks (long-form writing, chat). v0.1 targets tasks with checkable structured answers.
- Closed-model teachers. These are refused by design.
- Reinforcement learning or preference tuning. v0.1 uses supervised LoRA fine-tuning only.
- Multi-node or very large students (Nemotron 3 Nano 30B is a stretch goal).

## 4. Users and personas

### P1. Priya, CTO of an AI-native startup

- Runs a support-automation SaaS. Spends $25k+ a month on frontier APIs.
- Inference eats her gross margin. Investors ask about it.
- Has two engineers, no ML team.
- **Needs:** savings she can prove, no quality drop, almost no engineering time.

### P2. Marco, platform engineer at a mid-size company

- Owns the internal LLM gateway. Several teams call models for classification and extraction.
- **Needs:** an OpenAI-compatible drop-in, clear dashboards, safe rollouts, rollback, audit logs.

### P3. Anjali, AI lead at a regulated or sovereignty-minded company

- Works in finance or the public sector in India or the EU.
- Must know where data and models live and must prove training data is clean.
- Sovereign cloud spend is forecast at $80.4B in 2026 ([Gartner](https://www.gartner.com/en/newsroom/press-releases/2026-02-09-gartner-says-worldwide-sovereign-cloud-iaas-spending-will-total-us-dollars-80-billion-in-2026)).
- **Needs:** own the weights, a provenance record she can show auditors, region control.

### P4. Leo, an OpenAI fine-tuning migrant

- Has a fine-tuned GPT model in production. OpenAI stops new fine-tuning jobs on Jan 6, 2027.
- **Needs:** a path to a model he owns, built from his own traffic, with no license risk.

## 5. User stories and acceptance criteria

**US-1. Drop-in setup.** As Priya, I want to change only my base URL so that I can start without rewriting my app.
- Given an app using an OpenAI SDK, when its base URL points to Ratchet's `/v1`, then `chat.completions.create` returns a standard `chat.completion` object.
- Given a request with header `X-Ratchet-Task: ticket-triage`, then Ratchet routes it under that task's rules.

**US-2. Cold start.** As Priya, I want Ratchet to work on day one, before any student exists.
- Given no trained student, when a request arrives, then the teacher answers it and the answer is stored as a training example.

**US-3. Cheaper answers when safe.** As Priya, I want confident student answers served and unsure ones escalated.
- Given a live student, when its confidence is at or above that version's calibrated threshold, then the student's answer is served.
- Given confidence below the threshold, then the teacher answers and the request counts as escalated.

**US-4. Quality monitoring.** As Marco, I want ongoing checks on what the student serves.
- Given a student-served request, then with probability `audit_rate` the teacher also answers it and the agreement is recorded.

**US-5. Safe release.** As Marco, I want a new model to reach users only after it proves itself.
- Given a newly trained candidate, when its golden-set precision is below 95%, or it regresses against the live model, then it is rejected.
- Given a candidate that passes, when a live model exists, then it runs in shadow mode first.
- Given shadow results with enough served answers, precision of at least 95%, and coverage no worse than live, then it moves to canary 10%, then 50%, then 100%.

**US-6. Automatic rollback.** As Marco, I want a bad model pulled automatically.
- Given a canary version, when the 95% upper bound of its audited precision falls below 92%, then it is marked `rolled_back` and traffic returns to the previous live version (or the teacher).

**US-7. Provable data provenance.** As Anjali, I want to prove where training data came from.
- Given any training example, then it records teacher model, license name, and license URL.
- When I call `GET /ratchet/bom`, then I get every source, its license, its counts by split, and a SHA-256 fingerprint of the dataset.

**US-8. Closed teachers refused.** As Anjali, I want Ratchet to refuse unlicensed teachers.
- Given a teacher model starting with `gpt`, `o1`, `o3`, `o4`, `claude`, or `gemini`, or an unknown model, when Ratchet starts, then it raises `LicenseError`.

**US-9. Savings receipts.** As Priya, I want to see dollars saved against an all-teacher baseline.
- Given traffic, then the dashboard shows total saved, savings % by day, cost per 1,000 requests vs baseline, and the share answered by the student.

**US-10. Own the weights.** As Leo, I want the trained adapter in storage I control.
- Given a training run in live mode, then the adapter is written to the customer's Nebius Object Storage bucket under `adapters/<version>`.

**US-11. Demo without keys.** As a hackathon judge, I want to see the loop work without credentials.
- Given `RATCHET_MODE=mock`, when I click "Run 1 day + night", then a simulated day of traffic and a training night run, and the dashboard updates.
- When I click "Break next deploy", then the next deployed version is corrupted and later rolled back.

## 6. Functional requirements

Each requirement's Status is one of these forms, checked by `python scripts/reqtrace.py --check` in CI. The check also maps every row to its code and to the tests marked with its id (`@pytest.mark.req("FR-2.1")`); the generated matrix is [REQUIREMENTS.md](REQUIREMENTS.md).

| Status | Meaning |
|---|---|
| `Done (vX)` | Built in release vX and covered by an offline test (simulation, fakes or `httpx.MockTransport`), or listed as a known gap or a manual check in the matrix |
| `Done (vX), untested (live)` | Built and tested offline, but not yet run against the real service; also `untested (on GPU)` and `untested (on Nebius)` |
| `Done (vX); <text> planned` | Built, with a named extension still planned |
| `Partial: <text>` | Part of it is built; the text says what is missing |
| `Planned` | Not built yet. A test for it must be a strict expected failure |
| `Replaced by FR-a.b` | Superseded by another requirement |
| `Met in simulation (<text>)`, `Not met (<text>)`, `Not checked` | Success metrics (§8.1) only |

### 6.1 Gateway

| ID | Requirement | Status |
|---|---|---|
| FR-1.1 | `POST /v1/chat/completions` accepts OpenAI-format requests and returns a `chat.completion` object plus a `ratchet` metadata block (served_by, student_version, confidence, escalated, cost_usd, all_teacher_cost_usd, latency_ms; usage_breakdown since v0.5, FR-1.12) | Done (v0.1) |
| FR-1.2 | `GET /v1/models` lists a `ratchet` model | Done (v0.1) |
| FR-1.3 | Task selection via `X-Ratchet-Task` header or `metadata.ratchet_task`; the header wins when both are set. Since v0.5 a name that is not registered follows `RATCHET_UNKNOWN_TASKS`, and a task's `clients` limit who may use it (FR-2.15) | Done (v0.1) |
| FR-1.4 | Reject `stream: true` with HTTP 400 (until v0.4) | Replaced by FR-1.6 |
| FR-1.5 | Reject missing or empty `messages` with HTTP 400 | Done (v0.1) |
| FR-1.6 | Streaming responses: `stream: true` returns OpenAI `chat.completion.chunk` events and `[DONE]`, sent once the cascade has decided; since v0.5 `stream_options.include_usage` adds a last chunk with the usage (FR-1.12) | Done (v0.4) |
| FR-1.7 | API keys / auth on the gateway: named keys (`RATCHET_API_KEYS`), per-key daily quota, cost and savings per client. The demo VM also has an opt-in edge guard in Caddy | Done (v0.4) |
| FR-1.8 | Student endpoint errors fall back to the teacher; a teacher failure where the teacher's answer is needed (escalation, cold start, pinned, teacher-only) returns a mapped upstream status: 400 or 429 when the client's request or rate caused it, 502, 503 or 504 otherwise (FR-1.11). A failed audit or arbitration does not fail the request (FR-2.10) | Done (v0.1.1) |
| FR-1.9 | Web pages: landing page at `/`, Scout at `/scout`, dashboard at `/dashboard` | Done (v0.2) |
| FR-1.10 | Strict request validation and normalisation on `/v1/chat/completions`: a malformed request (wrong types, unknown role, bad `metadata`, out-of-range parameters, a malformed tool call, bad JSON or a string that is not valid Unicode) gets HTTP 400 before any model call, never a 500, and is neither logged nor counted against a quota; errors on `/v1/*` use OpenAI's envelope (`error.message`, `type`, `param`, `code`), so SDKs raise typed exceptions; limits on body size (413), messages per request and input characters (every string forwarded in the messages); the `developer` role and text content parts are accepted and normalised to the plain form, so they match plain requests in the ledger; other content parts are refused unless the teacher takes them (`RATCHET_TEACHER_MODALITIES`), and then the request goes to the teacher only and is never stored; unknown top-level fields are ignored, and message keys Ratchet does not use are dropped | Done (v0.5) |
| FR-1.11 | Upstream resilience: separate timeouts per role (teacher read `RATCHET_TEACHER_TIMEOUT_S`, student read and connect `RATCHET_STUDENT_TIMEOUT_S`, `RATCHET_STUDENT_CONNECT_TIMEOUT_S`); teacher and judge calls retried up to `RATCHET_TEACHER_RETRIES` times within `RATCHET_TEACHER_DEADLINE_S`, with full-jitter backoff and the server's `Retry-After`, only on connection failures and 408/409/429/5xx (a read timeout once at most, never another 4xx or a 2xx body that cannot be read); student calls on the request path never retried (the teacher is their retry); every failure raised as `UpstreamError` (status, OpenAI type, code, param, message, wait; never the URL). The gateway maps it: upstream 400/413/422 → 400 with the teacher's message and code, 401/403 → 502 `upstream_auth_error`, 404 → 502 `upstream_not_found`, 429 → 429 with `Retry-After` (1 to 60 s), 504 or a timeout → 504 `teacher_timeout`, anything else → 502 `teacher_unavailable`, with `X-Should-Retry: false` where a retry cannot help; the teacher's open breaker → 503 `teacher_circuit_open` with `Retry-After` (FR-2.12). The daily quota's 429 is `insufficient_quota` / `daily_quota_exceeded` with `Retry-After` and `X-Should-Retry: false`, and every response under a quota carries `X-RateLimit-Reset-Requests` | Done (v0.5) |
| FR-1.12 | OpenAI response parity ([ADR 0018](adr/0018-usage-reports-the-served-answer.md)): every `chat.completion` has `usage` (`prompt_tokens`, `completion_tokens`, `total_tokens`, `prompt_tokens_details.cached_tokens`, `completion_tokens_details.reasoning_tokens`) for the served answer as if its model had answered alone: the student's chosen sample, or the teacher's call with its reasoning tokens counted in `completion_tokens`; a count the model did not report is estimated at about 4 characters a token. Ratchet's own calls (the other samples, audit, shadow, arbitration) are listed in `ratchet.usage_breakdown` and priced in `cost_usd`. `system_fingerprint` is the same for every answer of one version and changes when a promotion or rollback puts another version in front or its threshold changes (the teacher has its own). `service_tier` (`default`), `choices[].logprobs` (null unless the client asks for logprobs, FR-1.14), `message.refusal` (null) and `message.annotations` (empty) are present, and `finish_reason` is `stop`, `length`, `tool_calls` or `content_filter`. Every stream chunk carries the fingerprint, the tier and null logprobs; with `stream_options.include_usage` every chunk has `usage: null` and a last chunk with no choices, the usage and the `ratchet` block comes before `[DONE]`. Responses and chunks pass the OpenAI SDK's own models | Done (v0.5) |
| FR-1.13 | Generation parameters reach the models by role ([ADR 0017](adr/0017-generation-parameters-by-role.md)): the teacher serving an answer (escalation, cold start, pinned, teacher-only) gets every field in `RATCHET_TEACHER_PARAMS` (default: every standard field but the tool-call fields, which the gateway cannot return yet, with `logprobs` and `top_logprobs` only when the client sends them, FR-1.14; never `user`, `safety_identifier`, `store`, `metadata` or `service_tier`) at the client's temperature or 0; the student gets the fields in `RATCHET_STUDENT_PARAMS` (default `max_tokens`, `stop`, `seed`, `response_format`, `logit_bias`, `top_logprobs`) under its own sampling (n = `RATCHET_SAMPLES`, temperature 0.7, logprobs); an audit or arbitration runs at temperature 0 with the same length limit, stops, format and tools. `max_completion_tokens` wins over `max_tokens` and is sent as `max_tokens`; output is capped per call (`RATCHET_TEACHER_MAX_TOKENS` 8,192, `RATCHET_STUDENT_MAX_TOKENS` 1,024), and the serving teacher may get `RATCHET_TEACHER_REASONING_HEADROOM` tokens more than the client asked for, a visible answer longer than the client's limit then being `length`. The response's `finish_reason` is the served answer's (`stop`, `length` or `content_filter`; never `tool_calls` without `message.tool_calls`, so a teacher tool call is a 502 `tool_calls_unsupported`). Examples store the conditions that define the target (`response_format`, `tools`, `tool_choice`, `stop`), which join the example's hash only when set, so every v0.4 hash stays; the split comes from the hash without them, so every variant of a prompt shares it and v0.4 splits stay. An answer written under a client's `logit_bias`, penalty, temperature above 1 or vendor field is served, not stored. The golden eval samples the student under the stored conditions, and a row the student endpoint refuses (400, 413, 422) scores as not served instead of stopping the night. The model sent is always the configured teacher | Done (v0.5) |
| FR-1.14 | `n` choices and token logprobs ([ADR 0020](adr/0020-n-choices-and-token-logprobs.md)): a request may ask for up to `RATCHET_MAX_N` (4) choices; more is a 400 `unsupported_value`. The student draws `max(RATCHET_SAMPLES, n)` samples, scores its confidence on the first `RATCHET_SAMPLES` of them as calibrated, and serves only when that is at or above the threshold and at least `n` samples share the majority answer; the choices are those samples by token probability, choice 0 the one a one-choice request would serve. Otherwise the request escalates (route `n_choices` when the student was confident) and the teacher writes `n` choices, at temperature 0.7 when the client sets none. Only the teacher's choice 0 can become a training example, so a request adds or bumps at most one; audits, arbitrations and the shadow judge choice 0. `usage.completion_tokens` adds up every choice, and a stream sends each choice's chunks with its own finish chunk. With `logprobs: true` every choice has `logprobs.content`, at most `top_logprobs` alternatives per token, whose tokens spell its content exactly: reasoning and fence tokens are dropped, and `content` is null when the model sent none or the answer cannot be told apart from what was dropped. The teacher gets `logprobs` and `top_logprobs` only when the client sends them; audits and arbitrations never | Done (v0.5) |

### 6.2 Cascade and confidence

| ID | Requirement | Status |
|---|---|---|
| FR-2.1 | Student sampled `n=3` times (temperature 0.7, logprobs on) per request (more only to fill a client's `n` choices, scored on the first 3, FR-1.14) | Done (v0.1) |
| FR-2.2 | Confidence = 0.4 × token probability of best majority sample + 0.6 × sample agreement; invalid-format answers score 0. Since v0.5 agreement is counted pairwise with the task's field comparators, and an answer that is not valid under the task's spec (a required field missing, a label outside its vocabulary) is invalid (FR-2.14) | Done (v0.1) |
| FR-2.3 | Each version carries its own calibrated threshold; global default 0.80 | Done (v0.1) |
| FR-2.4 | Escalate to teacher below threshold | Done (v0.1) |
| FR-2.5 | Audit a share of served student answers with the teacher (`audit_rate`, default 5%) | Done (v0.1) |
| FR-2.6 | Task definitions compare structured fields exactly (e.g. `category`, `priority`); free-text fields ignored for agreement. Since v0.5 each field has a typed comparator and the built-in fields are required labels over their vocabularies (FR-2.14); `compare_keys` keeps v0.4's exact rule | Done (v0.1) |
| FR-2.7 | Strip `<think>` reasoning and code fences before comparing, storing or serving: complete `<think>…</think>` blocks in any case, and since v0.5 also everything before a lone `</think>` (a chat template that opens the reasoning in the prompt) and an unterminated `<think>` that opens what is left, with all that follows (an answer cut off while reasoning; a tag after answer text is content); a structured task's answer that is one JSON object is kept whole, so tags quoted in its strings stay, and otherwise loses a code fence that wraps all of it when the body is JSON (v0.5), while a free-text answer keeps its fences, because a code block is content there (`tasks.clean_answer`) | Done (v0.1) |
| FR-2.8 | Canary traffic split by `traffic_pct` | Done (v0.1) |
| FR-2.9 | Pinned tasks: always answered by the teacher, logged, never stored as training examples. Since v0.5 only an operator's spec (built-in, file or API) pins a task, never a request, and Night School leaves out the stored rows of a task that is pinned now (FR-2.15) | Done (v0.2) |
| FR-2.10 | Optional calls never fail a request the student already answered: when the teacher's audit, the shadow's samples or the teacher's arbitration raise, the student's answer is served; each failure is counted (`Ratchet.health()`) and logged as a throttled `audit_error` or `shadow_error` event naming the exception class, never its message; a failed audit is not counted as an audit, and an unarbitrated shadow disagreement counts as a disagreement in the release gate ([ADR 0013](adr/0013-optional-teacher-calls-fail-open.md)) | Done (v0.5) |
| FR-2.11 | Student circuit breaker: after `RATCHET_STUDENT_BREAKER_FAILURES` (5) endpoint failures in a row (a timeout, a connection error, an unreadable or 5xx answer, 408, 429; not another 4xx) the student is skipped for `RATCHET_STUDENT_BREAKER_COOLDOWN_S` (30 s) and requests go straight to the teacher, logged with route `student_breaker` and not counted as student errors; then exactly one request probes the endpoint (another only once the probe has been out longer than the student call's own timeouts allow), a failed probe doubling the wait up to 300 s however slow it was and a successful one closing the breaker; every transition is a `circuit` event (at the 300 s wait, only the 1st, 10th, 100th and 1000th probe); requests the live student never answered do not count against a shadow's coverage; live and canary on one endpoint share a breaker, and shadow calls have their own. A vLLM 404 naming the version's adapter (lost in an endpoint restart) does not count against the breaker and, in live mode, queues an automatic reload of that adapter (at most once per adapter per 60 s; `adapter_reloaded` / `adapter_reload_failed` events). Every request row records its route (`student`, `confidence`, `cold_start`, `pinned`, `student_error`, `student_breaker`), and `/ratchet/stats` reports `routes` per task. The mock-only `POST /ratchet/demo/student-outage` shows the breaker open and close ([ADR 0015](adr/0015-student-circuit-breaker-and-adapter-reload.md)) | Done (v0.5) |
| FR-2.12 | When the teacher is down ([ADR 0019](adr/0019-teacher-breaker-degraded-mode-and-fallback-teacher.md)): every teacher call (escalation, cold start, pinned, teacher-only, audit, arbitration) goes through a circuit breaker that opens after `RATCHET_TEACHER_BREAKER_FAILURES` (5) failed calls in a row, each counted after its retries and only when the teacher looks down (a timeout, a connection error, an unreadable or 5xx answer, 408, 429), for `RATCHET_TEACHER_BREAKER_COOLDOWN_S` (30 s, doubling up to 300 s per failed probe); while it is open a request that needs the teacher's answer gets **503** `teacher_circuit_open` at once, with the time until the next probe as `Retry-After` (1 to 60 s), and audits and arbitrations are skipped without being counted again. Opt-in `RATCHET_ON_TEACHER_FAILURE=serve_student` (default `error`): when an escalation's teacher is unavailable (any failure but a 400, 413 or 422 about the request or a tool-call answer) and the student answered with at least one valid sample, that answer is served with route `degraded`, `ratchet.degraded` and `X-Ratchet-Degraded: teacher_unavailable`; it is not audited, stored or shadowed, so it never counts toward a release; pinned, teacher-only and tool requests never degrade. Opt-in fallback teacher `RATCHET_TEACHER_FALLBACK_MODEL` (at `RATCHET_TEACHER_FALLBACK_BASE`, default the teacher's endpoint, same key), licensed at startup (INV-1), answers a call whose answer is served only after the primary failed its retries or while its breaker is open, with route `fallback_teacher`, its own model id and price; its answers are not stored unless `RATCHET_STORE_FALLBACK_TEACHER=1`, and then with its own license. `/ratchet/stats` reports `degraded` and `fallback_teacher` per day | Done (v0.5) |
| FR-2.13 | Only complete, valid teacher answers become training data (INV-16, [ADR 0016](adr/0016-store-only-complete-valid-teacher-answers.md)): an answer is stored only when its `finish_reason` is `stop` or `tool_calls`, it is not empty once reasoning is stripped, and a structured task's answer parses as JSON, also after PII redaction. Any other answer is still served, but not stored, not counted as an audit (`audited = 0`) and never the reference that settles a shadow disagreement (an escalation or arbitration answer leaves `shadow_agree` NULL; an audit answer is ignored, and the shadow is compared with the served answer); it is logged as a `skip_example` event (the task and the reason, on the 1st, 10th, 100th and 1000th time per reason), and every request row records whether it stored an example (`requests.stored`). A student sample that did not end by itself (`finish_reason` other than `stop` or `tool_calls`) gets no vote, and a sample set with no valid answer is never served. The provider reads `finish_reason` and `usage.completion_tokens_details.reasoning_tokens` per choice, never the `reasoning_content` or `reasoning` fields, and splits `completion_tokens` over the choices by their logprob counts | Done (v0.5) |
| FR-2.14 | Field comparators and task specs ([ADR 0021](adr/0021-field-comparators-and-task-specs.md)): a task's `fields` are paths into the answer (`$` for all of it, dot paths into objects) with a typed comparator each: `exact` (v0.4's rule), `label` (NFKC, case-folded, separators collapsed, aliases, an optional vocabulary), `bool`, `number` (currency symbols and ISO 4217 codes, thousands separators by decimal mark, `(5)` as negative, percent; absolute and relative tolerance), `ordinal` (a tolerance in steps), `text` (exact, loose with suffixes such as `inc`, token set), `date` (ISO and common written forms, day-first option, UTC datetimes), `language` (ISO 639 codes, English and native names), `set` (Jaccard), `records` (keyed, ordered, or unordered by a maximum matching; at most 200 per answer, nested at most 4 deep) and `ranking` (top-k in order or as a set); at most 64 fields per task. A field is required unless it says otherwise: an answer with a required field missing, null when it is not nullable, or a value that does not normalise is invalid, so it gets no vote, is never stored (`skip_example` `invalid field <path>`), is not an audit or a shadow reference, is skipped as a golden reference (`golden_invalid_refs`) and, stored before the spec tightened, as a training row (`skipped_invalid` in the dataset manifest). Confidence counts agreement pairwise, so comparators with a tolerance work. The built-in tasks' labels are required and checked against `ratchet/vocab.py`, the lists their prompts offer; a task without fields compares canonical JSON when both answers are JSON, else the text; `compare_keys` stays a shortcut with v0.4's results; a task with `store` false is never stored | Done (v0.5) |
| FR-2.15 | Task registry ([ADR 0022](adr/0022-task-registry.md)): tasks are defined as data, in `RATCHET_TASKS_FILE` (JSON, or YAML with PyYAML; loaded at gateway startup and by `python -m ratchet.live`) or through the admin API (`GET /ratchet/tasks`, `GET /ratchet/tasks/{name}`, `POST /ratchet/tasks/validate`, `PUT` and `DELETE /ratchet/tasks/{name}`; API tasks are stored in the ledger's `tasks` table and registered again at startup). A spec has `name`, `description`, `format`, `fields` (path: type and options), `pinned`, `store`, `extends` (copy another task, merging `fields`, `policy` and `match` by key), `match`, `routing`, `policy`, `tools` and `clients`; unknown keys and options, wrong types and out-of-range values are refused, and every error is listed with its path (`tasks[2].fields.total.abs_tol: must be a number >= 0`), so a bad file stops startup. A file task may replace a built-in one; the API answers 403 for a built-in or file task and 422 with the errors for a bad spec; at most 500 tasks (50 API tasks in mock mode). Writes need an admin key (`RATCHET_ADMIN_KEYS`; 401 without one, 403 for an API key; the admin API is off in live mode without admin keys and open in mock mode), and only the key's name is stored and logged. Only an operator's spec pins a task: nothing in a request can. `RATCHET_UNKNOWN_TASKS` decides what an unregistered task name gets: `distill` (default, v0.4), `teacher` (the teacher answers, route `unknown_task`, nothing stored, no student call) or `reject` (400 `unknown_task` listing the registered tasks); under `teacher` and `reject` a header that is not a valid task name is a 400 too. `POST /ratchet/tasks/validate` checks a document as startup reads the tasks file (`extends` reaches built-in tasks and the file's own; the tasks fit in the registry). A task's `clients` limit it to those API keys (403 `task_not_allowed` for any other). Night School leaves out the rows of a task that is pinned now (`excluded_pinned`), records each golden task's `spec_hash` in every eval it writes, and a task whose spec changed since the serving version was scored goes to the teacher until a night scores the version under the new spec (also a night without enough new data to train) | Done (v0.5) |

### 6.3 Provenance and licensing

| ID | Requirement | Status |
|---|---|---|
| FR-3.1 | License registry for teachers; open Nemotron models allowed | Done (v0.1) |
| FR-3.2 | Closed models (OpenAI, Anthropic, Google prefixes) and unknown models refused | Done (v0.1) |
| FR-3.3 | Every example stores teacher, license, license URL, hash, split | Done (v0.1) |
| FR-3.4 | Duplicates stored once; repeat count kept in `seen` | Done (v0.1) |
| FR-3.5 | `GET /ratchet/bom` model bill of materials with dataset SHA-256; since v0.5 over the examples that still have their text, with `purged_examples` counting the purged ones (FR-13.3, FR-13.4) | Done (v0.1) |
| FR-3.6 | Signed, exportable BOM attached to each adapter: Ed25519, `/ratchet/versions/{id}/bom`, `python -m ratchet.bom verify` | Done (v0.4) |

### 6.4 Night School (training)

| ID | Requirement | Status |
|---|---|---|
| FR-4.1 | Build cumulative training set nightly from teacher answers only (never student answers) | Done (v0.1) |
| FR-4.2 | Retrain from the base model each night (no adapter stacking) | Done (v0.1) |
| FR-4.3 | Golden set: ~10% of unique requests by hash, held out; requests seen 3+ times move to training | Done (v0.1) |
| FR-4.4 | Skip training if fewer than 50 new examples, no golden set, or a release is in flight. New examples are the training rows now less those of the largest version so far, plus the training rows that version had which were purged since (v0.5, FR-13.3, FR-13.4), so deleting data never stops training | Done (v0.1) |
| FR-4.5 | LoRA fine-tune script with Unsloth (fallback: transformers + peft) for Nemotron 3 Nano 4B | Done (v0.1), untested (on GPU) |
| FR-4.6 | Run training as a Nebius Serverless Job; wait for adapter in bucket | Done (v0.1), untested (on Nebius) |
| FR-4.7 | PII redaction before storing examples (pattern-based with checksums; names and addresses not covered), in message content, `name`, `refusal` and tool and function call arguments | Done (v0.4); NER-based detection planned |

### 6.5 Evaluation

| ID | Requirement | Status |
|---|---|---|
| FR-5.1 | Golden eval samples the student exactly as in production: the same sampling, and since v0.5 the same generation parameters, from the conditions stored with each example (FR-1.13) | Done (v0.1) |
| FR-5.2 | Calibrate threshold on half the golden set (target precision 97%), measure on the other half | Done (v0.1) |
| FR-5.3 | Report precision, coverage, threshold, raw agreement | Done (v0.1) |
| FR-5.4 | Re-score the live version on the same golden set each night (champion vs challenger) | Done (v0.1) |
| FR-5.5 | Reference-based judge (Nemotron 3 Super, JSON mode) for free-text tasks | Done (v0.1), untested (live) |

### 6.6 Release gate

| ID | Requirement | Status |
|---|---|---|
| FR-6.1 | Golden gate: precision ≥ 95%, coverage ≥ 25%, no regression vs live beyond 2 points | Done (v0.1) |
| FR-6.2 | Cold start (no live): first passing candidate goes straight to canary 10% | Done (v0.1) |
| FR-6.3 | Shadow mode: candidate scores real traffic silently; teacher arbitrates disagreements with the live student | Done (v0.1) |
| FR-6.4 | Shadow promotion: ≥ 100 would-serve answers, precision ≥ 95%, coverage ≥ live − 2 points | Done (v0.1) |
| FR-6.5 | Canary 10% → 50% → 100%, each step needs ≥ 20 audits in that stage and precision ≥ 92% | Done (v0.1) |
| FR-6.6 | Rollback when Wilson 95% upper bound of audited precision < 92% | Done (v0.1) |
| FR-6.7 | Live alert when live version's audited precision upper bound < 92% | Done (v0.1) |
| FR-6.8 | Only one candidate in flight; older shadow/canary retired | Done (v0.1) |

### 6.7 Dashboard

| ID | Requirement | Status |
|---|---|---|
| FR-7.1 | Tiles: saved vs all-teacher, cost reduction, student share, live version | Done (v0.1) |
| FR-7.2 | Charts: cost per 1,000 requests vs baseline; student vs escalated share by day | Done (v0.1) |
| FR-7.3 | Versions table with eval numbers and state | Done (v0.1) |
| FR-7.4 | Release log and provenance panel | Done (v0.1) |
| FR-7.5 | Mock-mode controls: run a day, break next deploy, reset | Done (v0.1) |
| FR-7.6 | Light and dark mode; works at phone width | Done (v0.1) |
| FR-7.7 | **By task** table (requests, student share, escalations, savings, route) from `tasks` in `/ratchet/stats` | Done (v0.2) |
| FR-7.8 | Traffic selector for **Run 1 day + night** (tickets, Scout research calls, both); demo controls run one at a time (409) and reset has a 30 s cooldown (429) | Done (v0.2) |

### 6.8 Live operations on Nebius

| ID | Requirement | Status |
|---|---|---|
| FR-8.1 | Teacher and judge via Token Factory OpenAI-compatible API | Done (v0.1), untested (live) |
| FR-8.2 | Student served by vLLM on a Nebius Serverless Endpoint with runtime LoRA loading | Done (v0.1), untested (live) |
| FR-8.3 | Fallback to hosted untuned Nemotron Nano on Token Factory when no student endpoint | Done (v0.1) |
| FR-8.4 | `python -m ratchet.live night` and `tick` commands for cron | Done (v0.1), untested (on Nebius) |
| FR-8.5 | `scripts/day1_check.py` verifies model IDs, logprobs, JSON mode | Done (v0.1), untested (live) |

### 6.9 Simulation

| ID | Requirement | Status |
|---|---|---|
| FR-9.1 | Synthetic support tickets (6 categories, 4 priorities, 20 templates, 3 drift templates) | Done (v0.1) |
| FR-9.2 | Mock teacher, mock student with learning curve and confidently-wrong failures | Done (v0.1) |
| FR-9.3 | Fault injection: corrupted deploy of a chosen version | Done (v0.1) |
| FR-9.4 | CLI `python -m sim.simulate` with table output and JSON results | Done (v0.1) |
| FR-9.5 | Mock research world for Scout (8 questions, simulated results on `example.org`) and `--workload tickets\|research\|mixed` | Done (v0.2) |
| FR-9.6 | Mock warm-up: `RATCHET_MOCK_WARMUP_DAYS` simulated days at startup | Done (v0.2) |

### 6.10 Scout (multi-agent demo)

| ID | Requirement | Status |
|---|---|---|
| FR-10.1 | Five agents (planner, searcher, triage, reader, writer); every LLM call goes through Ratchet with a task name | Done (v0.2) |
| FR-10.2 | Planner and writer pinned to the teacher; triage and reader distillable | Done (v0.2) |
| FR-10.3 | Live search with the Tavily Search API; simulated search in mock mode, labelled in the UI. The Tavily client is tested offline only | Done (v0.2), untested (live) |
| FR-10.4 | Streaming run events to the page (Server-Sent Events) and a JSON endpoint (`/scout/run`) | Done (v0.2) |
| FR-10.5 | Budget guard: daily spend cap, daily run cap, per-IP hourly limit; spend counted per step | Done (v0.2) |
| FR-10.6 | CLI client: `python -m ratchet.scout "<question>" --base-url <gateway>/v1` | Done (v0.2) |

### 6.11 Demo hosting

| ID | Requirement | Status |
|---|---|---|
| FR-11.1 | One-command Nebius CPU VM setup (cloud-init, Docker Compose, Caddy HTTPS, firewall) | Done (v0.2), untested (on Nebius) |
| FR-11.2 | Daily ledger backups, update script with health checks | Done (v0.2), untested (on Nebius) |

### 6.12 Operations

No rows yet. `FR-12.x` rows go here, in the same `| ID | Requirement | Status |` table as the sections above.

### 6.13 Data governance

What is stored, about whom, and how a client or an end user keeps their data out of training.

| ID | Requirement | Status |
|---|---|---|
| FR-13.1 | Storage opt-outs ([ADR 0080](adr/0080-storage-opt-outs-and-pseudonymous-end-users.md)): a request's teacher answers are never stored as training examples (no example inserted, no `seen` bumped) when the body sends `store: false`, the `X-Ratchet-Store` header is `none`, `0` or `false`, or `metadata.ratchet_store` is `"false"` or `"0"`; when its API key has `store=0` in `RATCHET_KEY_LIMITS`; when its task's spec says `store: false` or `RATCHET_NO_STORE_TASKS` names it; when its end user is registered as no-train (FR-13.2); and for every request under `RATCHET_STORE_EXAMPLES=0` (zero retention), where Night School skips every night (`storage disabled`). An absent `store` means store, unlike OpenAI's default. The cascade runs as usual: the student may serve, audits still record agreement and shadow disagreements are still arbitrated, so release decisions do not depend on storage. Each request row records `stored` 0 and the broadest opt-out that applied (`requests.store_opt_out`: `global`, `task`, `key`, `request` or `end_user`); the response's `ratchet` block says `stored` and `store_opt_out`, and the response echoes `metadata`. Any other `X-Ratchet-Store` or `metadata.ratchet_store` value is a 400, and nothing in a request can undo an opt-out. `/ratchet/stats` reports `not_stored_requests` (and by reason), the opt-outs in force and each client's `stored_share` | Done (v0.5) |
| FR-13.2 | End-user attribution by HMAC only (ADR 0080): a request's end user is its `safety_identifier`, else its `user` (at most 128 characters; longer is a 400 that never quotes it), kept as HMAC-SHA256 under the deployment's key (`RATCHET_END_USER_KEY`, or 32 random bytes generated once and kept in the ledger's `meta` table, so hashes survive restarts but cannot be linked across deployments), scoped to the API key name, cut to 16 hex digits, in `requests.end_user` (indexed with `ts`) and `examples.end_user`. The raw value is never stored or logged. Examples record the API client of the request they were first stored from (`examples.client`); a duplicate from another client is recorded in `example_clients`. `POST /v1/ratchet/no-train` (`{"end_user": ...}`) registers an end user as no-train, so later requests from that user are not stored: open wherever chat completions are, an API key registers its own end users, an admin key a named client's or every client's. `DELETE` removes a registration: an API key its own but not one an admin made, an admin any, and without API keys a live gateway needs an admin key. `/ratchet/stats` counts no-train end users and each client's distinct `end_users`; a changed key writes an `end_user_key` event | Done (v0.5) |
| FR-13.3 | Verifiable erasure ([ADR 0081](adr/0081-retention-tombstones-erasure-and-export.md)): `POST /ratchet/admin/privacy/erase` (admin key; a dry run unless `dry_run` is false), `DELETE /v1/ratchet/examples` (an API key erases its own end users' examples; the end user in the JSON body, never the URL) and `python -m ratchet.admin erase` (a dry run unless `--apply`) select examples by `client`, `end_user` (its HMAC in the key's scope), `hashes` and `contains` (a LIKE match on the stored, already-redacted text), each narrowing the scope. An erased example keeps its hash, task, split, seen count, teacher and license as a tombstone, loses its messages, answer, conditions and end user, and is never stored again (`add_example` refuses it, from any client, and counts the refusal; the request is served and logged `stored` 0 with a `skip_example` `erased` event). Under `RATCHET_ERASURE_SHARED=delete` (default) an example another client also sent is purged too; under `keep` it stays and the erased party is unlinked. Erasing an end user also clears its hash from its request rows. Each erasure is logged in `erasures` (actor, redacted reason, scope with end users as HMACs and `contains` as its SHA-256, counts, `tainted` versions, `status`) and `erasure_items` (hashes, task and split, never text) and as an `erase` event with counts. The versions that may have been trained on the erased examples are reported (exactly by committed leaves when a version has them, else versions created after the example while it counted as a training row) and never edited; signed BOMs stay as written (INV-14). Unlearning from those adapters is out of scope | Done (v0.5) |
| FR-13.4 | Retention windows (ADR 0081): `RATCHET_EXAMPLE_RETENTION_DAYS` purges the text of examples not seen within the window (golden ones too; a later duplicate that may be stored restores it with its own provenance), and `RATCHET_REQUEST_RETENTION_DAYS` folds older request rows into `daily_rollup` (per day, task, client, route and opt-out: every sum a stats reader needs, no end user) and deletes them; `stats_by_day`, `stats_by_task`, `stats_by_client`, `totals`, the opt-out counts and the cost report add the sums back, so every figure but a client's distinct end users stays the same. Rows newer than the start of a release stage in flight are never folded, and day numbers survive the rows that defined day 1. Applied at most hourly by the live gateway on a background thread, at the start of every live night, and by `python -m ratchet.live purge [--client X] [--older-than-days N] [--dry-run]`; 0 keeps forever (default). A purged row still counts toward its request's frequency (INV-3). `/ratchet/stats` reports the windows and what was purged, erased and folded | Done (v0.5) |
| FR-13.5 | Export (ADR 0081): `GET /ratchet/admin/export/{examples,requests,versions,events,erasures}` (admin key) and `python -m ratchet.admin export` stream NDJSON in id order, a page at a time; examples are the ones that still have their text, with their provenance (teacher, license, license URL, hash, redactions, origin), split, seen count and every client that sent them; versions carry their BOMs as stored, erasures their items. Filters `task`, `split`, `since` (epoch seconds or an ISO 8601 date) and `client` where the table has them (400 otherwise). API keys are never stored and end-user hashes are left out, so no line holds either | Done (v0.5) |

### 6.14 Use cases

No rows yet. `FR-14.x` rows go here.

### 6.15 Cost and economics

A self-hosted student is billed by the GPU-hour while its endpoint runs, not per token. Below a break-even volume it costs more than it saves (see [BUSINESS.md](BUSINESS.md#when-self-hosting-pays-utilization-aware)). Ratchet must report that honestly.

| ID | Requirement | Status |
|---|---|---|
| FR-15.1 | Each request's cost is split into student and teacher spend (`requests.student_cost_usd`, `teacher_cost_usd`, with the student calls' tokens); the existing `cost_usd` stays their sum, summed call by call exactly as before, so history and the reference simulation never move ([ADR 0023](adr/0023-utilization-aware-cost-accounting.md)) | Done (v0.5) |
| FR-15.2 | Compute-time ledger: student endpoint uptime and training-job GPU-hours (`compute_intervals`), recorded by a CLI (`python -m ratchet.cost endpoint up\|down`), an authenticated endpoint (`POST /ratchet/cost/endpoint`, admin key), the deploy script, Night School (the training job's wall-clock time) and the simulator (a synthetic clock); overlapping intervals never double-count | Done (v0.5), untested (on Nebius) |
| FR-15.3 | Cost report (`GET /ratchet/cost`, `python -m ratchet.cost report`): savings per token and by GPU-hours paid (negative when self-hosting does not pay, never clamped), break-even student-served requests per GPU-hour, the crossover with the hosted fallback, and observed utilization; a compact `cost` block in `/ratchet/stats` | Done (v0.5) |
| FR-15.4 | Self-host recommendation (self-host, hosted fallback, scale to zero, or not enough data) with a plain-English explanation and the numbers behind it | Done (v0.5) |
| FR-15.5 | Verified savings receipts per API client: an audit-calibrated baseline with a confidence bound, both cost views, signed | Planned |

### 6.16 Verifiable evidence

Claims that a judge, customer or auditor can check from the data, not take on trust.

| ID | Requirement | Status |
|---|---|---|
| FR-16.1 | Anytime-valid release gate (opt-in): rollback by an e-process with no minimum audit count, evidence-based promotion, and confidence sequences, valid however often the gate runs | Planned |
| FR-16.2 | Anytime-valid live alert: the live version's alert uses the same e-process, at most one false alert per version at the stated rate | Planned |
| FR-16.3 | Certified precision: each candidate carries a Clopper–Pearson lower bound on precision at its threshold, recorded in its signed BOM; opt-in Learn-then-Test calibration | Planned |
| FR-16.4 | Committed training sets: each adapter's BOM commits to its training and golden examples with Merkle roots, with membership and non-membership proofs | Planned |
| FR-16.5 | Ledger time machine: replay past traffic under a different threshold or audit rate from logged propensities, with unbiased estimates | Planned |
| FR-16.6 | Label-free drift alarm with a bounded false-alarm rate (conformal test martingale on student confidence) | Planned |
| FR-16.7 | Teacher watch: free teacher self-consistency checks from duplicate requests; inconsistent labels are quarantined from training | Planned |
| FR-16.8 | Clean-room backfill: a day-0 student from historical prompts relabelled by the teacher through the Token Factory Batch API, with provenance in the BOM | Planned |
| FR-16.9 | Provable erasure: forget an example, retrain without it, and issue a signed certificate that the new adapter's training set excludes it | Planned |
| FR-16.10 | Guarantee harness: Monte Carlo checks that each statistical guarantee holds at its stated rate | Planned |
| FR-16.11 | Evidence tour: one command exercises every feature in mock mode and writes a signed evidence bundle | Planned |

## 7. Non-functional requirements

| ID | Area | Requirement | Status |
|---|---|---|---|
| NFR-1 | Latency | Student path adds at most one student call (n samples in one request). Target p50 under 1 s for student-served requests. Escalated requests cost one student call plus one teacher call. | Done (v0.1), untested (live) |
| NFR-2 | Availability | If the student endpoint errors, requests fall back to the teacher automatically (v0.1.1); if the teacher errors during an audit or a shadow arbitration, the student's answer is served (v0.5, FR-2.10); teacher calls are retried on transient failures within a deadline, and a hung student endpoint holds a request only for its own timeouts (2 s to connect, 15 s to answer, by default) before the teacher answers (v0.5, FR-1.11), and after 5 such failures in a row a circuit breaker skips it, so requests go straight to the teacher until a probe every 30 s (doubling to 300 s) finds it back (v0.5, FR-2.11); with `RATCHET_STUDENT_BASE` unset, the hosted untuned Nano is used with the default threshold. If the teacher keeps failing, its own breaker answers requests that need it with a 503 and a `Retry-After` at once instead of holding each for the 180 s deadline; opt-in, a licensed fallback teacher answers instead, or the student's own valid answer is served marked as degraded (v0.5, FR-2.12). Adapters lost in an endpoint restart are reloaded automatically when vLLM answers 404 for them (v0.5, FR-2.11); `python -m ratchet.live reload` stays as the manual fallback. A demo URL must stay live through judging (Dec 1–15, 2026). | Done (v0.1.1); a demo URL that stays live through judging planned |
| NFR-3 | Cost | Shadow, audit, and arbitration overhead is counted in reported cost. No training while a release is in flight. Every model call has an output-token cap (v0.5, FR-1.13). | Done (v0.1) |
| NFR-4 | Privacy | Prompts and teacher answers are stored in SQLite with identifiers (emails, phones, cards, IBANs, national IDs, IPs, secrets) replaced by placeholders (v0.4). Storage can be switched off per request, API key, task or end user, or for the whole gateway (zero retention; v0.5, FR-13.1), and end users are recorded only as keyed hashes (v0.5, FR-13.2). Stored examples can be erased for good by client, end user, hash or text, expire after a retention window, and can be exported with their provenance; request rows fold into daily sums after their own window (v0.5, FR-13.3 to FR-13.5). Use synthetic data only in demos. | Done (v0.5); NER-based redaction (Presidio) planned |
| NFR-5 | Security | Secrets come from environment variables only. The student endpoint uses token auth. Named gateway API keys (`RATCHET_API_KEYS`, v0.4) protect `/v1/*`, each with an optional daily request quota (`RATCHET_API_KEY_DAILY_REQUESTS`, 429 past it). Keys are compared as constant-time digests and are never stored or logged: the ledger and `/ratchet/stats` see only key names. `/healthz`, the pages, Scout and `/ratchet/*` take no key; on the demo VM, Caddy can guard `/v1/*` and `/ratchet/demo/*` with an admin token. See [Threat model](THREAT_MODEL.md). | Done (v0.4) |
| NFR-6 | Observability | Every request, version change, and release event is logged in the ledger and visible on the dashboard. | Done (v0.1) |
| NFR-7 | Portability | Any OpenAI-compatible teacher or student endpoint works. Adapters are standard LoRA files the customer can download and run elsewhere. | Done (v0.1) |
| NFR-8 | Correctness | Release decisions use held-out data and statistical bounds, not single numbers. | Done (v0.1) |
| NFR-9 | Licensing | Code under Apache-2.0. Model licenses (OpenMDW-1.1, NVIDIA Nemotron Open Model License) travel with any distributed adapter. | Partial: teacher licenses travel in each adapter's BOM (v0.4); the base-model license does not yet |
| NFR-10 | Traceability | Every requirement in this PRD and every invariant in CLAUDE.md maps to the code that implements it and the tests that prove it, in the generated [REQUIREMENTS.md](REQUIREMENTS.md). CI fails on a status outside the grammar above, a done requirement without a test or a recorded exception, a test that cites an unknown or replaced id, or a stale matrix. | Done (v0.5) |
| NFR-11 | Cost honesty | Every savings figure names its cost model. Alongside the per-token view, Ratchet reports savings by GPU-hours actually paid for the student endpoint and training, including negative savings below break-even, and labels list prices as unverified until the day-1 checks pass (`RATCHET_PRICES_VERIFIED`). | Done (v0.5) |

## 8. Success metrics

### 8.1 Product metrics

| ID | Metric | Target | Status |
|---|---|---|---|
| SM-1 | Cost reduction vs all-teacher, steady state | ≥ 70% | Not checked |
| SM-2 | Audited precision of student-served answers | ≥ 95% | Not checked |
| SM-3 | Student share of traffic, steady state | ≥ 80% | Not checked |
| SM-4 | Bad deploys reaching 100% traffic | 0 | Not checked |
| SM-5 | Time to recover from drift (escalations back to baseline) | ≤ 3 nights | Not checked |

`Not checked` means no test computes the metric yet. The simulation results below are a single run, not a check.

**Simulation results (mock models, not real model quality):** with seed 7, 2,000 tickets a day over 10 days, a product launch on day 5, and a corrupted deploy of v3:

- The student answered 98% of requests by day 3.
- Savings were 85–87% at steady state and 69% over the whole run, including the cold start.
- Audited precision ranged from 92% to 100%.
- The corrupted v3 was rolled back at 41.2% audited precision.
- Drift raised escalations to 14% on day 5; they were back to 2% by day 9.
- Across four other seeds, the final student share was 98% and final savings 85–87%.

### 8.2 Hackathon

| Metric | Target |
|---|---|
| Submission complete by Oct 29, 2026 | Yes |
| Live run on Token Factory + Nebius AI Cloud with real numbers in README and video | Yes |
| Demo URL live through Dec 15, 2026 | Yes |
| Stage One (pass/fail) met: the hosted demo runs in live mode during judging, with runtime Token Factory and Tavily calls | Yes |
| Real Nebius bill reported (total, failed jobs included) next to the cost report's break-even line | Yes |
| Placement | Track win or top 3 |

## 9. Release plan

| Release | Scope | Status |
|---|---|---|
| v0.1 (hackathon MVP) | Everything marked Done (v0.1) above; 34 tests passing; simulation and mock dashboard working | Built Oct 19, 2026 |
| v0.2.0 (demo layer) | Scout, pinned tasks, landing page, By task table, workloads, mock warm-up, Nebius VM deploy kit; 49 tests passing | Built Oct 20, 2026 |
| v0.3.0 (web app) | React story page, Scout page and dashboard; 49 tests passing | Built Oct 20, 2026 |
| v0.4.0 (hardening) | PII redaction, gateway API keys and quotas, streaming, signed BOM per adapter, `/healthz`; 64 tests passing | Built Oct 20, 2026 |
| v0.1 live | Day-1 checks; Nano 4B trained on a Serverless Job; vLLM endpoint; real numbers | Oct 20–28, 2026 |
| v0.5 (backend completeness) | Work packages A–F: traceability and a test safety net; request contract and resilience; task model and use cases; operations, security and performance; data governance and success metrics; cost accounting and verifiable evidence (§6.15, §6.16). See [Roadmap](ROADMAP.md) Milestone 0.9 | In progress (from Oct 20, 2026) |
| v0.6 | Postgres ledger, multi-worker shared state, PyPI package, NER-based redaction | After hackathon |
| v1.0 | Managed Ratchet Cloud, multi-tenant, savings-based billing, region choice (EU, India) | Startup track |

See [Roadmap](ROADMAP.md) for dates and exit criteria.

## 10. Dependencies

| Dependency | Use | Notes |
|---|---|---|
| Nebius Token Factory | Teacher (Nemotron 3 Ultra), judge (Super), fallback (Nano 30B) | OpenAI-compatible API; Builder Program gives $25 credit |
| Nebius Serverless Jobs | Nightly LoRA training | GPU billed per second |
| Nebius Serverless Endpoints | vLLM student serving | Stopped endpoints cost nothing |
| Nebius Object Storage | Datasets and adapters | S3-compatible |
| NVIDIA Nemotron 3 Nano 4B | Student base | NVIDIA Nemotron Open Model License |
| vLLM | Student serving, runtime LoRA | Nemotron-H LoRA support listed |
| Unsloth / TRL / PEFT | Training | Unsloth supports Nemotron 3 |
| FastAPI, httpx, SQLite | Gateway, client, ledger | |

## 11. Risks and mitigations

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| R1 | Token Factory cannot fine-tune or host a tuned Nemotron | High | Train on Serverless Jobs, serve on Serverless Endpoints with vLLM |
| R2 | Nano 30B needs about 60GB VRAM for LoRA | High | Use Nano 4B on L40S; 30B is a stretch goal |
| R3 | Small budget; no GPU credits in the Builder Program | High | Short training runs; stop endpoints when idle; ask for credits |
| R4 | Demo must stay live Dec 1–15 | Medium | Fallback to hosted Nano; mock-mode demo |
| R5 | Token Factory ToS 5(f) "competing service" clause | Medium | Email sent to tokenfactory-support@nebius.com on Oct 19; position as built on Nebius end to end |
| R6 | Small models are poorly calibrated | Medium | Combined confidence signal; per-version calibration; shadow; audits |
| R7 | Judge bias (all Nemotron) | Medium | Exact-match labels; reference-based judge only for free text |
| R8 | Forgetting and drift across retrains | Medium | Retrain from base on cumulative data; golden gate; champion vs challenger |
| R9 | PII in logs | Low for demo, high for startup | Synthetic data in demos; pattern-based redaction before storing (v0.4); Presidio for names and addresses planned |
| R10 | Cheap frontier tiers shrink savings to about 2x | Medium | Lead with quality, ownership, license safety, latency |
| R11 | Funded competitors (Orchestra/Understudy, Experiential Labs, Inference.net) | High for startup | License-clean wedge; release safety; open source first |

## 12. Open questions

| # | Question | Owner | How we close it |
|---|---|---|---|
| Q1 | Does Token Factory ToS 5(f) allow Ratchet? | Harsh | Email sent Oct 19, 2026; follow up in hackathon Discord if no reply in 2–3 days |
| Q2 | Do Nemotron models on Token Factory return logprobs? | Harsh | `scripts/day1_check.py` |
| Q3 | Does vLLM serve LoRA on Nemotron-H (Nano 4B) correctly, including runtime loading? | Harsh | Day-1 endpoint test; fallback: merge adapter |
| Q4 | What does Serverless GPU time actually cost? | Harsh | Nebius console on day 1 |
| Q5 | Are the exact `nebius ai` CLI flags in `deploy/` correct? | Harsh | `--help` on day 1 |
| Q6 | Which live task and dataset to show in the video? | Harsh | Default: synthetic ticket triage through the real teacher |

## 13. Glossary

See [Glossary](GLOSSARY.md).
