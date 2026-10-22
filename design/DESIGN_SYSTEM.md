# Design system (as used, v2)

The source of truth is `web/src/styles/globals.css` (tokens, effects) and `web/DESIGN.md` (rules). This page lists what the story page uses after the v2 pass.

## Colour tokens

CSS variables on `:root` (light) and `.dark`, exposed to Tailwind as `bg-*`, `text-*`, `border-*`.

| Token | Light | Dark | Meaning |
|---|---|---|---|
| `bg` / `bg-sunken` | #fbfaf7 / #f3f1ec | #0b0b0c / #070708 | page, recessed bands |
| `surface` / `surface-2` | #ffffff / #f7f6f2 | #141416 / #1a1a1d | cards, hover |
| `ink` / `ink-2` / `muted` | #121212 / #3d3c39 / #64625d | #f2f1ec / #d6d5cf / #a4a39c | text, strongest to weakest |
| `line` / `line-strong` | #e6e3dc / #d3cfc6 | #26262a / #34343a | borders, connectors |
| `owned` / `owned-ink` / `owned-soft` | #2a78d6 / #1c5cab / #e3eefc | #3987e5 / #86b6ef / 14% | the student, the adapter you own |
| `rented` / `rented-ink` / `rented-soft` | #eb6834 / #b4461a / #fdeae1 | #d95926 / #f29a72 / 16% | the teacher you rent |
| `good` / **`good-ink`** / `good-soft` | #1f8f3a / **#17722e** / #e3f4e7 | #51cf66 / #51cf66 / 14% | state only, always with a word or icon |
| `warn` / **`warn-ink`** / `warn-soft` | #a86400 / **#8a5200** / #fbf0dc | #f0b13f / #f0b13f / 14% | state only |
| `bad` / `bad-soft` | #c92a2a / #fbe4e4 | #ff6b6b / 14% | state only |
| `focus` | #2a78d6 | #86b6ef | `:focus-visible` outline |

New in v2: `good-ink` and `warn-ink`, the darker shades small text on a soft fill needs for 4.5:1 in light mode. The `good` and `warn` badge tones and the code-token string colour (`tok.s`) now use them, on every page. The automated contrast pass (FRONTEND_VERIFICATION.md) found no pair under AA on the story page in either theme.

## Type

- Inter Variable (sans) and JetBrains Mono Variable (mono), bundled.
- Hero: 2.6rem on phones to 5rem at xl, weight 600, tracking -0.04em, leading 1.03, words rising one by one.
- H2 (chapter titles): 1.875rem to 2.75rem, tracking -0.025em, words blurring into focus on scroll (`BlurText`), or a plain `H2` when the title holds a `Highlight`.
- Body 15 to 18px, `text-pretty`, about 64ch. Eyebrows: mono 11 to 12px, uppercase, tracking 0.12 to 0.14em.
- Big figures: 40 to 52px, tracking -0.04em, tabular numerals. The "Watch it learn" watermark: 11 to 24rem mono at 4 to 4.5% ink.

## Shape, depth, space

- Radius: large cards 22 to 24px, panels 16px, chips 12px, pills full, the closing card 28px.
- Depth comes in layers, back to front: a section background (grid, dots, aurora, orbs, lamp, beams, a sunken band), a glow pool under a featured card, the card's border and `shadow-card`, then the spotlight and glare on hover.
- Borders react: a SpotlightCard's border brightens where the pointer is; a HoverGrid cell's background slides to the hovered cell; the rollback story's border turns `bad` when the rollback fires.
- Container: max-w-6xl with 16px phone gutters. Chapters py-16 to py-24. No horizontal scroll at 360px (checked).

## Motion system (`web/src/lib/motion.ts`)

- One ease, `EASE = [0.22, 1, 0.36, 1]`; durations `fast` 0.25s, `base` 0.55s, `slow` 0.85s.
- `rise()` for anything that enters on scroll (fade + 18px), `enter()` for the hero (on mount), `list` variants for staggered lists (70ms).
- Choreography: the hero's words rise (50ms apart), then the lead, the diff, the buttons and the flow card in sequence; every chapter title blurs into focus word by word; cards stagger by column.
- Three kinds of motion, and nothing else: a one-time reveal, a response to the reader (hover, focus, drag, a tab, the scrubber), or an ambient loop that runs only while its element is on screen and the tab is visible (`useOnScreen`): the aurora, the hero flow, the Moving Border light, the Background Beams, the drifting orbs, the live strip's edge light, the shine on the eyebrow.
- Reduced motion: `MotionConfig reducedMotion="user"` plus the base CSS rule. The aurora draws one still frame; the beams, orbs, shine and edge light stand still; the Moving Border shows a still gradient ring; the split and blur texts simply appear; the Compare, tabs and scrubber work the same. Measured: 0 animation frames a second on the hero under reduced motion, and 0 with the tab hidden.

## Effects (web/src/components/fx)

| Component | Origin | Used in |
|---|---|---|
| `Aurora` | original WebGL2 shader (after React Bits' Aurora idea), lazy-loaded, 2.5 kB gzip | hero |
| `SplitText`, `BlurText`, `ShinyText` | original (after React Bits' ideas) | hero headline, every chapter title, hero eyebrow |
| `SpotlightCard`, `TiltCard`, `Magnet`, `GlareHover`, `GlowOrbs` | original (after React Bits' and Aceternity's ideas) | cards across the page, hero flow and agent cards, secondary buttons, the catch cards, section backgrounds |
| `MovingBorderLink`, `TracingBeam`, `Timeline`, `Lamp`, `Compare`, `BentoGrid`, `TextGenerate`, `BackgroundBeams`, `Highlight`, `HoverGrid` | ported from Aceternity UI, adapted (ASSET_AND_LICENSE_REGISTER.md) | primary buttons, chapter 03, the release stages, chapter 04, the two bills, why it is different, the closing line, the closing card, title words, the stack |

Every effect is decoration over content that reads the same without it.

## The mark

Unchanged from v1: `web/src/components/ui/logo.tsx`, 32-unit grid, eight teeth, the pawl in `rented`, one click per page load. It now also sits at 20px in the hero's eyebrow pill and at 48px over the closing card.
