# Changelog

All notable changes are listed here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [SemVer](https://semver.org/).

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
