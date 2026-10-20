# Business plan

Owner: Harsh Bhanushali (DualMind Labs). As of 2026-10-19. All market sizes below are estimates built from public numbers, not measurements.

## 1. Model

**Open-source core, paid managed cloud, priced on savings.** This is the usual model for developer infrastructure. Pricing on savings answers the buyer's main objection: buyers rank proving ROI (43%) far above price (7%) as their top challenge ([State of Tokenomics 2026](https://www.nops.io/blog/state-of-tokenomics-2026/)).

## 2. Tiers

| Tier | What you get | Price idea |
|---|---|---|
| Open source (Apache-2.0) | Gateway, cascade, provenance ledger, Night School scripts, release gate, dashboard. Self-host on any GPU | Free |
| Ratchet Cloud | Managed nightly loop on Nebius, hosted dashboard, canary and rollback, weight export, alerts | 20–30% of verified monthly savings. No savings, no fee |
| Enterprise | Runs in the customer's cloud or on-prem, data residency (EU, India), audit export of the provenance ledger, SSO, support | Annual contract |

"Verified savings" = all-teacher baseline cost minus actual cost, as recorded by the ledger (the same numbers the dashboard shows).

## 3. Market size (estimates)

| Layer | Estimate | How |
|---|---|---|
| API and inference spend pool, 2026 | $40–60B | Menlo's $12.5B enterprise API spend in 2025 ([Menlo](https://menlovc.com/perspective/2025-the-state-of-generative-ai-in-the-enterprise/)), scaled by 2026 lab run-rates (Anthropic about $65B, OpenAI nearly $70B incl. consumer) |
| Distillable spend (TAM input) | $12–24B / year | 30–40% of the pool, using NVIDIA's estimate that 40–70% of agent LLM calls could go to small models ([arXiv](https://arxiv.org/html/2506.02153v1)) |
| Revenue TAM | $2–6B / year | 70–90% savings on distillable spend, 20–30% share of savings |
| SAM | $0.4–2.5B | 5,000–10,000 AI-native companies spending $20k+/month, $300k–1M distillable spend each, 25% take rate |
| SOM (3 years) | $3–20M ARR | 50–150 customers at $50–150k ACV |

