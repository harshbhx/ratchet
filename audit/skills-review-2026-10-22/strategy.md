# Ratchet: hackathon strategy review

Reviewer persona: hackathon strategist and former judge. Read-only review of `harshbhx/ratchet` main at `3c9d063` (Oct 23 commit date, reviewed Oct 22). Solo entrant, no GPU, $25-50 of Token Factory credit. Submissions close Oct 30, 2026, 10:00 AM PT; target Oct 29. Judging Dec 1-15.

Rubric used: the draft's own reading of the rules (Stage One pass/fail, then four equal criteria: Technological Implementation, Design, Potential Impact, Quality of the Idea). It comes from web-search snippets, so it is unverified. The six scores below are the axes you asked for; the last table maps them onto the four official criteria.

---

## 0. Verdict in one paragraph

This is the best-engineered solo entry I would expect to see in this field, and it is at real risk of failing Stage One. **No live-mode gateway exists today.** I checked: `https://demo.ratchet.dualmindlab.tech/healthz` returns `{"status":"ok","version":"0.4.0","mode":"mock"}`, and `https://ratchet.dualmindlab.tech/healthz` returns the static landing page's HTML, so the "Live site" is a static page, not the gateway. The submission draft also claims four Nebius AI Cloud services (Serverless Jobs, Serverless Endpoints, Object Storage, a CPU VM) that have never run and, with no prepayment, will not run before the deadline. In the 221 commits since Oct 19 the effort has gone into features (drift martingales, Merkle proofs, savings receipts, teacher watch) while the one thing the rules require, a live Token Factory call during judging, has not shipped. **Stop adding features today.** For the next 7 days: go live, measure one real number, finish the one real training result, make the text honest, record the video.

---

## 1. Scores today (out of 10)

| Axis | Score | Why |
|---|---|---|
| **Technical depth** | **8** | Deep and real in code: cascade with calibrated confidence, a golden gate with champion-vs-challenger rescoring, shadow arbitration, canary with Wilson or anytime-valid e-process rollback, Clopper-Pearson certificates, Ed25519-signed BOMs, a requirements traceability check in CI, about 1,900 offline tests. It loses 2 points because none of it has touched a real model on the serving path. A judge who asks "does this work with Nemotron?" gets "in simulation". |
| **Use of NVIDIA / Nebius** | **5** | The design uses three Nemotron sizes and Token Factory well. In practice the only real Token Factory use is the text-to-SQL data generation on Oct 22, and no Nebius AI Cloud service has run at all. The draft lists five Nebius services as used ("Built with: ... nebius-serverless-jobs, nebius-serverless-endpoints, nebius-object-storage, nebius-compute"), and a Nebius judge will notice the gap. This can reach **7** honestly: Ultra teacher, Super judge and Nano student all on Token Factory at runtime, plus a Nemotron 3 Nano 4B LoRA trained on Ultra's outputs. |
| **Innovation** | **7** | The ideas themselves are known, and the README is honest about that (FrugalGPT, Cache & Distil, Online Cascade Learning, NVIDIA Data Flywheel). What is new is the combination: license lineage recorded per example and signed into each adapter, plus automatic promotion behind statistical gates. "NVIDIA's own blueprint says *flashlight, not autopilot*; Ratchet is the autopilot with brakes" is a strong, sourced line. It loses points because it is infrastructure, so the novelty is hard to feel in a 3-minute video. |
| **Completeness** | **4** | Complete in simulation, missing where judges check: no live gateway, no live numbers (every `[LIVE NUMBER]` is still a placeholder), the SQL results file reads `{"status": "pending"}`, the feedback section has `[UPDATE WITH REPLY]` and `[ADD ANY ISSUES FROM THE LIVE RUN]`, and the README's top table of NVIDIA and Nebius services describes a deployment that does not exist. |
| **Demo quality** | **5** | The mock demo is polished: the React story page, Scout lanes, the dashboard and the rollback drill are judge-friendly, and simulation is labelled. But the deployed demo is mock only, the "live site" is a static page, and nothing a judge can open today makes a Token Factory or Tavily call. The planned video records the mock demo with "Nano · yours" badges, which shows a trained student that does not exist live. |
| **Presentation** | **5** | The honesty discipline is excellent ("Status, honestly", Limitations, labelled simulation). The volume works against it: the README is 422 lines and about 45 KB across about 25 sections, and the draft packs most of the v0.5 changelog into "How we built it". A judge with 5 minutes per entry will not reach the good parts. The draft file mixes internal notes (prize table, past winners, Stage One checklist) with paste-ready text, which invites a bad paste. |

