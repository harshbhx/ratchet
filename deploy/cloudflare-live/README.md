# Ratchet LIVE demo on Cloudflare Containers

The same gateway image as the simulation demo (`../cloudflare`), but in **live mode**: every answer is a real call to
Nemotron 3 Ultra on Nebius Token Factory, behind a Worker at `live.ratchet.dualmindlab.tech`. The simulation demo stays at
`demo.ratchet.dualmindlab.tech`, labelled as simulation.

What it is, honestly: the public demo has no student endpoint yet (serving the student needs a GPU endpoint), so the
teacher answers every request, each one logged with its provenance, cost and request id. The student's real results are on the story
page and the judges' page; the cascade's behaviour with a student is the simulation demo.

## Keys: Cloudflare Worker secrets, never in this repo, the image or chat

Run these once in your own terminal (each prompts for the value; paste it there):

```
cd deploy/cloudflare-live
npm install
npx wrangler secret put NEBIUS_API_KEY --name ratchet-live      # your Token Factory key
npx wrangler secret put DEMO_API_KEY --name ratchet-live        # any long random string you make up (32+ characters)
npx wrangler secret put TAVILY_API_KEY --name ratchet-live      # optional: Scout's real searches (a Tavily key)
npx wrangler secret put EDGE_TOKEN --name ratchet-live          # any long random string; the SAME value goes on the site (below)
```

`EDGE_TOKEN` is how this Worker learns the visitor's address when a request arrives through the main site (Cloudflare Pages, `web/public/_worker.js`), whose
calls reach this Worker from Cloudflare, not from the visitor. With the same value set on the Pages project, the proxy sends the visitor's address and the
token as `x-ratchet-client-ip` and `x-ratchet-edge`; this Worker believes the address only when the token matches its own, and removes both headers before the
gateway sees the request. Without it every visitor of the main site looks like one address: the per-visitor limits below count them together, and so does
Scout's hourly limit.

```
cd web
npx wrangler pages secret put EDGE_TOKEN --project-name ratchet   # the same value as above
```

Set both before the first deploy of the daily limit, and never put the value in the repo, a variable or a chat.

Then deploy: Actions, `deploy-cloudflare-live`, Run workflow, type `deploy-live` (the workflow uses the same two repository
secrets as the simulation demo, `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`).

## Change anything without a push or a deploy

Every variable or secret on the Worker whose name starts with `RATCHET_` is passed to the gateway and **overrides the default in `src/index.ts`**. Edit them in the
Cloudflare dashboard: Workers & Pages, `ratchet-live`, Settings, Variables and Secrets. The ones you will want:

