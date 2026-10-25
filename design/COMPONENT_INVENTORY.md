# Component inventory (v2)

Added or changed in this pass. Everything else is listed in `web/AGENTS.md` and `web/DESIGN.md`.

## Effects: `web/src/components/fx/`

| Component | File | Status | Notes |
|---|---|---|---|
| `Aurora` | `aurora.tsx` | new, original | WebGL2 fragment shader (value noise, two curtains, one colour per pixel from the `--owned`/`--rented` tokens, pastel on light surfaces); draws only on screen and tab visible; one still frame under reduced motion; dpr capped at 1.5; CSS gradient fallback without WebGL2; recolours on theme change (MutationObserver on `<html class>`); lazy-loaded (2.5 kB gzip) |
| `SplitText` | `split-text.tsx` | new, original | words in clipped boxes rising on mount, per-segment classes, `sr-only` sentence |
| `BlurText` | `blur-text.tsx` | new, original | words blur into focus on `whileInView`; used by `ChapterHead` for every string title |
| `ShinyText` | `shiny-text.tsx` | new, original | CSS clipped-gradient shine (`.shiny-text`, keyframes `shine`); off under reduced motion |
| `SpotlightCard` | `spotlight-card.tsx` | new, original | pointer-driven CSS variables (`--sx`, `--sy`, `--so`) for a glow and a masked border ring; `tone` picks the colour; centred light on `:focus-within`; mouse only |
| `TiltCard` | `tilt-card.tsx` | new, original | perspective tilt on `useSpring`, mouse only, off under reduced motion |
| `Magnet` | `magnet.tsx` | new, original | a few pixels toward the pointer on springs; mouse only; off under reduced motion |
| `GlareHover` | `glare-hover.tsx` | new, original | one CSS sweep on hover or focus-within (`.glare`) |
| `GlowOrbs` | `glow-orbs.tsx` | new, original | two or three radial-gradient pools drifting on CSS transforms, `orbs-on` only while on screen |
| `MovingBorderLink` | `moving-border.tsx` | ported (Aceternity Moving Border) | a router link; the light rides an SVG rect path; its own frame loop runs only on screen; still gradient ring under reduced motion |
| `TracingBeam` | `tracing-beam.tsx` | ported (Aceternity Tracing Beam) | ResizeObserver height, story colours, `lineClassName`/`contentClassName`, full line under reduced motion |
| `Timeline` | `timeline.tsx` | ported (Aceternity Timeline) | `items: {title, sub, content, tone}`, sticky titles, scroll-filled line, full line under reduced motion |
| `Lamp` | `lamp.tsx` | ported (Aceternity Lamp) | both themes (fades into `--bg`), smaller, `whileInView` once |
| `Compare` | `compare.tsx` | ported (Aceternity Compare) | any content; one range input over the card for mouse, touch and keyboard; `aria-valuetext` |
| `BentoGrid`, `bentoSpan` | `bento-grid.tsx` | ported (Aceternity Bento Grid) | a `ul`, 6-column desktop grid |
| `TextGenerate` | `text-generate.tsx` | ported (Aceternity Text Generate Effect) | `whileInView`, no blur under reduced motion, `sr-only` sentence |
| `BackgroundBeams` | `background-beams.tsx` | ported (Aceternity Background Beams) | 18 fixed paths and timings, story colours, lights only on screen, still lines under reduced motion |
| `Highlight` | `highlight.tsx` | ported (Aceternity Hero Highlight's `Highlight`) | marker-pen grow on `whileInView`, `owned`/`rented` tones |
| `HoverGrid` | `hover-grid.tsx` | ported (Aceternity Card Hover Effect) | generic items and renderer, `layoutId` background, focus moves it |

## Shared libraries

| Module | File | Status | Notes |
|---|---|---|---|
| motion system | `web/src/lib/motion.ts` | new | `EASE`, `DUR`, `rise`, `enter`, `list` |
| visibility | `web/src/lib/visibility.ts` | moved from `shared.tsx` | `useElementWidth`, `useOnScreen` (re-exported by `shared.tsx`) |

## Story page

| Component | File | Status | Notes |
|---|---|---|---|
| `Hero`, `BaseUrlDiff` | `sections/hero.tsx` | rewritten | aurora, SplitText, ShinyText eyebrow with the mark, Moving Border and Magnet calls to action, the flow on a TiltCard in a pool of light; the adapter wording unchanged |
| `LiveStrip` | `sections/live-strip.tsx` | restyled | glass, edge light, `CountUp` figures, live dot |
| `Rent`, `TwoBills`, `Bill` | `sections/rent.tsx` | extended | Highlight, orbs, SpotlightCards, the Compare of two bills from `SIM_DAYS` |
| `Catch` | `sections/catch.tsx` | restyled | SpotlightCards under GlareHover |
| `Why` | `sections/why.tsx` | new | bento of six SpotlightCards with miniature drawings; data in `why-model.ts` |
| `WHY` | `why-model.ts` | new | six items: title, body, files, invariant `{n, says}`, `wide`; no JSX |
| why test | `web/tests/why-model.test.mjs` | new | files exist; each cited invariant's bold sentence contains the quoted words; ids distinct; two wide cards |
| `How` | `sections/how.tsx` | extended | TracingBeam, per-step tints behind the sticky diagram |
| `Safety`, `STAGES` | `sections/safety.tsx` | rewritten | Lamp, SpotlightCards, the Timeline of release stages from `LIFECYCLE_NUMBERS`, the rollback story |
| `RollbackStory` | `sections/rollback-story.tsx` | tweaked | red glow and border once rolled back; the traffic bar's colours moved to `owned-ink`/`owned-soft` for contrast |
| `Learn` | `sections/learn.tsx` | extended | the day watermark |
| `Checked` | `sections/checked.tsx` | restyled | Highlight, orbs, SpotlightCards; every claim, route, command and invariant string unchanged |
| `Scout` | `sections/scout.tsx` | extended | TiltCards, orbs, Magnet |
| `Stack` | `sections/stack.tsx` | rewritten | HoverGrid over the roles |
| `Integrate` | `sections/integrate.tsx` | new | tabs for Python, Node and curl, the `response.ratchet` rows (docs/API.md), Highlight, orbs |
| `OpenSource` | `sections/open-source.tsx` | extended | the three counts, Background Beams, TextGenerate, Moving Border, Magnet |
| `ChapterRail` | `rail.tsx` | redesigned | track, dots, scroll-progress fill; same ids |
| `ChapterHead` | `shared.tsx` | changed | string titles render as `BlurText`; `EASE` re-exported from `lib/motion` |
| `LandingPage` | `index.tsx` | changed | order: hero, live strip, rent, catch, why, how, lifecycle, safety, learn, bill, trust, scout, stack, integrate, run; `OneLineChange` removed (its content lives in `Integrate`) |

## Shared UI touched

| Component | File | Change |
|---|---|---|
| `Badge` | `web/src/components/ui/badge.tsx` | `good` and `warn` tones use `text-good-ink` / `text-warn-ink` |
| `tok.s` | `web/src/components/ui/code-block.tsx` | strings in `text-good-ink` |
| tokens | `web/src/styles/globals.css` | `--good-ink`, `--warn-ink`; the effects' CSS (`.aurora-fallback`, `.shiny-text`, `.spotlight-*`, `.glare*`, `.orb*`, `.edge-light`, `.live-dot`, `.bg-dots`) |

Reused, not changed: `HeroFlow` (loop now stops when paused, off screen or hidden instead of ticking idle), `HowDiagram`, `Lifecycle`, `HonestBill`, `CostChart`, `Logo`, `Button*`, `CodeBlock`, `Container`, `Eyebrow`, `H2`, `Lead`, `WhoBadge`.
