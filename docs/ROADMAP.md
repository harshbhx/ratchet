# Roadmap

As of 2026-10-19. Hackathon deadline: **Oct 30, 2026, 10:00 AM PDT (10:30 PM IST)**. Target submission: **Oct 29, 2026**.

## Milestone 0: v0.1 built (done, Oct 19)

- Gateway, cascade, provenance ledger, Night School, release gate, dashboard, simulator.
- 34 tests passing.
- Simulation shows the full loop: cold start, promotion, drift recovery, automatic rollback.
- Market research and stress test written.
- Token Factory ToS 5(f) question emailed to tokenfactory-support@nebius.com.

## Milestone 1: hackathon submission (Oct 19–29, 2026)

| Dates | Work | Exit criteria |
|---|---|---|
| Oct 19–20 | Day-1 checks ([DAY1.md](DAY1.md)): Token Factory model IDs, logprobs, JSON mode; Nano 4B LoRA on a Serverless Job; vLLM endpoint with LoRA; Serverless pricing | `scripts/day1_check.py` passes, or config updated; one adapter trained and served, or fallback chosen |
| Oct 21–22 | Live cascade: gateway in live mode against Token Factory teacher and the student endpoint | Real requests served by both paths; ledger shows real costs |
| Oct 23–24 | Live Night School: dataset → Serverless Job → hot-load adapter → golden eval → release gate | At least one version trained and evaluated live |
| Oct 25 | Live traffic run: synthetic tickets through the real teacher for several "nights" | At least 3 versions; real cost and precision numbers |
| Oct 26–27 | Dashboard polish; public demo URL with Token Factory fallback | Demo URL works logged-out; stays up when the student endpoint is stopped |
| Oct 28 | README with live numbers, feedback section, video recorded ([DEMO_SCRIPT.md](DEMO_SCRIPT.md)) | Video under 3 minutes on YouTube (public) |
| Oct 29 | Submit on Devpost ([HACKATHON_SUBMISSION.md](HACKATHON_SUBMISSION.md)) | Every checklist item ticked |
| Dec 1–15 | Judging | Demo URL live the whole window |

## Milestone 2: v0.2 hardening (Nov–Dec 2026)

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

Exit criteria: two design partners running v0.2 on one task each; zero bad deploys at 100%.

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
