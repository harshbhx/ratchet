# Ratchet: for judges

**Built on NVIDIA Nemotron and Nebius: every model call on the live site runs on Nebius Token Factory, the adapter was retrained on Nebius AI Cloud, and the student was served from a Nebius Serverless Endpoint in a measured test.**

Ratchet is an OpenAI-compatible gateway for license-clean distillation on open models. A small NVIDIA Nemotron 3 Nano 4B student answers first. When it is unsure, the request goes to the teacher, Nemotron 3 Ultra on Nebius Token Factory, and the answer is logged with the teacher's name and license (closed models are refused as teachers, in code). Night School is built to train a new student from those answers and release it only if it passes a statistical gate. Measured: a Nano adapter taught by Ultra goes from 12.8% to 39.0% on a text-to-SQL exam it never saw, for about $17 on record, and every score recomputes from the saved answers.

[**Try it live**](https://ratchet.dualmindlab.tech/live) · [**Judges page**](https://ratchet.dualmindlab.tech/judges) · [**Evidence**](evidence) · [**Video, 2:59**](https://video.ratchet.dualmindlab.tech/?from=judges) · [Docs](https://docs.ratchet.dualmindlab.tech/guides/for-judges/)

## Check it in 5 minutes

1. **Use it.** Open [ratchet.dualmindlab.tech/live](https://ratchet.dualmindlab.tech/live) and send a real request. It is answered by Nemotron 3 Ultra on Nebius Token Factory, with a badge that says who answered. No key needed; send a few at most.
2. **Read the exam results.** [The results table below](#what-was-run-and-how-to-check-it), or the [Results chapter on the judges page](https://ratchet.dualmindlab.tech/judges#exams).
3. **Check one result yourself.** Open one [evidence folder](evidence) and run one command (each row below has one). The first needs only a clone and Python:
   ```bash
   git clone https://github.com/harshbhx/ratchet.git && cd ratchet && pip install -e ".[dev]"
   python scripts/text2sql/exam_numbers.py --check
   ```
4. **Watch the video** (2:59): [video.ratchet.dualmindlab.tech](https://video.ratchet.dualmindlab.tech/?from=judges).

## What was run, and how to check it

Every number is read from saved files in this repository. Commands are from [docs/VERIFY.md](docs/VERIFY.md) and run on a clone with no GPU and no key.

| What | Result | Date | Evidence | Check it |
|---|---|---|---|---|
| Text-to-SQL exams (strict scoring, held-out databases) | Adapter **39.0%** / **33.0%** on exam 1 / exam 2; untrained 12.8% / 12.5%; teacher (Ultra) 25.6% / 15.0% | 22 to 24 Oct | [round 1](evidence/text2sql-round1), [round 2](evidence/text2sql-round2), [exam 2](evidence/text2sql-exam2) | `python scripts/text2sql/exam_numbers.py --check` |
| Tool calling | **90.7%** on the hard exam (Ultra 91.3%, untrained 62.7%); **80.3%** on BFCL | 24 Oct | [evidence/toolcall](evidence/toolcall) | `python scripts/toolcall_results.py --check` |
| Retrain on Nebius AI Cloud | **40.7% / 34.5%**: the 39.0% / 33.0% result reproduces. About **$2.16** at list prices | 27 Oct | [evidence/text2sql-round2-nebius](evidence/text2sql-round2-nebius) | `python evidence/text2sql-round2-nebius/score_nebius.py verify` |
| Student on a Nebius Serverless Endpoint (speed tests, no accuracy scored) | Cold start about **7.2 to 7.6 min**, restart **7.1 min**; 16 users at once: p50 **1.6 s** (SQL adapter) / **1.2 s** (tool calling) | 27 to 28 Oct | [evidence/student-endpoint-nebius](evidence/student-endpoint-nebius) | files in `results/` (see the folder's README) |
| Night School, end to end on Nebius, once, by hand | Gate **rejected** the new student: 81.8% precision against the 95% target. Never made live | 28 Oct | [evidence/nightschool-nebius](evidence/nightschool-nebius) | read its README; the logs are in the folder |
| Signed bill of materials | The Night School BOM, signed with Ed25519 key `81b0a086250a81d0`, signed 29 Oct after the run | 29 Oct | [evidence/keys](evidence/keys), [signed BOM](evidence/nightschool-nebius/ratchet_bom.signed.json) | `python -m ratchet.bom verify evidence/nightschool-nebius/ratchet_bom.signed.json --public-key Gh68hnDDlRSjBRxmc5dTo4l+rvqkftUsH7B2Nm72gpc= --notice evidence/nightschool-nebius/NOTICE.md` |
| Token Factory prices and settings, checked | List prices match the price list Token Factory serves; every probe passed | 29 Oct | [evidence/day1](evidence/day1) | read `evidence/day1/day1.json` |
| Live smoke run on Token Factory | 20 real requests, all answered by the teacher, about $0.01 | 23 Oct | [evidence/live-smoke-2026-10-23](evidence/live-smoke-2026-10-23) | `python -m ratchet.liverun verify evidence/live-smoke-2026-10-23` |
| Scout (multi-agent research) runs | Three recorded live runs, 18 to 20 model calls each, 13 to 16 s, about $0.02 each | 23 to 24 Oct | [evidence/scout](evidence/scout), [replay Space](https://huggingface.co/spaces/harshbhx/ratchet-scout) | replay in the browser |

The exams are synthetic text-to-SQL and tool calling, scored strictly (an answer counts only if it matches the reference). Agreement is with the reference, not ground truth. [What none of this proves](docs/VERIFY.md#what-none-of-this-proves).

## All the links

### Live

| | |
|---|---|
| Story site | [ratchet.dualmindlab.tech](https://ratchet.dualmindlab.tech) |
| Send a real request | [/live](https://ratchet.dualmindlab.tech/live) |
| Judges page | [/judges](https://ratchet.dualmindlab.tech/judges) |
| Dashboard | [/dashboard](https://ratchet.dualmindlab.tech/dashboard) |
| Scout | [/scout](https://ratchet.dualmindlab.tech/scout) (the access code for extra Scout runs is on the Devpost page, not in this repository) |
| Live gateway | [live.ratchet.dualmindlab.tech](https://live.ratchet.dualmindlab.tech) |
| Video, 2:59 | [video.ratchet.dualmindlab.tech](https://video.ratchet.dualmindlab.tech/?from=judges) |

### Docs

| | |
|---|---|
| Docs home | [docs.ratchet.dualmindlab.tech](https://docs.ratchet.dualmindlab.tech) |
| Quickstart | [guides/quickstart](https://docs.ratchet.dualmindlab.tech/guides/quickstart/) |
| For judges (the same pack, with diagrams) | [guides/for-judges](https://docs.ratchet.dualmindlab.tech/guides/for-judges/) |
| For language models | [llms.txt](https://docs.ratchet.dualmindlab.tech/llms.txt), [site llms.txt](https://ratchet.dualmindlab.tech/llms.txt) |
| Check it yourself | [docs/VERIFY.md](docs/VERIFY.md) |
| Where everything runs | [docs/WHERE_IT_RUNS.md](docs/WHERE_IT_RUNS.md) |

### Code

| | |
|---|---|
| Repository | [github.com/harshbhx/ratchet](https://github.com/harshbhx/ratchet) |
| README | [README.md](README.md) |
| License | [Apache-2.0](LICENSE) |
| Changelog | [CHANGELOG.md](CHANGELOG.md) |
| Latest release | [releases/latest](https://github.com/harshbhx/ratchet/releases/latest) |

### Evidence folders

[`text2sql-round1`](evidence/text2sql-round1) · [`text2sql-round2`](evidence/text2sql-round2) · [`text2sql-exam2`](evidence/text2sql-exam2) · [`toolcall`](evidence/toolcall) · [`text2sql-round2-nebius`](evidence/text2sql-round2-nebius) · [`student-endpoint-nebius`](evidence/student-endpoint-nebius) · [`nightschool-nebius`](evidence/nightschool-nebius) · [`keys`](evidence/keys) · [`day1`](evidence/day1) · [`live-smoke-2026-10-23`](evidence/live-smoke-2026-10-23) · [`scout`](evidence/scout) · [`hosted-nano`](evidence/hosted-nano)

### Hugging Face

| | |
|---|---|
| Collection | [harshbhx/ratchet](https://huggingface.co/collections/harshbhx/ratchet) |
| Models | [`ratchet-nemotron-nano-4b-text2sql`](https://huggingface.co/harshbhx/ratchet-nemotron-nano-4b-text2sql), [`-toolcall`](https://huggingface.co/harshbhx/ratchet-nemotron-nano-4b-toolcall), [`-text2sql-nebius`](https://huggingface.co/harshbhx/ratchet-nemotron-nano-4b-text2sql-nebius) |
| Datasets | [text-to-SQL exams and every answer](https://huggingface.co/datasets/harshbhx/ratchet-text2sql-exams), [tool-calling hard exam](https://huggingface.co/datasets/harshbhx/ratchet-toolcall-exam-hard), [BFCL exam](https://huggingface.co/datasets/harshbhx/ratchet-toolcall-exam-bfcl) |
| Spaces | [Scout replay](https://huggingface.co/spaces/harshbhx/ratchet-scout), [tool-calling exam explorer](https://huggingface.co/spaces/harshbhx/ratchet-toolcall-explorer) |

### Nebius and NVIDIA: what runs where

| Where | What runs there |
|---|---|
| Nebius Token Factory | Every model call on the live site: the teacher (Nemotron 3 Ultra), the hosted Nano for the random split, the judge |
| Nebius AI Cloud | The round-2 retrain (27 Oct) and the Night School training job (28 Oct), as Serverless Jobs on a spot L40S; the Serverless Endpoint for the speed tests and the Night School adapter; an Object Storage bucket; a CPU VM for the Night School gateway. All by hand; the endpoint and the VM were deleted afterwards, the storage bucket was kept |
| NVIDIA Nemotron | Ultra (teacher), Nano 4B (student, with a LoRA adapter), hosted Nano 30B (random split) |
| Cloudflare | The story site, the docs and the live gateway (CPU only) |
| A borrowed GPU | Training of the two original adapters |

## Hackathon checklist

| | Requirement | Link |
|---|---|---|
| ✓ | Nebius Token Factory and AI Cloud, with NVIDIA open models (Nemotron 3 Ultra, Nano 4B) | [what runs where](#nebius-and-nvidia-what-runs-where), [docs/WHERE_IT_RUNS.md](docs/WHERE_IT_RUNS.md) |
| ✓ | Video, 2:59 | [video](https://video.ratchet.dualmindlab.tech/?from=judges) |
| ✓ | Public repository with license and README | [repo](https://github.com/harshbhx/ratchet), [Apache-2.0](LICENSE), [README](README.md) |
| ✓ | Working URL | [ratchet.dualmindlab.tech](https://ratchet.dualmindlab.tech), [/live](https://ratchet.dualmindlab.tech/live) |
| ✓ | Feedback on Nebius and NVIDIA | [README, Feedback](README.md#feedback-on-nebius-and-nvidia) |

The track is the one chosen on the Devpost page.

## What is real, and what is not yet

**Ran, with evidence above:** real Token Factory calls on the live site; the text-to-SQL and tool-calling adapters and their exams; the retrain on Nebius AI Cloud; the endpoint speed tests; one Night School run on Nebius, by hand, that the gate rejected; the signed BOM; the 29 Oct price check.

**Not yet:** the nightly loop has not run on live traffic. The live site is not connected to a student endpoint, so the teacher answers every request. No saving has been measured. The GPU-hour rate used in cost estimates (about $1.55 per L40S hour) does not appear on the price list Token Factory serves and is unverified, so the Nebius bill decides.

## What it cost

All of these are estimates recorded by the scripts, not invoices.

| Item | About |
|---|---|
| Labelling by the teacher, round 1 / round 2 | $2.87 + $9.18 |
| Nebius retrain, 27 Oct (2.73 GPU-hours, failed attempts included) | $2.16 |
| Endpoint speed tests, 27 to 28 Oct | $0.67 |
| Night School run, 28 Oct | about 2.7 GPU-hours, at most about $4 (see its [README](evidence/nightschool-nebius/README.md)) |
| One real request on the live site | $0.0002 |
| One Scout run | $0.02 |

Request and Scout costs are from the [29 Oct day-1 check](evidence/day1/README.md). The whole-project record is in [docs/VERIFY.md](docs/VERIFY.md#what-it-cost).