**Weighted on the official four criteria (equal weight):**

| Official criterion | Today | After the 7-day plan |
|---|---|---|
| Technological Implementation | 6.5 (depth 8, pulled down by no live run) | 8 |
| Design | 6 (good UI; README and draft overwhelm) | 7.5 |
| Potential Impact | 6 (real problem, unproven numbers) | 7.5 (one real cost number and one real adapter result) |
| Quality of the Idea | 7.5 | 8 |
| **Average** | **6.5**, and **0 if Stage One fails** | **about 7.75**: top-10 range, a realistic shot at 3rd overall or a track win |

---

## 2. The five highest-leverage actions, Oct 22-29 (ordered)

Constraints: no GPU, $25-50 of Token Factory credit, solo. Each action costs only hours and cents.

### 1. Put a live-mode gateway on the internet and keep it up until Dec 15 (Stage One). 8-12 h. Impact: pass/fail.

- **Day-1 check first** (1 h, cents): `make day1` with `DAY1_CHECKS="all --json evidence/day1/day1.json"`. Confirm the model ids, logprobs on the hosted Nano 30B, JSON mode, and whether `chat_template_kwargs` turns reasoning off. Ultra's reasoning tokens are the biggest unknown in the cost estimate: if Ultra thinks on every planner and writer call, the "$0.02-0.03 per Scout run" in DEPLOY_NEBIUS_VM.md could be several times higher.
- **Host.** The plan in every doc is a Nebius CPU VM, but that needs the AI Cloud prepayment you cannot make. Stage One accepts "a runtime call to Token Factory **or** running on Nebius AI Cloud", so a live gateway hosted anywhere that calls Token Factory passes. In order of preference:
  1. **Ask for credits first (30 min, today).** Post in the hackathon Discord or email Nebius DevRel: "solo builder, the $25 AI Cloud prepayment blocks me from deploying on Nebius; are there hackathon credits?" A yes unlocks the VM story and maybe a GPU hour. A no costs nothing, and the same paragraph is your best "Most Valuable Feedback" entry.
  2. **A VM with a persistent disk.** For example a new cloud account's free-trial credit, or Oracle's Always Free tier, using the existing `deploy/vm/` Docker Compose and Caddy kit unchanged. A persistent disk matters: invariant 11's guard restore and the dashboard's history both need the SQLite ledger to survive a restart.
  3. **The existing Cloudflare Container** with `RATCHET_MODE=live` and secrets. It is the fastest, but its state is in memory (`deploy/cloudflare/README.md`: "State is in memory and resets when the container restarts"). Every restart resets the Scout guard's daily counters and empties the dashboard. Use it only with a low daily cap, and accept the reset risk knowingly.
- **Configure for a tiny budget.** The student is the hosted Nano 30B fallback, labelled. Set `RATCHET_SCOUT_DAILY_BUDGET_USD` to about 0.20 from Oct 29 to Nov 30 (Stage One reviewers need only one call) and about 0.75 from Dec 1 to 15, and set the judge code and runs per day (ADR 0120). Lock `/v1/*` with `RATCHET_API_KEYS` or `RATCHET_API_GUARD=guarded`: an open `/v1` is not covered by the Scout budget and can drain the credit in an afternoon.
- **Monitor it.** Add a free uptime monitor on `/readyz` and `/scout/status` with email alerts. Make sure the page shows the time of the last Token Factory call even when the day's budget is used up, so a Stage One reviewer always sees evidence of a runtime call.
- **Exit check:** logged out, in a private window, the live URL runs a Scout question end to end, with real Tavily URLs and Ultra and Nano badges.

### 2. Measure one real number through the live gateway. 5-7 h, about $5-8. Impact: fills the `[LIVE NUMBER]`s; judges in this sponsor's past events rewarded measured over polished.

