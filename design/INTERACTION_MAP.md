# Interaction map (v2)

What a visitor can do on the story page, and what responds. New or changed in v2 marked **v2**.

## Global (header, footer, rail)

| Control | Behaviour |
|---|---|
| Logo (link home) | clicks one tooth forward once per page load (none with reduced motion) |
| Nav, theme toggle, header call to action | unchanged; the theme is the only thing kept in browser storage |
| **v2** Chapter rail (xl and up) | a track with one dot per chapter and a fill that grows with scroll progress; the active chapter's dot and number turn blue; labels show on hover (and always from 1600px); each dot links to its chapter's id |

## Story (`/`)

| Area | Interaction |
|---|---|
| Hero | **v2** the aurora drifts while the hero is on screen; the headline's words rise; "Copy" copies the changed line (`aria-live` "Copied"); "Try the Scout demo" (Moving Border) → `/scout`; "Open the dashboard" (Magnet, leans toward the mouse) → `/dashboard`; the hero flow card tilts up to 4° toward the mouse; pause/play on the flow; "Watch it learn" → `#learn`; GitHub link |
| Live strip | **v2** numbers count up once when scrolled to; a line of light runs along the top edge; "Open the dashboard" |
| 01 The rent | **v2** the Compare slider: drag the handle with a mouse or finger, or focus it and use the arrow keys (one real `<input type="range">` over the card; `aria-valuetext` says the split); SpotlightCards light under the pointer |
| 02 The catch | **v2** a glare sweeps each card on hover or when a link inside takes focus |
| Why it is different (`#why`) | **v2** six SpotlightCards; each file link opens GitHub in a new tab |
| 03 How it turns | scroll-driven steps; **v2** the beam lights as far as the reader has scrolled and the diagram's tint follows the step |
| Follow one request (`#lifecycle`) | unchanged: scenario radios, stage buttons with roving focus, Previous/Next, "Walk the path", a polite live region |
| 04 One way only | **v2** the Lamp opens as the chapter scrolls in; the pawl plays once; the Timeline's line fills with scroll, each stage title sticking while its content passes; the rollback story replays on "Replay" and the card glows red once the rollback fires |
| 05 Watch it learn | scrubber, play/pause, click a day on the chart; **v2** the day is written large behind the chapter and changes with the scrubber |
| 06 The honest bill | unchanged: the live/reference toggle, the meter, the report |
| 07 Built to be checked | unchanged links and commands; **v2** cards as SpotlightCards |
| 08 Agent traffic | **v2** agent cards tilt toward the mouse; the call to action leans (Magnet) |
| 09 Nebius + NVIDIA | **v2** the hover background slides from role to role; keyboard focus moves it too |
| Integration (`#integrate`) | **v2** language tabs (`role="tablist"`: Left/Right/Home/End move and select, the panel is labelled by its tab); "Copy" copies the whole snippet; the response rows stagger in |
| 10 Run it | "Copy" on the quick start; **v2** the closing card's beams run only while it is on screen; the Moving Border and Magnet buttons |

## Keyboard and screen readers

- Every effect is either `aria-hidden` decoration (aurora, orbs, beams, lamp, glare, watermark, the Moving Border light) or has a plain control underneath (the Compare's range input, the tabs, the explorer's buttons).
- Split, blur and generated text render the sentence once for assistive technology (`sr-only`) and the animated words as decoration.
- A SpotlightCard with a focused link shows a centred light, so focus is never shown by colour alone (the global focus ring still applies).
- Checked in Chromium: Tab reaches the Compare slider and ArrowRight moves it; ArrowRight and End move the integration tabs and their panel; the rail's links name chapters that exist (FRONTEND_VERIFICATION.md).

## Scout (`/scout`) and dashboard (`/dashboard`)

Unchanged behaviour. Visual: the `good` and `warn` badges and code strings use the new `-ink` shades in light mode (AA contrast).
