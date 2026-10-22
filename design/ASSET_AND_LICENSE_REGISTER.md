# Asset and license register (v2)

No dependency was added: every effect is TypeScript in `web/src/components/fx/` on the libraries already in `web/package.json` (`motion`, `react`, `react-router`, `clsx`, `tailwind-merge`, Tailwind v4). No external script, font, image, CDN or iframe loads at runtime. No company logo is drawn. The `ogl` package the brief allowed was not needed: the aurora is a 5 kB WebGL2 shader of its own.

## Why React Bits code was not copied

React Bits (https://github.com/DavidHDev/react-bits) is licensed **MIT + Commons Clause** (LICENSE.md, read Oct 22, 2026), which forbids redistributing the components "whether alone, in a bundle, or as a ported version". Vendoring a port into this Apache-2.0 repository would be such a redistribution. Its current SplitText also depends on GSAP. So the five React Bits picks were written here from scratch, after the idea only (what the effect looks like, which any site may do), with no React Bits source in this repo. The fetched originals were read for reference and discarded.

| Idea from React Bits | URL (read) | Our file | License of ours |
|---|---|---|---|
| Aurora background | https://www.reactbits.dev/backgrounds/aurora (source: `src/ts-tailwind/Backgrounds/Aurora/Aurora.tsx`) | `fx/aurora.tsx`: a different shader (value-noise curtains, token colours, light-mode pastel), raw WebGL2 | Apache-2.0 with the repo |
| SplitText | https://www.reactbits.dev/text-animations/split-text | `fx/split-text.tsx`: `motion` word stagger, no GSAP | Apache-2.0 |
| BlurText | https://www.reactbits.dev/text-animations/blur-text | `fx/blur-text.tsx`: `motion` variants on `whileInView` | Apache-2.0 |
| ShinyText | https://www.reactbits.dev/text-animations/shiny-text | `fx/shiny-text.tsx` + `.shiny-text` CSS keyframes | Apache-2.0 |
| SpotlightCard | https://www.reactbits.dev/components/spotlight-card | `fx/spotlight-card.tsx`: CSS variables, masked ring | Apache-2.0 |
| TiltedCard | https://www.reactbits.dev/components/tilted-card | `fx/tilt-card.tsx`: `useSpring` tilt | Apache-2.0 |
| Magnet | https://www.reactbits.dev/animations/magnet | `fx/magnet.tsx` | Apache-2.0 |
| GlareHover | https://www.reactbits.dev/animations/glare-hover | `fx/glare-hover.tsx` + `.glare` CSS | Apache-2.0 |
| animated backgrounds (Particles, Silk) | https://www.reactbits.dev/backgrounds/particles | `fx/glow-orbs.tsx`: CSS-only drifting pools instead of a canvas | Apache-2.0 |

## Ported from Aceternity UI (adapted)

Fetched from the Aceternity registry (`https://ui.aceternity.com/registry/<name>.json`, Oct 22, 2026), the same files the site's "Install" copies. Aceternity UI describes its free components as free to copy into projects, and they are widely redistributed as MIT; the site's "Licence" page (https://ui.aceternity.com/licence) covers only the paid Pro items, and no license text ships inside the registry JSON, so the MIT status rests on the site's statement rather than a file in this repo. **Risk noted for the owner**: if a per-file license is wanted, the author (Manu Arora, Aceternity) should be asked for it; every port below is marked in its header comment and could be replaced by an original in an hour each. Each port keeps the structure of the original and changes what the table says.

| Component | Source URL | Our file | Modifications |
|---|---|---|---|
| Moving Border | https://ui.aceternity.com/components/moving-border | `fx/moving-border.tsx` | a `react-router` link, the story's colours, its own `requestAnimationFrame` loop that runs only on screen and tab visible (instead of motion's permanent `useAnimationFrame`), a still gradient ring under reduced motion, no `any` |
| Tracing Beam | https://ui.aceternity.com/components/tracing-beam | `fx/tracing-beam.tsx` | a straight line instead of the bent path, token colours, ResizeObserver for the height, `lineClassName`/`contentClassName`, full line under reduced motion |
| Timeline | https://ui.aceternity.com/components/timeline | `fx/timeline.tsx` | generic items with a tone, no page chrome, token colours, ResizeObserver, full line under reduced motion |
| Lamp | https://ui.aceternity.com/components/lamp-effect | `fx/lamp.tsx` | works on both themes (fades into `--bg`), smaller height, `whileInView` once, the glow in `--owned` |
| Compare | https://ui.aceternity.com/components/compare | `fx/compare.tsx` | arbitrary content instead of two images; the Sparkles and the Tabler icon dropped; a real `<input type="range">` over the card for mouse, touch and keyboard |
| Bento Grid | https://ui.aceternity.com/components/bento-grid | `fx/bento-grid.tsx` | a `ul`, token classes, the card left to the caller; the `@tabler/icons-react` dependency not used |
| Text Generate Effect | https://ui.aceternity.com/components/text-generate-effect | `fx/text-generate.tsx` | `whileInView` variants instead of `useAnimate` on mount, reduced motion, `sr-only` sentence |
| Background Beams | https://ui.aceternity.com/components/background-beams | `fx/background-beams.tsx` | 18 generated paths instead of 50 literals, deterministic timings instead of `Math.random`, token colours, lights only while on screen, still lines under reduced motion |
| Hero Highlight (`Highlight` only) | https://ui.aceternity.com/components/hero-highlight | `fx/highlight.tsx` | `whileInView`, token soft colours; the dot-pattern container and its `mini-svg-data-uri` dependency not used (our `.bg-dots` is a CSS radial gradient) |
| Card Hover Effect | https://ui.aceternity.com/components/card-hover-effect | `fx/hover-grid.tsx` | generic items and renderer, a `ul`, token colours, focus moves the background |

Not used although fetched: Sticky Scroll Reveal (its idea, a background that follows the active step, is reimplemented in `sections/how.tsx` with four tinted layers), Spotlight, Spotlight New.

## Original work in this repo (Apache-2.0 with the code)

| Asset | File | Notes |
|---|---|---|
| Ratchet mark and wordmark, favicon, logo click | `web/src/components/ui/logo.tsx`, `web/public/favicon.svg`, `web/src/styles/globals.css` | from v1 |
| The miniature drawings in the bento cards, the two bills, the day watermark, the live strip's edge light | `sections/why.tsx`, `sections/rent.tsx`, `sections/learn.tsx`, `globals.css` | this pass |

## Bundled dependencies the new work uses (unchanged versions)

| Package | Version | License | Used for |
|---|---|---|---|
| `motion` | 14.4.0 | MIT | reveals, springs, scroll progress, layout ids |
| `react-router` | 8.5.0 | MIT | the Moving Border link |
| `lucide-react` | 1.59.0 | ISC | icons |
| `@fontsource-variable/inter`, `@fontsource-variable/jetbrains-mono` | 5.3.0 | OFL-1.1 | fonts |
| `clsx`, `tailwind-merge`, `class-variance-authority` | as before | MIT | class names |

## Third-party facts and names

Product names (NVIDIA Nemotron, Nebius Token Factory, OpenAI SDK, Tavily) appear as text only. The two sourced facts in chapter 01 (Menlo Ventures, NVIDIA Research) and the Nebius quote in chapter 06 keep their links. Every source file named on the page links to this repository on GitHub.