- `make live-smoke`, then `python -m ratchet.liverun run --nights off --untuned-arm --max-usd 6` on the tickets workload. The harness already supports nights off and a control arm that calls Ultra directly. **Do not use the Makefile default `MAX_USD ?= 30`**: that is most of your credit.
- The honest headline this produces: *"Before any training, an untuned Nemotron 3 Nano answered X% of ticket-triage calls at Y% audited agreement with Ultra, Z% cheaper than a measured all-Ultra control, median latency A ms against B ms."* That is a real cascade number on Token Factory. Then: "and here is what one night of training adds" (action 3).
- Run one Scout question live for the Scout `[LIVE NUMBER]` line, then `liverun report --publish` and `liverun verify evidence/live`.

### 3. Finish and publish the text-to-SQL adapter: the one real training result. 6-10 h, free (Colab T4). Impact: turns "own it tomorrow" from simulation into evidence; strongest NVIDIA story (Ultra-to-Nano-4B distillation).

- Get `results.json` out of `measure_adapter.py report`: base Nano 4B against base plus LoRA on unseen databases, with Wilson intervals. Commit it so `docs-site/src/data/sql-results.json` stops saying `"pending"`.
- **Join it to the product.** Write the adapter's BOM and NOTICE through Ratchet's own `bom` and `notices` code (teacher Nemotron 3 Ultra, OpenMDW-1.1, dataset hash, golden-set certificate), sign it, and show `python -m ratchet.bom verify` printing OK in the video. Without this, the SQL work reads as a side project. With it, it is "Night School, run by hand on a T4".
- Publish to Hugging Face only after the ToS check in risk 4 below.
- If the adapter does not beat base, publish anyway with the intervals. An honest null result still makes the point that the gate exists to catch exactly that.

### 4. Make the README and submission match reality. 4-5 h. Impact: credibility with Nebius judges, Design score, and avoiding a misrepresentation flag.

- Rewrite the NVIDIA and Nebius table in two columns: **Runs today** (Token Factory with Ultra, Super and Nano; Tavily; a Nano 4B LoRA trained on Ultra outputs on a Colab T4) and **Written, scripted, not run, no GPU budget** (Serverless Jobs and Endpoints, Object Storage, the Nebius VM). Drop the unused services from "Built with".
- Cut the README's first screen to: one-liner, diagram, three real facts (live Token Factory cascade number, SQL adapter result, simulation loop with rollback, labelled), a "try it" link to the **live** gateway and to the mock demo, and the status box. Move the deep sections (certificates, Merkle proofs, replay, receipts, drift, teacher watch) to the docs site behind one "Under the hood" link.
- Split `docs/HACKATHON_SUBMISSION.md` into a **paste-only file** and an internal checklist, so the prize table, Stage One notes and "past winners" research cannot end up on Devpost.
- Rewrite the feedback section around friction you actually hit. The $25 prepayment barrier for a student builder, Nemotron missing from Token Factory fine-tuning, and the ToS 5(f) ambiguity are each concrete and quotable, and that section has its own prize (10 × $100).
- Overclaims to fix are quoted in section 5.

### 5. Record the video with the live run first. 6-8 h including edit. Impact: judges "need not watch past 3 minutes" and may never open the demo, so the video is the entry.

- Script in section 6. Record the live segment on the deployed URL with the address bar visible. Record the simulation segment with the "Simulation" label in frame. Upload unlisted and get a second viewer, then make it public on Oct 28.

**Order and calendar:** Oct 22 credits ask plus day-1 check · Oct 23-24 live host (action 1) · Oct 24 live-smoke, Oct 25 live run (action 2) · in parallel Oct 23-25, the Colab training runs unattended (action 3) · Oct 26 text rewrite (action 4) · Oct 27-28 video (action 5) · Oct 29 submit · Oct 30 morning buffer only.

---

## 3. What to cut or hide

