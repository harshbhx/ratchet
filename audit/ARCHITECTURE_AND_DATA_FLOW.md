# Architecture and data flow (as audited)

The authoritative descriptions are `docs/ARCHITECTURE.md` and the per-folder `CLAUDE.md` files. This page is the audit's
map of the critical path, with the trust boundaries and where each finding sits. Every file and function named was read.

## Request path (one chat request)

| Stage | Code | Inputs → outputs | Trust boundary / safeguard | Findings |
|---|---|---|---|---|
| Edge | `deploy/vm/Caddyfile` | HTTPS → app on :8000 (not published) | Optional `RATCHET_API_GUARD=guarded` token; strips it before the app | RAT-015 |
| Auth and rate | `gateway._client`, `_rate`, `ratelimit.py` | key → name; token bucket per key or IP | Keys compared by SHA-256 digest; names only stored (INV-13) | RAT-011, RAT-013 |
| Body, validation | `openai_compat.validate_chat_request`, `normalize_messages` | JSON → `ChatRequest` | Client mistakes are 400s in OpenAI's envelope | RAT-025 |
| Storage decision | `gateway._request_meta`, `Ratchet.store_opt_out` | headers, body, key limits → `RequestMeta` | Opt-outs only reduce storage (INV-22) | RAT-002, RAT-003 |
| Task, quota, budget | `gateway._serve_held`, `spend.py` | model/header → task; meter → 429 | Key scopes, quota held for in-flight requests | RAT-018 |
| Cascade | `router.Ratchet.handle` | messages → student samples → confidence → serve or escalate | Pinned, tool and multimodal requests never reach the student (INV-10) | RAT-012 |
| Upstream calls | `providers.OpenAICompatProvider`, `resilience.retry_call`, breakers | HTTP to Token Factory / vLLM | Retries only where the model never saw the request | RAT-006, RAT-007, RAT-016 |
| Storage | `router._store_example` → `Ledger.add_example` | teacher answer → redacted example with license | Only complete, valid teacher answers (INV-16); redaction first (INV-12) | RAT-009 |
| Off-path audits | `sidecar.py`, `router._finish_offpath` | audit/shadow after the response | Optional calls fail open (INV-15) | - |
| Response | `gateway._chat_response` | `Result` → OpenAI body or SSE | Cascade finishes before streaming, so errors are ordinary HTTP errors | - |

## Night and release path

| Stage | Code | Notes | Findings |
|---|---|---|---|
| Retention, receipts | `gateway.Runtime._purge`, `privacy.apply_retention`, `receipts.seal_due` | hourly in live mode, off the request path | RAT-008 |
| Dataset | `nightschool/dataset.py` | split by condition-free hash; FREQUENT rule; cross-task golden rule (ADR 0131) | RAT-022 (open) |
| Training | `live.NebiusTrainer` → `deploy/train_job.sh` → `nightschool/train.py` (GPU container) | Serverless Job, bucket mounted at /data | RAT-026 |
| Golden eval | `nightschool/evaluate.cascade_eval` | calibrate on even rows, measure on odd (INV-6); per-task records; certificate | RAT-004, RAT-005 |
| BOM | `bom.manifest`, `notices.py`, `merkle.py`, `proof.py` | signed once (INV-14); committed leaves append-only (INV-17) | RAT-017 |
| Gate | `release.ReleaseGate.admit` / `tick` | shadow → canary 10/50 → live, rollback; Wilson or anytime-valid rule | RAT-001, RAT-014 |
| Human labels | `feedback.py`, `importer.py` | corrections and imports as provenance `human` | RAT-010 |

## State and concurrency

- One SQLite ledger per gateway (WAL, one writer connection under an RLock, a read-only connection for stats). Release
  moves are compare-and-set inside `BEGIN IMMEDIATE` (`Ledger.transition`, `promote_live`), so the gateway's tick, the
  cron tick and an admin decide once. Before this audit the anytime-valid rule bypassed that (RAT-001).
- Background threads: the sidecar's audit workers, the retention job, the adapter reloader, readiness probes. The
  retention job released a lock it did not own (RAT-008).
- Multi-instance: one gateway per ledger file is assumed (NFR-60's lease is not built; documented as a strict xfail).
