# Lane C2: proposed invariants

Invariants proposed by lane C2 (model routing, risk-aware routing, adjacent OpenAI endpoints) for the integrator to number and add to CLAUDE.md. They are not in CLAUDE.md's numbered list yet, so their numbers cannot collide with other lanes'. The spec calls the first one INV-19; it gets whatever number is free at integration.

## The model field never selects an upstream model (API-MODEL-ROUTING, FR-1.50)

**Text.** The request's `model` field never selects an upstream model. It may name a task (`ratchet/<task>`, `ratchet:<task>`, a model alias in `RATCHET_MODEL_ALIASES`, an Azure deployment), and nothing else reads it: every teacher call sends `Settings.teacher_model` (or the licensed fallback teacher's `RATCHET_TEACHER_FALLBACK_MODEL`, licensed at startup by `licenses.require_distillable`), and every student call sends the model name the student resolver returns (the version's adapter, or `RATCHET_FALLBACK_STUDENT`). The response's `model` is the model that served; the request's is echoed only as `ratchet.requested_model` and, under `RATCHET_MODEL_POLICY=lenient`, `X-Ratchet-Model-Alias`. `model` is in `providers.NEVER_FORWARDED`, so no allowlist can forward it. This keeps INV-1 (only distillable teachers) independent of what a client sends: a request naming `gpt-4o` can never make the gateway call a closed model and offer its answer as training data. The same holds on the other OpenAI endpoints (API-ADJACENT-ENDPOINTS, FR-1.51): `/v1/completions` and `/v1/responses` become a `ChatRequest` and run the same chat path, and `/v1/embeddings` always sends `Settings.embeddings_model`, whatever `model` says (`check_embeddings_model` only admits or refuses the id), and stores nothing. See [ADR 0048](../adr/0048-model-field-routing.md) and [ADR 0050](../adr/0050-adjacent-openai-endpoints.md).

**Enforcing code.**

- `ratchet.openai_compat:resolve_task` and `ratchet.openai_compat:check_model`: the only readers of `ChatRequest.model`; they return a task name (or refuse the id), never a model.
- `ratchet.router:Ratchet._teacher_complete`: the model sent is `self.s.teacher_model` or `self.fallback_model`, never a parameter of the request.
- `ratchet.router:Ratchet._sample` with the gateway's resolver (`ratchet.gateway:Runtime.reset`): the student model is `version.adapter_uri` or `Settings.fallback_student_model`.
- `ratchet.providers:NEVER_FORWARDED` and `ratchet.providers:GenParams.to_body`: `model` is never forwarded from the request, and an allowlist naming it stops startup.
- `ratchet.gateway:_embed` and `ratchet.openai_compat:check_embeddings_model`: the embeddings call names `rt.settings.embeddings_model`; the request's `model` is only checked against `RATCHET_MODEL_POLICY`.

**Proving tests.**

- `tests/test_model_routing.py::test_teacher_always_gets_the_configured_model` (live mode, a fake teacher and student recording every body: eight different `model` values, a closed model's id and an Azure deployment among them, on the cold-start, student-serving and audit paths)
- `tests/test_openai_compat.py::test_generation_parameters_reach_teacher_student_and_audit` (a request with `model: "gpt-4o"`: the teacher gets `OPEN_TEACHER`, the student `student-v1`)
- `tests/test_core.py::test_gen_params_from_a_validated_request` (a vendor field named `model`, `n` or `messages` in `extra` is never sent)
- `tests/test_adjacent_endpoints.py::test_live_embeddings_forward_the_configured_model` (live mode: `model: text-embedding-3-large` reaches the upstream as `RATCHET_EMBEDDINGS_MODEL`, with no `user` or `encoding_format`) and `test_completions_with_the_openai_sdk_and_legacy_logprobs` (a completion's teacher call names the configured teacher)

**For `scripts/reqtrace.py`.** Once numbered (say INV-n), add `"INV-n": ("ratchet.openai_compat:resolve_task", "ratchet.openai_compat:check_model", "ratchet.router:Ratchet._teacher_complete", "ratchet.providers:NEVER_FORWARDED")` to `CODE` and mark `test_teacher_always_gets_the_configured_model` with `@pytest.mark.req("INV-n")` (it is marked `FR-1.50` and `INV-1` now).

## A critical task is served by the student only on measured, gated evidence (TASK-RISK-ROUTING, FR-2.50)

**Text.** A task whose spec declares critical classes (`routing.critical`) is answered by the student only on a version whose per-task record for it, under the task's current spec, carries the golden eval's measurements of those classes (`critical`) and was checked by the release gate: `ReleaseGate.admit` (through `task_failures` and `critical_failure`) for a candidate, and the same check applied in the same eval write by a night's re-score of the live version (`orchestrate.rescore` with `ReleaseGate.critical_holds`). Without such a record (no golden rows of the task yet, a record written before the classes were declared or without them, or the hosted untuned fallback model), the teacher answers (route `task_disabled`, without a student call, also when the resolver picks the untuned model). A class passes only with at least `RATCHET_MIN_TASK_TEST_N` held-out rows whose teacher reference is in it and a miss rate whose 95% Wilson upper bound is at most its `max_miss_rate`. This keeps a costly miss rate from reaching traffic on numbers no gate looked at, and an operator's declared limit from being ignored when the evidence is thin. See [ADR 0049](../adr/0049-risk-aware-routing.md).

**Enforcing code.**

- `ratchet.calibration:critical_measured`, `ratchet.calibration:threshold` and `ratchet.calibration:held_back`: SERVE_NOTHING, and no student call, for a critical task without a measured record.
- `ratchet.router:Ratchet.threshold` and `ratchet.router:Ratchet._held_back`: the hosted untuned model never answers a critical task, and is not sampled for it.
- `ratchet.release:ReleaseGate.critical_failure` (in `task_failures`, so in `admit`) and `ratchet.release:ReleaseGate.critical_holds` (in `ratchet.nightschool.orchestrate:rescore`, the one write of a re-score).
- `ratchet.nightschool.evaluate:cascade_eval`: every eval writes the measurements for a task with critical classes, also with `RATCHET_PER_TASK_CALIBRATION=0`.

**Proving tests.**

- `tests/test_routing.py::test_critical_task_without_measurements_stays_on_teacher` (no record, a record without `critical`, the hosted untuned model, a shadow)
- `tests/test_routing.py::test_critical_miss_rate_disables_task_at_admit` (admit disables, or rejects under `reject`; the router then skips the student)
- `tests/test_routing.py::test_rescore_disables_failing_critical_task` (a re-score disables in the same write)
- `tests/test_routing.py::test_critical_gate_needs_enough_rows_in_the_class` (the row minimum, the Wilson bound at 34 and 35 rows, unmeasured records)

**For `scripts/reqtrace.py`.** Once numbered (say INV-n), add `"INV-n": ("ratchet.calibration:critical_measured", "ratchet.release:ReleaseGate.critical_failure", "ratchet.release:ReleaseGate.critical_holds", "ratchet.nightschool.orchestrate:rescore")` to `CODE` and mark the four tests with `@pytest.mark.req("INV-n")` (they are marked `FR-2.50` now).

## Notes for the integrator

- **The ledger time machine's `label_p` (lane F, FR-16.5).** Since TASK-RISK-ROUTING a task's spec can set its own audit rate (`policy.audit_rate`, read through `ratchet.router:Ratchet.audit_rate(task)`). Wherever lane F logs a served request's labelling probability from the audit rate, it must use `self.audit_rate(task)`, not `self.s.audit_rate`, or the replay's Hajek weights are wrong for such a task. The version-level release statistics (`Ledger.version_live_quality`) pool audited rows without weights, so they weight tasks by volume times audit rate (ADR 0049).
- **The exact stored threshold (lane F, FR-16.3).** Lane F stores the global threshold exactly instead of rounded to 4 decimals. A per-task record stores `round(thr, 4)` in `ratchet.nightschool.evaluate:_task_record` (the local `stored`), and the critical misses are counted at that stored value; at the merge, set `stored = thr` there so records store the exact threshold too, and precision and coverage (measured at `thr`) then match what the router applies.
- **The other OpenAI endpoints (API-ADJACENT-ENDPOINTS, FR-1.51) and the chat handler.** `gateway._complete` is now `validate_chat_request` → `_serve` → `_chat_response`, and `/v1/completions` and `/v1/responses` call `_serve` too. Another lane's edit to the old `_complete` belongs in `_serve` when it is about the request (admission, budgets, idempotency, the request id) and in `_chat_response` when it is about the chat body, so the three endpoints stay alike:
  - Lane F's `X-Ratchet-Request-Id` and `ratchet.request_id` (FR-15.5): set them in `_serve` (from `Result.request_id`); in `_embed`, use `Ledger.log_request`'s return value. Lane F's receipts classify an embeddings row (served_by `upstream`, route `passthrough`) as `measured`, with its own cost as its baseline, so no savings figure moves.
  - Lane D's spend limits and budgets: the spec counts embeddings toward them, so call the same admission check in `_embed` (after `_quota`) as in `_serve`. Each embeddings row carries `client`, `cost_usd` and `teacher_cost_usd`.
  - Lane D2's idempotency: keyed per endpoint, since the same body means different things on `/v1/completions` and `/v1/chat/completions`.
  - Lane C's tool calls: `_respond` already turns a choice's `tool_calls` (read with `getattr`) into `function_call` items; once `Choice` carries them, `/v1/responses` returns them with no other change. `_completion_response` streams the buffered answer with `_stream_pieces`; if lane C's real teacher streaming replaces that in `_chat_response`, completions keep the buffered path until adapted.
  - The task name `embeddings` is reserved (`tasks.RESERVED_NAMES`): a lane adding a built-in task must not use it.
  - `_legacy_complete` turns any `APIError` or HTTP error raised for a batch's prompt after the first into `_batch_stopped` (`X-Should-Retry: false`, no `Retry-After`, the answered prompts named), and starts no prompt once the batch has run for `RATCHET_TEACHER_DEADLINE_S`. A lane whose admission check in `_serve` raises (lane D's spend limits, lane D2's readiness) gets this for free; an error type of its own that is neither must be added to the `except` there, or an SDK resends the batch and stores its answered prompts again (INV-3).
  - Lane E's success metrics and lane F's receipts: `Ledger.stats_by_day`, `totals` and `stats_by_client` leave out route `passthrough` rows (`ledger.NOT_PASSTHROUGH`); a new headline query over `requests` should do the same, while spend (the cost report, `stats_by_task`) keeps them.
