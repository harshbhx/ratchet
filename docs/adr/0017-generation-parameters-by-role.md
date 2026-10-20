# ADR 0017: Generation parameters by role, token caps, and the conditions stored with an example

- Status: Accepted
- Date: 2026-10-20

## Context

Until v0.5 the gateway validated `max_tokens`, `stop`, `seed`, the penalties, `response_format`, `tools` and the rest ([ADR 0012](0012-openai-request-validation-and-error-envelope.md)) and then dropped them: `Provider.complete` could not send them. The teacher always ran at temperature 0 with no output limit, and the student at its fixed sampling. Three problems follow:

- **Clients get the wrong answer.** A client that asks for `stop: ["\n\n"]` or 50 tokens, or JSON mode, gets whatever the teacher writes without them. Agent frameworks depend on stops and JSON mode.
- **Cost has no ceiling.** Nothing bounds a teacher answer (or a student sample) but the model's context. One long answer, or a client that asks for 10⁶ tokens, is paid in full (threat model T11).
- **Training targets lose their conditions.** A teacher answer written under JSON mode or a stop string is stored as if it had been written for the bare prompt. Two requests with the same messages but different formats collapse into one example, and the golden eval scores the student without the conditions production will impose.

The roles do not want the same parameters. The teacher serving an answer should do what the client asked. The student's sampling must not change: confidence is scored over n samples at temperature 0.7 with logprobs (FR-2.1), and its threshold was calibrated that way. An audit or arbitration is a reference the student's answer is compared with, so it should be deterministic, under the same conditions as the answer it checks.

## Decision

- **One carrier.** `providers.GenParams` holds a request's parameters by OpenAI's names (`max_completion_tokens` arrives as `max_tokens`, `stop` as a tuple, unknown top-level fields in `extra`). `Provider.complete(messages, model, n, temperature, logprobs, response_format, params=None, **_)`; the explicit arguments win over the same fields in `params`, and `logprobs=False` also drops `top_logprobs`.
- **One policy, three roles.** `providers.GenPolicy` (from `Settings.gen_policy()`) turns the client's parameters into a `GenPlan` per request:
  - *student*: the fields in `RATCHET_STUDENT_PARAMS` (default `max_tokens`, `stop`, `seed`, `response_format`, `tools`, `tool_choice`, `logit_bias`, `top_logprobs`: what shapes the answer it must give), `max_tokens` at most `RATCHET_STUDENT_MAX_TOKENS` (1,024). Its n, temperature and logprobs stay `Settings.samples` and `confidence.STUDENT_SAMPLING`, so calibration still holds.
  - *serve* (escalation, cold start, pinned, teacher-only): the fields in `RATCHET_TEACHER_PARAMS` (default every standard field; `user`, `safety_identifier`, `store` and `metadata` are not generation parameters and are never forwarded), at the client's temperature or 0, `max_tokens` = the client's plus `RATCHET_TEACHER_REASONING_HEADROOM`, at most `RATCHET_TEACHER_MAX_TOKENS` (8,192), or the cap alone.
  - *check* (audit, arbitration): the serve parameters at temperature 0 without `top_p`, `seed`, the penalties, `logit_bias` and logprobs; the length limit, stops, format, tools and reasoning effort stay, so the comparison is like for like.
  The router, `LiveEvaluator` and `MockFleet` all use the policy, so the golden eval sends the student what production sends (FR-5.1). A field outside an allowlist is never sent; the caps apply either way. A vendor field (`top_k`) is forwarded only when an allowlist names it. An allowlist that names a field Ratchet sets itself (`model`, `n`, `messages`, `stream`) or never forwards, or a negative cap, stops startup.
- **The reasoning headroom.** A teacher that reasons before it answers spends output tokens on reasoning; with the client's limit alone it can be cut off before the answer. The headroom (default 0) lets it write more. Its visible answer (`tasks.clean_answer`) can then exceed what the client asked for; when it does by `approx_tokens`, the answer is marked `length`: the client is told, and the answer is not stored (INV-16) or used as a reference. The text is not cut, because the count is an estimate. Without headroom the teacher stopped at the client's limit itself and its own `finish_reason` stands.
- **Conditions stored with the example.** `ledger.STORED_PARAM_KEYS` = `response_format`, `tools`, `tool_choice`, `stop`: the conditions that define what a right answer is. Temperature, seed and penalties change how an answer is sampled, not what it should be, and are not stored. `_store_example` stores the conditions of the call that produced the answer (the check parameters for an audit), redacted like the messages (`redact.redact_json`, INV-12), in `examples.params`. `example_hash(task, messages, params)` adds a `params` key to the hashed object only when there are conditions, so every v0.4 hash, and every golden or train split derived from it, is unchanged, and the same messages under other conditions are another example. `dataset.rows` returns them and `cascade_eval` passes them to the student sampler.
- **The client sees the real finish.** `Result.finish_reason` is the served answer's (`stop`, `length`, `tool_calls`, `content_filter`; anything else is reported as `stop`), and the gateway returns it in the response and the last stream chunk.
- **A request channel for later items.** `router.RequestMeta(request_id, client_request_id, retry_count, storable, end_user, idempotency_key)` travels with `handle(messages, task, client, params, meta)`. `storable=False` keeps the request's teacher answers out of the training set; the gateway does not set any field yet.

## Consequences

- PRD FR-1.13 (new), FR-5.1 and NFR-3 (extended).
- The mock models honour `n`, `stop` and `max_tokens` (about 4 characters per token) and report `finish_reason`. Mock answers are short and contain no stop string, so the pinned reference simulation is identical.
- Every teacher call now carries `max_tokens` (8,192 by default). A reasoning teacher whose reasoning plus answer needs more is cut off (`finish_reason length`, not stored); raise the cap, or set the headroom when clients send tight limits.
- A server that checks prompt plus `max_tokens` against its context (vLLM does) now refuses a prompt that leaves less room than the cap. For the teacher, `RATCHET_MAX_INPUT_CHARS` keeps prompts far below its context; a student refusal is a 4xx, so the request escalates and the breaker does not count it.
- A custom `Provider` must accept `params` (and should accept `**_`); one written against v0.4's signature raises `TypeError` on the first call.
- Forwarding `tools` means the teacher may answer with tool calls. Until the tool-call item parses them, such an answer has empty content: it is served empty and not stored (`empty answer`). Before, the teacher never saw the tools, and its prose answer to a prompt stripped of them was stored as a target.
- `logit_bias` is forwarded by default, but token ids are tokenizer-specific: an id meant for OpenAI's tokenizer means something else to Nemotron. Remove it from the allowlists when clients send OpenAI ids.
- Night School's training script does not yet render stored `tools` into the chat template; training rows carry `params` for when it does.

## Alternatives considered

- **Forward everything to every call.** Simple, but the client's temperature would change the student's sampling and break calibration, and an audit at temperature 1.2 is not a reference.
- **Filter in the provider only.** The provider cannot tell the client's `max_tokens` from Ratchet's cap, or the client's temperature from the audit's 0, so the allowlists live in the policy; `OpenAICompatProvider(allow=...)` remains for a provider used on its own.
- **Store every parameter with the example.** Seeds and temperatures would split one request into many examples that teach the same thing, and change the dedup and golden-split identity of almost every example.
- **Cut an over-budget answer to the client's limit.** It would match OpenAI more closely, but the cut would rest on an estimate of the tokenizer and could break a JSON answer the real tokenizer fits.
- **Refuse `max_tokens` above the cap with a 400**, as OpenAI does above a model's limit. Clients often send large defaults; capping serves them and bounds the cost the same way.
