# Ratchet web: design brief

The React app in `web/` replaces the hand-written pages. It is built with Vite into `ratchet/web/dist`, and the gateway serves it at `/` (story), `/scout` (multi-agent demo) and `/dashboard`. The old single-file pages stay at `/lite`, `/lite/scout` and `/lite/dashboard`.

## The story in one line

A ratchet turns only one way. Every night Ratchet clicks forward: more of your traffic moves from a model you rent (Nemotron 3 Ultra) to Nemotron 3 Nano 4B with a LoRA adapter you own. The gates are the pawl that stops it from slipping back: a bad release is rolled back automatically.

Use this metaphor with restraint: a few deliberate moments, not on every heading.

## Voice

- Plain English, short sentences, concrete numbers. No hype words ("revolutionary", "seamless", "game-changing", "unleash", "supercharge", "magic").
- Every number is either live from the API, or labelled **Simulation** with its source (`SIM_SOURCE` in `src/lib/sim.ts`), or a third-party fact with a link. Never invent numbers, customers, testimonials or logos.
- Never draw company logos (NVIDIA, Nebius, OpenAI, Anthropic, Tavily, GitHub). Name them in text.

## Color has meaning

| Token | Means | Use |
|---|---|---|
| `owned` (blue) | the student: Nano with the adapter **you own** | "Nano · yours" badges, student share, Ratchet cost line |
| `rented` (orange) | the teacher you **rent** per token | "Ultra · Token Factory" badges, all-teacher baseline, escalations |
| `good` / `warn` / `bad` | status only | live, holding, rolled back / rejected. Always with an icon or a word, never color alone |
| `ink`, `ink-2`, `muted` | text | text never wears a series color |
| `bg`, `bg-sunken`, `surface`, `surface-2`, `line`, `line-strong` | surfaces | |

These are CSS variables (see `src/styles/globals.css`) exposed as Tailwind colors: `bg-owned`, `text-rented-ink`, `border-line`, `bg-owned-soft`, etc. Use `*-ink` variants for colored text on light backgrounds (contrast). Dark mode is the `.dark` class on `<html>` (toggle in the header); use `dark:` only when a token is not enough.

The hosted fallback (the untuned Nano 30B on Token Factory that answers student calls while the GPU endpoint is off) is neither: its badge is `outline`, never the owned blue.

### Who answered: the label vocabulary

One function, `whoLabel` in `src/lib/who-model.ts`, gives every badge its words (FR-7.40); `WhoBadge` renders it. A student call is ours only when its `model` is not the gateway's `fallback_student`, so pass the call's `model` wherever it is known. A badge that says who answers a kind of call (an agent card, an explainer) passes `serving`, the model student calls go to now, from `WhoProvider` (`whoContext`); a badge that describes a component of the stack, or a labelled simulation, keeps its fixed words. The token names stay `owned` and `rented`; only the visible text below is shown.

| Who | Badge | Tone | Tooltip |
|---|---|---|---|
| Our adapter (`student-vN`) | Nano · yours | `owned` | "Nemotron 3 Nano 4B with our LoRA adapter v[N], on a Nebius Serverless Endpoint" |
| The hosted fallback | Nano 30B · hosted | `outline` | "Untuned Nemotron 3 Nano 30B on Token Factory, held to confidence threshold [T]. Not covered by our adapter's precision certificate." (plus "Our adapter answers during the daily endpoint windows." when the gateway reports windows) |
| The teacher | Ultra · Token Factory | `rented` | "Nemotron 3 Ultra on Nebius Token Factory" |
| A tool | Tavily, or Search | `neutral` | the tool's id |

While `student_serving.kind` is `hosted_fallback`: say "the student" or "the hosted Nano (untuned)", never "your model" (`studentCopy`); hide the live version's golden precision and say "not covered by the certificate" instead (`precisionShown`); the dashboard header names the serving student (`servingLine`). Never call the teacher "rented" in a badge; name where it runs.

