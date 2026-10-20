# CLAUDE.md: `web/` (React app)

The UI for `/` (the story), `/scout` and `/dashboard`. Vite 8, React 19, TypeScript, Tailwind v4, motion, lucide-react, recharts, react-router v8. Read [DESIGN.md](DESIGN.md) before changing anything visual.

## Commands

```bash
make web                                         # npm ci && npm run build -> ratchet/web/dist
make demo                                        # gateway on :8000 (serves the built UI)
make web-dev                                     # hot reload on :5173, API proxied to :8000
RATCHET_API=http://localhost:8831 npx vite       # dev server against another gateway
npx tsc --noEmit -p .                            # typecheck
```

## Rules

- **The build is committed.** `ratchet/web/dist` ships inside the Python package so `pip install .` and the Docker image serve the UI without Node. After any change in `web/`, run `make web` and commit `ratchet/web/dist`. CI fails if the committed build is stale.
- The gateway serves `dist/index.html` for `/`, `/scout`, `/dashboard` and mounts `/assets`. If `dist` is missing, it falls back to the hand-written pages, which also stay at `/lite`, `/lite/scout`, `/lite/dashboard`. New top-level routes need a matching route in `ratchet/gateway.py`.
- API types live in `src/lib/api.ts` and mirror [docs/API.md](../docs/API.md) and [docs/SCOUT.md](../docs/SCOUT.md). Change both sides together.
- Color carries meaning: `owned` (blue) = the student you own, `rented` (orange) = the teacher you rent. Status colors (`good`/`warn`/`bad`) are for state only and always come with an icon or a word.
- Every number is live from the API, labelled **Simulation** (`src/lib/sim.ts`), or a linked third-party fact. No invented numbers, testimonials or logos.
- Web content (Scout search results) is rendered as React text only; links go through `safeHref`. Never use `dangerouslySetInnerHTML`.
- No external scripts, fonts or CDNs at runtime: fonts are bundled from `@fontsource-variable`.
- Browser storage holds the theme and nothing else. A key the operator types (the admin key) lives in React state and goes out only as an `Authorization: Bearer` header (`adminHeaders` in `api.ts`); `test_dashboard_admin_key_stays_in_memory` scans `src/` for this.

## Layout

| Path | What |
|---|---|
| `src/lib/` | `api.ts` (typed client, `streamScout`), `hooks.ts`, `utils.ts`, `sim.ts` (reference simulation) |
| `src/components/ui/` | Button, Badge, Card, Section, CodeBlock, Stat/CountUp, Logo, WhoBadge |
| `src/components/layout/` | Header (nav, theme toggle), footer, layout |
| `src/pages/landing/` | The story: hero flow, chapters 01–08, scrollytelling, rollback story, simulation scrubber |
| `src/pages/scout/` | Scout: ask bar, agent graph, totals, lanes, answer, run summary (`state.ts` reduces the event stream) |
| `src/pages/dashboard/` | Operator console, in this order: demo controls, KPIs, release pipeline, economics (`economics.tsx`, helpers in `economics-model.ts`: `GET /ratchet/cost` fetched after every stats poll by `useCostReport`; savings per token and by GPU-hours paid, the break-even meter, the recommendation and prices used, savings by day as a chart or table; the `/ratchet/stats` `cost` summary when the report does not load, hidden without either), charts, tables (versions with BOM links, by task), task registry (`registry.tsx`, helpers in `registry-model.ts`: `GET /ratchet/tasks` fetched after every stats poll by `useTaskRegistry`, hidden on a 404; filter by source, search, unregistered names; `task-drawer.tsx`: one task's field specs from `GET /ratchet/tasks/{name}` in a `Drawer` (`drawer.tsx`, a focus-trapped side sheet), and the editor for API tasks (`POST /ratchet/tasks/validate`, `PUT`, `DELETE`) with an admin key held in the panel's state only, never in storage or a URL), resilience (`resilience.tsx`, helpers in `resilience-model.ts`: breaker states from `circuit` events, fallbacks from `tasks[].routes`, recent failures, and the mock-only outage drill with a countdown from its `fault` event; the event regexes there and `CIRCUIT_RE` in `model.ts` are checked against the gateway by `test_resilience_panel_contract`, so keep each on one line), trust and privacy (`trust.tsx`: redaction, API keys, BOM signing, per-client table, live redactor via `POST /ratchet/redact`), release log (every event kind labelled in `model.ts`), provenance |
