# Components plan: which page uses which component

A follow-up pass applies the components in [`README.md`](README.md#components) to the content pages once the docs branches
are merged. This file says, page by page, what to add, where, and the exact MDX. Every number in a snippet below is already
on that page or in the code (release gate defaults from `ratchet/release.py`, the test count from `AGENTS.md`); keep it that
way when a page changes. Do not invent numbers; a simulation number gets `tone="sim"`.

## Ground rules for the pass

- **Keep each guide's shape**: `<Summary>`, `<PlainWords>` (or the existing `<Eli10>`), "How it really works", "For
  engineers", "Common questions". Components support the text; they never replace a paragraph that explains something.
- **No motion inside running text.** In guides use `<Stat>`, `<Flow>`, `<Compare>`, `<PathCard>`, `<Stepper>` and the CSS
  callouts. `<BlurText>`, `<FadeContent>`, `<ShinyText>` and `<LogoLoop>` belong on landing-style pages (the home page).
  `<GradientText>` at most once per page, in a lead or closing line, never inside a `##` heading.
- **`##` and `###` stay Markdown headings** so they feed "On this page" and the anchor links.
- **Diagrams are Mermaid** (owned by the diagram pass). Put a `<Flow>` or a `<Stepper>` next to a diagram only when it adds
  a reading order the diagram does not have; never instead of it.
- **Colour meaning**: `tone="owned"` only for the student and what you own; `tone="rented"` only for the teacher.
- **One import block** at the top of each page, after the Starlight imports. From `src/content/docs/guides/*.mdx` the paths
  are `../../../components/...`; from `src/content/docs/api/*.mdx` the same.
- Replace `import Eli10 from '../../../components/Eli10.astro'` with `PlainWords` only if a page is being edited anyway; both
  work and look the same.

Common imports (copy only the ones a page uses):

```mdx
import Stat from '../../../components/Stat.astro';
import StatRow from '../../../components/StatRow.astro';
import Flow from '../../../components/Flow.astro';
import FlowStep from '../../../components/FlowStep.astro';
import PathCard from '../../../components/PathCard.astro';
import PathGrid from '../../../components/PathGrid.astro';
import Compare from '../../../components/Compare.astro';
import Stepper from '../../../components/ui/Stepper.astro';
import CountUp from '../../../components/ui/CountUp.astro';
```

---

## Start here

### `guides/what-is-ratchet.mdx`

1. **After `<PlainWords>`/`<Eli10>`, before `## How it really works`**: three headline facts.

```mdx
<StatRow cols={3} label="Ratchet at a glance">
  <Stat value={1781} label="tests in the suite" note="They run offline, with no network and no GPU." />
  <Stat value={95} suffix="%" label="held-out precision floor" tone="owned" note="A new adapter must reach it on golden rows it never trained on." />
  <Stat value="Open teachers only" label="license-clean, in code" note="Closed models are refused as teachers." />
</StatRow>
```

2. **In `### The problem`**, after the bullet list: the before and after.

```mdx
<Compare before="Renting a large model for every call" after="With Ratchet">
  <div slot="before">Every request, repeated or not, is a paid call to the teacher. Distilling by hand is slow, and closed models forbid training on their outputs.</div>
  <div slot="after">A student you own answers what it is sure about, the teacher answers the rest, and a better student is trained every night from open teachers only.</div>
</Compare>
```

3. **In `### The idea`**, keep the seven `<Steps>` (they carry detail). Add a four-part summary above them:

```mdx
<Flow loop loopLabel="Every night, again" label="The four parts">
  <FlowStep title="The student answers first" tone="owned" meta="Nano 4B + your LoRA">Sampled 3 times; its confidence decides.</FlowStep>
  <FlowStep title="The teacher answers the rest" tone="rented" meta="Ultra on Token Factory">And checks a random 5% of the student's answers.</FlowStep>
  <FlowStep title="Answers become training data" meta="with provenance">Each with its teacher and license, after redaction.</FlowStep>
  <FlowStep title="Released through gates" meta="every night">Golden gate, shadow, canary 10%, 50%, 100%, rollback.</FlowStep>
</Flow>
```

4. **`## Related`**: replace the `<CardGrid>` of `<LinkCard>`s with a `<PathGrid cols={2}>` of `<PathCard>`s (same titles,
   links and descriptions; icons `loop`, `start`, `gate`, `check`).

### `guides/business.mdx`

1. **`## The value`**: open with a `<Compare>` (rented and owned), using the page's own sentences.
2. **`## How it differs`**: keep the table.
3. **`## Honest limits`**: keep the caution `<Aside>`.

### `guides/quickstart.mdx`

1. **After `<Eli10>`**: the three things it needs, as stats with text values.

```mdx
<StatRow cols={3} label="What mock mode needs">
  <Stat value="No keys" label="no API keys" note="Mock models stand in for the student and the teacher." />
  <Stat value="No GPU" label="runs on a laptop" />
  <Stat value="No bill" label="no paid calls" />
</StatRow>
```

2. **`### Open the pages`**: keep the table. Under it, a `<PathGrid cols={3}>` with the story (`/`), Scout (`/scout`) and the
   dashboard (`/dashboard`) is optional; the table is enough.
3. **`### Send your own request`**: keep the code tabs; add `<div class="rt-callout rt-callout--sim">` above the response
   fields table: "In mock mode every number in the response comes from mock models."

### `guides/for-judges.mdx`

1. **`### Where the demo is`**: a `<PathGrid cols={3}>` with the live site, Scout and the dashboard (icons `spark`, `search`,
   `chart`), each `href` the public URL.
2. **`### What is simulation and what is live`**: put each statement in the matching callout: `rt-callout--sim` for the
   story page's reference simulation, `rt-callout--live` only for what has a published live run. Keep the dates.
3. **`### A five-minute tour`**: a `<Stepper label="A five-minute tour">` with one step per minute row of the table (the
   table stays for print and scanning).

---

## How it works

### `guides/big-picture.mdx`

1. **`### The components`**: keep the table (it is the reference). Above it, a `<Flow>` for the request path only:

```mdx
<Flow label="One request">
  <FlowStep title="Your app" meta="any OpenAI SDK">Sends a chat request with a task name.</FlowStep>
  <FlowStep title="Gateway and cascade" meta="one process">Admission checks, then student first.</FlowStep>
  <FlowStep title="Student" tone="owned" meta="Nano 4B + LoRA">Answers when confident.</FlowStep>
  <FlowStep title="Teacher" tone="rented" meta="Ultra on Token Factory">Answers the rest.</FlowStep>
</Flow>
```

2. **`### Mock mode and live mode`**: replace nothing; add `<Compare before="Mock mode" after="Live mode" beforeTone="neutral" afterTone="neutral">` only if the table is cut to two lines per side. Otherwise keep the table.

### `guides/request-journey.mdx`

1. **`### Inside the cascade`**: add a `<Stepper label="One request through the cascade">` built from the `<Steps>` items
   (title = the bold lead of each step, body = its first sentence). Keep the `<Steps>`; the Stepper is the guided version.
2. **`### Before the cascade: admission`** and **`### Every route`**: keep the tables (12+ rows get the sticky header).

### `guides/confidence.mdx`

1. **`### Why one signal is not enough`**: the two signals side by side.

```mdx
<Compare before="Agreement across samples" after="Token probabilities" beforeTone="neutral" afterTone="owned">
  <div slot="before">The student answers 3 times. Answers that agree are a sign it knows; answers that differ are a sign it guesses.</div>
  <div slot="after">How sure the student was of each token it wrote. Mixed with agreement, it separates confident right answers from confident wrong ones.</div>
</Compare>
```

2. **`### Precision and coverage, simply`**: keep the toy table and its note `<Aside>`.
3. **`## The threshold, per version`**: keep `<Steps>`.

### `guides/escalation-and-audits.mdx`

1. **`### When a request escalates`**: keep the table.
2. **`### Audits: what they are`**: a stat pair before the note `<Aside>`:

```mdx
<StatRow cols={2}>
  <Stat value={5} suffix="%" count={false} label="of served answers audited" tone="rented" note="The teacher checks a random sample of what the student served." />
  <Stat value={0.92} decimals={2} count={false} label="rollback floor" note="A canary is rolled back when the 95% Wilson upper bound of audited precision falls below it." />
</StatRow>
```

3. **`### How audits drive the release gate`**: keep the table.

### `guides/ledger.mdx`

1. **`### One request on its way into the ledger`**: keep `<Steps>`.
2. **`### What is never stored`**: a `rt-callout--owned` callout above the table: "Training targets are teacher answers or
   customer-attested human labels, never the student's own answers."
3. Tables stay.

### `guides/night-school.mdx`

1. **After `<Eli10>`**: a `<Flow loop loopLabel="Every night">` of the night: build the sets, train from the base, score on the
   golden set, sign the bill of materials, hand to the gate. Tone `owned` on "train".
2. **`### What LoRA is`**: keep the settings table.
3. **`### The golden set: the sealed exam`**: `rt-callout--note` with the one-line rule "Golden examples are never trained
   on" (invariant 3, already stated on the page).

### `guides/release-gate.mdx`

1. **After the states table**: the rollout as a Stepper (the `<Steps>` below keep every rule).

```mdx
<Stepper
  label="Rollout stages"
  steps={[
    { title: 'Golden gate', body: 'Held-out precision at least **95%** and coverage at least **25%**, and no regression of more than 2 points against the live version, graded on the same golden rows the same night.' },
    { title: 'Shadow', body: 'The candidate answers silently next to the live version. After it would have served **100** answers: precision at least 95% and coverage within 2 points, or it is rejected.', tone: 'owned' },
    { title: 'Canary 10%', body: 'One request in ten. With at least **20** audits in the stage, it advances, holds, or is rolled back when the 95% Wilson upper bound of audited precision is below **0.92**.', tone: 'owned' },
    { title: 'Canary 50%', body: 'The same rule, with the audit count starting again.', tone: 'owned' },
    { title: 'Live', body: 'One transaction makes the canary live at 100% and retires the old version. The live version is watched and alerts, but is never rolled back automatically.', tone: 'owned' },
  ]}
/>
```

2. **`## The Wilson bound, precisely`**: keep the table; add a `<StatRow cols={3}>` above it with `min_audits` 20,
   `live_floor` 0.92 and `min_shadow` 100 (all `count={false}`).

### `guides/training-on-your-data.mdx`

1. **`### The path from a request to a loaded adapter`**: a `<Flow>` with request, teacher answer stored, training file in the
   bucket, Serverless Job, adapter loaded. Tone `rented` on the teacher step, `owned` on the adapter.
2. **`### What is verified and what is not`**: split into `rt-callout--live` (verified) and plain text (not yet), keeping the
   page's dates and wording. Nothing here may say the student ran on a Nebius GPU until it has.

### `guides/licenses.mdx`

1. **`### Why closed teachers are refused`**: the before and after.

```mdx
<Compare before="A closed teacher" after="An open teacher" beforeTone="neutral" afterTone="owned">
  <div slot="before">Terms such as OpenAI's Business Terms §3.3(e) forbid training competing models on the outputs. Ratchet refuses these teachers in code.</div>
  <div slot="after">Nemotron 3 Ultra is released under OpenMDW-1.1, which places no restrictions on outputs. Each stored example records the teacher and its license.</div>
</Compare>
```

2. **`### The license registry`**: keep the table.
3. **`### What you own`**: `rt-callout--owned`: "The LoRA adapter and its signed bill of materials are yours; the Nano base
   keeps the NVIDIA Nemotron Open Model License."

### `guides/cost-and-savings.mdx`

1. **After `<Eli10>`**: the two views as a `<Compare before="Per token" after="By GPU-hours paid" beforeTone="neutral" afterTone="owned">`
   using the table's "What it assumes" cells.
2. **`### Prices used`**: keep the table. If a stat row is added, its prices must say "estimate" and the date, as the
   page does.
3. **Any simulation figure** on this page goes in `<Stat tone="sim">` or `rt-callout--sim`, never a plain stat.

### `guides/evidence.mdx`

1. **After `<Eli10>`**: a `<PathGrid cols={3}>` of the five kinds of evidence (signed BOM, certificate, committed training
   set, ledger replay, savings receipt) with in-page anchors as `href` (`#signed-bills-of-materials`, ...), icons `file`,
   `check`, `layers`, `loop`, `coin`.
2. **`## Certified precision`**: keep the sample-size table (it is exact).

---

## Scout, Run it, Security, Help

### `guides/scout.mdx`

1. **`### Pinned and distilled calls, and why`**: a `<Flow>` of the five agents with tones: planner and writer `rented`
   (pinned to the teacher), triage and reader `owned` (distilled), searcher neutral (a tool).
2. **`### What the simulation shows`**: wrap the table in `rt-callout--sim` or add `<Stat tone="sim">` tiles with the
   table's own numbers (92%, 81%, 62% for per-task calibration on), each with the note "Simulation, mock models".

### `guides/host-on-nebius.mdx`

1. **`### The order of work`**: keep `<Steps>`; it is a procedure to follow, not to click through.
2. **`### The pieces and how they connect`**: keep the table; add a `<PathGrid cols={3}>` of the Nebius pieces only if each
   card links to its section below.
3. Keep the caution `<Aside>` on what is scripted and ready to run until it changes.

### `guides/security.mdx`

1. **`### Keys and who may call what`**: keep both tables.
2. **`### The safety rules the loop never breaks`**: a `<StatRow cols={2}>` is not right here (no numbers); use
   `rt-callout--owned` for the training-data rule and keep the list.

### `guides/glossary.mdx`, `guides/faq.mdx`

- No new components. The letter headings and questions stay Markdown so search and "On this page" find them. In the FAQ,
  `<details>` blocks already get the bordered style; use them for long answers if the page grows.

---

## API & reference

### `api/http/` (generated by starlight-openapi)

- Nothing to add by hand; the operation list, method badges and panels are styled in `src/styles/content.css`.
- Note for the generator: an operation description quotes an ADR number ("ADR 0071") from the code's docstrings. Public pages
  should not; reword the docstring or strip it in `scripts/export-reference.py`.

### `api/endpoints.mdx` (new on the reference branch), `reference/api` (synced)

- A `<PathGrid cols={2}>` at the top, grouping the routes (OpenAI-compatible, Ratchet, admin, Scout) with `icon="code"`
  and in-page anchors.

### `api/python.mdx`

- **`## For engineers`**: a `<Compare before="Over HTTP" after="Embedded" beforeTone="neutral" afterTone="neutral">` from the
  page's own "When should I embed" answer.

### `api/environment`, `api/cli` (generated by the sync script)

- No components: the generator writes Markdown tables, which already scroll and keep their header.

---

## Pages on other branches

### `guides/text-to-sql.mdx`

1. **After `<Eli10>`**: the pipeline as a `<Flow>`: teacher writes databases, questions and SQL (`rented`); the checker runs
   every answer; the student trains on kept rows (`owned`); the exam on unseen databases.
2. **`## The problem`**: `<Compare before="A large model for every question" after="A small model you run">` using the
   page's "Cost" and "Exposure" bullets.
3. Training hardware is a borrowed GPU: say so in a `rt-callout--note` if the page shows a training run; never imply a
   Nebius GPU for it.

### `guides/results.mdx`

1. Above `<SqlResults />`: `<StatRow>` tiles only if they read the same `results.json` as `SqlResults` (no copied numbers).
   Each score shows its range, as the page says.
2. Keep simulation numbers in `rt-callout--sim`, apart from the adapter's measured numbers.

### `guides/proof.mdx`

1. Each claim with its command: a `<Stepper label="Check it yourself">`, one step per claim (title = the claim, body = the
   command in backticks and what a failure looks like). Keep the full text below it.

### `guides/why-it-matters.mdx`

1. Open with the four reasons as `<PathGrid cols={2}>` (Cost `coin`, Ownership `key` with `tone="owned"`, License-clean
   `scale`, Safe releases `gate`) linking to the sections below; the home page links here.
2. Third-party facts with their links (see `web/DESIGN.md`, "Facts you may use"); no invented figures.
