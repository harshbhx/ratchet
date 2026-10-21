# Lane C: proposed invariants

Invariants proposed by lane C (task model and use cases) for the integrator to number and add to CLAUDE.md. They are not in CLAUDE.md's numbered list yet, so their numbers cannot collide with other lanes'.

## Per-task held-out evidence (TASK-PER-TASK-CALIBRATION, FR-14.1)

**Text.** When several tasks share one adapter, the student answers a task only at a threshold backed by held-out evidence for that task under its current spec. A task that has a per-task record on the serving version is never served by the student without held-out rows (threshold 1.01), nor when its spec changed since the version was scored (1.01 until a night re-scores it), nor when the release gate disabled it on that version; a task the gate disabled stays disabled for that version's life, through every re-score, and its requests never get a student answer, not even in degraded mode. A task with no record (no golden row of it, or a version scored before per-task calibration or with `RATCHET_PER_TASK_CALIBRATION=0`) uses the version's global threshold. See [ADR 0040](../adr/0040-per-task-calibration.md).

**Enforcing code.**

- `ratchet.nightschool.evaluate:_task_record`: 1.01 for a task with no held-out row (and a record with no rows for a task whose golden references are all invalid).
- `ratchet.calibration:threshold`, called by `ratchet.router:Ratchet.threshold` on every request: the task's record under its current `spec_hash`, 1.01 for a stale hash, a disabled record or an unreadable threshold.
- `ratchet.release:ReleaseGate.admit` and `task_failures`: disable (`Ledger.merge_task_eval`, threshold 1.01) or reject on a task's own evidence.
- `ratchet.nightschool.orchestrate:rescore` with `ratchet.calibration:keep_disabled`: a re-score never re-enables a disabled task.
- `ratchet.calibration:held_back`, read by `ratchet.router:Ratchet.handle`: a record that serves nothing means no student call (route `task_disabled`), so degraded mode has no student answer to serve.

**Proving tests** (`tests/test_calibration.py`).

- `test_task_without_test_rows_is_teacher_only`
- `test_stale_spec_hash_threshold_ignored`
- `test_threshold_reads_malformed_records_safely`
- `test_admit_disables_regressed_task_but_ships_version`
- `test_disabled_task_survives_rescore`
- `test_versions_without_task_eval_unchanged`
- `test_disabled_task_skips_the_student`

**For `scripts/reqtrace.py`.** Once numbered (say INV-n), add `"INV-n": ("ratchet.calibration:threshold", "ratchet.nightschool.evaluate:cascade_eval", "ratchet.release:ReleaseGate.admit", "ratchet.nightschool.orchestrate:rescore", "ratchet.calibration:held_back")` to `CODE` and mark the tests above with `@pytest.mark.req("INV-n")`.

## Structured answers meet their request's contract (API-STRUCTURED-OUTPUTS, FR-1.40, FR-14.2)

**Text.** An answer to a request with a `response_format` counts only when it meets it (`json_object`: one JSON object; `json_schema`: JSON that matches the schema), everywhere an answer is judged: a student sample that breaks it gets no vote, a teacher answer that breaks it is never stored, never an audit and never a shadow reference, and Night School holds every stored row to the `response_format` stored with it, exactly as the router held the request. A schema repair is a teacher answer to the original request: the example stores the request's own messages, never the repair conversation. `x-ratchet` annotations never reach a model or the ledger, and the `tasks` table never stores an auto task whose derived spec holds personal data. An auto task judges only requests whose own schema derives its fields, so no client's schema sets the fields another client's answers are compared on. See [ADR 0042](../adr/0042-auto-tasks-from-response-format-schemas.md).

**Enforcing code.**

- `ratchet.tasks:Task.bound` and `ratchet.tasks:Task._key` (through `ratchet.schema_tasks:Contract.problem`): a bound task's `check`, `key`, `agree` and `valid` refuse an answer that breaks the contract, so `ratchet.confidence:score`, `ratchet.router:answer_problem` and `ratchet.router:Ratchet._store_example` apply it unchanged.
- `ratchet.router:Ratchet.resolve`, called first by `Ratchet.handle`, `Ratchet.teacher_only` and `Ratchet.task_for`: every request with a `response_format` is judged under a bound task.
- `ratchet.tasks:for_row`, called by `ratchet.nightschool.dataset:rows` and `ratchet.nightschool.evaluate:cascade_eval`.
- `ratchet.router:Ratchet._repair` with `ratchet.schema_tasks:repair_messages`: `_call_teacher` stores the repaired answer under the request's original `messages`.
- `ratchet.schema_tasks:forwardable`, applied in `Ratchet.handle` and `Ratchet.teacher_only` before `plan`; `ratchet.router:Ratchet._auto_task` (the `redact_json` check before `Ledger.save_task`).
- `ratchet.router:Ratchet._derives`, checked by `Ratchet.resolve` on every registered auto task it would use.

