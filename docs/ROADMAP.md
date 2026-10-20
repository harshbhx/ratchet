# Roadmap

As of 2026-10-20. Hackathon deadline: **Oct 30, 2026, 10:00 AM PDT (10:30 PM IST)**. Target submission: **Oct 29, 2026**.

## Milestone 0: v0.1 built (done, Oct 19)

- Gateway, cascade, provenance ledger, Night School, release gate, dashboard, simulator.
- 34 tests passing.
- Simulation shows the full loop: cold start, promotion, drift recovery, automatic rollback.
- Market research and stress test written.
- Token Factory ToS 5(f) question emailed to tokenfactory-support@nebius.com.

## Milestone 0.5: v0.2 demo layer (done, Oct 20)

- **Scout**, a multi-agent research demo ([SCOUT.md](SCOUT.md)): five agents whose calls all go through Ratchet. Triage and reader calls are distilled; planner and writer calls are pinned to the teacher. Tavily search in live mode, a mock research world in mock mode. Budget guard for the public demo.
- **Pinned tasks** in the task registry: always the teacher, never training data.
- **Landing page** (`/`), Scout page (`/scout`), dashboard moved to `/dashboard` with a **By task** table and a traffic selector.
- Simulator workloads: `tickets`, `research`, `mixed`. Mock warm-up at startup.
- **Nebius VM deploy kit** (`deploy/vm/`, [DEPLOY_NEBIUS_VM.md](DEPLOY_NEBIUS_VM.md)): cloud-init, Docker Compose, Caddy with HTTPS and an opt-in API guard, backups, updates.
- 49 tests passing.

## Milestone 0.75: v0.3 web app and v0.4 hardening (done, Oct 20)

- **v0.3.0:** React story page, Scout page and dashboard, served by the gateway; hand-written pages kept at `/lite*`.
- **v0.4.0:** the offline items of the hardening milestone, pulled forward because they need no Nebius account:
  - PII redaction before examples are stored ([ADR 0009](adr/0009-pattern-based-pii-redaction.md)).
  - Gateway API keys with per-key daily quotas and per-client cost and savings.
  - Streaming responses (`stream: true`).
  - A signed bill of materials per adapter, with an offline verifier ([ADR 0010](adr/0010-signed-adapter-bom.md)).
  - `/healthz`, coverage in CI, and a **Trust and privacy** panel on the dashboard with a live redactor.
- 64 tests passing.

## Milestone 0.9: v0.5 backend completeness (in progress, from Oct 20)

Built package by package. Every item gets offline tests and an adversarial review, and the reference simulation numbers must not change: since the evidence foundations, three workloads (tickets, mixed, research) must reproduce v0.4.0 exactly (`make reference`). The UI is out of scope for v0.5.

| Package | Scope |
|---|---|
| A. Verification foundation | Requirements traceability matrix generated from the PRD and checked in CI; tests for every invariant and release-gate rule; a pinned reference simulation; contract tests for the gateway, stats and licensing; offline tests for live-mode entry points |
| B. Request contract and resilience | Strict validation with OpenAI-style errors; teacher retries with backoff and Retry-After; a student circuit breaker; optional teacher calls that never break a served answer; answer hygiene; generation parameters and token caps; full OpenAI response parity; a degraded mode when the teacher is down |
| C. Task model and use cases | Per-field comparators; task registry and per-task thresholds; structured outputs (JSON schema) as tasks; tool-call pass-through and opt-in tool-call distillation; risk-aware routing; free-text and SQL tasks; mock workloads and a use-case catalog; adjacent OpenAI endpoints; real teacher streaming |
| D. Operations, security, performance | Hot-path performance; authorization for management endpoints; request IDs, metrics and a failures table; per-key spend and rate limits; an admin API with an audit trail; config validation and readiness; Night School leases and alert webhooks; idempotency; quality checks off the request path |
| E. Data governance and KPIs | Storage opt-outs per request, key, task and end user, zero retention, and end users kept as keyed hashes (done: FR-13.1, FR-13.2, [ADR 0080](adr/0080-storage-opt-outs-and-pseudonymous-end-users.md)); retention, verifiable erasure and export (done: FR-13.3 to FR-13.5, [ADR 0081](adr/0081-retention-tombstones-erasure-and-export.md)); redaction for extraction tasks; licenses that travel with the adapter; human feedback and labelled-data import; PRD success metrics computed and checked in CI |
| F. Novel features | Utilization-aware cost accounting (GPU-hours, break-even, self-host recommendation); shared foundations built first (exact statistics, Merkle trees, one schema change with append-only tables, reserved settings, BOM v2; [ADR 0024](adr/0024-evidence-foundations.md)); an anytime-valid release gate (e-process rollback, confidence sequences); committed training sets (Merkle roots in the BOM, membership proofs); certified precision; a ledger time machine for counterfactual replay; a label-free drift alarm with a false-alarm bound; teacher self-consistency checks; verified savings receipts; clean-room backfill through the Batch API; provable erasure; a guarantee harness; and a one-command evidence tour |

