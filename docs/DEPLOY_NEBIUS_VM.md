# Hosting the public demo on a Nebius CPU VM

This guide puts the Ratchet demo on one small Nebius AI Cloud virtual machine, so judges can use it from **Oct 29 to Dec 15, 2026**. The VM runs two containers: the Ratchet gateway and [Caddy](https://caddyserver.com/), which gets an HTTPS certificate automatically.

Files: [`deploy/vm/`](../deploy/vm/). Facts about Nebius below were checked on docs.nebius.com and nebius.com on **Oct 20, 2026**. Anything marked **verify on day 1** has not been run against a real account yet.

## Why a CPU VM

The gateway does no model inference. It routes requests, keeps the SQLite ledger and serves the web pages. The models run elsewhere: the teacher on Token Factory, the student on a Serverless Endpoint (or the hosted fallback). So a 2 vCPU / 8 GB VM is enough, and it costs about a tenth of the cheapest GPU.

```
judge's browser ──HTTPS──▶ Caddy :443 ──▶ ratchet:8000 (gateway, 1 worker, SQLite in a volume)
                                              │ live mode only
                                              ├──▶ Token Factory (teacher, judge, fallback student)
                                              ├──▶ Serverless Endpoint (student, optional)
                                              └──▶ Tavily (Scout search)
```

## What you need

1. **The Nebius CLI**, installed and logged in ([CLI quickstart](https://docs.nebius.com/cli/quickstart)):

   ```bash
   curl -sSL https://storage.eu-north1.nebius.cloud/cli/install.sh | bash
   exec -l $SHELL && nebius version
   export NEBIUS_PROJECT_ID=<project ID from the console>
   nebius profile create --parent-id $NEBIUS_PROJECT_ID     # browser login
   ```

2. **jq** on your laptop (the script reads the public IP with it).
3. **An SSH key**, for example `~/.ssh/id_ed25519.pub` (`ssh-keygen -t ed25519`).
4. **The repo public** at `https://github.com/harshbhx/ratchet`. The VM clones it on first boot.
   - Private repo instead: create a fine-grained GitHub token with read-only access to this one repo, and set
     `RATCHET_REPO_URL=https://x-access-token:<token>@github.com/harshbhx/ratchet.git`.
     The VM removes the token from its git remote after cloning, but the token stays in the VM's cloud-init user data. Revoke it after the first boot. Later `git pull` (and `update.sh`) then needs the repo public, or a credential you add by hand.
5. **A region.** The default platform `cpu-e2` (Intel Ice Lake) exists only in `eu-north1`. In other regions use `RATCHET_VM_PLATFORM=cpu-d3` (AMD EPYC Genoa). Both have the `2vcpu-8gb` preset ([VM types](https://docs.nebius.com/compute/virtual-machines/types)).

## Step by step

### 1. Create the VM

```bash
export NEBIUS_PROJECT_ID=<project ID>
export SSH_PUBLIC_KEY_PATH=~/.ssh/id_ed25519.pub
DRY_RUN=1 bash deploy/vm/create_vm.sh    # prints the commands, creates nothing
bash deploy/vm/create_vm.sh
```

The script:

1. Finds the first subnet in the project (`nebius vpc subnet list`), unless you set `NEBIUS_SUBNET_ID`.
2. Creates a 30 GiB `network_ssd` boot disk from the image family `ubuntu24.04-driverless` (`nebius compute disk create`).
3. Creates the VM `ratchet-demo` (`nebius compute instance create`) with that disk, a **static** public IP (kept while the VM is stopped), and [`cloud-init.yaml`](../deploy/vm/cloud-init.yaml) as user data.
4. Prints the public IP, the VM and disk IDs, and the next steps.

On first boot, cloud-init installs Docker Engine and the Compose plugin from Docker's apt repository, turns on unattended security upgrades and the `ufw` firewall (ports 22, 80, 443), creates a `ratchet` service user, clones the repo to `/opt/ratchet`, writes `deploy/vm/.env` from [`.env.vm.example`](../deploy/vm/.env.vm.example), and runs `docker compose up -d --build`. This takes 5–10 minutes.

```bash
ssh ubuntu@<ip> 'sudo tail -f /var/log/ratchet-bootstrap.log'
```

Optional settings: `RATCHET_VM_NAME`, `RATCHET_VM_PRESET` (for example `4vcpu-16gb`), `RATCHET_VM_DISK_GB`, `RATCHET_SSH_USER` (default `ubuntu`; Nebius reserves `root` and `admin`), `RATCHET_DOMAIN`.

### 2. Pick the web address

- **Fastest: sslip.io.** If you do not set `RATCHET_DOMAIN`, first boot sets it to `<public-ip>.sslip.io`, for example `203.0.113.7.sslip.io`. That name resolves to the IP, so Caddy gets a real certificate without any DNS work. The VM finds its public IP by asking `api.ipify.org` (verify on day 1).
- **Your own name:** add a DNS **A record**, for example `ratchet.dualmindlab.tech → <public IP>`, wait until `dig +short ratchet.dualmindlab.tech` returns the IP, then on the VM:

  ```bash
  cd /opt/ratchet/deploy/vm
  sudo -u ratchet sed -i 's|^RATCHET_DOMAIN=.*|RATCHET_DOMAIN=ratchet.dualmindlab.tech|' .env
  sudo -u ratchet docker compose up -d
  ```

  Set the DNS record **before** switching. Caddy retries failed certificate requests, but repeated failures can hit Let's Encrypt rate limits.

### 3. Edit `.env`

```bash
sudo -u ratchet nano /opt/ratchet/deploy/vm/.env     # mode 0600, owned by ratchet
cd /opt/ratchet/deploy/vm && sudo -u ratchet docker compose up -d   # apply changes
```

The file starts in `RATCHET_MODE=mock` with `RATCHET_MOCK_WARMUP_DAYS=4`, so the dashboard opens on a student that has already been through a few simulated days. Every variable is explained in the file.

### 4. Check it works

| URL | Expect |
|---|---|
| `https://<domain>/` | Landing page |
| `https://<domain>/scout` | Scout UI. In mock mode it shows "Simulated search results" and only the example questions work |
| `https://<domain>/dashboard` | Ratchet dashboard (simulation) |
| `https://<domain>/ratchet/stats` | JSON stats |
| `https://<domain>/healthz` | `{"status":"ok",...}` (always public) |
| `https://<domain>/readyz` | 200 with `"status": "ready"` (or `degraded`, with the reason under `checks`); 503 `not_ready` when the ledger cannot be written or no teacher can answer (always public; see [API.md](API.md#get-readyz)) |
| `https://<domain>/v1/models` | `{"object":"list","data":[{"id":"ratchet",...}]}` (403 if the Caddy guard is on, 401 if `RATCHET_API_KEYS` is set and you send no key; see Security) |

```bash
curl -sN "https://<domain>/scout/stream?q=What%20is%20LoRA%20fine-tuning%3F"   # events should arrive one by one
```

## Switching from mock to live

Live mode spends real money on every Scout run and every `/v1/chat/completions` call. Do it only after the checks in [DAY1.md](DAY1.md) pass.

> **For judging, live mode is required.** The hackathon's pass/fail Stage One needs a runtime call to Token Factory, and the Best Use of Tavily prize needs a runtime call to Tavily. Mock mode makes neither. Switch the VM to live mode before submitting (Oct 29) and keep it running, with the budget guard on, until judging ends on Dec 15, 2026. Make sure the Token Factory and Tavily credits last that long. See [HACKATHON_SUBMISSION.md](HACKATHON_SUBMISSION.md#stage-one-passfail-readiness).

1. In `.env`, set:
   - `RATCHET_MODE=live`
   - `NEBIUS_API_KEY` (Token Factory) and `TAVILY_API_KEY`
   - `RATCHET_STUDENT_BASE` and `RATCHET_STUDENT_KEY` only while the vLLM endpoint is running. Leave both empty to use the hosted, untuned fallback student, which costs per token instead of per GPU-hour. Say so in the demo. With the endpoint off, the pages say the hosted Nano answers, at which threshold, and that no certificate covers it: student answers carry the "Nano 30B · hosted" badge, the landing strip and Scout say "Answered by the student", the dashboard header reads "Nano 30B · hosted on Token Factory (endpoint off)", and the live version's golden precision is hidden (`student_serving` in `/ratchet/stats`, FR-7.40). `curl -s https://<domain>/ratchet/stats | jq .student_serving` shows what they read.
   - **Protect `/v1/*`.** Either `RATCHET_API_KEYS=me:$(openssl rand -hex 24)` (gateway API keys; add `RATCHET_API_KEY_DAILY_REQUESTS=200` for a daily cap), or **`RATCHET_API_GUARD=guarded`** with `RATCHET_ADMIN_TOKEN=$(openssl rand -hex 32)` (Caddy edge guard), or both. Without one of them, anyone can send unlimited paid requests to `/v1/chat/completions`. The gateway refuses to start in live mode without `RATCHET_API_KEYS`; if Caddy's guard is your only protection, also set `RATCHET_ALLOW_UNAUTHENTICATED=1` to say so ([ADR 0061](adr/0061-endpoint-access-levels.md)).
   - **Decide who sees the dashboard.** In live mode the dashboard API (`/ratchet/stats`, `/ratchet/cost`, the task list, the BOMs) needs an admin or dashboard key unless `RATCHET_PUBLIC_DASHBOARD=1`. `.env.vm.example` sets `1`, so judges see the live dashboard; anonymous readers then get client pseudonyms (`client-1`, ...) and events without URLs or error messages, while a request with your key gets everything. For a private dashboard set `0` and give yourself `RATCHET_DASHBOARD_KEYS=me:$(openssl rand -hex 24)`: read `/ratchet/stats` with it (`curl -H "Authorization: Bearer <key>" ...`); the page itself sends no key yet and shows no data. `RATCHET_PUBLIC_BOM=1` keeps the BOMs public either way. `/docs` and `/openapi.json` are off in live mode (`RATCHET_OPENAPI=1` turns them on).
   - Optional, for signed adapter BOMs: run `python -m ratchet.bom keygen` on your laptop, put the `RATCHET_BOM_SIGNING_KEY=` line in `.env`, and publish the public key it prints (README or Devpost). This also works in mock mode, so the public demo can show signed BOMs.
2. Set the **budget guard** to what you can afford:
   - `RATCHET_SCOUT_DAILY_BUDGET_USD` caps Scout's Ratchet spend per UTC day (default 3.00). Over 48 days the worst case is the cap × 48, so 3.00 can reach **$144**. The Builder Program credit is $25 ([DEPLOYMENT.md](DEPLOYMENT.md#step-1-token-factory-key-and-checks)), so **0.50/day ($24 worst case)** fits inside it.
   - `RATCHET_SCOUT_RUNS_PER_HOUR` (default 6) limits each client IP.
   - `RATCHET_SCOUT_MAX_RUNS_PER_DAY` (default 30) caps runs per UTC day for everyone, and with it Tavily credits (about 3 per run).
   - `RATCHET_AUDIT_RATE` (default 0.05): each audit is an extra Ultra call. Keep it at 0.05 or lower.
   - The counters are kept in the ledger and restored at startup, so `update.sh`, a crash or a recreate no longer resets the daily cap ([ADR 0120](adr/0120-judge-access-and-a-persistent-scout-guard.md)). The first restart after upgrading misses the runs from earlier that day, once.
3. Hold part of the day back for **judges**, and set the **Tavily credit budget** (the commented lines in `.env.vm.example`; details in [CONFIGURATION.md](CONFIGURATION.md#judge-access-and-tavily-credits)). The Devpost gallery is public, so without a reserve any visitor can use up the day's runs before a judge arrives. An example that keeps runs and credits consistent through judging:

   ```bash
   RATCHET_SCOUT_JUDGE_CODE=<openssl rand -hex 16>   # goes in the submission's testing instructions only
   RATCHET_SCOUT_MAX_RUNS_PER_DAY=22
   RATCHET_SCOUT_JUDGE_RUNS_PER_DAY=7                 # the public gets 15 runs a day
   RATCHET_TAVILY_MONTHLY_CREDITS=1000                # the free plan; more if the Builders Program adds credits (unverified)
   RATCHET_TAVILY_PACE_UNTIL=2026-12-15               # spread what is left until the last day of judging
   ```

   - Credit math: Tavily's free plan gives **1,000 credits a month**, and a basic search costs 1 credit ([Tavily credits](https://docs.tavily.com/documentation/api-credits), checked Oct 20, 2026). The Nebius Builders Program may offer more (unverified; set the real number). Scout makes up to 3 searches a run.
   - The judges' credits default to `floor(1000 × 7 / 22) = 318`. In a calendar-month cycle that leaves `floor(682 / 15) = 45` public credits a day on Dec 1, which is 15 public runs of 3 searches; leftovers roll forward. The judges' budget defaults to their share of `RATCHET_SCOUT_DAILY_BUDGET_USD` (7/22 of $0.50 is $0.15).
   - Tavily does not document its monthly reset day (unverified): set `RATCHET_TAVILY_CYCLE_DAY` to the day the Tavily dashboard shows.
   - When the public's credits for the day are used up, public runs still run, without web search and labelled "search unavailable": the planner has already called Token Factory, and the writer answers without sources. Judge runs keep searching from their reserve.
4. Check, then apply: `sudo -u ratchet docker compose run --rm --no-deps -T ratchet python -m ratchet.config check` lists every problem with `.env` at once (exit 2: the gateway would not start; it also warns that `RATCHET_MOCK_WARMUP_DAYS` does nothing in live mode, so remove that line), then `sudo -u ratchet docker compose up -d` (or `update.sh`, which runs the same check first) and `curl -s https://<domain>/readyz` should say `ready`. The ledger now lives in the `ratchet-data` volume at `/data/ratchet.db` and survives restarts and rebuilds.
5. Watch the Token Factory usage page and the Tavily dashboard daily for the first week, and check `curl -s https://<domain>/scout/status | jq .search` shows `"available": true`.

### Night School cron (off by default)

`/etc/cron.d/ratchet` contains two commented lines: `python -m ratchet.live night` at 02:30 UTC and `... tick` every 5 minutes, both through `docker compose exec -T ratchet`. Leave them off for the judging period unless all of these hold:

- live mode works and the student endpoint and training job have been run by hand ([DEPLOYMENT.md](DEPLOYMENT.md) steps 1–5);
- the gateway image contains the `nebius` and `aws` CLIs, their credentials and the `deploy/` scripts. The stock [`Dockerfile`](../Dockerfile) does not, so `night` cannot submit a training job from inside the container yet.

To turn them on, remove the leading `# ` from those two lines with `sudo nano /etc/cron.d/ratchet`. Logs go to `/var/log/ratchet/`.

## Keeping it alive until Dec 15

### Cost estimate

Prices from [Compute pricing](https://docs.nebius.com/compute/resources/pricing) and [nebius.com/prices](https://nebius.com/prices), as of **Oct 20, 2026**, USD, before tax. The period Oct 29 – Dec 15 is 48 days (1,152 hours). Nebius prices disks per GiB per 730 hours, which is one month.

| Item | Rate | Per month (730 h) | Oct 29 – Dec 15 |
|---|---|---|---|
| VM `cpu-e2` `2vcpu-8gb` (eu-north1) | $0.012/vCPU-h + $0.0045/GiB-h = **$0.060/h** | $43.80 | **$69.12** |
| or VM `cpu-d3` `2vcpu-8gb` (other regions) | $0.015/vCPU-h + $0.0045/GiB-h = $0.066/h | $48.18 | $76.03 |
| Boot disk, 30 GiB network SSD | $0.071 per GiB per 730 h | $2.13 | $3.36 |
| Public IP | Free | $0 | $0 |
| Network egress from the VM | Free | $0 | $0 |
| Backups in Object Storage (Standard) | $0.0147 per GiB-month | < $0.01 | < $0.01 |
| **Infrastructure total** | | | **≈ $72.50** (`cpu-e2`) or ≈ $79.40 (`cpu-d3`) |

Notes:

- The CPU rates above took effect on **Oct 1, 2026**. Before that they were $0.012/vCPU-h and $0.0032/GiB-h. Check the pricing page again before you rely on these numbers.
- The VM bills while it runs. A stopped VM keeps billing for its disk; the static IP is kept while stopped.

Usage costs in live mode (on top of the infrastructure):

| Item | How it is bounded | Expected | Worst case |
|---|---|---|---|
| Token Factory (Scout) | `RATCHET_SCOUT_DAILY_BUDGET_USD` per UTC day | A Scout run makes 1 planner + up to 12 triage + up to 6 reader + 1 writer call. With list prices in [CONFIGURATION.md](CONFIGURATION.md#prices-default_prices) that is roughly $0.02–0.03 per run before the student is trained, and under $0.01 after. 10–30 runs a day is about **$0.10–0.90/day** | cap × 48 days: $24 at 0.50, $144 at 3.00 |
| Token Factory (`/v1/chat/completions`) | **Not covered by the Scout budget.** Gateway API keys with `RATCHET_API_KEY_DAILY_REQUESTS`, or the edge guard | $0 with `RATCHET_API_GUARD=guarded` or keys you do not share | Keys: the daily quota × your keys. Unbounded with both off |
| Tavily search | 3 basic searches per run, 1 credit each ([Tavily credits](https://docs.tavily.com/documentation/api-credits)); `RATCHET_TAVILY_MONTHLY_CREDITS` with daily pacing | 1,000 free credits per month ≈ 330 runs a month | `RATCHET_TAVILY_MONTHLY_CREDITS` a cycle when set; otherwise `RATCHET_SCOUT_MAX_RUNS_PER_DAY` (30) × 3 = 90 credits a day; beyond the free credits, $0.008 per credit on pay-as-you-go |
| Student endpoint (L40S) | Only while running | $0 if you use the hosted fallback | ~$37 per full day if left on ([DEPLOYMENT.md](DEPLOYMENT.md#4-cost-estimates)) |

To keep Tavily at $0, stay on the free plan without pay-as-you-go: once the 1,000 monthly credits are used, Tavily answers 432 and charges nothing. Scout then pauses searching for an hour (a `tavily_exhausted` event) and every run still finishes, labelled "search unavailable", with the planner's and the writer's Token Factory calls. With `RATCHET_TAVILY_MONTHLY_CREDITS` set, the meter usually stops before Tavily has to.

### Monitoring

```bash
cd /opt/ratchet/deploy/vm
sudo -u ratchet docker compose ps                    # both "running", ratchet "healthy"
sudo -u ratchet docker compose logs -f --tail=100 ratchet
sudo -u ratchet docker compose logs -f --tail=100 caddy   # certificate problems show here
curl -s https://<domain>/scout/status                 # live mode: spend today vs budget, judges' reserve, search and credits
```

- `/scout/status` keeps today's runs and spend across `update.sh`: the counters are restored from the ledger. `search.available` false, a `search_error` event or a `tavily_exhausted` event: see [RUNBOOK.md](RUNBOOK.md#tavily-credits-running-low-or-search-unavailable).
- One gateway process per ledger: the compose file pins `--workers 1`. Detection of a second process (`gateway_lease`, NFR-60) waits for the leases table; until then see [RUNBOOK.md](RUNBOOK.md#two-gateway-processes-on-one-ledger).

- The ratchet container has a Docker health check on `/healthz` every 30 seconds (public, so it keeps working when `RATCHET_API_KEYS` protects `/v1/*`). Both containers restart automatically (`restart: unless-stopped`), also after a VM reboot.
- Container logs rotate at 10 MB × 5 files each.
- For an outside check, point a free uptime monitor at `https://<domain>/readyz` (always public; 200 while requests can be answered, 503 when they cannot) and, for "is the process up", `https://<domain>/healthz`. A load balancer in front of the VM should use `/readyz` too; keep restarts on `/healthz`, so a teacher outage never becomes a restart loop. `/ratchet/stats` needs a key when the dashboard is private.

### Updating

```bash
sudo -u ratchet /opt/ratchet/deploy/vm/update.sh
```

It runs `git pull --ff-only` and builds the new image, then runs `python -m ratchet.config check` in it with your `.env` ([CONFIGURATION.md](CONFIGURATION.md#configuration-checks)): on errors (exit 2) it stops and leaves the running gateway as it is, so a typo in `.env` never takes the demo down; warnings are printed and the update goes on. Then `docker compose up -d`, waits for the Docker health check (`/healthz`) and for `/readyz` to answer 200 inside the container (printing its checks), fetches `/`, `/healthz`, `/readyz`, `/dashboard` and `/scout` over HTTPS (and `/ratchet/stats` when the dashboard is public) and exits non-zero if any of them fail. Run the check yourself after editing `.env`: `sudo -u ratchet docker compose run --rm --no-deps -T ratchet python -m ratchet.config check`.

### Backups

`/etc/cron.d/ratchet` runs [`backup.sh`](../deploy/vm/backup.sh) every day at 03:10 UTC. In live mode it makes a consistent copy of the ledger with SQLite's online backup API inside the container, checks it with `PRAGMA integrity_check`, and saves `/var/backups/ratchet/ratchet-<UTC time>.db.gz`, keeping the newest 14. In mock mode the ledger is in memory and the script does nothing.

To also copy each backup to Nebius Object Storage:

1. Create a bucket and an access key for it in the console.
2. On the VM: `sudo snap install aws-cli --classic`, then `sudo -u ratchet -H aws configure` with that key.
3. Set `RATCHET_BUCKET_S3=s3://<bucket>/ratchet` and `RATCHET_S3_ENDPOINT` in `.env`. Copy the endpoint URL from the console (verify on day 1).

Restore: stop the gateway, remove the old WAL files, copy the file into the volume, start it again. The ledger runs in SQLite's WAL journal (`RATCHET_DB_SYNC=NORMAL`, the default; [ADR 0060](adr/0060-hot-path-caches-and-throttled-tick.md)), so recent commits sit in `ratchet.db-wal` until a checkpoint: a stale `-wal` or `-shm` file left next to a restored copy would be replayed onto it. The backup script uses SQLite's backup API, which includes them; never copy the live file with `cp`.

```bash
cd /opt/ratchet/deploy/vm
gunzip -k /var/backups/ratchet/ratchet-<time>.db.gz
sudo -u ratchet docker compose stop ratchet
sudo -u ratchet docker compose run --rm --no-deps --entrypoint rm ratchet -f /data/ratchet.db-wal /data/ratchet.db-shm
sudo -u ratchet docker compose cp /var/backups/ratchet/ratchet-<time>.db ratchet:/data/ratchet.db
sudo -u ratchet docker compose start ratchet
```

## Security notes

- **No secrets in git.** Keys live only in `deploy/vm/.env` on the VM (mode 0600). The repo's `.gitignore` already ignores `.env` files at any depth. Never paste keys into `cloud-init.yaml` or `create_vm.sh`.
- **Firewall.** `ufw` allows only 22, 80 and 443. The default Nebius security group allows all traffic ([security groups](https://docs.nebius.com/vpc/security-groups/overview)), so `ufw` is the filter that matters. Docker publishes Caddy's ports 80 and 443 through its own rules, which bypass `ufw`; the gateway's port 8000 is not published, so only Caddy can reach it.
- **SSH.** Key login only, user `ubuntu` (or `RATCHET_SSH_USER`). The `ratchet` user cannot log in.
- **Gateway API keys (v0.4).** `RATCHET_API_KEYS=name:key,...` makes `/v1/*` answer 401 without `Authorization: Bearer <key>`; `RATCHET_API_KEY_DAILY_REQUESTS` caps each key per UTC day. The dashboard's **Trust and privacy** panel shows per-client usage (pseudonyms for anonymous readers of a public live dashboard). Pages, Scout and `/healthz` are always public; the dashboard API is public in mock mode and, in live mode, only with `RATCHET_PUBLIC_DASHBOARD=1`; the mock demo buttons keep working. Every response carries `nosniff` and a referrer policy (Caddy's own `Referrer-Policy` replaces the gateway's on the VM); `RATCHET_CSP` adds a Content-Security-Policy. Details in [API.md](API.md#access-levels).
- **Caddy edge guard.** Independent of the app keys, and also covers the mock demo controls:
  - `RATCHET_API_GUARD=guarded` blocks `/v1/*`, `/ratchet/demo/*` and `/ratchet/admin/*` with HTTP 403 unless the request carries `X-Ratchet-Admin-Token: <RATCHET_ADMIN_TOKEN>`. On the open mock demo, visitors can use the admin API (as `mock-admin`) like the demo buttons: a Reset undoes their switches.
  - These stay public at the edge: `/`, `/scout*` (including `/scout/stream`, `/scout/run`, `/scout/status`), `/dashboard`, `/ratchet/stats`, `/ratchet/bom`. Whether the gateway answers the last two without a key is `RATCHET_PUBLIC_DASHBOARD`.
  - If the token is empty or shorter than 32 characters, the guarded paths are blocked for everyone. Caddy strips the token header before it reaches the gateway.
  - In mock mode the guard also disables the dashboard's **Run day**, **Break next deploy** and **Reset** buttons, which call `/ratchet/demo/*`. Use `open` for the mock demo and `guarded` for live mode.
  - Using the API yourself while guarded: `curl -H "X-Ratchet-Admin-Token: $TOKEN" https://<domain>/v1/models`, or pass `default_headers={"X-Ratchet-Admin-Token": TOKEN}` to the OpenAI SDK.
- **The mock demo is shared.** All visitors see the same simulated state, and one visitor's Reset affects everyone. That is acceptable for a demo, and it costs nothing.
- **Budget guard limits.** It caps Scout's daily spend, runs per day and runs per IP. It does not cover `/v1/chat/completions` (hence API keys or the edge guard), and it does not stop someone with many IP addresses from using up the public share early; the judges' reserve is held back from them. The budget is checked when a run starts, so the worst case is the daily cap plus the runs already in progress when it is reached (a few cents each).
- **Judge code.** Never put `RATCHET_SCOUT_JUDGE_CODE` on the public project page, in the video or in a URL. Judges type it into the Scout page, which sends it once in a POST body; the session then lives in an `HttpOnly`, `SameSite=Strict`, `Secure` cookie. Rotating the code ends every session ([THREAT_MODEL.md](THREAT_MODEL.md) T24).
- **Access logs.** Caddy logs request headers and redacts only Cookie, Set-Cookie, Authorization and Proxy-Authorization, so the Caddyfile's log filter deletes `X-Ratchet-Admin-Token` (guarded mode). Check it once on the VM with `docker compose exec caddy caddy validate --config /etc/caddy/Caddyfile`. Scout questions stay in both access logs (`/scout/stream?q=`): they are public demo questions.
- **Real client IPs.** Caddy replaces any client-sent `X-Forwarded-For` with the real address, and uvicorn runs with `--proxy-headers`, so the per-IP limit sees real IPs.
- **After judging:** rotate `NEBIUS_API_KEY`, `TAVILY_API_KEY`, `RATCHET_STUDENT_KEY`, `RATCHET_ADMIN_TOKEN`, `RATCHET_API_KEYS`, `RATCHET_ADMIN_KEYS` and `RATCHET_DASHBOARD_KEYS`, and revoke any GitHub token used for cloning. Keep `RATCHET_BOM_SIGNING_KEY` (or its public key) if you want old BOMs to stay verifiable.

## Teardown

On Dec 16, or whenever judging ends. The boot disk was created on its own, so deleting the VM does **not** delete it; delete both.

```bash
# Optional: keep a last copy of the ledger
scp ubuntu@<ip>:/var/backups/ratchet/*.db.gz ./ratchet-backups/

VM_ID=$(nebius compute instance get-by-name --parent-id $NEBIUS_PROJECT_ID --name ratchet-demo --format json | jq -r .metadata.id)
nebius compute instance delete $VM_ID
nebius compute disk list --parent-id $NEBIUS_PROJECT_ID     # find ratchet-demo-boot (verify on day 1: flags)
nebius compute disk delete <disk ID>
```

Then remove the DNS record, stop the student endpoint if it is still running, and rotate the keys listed above.

## What is confirmed and what is not

Confirmed in the Nebius docs on Oct 20, 2026:

- `nebius compute disk create` with `--parent-id`, `--name`, `--type network_ssd`, `--size-gibibytes`, `--block-size-bytes`, `--source-image-family-image-family` ([CLI reference](https://docs.nebius.com/cli/reference/compute/disk/create)).
- `nebius compute instance create` with `--parent-id`, `--name`, `--resources-platform`, `--resources-preset`, `--boot-disk-existing-disk-id`, `--boot-disk-attach-mode`, `--cloud-init-user-data`, `--network-interfaces`, `--format` ([CLI reference](https://docs.nebius.com/cli/reference/compute/instance/create)).
- `"public_ip_address": {"static": true}` for a static address; `{}` for a dynamic one ([Managing VMs](https://docs.nebius.com/compute/virtual-machines/manage)).
- Reading the public IP from `.status.network_interfaces[0].public_ip_address.address` with `nebius compute instance get --id` or `get-by-name` ([Compute quickstart](https://docs.nebius.com/compute/quickstart), [Connecting to VMs](https://docs.nebius.com/compute/virtual-machines/connect)).
- `nebius compute instance delete <id>` (also deletes managed disks, not existing ones) and `nebius compute disk delete <id>` ([instance commands](https://docs.nebius.com/cli/reference/compute/instance), [Managing volumes](https://docs.nebius.com/compute/storage/manage)).
- Platforms `cpu-e2` (eu-north1 only) and `cpu-d3`, preset `2vcpu-8gb` ([VM types](https://docs.nebius.com/compute/virtual-machines/types)); image family `ubuntu24.04-driverless` is the recommended image for both ([boot disk images](https://docs.nebius.com/compute/storage/boot-disk-images)).
- The CLI installer URL and `nebius profile create --parent-id` ([CLI quickstart](https://docs.nebius.com/cli/quickstart)).

Verify on day 1:

- Tavily's `usage.credits` in a search response (`include_usage`), its 432 and 433 answers and its monthly reset day, against the Tavily dashboard (FR-10.22).
- The judge cookie through Caddy over HTTPS: `POST /scout/judge` from the Scout page, then `/scout/status` shows `"session": true` (FR-10.21).
- `caddy validate` with the log filter for `X-Ratchet-Admin-Token`.

- `nebius vpc subnet list --parent-id ...` (the quickstart runs it without `--parent-id`).
- `nebius compute disk list --parent-id ...` in the teardown.
- Public IP detection from inside the VM (`api.ipify.org`), used only for the sslip.io default.
- Let's Encrypt issuing for `*.sslip.io` names on the first try.
- The Object Storage S3 endpoint URL.
