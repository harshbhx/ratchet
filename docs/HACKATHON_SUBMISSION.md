# Devpost submission: Nebius x NVIDIA Global AI Hackathon

Ready to paste. Fill each `[LIVE NUMBER]` from `evidence/live/results.json` using the placeholder map in `docs/LIVE_RESULTS.md`, and put its `<!-- claim:live.* -->` anchor right after the number. Keep simulation results labelled as simulation.

- **Deadline:** Oct 30, 2026, 10:00 AM PDT (10:30 PM IST). Target: submit Oct 29.
- **Track:** Best Apps and Agents ("Useful applications and agents powered by Nemotron models through Token Factory").
- **Also eligible for:** Best Use of Tavily (Scout's searcher uses the Tavily Search API). It needs a working runtime call to Tavily, so only live mode counts.
- **Judging:** a pass/fail Stage One check against the submission requirements, then four equally weighted criteria: Technological Implementation, Design, Potential Impact, Quality of the Idea. Judging runs Dec 1–15, 2026.
- **Demo URL:** [DEMO URL], hosted on a Nebius VM (see [DEPLOY_NEBIUS_VM.md](DEPLOY_NEBIUS_VM.md)). Pages: `/` landing, `/scout` Scout demo, `/dashboard` dashboard.

### Prizes and field (as reported, Oct 2026)

The Devpost page blocks automated reading, so these come from an organizer-deck recap and third-party listings. Confirm on [Devpost](https://nebiusglobalaihackathon.devpost.com/) before relying on them.

| Prize | Amount | Note |
|---|---|---|
| Grand prize | $20,000 | Overall, across all four tracks |
| 2nd place | $10,000 | Overall |
| 3rd place | $6,000 | Overall |
| Track winners (4) | NVIDIA Jetson Orin Nano each | Hardware, not cash |
| Best Use of Tavily | $3,000 | Needs a functional runtime Tavily call |
| Most Valuable Feedback (10) | $100 plus NVIDIA swag | For the feedback section below |
| City winners (20) | $500 each | In-person Builders & Brews attendees only; no Indian city |

Registrants: 24,785 as of Oct 19, 2026 (third-party tracker; the number of submissions is not published). Sources: [Amsterdam recap with the organizer deck](https://lucaberton.com/blog/aaif-builders-brews-amsterdam-nebius-nvidia-hackathon-2026/), [Tierones listing](https://tierones.io/opportunities/nebius-nvidia-ai-2026), [participant's rules summary checked Oct 11](https://github.com/smanika3/Nebius-x-NVIDIA-Hackathon/blob/main/README.md).

### Stage One (pass/fail) readiness

A submission that misses any of these is out of scope regardless of quality:

- **A runtime call to Token Factory, or running on Nebius AI Cloud.** Mock mode makes no Token Factory call, so the hosted demo must run in **live mode** (at least the teacher) during judging.
- **At least one NVIDIA open model:** Nemotron 3 Ultra (teacher), Super (judge), Nano (student and fallback).
- **A working demo URL, free for judges, up until Dec 15, 2026.** Check that the Token Factory credit lasts that long, or top it up; the guide's $0.50/day Scout budget is about $24 over 48 days.
- **A public repo with an open-source license file** (Apache-2.0) and a README with setup instructions.
- **A demo video under 3 minutes**, public on YouTube, with no copyrighted music. Judges need not watch past 3 minutes, so lead with the strongest moment.
- **English materials, a description of features, and the feedback section.**

**Judges reaching a live Scout run (unverified where marked).** What the rules say comes only from web-search snippets of the rules page (it returns 403 to fetches), Oct 20, 2026, so treat it as **unverified**: judges are not required to test the project and may judge on the text, images and video alone; a private site must give login credentials in its testing instructions. So:

- The video must show a live run too, because a judge may never open the demo.
- Judge access ([ADR 0120](adr/0120-judge-access-and-a-persistent-scout-guard.md)) holds runs back for judges (`RATCHET_SCOUT_JUDGE_RUNS_PER_DAY`) behind an access code (`RATCHET_SCOUT_JUDGE_CODE`). Put the code in the submission's **testing instructions**. The rules require that field for private sites; whether only managers and judges see it here is **unverified** (a Devpost manager said so for another hackathon). The fallback is email to the hackathon managers.
- Never put the code on the public project page or in the video. Without it, judges still get the public pool, and a run without search still makes a Token Factory call: the planner calls the teacher before any search.
- The guard's counters survive restarts, Tavily credits are paced until `RATCHET_TAVILY_PACE_UNTIL`, and a failed search no longer ends a run, so the public cannot lock a judge out by using up the day or the month early.

### What past winners had in common

- **Nebius's [Serverless AI Builders Challenge](https://nebius.com/blog/posts/serverless-ai-builders-challenge-winners)** (35 qualifying projects; criteria: technical implementation, reproducibility, educational content quality, product usage depth, real-world usefulness, originality). First place LoRA-fine-tuned 7–8B models on Serverless Jobs, served the winner on a Serverless Endpoint and reported its full bill ($320, failed jobs included). Second place compared Token Factory against self-hosted vLLM on quality, latency and cost, and found self-hosting wins only at high utilization. Third place injected faults into multi-agent systems and measured robustness, with a $3.64 bill.
- **NVIDIA's [Nemotron hackathon](https://blog.nota.ai/insights/nemotron-hackathon-winner)** grand prize went to an infrastructure project; the judges singled out "not only qualitative but also quantitative evaluation" over polished but unmeasured demos.
- **NVIDIA's [Agent Toolkit hackathon](https://developer.nvidia.com/blog/hackathon-winners-bring-agentic-ai-to-life-with-the-nvidia-nemo-agent-toolkit/)** was won by a project that used several NVIDIA products together.

For Ratchet that means: real measured numbers from the live run, the real Nebius bill (total, failed jobs included), deep use of Jobs, Endpoints, Object Storage and Token Factory, and an honest cost model. Ratchet's GPU-hour cost accounting reports when self-hosting does not pay and recommends the hosted fallback instead (see [BUSINESS.md](BUSINESS.md#when-self-hosting-pays-utilization-aware)).

---

## Project name

Ratchet

## Tagline

License-clean distillation on open models. Rent intelligence today, own it tomorrow.

## Inspiration

Every AI startup we know rents its intelligence. Each request goes to a big model, and the bill grows with every user. Much of that traffic is narrow and repetitive: classifying a ticket, extracting a field, one step inside an agent. A small model could do it, if someone trained it.

Distilling a big model into a small one works, but it is slow manual work. And if the big model is GPT or Claude, their terms forbid using the outputs to train competing models. We wanted a tool that does it automatically, safely, and legally, on open models, so the customer ends up owning the trained adapter and its signed lineage.

## What it does

Ratchet is an OpenAI-compatible gateway. You change one line, your base URL. Then:

1. A small model (NVIDIA Nemotron 3 Nano 4B with a LoRA adapter you own) answers each request first.
2. If it is not confident, the request goes to NVIDIA Nemotron 3 Ultra on Nebius Token Factory.
3. Every Ultra answer is saved with its provenance: which teacher wrote it and which license allows training on it.
4. Every night, Night School fine-tunes a new student on a Nebius Serverless Job and tests it on a held-out golden set.
5. A new student reaches users only step by step: shadow mode, then 10%, 50%, 100% of traffic. If its audited quality drops, Ratchet rolls it back automatically.

The dashboard shows dollars saved against an all-Ultra baseline, the share of traffic your own model answers, results by task, every version and its state, the release log, and a model bill of materials.

It is honest about cost. A self-hosted endpoint bills GPU-hours whether requests arrive or not, so Ratchet records when the endpoint and the training jobs ran and reports savings two ways: per token, and by the GPU-hours actually paid. It works out the break-even volume (about 1,500 student answers per endpoint hour at unverified L40S list prices) and says plainly when self-hosting does not pay, recommending the hosted fallback or scaling the endpoint to zero instead. At demo volume it says exactly that.

It is built for real traffic: personal data (emails, phones, card numbers, IBANs, national IDs, IPs, API secrets) is replaced with placeholders before anything becomes training data; the gateway takes API keys with a daily quota per client; it streams like the OpenAI API; and every trained adapter ships with its own bill of materials, signed with Ed25519, that anyone with the public key can verify offline.

**Scout** shows Ratchet on agent traffic. It is a team of five research agents that answers a question from the web, and every LLM call goes through Ratchet:

- The **planner** writes three search queries and the **writer** writes the cited answer. These open-ended calls are **pinned** to Nemotron 3 Ultra and never become training data.
- The **searcher** runs the queries on the **Tavily Search API**.
- The **triage** agent labels each result (relevant? what kind of source?) and the **reader** pulls the key fact from each relevant page. These are many short, repetitive calls, and the student learns to take them over.

The Scout page shows each call as it happens, with a badge for who answered it (your model or Ultra), its cost against Ultra alone, and a savings bar for the whole run.

## How we built it

- **Gateway and cascade** (FastAPI, Python). The student is sampled 3 times with logprobs. Confidence combines token probability and agreement between samples. Each model version has its own threshold, calibrated on half the golden set to reach 97% precision.
- **Provenance ledger** (SQLite). Every example records its teacher and license. Closed-model teachers are refused in code.
- **Night School.** Builds a cumulative dataset from teacher answers only, retrains LoRA from the base model each night (TRL + PEFT on a Nebius Serverless Job, on the same prompt the student is served), and hot-loads the adapter into vLLM on a Nebius Serverless Endpoint.
- **Release gate.** Golden gate with champion-vs-challenger rescoring, shadow mode with teacher arbitration, canary with teacher audits, and rollback on a 95% Wilson upper bound, or (opt-in) on an anytime-valid e-process that stays valid however often the gate looks and needs no minimum audit count, with the e-value and a confidence sequence in each of its events. The live alert uses the same e-process.
- **Simulator.** Synthetic support tickets, mock models with a learning curve and confident mistakes, and fault injection, so the whole loop is testable without GPUs. A mock research world (8 questions, simulated search results on `example.org`) lets Scout run with no keys.
- **Scout.** Five agents in Python. Each is a plain OpenAI client pointed at Ratchet, with a task name per call. Triage and reader calls run in parallel threads; the ledger is guarded by a lock. Two tasks are pinned to the teacher in Ratchet's task registry, so open-ended work never trains the student. Events stream to the page with Server-Sent Events. A budget guard (daily spend cap, daily run cap, per-IP hourly limit) protects the public demo.
- **Web app.** A story page, the Scout page and the dashboard in React (Vite, Tailwind, motion, recharts), built into the Python package so the gateway serves it without Node. Light and dark mode. The dashboard's Trust and privacy panel has a live redactor: paste any text and see what would be stored. Its Resilience panel runs an outage drill: take the student endpoint down and watch its circuit breaker open, the teacher answer, and a probe close it again (simulation). The story page has an honest bill (the same traffic priced per token and by the GPU-hours a self-hosted endpoint runs, the break-even volume, and Ratchet saying when self-hosting does not pay; from the gateway's cost report, or the reference simulation, labelled) and a chapter of things a reader can check: the redactor, the BOM verifier command, the traceability check in CI and the release gate's bounds.
- **Cost accounting.** Each request's cost is split into student and teacher spend; a compute-time ledger records endpoint uptime and training GPU-hours (CLI, an admin-key endpoint, the deploy script, Night School); `GET /ratchet/cost` and `python -m ratchet.cost report` give savings per token and by GPU-hours, the break-even and hosted-fallback crossover volumes, and a recommendation.
- **Privacy and provenance.** Pattern-based PII redaction with checksums (Luhn, mod-97, Verhoeff) before examples are stored. Named API keys with per-key quotas and per-client savings. A `ratchet.bom/v1` document per adapter with an Ed25519 signature and an offline verifier (`python -m ratchet.bom verify`).
- **Hosting.** One Nebius CPU VM running the gateway and Caddy (automatic HTTPS) with Docker Compose, set up by a cloud-init script.

## How we use NVIDIA Nemotron, Token Factory and Nebius

| Component | Model / service | Role |
|---|---|---|
| Teacher | `nvidia/Nemotron-3-Ultra-550b-a55b` on **Nebius Token Factory** | Answers hard requests; its answers become training data (OpenMDW-1.1 allows this) |
| Judge | `nvidia/nemotron-3-super-120b-a12b` on **Token Factory** | Reference-based scoring of free-text answers, JSON mode |
| Fallback student | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` on **Token Factory** | Keeps the app running when our endpoint is stopped |
| Student | `nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16` + LoRA | The customer owns the adapter; the base stays under the NVIDIA Nemotron Open Model License |
| Training | **Nebius Serverless Jobs** (L40S) | Nightly LoRA fine-tune |
| Serving | **Nebius Serverless Endpoints** (vLLM) | Student inference with runtime LoRA loading |
| Storage | **Nebius Object Storage** | Datasets and adapters |
| Demo hosting | **Nebius AI Cloud** CPU VM (`2vcpu-8gb`) | Runs the gateway, the pages and Scout for judges |
| Web search | **Tavily Search API** (not NVIDIA or Nebius) | Scout's searcher agent (`search_depth: basic`, 1 credit per query) |

Where Token Factory sped us up: one OpenAI-compatible API for teacher, judge and fallback; three Nemotron sizes on the same endpoint made routing by difficulty trivial.

## Challenges we ran into

- Token Factory can't fine-tune or serve a tuned Nemotron yet, so we moved training and serving to Nebius AI Cloud Serverless.
- Our first simulation rejected every new model. Shadow mode judged candidates on all traffic, not just the answers they would actually serve. We redesigned it around precision and coverage, with the teacher breaking ties.
- Deduplication made the golden set over-represent rare phrasings, and frequent requests were held out forever. Now requests seen 3+ times always go to training.
- Comparing a new model against the live model's old score, measured on an older and easier golden set, made every newer model look like a regression. Now the live model is re-scored on the same set each night.
- Tests caught a tie-handling bug in threshold calibration that could promise 95% precision and deliver 94%.

## Accomplishments we're proud of

- A full, tested loop: 1750 tests (96% line coverage), including end-to-end simulations of cold start, drift and rollback, Scout end to end, the privacy and security features, and every live-mode entry point run offline against fake Nebius endpoints. CI traces every PRD requirement to its code and tests, and lists the ones that can only be checked live.
- In simulation (mock models): the student answered 98% of requests by day 3, savings reached 85–87%, a corrupted deploy was rolled back at 41% audited precision, and a product-launch drift recovered within four nights.
- In simulation (mock models), on Scout's triage and reader calls: the student answered 92% of them by day 8 and that day cost 81% less than all-Ultra (62% over all 8 days, including the cold start), with per-task calibration on (the default), which holds each of the two tasks to its own threshold; with one pooled threshold (`RATCHET_PER_TASK_CALIBRATION=0`, v0.4) 96%, 85% and 63%.
- Live Scout run: [LIVE NUMBER] of [LIVE NUMBER] LLM calls answered by the student, [LIVE NUMBER]% cheaper than all-Ultra.
- Live run: [LIVE NUMBER]% cost reduction, [LIVE NUMBER]% audited precision, [LIVE NUMBER] ms median student latency vs [LIVE NUMBER] ms for Ultra.
- License-clean provenance with a signed bill of materials for every adapter.
- A cost report that admits when self-hosting loses money: in simulation, 1,500 requests a day save money per token and lose it by GPU-hours on a 24/7 endpoint, and the report recommends the hosted fallback. Live: [LIVE NUMBER] endpoint hours and [LIVE NUMBER]% savings by GPU-hours paid.

## What we learned

- For a cascade, raw accuracy is the wrong metric. What matters is the precision of what the model chooses to serve and how much it serves.
- Release decisions need held-out data and statistical bounds; single numbers on small samples lie.
- Data licensing is a product feature, not a footnote.

## What's next

A bundled name and address detector (the plugin hook exists; candidates: NVIDIA's gliner-PII, Presidio) on top of the pattern-based redactor, a Postgres ledger, multiple tasks per deployment with their own golden sets, Nemotron Nano 30B students, and a managed Ratchet Cloud with savings-based pricing built on the per-client receipts.

## Built with

python, fastapi, httpx, sqlite, vllm, trl, peft, nvidia-nemotron, nemotron-3-ultra, nemotron-3-super, nemotron-3-nano, nebius-token-factory, nebius-ai-cloud, nebius-serverless-jobs, nebius-serverless-endpoints, nebius-object-storage, nebius-compute, lora, tavily, caddy, docker

---

## Feedback on Nebius and NVIDIA tools (draft)

**What worked well**

- One OpenAI-compatible API for every Nemotron size made routing by difficulty very simple.
- Nemotron 3 Ultra's OpenMDW-1.1 license is clear that outputs are unrestricted, which made a license-clean distillation product possible.
- Serverless Jobs and Endpoints billed per second suit a nightly training loop: a stopped endpoint is not billed for compute or its own storage, though a mounted Object Storage bucket still is ([Serverless overview](https://docs.nebius.com/serverless/overview), read Oct 21, 2026).

**Friction we hit**

1. **No Nemotron fine-tuning on Token Factory.** The [post-training model list](https://docs.tokenfactory.nebius.com/post-training/models.md) has 49 models from the Qwen, Llama, Gemma, gpt-oss and DeepSeek families and no Nemotron (read Oct 21, 2026). We had to build our own training job.
2. **Serverless pricing is hard to find.** We could find on-demand GPU prices but not clear Serverless Jobs/Endpoints rates.
3. **Terms of Service 5(f).** "Develop a product or service that competes with the Service" is broad. A gateway built entirely on Nebius that reduces token spend could be read either way. A short FAQ for builders would help. (We emailed support on Oct 19, 2026.) [UPDATE WITH REPLY]
4. **Custom weights on dedicated endpoints** are "currently in beta and available on request" ([custom weights](https://docs.tokenfactory.nebius.com/ai-models-inference/dedicated-endpoints/custom-weights.md), read Oct 21, 2026), with supported configurations undocumented. Serving a Nemotron Nano adapter there would remove the need for our own vLLM endpoint.
5. [ADD ANY ISSUES FROM THE LIVE RUN: logprobs, JSON mode, CLI flags, cold starts]

**Requests**

- Nemotron Nano in Token Factory fine-tuning and serverless LoRA.
- Published Serverless rates next to on-demand rates.
- A builder FAQ on ToS 5(f).

---

## How to verify our claims

### Live numbers (after the live run)

| What | How |
|---|---|
| Every live number, with how it was computed and the run that measured it | `evidence/live/results.json` (`values` and `sources`, one `live.*` id per number; `docs/LIVE_RESULTS.md` is its readable form) |
| The client-side numbers, recomputed from the per-request records | `python -m ratchet.liverun verify evidence/live` |

## Submission checklist

- [ ] Working project uses NVIDIA Nemotron on Token Factory and Nebius AI Cloud
- [ ] Hosted demo runs in **live mode** (Stage One needs a runtime Token Factory call; the Tavily prize needs a runtime Tavily call)
- [ ] Token Factory and Tavily credits last until Dec 15, 2026 (or are topped up); budget guard set accordingly
- [ ] Judge access set and delivered: `RATCHET_SCOUT_JUDGE_CODE` and `RATCHET_SCOUT_JUDGE_RUNS_PER_DAY` in `deploy/vm/.env`, the code in the testing instructions only (unverified who sees them; fallback: email the managers), never on the project page or in the video
- [ ] Tavily credit cycle set: `RATCHET_TAVILY_MONTHLY_CREDITS`, `RATCHET_TAVILY_CYCLE_DAY` (the dashboard's reset day) and `RATCHET_TAVILY_PACE_UNTIL=2026-12-15`
- [ ] `curl -s https://<domain>/scout/status | jq .search` shows `"available": true`, and a judge session works from the Scout page (`"session": true`)
- [ ] Track selected: Best Apps and Agents
- [ ] Project description (this file, sections above)
- [ ] Working demo URL, tested logged-out, stays live through Dec 15, 2026
- [ ] Demo VM on Nebius up and checked ([DEPLOY_NEBIUS_VM.md](DEPLOY_NEBIUS_VM.md#4-check-it-works)): `/`, `/scout`, `/dashboard` load over HTTPS; budget guard set; `/v1/*` protected (`RATCHET_API_KEYS` or `RATCHET_API_GUARD=guarded`) in live mode
- [ ] Real numbers from the live run in the description and video, including the total Nebius bill (failed jobs included) and the cost report's break-even line
- [ ] `make live-run` published (`python -m ratchet.liverun report <run dir> --publish`), and `python -m ratchet.liverun verify evidence/live` exits 0
- [ ] Nebius bill entered (`python -m ratchet.liverun report <run dir> --bill-usd X --bill-note "..."`)
- [ ] Best Use of Tavily: prize confirmed on Devpost, selected if it needs opting in, and Tavily use described (Scout's searcher)
- [ ] Demo video under 3 minutes, public on YouTube, no unlicensed music or third-party trademarks ([DEMO_SCRIPT.md](DEMO_SCRIPT.md))
- [ ] Public GitHub repo with Apache-2.0 license visible at the top of the repo page
- [ ] README with setup and run instructions
- [ ] README explains how Nemotron is used, where Token Factory helped, and other Nebius services used
- [ ] Feedback section filled in (above)
- [ ] "Significant updates" note not needed: project created during the submission period (Oct 2026)
- [ ] City selection: only if a Builders & Brews event was attended
- [ ] All `[LIVE NUMBER]` placeholders and `[DEMO URL]` replaced
- [ ] Re-read the Official Rules once more before submitting
