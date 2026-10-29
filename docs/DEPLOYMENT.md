# Deployment

There are two ways to run Ratchet: **mock** (local, no keys, about a minute) and **live** (real Nemotron on Nebius). The `nebius ai` commands in `deploy/` follow the Nebius CLI reference (auto generated on 19-Oct-2026, read Oct 21, 2026) and Nebius's L40S fine-tuning tutorial. They are tested offline, and the job and the endpoint each ran once by hand on Nebius (27 to 28 Oct 2026): run both scripts with `DRY_RUN=1` first, and check anything marked **verify on day 1** with `--help` before relying on it. `python scripts/day1_check.py` runs the checks.

> **One command for the whole path:** `make nebius-plan` prints every step below (the CPU VM, the bucket, the student endpoint, the wiring and the smoke check) and changes nothing; `deploy/nebius/nebius.sh up --yes --with-student` runs them, with `RATCHET_I_UNDERSTAND_THIS_COSTS_MONEY=1`. Same status as everything here: scripted, tested offline, ready to run. Three things have run on Nebius by hand: a training job (the round-2 retraining on 27 Oct 2026, evidence in `evidence/text2sql-round2-nebius/`), one speed test of the student on a Serverless Endpoint the same day (evidence in `evidence/student-endpoint-nebius/`; the endpoint was deleted afterwards), and one Night School end to end on 28 Oct 2026 (a CPU VM, a spot L40S job and an endpoint; the golden gate rejected the new student, so it was never made live; evidence in `evidence/nightschool-nebius/`). The wiring of the live gateway to an endpoint and a nightly schedule have not run. [Where everything runs](WHERE_IT_RUNS.md) has the map and the costs.

## 1. Local mock mode

```bash
git clone <your repo> ratchet && cd ratchet
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest -q                                                          # 1994 tests, ~120 s
RATCHET_MODE=mock uvicorn ratchet.gateway:app --port 8000          # http://localhost:8000
```

Mock mode answers with mock models and calls nothing: it is for developing and testing, and its numbers are not results. Real behaviour is in live mode (section 2).

## 2. Live mode on Nebius

```
Your app ──▶ Gateway (any host, RATCHET_MODE=live) ──▶ Token Factory (teacher/judge)
                    │                                   
                    └──▶ Serverless Endpoint (vLLM + LoRA) ◀── bucket /data/adapters
cron: python -m ratchet.live night ──▶ Serverless Job (train) ──▶ bucket /data/adapters/<version>
cron: python -m ratchet.live tick
```

### Step 1: Token Factory key and checks

1. Join the Nebius Builder Program for credits. Its terms say $25 of Token Factory credit, expiring after 90 days (research notes, 2026-10-25; re-check the program terms before submission).
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
/data/night/train.jsonl                    written by ratchet.live (via RATCHET_SYNC_CMD): prompt-completion rows
/data/boms/student-vN.json                 the adapter's bill of materials, written by ratchet.live after the night
/data/adapters/student-vN/                 written by the training job (weights, versions.json, ratchet_manifest.json),
                                           read by vLLM
/data/adapters/student-vN/NOTICE.md        the licenses that travel with the adapter (the job writes it; ratchet.live
                                           replaces it with the text the BOM signed)