- **Freeze features now.** Nothing in the v0.5 changelog moves a score now; every hour on it is an hour not spent on Stage One. Lanes D, E, F and G are done for this hackathon.
- **Out of the video and the Devpost text, kept in the repo:** drift alarm (conformal martingales), teacher watch, savings receipts (RFC 9162 trees), Merkle training-set proofs, ledger replay, erasure and retention, admin API, idempotency, the sidecar, SQL-by-execution tasks, Indian-identifier redaction. At most one line: "Under the hood: certificates, signed lineage, drift and teacher checks; see the docs."
- **Cut from the demo script:** the redactor paste (2:28) and the task registry. Keep one signed-BOM verify moment.
- **Cut the SM-5 "not met" row** from the video only. It belongs in the README's honesty section, not in a 3-minute pitch.
- **Hide the `sim.<domain>` and Nebius VM wording.** One live URL and one mock demo URL, named plainly.
- **Cut the doubled numbers.** The draft gives Scout numbers twice ("92% ... 81% ... 62% ... with one pooled threshold ... 96%, 85% and 63%"). Pick one setting.
- **Don't show "Nano · yours" badges from mock mode** in anything that could be read as live.

---

## 4. Risks that could disqualify or sink the entry

| # | Risk | Likelihood / severity | Mitigation |
|---|---|---|---|
| 1 | **Stage One fails: no runtime Token Factory call during review.** Today the only deployed gateway is `"mode":"mock"`. | High / fatal | Action 1, by Oct 24, not Oct 28. Keep the last-call timestamp visible. Put the live URL first in the testing instructions. |
| 2 | **Credit runs out before Dec 15, so the demo dies mid-judging.** Spend: the SQL data so far, day-1, smoke and live run, rehearsals, plus 48 days of public traffic. Ultra reasoning tokens may be 3-5× the estimate. An in-memory host resets the guard on each restart. An open `/v1` is not under the Scout budget. | Medium-high / fatal in judging | Budget: day-1 and smoke about $2, live run $6 or less, video $2, judging window about $18 (0.20/day for 32 days plus 0.75/day for 15 days). Total about $28. With only $25, drop the live run to $4. Persistent disk, a locked `/v1`, a weekly balance check. If Token Factory offers per-key spend limits, use a separate demo key. |
| 3 | **Overclaims about Nebius services.** The draft says training, serving, storage and hosting run on Nebius AI Cloud. A Nebius judge can see in their console data that this account has no AI Cloud usage. At best credibility drops across the whole entry; at worst it reads as misrepresentation. | High if unfixed / severe | Action 4. Say "designed and scripted for Serverless; not run (no GPU budget)". Honesty is already this project's brand, so apply it to the submission text. |
| 4 | **ToS 5(f) and training on Token Factory outputs.** OpenMDW-1.1 frees Ultra's *outputs*, but Token Factory's own ToS can still restrict what you do with the *service* ("develop a product or service that competes with the Service"). Publishing on Hugging Face a Nano adapter trained on Token Factory outputs is the most exposed act. The support email from Oct 19 is unanswered. | Medium / could sink it with sponsor judges | (a) Follow up today in the hackathon Discord, publicly, quoting 5(f). Organizers answer there fast, and a public answer is evidence. (b) **Reframe**: Ratchet moves spend from Ultra to Nano *on Token Factory* (the live demo's student is Token Factory's Nano) and serves adapters on Nebius Serverless. It keeps the customer on Nebius instead of competing with it. Say so in the pitch. (c) Hold the HF upload until there is an answer or a clear no-objection. The results and BOM can be published without the weights. |
| 5 | **Demo downtime or cold start during review.** Solo maintainer, unverified deploy (`@cloudflare/containers` "not pinned"), mock warm-up CPU at boot, Tavily monthly credit cycle. | Medium / severe | Uptime alerts, pinned versions, a restart runbook you have tried once, `RATCHET_TAVILY_PACE_UNTIL=2026-12-15`, and a search-unavailable path that still makes the Token Factory call (already built). |
| 6 | **Video shows simulation that reads as live.** The current script records mock mode only, and its 2:42 numbers slide waits on a live run. | Medium / credibility | Live segment first and labelled live. Simulation segment labelled on screen and in the voice-over. |
| 7 | **Track fit.** "Best Apps and Agents" is described as "useful applications and agents". Ratchet is infrastructure; Scout is the agent. | Low-medium | Lead the Devpost description and video with Scout as the agent, and Ratchet as what makes it cheaper over time. Re-check the four tracks on Devpost in case one fits infrastructure better. The cash prizes are overall anyway. |
| 8 | **Tavily prize lost.** It needs a runtime Tavily call, so it carries the same dependency as risk 1. | Medium / $3k | Covered by action 1. Show real Tavily result URLs in the video. |

