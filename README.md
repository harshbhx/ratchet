# Ratchet

**License-clean distillation on open models. Rent intelligence today, own it tomorrow.**

Ratchet is an OpenAI-compatible gateway. Change one line (your base URL) and:

1. A small model **you own** answers each request first (NVIDIA Nemotron 3 Nano 4B + LoRA, served by vLLM on a Nebius Serverless Endpoint).
2. If it is not confident, the request goes to the teacher, **NVIDIA Nemotron 3 Ultra on Nebius Token Factory**.
3. Every teacher answer is logged with its **provenance**: which teacher wrote it, and the license that permits training on it.
4. Every night, **Night School** fine-tunes a new student on a Nebius Serverless Job, evaluates it on a held-out golden set, and releases it safely: shadow mode, then canary 10% → 50% → 100%, with automatic rollback.

Your bill only goes down, and at the end you own the model weights.

![Ratchet: the story page](docs/img/landing.jpg)

The gateway serves a React web app ([web/](web/CLAUDE.md)):

- `/`: the story. How Ratchet works, why it is license-clean and why releases are safe, with an interactive replay of the reference simulation.
- `/scout`: the Scout multi-agent demo (below). Watch each call get answered by your model or by Ultra.
- `/dashboard`: savings, the release pipeline, versions, per-task routing, trust and privacy, release log and provenance, with demo controls in mock mode.

The original no-build pages stay at `/lite`, `/lite/scout` and `/lite/dashboard`.

| Scout | Dashboard |
|---|---|
| ![Scout run (simulation)](docs/img/scout.jpg) | ![Dashboard (simulation)](docs/img/dashboard.jpg) |

## Scout: Ratchet on agent traffic

[Scout](docs/SCOUT.md) is a small multi-agent research demo. It answers a question from the web, and every one of its LLM calls goes through Ratchet with a task name:

