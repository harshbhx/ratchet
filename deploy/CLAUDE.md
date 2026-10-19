# CLAUDE.md: `deploy/` (Nebius)

- `train_job.sh <version>`: submits a Nebius Serverless Job that runs `python -m ratchet.nightschool.train` with the bucket mounted at `/data`. If `RATCHET_BUCKET_S3` is set, it polls for `adapters/<version>/adapter_model.safetensors` (up to 2 h) so `ratchet.live` can wait on it.
- `student_endpoint.sh`: creates a Nebius Serverless Endpoint running `vllm/vllm-openai:v0.18.0-cu130` with `--enable-lora` and `VLLM_ALLOW_RUNTIME_LORA_UPDATING=True`, so new adapters are hot-loaded (`POST /v1/load_lora_adapter`) without a restart.
- `Dockerfile.train`: CUDA 12.8 + torch + Unsloth/TRL/PEFT + Mamba kernels.

Rules:

- **All `nebius` CLI flags here are unverified.** They follow the Nebius Serverless quickstart and tutorials as of Oct 2026. Check `nebius ai create --help` and `nebius ai endpoint create --help` on day 1 and fix them here, not in docs only.
- Stop the endpoint when idle; stopped endpoints cost nothing.
- Never put keys in these scripts; read them from env vars.
- Pin image versions once the day-1 run passes, and record them in [docs/DEPLOYMENT.md](../docs/DEPLOYMENT.md).
