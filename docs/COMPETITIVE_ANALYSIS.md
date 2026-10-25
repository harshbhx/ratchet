# Competitive analysis

As of Oct 25, 2026 (rows without their own date were last read Oct 21, 2026). Based on pages opened during research. A fact re-read on a primary page says "checked" with its date; a fact taken from the research notes without a fresh check says "(research notes, <date>)" until someone re-checks it; anything nobody could confirm is marked "unverified".

## Summary

Every piece of what Ratchet does exists somewhere: gateways, routers, one-off distillation jobs, fine-tuning APIs, and managed training loops that stage each new model for review. A fully automatic loop with a statistical release gate is rare. OpenRouter filters for distillable teachers at request time; no gateway we found records each example's teacher and license when it is captured and signs that lineage into every trained adapter.

The closest rivals are Inference.net, whose platform (Apr 14, 2026) retrains on production data, benchmarks each candidate against the live model and stages new models "for final review and deployment", and two YC Summer 2026 teams of two that launched between August and October 2026: Orchestra/Understudy and Experiential Labs. The two earlier independents, OpenPipe and Predibase, were both acquired in 2025. NVIDIA has published the pattern itself, as a reference blueprint (Jun 2025, deprecated Apr 2026) and a license-compliant distillation tutorial (Feb 5, 2026).

## Competitor table

