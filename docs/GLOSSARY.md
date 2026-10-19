# Glossary

| Term | Meaning in Ratchet |
|---|---|
| **Teacher** | The large model that answers requests the student can't handle. Default: NVIDIA Nemotron 3 Ultra on Nebius Token Factory. Its answers become training data. |
| **Student** | The small model the customer owns. Default: NVIDIA Nemotron 3 Nano 4B plus a LoRA adapter, served by vLLM. |
| **Version** | One trained student adapter (`student-v1`, `student-v2`, ...). Each has its own eval numbers, threshold and state. |
| **Cascade** | Try the cheap model first; send the request to the expensive model only when needed. |
| **Escalation** | A request the student was not confident about, sent to the teacher. |
| **Confidence** | A score from 0 to 1: 0.4 × token probability of the best majority sample + 0.6 × agreement among samples. Invalid-format answers score 0. |
| **Threshold** | The confidence at or above which the student's answer is served. Calibrated per version. |
| **Calibration** | Picking the lowest threshold whose served answers reach a target precision (97%) on held-out data. |
| **Self-consistency / agreement** | How many of the student's n samples (default 3) give the same answer. |
| **Logprobs** | The model's log-probabilities for each generated token. Used to estimate how sure it is. |
| **Task** | A named workload (for example `ticket-triage`) that defines which answer fields must match for two answers to agree. |
| **Audit** | The teacher re-answers a random share of student-served requests (`audit_rate`) to measure live quality. |
| **Precision** | Of the answers a student serves (or would serve), the share that agree with the teacher. |
| **Coverage** | The share of requests a student serves (or would serve). Higher coverage = more savings. |
| **Golden set** | About 10% of unique requests, held out from training and used to evaluate new versions. Requests seen 3+ times leave the golden set and go to training. |
| **Golden gate** | The first release check: precision ≥ 95%, coverage ≥ 25%, and no regression against the live version on the same golden set. |
| **Champion vs challenger** | The live version (champion) is re-scored on the same golden set as the new candidate (challenger) before comparing them. |
| **Shadow mode** | A candidate runs silently on real traffic. Its answers are compared but never shown to users. |
| **Arbitration** | When a shadow candidate disagrees with the live student's served answer, the teacher decides who was right. |
| **Canary** | A candidate serves a slice of real traffic (10%, then 50%) while audits measure its quality. |
| **Rollback** | Pulling a canary version when the 95% upper bound of its audited precision falls below 92%. Traffic returns to the previous live version, or to the teacher. |
| **Wilson interval** | A statistical range for a proportion that behaves well on small samples. Used for rollback decisions. |
| **Live** | The version serving 100% of student traffic. |
| **States** | `candidate`, `rejected`, `shadow`, `canary`, `live`, `retired`, `rolled_back`. |
| **Cold start** | The period before any student exists. The teacher answers everything. |
| **Drift** | New kinds of requests the student has never seen, for example after a product launch. Escalations rise, then fall as Night School learns them. |
| **Night School** | The nightly job: build dataset, train, evaluate, run the release gate. |
| **Distillation** | Training a small model to copy a larger model's answers. |
| **LoRA** | Low-Rank Adaptation. Trains a small set of extra weights (an adapter) on top of a frozen base model. Cheap to train and to swap. |
| **Adapter** | The LoRA weights for one version, stored in the customer's bucket. |
| **Provenance ledger** | The record of every training example: teacher, license, license URL, hash, split, times seen. |
| **Model bill of materials (BOM)** | A summary of the provenance ledger: every teacher and license behind the training data, with counts and a SHA-256 fingerprint of the dataset. `GET /ratchet/bom`. |
| **License-clean distillation** | Distilling only from teachers whose licenses allow training on their outputs. Closed models whose terms forbid it are refused. |
| **OpenMDW-1.1** | The license of Nemotron 3 Ultra. It places no restrictions on outputs. |
| **Baseline cost** | What a request would have cost if the teacher answered it alone. Used for savings receipts. |
| **Mock mode** | Running with simulated models and synthetic tickets. No keys or GPUs needed. Numbers show loop behavior, not real model quality. |
| **Live mode** | Running against real Token Factory and Nebius AI Cloud. |
| **Token Factory** | Nebius's OpenAI-compatible inference service for open models. |
| **Serverless Jobs / Endpoints** | Nebius AI Cloud services that run a container to completion (jobs) or serve it at a URL (endpoints), billed per second. |
