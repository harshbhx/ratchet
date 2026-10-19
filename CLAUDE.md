# CLAUDE.md: Ratchet

Guidance for Claude (and humans) working in this repo. Read this first; per-folder CLAUDE.md files add detail.

## What this is

Ratchet is an OpenAI-compatible gateway for **license-clean distillation on open models**:

- A small student model (NVIDIA Nemotron 3 Nano 4B + LoRA, vLLM on a Nebius Serverless Endpoint) answers each request first.
- When it is unsure, the request escalates to the teacher (Nemotron 3 Ultra on Nebius Token Factory).
- Every teacher answer is logged with its provenance (teacher + license).
- Every night, Night School fine-tunes a new student, gates it on a golden set, and releases it through shadow → canary 10% → 50% → 100% with automatic rollback.

Built for the **Nebius x NVIDIA Global AI Hackathon** (deadline Oct 30, 2026, 10:30 PM IST; target submission Oct 29). Owner: Harsh Bhanushali (DualMind Labs). The product spec is [docs/PRD.md](docs/PRD.md).

## Commands

```bash
make install        # pip install -e ".[dev]"
make test           # pytest -q (34 tests, ~10 s, no network, no GPU)
make lint           # ruff check + ruff format --check
make fmt            # ruff format + ruff check --fix
make sim            # 10-day simulation with a corrupted deploy (mock models)
make demo           # mock gateway + dashboard on http://localhost:8000
make day1           # live Token Factory checks (needs NEBIUS_API_KEY; costs cents)
```

Always run `make lint test` before committing.

## Repo map

| Path | What |
|---|---|
| `ratchet/gateway.py` | FastAPI app: `/v1/chat/completions`, `/ratchet/stats`, `/ratchet/bom`, mock-only demo endpoints, dashboard |
| `ratchet/router.py` | The cascade (`Ratchet.handle`): student → confidence → teacher, audits, shadow arbitration, cost accounting |
| `ratchet/confidence.py` | Confidence score (logprob + self-consistency) and threshold calibration |
| `ratchet/release.py` | `ReleaseGate`: golden gate, shadow, canary, rollback (Wilson bounds); `ReleasePolicy` |
| `ratchet/ledger.py` | SQLite ledger: requests, examples (provenance), versions, events; migrations; `auto_day` |
| `ratchet/licenses.py` | Teacher license registry; closed-model teachers are refused |
| `ratchet/tasks.py` | Per-task answer comparison (`compare_keys`) |
| `ratchet/providers.py` | OpenAI-compatible HTTP client (`Completion` with token logprobs) |
| `ratchet/nightschool/` | Dataset build, LoRA training script, golden eval, nightly orchestration |
| `ratchet/live.py` | Live Night School on Nebius: `python -m ratchet.live night|tick|reload` |
| `sim/` | Synthetic tickets + mock teacher/student/trainer for demos and tests |
| `deploy/` | Nebius Serverless Job / Endpoint scripts, training Dockerfile |
| `scripts/day1_check.py` | Verifies model IDs, logprobs, JSON mode on Token Factory |
| `docs/` | PRD, architecture, API, config, deployment, runbook, threat model, testing, ADRs, business docs |

## Invariants (do not break these)

1. **Only distillable teachers.** Every teacher goes through `licenses.require_distillable`. Never add a closed model (GPT, Claude, Gemini) as a teacher; their terms forbid training competing models. See [ADR 0001](docs/adr/0001-open-teacher-only.md).
2. **Never train on student outputs.** Training targets are teacher answers only (avoids model collapse).
3. **Golden examples are never trained on**, except requests seen `FREQUENT` (3) or more times, which move to training and leave the golden set. See [ADR 0007](docs/adr/0007-golden-set-split-and-frequent-requests.md).
4. **Champion vs challenger on the same data.** `run_night` re-scores the live version on the current golden set before comparing.
5. **No training while a release is in flight** (shadow or canary).
6. **Precision is measured on held-out data.** `cascade_eval` calibrates on even golden rows and measures on odd rows.
7. **Student failures fall back to the teacher**; teacher failures return 502. Never crash the request path on a student error.
8. **Mock-only endpoints stay mock-only** (`_mock_only()` in `gateway.py`).
9. Model IDs and prices in `config.py` are **unverified until `scripts/day1_check.py` passes** with a real key.

## Conventions

- Python ≥ 3.10, type hints, `from __future__ import annotations`. Ruff, line length 130.
- Plain, short docstrings that say *why*. No hype words in docs or UI copy.
- Tests: pure logic in `tests/test_core.py`; loop behaviour via `sim.simulate.run` in `tests/test_system.py`; regressions in `tests/test_hardening.py`. Assert on trends, not exact simulation numbers. Never call real APIs in tests (use `httpx.MockTransport`).
- Simulation numbers are **mock**. Label them as simulation anywhere they appear (README, docs, video).
- Database changes: add the column to `SCHEMA` **and** to `MIGRATIONS` in `ledger.py`.
- New env var: read it in `config.py` (or `ReleasePolicy.from_env`) and document it in [docs/CONFIGURATION.md](docs/CONFIGURATION.md).
- New endpoint: document it in [docs/API.md](docs/API.md).
- Architectural decision: add an ADR in `docs/adr/` and link it from `docs/adr/README.md`.

## Current status (Oct 19, 2026)

- v0.1.1: loop complete and tested in simulation; live mode written but **not yet run on Nebius**.
- Waiting on Nebius support's reply about Token Factory ToS §5(f) (email sent Oct 19 from harshbhx@gmail.com).
- Next: the checks in [docs/DAY1.md](docs/DAY1.md), then a real live run to replace placeholder numbers in [docs/HACKATHON_SUBMISSION.md](docs/HACKATHON_SUBMISSION.md).
