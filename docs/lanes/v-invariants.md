# Lane V: proposed invariants

Invariants lane V proposes for the numbered list in [CLAUDE.md](../../CLAUDE.md#invariants-do-not-break-these). The integrator numbers them after the lanes merge; until then they are not numbered and no test cites them by id. Each entry names its text, the code that enforces it and the tests that prove it.

## Deploy CLI correctness (item W0-DEPLOY-CLI-CORRECTNESS)

### The endpoint script acts only on an endpoint it has identified exactly

**Text.** `deploy/student_endpoint.sh` starts, stops or creates a Nebius endpoint only after it knows which one it means: the id given as `RATCHET_ENDPOINT_ID`, or exactly one endpoint with the name in `nebius ai endpoint list --format json`. Output that is not JSON, two endpoints with the name, an endpoint without a usable id, a failed list, or a name missing from a list that has more pages stops the script (exit 1) before any start, stop or create; only a complete list without the name lets `start` create one. `create` refuses a name that exists. A second endpoint bills a second GPU, and a stop sent to the wrong one leaves the right one billing. The scripts never treat a `DRY_RUN` other than `0` or `1` as a real run.

**Enforcing code.** `deploy/student_endpoint.sh`: `LOOKUP_PY`, `lookup`, `resolve`, `create`, `start`, `stop`, and the `DRY_RUN` check; `deploy/train_job.sh`: the `DRY_RUN` check.

**Proving tests.** `tests/test_live.py::test_endpoint_lookup_never_guesses`, `test_endpoint_stop_and_start_use_the_id`, `test_train_job_uses_job_create` (the `DRY_RUN` refusal).

## Train template parity (item W13-TRAIN-TEMPLATE-PARITY)

### The student trains on the prompt it is served

**Text.** Every row of the training file carries the chat-template arguments the student endpoint serves with (`{"enable_thinking": false}`): `dataset.TRAIN_TEMPLATE_KWARGS`, `train.TEMPLATE_KWARGS` and `DEFAULT_TEMPLATE_KWARGS` in `deploy/student_endpoint.sh` are one value, changed together. The training target is the completion only (the teacher's stored answer and its end-of-turn token; never `assistant_only_loss`). `train.py` never trains a row whose prompt, rendered with the generation prompt, is not a prefix of prompt + answer, or differs from the prompt rendered for the request with the endpoint's arguments; it stops the job instead. A train row whose answer holds a literal `<think>` or `</think>` is left out of training (and of the training commitment) and counted. TRL only warns on a mismatch, so without this a quietly worse adapter would ship.

**Enforcing code.** `ratchet/nightschool/dataset.py`: `TRAIN_TEMPLATE_KWARGS`, `THINK_TAGS`, `rows` (the think-tag rule), `training_record`, `write_training` (the one writer of the training file, used by `live.NebiusTrainer` and `dataset.build`); `ratchet/nightschool/train.py`: `TEMPLATE_KWARGS`, `check_rows`, `check_prefix`, `columns`, the `SFTConfig` in `main` (`completion_only_loss=True`); `deploy/student_endpoint.sh`: `DEFAULT_TEMPLATE_KWARGS`.

**Proving tests.** `tests/test_nightschool.py::test_training_rows_carry_reasoning_off`, `test_training_prompt_is_a_prefix_and_matches_serving` (with the vendored template `tests/fixtures/nano4b_chat_template.jinja`), `test_train_args_have_no_assistant_only_loss`, `test_train_refuses_rows_that_would_train_another_prompt`; `tests/test_live.py::test_train_parse_args_defaults_and_row_contract`.

### The training image installs exact versions and never ignores a failed kernel build

**Text.** Every package `deploy/Dockerfile.train` installs carries `==`, and the set equals `deploy/train-versions.txt`, which every pip step after torch reads as constraints. No step ends in `|| true`, and the image's last build step imports the Mamba kernels, so an image without them never reaches a GPU. `train.py` records the versions it ran with (`versions.json`, `train.LIBRARIES` = the pinned packages) beside the adapter; they are never written into a stored BOM (INV-14).

**Enforcing code.** `deploy/Dockerfile.train`, `deploy/train-versions.txt`, `ratchet/nightschool/train.py`: `LIBRARIES`, `versions`.

**Proving tests.** `tests/test_nightschool.py::test_dockerfile_is_pinned`, `test_train_records_library_versions`.