/data/adapters/student-vN/ratchet_bom.json the adapter's BOM, synced by ratchet.live after the night
```

`RATCHET_SYNC_CMD` runs twice on a night that trains: before the job (the dataset) and after the evaluation (the BOM and `NOTICE.md` into the adapter's folder). It must only upload: `aws s3 sync` without `--delete` leaves the weights the job wrote alone.

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

Build from the repository root (the image copies `deploy/train-versions.txt` and `ratchet/`). The image is CUDA 12.8 devel with exact versions of torch, Transformers, PEFT, TRL, Accelerate, datasets and the Mamba kernels (`causal-conv1d`, `mamba-ssm`) that Nemotron-H's layers run on ([ADR 0145](adr/0145-train-on-the-served-prompt.md)):

- `deploy/train-versions.txt` lists every pin, and each pip step after torch reads it as constraints, so a version that differs between the two files, or a dependency that would move a pinned package, fails the build. `tests/test_nightschool.py::test_dockerfile_is_pinned` checks the two files offline.
- The Mamba kernels build with no `|| true`, and the last build step imports them (and checks torch's CUDA build), so an image without them never reaches a GPU. If no prebuilt wheel matches torch 2.11 and CUDA 12, they compile from source in the devel image, which takes a while. The import needs no GPU (torch's CUDA libraries load without a driver), but that was not checked on a real builder: if it fails with a driver error rather than a missing module, build on a GPU VM.
- There is no Unsloth: its release of Oct 19, 2026 requires `trl<=1.15.0`, and the image pins TRL 1.16.0. `train.py` uses Transformers + PEFT + TRL.
- `train.py` checks every row and its rendering with the model's chat template before it trains (FR-8.42): the student must be trained on the prompt the endpoint serves (thinking off). A `TemplateMismatch` or `row N: ...` error in the job's logs means the training file or the tokenizer does not match the endpoint; it stops the job before the GPU trains anything.
- The job writes `versions.json` (Python, CUDA and every pinned package) next to the adapter before training starts.

The versions are candidates read on Oct 21, 2026. To change one (or to adopt what the day-1 smoke job ran with, from its `versions.json`): edit `deploy/train-versions.txt` and the matching `"name==version"` in `deploy/Dockerfile.train` together (and `train.LIBRARIES` if you add a package), run `pytest -q tests/test_nightschool.py`, rebuild, push under a new tag and set `RATCHET_TRAIN_IMAGE` to it. If the image cannot build at all, the fallbacks in order are an NGC PyTorch base image (`nvcr.io/nvidia/pytorch`, a CUDA 12.8 tag) with the same pip steps, or Nebius's tutorial image `docker.io/axolotlai/axolotl:main-20260309-py3.11-cu128-2.9.1` ([fine-tuning tutorial](https://docs.nebius.com/serverless/tutorials/fine-tuning), read Oct 21, 2026) with `ratchet/` copied in; `versions.json` then records what that image holds.

### Step 4: Student endpoint (vLLM with runtime LoRA)

```bash
export RATCHET_STUDENT_KEY=$(openssl rand -hex 24)
DRY_RUN=1 bash deploy/student_endpoint.sh create   # nebius validates the request (--dry-run), creates nothing
bash deploy/student_endpoint.sh create             # once
export RATCHET_STUDENT_BASE=https://<endpoint host>/v1
bash deploy/student_endpoint.sh status             # prints "<id> <state>", e.g. "aiendpoint-... RUNNING"
bash deploy/student_endpoint.sh stop               # when idle
bash deploy/student_endpoint.sh start              # the same endpoint again: same URL, same token
```

The four actions:

| Action | What it runs |
|---|---|
| `create` | `nebius ai endpoint create ...` (below). Run it once: it stops with an error when an endpoint with the name exists |
| `start` (the default) | `nebius ai endpoint start --id <id>`. Nothing when the endpoint is already `RUNNING`, `STARTING`, `IMAGE_PULLING` or `PROVISIONING`; an error while it is `STOPPING` or `DELETING`. When no endpoint has the name, it runs `create` instead |
| `stop` | `nebius ai endpoint stop --id <id>`. Nothing when it is already `STOPPED` or `STOPPING`; an error when no endpoint has the name |
| `status` | Prints `<id> <state>` (exit 1 when there is no such endpoint) |

`nebius ai endpoint start` and `stop` take the endpoint's id, not its name (`--id`, required; there is no `--name`). The script finds the id by name (`RATCHET_ENDPOINT_NAME`, default `ratchet-student`) in `nebius ai endpoint list --format json`, read with `RATCHET_PYTHON`, or uses `RATCHET_ENDPOINT_ID` as given without the lookup. It never guesses: output that is not JSON, two endpoints with the name, an endpoint without an id, or a name missing from a list with more pages stops the script before anything starts, stops or is created; set `RATCHET_ENDPOINT_ID` then. (`nebius ai endpoint get-by-name` needs `--parent-id` and does not document what it does for a missing name, so the script reads the list.)

With `DRY_RUN=1`, `create` passes `--dry-run`; `start` and `stop` have no `--dry-run`, so the script prints the command instead of running it. The read-only list lookup still runs, and no uptime is recorded. Every command is echoed on stderr first, with the token shown as `<RATCHET_STUDENT_KEY>`.

After `create`, `start` and `stop`, the script records the endpoint in the ledger (`python -m ratchet.cost endpoint up|down`, with `RATCHET_DB` pointing at the ledger file), so the cost report knows the GPU-hours you paid for ([Cost estimates](#4-cost-estimates)). Recording is best effort: run where the ledger is not, the deploy still succeeds and prints the command to run there (or use `POST /ratchet/cost/endpoint` with an admin key). A stopped endpoint is not billed for compute or its own storage, but the mounted bucket still is, also while the endpoint is stopped ([Nebius Serverless overview](https://docs.nebius.com/serverless/overview), read Oct 21, 2026).

What `create` sets:

- Image: `vllm/vllm-openai:v0.18.0-cu130` (`RATCHET_VLLM_IMAGE`; `vllm/vllm-openai:v0.18.0` is the CUDA 12 build, for a host driver too old for CUDA 13)
- Platform and preset: `gpu-l40s-a`, `1gpu-8vcpu-32gb`
- `--container-port 8000`, `--shm-size 16Gi`
- `--volume $RATCHET_BUCKET_ID:/data`, and `--subnet-id $RATCHET_SUBNET_ID` when set
- `--auth token --token $RATCHET_STUDENT_KEY`
- `--env VLLM_ALLOW_RUNTIME_LORA_UPDATING=True`
- vLLM args: `--model nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16 --trust-remote-code --enable-lora --max-lora-rank 16 --max-loras 4 --mamba_ssm_cache_dtype float32 --max-model-len 8192 --max-num-seqs 64 --default-chat-template-kwargs '{"enable_thinking":false}'`. `--mamba_ssm_cache_dtype float32` is the model card's flag ([model card](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16), read Oct 21, 2026). The context cap (`RATCHET_SERVE_MAX_LEN`) is far below the card's 262,144-token example: ticket prompts are short, and a longer one gets vLLM's 400 and escalates. `--default-chat-template-kwargs` (vLLM v0.18.0, parsed as JSON) turns thinking off unless a request asks (`RATCHET_SERVE_TEMPLATE_KWARGS`, empty to leave it out). With `RATCHET_REASONING_PARSER_PLUGIN=/data/nano_v3_reasoning_parser.py` (the card's parser, uploaded to the bucket), it adds `--reasoning-parser-plugin /data/nano_v3_reasoning_parser.py --reasoning-parser nano_v3`.

The vLLM arguments are one string that the container splits as a shell would (Nebius's fine-tuning tutorial relies on the same splitting for its `bash -c` script), so the JSON keeps its single quotes. `--dry-run` validates the request but cannot show the split: on day 1, check that the endpoint reaches `RUNNING` and that a request without `chat_template_kwargs` returns no reasoning. If vLLM refuses the flag, recreate the endpoint with `RATCHET_SERVE_TEMPLATE_KWARGS=` and rely on the per-request `chat_template_kwargs`.

Checks once it is up:

```bash
curl -s $RATCHET_STUDENT_BASE/models -H "Authorization: Bearer $RATCHET_STUDENT_KEY"
# load an adapter by hand (ratchet.live does this automatically after training)
curl -s ${RATCHET_STUDENT_BASE%/v1}/v1/load_lora_adapter -H "Authorization: Bearer $RATCHET_STUDENT_KEY" \
  -H 'content-type: application/json' -d '{"lora_name":"student-v1","lora_path":"/data/adapters/student-v1"}'