**The biggest risk** is risk 1, made worse by risk 2. If the live gateway is not up and staying up through Dec 15 with a Token Factory call a reviewer can see, nothing else in the repo is scored.

---

## 5. Overclaims and confusing parts (quoted)

### `docs/HACKATHON_SUBMISSION.md`

1. *"Demo URLs: [DEMO URL] (live: Token Factory and Tavily ...) and [SIM URL] ..., both hosted on one Nebius VM"*. No Nebius VM exists, and the deployed demo is a Cloudflare Container in mock mode.
2. What it does, step 4: *"Every night, Night School fine-tunes a new student on a Nebius Serverless Job and tests it on a held-out golden set."* This has never run and cannot run without a GPU. Say "designed to run on a Nebius Serverless Job; the one adapter so far was trained on a Colab T4".
3. How we built it: *"retrains LoRA from the base model each night (TRL + PEFT on a Nebius Serverless Job ...), and hot-loads the adapter into vLLM on a Nebius Serverless Endpoint."* Same problem.
4. Services table rows *"Training | Nebius Serverless Jobs (L40S)"*, *"Serving | Nebius Serverless Endpoints (vLLM)"*, *"Storage | Nebius Object Storage"*, *"Demo hosting | Nebius AI Cloud CPU VM"*. All are presented as in use, and none is.
5. *"Hosting. One Nebius CPU VM running the gateway and Caddy (automatic HTTPS) with Docker Compose"*. Not true.
6. Challenges: *"Token Factory can't fine-tune or serve a tuned Nemotron yet, so we moved training and serving to Nebius AI Cloud Serverless."* This reads as done. Say "so we wrote training and serving for Nebius AI Cloud Serverless; without GPU credit, we trained on a Colab T4".
7. Built with: *"vllm, ... nebius-ai-cloud, nebius-serverless-jobs, nebius-serverless-endpoints, nebius-object-storage, nebius-compute"*. Remove what did not run.
8. *"Fallback student | ... Nano-30B-A3B on Token Factory | Keeps the app running when our endpoint is stopped"*. There is no endpoint: during judging this model *is* the student. Say so; it is a better story ("all three Nemotron sizes on Token Factory").
9. Scout: *"a badge for who answered it (your model or Ultra)"*. In the live demo it is the untuned hosted Nano, not "your model". The UI has a fallback badge, so make the text match it.
10. *"The student is sampled 3 times with logprobs."* Logprobs on Token Factory's Nano are unverified (PRD Q2). Confirm with the day-1 check or soften.
11. *"1900 tests (96% line coverage)"*. v0.4 shipped at 87%, and CLAUDE.md still says 1781 tests. Re-run `pytest -q --cov` and quote what it prints.
12. Feedback: *"Serverless Jobs and Endpoints billed per second suit a nightly training loop"*. This is praise for a service you have not used, taken from its docs. Nebius reviewers will notice. Replace it with the prepayment barrier.
13. The file says *"Ready to paste"* but also holds the prize table, registrant counts, "past winners" research and the internal checklist. Split it.

### `README.md`

