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

## Diagrams

Every diagram is a ` ```mermaid ` block. [astro-mermaid](https://github.com/joesaby/astro-mermaid) renders it in the browser from
the bundled `mermaid` package (no CDN). No SVG or image files.

How it is themed:

- `src/config/mermaid.mjs` holds the whole Mermaid configuration (imported into the `mermaid({...})` block of
  `astro.config.mjs`): the `base` theme, Inter and JetBrains Mono, spacing, and `themeCSS`, a set of rules written in the
  site's CSS variables (`--rt-*` in `src/styles/theme.css`). Mermaid scopes those rules to each diagram, so they win over
  its own colours, and they follow the light and dark theme as soon as it changes, with no re-render.
- astro-mermaid's `autoTheme` is off on purpose: it would swap Mermaid to its own `default` and `dark` themes and drop
  `base` and the brand colours.
- `src/styles/diagrams.css` is the frame: the rounded card, the optional caption and key, scroll shadows, and sideways
  scrolling on a phone instead of shrinking the text.

### The class kit

One set of classes, defined once for the whole site. Tag a node with `A:::student` or `class A,B student`; no `classDef`
is needed.

| Class | Looks like | Use it for |
|---|---|---|
| `student` | owned blue | the student: Nano 4B with the adapter you own (and a version on trial: shadow, canary) |
| `teacher` | rented orange | the teacher you rent per token (Nemotron 3 Ultra on Token Factory) |
| `store` | grey, darker border | the ledger, a bucket, a file: anything that keeps data (pair it with the cylinder `[("...")]`) |
| `gate` | white, dark border | a check or a decision that can stop the flow (pair it with the hexagon `{{"..."}}`) |
| `ok` | green | a good outcome: live, served, in use |
| `danger` | red | a bad outcome: rejected, rolled back, refused |
| `pending` | amber, dashed | written but not run yet |
| `outline` | dashed outline | optional, off by default, or the untuned hosted fallback (never the owned blue) |
| `neutral` | the default node | when a class must be named |

Subgraphs take zone classes, which colour only the border: `studentZone`, `teacherZone`, `okZone`, `pendingZone`,
`outlineZone`. Edges carry meaning by their stroke: `==>` is the main path, `-->` a normal step, `-.->` optional or off.

In a sequence diagram, a participant whose id is `Student` (or `Endpoint`), `Teacher`, `Ledger` or `Bucket` gets the kit's
colours: `participant Student as Student<br/>vLLM`.

The copy-paste snippet below declares the kit's names at the end of a diagram. It sets only stroke widths and dashes,
never a colour: Mermaid writes a `classDef` inline with `!important`, so a fixed colour there would stay light in dark
mode (and a flowchart `classDef` cannot read a CSS variable). The colours come from `themeCSS` on this site; elsewhere
(GitHub, the Mermaid live editor) the dashes still show which boxes are optional or not run.

```text
  classDef student stroke-width:1.5px
  classDef teacher stroke-width:1.5px
  classDef store stroke-width:1.25px
  classDef gate stroke-width:1.5px
  classDef ok stroke-width:1.5px
  classDef danger stroke-width:1.5px
  classDef pending stroke-width:1.5px,stroke-dasharray:5 4
  classDef outline stroke-width:1.25px,stroke-dasharray:4 3
  classDef neutral stroke-width:1px
```

### A diagram with a caption

````mdx
<figure class="rt-diagram">

```mermaid
flowchart TB
  app(["Your app"]) ==> router["Cascade"]
  router ==>|"3 samples"| student["Nano 4B + LoRA"]
  router -->|"escalate"| teacher["Nemotron 3 Ultra"]
  class student student
  class teacher teacher
```

<figcaption>

**Reading this diagram.** Two to five plain sentences: where to start, what the thick path is, what is left out.

<ul class="rt-key">
  <li class="student">The student you own</li>
  <li class="teacher">Rented per token</li>
</ul>

</figcaption>
</figure>
````

Keep the blank lines around the block and inside `<figcaption>`, or MDX reads them as inline text. A bare block with no
`<figure>` gets the same card without a caption. The key's chips are the kit's classes plus `edge-main` and `edge-optional`.

### Rules of thumb

- Fit the column: at most about 730 px of diagram (the card leaves 734 px in the 48rem column), at the natural text size.
  Wider diagrams scroll sideways. A phone always scrolls, which is fine: a figure tells phone readers to scroll sideways.
- About 12 nodes at most. Split a bigger idea into two diagrams, or move detail into the caption.
- Short labels: two lines of three or four words. Put names in quotes (`A["..."]`), and never use parentheses, `<` or
  `>` in an unquoted label. `<br/>` breaks a line.
- `flowchart TB` for hierarchies and anything with more than four steps (it stays narrow); `LR` only for three or four
  boxes in a row.
- Subgraphs group what runs in one place. Mermaid's layout gets wide when many edges leave a subgraph from the nodes
  inside it: connect the subgraph itself (`vm ==> tf`), keep labels off long edges, and stack siblings with `~~~`
  (an invisible link) when a row gets too wide.
- Colour never stands alone: say "not run", "in use" or "rejected" in a label or the caption as well.
- Simulation numbers in a diagram say "simulation" next to them, as everywhere else.
- Check both themes and a 390 px wide window before you commit.

## Writing guides

Every guide follows the same shape: `<Summary>` (one sentence), `<Eli10>` (an everyday analogy), "How it really works"
(numbered steps and a diagram), "For engineers" (files, functions, settings, endpoints) and "Common questions". Plain English,
short sentences, no hype; simulation numbers say "simulation", costs say "estimate" with a source and date. Components live in
`src/components/` (`Who` badges use the landing page's words; the diagrams in `diagrams/` use the owned blue for the student
and the rented orange for the teacher). Mermaid code blocks render in the browser from a bundled copy; nothing loads from a CDN.
