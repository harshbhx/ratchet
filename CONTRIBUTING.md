# Contributing to Ratchet

Thanks for helping. Ratchet is small on purpose; keep changes focused.

## Setup

```bash
git clone https://github.com/harshbhx/ratchet.git && cd ratchet
make install
make lint test reference   # must pass before every commit (lint includes the traceability check)
make sim            # see the whole loop run with mock models
make demo           # http://localhost:8000: / landing, /scout, /dashboard
```

No API keys or GPUs are needed for development. Live mode needs a Nebius Token Factory key; see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Ground rules

Read [CLAUDE.md](CLAUDE.md) first. The invariants there are non-negotiable, especially:

- Only teachers whose licenses allow distillation (`ratchet/licenses.py`).
- Never train on the student's own outputs.
- Every bug fix gets a regression test in `tests/test_hardening.py`.
- Behaviour changes update the matching doc in `docs/` in the same PR, including the PRD row, and the test that proves it carries `@pytest.mark.req("FR-a.b")`. Run `make trace` and commit `docs/REQUIREMENTS.md` ([docs/TESTING.md](docs/TESTING.md#traceability)).

## Pull requests

1. Branch from `main`.
2. Keep the PR to one change. Describe what and why.
3. Fill in the PR template checklist.
4. CI runs ruff, pytest on Python 3.10–3.13, the requirements traceability check, the startup config check, the reference workloads, a simulation smoke test, a web job (type check, `npm test`, build, and a check that the committed build matches), a docs job, a Docker build, the evidence, link and secret checks. A pull request runs only the checks for the areas it changed; `main` runs all of them ([docs/TESTING.md](docs/TESTING.md#what-ci-runs)). The `ci-ok` check sums them up.

## Commit messages

Imperative mood, short subject (`Add per-task audit rate`), body explaining why when it isn't obvious.

## Reporting issues

Use the issue templates. Never paste API keys or customer data. Security problems: see [SECURITY.md](SECURITY.md).
