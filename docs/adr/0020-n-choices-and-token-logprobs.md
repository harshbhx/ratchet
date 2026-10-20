# ADR 0020: `n` choices and token logprobs without moving the calibration or the training set

- Status: Accepted
- Date: 2026-10-20

## Context

Until now a request for `n` > 1 choices was a 400 `unsupported_value`, and every choice carried `logprobs: null` (ADR 0018). Both are common in OpenAI clients: `n` for best-of-n reranking and sampling diversity, `logprobs` and `top_logprobs` for classification scores, highlighting uncertain tokens and calibration on the client side. US-1 promises that only the base URL changes.

A cascade makes both less simple than forwarding a field:

- The student answers only when it is confident. Its confidence is scored on `RATCHET_SAMPLES` (3) samples at temperature 0.7, and each version's threshold was calibrated on exactly that statistic by the golden eval (FR-2.3, FR-5.1). Scoring on more samples would change the statistic the threshold was set for: the best sample's token probability is a maximum over the samples that agree, so it rises with their number.
- Every teacher answer can become a training example, and `examples.seen` counts how often a prompt was asked (the `FREQUENT` rule, ADR 0007). A request whose n choices were all stored would count n times, and the client could pick, from the choices it sees, which one becomes the label.
- Audits, shadows and arbitrations are release signals computed per request. They must mean the same whatever `n` a client sent.
- Answers are cleaned before they are served: reasoning is stripped, and a structured answer loses a fence around it (`tasks.clean_answer`, ADR 0016). Token logprobs from the model cover the text before cleaning, and a server with a reasoning parser lists the reasoning tokens even though its `content` leaves them out. Returning them as they came would show the client text it never receives, reasoning included.

## Decision

- **`n` up to `RATCHET_MAX_N`** (4; 1 to 128, OpenAI's own limit; a bad value stops startup). More is the same 400 as before, naming the variable.
- **The student serves n choices only when it would serve one, and n samples agree.** It draws `max(RATCHET_SAMPLES, n)` samples in its one call. The confidence is scored on the first `RATCHET_SAMPLES` of them, exactly as calibrated (the choices of one call are independent samples, so a prefix is the same draw a one-choice request makes). It serves when that confidence is at or above the version's threshold and at least `n` of all its samples share the scored majority answer (`confidence.majority`). The choices are the best scored sample, which is the sample a one-choice request would serve, then the other agreeing samples by token probability. A confident student that cannot fill `n` choices escalates with a route of its own, `n_choices`, so the dashboard does not count it as unsure; `Ledger.shadow_quality` counts such a request as served in `live_coverage`, because the shadow's verdict uses the one-choice rule.
- **The teacher writes `n` choices** when its answer is served (escalation, cold start, pinned, teacher-only, the fallback teacher). With no client temperature it samples at 0.7 (`providers.CHOICES_TEMPERATURE`, the student's own temperature), since at its default of 0 every choice would be the same answer. A teacher that returns fewer choices than asked gives the client fewer; Ratchet never pads with copies.
- **Only the teacher's choice 0 can become a training example.** One request adds or bumps at most one example, so `seen` keeps counting requests and the client cannot choose the label. Choice 0 is stored under the usual rules: an answer at 0.7 is within `STORED_MAX_TEMPERATURE` (ADR 0017).
- **Audits, arbitrations and the shadow judge choice 0.** An audit or arbitration is one answer at temperature 0; the shadow samples `RATCHET_SAMPLES` as calibrated. Release statistics do not depend on `n`.
- **`usage` adds up every choice** (the prompt once), as OpenAI counts them; `usage_breakdown.served` is the same choices; `cost_usd` prices whole calls; the all-teacher baseline is the teacher writing one answer per choice. A stream sends each choice's chunks in turn, each with its own finish chunk; the `ratchet` block rides on the last one (or on the `include_usage` chunk).
- **Logprobs only when asked, and only tokens that spell the served text.** The provider keeps a choice's `logprobs.content` as it came (`Completion.logprobs_content`, no copy). For a client that sent `logprobs: true`, `providers.served_logprobs` checks each entry, cuts `top_logprobs` to the client's count and returns the tokens of the last place the served text occurs in them, trimming a boundary token only by the whitespace `clean_answer` strips. Any other overlap, an answer that is not one stretch of the tokens, tokens that do not spell it, or a malformed entry gives `logprobs.content: null`. So the tokens a client gets always spell `message.content` exactly, and reasoning tokens never reach it. The teacher gets `logprobs` and `top_logprobs` only when the client sends them (they left `NOT_YET_FORWARDED`); audits and arbitrations never. The student is sampled with logprobs anyway and gets the client's `top_logprobs`, as before.

## Consequences

- PRD FR-1.14 (new); FR-1.12, FR-1.13 and FR-2.1 are extended; `router.ROUTES` gains `n_choices`.
- A request for more choices than `RATCHET_SAMPLES` pays for more student samples, and an escalated one for `n` teacher answers, which the teacher bills: `RATCHET_MAX_N` bounds both (threat model T11). `RATCHET_MAX_N=1` restores one choice per request.
- A request for `n` > 1 escalates more often than the same request for one choice, because n samples must agree. That is the price of returning only answers the student would stand behind.
- A request for `n` > 1 with no temperature now gets varied teacher answers, and its stored choice 0 (also the shadow's reference on that escalation) is a sample at 0.7 rather than the teacher's deterministic answer. The labels that come from such requests are as valid as any client's at that temperature, but slightly noisier than the default path's.
- A reasoning teacher or student whose tokens cannot be told apart from the answer gets `logprobs.content: null` for that choice rather than wrong or leaking tokens; so does an answer with a character split over tokens that do not decode alone (vLLM shows each part as U+FFFD). Clients must treat `content: null` as possible whenever they ask for logprobs.
- Logprobs cost nothing for a client that does not ask: the provider keeps the server's list as it came and nothing walks it.
- The mock student lists its tokens (about 4 characters each, with its constant logprob), so the mock demo shows logprobs; the mock teacher lists none, so teacher-served mock choices have `content: null`. The reference simulation is unchanged.

## Alternatives considered

- **Score the confidence on all `max(RATCHET_SAMPLES, n)` samples.** Simpler, but it is not the statistic the threshold was calibrated on, and the best sample's probability rises with the number of samples, so a request for 4 choices would be served more readily than the same request for one.
- **Return the student's n samples whatever they say.** A client asking for 2 choices would get the minority answer the cascade judged wrong as choice 1. Choices must be answers the student is confident in.
- **Separate calls for each choice.** n round trips to the endpoint instead of one call with `n`; vLLM and Token Factory return n choices in one call.
- **Store every teacher choice.** It would inflate `seen` by n per request, move prompts across the `FREQUENT` line, and let a client choose labels from what it sees.
- **Return the model's logprobs untouched.** They would cover reasoning and fences the client never sees in `content`, and with a reasoning parser they would leak the reasoning itself.
- **Re-tokenise the served text and approximate its logprobs.** No tokenizer is loaded in the gateway, and approximate numbers presented as the model's would mislead; `null` says plainly that they are not available.
