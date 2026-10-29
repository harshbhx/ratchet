# Ratchet live gateway on Cloudflare Containers

The same gateway image as the optional mock-mode host (`../cloudflare`), but in **live mode**: every answer is a real call to
Nemotron 3 Ultra on Nebius Token Factory, behind a Worker at `live.ratchet.dualmindlab.tech`. The mock-mode host stays at
`demo.ratchet.dualmindlab.tech` (not linked from the site).

What it is, honestly: Ratchet's own student adapter is not connected to the live gateway (serving it needs a GPU endpoint), so
Ultra teaches and a random 40% of eligible requests are answered by the hosted Nano 30B (a quarter of those
re-checked by Ultra), each one logged with its provenance, cost and request id. The student's real results are on the story
page and the judges' page; the cascade's behaviour with a student is covered by the mock-mode tests; the student was served on a Nebius endpoint for speed tests on 27 to 28 Oct 2026 but the live gateway is not connected to it.

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
secrets as the mock-mode host, `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`).

## Change anything without a push or a deploy

Every variable or secret on the Worker whose name starts with `RATCHET_` is passed to the gateway and **overrides the default in `src/index.ts`**. Edit them in the
Cloudflare dashboard: Workers & Pages, `ratchet-live`, Settings, Variables and Secrets. The ones you will want:

