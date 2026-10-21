# Ratchet

**License-clean distillation on open models. Rent intelligence today, own it tomorrow.**

Ratchet is an OpenAI-compatible gateway. Change one line (your base URL) and:

1. A small model answers each request first: NVIDIA Nemotron 3 Nano 4B with a LoRA adapter **you own**, served by vLLM on a Nebius Serverless Endpoint.
2. If it is not confident, the request goes to the teacher, **NVIDIA Nemotron 3 Ultra on Nebius Token Factory**.
3. Every teacher answer is logged with its **provenance**: which teacher wrote it, and the license that permits training on it.
4. Every night, **Night School** fine-tunes a new student on a Nebius Serverless Job, evaluates it on a held-out golden set, and releases it safely: shadow mode, then canary 10% → 50% → 100%, with automatic rollback.

Your bill falls as your own model takes over, and Ratchet tells you when a GPU would cost more than it saves. You own the LoRA adapter and its signed training lineage; the Nano base stays under NVIDIA's Nemotron Open Model License.

![Ratchet: the story page](docs/img/landing.jpg)

The gateway serves a React web app ([web/](web/CLAUDE.md)):

- `/`: the story. How Ratchet works, why it is license-clean and why releases are safe, with an interactive replay of the reference simulation.
- `/scout`: the Scout multi-agent demo (below). Watch each call get answered by your model or by Ultra.
- `/dashboard`: savings, the release pipeline, economics (savings per token and by GPU-hours paid, break-even and a self-host recommendation), versions, per-task routing, the task registry (each task's comparators and spec, API tasks editable with an admin key), resilience (circuit breakers, fallbacks, recent failures and an outage drill), trust and privacy, release log and provenance, with demo controls in mock mode.

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

In its own words, OpenMDW-1.1 "does not impose any restrictions or obligations with respect to any use, modification, or sharing of any outputs" ([license page](https://openmdw.ai/license/1-1/), last updated May 27, 2026; checked Oct 21, 2026). What you own at the end is the LoRA adapter Night School trains and its signed bill of materials. The student's base, Nemotron 3 Nano 4B, stays under the [NVIDIA Nemotron Open Model License](https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-nemotron-open-model-license/) (v. Dec 15, 2025), which asks you to give anyone you distribute it to a copy of that license.

## How it uses NVIDIA and Nebius

| Role | Model / service | Why |
|---|---|---|
| Teacher | `nvidia/Nemotron-3-Ultra-550b-a55b` on **Token Factory** | Best reasoning; OpenMDW license allows distillation |
| Judge (free-text eval) | `nvidia/nemotron-3-super-120b-a12b` on **Token Factory** | Fast, cheap, JSON mode |
| Fallback student | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` on **Token Factory** | Used when your endpoint is stopped |
| Student | `nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16` + LoRA on a **Nebius Serverless Endpoint** (vLLM) | Cheap, fast; the adapter is yours |
| Night School | **Nebius Serverless Jobs** (L40S) with Unsloth | Pay only while training |
| Dataset / adapters | **Nebius Object Storage** bucket mounted in jobs and endpoints | |
| Public demo host | **Nebius AI Cloud** CPU VM (`2vcpu-8gb`) | The gateway does no inference |
| Scout web search | Tavily Search API (not NVIDIA or Nebius) | Search tool for the Scout demo only |

Token Factory does not offer fine-tuning or LoRA serving for Nemotron yet (Oct 2026), so the student is trained and served on Nebius AI Cloud.

## NVIDIA lineage

NVIDIA Research's paper "Small Language Models are the Future of Agentic AI" ([arXiv 2506.02153](https://arxiv.org/abs/2506.02153), v3, Sep 22, 2026) estimates that about 40%, 60% and 70% of the LLM queries in three open agent systems could be handled by specialized small models, and gives a six-step conversion algorithm. Ratchet automates all six:

| Step in the paper | In Ratchet |
|---|---|
| S1 "Secure usage data collection" | The gateway logs every request's metadata (task, route, confidence, cost); request text is kept only in training examples, redacted by default, and API keys only by name |
| S2 "Data curation and filtering" | PII redaction before storage; only complete teacher answers that are valid under the task's spec are kept |
| S3 "Task clustering" | The task registry: a typed field spec per task, pinned or distilled, with statistics per task |
| S4 "SLM selection" | Nemotron 3 Nano 4B, chosen for size, license and serving cost |
| S5 "Specialized SLM fine-tuning" | A LoRA fine-tune each night on a Nebius Serverless Job, from teacher answers only |
| S6 "Iteration and refinement" | Night School every night, gated by a held-out golden set (with an exact precision certificate in each signed BOM), shadow, canary and automatic rollback |

NVIDIA's [Data Flywheel Blueprint](https://developer.nvidia.com/blog/build-efficient-ai-agents-through-model-distillation-with-nvidias-data-flywheel-blueprint) (Jun 2025) also distils production traffic into smaller models, but its README says "Promotion to production [...] remains a human decision" ([repo](https://github.com/NVIDIA-AI-Blueprints/data-flywheel), marked deprecated in Apr 2026; checked Oct 21, 2026). Ratchet makes that decision with statistics on held-out rows and audited traffic, and writes each decision and its numbers to the release log.

## Quick start (no keys, 1 minute)

```bash
pip install -e ".[dev]"            # or: make install
python -m sim.simulate --days 10 --requests 2000 --bad-deploy-version 3   # full loop, mock models
RATCHET_MODE=mock uvicorn ratchet.gateway:app --port 8000                # http://localhost:8000 (/, /scout, /dashboard)
make web                                                                 # optional: rebuild the React UI (Node 22)
RATCHET_MODE=mock RATCHET_MOCK_WARMUP_DAYS=4 uvicorn ratchet.gateway:app --port 8000   # same, starts with a trained student
python -m ratchet.scout "What is LoRA fine-tuning?" --base-url http://localhost:8000/v1   # Scout as an outside client
pytest -q                                                                # 772 tests
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

With **unverified list prices** (an L40S at about $1.55/hour, about $0.001 of Ultra spend avoided per ticket-triage answer in the simulation) that is about 1,500 student answers an hour, roughly 36,000 a day. At the demo's 1,500 requests a day a 24/7 endpoint loses money, and the report says so and recommends the hosted fallback or scaling the endpoint to zero. At customer volume (10M requests a month, about 14,000 an hour) the endpoint beats the teacher by a wide margin, but per answer the untuned hosted Nano is cheaper still below roughly 52,000 student answers an hour, so the report says "Self-hosting beats the teacher, but the hosted fallback would cost less per answer" and recommends `hosted_fallback`, adding that this assumes the hosted model serves as many requests as the tuned student. One GPU serving many LoRA adapters raises the volume per GPU-hour ([ADR 0023](docs/adr/0023-utilization-aware-cost-accounting.md)).

## How the safety works

- **Confidence** = token probability (vLLM logprobs) + agreement across 3 samples + format validity. Small models are poorly calibrated, so no single signal is trusted.
- **Per-version threshold**, calibrated on half the golden set (target 97% precision) and measured on the other half.
- **Golden gate:** a candidate must reach 95% precision and not regress against the live model, which is re-scored on the same golden set the same night (champion vs challenger).
- **Shadow:** the candidate runs silently on real traffic. Disagreements with the live student are arbitrated by the teacher, and the arbitration becomes training data.
- **Canary 10% → 50% → 100%**, gated on teacher audits of what it serves. **Rollback** fires when the 95% upper bound of audited precision falls below 92%.
- **Training data:** only teacher answers, never the student's own (avoids model collapse). Retrains from the base model on the cumulative set each night (limits forgetting). Requests that repeat 3+ times always go to training, so frequent questions are never held out forever.
- **No training while a release is in flight**, which saves GPU money.

## What a certificate means

Every adapter's golden eval, and so its signed bill of materials, carries a **certificate** ([ADR 0026](docs/adr/0026-certified-thresholds-and-a-paired-release-gate.md)): of the held-out golden rows the threshold was *not* tuned on, how many the version would serve and how many of those agree with the teacher, and an exact one-sided Clopper–Pearson lower bound on its precision. "Certified at 95% (lcb 96.4%, delta 0.05)" means: with probability at least 95%, the answers this version serves at its threshold agree with the teacher at least 96.4% of the time. That is a statement about agreement with the teacher, not ground truth, and it assumes the golden rows look like live traffic (they come from escalations, audits and arbitrations, so they lean towards requests the student found hard). A point estimate such as "99.2% precision" on 130 rows cannot tell 0.96 from 0.99; the bound can.

- **Frontier:** the same bound at every threshold from 0.95 down to 0.50, corrected so all ten hold at once: the certified trade-off between precision and the share of traffic served.
- **Per task:** the counts per task, so a weak task cannot hide inside a pooled number (a diagnostic; it does not change routing).
- **Paired gate (opt-in, `RATCHET_PAIRED_GATE=1`):** the candidate and the live version are compared row by row on the same golden rows with exact McNemar tests, so a difference within noise no longer rejects a better model, and a significant regression still does. In the reference run (simulation) v2 was rejected for serving 70 of 74 rows against live's 74: four discordant rows, p = 0.0625.
- **Learn-then-Test (opt-in, `RATCHET_CALIBRATION=ltt`):** the threshold itself is chosen so its precision is certified, over a fixed grid of thresholds.

```bash
python -m ratchet.stats plan --target 0.95 --delta 0.05        # 0 errors: 59 rows, 1: 93, 2: 124, 3: 153
curl -s localhost:8000/ratchet/versions/2/certificate          # signed and latest certificate, assumptions
curl -s localhost:8000/ratchet/versions/2/bom > bom.json
python -m ratchet.stats check bom.json --public-key <yours>     # recompute every number from the signed counts
```

## Privacy and security

Built for real traffic, not only the demo ([threat model](docs/THREAT_MODEL.md)):

- **PII redaction before training.** Emails, phone numbers, card numbers (Luhn-checked), IBANs, Aadhaar, SSNs, PANs, IP addresses and API secrets are replaced with placeholders like `[EMAIL]` before a request becomes a training example, so an adapter cannot memorise them. The teacher still sees the original. On by default; names and addresses are not covered yet ([ADR 0009](docs/adr/0009-pattern-based-pii-redaction.md)).
- **Gateway API keys** (`RATCHET_API_KEYS=acme:<key>,...`) with a daily quota per key, and cost and savings per client: the basis for per-customer receipts.
- **Streaming** (`stream: true`) in the OpenAI chunk format, so streaming clients work unchanged.
- **Signed bill of materials per adapter** ([ADR 0010](docs/adr/0010-signed-adapter-bom.md)): teachers, licenses, dataset and golden-set hashes and the evaluation, written at training time.

```bash
python -m ratchet.bom keygen                       # signing key + public key to publish
RATCHET_API_KEYS="acme:$(openssl rand -hex 24)" RATCHET_BOM_SIGNING_KEY=... make demo
curl -s localhost:8000/ratchet/versions/2/bom | python -m ratchet.bom verify - --public-key <yours>
```

The dashboard's **Trust and privacy** panel shows all of it, including a box where you can paste any text and see what would be stored.

![Trust and privacy panel (simulation)](docs/img/trust.jpg)

## Prior art and what is new

We did not invent the pieces. Ratchet puts them in one loop that runs by itself. Sources and dates are in [docs/COMPETITIVE_ANALYSIS.md](docs/COMPETITIVE_ANALYSIS.md).

- **Table stakes:** an OpenAI-compatible gateway, routing, API keys and quotas ([LiteLLM](https://github.com/BerriAI/litellm), [Portkey](https://portkey.ai), [OpenRouter](https://openrouter.ai)).
- **Distillation services:** [OpenPipe](https://coreweave.com/news/coreweave-to-acquire-openpipe-leader-in-reinforcement-learning) (acquired by CoreWeave, Sep 2025), [AWS Bedrock Model Distillation](https://aws.amazon.com/bedrock/model-distillation), [OpenAI distillation](https://openai.com/index/api-model-distillation/) (no new fine-tuning jobs after Jan 6, 2027), [Inference.net](https://inference.net/blog/introducing-inference-platform/) (a managed training loop whose releases a person approves, Apr 14, 2026) and the YC Summer 2026 teams Understudy and Experiential Labs. Each turns traffic into a fine-tune; none we found records each example's teacher license at capture, releases automatically behind a statistical gate, and reports when the small model loses money.
- **Known ideas:** LLM cascades ([FrugalGPT](https://arxiv.org/abs/2305.05176), 2023) and cascades that distil the large model into the small one as traffic arrives ([Cache & Distil](https://arxiv.org/abs/2310.13561), ACL Findings 2024; [Online Cascade Learning](https://arxiv.org/abs/2402.04513), ICML 2024); distillation from production logs ([NVIDIA's Data Flywheel Blueprint](https://github.com/NVIDIA-AI-Blueprints/data-flywheel), 2025). License-aware teacher choice at request time: OpenRouter's [`enforce_distillable_text`](https://openrouter.ai/docs/guides/evaluate-and-optimize/distillation) restricts routing to models that allow distillation, with license data "on a best-effort basis", and NVIDIA's [license-compliant distillation tutorial](https://developer.nvidia.com/blog/how-to-build-license-compliant-synthetic-data-pipelines-for-ai-model-distillation/) (Feb 5, 2026) builds on it. Escalation with a provable agreement guarantee ([Trust or Escalate](https://arxiv.org/abs/2407.18370), ICLR 2025) and sequential tests for canary deploys ([Netflix](https://arxiv.org/abs/2205.14762), 2022).
- **New in Ratchet, together:** a per-request escalation, a nightly retrain and a gated release (golden gate, shadow, canary, rollback) in one loop; an exact Clopper–Pearson certificate in each signed BOM and an opt-in anytime-valid canary, both recomputable from their counts; each example's teacher and license recorded when it is captured and signed into every adapter's BOM; savings reported by GPU-hours actually paid, with the break-even volume and a recommendation. On Nemotron and Nebius.
- **Not built yet:** committed training sets ([PRD FR-16.4](docs/PRD.md#616-verifiable-evidence)), Merkle proofs of what an adapter was and was not trained on. The Merkle trees exist (`ratchet/merkle.py`); the BOM does not commit to them yet.

## Limitations

What breaks first:

- **Low volume does not pay.** Below break-even, a GPU endpoint running 24/7 costs more than the teacher spend it saves. The cost report says so and recommends the hosted fallback or scaling the endpoint to zero.
- **Without a GPU endpoint the student is a hosted, untuned model.** With `RATCHET_STUDENT_BASE` unset, a trained version's calls go to Nemotron 3 Nano 30B on Token Factory, and they are still reported as `served_by: student`. The response's `model` field names the model that wrote the answer.
- **Precision means agreement with Nemotron 3 Ultra,** not ground truth, and it assumes the golden rows look like live traffic. They come from escalations, audits and arbitrations, so they lean towards requests the student found hard.
- **The redactor is pattern-based.** Names and street addresses are not caught yet.
- **A signed BOM is the operator's signed statement** of the data and evaluation behind an adapter. It shows that nothing changed after signing, not that the weights were trained on that data.
- **One gateway process.** The ledger is one SQLite file behind one lock; a Postgres ledger for several workers is planned ([roadmap](docs/ROADMAP.md)).
- **Simulation numbers come from mock models.** They show how the loop behaves, not real model quality. As of Oct 21, 2026, Ratchet has not run on Nebius yet.
- **Token Factory's Terms of Service §5(f)** ("competing service") is broad. We asked Nebius support about it in writing on Oct 19, 2026; until the answer is final, treat the question as open. Not legal advice.

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
ratchet/release.py      golden gate, shadow, canary, rollback (Wilson bounds, or an anytime-valid e-process)
ratchet/certificate.py  certified precision: Clopper-Pearson certificates, frontier, paired McNemar comparison
ratchet/stats.py        exact statistics, `python -m ratchet.stats plan|bound|mcnemar|check`
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
