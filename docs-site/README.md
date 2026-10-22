# Ratchet docs site

The documentation site for Ratchet, built with [Starlight](https://starlight.astro.build) (Astro) and the
[Lucode](https://github.com/lucas-labs/lucode-starlight-theme) theme, re-skinned with the landing page's palette and fonts.
It is published at https://docs.ratchet.dualmindlab.tech (Cloudflare Pages project `ratchet-docs`).

## Where the content comes from

| Content | Source | Committed? |
|---|---|---|
| Reference docs (`/reference/...`) | `../docs/**/*.md`, copied by `scripts/sync-docs.mjs` before every `dev` and `build` | No: `docs/` is the source of truth |
| Guides (`/guides/...`), home page, Python API | `src/content/docs/` | Yes |
| HTTP API (`/api/http/...`) | `openapi/ratchet.json`, rendered by `starlight-openapi` | Yes (generated) |
| Environment variables, Command line (`/api/...`) | `generated/*.json` plus `docs/CONFIGURATION.md`, rendered by the sync script | Data yes, pages no |

The sync script turns each doc's first `# ` heading into its title, rewrites links between docs to site routes and links to
code to GitHub, drops an anchor that matches no heading (and says so), and skips the per-folder contributor notes.

## Run it

Node 22.12 or newer (Astro 7.3). From this folder:

```bash
npm ci
npm run dev       # http://localhost:4321, with hot reload (re-run npm run sync after editing ../docs)
npm run build     # sync, build into dist/, check every internal link (the build fails on a broken one)
npm run preview   # serve dist/
```

After changing a route, a setting or a command line tool, regenerate the reference data (needs the Python package
installed, `make install` at the repo root) and commit it:

```bash
python docs-site/scripts/export-reference.py          # from the repo root
python docs-site/scripts/export-reference.py --check  # exit 1 if the committed data is stale (for CI)
```

## Deploy

```bash
npm run build
npx wrangler pages deploy dist --project-name ratchet-docs --branch main
```

The custom domain `docs.ratchet.dualmindlab.tech` is attached to the Pages project in the Cloudflare dashboard.

## Writing guides

Every guide follows the same shape: `<Summary>` (one sentence), `<Eli10>` (an everyday analogy), "How it really works"
(numbered steps and a diagram), "For engineers" (files, functions, settings, endpoints) and "Common questions". Plain English,
short sentences, no hype; simulation numbers say "simulation", costs say "estimate" with a source and date. Components live in
`src/components/` (`Who` badges use the landing page's words; the diagrams in `diagrams/` use the owned blue for the student
and the rented orange for the teacher). Mermaid code blocks render in the browser from a bundled copy; nothing loads from a CDN.
