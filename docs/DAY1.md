# Day-1 checklist (Oct 19–20)

Run these before building more. Each one is a known risk from the stress test. Each has a fallback.

| # | Check | How | If it fails |
|---|---|---|---|
| 1 | Model IDs and prices on Token Factory | `python scripts/day1_check.py` | Update `ratchet/config.py` with the IDs it prints |
| 2 | Logprobs returned | same script | Confidence falls back to sample agreement (already supported) |
| 3 | JSON mode works | same script | Rely on prompt + `tasks.parse_json` (already tolerant) |
| 4 | Nano 4B LoRA trains on a Serverless Job (L40S) | build `deploy/Dockerfile.train`, run `deploy/train_job.sh test` on ~200 rows | Use the official Nebius Axolotl tutorial image; or use H100 for Nano 30B |
| 5 | vLLM serves Nano 4B with `--enable-lora` | `deploy/student_endpoint.sh`, then call it with the adapter name | Merge the adapter into the base weights and serve one endpoint per version |
| 6 | Runtime LoRA loading | `POST /v1/load_lora_adapter` (needs `VLLM_ALLOW_RUNTIME_LORA_UPDATING=True`) | Restart the endpoint with `--lora-modules name=path` per promotion |
| 7 | Serverless pricing | Nebius console | Keep training runs short; stop the endpoint when idle |
| 8 | Terms 5(f) of Token Factory ("competing service") | Ask Nebius DevRel in Discord | Position as "built on Nebius end to end" (it is) |
| 9 | Answers end with `finish_reason=stop` | `python scripts/day1_check.py` prints it per model | Only complete answers are stored (invariant 16): with `length`, raise the script's `max_tokens` to see whether reasoning eats the budget; see [RUNBOOK.md](RUNBOOK.md#teacher-answers-not-stored) |

Budget guess: about $40–80 total, assuming L40S at about $1.55/hour (training runs under an hour, endpoint only while testing and recording, $20–30 of Ultra calls). The Builder Program gives $25 of Token Factory credit; ask in Discord for more.
