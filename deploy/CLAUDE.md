# CLAUDE.md: `deploy/` (Nebius)

- `train_job.sh <version>`: submits a Nebius Serverless Job that runs `python -m ratchet.nightschool.train` with the bucket mounted at `/data`. If `RATCHET_BUCKET_S3` is set, it polls for `adapters/<version>/adapter_model.safetensors` (up to 2 h) so `ratchet.live` can wait on it.
- `student_endpoint.sh`: creates a Nebius Serverless Endpoint running `vllm/vllm-openai:v0.18.0-cu130` with `--enable-lora` and `VLLM_ALLOW_RUNTIME_LORA_UPDATING=True`, so new adapters are hot-loaded (`POST /v1/load_lora_adapter`) without a restart.
- `Dockerfile.train`: CUDA 12.8 + torch + Unsloth/TRL/PEFT + Mamba kernels.

- `vm/`: the public demo on one Nebius CPU VM ([docs/DEPLOY_NEBIUS_VM.md](../docs/DEPLOY_NEBIUS_VM.md)).
  - `create_vm.sh`: boot disk + VM with a static public IP + `cloud-init.yaml` as user data. `DRY_RUN=1` prints the commands.
  - `cloud-init.yaml`: Docker, `ufw` (22/80/443), unattended upgrades, a `ratchet` service user, clone to `/opt/ratchet`, `.env` from `.env.vm.example`, sslip.io domain default, `docker compose up -d --build`, and `/etc/cron.d/ratchet` (daily backup on; Night School lines commented out).
  - `docker-compose.yml`: the gateway (1 uvicorn worker, `--proxy-headers`, ledger in the `ratchet-data` volume, health check on `/healthz`, which stays public when `RATCHET_API_KEYS` protects `/v1/*`) and Caddy (`caddy:2.11.7-alpine`).
  - `Caddyfile`: HTTPS for `RATCHET_DOMAIN`, security headers, no buffering for `/scout/stream`, and the opt-in `RATCHET_API_GUARD=guarded` (403 on `/v1/*` and `/ratchet/demo/*` without `X-Ratchet-Admin-Token`).
  - `update.sh`: pull, rebuild, wait for health, then check `/`, `/ratchet/stats`, `/dashboard`, `/scout` over HTTPS. `backup.sh`: SQLite online backup, integrity check, keep 14, optional upload to Object Storage.
  - `.env.vm.example`: every VM setting with comments. Starts in mock mode with `RATCHET_MOCK_WARMUP_DAYS=4`.

Rules:

- **All `nebius` CLI flags here are unverified.** They follow the Nebius Serverless quickstart and tutorials as of Oct 2026. Check `nebius ai create --help` and `nebius ai endpoint create --help` on day 1 and fix them here, not in docs only.
- Stop the endpoint when idle; stopped endpoints cost nothing.
- Never put keys in these scripts; read them from env vars. On the VM, keys live only in `deploy/vm/.env` (mode 0600, git-ignored).
- In live mode on the VM, keep the Scout budget guard on and set `RATCHET_API_GUARD=guarded` with a 32+ character hex token; otherwise `/v1/chat/completions` is an open, paid endpoint.
- Port 8000 must stay unpublished (`expose`, not `ports`): `--forwarded-allow-ips "*"` is safe only because Caddy is the only thing that can reach the gateway.
- `nebius compute` flags in `vm/create_vm.sh` were checked against docs.nebius.com on Oct 20, 2026; the lines marked VERIFY ON DAY 1 were not.
- Pin image versions once the day-1 run passes, and record them in [docs/DEPLOYMENT.md](../docs/DEPLOYMENT.md).
