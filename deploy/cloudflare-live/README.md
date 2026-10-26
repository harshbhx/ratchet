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
```

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
| A key's limits | `RATCHET_KEY_LIMITS`, for example `demo:daily_requests=300,daily_usd=1.5,rpm=10,concurrency=2` |
| The whole gateway's daily spend | `RATCHET_DAILY_BUDGET_USD` and `RATCHET_BUDGET_MODE` |
| Scout's spend and rate | `RATCHET_SCOUT_DAILY_BUDGET_USD`, `RATCHET_SCOUT_RUNS_PER_HOUR` |
| The per-IP rate | `RATCHET_IP_RPM`, `RATCHET_MAX_INFLIGHT` |
| Anything else in `docs/CONFIGURATION.md` | the variable of that name |

`wrangler.json` has `keep_vars`, so a later deploy does not erase what you set in the dashboard. A change reaches the gateway **the next time its container starts**:
it sleeps 30 minutes after the last request, or press Redeploy on the Worker to apply it at once. A value that the gateway rejects stops the container at startup
with its config check; `python -m ratchet.config check` runs the same check on your laptop.

## Spending is capped four ways (the defaults are in `src/index.ts`; override any of them as above)

| Cap | Value |
|---|---|
| The demo key (`RATCHET_KEY_LIMITS`) | 300 requests and $1.50 a day, 10 a minute, 2 at once |
| The gateway as a whole (`RATCHET_DAILY_BUDGET_USD`, `RATCHET_BUDGET_MODE=block`) | $2.50 a day; past it, requests get a 429 |
| Scout (`RATCHET_SCOUT_DAILY_BUDGET_USD`) | $1.50 a day, 6 runs an hour |
| Per IP | 30 requests a minute, 6 in flight |

The container keeps its ledger on its own disk, so a restart (it sleeps 30 minutes after the last request) resets the day's counters. The caps are
low for that reason, and your Token Factory balance is the last limit. Nothing calls it in the background.
