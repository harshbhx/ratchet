# Research summary: market and stress test

As of 2026-10-19. Condensed from the shared doc "Ratchet — Market Research & Stress Test". Detailed versions: [Competitive analysis](COMPETITIVE_ANALYSIS.md), [Business plan](BUSINESS.md), [PRD risks](PRD.md#11-risks-and-mitigations).

## Verdict

- **Hackathon:** strongest-fit idea. It uses Nemotron Ultra, Super and Nano, Token Factory, Serverless Jobs and Serverless Endpoints: the judges' own stack.
- **Open source:** worth it, launched through the hackathon. Needs clear differences from Experiential Labs: an automatic nightly loop, release safety, provenance.
- **Startup:** possible but crowded. Decide after the open-source launch shows traction.
- **The angle that matters:** license-clean distillation. Closed-model terms forbid training competing models on their outputs; Ratchet only uses open teachers and proves it.

## Market

| Signal | Number | Source |
|---|---|---|
| Enterprise generative AI spend, 2025 | $37B (3.2x 2024); $12.5B on model APIs | [Menlo Ventures](https://menlovc.com/perspective/2025-the-state-of-generative-ai-in-the-enterprise/) |
| Inference share of AI cloud spend, 2026 | 55% ($23.3B) | [Gartner](https://www.gartner.com/en/newsroom/press-releases/2026-08-10-gartner-forecasts-worldwide-artificial-intelligence-optimized-iaas-spending-to-grow-96-percent-in-2026) |
| Average enterprise LLM spend | about $7M, +65% expected | [a16z](https://www.a16z.news/p/leaders-gainers-and-unexpected-winners) |
| Small task-specific models by 2027 | used 3x more than general LLMs | [Gartner](https://www.gartner.com/en/newsroom/press-releases/2025-04-09-gartner-predicts-by-2027-organizations-will-use-small-task-specific-ai-models-three-times-more-than-general-purpose-large-language-models) |
| Replaceable agent LLM calls | 40–70%; small models 10–30x cheaper | [NVIDIA Research](https://arxiv.org/html/2506.02153v1) |
| Companies using or evaluating a router | 86% (n=472) | [State of Tokenomics 2026](https://www.nops.io/blog/state-of-tokenomics-2026/) |
| Open-weight share of enterprise tokens | 34% now, 41% in 12 months | [ETR via Techstrong](https://techstrong.ai/articles/open-weight-ai-models-expected-to-capture-41-of-enterprise-token-usage/) |
| Sovereign cloud spend, 2026 | $80.4B | [Gartner](https://www.gartner.com/en/newsroom/press-releases/2026-02-09-gartner-says-worldwide-sovereign-cloud-iaas-spending-will-total-us-dollars-80-billion-in-2026) |

**Price gap.** Nemotron 3 Nano on Token Factory: $0.06 in / $0.24 out per million tokens, about 76x cheaper than top-tier frontier models but only about 2x cheaper than Claude Haiku 5.5 ($0.10 / $0.50) ([Token Factory catalog](https://tokenfactory.nebius.com/model-catalog.md), [Claude pricing](https://platform.claude.com/docs/en/about-claude/pricing)).

**Caution.** Buyers rank proving ROI (43%) far above price (7%). Sell proven savings with no quality loss.

## Competitors

Closest: Orchestra/Understudy and Experiential Labs (both YC S26), Inference.net ($11.8M seed). OpenPipe (CoreWeave) and Predibase (Rubrik) were acquired in 2025. Full table: [COMPETITIVE_ANALYSIS.md](COMPETITIVE_ANALYSIS.md).

## Stress test: top findings

1. **Token Factory cannot fine-tune or serve a tuned Nemotron.** Its post-training list has 49 models and no Nemotron ([models](https://docs.tokenfactory.nebius.com/post-training/models.md), read Oct 21, 2026); custom weights on dedicated endpoints are "currently in beta and available on request" ([custom weights](https://docs.tokenfactory.nebius.com/ai-models-inference/dedicated-endpoints/custom-weights.md), read Oct 21, 2026). Train on Serverless Jobs, serve on Serverless Endpoints with vLLM.
2. **Nano 30B needs about 60GB VRAM for LoRA** ([Unsloth](https://unsloth.ai/docs/models/tutorials/nemotron-3)). Use [Nemotron 3 Nano 4B](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16) on L40S.
3. **Budget.** Builder Program: $25 Token Factory credit, no GPU credit ([terms](https://nebius.com/builders-terms-and-conditions)). L40S from about $1.55/hour on-demand ([prices](https://nebius.com/prices)); Serverless rates unconfirmed.
4. **Token Factory ToS 5(f)** bans using the service to build a competing service ([ToS](https://docs.tokenfactory.nebius.com/legal/terms-of-service)). Email sent to tokenfactory-support@nebius.com on Oct 19, 2026.
5. **Licensing is clean.** Nemotron 3 Ultra under [OpenMDW-1.1](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Ultra-550B-A55B-BF16) places no restrictions on outputs. The [NVIDIA Nemotron Open Model License](https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-nemotron-open-model-license/) allows commercial derivatives.
6. **Closed teachers violate terms:** [OpenAI 3.3(e)](https://openai.com/policies/business-terms/), [Anthropic D.4(a)](https://www.anthropic.com/legal/commercial-terms).
7. **Technical pitfalls and mitigations:** forgetting ([LoRA Learns Less and Forgets Less](https://arxiv.org/abs/2405.09673)) → retrain from base on cumulative data; model collapse ([arXiv](https://arxiv.org/abs/2305.17493)) → train on teacher answers only; judge self-preference ([arXiv](https://arxiv.org/abs/2404.13076)) → exact-match labels and reference-based judging; poor calibration ([arXiv](https://arxiv.org/abs/2207.05221)) → combined confidence, self-consistency ([arXiv](https://arxiv.org/abs/2203.11171)), per-version calibration; PII → [Presidio](https://presidio.dataprivacystack.org/) redaction (planned).

## Design changes the research caused

1. Student = Nemotron 3 Nano 4B on Nebius AI Cloud.
2. Provenance ledger and model bill of materials.
3. Per-request cascade with a combined, calibrated confidence score.
4. Shadow → canary 10% → 50% → 100% with automatic rollback.
5. Savings receipts against an all-teacher baseline.

## Design changes the simulation caused

1. Shadow judged on precision and coverage of what the candidate would serve, with teacher arbitration.
2. Frequent requests (seen 3+ times) always go to training.
3. Threshold calibrated on half the golden set, measured on the other half.
4. Live model re-scored on the same golden set each night.
5. No training while a release is in flight.
6. Calibration handles tied confidence values.
