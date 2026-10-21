// Edits applied to the raw text of the public docs before they are published, so the site never mentions the
// private planning documents (business plan, roadmap, submission notes, requirements matrix, decision records).
// Each edit is [pattern, replacement]; a pattern that no longer matches is reported by sync-docs.mjs, so a doc
// that changes does not silently bring a mention back.

/** Whole sections (heading line to the next heading of the same or a higher level) that only describe private docs. */
export const DROP_SECTIONS = {
  'TESTING.md': [/^### `tests\/test_docs_claims\.py`/, /^### `tests\/test_traceability\.py`/, /^## Traceability\s*$/],
};

export const EDITS = {
  'ARCHITECTURE.md': [
    [/Product context is in \[PRD\.md\]\(PRD\.md\); the reasoning behind each choice is in the \[ADRs\]\(adr\/README\.md\)\./, 'The reasoning behind each choice is in [Design decisions](#9-design-decisions).'],
    [/^\| ADR \| Decision \|$/m, '| # | Decision |'],
    [/^\| \[0011\]\(.*\n/m, ''],
    [/Before \[?ADR 0060\]?(\([^)]*\))?/, 'Before the hot-path caches'],
  ],
  'CONFIGURATION.md': [
    [/for hackathon judges/g, 'for evaluators'],
    [/ \(PRD §6\.16\)/g, ''],
  ],
  'DEPLOY_NEBIUS_VM.md': [
    [/so judges can use it from \*\*Oct 29 to Dec 15, 2026\*\*\./, 'so evaluators can use it.'],
    [/the checks in \[DAY1\.md\]\(DAY1\.md\) pass/, 'the first-run checks (`scripts/day1_check.py`) pass'],
    [/^> \*\*For judging, live mode is required\.\*\*.*$/m, '> **The public demo should run in live mode.** Mock mode makes no Token Factory or Tavily call. Keep the VM running with the budget guard on, and make sure the Token Factory and Tavily credits last as long as the demo is meant to stay up.'],
    [/\(README or Devpost\)/, '(for example in the README)'],
    [/The Devpost gallery is public, so without/, 'The demo is public, so without'],
    [/before a judge arrives\. An example that keeps runs and credits consistent through judging:/, 'before an evaluator arrives. An example that keeps runs and credits consistent:'],
    [/# goes in the submission's testing instructions only/, "# share it with evaluators directly, never publish it"],
    [/^## Keeping it alive until Dec 15$/m, '## Keeping it alive'],
    [/The period Oct 29 – Dec 15 is 48 days/, 'The demo period is taken as 48 days'],
    [/\| Oct 29 – Dec 15 \|/, '| Demo period (48 days) |'],
    [/until the last day of judging/, 'until the last day of the demo'],
    [/\*\*After judging:\*\*/, '**When the demo ends:**'],
    [/On Dec 16, or whenever judging ends\./, 'When the demo ends.'],
  ],
  'DEPLOYMENT.md': [
    [/^## 3\. Keeping the demo alive for judging \(Dec 1–15, 2026\)$/m, '## 3. Keeping the demo alive'],
    [/Judges need a working demo URL for two weeks, and/, 'A public demo needs a working URL for as long as it is shown, and'],
    [/\| Hackathon total \|/, '| Short evaluation total |'],
  ],
  'RUNBOOK.md': [
    [/, but the Tavily prize needs working Tavily calls:/, ', but the demo needs working Tavily calls:'],
    [/ \(lane D\)/, ''],
    [/to the hackathon managers/, 'to the evaluators'],
    [/If lane E's retention window/, 'If the retention window'],
    [/^3\. Fill each `\[LIVE NUMBER\]` in .*$/m, '3. Replace each `[LIVE NUMBER]` in the published figures with the value from `evidence/live/results.json`, using the placeholder map in `docs/LIVE_RESULTS.md`.'],
    [/^4\. Move the PRD statuses.*\n/m, ''],
    [/^5\. Stop the endpoint/m, '4. Stop the endpoint'],
  ],
  'THREAT_MODEL.md': [
    [/The ADR, API\.md and the CLI say/, 'The API guide and the CLI say'],
    [/ ADR 0029 and the runbook say/, ' The runbook says'],
  ],
  'SCOUT.md': [
    [/for hackathon judges/g, 'for evaluators'],
    [/ \(and the Tavily prize needs working Tavily calls\)/, ''],
  ],
  'TESTING.md': [
    [/ in the PRD until the day-1 checks \(\[?DAY1\.md\]?(\([^)]*\))?\)/, ' until the first-run checks'],
    [/where the PRD plans it and its foundations exist/, 'where its foundations exist'],
    [/, and `tests\/test_traceability\.py` checks that the PRD, the invariants, the code and the tests agree \(\[Traceability\]\(#traceability\)\)/, ''],
    [/^python scripts\/reqtrace\.py --check .*\n/m, ''],
    [/^\| `tests\/test_traceability\.py` \|.*\n/m, ''],
    [/, and update the test counts above \(\[Traceability\]\(#traceability\)\)/, ', and update the test counts above'],
    [/^\| `tests\/test_docs_claims\.py` \|.*\n/m, ''],
  ],
};