### Live and simulation: the switch, the strip and the shared notice (FR-11.30, FR-11.31)

The demo VM serves a live gateway and a simulation ([ADR 0133](../docs/adr/0133-live-and-simulation-hosts.md)); `src/lib/surface-model.ts` (pure, tested in `tests/surface-model.test.mjs`) reads `/ratchet/stats` `links`, `demo` and `upstream`, and every field is optional so an older gateway just hides what needs it.

- **Header switch.** A row under the header bar: "Live on Nebius Token Factory | Simulation". The surface this page is on is a highlighted label; the other is an external link when its URL is known (`links.sim` on the live host, `links.live` on the simulation), else it is left out. The label says "Nebius Token Factory" only when `upstream.token_factory` is true, else just "Live". The host name never appears in the build; both URLs come from the gateway. It wraps at 360 px and never scrolls the page sideways.
- **Live strip.** In live mode, a row under the four numbers: "Last Token Factory call: 3 min ago · nvidia/Nemotron-3-Ultra-550b-a55b · 4.2 s" and the same for Tavily. A failure reads "failed 3 min ago (timeout)" or "(HTTP 429)", then "last success 40 min ago". With no call yet: "No Token Factory call since the gateway started N min ago". Never content, only a model id, a time and a status. Hidden in mock mode, where the strip already says "Simulation (mock models)". "Open the simulation" or "Open the live gateway" sits next to "Open the dashboard" when the link is known.
- **Dashboard.** Live: the same last-call line in the header. Simulation: the notice "Shared simulation: every visitor sees and changes the same state. It resets after 2 h without activity, 30 simulated days or 200,000 requests." above the demo controls (only the limits that are set), and a note when a click reset it. A `demo_reset` event reads "Simulation reset".

## Type and layout

- Inter Variable (sans) and JetBrains Mono Variable (mono) are bundled. Mono for model names, task names, code, small eyebrows.
- Headings: tight tracking (`tracking-[-0.025em]`), `text-balance`. Body 16–18px, `text-pretty`, max ~68ch.
- Container: `<Container>` (max-w-6xl, 16px gutters on phones). No horizontal scroll at 360px.
- Cards: `<Card>` (rounded-2xl, border-line, shadow-card). Generous whitespace between chapters (py-24 to py-32 on desktop).
- Background texture: `.bg-grid` (faint engineering grid) only behind the hero or one or two story moments.

## Shared building blocks (use these, don't re-create)

