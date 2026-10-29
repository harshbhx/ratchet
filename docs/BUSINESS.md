# Business plan

Owner: Harsh Bhanushali (DualMind Labs). As of 2026-10-22 (individual figures carry their own dates). All market sizes below are estimates built from public numbers, not measurements. Third-party figures carry the date they were read; a figure marked "research notes" was not re-checked and is unverified.

## 1. Model

**Open-source core, paid managed cloud, priced on savings.** This is the usual model for developer infrastructure. Pricing on savings answers the buyer's main objection: buyers rank proving ROI (43%) far above price (7%) as their top challenge ([State of Tokenomics 2026](https://www.nops.io/blog/state-of-tokenomics-2026/); research notes, Oct 19, 2026).

## 2. Tiers

| Tier | What you get | Price idea |
|---|---|---|
| Open source (Apache-2.0) | Gateway, cascade, provenance ledger, Night School scripts, release gate, dashboard. Self-host on any GPU | Free |
| Ratchet Cloud | Managed nightly loop on Nebius, hosted dashboard, canary and rollback, adapter export with its signed BOM, alerts | 20–30% of verified monthly savings. No savings, no fee |
| Enterprise | Runs in the customer's cloud or on-prem, data residency (EU, India), audit export of the provenance ledger, SSO, support | Annual contract |