1. *"[Live site](https://ratchet.dualmindlab.tech)"*. It is a static landing page: `/healthz` and `/v1/models` both return its HTML. The working mock demo, `demo.ratchet.dualmindlab.tech`, is not linked from the README.
2. Status: *"Live mode runs against Token Factory. The teacher and the judge are real calls; the live-run harness measures them."* Present tense, no measured run. Elsewhere the README says *"As of Oct 21, 2026, Ratchet has not run on Nebius yet"*, and the judges' guide says *"live mode stays unverified until `scripts/day1_check.py` passes"*. Three statements, three different impressions.
3. *"A text-to-SQL adapter was trained on a free Colab T4. Its results will be published separately on Hugging Face."* The results file is `"pending"`. Don't claim the result before the file exists.
4. *"## Hosting the demo — The public demo runs on one small Nebius CPU VM"*. False today.
5. The "How it uses NVIDIA and Nebius" table has the same problem as draft items 4 and 8.
6. Structure: the heading *"## Proving what an adapter was trained on"* is followed straight away by the drift-alarm paragraph (*"A certificate holds only while traffic looks like what it was measured on. A drift alarm ..."*). The teacher-watch paragraph (*"Every one of these numbers is agreement with the teacher, so the teacher watch ..."*) is attached to "Savings you can check" with no heading or blank line. These look like merge leftovers.
7. Length: 422 lines and about 45 KB. "At a glance" is good; everything after "Status, honestly" should move to the docs site.
8. Small inconsistencies a careful judge will notice: `/healthz` reports `"version":"0.4.0"` while the README describes v0.5 features; the test count is "1900" in the README and "1781" in CLAUDE.md.

**Strengths to keep:** the "Status, honestly" box, the Limitations section, the sourced prior-art section, the NVIDIA lineage table that admits S3 and S4 are manual, and the "Simulation" labels everywhere.

---

## 6. The 3-minute video, in order

Lead with the live call: Stage One and Tavily evidence come first, and judges may stop early.

| Time | On screen | Point |
|---|---|---|
| 0:00-0:15 | Title card, then one line of text: "You can't legally train on GPT or Claude outputs." with the §3.3(e) quote | Hook: the bill grows, and the obvious fix breaks the terms |
| 0:15-0:30 | The one-line `base_url` change and the four-step diagram | What Ratchet is |
| 0:30-1:15 | **Live**, on the deployed URL with the address bar visible: a Scout question; planner on Ultra; real Tavily result URLs; triage and reader calls with Nano (hosted) or Ultra badges and an escalation; the live strip showing a Token Factory call; the cost against all-Ultra | Real Nemotron on Token Factory, real Tavily, an agent workload |
| 1:15-1:40 | **Real result:** the SQL adapter, base Nano 4B against the adapter on unseen databases with intervals ("trained on a free Colab T4 from Ultra's answers"); terminal: `python -m ratchet.bom verify ...` prints OK | Distillation works, and the lineage is signed and license-clean |
| 1:40-2:20 | **Simulation**, labelled: the dashboard over 10 days, student share 0 to 98%, release log shadow, 10%, 50%, 100%; then **Break next deploy**, and the automatic rollback event appears | The autopilot with brakes; no human in the loop |
| 2:20-2:40 | **Live numbers** card from `evidence/live` (untuned cascade share, audited agreement, cost against the measured Ultra control, latency); one line from the cost report: "at this volume, don't self-host; use the hosted Nano" | Measured, and honest about when it doesn't pay |
| 2:40-3:00 | Close: "Rent intelligence today. Own it tomorrow." Repo URL, live URL. One sentence on the next step: "Serverless training on Nebius when we have GPU credit" | Ask and roadmap |

Rules: real voice, no music, no third-party logos. Never show the judge code. Every simulated frame carries the label.

---

## 7. The pitch

> **Ratchet is a drop-in OpenAI-compatible gateway that turns your Nemotron 3 Ultra traffic into a small Nemotron model you own, learning only from teachers whose licenses allow it and shipping each new version only when a statistical gate says it is as good as the one in production.**

Shorter, for the video close: *"Ratchet turns your rented LLM calls into a model you own: legally, automatically, and only when the numbers say it's safe."*

---

## 8. Next steps, by time sensitivity

1. **Today (Oct 22):** post the credits request and the ToS 5(f) question in the hackathon Discord; run the day-1 check; freeze features.
2. **Oct 23-24:** live gateway up on a persistent host, `/v1` locked, budget tiers set, uptime monitor on. Start the Colab training.
3. **Oct 25:** live smoke, then the live run with `--nights off --untuned-arm --max-usd 6`, published and verified.
4. **Oct 26:** SQL results committed with a signed BOM; README and draft rewritten; paste-only file split out.
5. **Oct 27-28:** record, edit, second viewer, upload.
6. **Oct 29:** submit, then check the live URL logged out again. **Oct 30:** buffer only.
