# Publishing the story site and the docs (Cloudflare Pages)

Two Pages projects: `ratchet` (the story site, judges' page and dashboard, at ratchet.dualmindlab.tech) and `ratchet-docs` (the docs). The docs are also at docs.ratchet.dualmindlab.tech; the story site at ratchet.dualmindlab.tech.

The two gateways are not on Pages: they are the Cloudflare Containers workers `deploy-cloudflare-live` (live.ratchet.dualmindlab.tech, live mode) and `deploy-cloudflare-demo` (demo.ratchet.dualmindlab.tech, mock mode), see [deploy/cloudflare-live/README.md](../deploy/cloudflare-live/README.md).

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
point at the simulation demo while production points at the live one. Saving it applies at once: no push, no deploy. Without it the simulation demo is used; a value that
is not a plain https address is ignored, so a typo cannot take the site down.
