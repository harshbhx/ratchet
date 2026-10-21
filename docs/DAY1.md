# Day-1 checklist (Oct 19–20)

Run these before building more. Each one is a known risk from the stress test. Each has a fallback.

Run everything and keep the facts: `python scripts/day1_check.py all --json evidence/day1/day1.json` (or `make day1 DAY1_CHECKS="all --json evidence/day1/day1.json"`). Review the file, which holds no key, URL or host, before committing it. Each check also runs alone (`teacher`, the default; `choices`, `reasoning`, `student`, `tavily`, `cli`); exit 0 means no check failed, 1 a check failed, 2 a missing key or bad input.

| # | Check | How | If it fails |
|---|---|---|---|
| 1 | Model IDs and prices on Token Factory | `python scripts/day1_check.py` | Update `ratchet/config.py` with the IDs it prints |
| 2 | Logprobs returned | same script | Confidence falls back to sample agreement (already supported) |
| 3 | JSON mode works | same script | Rely on prompt + `tasks.parse_json` (already tolerant) |
| 4 | Nano 4B LoRA trains on a Serverless Job (L40S) | build `deploy/Dockerfile.train`, run `deploy/train_job.sh test` on ~200 rows | Use the official Nebius Axolotl tutorial image; or use H100 for Nano 30B |
| 5 | vLLM serves Nano 4B with `--enable-lora` | `deploy/student_endpoint.sh`, then `python scripts/day1_check.py student` (the base model must answer; the adapter's answer must differ from it) | Merge the adapter into the base weights and serve one endpoint per version (NS-STUDENT-SERVING-FALLBACK) |
| 6 | Runtime LoRA loading, and the 404 for a lost adapter | same command. It loads `--adapter-path` (default `/data/adapters/test`, row 4's adapter, trained with `train.py`'s default `--targets`) twice, samples it as production does, asks for `student-v0`, and unloads. It never touches `student-v<n>` | A load error that lists the modules vLLM expected and received: retrain without the modules it names (or `--targets all-linear`) and read the endpoint log for "will be ignored". `adapter_missing` fails: lost adapters would never reload, so build NS-STUDENT-SERVING-FALLBACK (static `--lora-modules name=path` per promotion, or merged weights). A 404 on load: start the endpoint with `VLLM_ALLOW_RUNTIME_LORA_UPDATING=True` and `--enable-lora` |
| 7 | Serverless pricing | Nebius console | Keep training runs short; stop the endpoint when idle |
| 8 | Terms 5(f) of Token Factory ("competing service") | Ask Nebius DevRel in Discord | Position as "built on Nebius end to end" (it is) |
| 9 | Answers end with `finish_reason=stop` | `python scripts/day1_check.py` prints it per model | Only complete answers are stored (invariant 16): with `length`, raise the script's `max_tokens` to see whether reasoning eats the budget; see [RUNBOOK.md](RUNBOOK.md#teacher-answers-not-stored) |
| 10 | The teacher honours `n` and returns logprobs for its choices | `python scripts/day1_check.py choices` (one Ultra call with `"n": 2, "logprobs": true`, as an escalation sends it) | With fewer choices than asked, clients get fewer on escalated requests (set `RATCHET_MAX_N=1` to refuse `n` > 1); if it refuses `logprobs`, remove `logprobs` and `top_logprobs` from `RATCHET_TEACHER_PARAMS` (teacher-served choices then carry `logprobs.content: null`) |
| 11 | Token Factory applies a thinking switch? | `python scripts/day1_check.py reasoning` (per listed model: no switch, `chat_template_kwargs`, the same inside `extra_body`, `reasoning_effort: none`; at most 16 calls of 1,024 tokens) | Record which variant changes `reasoning_tokens`. NEMOTRON-REASONING-CONTROL's settings follow from it |
| 12 | The student endpoint honours `chat_template_kwargs` | the `student` check's thinking line | The same: record it for NEMOTRON-REASONING-CONTROL |
| 13 | Tavily answers with this key | `python scripts/day1_check.py tavily` (one basic search, 1 credit) | 401: the key. 432 or 433: no credits left. Check the plan before judging (Dec 1-15) |
| 14 | The `nebius` flags in `deploy/` exist | `python scripts/day1_check.py cli` where the deploy scripts run (warnings only; runs only `nebius version` and `--help`) | Fix the script, then `tests/test_cost.py`'s stub assertion. The Oct 19 reference shows `ai endpoint stop` takes `--id` and `ai create` is the interactive wizard (`nebius ai job create` is the scripted form) |

Rows 11 to 14 take the next free numbers when other items add rows here.

Budget guess: about $40–80 total, assuming L40S at about $1.55/hour (training runs under an hour, endpoint only while testing and recording, $20–30 of Ultra calls). The Builder Program gives $25 of Token Factory credit; ask in Discord for more.
