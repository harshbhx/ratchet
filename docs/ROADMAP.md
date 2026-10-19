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

## Milestone 1: hackathon submission (Oct 19–29, 2026)

| Dates | Work | Exit criteria |
|---|---|---|
| Oct 19–20 | Day-1 checks ([DAY1.md](DAY1.md)): Token Factory model IDs, logprobs, JSON mode; Nano 4B LoRA on a Serverless Job; vLLM endpoint with LoRA; Serverless pricing | `scripts/day1_check.py` passes, or config updated; one adapter trained and served, or fallback chosen |
| Oct 21–22 | Live cascade: gateway in live mode against Token Factory teacher and the student endpoint | Real requests served by both paths; ledger shows real costs |
| Oct 23–24 | Live Night School: dataset → Serverless Job → hot-load adapter → golden eval → release gate | At least one version trained and evaluated live |
| Oct 25 | Live traffic run: synthetic tickets through the real teacher for several "nights" | At least 3 versions; real cost and precision numbers |
| Oct 26–27 | Public demo on the Nebius VM ([DEPLOY_NEBIUS_VM.md](DEPLOY_NEBIUS_VM.md)): mock first, then live Scout with Tavily and the budget guard, Token Factory fallback student | Demo URL works logged-out over HTTPS; `/`, `/scout`, `/dashboard` load; stays up when the student endpoint is stopped |
| Oct 28 | README with live numbers, feedback section, video recorded ([DEMO_SCRIPT.md](DEMO_SCRIPT.md)) | Video under 3 minutes on YouTube (public) |
| Oct 29 | Submit on Devpost ([HACKATHON_SUBMISSION.md](HACKATHON_SUBMISSION.md)) | Every checklist item ticked |
| Dec 1–15 | Judging | Demo URL live the whole window (VM runs Oct 29 – Dec 15) |

## Milestone 2: hardening (Nov–Dec 2026)

Called "v0.2 hardening" in earlier drafts. The v0.2.0 version number went to the demo layer above, so these items now target v0.3.

| Item | Why |
|---|---|
| PII redaction (Presidio) before storing examples | Real customer data |
| Gateway API keys and per-key quotas | Security |
| Streaming responses | Many apps stream |
| Postgres ledger option | Concurrency, durability |
| Multiple tasks per deployment with separate golden sets and thresholds | Real workloads mix tasks |
| Signed model bill of materials stored with each adapter | Auditable provenance |
| Batch API for backfill labelling (50% cheaper on Token Factory) | Cost |
| Nemotron 3 Nano 30B student on H100/H200 | Harder tasks |
| GitHub Actions CI with coverage; PyPI package | Open-source adoption |

Exit criteria: two design partners running v0.3 on one task each; zero bad deploys at 100%.

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
