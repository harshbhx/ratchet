# Competitive analysis

As of 2026-10-19. Based on pages opened during research; anything unconfirmed is marked "unverified".

## Summary

Every piece of what Ratchet does exists somewhere: gateways, routers, one-off distillation jobs, fine-tuning APIs. A fully automatic, safely gated loop is rare. **No competitor sells license-clean distillation.**

The closest rivals are two-person YC Summer 2026 teams that launched between August and October 2026: Orchestra/Understudy and Experiential Labs. Inference.net has the pieces and $11.8M of funding, but you start each training run yourself. The two earlier independents, OpenPipe and Predibase, were both acquired in 2025.

## Competitor table

| Player | What they do | Fully automatic loop? | Customer owns weights? | Status, Oct 2026 |
|---|---|---|---|---|
| [Orchestra / Understudy](https://www.ycombinator.com/launches/SPb-understudy-the-self-optimizing-inference-cloud) | Swap one URL. Builds evals from traffic, trains specialist open models, routes once they match the frontier model | Yes, as claimed; no data shown | Yes ("your company owns") | YC S26, 2 people, launched Aug 5, 2026. Claims "100x" cost cut and "80% off an Anthropic bill" |
| [Experiential Labs](https://github.com/experientiallabs/experiential) | Apache-2.0, OpenAI-compatible gateway (~9.6k GitHub stars). Custom router from traffic; fine-tunes open models on demand | Partial: training is a manual CLI command; no eval or rollout steps shown | Yes, explicitly | YC S26, 2 people, free. Guarantees at least 50% lower cost with an SLA |
| [Inference.net](https://inference.net/blog/seed-round/) | Gateway logging, datasets from traffic, LLM-judge evals, training recipes, dedicated GPU serving | No: you start each run | Says yes; download unverified | $11.8M seed (Oct 2025). Targets customers spending $50K+/month |
| [OpenPipe](https://coreweave.com/news/coreweave-to-acquire-openpipe-leader-in-reinforcement-learning) | Logged traffic into fine-tunes; later RL (ART library) | No | Unverified | Acquired by CoreWeave (Sep 2025). Tech went into CoreWeave Serverless RL and Forge |
| [Predibase](https://techcrunch.com/2025/06/25/rubrik-acquires-predibase-to-accelerate-adoption-of-ai-agents/) | LoRA/RFT fine-tuning; LoRAX multi-adapter serving | No | VPC option | Acquired by Rubrik (Jun 2025) |
| [Proxis](https://www.ycombinator.com/companies/proxis) | Was a distillation and serving platform | n/a | n/a | Pivoted to email and sales agents |
| Lamini | Enterprise fine-tuning | No | n/a | Effectively shut down in 2025 |
| [Arcee AI](https://docs.arcee.ai/arcee-conductor) | Now builds its own open models; Conductor routes between models | No (routing only) | Own models are open | Series B, $1B+ valuation (Sep 2026) |
| [Fireworks AI](https://docs.fireworks.ai/fine-tuning/managed-finetuning-intro) | Managed SFT and DPO (LoRA); full tuning via Training API | No | Export unverified | Active |
| [Together AI](https://docs.together.ai/docs/fine-tuning/overview.md) | SFT and DPO; GRPO separately | No | Yes, download | Active |
| Unsloth | Open-source fine-tuning library and desktop app | No (a tool, not a service) | Yes (local) | Active |
| Distil Labs | Train a small model from a prompt plus a few examples | No (one-off) | Yes, download | Active |
| [AWS Bedrock distillation](https://aws.amazon.com/bedrock/model-distillation) | Teacher to student, can use invocation logs | Semi: you start each job | Not stated | GA since May 2025 |
| Google Gemini distillation | Gemini teacher to Gemini student | Semi | No | Early access |
| [OpenAI distillation](https://openai.com/index/api-model-distillation/) | Stored completions to fine-tuned GPT-4o mini | No | No (closed) | Self-serve fine-tuning ending: no new jobs after Jan 6, 2027 ([deprecations](https://developers.openai.com/api/docs/deprecations)) |
| Azure OpenAI distillation | Stored completions, "Distill" button | No | No | Stored completions retired Oct 15, 2026 |
| [Nebius Token Factory post-training](https://nebius.com/blog/posts/post-training-in-token-factory) | SFT (LoRA/full), downloadable checkpoints | No: "continuous optimization" is on the roadmap | Yes | Active; no Nemotron students |
| Routers: [Not Diamond](https://notdiamond.ai), [RouteLLM](https://lmsys.org/blog/2024-07-01-routellm/), OpenRouter Auto | Pick a model per request | No training | n/a | Active |
| Gateways: LiteLLM, Portkey, Helicone | Logging, routing, policy | No training | n/a | Portkey being acquired by Palo Alto Networks; Helicone acquired by Mintlify |
| taskdistill (open source, hobby) | Capture proxy, MLX LoRA, confidence cascade | No (scheduled retraining on roadmap) | Yes | 97.6% agreement at 23.7% escalation on Banking77 ([GitHub](https://github.com/B0yko/taskdistill)) |

## Where Ratchet wins

1. **License-clean by design.** OpenAI's [Business Terms 3.3(e)](https://openai.com/policies/business-terms/) and Anthropic's [Commercial Terms D.4(a)](https://www.anthropic.com/legal/commercial-terms) ban training competing models on their outputs. A tool that distills Claude or GPT traffic exposes its users. Ratchet refuses those teachers in code and keeps a per-example provenance ledger. Nemotron 3 Ultra's [OpenMDW-1.1](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Ultra-550B-A55B-BF16) license places no restrictions on outputs.
2. **A decision for every request.** The student answers each request; only that request escalates if the student is unsure. Orchestra routes per task once a specialist passes evals.
3. **Release safety.** Shadow mode, a golden-set gate with champion-vs-challenger rescoring, canary 10% → 50% → 100%, and rollback on a statistical bound. No competitor page documents all of these.
4. **A home for OpenAI fine-tuning migrants.** The Jan 6, 2027 cutoff creates a deadline-driven buyer.
5. **Native to Nebius and NVIDIA.** It fills the gap on Nebius's own "continuous optimization" roadmap. Nebius could be a partner or an acquirer.

## Where Ratchet is weaker today

- No live production data yet. Simulation results only.
- Single developer. Orchestra and Experiential Labs have two founders each and YC backing.
- Experiential Labs already has open-source traction (~9.6k stars).
- Per-token savings against the labs' cheapest tiers (Claude Haiku 5.5 at $0.10/$0.50 per million tokens) are only about 2x. The big savings come from moving traffic off top-tier models.

## Evidence numbers

| Source | Result |
|---|---|
| [FrugalGPT](https://arxiv.org/abs/2305.05176) | Matches GPT-4 at up to 98% lower cost |
| [RouteLLM](https://lmsys.org/blog/2024-07-01-routellm/) | Over 85% cost reduction on MT-Bench at 95% of GPT-4 quality |
| [Online Cascade Learning](https://arxiv.org/html/2402.04513v2) | Up to 90% lower inference cost |
| [Distilling Step-by-Step](https://arxiv.org/html/2305.02301v2) | A 770M T5 beats 540B PaLM few-shot |
| [AWS Bedrock distillation](https://aws.amazon.com/bedrock/model-distillation) | Up to 500% faster, 75% cheaper, under 2% accuracy loss (RAG) |
| [taskdistill](https://github.com/B0yko/taskdistill) | 97.6% teacher agreement at 23.7% escalation |
| Experiential Labs (claim) | At least 50% lower cost under SLA; up to 97% on some tasks |
| Understudy (claim, no data) | 80% off an Anthropic bill |

## Positioning statement

For AI teams paying frontier prices for narrow, high-volume tasks, Ratchet is a drop-in gateway that turns their traffic into a model they own. Unlike distillation tools that learn from closed models, Ratchet only learns from teachers whose licenses allow it, proves it with a provenance ledger, and never ships a model that has not beaten the one in production.
