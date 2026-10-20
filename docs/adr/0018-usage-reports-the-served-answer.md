# ADR 0018: `usage` reports the served answer; Ratchet's own calls go in a breakdown

- Status: Accepted
- Date: 2026-10-20

## Context

Until v0.5 a Ratchet response had no `usage`, no `system_fingerprint`, no `service_tier` and a choice without `logprobs`, `message.refusal` or `message.annotations`, and the stream ignored `stream_options.include_usage`. OpenAI SDKs accept a missing `usage`, but many clients do not: cost dashboards, LangChain's token callbacks, LiteLLM's spend tracking and budget guards read `usage.total_tokens` from every response, and a streaming client that asked for `include_usage` waits for a chunk that never comes. US-1 promises that only the base URL changes.

A cascade makes `usage` ambiguous. One request can make up to five model calls: the student's n samples (one call), the teacher's answer or its audit, a shadow's samples and the teacher's arbitration. Three readings are possible:

1. **Everything**: the sum of every call's tokens.
2. **The served answer**: the tokens of the one answer the client got, as if its model had answered alone.
3. **Teacher-equivalent**: what the request would have used on the teacher alone (the `all_teacher_cost_usd` view).

## Decision

- **`usage` is the served answer's** (reading 2). For the student, the chosen sample's prompt and completion tokens (the provider splits a call's `completion_tokens` over its choices, ADR 0016); for the teacher, its call, with `completion_tokens_details.reasoning_tokens` part of `completion_tokens`, as OpenAI counts them for its reasoning models, and `prompt_tokens_details.cached_tokens` when the endpoint reports them. A count the served model did not report is estimated at about 4 characters a token, so a client never sees an answer with 0 tokens. Ratchet has no audio and no predicted outputs, so those counts are 0.
- **Ratchet's overhead is visible elsewhere.** `ratchet.usage_breakdown` lists every call's tokens as its model reported them (`student_samples`, `served`, `audit`, `shadow`, `arbitration`; `null` for a call that was not made or failed), and `ratchet.cost_usd` already prices all of them. A test checks that `cost_usd` is exactly the price of the calls in the breakdown on 11 paths.
- **`system_fingerprint` names the serving configuration**: `fp_` and 12 hex digits of SHA-256 over `student|adapter|version id|training-set hash|threshold` for the student, `teacher|model` for the teacher. It stays the same within a version and changes when a promotion or rollback puts another version in front, or when a version's threshold changes, which is what a client that caches or compares answers by fingerprint needs to know.
- **Every OpenAI field is present.** `service_tier` is `default`; `choices[0].logprobs` is `null` until token logprobs are passed through (API-N-LOGPROBS; done in [ADR 0020](0020-n-choices-and-token-logprobs.md), which also adds `n` choices, whose `usage` adds up every choice); `message.refusal` is `null` and `message.annotations` is `[]`. Every stream chunk carries the fingerprint, the tier and `logprobs: null`. With `stream_options.include_usage` (read only with `stream: true`, as OpenAI does), every chunk has `usage: null` and a last chunk with `choices: []`, the usage and the `ratchet` block comes before `[DONE]`; without it nothing changes. Responses and chunks are checked against the OpenAI SDK's own `ChatCompletion` and `ChatCompletionChunk` models.
- The router computes all of it from the completions it already holds (`router.Result`): no extra call, no lock, no ledger column.

## Consequences

- PRD FR-1.12 (new); FR-1.1 and FR-1.6 are extended.
- A client billing its own users from `usage` charges for the answer they got, at the size it would have had from that model directly. The same answer costs the same tokens whether or not the request was audited or shadowed, so `usage` does not jump when a release is in flight.
- `usage` is not Ratchet's bill. Sum `ratchet.usage_breakdown`, or read `ratchet.cost_usd`, for that; API.md says so next to the response.
- A student-served `usage` is far smaller than a teacher-served one (no reasoning tokens), so a client's token dashboard shows the student taking over. That is the product working, but it is a per-token view, not a GPU-hours one (NFR-11).
- A custom provider that sets `Completion.cached_tokens` and `reasoning_tokens` gets them reported; one that does not reports 0 for both.

## Alternatives considered

- **Everything (reading 1).** Honest about upstream spend, but the same answer would cost three to five times more tokens on a request that happened to be audited or shadowed, and a student-served answer would report n samples for one answer. Clients would see Ratchet as more expensive per token than the model it replaces, and `total_tokens` would not be reproducible. The breakdown gives the same numbers without that.
- **Teacher-equivalent (reading 3).** Matches what the client would have paid before Ratchet, but it would report reasoning tokens that were never generated for a student answer, and it is already in `ratchet.all_teacher_cost_usd`.
- **A fingerprint per process or per deploy.** Changes on every restart and says nothing about which adapter answered; one per version tracks the thing that changes answers.
- **A fixed or empty `system_fingerprint`.** The SDK accepts `null`, but clients that log it to detect backend changes would never see a promotion.
