# Deployment

There are two ways to run Ratchet: **mock** (local, no keys, about a minute) and **live** (real Nemotron on Nebius). Every Nebius CLI flag below follows the Nebius Serverless quickstart and tutorials as of October 2026. Flags marked **verify on day 1** have not been run against a real account yet, so check them with `--help` before relying on them. The checklist is in [DAY1.md](DAY1.md).

## 1. Local mock mode

```bash
git clone <your repo> ratchet && cd ratchet
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest -q                                                          # 792 tests, ~75 s
python -m sim.simulate --days 10 --requests 2000 --bad-deploy-version 3
RATCHET_MODE=mock uvicorn ratchet.gateway:app --port 8000          # http://localhost:8000
```

Open `/dashboard` and click **Run 1 day + night** a few times (the **Traffic** selector picks tickets, Scout research calls, or both). Click **Break next deploy** to watch an automatic rollback. Open `/scout` and ask an example question to watch the agents. Add `RATCHET_MOCK_WARMUP_DAYS=4` to start with a trained student.

## 2. Live mode on Nebius

```
Your app ──▶ Gateway (any host, RATCHET_MODE=live) ──▶ Token Factory (teacher/judge)
                    │                                   
                    └──▶ Serverless Endpoint (vLLM + LoRA) ◀── bucket /data/adapters
cron: python -m ratchet.live night ──▶ Serverless Job (train) ──▶ bucket /data/adapters/<version>
cron: python -m ratchet.live tick
```

### Step 1: Token Factory key and checks

1. Join the Nebius Builder Program for credits. Its terms say $25 of Token Factory credit, expiring after 90 days.
2. Create an API key in the Token Factory console.
3. Run the checks:

   ```bash
   export NEBIUS_API_KEY=...
   python scripts/day1_check.py
   ```

   This confirms the four model IDs exist, and that the teacher answers, returns logprobs and supports JSON mode. If an ID differs, set `RATCHET_TEACHER_MODEL` (and update `ratchet/licenses.py` and `DEFAULT_PRICES`).

### Step 2: Object Storage bucket

Create a bucket in the same region as your GPUs (eu-north1 has L40S and H100). It is mounted at `/data` in both the job and the endpoint, with this layout:

```
/data/night/train.jsonl          written by ratchet.live (via RATCHET_SYNC_CMD)
/data/adapters/student-vN/       written by the training job, read by vLLM
```

Set:

```bash
export RATCHET_BUCKET_ID=<bucket id>                       # for --volume
export RATCHET_BUCKET_S3=s3://<bucket name>                # for polling in train_job.sh
export RATCHET_S3_ENDPOINT=https://storage.eu-north1.nebius.cloud   # verify on day 1
export RATCHET_DATA_DIR=./data
export RATCHET_SYNC_CMD="aws s3 sync ./data s3://<bucket name> --endpoint-url $RATCHET_S3_ENDPOINT"
```

The S3 endpoint URL format is an assumption; copy the real one from the console.

### Step 3: Training image

```bash
docker build -f deploy/Dockerfile.train -t <registry>/ratchet-train:0.1 .
docker push <registry>/ratchet-train:0.1
export RATCHET_TRAIN_IMAGE=<registry>/ratchet-train:0.1
```

The image is CUDA 12.8 devel with torch, Unsloth, TRL, PEFT and datasets. It also tries to install `causal-conv1d` and `mamba-ssm`, which Nemotron-H layers may need; the install is allowed to fail (`|| true`). Before pinning versions, test a 200-row run (DAY1 check 4). If Unsloth fails on the hybrid Mamba model, `train.py` falls back to plain transformers + PEFT (`target_modules="all-linear"`), or you can use the official Nebius Axolotl tutorial image.

### Step 4: Student endpoint (vLLM with runtime LoRA)

```bash
export RATCHET_STUDENT_KEY=$(openssl rand -hex 24)
bash deploy/student_endpoint.sh        # verify on day 1: nebius ai endpoint create --help
export RATCHET_STUDENT_BASE=https://<endpoint host>/v1
bash deploy/student_endpoint.sh stop   # when idle: stopped endpoints cost nothing (verify: nebius ai endpoint stop --help)
```

