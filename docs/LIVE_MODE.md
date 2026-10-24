# Live mode: real Token Factory, and measuring a run

## Run it live

```bash
export NEBIUS_API_KEY=...                       # Token Factory key
python scripts/day1_check.py                    # model IDs, logprobs, JSON mode

# Student endpoint (vLLM + runtime LoRA loading) and training job
export RATCHET_BUCKET_ID=... RATCHET_STUDENT_KEY=... RATCHET_TRAIN_IMAGE=...
bash deploy/student_endpoint.sh create          # once (DRY_RUN=1 first); later: stop when idle, start to resume
export RATCHET_STUDENT_BASE=https://<endpoint>/v1

RATCHET_MODE=live uvicorn ratchet.gateway:app --port 8000   # point your app at http://<host>:8000/v1
python -m ratchet.live night                                # nightly (cron)
python -m ratchet.live tick                                 # every few minutes (cron)
python -m ratchet.live seal                                 # hourly (cron): seal the savings receipts of days that ended
```

Use it from any OpenAI SDK:

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="unused")
r = client.chat.completions.create(model="ratchet", messages=[...], extra_headers={"X-Ratchet-Task": "ticket-triage"})
print(r.ratchet)  # served_by, confidence, cost_usd, all_teacher_cost_usd, usage_breakdown ...
print(r.usage.total_tokens, r.system_fingerprint)  # the served answer's tokens; the fingerprint changes with the version
```

### Measuring a live run

`python -m ratchet.liverun` ([ADR 0121](adr/0121-live-measurement-method.md)) sends synthetic workload traffic through the live gateway in rounds with Night School between them, sends a small control sample straight to Ultra for a measured all-Ultra baseline, and writes one results file behind every live number in the docs. It runs on the gateway's host with its `.env`, under its own API key, and never spends past `--max-usd`:

```bash
export RATCHET_LIVERUN_KEY=...   # the key of liverun:<key> in RATCHET_API_KEYS; never a flag
make live-plan                   # free: requests per release stage, golden size, USD per round
make live-smoke                  # 20 requests, about 5 control calls, no nights: cents
make live-run MAX_USD=30         # results in evidence/live/<run id>/
```

The [runbook](RUNBOOK.md#live-measurement-run) covers the prerequisites (the gateway runs `RATCHET_SEQUENTIAL=1` and `RATCHET_CANARY_AUDIT_RATE=0.5`), resuming, the repeat run, entering the Nebius bill and publishing.

## Evidence on record

| What | How |
|---|---|
| A 20-request smoke run through the live gateway, about $0.013 | `evidence/live-smoke-2026-10-23/` (`results.json`, `requests.ndjson.gz`, `log.txt`); recompute its client-side numbers with `python -m ratchet.liverun verify evidence/live-smoke-2026-10-23` |
| A full measured live run (rounds, nights, control arm) | Not run yet. When one is published, its results go to `evidence/live/results.json` and `docs/LIVE_RESULTS.md` |

## Hosting the demo

The public demo currently runs on Cloudflare (Workers with Containers: `deploy/cloudflare-live` for the live gateway, `deploy/cloudflare` for the simulation). The same two-host layout is scripted for one small Nebius CPU VM with Docker Compose and Caddy (automatic HTTPS) in `deploy/vm/`; that kit is tested offline and has not been run on Nebius yet. For the VM option, the two hosts are: the live gateway at the root domain (real Token Factory and Tavily calls; the live strip shows the last of each) and the simulation at `sim.<domain>` (mock models: run days, break a deploy, watch the rollback; labelled simulation, shared by every visitor). The gateway does no inference, so 2 vCPU / 8 GB is expected to be enough; that both hosts fit under load is unverified. Scripts are in `deploy/vm/`; the guide, with costs (estimate: about $72 for Oct 29 – Dec 15, 2026, before live-mode usage), security notes and teardown, is [docs/DEPLOY_NEBIUS_VM.md](DEPLOY_NEBIUS_VM.md).