```

> Adapters loaded at runtime are lost when the endpoint restarts. The gateway notices vLLM's 404 for an adapter and loads it again by itself (at most once per adapter per minute; `adapter_reloaded` events), so student calls fall back to the teacher for a few requests (they still succeed, at teacher cost). `python -m ratchet.live reload` re-loads every `live`, `canary` and `shadow` adapter by hand if that fails. See [RUNBOOK.md](RUNBOOK.md#student-endpoint-down-or-adapters-lost).

### Step 5: Training job

`deploy/train_job.sh <version>` submits (`DRY_RUN=1` adds `--dry-run`: nebius validates the request and submits nothing):

```
nebius ai job create --preemptible --follows-spot-price --name ratchet-<version> --image $RATCHET_TRAIN_IMAGE
  --platform gpu-l40s-a --preset 1gpu-8vcpu-32gb --volume $RATCHET_BUCKET_ID:/data
  --disk-size 200Gi --timeout 2h --container-command bash
  --args "-c 'bash /app/install_kernels.sh && python -m ratchet.nightschool.train --data /data/night/train.jsonl --base <base> --out /tmp/adapter-out && mkdir -p /data/adapters/<version> && cp -r /tmp/adapter-out/. /data/adapters/<version>/'"
  [--subnet-id $RATCHET_SUBNET_ID] [--registry-secret $RATCHET_REGISTRY_SECRET]
