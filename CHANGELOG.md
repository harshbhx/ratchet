# Changelog

All notable changes are listed here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [SemVer](https://semver.org/).

## [Unreleased]

### Added
- **Requirements traceability** (PRD NFR-10, [ADR 0011](docs/adr/0011-requirements-traceability.md)): `scripts/reqtrace.py` (standard library only) maps every PRD requirement and every CLAUDE.md invariant to its code (`CODE`) and to the tests marked `@pytest.mark.req(...)`, and writes the generated matrix [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md). `python scripts/reqtrace.py --check` fails on a status outside the grammar, a duplicate or misplaced id, a done requirement, story or invariant without a test, exemption or known gap, a marker citing an unknown or replaced id, a planned id or unmet metric without a strict xfail, an unresolvable code reference, an `untested (live)` row without a live check, a stale matrix, an undocumented route or environment variable, or a wrong "N tests" claim. It runs in `make lint`, as a CI step and as `tests/test_traceability.py`. `make trace` regenerates the matrix.
- `@pytest.mark.req` markers on 55 of the 60 existing test functions (the other five are migration, `auto_day` and policy regressions with no PRD row); the `req` marker is registered and `--strict-markers` is on.
- `KNOWN_GAPS`: the 30 done requirements and invariants that have no test yet (for example shadow mode, the canary split, the Night School skip rules, champion vs challenger, mock-only endpoints). It may only shrink.
- 44 tests in `tests/test_traceability.py` (108 in total), including one that checks the AST test count against `pytest --collect-only`.
- **Test safety net for Night School and the release gate**, before any refactor (no production code changed). `tests/test_nightschool.py` (13 tests) proves INV-1 to INV-6 directly: training targets are the teacher's answer on every storing path (audit, arbitration, escalation) even when a confident student serves a marker answer; golden rows never reach the trainer except requests seen `FREQUENT` times; closed-teacher rows are dropped at dataset build; the five skip reasons; retraining from the base model on cumulative data; `cascade_eval` grading on the held-out half; the champion re-scored on the same golden set. `tests/test_release.py` (9 tests) covers the coverage floor, the tolerance boundary, retiring older candidates, shadow promotion and both rejections, a canary holding without strong evidence, rollback to the teacher at cold start, shadow arbitration and its cost, and the canary traffic split (4,000 seeded picks inside the 99% Wilson interval).
- INV-2 structural guard: an AST scan of `ratchet/` fails on any use of `add_example` outside `ALLOWED_ADD_EXAMPLE_CALLERS` (today only `Ratchet._call_teacher`), including bound methods and `getattr(..., "add_example")`, and on a listed caller that no longer uses it.
- `tests/conftest.py` (the `gw` and `gw_env` gateway fixtures, moved from `test_scout.py` and `test_hardening.py`, and the session-scoped `reference_run`) and `tests/fakes.py` (`RecordingProvider`, `RecordingTrainer`, `RecordingEvaluator`, `FakeClock`, and the ledger builders `_version`, `_audit`, `_shadow`, `_examples`, `_repeat`).
- **Pinned reference simulation**: `tests/fixtures/sim_reference_seed7.json` records the inputs, daily numbers (4 decimal places), release events and final versions of `make sim` (seed 7, 10 days × 2,000, drift day 5, corrupted v3; mock models), and `test_reference_simulation_unchanged` compares a fresh run exactly. It is the one exception to "assert on trends": regenerate it only with `python scripts/regen_sim_reference.py` (new; `--check` compares without writing) and a CHANGELOG line. Created in this release from the current behaviour, which is unchanged since v0.1.

### Changed
- PRD: a status grammar in §6; a Status column for §7; ids and statuses for the §8.1 metrics (SM-1 to SM-5, all `Not checked` until a test computes them); NFR-5 describes the v0.4 API keys and quotas; NFR-10 (traceability); empty sections 6.12 Operations, 6.13 Data governance and 6.14 Use cases; FR-1.4 is `Replaced by FR-1.6`; FR-1.7's Caddy note moved into its text; `untested live` became `untested (live)` (and `(on GPU)`, `(on Nebius)`); FR-8.4 and FR-8.5 are marked `untested` because they have never run against Nebius; §8.1 is renamed "Product metrics".
- CONFIGURATION.md: an explicit `RATCHET_<FIELD>` row for every `ReleasePolicy` field, and `RATCHET_API_KEY` (Scout CLI).
- Test counts corrected in CLAUDE.md, tests/CLAUDE.md, TESTING.md, README, DEPLOYMENT.md (it still said 49) and the Devpost draft.
- `KNOWN_GAPS` shrinks from 30 to 15: FR-2.8, FR-4.1, FR-4.2, FR-4.4, FR-5.3, FR-5.4, FR-6.3, FR-6.4, FR-6.8, NFR-3 and INV-2 to INV-6 now have tests. 131 tests in total; the suite takes about 25 s (the reference simulation is about 7 s of it).
- `test_core.py` uses the shared ledger builders from `tests/fakes.py` instead of its own `_version` / `_audit`; the guard tests use `FakeClock`. Assertions are unchanged.
- Docs: TESTING.md (the new test tables, the pinned reference simulation), tests/CLAUDE.md (fixtures, fakes, the reference-fixture rule), and CLAUDE.md, ratchet/CLAUDE.md, ratchet/nightschool/CLAUDE.md and sim/CLAUDE.md (the `add_example` allowlist and the reference fixture).

## [0.4.0] - 2026-10-20

The offline items of the hardening milestone (PRD FR-1.6, FR-1.7, FR-3.6, FR-4.7).

### Added
- **PII redaction before training** (`ratchet/redact.py`, [ADR 0009](docs/adr/0009-pattern-based-pii-redaction.md)): emails, phone numbers, card numbers (Luhn), IBANs (mod-97), Aadhaar (Verhoeff), US SSNs, Indian PANs, IPv4 addresses and API secrets become `[LABEL]` placeholders in the request and the teacher's answer before `add_example`. The teacher still sees the original. On by default (`RATCHET_REDACT_PII`).
- **Gateway API keys** (`ratchet/auth.py`): `RATCHET_API_KEYS=name:key,...` requires `Authorization: Bearer` (or `X-API-Key`) on `/v1/*` (401 otherwise). The key name is logged per request (`requests.client`); `RATCHET_API_KEY_DAILY_REQUESTS` caps each key per UTC day (429, `X-RateLimit-*` headers).
- **Streaming**: `stream: true` returns OpenAI `chat.completion.chunk` events, a final chunk with the `ratchet` block, and `[DONE]`.
- **Signed bill of materials per adapter** (`ratchet/bom.py`, [ADR 0010](docs/adr/0010-signed-adapter-bom.md)): written by Night School at training time (sources and licenses, tasks, dataset and golden-set hashes, eval), signed with Ed25519 when `RATCHET_BOM_SIGNING_KEY` is set. `python -m ratchet.bom keygen|verify`. Live mode also writes `data/boms/student-vN.json`.
- Endpoints: `GET /healthz`, `GET /ratchet/versions/{id}/bom`, `GET /ratchet/bom/public-key`, `POST /ratchet/redact` (preview, stores nothing).
- `/ratchet/stats`: `clients`, `privacy`, `security`, and `bom` per version.
- Ledger: `requests.client`, `examples.redactions`, `versions.bom` (with migrations), a `(client, ts)` index, `stats_by_client`, `privacy_stats`, `client_requests_since`.
- Dashboard: **Trust and privacy** panel (redaction, API keys and quota, BOM signing, per-client usage, a live redactor) and a **BOM** column in Model versions. The landing page's bill-of-materials card mentions per-adapter signatures. README screenshot `docs/img/trust.jpg`.
- `sign` extra (`cryptography`), installed in the gateway image; `pytest-cov` in `dev`; CI reports coverage.
- ADRs 0009 and 0010. 15 new tests (64 in total).

### Changed
- The demo VM's Docker health check and `update.sh` use `/healthz`, which stays public when API keys protect `/v1/*`.
- `test_gateway_rejects_bad_requests` no longer expects `stream: true` to be rejected.
- Docs: API, configuration, architecture, threat model (T1, T4, T6, T19, new T21), PRD, roadmap, runbook, deployment, demo VM, testing, glossary, Devpost draft, demo script, README, CLAUDE.md files.

## [0.3.0] - 2026-10-20

### Added
- **React web app** (`web/`: Vite 8, React 19, TypeScript, Tailwind v4, motion, lucide-react, recharts, react-router), built into `ratchet/web/dist` and served by the gateway at `/`, `/scout` and `/dashboard`. Design brief in [web/DESIGN.md](web/DESIGN.md).
  - **Story page** (`/`): animated request-flow hero driven by the reference simulation, live gateway strip, and eight chapters: the rent, the license catch, a scrollytelling "how it turns" diagram, the four release gates with an animated rollback story (Wilson bound crossing the 92% floor), an interactive day scrubber over the simulation, Scout, the Nebius and NVIDIA stack, and quick start.
  - **Scout** (`/scout`): command-bar ask, live agent graph (each call coloured by who answered), sticky totals, answer with citations, per-agent run summary, detail lanes, error handling, POST fallback when Server-Sent Events are unavailable.
  - **Dashboard** (`/dashboard`): demo controls with night reports and 409/429 handling, KPI tiles with sparklines, release pipeline with animated version chips, cost / share / audited-agreement charts with table views, versions and by-task tables, filterable release log, provenance with fingerprint copy.
- `/lite`, `/lite/scout`, `/lite/dashboard` keep the hand-written pages; they are also the fallback when the build is missing.
- `id` and `ts` on events in `/ratchet/stats`.
- `make web`, `make web-dev`; CI `web` job (typecheck, build, committed build up to date).
- README screenshots of the new pages (`docs/img/landing.jpg`, `scout.jpg`, `dashboard.jpg`).
- `test_react_app_is_served_with_lite_fallback`. 49 tests in total.

### Changed
- `make demo` starts with a 4-day mock warm-up.

## [0.2.0] - 2026-10-20

### Added
- **Scout**, a multi-agent research demo (`ratchet/scout/`, [docs/SCOUT.md](docs/SCOUT.md)): planner → searcher → triage → reader → writer. Every LLM call goes through Ratchet with a task name. Triage and reader calls run in a thread pool.
- Scout search: `TavilySearch` (Tavily Search API, `search_depth: basic`) in live mode, `MockSearch` (simulated results) in mock mode.
- Scout budget guard (`ratchet/scout/guard.py`): daily spend cap, daily run cap and per-IP hourly limit; spend is counted per step, so a failed run still counts.
- Scout endpoints: `GET /scout/examples`, `GET /scout/status`, `GET /scout/stream` (Server-Sent Events), `POST /scout/run` (502 when a run fails partway).
- Scout CLI: `python -m ratchet.scout "<question>" --base-url <gateway>/v1 [--api-key] [--max-results]`.
- **Pinned tasks** (`Task.pinned`): always answered by the teacher, logged, never stored as training examples.
- New tasks: `source-triage`, `page-read` (distillable), `research-plan`, `research-write` (pinned).
- **Landing page** at `/` and Scout page at `/scout` (`ratchet/web/`). The dashboard moved to `/dashboard`.
- Dashboard: navigation links, a **Traffic** selector (tickets, Scout research calls, both), a **By task** table, and error messages under the demo buttons.
- `tasks` in `GET /ratchet/stats` (`Ledger.stats_by_task`).
- `workload` parameter for `POST /ratchet/demo/day` (`tickets`, `research`, `mixed`).
- Mock research world (`sim/research.py`): 8 questions, 12 result templates × 6 variants on `example.org`. Workload streams and the shared mock item interface (`sim/world.py`). `--workload` flag for `python -m sim.simulate`.
- Mock warm-up at startup (`RATCHET_MOCK_WARMUP_DAYS`, `RATCHET_MOCK_WARMUP_REQUESTS`) and Scout pacing in mock mode (`RATCHET_MOCK_PACE`). `warmup` event kind.
- New settings: `TAVILY_API_KEY`, `RATCHET_SCOUT_DAILY_BUDGET_USD`, `RATCHET_SCOUT_RUNS_PER_HOUR`, `RATCHET_SCOUT_MAX_RUNS_PER_DAY`, `RATCHET_SCOUT_MAX_RESULTS`, `RATCHET_TRUST_PROXY`.
- **Nebius VM deploy kit** (`deploy/vm/`, [docs/DEPLOY_NEBIUS_VM.md](docs/DEPLOY_NEBIUS_VM.md)): `create_vm.sh`, `cloud-init.yaml`, Docker Compose with Caddy (automatic HTTPS, SSE pass-through, opt-in `RATCHET_API_GUARD` for `/v1/*` and `/ratchet/demo/*`), `update.sh`, `backup.sh`, `.env.vm.example`.
- `tests/test_scout.py` (14 tests). 49 tests in total.
- [ADR 0008](docs/adr/0008-pinned-tasks.md): pinned tasks.
- `pinned` per task in `/ratchet/stats` `tasks`; the Scout page labels simulated search as "Simulated search" instead of "Tavily".

### Changed
- Demo controls are safe to share: `day` and `reset` run one at a time (409 when busy) and `reset` has a 30-second cooldown (429).
- `Ledger` methods hold a re-entrant lock, because Scout's agents call the gateway from several threads.
- Mock models work on any mock item (tickets and research calls) through one interface: `labels`, `label_space`, `fragile`, `answer()`. The mock teacher also answers Scout's pinned planner and writer prompts. Ticket simulation numbers are unchanged.
- `Ratchet.handle` routes pinned tasks straight to the teacher.
- `ratchet/web/*.html` ships as package data. `.dockerignore` excludes `.env` files at any depth.
- Docs updated for all of the above: README, API, configuration, architecture, testing, deployment, threat model, glossary, PRD, roadmap, Devpost draft, demo script, runbook, CLAUDE.md files.

## [0.1.1] - 2026-10-19

### Added
- Full documentation set: PRD, architecture, API, configuration, deployment, runbook, threat model, testing, 7 ADRs, business and competitive docs, Devpost submission draft.
- CLAUDE.md files for the repo and each folder.
- CI (ruff, pytest on 3.10–3.13, simulation smoke test, Docker build), issue/PR templates, Dependabot, Makefile, gateway Dockerfile, `.env.example`.
- `python -m ratchet.live reload` re-loads adapters after an endpoint restart.
- `ReleasePolicy.from_env()` (`RATCHET_<FIELD>` overrides).
- `train.py --targets` for Nemotron-H LoRA target modules.

### Fixed
- Live mode now advances the day (`Ledger(auto_day=True)`).
- Student endpoint errors fall back to the teacher; teacher errors return 502 instead of 500.
- The untuned fallback model uses the default threshold, not a tuned version's.
- Existing SQLite files are migrated on open.
- Live precision alerts fire once per version instead of every tick.
- Unknown task names no longer grow the task registry.

## [0.1.0] - 2026-10-19

### Added
- OpenAI-compatible gateway with per-request cascade (Nemotron Nano student → Nemotron 3 Ultra teacher).
- Confidence scoring (token logprobs + self-consistency) with per-version calibrated thresholds.
- Provenance ledger and model bill of materials; closed-model teachers refused.
- Night School: dataset build, Unsloth LoRA training script, golden-set evaluation, champion vs challenger.
- Release gate: golden gate, shadow with teacher arbitration, canary 10% → 50% → 100%, Wilson-bound rollback.
- Dashboard, offline simulator with drift and fault injection, Nebius deploy scripts, day-1 check script.