| Player | What they do | Fully automatic loop? | Customer owns weights? | Status, Oct 2026 |
|---|---|---|---|---|
| [Orchestra / Understudy](https://www.ycombinator.com/launches/SPb-understudy-the-self-optimizing-inference-cloud) | Swap one URL. Builds evals from traffic, trains specialist open models, routes once they match the frontier model | Yes, as claimed; no data shown | Yes ("your company owns") | YC S26, 2 people, launched Aug 5, 2026. Claims "100x" cost cut and "80% off an Anthropic bill" |
| [Experiential Labs](https://github.com/experientiallabs/experiential) | Apache-2.0, OpenAI-compatible gateway (about 10k GitHub stars, checked Oct 21, 2026; about 9.9k in the research notes the same day). Custom router from traffic; fine-tunes open models on demand | Partial: training is a manual CLI command; no eval or rollout steps shown | Yes, explicitly ("a specialized model you own", checked Oct 21, 2026) | YC S26, 2 people, free |
| [Inference.net](https://inference.net/blog/seed-round/) | Gateway logging, datasets from traffic, LLM-judge evals, training, dedicated GPU serving. Fine-tunes NVIDIA Nemotron Nano 2 models with NeMo Megatron-Bridge ([blog, Mar 11, 2026](https://inference.net/blog/nemotron-finetuning/), checked Oct 21, 2026) | Partial: the platform runs a continuous training loop on production data and benchmarks each candidate against the live model; "When new models are ready, they're staged for final review and deployment"; no canary or rollback described ([launch post, Apr 14, 2026](https://inference.net/blog/introducing-inference-platform/), checked Oct 21, 2026) | Says yes ("The model weights are fully owned by you"); download not stated | $11.8M seed (Oct 2025). Targets customers spending $50K+/month |
| [OpenPipe](https://coreweave.com/news/coreweave-to-acquire-openpipe-leader-in-reinforcement-learning) | Logged traffic into fine-tunes; later RL (ART library) | No | Unverified | Acquired by CoreWeave (Sep 2025). Tech went into CoreWeave Serverless RL and Forge |
| [Predibase](https://techcrunch.com/2025/06/25/rubrik-acquires-predibase-to-accelerate-adoption-of-ai-agents/) | LoRA/RFT fine-tuning; LoRAX multi-adapter serving | No | VPC option | Acquired by Rubrik (Jun 2025) |
| [Proxis](https://www.ycombinator.com/companies/proxis) | Was a distillation and serving platform | n/a | n/a | Pivoted to email and sales agents (research notes, Oct 21, 2026) |
| Lamini | Enterprise fine-tuning | No | n/a | Effectively shut down in 2025 (research notes, Oct 21, 2026; unverified) |
| [Arcee AI](https://docs.arcee.ai/arcee-conductor) | Now builds its own open models; Conductor routes between models | No (routing only) | Own models are open | Series B, $1B+ valuation (Sep 2026) (research notes, Oct 21, 2026; the linked docs page does not support it; unverified) |
| [Fireworks AI](https://docs.fireworks.ai/fine-tuning/managed-finetuning-intro) | Managed SFT and DPO (LoRA); full tuning via Training API | No | Export unverified | Active (research notes, Oct 21, 2026) |
| [Together AI](https://docs.together.ai/docs/fine-tuning/overview.md) | SFT and DPO; GRPO separately | No | Yes, download | Active |
| Unsloth | Open-source fine-tuning library and desktop app | No (a tool, not a service) | Yes (local) | Active |
| Distil Labs | Train a small model from a prompt plus a few examples | No (one-off) | Yes, download | Active |
| [AWS Bedrock distillation](https://aws.amazon.com/bedrock/model-distillation) | Teacher to student, can use invocation logs | Semi: you start each job | Not stated | GA since May 2025 (research notes, Oct 21, 2026) |
| Google Gemini distillation | Gemini teacher to Gemini student | Semi | No | Early access (research notes, Oct 21, 2026; unverified) |
| [OpenAI distillation](https://openai.com/index/api-model-distillation/) | Stored completions to fine-tuned GPT-4o mini | No | No (closed) | Self-serve fine-tuning ending: no new jobs after Jan 6, 2027 ([deprecations](https://developers.openai.com/api/docs/deprecations)) |
| Azure OpenAI distillation | Stored completions, "Distill" button | No | No | Stored completions retired Oct 15, 2026 (research notes, Oct 21, 2026; unverified) |
| [Nebius Token Factory post-training](https://nebius.com/blog/posts/post-training-in-token-factory) | SFT (LoRA/full), downloadable checkpoints | No: "continuous optimization" is on the roadmap | Yes | Active; no Nemotron students (post read Oct 21, 2026) |
| Routers: [Not Diamond](https://notdiamond.ai), [RouteLLM](https://lmsys.org/blog/2024-07-01-routellm/), OpenRouter Auto | Pick a model per request | No training | n/a | Active |
| Gateways: LiteLLM, Helicone | Logging, routing, policy | No training | n/a | Helicone acquired by Mintlify (research notes, Oct 21, 2026; unverified) |
| [Portkey](https://www.paloaltonetworks.com/company/press/2026/palo-alto-networks-completes-acquisition-of-portkey-to-secure-ai-agents) | AI gateway: logging, routing, policy | No training | n/a | Acquired by Palo Alto Networks; the deal closed May 29, 2026 (press release, checked Oct 21, 2026) |
| [OpenRouter distillable routing](https://openrouter.ai/docs/guides/evaluate-and-optimize/distillation) | A router across providers. `enforce_distillable_text` will "Restrict routing to only models that allow text distillation"; "OpenRouter provides distillation information on a best-effort basis" | No training, no lineage kept | n/a | Active (guide undated, checked Oct 21, 2026) |
| [NVIDIA Data Flywheel Blueprint](https://github.com/NVIDIA-AI-Blueprints/data-flywheel) | Reference blueprint that distils an agent's production traffic into smaller models with NeMo microservices and evaluates them ([blog, Jun 11, 2025](https://developer.nvidia.com/blog/build-efficient-ai-agents-through-model-distillation-with-nvidias-data-flywheel-blueprint)) | No: "Promotion to production—as well as any deeper evaluation or dataset curation—remains a human decision"; "Think of the Flywheel as a flashlight, not an autopilot" | n/a (a blueprint you run) | Repo marked deprecated in Apr 2026 (checked Oct 21, 2026) |
| [NVIDIA license-compliant distillation tutorial](https://developer.nvidia.com/blog/how-to-build-license-compliant-synthetic-data-pipelines-for-ai-model-distillation/) | Synthetic data from Nemotron 3 Nano through OpenRouter's distillable endpoints (`enforce_distillable_text`), generated with NeMo Data Designer and scored by an LLM judge | No: a one-off batch run | n/a (a tutorial) | Published Feb 5, 2026 (checked Oct 21, 2026) |
| [TensorZero](https://github.com/tensorzero/tensorzero) | Open-source LLMOps platform: gateway, observability, evaluation, optimization and experimentation | n/a: archived | n/a | Repo archived by its owner on Jun 12, 2026 (checked Oct 21, 2026); about 11.7k stars |
| taskdistill (open source, hobby) | Capture proxy, MLX LoRA, confidence cascade | No (scheduled retraining on roadmap) | Yes | 97.6% agreement at 23.7% escalation on Banking77 ([GitHub](https://github.com/B0yko/taskdistill)) |

## Where Ratchet wins

1. **License-clean by design, with lineage.** OpenAI's [Business Terms 3.3(e)](https://openai.com/policies/business-terms/) and Anthropic's [Commercial Terms D.4(a)](https://www.anthropic.com/legal/commercial-terms) ban training competing models on their outputs. A tool that distills Claude or GPT traffic exposes its users. Ratchet refuses those teachers in code, records each example's teacher and license when it is captured, and signs them into every adapter's bill of materials. OpenRouter's `enforce_distillable_text` filters teachers at request time, on license data it gives "on a best-effort basis", and keeps no training lineage. Nemotron 3 Ultra's [OpenMDW-1.1](https://openmdw.ai/license/1-1/) "does not impose any restrictions or obligations with respect to any use, modification, or sharing of any outputs" (license page last updated May 27, 2026).
2. **A decision for every request.** The student answers each request; only that request escalates if the student is unsure.
3. **Release safety without a person in the loop.** Shadow mode, a golden-set gate with champion-vs-challenger rescoring and an exact precision certificate in each signed BOM, canary 10% → 50% → 100%, and rollback on a statistical bound. NVIDIA's blueprint leaves promotion to production to "a human decision", and Inference.net stages new models "for final review and deployment"; no competitor page we found documents shadow, canary and automatic rollback.
4. **A home for OpenAI fine-tuning migrants.** The Jan 6, 2027 cutoff creates a deadline-driven buyer.
5. **Built for Nebius Token Factory and NVIDIA Nemotron.** The teacher, judge and hosted fallback run on Token Factory and the student is a Nemotron Nano LoRA adapter. The adapters measured so far were trained on a borrowed GPU; the Nebius Serverless Job and Endpoint scripts are written and tested offline, ready to run. It fills the gap on Nebius's own "continuous optimization" roadmap and runs four of the six steps of the LLM-to-SLM algorithm in NVIDIA Research's [arXiv 2506.02153](https://arxiv.org/abs/2506.02153) (v3, Sep 22, 2026) automatically: S1, S2, S5 and S6. Task clustering (S3) and model selection (S4) are manual: the caller or operator names each task, and there is one base model. Nebius could be a partner or an acquirer.

## Where Ratchet is weaker today

- No production traffic yet. Measured so far: two text-to-SQL exams and a tool-calling exam on adapters trained on a borrowed GPU (`evidence/`), a 20-request live smoke run and three recorded Scout runs, all answered by the teacher. Savings numbers are simulation (mock models).
- Single developer. Orchestra and Experiential Labs have two founders each and YC backing.
- Experiential Labs already has open-source traction (about 10k stars, checked Oct 21, 2026).
- Inference.net has funding and a managed training loop with evals against the live model (Apr 14, 2026).
- Per-token savings against the labs' cheapest tiers (Claude Haiku 5.5 at $0.10/$0.50 per million tokens; research notes, Oct 21, 2026; unverified) are only about 2x. The big savings come from moving traffic off top-tier models.

## Evidence numbers

| Source | Result |
|---|---|
| [FrugalGPT](https://arxiv.org/abs/2305.05176) | Matches GPT-4 at up to 98% lower cost |
| [RouteLLM](https://lmsys.org/blog/2024-07-01-routellm/) | Over 85% cost reduction on MT-Bench at 95% of GPT-4 quality |
| [Online Cascade Learning](https://arxiv.org/html/2402.04513v2) | Up to 90% lower inference cost |
| [Distilling Step-by-Step](https://arxiv.org/html/2305.02301v2) | A 770M T5 beats 540B PaLM few-shot |
| [AWS Bedrock distillation](https://aws.amazon.com/bedrock/model-distillation) | Up to 500% faster, 75% cheaper, under 2% accuracy loss (RAG) (AWS's claim, research notes, Oct 21, 2026) |
| [taskdistill](https://github.com/B0yko/taskdistill) | 97.6% teacher agreement at 23.7% escalation |
| Understudy (claim, no data) | 80% off an Anthropic bill |

## Positioning statement

For AI teams paying frontier prices for narrow, high-volume tasks, Ratchet is a drop-in gateway that turns their traffic into a LoRA adapter they own. Unlike distillation tools that learn from closed models, Ratchet only learns from teachers whose licenses allow it, records that lineage for every example and signs it into every adapter, and never promotes a model without checking it against the one in production on the same held-out rows.