```

`nebius ai create` is the interactive wizard; scripts use `nebius ai job create` ([CLI reference](https://docs.nebius.com/cli/reference/ai/job/create)). `--timeout` is the job's own limit (1 h to 168 h, default 24 h), `--subnet-id` defaults to the project's default subnet, and `--registry-secret` names a MysteryBox secret with the credentials of a private image. With `RATCHET_BUCKET_S3` set, the script then polls for `adapters/<version>/adapter_model.safetensors` every 30 s for up to 2 h. That makes it suitable as `RATCHET_TRAIN_CMD`, which must block until the adapter exists; a refused job fails the script, so the night stops instead of waiting.

What the 28 Oct 2026 run taught the script (evidence: `evidence/nightschool-nebius/`): the job is **spot by default** (`--preemptible --follows-spot-price`); `RATCHET_TRAIN_ON_DEMAND=1` is the only way to pay the on-demand price. The adapter is saved to the job's **local disk** and then copied into the bucket mount, because the mount refuses the in-place rename safetensors makes (`EPERM`). `einops` is pinned in `deploy/train-versions.txt` and installed by the image (rebuild it with the build-train-image workflow after this change). With `RATCHET_BUCKET_S3` set, the script and the night both need the **`aws` CLI on PATH** and stop with a message when it is missing, instead of waiting for an adapter they cannot see. The student endpoint must be **running before the hot-load**: the night does not start or wake it, and the first call after a hot-load into a cold LoRA endpoint is slow, so the golden eval's student calls have their own timeout, `RATCHET_NIGHT_STUDENT_TIMEOUT_S` (120 s by default; the request path keeps `RATCHET_STUDENT_TIMEOUT_S`).

Training defaults (`train.py`): 2 epochs, learning rate 2e-4, LoRA rank 16 (alpha 32), max sequence 2048, batch 8, bf16, assistant-only loss.

### Step 6: Gateway

Any small VM or container with Python 3.10+ works:

```bash
pip install -e .
export RATCHET_MODE=live RATCHET_DB=/var/lib/ratchet/ratchet.db
export RATCHET_API_KEYS="app:$(openssl rand -hex 24)" RATCHET_DASHBOARD_KEYS="me:$(openssl rand -hex 24)"
python -m ratchet.config check     # exit 0 clean, 1 warnings, 2 errors: the gateway would not start
uvicorn ratchet.gateway:app --host 0.0.0.0 --port 8000 --workers 1
```

A bad setting stops startup with one error that lists every problem by variable ([CONFIGURATION.md](CONFIGURATION.md#configuration-checks)); `python -m ratchet.config check` gives the same verdict without starting anything, so run it after each change to the environment. Set `RATCHET_STRICT_CONFIG=1` to make warnings (an unknown or misspelled variable, an unpriced model) fatal too.

Health checks ([API.md](API.md#get-readyz), [ADR 0070](adr/0070-configuration-checks-and-readiness.md)):

- **Load balancers and deploy scripts: `GET /readyz`.** 200 (`ready`, or `degraded` while the teacher answers for a student endpoint that is down) keeps the instance in rotation; 503 `not_ready` (the ledger cannot be written, or no teacher can answer) takes it out. An instance taken out comes back by itself: once the teacher's breaker has waited out its cooldown and the teacher's `/models` answers, `/readyz` says 200 `degraded` (`probe_due`) and the next request probes the teacher. The probes are cached for 15 s and spend no tokens, so poll it as often as you like. The student endpoint gets one `GET /models` per 5 minutes from each gateway; if your platform scales an idle endpoint to zero and counts that request as traffic, it keeps the endpoint running.
- **Restarts (Docker, systemd, a Kubernetes liveness probe): `GET /healthz` or `/livez`.** It only says the process answers, also with every worker thread busy. Never restart on `/readyz`: a teacher outage would turn into a restart loop.

Use **one worker**. The ledger is a single SQLite connection inside the process, and the release gate is not designed for concurrent writers (see [ADR 0006](adr/0006-sqlite-ledger-for-v0.md)). Live mode does not start without `RATCHET_API_KEYS` (or `RATCHET_ALLOW_UNAUTHENTICATED=1` when a proxy checks callers instead), and its dashboard API (`/ratchet/stats`, `/ratchet/bom`, ...) answers only admin and dashboard keys unless `RATCHET_PUBLIC_DASHBOARD=1` ([API.md](API.md#access-levels)). Put TLS in front with a reverse proxy.

### Step 7: Cron

`python -m ratchet.live` must run on a machine that has the `nebius` CLI, the `aws` CLI (for sync and polling), the same `RATCHET_DB` file, and network access to the student endpoint. In v0.1 that means the same host as the gateway. The ledger runs in SQLite's WAL journal (`RATCHET_DB_SYNC=NORMAL`, the default; [ADR 0060](adr/0060-hot-path-caches-and-throttled-tick.md)), which needs every process that opens it on that one host with the file on a local disk: never put `RATCHET_DB` on the bucket mount or another network filesystem (if you must, set `RATCHET_DB_SYNC=FULL` for every process).

```cron
# Night School at 02:30 local time
30 2 * * *  cd /opt/ratchet && . ./.env && python -m ratchet.live night >> /var/log/ratchet-night.log 2>&1
# Release gate every 5 minutes (the gateway also ticks on every request)
*/5 * * * * cd /opt/ratchet && . ./.env && python -m ratchet.live tick  >> /var/log/ratchet-tick.log 2>&1
# Savings receipts of every ledger day that has ended, hourly: a ledger day starts at the first request's time of day,
# not at 00:00 UTC (the gateway also seals when a receipt is asked for)
20 * * * *  cd /opt/ratchet && . ./.env && python -m ratchet.live seal  >> /var/log/ratchet-seal.log 2>&1
```

`python -m ratchet.live night` runs `run_night` with `NebiusTrainer` and `LiveEvaluator` (judge = `RATCHET_JUDGE_MODEL`), writes the adapter's BOM and `NOTICE.md` into `RATCHET_DATA_DIR/adapters/<name>/` and runs `RATCHET_SYNC_CMD` again, then prints the last 10 events. If that second sync fails, the night still counts (the version exists and is in the release gate), a `sync_failed` event is written and the command exits non-zero: run `RATCHET_SYNC_CMD` by hand. `tick` runs `ReleaseGate.tick()` once. `seal` seals the savings receipts of every day that has ended ([RUNBOOK](RUNBOOK.md#savings-receipts)); `purge` seals them too before it folds request rows. Without `RATCHET_STUDENT_BASE`, `night` stops before syncing or starting the training job, and `reload` stops with the same message (nothing to reload is not an error). The same commands run offline in `tests/test_live.py` and `tests/test_licensing.py`, against fake endpoints, before they run here.

### Export an adapter with its licenses

To hand an adapter to someone (another team, a customer, a model registry), export it with its licenses ([ADR 0090](adr/0090-licenses-travel-with-the-adapter.md)):

```bash
aws s3 sync s3://<bucket name>/adapters/student-v3 ./data/adapters/student-v3 --endpoint-url $RATCHET_S3_ENDPOINT  # weights, if not local
python -m ratchet.live export --version 3 --out ./student-v3-export
# exported student-v3 (v3) from data/adapters/student-v3 to student-v3-export
# bill of materials: student-v3-export/ratchet_bom.json (signature valid (Ed25519, key 6cf6bc1a39c4491d, embedded key only; ...))
# notice: student-v3-export/NOTICE.md
# bom sha256: 9b1f…
```

The folder gets the adapter files from `RATCHET_DATA_DIR/adapters/<name>/` when they are on this machine. With an upload-only `RATCHET_SYNC_CMD`, that folder holds only the `ratchet_bom.json` and `NOTICE.md` the night wrote, not the weights; the export then counts no adapter files and says so (`exported the licenses of student-v3 (v3) to student-v3-export; the adapter weights (adapter_config.json and adapter_model.safetensors) are not at data/adapters/student-v3: copy them from the bucket's adapters/student-v3/ into student-v3-export`). It also gets the BOM stored at training time (`ratchet_bom.json`, checked against its own `sha256` and signature first; a BOM that does not verify is refused) and the `NOTICE.md` that BOM signed: the base model's license (the NVIDIA Nemotron Open Model License for the Nano models), each teacher's license and URL, that the targets were teacher outputs only, and the Ratchet version. The output folder must be new or empty, and outside `RATCHET_DATA_DIR`, which the next sync would upload to the bucket. Give the recipient your public key: `python -m ratchet.bom verify student-v3-export/ratchet_bom.json --public-key <yours> --notice student-v3-export/NOTICE.md` prints `OK` for the BOM and `OK: NOTICE.md matches the BOM` for the notice (its SHA-256 equals the BOM's `notices[0].sha256`). Keep `NOTICE.md` with the adapter wherever it goes; a version trained before v0.4 has no BOM and is refused.

