# Lane V: proposed invariants

Invariants lane V proposes for the numbered list in [CLAUDE.md](../../CLAUDE.md#invariants-do-not-break-these). The integrator numbers them after the lanes merge; until then they are not numbered and no test cites them by id. Each entry names its text, the code that enforces it and the tests that prove it.

## Deploy CLI correctness (item W0-DEPLOY-CLI-CORRECTNESS)

### The endpoint script acts only on an endpoint it has identified exactly

**Text.** `deploy/student_endpoint.sh` starts, stops or creates a Nebius endpoint only after it knows which one it means: the id given as `RATCHET_ENDPOINT_ID`, or exactly one endpoint with the name in `nebius ai endpoint list --format json`. Output that is not JSON, two endpoints with the name, an endpoint without a usable id, a failed list, or a name missing from a list that has more pages stops the script (exit 1) before any start, stop or create; only a complete list without the name lets `start` create one. `create` refuses a name that exists. A second endpoint bills a second GPU, and a stop sent to the wrong one leaves the right one billing. The scripts never treat a `DRY_RUN` other than `0` or `1` as a real run.

**Enforcing code.** `deploy/student_endpoint.sh`: `LOOKUP_PY`, `lookup`, `resolve`, `create`, `start`, `stop`, and the `DRY_RUN` check; `deploy/train_job.sh`: the `DRY_RUN` check.

**Proving tests.** `tests/test_live.py::test_endpoint_lookup_never_guesses`, `test_endpoint_stop_and_start_use_the_id`, `test_train_job_uses_job_create` (the `DRY_RUN` refusal).
