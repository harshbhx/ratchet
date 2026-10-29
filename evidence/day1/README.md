# Day-1 check on Token Factory, 29 Oct 2026

`day1.json` is the output of `python scripts/day1_check.py teacher reasoning --json evidence/day1/day1.json`, run on 29 Oct 2026 (UTC 05:32). It holds no key, URL or host. The `student`, `choices` and `tavily` checks were not run.

## Result: all checks passed

- All four models and the embeddings model are listed. Each answers, returns logprobs and takes JSON mode.
- The teacher (Nemotron 3 Ultra) accepts `max_tokens=8192`, `stop`, `seed`, `temperature` and `response_format` json_schema (strict).
- The embeddings model answers with 4,096 dimensions.

## Reasoning tokens, per model (1,024-token cap, one short probe)

| Model | Default | `chat_template_kwargs` enable_thinking=false | `extra_body` form | `reasoning_effort: none` |
|---|---|---|---|---|
| Nemotron 3 Ultra | 38 reasoning tokens | 0 | 0 | 0 |
| Nemotron 3 Super 120B | 50 | 0 | 0 | 0 |
| Nemotron 3 Nano 30B | not reported (112 completion tokens, reasoning text in the message) | not reported (11 completion tokens) | not reported (11) | not reported (11; reasoning still in the message) |
| Nemotron 3.5 Lightning | 318 | 0 | 0 | 0 |

So Ultra does think on the gateway's default settings (38 tokens on the probe, 16 on the "Try a real request" prompt), the field is accepted, and thinking off drops it to 0. The settings are accepted by every model; Nano 30B does not report a reasoning count, so its thinking is visible only as extra completion tokens.

## List prices

Read on 29 Oct 2026 from the price list Token Factory itself serves (`GET /v1/models?verbose=true`, field `pricing`, USD per token). The public price page (tokenfactory.nebius.com/organization/prices) is rendered by script and returned no text to the fetcher, so the models endpoint is the source.

| Model | Published in / out, USD per 1M | `ratchet/config.py` | Match |
|---|---|---|---|
| Nemotron 3 Ultra | 1.00 / 3.00 (cached input 0.12) | 1.00 / 3.00 | yes |
| Nemotron 3 Super 120B | 0.30 / 0.90 (cached 0.06) | 0.30 / 0.90 | yes |
| Nemotron 3 Nano 30B | 0.06 / 0.24 (cached 0.012) | 0.06 / 0.24 | yes |
| Nemotron 3.5 Lightning | 0.06 / 0.24 (cached 0.03) | 0.06 / 0.24 | yes |
| Qwen3-Embedding-8B | 0.01 / 0 | 0.01 / 0 | yes |

The GPU-hour rate (1.55 USD per L40S hour) is not on this list and is still unverified.

## What a real request costs (measured, local live gateway, same settings as the live Worker)

- "Try a real request" (`In one sentence, what is LoRA fine-tuning?`, `max_tokens` 120): 28 in, 69 out (16 of them reasoning), answered by Ultra: **0.000235 USD**. Reasoning tokens are already inside `completion_tokens`, which is what the gateway prices, so they are counted.
- One Scout run (`What is LoRA fine-tuning?`, 20 teacher calls): **0.0221 USD**.

The most one gateway call can cost is bounded by `RATCHET_TEACHER_MAX_TOKENS` (8,192 output tokens, about 0.025 USD on Ultra).

Total spent on Token Factory for this folder: under 0.05 USD.
