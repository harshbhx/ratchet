# ADR 0016: Store only complete, valid teacher answers

- Status: Accepted
- Date: 2026-10-20

## Context

Every teacher answer Ratchet sees (an escalation, a cold start, an audit, a shadow arbitration) became a training example. Before this change the only cleaning was removing complete `<think>…</think>` blocks. A code reading found four ways bad targets got in:

- **A lone `</think>`.** Some chat templates open the reasoning in the prompt, so the model's output starts inside it and has only the closing tag. The whole reasoning was stored as part of the answer, and served to the client.
- **An unterminated `<think>`.** An answer cut off by the token limit while reasoning kept its reasoning. For a structured task, `parse_json` then found a JSON object inside the reasoning and counted it as the answer.
- **Truncation.** The provider ignored `finish_reason`, so an answer cut off by the token limit was stored like any other. The student would learn to stop mid-answer.
- **Format.** A teacher answer that a structured task cannot parse (prose for `ticket-triage`, a code fence around the JSON) was stored too. The student learned to write fences, and a prose answer in the golden set is a reference no student answer can ever agree with, which lowers every candidate's measured precision.

The same answers also served as references: an audit compared the student with a truncated teacher answer and counted a disagreement against the student, and a shadow was judged against it.

## Decision

- **One cleaning function.** `tasks.strip_reasoning` removes complete blocks (any case), then everything up to the last remaining `</think>`, then an opening `<think>` still left with everything after it. `tasks.clean_answer(task, text)` adds, for structured tasks only, unwrapping one code fence that is the whole answer when its body parses as JSON; free-text answers keep fences, because a code block is content there. `Task.canonical` and everything that compares, stores or serves goes through it, so the served text and the stored target are the same string. The provider never reads `reasoning_content` or `reasoning`, the fields where vLLM's and Token Factory's reasoning parsers put the reasoning.
- **One storing function.** `Ratchet._store_example(task, messages, completion)` is the only caller of `Ledger.add_example` (the INV-2 allowlist names it). It stores an answer only when `router.answer_problem` finds nothing: `finish_reason` is `stop` or `tool_calls`, the cleaned answer is not empty, and a structured task's answer is valid JSON. Redaction runs after that, and a structured answer that redaction breaks (a card-like number outside quotes becomes `[CARD]`) is not stored either. Invariant 16.
- **Serve anyway.** A skipped answer is still the response: the client asked, and a truncated answer is all there is. Storing is about training data, not about the response.
- **Not a reference.** A teacher answer `answer_problem` rejects is not an audit (`audited=0`, counted neither for nor against the student) and settles no shadow disagreement (`shadow_agree` NULL, which the gate already counts against the shadow, [ADR 0013](0013-optional-teacher-calls-fail-open.md)).
- **Visible, not noisy.** Each skip writes a `skip_example` event naming the task and the reason, on the 1st, 10th, 100th and 1000th skip of each reason (per process, under the router's counter lock), never the answer. `requests.stored` records per request whether an example was inserted or a duplicate's `seen` bumped, so an operator can compare escalations with stored examples.
- **The student side matches.** A student sample cut off by the token limit or a content filter gets no vote in `confidence.score`, an empty answer is invalid for every task, and a sample set with no valid answer is never served, whatever the threshold.
- **Only string operations.** No model call, no schema library; the check costs microseconds on the request path.

## Consequences

- Invariant 16; PRD FR-2.13 (new) and FR-2.7 (extended).
- With the mock models nothing changes: their answers are bare JSON that always end with `stop`, so the pinned reference simulation is identical.
- A teacher that often runs out of tokens, or ignores a structured task's format, now produces fewer examples, and Night School may skip nights for lack of data. That is the intent; the `skip_example` events and `requests.stored` show why, and the RUNBOOK's "Teacher answers not stored" playbook says what to change. `scripts/day1_check.py` prints each model's `finish_reason`.
- An endpoint that reports another word for a natural stop (for example `eos_token`) would store nothing. Token Factory and vLLM report `stop`; the day-1 check confirms it before a live run.
- Answers stored before v0.5 are not cleaned again; no live ledger exists yet.
- `finish_reason` and `reasoning_tokens` are now on every `Completion`, ready for the response parity and cost items. The response's own `finish_reason` is still always `stop`.

## Alternatives considered

- **Store everything, filter at dataset build.** Keeps the raw answers for later, but the golden set, audits and shadow judgments read the ledger directly, and a filter in two places drifts. Training data is the product; refusing it at the door is simpler to reason about.
- **Repair invalid answers** (ask the teacher again, or extract the JSON object from prose). Costs another teacher call or guesses at what the teacher meant; a repair loop belongs with structured outputs, where the schema says what valid means.
- **Strip fences for every task.** Simpler, but a free-text answer that is a code block would lose its formatting in training data and in the response.
- **Treat a cut-off student sample as a normal vote.** It may parse, but its text was cut; letting it vote would let a truncated answer be served with high confidence.