- `@/components/ui/button`: `Button`, `ButtonLink` (router), `ButtonA` (external); variants `primary | owned | outline | ghost | subtle | danger`, sizes `sm | md | lg | icon`.
- `@/components/ui/badge`: `Badge` with `tone` `neutral | owned | rented | good | warn | bad | outline`.
- `@/components/ui/who`: `WhoBadge servedBy="student|teacher|tool" model=... fallback=...`: the one vocabulary for who answered (`whoLabel`, above). A page that knows the gateway's fallback id wraps its badges in `WhoProvider`.
- `@/components/ui/card`, `@/components/ui/section` (`Container`, `Eyebrow`, `H2`, `Lead`), `@/components/ui/code-block` (`CodeBlock`, `tok`), `@/components/ui/stat` (`Stat`, `CountUp`), `@/components/ui/logo`.
- `@/lib/api`: typed client (`api.stats()`, `api.cost()`, `api.tasks()`, `api.task(name)`, `api.validateTask()`, `api.putTask()`, `api.deleteTask()` (these three take the admin key as an argument), `api.demoDay()`, `api.breakNext()`, `api.studentOutage()`, `api.reset()`, `api.scoutExamples()`, `api.scoutStatus()`, `streamScout()`), and all response types. `ApiError.body` holds a parsed error body (a 422's `errors`).
- `src/pages/dashboard/drawer.tsx`: `Drawer`, `DrawerBody`, `DrawerFooter`, a side sheet for detail and editing (modal, focus kept inside, Escape and a click outside close it, focus returns to the opener).
- `@/lib/hooks`: `useStats(intervalMs)`, `useTheme()`, `useCountUp()`, `useInterval()`.
- `@/lib/utils`: `cn`, `fmtUsd`, `fmtPct`, `fmtInt`, `fmtMs`, `domainOf`, `safeHref`, `REPO_URL`, `DOCS_URL`.
- `@/lib/sim`: the seed-7 simulation table (`SIM_DAYS`, `SIM_TOTAL`, `SIM_SOURCE`), the same run's cost report (`SIM_COST_REPORT`, a `CostReport`, so it renders with the code a live report uses) and the command that reproduces it (`SIM_COMMAND`). A test pins every number to a fresh run.
- Libraries available: `motion/react` (animation; respect reduced motion; `MotionConfig reducedMotion="user"` is set globally), `lucide-react` (icons; there is no GitHub icon), `recharts` (charts), `react-router` v8 (`Link`, `NavLink`).

## Motion

- Purposeful: reveal on scroll (fade + 12–24px rise, 0.5–0.7s, ease `[0.22, 1, 0.36, 1]`), animate state changes, count numbers up once. No parallax for its own sake, no bouncing, no infinite attention-seeking loops except one calm ambient element in the hero.
- Everything must still read well with reduced motion (final state shown).

## Charts (dataviz rules)

- One y-axis per chart. Thin 2px lines, rounded data ends, recessive grid (`var(--line)`), axis text in `muted`.
- Two series → a legend and direct labels; identity never by color alone.
- Every chart has a hover tooltip (crosshair on lines) and a text alternative (`aria-label` summary or a table).
- Owned = `var(--owned)`, rented/baseline = `var(--rented)` (dashed for baselines). Status colors never used as series colors.

## Accessibility

Semantic landmarks and headings in order, visible focus (global `:focus-visible`), `aria-live` for streaming progress, labels on icon buttons, 4.5:1 text contrast, keyboard operable, links from search results only via `safeHref` with `rel="noopener noreferrer"`.

## Facts you may use (with links)

- OpenAI Business Terms §3.3(e) and Anthropic Commercial Terms §D.4(a) forbid training competing models on their outputs: https://openai.com/policies/business-terms/ , https://www.anthropic.com/legal/commercial-terms
- Nemotron 3 Ultra is released under OpenMDW-1.1, which places no restrictions on outputs: https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Ultra-550B-A55B-BF16
- NVIDIA Research: 40–70% of LLM calls in agent systems could be handled by small models, which are 10–30x cheaper to serve: https://arxiv.org/html/2506.02153v1
- Menlo Ventures: enterprises spent $12.5B on model APIs in 2025 (of $37B generative AI spend): https://menlovc.com/perspective/2025-the-state-of-generative-ai-in-the-enterprise/
- OpenAI stops new self-serve fine-tuning jobs after Jan 6, 2027: https://developers.openai.com/api/docs/deprecations
- Buyers rank proving ROI (43%) far above price (7%): https://www.nops.io/blog/state-of-tokenomics-2026/
- Nebius Serverless AI Builders Challenge: the second-place project (an evaluation dashboard) found that "self-hosting only wins on cost at high utilization": https://nebius.com/blog/posts/serverless-ai-builders-challenge-winners
- Release gate numbers (from `ratchet/release.py`): golden precision ≥ 95%, coverage ≥ 25%, no regression vs live beyond 2 points; shadow needs ≥ 100 would-serve answers; canary 10% → 50% → 100% with ≥ 20 audits per stage; rollback when the 95% Wilson upper bound of audited precision < 92%.
- Model roles: teacher `nvidia/Nemotron-3-Ultra-550b-a55b` (Token Factory), judge `nvidia/nemotron-3-super-120b-a12b`, fallback student `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` (Token Factory), student `nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16` + LoRA (vLLM on a Nebius Serverless Endpoint), Night School on Nebius Serverless Jobs, storage on Nebius Object Storage, search by Tavily.