### Step 8: Measure

The live-run harness (`python -m ratchet.liverun`, [ADR 0121](adr/0121-live-measurement-method.md)) runs on the same host and with the same `.env` as `python -m ratchet.live`, because it runs the nights itself between its rounds and reads the gateway's ledger file. Add `liverun:<key>` to the gateway's `RATCHET_API_KEYS` and set the run's gate profile (`RATCHET_SEQUENTIAL=1`, `RATCHET_CANARY_AUDIT_RATE=0.5`) on the gateway, restart it, pause the cron nights, then:

```bash
export RATCHET_LIVERUN_KEY=<the liverun key>   # in this shell only, never a flag
make live-plan && make live-smoke && make live-run MAX_USD=30
```

On a host or image without `.git`, set `RATCHET_GIT_SHA` to the deployed commit (`git rev-parse HEAD` where you built it) so the results file names the code; it carries a digest of the sources either way. The [runbook](RUNBOOK.md#live-measurement-run) has the prerequisites, the stop reasons, resuming, the repeat run, entering the Nebius bill and publishing.

## 3. Keeping the live gateway up for judging (Dec 1–15, 2026)

Judges need a working live URL for two weeks, and a GPU endpoint running nonstop is the biggest cost.

**Where the live gateway runs today:** the live gateway (live.ratchet.dualmindlab.tech) runs on Cloudflare Workers with Containers, see [deploy/cloudflare-live/README.md](../deploy/cloudflare-live/README.md). The live Worker limits each visitor to 3 "Try a real request" calls and 3 Scout runs per UTC day (a Durable Object counts them by a hash of the visitor's address, before the container wakes; `EDGE_DAILY_TRIES` and `EDGE_DAILY_SCOUT` change the numbers, `0` turns one off; a judge's cookie skips the Scout count), and the shared site key's own limit is 60 requests a minute. To know the visitor's address behind the main site's Pages proxy, both sides need the same secret `EDGE_TOKEN`: `npx wrangler secret put EDGE_TOKEN --name ratchet-live` in `deploy/cloudflare-live`, and `npx wrangler pages secret put EDGE_TOKEN --project-name ratchet` for the site (never in the repo); without it all main-site visitors count as one address. The Nebius CPU VM kit below is the alternative host: it is written and tested offline, ready to run with `make nebius-plan`.

**Alternative host: one Nebius CPU VM.** [DEPLOY_NEBIUS_VM.md](DEPLOY_NEBIUS_VM.md) sets up the gateway with Docker Compose and Caddy (automatic HTTPS) on a 2 vCPU / 8 GB VM, with the budget guard, an opt-in edge guard for `/v1/*` and `/openai/*`, backups, monitoring, updates and teardown. It starts in mock mode with a warmed-up student. The points below still apply to it.

1. **The live gateway at the root domain** (the URL for Stage One), in live mode with `RATCHET_STUDENT_BASE` **unset** when the GPU endpoint is off. Student calls then go to the hosted, untuned `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` on Token Factory, and you pay per token instead of per GPU-hour. Be honest that this is the fallback, not the tuned student. Note that a version's calibrated threshold was set for the tuned adapter, so precision on the fallback may be lower; audits will show it.
2. **Stop the vLLM endpoint when not recording or testing** (`bash deploy/student_endpoint.sh stop`, then `start` to bring the same endpoint back). A stopped endpoint is not billed for compute or its own storage; a mounted Object Storage bucket still is ([Serverless overview](https://docs.nebius.com/serverless/overview), read Oct 21, 2026).
3. **Cap teacher spend:** keep `RATCHET_AUDIT_RATE` at 0.05 or lower and watch the Token Factory usage page daily. In live mode, keep Scout's budget guard on (`RATCHET_SCOUT_DAILY_BUDGET_USD`, `RATCHET_SCOUT_MAX_RUNS_PER_DAY`, `RATCHET_SCOUT_RUNS_PER_HOUR`; see [CONFIGURATION.md](CONFIGURATION.md#scout-the-multi-agent-research-tool)) and turn on the Caddy guard (`RATCHET_API_GUARD=guarded`).

## 4. Cost estimates

These are **estimates**: Serverless pricing was not confirmed (on-demand list prices assumed), and token counts depend on your prompts.

Ratchet measures the real thing once it runs: `python -m ratchet.cost report` (or `GET /ratchet/cost`) prices the recorded endpoint and training hours at `RATCHET_STUDENT_GPU_USD_PER_HOUR` and `RATCHET_TRAIN_GPU_USD_PER_HOUR`, reports savings per token and by GPU-hours paid, the break-even volume, and whether self-hosting pays at your traffic ([RUNBOOK.md](RUNBOOK.md#cost-and-the-self-host-recommendation), [ADR 0023](adr/0023-utilization-aware-cost-accounting.md)). At $1.35 per GPU-hour (an L40S on demand, [Nebius pricing](https://docs.nebius.com/compute/resources/pricing), read Oct 22, 2026; an estimate until your bill confirms it) a 24/7 endpoint breaks even near 1,350 student-served requests an hour; below that, use the hosted fallback or stop the endpoint when idle. Above it the endpoint costs less than the teacher, but the untuned hosted fallback is cheaper per answer below its crossover (roughly 52,000 student answers an hour at list prices), if it serves as many requests as the tuned student.

| Item | Assumption | Estimate |
|---|---|---|
| Training run | L40S at $1.35/GPU-hour, 30–60 min per night | ~$0.70–1.35 per night (estimate) |
| Student endpoint | L40S at $1.35/GPU-hour, only while testing or recording | ~$32 per full day if left on (estimate) |
| Teacher calls | Ultra $1 / $3 per 1M tokens (list price in `ratchet/config.py`, research notes, 2026-10-25; unverified); ~150 input + ~380 output tokens per call | ~$1.30 per 1,000 calls |
| Judge | Super $0.30 / $0.90 (same source); only for free-text tasks | small |
| Hackathon total | 6–8 training runs, endpoint on a few hours a day, ~$20–30 of teacher calls | **~$35–75** (estimate) |

## 5. Verification after deploy

1. `curl $GATEWAY/healthz` returns `{"status":"ok",...}`, `curl $GATEWAY/readyz` returns 200 with `"status": "ready"` (or `degraded`, with the reason under `checks`; a routed adapter the endpoint no longer lists is in `checks.student.missing_adapters` and queued for reload), and `curl -H "Authorization: Bearer $KEY" $GATEWAY/v1/models` returns `ratchet` (drop the header if `RATCHET_API_KEYS` is not set).
2. Send 20 real requests. `GET /ratchet/stats` (with `-H "Authorization: Bearer $DASHBOARD_KEY"` unless the dashboard is public) shows them with `served_by=teacher` (cold start), and `GET /ratchet/bom` shows the teacher with license `OpenMDW-1.1`.
3. Run `python -m ratchet.live night` once you have at least 50 new examples. Expect a `trained` event and either `canary` or `rejected`, and a bill of materials at `data/boms/student-v1.json` (also at `GET /ratchet/versions/1/bom`), with `ratchet_bom.json` and `NOTICE.md` in the bucket's `adapters/student-v1/` next to the weights. With `RATCHET_BOM_SIGNING_KEY` set, `python -m ratchet.bom verify data/boms/student-v1.json --public-key <yours>` prints `OK`.
4. Check that `curl $RATCHET_STUDENT_BASE/models` lists `student-v1` after the night.
5. Watch the `canary` → `live` events over the next requests. Audits need traffic: at a 5% audit rate and 10% canary, reaching 20 audits takes roughly 4,000+ requests, so for a short test run raise `RATCHET_AUDIT_RATE`, or lower `RATCHET_MIN_AUDITS` (the default is 20).

## 6. The video link

`video.ratchet.dualmindlab.tech` is a short, counted link to the video (`https://youtu.be/GIeSmDtopfs`): a small Cloudflare Worker with a D1 database, in `deploy/cloudflare-video/`. Only the manual workflow `deploy-cloudflare-video` deploys it (Actions, Run workflow, type `deploy-video`; the same two repository secrets as the other deploys). It runs the Worker's tests, applies the D1 migrations, deploys, and then checks the link from outside with HEAD requests only, so a deploy adds no click.

- **Every GET or HEAD**, on any path, is a `302` to the video with `Cache-Control: no-store`. Both are counted, a HEAD (link checkers, the deploy smoke) always as a bot with the reason `head`; `/robots.txt`, `/favicon.ico` and `/.well-known/*` are not counted. The count is written after the answer has gone out (`ctx.waitUntil`), so a slow or failing D1 never delays or breaks the redirect.
- **Where a link was posted** goes in `?from=`: `devpost`, `readme`, `site`, `live`, `demo`, `docs`, `hf`, `youtube`, `x`, `linkedin`, `discord`, `email`, `release`, `judges` or `other`. A link without it counts as `direct` and any other value as `other`; the raw query is never stored. Ratchet's own `/video` paths forward here with their source in a no-store 302: the story site (`site`, in `web/public/_worker.js`), the live gateway (`live`, answered by its Worker before the container is touched) and the docs (`docs`, `docs-site/public/_redirects`); the README's links use `readme`, the story page's `site` and the judges' page's `judges`. A test in `deploy/cloudflare-video/test/config.test.mjs` checks every such tag against the allowlist.
- **Human or bot, and why.** Most "human" clicks on a link in a message are email and chat link scanners, previews and data-centre crawlers with a browser user agent, so the verdict has several rules (one small function, `verdictOf` in `src/visitor.ts`, tested in `test/visitor.test.mjs`). The first that matches sets `kind` to `bot` and records the reason; none matching is `human` with an empty reason:
  1. `ua`: a user agent that names a bot, crawler, spider or preview (Slackbot, Twitterbot, Discordbot, facebookexternalhit, LinkedInBot, WhatsApp, TelegramBot, Googlebot, bingbot, Applebot and the like), a script or library (curl, python-requests, Go-http-client, ...), a headless browser, this repository's link check or the deploy smoke, an empty user agent, or a browser prefetch.
  2. `verified-bot`: Cloudflare marks the request as a verified bot.
  3. `head`: a HEAD request (a browser opening a link sends a GET).
  4. `scanner`: the network is an email-security scanner: Proofpoint (AS22843, AS26211), Mimecast (AS30031), Barracuda (AS15324), or the network name says so.
  5. `datacenter`: the network is a hosting or cloud provider: Amazon (AS16509, AS14618), Google (AS15169, AS396982), Microsoft (AS8075, which also runs Outlook and Defender link scanning), Cloudflare (AS13335), DigitalOcean (AS14061), OVH (AS16276), Hetzner (AS24940), Akamai/Linode (AS63949), Oracle (AS31898), Alibaba (AS45102), Tencent (AS132203), Contabo (AS51167), Vultr (AS20473), Scaleway (AS12876), Meta (AS32934); or its name has hosting, cloud, data center, server, vps or colocation in it.
  6. `no-accept-language`: a GET without an Accept-Language header (every browser sends one).

  A chat app's link preview is a bot, and the person who then clicks the link is a human. A real person on a corporate Microsoft or Google network is counted as a bot by rule 5; that is the price of filtering the scanners.
- **What is stored** in D1 (`ratchet-video`): a count per UTC day, source and kind (`clicks`); for each counted visit (`events`) its time, source, kind and bot reason, the country, city and region and the network's name and number that Cloudflare reports for the connection, three coarse words for the device (desktop, phone or tablet; Windows, macOS, Android, iOS, Linux, ChromeOS or other; Chrome, Safari, Firefox, Edge, Samsung or other) and a visitor hash; and how many alert emails went out per UTC day (`mail`). All of the per-visit data is deleted after 30 days by the daily cron. **Never stored:** the IP address, the full user agent, a cookie, the path or the raw query. The visitor hash is the first 16 hex characters of HMAC-SHA256 of the IP address, keyed with a salt derived from the `STATS_KEY` secret and the UTC day. The salt changes every day, so the hash cannot follow a person from one day to the next, and it cannot be turned back into the IP address. It counts unique humans per day and limits the alert emails to one per visitor per day. Workers Logs are off for this Worker, so request details are not kept there either; `npx wrangler tail ratchet-video` shows its failure lines live. Migration `0002_visitor_details.sql` adds these columns; rows from before it have empty fields.

**Reading the stats.** The stats are a private endpoint, not a public page (it answers 404 without the key). `GET /stats` on the Worker's own address (`VIDEO_STATS_URL` below) with the Worker secret `STATS_KEY` as a bearer token returns totals per day and source (human and bot apart), per source, per country over the last 30 days, unique humans per day, bots by reason, the top networks of humans and of bots, the top cities and devices of humans, and the emails sent; add `?format=html` for the same as tables. Without the key, or with a wrong one, `/stats` is a 404. The key is kept outside the repository; to replace it, pipe a new random value into `npx wrangler secret put STATS_KEY --name ratchet-video` from `deploy/cloudflare-video/`.

```bash
curl -s -H "Authorization: Bearer $(cat "$STATS_KEY_FILE")" "$VIDEO_STATS_URL"   # the Worker's private /stats endpoint
```

**Email alerts.** With `EMAIL_ENABLED` set to `"1"` in `deploy/cloudflare-video/wrangler.json` (it is: the destination address is verified), each human visitor sends a short plain-text email from `EMAIL_FROM` to `EMAIL_TO` through the Worker's `send_email` binding (Cloudflare Email Routing), at most one per visitor per UTC day and never for a bot: the city and country, the network, the device (for example "Windows desktop, Chrome"), the source, the time in IST and "unique humans today: N", never an IP address or a user agent. At most 40 visit emails go out per UTC day; past that, visits are still counted. A daily cron at 03:30 UTC (09:00 IST) emails a summary of the previous UTC day (humans, unique humans and bots, bots by reason, the top 5 networks of humans and of bots, clicks per source and countries; nothing for a day without clicks) and prunes the old events, also while email is off. To switch email on for a new address:

1. The destination address must be verified in Cloudflare Email Routing (dashboard: the `dualmindlab.tech` zone, Email, Email Routing, Destination addresses; Cloudflare emails it a confirmation link). The sender is on `mail.dualmindlab.tech`, which already has Cloudflare's Email Routing and Email Sending records; the root domain's own mail records are not touched.
2. Set `EMAIL_ENABLED` to `"1"` in `wrangler.json`, merge, and run `deploy-cloudflare-video`.

Setting it to `"0"` (also the behaviour when the variable is missing) stops every email at the next deploy. The redirect and the counts never depend on email: a missing binding, a bad address or a failed send only skips the email.