"Verified savings" = the **billable lower bound** of a sealed savings receipt ([ADR 0029](adr/0029-verified-savings-receipts.md)): for each day and each client, the all-teacher baseline of the requests the student served, calibrated on audited teacher bills (a ratio estimator with a confidence bound), plus the teacher's own bills, minus actual cost, at the one-sided 95% lower bound, minus spend outside requests. The fee is the take rate (`RATCHET_TAKE_RATE`, 25% by default) of that bound, never of the token formula's estimate, which assumes 300 reasoning tokens per teacher answer, a figure not yet measured at scale (the smoke run's control arm saw a mean of 81 reasoning tokens, n = 6). A task with too few audits that day (`RATCHET_RECEIPT_MIN_AUDITS`, 30) is reported but not billed. Each receipt is signed, chained to the client's previous one and proves every request id the client saw (`X-Ratchet-Request-Id`), so the client can check it offline (`python -m ratchet.receipt verify`). It cannot prove the costs logged at request time; the client reconciles the receipts' teacher spend with its Token Factory bill for the same key. The dashboard's savings are the formula's, as the ledger records them; they are not what is billed.

## 3. Market size (estimates)

| Layer | Estimate | How |
|---|---|---|
| API and inference spend pool, 2026 | $40–60B | Menlo's $12.5B enterprise API spend in 2025 ([Menlo](https://menlovc.com/perspective/2025-the-state-of-generative-ai-in-the-enterprise/); research notes, Oct 19, 2026), times 3 to 5 for 2026 (our assumption, not a sourced figure) |
| Distillable spend (TAM input) | $12–24B / year | 30–40% of the pool, guided by NVIDIA Research's estimate that about 40%, 60% and 70% of the LLM queries in the three open agent systems it studied could be handled by small models ([arXiv 2506.02153](https://arxiv.org/abs/2506.02153), v3, Sep 22, 2026) |
| Revenue TAM | $2–6B / year | 70–90% savings on distillable spend, 20–30% share of savings |
| SAM | $0.4–2.5B | 5,000–10,000 AI-native companies spending $20k+/month, $300k–1M distillable spend each, 25% take rate |
| SOM (3 years) | $3–20M ARR | 50–150 customers at $50–150k ACV |

Growth drivers (research notes, Oct 19, 2026): inference overtakes training in 2026 ([Gartner](https://www.gartner.com/en/newsroom/press-releases/2026-08-10-gartner-forecasts-worldwide-artificial-intelligence-optimized-iaas-spending-to-grow-96-percent-in-2026)); open-weight models are expected to go from 34% to 41% of enterprise tokens in 12 months ([ETR via Techstrong](https://techstrong.ai/articles/open-weight-ai-models-expected-to-capture-41-of-enterprise-token-usage/)); 57% of pilot organizations would move a workload for savings of 25% or less (same source).

Headwinds (research notes, Oct 19, 2026): open-source share of enterprise LLM *spend* fell from 19% to 11% ([Menlo](https://menlovc.com/perspective/2025-the-state-of-generative-ai-in-the-enterprise/)); large enterprises moved from fine-tuning toward prompting and routing ([a16z](https://www.a16z.news/p/leaders-gainers-and-unexpected-winners)). So the first market is AI-native startups and scale-ups, not the Global 2000.

## 4. Customer segments, in order

1. **AI-native startups and scale-ups** with high-volume narrow calls and squeezed margins.
2. **OpenAI fine-tuning migrants.** No new OpenAI fine-tuning jobs after Jan 6, 2027 ([OpenAI deprecations](https://developers.openai.com/api/docs/deprecations), read Oct 21, 2026).
3. **Sovereignty-minded buyers in India and the EU.** Sovereign cloud spend: $80.4B in 2026 ([Gartner](https://www.gartner.com/en/newsroom/press-releases/2026-02-09-gartner-says-worldwide-sovereign-cloud-iaas-spending-will-total-us-dollars-80-billion-in-2026)). India's AI Mission offers GPUs at under ₹100/hour ([PIB](https://www.pib.gov.in/PressReleasePage.aspx?PRID=2223115&reg=48&lang=2)). Both from the research notes, Oct 19, 2026.
4. **Mid-market platform teams** that already run an internal LLM gateway.

## 5. Go-to-market

1. **Launch through the hackathon.** Public repo, video, Devpost page.
2. **Developer channels.** Hacker News "Show HN", r/LocalLLaMA, NVIDIA and Nebius developer communities.
3. **Migration content.** "Move your OpenAI fine-tune to an open Nemotron model with a LoRA adapter you own before Jan 6, 2027."
4. **Partners.** Apply to NVIDIA Inception. Pitch Ratchet to Nebius as the "continuous optimization" layer on its Token Factory roadmap.
5. **Design partners.** First 10 from Parul University's PIERC incubation cell and the Indian startup scene, then AI-native startups abroad.
6. **Proof assets.** Every design partner gets its signed daily savings receipts (`GET /ratchet/receipts`), which double as a case study.

## 6. First customers: what we offer

- Free setup and a 30-day pilot on one task.
- Weekly savings report from the ledger, backed by the signed daily receipts.
- No fee unless savings are verified.
- They keep the adapter and its signed training lineage either way.

## 7. Open-source strategy

- Keep the core loop open (Apache-2.0): it builds trust, and the license-clean claim is easier to believe when the code is public.
- Charge for running it: managed GPUs, hosted dashboard, alerts, SSO, compliance exports, support.
- Differentiate from Experiential Labs (open source, about 10k GitHub stars, checked Oct 21, 2026) on the automatic nightly loop, release safety, and provenance.

## 8. Exit logic

GPU clouds and platforms buy this capability:

- CoreWeave acquired OpenPipe (Sep 2025; [announcement](https://coreweave.com/news/coreweave-to-acquire-openpipe-leader-in-reinforcement-learning)).
- Rubrik acquired Predibase (Jun 2025; [TechCrunch](https://techcrunch.com/2025/06/25/rubrik-acquires-predibase-to-accelerate-adoption-of-ai-agents/)).
- Palo Alto Networks acquired Portkey (announced Apr 2026, closed May 29, 2026: [press release](https://www.paloaltonetworks.com/company/press/2026/palo-alto-networks-completes-acquisition-of-portkey-to-secure-ai-agents), checked Oct 21, 2026).

Natural acquirers for Ratchet: Nebius (fills its continuous-optimization roadmap), other GPU clouds, and security/governance companies that value the provenance ledger.

## 9. Risks to the business

| Risk | Response |
|---|---|
| Crowded field, funded rivals | License-clean wedge, release safety, Nebius/NVIDIA alignment |
| Frontier cheap tiers shrink savings | Sell quality on the task, latency, ownership, compliance |
| Platform terms (Token Factory ToS 5(f)) | Written clarification requested Oct 19, 2026, no answer received as of 29 Oct 2026; build for Token Factory and say plainly that the teacher calls run on Token Factory and the measured adapters were trained on a borrowed GPU |
| Self-hosting does not pay at low volume | Report savings by GPU-hours actually paid, not only per token; recommend the hosted fallback or scale-to-zero below break-even (see §10) |
| Single founder bandwidth | Open-source contributors; co-founder search after hackathon |

## 10. Unit economics sketch (illustrative)

No measured savings figure exists yet: the live gateway has no student endpoint, so the teacher answers every request, and Night School has not run on live traffic. Everything below is arithmetic on list prices, to be confirmed with live numbers before it is quoted to customers.

### When self-hosting pays (utilization-aware)

A student priced per token, using the hosted Nano price as a stand-in, looks cheaper than it is. A Nebius Serverless Endpoint bills GPU-hours while it runs, whether or not requests arrive, so the per-token view flatters the student whenever the endpoint sits idle. Nebius's own Serverless AI Builders Challenge reached the same conclusion: its second-place project found that self-hosting beats Token Factory on cost only at high utilization ([winners post](https://nebius.com/blog/posts/serverless-ai-builders-challenge-winners); research notes, Oct 20, 2026).

The break-even point is simple:

```
break-even student-served requests per GPU-hour = GPU price per hour ÷ teacher cost avoided per student-served request
```

Worked example with **estimated prices** (confirm on the bill): an L40S at $1.35 per GPU-hour on demand ([Nebius pricing](https://docs.nebius.com/compute/resources/pricing), read Oct 22, 2026), and about $0.001 of Ultra spend avoided per ticket-triage request (an assumed all-teacher cost of about $1.09 per 1,000 requests, from the code's mock price list). Break-even is about 1,350 student-served requests per GPU-hour, roughly 32,000 a day (the code default for `RATCHET_STUDENT_GPU_USD_PER_HOUR` is $1.55; set it to 1.35 to match).

- **At small volume (1,500 requests a day) a 24/7 endpoint loses money.** Below break-even, use the hosted, untuned Nano fallback on Token Factory (pay per token), or run the endpoint only while traffic arrives (scale to zero).
- **At customer volume the endpoint costs less than the teacher.** 10M requests a month is about 14,000 an hour, far above break-even: a GPU running all month (about $1,000 at the price above) is small next to about $9,300 of avoided Ultra spend.
- **On price alone the hosted fallback is cheaper still.** The untuned Nano on Token Factory costs about $0.00003 per answer (list price, assumed token counts), so a 24/7 endpoint beats it only above roughly 52,000 student answers an hour, and at 14,000 an hour the report recommends `hosted_fallback`. That comparison assumes the hosted model serves as many requests as the tuned student. It should not: serving more requests than an untuned model is what the fine-tuning is for, and each request the hosted model escalates costs about $0.001 of Ultra. The report states the assumption; an operator compares the student's share over a day on each before switching.
- **The levers:** serve many LoRA adapters (tasks or customers) from one GPU with vLLM multi-LoRA, scale to zero between bursts, and fall back to the hosted model below break-even.

Ratchet reports this itself (v0.5): it records endpoint uptime and training GPU-hours, computes savings both ways, works out the break-even volume and recommends self-hosting, the hosted fallback or scaling to zero (`GET /ratchet/cost`, `python -m ratchet.cost report`; [ADR 0023](adr/0023-utilization-aware-cost-accounting.md)).
