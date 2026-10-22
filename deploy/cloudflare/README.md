# Ratchet demo on Cloudflare Containers

The same mock-mode demo as [`deploy/shared-vm`](../shared-vm), run as a Cloudflare Container behind a Worker at
`demo-cf.ratchet.dualmindlab.tech`. No keys, nothing secret; every number it shows is simulation, labelled as such.

- **Manual deploys only.** The workflow `.github/workflows/deploy-cloudflare-demo.yml` runs only from the Actions tab
  (Run workflow, type `deploy`). Pushes and merges never deploy it, and nothing rebuilds it on a schedule.
- **Stays awake.** A cron trigger pings `/healthz` every 5 minutes, so the mock warm-up (a minute or two of CPU) is not
  paid by a visitor. Instance type `standard-2` (1 vCPU, 6 GiB), at most one instance.
- **State** is in memory and resets when the container restarts or is redeployed.

One-time setup, in Cloudflare and GitHub (never paste a token in chat):
1. Cloudflare dashboard, My Profile, API Tokens, create a token from the "Edit Cloudflare Workers" template and add the
   Containers permission if it asks. Copy your Account ID from the dashboard.
2. GitHub, repo Settings, Secrets and variables, Actions: add `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`.
3. Actions, `deploy-cloudflare-demo`, Run workflow, type `deploy`.

Not verified: this was written without a Cloudflare token or Docker on hand, so the first run is the real test. If it fails,
the Actions log names the step. `@cloudflare/containers` is not pinned to a version; pin it once a deploy works.
