# Ratchet: Product Requirements Document

| | |
|---|---|
| Owner | Harsh Bhanushali (DualMind Labs) |
| Status | v0.4.0 built (hackathon MVP, Scout demo, React web app, demo VM kit, privacy and security hardening); live Nebius run pending |
| Last updated | 2026-10-20 |
| Related | [Requirements matrix](REQUIREMENTS.md) · [Architecture](ARCHITECTURE.md) · [API](API.md) · [Deployment](DEPLOYMENT.md) · [Research summary](RESEARCH_SUMMARY.md) · [Roadmap](ROADMAP.md) |

## 1. Overview

**One-liner:** Ratchet is an OpenAI-compatible gateway that turns a company's own AI traffic into a cheaper model the company owns, using only teachers whose licenses allow it.

**Tagline:** Rent intelligence today. Own it tomorrow.

An app changes one line, its API base URL. From then on:

1. A small model the customer owns (the **student**) tries each request first.
2. If the student is not confident, the request goes to a large open model (the **teacher**, NVIDIA Nemotron 3 Ultra on Nebius Token Factory).
3. Every teacher answer is stored with its **provenance**: which teacher wrote it and which license allows training on it.
4. Every night, **Night School** trains a new student on that data, tests it on a held-out **golden set**, and releases it step by step (shadow, canary 10%, 50%, 100%), with automatic rollback.

The bill goes down over time. At the end, the customer owns the model weights.

## 2. Problem

### 2.1 What hurts

