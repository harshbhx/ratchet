# Publishing the story site and the docs (Cloudflare Pages)

Two Pages projects: `ratchet` (the story site, judges' page and dashboard, at ratchet.dualmindlab.tech) and `ratchet-docs` (the docs). The docs are also at docs.ratchet.dualmindlab.tech; the story site at ratchet.dualmindlab.tech.

The gateway is not on Pages: it is the Cloudflare Containers worker `deploy-cloudflare-live` (live.ratchet.dualmindlab.tech, live mode), see [deploy/cloudflare-live/README.md](../deploy/cloudflare-live/README.md).

## Previews are automatic, production is a button

- **Any push to a branch other than `main`** publishes a preview of both sites (workflow `preview-pages`): `https://<branch>.ratchet-aec.pages.dev` and
  `https://<branch>.ratchet-docs.pages.dev`. Production is never touched.
- **Production** is published only by the manual workflow `deploy-pages-production` (Actions, Run workflow, type `deploy-pages`), from `main`.
- Both use the repository secrets `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`. The token needs the **Cloudflare Pages: Edit** permission (Cloudflare dashboard,
  My Profile, API Tokens, edit the token, add Account, Cloudflare Pages, Edit). Nobody needs to share a token with anyone: the repository holds it.
- Secrets are not given to forks, so a push from a fork publishes nothing.

## How the static site reaches a gateway, and how to change which one

The pages call their gateway on the same origin (`/ratchet/*`, `/scout/*`, `/v1/*`, `/healthz`, `/readyz`). On Pages there is no gateway there, so the build ships
`_worker.js` and `_routes.json` (from `web/public/`): only those paths run the worker, which forwards them to a gateway and leaves every page and asset static.

**The gateway is a Cloudflare setting, not code.** In the dashboard, Workers & Pages, the project `ratchet`, Settings, Variables and Secrets, add
`GATEWAY_ORIGIN` (an `https://` address with no path, for example `https://live.ratchet.dualmindlab.tech`). Production and Preview have separate lists, so a preview can
point at another gateway while production points at the live one. Saving it applies at once: no push, no deploy. Without it the live gateway is used (the whole site is one live surface); a value that
is not a plain https address is ignored, so a typo cannot take the site down.

**Scout is separate.** The paths under `/scout/` (the Scout page's status, examples, stream, recordings and the judge code, whose signed `ratchet_judge` cookie passes through the proxy
unchanged) go to `SCOUT_ORIGIN`, which has the same rules and defaults to `https://live.ratchet.dualmindlab.tech`. So `/scout` on the story site makes real Token Factory and Tavily
calls under the live gateway's spend guard, and its label comes from `/scout/status` (`mode`), so it reads Live. The dashboard, the judges' live chapters, `/ratchet/*`, `/v1/*`,
`/healthz` and `/readyz` follow `GATEWAY_ORIGIN`, which also defaults to the live gateway. The live ledger lives in the container's `/tmp`, so a restart empties it and the dashboard then shows its empty states; the mock-only `/ratchet/demo/*` controls answer 403 there and the pages hide them. Set `SCOUT_ORIGIN` separately to move Scout; an empty or invalid value
falls back to the default. Like `GATEWAY_ORIGIN`, it applies at once.

**When the live gateway has slept.** The live container sleeps after 30 minutes without a request, and its first start takes about 20 seconds. The worker tries a reading request again up to three times (0.7, 1.5 and 3 seconds apart) when the gateway answers 502 or 504, drops the connection or says its container is not ready; it never repeats a `/v1/*` call, a Scout run or a control action, so nothing is paid twice, and a normal answer passes through untouched. If the gateway is still not ready, the visitor gets `503` with `Retry-After: 5` and `{"warming_up": true}`; the web app then shows a banner under the header (the live gateway is waking up, about 20 seconds; the page updates by itself), checks `/healthz` every 3 seconds for up to 90 seconds, fills the page in when it answers, and keeps Scout's Run button disabled until then.