| To change | Set |
|---|---|
| The student's GPU endpoint (once one is hosted) | `RATCHET_STUDENT_BASE` = `https://<endpoint>/v1`, and the secret `RATCHET_STUDENT_KEY` |
| Let the hosted, untuned Nano 30B answer, before Ultra, the calls it was calibrated for (none yet: [evidence/hosted-nano](../../evidence/hosted-nano/README.md)) | `RATCHET_HOSTED_STUDENT` = `1`; needs an image with FR-12.33, and leaves `RATCHET_FALLBACK_STUDENT`, `RATCHET_SAMPLES` and `RATCHET_FALLBACK_STUDENT_REASONING` at their defaults |
| Show real Nano answers and savings on the live demo: a random share of eligible calls (never Scout's planner and writer, tool requests or other teacher-only calls) goes to the hosted Nano whatever its confidence, labelled "Demo split" on every page and audited against Ultra ([ADR 0147](../../docs/adr/0147-demo-split.md)) | `RATCHET_DEMO_SPLIT` = `0.4` (0 is off), together with `RATCHET_HOSTED_STUDENT` = `1`; `RATCHET_DEMO_SPLIT_AUDIT` (default `0.25`) sets the audited share; needs an image with FR-12.34 |
| A key's limits | `RATCHET_KEY_LIMITS`, for example `demo:daily_requests=300,daily_usd=1.5,rpm=60,concurrency=4` |
| The whole gateway's daily spend | `RATCHET_DAILY_BUDGET_USD` and `RATCHET_BUDGET_MODE` |
| Scout's spend and rate | `RATCHET_SCOUT_DAILY_BUDGET_USD`, `RATCHET_SCOUT_RUNS_PER_HOUR` |
| The per-IP rate | `RATCHET_IP_RPM`, `RATCHET_MAX_INFLIGHT` |
| Anything else in `docs/CONFIGURATION.md` | the variable of that name |

`wrangler.json` has `keep_vars`, so a later deploy does not erase what you set in the dashboard. A change reaches the gateway **the next time its container starts**:
it sleeps 30 minutes after the last request, or press Redeploy on the Worker to apply it at once. A value that the gateway rejects stops the container at startup
with its config check; `python -m ratchet.config check` runs the same check on your laptop.

## Spending is capped five ways (the defaults are in `src/index.ts`; override any of them as above)

| Cap | Value |
|---|---|
| Each visitor, at the edge (`EDGE_DAILY_TRIES`, `EDGE_DAILY_SCOUT`) | 3 "Try a real request" calls and 3 Scout runs per visitor address per UTC day, counted before the container wakes |
| The demo key (`RATCHET_KEY_LIMITS`) | 300 requests and $1.50 a day, 60 a minute (the ceiling all visitors share), 4 at once |
| The gateway as a whole (`RATCHET_DAILY_BUDGET_USD`, `RATCHET_BUDGET_MODE=block`) | $2.50 a day; past it, requests get a 429 |
| Scout (`RATCHET_SCOUT_DAILY_BUDGET_USD`) | $1.50 a day, 6 runs an hour |
| Per IP | 30 requests a minute, 6 in flight |

**The daily limit per visitor** exists because every paid call reaches Token Factory and all visitors share one demo key. The Worker counts in a small Durable
Object (`VisitorQuota`, SQLite-backed, deleted an hour after its day ends) named by bucket, UTC day and the SHA-256 of the visitor's address; the address itself
is never stored or logged. Two buckets, each 3 a day by default: `try` (a keyless `POST /v1/*`, the "Try a real request" box) and `scout` (`GET /scout/stream` and
`POST /scout/run`, about 20 Token Factory calls each). A request with a `ratchet_judge` cookie skips the Scout count (the gateway verifies the cookie and keeps its
own guard). Reads (`/scout/status`, `/v1/models`, `/ratchet/*`) and calls that bring their own API key are never counted. Over the limit the Worker answers 429
without waking the container, with `retry-after` set to the seconds until 00:00 UTC: an OpenAI-shaped error (`daily_limit_reached`) for `/v1`, and `{"detail": ...}`
(the gateway's own shape) for Scout. If the Durable Object fails the call goes through (the demo key's limits and the budgets still hold) and the Worker logs
`quota_error` with no address.

**Running totals that survive restarts.** The gateway's ledger is on the container's own disk, so a restart or a redeploy empties `/ratchet/stats`. The Worker therefore
keeps aggregate totals itself, in one more SQLite-backed Durable Object (`LiveCounters`, binding `LIVE_COUNTERS`, migration `v3`): after every paid call, when the totals
are read while the container is up, and when the container's idle timeout expires, it reads `GET /ratchet/counters` from the container and adds the difference since
its last reading of the same instance (a new random `instance` id means a restart, so that reading counts in full). `GET /ratchet/stats/persistent` answers from the
Durable Object without waking the container: `{"recorded", "since", "updated", "instances", "totals"}` with requests by route group, student tokens, Token Factory
cost, Scout runs and errors since the first record (`recorded: false` while nothing is stored, and the pages then show their measured snapshot instead). Counts and
dollars only: no address, prompt, client or key. What can be missed is what a container counted after the Worker's last reading of it, if it is killed straight after.
No secret and no setting; **the migration is applied by `wrangler deploy`** (the deploy workflow), so there is nothing else to run. `curl -s https://live.ratchet.dualmindlab.tech/ratchet/stats/persistent`
checks it after the deploy.

Change the limits in the dashboard like any other setting, with the variables `EDGE_DAILY_TRIES` and `EDGE_DAILY_SCOUT` (a whole number; `0` turns that bucket
off). They have no `RATCHET_` prefix on purpose: the Worker passes every `RATCHET_*` variable to the gateway, which warns about names it does not know. The Worker
picks them up at once, without a container restart.

The container keeps its ledger on its own disk, so a restart (it sleeps 30 minutes after the last request) resets the day's counters. The caps are
low for that reason, and your Token Factory balance is the last limit. Nothing calls it in the background.
