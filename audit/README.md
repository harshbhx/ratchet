# Audits

Two independent read-only audits of Ratchet, kept here so their findings and status stay with the code.

| Folder | When | Scope | Method |
|---|---|---|---|
| `./` (this folder) | 22 Oct 2026, at `3bfa5ab` | Security, ML and statistics, privacy and provenance, API and deployment | Four parallel audits; the lead reproduced each defect. Start with [EXECUTIVE_SUMMARY.md](EXECUTIVE_SUMMARY.md), then [FINDINGS.md](FINDINGS.md) (RAT-001 to RAT-027) and [REMEDIATION_LOG.md](REMEDIATION_LOG.md). |
| [`skills-review-2026-10-22/`](skills-review-2026-10-22/) | 22 Oct 2026, at `3c9d063` (`main`) | Training and distillation, evaluation and the release gate, serving on vLLM, public-repo readiness, hackathon strategy | Five read-only reviews against the knowledge-distillation, PEFT, TRL, LLM-evaluation and vLLM skills and the model-evaluator, repo-publication-auditor and hackathon-strategist agents. |

**Important:** the first audit's fixes (the 20 defects its REMEDIATION_LOG marks fixed, with regression tests) were made on the
branch `claude/epic-mendel-c2kbrx`, which sits on the history from before `main` was rewritten on 22 Oct. **They are not on
`main`.** Only the report files were brought over. Porting the fixes means re-applying them on `main` one by one.

## Consolidated findings (both audits), most urgent first

Status: **open** unless marked. "Both" means the two audits found it independently.

### Hackathon (blocks or weakens the submission)

1. **No live-mode gateway exists.** `demo.ratchet.dualmindlab.tech/healthz` reports `"mode":"mock"`; `ratchet.dualmindlab.tech` is the static landing page. Stage One needs a runtime Token Factory call during judging (about 1-15 Dec). Put a live gateway online with the guards on and keep it up. ([strategy.md](skills-review-2026-10-22/strategy.md), [HACKATHON_READINESS.md](HACKATHON_READINESS.md))
2. **The submission draft overclaims Nebius.** It says the demo runs on a Nebius VM and Night School on a Serverless Job, and lists Endpoints and Object Storage as used; none has run. Split "runs today" from "scripted, not run (no GPU budget)".
3. **The text-to-SQL result page promises execution match; `scripts/measure_adapter.py` compares text** (both). The docs cite `sql_same`, `annotate` and `pipeline.sh`, which do not exist; the teacher's trailing `;` makes right answers count as wrong; its confidence is not the gateway's rule. Nothing wrong is published yet (`sql-results.json` is pending). Score with `ratchet/sqlcheck.Sandbox` or correct the page.
4. **Every `[LIVE NUMBER]` is still a placeholder;** the feedback section has open placeholders. Measure one real number through the live gateway with a small cap (about $6), not the Makefile's `MAX_USD ?= 30`.

### Correctness (before the first real training night)

5. **GPU job to vLLM handoff** (both; first audit RAT-026 fixed only on its branch). Without `RATCHET_BUCKET_S3` the default train command does not wait for the job; a failed night reuses `student-vN` and the folder; the poll only checks that a weights file exists (PEFT writes it before `adapter_config.json`). Stale or half-written weights can be loaded, graded and signed with tonight's dataset, which breaks INV-14. Fix: a new adapter name and folder per attempt, a done-marker written last, require the bucket, poll the job's state. ([serving.md](skills-review-2026-10-22/serving.md), [training.md](skills-review-2026-10-22/training.md))
6. **Canary promotion uses a point estimate checked on every tick.** A version at true precision 0.90 against a 0.92 floor reaches 100% about 34% of the time (41% under the sequential profile); once live, the alert needs about 2,360 audits to flag it. Promote on evidence by default and publish the false-promotion rates. Changes the pinned reference simulation. ([evaluation.md](skills-review-2026-10-22/evaluation.md))
7. **The signed certificate measures the global threshold while the router serves per-task thresholds** (both; first audit RAT-004 fixed only on its branch). Reproduced: "certified, 1.000, 0 errors" signed, 0.958 with 8 errors as served.
8. **Mamba `out_proj` LoRA trains nothing** (the fused kernel reads the base weight); long rows are truncated at 2048 tokens and still counted in the BOM; the base model revision is not pinned in training or serving; redacted training text against raw serving text lets extraction tasks learn `[EMAIL]` while the golden eval scores it as agreement.
9. **Serving costs and first-run risks:** no reachability check before the paid job; a crashed job is waited on for 2 h and billed as 2 GPU-hours; each endpoint start re-downloads about 8 GB; the student's 15 s read timeout can trip the shared breaker on long answers.
10. **Evaluation bias:** shadow precision is mostly agreement with the live student; golden rows come from the live model's own escalations; templates leak between train and golden; SM-4 is circular; audit samples at different rates are pooled unweighted.

### Publication

11. **Old history with Claude authorship is still public on three branches:** `claude/epic-mendel-c2kbrx`, `docs/design`, `docs/mermaid` (62 commits, trailer lines, session links). `main` has none. Delete them once this folder is merged. The same 32 commits stay visible in the Commits tab of PRs #4-#10 and #12; only GitHub Support can remove those refs.
12. **PR descriptions #12 and #13 carried a tool footer.** *Fixed 22 Oct:* both rewritten; the old text stays in their edit history until the owner deletes those revisions.
13. **React Bits components (MIT + Commons Clause) in `docs-site/src/components/rb/`.** *Resolved 23 Oct:* the owner has the React Bits author's approval. Record it next to `rb/LICENSE.md` and update `design/ASSET_AND_LICENSE_REGISTER.md` so they agree.
14. **Aceternity UI ports in `web/src/components/fx/` carry no license notice** (also in `ratchet/web/dist`). Confirm the license and add a third-party notices file.
15. **`.gitignore` gaps:** `.mcp.json`, `.claude/`, `.env.*` (keeping `.env.example`), `*.db-wal`, `*.db-shm`, `out/`.
16. **Six squash merges on `main` were made in the GitHub web UI,** so their committer is GitHub (author Harsh) and they show "Verified". Merge locally from now on if the committer must be Harsh.
17. **No secrets anywhere:** gitleaks and regex scans over 28 branches, 22 PR refs and 619 commits found only invented test values. No rotation needed.

### Sound (checked and holding)

Exact binomial tails, Clopper-Pearson, Learn-then-Test, the e-values and confidence sequences; the golden eval's held-out split within a night; loss on the answer only and chat-template parity with thinking off; memory and bf16 on an L40S; the vLLM flags for the hybrid Mamba model, runtime LoRA load and lost-adapter reload, logprobs; invariants 2, 3, 6, 7, 10, 15 and 16 on the paths read.
