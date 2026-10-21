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

## Model-written SQL runs only in the sandbox (TASK-SQL-EXECUTION, FR-2.56)

**Text.** SQL from a model answer (student or teacher, which a client's prompt can steer) is run only by `sqlcheck.Sandbox`, and only on the operator's fixture: a script inside `RATCHET_FIXTURES_DIR`, loaded into a private in-memory SQLite database. Never on a customer database, a file or any other connection. Every query runs with `PRAGMA query_only = ON`, the read-only authorizer (SQLITE_SELECT, SQLITE_READ, SQLITE_RECURSIVE and the deterministic functions in `sqlcheck.FUNCTIONS`; SQLITE_DENY for everything else), the progress handler (`max_steps`, `QUERY_SECONDS`, `QUERY_MEMORY_BYTES`) and the size limits (`Connection.setlimit`: values and LIKE patterns; `max_rows`, `RESULT_BYTES`), and with the built-ins SQLite runs in quadratic time inside one step replaced by linear versions (`_GUARDED`) or denied; a sqlite fixture is refused where a value's size cannot be capped (Python 3.10). An answer that is not one query, fails, is denied or hits a limit is invalid, with a fixed reason that never quotes the answer or SQLite's message, so it gets no vote and is never stored (INV-16). See [ADR 0055](../adr/0055-sql-tasks-compared-by-execution.md).

**Enforcing code.**

- `ratchet.sqlcheck:Sandbox` (`__init__`, `_load`, `run`, `_execute`), `ratchet.sqlcheck:_query_authorizer`, `ratchet.sqlcheck:_load_authorizer`, `ratchet.sqlcheck:_Budget`, `ratchet.sqlcheck:_guarded` and `_GUARDED`, `ratchet.sqlcheck:CAN_LIMIT`.
- `ratchet.sqlcheck:read_fixture` and `ratchet.sqlcheck:resolve_fixture` (inside the directory, at most 5 MB, a regular file, no symlink), called by `ratchet.taskspec:_sql` through `SqlSpec.load`.
- `ratchet.sqlcheck:parse_query` (one statement starting with SELECT, WITH or VALUES) and `ratchet.sqlcheck:SqlSpec.key`, the only path from `ratchet.tasks:Task.key` to SQL.

**Proving tests.**

- `tests/test_sqlcheck.py::test_sql_write_and_attach_denied_and_fixture_unchanged`
- `tests/test_sqlcheck.py::test_sql_pragma_denied`
- `tests/test_sqlcheck.py::test_sql_random_function_denied`
- `tests/test_sqlcheck.py::test_sql_runaway_recursive_cte_interrupted_quickly`
- `tests/test_sqlcheck.py::test_sql_rows_result_size_and_value_limits`
- `tests/test_sqlcheck.py::test_sql_value_limit`
- `tests/test_sqlcheck.py::test_sql_memory_guard`
- `tests/test_sqlcheck.py::test_sql_quadratic_builtins_replaced_by_linear_ones`
- `tests/test_sqlcheck.py::test_fixture_path_outside_dir_rejected`
- `tests/test_sqlcheck.py::test_sql_execution_needs_python_311`
- `tests/test_sqlcheck.py::test_sql_task_in_the_cascade`

**For `scripts/reqtrace.py`.** Once numbered (say INV-p), add `"INV-p": ("ratchet/sqlcheck.py", "ratchet.sqlcheck:Sandbox", "ratchet.sqlcheck:_query_authorizer", "ratchet.sqlcheck:read_fixture", "ratchet.sqlcheck:SqlSpec.key")` to `CODE` and mark the tests above with `@pytest.mark.req("INV-p")`.