- Companies rent frontier models for tasks a small model could handle. Many requests are narrow and repetitive: classification, extraction, ticket triage, sub-steps inside agents.
- Inference spend is large and growing. Enterprise generative AI spend reached $37B in 2025, 3.2x the year before, with $12.5B on model APIs ([Menlo Ventures](https://menlovc.com/perspective/2025-the-state-of-generative-ai-in-the-enterprise/)). Gartner puts inference at 55% ($23.3B) of AI-optimized cloud spend in 2026 ([Gartner](https://www.gartner.com/en/newsroom/press-releases/2026-08-10-gartner-forecasts-worldwide-artificial-intelligence-optimized-iaas-spending-to-grow-96-percent-in-2026)).
- AI-native startups feel it in their margins. Some AI coding startups report neutral or negative gross margins ([TechCrunch](https://techcrunch.com/2025/08/07/the-high-costs-and-thin-margins-threatening-ai-coding-startups/)).
- Distillation works, but doing it by hand is slow and risky. Teams must log traffic, clean data, fine-tune, evaluate, deploy, and watch for regressions. Most tools cover only one of those steps.
- Distilling from closed models breaks their terms. OpenAI's [Business Terms 3.3(e)](https://openai.com/policies/business-terms/) and Anthropic's [Commercial Terms D.4(a)](https://www.anthropic.com/legal/commercial-terms) forbid using outputs to train competing models. A tool that learns from GPT or Claude traffic puts its users at risk.
- OpenAI is ending self-serve fine-tuning: no new jobs after Jan 6, 2027 ([OpenAI deprecations](https://developers.openai.com/api/docs/deprecations)). Teams with fine-tunes there need a new home.

### 2.2 Evidence the approach works

- Gartner expects small task-specific models to be used 3x more than general LLMs by 2027 ([Gartner](https://www.gartner.com/en/newsroom/press-releases/2025-04-09-gartner-predicts-by-2027-organizations-will-use-small-task-specific-ai-models-three-times-more-than-general-purpose-large-language-models)).
- NVIDIA Research estimates 40–70% of LLM calls in agent systems could go to small models, which are 10–30x cheaper to serve ([arXiv 2506.02153](https://arxiv.org/html/2506.02153v1)).
- Checkr replaced GPT-4 with a fine-tuned 8B model at 5x lower cost ([Rubrik blog](https://www.rubrik.com/blog/ai/24/how-checkr-streamlines-background-checks-with-fine-tuned-small-language)). Convirza cut cost 10x with LoRA adapters ([ZenML](https://zenml.io/llmops-database/optimizing-call-center-analytics-with-small-language-models-and-multi-adapter-serving)).
- Cascades: FrugalGPT matched GPT-4 at up to 98% lower cost ([arXiv](https://arxiv.org/abs/2305.05176)). Online Cascade Learning reported up to 90% lower cost ([arXiv](https://arxiv.org/html/2402.04513v2)).

### 2.3 Buyer caution

Buyers rank proving ROI (43%) far above price (7%) as their top challenge ([State of Tokenomics 2026](https://www.nops.io/blog/state-of-tokenomics-2026/)). Ratchet must show savings with receipts and show that quality held, not just promise cheaper tokens.

## 3. Goals and non-goals

### 3.1 Goals

| ID | Goal |
|---|---|
| G1 | Cut inference cost for narrow, high-volume tasks without users noticing a quality drop |
| G2 | Make distillation automatic: no manual dataset curation, training runs, or deploys |
| G3 | Make every release safe: nothing reaches users unless it beats or matches what is live |
| G4 | Make training data license-clean and provable (provenance ledger, model bill of materials) |
| G5 | Give the customer ownership of the trained weights |
| G6 | Run end to end on open models and Nebius infrastructure (Token Factory + AI Cloud) |
| G7 | Win or place in the Nebius x NVIDIA Global AI Hackathon (Best Apps and Agents track) |

### 3.2 Non-goals (v0.1)

- Streaming the student's tokens as they are generated (v0.4 streams the decided answer; see FR-1.6).
- Multi-tenant SaaS with billing, auth, and per-customer isolation.
- Open-ended generation tasks (long-form writing, chat). v0.1 targets tasks with checkable structured answers.
- Closed-model teachers. These are refused by design.
- Reinforcement learning or preference tuning. v0.1 uses supervised LoRA fine-tuning only.
- Multi-node or very large students (Nemotron 3 Nano 30B is a stretch goal).

## 4. Users and personas

### P1. Priya, CTO of an AI-native startup

- Runs a support-automation SaaS. Spends $25k+ a month on frontier APIs.
- Inference eats her gross margin. Investors ask about it.
- Has two engineers, no ML team.
- **Needs:** savings she can prove, no quality drop, almost no engineering time.

### P2. Marco, platform engineer at a mid-size company

- Owns the internal LLM gateway. Several teams call models for classification and extraction.
- **Needs:** an OpenAI-compatible drop-in, clear dashboards, safe rollouts, rollback, audit logs.

### P3. Anjali, AI lead at a regulated or sovereignty-minded company

- Works in finance or the public sector in India or the EU.
- Must know where data and models live and must prove training data is clean.
- Sovereign cloud spend is forecast at $80.4B in 2026 ([Gartner](https://www.gartner.com/en/newsroom/press-releases/2026-02-09-gartner-says-worldwide-sovereign-cloud-iaas-spending-will-total-us-dollars-80-billion-in-2026)).
- **Needs:** own the weights, a provenance record she can show auditors, region control.

### P4. Leo, an OpenAI fine-tuning migrant

- Has a fine-tuned GPT model in production. OpenAI stops new fine-tuning jobs on Jan 6, 2027.
- **Needs:** a path to a model he owns, built from his own traffic, with no license risk.

## 5. User stories and acceptance criteria

**US-1. Drop-in setup.** As Priya, I want to change only my base URL so that I can start without rewriting my app.
- Given an app using an OpenAI SDK, when its base URL points to Ratchet's `/v1`, then `chat.completions.create` returns a standard `chat.completion` object.
- Given a request with header `X-Ratchet-Task: ticket-triage`, then Ratchet routes it under that task's rules.

**US-2. Cold start.** As Priya, I want Ratchet to work on day one, before any student exists.
- Given no trained student, when a request arrives, then the teacher answers it and the answer is stored as a training example.

**US-3. Cheaper answers when safe.** As Priya, I want confident student answers served and unsure ones escalated.
- Given a live student, when its confidence is at or above that version's calibrated threshold, then the student's answer is served.
- Given confidence below the threshold, then the teacher answers and the request counts as escalated.

**US-4. Quality monitoring.** As Marco, I want ongoing checks on what the student serves.
- Given a student-served request, then with probability `audit_rate` the teacher also answers it and the agreement is recorded.

**US-5. Safe release.** As Marco, I want a new model to reach users only after it proves itself.
- Given a newly trained candidate, when its golden-set precision is below 95%, or it regresses against the live model, then it is rejected.
- Given a candidate that passes, when a live model exists, then it runs in shadow mode first.
- Given shadow results with enough served answers, precision of at least 95%, and coverage no worse than live, then it moves to canary 10%, then 50%, then 100%.

**US-6. Automatic rollback.** As Marco, I want a bad model pulled automatically.
- Given a canary version, when the 95% upper bound of its audited precision falls below 92%, then it is marked `rolled_back` and traffic returns to the previous live version (or the teacher).

**US-7. Provable data provenance.** As Anjali, I want to prove where training data came from.
- Given any training example, then it records teacher model, license name, and license URL.
- When I call `GET /ratchet/bom`, then I get every source, its license, its counts by split, and a SHA-256 fingerprint of the dataset.

**US-8. Closed teachers refused.** As Anjali, I want Ratchet to refuse unlicensed teachers.
- Given a teacher model starting with `gpt`, `o1`, `o3`, `o4`, `claude`, or `gemini`, or an unknown model, when Ratchet starts, then it raises `LicenseError`.

**US-9. Savings receipts.** As Priya, I want to see dollars saved against an all-teacher baseline.
- Given traffic, then the dashboard shows total saved, savings % by day, cost per 1,000 requests vs baseline, and the share answered by the student.

**US-10. Own the weights.** As Leo, I want the trained adapter in storage I control.
- Given a training run in live mode, then the adapter is written to the customer's Nebius Object Storage bucket under `adapters/<version>`.

**US-11. Demo without keys.** As a hackathon judge, I want to see the loop work without credentials.
- Given `RATCHET_MODE=mock`, when I click "Run 1 day + night", then a simulated day of traffic and a training night run, and the dashboard updates.
- When I click "Break next deploy", then the next deployed version is corrupted and later rolled back.

## 6. Functional requirements

Each requirement's Status is one of these forms, checked by `python scripts/reqtrace.py --check` in CI. The check also maps every row to its code and to the tests marked with its id (`@pytest.mark.req("FR-2.1")`); the generated matrix is [REQUIREMENTS.md](REQUIREMENTS.md).

| Status | Meaning |
|---|---|
| `Done (vX)` | Built in release vX and covered by an offline test (simulation, fakes or `httpx.MockTransport`), or listed as a known gap or a manual check in the matrix |
| `Done (vX), untested (live)` | Built and tested offline, but not yet run against the real service; also `untested (on GPU)` and `untested (on Nebius)` |
| `Done (vX); <text> planned` | Built, with a named extension still planned |
| `Partial: <text>` | Part of it is built; the text says what is missing |
| `Planned` | Not built yet. A test for it must be a strict expected failure |
| `Replaced by FR-a.b` | Superseded by another requirement |
| `Met in simulation (<text>)`, `Not met (<text>)`, `Not checked` | Success metrics (§8.1) only |

### 6.1 Gateway

| ID | Requirement | Status |
|---|---|---|
| FR-1.1 | `POST /v1/chat/completions` accepts OpenAI-format requests and returns a `chat.completion` object plus a `ratchet` metadata block (served_by, student_version, confidence, escalated, cost_usd, all_teacher_cost_usd, latency_ms) | Done (v0.1) |
| FR-1.2 | `GET /v1/models` lists a `ratchet` model | Done (v0.1) |
| FR-1.3 | Task selection via `X-Ratchet-Task` header or `metadata.ratchet_task` | Done (v0.1) |
| FR-1.4 | Reject `stream: true` with HTTP 400 (until v0.4) | Replaced by FR-1.6 |
| FR-1.5 | Reject missing or empty `messages` with HTTP 400 | Done (v0.1) |
| FR-1.6 | Streaming responses: `stream: true` returns OpenAI `chat.completion.chunk` events and `[DONE]`, sent once the cascade has decided | Done (v0.4) |
| FR-1.7 | API keys / auth on the gateway: named keys (`RATCHET_API_KEYS`), per-key daily quota, cost and savings per client. The demo VM also has an opt-in edge guard in Caddy | Done (v0.4) |
| FR-1.8 | Student endpoint errors fall back to the teacher; teacher errors return HTTP 502 | Done (v0.1.1) |
| FR-1.9 | Web pages: landing page at `/`, Scout at `/scout`, dashboard at `/dashboard` | Done (v0.2) |

### 6.2 Cascade and confidence

| ID | Requirement | Status |
|---|---|---|
| FR-2.1 | Student sampled `n=3` times (temperature 0.7, logprobs on) per request | Done (v0.1) |
| FR-2.2 | Confidence = 0.4 × token probability of best majority sample + 0.6 × sample agreement; invalid-format answers score 0 | Done (v0.1) |
| FR-2.3 | Each version carries its own calibrated threshold; global default 0.80 | Done (v0.1) |
| FR-2.4 | Escalate to teacher below threshold | Done (v0.1) |
| FR-2.5 | Audit a share of served student answers with the teacher (`audit_rate`, default 5%) | Done (v0.1) |
| FR-2.6 | Task definitions compare structured fields exactly (e.g. `category`, `priority`); free-text fields ignored for agreement | Done (v0.1) |
| FR-2.7 | Strip `<think>` reasoning and code fences before comparing or storing | Done (v0.1) |
| FR-2.8 | Canary traffic split by `traffic_pct` | Done (v0.1) |
| FR-2.9 | Pinned tasks: always answered by the teacher, logged, never stored as training examples | Done (v0.2) |

### 6.3 Provenance and licensing

| ID | Requirement | Status |
|---|---|---|
| FR-3.1 | License registry for teachers; open Nemotron models allowed | Done (v0.1) |
| FR-3.2 | Closed models (OpenAI, Anthropic, Google prefixes) and unknown models refused | Done (v0.1) |
| FR-3.3 | Every example stores teacher, license, license URL, hash, split | Done (v0.1) |
| FR-3.4 | Duplicates stored once; repeat count kept in `seen` | Done (v0.1) |
| FR-3.5 | `GET /ratchet/bom` model bill of materials with dataset SHA-256 | Done (v0.1) |
| FR-3.6 | Signed, exportable BOM attached to each adapter: Ed25519, `/ratchet/versions/{id}/bom`, `python -m ratchet.bom verify` | Done (v0.4) |

### 6.4 Night School (training)

| ID | Requirement | Status |
|---|---|---|
| FR-4.1 | Build cumulative training set nightly from teacher answers only (never student answers) | Done (v0.1) |
| FR-4.2 | Retrain from the base model each night (no adapter stacking) | Done (v0.1) |
| FR-4.3 | Golden set: ~10% of unique requests by hash, held out; requests seen 3+ times move to training | Done (v0.1) |
| FR-4.4 | Skip training if fewer than 50 new examples, no golden set, or a release is in flight | Done (v0.1) |
| FR-4.5 | LoRA fine-tune script with Unsloth (fallback: transformers + peft) for Nemotron 3 Nano 4B | Done (v0.1), untested (on GPU) |
| FR-4.6 | Run training as a Nebius Serverless Job; wait for adapter in bucket | Done (v0.1), untested (on Nebius) |
| FR-4.7 | PII redaction before storing examples (pattern-based with checksums; names and addresses not covered) | Done (v0.4); NER-based detection planned |

### 6.5 Evaluation

| ID | Requirement | Status |
|---|---|---|
| FR-5.1 | Golden eval samples the student exactly as in production | Done (v0.1) |
| FR-5.2 | Calibrate threshold on half the golden set (target precision 97%), measure on the other half | Done (v0.1) |
| FR-5.3 | Report precision, coverage, threshold, raw agreement | Done (v0.1) |
| FR-5.4 | Re-score the live version on the same golden set each night (champion vs challenger) | Done (v0.1) |
| FR-5.5 | Reference-based judge (Nemotron 3 Super, JSON mode) for free-text tasks | Done (v0.1), untested (live) |

### 6.6 Release gate

| ID | Requirement | Status |
|---|---|---|
| FR-6.1 | Golden gate: precision ≥ 95%, coverage ≥ 25%, no regression vs live beyond 2 points | Done (v0.1) |
| FR-6.2 | Cold start (no live): first passing candidate goes straight to canary 10% | Done (v0.1) |
| FR-6.3 | Shadow mode: candidate scores real traffic silently; teacher arbitrates disagreements with the live student | Done (v0.1) |
| FR-6.4 | Shadow promotion: ≥ 100 would-serve answers, precision ≥ 95%, coverage ≥ live − 2 points | Done (v0.1) |
| FR-6.5 | Canary 10% → 50% → 100%, each step needs ≥ 20 audits in that stage and precision ≥ 92% | Done (v0.1) |
| FR-6.6 | Rollback when Wilson 95% upper bound of audited precision < 92% | Done (v0.1) |
| FR-6.7 | Live alert when live version's audited precision upper bound < 92% | Done (v0.1) |
| FR-6.8 | Only one candidate in flight; older shadow/canary retired | Done (v0.1) |

### 6.7 Dashboard

| ID | Requirement | Status |
|---|---|---|
| FR-7.1 | Tiles: saved vs all-teacher, cost reduction, student share, live version | Done (v0.1) |
| FR-7.2 | Charts: cost per 1,000 requests vs baseline; student vs escalated share by day | Done (v0.1) |
| FR-7.3 | Versions table with eval numbers and state | Done (v0.1) |
| FR-7.4 | Release log and provenance panel | Done (v0.1) |
| FR-7.5 | Mock-mode controls: run a day, break next deploy, reset | Done (v0.1) |
| FR-7.6 | Light and dark mode; works at phone width | Done (v0.1) |
| FR-7.7 | **By task** table (requests, student share, escalations, savings, route) from `tasks` in `/ratchet/stats` | Done (v0.2) |
| FR-7.8 | Traffic selector for **Run 1 day + night** (tickets, Scout research calls, both); demo controls run one at a time (409) and reset has a 30 s cooldown (429) | Done (v0.2) |

### 6.8 Live operations on Nebius

| ID | Requirement | Status |
|---|---|---|
| FR-8.1 | Teacher and judge via Token Factory OpenAI-compatible API | Done (v0.1), untested (live) |
| FR-8.2 | Student served by vLLM on a Nebius Serverless Endpoint with runtime LoRA loading | Done (v0.1), untested (live) |
| FR-8.3 | Fallback to hosted untuned Nemotron Nano on Token Factory when no student endpoint | Done (v0.1) |
| FR-8.4 | `python -m ratchet.live night` and `tick` commands for cron | Done (v0.1), untested (on Nebius) |
| FR-8.5 | `scripts/day1_check.py` verifies model IDs, logprobs, JSON mode | Done (v0.1), untested (live) |

### 6.9 Simulation

| ID | Requirement | Status |
|---|---|---|
| FR-9.1 | Synthetic support tickets (6 categories, 4 priorities, 20 templates, 3 drift templates) | Done (v0.1) |
| FR-9.2 | Mock teacher, mock student with learning curve and confidently-wrong failures | Done (v0.1) |
| FR-9.3 | Fault injection: corrupted deploy of a chosen version | Done (v0.1) |
| FR-9.4 | CLI `python -m sim.simulate` with table output and JSON results | Done (v0.1) |
| FR-9.5 | Mock research world for Scout (8 questions, simulated results on `example.org`) and `--workload tickets\|research\|mixed` | Done (v0.2) |
| FR-9.6 | Mock warm-up: `RATCHET_MOCK_WARMUP_DAYS` simulated days at startup | Done (v0.2) |

### 6.10 Scout (multi-agent demo)

| ID | Requirement | Status |
|---|---|---|
| FR-10.1 | Five agents (planner, searcher, triage, reader, writer); every LLM call goes through Ratchet with a task name | Done (v0.2) |
| FR-10.2 | Planner and writer pinned to the teacher; triage and reader distillable | Done (v0.2) |
| FR-10.3 | Live search with the Tavily Search API; simulated search in mock mode, labelled in the UI. The Tavily client is tested offline only | Done (v0.2), untested (live) |
| FR-10.4 | Streaming run events to the page (Server-Sent Events) and a JSON endpoint (`/scout/run`) | Done (v0.2) |
| FR-10.5 | Budget guard: daily spend cap, daily run cap, per-IP hourly limit; spend counted per step | Done (v0.2) |
| FR-10.6 | CLI client: `python -m ratchet.scout "<question>" --base-url <gateway>/v1` | Done (v0.2) |

### 6.11 Demo hosting

| ID | Requirement | Status |
|---|---|---|
| FR-11.1 | One-command Nebius CPU VM setup (cloud-init, Docker Compose, Caddy HTTPS, firewall) | Done (v0.2), untested (on Nebius) |
| FR-11.2 | Daily ledger backups, update script with health checks | Done (v0.2), untested (on Nebius) |

### 6.12 Operations

No rows yet. `FR-12.x` rows go here, in the same `| ID | Requirement | Status |` table as the sections above.

### 6.13 Data governance

No rows yet. `FR-13.x` rows go here.

### 6.14 Use cases

No rows yet. `FR-14.x` rows go here.

## 7. Non-functional requirements

| ID | Area | Requirement | Status |
|---|---|---|---|
| NFR-1 | Latency | Student path adds at most one student call (n samples in one request). Target p50 under 1 s for student-served requests. Escalated requests cost one student call plus one teacher call. | Done (v0.1), untested (live) |
| NFR-2 | Availability | If the student endpoint errors, requests fall back to the teacher automatically (v0.1.1); with `RATCHET_STUDENT_BASE` unset, the hosted untuned Nano is used with the default threshold. `python -m ratchet.live reload` restores adapters after an endpoint restart. A demo URL must stay live through judging (Dec 1–15, 2026). | Done (v0.1.1); a demo URL that stays live through judging planned |
| NFR-3 | Cost | Shadow, audit, and arbitration overhead is counted in reported cost. No training while a release is in flight. | Done (v0.1) |
| NFR-4 | Privacy | Prompts and teacher answers are stored in SQLite with identifiers (emails, phones, cards, IBANs, national IDs, IPs, secrets) replaced by placeholders (v0.4). Use synthetic data only in demos. | Done (v0.4); NER-based redaction (Presidio) and zero-retention options planned |
| NFR-5 | Security | Secrets come from environment variables only. The student endpoint uses token auth. Named gateway API keys (`RATCHET_API_KEYS`, v0.4) protect `/v1/*`, each with an optional daily request quota (`RATCHET_API_KEY_DAILY_REQUESTS`, 429 past it). Keys are compared as constant-time digests and are never stored or logged: the ledger and `/ratchet/stats` see only key names. `/healthz`, the pages, Scout and `/ratchet/*` take no key; on the demo VM, Caddy can guard `/v1/*` and `/ratchet/demo/*` with an admin token. See [Threat model](THREAT_MODEL.md). | Done (v0.4) |
| NFR-6 | Observability | Every request, version change, and release event is logged in the ledger and visible on the dashboard. | Done (v0.1) |
| NFR-7 | Portability | Any OpenAI-compatible teacher or student endpoint works. Adapters are standard LoRA files the customer can download and run elsewhere. | Done (v0.1) |
| NFR-8 | Correctness | Release decisions use held-out data and statistical bounds, not single numbers. | Done (v0.1) |
| NFR-9 | Licensing | Code under Apache-2.0. Model licenses (OpenMDW-1.1, NVIDIA Nemotron Open Model License) travel with any distributed adapter. | Partial: teacher licenses travel in each adapter's BOM (v0.4); the base-model license does not yet |
| NFR-10 | Traceability | Every requirement in this PRD and every invariant in CLAUDE.md maps to the code that implements it and the tests that prove it, in the generated [REQUIREMENTS.md](REQUIREMENTS.md). CI fails on a status outside the grammar above, a done requirement without a test or a recorded exception, a test that cites an unknown or replaced id, or a stale matrix. | Done (v0.5) |

## 8. Success metrics

### 8.1 Product metrics

| ID | Metric | Target | Status |
|---|---|---|---|
| SM-1 | Cost reduction vs all-teacher, steady state | ≥ 70% | Not checked |
| SM-2 | Audited precision of student-served answers | ≥ 95% | Not checked |
| SM-3 | Student share of traffic, steady state | ≥ 80% | Not checked |
| SM-4 | Bad deploys reaching 100% traffic | 0 | Not checked |
| SM-5 | Time to recover from drift (escalations back to baseline) | ≤ 3 nights | Not checked |

`Not checked` means no test computes the metric yet. The simulation results below are a single run, not a check.

**Simulation results (mock models, not real model quality):** with seed 7, 2,000 tickets a day over 10 days, a product launch on day 5, and a corrupted deploy of v3:

- The student answered 98% of requests by day 3.
- Savings were 85–87% at steady state and 69% over the whole run, including the cold start.
- Audited precision ranged from 92% to 100%.
- The corrupted v3 was rolled back at 41.2% audited precision.
- Drift raised escalations to 14% on day 5; they were back to 2% by day 9.
- Across four other seeds, the final student share was 98% and final savings 85–87%.

### 8.2 Hackathon

| Metric | Target |
|---|---|
| Submission complete by Oct 29, 2026 | Yes |
| Live run on Token Factory + Nebius AI Cloud with real numbers in README and video | Yes |
| Demo URL live through Dec 15, 2026 | Yes |
| Placement | Track win or top 3 |

## 9. Release plan

| Release | Scope | Status |
|---|---|---|
| v0.1 (hackathon MVP) | Everything marked Done (v0.1) above; 34 tests passing; simulation and mock dashboard working | Built Oct 19, 2026 |
| v0.2.0 (demo layer) | Scout, pinned tasks, landing page, By task table, workloads, mock warm-up, Nebius VM deploy kit; 49 tests passing | Built Oct 20, 2026 |
| v0.3.0 (web app) | React story page, Scout page and dashboard; 49 tests passing | Built Oct 20, 2026 |
| v0.4.0 (hardening) | PII redaction, gateway API keys and quotas, streaming, signed BOM per adapter, `/healthz`; 64 tests passing | Built Oct 20, 2026 |
| v0.1 live | Day-1 checks; Nano 4B trained on a Serverless Job; vLLM endpoint; real numbers | Oct 20–28, 2026 |
| v0.5 | Postgres ledger, multiple tasks per deployment, NER-based redaction, per-key spend caps | After hackathon |
| v1.0 | Managed Ratchet Cloud, multi-tenant, savings-based billing, region choice (EU, India) | Startup track |

See [Roadmap](ROADMAP.md) for dates and exit criteria.

## 10. Dependencies

| Dependency | Use | Notes |
|---|---|---|
| Nebius Token Factory | Teacher (Nemotron 3 Ultra), judge (Super), fallback (Nano 30B) | OpenAI-compatible API; Builder Program gives $25 credit |
| Nebius Serverless Jobs | Nightly LoRA training | GPU billed per second |
| Nebius Serverless Endpoints | vLLM student serving | Stopped endpoints cost nothing |
| Nebius Object Storage | Datasets and adapters | S3-compatible |
| NVIDIA Nemotron 3 Nano 4B | Student base | NVIDIA Nemotron Open Model License |
| vLLM | Student serving, runtime LoRA | Nemotron-H LoRA support listed |
| Unsloth / TRL / PEFT | Training | Unsloth supports Nemotron 3 |
| FastAPI, httpx, SQLite | Gateway, client, ledger | |

## 11. Risks and mitigations

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| R1 | Token Factory cannot fine-tune or host a tuned Nemotron | High | Train on Serverless Jobs, serve on Serverless Endpoints with vLLM |
| R2 | Nano 30B needs about 60GB VRAM for LoRA | High | Use Nano 4B on L40S; 30B is a stretch goal |
| R3 | Small budget; no GPU credits in the Builder Program | High | Short training runs; stop endpoints when idle; ask for credits |
| R4 | Demo must stay live Dec 1–15 | Medium | Fallback to hosted Nano; mock-mode demo |
| R5 | Token Factory ToS 5(f) "competing service" clause | Medium | Email sent to tokenfactory-support@nebius.com on Oct 19; position as built on Nebius end to end |
| R6 | Small models are poorly calibrated | Medium | Combined confidence signal; per-version calibration; shadow; audits |
| R7 | Judge bias (all Nemotron) | Medium | Exact-match labels; reference-based judge only for free text |
| R8 | Forgetting and drift across retrains | Medium | Retrain from base on cumulative data; golden gate; champion vs challenger |
| R9 | PII in logs | Low for demo, high for startup | Synthetic data in demos; pattern-based redaction before storing (v0.4); Presidio for names and addresses planned |
| R10 | Cheap frontier tiers shrink savings to about 2x | Medium | Lead with quality, ownership, license safety, latency |
| R11 | Funded competitors (Orchestra/Understudy, Experiential Labs, Inference.net) | High for startup | License-clean wedge; release safety; open source first |

## 12. Open questions

| # | Question | Owner | How we close it |
|---|---|---|---|
| Q1 | Does Token Factory ToS 5(f) allow Ratchet? | Harsh | Email sent Oct 19, 2026; follow up in hackathon Discord if no reply in 2–3 days |
| Q2 | Do Nemotron models on Token Factory return logprobs? | Harsh | `scripts/day1_check.py` |
| Q3 | Does vLLM serve LoRA on Nemotron-H (Nano 4B) correctly, including runtime loading? | Harsh | Day-1 endpoint test; fallback: merge adapter |
| Q4 | What does Serverless GPU time actually cost? | Harsh | Nebius console on day 1 |
| Q5 | Are the exact `nebius ai` CLI flags in `deploy/` correct? | Harsh | `--help` on day 1 |
| Q6 | Which live task and dataset to show in the video? | Harsh | Default: synthetic ticket triage through the real teacher |

## 13. Glossary

See [Glossary](GLOSSARY.md).
