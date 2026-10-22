# Creative brief (v2)

## The product in one line

An OpenAI-compatible gateway that moves repetitive requests from a rented frontier model (Nemotron 3 Ultra) to a LoRA adapter the customer owns on NVIDIA's open Nano base, with a statistical release gate so quality only moves forward.

## What changed from v1

The owner's verdict on v1 was "not nearly ambitious enough". v2 keeps every word of truth v1 earned (the adapter wording, the labelled simulation numbers, the trust claims held to the code) and rebuilds the page as a developer-infrastructure site with a motion system, depth and a distinct visual signature per section. The bar is Linear, Vercel, Resend and Raycast: generous whitespace, one type scale, layered backgrounds, effects that serve the story and never run when nobody is looking.

## Audience

1. Hackathon judges (Nebius x NVIDIA): minutes, not hours. They should feel the quality on the first screen and find the proof (files, invariants, commands) without hunting.
2. A platform engineer deciding whether to try it: integration cost, failure modes, evidence.

## Positioning

- Headline: **"Stop paying frontier prices for repetitive work."** Unchanged; now it rises word by word (SplitText) over the aurora.
- What the customer keeps, stated exactly: a Nemotron adapter they own (LoRA on NVIDIA's open Nano base) and its signed bill of materials. Never "a model you own" (tests/test_docs_claims.py).
- Integration cost on the first screen: one changed line, `base_url`; and again, in three languages, before the closing call to action.
- The ratchet metaphor stays a few deliberate moments: the mark's click, the hero flow's wheel, the pawl in chapter 04, the Lamp over the gates.

## The arc (the page, top to bottom)

| # | Section | Signature | What it proves |
|---|---|---|---|
| | Hero | Aurora wash in the two story colours, SplitText headline, ShinyText eyebrow, Moving Border call to action, Magnet on the second, the hero flow on a TiltCard in a pool of light | The one-line change, the adapter wording, the loop in motion (Simulation) |
| | Live strip | Glass panel, a line of light along its edge, numbers that count up, the mode badge (Simulation vs Live) | The gateway's real numbers right now |
| 01 | The rent | Highlight on "rent", rented glow orbs, SpotlightCards on the two sourced facts, the call grid, and a **Compare slider**: the same 2,000 tickets billed all-Ultra and on day 10 of the reference run | Two linked third-party facts; simulation numbers from `sim.ts`, labelled |
| 02 | The catch | Sunken band, GlareHover sweeps over the license check and the BOM example | Closed teachers refused in code (`ratchet/licenses.py`) |
| | Why it is different | Dot field, drifting orbs, a **Bento grid** of six SpotlightCards with a miniature drawing each, every card naming its files and invariant | `why-model.ts`, held to the repo by `web/tests/why-model.test.mjs` |
| 03 | How it turns | **Tracing Beam** down the four steps; the sticky diagram's background tints with the active step (the sticky-scroll idea) | The cascade and Night School, step by step |
| | Follow one request | The explorer, unchanged in substance (its model is tested) | Stages, rules and files are the real ones |
| 04 | One way only | **Lamp** over the title, the pawl, four gate SpotlightCards, an Aceternity-style **Timeline** of the release stages with the gate's defaults, the rollback story that glows red when the rollback fires | `ReleasePolicy` defaults via `LIFECYCLE_NUMBERS`, tested against `ratchet/release.py` |
| 05 | Watch it learn | The day written 24rem tall behind the chapter, following the scrubber | The reference run, labelled Simulation |
| 06 | The honest bill | Unchanged data flow (`GET /ratchet/cost` or the reference report) | Break-even, never a promise below it |
| 07 | Built to be checked | Highlight on "checked", good-tone orbs, the four claims as SpotlightCards | `trust-model.ts`, held to the code by the contract test |
| 08 | Agent traffic | Orbs, five agent cards on TiltCards, Magnet on the call to action | Which Scout calls are distilled, which are pinned |
| 09 | Nebius + NVIDIA | A HoverGrid whose background slides between roles | Every model role and where it runs |
| | Integration | Highlight on "Name the task", code tabs (Python, Node, curl) with copy, the `response.ratchet` block row by row | docs/API.md's response fields |
| 10 | Run it | Quick start, three true counts (1,815 tests offline, 26 invariants, Apache-2.0), the closing card with **Background Beams**, Text Generate and the Moving Border button | The repo as it is |

## Principles

- **Proof over promise.** Every number is live, a labelled simulation from the pinned reference run, or a linked third-party fact. An illustration says it is one. No testimonials, logos, counts or avatars that are not true.
- **Show the mechanism.** The explorer, the timeline and the bento cards each name the file that does the thing.
- **Motion with a reason.** One motion system (`src/lib/motion.ts`): reveals on scroll, responses to the reader, and ambient loops that stop off screen, behind another tab and under reduced motion. Nothing moves to get attention.
- **Depth, not noise.** Layered backgrounds (aurora, orbs, dots, grid, lamp, beams) sit behind content at low alpha; cards get a spotlight and a glare only when the pointer asks.
- **Plain words.** Short sentences, no hype words (web/DESIGN.md "Voice").

## Out of scope

Changes to Python, tests, the simulation numbers in `src/lib/sim.ts`, the chapter ids, or the Scout and dashboard pages beyond the shared badge and code-token colours.
