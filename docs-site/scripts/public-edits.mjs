// Edits applied to the raw text of the public docs before they are published, so the site never mentions the
// private planning documents (business plan, roadmap, submission notes, requirements matrix, decision records).
// Each edit is [pattern, replacement]; a pattern that no longer matches is reported by sync-docs.mjs, so a doc
// that changes does not silently bring a mention back.

/** Whole sections (heading line to the next heading of the same or a higher level) that only describe private docs. */
export const DROP_SECTIONS = {
  'TESTING.md': [/^### `tests\/test_docs_claims\.py`/, /^### `tests\/test_traceability\.py`/, /^## Traceability\s*$/],
};

export const EDITS = {
  'API.md': [
    // Inside a code block, where the id scrubber does not look: the metric's key in the sample reply.
    [/"NFR-1": \{/, '"latency": {'],
  ],
  'ARCHITECTURE.md': [
    [/written once \(append-only, INV-17\)/, 'written once (append-only)'],
    [/^NFR-1   = median latency/m, 'latency  = median latency'],
    [/^SM-(\d)    =/gm, 'metric $1 ='],
    [/Product context is in \[PRD\.md\]\(PRD\.md\); the reasoning behind each choice is in the \[ADRs\]\(adr\/README\.md\)\./, 'The reasoning behind each choice is in [Design decisions](#9-design-decisions).'],
    [/^\| ADR \| Decision \|$/m, '| # | Decision |'],
    [/^\| \[0011\]\(.*\n/m, ''],
    [/Before \[?ADR 0060\]?(\([^)]*\))?/, 'Before the hot-path caches'],
    // The id scrubber turns "PRD" into "product requirements", which reads badly before "§8.1's".
    [/PRD §8\.1's SM-1 to SM-5/, 'SM-1 to SM-5'],
    [/^PRD §8\.1's metrics come from/m, 'The success metrics come from'],
  ],
  'CONFIGURATION.md': [
    [/for hackathon judges/g, 'for evaluators'],
    [/ \(PRD §6\.16\)/g, ''],
  ],
  'DEPLOY_NEBIUS_VM.md': [
    [/so judges can use it from \*\*Oct 29 to Dec 15, 2026\*\* if you choose this host\./, 'so evaluators can use it if you choose this host.'],
    [/^> \*\*For judging, live mode is required\.\*\*.*$/m, '> **The public gateway should run in live mode.** Mock mode makes no Token Factory or Tavily call. Keep the VM running with the budget guard on, and make sure the Token Factory and Tavily credits last as long as the demo is meant to stay up.'],
    [/\(README or Devpost\)/, '(for example in the README)'],
    [/The Devpost gallery is public, so without/, 'The live gateway is public, so without'],
    [/before a judge arrives\. An example that keeps runs and credits consistent through judging:/, 'before an evaluator arrives. An example that keeps runs and credits consistent:'],
    [/# goes in the submission's testing instructions only/, "# share it with evaluators directly, never publish it"],
    [/^## Keeping it alive until Dec 15$/m, '## Keeping it alive'],
    [/The period Oct 29 – Dec 15 is 48 days/, 'The public period is taken as 48 days'],
    [/\| Oct 29 – Dec 15 \|/, '| Demo period (48 days) |'],
    [/until the last day of judging/, 'until the last public day'],
    [/\*\*After judging:\*\*/, '**When the public period ends:**'],
    [/On Dec 16, or whenever judging ends\./, 'When the public period ends.'],
  ],
  'DEPLOYMENT.md': [
    [/^## 3\. Keeping the live gateway up for judging \(Dec 1–15, 2026\)$/m, '## 3. Keeping the live gateway up'],
    [/Judges need a working live URL for two weeks, and/, 'A public gateway needs a working URL for as long as it is shown, and'],
    [/\| Hackathon total \|/, '| Short evaluation total |'],
  ],
  'RUNBOOK.md': [
    [/, but the Tavily prize needs working Tavily calls:/, ', but live Scout runs need working Tavily calls:'],
    [/to the hackathon managers/, 'to the evaluators'],
    [/^3\. Take each live number for the README and the submission text from .*$/m, '3. Take each live number for the published figures from `evidence/live/results.json`, using the placeholder map in `docs/LIVE_RESULTS.md`, with the `<!-- claim:live.* -->` anchor right after each number.'],
    [/^4\. Move the PRD statuses.*\n/m, ''],
    [/^5\. Stop the endpoint/m, '4. Stop the endpoint'],
  ],
  'THREAT_MODEL.md': [
    [/The ADR, API\.md and the CLI say/, 'The API guide and the CLI say'],
    [/ ADR 0029 and the runbook say/, ' The runbook says'],
  ],
  'SCOUT.md': [
    [/for hackathon judges/g, 'for evaluators'],
  ],
  'TESTING.md': [
    [/ in the PRD until the day-1 checks \(`scripts\/day1_check\.py`\)/, ' until the first-run checks (`scripts/day1_check.py`)'],
    // The id scrubber turns "PRD" into "product requirements", which reads badly in these two sentences.
    [/meets PRD §8\.1's SM-1 \(/, 'meets SM-1 ('],
    [/, so the PRD says `Not met`\./, ', so its status is `Not met`.'],
    [/these back the PRD's `Met in simulation \(seed 7\)` statuses/, 'these back the `Met in simulation (seed 7)` statuses'],
    [/where the PRD plans it and its foundations exist/, 'where its foundations exist'],
    [/, and `tests\/test_traceability\.py` checks that the PRD, the invariants, the code and the tests agree \(\[Traceability\]\(#traceability\)\)/, ''],
    [/^python scripts\/reqtrace\.py --check .*\n/m, ''],
    [/^\| `tests\/test_traceability\.py` \|.*\n/m, ''],
    [/, and update the test counts above \(\[Traceability\]\(#traceability\)\)/, ', and update the test counts above'],
    [/^\| `tests\/test_docs_claims\.py` \|.*\n/m, ''],
  ],
};

/**
 * Edits for the generated reference pages (api/cli.md), applied once the page is built from generated/cli.json. Its help
 * text is captured from the code, which points at rows of a private checklist; the published page names no row.
 */
export const GENERATED_EDITS = {
  'cli.md': [
    [/ as an escalation asks for them \(DAY1 row 10\)\./, ' as an escalation asks for them.'],
    [/: DAY1 row 4's `bash deploy\/train_job\.sh test` is that adapter\./, ': `bash deploy/train_job.sh test` trains that adapter.'],
  ],
};
