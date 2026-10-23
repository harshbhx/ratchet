# Ratchet demo on Cloudflare Containers

The mock-mode demo (the repo's Dockerfile, no keys, nothing secret), run as a Cloudflare Container behind a Worker at
`demo.ratchet.dualmindlab.tech`. No keys, nothing secret; every number it shows is simulation, labelled as such.

- **Manual deploys only.** The workflow `.github/workflows/deploy-cloudflare-demo.yml` runs only from the Actions tab
  (Run workflow, type `deploy`). Pushes and merges never deploy it, and nothing rebuilds it on a schedule.
- **On demand.** Nothing calls it in the background (no cron, no keep-alive). The first visitor starts it; the mock warm-up (two
  simulated days) takes about a minute, so that first page load is slow. A visitor never sees a blank page: after 2 seconds without an answer the Worker shows a
  "Waking up the demo server" page with a seconds counter, which checks `/__ready` every 2 seconds and opens the demo by itself. It sleeps again 20 minutes after the last request, so it costs
  nothing while nobody is looking. Instance type `standard-2` (1 vCPU, 6 GiB), at most one instance.
- **State** is in memory and resets when the container restarts or is redeployed.

One-time setup, in Cloudflare and GitHub (never paste a token in chat):
1. Cloudflare dashboard, My Profile, API Tokens, create a token from the "Edit Cloudflare Workers" template and add the
   Containers permission if it asks. Copy your Account ID from the dashboard.
2. GitHub, repo Settings, Secrets and variables, Actions: add `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`.
3. Actions, `deploy-cloudflare-demo`, Run workflow, type `deploy`.

Not verified: this was written without a Cloudflare token or Docker on hand, so the first run is the real test. If it fails,
the Actions log names the step. `@cloudflare/containers` is not pinned to a version; pin it once a deploy works.
