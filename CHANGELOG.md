# Changelog

All notable changes are listed here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [SemVer](https://semver.org/).

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
