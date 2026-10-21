# Lane C3: proposed invariants

Invariants proposed by lane C3 (free-text similarity, SQL execution comparator) for the integrator to number and add to CLAUDE.md. They are not in CLAUDE.md's numbered list yet, so their numbers cannot collide with other lanes'.

## Proxy precision is labelled, and the judge stays at night (TASK-FREE-TEXT-SIMILARITY, FR-2.55, FR-14.40)

**Text.** Agreement under a `similarity` field is closeness to another answer, not correctness. A precision measured on a task with a similarity field is never reported as exact: its per-task record, the eval (when such a task was scored) and `/ratchet/stats` `tasks[]` say `precision_kind: proxy`. The judge that grades free text runs only in the golden eval, at night (`cascade_eval(judge=...)`), never on the request path: `Ratchet.handle` and the modules it runs through never call or import it. With `RATCHET_MIN_JUDGE_PASS` above 0 a candidate is never admitted on proxy precision alone: an overall judge pass rate below it rejects the candidate, a free-text task below it is disabled or rejects the candidate (`task_mode`), and a free-text task the candidate would answer that no judge graded fails. See [ADR 0054](../adr/0054-free-text-similarity-and-a-judge-gate.md).

**Enforcing code.**

- `ratchet.tasks:Task.precision_kind` and `ratchet.nightschool.evaluate:_task_record`, `cascade_eval`: `proxy` in each similarity task's record and in the eval.
- `ratchet.ledger:_live_calibration` (read by `Ledger.stats_by_task`): `precision_kind` from the record, only a known kind, else null.
- `ratchet.nightschool.evaluate:cascade_eval` (`judge=`) and `LiveEvaluator.evaluate`: the only callers of the judge.
- `ratchet.release:ReleaseGate.admit` with `judge_problem` and `judge_failures`.

**Proving tests.**

- `tests/test_tasks.py::test_mixed_label_and_similarity_fields` (`precision_kind` per task type)
- `tests/test_tasks.py::test_router_and_golden_eval_use_policy_samples_per_task` (`proxy` per task and globally)
- `tests/test_tasks.py::test_stats_report_proxy_precision_and_judge_pass_rate`
- `tests/test_release.py::test_judge_never_on_the_request_path`
- `tests/test_release.py::test_gate_rejects_below_min_judge_pass`
- `tests/test_release.py::test_judge_gate_disables_task`
- `tests/test_release.py::test_mock_judge_catches_what_the_proxy_misses`

**For `scripts/reqtrace.py`.** Once numbered (say INV-n), add `"INV-n": ("ratchet.tasks:Task.precision_kind", "ratchet.nightschool.evaluate:cascade_eval", "ratchet.release:ReleaseGate.judge_problem", "ratchet.release:ReleaseGate.judge_failures")` to `CODE` and mark the tests above with `@pytest.mark.req("INV-n")`.

## A similarity task is scored on two samples or more (TASK-FREE-TEXT-SIMILARITY, FR-2.55)

**Text.** A task with a `similarity` field always says itself, in its spec's `policy.samples`, that its confidence rests on at least 2 student samples, and the router (the shadow too) and the golden eval draw exactly the task's count (more only to fill a client's `n` choices, scored on the task's count). With one sample, agreement is always 1, so a confident score would mean nothing; and a global `RATCHET_SAMPLES=1` cannot take the count away.

**Enforcing code.**

- `ratchet.tasks:samples_problem`, called by `ratchet.tasks:Task.__post_init__` (a `ValueError` for any task built in code) and by `ratchet.taskspec:_build` (a `SpecError` with its path for a tasks file, the admin API and `extends`).
- `ratchet.tasks:Task.sample_count`, read by `ratchet.router:Ratchet.handle` and `ratchet.nightschool.evaluate:cascade_eval`.

**Proving tests.**

- `tests/test_tasks.py::test_similarity_task_requires_two_samples`
- `tests/test_tasks.py::test_router_and_golden_eval_use_policy_samples_per_task`
- `tests/test_live.py::test_live_evaluator_judge_pass_rate_covers_free_text_rows_only`

**For `scripts/reqtrace.py`.** Once numbered (say INV-m), add `"INV-m": ("ratchet.tasks:samples_problem", "ratchet.tasks:Task.sample_count", "ratchet.router:Ratchet.handle", "ratchet.nightschool.evaluate:cascade_eval")` to `CODE` and mark the tests above with `@pytest.mark.req("INV-m")`.
