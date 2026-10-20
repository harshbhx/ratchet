# Runbook

How to operate Ratchet in live mode: what to check every day, what the events mean, how to intervene by hand, and what to do when something breaks. The mechanics are explained in [ARCHITECTURE.md](ARCHITECTURE.md).

## Daily checks (5 minutes)

1. **Dashboard** (`/dashboard`) or `GET /ratchet/stats`:
   - **Answered by your model** should be stable or rising. A sudden drop means drift (new kinds of requests) or a student problem.
   - **Cost reduction** should track student share. If share is high but savings are low, audits or shadow are costing more than expected.
   - **Release log**: read every event since yesterday (meanings below).
2. **Night School log** (`/var/log/ratchet-night.log`): exactly one of `trained` or `skip` per night.
3. **Bills:** Token Factory usage page and the Nebius billing page. Check that the endpoint is stopped if you are not using it.
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

## Event kinds

| Kind | Written by | Meaning | Action |
|---|---|---|---|
| `trained` | `run_night` | A new version was trained and scored on the golden set | None |
| `skip` | `run_night` | No training tonight: release in flight, fewer than 50 new examples, or no golden set yet | None, unless it repeats for days with no release in flight |
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
| `skip_example` | `Ratchet._store_example` | A teacher answer was served but not stored as a training example (INV-16). Detail is the task and the reason: `finish_reason length` (cut off by the token limit), another `finish_reason` (`content_filter`, `other`, ...), `empty answer` (nothing left once reasoning is stripped), `invalid answer` (a structured task's answer is not valid JSON), `invalid after redaction`, or `client logit_bias`, `client penalty`, `client temperature above 1`, `client vendor field` (written under a client's sampling that can steer the label, so it is never a training target). Logged on the 1st, 10th, 100th and 1000th skip of each reason; never the answer. The request row has `stored = 0`, and an audit with such an answer is not counted | A few are normal. Many: see [Teacher answers not stored](#teacher-answers-not-stored) |
| `gateway_error` | the gateway's error handler | An unhandled exception in a request: a bug, not a client mistake. The client got a 500 (`api_error` on `/v1/*`). Detail is the exception's class and how many there have been, never its message. Logged on the 1st, 10th, 100th, ... occurrence since the last restart | Read the traceback in the gateway's log (`docker compose logs ratchet` on the demo VM) and fix the bug |
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
| Add or rotate a client's API key | Edit `RATCHET_API_KEYS` (`name:key,...`) and restart the gateway. Keep the same name so per-client stats continue; the old key stops working at once |
| Rotate the BOM signing key | `python -m ratchet.bom keygen`, set the new `RATCHET_BOM_SIGNING_KEY`, restart, publish the new public key. Old BOMs keep their old key id and still verify against the old public key, so keep it listed |

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

- "golden precision < 95%": the base model may be too small for the task, the task too subjective, or the comparison keys too strict. Check `compare_keys`.
- "golden coverage < 25%": too few examples per pattern. Wait for more data, or lower `min_coverage` in code.
- "regresses vs live": live is already good. This is expected and harmless.
- "shadow precision/coverage": real traffic differs from the golden set. Check for drift.
- "shadow precision ... (N unarbitrated counted as disagreements)": the teacher failed some arbitrations (`shadow_error` events). If N explains the shortfall, the candidate may be fine; the next night trains a new one.

### Teacher answers not stored

**Symptoms:** `skip_example` events reaching the 100th or 1000th of a reason; Night School says `skip` (fewer than 50 new examples) although `escalation_rate` is high; `SELECT task, COUNT(*), SUM(stored) FROM requests WHERE served_by='teacher' GROUP BY task` shows far fewer stored than escalated.

- `finish_reason length`: the teacher runs out of tokens, usually while reasoning: at the client's `max_tokens`, or at `RATCHET_TEACHER_MAX_TOKENS` (8,192) when the client sent none. Check the answer length on Token Factory; shorten the system prompt's demands or ask for a shorter answer. If clients send tight limits to a reasoning teacher, set `RATCHET_TEACHER_REASONING_HEADROOM` (about `RATCHET_TEACHER_REASONING_TOKENS`); with it, an answer over the client's own limit is also `length` ([CONFIGURATION.md](CONFIGURATION.md#generation-parameters-and-token-caps)). The client still got the answer, with `finish_reason: "length"`.
- `empty answer`: the teacher never left its reasoning (`<think>` without `</think>`), or the endpoint returned the answer in a reasoning field only. Check the model ID and whether a reasoning parser is on.
- `invalid answer`: the teacher does not answer a structured task with JSON. Check the task's system prompt (it must ask for JSON only), and have the client send `response_format: {"type": "json_object"}`, which reaches the teacher (and is stored with the example).
- `invalid after redaction`: a redaction pattern matched a number outside quotes in the JSON (a card-like order id). Make the prompt ask for ids as strings.
- `client logit_bias`, `client penalty`, `client temperature above 1`, `client vendor field`: clients send sampling fields that can steer what the teacher writes, so their escalations are served but not stored (threat model T2). Expected for clients that need them. If one client sends them by habit (a framework default such as `temperature: 1.2`), ask it to drop them, or take the field out of `RATCHET_TEACHER_PARAMS`: a field the teacher never gets does not stop storage.
- `finish_reason other` or `unknown`: the teacher endpoint reports a stop reason other than `stop`, `tool_calls` or the usual ones (a missing, null or empty one is read as `stop`). The student's samples with such a value get no vote either, so the student escalates everything. `scripts/day1_check.py` prints each model's `finish_reason`; look at a raw response before changing anything, because storing only complete answers is invariant 16.

### Cost spike

**Checks:**

1. Is `escalation_rate` up? That means drift, or a broken student.
2. Is shadow running? Shadow adds student sampling and arbitration calls to every request.
3. Is `RATCHET_AUDIT_RATE` too high?
4. Is the vLLM endpoint left running (GPU-hours)?
5. Are teacher outputs long because of reasoning? Look at token usage on Token Factory.

**Mitigations:** lower the audit rate (but not to 0 while a canary is in flight, since audits drive promotion and rollback), stop the endpoint when idle, lower `RATCHET_TEACHER_MAX_TOKENS` (the most output tokens any teacher call may use, 8,192 by default) or ask clients to send a smaller `max_tokens`.

### PII leak

All request messages escalated or audited to the teacher are stored in `examples.messages`, and copies go to `night/train.jsonl` in the bucket. With `RATCHET_REDACT_PII=1` (the default) emails, phones, cards, IBANs, national IDs, IPs and API secrets are stored as placeholders, so a leak there usually means a name, an address, or an identifier format the redactor does not know. Paste a sample into the dashboard's **Try the redactor** box (or `POST /ratchet/redact`) to see what it catches.

**Steps:**

1. Stop the gateway and Night School.
2. Find the rows: `SELECT id, hash FROM examples WHERE messages LIKE '%<identifier>%';`
3. Delete them from `examples`, then rebuild `night/train.jsonl` and re-sync it. Retrain if the data reached an adapter: adapters can memorise training text, so treat any adapter trained on the data as affected.
4. Delete affected files from the bucket, including old adapters and datasets.
5. Follow your legal and notification duties.

**Prevention:** keep `RATCHET_REDACT_PII=1`; add a pattern to `ratchet/redact.py` (with a test) for any identifier format you find leaking. Planned: NER-based detection for names and addresses, and Token Factory zero-data-retention settings. See [THREAT_MODEL.md](THREAT_MODEL.md) T4.

## Backups

The ledger is the source of truth for provenance, versions and training data. Back it up daily:

```bash
sqlite3 /var/lib/ratchet/ratchet.db ".backup '/var/backups/ratchet-$(date +%F).db'"
aws s3 cp /var/backups/ratchet-$(date +%F).db s3://<bucket>/backups/ --endpoint-url $RATCHET_S3_ENDPOINT
```

Keep at least 14 days. Adapters and `train.jsonl` are already in the bucket; keep every adapter referenced by a `live` version for rollback.

**Restore:** stop the gateway, copy the backup over `RATCHET_DB`, start the gateway, then reload the live adapter in vLLM.

**Schema changes:** `CREATE TABLE IF NOT EXISTS` does not add new columns to an existing file. After upgrading Ratchet, compare `PRAGMA table_info(versions)` with `SCHEMA` in `ledger.py`, and `ALTER TABLE ... ADD COLUMN` anything missing.
