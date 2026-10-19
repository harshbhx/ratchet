# Contributing to Ratchet

Thanks for helping. Ratchet is small on purpose; keep changes focused.

## Setup

```bash
git clone <repo> && cd ratchet
make install
make lint test      # must pass before every commit
make sim            # see the whole loop run with mock models
make demo           # dashboard at http://localhost:8000
```

No API keys or GPUs are needed for development. Live mode needs a Nebius Token Factory key; see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Ground rules

Read [CLAUDE.md](CLAUDE.md) first. The invariants there are non-negotiable, especially:

- Only teachers whose licenses allow distillation (`ratchet/licenses.py`).
- Never train on the student's own outputs.
- Every bug fix gets a regression test in `tests/test_hardening.py`.
- Behaviour changes update the matching doc in `docs/` in the same PR.

## Pull requests

1. Branch from `main`.
2. Keep the PR to one change. Describe what and why.
3. Fill in the PR template checklist.
4. CI runs ruff, pytest on Python 3.10–3.13, a simulation smoke test, and a Docker build.

## Commit messages

Imperative mood, short subject (`Add per-task audit rate`), body explaining why when it isn't obvious.

## Reporting issues

Use the issue templates. Never paste API keys or customer data. Security problems: see [SECURITY.md](SECURITY.md).