After creating and after stopping the endpoint, the script records it in the ledger (`python -m ratchet.cost endpoint up|down`, with `RATCHET_DB` pointing at the ledger file), so the cost report knows the GPU-hours you paid for ([Cost estimates](#4-cost-estimates)). Recording is best effort: run where the ledger is not, the deploy still succeeds and prints the command to run there (or use `POST /ratchet/cost/endpoint` with an admin key).

What the script sets:

- Image: `vllm/vllm-openai:v0.18.0-cu130`
- Platform and preset: `gpu-l40s-a`, `1gpu-8vcpu-32gb`
- `--container-port 8000`, `--shm-size 16Gi`
- `--volume $RATCHET_BUCKET_ID:/data`
- `--auth token --token $RATCHET_STUDENT_KEY`
- `--env VLLM_ALLOW_RUNTIME_LORA_UPDATING=True`
- vLLM args: `--model nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16 --trust-remote-code --enable-lora --max-lora-rank 16 --max-loras 4`

Checks once it is up:

```bash
curl -s $RATCHET_STUDENT_BASE/models -H "Authorization: Bearer $RATCHET_STUDENT_KEY"
# load an adapter by hand (ratchet.live does this automatically after training)
curl -s ${RATCHET_STUDENT_BASE%/v1}/v1/load_lora_adapter -H "Authorization: Bearer $RATCHET_STUDENT_KEY" \
  -H 'content-type: application/json' -d '{"lora_name":"student-v1","lora_path":"/data/adapters/student-v1"}'
```

> Adapters loaded at runtime are lost when the endpoint restarts. The gateway notices vLLM's 404 for an adapter and loads it again by itself (at most once per adapter per minute; `adapter_reloaded` events), so student calls fall back to the teacher for a few requests (they still succeed, at teacher cost). `python -m ratchet.live reload` re-loads every `live`, `canary` and `shadow` adapter by hand if that fails. See [RUNBOOK.md](RUNBOOK.md#student-endpoint-down-or-adapters-lost).

### Step 5: Training job

`deploy/train_job.sh <version>` submits:

```
nebius ai create --type job --name ratchet-<version> --image $RATCHET_TRAIN_IMAGE
  --platform gpu-l40s-a --preset 1gpu-8vcpu-32gb --volume $RATCHET_BUCKET_ID:/data
  --disk-size 200Gi --timeout 2h --container-command bash
  --args "-c 'python -m ratchet.nightschool.train --data /data/night/train.jsonl --base <base> --out /data/adapters/<version>'"
```

(Verify these flags on day 1.) With `RATCHET_BUCKET_S3` set, the script then polls for `adapters/<version>/adapter_model.safetensors` every 30 s for up to 2 h. That makes it suitable as `RATCHET_TRAIN_CMD`, which must block until the adapter exists.

Training defaults (`train.py`): 2 epochs, learning rate 2e-4, LoRA rank 16 (alpha 32), max sequence 2048, batch 8, bf16, assistant-only loss.

### Step 6: Gateway

Any small VM or container with Python 3.10+ works:

```bash
pip install -e .
export RATCHET_MODE=live RATCHET_DB=/var/lib/ratchet/ratchet.db
uvicorn ratchet.gateway:app --host 0.0.0.0 --port 8000 --workers 1
```

Use **one worker**. The ledger is a single SQLite connection inside the process, and the release gate is not designed for concurrent writers (see [ADR 0006](adr/0006-sqlite-ledger-for-v0.md)). Put TLS and authentication in front with a reverse proxy, since the gateway has no auth of its own.

### Step 7: Cron

`python -m ratchet.live` must run on a machine that has the `nebius` CLI, the `aws` CLI (for sync and polling), the same `RATCHET_DB` file, and network access to the student endpoint. In v0.1 that means the same host as the gateway.

```cron
# Night School at 02:30 local time
30 2 * * *  cd /opt/ratchet && . ./.env && python -m ratchet.live night >> /var/log/ratchet-night.log 2>&1
# Release gate every 5 minutes (the gateway also ticks on every request)
*/5 * * * * cd /opt/ratchet && . ./.env && python -m ratchet.live tick  >> /var/log/ratchet-tick.log 2>&1
```

`python -m ratchet.live night` runs `run_night` with `NebiusTrainer` and `LiveEvaluator` (judge = `RATCHET_JUDGE_MODEL`), then prints the last 10 events. `tick` runs `ReleaseGate.tick()` once. Without `RATCHET_STUDENT_BASE`, `night` stops before syncing or starting the training job, and `reload` stops with the same message (nothing to reload is not an error). The same commands run offline in `tests/test_live.py`, against fake endpoints, before they run here.

## 3. Keeping the demo alive for judging (Dec 1–15, 2026)

Judges need a working demo URL for two weeks, and a GPU endpoint running nonstop is the biggest cost.

**Recommended host: one Nebius CPU VM.** [DEPLOY_NEBIUS_VM.md](DEPLOY_NEBIUS_VM.md) sets up the gateway with Docker Compose and Caddy (automatic HTTPS) on a 2 vCPU / 8 GB VM, with the budget guard, an opt-in edge guard for `/v1/*`, backups, monitoring, updates and teardown. It starts in mock mode with a warmed-up student. The points below still apply to it.

1. **Host the gateway in mock mode as the public demo.** The dashboard with Run day, Break next deploy and Reset is safe, costs nothing and never breaks. Label it "simulation".
2. **For the live path,** keep a second gateway in live mode with `RATCHET_STUDENT_BASE` **unset**. Student calls then go to the hosted, untuned `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` on Token Factory, and you pay per token instead of per GPU-hour. Be honest that this is the fallback, not the tuned student. Note that a version's calibrated threshold was set for the tuned adapter, so precision on the fallback may be lower; audits will show it.
3. **Stop the vLLM endpoint when not recording or testing.** Stopped endpoints cost nothing for compute; mounted buckets are still billed.
4. **Cap teacher spend:** keep `RATCHET_AUDIT_RATE` at 0.05 or lower and watch the Token Factory usage page daily. In live mode, keep Scout's budget guard on (`RATCHET_SCOUT_DAILY_BUDGET_USD`, `RATCHET_SCOUT_MAX_RUNS_PER_DAY`, `RATCHET_SCOUT_RUNS_PER_HOUR`; see [CONFIGURATION.md](CONFIGURATION.md#scout-the-multi-agent-demo)) and turn on the Caddy guard (`RATCHET_API_GUARD=guarded`).

## 4. Cost estimates

These are **estimates**: Serverless pricing was not confirmed (on-demand list prices assumed), and token counts depend on your prompts.

Ratchet measures the real thing once it runs: `python -m ratchet.cost report` (or `GET /ratchet/cost`) prices the recorded endpoint and training hours at `RATCHET_STUDENT_GPU_USD_PER_HOUR` and `RATCHET_TRAIN_GPU_USD_PER_HOUR`, reports savings per token and by GPU-hours paid, the break-even volume, and whether self-hosting pays at your traffic ([RUNBOOK.md](RUNBOOK.md#cost-and-the-self-host-recommendation), [ADR 0023](adr/0023-utilization-aware-cost-accounting.md)). At about $1.55 per GPU-hour a 24/7 endpoint breaks even near 1,500 student-served requests an hour (unverified list prices); below that, use the hosted fallback or stop the endpoint when idle. Above it the endpoint beats the teacher, but the untuned hosted fallback is cheaper per answer below its crossover (roughly 52,000 student answers an hour at list prices), if it serves as many requests as the tuned student.

| Item | Assumption | Estimate |
|---|---|---|
| Training run | L40S at about $1.55/h, 30–60 min per night | ~$0.80–1.60 per night |
| Student endpoint | L40S at about $1.55/h, only while testing or recording | ~$37 per full day if left on |
| Teacher calls | Ultra $1 / $3 per 1M tokens; ~150 input + ~380 output tokens per call | ~$1.30 per 1,000 calls |
| Judge | Super $0.30 / $0.90; only for free-text tasks | small |
| Hackathon total | 6–8 training runs, endpoint on a few hours a day, ~$20–30 of teacher calls | **~$40–80** |

## 5. Verification after deploy

1. `curl $GATEWAY/healthz` returns `{"status":"ok",...}`, and `curl -H "Authorization: Bearer $KEY" $GATEWAY/v1/models` returns `ratchet` (drop the header if `RATCHET_API_KEYS` is not set).
2. Send 20 real requests. `GET /ratchet/stats` shows them with `served_by=teacher` (cold start), and `GET /ratchet/bom` shows the teacher with license `OpenMDW-1.1`.
3. Run `python -m ratchet.live night` once you have at least 50 new examples. Expect a `trained` event and either `canary` or `rejected`, and a bill of materials at `data/boms/student-v1.json` (also at `GET /ratchet/versions/1/bom`). With `RATCHET_BOM_SIGNING_KEY` set, `python -m ratchet.bom verify data/boms/student-v1.json --public-key <yours>` prints `OK`.
4. Check that `curl $RATCHET_STUDENT_BASE/models` lists `student-v1` after the night.
5. Watch the `canary` → `live` events over the next requests. Audits need traffic: at a 5% audit rate and 10% canary, reaching 20 audits takes roughly 4,000+ requests, so for a demo raise `RATCHET_AUDIT_RATE`, or lower `ReleasePolicy.min_audits` in code (there is no environment variable for it).
