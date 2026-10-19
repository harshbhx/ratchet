# Ratchet documentation

Ratchet is license-clean distillation on open models: an OpenAI-compatible gateway that turns your traffic into a cheaper model you own. Start with the [project README](../README.md) for a quick start.

## Product

| Doc | What it covers |
|---|---|
| [PRD.md](PRD.md) | Product requirements: problem, users, goals, scope, requirements, success metrics |
| [RESEARCH_SUMMARY.md](RESEARCH_SUMMARY.md) | Market, competitor and feasibility research, with sources |
| [COMPETITIVE_ANALYSIS.md](COMPETITIVE_ANALYSIS.md) | Who else does distillation, routing and gateways, and where Ratchet wins |
| [BUSINESS.md](BUSINESS.md) | Business model, pricing, go-to-market, sizing |
| [ROADMAP.md](ROADMAP.md) | What ships when, from the hackathon to Ratchet Cloud |
| [HACKATHON_SUBMISSION.md](HACKATHON_SUBMISSION.md) | Devpost submission text for the Nebius x NVIDIA Global AI Hackathon |
| [GLOSSARY.md](GLOSSARY.md) | Terms: teacher, student, cascade, golden set, canary, coverage, precision… |

## Technical

| Doc | What it covers |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Components, request path, nightly loop, data model, version state machine, confidence math, cost accounting |
| [API.md](API.md) | Every HTTP endpoint (pages, OpenAI API, stats, demo controls, Scout) with schemas, errors, and curl and SDK examples |
| [CONFIGURATION.md](CONFIGURATION.md) | Every environment variable, prices, release policy, tasks (distillable and pinned), licenses |
| [SCOUT.md](SCOUT.md) | Scout, the multi-agent research demo: agents, routes, event contract, budget guard, mock research world |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Local mock mode and live mode on Nebius step by step, cost estimates, keeping the demo alive |
| [DEPLOY_NEBIUS_VM.md](DEPLOY_NEBIUS_VM.md) | Hosting the public demo on one Nebius CPU VM: setup, mock to live, costs, monitoring, backups, security, teardown |
| [RUNBOOK.md](RUNBOOK.md) | Daily checks, event meanings, manual rollback and promotion, incident playbooks, backups |
| [THREAT_MODEL.md](THREAT_MODEL.md) | Security and privacy: assets, trust boundaries, threats, mitigations present vs planned |
| [TESTING.md](TESTING.md) | Test suite, simulation method and assumptions, bugs found and fixed, seed robustness, known gaps |
| [adr/](adr/README.md) | Architecture Decision Records 0001–0007 |

## Hackathon working docs

| Doc | What it covers |
|---|---|
| [DAY1.md](DAY1.md) | Day-1 checklist: the risky Nebius and Token Factory pieces to verify first, with fallbacks |
| [DEMO_SCRIPT.md](DEMO_SCRIPT.md) | The 3-minute demo video, shot by shot |

## Images

`img/dashboard.jpg` is a dashboard screenshot from the simulation (mock models).

## Repo-level files

- [../CLAUDE.md](../CLAUDE.md): how to work in this repo (commands, invariants, conventions); each folder has its own CLAUDE.md.
- [../CONTRIBUTING.md](../CONTRIBUTING.md), [../SECURITY.md](../SECURITY.md), [../CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md), [../CHANGELOG.md](../CHANGELOG.md).
