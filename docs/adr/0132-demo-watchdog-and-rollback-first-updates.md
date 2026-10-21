# ADR 0132: A host watchdog, a live probe and updates that can be undone

- **Status:** Accepted
- **Date:** 2026-10-22

## Context

Stage One of the hackathon is pass or fail and needs a runtime Token Factory call from the hosted demo, which therefore runs
in live mode from submission (Oct 29) until judging ends (Dec 15). The gateway runs on one small VM with no GPU endpoint.
Three things can break it without anyone noticing:

1. Docker's restart policy acts when a container exits. A running but hung gateway stays down, and a failing health check
   only changes a status.
2. `/readyz` asks Token Factory for `GET /models`, which spends no tokens, so it can stay green while every completion fails.
   Alerts built on events need traffic, and judging traffic is sparse.
3. `update.sh` pulled, rebuilt and pruned with no backup and no way back, while schema changes are going live.

## Decision

- **`python3 -m ratchet.watch`**, one module that imports only the standard library, runs on the host from
  `/etc/cron.d/ratchet-watch` (a dead gateway cannot restart or alert itself, and the host has Python but no project
  packages). The `inside` subcommands import the gateway's modules lazily and run in the container.
- **Restart rule.** `tick` restarts the container after N failed liveness checks or N failed `checks.ledger` readings in a
  row, with a start grace, a cooldown, an hourly cap, a pause and the update lock. It never reads the teacher or student
  checks (a restart would not fix Token Factory and drops the breakers' state), never starts a stopped container, and runs
  `docker compose restart`, so a changed `.env` is not applied by it.
- **Probe.** One 16-token completion an hour through the public URL with a dedicated `watchdog` key on the operator-pinned
  task `watchdog-probe` (`deploy/vm/config/tasks.json`: pinned, `store: false`, `clients: ["watchdog"]`). Before any paid call
  it asks the container whether the task is registered, pinned and the key's alone, and fails closed otherwise: with the
  default `RATCHET_UNKNOWN_TASKS=distill`, an unregistered task name would store the answer, which INV-10 forbids.
- **Classification by code.** The gateway maps the teacher's 401, 402 and 403 to 502 `upstream_auth_error` or
  `teacher_unavailable` (the message names `upstream 402`), so the probe classifies by the error `code` and that status,
  never by HTTP status alone, and tells a gateway 401, a Caddy 403 and the watchdog key's own caps apart from a teacher
  failure. **Which status Token Factory returns for exhausted credit is unverified**: alerts name the code and the upstream
  status and say "may be", and the runbook sends the operator to the console.
- **Alerts** are a signed JSON webhook built in the watchdog (the gateway's notifier is not built): `{kind, detail, ts, day,
  gateway, text}`, compact sorted JSON, `X-Ratchet-Signature: sha256=<HMAC-SHA256>`. One alert per failing check, on a code
  change, every 6 h while failing and on recovery. `probe` and `watchdog_restart` are the kinds it sends. A later gateway
  notifier should import `alert_body`, `signature` and `DEFAULT_ALERT_KINDS` from this module so both processes send one format.
- **Banner flag.** The probe writes `meta['watch.live']` through `inside flag`; `/scout/status` serves `live_check`
  (`state`, `since`, `checked`, `search`) from a 30 s cache, `stale` after `RATCHET_WATCH_FLAG_MAX_AGE_S`. It never exposes a
  code or text. The page banner and its `web/` change are left to the UI lane.
- **Updates.** `update.sh` takes a lock, snapshots the git SHA, backs up (`pre-update`), tags the running image
  `ratchet-gateway:previous`, builds, runs the config check (exit 2 puts the old image and SHA back), starts, and verifies:
  healthy, `/readyz`'s ledger, the mode, and the probe's side. It rolls back (checkout and image, then recreate) on a failure
  that is the new code's, never for the teacher, and restores the ledger (`restore.sh`, which moves the old file and its
  WAL aside together) only when the old image cannot open it. Migrations only add columns and tables, so the previous image
  reads a migrated ledger.
- **`scripts/demo_check.py`** checks the pages, `/healthz` mode, `/readyz`, `/scout/status`, an optional key and an optional
  live completion from outside. It never runs Scout (a run spends budget through `Guard.admit`).

## Consequences

- No GPU, student endpoint or window check is involved; the item is lean for a 1 GB VM.
- The probe's 24 teacher-only requests a day appear in the traffic views as the `watchdog-probe` task and the `watchdog`
  client. They are not filtered out of the daily series; their savings are zero (pinned rows cost their baseline).
- Left out: the Tavily direct search and usage check (Scout's free `/scout/status` search state is read instead), the
  daily model-id list check, the simulation host, the Scout run in the external check, the page banner and a ledger event
  for restarts.
- Unverified until run on the VM: Token Factory's exhausted-credit status, whether the VM reaches its own public IP
  (`RATCHET_WATCH_CONNECT=local` is the fallback), Slack's and Discord's handling of the body's extra keys, and the Docker
  Compose behaviour of the fake-tested rollback.