Growth drivers: inference overtakes training in 2026 ([Gartner](https://www.gartner.com/en/newsroom/press-releases/2026-08-10-gartner-forecasts-worldwide-artificial-intelligence-optimized-iaas-spending-to-grow-96-percent-in-2026)); open-weight models are expected to go from 34% to 41% of enterprise tokens in 12 months ([ETR via Techstrong](https://techstrong.ai/articles/open-weight-ai-models-expected-to-capture-41-of-enterprise-token-usage/)); 57% of pilot organizations would move a workload for savings of 25% or less (same source).

Headwinds: open-source share of enterprise LLM *spend* fell from 19% to 11% ([Menlo](https://menlovc.com/perspective/2025-the-state-of-generative-ai-in-the-enterprise/)); large enterprises moved from fine-tuning toward prompting and routing ([a16z](https://www.a16z.news/p/leaders-gainers-and-unexpected-winners)). So the first market is AI-native startups and scale-ups, not the Global 2000.

## 4. Customer segments, in order

1. **AI-native startups and scale-ups** with high-volume narrow calls and squeezed margins.
2. **OpenAI fine-tuning migrants.** No new OpenAI fine-tuning jobs after Jan 6, 2027 ([OpenAI deprecations](https://developers.openai.com/api/docs/deprecations)).
3. **Sovereignty-minded buyers in India and the EU.** Sovereign cloud spend: $80.4B in 2026 ([Gartner](https://www.gartner.com/en/newsroom/press-releases/2026-02-09-gartner-says-worldwide-sovereign-cloud-iaas-spending-will-total-us-dollars-80-billion-in-2026)). India's AI Mission offers GPUs at under ₹100/hour ([PIB](https://www.pib.gov.in/PressReleasePage.aspx?PRID=2223115&reg=48&lang=2)).
4. **Mid-market platform teams** that already run an internal LLM gateway.

## 5. Go-to-market

1. **Launch through the hackathon.** Public repo, demo video, Devpost page.
2. **Developer channels.** Hacker News "Show HN", r/LocalLLaMA, NVIDIA and Nebius developer communities.
3. **Migration content.** "Move your OpenAI fine-tune to a model you own before Jan 6, 2027."
4. **Partners.** Apply to NVIDIA Inception. Pitch Ratchet to Nebius as the "continuous optimization" layer on its Token Factory roadmap.
5. **Design partners.** First 10 from Parul University's PIERC incubation cell and the Indian startup scene, then AI-native startups abroad.
6. **Proof assets.** Every design partner gets a savings receipt (dashboard export) that doubles as a case study.

## 6. First customers: what we offer

- Free setup and a 30-day pilot on one task.
- Weekly savings report from the ledger.
- No fee unless savings are verified.
- They keep the adapter weights either way.

## 7. Open-source strategy

- Keep the core loop open (Apache-2.0): it builds trust, and the license-clean claim is easier to believe when the code is public.
- Charge for running it: managed GPUs, hosted dashboard, alerts, SSO, compliance exports, support.
- Differentiate from Experiential Labs (open source, ~9.6k stars) on the automatic nightly loop, release safety, and provenance.

## 8. Exit logic

GPU clouds and platforms buy this capability:

- CoreWeave acquired OpenPipe (Sep 2025).
- Rubrik acquired Predibase (Jun 2025; reported $100–500M).
- Palo Alto Networks is acquiring Portkey (announced Apr 2026).

Natural acquirers for Ratchet: Nebius (fills its continuous-optimization roadmap), other GPU clouds, and security/governance companies that value the provenance ledger.

## 9. Risks to the business

| Risk | Response |
|---|---|
| Crowded field, funded rivals | License-clean wedge, release safety, Nebius/NVIDIA alignment |
| Frontier cheap tiers shrink savings | Sell quality on the task, latency, ownership, compliance |
| Platform terms (Token Factory ToS 5(f)) | Written clarification requested Oct 19, 2026; build as a Nebius-native product |
| Self-hosting does not pay at low volume | Report savings by GPU-hours actually paid, not only per token; recommend the hosted fallback or scale-to-zero below break-even (see §10) |
| Single founder bandwidth | Open-source contributors; co-founder search after hackathon |

## 10. Unit economics sketch (simulation-based, illustrative)

From the simulation (mock models): all-teacher cost was about $1.09 per 1,000 ticket-triage requests and Ratchet's steady-state cost about $0.15–0.16, an 85–87% saving. At a 25% share of savings, a customer sending 10M such requests a month would save about $9,300 and pay about $2,300. GPU costs for training and serving must come out of that; confirm with live numbers before quoting to customers.

### When self-hosting pays (utilization-aware)

The figures above price the self-hosted student per token, using the hosted Nano price as a stand-in. A Nebius Serverless Endpoint bills GPU-hours while it runs, whether or not requests arrive, so the per-token view flatters the student whenever the endpoint sits idle. Nebius's own Serverless AI Builders Challenge reached the same conclusion: its second-place project found that self-hosting beats Token Factory on cost only at high utilization ([winners post](https://nebius.com/blog/posts/serverless-ai-builders-challenge-winners)).

The break-even point is simple:

```
break-even student-served requests per GPU-hour = GPU price per hour ÷ teacher cost avoided per student-served request
```

Worked example with **unverified list prices** (confirm on day 1): an L40S at about $1.55/hour, and about $0.001 of Ultra spend avoided per ticket-triage request (the simulation's all-teacher cost of $1.09 per 1,000). Break-even is about 1,500 student-served requests per GPU-hour, roughly 36,000 a day.

- **At demo volume (1,500 requests a day) a 24/7 endpoint loses money.** Below break-even, use the hosted, untuned Nano fallback on Token Factory (pay per token), or run the endpoint only while traffic arrives (scale to zero).
- **At customer volume the case holds.** 10M requests a month is about 14,000 an hour, far above break-even: a GPU running all month (about $1,100 at the price above) is small next to about $9,300 of avoided Ultra spend.
- **The levers:** serve many LoRA adapters (tasks or customers) from one GPU with vLLM multi-LoRA, scale to zero between bursts, and fall back to the hosted model below break-even.

Ratchet will report this itself: v0.5 adds utilization-aware cost accounting (endpoint uptime and training GPU-hours, savings computed both ways, the break-even volume and a self-host recommendation). See [ROADMAP.md](ROADMAP.md).
