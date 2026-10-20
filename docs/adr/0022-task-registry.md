# ADR 0022: A task registry: specs as data, operator-only pinning, an unknown-task policy and admin keys

- Status: Accepted
- Date: 2026-10-20

## Context

Until v0.5 the tasks lived in a Python dict (`tasks.REGISTRY`). Adding one meant editing `ratchet/tasks.py` and redeploying, so an operator could not define a task without a code change, and nothing checked a spec beyond what `Task` and `FieldSpec` raise on their first error. Three gaps came with that:

- **Every unknown name was distilled.** `tasks.get` gave any valid name a fresh task without fields, and its teacher answers became training data. A typo in a header, or a client inventing names, put free-text examples in the training set that no comparator could grade.
- **Pinning was enforced only on the way in.** `Ratchet._pinned` never stored an example, but `dataset.rows` and `cascade_eval` never checked `pinned`. A task pinned after it had data kept training the student and calibrating the threshold on answers the student would never serve.
- **A spec change went unnoticed.** A version's threshold was calibrated under the comparators of the night it was trained. Loosening a tolerance or changing a vocabulary left that threshold in force, measured under other rules.

Management endpoints also need authentication that is not a client's API key: the API keys name billing clients, and a client must not be able to change what the gateway does.

## Decision

- **Specs are data.** `ratchet/taskspec.py` parses a tasks file (`RATCHET_TASKS_FILE`, JSON, or YAML when PyYAML is installed) or an admin API body into `tasks.Task`. Fields are written as `{path: {type, ...options}}`. Unknown keys and options (including an option the field's type does not use) are refused, every value is type- and range-checked, and `SpecError.errors` lists every problem with its path (`tasks[2].fields.total.abs_tol: must be a number >= 0`). `FieldSpec` and `Task` check the result again, so the parser cannot admit what they refuse. A bad tasks file raises at startup (the gateway and `python -m ratchet.live`), with all errors in the message. Paths are `$` or dot-separated `[A-Za-z0-9_]` names.
- **`extends` copies at definition time.** A tasks file's spec may extend a task of the same file (any order) or a built-in task, never an API task, because startup reads the file before it registers the stored API tasks (`POST /ratchet/tasks/validate` checks a document the same way, so a document that validates starts); an API spec may extend any registered task: `fields`, `policy` and `match` merge by key (null removes an entry), other keys replace, `name` and `description` are not inherited. The resolved spec is what is registered and stored, so a later change to the base does not reach the copy.
- **One registry, three sources.** `tasks.REGISTRY` starts with `BUILTIN` (the tasks in code); `taskspec.install` registers the tasks file's (which may replace a built-in task) and then the API tasks stored in the ledger's new `tasks` table. The admin API may replace only its own tasks (and later auto tasks): `PUT` answers 403 for a built-in or file task, so a stored API task whose name the file defines later is skipped at startup with a `task_skipped` event. Auto and adhoc tasks never shadow a registered name. `install` builds the whole registry aside and publishes it in one assignment (`tasks.replace_all`), so a request during a mock demo reset never sees it without the file's tasks, and a file that went bad on disk leaves the registry as it was. At most 500 tasks (`MAX_TASKS`), which a tasks file must fit in with the built-in tasks it does not replace; 50 API tasks in mock mode, where a reset clears them. Writers take a lock and publish a new dict in one assignment, so `tasks.get` on the request path reads without a lock.
- **Admin keys.** `RATCHET_ADMIN_KEYS` uses the `RATCHET_API_KEYS` syntax (`name:key`, at least 16 characters, compared as digests in constant time) and must not share a key with it (startup error). `gateway._admin` (Bearer or `X-API-Key`) returns the admin's name: 401 for a missing or wrong key, 403 for an API key; with no admin keys, 403 in live mode (`admin API disabled: set RATCHET_ADMIN_KEYS`) and the name `mock-admin` in mock mode. Only the name is stored (`tasks.updated_by`) and logged (`task_registered`, `task_changed`, `task_deleted`), as invariant 13 requires. This is the primitive later management endpoints use; it is not the Caddy edge guard's `RATCHET_ADMIN_TOKEN`.
- **Operator-only pinning.** `pinned` is read only from built-in, file and API specs. No header, metadata key, `model` value, `response_format` or `x-ratchet` annotation sets it, and `Task` refuses `pinned` for an auto or adhoc task.
- **Pinning enforced at read time.** `dataset.rows` and `cascade_eval` skip the rows of a task that is pinned now and count them (`excluded_pinned` in the manifest and the eval). The ledger keeps the rows, so unpinning brings them back.
- **The unknown-task policy.** `RATCHET_UNKNOWN_TASKS` is `distill` (the default, v0.4's behaviour), `teacher` (the teacher answers through `Ratchet._teacher_only`, route `unknown_task`: no student call, nothing stored) or `reject` (400 `unknown_task` naming the registered tasks the caller may use). Under `teacher` and `reject` an `X-Ratchet-Task` header that is not a valid name is a 400 as well; under `distill` it still means `default`, as in v0.4. `_pinned` became `_teacher_only(..., route)`, shared by pinned tasks, teacher-only requests and this policy. `tasks.get` itself stays policy-free, because Night School needs a comparator for every stored name.
- **A client allow-list per task.** A task's `clients` names the API keys that may use it; any other key, and a request without a key, gets 403 `task_not_allowed` before the quota is touched. Names are checked against `RATCHET_API_KEYS` when a spec is loaded or written.
- **Spec changes send a task to the teacher.** `run_night` records `eval['spec_hashes']` (each golden task's `spec_hash`) in the champion's re-score and the candidate's eval. `Ratchet.threshold(version, model, task)` is 1.01 (serve nothing) for a task whose recorded hash differs from the current one, until a night scores the version under the new spec. A night without enough new data to train still re-scores the live version when its recorded hashes are stale (event `rescored`; a failure is a `rescore_failed` event and the night stays a skip night), so a low-traffic deployment does not wait for 50 new examples. `GET /ratchet/tasks` shows such a task as `recalibrating`.
- **What the hash covers.** `spec_hash` stays the hash of `Task.spec()` (format, fields, pinned, store, tools, policy, routing; ADR 0021). `description`, `clients` and `match` decide who uses a task and how a request finds it, not how answers compare, so editing them never forces recalibration.
- **Fixture files** that a spec names (for the reserved `sql` format) must resolve inside `RATCHET_FIXTURES_DIR` (`taskspec.resolve_fixture`), so a spec cannot make the gateway read another file.

## Consequences

- Operators add, change and remove tasks without a release: in a file under version control, or over the admin API with an audit trail in the event log. PRD FR-2.15 (new); FR-1.3 and FR-2.9 extended; invariant 10 now covers reads.
- The pinned reference simulation is unchanged: the mock world uses only built-in tasks, nothing is pinned after it has data, and specs never change during a run.
- `/ratchet/stats` is unchanged. Request rows gain the route `unknown_task`.
- With `teacher` or `reject`, rows stored earlier under unknown names (while the policy was `distill`) stay trainable; register or pin those names to change that.
- A task with `clients` refuses every request when `RATCHET_API_KEYS` is off, because there is no key name to check (fail closed).
- The registry is process state. One gateway process owns it (ADR 0006); `python -m ratchet.live` builds its own from the same file and ledger. A second gateway process would not see another's API writes until it restarts.
- Spec errors and admin calls use `{"detail": ...}` like the other `/ratchet/*` routes; a 422 adds `errors`.

## Alternatives considered

- **Keep tasks in code.** Simple and reviewed, but every customer task would be a fork or a release, and nothing would check a spec as a whole.
- **Let the API replace built-in and file tasks.** One more source of truth for the same name; a restart would silently pick one. The file stays the place to change a file task, and a spec may `extend` a built-in one under a new name.
- **Pinning from a request header.** Lets a client keep its traffic off the student, but also lets any client turn distillation off for a task others share, and makes stored data depend on who sent it. Pinning stays an operator decision.
- **Hash everything but the name.** Would send a task back to the teacher whenever an operator edits its description or allow-list, for no change in how answers compare.
- **Reject unknown tasks by default.** Safer, but breaks every v0.4 client that relies on ad hoc names. `distill` stays the default; `teacher` and `reject` are one variable away.
- **Re-score only on training nights.** Simpler, but a spec change on a quiet deployment would keep the task on the teacher until 50 new examples arrive.
