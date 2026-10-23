# Ratchet LIVE demo on Cloudflare Containers

The same gateway image as the simulation demo (`../cloudflare`), but in **live mode**: every answer is a real call to
Nemotron 3 Ultra on Nebius Token Factory, behind a Worker at `live.ratchet.dualmindlab.tech`. The simulation demo stays at
`demo.ratchet.dualmindlab.tech`, labelled as simulation.

What it is, honestly: a fresh live gateway has no trained student yet (training needs a GPU, and none has run on Nebius), so the
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

## Spending is capped three ways (set in `src/index.ts`)

| Cap | Value |
|---|---|
| The demo key (`RATCHET_KEY_LIMITS`) | 300 requests and $1.50 a day, 10 a minute, 2 at once |
| The gateway as a whole (`RATCHET_DAILY_BUDGET_USD`, `RATCHET_BUDGET_MODE=block`) | $2.50 a day; past it, requests get a 429 |
| Scout (`RATCHET_SCOUT_DAILY_BUDGET_USD`) | $1.50 a day, 6 runs an hour |
| Per IP | 30 requests a minute, 6 in flight |

The container keeps its ledger on its own disk, so a restart (it sleeps 30 minutes after the last request) resets the day's counters. The caps are
low for that reason, and your Token Factory balance is the last limit. Nothing calls it in the background.
