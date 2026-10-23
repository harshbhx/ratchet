# Devpost text, as of 24 Oct 2026

The text to paste. It says only what ran. `HACKATHON_SUBMISSION.md` holds the older planning notes; where the two differ, this file is right. Replace `[LIVE DEMO URL]` and `[VIDEO URL]` when they exist.

## Project name

Ratchet

## Tagline

An OpenAI-compatible gateway that lets a small open model answer first, a big one answer when it is unsure, and the small one learn from the big one's answers, with every lesson's license on record.

## Inspiration

Most app and agent traffic is narrow and repetitive, and every request is billed at frontier prices. Distilling a big model into a small one fixes that, but it is manual work, and when the big model is a closed one its terms forbid training on its outputs. We wanted a tool that does it on open models, measures honestly whether the small model is ready, and refuses to serve it when it is not.

## What it does

You change one line, the base URL. A small Nemotron 3 Nano 4B adapter you own answers what it is sure of; Nemotron 3 Ultra on Nebius Token Factory answers the rest and its answers, with their teacher and license, become the next adapter's lessons. A confidence gate decides who answers, random audits measure how often the small model is really right, and a new adapter reaches users step by step (shadow, then 10%, 50%, 100%) with automatic rollback.

## What is real, what is simulation, and what was not run

| | State |
|---|---|
| Teacher data: 4,322 text-to-SQL examples written by Nemotron 3 Ultra on Token Factory, every query executed | **Real** (22 to 23 Oct) |
| Two LoRA adapters trained on it (821 and 4,322 examples) | **Real**, on a free Colab T4 and a rented Modal T4. Not on Nebius GPUs |
| Two held-out exams, scored strictly, every answer saved and recomputable | **Measured**: the round 2 adapter scores 39.0% and 33.0%, the untrained model 12.8% and 12.5% |
| Adapter, training data and exams published | **Real**, on Hugging Face (links below) |
| The gateway in live mode, answering through real Token Factory calls | **Real**: 20 requests on 23 Oct, about $0.01, records published; the live demo is the same gateway (`[LIVE DEMO URL]`) |
| The cascade, release gate and rollback demo, the dashboard's savings | **Simulation** (mock models), labelled as such wherever it appears |
| Training and serving the student on Nebius Serverless Jobs and Endpoints | **Not run**: scripted and tested offline |
| Scout with real Tavily searches | **Not recorded yet** |

## Results

Two exams of text-to-SQL questions on databases the adapter never saw, scored strictly (an answer counts only if it returns exactly the reference rows). Exam 1 (172 questions) was written by the teacher; exam 2 (200 questions, new industries) by a different model, for testing only.

| | Exam 1 | Exam 2 |
|---|---|---|
| Untrained Nano 4B | 12.8% | 12.5% |
| Teacher (Nemotron 3 Ultra), same prompts | 25.6% | 15.0% |
| Adapter, round 1 (821 examples) | 33.1% | 28.0% |
| **Adapter, round 2 (4,322 examples)** | **39.0%** | **33.0%** |

The adapter scores about 2.6 to 3 times the untrained model on both exams. Round 2 beat round 1 by 10 questions on both, so more data helped, though each result alone could partly be chance (p = 0.12 and 0.13). **It does not replace the teacher yet:** when we ask for 95% right, the student answers about 2% of questions or none, and the gate sends the rest to the teacher, which is the gate doing its job. At a 70% bar, the share it can answer rose from 13% to 26% on exam 1 and from 2% to 10% on exam 2 between rounds, measured by replaying the routing rule on the real answers. The strict exam has one reference answer per question, so even the teacher scores 26% and 15%; we do not claim the adapter beats it. Cost: about $15 of teacher answers and about $8 of rented GPU.

## How we built it