| Agent | Task | Route |
|---|---|---|
| Planner | `research-plan` | **Pinned** to Nemotron 3 Ultra (open-ended) |
| Searcher | none (a tool) | [Tavily Search API](https://docs.tavily.com) in live mode, simulated results in mock mode |
| Triage | `source-triage` | **Distilled**: one short labelling call per search result |
| Reader | `page-read` | **Distilled**: one call per relevant result, pulls out the key fact |
| Writer | `research-write` | **Pinned** to Nemotron 3 Ultra (open-ended) |

Pinned tasks always go to the teacher and are never stored as training data. The many narrow triage and reader calls are where the student takes over. A public demo has a budget guard: a daily spend cap, a daily run cap and a per-IP hourly limit.

## Why "license-clean"

OpenAI's [Business Terms §3.3(e)](https://openai.com/policies/business-terms/) and Anthropic's [Commercial Terms §D.4(a)](https://www.anthropic.com/legal/commercial-terms) forbid using their outputs to train competing models. A distillation tool that learns from GPT or Claude traffic puts its users at risk.

Ratchet only distills from teachers whose licenses allow it. Nemotron 3 Ultra is under [OpenMDW-1.1](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Ultra-550B-A55B-BF16), which places no restrictions on outputs. Closed models are refused as teachers in code (`ratchet/licenses.py`). `GET /ratchet/bom` exports a **model bill of materials**: every training source, its license, and a SHA-256 fingerprint of the dataset. Each trained adapter also gets **its own bill of materials, signed with Ed25519**, that anyone with your public key can check offline (`python -m ratchet.bom verify`). *Not legal advice: check licenses yourself.*

## How it uses NVIDIA and Nebius

| Role | Model / service | Why |
|---|---|---|
| Teacher | `nvidia/Nemotron-3-Ultra-550b-a55b` on **Token Factory** | Best reasoning; OpenMDW license allows distillation |
| Judge (free-text eval) | `nvidia/nemotron-3-super-120b-a12b` on **Token Factory** | Fast, cheap, JSON mode |
| Fallback student | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` on **Token Factory** | Used when your endpoint is stopped |
| Student | `nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16` + LoRA on a **Nebius Serverless Endpoint** (vLLM) | Cheap, fast, yours |
| Night School | **Nebius Serverless Jobs** (L40S) with Unsloth | Pay only while training |
| Dataset / adapters | **Nebius Object Storage** bucket mounted in jobs and endpoints | |
| Public demo host | **Nebius AI Cloud** CPU VM (`2vcpu-8gb`) | The gateway does no inference |
| Scout web search | Tavily Search API (not NVIDIA or Nebius) | Search tool for the Scout demo only |

Token Factory does not offer fine-tuning or LoRA serving for Nemotron yet (Oct 2026), so the student is trained and served on Nebius AI Cloud.

## Quick start (no keys, 1 minute)

```bash
pip install -e ".[dev]"            # or: make install
python -m sim.simulate --days 10 --requests 2000 --bad-deploy-version 3   # full loop, mock models
RATCHET_MODE=mock uvicorn ratchet.gateway:app --port 8000                # http://localhost:8000 (/, /scout, /dashboard)
make web                                                                 # optional: rebuild the React UI (Node 22)
RATCHET_MODE=mock RATCHET_MOCK_WARMUP_DAYS=4 uvicorn ratchet.gateway:app --port 8000   # same, starts with a trained student
python -m ratchet.scout "What is LoRA fine-tuning?" --base-url http://localhost:8000/v1   # Scout as an outside client
pytest -q                                                                # 767 tests
```

`RATCHET_MOCK_WARMUP_DAYS=4` runs four simulated days and nights at startup (about 2 s), so the dashboard and Scout open on a student that is already live. The Scout CLI also takes `--api-key` and `--max-results` (default 4). Without `TAVILY_API_KEY` it uses simulated search results, which only cover the example questions and only work against a mock-mode gateway.

The simulation uses **mock models**: it shows how the loop behaves (cold start, drift, rollback), not real model quality. Typical run (seed 7, 2,000 tickets/day, product launch on day 5, corrupted deploy of v3):

```
day  student  escal. audit ok     $/1k all-Ultra  saved
  1       0%      0%        -    1.093     1.093     0%     <- cold start: all teacher
  3      98%      2%     100%    0.160     1.093    85%     <- first student live
  4      98%      2%      93%    0.160     1.093    85%     <- v3 deployed corrupted -> rolled back automatically
  5      86%     14%      97%    0.272     1.093    75%     <- product launch: new questions escalate
  9      98%      2%      97%    0.162     1.093    85%     <- learned overnight
 10      98%      2%      99%    0.147     1.093    87%
```

`--workload research` sends Scout's triage and reader calls instead of tickets, and `--workload mixed` sends half of each. Simulation, mock models, seed 7, 8 days × 1,500 requests: the student answered 96% of research calls on day 8 (85% saved that day, 63% over all 8 days), and 98% of mixed calls (86% saved that day, 54% overall). See [docs/TESTING.md](docs/TESTING.md#workloads-research-and-mixed).

## Live mode (real Token Factory + Nebius)

```bash
export NEBIUS_API_KEY=...                       # Token Factory key
python scripts/day1_check.py                    # model IDs, logprobs, JSON mode

# Student endpoint (vLLM + runtime LoRA loading) and training job
export RATCHET_BUCKET_ID=... RATCHET_STUDENT_KEY=... RATCHET_TRAIN_IMAGE=...
bash deploy/student_endpoint.sh
export RATCHET_STUDENT_BASE=https://<endpoint>/v1

RATCHET_MODE=live uvicorn ratchet.gateway:app --port 8000   # point your app at http://<host>:8000/v1
python -m ratchet.live night                                # nightly (cron)
python -m ratchet.live tick                                 # every few minutes (cron)
```

Use it from any OpenAI SDK:

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="unused")
r = client.chat.completions.create(model="ratchet", messages=[...], extra_headers={"X-Ratchet-Task": "ticket-triage"})
print(r.ratchet)  # served_by, confidence, cost_usd, all_teacher_cost_usd, usage_breakdown ...
print(r.usage.total_tokens, r.system_fingerprint)  # the served answer's tokens; the fingerprint changes with the version
```

See [docs/DAY1.md](docs/DAY1.md) for the checks to run before relying on live mode.

## Hosting the demo

The public demo runs on one small Nebius CPU VM with Docker Compose and Caddy (automatic HTTPS). The gateway does no inference, so 2 vCPU / 8 GB is enough. Scripts are in `deploy/vm/`; the guide, with costs (estimate: about $72 for Oct 29 – Dec 15, 2026, before live-mode usage), security notes and teardown, is [docs/DEPLOY_NEBIUS_VM.md](docs/DEPLOY_NEBIUS_VM.md).

## How the economics work

Per token, a student answer costs a fraction of a teacher answer. But the self-hosted student runs on a GPU endpoint that bills by the hour whether requests arrive or not, so Ratchet also reports savings by the GPU-hours actually paid (`python -m ratchet.cost report`, `GET /ratchet/cost`), and says when self-hosting does not pay:

```
break-even student answers per endpoint hour = GPU price per hour ÷ teacher spend avoided per student answer
```

With **unverified list prices** (an L40S at about $1.55/hour, about $0.001 of Ultra spend avoided per ticket-triage answer in the simulation) that is about 1,500 student answers an hour, roughly 36,000 a day. At the demo's 1,500 requests a day a 24/7 endpoint loses money, and the report says so and recommends the hosted fallback or scaling the endpoint to zero; at customer volume (10M requests a month) self-hosting pays. One GPU serving many LoRA adapters raises the volume per GPU-hour ([ADR 0023](docs/adr/0023-utilization-aware-cost-accounting.md)).

## How the safety works

- **Confidence** = token probability (vLLM logprobs) + agreement across 3 samples + format validity. Small models are poorly calibrated, so no single signal is trusted.
- **Per-version threshold**, calibrated on half the golden set (target 97% precision) and measured on the other half.
- **Golden gate:** a candidate must reach 95% precision and not regress against the live model, which is re-scored on the same golden set the same night (champion vs challenger).
- **Shadow:** the candidate runs silently on real traffic. Disagreements with the live student are arbitrated by the teacher, and the arbitration becomes training data.
- **Canary 10% → 50% → 100%**, gated on teacher audits of what it serves. **Rollback** fires when the 95% upper bound of audited precision falls below 92%.
- **Training data:** only teacher answers, never the student's own (avoids model collapse). Retrains from the base model on the cumulative set each night (limits forgetting). Requests that repeat 3+ times always go to training, so frequent questions are never held out forever.
- **No training while a release is in flight**, which saves GPU money.

## Privacy and security

Built for real traffic, not only the demo ([threat model](docs/THREAT_MODEL.md)):

- **PII redaction before training.** Emails, phone numbers, card numbers (Luhn-checked), IBANs, Aadhaar, SSNs, PANs, IP addresses and API secrets are replaced with placeholders like `[EMAIL]` before a request becomes a training example, so an adapter cannot memorise them. The teacher still sees the original. On by default; names and addresses are not covered yet ([ADR 0009](docs/adr/0009-pattern-based-pii-redaction.md)).
- **Gateway API keys** (`RATCHET_API_KEYS=acme:<key>,...`) with a daily quota per key, and cost and savings per client: the basis for per-customer receipts.
- **Private by default in live mode**: every route has an access level; the dashboard API needs an admin or read-only dashboard key unless you make it public, and then anonymous readers see client pseudonyms, not names. Live mode does not start without API keys, `/docs` is off, and every response carries `nosniff` and `no-referrer` ([ADR 0061](docs/adr/0061-endpoint-access-levels.md)).
- **Streaming** (`stream: true`) in the OpenAI chunk format, so streaming clients work unchanged.
- **Request ids, failures, logs and metrics** ([ADR 0062](docs/adr/0062-request-ids-failures-logs-and-metrics.md)): every response carries `x-request-id`, which also names the request's ledger row, its log line and its failures; failed requests are recorded with what they spent; live mode logs one JSON line per request with no prompt or answer text, and `/metrics` serves Prometheus metrics.
- **Signed bill of materials per adapter** ([ADR 0010](docs/adr/0010-signed-adapter-bom.md)): teachers, licenses, dataset and golden-set hashes and the evaluation, written at training time.

```bash
python -m ratchet.bom keygen                       # signing key + public key to publish
RATCHET_API_KEYS="acme:$(openssl rand -hex 24)" RATCHET_BOM_SIGNING_KEY=... make demo
curl -s localhost:8000/ratchet/versions/2/bom | python -m ratchet.bom verify - --public-key <yours>
```

The dashboard's **Trust and privacy** panel shows all of it, including a box where you can paste any text and see what would be stored.

![Trust and privacy panel (simulation)](docs/img/trust.jpg)

## Documentation

Start at [docs/README.md](docs/README.md). Highlights:

- [PRD](docs/PRD.md): problem, users, requirements, metrics, release plan
- [Architecture](docs/ARCHITECTURE.md), [API](docs/API.md), [Configuration](docs/CONFIGURATION.md), [Scout](docs/SCOUT.md)
- [Deployment on Nebius](docs/DEPLOYMENT.md), [Demo VM on Nebius](docs/DEPLOY_NEBIUS_VM.md), [Runbook](docs/RUNBOOK.md), [Threat model](docs/THREAT_MODEL.md)
- [Testing and simulation](docs/TESTING.md), [Architecture decisions](docs/adr/README.md)
- [Business](docs/BUSINESS.md), [Competitive analysis](docs/COMPETITIVE_ANALYSIS.md), [Roadmap](docs/ROADMAP.md)
- [Devpost submission draft](docs/HACKATHON_SUBMISSION.md), [Demo script](docs/DEMO_SCRIPT.md), [Day-1 checklist](docs/DAY1.md)

Working with Claude? Read [CLAUDE.md](CLAUDE.md). Contributing: [CONTRIBUTING.md](CONTRIBUTING.md).

## Repo map

```
ratchet/gateway.py      OpenAI-compatible API (keys, quotas, streaming), pages, demo controls, Scout endpoints
ratchet/router.py       the cascade, pinned tasks, audits, shadow arbitration, cost accounting
ratchet/tasks.py        task specs: typed fields, valid answers, answer cleaning, built-in tasks
ratchet/taskspec.py     task specs as data: RATCHET_TASKS_FILE and the admin API, every error with its path
ratchet/compare.py      field comparators (label, number, date, set, records, ...)
ratchet/confidence.py   confidence score (pairwise agreement) + threshold calibration
ratchet/release.py      golden gate, shadow, canary, rollback (Wilson bounds)
ratchet/ledger.py       SQLite: requests, provenance, versions, events
ratchet/licenses.py     teacher license registry; closed teachers refused
ratchet/redact.py       PII placeholders before examples are stored
ratchet/auth.py         gateway API keys (named, per-key quota in gateway.py)
ratchet/bom.py          per-adapter bill of materials, Ed25519 signing, `python -m ratchet.bom`
ratchet/nightschool/    dataset build, LoRA training (Unsloth), golden eval, nightly orchestration
ratchet/live.py         live Night School on Nebius (job -> hot-load adapter -> eval -> gate)
ratchet/scout/          Scout agents, prompts, Tavily/mock search, budget guard, CLI
web/                    React app: story, Scout, dashboard (Vite, Tailwind, motion, recharts)
ratchet/web/dist/       committed build of web/, served by the gateway
ratchet/web/*.html      lite pages without a build step (/lite*)
deploy/                 Serverless Job / Endpoint scripts, training Dockerfile
deploy/vm/              demo VM on Nebius: cloud-init, Docker Compose, Caddy, update and backup scripts
sim/                    synthetic tickets + mock models for demos and tests
sim/research.py         mock research world for Scout (8 questions, simulated search results)
```

## License

Apache-2.0 for the code. Model weights are under their own licenses: Nemotron Nano under the [NVIDIA Nemotron Open Model License](https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-nemotron-open-model-license/), Nemotron 3 Ultra under OpenMDW-1.1. Include those licenses if you distribute adapters.
