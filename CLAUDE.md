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
make test           # pytest -q (305 tests, ~35 s, no network, no GPU)
make lint           # ruff check + ruff format --check + python scripts/reqtrace.py --check
make fmt            # ruff format + ruff check --fix
make trace          # regenerate docs/REQUIREMENTS.md after changing PRD rows, invariants or test markers
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
python -m ratchet.bom keygen                                                             # BOM signing key + public key
python -m ratchet.bom verify bom.json --public-key <key>                                 # check an adapter's BOM
pytest -q --cov=ratchet --cov=sim                                                        # coverage, as in CI
python scripts/regen_sim_reference.py [--check]                                          # pinned reference simulation fixture
```

Always run `make lint test` before committing.

## Repo map

| Path | What |
|---|---|
| `ratchet/gateway.py` | FastAPI app: `/v1/chat/completions` (API keys, request validation, per-key quota, streaming; OpenAI error envelope on `/v1/*`, body limit), `/healthz`, `/ratchet/stats`, `/ratchet/bom`, `/ratchet/versions/{id}/bom`, `/ratchet/redact`, mock-only demo endpoints, pages (`/`, `/scout`, `/dashboard`), Scout endpoints (`/scout/*`) |
| `ratchet/router.py` | The cascade (`Ratchet.handle`): pinned tasks → teacher; otherwise student → confidence → teacher, audits, shadow arbitration, cost accounting; upstream failure counts (`Ratchet.health()`) |
| `ratchet/confidence.py` | Confidence score (logprob + self-consistency) and threshold calibration |
| `ratchet/release.py` | `ReleaseGate`: golden gate, shadow, canary, rollback (Wilson bounds); `ReleasePolicy` |
| `ratchet/ledger.py` | SQLite ledger: requests (with API client), examples (provenance, redaction count), versions (with BOM), events; migrations; `auto_day`; `stats_by_task`, `stats_by_client`, `privacy_stats`; one re-entrant lock |
| `ratchet/licenses.py` | Teacher license registry; closed-model teachers are refused |
| `ratchet/redact.py` | PII redaction (`[EMAIL]`, `[CARD]`, …) applied before examples are stored |
| `ratchet/auth.py` | `KeyRing`: named gateway API keys from `RATCHET_API_KEYS` |
| `ratchet/openai_compat.py` | OpenAI request contract (standard library only): `validate_chat_request` → `ChatRequest`, `normalize_messages` (developer → system, text parts → string), `APIError` and the error envelope, `Limits`. See [ADR 0012](docs/adr/0012-openai-request-validation-and-error-envelope.md) |
| `ratchet/bom.py` | Per-adapter bill of materials, Ed25519 signing and verification, `python -m ratchet.bom` |
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
| `scripts/regen_sim_reference.py` | Regenerates (or `--check`s) `tests/fixtures/sim_reference_seed7.json`, the pinned reference simulation that `test_reference_simulation_unchanged` compares exactly |
| `scripts/reqtrace.py` | Requirements traceability: PRD rows and invariants → code (`CODE`) and `@pytest.mark.req` tests; writes and checks `docs/REQUIREMENTS.md` (generated, never edit by hand) |
| `tests/conftest.py`, `tests/fakes.py`, `tests/sdk_bridge.py` | Shared fixtures (`gw`, `gw_env`, `day1_check`, `reference_run`), test doubles (`RecordingProvider`, `RecordingTrainer`, `RecordingEvaluator`, `FakeClock`, ledger builders) and `make_openai_client(app)` (the real OpenAI SDK against the gateway in-process). See [tests/CLAUDE.md](tests/CLAUDE.md) |
| `docs/` | PRD, the generated requirements matrix (`REQUIREMENTS.md`), architecture, API, config, Scout, deployment (incl. the demo VM), runbook, threat model, testing, ADRs, business docs |

## Invariants (do not break these)

1. **Only distillable teachers.** Every teacher goes through `licenses.require_distillable`. Never add a closed model (GPT, Claude, Gemini) as a teacher; their terms forbid training competing models. See [ADR 0001](docs/adr/0001-open-teacher-only.md).
2. **Never train on student outputs.** Training targets are teacher answers only (avoids model collapse).
3. **Golden examples are never trained on**, except requests seen `FREQUENT` (3) or more times, which move to training and leave the golden set. See [ADR 0007](docs/adr/0007-golden-set-split-and-frequent-requests.md).
4. **Champion vs challenger on the same data.** `run_night` re-scores the live version on the current golden set before comparing.
5. **No training while a release is in flight** (shadow or canary).
6. **Precision is measured on held-out data.** `cascade_eval` calibrates on even golden rows and measures on odd rows.
7. **Student failures fall back to the teacher**; a teacher failure where its answer is needed (escalation, cold start, pinned) returns 502. Never crash the request path on a student error.
8. **Mock-only endpoints stay mock-only** (`_mock_only()` in `gateway.py`).
9. Model IDs and prices in `config.py` are **unverified until `scripts/day1_check.py` passes** with a real key.
10. **Pinned tasks never become training examples.** `Ratchet._pinned` must not call `add_example`, and pinned tasks never reach the student. See [docs/SCOUT.md](docs/SCOUT.md).
11. **The demo budget guard must stay on in live mode.** Every Scout run goes through `Guard.admit` before it starts and `Guard.add_spend` after it ends (also on failure). Never pass `daily_budget_usd=None` or skip `_scout_precheck` in live mode. See [docs/DEPLOY_NEBIUS_VM.md](docs/DEPLOY_NEBIUS_VM.md#switching-from-mock-to-live).
12. **Redact before storing.** Everything passed to `Ledger.add_example` goes through `redact_messages` / `redact` first when `Settings.redact_pii` is on (the default). Never store raw request text anywhere else (`requests` holds metadata only). See [ADR 0009](docs/adr/0009-pattern-based-pii-redaction.md).
13. **API keys are never stored or logged.** The ledger and `/ratchet/stats` see the key's *name* only; compare keys with `KeyRing.identify` (constant-time digests). `/healthz` stays public.
14. **A BOM is written once, at training time,** from the exact train and golden rows (`run_night` → `bom.manifest`). Never rewrite a stored BOM; a signature covers the canonical JSON (`bom.canonical`), so any change must produce a new document. See [ADR 0010](docs/adr/0010-signed-adapter-bom.md).
15. **Optional teacher calls (audit, shadow arbitration) never fail a request the student already answered.** Neither do the shadow's own samples. A failure is counted (`Ratchet.health()`), logged as a throttled `audit_error` or `shadow_error` event naming the exception class (never its message), and the student's answer is served; a failed audit is not an audit, and an unarbitrated shadow disagreement counts against the shadow. A teacher failure on the escalation path still returns an upstream error. See [ADR 0013](docs/adr/0013-optional-teacher-calls-fail-open.md).

## Conventions

- Python ≥ 3.10, type hints, `from __future__ import annotations`. Ruff, line length 130.
- Plain, short docstrings that say *why*. No hype words in docs or UI copy.
- Tests: pure logic in `tests/test_core.py`; Night School rules (INV-1 to INV-6, incl. the `add_example` allowlist) in `tests/test_nightschool.py`; the release gate, shadow arbitration and the canary split in `tests/test_release.py`; loop behaviour via `sim.simulate.run` in `tests/test_system.py`; regressions in `tests/test_hardening.py`; Scout, the research world and the guard in `tests/test_scout.py`; what clients, the dashboard and the license rules rely on (the gateway contract, the cascade's sampling, confidence, threshold, audit and cost semantics, the `/ratchet/stats` keys in `tests/fixtures/stats_keys.json`, which only grow) in `tests/test_contract.py`; OpenAI request validation, normalisation, the error envelope and the limits (incl. the real OpenAI SDK) in `tests/test_openai_compat.py`; upstream failures around a served request (audit, arbitration, shadow samples) in `tests/test_resilience.py`; the traceability check in `tests/test_traceability.py`; live mode, the CLIs and the deploy scripts, run offline through default-argument seams (`transport=`, `transports=`, `client=`, `run=`), in `tests/test_live.py`. Shared fixtures are in `tests/conftest.py`, fakes and ledger builders in `tests/fakes.py`. Assert on trends, not exact simulation numbers; the one exception is `test_reference_simulation_unchanged`, which pins the reference run exactly (regenerate it only with `scripts/regen_sim_reference.py` and a CHANGELOG line). Never call real APIs in tests (use `httpx.MockTransport` or `RecordingProvider`).
- A new call to `Ledger.add_example` under `ratchet/` fails `test_add_example_called_only_from_allowed_callers` until it is added to `ALLOWED_ADD_EXAMPLE_CALLERS` in a reviewed change (INV-2, INV-10).
- Behaviour change: update the PRD row and mark the proving test with `@pytest.mark.req("FR-a.b")`, then run `make trace`. A new requirement or invariant needs a `CODE` entry in `scripts/reqtrace.py`; ids never change (INV-n is the list number). Statuses follow the grammar in [PRD §6](docs/PRD.md#6-functional-requirements). `KNOWN_GAPS` only shrinks: remove an id when its test lands. See [docs/TESTING.md](docs/TESTING.md#traceability).
- Simulation numbers are **mock**. Label them as simulation anywhere they appear (README, docs, video).
- Database changes: add the column to `SCHEMA` **and** to `MIGRATIONS` in `ledger.py`.
- New env var: read it in `config.py` (or `ReleasePolicy.from_env`) and document it in [docs/CONFIGURATION.md](docs/CONFIGURATION.md).
- New endpoint: document it in [docs/API.md](docs/API.md).
- Architectural decision: add an ADR in `docs/adr/` and link it from `docs/adr/README.md`.

## Current status (Oct 20, 2026)

- Unreleased: requirements traceability. Every PRD row and invariant maps to code and tests in the generated [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md), checked by `make lint` and CI; done requirements without a test are listed there as known gaps (none left).
- Unreleased: the live entry points are tested offline (`tests/test_live.py`): `ratchet.live`, the gateway's live `Runtime`, `LiveEvaluator`, `train.py`'s arguments, the Scout and simulator CLIs, the mock warm-up, `RATCHET_TRUST_PROXY`, the day-1 check and the deploy scripts. The student's sampling is one constant, `confidence.STUDENT_SAMPLING`.
- Unreleased: a teacher failure during an audit or a shadow arbitration no longer fails a request the student answered (INV-15, FR-2.10); failures are counted in `Ratchet.health()` and logged as throttled events, and an unarbitrated shadow disagreement counts against the candidate ([ADR 0013](docs/adr/0013-optional-teacher-calls-fail-open.md)).
- Unreleased: strict `/v1/chat/completions` validation and normalisation (FR-1.10): client mistakes are 400s in OpenAI's error envelope, never 500s, and cost nothing; body, message and input limits; the developer role and text content parts work ([ADR 0012](docs/adr/0012-openai-request-validation-and-error-envelope.md)).
- Unreleased: a test safety net before refactoring: INV-1 to INV-6, the Night School skip and retrain rules, every release-gate rule, shadow arbitration and the canary split are tested directly, and a pinned reference simulation must stay byte-identical. Contract tests pin the gateway, the cascade's semantics, the `/ratchet/stats` keys and the license rules before the router and gateway rewrites; known bugs there are strict xfails naming the item that fixes them.
- v0.4.0: PII redaction before training, gateway API keys with per-key quotas, streaming, a signed bill of materials per adapter, `/healthz`, coverage in CI, and the dashboard's Trust and privacy panel; 87% line coverage at release.
- v0.3.0: React web app (story landing page, Scout, dashboard) replaces the hand-written pages, which stay at `/lite*`.
- v0.2.0: loop complete and tested in simulation; Scout, landing page and the Nebius VM deploy kit added ([CHANGELOG.md](CHANGELOG.md)). Live mode, live Scout (Tavily) and the VM kit are written but **not yet run on Nebius**.
- Waiting on Nebius support's reply about Token Factory ToS §5(f) (email sent Oct 19 from harshbhx@gmail.com).
- Next: the checks in [docs/DAY1.md](docs/DAY1.md), then a real live run to replace placeholder numbers in [docs/HACKATHON_SUBMISSION.md](docs/HACKATHON_SUBMISSION.md), then the public demo VM ([docs/DEPLOY_NEBIUS_VM.md](docs/DEPLOY_NEBIUS_VM.md)).
