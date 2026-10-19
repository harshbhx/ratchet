# CLAUDE.md: Ratchet

Guidance for Claude (and humans) working in this repo. Read this first; per-folder CLAUDE.md files add detail.

## What this is

Ratchet is an OpenAI-compatible gateway for **license-clean distillation on open models**:

- A small student model (NVIDIA Nemotron 3 Nano 4B + LoRA, vLLM on a Nebius Serverless Endpoint) answers each request first.
- When it is unsure, the request escalates to the teacher (Nemotron 3 Ultra on Nebius Token Factory).
- Every teacher answer is logged with its provenance (teacher + license).
- Every night, Night School fine-tunes a new student, gates it on a golden set, and releases it through shadow → canary 10% → 50% → 100% with automatic rollback.
- **Scout** ([docs/SCOUT.md](docs/SCOUT.md)) is a multi-agent research demo on top: its triage and reader calls are distilled; its planner and writer calls are pinned to the teacher.

Built for the **Nebius x NVIDIA Global AI Hackathon** (deadline Oct 30, 2026, 10:30 PM IST; target submission Oct 29). Owner: Harsh Bhanushali (DualMind Labs). The product spec is [docs/PRD.md](docs/PRD.md).

## Commands

```bash
make install        # pip install -e ".[dev]"
make test           # pytest -q (49 tests, ~10 s, no network, no GPU)
make lint           # ruff check + ruff format --check
make fmt            # ruff format + ruff check --fix
make sim            # 10-day simulation with a corrupted deploy (mock models)
make demo           # mock gateway (4-day warm-up) on http://localhost:8000: / story, /scout, /dashboard
make web            # rebuild the React UI into ratchet/web/dist (commit the result)
make web-dev        # UI hot reload on :5173 against the gateway on :8000
make day1           # live Token Factory checks (needs NEBIUS_API_KEY; costs cents)
make docker         # build the gateway image (ratchet-gateway)
make clean          # remove caches, build output, sim_results.json, ratchet.db
```

No Make targets for these (run them directly):

```bash
RATCHET_MODE=mock RATCHET_MOCK_WARMUP_DAYS=4 uvicorn ratchet.gateway:app --port 8000   # demo with a trained student
python -m ratchet.scout "What is LoRA fine-tuning?" --base-url http://localhost:8000/v1   # Scout CLI
python -m sim.simulate --workload research --days 8 --requests 1500                     # or --workload mixed
DRY_RUN=1 bash deploy/vm/create_vm.sh                                                    # demo VM (prints commands only)
```

Always run `make lint test` before committing.

## Repo map

| Path | What |
|---|---|
| `ratchet/gateway.py` | FastAPI app: `/v1/chat/completions`, `/ratchet/stats`, `/ratchet/bom`, mock-only demo endpoints, pages (`/`, `/scout`, `/dashboard`), Scout endpoints (`/scout/*`) |
| `ratchet/router.py` | The cascade (`Ratchet.handle`): pinned tasks → teacher; otherwise student → confidence → teacher, audits, shadow arbitration, cost accounting |
| `ratchet/confidence.py` | Confidence score (logprob + self-consistency) and threshold calibration |
| `ratchet/release.py` | `ReleaseGate`: golden gate, shadow, canary, rollback (Wilson bounds); `ReleasePolicy` |
| `ratchet/ledger.py` | SQLite ledger: requests, examples (provenance), versions, events; migrations; `auto_day`; `stats_by_task`; one re-entrant lock |
| `ratchet/licenses.py` | Teacher license registry; closed-model teachers are refused |
| `ratchet/tasks.py` | Task registry: answer comparison (`compare_keys`) and `pinned` |
| `ratchet/providers.py` | OpenAI-compatible HTTP client (`Completion` with token logprobs) |
| `ratchet/nightschool/` | Dataset build, LoRA training script, golden eval, nightly orchestration |
| `ratchet/live.py` | Live Night School on Nebius: `python -m ratchet.live night|tick|reload` |
| `ratchet/scout/` | Scout: agents, prompts (shared with `sim/`), Tavily and mock search, budget guard, CLI |
| `web/` | React app (Vite, Tailwind, motion, recharts) for `/`, `/scout`, `/dashboard`. See [web/CLAUDE.md](web/CLAUDE.md) and [web/DESIGN.md](web/DESIGN.md) |
| `ratchet/web/dist/` | The committed build of `web/`, served by the gateway |
| `ratchet/web/*.html`, `ratchet/dashboard.html` | Lite pages without a build step, served at `/lite*` and as a fallback |
| `sim/` | Synthetic tickets, mock research world (`research.py`), workloads (`world.py`), mock teacher/student/trainer for demos and tests |
| `deploy/` | Nebius Serverless Job / Endpoint scripts, training Dockerfile |
| `deploy/vm/` | Public demo on one Nebius CPU VM: `create_vm.sh`, cloud-init, Docker Compose + Caddy, update and backup scripts |
| `scripts/day1_check.py` | Verifies model IDs, logprobs, JSON mode on Token Factory |
| `docs/` | PRD, architecture, API, config, Scout, deployment (incl. the demo VM), runbook, threat model, testing, ADRs, business docs |

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
10. **Pinned tasks never become training examples.** `Ratchet._pinned` must not call `add_example`, and pinned tasks never reach the student. See [docs/SCOUT.md](docs/SCOUT.md).
11. **The demo budget guard must stay on in live mode.** Every Scout run goes through `Guard.admit` before it starts and `Guard.add_spend` after it ends (also on failure). Never pass `daily_budget_usd=None` or skip `_scout_precheck` in live mode. See [docs/DEPLOY_NEBIUS_VM.md](docs/DEPLOY_NEBIUS_VM.md#switching-from-mock-to-live).

## Conventions

- Python ≥ 3.10, type hints, `from __future__ import annotations`. Ruff, line length 130.
- Plain, short docstrings that say *why*. No hype words in docs or UI copy.
- Tests: pure logic in `tests/test_core.py`; loop behaviour via `sim.simulate.run` in `tests/test_system.py`; regressions in `tests/test_hardening.py`; Scout, the research world and the guard in `tests/test_scout.py`. Assert on trends, not exact simulation numbers. Never call real APIs in tests (use `httpx.MockTransport`).
- Simulation numbers are **mock**. Label them as simulation anywhere they appear (README, docs, video).
- Database changes: add the column to `SCHEMA` **and** to `MIGRATIONS` in `ledger.py`.
- New env var: read it in `config.py` (or `ReleasePolicy.from_env`) and document it in [docs/CONFIGURATION.md](docs/CONFIGURATION.md).
- New endpoint: document it in [docs/API.md](docs/API.md).
- Architectural decision: add an ADR in `docs/adr/` and link it from `docs/adr/README.md`.

## Current status (Oct 20, 2026)

- v0.3.0: React web app (story landing page, Scout, dashboard) replaces the hand-written pages, which stay at `/lite*`.
- v0.2.0: loop complete and tested in simulation; Scout, landing page and the Nebius VM deploy kit added ([CHANGELOG.md](CHANGELOG.md)). Live mode, live Scout (Tavily) and the VM kit are written but **not yet run on Nebius**.
- Waiting on Nebius support's reply about Token Factory ToS §5(f) (email sent Oct 19 from harshbhx@gmail.com).
- Next: the checks in [docs/DAY1.md](docs/DAY1.md), then a real live run to replace placeholder numbers in [docs/HACKATHON_SUBMISSION.md](docs/HACKATHON_SUBMISSION.md), then the public demo VM ([docs/DEPLOY_NEBIUS_VM.md](docs/DEPLOY_NEBIUS_VM.md)).
