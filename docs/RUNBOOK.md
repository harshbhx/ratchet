# Runbook

How to operate Ratchet in live mode: what to check every day, what the events mean, how to intervene by hand, and what to do when something breaks. The mechanics are explained in [ARCHITECTURE.md](ARCHITECTURE.md).

## Daily checks (5 minutes)

1. **Dashboard** (`/dashboard`) or `GET /ratchet/stats`:
   - **Answered by your model** should be stable or rising. A sudden drop means drift (new kinds of requests) or a student problem.
   - **Cost reduction** should track student share. If share is high but savings are low, audits or shadow are costing more than expected.
   - **Release log**: read every event since yesterday (meanings below).
2. **Night School log** (`/var/log/ratchet-night.log`): exactly one of `trained` or `skip` per night.
3. **Bills:** Token Factory usage page and the Nebius billing page. Check that the endpoint is stopped if you are not using it, and that `python -m ratchet.cost report` (or `GET /ratchet/cost`) shows the same endpoint hours as the bill (see [Cost and the self-host recommendation](#cost-and-the-self-host-recommendation)).
4. **Ledger backup** exists for today (see [Backups](#backups)).
5. **Savings receipts**: yesterday's are sealed (`GET /ratchet/receipts` with an admin key lists them; the cron job below seals them). See [Savings receipts](#savings-receipts).

## Reading the dashboard

| Panel | Source | What to look for |
|---|---|---|
| Saved vs all-teacher | `totals.saved_usd` | Cumulative; it should only go up |
| Cost reduction (latest day) | `days[-1].savings_pct` | Below 50% after the first week means the student is not taking traffic |
| Answered by your model | `days[-1].student_share` | Escalations show as the orange part of each bar |
| Model versions | `versions` | Exactly one `live` once past cold start; at most one `shadow` or `canary` |
| Release log | last 40 `events` | See below |
| Provenance | `bill_of_materials` | Only distillable teachers and licenses; fingerprint changes as data grows |
| Economics | `GET /ratchet/cost` (else the `cost` summary) | The GPU-hour view next to the per-token one, break-even and the recommendation. The other panels price the student per token (`totals.cost_model`) |
| By task | `tasks` | A task whose share stays at 0 while its neighbours rise: check its spec in the Task registry |
| Task registry | `GET /ratchet/tasks` | Unregistered names (typos, or tasks to register or pin); `Recalibrating` should clear after the next night. Admin API tasks can be changed here with an admin key, which the page keeps in memory only |
| Resilience | `circuit` events and `tasks[].routes` | An `Open` breaker that does not close within a few minutes (see [Student endpoint down](#student-endpoint-down-or-adapters-lost), [Teacher API down](#teacher-api-down-token-factory-errors-or-timeouts)); a rising `student_breaker` or `student_error` count. The state comes from the last 40 events, so "No recent change" means none of them moved that breaker |

## Event kinds

| Kind | Written by | Meaning | Action |
|---|---|---|---|
| `trained` | `run_night` | A new version was trained and scored on the golden set | None |
| `skip` | `run_night` | No training tonight: storage disabled (`RATCHET_STORE_EXAMPLES=0`; a task whose spec changed is still re-scored), release in flight, fewer than 50 new examples, or no golden set yet | None, unless it repeats for days with no release in flight and storage on |
| `rejected` | `admit` / `tick` | Candidate failed the golden gate or shadow (reason in detail) | Repeated rejections: see [Persistent rejections](#persistent-rejections) |
| `shadow` | `admit` | Candidate passed the golden gate and is running silently. Appends `serves +c−b of n vs live vL (McNemar p=…); nf negative flips, pf positive flips` with the paired gate, and `certified >= 95% (lcb 96.4%, delta 0.05)` when its certificate holds ([Checking a certificate](#checking-a-certificate)) | None |
| `canary` | `admit` / `tick` | Candidate serves 10% or 50% of traffic | Watch for `rollback` |
| `live` | `tick` | Candidate promoted to 100%; previous live retired | None |
| `retired` | `admit` | An in-flight shadow or canary was replaced by a newer candidate | None |
| `rollback` | `tick` | The canary's audited precision is clearly below 0.92: by default its Wilson upper bound after `min_audits` audits (`v3 audited precision 41.2% (n=17, upper bound 64.0% < 92%)`), with `RATCHET_SEQUENTIAL=1` an e-value over all its audits (`v3 rolled back: 3/7 audits agree; e-value 64.9 >= 20 ...`, see [Reading an e-value event](#reading-an-e-value-event)). Traffic is back on the previous live version (or the teacher) | Investigate: see [Bad deploy](#bad-deploy--rollback) |
| `inconclusive` | `tick` | `RATCHET_SEQUENTIAL=1` only: a canary stage reached `RATCHET_MAX_STAGE_AUDITS` audits without enough evidence either way (`v4 retired after 400 audits at canary 50% without enough evidence either way (366/400 agree); 95% CS [0.865, 0.953]; ...`). The canary is `retired`, traffic is back on the live version, and Night School trains again | Usually a version right at the floor. If it repeats, compare its golden precision with the confidence sequence; a raised audit rate (`RATCHET_CANARY_AUDIT_RATE`) decides sooner |
| `alert` | `tick` | **Live** version's audited precision since it went live is clearly below 0.92: by default an e-value of 100 or more (`live v4: 0/3 audits agree; e-value 268 >= 100 (anytime-valid, alpha 1%); ...`), with `RATCHET_ALERT_EPROCESS=0` the Wilson upper bound. It is still serving, because there is no automatic action on live | Act now: see [Precision alert](#precision-alert-on-live) |
| `student_error` | `Ratchet.handle` | A student call raised; the request fell back to the teacher (route `student_error`). Logged on the 1st, 10th, 100th and 1000th failure. Detail is the version and the exception's class, with the HTTP status and code for an HTTP error (`v3: UpstreamError 503`), never its message, which can quote the request. `AdapterMissing 404` in the detail means vLLM lost the adapter (an endpoint restart); the gateway reloads it by itself | See [Student endpoint down or adapters lost](#student-endpoint-down-or-adapters-lost) |
| `circuit` | `Ratchet` (student and teacher breakers) | A breaker changed state. `student: closed->open after 5 failures (<error>)`: the endpoint kept failing, so requests now go straight to the teacher (route `student_breaker`) without waiting on it. `open->half_open`: one probe request is trying it again. `half_open->open for 60 s after a failed probe`: still down; the wait doubles up to 300 s. Once the wait is 300 s, that pair is logged only for the 1st, 10th, 100th and 1000th probe at that wait (the event says which), so a long outage leaves the rest of the log in view. `half_open->closed`: the endpoint is back (always logged). A `shadow:` breaker guards only the shadow candidate's calls. A `teacher:` breaker (`teacher: closed->open after 5 failures (UpstreamError 503)`) means Token Factory kept failing: requests that need the teacher now get a 503 `teacher_circuit_open` at once, and audits and arbitrations are skipped; a `fallback_teacher:` breaker guards the fallback teacher | `student:` or `shadow:` `open` that does not close within a few minutes: see [Student endpoint down or adapters lost](#student-endpoint-down-or-adapters-lost); `teacher:` see [Teacher API down](#teacher-api-down-token-factory-errors-or-timeouts) |
| `adapter_reloaded` | the gateway's `AdapterReloader` | vLLM answered 404 for a version's adapter, and the gateway loaded it again (`/v1/load_lora_adapter`) | None. Several at once usually mean the endpoint restarted |
| `adapter_reload_failed` | the gateway's `AdapterReloader` | Loading a lost adapter failed (detail: the error class and HTTP status). The next 404 after 60 s tries again; meanwhile that version's requests go to the teacher | See [Student endpoint down or adapters lost](#student-endpoint-down-or-adapters-lost) |
| `audit_error` | `Ratchet.handle` | A teacher audit of a served student answer raised (after the teacher's retries); the student's answer was served and the request is logged with `audited=0`. Detail is the task and the exception's class (with the HTTP status for an HTTP error, and the code for an upstream error: `UpstreamError 504 upstream_timeout`), never its message. Logged on the 1st, 10th, 100th and 1000th failure | See [Teacher API down](#teacher-api-down-token-factory-errors-or-timeouts) |
| `shadow_error` | `Ratchet.handle` | A shadow call raised: `sampling` (the shadow's own samples; no shadow record for that request) or `arbitration` (the teacher's arbitration; the disagreement counts against the shadow). The request was not affected. Same detail and throttling as `audit_error` | `sampling`: the shadow's adapter may be missing, see [Student endpoint down or adapters lost](#student-endpoint-down-or-adapters-lost). `arbitration`: see [Teacher API down](#teacher-api-down-token-factory-errors-or-timeouts) |
| `degraded` | `Ratchet.handle` | `RATCHET_ON_TEACHER_FAILURE=serve_student`: an escalated request got the student's own below-threshold answer because the teacher was unavailable (route `degraded`, `X-Ratchet-Degraded: teacher_unavailable`). Detail is the task and the teacher's failure (`ticket-triage: UpstreamError 503`, or `CircuitOpen 503 teacher_circuit_open` while its breaker is open). Logged on the 1st, 10th, 100th and 1000th since the last restart; `days[].degraded` in `/ratchet/stats` has the count | See [Teacher API down](#teacher-api-down-token-factory-errors-or-timeouts) |
| `fallback_teacher` | `Ratchet` | The fallback teacher (`RATCHET_TEACHER_FALLBACK_MODEL`) answered because the teacher was unavailable (route `fallback_teacher`). Detail is the task and the teacher's failure. Same throttling; `days[].fallback_teacher` has the count | See [Teacher API down](#teacher-api-down-token-factory-errors-or-timeouts) |
| `skip_example` | `Ratchet._store_example` | A teacher answer was served but not stored as a training example (INV-16). Detail is the task and the reason: `finish_reason length` (cut off by the token limit), another `finish_reason` (`content_filter`, `other`, ...), `empty answer` (nothing left once reasoning is stripped), `invalid answer` (a JSON task's answer does not parse), `invalid field <path>` (a field of the task's spec is missing, null where it may not be, or does not normalise, such as a label outside its vocabulary; FR-2.14), `invalid after redaction`, `client logit_bias`, `client penalty`, `client temperature above 1`, `client vendor field` (written under a client's sampling that can steer the label, so it is never a training target), or `erased` (the same text was erased before: its hash is tombstoned and never stored again, FR-13.3). Logged on the 1st, 10th, 100th and 1000th skip of each reason; never the answer. The request row has `stored = 0`, and an audit with such an answer is not counted | A few are normal. Many: see [Teacher answers not stored](#teacher-answers-not-stored) |
| `gateway_error` | the gateway's error handler | An unhandled exception in a request: a bug, not a client mistake. The client got a 500 (`api_error` on `/v1/*`). Detail is the exception's class and how many there have been, never its message. Logged on the 1st, 10th, 100th, ... occurrence since the last restart | Read the traceback in the gateway's log (`docker compose logs ratchet` on the demo VM) and fix the bug |
| `task_registered`, `task_changed`, `task_deleted` | the admin API (`/ratchet/tasks`) | An admin created, changed or removed an API task. Detail: the task, its spec hash (`old -> new` on a change, or `kept` when only `description`, `clients` or `match` changed) and the admin key's name, never the key | Expected after an operator change. A changed spec keeps the task on the teacher until a night re-scores the live version (`recalibrating` in `GET /ratchet/tasks`) |
| `task_skipped` | gateway startup | A task stored through the admin API was not registered: its spec no longer parses (detail: the first error), or `RATCHET_TASKS_FILE` or a built-in task now has its name. The gateway started anyway; requests naming it follow `RATCHET_UNKNOWN_TASKS` | Fix it with `PUT /ratchet/tasks/<name>` or remove it with `DELETE`; for a name the file now owns, delete the stored one |
| `rescored` | `run_night` | A night without enough new data to train re-scored the live version, because a golden task's spec changed since it was scored. The task's student traffic resumes under the new threshold | None |
| `rescore_failed` | `run_night` | That re-score failed (detail: the error class and status). The task stays on the teacher; the next night tries again | Check the student endpoint, as for a failed night |
| `endpoint` | `python -m ratchet.cost endpoint`, `POST /ratchet/cost/endpoint`, `deploy/student_endpoint.sh` | The student endpoint was recorded as up (`student endpoint up: 1 GPU at $1.55 per GPU-hour (cli)`) or down (`... down after 9.5 h (api by ops)`), with the source and the admin's name for the API, never a key. Recording `up` while up, or `down` while down, writes nothing | None. An `up` with no `down` hours later while you thought the endpoint was stopped: check the Nebius console, then record `down` |
| `integrity` | `POST /ratchet/versions/{id}/proof`, `GET /ratchet/versions/{id}/heldout`, `GET /ratchet/receipts/{day}/proof/{id}` and `/leaves` | A sealed receipt's day (`receipt of day 3 (acme): the ledger's rows no longer rebuild its signed root`: a request row of that day was changed, added or removed after sealing, other than by request retention, whose fold answers 410 and is no integrity failure; the receipt itself never changes), or a version's stored `version_leaves` (`v3 train leaves do not rebuild the root its BOM signs; the ledger was changed`) no longer rebuild the set commitment its signed BOM carries. SQLite's triggers refuse any edit through its API (INV-17), so someone edited the database file by other means (or dropped the triggers). The endpoints answer 500 for that version and split and serve no proof. Written once per version and split per process | Treat as tampering: stop the gateway, keep the file for forensics, restore the ledger from a backup ([Backups](#backups)) and compare `version_leaves` with the backup's. The signed BOM still holds the true roots |
| `no_train` | `POST` / `DELETE /v1/ratchet/no-train` | End users were registered as no-train, or registrations removed: a count and who (`2 end user registration(s) by admin ops`, `1 ... removed by acme`), never the end user or its hash | None. See [Opt-out requests](#opt-out-requests-an-end-user-or-a-client) |
| `no_train_unmatched` | gateway startup (`privacy.check_no_train_scopes`) | No-train registrations match no request under the current API keys: their scope is a key name no longer in `RATCHET_API_KEYS` (renamed or removed), or keyless while keys are on. The detail gives the count and the scopes (names only); written once per change, and `/ratchet/stats` → `privacy.no_train_unmatched` keeps the count | If a key was renamed by mistake, restore its name and restart. Otherwise have those end users registered again under the new name, or by an admin for every client |
| `end_user_key` | gateway startup (`privacy.end_users`) | The end-user HMAC key differs from the one the ledger last saw (`RATCHET_END_USER_KEY` set, changed or removed): every earlier end-user hash, and the no-train registrations the detail counts, no longer match, so those end users' requests are stored again | If unintended, restore the previous key and restart. If intended, have every no-train end user registered again |
| `erase` | `privacy.erase` (`POST /ratchet/admin/privacy/erase`, `DELETE /v1/ratchet/examples`, `python -m ratchet.admin erase`) | Stored examples were erased for good: the erasure's id, what selected them (`client`, `end user`, `hashes`, `content`), who asked, how many were purged, shared ones kept, request rows unlinked, and the versions that may have been trained on them (`may be held by v4, v5 (reported, not changed)`). Never a hash, an end user or the text | If versions are named, decide whether to retire them or wait for the next night's version, which is trained without the erased rows. See [PII leak](#pii-leak) |
| `purge` | retention (`privacy.apply_retention`: the live gateway hourly, a live night, `python -m ratchet.live purge`) | Examples not seen within `RATCHET_EXAMPLE_RETENTION_DAYS` lost their text, and request rows older than `RATCHET_REQUEST_RETENTION_DAYS` were folded into daily sums; counts, and the client for a `--client` run | None |
| `purge_failed` | the live gateway's hourly retention | A scheduled purge raised (detail: the exception's class). Nothing was half-done: each purge and each fold chunk is one transaction. The next hour tries again | Run `python -m ratchet.live purge --dry-run`, then without `--dry-run`, and read the error |
| `sync_failed` | `python -m ratchet.live night` | After a night that trained, the adapter's `ratchet_bom.json` and `NOTICE.md` were written to `RATCHET_DATA_DIR/adapters/<name>/`, but the second `RATCHET_SYNC_CMD` failed (detail: the version and the exception's class), so the bucket's adapter folder has the weights without their licenses. The night itself stands: the version exists and is in the release gate. The command exits non-zero | Run `RATCHET_SYNC_CMD` by hand (it only uploads), then check that the bucket's `adapters/<name>/` holds `NOTICE.md` and `ratchet_bom.json`. `python -m ratchet.live export --version N --out DIR` bundles the adapter with its licenses at any time |
| `human_alert` | `POST /v1/ratchet/feedback` (`feedback.check_human_alert`) | A version's human precision on the answers it served itself has at least `RATCHET_MIN_AUDITS` verdicts and anytime-valid evidence (a mixture e-value of at least 40, `feedback.ALERT_ALPHA` 0.025) that it is below `RATCHET_LIVE_FLOOR`: the people using it judge it worse than the floor. Checked after every verdict, with a false-alarm rate under 2.5% however many arrive. Once per version (kept across restarts). It never promotes or rolls back anything | See [Human feedback alert](#human-feedback-alert) |
| `import` | `python -m ratchet.importer` | Labelled rows were imported: how many of how many were stored, as human labels or an open teacher's answers, golden only or not, new, replaced, duplicates, invalid and refused, the file's name and SHA-256 and the attestation (redacted). Never row text | Keep the file and the attestation with your records; see [Importing labelled data](#importing-labelled-data) |
| `receipt_skipped` | sealing (`receipts.seal_due`: `python -m ratchet.live seal`, the gateway when a receipt is asked for) | Request retention folded some of a day's request rows before its receipts were sealed, so the day gets no receipt (the rows left would leave requests out); the next day's receipts chain to the last one sealed. Written once per day | Run `python -m ratchet.live seal` hourly from cron; the gateway's hourly retention, `python -m ratchet.live purge` and a live night seal every day that has ended before they fold, so this means sealing failed or the clock moved |
| `judge` | `POST /scout/judge` | A judge opened a session with the access code (`judge session opened`). No IP and no code; refused attempts write nothing | None. Many in a day when no judging is under way: the code may have leaked, see [Judges cannot run Scout](#judges-cannot-run-scout) |
| `search_error` | the gateway's Scout runs (`Runtime.note_search`) | A Scout search failed and the run finished without (more) web sources, labelled "search unavailable" (`scout search: HTTPStatusError 401`, `ConnectError`): the class and status, never the message or URL. Logged on the 1st, 10th, 100th and 1000th failure since the last start; credit refusals are not failures | See [Tavily credits running low or search unavailable](#tavily-credits-running-low-or-search-unavailable). A 401 or 403 is a broken key |
| `tavily_exhausted` | `CreditMeter` | Tavily answered 432 ("Key limit or Plan Limit exceeded") or 433 ("PayGo limit exceeded"): no search is sent for an hour, and runs answer without search, labelled. Once per exhaustion | See [Tavily credits running low or search unavailable](#tavily-credits-running-low-or-search-unavailable) |
| `gateway_lease` | (planned, NFR-60) | Not written yet: the single-writer lease waits for the leases table. It will say that another gateway process serves the same live ledger, so Scout's guard and the other in-memory limits are per process | See [Two gateway processes on one ledger](#two-gateway-processes-on-one-ledger) |
| `liverun_start`, `liverun_round`, `liverun_stop` | `python -m ratchet.liverun` | A live measurement run's synthetic traffic: `liverun 20261025T083000Z started: synthetic tickets traffic, 4 rounds x 1500 requests, cap $30.00 (key liverun)`, one line per round (`round 2 done: 1512 requests, 71% student, 388 audits`) and `stopped: <reason>` or `finished`. Key names, counts and reasons only | None. They mark which traffic in the release log was synthetic; see [Live measurement run](#live-measurement-run) |
| `fault` | demo only | Fault injection via `/ratchet/demo/break-next` or `/ratchet/demo/student-outage` | Mock mode only |
| `warmup` | mock startup | `RATCHET_MOCK_WARMUP_DAYS` simulated days ran before the demo opened | Mock mode only |

The `alert` event is written once per live version per process (a restart can write it again), not on every tick. An `inconclusive` canary is retired, not rolled back: it was not shown to be worse.

## Manual interventions

All state lives in the SQLite ledger (`RATCHET_DB`). Stop the gateway or make sure no Night School run is in progress, take a backup, then use either the Python API or SQL.

Python (preferred: it also updates `stage_since` and writes events):

```python
from ratchet.ledger import Ledger

led = Ledger("ratchet.db")
for v in led.versions():
    print(v.id, v.state, v.traffic_pct, v.eval.get("precision"), v.eval.get("coverage"))
```

| Goal | Python |
|---|---|
| Roll back the current canary | `led.set_version(<canary id>, "rolled_back", 0); led.event("rollback", "manual: <reason>")` |
| Take the live version out (all traffic to teacher) | `led.set_version(<live id>, "retired", 0); led.event("retired", "manual: <reason>")` |
| Restore a previous version as live | `led.set_version(<old id>, "live", 100); led.set_version(<bad id>, "retired", 0); led.event("live", "manual restore v<old id>")`. The old adapter must be loaded in vLLM: `ratchet.live.load_adapter(base, key, "student-v<old>", "/data/adapters/student-v<old>")` |
| Force a candidate to canary (skip shadow) | `led.set_version(<id>, "canary", 10); led.event("canary", "manual")`. Only if you accept the risk |
| Cancel an in-flight shadow so tonight's training runs | `led.set_version(<shadow id>, "retired", 0)` |

| Check an adapter's bill of materials | `curl -s $GATEWAY/ratchet/versions/<id>/bom > bom.json && python -m ratchet.bom verify bom.json --public-key <published key>` |
| Add or rotate a client's API key | Edit `RATCHET_API_KEYS` (`name:key,...`) and restart the gateway. Keep the same name so per-client stats, end-user hashes and the client's no-train registrations continue (renaming unlinks them; a `no_train_unmatched` event says how many); the old key stops working at once |
| Add, change or remove a task | Edit `RATCHET_TASKS_FILE` and restart, or use the admin API with a key from `RATCHET_ADMIN_KEYS`: `curl -X PUT $GATEWAY/ratchet/tasks/<name> -H "Authorization: Bearer $ADMIN_KEY" -d @spec.json` (check it first with `POST /ratchet/tasks/validate`). [CONFIGURATION.md](CONFIGURATION.md#adding-a-task) has the spec format |
| Keep a task on the teacher | Set `"pinned": true` in its spec (file or API). Its stored examples stay in the ledger but leave training and the golden eval from the next night on (`excluded_pinned` in the manifest) |
| Stop distilling task names nobody registered | `RATCHET_UNKNOWN_TASKS=teacher` (teacher only, nothing stored) or `reject` (a 400 that lists the registered tasks); `GET /ratchet/tasks` lists the unregistered names requests used |
| Record the student endpoint going up or down | `python -m ratchet.cost endpoint up` / `down` where the ledger is (`--db`, default `RATCHET_DB`; `--at "2026-10-20T08:00:00Z"` for a time other than now, `--usd-per-hour`, `--gpus`), or `POST /ratchet/cost/endpoint` with an admin key. `deploy/student_endpoint.sh` and `... stop` do it for you when they run next to the ledger. `up` while up and `down` while down change nothing |
| Close a training job whose end was lost (the night process died) | `python -m ratchet.cost close training_job --at <end time from the Nebius console>`; until then the report counts it up to now and warns |
| Seal the savings receipts of every day that has ended | `python -m ratchet.live seal` (cron does it hourly, below), or `python -m ratchet.receipt seal --db "$RATCHET_DB" --period <day>` to seal through a day and print its receipt. A sealed receipt is never changed or deleted (invariant 17); there is nothing to redo |
| Rotate the BOM signing key | `python -m ratchet.bom keygen`, set the new `RATCHET_BOM_SIGNING_KEY`, restart, publish the new public key. Old BOMs keep their old key id and still verify against the old public key, so keep it listed |
| Erase stored examples (a client, an end user, hashes or text) | `python -m ratchet.admin erase --reason "<why>" --client <name>` (or `--end-user -` to read the value from stdin, `--hash <hash>`, `--contains "<text>"`) shows what would go; add `--apply` to erase. Or `POST /ratchet/admin/privacy/erase` with an admin key. It runs next to a serving gateway; no restart. See [PII leak](#pii-leak) |
| Apply the retention windows now | `python -m ratchet.live purge [--client <name>] [--older-than-days N] [--dry-run]` (the gateway does it hourly) |
| Hand over data (an audit, a client leaving) | `python -m ratchet.admin export examples --client <name> --out examples.ndjson` (also `requests`, `versions`, `events`, `erasures`), or `GET /ratchet/admin/export/<kind>` with an admin key |

Never keep two versions in `live`. `Ledger.live()` returns the newest one, and the other would never be retired automatically.

SQL equivalents (no `stage_since` update, so prefer Python):

```sql
UPDATE versions SET state='rolled_back', traffic_pct=0 WHERE id=7;
INSERT INTO events (ts, day, kind, detail) VALUES (strftime('%s','now'), 0, 'rollback', 'manual: bad outputs');
```

## Playbooks

### Teacher API down (Token Factory errors or timeouts)

**Symptoms:** `/v1/chat/completions` returns **502** `teacher_unavailable` (`teacher unavailable: upstream 503`, or `upstream 502 upstream_unreachable` when Token Factory cannot be reached) or **504** `teacher_timeout` for cold-start, escalated and pinned requests, which need the teacher's answer. After 5 such failures in a row a `circuit` event `teacher: closed->open after 5 failures (...)`, and from then on those requests get **503** `teacher_circuit_open` at once, with `Retry-After` until the breaker's next probe. `audit_error` events in the release log, and `shadow_error` events (`arbitration`) while a shadow runs, each logged on the 1st, 10th, 100th and 1000th failure; the detail names the status and code (`ticket-triage: UpstreamError 504 upstream_timeout`). With the opt-in options below, `fallback_teacher` or `degraded` events and the same routes in `/ratchet/stats`. A **502** `upstream_auth_error` or `upstream_not_found` is not an outage: see step 1.

**What the gateway already does** ([ADR 0014](adr/0014-upstream-failure-semantics.md), [ADR 0019](adr/0019-teacher-breaker-degraded-mode-and-fallback-teacher.md)): each teacher call is retried up to `RATCHET_TEACHER_RETRIES` (2) times within `RATCHET_TEACHER_DEADLINE_S` (180 s), with backoff, on connection failures and 408/409/429/5xx; a read timeout (`RATCHET_TEACHER_TIMEOUT_S`, 120 s) is retried once. So a short blip costs latency, not errors. OpenAI SDKs retry the 502 and 504 twice more on their own. When Token Factory asked for a wait longer than 8 s (`Retry-After` on a 503, say), the gateway does not hold the request for it: it answers at once and passes the wait on as `Retry-After` (clamped to 1 to 60 s), which the SDKs honour. After `RATCHET_TEACHER_BREAKER_FAILURES` (5) failed calls in a row the teacher's breaker opens: for `RATCHET_TEACHER_BREAKER_COOLDOWN_S` (30 s) no request waits on the teacher (a 503 with `Retry-After`, which the SDKs honour up to 60 s), then one request probes it; each failed probe doubles the wait up to 300 s, and a successful one closes the breaker (`teacher: half_open->closed`). So a long outage no longer ties up a worker thread per request for the whole deadline.

**Impact:** requests the student serves with confidence still succeed (INV-15): when the audit or the shadow's arbitration fails, the student's answer is served and the failure is counted. Until the breaker opens, each failed audit waits for the teacher first (up to the 180 s deadline when the teacher hangs), so audited requests are slow; once it is open, audits and arbitrations are skipped at once (not counted as more errors). A failed or skipped audit is not counted as an audit, so canary promotion and rollback pause until the teacher is back. A shadow's disagreements go unarbitrated and count against it, so a shadow running through a long outage will likely be rejected; the reason says how many were unarbitrated, and the next night trains a new candidate.

**Degraded options** (opt-in, restart to change; [CONFIGURATION.md](CONFIGURATION.md#when-the-teacher-is-down)):

- **A fallback teacher**: `RATCHET_TEACHER_FALLBACK_MODEL` set to another licensed model on Token Factory (`nvidia/nemotron-3-super-120b-a12b`, say), optionally at `RATCHET_TEACHER_FALLBACK_BASE`. Escalations, cold starts and pinned requests are answered by it when the teacher is unavailable (route `fallback_teacher`, the response's `model` is the fallback's id, priced at its price). It helps when one model is overloaded; when all of Token Factory is down it fails too, and clients get the teacher's error. Its answers are not stored unless `RATCHET_STORE_FALLBACK_TEACHER=1`; then each example names the fallback model and its license. A model outside the license registry stops startup.
- **Serve the student**: `RATCHET_ON_TEACHER_FAILURE=serve_student`. An escalated request whose student gave a valid answer gets that answer, below its threshold, marked `ratchet.degraded: "teacher_unavailable"` and `X-Ratchet-Degraded: teacher_unavailable` (route `degraded`). Clients that cannot use a lower-confidence answer should check the flag. Nothing is stored, audited or shadowed, so releases are not affected. Pinned and teacher-only requests, requests with `tools`, cold starts and requests whose student call failed still get the error.

**Steps:**

1. `upstream_auth_error` (401/403): `NEBIUS_API_KEY` is wrong, expired or lacks access to the model. `upstream_not_found` (404): `RATCHET_TEACHER_MODEL` or `RATCHET_TEACHER_BASE` is wrong. Fix the setting and restart; retrying will not help, and the gateway tells SDKs so (`X-Should-Retry: false`). These do not open the teacher's breaker, but with `serve_student` they are answered degraded: check the `degraded` events' detail (`UpstreamError 401`).
2. Otherwise check the Token Factory status page and `scripts/day1_check.py`. `Ratchet.health()["breakers"]["teacher"]` (Python) and the `circuit` events show the breaker's state.
3. Short outage: tell your app to retry the 502s, 503s and 504s after their `Retry-After`. Long outage: turn on a degraded option above if your app can take its answers. Set `RATCHET_AUDIT_RATE=0` and restart if audited requests are slow before the breaker opens (with the breaker open they are not). Restore the audit rate afterwards: audits drive canary promotion and rollback. If requests pile up before the breaker opens, lower `RATCHET_TEACHER_BREAKER_FAILURES` or `RATCHET_TEACHER_DEADLINE_S` (and `RATCHET_TEACHER_TIMEOUT_S`) so they give up sooner.
4. Don't run Night School during the outage.
5. After the outage, turn `serve_student` off again if you only wanted it for the outage: degraded answers are below the precision target the release gate calibrated for.

### Rate limited

**Symptoms:** clients get **429**. Tell the two kinds apart by `error.code`:

- `daily_quota_exceeded` (`type` `insufficient_quota`): a gateway API key used up `RATCHET_API_KEY_DAILY_REQUESTS`. `Retry-After` and `X-RateLimit-Reset-Requests` say when it resets (00:00 UTC), and `X-Should-Retry: false` stops OpenAI SDKs from retrying. The dashboard's **Trust and privacy** panel shows requests per key.
- `upstream_rate_limited` (`type` `rate_limit_error`): Token Factory rate-limited the teacher. The gateway already waited for Token Factory's `Retry-After` and retried (twice, if each wait was 8 s or less); `Retry-After` on the 429 is Token Factory's wait, clamped to 1 to 60 s, and SDKs honour it. Five such 429s in a row open the teacher's breaker: clients then get a 503 `teacher_circuit_open` with the breaker's wait instead, and the teacher gets no calls until its probe.

**Steps:**

1. Daily quota: raise `RATCHET_API_KEY_DAILY_REQUESTS` and restart, give the client its own key, or wait for midnight UTC. A refused request was not logged and used no quota.
2. Token Factory rate limit: check the account's limits in the Token Factory console. Fewer escalations mean fewer teacher calls: look at the escalation rate (drift, or a broken student) and the audit rate. Lower `RATCHET_AUDIT_RATE` (not to 0 while a canary is in flight) and avoid running Night School's judge during peak traffic.
3. Repeated `audit_error` events with `UpstreamError 429` mean audits are being rate-limited: the student's answers are still served, but canary promotion slows down.

### Student endpoint down or adapters lost

**Symptoms:** escalation rate jumps towards 100% and cost goes back to the all-teacher baseline. Requests still succeed: student failures fall back to the teacher (INV-7). In the release log:

- **Endpoint down or hanging:** `student_error` events (on the 1st, 10th, 100th and 1000th failure) naming a timeout, a connection error or a 5xx, then `circuit` `student: closed->open after 5 failures (...)`. From then on requests skip the student and go straight to the teacher (route `student_breaker` in `/ratchet/stats` → `tasks[].routes`), so they no longer wait out the student's timeouts. Every 30 s, doubling to 300 s while it stays down, one request probes the endpoint (`open->half_open`, then `half_open->open ...` or `half_open->closed`; at 300 s only the 1st, 10th, 100th and 1000th probe are logged). A probe on a hanging endpoint is never replaced while its call can still be running (the student's connect, write, read and pool timeouts plus 5 s, 37 s by default), and a probe that outlives even that still reopens the breaker when it fails, so the wait doubles however slow the endpoint is.
- **Endpoint restarted:** vLLM forgets every adapter loaded at runtime and answers 404 ``The model `student-vN` does not exist.`` The `student_error` detail says `AdapterMissing 404 adapter_missing`. The breaker stays closed (the endpoint answers), and the gateway reloads the adapter by itself: one `adapter_reloaded` event per version within seconds, at most one try per adapter per minute. Shadow and canary adapters are reloaded the same way when their traffic hits the 404.

**What the gateway already does** ([ADR 0015](adr/0015-student-circuit-breaker-and-adapter-reload.md)): opens the student breaker after `RATCHET_STUDENT_BREAKER_FAILURES` (5) endpoint failures in a row, probes again after `RATCHET_STUDENT_BREAKER_COOLDOWN_S` (30 s), and reloads lost adapters. A breaker that closes again and `adapter_reloaded` events need no action.

**Steps:**

1. The breaker stays `open` (probes keep failing): check the endpoint (`bash deploy/student_endpoint.sh status` prints its id and state, or the console) and start it if it is stopped (`bash deploy/student_endpoint.sh start`, the same endpoint and URL). The next successful probe closes the breaker; nothing else needs restarting.
2. `adapter_reload_failed` keeps repeating: the detail names the error. A 404 or 400 from `/v1/load_lora_adapter` usually means the adapter path is wrong (`RATCHET_ADAPTER_ROOT`, default `/data/adapters`, must be where the endpoint sees the bucket) or the endpoint was started without `--enable-lora` and `VLLM_ALLOW_RUNTIME_LORA_UPDATING=True`. A 401 means `RATCHET_STUDENT_KEY` is wrong.
3. **Manual fallback:** reload the adapters of every version in `live`, `canary` and `shadow` by hand:

   ```bash
   python -m ratchet.live reload
   ```

4. **Emergency fallback:** unset `RATCHET_STUDENT_BASE` and restart the gateway. Student calls then go to the hosted, untuned Nano 30B on Token Factory. Precision may drop, and audits will show it.
5. A breaker that opens and closes many times a day means the endpoint is flaky (scale-to-zero cold starts, or too little GPU for the load). Raise `RATCHET_STUDENT_TIMEOUT_S` for slow cold starts (a probe is then given as long before another request tries the endpoint), or keep a replica warm. While a shadow's breaker (`shadow:`) is open the shadow collects no evidence, so its release waits.

### Precision alert on live

**Symptoms:** `alert` events saying "live vN: S/N audits agree; e-value E >= 100 (anytime-valid, alpha 1%); 95% CS [lo, hi]" (the default rule; [Reading an e-value event](#reading-an-e-value-event)), or with `RATCHET_ALERT_EPROCESS=0` "live vN audited precision X% (n=…) below floor".

**Steps:**

1. Check `GET /ratchet/stats` → `days[-1].audit_agreement` and whether escalations jumped (drift).
2. If drift is the cause (a new product or new wording), the student's confidence usually drops and it escalates. The `alert` means it is confidently wrong on some slice, which is serious.
3. Immediately take the live version out (manual intervention above) or restore the previous good version. All traffic then goes to the teacher, which costs more but is correct.
4. Inspect recent audited disagreements:

   ```sql
   SELECT id, task, confidence FROM requests WHERE audited=1 AND audit_agree=0 ORDER BY id DESC LIMIT 50;
   ```

5. Let Night School retrain. The disagreements are already training data.

### Reading an e-value event

With the anytime-valid rules ([ADR 0025](adr/0025-anytime-valid-release-gate.md)), `rollback`, `alert`, `canary`, `live` and `inconclusive` events carry the evidence the gate decided on, for example:

```
v3 rolled back: 3/7 audits agree; e-value 64.9 >= 20 (anytime-valid, alpha 5%); 95% CS [0.041, 0.908]; traffic back to live v1
```

- `3/7 audits agree`: the counts. For a rollback they cover every audit of the canary's life; for an alert, the audits since the version went live; for a promotion (`canary`, `live`) and `inconclusive`, the current stage.
- `e-value 64.9 >= 20`: how strongly the audits contradict "precision is at least the floor" (for a promotion: "at most the floor"). 1 means no evidence either way; the threshold is 1/alpha (`RATCHET_ALPHA_ROLLBACK` 0.05 → 20, `RATCHET_ALPHA_ALERT` 0.01 → 100, `RATCHET_ALPHA_PROMOTE` 0.05 → 20). The chance that a version whose precision stays at or above the floor ever reaches it is at most alpha, however often the gate runs. Small counts can be decisive: 0 agreeing out of 2 gives 29.8.
- `95% CS [0.041, 0.908]`: an anytime-valid 95% confidence sequence for the version's precision (computed on a 0.001 grid). Its upper end below the floor means the version is worse; a range that straddles the floor after hundreds of audits is what `inconclusive` means.
- Re-check any of these from the counts: `python -c "from ratchet import stats; print(stats.e_bernoulli(3, 7, 0.92, 'below'), stats.confidence_sequence(3, 7))"`, or read `versions[].evidence` in `GET /ratchet/stats` for the canary and the live version.
- `python -m sim.simulate --bad-deploy-version 3 --compare-release` shows both rules on the same simulated traffic (mock models).

### Bad deploy / rollback

**Symptoms:** a `rollback` event. The system has already moved traffic back. The event names the evidence: the Wilson bound and `n` by default, or the counts, e-value and confidence sequence under `RATCHET_SEQUENTIAL=1` ([Reading an e-value event](#reading-an-e-value-event)). The answers the version served before it was rolled back are its requests with `served_by='student'` (`Ledger.served_counts()`).

**Steps:**

1. Confirm the right adapter was loaded: the adapter folder's `ratchet_manifest.json` should show the expected example count.
2. Compare the version's golden `precision` with its audited precision. A big gap means a training or serving mismatch, such as the wrong checkpoint, a tokenizer or chat-template change, or a vLLM version change.
3. Don't delete the rolled-back adapter until you understand why it failed.

### Persistent rejections

**Symptoms:** a `rejected` event every night.

- "golden precision < 95%": the base model may be too small for the task, the task too subjective, or the task's fields too strict (an `exact` field where a `number` with a tolerance, a `set` or a `label` with aliases fits; [CONFIGURATION.md](CONFIGURATION.md#tasks)). Check the task's `fields`, and `golden_invalid_refs` in the version's eval: references the spec refuses are not graded.
- "golden coverage < 25%": too few examples per pattern. Wait for more data, or lower `min_coverage` in code.
- "regresses vs live": live is already good. This is expected and harmless. If the difference looks like noise (a few rows on a small golden set), `RATCHET_PAIRED_GATE=1` compares the two versions row by row and rejects only a significant regression ([ADR 0026](adr/0026-certified-thresholds-and-a-paired-release-gate.md)).
- "serves N fewer of M golden rows than live", "N negative flips vs P positive flips", "negative flip rate …": the paired gate found a regression that is larger than `tolerance` and significant (McNemar p below `RATCHET_REGRESSION_ALPHA`), or a flip rate above `RATCHET_MAX_NFR`. The candidate is worse on the same rows; look at the tasks its `per_task` block shows weakest.
- "certified lower bound X% < Y%": `RATCHET_MIN_CERTIFIED_PRECISION` is set and the held-out rows do not support it. More golden rows fix this faster than a better model: `python -m ratchet.stats plan --target Y` says how many served rows the target needs. "no certificate to check": the evaluator wrote none (a custom evaluator, or one from before v0.5).
- "shadow precision/coverage": real traffic differs from the golden set. Check for drift.
- "shadow precision ... (N unarbitrated counted as disagreements)": the teacher failed some arbitrations (`shadow_error` events). If N explains the shortfall, the candidate may be fine; the next night trains a new one.

### Checking a certificate

`GET /ratchet/versions/{id}/certificate` gives a version's status (`certified` or `not certified` from the latest certificate; `no certificate` before v0.5) with the signed certificate from training and the latest re-score's ([API.md](API.md#get-ratchetversionsidcertificate)). To check a BOM someone sent you, or one you are about to publish:

```bash
curl -s https://<gateway>/ratchet/versions/3/bom > bom.json
python -m ratchet.stats check bom.json --public-key "<base64 public key>"
```

Exit 0 means the signature holds and every bound, verdict, frontier row, per-task row, Learn-then-Test step and paired p-value follows from the signed counts. Exit 1 prints each `FAIL:` line: a signature or hash failure means the document was edited or signed by another key; a number that does not follow from the counts means whoever wrote it computed it differently (or edited the document and re-hashed it). A certificate is about agreement with the teacher on the golden rows; read the `assumptions` before quoting it. When a live version is `not certified` but its audits look fine (`live` evidence), the golden set is usually too small: `python -m ratchet.stats plan` says how many served held-out rows the target needs.

### Proving what an adapter was trained on

Each BOM written since v0.5 signs a root per split over the exact training and golden examples ([ADR 0027](adr/0027-committed-training-sets.md), [API.md](API.md#committed-training-sets)). To answer "was this request used to train adapter 3?" for a customer or an auditor, with an admin key:

```bash
curl -s localhost:8000/ratchet/versions/3/bom > bom.json
curl -s "localhost:8000/ratchet/versions/3/proof?split=train" -H "Authorization: Bearer $ADMIN_KEY" \
  -H 'content-type: application/json' -d @request.json > proof.json    # {"task": ..., "messages": [...]} or {"key": ...}
python -m ratchet.proof verify proof.json --bom bom.json --public-key "<base64 public key>"
curl -s localhost:8000/ratchet/versions/3/heldout -H "Authorization: Bearer $ADMIN_KEY" > heldout.json
python -m ratchet.proof heldout heldout.json --bom bom.json --public-key "<base64 public key>"
```

Send them `bom.json` and `proof.json` (or `heldout.json`) and the public key you published; they need nothing else. `result: absent` for a request you expected to be included usually means the key differs: the request's task, a `stop`, `response_format` or `tools` field, or the redaction setting (`python -m ratchet.proof key ... [--no-redact]` prints the key the gateway would compute), or the teacher's answer was never stored (`skip_example`, or `requests.stored = 0`). `Ledger.versions_containing(key)` lists every version whose training set holds a key. A 404 `commitment not available` means the version was trained before v0.5. Share a held-out certificate with auditors, not publicly: each non-membership proof names a neighbouring training example's hashes.

### Reading the replay frontier

The ledger replay ([ADR 0028](adr/0028-designed-propensity-replay.md), [API.md](API.md#ledger-replay)) answers "what would last week have looked like at another threshold or audit rate?" for one version:

```bash
curl -s "localhost:8000/ratchet/replay/frontier?target=0.97&since_day=12&until_day=18" | jq '.rows[] | [.threshold, .coverage, .precision, .interval, .savings]'
python -m ratchet.replay --db "$RATCHET_DB" --version 3 --sweep 0.4:0.95:0.05 --audit-rate 0.02   # the same from the ledger file
```

- Read each row as coverage (exact), precision with its approximate interval, and savings at that threshold. Precision below the live threshold comes from escalations, which the teacher labels every time; above it from audits, weighted by 1 / audit rate. A narrow interval needs labels: with an audit rate of 0.05, a threshold above the live one rests on one served answer in twenty.
- `precision_reason` "audit rate was 0: precision above the live threshold cannot be identified" means nothing labels the answers the student served: raise `RATCHET_AUDIT_RATE` above 0 for a while before asking again. A null precision is never a zero.
- `suggested` is advice. Its interval is approximate (Wilson at Kish's effective sample size) and computed on the same traffic you are looking at, so it is not a certificate, and nothing applies it. To act on it, let Night School pick the threshold on held-out golden rows (`RATCHET_CALIBRATION=ltt` with `RATCHET_CERT_TARGET` certifies the threshold it picks) and check the result with `GET /ratchet/versions/{id}/certificate`. Never edit a version's threshold by hand to match the replay.
- `money_left_on_table` (when the suggestion is below the current threshold) is the savings the current threshold gave up on that traffic. When the suggestion is above the current threshold, the replay's interval does not support the target at the current one: compare with the live evidence (`/ratchet/stats` `versions[].evidence`) and the certificate before worrying, since the replay's interval is the least strict of the three.
- `calibrated_baseline.R` far from 1 means the teacher writes a different number of reasoning tokens than `RATCHET_TEACHER_REASONING_TOKENS` assumes, so every per-token savings figure for student-served answers is off by about that factor. The replay already prices unaudited answers at R times the estimate; to fix the stored figures going forward, change the setting until R is close to 1 on later traffic, and record why.
- `excluded_by.before_logging` counts rows written before this feature; `no_student_answer` rows the student never answered (endpoint down); `pinned_now` a task pinned since. One version at a time: a request for two versions is a 400, because their confidence scales differ.

### Savings receipts

Each ledger day gets a signed savings receipt for the gateway and one per API client ([ADR 0029](adr/0029-verified-savings-receipts.md), [API.md](API.md#savings-receipts)). They are what the fee is computed from, so seal them every day and keep them.

```cron
# Seal the savings receipts of every ledger day that ended at least 5 minutes ago, hourly
20 * * * *  cd /opt/ratchet && . ./.env && python -m ratchet.live seal >> /var/log/ratchet-seal.log 2>&1
```

A ledger day is not a UTC day: day 1 starts at the first request's time, and each day is 24 hours from there (`Ledger.day_of`), so with a first request at 15:00 UTC every day ends at 15:00 UTC. Run the command hourly, not once a day: a run with nothing due prints `nothing to seal` and writes nothing, and an hourly run seals each day within the hour after it ends.

- `python -m ratchet.live seal` prints `sealed the receipt of day N (acme)` per receipt, or `nothing to seal`. It seals every day that ended and has no receipt yet, oldest first, so a missed run is caught up by the next. The gateway also seals when anyone asks for a receipt, so the two can run at once: each receipt is written once and the second writer keeps the first's.
- A task shows `insufficient_audits` until it has `RATCHET_RECEIPT_MIN_AUDITS` (30) audits that day (in a client's receipt, that client's). Its savings are reported but not billed. To bill a small task sooner, raise `RATCHET_AUDIT_RATE` for it (more teacher spend), never lower the minimum below what the interval needs.
- `calibration.tasks.<task>.ratio` far from 1 means the teacher's bills differ from the token formula (`RATCHET_TEACHER_REASONING_TOKENS`). The receipts already correct for it; the dashboard's savings do not. Compare with the replay's `calibrated_baseline` ([Reading the replay frontier](#reading-the-replay-frontier)).
- `basis: formula` and `fee_nusd: 0` mean no task was calibrated that day. That is the intended outcome for a quiet day, not an error.
- Every month, reconcile the receipts' summed `totals.teacher_cost_nusd` (nano-USD) with the Token Factory bill for the same API key. Receipts prove the ledger was not changed after sealing; they cannot prove the costs logged at request time.
- An `integrity` event naming a receipt means a request row of a sealed day was changed outside Ratchet. The receipt still stands (it cannot be edited); proofs and leaves for that day answer 500 until the rows are restored from a backup taken before the change.
- With `RATCHET_REQUEST_RETENTION_DAYS` set, request rows older than the window are folded into daily sums ([CONFIGURATION.md](CONFIGURATION.md#privacy-and-security)), and a receipt needs all of its day's rows: the gateway's hourly retention, `python -m ratchet.live purge` and a live night seal every day that has ended before they fold. After the fold a receipt still verifies, but its leaves and proofs answer 410; hand customers the leaves (`GET /ratchet/receipts/{day}/leaves`) before the window passes if they want to check them later. A `receipt_skipped` event means a day was folded before it could be sealed, and it has no receipt.
- Changing `RATCHET_TAKE_RATE` or `RATCHET_RECEIPT_MIN_AUDITS` affects days sealed after the change only. Each receipt states the values it used.

### Teacher answers not stored

**Symptoms:** `skip_example` events reaching the 100th or 1000th of a reason; Night School says `skip` (fewer than 50 new examples) although `escalation_rate` is high; `SELECT task, COUNT(*), SUM(stored) FROM requests WHERE served_by='teacher' GROUP BY task` shows far fewer stored than escalated.

- `finish_reason length`: the teacher runs out of tokens, usually while reasoning: at the client's `max_tokens`, or at `RATCHET_TEACHER_MAX_TOKENS` (8,192) when the client sent none. Check the answer length on Token Factory; shorten the system prompt's demands or ask for a shorter answer. If clients send tight limits to a reasoning teacher, set `RATCHET_TEACHER_REASONING_HEADROOM` (about `RATCHET_TEACHER_REASONING_TOKENS`); with it, an answer over the client's own limit is also `length` ([CONFIGURATION.md](CONFIGURATION.md#generation-parameters-and-token-caps)). The client still got the answer, with `finish_reason: "length"`.
- `empty answer`: the teacher never left its reasoning (`<think>` without `</think>`), or the endpoint returned the answer in a reasoning field only. Check the model ID and whether a reasoning parser is on.
- `invalid answer`: the teacher does not answer a structured task with JSON. Check the task's system prompt (it must ask for JSON only), and have the client send `response_format: {"type": "json_object"}`, which reaches the teacher (and is stored with the example).
- `invalid field <path>`: the teacher's JSON parses, but a field the task's spec checks is missing or not a value it accepts: `invalid field category` for `"Billing Inquiry"` when the vocabulary is `billing`, `technical`, ... ([CONFIGURATION.md](CONFIGURATION.md#tasks)). Make the system prompt list the allowed values exactly (the built-in prompts take them from `ratchet/vocab.py`), or add the teacher's spelling to the field's `aliases`. Loosen the field (`required=False`, no `values`) only if the answers really are right: an answer the spec refuses is also refused as a student answer and as a golden reference.
- `invalid after redaction`: a redaction pattern matched a number outside quotes in the JSON (a card-like order id), or an identifier inside a field the spec checks. Make the prompt ask for ids as strings.
- `client logit_bias`, `client penalty`, `client temperature above 1`, `client vendor field`: clients send sampling fields that can steer what the teacher writes, so their escalations are served but not stored (threat model T2). Expected for clients that need them. If one client sends them by habit (a framework default such as `temperature: 1.2`), ask it to drop them, or take the field out of `RATCHET_TEACHER_PARAMS`: a field the teacher never gets does not stop storage.
- `finish_reason other` or `unknown`: the teacher endpoint reports a stop reason other than `stop`, `tool_calls` or the usual ones (a missing, null or empty one is read as `stop`). The student's samples with such a value get no vote either, so the student escalates everything. `scripts/day1_check.py` prints each model's `finish_reason`; look at a raw response before changing anything, because storing only complete answers is invariant 16.

### Cost spike

**Checks:**

1. Is `escalation_rate` up? That means drift, or a broken student.
2. Is shadow running? Shadow adds student sampling and arbitration calls to every request.
3. Is `RATCHET_AUDIT_RATE` too high?
4. Is the vLLM endpoint left running (GPU-hours)? `python -m ratchet.cost report --days 1` shows today's endpoint hours and whether they paid for themselves.
5. Are teacher outputs long because of reasoning? Look at token usage on Token Factory.

**Mitigations:** lower the audit rate (but not to 0 while a canary is in flight, since audits drive promotion and rollback), stop the endpoint when idle, lower `RATCHET_TEACHER_MAX_TOKENS` (the most output tokens any teacher call may use, 8,192 by default) or ask clients to send a smaller `max_tokens`.

### Cost and the self-host recommendation

`python -m ratchet.cost report [--days 7]` (or `GET /ratchet/cost?days=7`) prints savings two ways: per token (the student at the hosted Nano price, as on the dashboard) and by the GPU-hours actually paid for the student endpoint and the training jobs. Below break-even the GPU-hour savings are negative; that is the honest number, not a bug. The prices are list prices, labelled unverified until `RATCHET_PRICES_VERIFIED=1` ([CONFIGURATION.md](CONFIGURATION.md#cost-accounting)).

**Keep the uptime records complete.** The report knows only the endpoint hours that were recorded: run `bash deploy/student_endpoint.sh` / `stop` next to the ledger, or record `python -m ratchet.cost endpoint up` / `down` yourself (see [Manual interventions](#manual-interventions)). A warning counts student answers in hours with no recorded uptime, which are priced per token. Compare `endpoint_hours` with the Nebius bill weekly.

**Reading the recommendation:**

| `recommendation` | Means | Do |
|---|---|---|
| `self_host` | The endpoint served at least the break-even volume of student answers per hour it ran, and more than the volume at which the hosted fallback would be cheaper | Keep it. If the explanation says it was idle much of the time, scale it to zero between bursts. If it says the endpoint covers its hours but not the whole bill, training jobs cost more than the endpoint's margin: check how often Night School trains |
| `hosted_fallback` | Below the hosted fallback's crossover: per answer the untuned hosted Nano would cost less than the endpoint. The explanation says which case it is. Below break-even ("Self-hosting does not pay") the endpoint also costs more than the teacher spend it avoids. Between break-even and the crossover ("Self-hosting beats the teacher, but the hosted fallback would cost less per answer"; at list prices about 1,600 to 52,000 student answers an hour, the 10M-requests-a-month customer included) the GPU-hour savings are positive and the hosted model wins only if it serves about as many requests as the tuned student | Below break-even: leave `RATCHET_STUDENT_BASE` empty so the untuned hosted Nano on Token Factory serves the student role per token, or run the endpoint only while traffic arrives. Night School still needs the endpoint while it loads and evaluates a new adapter: start it for the night run (with `RATCHET_STUDENT_BASE` set in the night job's environment) and stop it after; each start and stop is recorded, and answers in hours without uptime count as the hosted fallback's. Between break-even and the crossover: run a day on the hosted fallback and compare the student's share of requests (`/ratchet/stats`) with a day on the endpoint before switching; more escalations to Ultra can cost more than the GPU-hours saved |
| `scale_to_zero` | More than half of the endpoint's hours fell in hours (a live ledger; days in the simulation) when no request reached the student, or neither the endpoint nor the hosted fallback pays at this volume | Stop the endpoint when idle (`bash deploy/student_endpoint.sh stop`) and start it for busy periods |
| `insufficient_data` | No endpoint uptime recorded in the window, or the student has not served a request yet | Record uptime; wait for a trained version |

`break_even_student_requests_per_gpu_hour` is the volume to aim for; `observed_student_requests_per_endpoint_hour` is where you are. More traffic per GPU (more tasks or customers as LoRA adapters on one vLLM endpoint) moves observed up; scale-to-zero moves endpoint hours down.

### Opt-out requests (an end user or a client)

Opt-outs only remove storage; requests are still answered, audited and logged ([API.md](API.md#end-users-and-storage), FR-13.1, FR-13.2).

| Who asks | Do |
|---|---|
| An end user of one client | The client calls `POST /v1/ratchet/no-train` with `{"end_user": "<the value it sends as user or safety_identifier>"}` and its API key. A request is matched on both fields it sends. The registration is scoped to the key's name: keep the name when rotating the key |
| An end user, through you (a data subject request) | `POST /v1/ratchet/no-train` with an admin key and no `client`: one registration (scope `*`) for every API key, also keys added later, and keyless requests. Remove with `DELETE` and the same body |
| A client, for all its traffic | Add `name:store=0` to `RATCHET_KEY_LIMITS` and restart |
| A task's data owner | `store: false` in the task's spec (`PUT /ratchet/tasks/<name>`), or the name in `RATCHET_NO_STORE_TASKS` and restart |
| Everyone (zero retention) | `RATCHET_STORE_EXAMPLES=0` and restart; Night School then skips every night (a task whose spec changed is still re-scored, on the golden rows stored before) |

Check `/ratchet/stats` → `privacy.not_stored_by_reason` and the response's `ratchet.store_opt_out`. An opt-out does not delete what was stored before; to remove that too, erase it:

| Who asks | Do |
|---|---|
| An end user of one client | The client calls `DELETE /v1/ratchet/examples` with `{"end_user": "<value>"}` and its API key (after registering no-train, so nothing new is stored) |
| An end user, through you | `python -m ratchet.admin erase --reason "<request id>" --end-user -` (every scope the ledger holds end users in; add `--client <name>` for one), check the dry run, then `--apply`. Run it with the gateway's environment (`docker compose exec -T ratchet ...` on the demo VM): it hashes with the gateway's `RATCHET_END_USER_KEY` (or the key generated into the ledger when the gateway has none) and refuses, without writing anything, when the key it finds is not the one the ledger recorded |
| A client leaving | `python -m ratchet.admin export examples --client <name> --out <file>` if the contract says so, then `python -m ratchet.admin erase --reason "<why>" --client <name> --apply`. `RATCHET_ERASURE_SHARED=keep` keeps examples another client sent too |

Keep `RATCHET_END_USER_KEY` stable: changing it unlinks every no-train registration (an `end_user_key` event) and every end user's stored examples, which an erasure by end user then cannot find.

### Importing labelled data

Labels you already own (a fine-tuning dataset written by your team, a golden set your reviewers checked) or outputs of an open teacher you ran yourself can go straight into the ledger ([ADR 0091](adr/0091-human-labels-as-a-provenance-source.md)). Run the importer with the gateway's environment (`RATCHET_DB`, `RATCHET_TASKS_FILE`, `RATCHET_TEACHER_LICENSES`, `RATCHET_REDACT_PII`); it may run while the gateway serves.

```bash
# 1. Dry run: the ledger file is read (read-only) into an in-memory copy, nothing written. Read the counts and the
#    first problems. A --db path (or RATCHET_DB) with no ledger file is refused, so a typo creates nothing.
python -m ratchet.importer labels.jsonl --task ticket-triage --source human \
    --attest "Written by the support leads, Q3 2026; owned by Acme" --dry-run
# 2. Import. --client attributes the rows to an API key name (for erasure and export by client).
python -m ratchet.importer labels.jsonl --task ticket-triage --source human --attest "..." --client acme
# A human-verified golden set: never trained on, even past the frequent-request rule.
python -m ratchet.importer golden.jsonl --task ticket-triage --source human --attest "..." --golden-only
# An open teacher's outputs (must be in the registry; closed models are refused with their terms quoted).
python -m ratchet.importer outputs.jsonl --task ticket-triage --source teacher:nvidia/nemotron-3-super-120b-a12b
```

- The format is OpenAI's chat fine-tuning JSONL (`{"messages": [..., {"role": "assistant", "content": "..."}]}`, the answer last) or Ratchet's own `train.jsonl`. Every label must be valid under the task's spec; an invalid row is counted (`invalid_by_reason`) and listed with its line number, the rest import.
- `refused_by_reason: {"trained": n}` on a golden-only import: those prompts (or a variant of them) are already training rows, so they cannot be held-out evidence. Use other prompts for the golden set.
- `duplicates`: the ledger already had the example with an equal or stronger label. An import replaces teacher answers and client corrections (human labels beat teacher answers; an operator's import beats a client's correction) and earlier imports of the same rows.
- The task must be registered (`RATCHET_TASKS_FILE` or the admin API), and neither pinned nor no-store; zero retention refuses every import.
- Imported rows are no occurrence of their request: a new example starts at `seen` 0 (an existing one keeps its count), so an import never pushes a golden prompt past the frequent-request rule. Only real requests do.
- New data for Night School is new training rows (50 by default, `run_night`'s `min_new_examples`). A label that replaces a teacher's answer on an existing training row adds no row, so an import (or corrections) that only relabels does not count: the night says `only 0 new examples` and skips. The relabelled rows are trained on at the next night with enough new rows, which trains on the whole current dataset, labels included.
- To undo an import, erase its rows: `python -m ratchet.admin erase --reason "..." --client <name> --apply` when you passed `--client`, else by `--hash` from `python -m ratchet.admin export examples --task <task>` (rows with `"origin": "import"`).

### Human feedback alert

A `human_alert` event means the clients' verdicts (`POST /v1/ratchet/feedback`) on a version's own answers are well below the floor. It is a second opinion, biased toward what people notice, and never moves a release.

1. Compare with the audited precision: `/ratchet/stats` → `feedback.by_version` (human) next to the version's audits (`alert` and `rollback` events, the dashboard's audit agreement). If the audits agree, follow [Precision alert on live](#precision-alert-on-live).
2. If only humans disagree, the teacher may be wrong on those requests (the audits grade against the teacher): read the stored corrections (`python -m ratchet.admin export examples --task <task>` and look for `"provenance": {"teacher": "human", ...}`) and the task's spec. Corrections are training data for the next night, which re-scores the live version on a golden set that now includes any human labels in it.
3. One client sending most of the `incorrect` verdicts: check that client's use of the task (`/ratchet/stats` → `clients`). If its clients should not write labels, set `RATCHET_FEEDBACK_CORRECTIONS=0` (verdicts are still kept) or erase its corrections by client.

### PII leak

All request messages escalated or audited to the teacher are stored in `examples.messages`, and copies go to `night/train.jsonl` in the bucket. With `RATCHET_REDACT_PII=1` (the default) emails, phones, cards, IBANs, national IDs, IPs and API secrets are stored as placeholders, so a leak there usually means a name, an address, or an identifier format the redactor does not know. Paste a sample into the dashboard's **Try the redactor** box (or `POST /ratchet/redact`) to see what it catches.

**Steps** (the gateway keeps serving; erasure is a transaction on the same ledger):

1. **Find it.** `python -m ratchet.admin erase --reason "PII leak <ticket>" --contains "<the leaked text>"` is a dry run: it says how many examples match and which versions may have been trained on them. Stored text is already redacted, so search for what leaked (a name, an address), not for an email the redactor replaced. Narrow it with `--client <name>`, or name examples by `--hash` (from `python -m ratchet.admin export examples`).
2. **Erase it.** Run the same command with `--apply`. The text goes from the ledger for good, the hashes are tombstoned (the same text is never stored again, from any client) and the erasure is logged with hashes only (`python -m ratchet.admin export erasures`). Repeat with other spellings of the leaked value.
3. **Adapters.** The `erase` event and the result's `affected_versions` name the versions that may have memorised the text; their BOMs stay as written (they describe what each adapter saw). Tonight's training run leaves the erased rows out. If a named version is live or in canary and the leak is serious, take it out now ([Manual interventions](#manual-interventions)) and release the next night's version; delete the named adapters and old `night/train.jsonl` copies from the bucket, and from backups as your policy says. Ratchet cannot remove what an existing adapter memorised (THREAT_MODEL.md).
4. Follow your legal and notification duties. The erasure record (who, when, why, which hashes) is the evidence.

**Prevention:** keep `RATCHET_REDACT_PII=1`; add a pattern to `ratchet/redact.py` (with a test) for any identifier format you find leaking. Keep sensitive traffic out of storage altogether with an opt-out ([Opt-out requests](#opt-out-requests-an-end-user-or-a-client)), and set `RATCHET_EXAMPLE_RETENTION_DAYS` so stored text does not outlive its use. Planned: NER-based detection for names and addresses, and Token Factory zero-data-retention settings. See [THREAT_MODEL.md](THREAT_MODEL.md) T4.

### Startup refused: RATCHET_TEACHER_LICENSES

The gateway, the simulator, `python -m ratchet.live` or `python -m ratchet.admin` stops with `LicenseConfigError: RATCHET_TEACHER_LICENSES <file>: invalid teacher licenses (N errors)` and one line per problem (`[2].license: 'CC-BY-NC-4.0' is not an allowed license; ...`). The file adds open teachers to the license registry, and every entry is checked conservatively ([CONFIGURATION.md](CONFIGURATION.md#teacher-licenses), [ADR 0090](adr/0090-licenses-travel-with-the-adapter.md)): only OpenMDW-1.1, Apache-2.0, MIT or the NVIDIA Nemotron Open Model License, never a model id in a closed family (a part starting with `gpt`, `o1`, `o3`, `o4`, `claude`, `gemini` or `o` and a digit, or containing `gpt`, `claude`, `gemini`, `davinci`, `babbage`, `codex`, `openai` or `anthropic`), never a router alias (`openrouter/...`, `.../auto`), never a built-in teacher, an `https://` URL. Fix each line the message lists. A teacher whose license is not on that list, or whose name matches a closed family although it is open, needs a reviewed change to `ratchet/licenses.py` instead. Nothing was opened or changed before the stop.

### Judges cannot run Scout

1. `curl -s https://<domain>/scout/status | jq '{judge, runs_today, public_runs_left_today, search}'`.
2. `judge.enabled` false: `RATCHET_SCOUT_JUDGE_CODE` and `RATCHET_SCOUT_JUDGE_RUNS_PER_DAY` are not set in `deploy/vm/.env` (both, or neither: one alone stops startup). Set them and run `update.sh`.
3. `POST /scout/judge` answers 401: the code in the testing instructions does not match the VM's. 429: more than 10 attempts from that address within an hour; wait, or check for guessing in the access log (the code is never in it).
4. `judge.runs_left_today` 0: the judges' reserve and the public's room are used up for today; they come back at 00:00 UTC. Raise `RATCHET_SCOUT_JUDGE_RUNS_PER_DAY` (and the run cap) if judges need more. A restart does not reset the counters any more: they are restored from the ledger.
5. A judge without the code still gets public runs, and a run without search still calls Token Factory.
6. To revoke every session (a leaked code), set a new code and run `update.sh`, then send the new code to the hackathon managers.

### Tavily credits running low or search unavailable

1. `curl -s https://<domain>/scout/status | jq .search`: `available`, `last_error`, `errors_today` and `credits` (`used_this_cycle`, `left`, `public_left_today`, `judge_left`, `exhausted_until`).
2. `last_error` is `HTTPStatusError 401` or `403`: the key is wrong or revoked. Runs still finish without web sources, but the Tavily prize needs working Tavily calls: fix `TAVILY_API_KEY` and run `update.sh`.
3. `exhausted_until` is set: Tavily answered 432 or 433 and searches pause for an hour. Check the plan in the Tavily dashboard; buy credits or wait for the reset, and set `RATCHET_TAVILY_CYCLE_DAY` to the day the dashboard shows.
4. `public_left_today` 0: today's public allowance is spent; public runs answer without search until 00:00 UTC, judge runs still search from their reserve. Nothing to do unless it happens every day early: lower `RATCHET_SCOUT_MAX_RUNS_PER_DAY` or raise `RATCHET_TAVILY_MONTHLY_CREDITS` if you have more credits.
5. Weekly: compare `used_this_cycle` with the Tavily dashboard. The meter counts conservatively (a 5xx or a read timeout is charged at the table amount, because whether Tavily bills those is not documented), so it should read at or above the dashboard.

### Two gateway processes on one ledger

The compose file runs one uvicorn worker (ADR 0006), and Scout's guard, the per-IP windows and the breakers are per process. Until the single-writer lease lands (NFR-60, `gateway_lease` events), check by hand after an unusual deploy: `docker compose ps` shows one `ratchet` container, and its command has `--workers 1`. A second process on the same `RATCHET_DB` doubles every in-memory limit; the Scout counters restored at startup are only read once.

### Startup refused: a reserved setting

The gateway, the simulator or `python -m ratchet.live` stops with `ValueError: RATCHET_<NAME>=... has no effect yet: it is reserved for <feature>, not built yet`. The variable belongs to an evidence feature that is declared but not built in this version ([CONFIGURATION.md](CONFIGURATION.md#reserved-settings), [ADR 0024](adr/0024-evidence-foundations.md)). Ratchet refuses it rather than run without the protection you asked for. Unset it, or set it to the default the message names. A `ValueError` that names `ReleasePolicy.<field>` is a value out of range (for example `RATCHET_LIVE_FLOOR=1`). One that names `RATCHET_PROMOTE_EVIDENCE` or `RATCHET_MAX_STAGE_AUDITS` is a sequential-gate setting that would decide nothing (evidence-based promotion without `RATCHET_SEQUENTIAL=1`, or a cap that would retire every canary); the message says how many audits are needed. `RATCHET_CANARY_AUDIT_RATE` must be more than 0 and at most 1. `RATCHET_CALIBRATION` must be `point` or `ltt`, and `RATCHET_CERT_TARGET` and `RATCHET_CERT_DELTA` strictly between 0 and 1. A message about `RATCHET_SCOUT_JUDGE_*` or `RATCHET_TAVILY_*` names the bad Scout setting ([CONFIGURATION.md](CONFIGURATION.md#judge-access-and-tavily-credits)); it never shows the judge code. `RATCHET_GATEWAY_LEASE` is reserved for NFR-60: keep it unset or `warn`.

## Live measurement run

`python -m ratchet.liverun` ([ADR 0121](adr/0121-live-measurement-method.md)) sends synthetic workload traffic through the live gateway in rounds, runs Night School in process between them, sends a 5% control sample straight to Ultra, and writes one results file behind every live number in the docs. It never calls `/ratchet/*`, and it writes no request text, answer text, key or base URL.

**Before you start:**

- The [day-1 checks](DAY1.md) passed, and `make live-smoke` ran once (cents).
- The student endpoint is up and recorded: `bash deploy/student_endpoint.sh start` (or `python -m ratchet.cost endpoint up`). Without an open `student_endpoint` interval the harness refuses, because the GPU-hours view would miss the endpoint.
- The harness has its own key: `liverun:<key>` in the gateway's `RATCHET_API_KEYS`, and `export RATCHET_LIVERUN_KEY=<key>` in the harness's shell only. Optionally `export NEBIUS_API_KEY_LIVERUN=<a second Token Factory key>` for the arms, so a runaway run cannot drain the demo's key. Once the per-key spend limits land (lane D), add `RATCHET_KEY_LIMITS='liverun:daily_usd=<cap>'` as a server-side backstop.
- The gateway runs the planned gate profile: `RATCHET_SEQUENTIAL=1` and `RATCHET_CANARY_AUDIT_RATE=0.5` in its `.env`, then restart it. `run` refuses without them (the plan's stage table shows why: at the default rates a 10% canary stage alone needs about 7,400 requests). `--gate-profile as-set` measures other settings on purpose.
- Pause the cron nights (`python -m ratchet.live night`) for the run: the harness runs the nights itself. If lane E's retention window is set, it must be longer than the run.
- Run on the host where nights can run, with the gateway's `.env` sourced (same `RATCHET_DB`, task registry and prices): the stock gateway image has neither the `nebius` CLI nor `deploy/` ([DEPLOYMENT.md](DEPLOYMENT.md) step 7).

**Commands:**

```bash
make live-plan                 # free: the plan, no call at all (MAX_USD=30, ROUNDS=4, REQUESTS=1500, WORKLOAD=tickets)
make live-smoke                # 20 requests, about 5 control calls, no nights: cents
make live-run MAX_USD=30       # rounds with nights between; results in evidence/live/<run id>/
python -m ratchet.liverun run --resume evidence/live/<run id> --base-url http://localhost:8000/v1   # after a crash or a stop
python -m ratchet.liverun smoke --repeat-of evidence/live/<smoke id> --base-url http://localhost:8000/v1   # the repeat run
python -m ratchet.liverun report evidence/live/<run id> --bill-usd 14.20 --bill-note "Nebius console, failed jobs included"
python -m ratchet.liverun report evidence/live/<run id> --repeat evidence/live/<repeat id> --publish
python -m ratchet.liverun verify evidence/live
```

The plan prints, before any paid call: requests per release stage for `RATCHET_MIN_AUDITS` audits (at the configured canary audit rate, at 0.5 and at 1.0), the golden size a certifiable first version needs, whether round 1 stores the 50 new examples a night needs, and per round the teacher, control, training and endpoint USD. A plan over `--max-usd` exits 3 having sent nothing. After each round the rest is planned again from what was measured, and the run stops with `plan_over_budget` rather than start a round it cannot finish. The cap is checked before every batch, so a batch already sent can overshoot it by its own cost.

**The run directory** (`evidence/live/<run id>/`, or `--out`): `plan.json` (the first plan, written before round 1), `state.json` (arguments, windows, nights and the arms' spend, rewritten after every batch; what `--resume` reads), `requests.ndjson.gz` (one metadata record per request and control call, no text), `log.txt` (stdout), `results.json` and `LIVE_RESULTS.md`. `results.json` (generator `ratchet.liverun/1`) has, in order: `generator`, `kind` (`run` or `smoke`), `mode`, `run_id`, `started`, `finished`, `stopped` (null or `{reason, detail}`), `code` (`git_sha`, `code_digest`, `version`), `models` (as Token Factory and vLLM list them), `client` (the key's name), `workload`, `settings` (non-secret only, with `gate_profile` and `arms_teacher_key`, the variable's name), `plan`, `rounds` (each round's window, `other_clients`, `unrecorded`, extension, settled, the ledger snapshot `ledger`, the client-side block `client`, the night and the endpoint hours), `arms` (`control`, `untuned`, `scout`), `cost`, `kpis`, `repeat` (null, or the comparison with the other run of a repeat pair), `values` (flat `{live.*: number}`), `sources` (one line per value, or why it was not measured) and `caveats`.

**Exit codes:** 0 done; 1 stopped early with a valid partial results file; 2 bad input or a refused preflight (one line says what to fix); 3 the plan exceeds `--max-usd`.

**Stop reasons** (`results.json` `stopped.reason`, and the `liverun_stop` event):

| Reason | Meaning | What to do |
|---|---|---|
| `max_usd` | The next batch (or the untuned arm, or the Scout phase) would pass `--max-usd`, or a night did | Decide whether the rest is worth it; `--resume` with a higher `--max-usd` continues |
| `plan_over_budget` | The re-planned remaining rounds would pass the cap | As above, or publish the partial run with `--allow-partial` |
| `server_cap` | The gateway refused the key with its own daily quota or budget (429 `daily_quota_exceeded` or `budget_exceeded`) | Raise the key's limit, or wait for the next UTC day, then `--resume` |
| `auth` | The gateway answered 401 for the key | Check `RATCHET_API_KEYS` and `RATCHET_LIVERUN_KEY` |
| `teacher_down` | Three 503 `teacher_circuit_open` answers in a row | See [Teacher API down](#teacher-api-down-token-factory-errors-or-timeouts), then `--resume` |
| `gateway_errors` | More than 10% of a batch failed (detail: the commonest statuses) | Read the gateway's log; `--resume` once fixed |
| `control_failed` | Every control call of a batch failed (detail: the error class) | Check Token Factory and `NEBIUS_API_KEY_LIVERUN` |
| `night_failed` | A night raised (detail: the class only, never the message) | Read `python -m ratchet.live night`'s own output by running it by hand; once fixed, `--resume` runs that night again before the next round. `--on-night-failure continue` goes on without it |
| `error`, `interrupted` | The harness itself failed (class only), or Ctrl-C | `--resume`. Requests sent but not recorded before a crash are counted from the ledger as `unrecorded`, and their spend is counted |

Rate limits (429 `rate_limit_exceeded`, 503 `overloaded`) wait `Retry-After` (at most 60 s) and retry once. An unsettled release at the end of a round (`settled: false`, `--max-extend` reached) is recorded, and the next night skips by itself (INV-5); the harness never forces it.

**Resume** rebuilds the stream and skips every index already handed out, so the next request gets the same text as an uninterrupted run with the same seed and no index is sent twice. Only `--max-usd` and `--base-url` may change on resume.

**The repeat run.** After the run, repeat the smoke run once against the final version (`smoke --repeat-of`): the same plan, workload and seed, a new run id. Its results compare every value with the first run's, and `report <first> --repeat <repeat> --publish` attaches the comparison and copies the repeat to `evidence/live/repeat/`. Two runs show how much a number moves between runs; they are not a confidence interval.

**The bill.** Read the Nebius console's total for the run's window, failed jobs included, and enter it with `report --bill-usd X --bill-note TEXT` (the note is redacted and at most 200 characters). LIVE_RESULTS.md shows it next to Ratchet's own total and the cost report's break-even line. Ratchet's numbers use list prices until the day-1 checks verify them, and say so.

**Publishing and the post-run checklist:**

1. `report <run dir> --publish` copies `results.json` and `requests.ndjson.gz` to `evidence/live/` and the Markdown to `docs/LIVE_RESULTS.md`. It refuses a smoke run, and a stopped run without `--allow-partial`.
2. `python -m ratchet.liverun verify evidence/live` exits 0 (it recomputes every client-side value from the records; ledger-side values are snapshots and it says which).
3. Fill each `[LIVE NUMBER]` in `docs/HACKATHON_SUBMISSION.md` and the demo script from `evidence/live/results.json`, using the placeholder map in `docs/LIVE_RESULTS.md`, with the `<!-- claim:live.* -->` anchor right after each number.
4. Move the PRD statuses that wait on live evidence (NFR-1, FR-8.1, FR-8.2, FR-8.4, SM-1 to SM-5) in that same commit, and only there.
5. Stop the endpoint (`bash deploy/student_endpoint.sh stop`, which records the uptime) and resume the cron nights.

**Tavily:** `--scout-runs K` runs Scout's example questions over `/v1` with live search: up to 3 credits a run from the same monthly allowance as the demo. Subtract them from `RATCHET_TAVILY_MONTHLY_CREDITS`'s pacing, since the gateway's credit meter does not see the harness's searches.

## Backups

The ledger is the source of truth for provenance, versions and training data. Back it up daily:

```bash
sqlite3 /var/lib/ratchet/ratchet.db ".backup '/var/backups/ratchet-$(date +%F).db'"
aws s3 cp /var/backups/ratchet-$(date +%F).db s3://<bucket>/backups/ --endpoint-url $RATCHET_S3_ENDPOINT
```

Keep at least 14 days. Adapters and `train.jsonl` are already in the bucket; keep every adapter referenced by a `live` version for rollback.

**Restore:** stop the gateway, copy the backup over `RATCHET_DB`, start the gateway, then reload the live adapter in vLLM. A backup taken before an erasure still holds the erased text: after a restore, run every erasure since the backup again (the `erasures` table of the newer file, or your record of them), and keep backups no longer than your retention window.

**Schema changes:** opening a ledger migrates it: `Ledger._migrate()` adds the columns in `MIGRATIONS`, `SCHEMA` creates new tables and triggers, and `INDEXES` the indexes, so an upgraded gateway or `python -m ratchet.live` brings an old file up to date by itself (back it up first). Never edit `version_leaves` or `receipts` by hand: they are append-only (INV-17) and the database refuses the change, because signed roots and receipt chains are built from those rows.
