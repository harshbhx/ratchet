# Ratchet demo on Cloudflare Containers

The mock-mode demo (the repo's Dockerfile, no keys, nothing secret), run as a Cloudflare Container behind a Worker at
`demo.ratchet.dualmindlab.tech`. Every number it shows is simulation, labelled as such.

- **Manual deploys only.** The workflow `.github/workflows/deploy-cloudflare-demo.yml` runs only from the Actions tab
  (Run workflow, type `deploy`). Pushes and merges never deploy it, and nothing rebuilds it on a schedule.
- **On demand.** Nothing calls it in the background (no cron, no keep-alive). The first visitor starts it; the mock warm-up (four
  simulated days, as `make demo`) takes about a minute, so that first page load is slow. A visitor never sees a blank page: after 2 seconds without an answer the Worker shows a
  "Waking up the demo server" page with a seconds counter, which checks `/__ready` every 2 seconds and opens the demo by itself. It sleeps again 20 minutes after the last request, so no
  container time is billed while nobody is looking (the Workers Paid plan that Containers need is a monthly fee either way). Instance type `standard-2` (1 vCPU, 6 GiB), at most one instance.
- **State** is in memory and resets when the container restarts or is redeployed.

One-time setup, in Cloudflare and GitHub (never paste a token in chat):
1. Cloudflare dashboard, My Profile, API Tokens, create a token from the "Edit Cloudflare Workers" template and add the
   Containers permission if it asks. Copy your Account ID from the dashboard.
2. GitHub, repo Settings, Secrets and variables, Actions: add `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`.
3. Actions, `deploy-cloudflare-demo`, Run workflow, type `deploy`.

Status: deployed and running at `demo.ratchet.dualmindlab.tech` through the workflow above. `package.json` pins every package
to the exact version in `package-lock.json` (`@cloudflare/containers` 0.3.7, Wrangler 4.129.0, `@cloudflare/workers-types`
5.20261023.1, TypeScript 7.0.2), and the workflow installs them with `npm ci`. If a deploy fails, the Actions log names the step.