| To change | Set |
|---|---|
| The student's GPU endpoint (once one is hosted) | `RATCHET_STUDENT_BASE` = `https://<endpoint>/v1`, and the secret `RATCHET_STUDENT_KEY` |
| Let the hosted, untuned Nano 30B answer, before Ultra, the calls it was calibrated for (none yet: [evidence/hosted-nano](../../evidence/hosted-nano/README.md)) | `RATCHET_HOSTED_STUDENT` = `1`; needs an image with FR-12.33, and leaves `RATCHET_FALLBACK_STUDENT`, `RATCHET_SAMPLES` and `RATCHET_FALLBACK_STUDENT_REASONING` at their defaults |
| Show real Nano answers and savings on the live site: a random share of eligible calls (never Scout's planner and writer, tool requests or other teacher-only calls) goes to the hosted Nano whatever its confidence, labelled "Demo split" on every page and audited against Ultra ([ADR 0147](../../docs/adr/0147-demo-split.md)) | `RATCHET_DEMO_SPLIT` = `0.4` (0 is off), together with `RATCHET_HOSTED_STUDENT` = `1`; `RATCHET_DEMO_SPLIT_AUDIT` (default `0.25`) sets the audited share; needs an image with FR-12.34 |
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
| The shared site key (`RATCHET_KEY_LIMITS`) | 300 requests and $1.50 a day, 60 a minute (the ceiling all visitors share), 4 at once |
| The gateway as a whole (`RATCHET_DAILY_BUDGET_USD`, `RATCHET_BUDGET_MODE=block`) | $2.50 a day; past it, requests get a 429 |
| Scout (`RATCHET_SCOUT_DAILY_BUDGET_USD`) | $1.50 a day, 6 runs an hour |
| Per IP | 30 requests a minute, 6 in flight |

**The daily limit per visitor** exists because every paid call reaches Token Factory and all visitors share one site key. The Worker counts in a small Durable
Object (`VisitorQuota`, SQLite-backed, deleted an hour after its day ends) named by bucket, UTC day and the SHA-256 of the visitor's address; the address itself
is never stored or logged. Two buckets, each 3 a day by default: `try` (a keyless `POST /v1/*`, the "Try a real request" box) and `scout` (`GET /scout/stream` and
`POST /scout/run`, about 20 Token Factory calls each). A request with a `ratchet_judge` cookie skips the Scout count (the gateway verifies the cookie and keeps its
own guard). Reads (`/scout/status`, `/v1/models`, `/ratchet/*`) and calls that bring their own API key are never counted. Over the limit the Worker answers 429
without waking the container, with `retry-after` set to the seconds until 00:00 UTC: an OpenAI-shaped error (`daily_limit_reached`) for `/v1`, and `{"detail": ...}`
(the gateway's own shape) for Scout. If the Durable Object fails the call goes through (the site key's limits and the budgets still hold) and the Worker logs
`quota_error` with no address.

**The human check (Cloudflare Turnstile), optional.** The buttons that spend Token Factory money in a browser (Try a real request, Scout) can be made to pass a
"verify you are human" check first. It is **off until you set the secret**, so this code can be deployed before the widget exists.

| Name | Kind | Where | What it does |
|---|---|---|---|
| `TURNSTILE_SECRET_KEY` | secret | the `ratchet-live` Worker | Turns the check on. Without it nothing below happens. |
| `TURNSTILE_SITE_KEY` | variable (public) | the `ratchet-live` Worker | The widget's site key. The web app reads it from `GET /ratchet/turnstile` (answered by the Worker), so setting or changing it needs no rebuild of the site. Without it no widget is drawn. |
| `EDGE_TURNSTILE_MAX_FAILS` | variable | the `ratchet-live` Worker | Invalid tokens per visitor per UTC day before a block until 00:00 UTC. Default 5, `0` is off. |
| `TURNSTILE_HOSTNAMES` | variable | the `ratchet-live` Worker | Comma-separated hostnames a good token may have been solved on (siteverify's `hostname`). Default `ratchet.dualmindlab.tech,live.ratchet.dualmindlab.tech`; localhost is never in the default. A token for any other hostname is treated as invalid (403, counted, logged as `turnstile_wrong_hostname` with the hostname only). Cloudflare's test keys answer `example.com`, so testing with them needs `TURNSTILE_HOSTNAMES=example.com`. |
| `EDGE_DAILY_CURL_TOTAL` | variable | the `ratchet-live` Worker | Keyless calls **without** a token (curl), all visitors together, per UTC day. Default 100, `0` is off. |

Set up: in the Cloudflare dashboard, Turnstile, add a widget (mode **Managed**) for the hostnames `ratchet.dualmindlab.tech` (the site, whose requests the Pages proxy
forwards) and `live.ratchet.dualmindlab.tech` (the live host serves the same pages directly). Then `npx wrangler secret put TURNSTILE_SECRET_KEY --name ratchet-live`
(the widget's secret key, never in the repo) and add the variable `TURNSTILE_SITE_KEY` (the widget's site key) in the dashboard (Workers & Pages, ratchet-live, Settings,
Variables and Secrets). No deploy is needed. The pages' browser calls are same-origin (the Worker sends no CORS headers), so no other hostname is involved.

How it works. The page asks the Worker for the site key, draws the widget (managed, quiet: it passes without a click unless Cloudflare wants one) and sends a fresh token
with each paid call, in the header `cf-turnstile-response` (for Scout's EventSource, which cannot set headers, in the query parameter of the same name). The Worker verifies it
with Cloudflare's siteverify (secret, token and the visitor's address) **before** the call reaches the container or Token Factory, once per request, and removes it before forwarding.
A token works once and lives 300 seconds, so every check draws a new widget.

**Verify once per visit.** The widget is drawn inline, right under the button that asked (the "Try a real request" send button on the landing page, `/live` and the judges
page; the Ask button on Scout), never in a floating box. When a token passes siteverify (success, and a hostname on the allowlist) the Worker answers with a session cookie,
`rt_human`: `HttpOnly; Secure; SameSite=Lax; Path=/`, valid for **12 hours** from the check (not tied to midnight; the daily limits are counted per UTC day by address, on their own).
Its value is `v1.<expiry>.<first 32 hex of the SHA-256 of the visitor's address>.<signature>`, signed with HMAC-SHA256 under a key derived from the Turnstile secret
(`HMAC(TURNSTILE_SECRET_KEY, "rt_human_v1")`), so there is no new secret and rotating the Turnstile secret signs everyone out. It is bound to the visitor's address: one solved
check cannot be shared with a farm of machines, and a visitor whose address changes just checks again. A paid keyless call that carries a valid cookie needs no token (a token sent
along is not spent); it is still under the per-visitor daily limits and every dollar cap. A cookie that is missing, tampered with, expired or for another address counts as absent:
no failure is counted and nothing is refused for that reason, the token flow below simply applies. The signature is checked in constant time. A blocked visitor stays blocked even
with a cookie. Only a token Cloudflare accepted earns the cookie, never a fail-open call. The cookie is for the Worker only: it is removed before a request reaches the gateway.
`GET /ratchet/human` answers `{"verified": true|false, "expires": <ISO time>|null}` from the Worker (no container), so the page knows whether to show the check at all.
The cookie reaches the browser on the main site through the Pages proxy (`web/public/_worker.js` forwards the `Cookie` header to this Worker and returns `Set-Cookie` untouched,
with no `Domain`, so it belongs to `ratchet.dualmindlab.tech`), and Scout's EventSource, being same-origin, sends it by itself; the query-parameter token is only needed for the
first call.

- **Good token**: the call goes on to the per-visitor daily limit above, as before.
- **Invalid token** (siteverify says `invalid-input-response`: forged or malformed): 403 and the words "Human check failed. Refresh the page and try again." (an OpenAI-shaped
  error with the code `human_check_failed` for `/v1`, `{"detail": ...}` for Scout), and one failure is counted for that visitor and day (a Durable Object, the same
  `VisitorQuota` class, named by day and the hash of the address). After `EDGE_TURNSTILE_MAX_FAILS` failures that visitor gets 429 (`human_check_blocked`, with `retry-after`)
  on every keyless paid call, with or without a token, until 00:00 UTC.
- **Expired or already-used token** (`timeout-or-duplicate`, what a retry or a reconnect with an old token produces): the same 403 and words, but **not counted**, so a real
  person who retries is never blocked. A missing token on a Scout call is a plain 403 (`Human check required`), also not counted.
- **Siteverify unreachable or erroring** (network failure, 5xx, a wrong secret on our side): the call fails open to the per-visitor limit, nothing is counted against the
  visitor and the Worker logs `turnstile_unavailable`. A Cloudflare outage never locks a judge out.
- **No token on `POST /v1/*` (curl)**: still allowed, 3 a day per visitor, and now also counted in one shared allowance (`EDGE_DAILY_CURL_TOTAL`, a Durable Object named by
  day) so that rotating addresses cannot drain the budget kept for verified people. When it is used up: 429 (`curl_allowance_used`) saying so and pointing to the browser. A
  browser whose widget is blocked (an ad blocker) falls into this case too.
- **Scout** is browser-only while the check is on: a Scout run needs a valid token, even with a judge cookie (the Worker only sees that the cookie exists).
- **Calls with an `Authorization` header** (a real API key) never pass through the check: the gateway checks the key.

What the visitor sees. On the first click with no verified visit, a slim row opens under the button ("Checking you're human · Cloudflare"). It usually passes without a click
(`interaction-only`); if Cloudflare needs one, the widget appears in that same spot with "One quick check, then you're set for this visit." (size `flexible`, the site's current
theme). On success it settles into "Verified for this visit" and the request goes on by itself. Later clicks show nothing. A failure is "Couldn't confirm you're human. Try again."
with a Try again button; a block is "Too many failed checks from your network today. It resets at 00:00 UTC."; a widget that did not load (an ad blocker) says so, and `/v1` still
goes as a plain keyless call (a few a day) while Scout says it needs the check.

Retries and reconnects. A token is checked once per request. The web app never reuses one: a Scout stream that errors is closed by the page (no browser auto-reconnect with
the same address) and the fallback `POST /scout/run` asks for a new token first; the "Try a real request" box sends once and a new send gets a new token. The Worker's own
loops that hold a request while the container wakes re-send it without checking the token again. The Pages proxy never retries `/v1/*` or Scout runs.

**Running totals that survive restarts.** The gateway's ledger is on the container's own disk, so a restart or a redeploy empties `/ratchet/stats`. The Worker therefore
keeps aggregate totals itself, in one more SQLite-backed Durable Object (`LiveCounters`, binding `LIVE_COUNTERS`, migration `v3`): after every paid call, when the totals
are read while the container is up, and when the container's idle timeout expires, it reads `GET /ratchet/counters` from the container and adds the difference since
its last reading of the same instance (a new random `instance` id means a restart, so that reading counts in full). `GET /ratchet/stats/persistent` answers from the
Durable Object without waking the container: `{"recorded", "since", "updated", "instances", "totals"}` with requests by route group, student tokens, Token Factory
cost, Scout runs, errors and server errors since the first record (`errors` counts every refused key too and is kept for compatibility; `server_errors`, a new key that starts at zero, counts failed model calls and the gateway's own 5xx, and is the one the pages show) (`recorded: false` while nothing is stored, and the pages then show their measured snapshot instead). Counts and
dollars only: no address, prompt, client or key. What can be missed is what a container counted after the Worker's last reading of it, if it is killed straight after.
No secret and no setting; **the migration is applied by `wrangler deploy`** (the deploy workflow), so there is nothing else to run. `curl -s https://live.ratchet.dualmindlab.tech/ratchet/stats/persistent`
checks it after the deploy.

Change the limits in the dashboard like any other setting, with the variables `EDGE_DAILY_TRIES` and `EDGE_DAILY_SCOUT` (a whole number; `0` turns that bucket
off). They have no `RATCHET_` prefix on purpose: the Worker passes every `RATCHET_*` variable to the gateway, which warns about names it does not know. The Worker
picks them up at once, without a container restart.

The container keeps its ledger on its own disk, so a restart (it sleeps 30 minutes after the last request) resets the day's counters. The caps are
low for that reason, and your Token Factory balance is the last limit. Nothing calls it in the background.
