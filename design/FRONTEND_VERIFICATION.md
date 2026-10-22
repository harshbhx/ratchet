# Frontend verification (v2)

Run on Oct 22, 2026 in this worktree (branch `design/landing-v2`, base `a7bff80`), Node 22.22.0, Python 3.13, Chromium 1194 (Playwright 1.56.1 from `/opt/node-tools`, `PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers`), against the mock gateway: `RATCHET_MODE=mock RATCHET_MOCK_WARMUP_DAYS=4 python -m uvicorn ratchet.gateway:app --port 8766`.

## Build and tests

| Command (in `web/` unless noted) | Result |
|---|---|
| `npm ci` | 0 vulnerabilities; **no dependency added or changed** |
| `npx tsc -b` | clean |
| `npm test` | 40 pass, 0 fail (37 before, plus 3 in `tests/why-model.test.mjs`) |
| `npm run build` | writes `ratchet/web/dist`; committed |
| repo root: `python -m pytest -q -p no:cacheprovider tests/test_contract.py tests/test_docs_claims.py tests/test_security.py` | 78 passed |
| repo root: `python -m pytest --collect-only -q` | 1815 tests (the figure the closing chapter prints) |

## Bundle (gzip)

| Chunk | Before (a7bff80) | After |
|---|---|---|
| `landing-*.js` (the story page) | 148.20 kB / **42.76 kB** | 204.59 kB / **59.58 kB** (+16.8 kB gzip for 19 effects, the bento, the timeline, the compare, the tabs) |
| `aurora-*.js` (lazy, after the hero paints) | – | 5.42 kB / **2.55 kB** |
| `index-*.css` | 79.48 kB / 16.94 kB | 96.59 kB / 19.79 kB |
| `index-*.js` (app shell), `dashboard-*.js`, `scout-*.js`, `LineChart-*.js` (lazy) | unchanged within 0.1 kB | |

No new package: `ogl`, GSAP, `@tabler/icons-react` and `mini-svg-data-uri` were all avoided.

## Browser checks (Playwright scripts kept outside the repo)

| Check | Result |
|---|---|
| Horizontal overflow at 360 x 780, `/`, `/scout`, `/dashboard`, light and dark, scrolled top to bottom in 700px steps | `scrollWidth - innerWidth = 0` on all six |
| Console errors and page errors on every page, both themes, every screenshot run | none |
| Animation frames a second on the hero, measured over 1.5 s with a wrapped `requestAnimationFrame` (Chromium headless, so the absolute numbers are low) | hero on screen: 37; scrolled 2,600px down (hero off screen, springs settled): **0**; back on screen: 35; tab hidden (`document.hidden` forced, `visibilitychange` dispatched): **3**, then 37 again when visible |
| Reduced motion (`reducedMotion: "reduce"`) | hero frames a second: **0**; `.shiny-text` and the logo have `animation-name: none`; the headline is at full opacity and height; no Moving Border light is rendered; the aurora draws one still frame |
| Aurora | a 1440 x 640 WebGL2 canvas, not the CSS fallback |
| Keyboard: the Compare slider | Tab reaches the range input; ArrowRight twice moves it 58 → 60; `aria-valuetext` "60% All-Ultra, 40% With Ratchet · day 10" |
| Keyboard: the integration tabs | from "Python", ArrowRight selects and focuses "Node" and the panel shows the Node snippet; End selects "curl" |
| Rail and anchors | every rail link names a section that exists; `#lifecycle`, `#why`, `#integrate`, `#trust`, `#bill`, `#learn` all resolve (the cross-page links `/dashboard#redactor` are unchanged) |
| Text contrast, computed colours with alpha composited against the nearest opaque ancestor (Tailwind v4 reports `oklab()`, converted), 411 text nodes in the hero, live strip, chapters 01, 04, 07, 10, the bento and the integration tabs, light and dark | **0 pairs under WCAG 2.2 AA** (4.5:1, 3:1 for large text). Before the token fix, nine light-mode pairs failed: the `good`/`warn` badge text on their soft fills (3.64, 4.14), `tok.s` strings (4.16) and two spans in the rollback story's traffic bar (4.42, 3.73); fixed with `--good-ink`, `--warn-ink` and `owned-ink`/`owned-soft` |

## Screenshots

`design/screenshots/v2/` (38 PNGs, quantised to 256 colours, 1.65 MB in all), 1x, taken after one scroll through the page so every reveal had fired:

- Hero: `landing-hero-{1440,390}-{light,dark}.png`, `reduced-motion-hero-1440-light.png`
- 01 and the two bills: `landing-rent-1440-{light,dark}.png`, `landing-compare-1440-{light,dark}.png`, `landing-rent-390-dark.png`, `landing-compare-390-dark.png`
- 02: `landing-catch-1440-light.png`
- Why it is different: `landing-why-1440-{light,dark}.png` (whole section), `landing-why-390-light.png`
- 03: `landing-how-1440-{light,dark}.png`, `landing-how-beam-1440-{light,dark}.png` (step 2, the beam and the tint)
- Explorer: `landing-lifecycle-1440-light.png`
- 04: `landing-safety-1440-{light,dark}.png` (the lamp and the gates), `landing-timeline-1440-{light,dark}.png`, `landing-safety-390-light.png`
- 05: `landing-learn-1440-dark.png`; 06: `landing-bill-1440-dark.png`; 07: `landing-trust-1440-{light,dark}.png`; 08: `landing-scout-1440-dark.png`; 09: `landing-stack-1440-light.png`
- Integration: `landing-integrate-1440-{light,dark}.png`, `landing-integrate-390-light.png`
- 10: `landing-run-1440-{light,dark}.png` (whole chapter)
- Regressions: `scout-1440-light.png`, `dashboard-1440-light.png`

`design/screenshots/after/` keeps three v1 shots for comparison (its README says which); the rest of v1's set and the `before/` set are in git history.

## Not checked

- Screen readers beyond the accessibility names, roles and live regions in the DOM (no VoiceOver or NVDA run).
- Safari and Firefox: only Chromium is installed. `color-mix()`, `mask-composite`, `backdrop-filter` and WebGL2 are current in all three; without WebGL2 the aurora falls back to a CSS gradient.
- Real CPU figures: headless Chromium has no DevTools performance panel here. The frame counts above show the loops stop; the aurora is one full-screen fragment shader at a pixel ratio of at most 1.5, the orbs are compositor-only transforms, the beams are 18 SVG gradient animations that unmount off screen.
