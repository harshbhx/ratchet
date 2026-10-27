# Where everything runs

![Where everything runs. Real today: your laptop (development and tests, CPU), a borrowed GPU (the original adapters' training), Cloudflare (the live demo, the simulation demo, the story site and docs, all CPU), Nebius Token Factory (the teacher) and, from 27 Oct 2026, one hand-run Nebius Serverless Job on an L40S (the round-2 retraining, with an Object Storage bucket). Scripted and tested offline, ready to run: a Nebius CPU VM, a Serverless Endpoint for the student, and the Serverless Job started by the nightly loop.](img/where-it-runs.svg)

**When the live demo has slept.** The live gateway's container sleeps after 30 minutes idle (a Cloudflare setting), so the first visitor after a quiet spell waits about 20 seconds while it starts, and its ledger counters reset. Instead of an error, the main site shows a "Waking up the live demo" banner and fills in by itself; the live address shows a splash page that reloads itself. This is a cost choice, not an outage.

This page says, for every part of Ratchet, where it runs **today** and where it is **designed** to run on Nebius AI Cloud. Read the last column first: **Real** and **Simulation** rows run today. **Scripted, ready to run** means the scripts exist and pass their tests against a fake `nebius` command, ready for `make nebius-plan` and `make nebius-up`. Token Factory (the teacher's calls) and one hand-run Serverless Job (the round-2 retraining, 27 Oct 2026) are the Nebius services that have run. The student endpoint and the VM kit have not.

## The table

| Component | What it is | Runs today where | CPU or GPU | Designed to run on Nebius as | Status |
|---|---|---|---|---|---|
| Gateway and router | The FastAPI app: the OpenAI-compatible routes, the cascade (student first, teacher on doubt), the release gate | The live and simulation demos on Cloudflare Workers with Containers; your laptop in mock mode | CPU | A small CPU VM with Docker Compose and Caddy ([`deploy/vm`](../deploy/vm)) | Real today; the VM is Scripted, ready to run |
| Ledger | One SQLite file: requests, stored teacher answers with their license, versions, signed bills of materials | On the live demo container's disk (a restart resets the day's counters); in memory in mock mode | CPU | The VM's Docker volume, with a daily online backup | Real today; the VM is Scripted, ready to run |
| Night School (training) | Builds the dataset, fine-tunes a LoRA adapter, grades it on the held-out golden set | On 27 Oct 2026 the round-2 text-to-SQL recipe (same 4,322 examples, same settings) was retrained on Nebius AI Cloud by hand, as a Serverless Job on one preemptible (spot) L40S, with checkpoints in an Object Storage bucket (evidence: [`evidence/text2sql-round2-nebius/`](../evidence/text2sql-round2-nebius/)). The original published adapters were trained by hand on a borrowed GPU. The nightly loop itself runs in the simulator | GPU | A Nebius Serverless Job on an L40S, with a bucket mounted at `/data` ([`deploy/train_job.sh`](../deploy/train_job.sh)) | Real (the hand-run Nebius job and the borrowed-GPU training); Simulation (the nightly loop); Scripted, ready to run (the job started by the nightly loop) |
| Student serving | Nemotron 3 Nano 4B with a LoRA adapter you own, served by vLLM | The public demo has no student endpoint yet, so the teacher answers every request; the simulation uses a mock student | GPU | A Nebius Serverless Endpoint on an L40S ([`deploy/student_endpoint.sh`](../deploy/student_endpoint.sh)) | Scripted, ready to run |
| Teacher | Nemotron 3 Ultra, an open model whose license allows training on its outputs | Nebius Token Factory, called per token from the live demo | Hosted by Nebius; you do not size it | The same | Real |
| Scout and Tavily | The research agents on the same gateway; Tavily is the web-search API they call (not a Nebius product) | The live demo (real Token Factory and Tavily calls, under a spend guard) and recorded runs; a mock research world in the simulation | CPU | The same gateway on the VM | Real; Simulation (the mock world). The story site's `/scout` page uses the live gateway (`SCOUT_ORIGIN`, [DEPLOY_PAGES.md](DEPLOY_PAGES.md)) |
| Dashboard and web app | The React app the gateway serves at `/`, `/scout` and `/dashboard` | Both demos, from the gateway image | CPU | The VM, from the same image | Real |
| Docs site and story site | Static pages built from `docs-site/` and `web/` | Cloudflare Pages | none (static files) | Nothing planned: static files stay where they are | Real |
| Live demo | `live.ratchet.dualmindlab.tech`: the gateway in live mode | Cloudflare Workers with Containers ([`deploy/cloudflare-live`](../deploy/cloudflare-live/README.md)) | CPU | The VM runs the same image | Real |
| Simulation demo | `demo.ratchet.dualmindlab.tech`: mock models, no keys, every number labelled Simulation | Cloudflare Workers with Containers ([`deploy/cloudflare`](../deploy/cloudflare)) | CPU | The VM runs it beside the live gateway at `sim.<domain>` | Simulation |
| Main site | `ratchet.dualmindlab.tech`: static pages on Cloudflare Pages whose worker sends every gateway-backed path (dashboard, judges, `/ratchet/*`, `/scout/*`) to the live gateway by default; `GATEWAY_ORIGIN` switches it ([`DEPLOY_PAGES.md`](DEPLOY_PAGES.md)) | Cloudflare Pages | none | the gateway serves the same pages | Real for gateway numbers; reference runs on it stay labelled Simulation |
| Local development | `make demo`: the gateway in mock mode on `localhost:8000` | Your laptop | CPU | Not applicable | Real |
| Tests and CI | `pytest`, lint and the reference runs: no network, no GPU | Your laptop and GitHub Actions | CPU | Not applicable | Real |

## The router is just CPU

The gateway does no model inference. It checks keys, routes a request, calls the student and teacher over HTTP, writes the ledger and serves the web pages, so a 2 vCPU, 8 GB machine is enough and costs about six cents an hour (estimate, below). That is why it already runs on Cloudflare's CPU-only containers and on a laptop. Only two things need a GPU: **training** (Night School, a job that runs for under an hour a night and then stops) and **serving the student** (an endpoint that bills for every hour it is on, so it should be stopped when idle). Without a student endpoint the whole system still works: the teacher answers every call, which is exactly how the live demo runs today.

## One command to host it all on Nebius

> **Scripted and tested offline, ready to run, apart from one hand-run job.** Only one Nebius GPU step has run for real: the round-2 text-to-SQL retraining on 27 Oct 2026, as a Serverless Job on one preemptible L40S, started by hand (evidence: [`evidence/text2sql-round2-nebius/`](../evidence/text2sql-round2-nebius/)). Everything else below, including the student endpoint and the VM kit, has not run. `deploy/nebius/nebius.sh` and the scripts it calls were written from the Nebius CLI reference and tested offline against a fake `nebius` command, a fake `ssh` and a stub smoke check. Nothing here is endorsed by or affiliated with Nebius. Do the [day-1 checks](DEPLOY_NEBIUS_VM.md#what-is-confirmed-and-what-is-not) on the first real run.

```bash
make nebius-plan                      # prints every command; changes nothing; calls no Nebius API
RATCHET_I_UNDERSTAND_THIS_COSTS_MONEY=1 make nebius-up YES=1                       # the CPU VM only
RATCHET_I_UNDERSTAND_THIS_COSTS_MONEY=1 make nebius-up YES=1 ARGS="--with-student" # plus the GPU student endpoint
make nebius-status                    # read-only: the VM, the endpoint, the URLs
RATCHET_I_UNDERSTAND_THIS_COSTS_MONEY=1 make nebius-down YES=1                     # stop the endpoint, delete the VM
```

The same, without `make`: `bash deploy/nebius/nebius.sh plan|up|status|down [--yes] [--with-student] [--with-training] [--live]`.

**A real run needs both `--yes` and `RATCHET_I_UNDERSTAND_THIS_COSTS_MONEY=1`.** Without both, `up` and `down` are the same dry run as `plan`: a local stand-in replaces the `nebius` command, so no Nebius API is called and nothing is created, started, stopped or deleted. The GPU parts are opt-in: `--with-student` adds the bucket and the student endpoint, `--with-training` also submits one training job, and `--live` switches the VM's gateway to live mode.

What `up` does, in order:

1. **Preflight.** The `nebius` CLI is installed and signed in, `jq` and `ssh` exist, the project has a subnet, the variables each flag needs are set (it names every missing one), and the platform fits the region (`cpu-e2` exists only in `eu-north1`). It prints the estimated hourly cost.
2. **The CPU VM** with the gateway, the simulation host and Caddy: [`deploy/vm/create_vm.sh`](../deploy/vm/create_vm.sh).
3. **With `--with-student`: a bucket** (unless `RATCHET_BUCKET_ID` is set) and **the student endpoint**: [`deploy/student_endpoint.sh start`](../deploy/student_endpoint.sh), which creates it when no endpoint has the name. With `--with-training` it then submits [`deploy/train_job.sh`](../deploy/train_job.sh) (the dataset must already be in the bucket at `night/train.jsonl`, and `RATCHET_TRAIN_IMAGE` must name an image built from `deploy/Dockerfile.train` and pushed where Nebius can pull it). It always prints the command that stops the GPU.
4. **Wiring.** `RATCHET_STUDENT_BASE` and `RATCHET_STUDENT_KEY` (and, with `--live`, `RATCHET_MODE=live` and the keys) are written to the VM's `.env` over `ssh` by [`deploy/vm/set_env.sh`](../deploy/vm/set_env.sh), with each value on stdin so it never appears in a command line, a log or the output; then [`deploy/vm/update.sh`](../deploy/vm/update.sh) rebuilds, checks the configuration and restarts.
5. **Smoke check** from outside with [`scripts/demo_check.py`](../scripts/demo_check.py), then the URLs and the teardown command.

`down` stops the student endpoint **first** (a stopped endpoint is not billed for compute or its own storage; its mounted bucket still is), then deletes the VM and its boot disk. It never deletes the endpoint or the bucket: it prints those commands, because deleting them also deletes your adapters.

What you need before a real run: the Nebius CLI signed in, `NEBIUS_PROJECT_ID`, an SSH key, the repository public on GitHub (the VM clones it), and `RATCHET_STUDENT_KEY` for `--with-student` (`openssl rand -hex 24`). Every variable is in [CONFIGURATION.md](CONFIGURATION.md#deploy-scripts-deploy).

### Cost, as estimates

These are estimates from Nebius list prices read on Oct 20 to 22, 2026, in US dollars before tax. Your bill decides; the prices were not confirmed by a run.

| Part | Estimate | Source |
|---|---|---|
| CPU VM `cpu-e2`, `2vcpu-8gb` | about $0.060 an hour (about $43.80 a month) | [Compute pricing](https://docs.nebius.com/compute/resources/pricing), [DEPLOY_NEBIUS_VM.md](DEPLOY_NEBIUS_VM.md#cost-estimate) |
| 30 GiB boot disk | about $0.003 an hour ($2.13 a month) | the same |
| Student endpoint, one L40S | about $1.35 an hour while it runs, about $32 a day if left on | [DEPLOYMENT.md](DEPLOYMENT.md#4-cost-estimates) |
| One training job, one L40S | about $0.70 to $1.35 per run; the job's own 2 hour limit caps it near $2.70 | the same |
| Bucket | per GiB-month, also while the endpoint is stopped | the same |
| Teacher calls | about $1.30 per 1,000 calls at list prices | the same |

At demo traffic a 24/7 student endpoint costs more than it saves, and Ratchet's cost report says so ([Cost accounting](ARCHITECTURE.md#7-cost-accounting)). Use `--with-student` when you want to measure the student, and stop it afterwards.

More detail: [DEPLOY_NEBIUS_VM.md](DEPLOY_NEBIUS_VM.md) (the VM, monitoring, backups, teardown), [DEPLOYMENT.md](DEPLOYMENT.md) (the endpoint and the job step by step) and the guide [Host it on Nebius](https://docs.ratchet.dualmindlab.tech/guides/host-on-nebius/).

## Run it on your own machine

No keys, no GPU, no bill:

```bash
pip install -e ".[dev]"
make demo        # mock mode with a trained student, on http://localhost:8000
make test        # the offline test suite
```

`make demo` serves the story page at `/`, Scout at `/scout` and the dashboard at `/dashboard`, with simulated models. Every number it shows is labelled Simulation. Steps: [the README's quick start](../README.md#use-it).

## Run the live gateway on a VM

The live gateway (real Token Factory calls) runs on any machine that runs Docker. On a Nebius VM, the steps are in [DEPLOY_NEBIUS_VM.md](DEPLOY_NEBIUS_VM.md#switching-from-mock-to-live); `deploy/nebius/nebius.sh up --live` does the wiring for you. Anywhere else: `RATCHET_MODE=live` with `NEBIUS_API_KEY` and `RATCHET_API_KEYS` set ([LIVE_MODE.md](LIVE_MODE.md)). The public live demo uses Cloudflare Workers with Containers instead ([`deploy/cloudflare-live`](../deploy/cloudflare-live/README.md)), which is the host that has actually run.

## Unlock Scout runs as a judge

Hackathon judges can hold back runs and spend for themselves on the live Scout page. The access code is on the Devpost page, never in this repository. The picture below is an illustration of the page, not a recording.

![Illustration, not a screen recording, of the Scout page's judge box in four steps: click "I am a judge", type the code (shown only as dots), press Unlock, and see "Judge access on" with "Sign out".](img/anim/judge-box-howto.gif)

How the access works: [Scout](SCOUT.md) and [ADR 0120](adr/0120-judge-access-and-a-persistent-scout-guard.md).
