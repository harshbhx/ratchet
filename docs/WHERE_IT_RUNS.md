# Where everything runs

![Where everything runs. Real on Nebius: Token Factory (every model call on the live site: the teacher, the hosted Nano for the random split, the judge), a Serverless Job (the round-2 retraining, by hand, 27 Oct 2026), a Serverless Endpoint (the student, served for measured speed tests on 27 to 28 Oct 2026, deleted afterwards) and an Object Storage bucket. Real elsewhere: your laptop (development and tests, CPU), a borrowed GPU (the original adapters' training) and Cloudflare (the website, the docs and the live gateway, CPU). Built and tested, not run nightly on live traffic: the nightly training loop (Night School ran end to end once on Nebius by hand on 28 Oct 2026, on a CPU VM, a spot L40S job and an endpoint, and the golden gate rejected it) and the one-command CPU VM kit.](img/where-it-runs.svg)

**When the live gateway has slept.** The live gateway's container sleeps after 30 minutes idle (a Cloudflare setting), so the first visitor after a quiet spell waits about 20 seconds while it starts, and its ledger counters reset. Instead of an error, the main site shows a banner that the gateway is waking up and fills in by itself; the live address shows a splash page that reloads itself. This is a cost choice, not an outage.

This page says, for every part of Ratchet, where it runs **today** and where it is **designed** to run on Nebius AI Cloud. Read the last column first: **Real** rows run today. **Scripted, ready to run** means the scripts exist and pass their tests against a fake `nebius` command, ready for `make nebius-plan` and `make nebius-up`. Token Factory (the teacher's calls), one hand-run Serverless Job (the round-2 retraining, 27 Oct 2026) and one hand-run Serverless Endpoint (a speed test of the student, 27 Oct 2026) and one hand-run Night School (a CPU VM, a spot L40S job and an endpoint, 28 Oct 2026) are the Nebius services that have run. Night School ran end to end once on Nebius, by hand (28 Oct 2026): the gateway on a Nebius CPU VM, the training job on a spot L40S, the adapter served from a Nebius Serverless Endpoint. The golden gate rejected it (81.8% precision against the 95% target), so it was never made live ([`evidence/nightschool-nebius/`](../evidence/nightschool-nebius/README.md)). The live site's own gateway is still on Cloudflare and is not connected to that endpoint, and the loop has not run nightly on live traffic. The one-command VM kit and the nightly schedule have not run.

## The table

| Component | What it is | Runs today where | CPU or GPU | Designed to run on Nebius as | Status |
|---|---|---|---|---|---|
| Gateway and router | The FastAPI app: the OpenAI-compatible routes, the cascade (student first, teacher on doubt), the release gate | The live gateway on Cloudflare Workers with Containers | CPU | A small CPU VM with Docker Compose and Caddy ([`deploy/vm`](../deploy/vm)) | Real today; the VM ran once by hand for the 28 Oct Night School, the one-command kit is Scripted, ready to run |
| Ledger | One SQLite file: requests, stored teacher answers with their license, versions, signed bills of materials | On the live gateway container's disk (a restart resets the day's counters) | CPU | The VM's Docker volume, with a daily online backup | Real today; the VM ran once by hand for the 28 Oct Night School, the one-command kit is Scripted, ready to run |
| Night School (training) | Builds the dataset, fine-tunes a LoRA adapter, grades it on the held-out golden set | On 27 Oct 2026 the round-2 text-to-SQL recipe (same 4,322 examples, same settings) was retrained on Nebius AI Cloud by hand, as a Serverless Job on one preemptible (spot) L40S, with checkpoints in an Object Storage bucket (evidence: [`evidence/text2sql-round2-nebius/`](../evidence/text2sql-round2-nebius/README.md)). The retrained adapter ([on Hugging Face](https://huggingface.co/harshbhx/ratchet-nemotron-nano-4b-text2sql-nebius)) scored 40.7% (70 of 172) on exam 1 and 34.5% (69 of 200) on exam 2, against 39.0% and 33.0% for the original adapter: the result reproduces (the row-by-row differences are within chance). The original published adapters were trained by hand on a borrowed GPU. Night School then ran end to end once on Nebius, by hand, on 28 Oct 2026: a CPU VM imported 4,322 existing Ultra-labelled rows (no new teacher calls), a spot L40S job trained on 3,864 of them (2 epochs, 966 steps, 1,478 s), the adapter was hot-loaded into a Serverless Endpoint and the golden gate rejected it (81.8% precision against the 95% target on the 11 answers it would serve), so it was never made live; four job submissions, a re-run of the eval with a 120 s timeout through a one-off driver and five VM-only fixes are all stated in the evidence ([`evidence/nightschool-nebius/`](../evidence/nightschool-nebius/README.md)). The nightly schedule has not run on live traffic | GPU | A Nebius Serverless Job on an L40S, with a bucket mounted at `/data` ([`deploy/train_job.sh`](../deploy/train_job.sh)) | Real (the hand-run Nebius jobs and the borrowed-GPU training; one Night School run, rejected by the gate); Built and unit-tested, not run nightly on live traffic (the schedule) |
| Student serving | Nemotron 3 Nano 4B with a LoRA adapter you own, served by vLLM | The live gateway is not connected to a student endpoint, so the teacher answers every request. On 27 to 28 Oct 2026 the student was served by hand, in two short sessions, on a Nebius Serverless Endpoint (one spot L40S) for speed tests: cold start about 7.2 to 7.6 minutes to the first answer; a restart (stop, then start) about 7.1 minutes, with the adapters loaded again each time; 16 requests at once p50 1.6 s (SQL adapter) and 1.2 s (tool-calling adapter); about $0.67 of GPU time in all; the endpoint was deleted afterwards (evidence: [`evidence/student-endpoint-nebius/`](../evidence/student-endpoint-nebius/README.md)) | GPU | A Nebius Serverless Endpoint on an L40S ([`deploy/student_endpoint.sh`](../deploy/student_endpoint.sh)) | Real (the one hand-run speed test, not connected to the live gateway); Scripted, ready to run (the endpoint script and the live gateway's connection to it) |
| Teacher | Nemotron 3 Ultra, an open model whose license allows training on its outputs | Nebius Token Factory, called per token from the live gateway | Hosted by Nebius; you do not size it | The same | Real |
| Scout and Tavily | The research agents on the same gateway; Tavily is the web-search API they call (not a Nebius product) | The live gateway (real Token Factory and Tavily calls, under a spend guard) and recorded runs | CPU | The same gateway on the VM | Real. The story site's `/scout` page uses the live gateway (`SCOUT_ORIGIN`, [DEPLOY_PAGES.md](DEPLOY_PAGES.md)) |
| Dashboard and web app | The React app the gateway serves at `/`, `/scout` and `/dashboard` | The live gateway, from its image | CPU | The VM, from the same image | Real |
| Docs site and story site | Static pages built from `docs-site/` and `web/` | Cloudflare Pages | none (static files) | Nothing planned: static files stay where they are | Real |
| Live gateway | `live.ratchet.dualmindlab.tech`: the gateway in live mode | Cloudflare Workers with Containers ([`deploy/cloudflare-live`](../deploy/cloudflare-live/README.md)) | CPU | The VM runs the same image | Real |
| Main site | `ratchet.dualmindlab.tech`: static pages on Cloudflare Pages whose worker sends every gateway-backed path (dashboard, judges, `/ratchet/*`, `/scout/*`) to the live gateway by default; `GATEWAY_ORIGIN` switches it ([`DEPLOY_PAGES.md`](DEPLOY_PAGES.md)) | Cloudflare Pages | none | the gateway serves the same pages | Real for gateway numbers |
| Tests and CI | `pytest`, lint and the reference runs: no network, no GPU | Your laptop and GitHub Actions | CPU | Not applicable | Real |

## The router is just CPU

The gateway does no model inference. It checks keys, routes a request, calls the student and teacher over HTTP, writes the ledger and serves the web pages, so a 2 vCPU, 8 GB machine is enough and costs about six cents an hour (estimate, below). That is why it already runs on Cloudflare's CPU-only containers and on a laptop. Only two things need a GPU: **training** (Night School, a job that runs for under an hour a night and then stops) and **serving the student** (an endpoint that bills for every hour it is on, so it should be stopped when idle). Without a student endpoint the whole system still works: the teacher answers every call, which is exactly how the live gateway runs today.

## One command to host it all on Nebius

> **Scripted and tested offline, ready to run, apart from the hand-run steps.** Only these Nebius steps have run for real, all started by hand: on 27 Oct 2026 the round-2 text-to-SQL retraining, as a Serverless Job on one preemptible L40S (evidence: [`evidence/text2sql-round2-nebius/`](../evidence/text2sql-round2-nebius/)), and a speed test of the student on a Serverless Endpoint (evidence: [`evidence/student-endpoint-nebius/`](../evidence/student-endpoint-nebius/)); and on 28 Oct 2026 one Night School on a CPU VM, a spot L40S job and an endpoint, which the golden gate rejected (evidence: [`evidence/nightschool-nebius/`](../evidence/nightschool-nebius/)). Everything else below, including the one-command VM kit, the nightly schedule on live traffic and the live gateway's connection to a student endpoint, has not run: the live site's own gateway is still on Cloudflare and is not connected to that endpoint. `deploy/nebius/nebius.sh` and the scripts it calls were written from the Nebius CLI reference and tested offline against a fake `nebius` command, a fake `ssh` and a stub smoke check. Nothing here is endorsed by or affiliated with Nebius. Do the [day-1 checks](DEPLOY_NEBIUS_VM.md#what-is-confirmed-and-what-is-not) on the first real run.

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
2. **The CPU VM** with the gateway and Caddy: [`deploy/vm/create_vm.sh`](../deploy/vm/create_vm.sh).
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

At low traffic a 24/7 student endpoint costs more than it saves, and Ratchet's cost report says so ([Cost accounting](ARCHITECTURE.md#7-cost-accounting)). Use `--with-student` when you want to measure the student, and stop it afterwards.

More detail: [DEPLOY_NEBIUS_VM.md](DEPLOY_NEBIUS_VM.md) (the VM, monitoring, backups, teardown), [DEPLOYMENT.md](DEPLOYMENT.md) (the endpoint and the job step by step) and the guide [Host it on Nebius](https://docs.ratchet.dualmindlab.tech/guides/host-on-nebius/).

## Run the tests on your own machine

No keys, no GPU, no bill:

```bash
pip install -e ".[dev]"
make test        # the offline test suite
```

Steps: [the README's quick start](../README.md#use-it).

## Run the live gateway on a VM

The live gateway (real Token Factory calls) runs on any machine that runs Docker. On a Nebius VM, the steps are in [DEPLOY_NEBIUS_VM.md](DEPLOY_NEBIUS_VM.md#switching-from-mock-to-live); `deploy/nebius/nebius.sh up --live` does the wiring for you. Anywhere else: `RATCHET_MODE=live` with `NEBIUS_API_KEY` and `RATCHET_API_KEYS` set ([LIVE_MODE.md](LIVE_MODE.md)). The live gateway uses Cloudflare Workers with Containers instead ([`deploy/cloudflare-live`](../deploy/cloudflare-live/README.md)), which is the host that has actually run.

## Unlock Scout runs as a judge

Hackathon judges can hold back runs and spend for themselves on the live Scout page. The access code is on the Devpost page, never in this repository. The picture below is an illustration of the page, not a recording.

![Illustration, not a screen recording, of the Scout page's judge box in four steps: click "I am a judge", type the code (shown only as dots), press Unlock, and see "Judge access on" with "Sign out".](img/anim/judge-box-howto.gif)

How the access works: [Scout](SCOUT.md) and [ADR 0120](adr/0120-judge-access-and-a-persistent-scout-guard.md).
