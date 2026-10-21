# Lane C2: proposed invariants

Invariants proposed by lane C2 (model routing, risk-aware routing, adjacent OpenAI endpoints) for the integrator to number and add to CLAUDE.md. They are not in CLAUDE.md's numbered list yet, so their numbers cannot collide with other lanes'. The spec calls the first one INV-19; it gets whatever number is free at integration.

## The model field never selects an upstream model (API-MODEL-ROUTING, FR-1.50)

**Text.** The request's `model` field never selects an upstream model. It may name a task (`ratchet/<task>`, `ratchet:<task>`, a model alias in `RATCHET_MODEL_ALIASES`, an Azure deployment), and nothing else reads it: every teacher call sends `Settings.teacher_model` (or the licensed fallback teacher's `RATCHET_TEACHER_FALLBACK_MODEL`, licensed at startup by `licenses.require_distillable`), and every student call sends the model name the student resolver returns (the version's adapter, or `RATCHET_FALLBACK_STUDENT`). The response's `model` is the model that served; the request's is echoed only as `ratchet.requested_model` and, under `RATCHET_MODEL_POLICY=lenient`, `X-Ratchet-Model-Alias`. `model` is in `providers.NEVER_FORWARDED`, so no allowlist can forward it. This keeps INV-1 (only distillable teachers) independent of what a client sends: a request naming `gpt-4o` can never make the gateway call a closed model and offer its answer as training data. See [ADR 0048](../adr/0048-model-field-routing.md).

**Enforcing code.**

- `ratchet.openai_compat:resolve_task` and `ratchet.openai_compat:check_model`: the only readers of `ChatRequest.model`; they return a task name (or refuse the id), never a model.
- `ratchet.router:Ratchet._teacher_complete`: the model sent is `self.s.teacher_model` or `self.fallback_model`, never a parameter of the request.
- `ratchet.router:Ratchet._sample` with the gateway's resolver (`ratchet.gateway:Runtime.reset`): the student model is `version.adapter_uri` or `Settings.fallback_student_model`.
- `ratchet.providers:NEVER_FORWARDED` and `ratchet.providers:GenParams.to_body`: `model` is never forwarded from the request, and an allowlist naming it stops startup.

**Proving tests.**

- `tests/test_model_routing.py::test_teacher_always_gets_the_configured_model` (live mode, a fake teacher and student recording every body: eight different `model` values, a closed model's id and an Azure deployment among them, on the cold-start, student-serving and audit paths)
- `tests/test_openai_compat.py::test_generation_parameters_reach_teacher_student_and_audit` (a request with `model: "gpt-4o"`: the teacher gets `OPEN_TEACHER`, the student `student-v1`)
- `tests/test_core.py::test_gen_params_from_a_validated_request` (a vendor field named `model`, `n` or `messages` in `extra` is never sent)

**For `scripts/reqtrace.py`.** Once numbered (say INV-n), add `"INV-n": ("ratchet.openai_compat:resolve_task", "ratchet.openai_compat:check_model", "ratchet.router:Ratchet._teacher_complete", "ratchet.providers:NEVER_FORWARDED")` to `CODE` and mark `test_teacher_always_gets_the_configured_model` with `@pytest.mark.req("INV-n")` (it is marked `FR-1.50` and `INV-1` now).

## A critical task is served by the student only on measured, gated evidence (TASK-RISK-ROUTING, FR-2.50)

**Text.** A task whose spec declares critical classes (`routing.critical`) is answered by the student only on a version whose per-task record for it, under the task's current spec, carries the golden eval's measurements of those classes (`critical`) and was checked by the release gate: `ReleaseGate.admit` (through `task_failures` and `critical_failure`) for a candidate, and the same check applied in the same eval write by a night's re-score of the live version (`orchestrate.rescore` with `ReleaseGate.critical_holds`). Without such a record (no golden rows of the task yet, a record written before the classes were declared or without them, or the hosted untuned fallback model), the teacher answers (route `task_disabled`, or `confidence` for the untuned model). A class passes only with at least `RATCHET_MIN_TASK_TEST_N` held-out rows whose teacher reference is in it and a miss rate whose 95% Wilson upper bound is at most its `max_miss_rate`. This keeps a costly miss rate from reaching traffic on numbers no gate looked at, and an operator's declared limit from being ignored when the evidence is thin. See [ADR 0049](../adr/0049-risk-aware-routing.md).

**Enforcing code.**

- `ratchet.calibration:critical_measured`, `ratchet.calibration:threshold` and `ratchet.calibration:held_back`: SERVE_NOTHING, and no student call, for a critical task without a measured record.
- `ratchet.router:Ratchet.threshold` and `ratchet.router:Ratchet._held_back`: the hosted untuned model never answers a critical task.
- `ratchet.release:ReleaseGate.critical_failure` (in `task_failures`, so in `admit`) and `ratchet.release:ReleaseGate.critical_holds` (in `ratchet.nightschool.orchestrate:rescore`, the one write of a re-score).
- `ratchet.nightschool.evaluate:cascade_eval`: every eval writes the measurements for a task with critical classes, also with `RATCHET_PER_TASK_CALIBRATION=0`.

**Proving tests.**

- `tests/test_routing.py::test_critical_task_without_measurements_stays_on_teacher` (no record, a record without `critical`, the hosted untuned model, a shadow)
- `tests/test_routing.py::test_critical_miss_rate_disables_task_at_admit` (admit disables, or rejects under `reject`; the router then skips the student)
- `tests/test_routing.py::test_rescore_disables_failing_critical_task` (a re-score disables in the same write)
- `tests/test_routing.py::test_critical_gate_needs_enough_rows_in_the_class` (the row minimum, the Wilson bound at 34 and 35 rows, unmeasured records)

**For `scripts/reqtrace.py`.** Once numbered (say INV-n), add `"INV-n": ("ratchet.calibration:critical_measured", "ratchet.release:ReleaseGate.critical_failure", "ratchet.release:ReleaseGate.critical_holds", "ratchet.nightschool.orchestrate:rescore")` to `CODE` and mark the four tests with `@pytest.mark.req("INV-n")` (they are marked `FR-2.50` now).
