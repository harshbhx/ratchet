# The hosted Nano as a stand-in student: calibration data

Real Token Factory calls, made on 26 Oct 2026 (UTC) from one laptop, to decide whether the untuned, hosted
**Nemotron 3 Nano 30B** (`nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`) may answer Scout's two distillable calls on the live site
before Ultra, and at which confidence ([ADR 0146](../../docs/adr/0146-hosted-student.md), FR-12.33). The answer, for now, is
no: neither task reached the bar, so `RATCHET_HOSTED_STUDENT=1` changes no answer yet.

## What is here

| File | What |
|---|---|
| `rows.jsonl` | 132 rows: the prompt, Ultra's label (its cleaned answer and token counts) and the hosted Nano's 3 samples (text, `finish_reason`, token logprobs) |
| `collect.json` | When, which models, the exact call parameters and the spend |

## How it was made

- **Prompts.** The three recorded live Scout runs in [`evidence/scout/`](../scout/) hold 33 real Tavily search results for
  three questions. Every pair of a recorded question and a recorded result is a `source-triage` prompt, built exactly as
  Scout builds it (`ratchet/scout/prompts.py`): 99 prompts, 33 of them the pairs Scout triaged itself ("own" pairs, the
  ones production sees) and 66 a result paired with another run's question (mostly not relevant). Every pair Ultra called
  relevant is also a `page-read` prompt: 33, all own pairs. Scout's reader sees up to 1,500 characters of a page; the
  recordings keep 500, so the reader prompts here are shorter than production's.
- **Labels.** Ultra (`nvidia/Nemotron-3-Ultra-550b-a55b`) answered each prompt as an audit does: temperature 0, the
  gateway's audit parameters, its default reasoning. A label is its cleaned answer when the gateway would store it (INV-16);
  all 132 were.
- **Student samples.** The hosted Nano answered each prompt the way the gateway samples a student: 3 choices in one call,
  temperature 0.7, token logprobs, thinking off (`chat_template_kwargs: {"enable_thinking": false}`), at most 1,024 tokens.
  Token Factory returned 3 choices with one logprob per output token and no reasoning tokens.
- **Calibration.** `nightschool.evaluate.cascade_eval`, the golden eval a night runs, over the rows in a fixed order (by a
  hash of the row id): even rows calibrate each task's threshold at the default target (97%), odd rows measure it. Agreement
  is the task's own comparison, the one an audit makes (`relevant` and `source_type`; `answers_question` and `fact_type`).
  A task may be served only when its held-out precision is at least 95% on at least 20 served rows (`hosted.gate`).

## What it showed

| | source-triage | page-read |
|---|---|---|
| Rows (calibrate / measure) | 99 (53 / 46) | 33 (13 / 20) |
| The Nano agrees with Ultra, all rows | 67 of 99 | 17 of 33 |
| Measured threshold | 0.9550 | 0.9576 |
| Held-out rows served at it | 7 of 46 (15%) | 4 of 20 (20%) |
| Held-out precision against Ultra | 7 of 7 | 2 of 4 |
| Verdict | not served: 7 served rows, 20 needed | not served: 50% |

- The triage figure rests on easy rows: 6 of the 7 served held-out rows are cross pairs that Ultra and the Nano both call
  not relevant. On the 33 own pairs, the kind production sees, the Nano agrees with Ultra on 24, and at the measured
  threshold it would answer 3 of them (all 3 right): about 9%.
- The disagreements on own pairs are `source_type`: docs or blog or other for a Wikipedia article, a Coursera article, an
  FAQ page. Ultra is not consistent there either: its new label agrees with the label it gave when Scout recorded the run
  on 30 of the 33 own pairs. A 95% bar against Ultra is close to Ultra's agreement with itself on these labels.
- Cost per call at list prices: Ultra $0.00081 per triage call (262 tokens in, 182 out of which 143 reasoning) and $0.00215
  per reader call (264 in, 629 out of which 575 reasoning); the Nano's 3 samples $0.000044 and $0.000055. The Nano is
  sampled on every call of a task it serves, so it must answer more than about 5% of them to pay for its own samples.

So at the bar the gateway holds every student to, the hosted Nano would answer under a tenth of Scout's triage calls and
save about nothing. The rows are too few, and too few of them are of production's kind, to say more.

## Spend

$0.157 at list prices: $0.151 Ultra, $0.006 Nano (`collect.json`). No key, header or cookie is in these files.

## Check it

```bash
python scripts/hosted_nano_calibration.py check   # recompute the records in ratchet/hosted.py from rows.jsonl (offline)
python scripts/hosted_nano_calibration.py show    # the records, the pooled numbers, the costs, Ultra against itself
```

`collect` makes the calls again (about $0.16; it needs `NEBIUS_API_KEY`); a new collection gives new samples, so the
records must then be replaced from `show`'s output.
