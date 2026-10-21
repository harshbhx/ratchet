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
| Night School | **Nebius Serverless Jobs** (L40S) with TRL + PEFT | Pay only while training |
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
pytest -q                                                                # 845 tests
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
bash deploy/student_endpoint.sh create          # once (DRY_RUN=1 first); later: stop when idle, start to resume
export RATCHET_STUDENT_BASE=https://<endpoint>/v1

RATCHET_MODE=live uvicorn ratchet.gateway:app --port 8000   # point your app at http://<host>:8000/v1
python -m ratchet.live night                                # nightly (cron)
python -m ratchet.live tick                                 # every few minutes (cron)
python -m ratchet.live seal                                 # daily (cron): seal the savings receipts of days that ended
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

## Proving what an adapter was trained on

Every adapter's signed bill of materials also commits to its exact training and golden sets ([ADR 0027](docs/adr/0027-committed-training-sets.md)): one sparse Merkle root per set, over each example's hash mapped to a hash of its label, teacher, license and redaction count. From the BOM alone, anyone can then check a short proof that one request was, or was not, in an adapter's training set, and a held-out certificate that **no golden example was trained on**: invariants 3 and 6 as something you verify, not something you are told. The proofs show the operator's signed commitment, at training time, to the dataset; they do not show that the weights came from it. With guessable texts the proof endpoint would tell anyone what was trained on, so it needs an admin key.

```bash
curl -s localhost:8000/ratchet/versions/2/bom > bom.json
curl -s "localhost:8000/ratchet/demo/sample-example?version=2" > ex.json                    # mock demo: a committed prompt
curl -s "localhost:8000/ratchet/versions/2/proof?split=train" -H 'content-type: application/json' -d @ex.json > proof.json
python -m ratchet.proof verify proof.json --bom bom.json --expect included
curl -s localhost:8000/ratchet/versions/2/heldout > heldout.json
python -m ratchet.proof heldout heldout.json --bom bom.json   # OK: all N golden examples provably absent from the training set
```

## Replaying past traffic at another threshold

Ratchet designs its own labelling, so it knows the chance of every teacher label it sees: 1 for a request the student escalated, the audit rate for an answer it served. Each request logs that probability, and the **ledger replay** ([ADR 0028](docs/adr/0028-designed-propensity-replay.md)) uses it to answer "what would last week's precision, coverage and cost have been at threshold t, or at audit rate a?" for one version, including thresholds below the live one, where audits alone see nothing. Coverage is exact; precision is a weighted estimate with an approximate interval (null when nothing labels the answers it would count); the frontier's suggested threshold is advice, never applied: thresholds change only through Night School. The replay also measures how far the baseline's 300-reasoning-token assumption is from what the teacher really costs. It adds no model call and no random draw to the request path.

```bash
curl -s "localhost:8000/ratchet/replay?threshold=0.4&audit_rate=0.05"     # what happened, and what would have
curl -s "localhost:8000/ratchet/replay/frontier?target=0.97"               # thresholds 0.30 to 1.00, an advisory suggestion
python -m sim.simulate --db sim.db && python -m ratchet.replay --db sim.db   # the same from a simulated run (simulation)
```

## Savings you can check

Ratchet is priced on savings, so the savings must be checkable by the customer, not just reported. Each day gets a **savings receipt** for the gateway and one per API client ([ADR 0029](docs/adr/0029-verified-savings-receipts.md)). The baseline of an answer the student served is calibrated on the audited answers' real teacher bills (a ratio estimator with a confidence bound), so a wrong reasoning-token assumption is measured and corrected, and the fee is a share of the one-sided 95% **lower bound** of the savings. Each receipt is an RFC 9162 Merkle tree over the day's request metadata (no text), signed and chained to the client's previous one: any request id a client got in `X-Ratchet-Request-Id` can be proven to be in it, and an edit after sealing shows. Receipts cannot prove the costs written at request time; reconcile them with the Token Factory bill.

```bash
curl -s localhost:8000/ratchet/receipts/3 > r3.json                            # one day's receipt (your own, with an API key)
curl -s localhost:8000/ratchet/receipts/3/proof/3006 > p.json                  # a request id you saw is in it
python -m ratchet.receipt verify r3.json --public-key <yours> --proof p.json  # OK: signature, OK: proof
python -m sim.simulate --receipts --teacher-reasoning-mean 420                 # the bound next to the true cost (simulation)
```

## Privacy and security

Built for real traffic, not only the demo ([threat model](docs/THREAT_MODEL.md)):

- **PII redaction before training.** Emails, phone numbers, card numbers (Luhn-checked), IBANs, Aadhaar, SSNs, PANs, IP addresses and API secrets are replaced with placeholders like `[EMAIL]` before a request becomes a training example, so an adapter cannot memorise them. The teacher still sees the original. On by default; names and addresses are not covered yet ([ADR 0009](docs/adr/0009-pattern-based-pii-redaction.md)).
- **Gateway API keys** (`RATCHET_API_KEYS=acme:<key>,...`) with a daily quota per key, cost and savings per client, and each client's own signed savings receipts ([above](#savings-you-can-check)).
- **Streaming** (`stream: true`) in the OpenAI chunk format, so streaming clients work unchanged.
- **Signed bill of materials per adapter** ([ADR 0010](docs/adr/0010-signed-adapter-bom.md)): teachers, licenses, dataset and golden-set hashes, the evaluation and, since v0.5, a set commitment per split ([above](#proving-what-an-adapter-was-trained-on)), written at training time; in live mode a copy goes into the adapter's folder in the bucket.

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
ratchet/release.py      golden gate, shadow, canary, rollback (Wilson bounds, or an anytime-valid e-process)
ratchet/certificate.py  certified precision: Clopper-Pearson certificates, frontier, paired McNemar comparison
ratchet/stats.py        exact statistics, `python -m ratchet.stats plan|bound|mcnemar|check`
ratchet/ledger.py       SQLite: requests, provenance, versions, events
ratchet/licenses.py     teacher license registry; closed teachers refused
ratchet/redact.py       PII placeholders before examples are stored
ratchet/auth.py         gateway API keys (named, per-key quota in gateway.py)
ratchet/bom.py          per-adapter bill of materials, Ed25519 signing, `python -m ratchet.bom`
ratchet/proof.py        committed training sets: membership and held-out proofs, `python -m ratchet.proof`
ratchet/replay.py       ledger replay: past traffic at another threshold or audit rate, `python -m ratchet.replay`
ratchet/receipts.py     verified savings receipts: calibrated baseline, lower-bound billing, `python -m ratchet.receipt`
ratchet/nightschool/    dataset build, LoRA training (TRL + PEFT), golden eval, nightly orchestration
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