A FastAPI gateway with a provenance ledger (every example keeps its teacher and license, closed-model teachers are refused in code), personal-data redaction before anything is stored, a nightly training loop with a held-out golden set, and a release gate with statistical bounds. A signed bill of materials travels with each adapter. A React web app (story page, Scout agent demo, dashboard, a judges' page whose "What's real" table gives every claim one status and a way to check it), a 138-page docs site, and 1,909 offline tests. Every number on the pages is computed from saved answers, and a test fails if they drift.

## How we use NVIDIA Nemotron, Token Factory and Nebius

- **Nemotron 3 Ultra** on **Nebius Token Factory**: the teacher. It wrote all the training data and exam 1, its own answers to both exams are scored as the teacher's, and it answers every request in the live demo.
- **Nemotron 3 Nano 4B**: the student's base model, fine-tuned with LoRA.
- **Token Factory** also lists the hosted Nano 30B (the fallback student) and Super (the judge), which the gateway is built to use; we did not exercise them in a run.
- **Nebius AI Cloud GPUs** (Serverless Jobs and Endpoints) are scripted but were not run.

## Built during the hackathon

The repository was created on 18 Oct 2026, after the submission period began (26 Aug), so all of it is new work: the gateway, the cascade, the release gate, the ledger, Night School, Scout, the web app, the docs, the simulator and the evidence tooling were written between 18 and 24 Oct. Nothing was carried over from earlier work. `main`'s commit history was rewritten once, on 22 Oct, for cleanup. In the last days we added the real text-to-SQL training data, two training rounds, two exams with their scorers, the Hugging Face releases and the live demo.

## Challenges we ran into

- The first slow, real exam answering took about six minutes a row. The Nemotron 3 Nano model's own code recomputed the whole prompt for every token; our decoder fixes the cache path and runs about 45 times faster with identical answers.
- Free Colab GPUs were reclaimed every 20 minutes or so, so a 66-minute training run took 15 attempts, plus an attempt on another cloud, before we moved round 2 to a detached, cost-capped job.
- Our first scorer compared text, not results. We replaced it with one that runs both queries and compares rows, and published every answer so anyone can recompute the scores.
- Our first exam was written by the same model that wrote the training data, which flatters it. We added a second exam by a different author.

## What we learned

For a cascade, the question is not accuracy but what the model is sure of and how much of the traffic that covers. A small model can be 2.6 to 3 times better than untrained and still not be safe to serve on its own; a gate that says so is the product.

## Links

- Live demo: `[LIVE DEMO URL]` · simulation demo: https://demo.ratchet.dualmindlab.tech · story, results and judges' page: https://ratchet.dualmindlab.tech · docs: https://ratchet-docs.pages.dev
- Code: https://github.com/harshbhx/ratchet
- Adapter: https://huggingface.co/harshbhx/ratchet-nemotron-nano-4b-text2sql · training data: https://huggingface.co/datasets/harshbhx/ratchet-text2sql-synthetic · exams and answers: https://huggingface.co/datasets/harshbhx/ratchet-text2sql-exams
- Video: `[VIDEO URL]`

## Feedback on Nebius and NVIDIA tools

**What worked well**

- One OpenAI-compatible API for every Nemotron size; the teacher answered a typical short request in about a second.
- Nemotron 3 Ultra's OpenMDW-1.1 license is clear that outputs are unrestricted, which made a license-clean distillation product possible.
- `chat_template_kwargs: {"enable_thinking": false}` works on Token Factory and was essential: with thinking on, the model's reasoning ate the output limit of our labelling calls.
- Cheap data: 4,322 verified examples plus two exams cost about $15.

**Friction we hit**

1. **No Nemotron fine-tuning on Token Factory.** The [post-training model list](https://docs.tokenfactory.nebius.com/post-training/models.md) has 49 models from other families and no Nemotron (read 21 Oct 2026), so we trained elsewhere.
2. **GPU access on AI Cloud needs a prepayment.** For a project on a student budget that was the barrier to training on Nebius GPUs at all; hackathon GPU credits would have let us run the Serverless Jobs path we scripted.
3. **Serverless pricing is hard to find** next to the on-demand GPU prices (read 21 Oct 2026).
4. **Custom weights on dedicated endpoints** are "currently in beta and available on request" ([docs](https://docs.tokenfactory.nebius.com/ai-models-inference/dedicated-endpoints/custom-weights.md), read 21 Oct 2026).
5. **Nemotron 3 Nano on a small GPU, four things cost us days:** (a) the model's `generate` path passes its cache under a name its forward ignores, so every token recomputes the prompt; (b) with 4-bit quantisation the `in_proj`, `out_proj` and `lm_head` layers must stay in fp16 because the Mamba kernels read them directly; (c) the first training step spends 15 to 25 minutes compiling Triton kernels on two CPU cores while the GPU idles, and a shipped kernel cache would remove it; (d) the `mamba-ssm` and `causal-conv1d` kernels we compiled on Colab (CUDA 13) did not load on CUDA 12 machines, so each environment needed its own hour-long rebuild.

**Requests**

- Nemotron Nano in Token Factory fine-tuning and serverless LoRA.
- Hackathon GPU credits for AI Cloud, or a free tier for Serverless Jobs.
- Published Serverless rates next to on-demand rates.
- Prebuilt kernels or a kernel cache for the hybrid Mamba models, and a fix to the cache argument in the model's generation code.

## Video: what to show, what to avoid

Under 3 minutes, public on YouTube, no third-party trademarks or copyrighted music. Lead with the strongest moment.

1. 0:00 to 0:15, the problem: repetitive requests at frontier prices.
2. 0:15 to 0:40, the idea: one changed line, a small adapter first, the big model on doubt.
3. 0:40 to 1:40, **the real results**: the two exam charts, who wrote each exam, "2.6 to 3 times the untrained model", and the honest limit.
4. 1:40 to 2:10, **proof it is real**: the Hugging Face adapter page with its license, the terminal printing `OK: every number … recomputed`, your Nebius usage page, the judges' "What's real" table.
5. 2:10 to 2:40, **a live request** through the live demo (real Token Factory call, request id and cost visible), then the simulation demo's rollback with "Simulation" on screen.
6. 2:40 to 3:00, close: what is real, what is not yet, the links.

Avoid on screen: other companies' names and logos (for example the docs' "Ask AI" menu, or the exam 2 author's name: say "a different lab's model"), "beats the teacher", "trained on Nebius GPUs", and any simulated number said as if it were real.
