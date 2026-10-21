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

## Reading the dashboard

| Panel | Source | What to look for |
|---|---|---|
| Saved vs all-teacher | `totals.saved_usd` | Cumulative; it should only go up |
| Cost reduction (latest day) | `days[-1].savings_pct` | Below 50% after the first week means the student is not taking traffic |
| Answered by your model | `days[-1].student_share` | Escalations show as the orange part of each bar |
| Model versions | `versions` | Exactly one `live` once past cold start; at most one `shadow` or `canary` |
| Release log | last 40 `events` | See below |
| Provenance | `bill_of_materials` | Only distillable teachers and licenses; fingerprint changes as data grows |
| (not on the dashboard yet) | `cost` | The GPU-hour view and the recommendation; `GET /ratchet/cost` has the full report. The panels above price the student per token (`totals.cost_model`) |

## Event kinds

| Kind | Written by | Meaning | Action |
|---|---|---|---|
| `trained` | `run_night` | A new version was trained and scored on the golden set | None |
| `skip` | `run_night` | No training tonight: storage disabled (`RATCHET_STORE_EXAMPLES=0`; a task whose spec changed is still re-scored), release in flight, fewer than 50 new examples, or no golden set yet | None, unless it repeats for days with no release in flight and storage on |
| `rejected` | `admit` / `tick` | Candidate failed the golden gate or shadow (reason in detail) | Repeated rejections: see [Persistent rejections](#persistent-rejections) |
| `shadow` | `admit` | Candidate passed the golden gate and is running silently | None |
| `canary` | `admit` / `tick` | Candidate serves 10% or 50% of traffic | Watch for `rollback` |
| `live` | `tick` | Candidate promoted to 100%; previous live retired | None |
| `retired` | `admit` | An in-flight shadow or canary was replaced by a newer candidate | None |
| `rollback` | `tick` | Canary audited precision has a Wilson upper bound below 0.92; traffic is back on the previous live version (or the teacher) | Investigate: see [Bad deploy](#bad-deploy--rollback) |
| `alert` | `tick` | **Live** version's audited precision is clearly below 0.92. It is still serving, because there is no automatic action on live | Act now: see [Precision alert](#precision-alert-on-live) |
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
| `no_train` | `POST` / `DELETE /v1/ratchet/no-train` | End users were registered as no-train, or registrations removed: a count and who (`2 end user registration(s) by admin ops`, `1 ... removed by acme`), never the end user or its hash | None. See [Opt-out requests](#opt-out-requests-an-end-user-or-a-client) |
| `no_train_unmatched` | gateway startup (`privacy.check_no_train_scopes`) | No-train registrations match no request under the current API keys: their scope is a key name no longer in `RATCHET_API_KEYS` (renamed or removed), or keyless while keys are on. The detail gives the count and the scopes (names only); written once per change, and `/ratchet/stats` → `privacy.no_train_unmatched` keeps the count | If a key was renamed by mistake, restore its name and restart. Otherwise have those end users registered again under the new name, or by an admin for every client |
| `end_user_key` | gateway startup (`privacy.end_users`) | The end-user HMAC key differs from the one the ledger last saw (`RATCHET_END_USER_KEY` set, changed or removed): every earlier end-user hash, and the no-train registrations the detail counts, no longer match, so those end users' requests are stored again | If unintended, restore the previous key and restart. If intended, have every no-train end user registered again |
| `erase` | `privacy.erase` (`POST /ratchet/admin/privacy/erase`, `DELETE /v1/ratchet/examples`, `python -m ratchet.admin erase`) | Stored examples were erased for good: the erasure's id, what selected them (`client`, `end user`, `hashes`, `content`), who asked, how many were purged, shared ones kept, request rows unlinked, and the versions that may have been trained on them (`may be held by v4, v5 (reported, not changed)`). Never a hash, an end user or the text | If versions are named, decide whether to retire them or wait for the next night's version, which is trained without the erased rows. See [PII leak](#pii-leak) |
| `purge` | retention (`privacy.apply_retention`: the live gateway hourly, a live night, `python -m ratchet.live purge`) | Examples not seen within `RATCHET_EXAMPLE_RETENTION_DAYS` lost their text, and request rows older than `RATCHET_REQUEST_RETENTION_DAYS` were folded into daily sums; counts, and the client for a `--client` run | None |
| `purge_failed` | the live gateway's hourly retention | A scheduled purge raised (detail: the exception's class). Nothing was half-done: each purge and each fold chunk is one transaction. The next hour tries again | Run `python -m ratchet.live purge --dry-run`, then without `--dry-run`, and read the error |
| `sync_failed` | `python -m ratchet.live night` | After a night that trained, the adapter's `ratchet_bom.json` and `NOTICE.md` were written to `RATCHET_DATA_DIR/adapters/<name>/`, but the second `RATCHET_SYNC_CMD` failed (detail: the version and the exception's class), so the bucket's adapter folder has the weights without their licenses. The night itself stands: the version exists and is in the release gate. The command exits non-zero | Run `RATCHET_SYNC_CMD` by hand (it only uploads), then check that the bucket's `adapters/<name>/` holds `NOTICE.md` and `ratchet_bom.json`. `python -m ratchet.live export --version N --out DIR` bundles the adapter with its licenses at any time |
| `fault` | demo only | Fault injection via `/ratchet/demo/break-next` or `/ratchet/demo/student-outage` | Mock mode only |
| `warmup` | mock startup | `RATCHET_MOCK_WARMUP_DAYS` simulated days ran before the demo opened | Mock mode only |

The `alert` event is written once per live version per process (a restart can write it again), not on every tick.

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

1. The breaker stays `open` (probes keep failing): check the endpoint (`nebius ai endpoint` commands, or the console) and restart it if it is down. The next successful probe closes the breaker; nothing else needs restarting.
2. `adapter_reload_failed` keeps repeating: the detail names the error. A 404 or 400 from `/v1/load_lora_adapter` usually means the adapter path is wrong (`RATCHET_ADAPTER_ROOT`, default `/data/adapters`, must be where the endpoint sees the bucket) or the endpoint was started without `--enable-lora` and `VLLM_ALLOW_RUNTIME_LORA_UPDATING=True`. A 401 means `RATCHET_STUDENT_KEY` is wrong.
3. **Manual fallback:** reload the adapters of every version in `live`, `canary` and `shadow` by hand:

   ```bash
   python -m ratchet.live reload
   ```

4. **Emergency fallback:** unset `RATCHET_STUDENT_BASE` and restart the gateway. Student calls then go to the hosted, untuned Nano 30B on Token Factory. Precision may drop, and audits will show it.
5. A breaker that opens and closes many times a day means the endpoint is flaky (scale-to-zero cold starts, or too little GPU for the load). Raise `RATCHET_STUDENT_TIMEOUT_S` for slow cold starts (a probe is then given as long before another request tries the endpoint), or keep a replica warm. While a shadow's breaker (`shadow:`) is open the shadow collects no evidence, so its release waits.

### Precision alert on live

**Symptoms:** `alert` events saying "live vN audited precision X% (n=…) below floor".

**Steps:**

1. Check `GET /ratchet/stats` → `days[-1].audit_agreement` and whether escalations jumped (drift).
2. If drift is the cause (a new product or new wording), the student's confidence usually drops and it escalates. The `alert` means it is confidently wrong on some slice, which is serious.
3. Immediately take the live version out (manual intervention above) or restore the previous good version. All traffic then goes to the teacher, which costs more but is correct.
4. Inspect recent audited disagreements:

   ```sql
   SELECT id, task, confidence FROM requests WHERE audited=1 AND audit_agree=0 ORDER BY id DESC LIMIT 50;
   ```

5. Let Night School retrain. The disagreements are already training data.

### Bad deploy / rollback

**Symptoms:** a `rollback` event. The system has already moved traffic back.

**Steps:**

1. Confirm the right adapter was loaded: the adapter folder's `ratchet_manifest.json` should show the expected example count.
2. Compare the version's golden `precision` with its audited precision. A big gap means a training or serving mismatch, such as the wrong checkpoint, a tokenizer or chat-template change, or a vLLM version change.
3. Don't delete the rolled-back adapter until you understand why it failed.

### Persistent rejections

**Symptoms:** a `rejected` event every night.

- "golden precision < 95%": the base model may be too small for the task, the task too subjective, or the task's fields too strict (an `exact` field where a `number` with a tolerance, a `set` or a `label` with aliases fits; [CONFIGURATION.md](CONFIGURATION.md#tasks)). Check the task's `fields`, and `golden_invalid_refs` in the version's eval: references the spec refuses are not graded.
- "golden coverage < 25%": too few examples per pattern. Wait for more data, or lower `min_coverage` in code.
- "regresses vs live": live is already good. This is expected and harmless.
- "shadow precision/coverage": real traffic differs from the golden set. Check for drift.
- "shadow precision ... (N unarbitrated counted as disagreements)": the teacher failed some arbitrations (`shadow_error` events). If N explains the shortfall, the candidate may be fine; the next night trains a new one.

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

**Keep the uptime records complete.** The report knows only the endpoint hours that were recorded: run `bash deploy/student_endpoint.sh` / `stop` next to the ledger, or record `python -m ratchet.cost endpoint up` / `down` yourself (see [Manual interventions](#manual-interventions)). A warning counts student answers on days with no recorded uptime, which are priced per token. Compare `endpoint_hours` with the Nebius bill weekly.

**Reading the recommendation:**

| `recommendation` | Means | Do |
|---|---|---|
| `self_host` | The endpoint served at least the break-even volume of student answers per hour it ran, and more than the volume at which the hosted fallback would be cheaper | Keep it. If the explanation says it was idle much of the time, scale it to zero between bursts |
| `hosted_fallback` | Below the hosted fallback's crossover (and usually far below break-even): the endpoint costs more than it saves | Leave `RATCHET_STUDENT_BASE` empty so the untuned hosted Nano on Token Factory serves the student role per token, or run the endpoint only while traffic arrives. Night School still needs the endpoint while it loads and evaluates a new adapter: start it for the night run (with `RATCHET_STUDENT_BASE` set in the night job's environment) and stop it after; each start and stop is recorded |
| `scale_to_zero` | More than half of the endpoint's hours fell on days when no request reached the student, or neither the endpoint nor the hosted fallback pays at this volume | Stop the endpoint when idle (`bash deploy/student_endpoint.sh stop`) and start it for busy periods |
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

### Startup refused: a reserved setting

The gateway, the simulator or `python -m ratchet.live` stops with `ValueError: RATCHET_<NAME>=... has no effect yet: it is reserved for <feature>, not built yet`. The variable belongs to an evidence feature that is declared but not built in this version ([CONFIGURATION.md](CONFIGURATION.md#reserved-settings), [ADR 0024](adr/0024-evidence-foundations.md)). Ratchet refuses it rather than run without the protection you asked for. Unset it, or set it to the default the message names. A `ValueError` that names `ReleasePolicy.<field>` is a value out of range (for example `RATCHET_LIVE_FLOOR=1`).

## Backups

The ledger is the source of truth for provenance, versions and training data. Back it up daily:

```bash
sqlite3 /var/lib/ratchet/ratchet.db ".backup '/var/backups/ratchet-$(date +%F).db'"
aws s3 cp /var/backups/ratchet-$(date +%F).db s3://<bucket>/backups/ --endpoint-url $RATCHET_S3_ENDPOINT
```

Keep at least 14 days. Adapters and `train.jsonl` are already in the bucket; keep every adapter referenced by a `live` version for rollback.

**Restore:** stop the gateway, copy the backup over `RATCHET_DB`, start the gateway, then reload the live adapter in vLLM. A backup taken before an erasure still holds the erased text: after a restore, run every erasure since the backup again (the `erasures` table of the newer file, or your record of them), and keep backups no longer than your retention window.

**Schema changes:** opening a ledger migrates it: `Ledger._migrate()` adds the columns in `MIGRATIONS`, `SCHEMA` creates new tables and triggers, and `INDEXES` the indexes, so an upgraded gateway or `python -m ratchet.live` brings an old file up to date by itself (back it up first). Never edit `version_leaves` or `receipts` by hand: they are append-only (INV-17) and the database refuses the change, because signed roots and receipt chains are built from those rows.