## Milestone 1: hackathon submission (Oct 19–29, 2026)

> Stage One of judging is pass/fail on the submission requirements, and one of them is a runtime call to Token Factory. Mock mode makes none, so the public demo must run in live mode during judging (Dec 1–15). Details in [HACKATHON_SUBMISSION.md](HACKATHON_SUBMISSION.md#stage-one-passfail-readiness).

| Dates | Work | Exit criteria |
|---|---|---|
| Oct 19–20 | Day-1 checks ([DAY1.md](DAY1.md)): Token Factory model IDs, logprobs, JSON mode; Nano 4B LoRA on a Serverless Job; vLLM endpoint with LoRA; Serverless pricing | `scripts/day1_check.py` passes, or config updated; one adapter trained and served, or fallback chosen |
| Oct 21–22 | Live cascade: gateway in live mode against Token Factory teacher and the student endpoint | Real requests served by both paths; ledger shows real costs |
| Oct 23–24 | Live Night School: dataset → Serverless Job → hot-load adapter → golden eval → release gate | At least one version trained and evaluated live |
| Oct 25 | Live traffic run: synthetic tickets through the real teacher for several "nights" | At least 3 versions; real cost and precision numbers |
| Oct 26–27 | Public demo on the Nebius VM ([DEPLOY_NEBIUS_VM.md](DEPLOY_NEBIUS_VM.md)): mock first, then **live mode** (required for Stage One) with live Scout, Tavily and the budget guard, Token Factory fallback student | Demo URL works logged-out over HTTPS; `/`, `/scout`, `/dashboard` load; live Token Factory and Tavily calls succeed; stays up when the student endpoint is stopped; credits cover the demo until Dec 15 |
| Oct 28 | README with live numbers, feedback section, video recorded ([DEMO_SCRIPT.md](DEMO_SCRIPT.md)) | Video under 3 minutes on YouTube (public) |
| Oct 29 | Submit on Devpost ([HACKATHON_SUBMISSION.md](HACKATHON_SUBMISSION.md)) | Every checklist item ticked |
| Dec 1–15 | Judging | Demo URL live the whole window (VM runs Oct 29 – Dec 15) |

## Milestone 2: hardening (Nov–Dec 2026)

Called "v0.2 hardening" in earlier drafts. Version numbers went to the demo layer (v0.2), the web app (v0.3) and the first hardening items (v0.4, done above), so what is left targets v0.5.

| Item | Why | Status |
|---|---|---|
| PII redaction before storing examples | Real customer data | Pattern-based: done (v0.4). NER-based (Presidio) for names and addresses: planned |
| Gateway API keys and per-key quotas | Security | Done (v0.4). Per-key spend caps: planned |
| Streaming responses | Many apps stream | Done (v0.4) |
| Signed model bill of materials stored with each adapter | Auditable provenance | Done (v0.4). Copy inside the adapter folder in the bucket: planned |
| GitHub Actions CI with coverage | Open-source adoption | Done (v0.4). PyPI package: planned |
| Postgres ledger option | Concurrency, durability | Planned |
| Multiple tasks per deployment with separate golden sets and thresholds | Real workloads mix tasks | Planned |
| Batch API for backfill labelling (50% cheaper on Token Factory) | Cost | Planned |
| Nemotron 3 Nano 30B student on H100/H200 | Harder tasks | Planned |

Exit criteria: two design partners running v0.5 on one task each; zero bad deploys at 100%.

## Milestone 3: v1.0 Ratchet Cloud (Q1–Q2 2027)

- Managed, multi-tenant service on Nebius.
- Savings-based billing from ledger receipts.
- Region choice: EU and India.
- Alerts (email, Slack), audit exports, SSO.
- One-click migration path for OpenAI fine-tuning users (before or soon after Jan 6, 2027).

Exit criteria: 10 paying customers; median verified savings ≥ 60%; audited precision ≥ 95%.

## Startup track (parallel)

| When | Step |
|---|---|
| Nov 2026 | Apply to NVIDIA Inception; talk to Nebius about partnership |
| Nov–Dec 2026 | 10 design-partner conversations (PIERC network, Indian AI startups) |
| Dec 2026 | Show HN launch with live numbers |
| Q1 2027 | Decide: raise a pre-seed, or keep it as an open-source project |

Decision rule for going full startup: at least 3 design partners with verified savings and a written intent to pay.