**Proving tests** (`tests/test_structured.py`).

- `test_invalid_student_samples_get_no_vote_and_escalate`
- `test_a_teacher_answer_invalid_twice_is_served_but_never_stored`
- `test_a_repaired_teacher_answer_is_served_and_stored_with_the_original_messages`
- `test_auto_tasks_come_back_at_startup_and_night_school_grades_under_the_schema`
- `test_annotations_never_reach_a_model_or_the_ledger`
- `test_an_auto_task_whose_schema_holds_personal_data_is_not_stored`
- `test_a_registered_task_beats_the_schema`
- `test_a_client_cannot_choose_the_fields_another_clients_answers_are_compared_on`
- `test_an_auto_name_held_by_other_fields_stores_nothing`

**For `scripts/reqtrace.py`.** Once numbered (say INV-n), add `"INV-n": ("ratchet.tasks:Task.bound", "ratchet.router:Ratchet.resolve", "ratchet.tasks:for_row", "ratchet.router:Ratchet._repair", "ratchet.schema_tasks:forwardable")` to `CODE` and mark the tests above with `@pytest.mark.req("INV-n")`.

## Tool-call and multimodal requests are teacher-only (API-TOOL-PASSTHROUGH, FR-1.41)

**Text.** Tool-call and multimodal requests are teacher-only and never stored unless their task opts in (`Task.tools = distill`, honoured only once tool-call distillation is built); the client's `model` field never changes that. A tool request is one that offers tools (`tools` or the legacy `functions`) or whose messages carry tool calls or tool results; a multimodal request is one with a content part only the teacher reads (`RATCHET_TEACHER_MODALITIES`). Neither reaches the student, an audit or the shadow, and neither calls `Ledger.add_example`; a teacher tool call anywhere else (a request without tools) is refused, never served or stored. Invariants 1, 2 and 10 hold: the model sent is always the configured teacher, nothing a student wrote is stored, and a request never pins a task. See [ADR 0043](../adr/0043-tool-calls-teacher-only-by-default.md).

**Enforcing code.**

- `ratchet.router:uses_tools` and `ratchet.openai_compat:tool_turns`: what a tool request is (`params.tools`, where the gateway maps `functions`, or tool turns in the messages).
- `ratchet.router:Ratchet.handle`: a tool request goes to `_teacher_only` (route `tools`) before any version is picked; `ratchet.router:Ratchet.teacher_only` and the gateway's `_complete` send a multimodal request there too (route `multimodal`).
- `ratchet.router:Ratchet._teacher_only`: never calls `_store_example`, never samples the student, runs no audit or shadow (INV-10's code).
- `ratchet.router:Ratchet._ask_teacher`: a tool call is an answer only with `tool_calls=True`, which only `_teacher_only` passes; anywhere else it raises `UpstreamError` 502 `tool_calls_unsupported`.
- `ratchet.providers:DEFAULT_STUDENT_PARAMS`: the student is never sent tools.

**Proving tests** (`tests/test_tools.py`).

- `test_a_tool_request_is_answered_by_the_teacher_alone_and_never_stored`
- `test_tool_history_without_tools_is_teacher_only`
- `test_tool_routes_keep_the_operators_reason_first`
- `test_a_tool_request_gets_no_audit_and_no_shadow`
- `test_tool_calls_pass_through_the_gateway` (the client's `model` is `gpt-4o`; the teacher is still the configured one)
- `test_multimodal_requests_are_teacher_only_with_their_own_route`
- `test_the_openai_sdk_runs_a_tool_loop_against_the_mock_gateway`
- In `tests/test_openai_compat.py`: `test_finish_reason_is_tool_calls_only_with_tool_calls` (a teacher tool call to a request without tools is a 502 with nothing stored).

**For `scripts/reqtrace.py`.** Once numbered (say INV-n), add `"INV-n": ("ratchet.router:uses_tools", "ratchet.router:Ratchet.handle", "ratchet.router:Ratchet.teacher_only", "ratchet.router:Ratchet._teacher_only", "ratchet.router:Ratchet._ask_teacher")` to `CODE` and mark the tests above with `@pytest.mark.req("INV-n")`.
