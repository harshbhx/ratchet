# Ratchet documentation

Ratchet is license-clean distillation on open models: an OpenAI-compatible gateway that turns your traffic into a Nemotron adapter you own (LoRA on NVIDIA's open Nano base). Start with the [project README](../README.md) for a quick start. The only trained adapters so far are the two text-to-SQL adapters and one tool-calling adapter in [Real results](../README.md#results), trained on a borrowed GPU.

## Where to start

| You are | Read, in this order |
|---|---|
| **A judge, with five minutes** | [README](../README.md) (the results chart and the judges' path) → [the judges' page](https://ratchet.dualmindlab.tech/judges) → [VERIFY](VERIFY.md) to recompute any claim |
| **A developer who wants it running** | [README: Quick start](../README.md#use-it) → [API](API.md) → [CONFIGURATION](CONFIGURATION.md) |
| **An operator hosting it** | [WHERE_IT_RUNS](WHERE_IT_RUNS.md) → [DEPLOYMENT](DEPLOYMENT.md) → [LIVE_MODE](LIVE_MODE.md) → [RUNBOOK](RUNBOOK.md) |
| **Reviewing security or licenses** | [THREAT_MODEL](THREAT_MODEL.md) → [README: Why license-clean](../README.md#why-license-clean) → [ADR 0001](adr/0001-open-teacher-only.md) and [ADR 0090](adr/0090-licenses-travel-with-the-adapter.md) |
| **Contributing** | [../CONTRIBUTING.md](../CONTRIBUTING.md) → [ARCHITECTURE](ARCHITECTURE.md) → [TESTING](TESTING.md) |

## Understand it

| Doc | What it covers |
|---|---|
| [PRD.md](PRD.md) | Product requirements: problem, users, goals, scope, requirements, success metrics |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Components, request path, nightly loop, data model, version state machine, confidence math, cost accounting |
| [FEATURES.md](FEATURES.md) | The longer explanations: the reference simulation, economics, the safety rules, certificates, proofs of what an adapter was trained on, ledger replay, savings receipts, privacy |
| [SCOUT.md](SCOUT.md) | Scout, the multi-agent research demo: agents, routes, event contract, budget guard, mock research world |
| [GLOSSARY.md](GLOSSARY.md) | Terms: teacher, student, cascade, golden set, canary, coverage, precision… |
| [adr/](adr/README.md) | Architecture decision records: one file per decision, with context, the decision, consequences and alternatives |

## Use and integrate it

| Doc | What it covers |
|---|---|
| [API.md](API.md) | Every HTTP endpoint (pages, OpenAI API with keys and streaming, stats, adapter BOMs, redaction preview, demo controls, Scout) with schemas, errors, and curl and SDK examples |
| [CONFIGURATION.md](CONFIGURATION.md) | Every environment variable (incl. privacy and security), prices, release policy, tasks (distillable and pinned), licenses |

## Run and operate it

| Doc | What it covers |
|---|---|
| [WHERE_IT_RUNS.md](WHERE_IT_RUNS.md) | Where every part runs today (laptop, Cloudflare, Token Factory) and where it is designed to run on Nebius, and the one command that hosts it all there (scripted and tested offline, ready to run) |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Local mock mode and live mode on Nebius step by step, cost estimates, keeping the demo alive |
| [LIVE_MODE.md](LIVE_MODE.md) | Running against real Token Factory, the student endpoint and nightly jobs, measuring a live run |
| [DEPLOY_NEBIUS_VM.md](DEPLOY_NEBIUS_VM.md) | Hosting the public demo on one Nebius CPU VM: setup, mock to live, costs, monitoring, backups, security, teardown |
| [RUNBOOK.md](RUNBOOK.md) | Daily checks, event meanings, manual rollback and promotion, incident playbooks, backups |
| [THREAT_MODEL.md](THREAT_MODEL.md) | Security and privacy: assets, trust boundaries, threats, mitigations present vs planned |

## Check the evidence

| Doc or folder | What it covers |
|---|---|
| [VERIFY.md](VERIFY.md) | Check it yourself: one command per claim, the output to expect, and what none of it proves |
| [TESTING.md](TESTING.md) | Test suite, contract tests, live-path tests (live mode, CLIs and deploy scripts run offline), the traceability check, simulation method and assumptions, bugs found and fixed, seed robustness |
| [REQUIREMENTS.md](REQUIREMENTS.md) | Generated traceability matrix: every PRD requirement and invariant with its code, tests and how it is verified (`make trace`) |
| [../evidence/text2sql-round1](../evidence/text2sql-round1), [round2](../evidence/text2sql-round2), [exam2](../evidence/text2sql-exam2) | The measured text-to-SQL results: data, exams, every answer, scores and the scripts that recompute them |
| [../evidence/toolcall](../evidence/toolcall) | The measured tool-calling results (round 3): both exams, every saved answer, the exams' own scorers, `results.json` and the drafts rendered from it; `python scripts/toolcall_results.py --check` recomputes it all |
| [../evidence/live-smoke-2026-10-23](../evidence/live-smoke-2026-10-23) | A real run through the live gateway: 20 requests to Token Factory, about $0.01, every record published |
| [../evidence/scout](../evidence/scout) | Three recorded live Scout runs (real Token Factory and Tavily calls), replayed by the gateway |

## Business and research

| Doc | What it covers |
|---|---|
| [BUSINESS.md](BUSINESS.md) | Business model, pricing, go-to-market, sizing |
| [COMPETITIVE_ANALYSIS.md](COMPETITIVE_ANALYSIS.md) | Who else does distillation, routing and gateways, and where Ratchet differs, with dated sources |
| [ROADMAP.md](ROADMAP.md) | What ships when, from the hackathon to Ratchet Cloud |

## Images

`img/landing.jpg`, `img/scout.jpg`, `img/dashboard.jpg` and `img/trust.jpg` (the dashboard's Trust and privacy panel) are screenshots from the simulation (mock models). `img/toolcall-results.svg` is the tool-calling chart, drawn by `scripts/toolcall_results_svg.py` from `evidence/toolcall/results.json` (`--check` compares only). `img/results.svg` is the README's results chart, drawn from the exam data by `scripts/readme_results_svg.py`. `img/architecture.svg` and its two PNGs (`architecture-light.png`, `architecture-dark.png`, and a 1200 by 800 copy for Devpost) are the architecture diagram, drawn by `scripts/render_architecture.py`. `img/where-it-runs.svg` and its two PNGs (`where-it-runs-light.png`, `where-it-runs-dark.png`) are the map of where everything runs, drawn in the same style and rendered by the same script. `img/run-on-nebius.svg` is the README's text-only "Run on Nebius" badge. `img/anim/` holds the README's animations (`readme-banner.gif`, `readme-stats.gif`, `readme-runs.gif` and `readme-footer.gif` are the front page's banner, at-a-glance strip, where-it-runs map and footer; `ratchet-how-it-works-light.gif` is the animated walkthrough the README links under the architecture diagram, and `ratchet-demo-reel.gif`, `ratchet-terminal-demo.gif` and the other task gifs are the demo reel and its parts).

## Repo-level files

- [../AGENTS.md](../AGENTS.md): how to work in this repo (commands, invariants, conventions); each folder has its own AGENTS.md.
- [../CONTRIBUTING.md](../CONTRIBUTING.md), [../SECURITY.md](../SECURITY.md), [../CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md), [../CHANGELOG.md](../CHANGELOG.md).
