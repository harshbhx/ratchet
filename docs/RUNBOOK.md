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
| `student_error` | `Ratchet.handle` | A student call raised; the request fell back to the teacher. Logged on the 1st, 10th, 100th and 1000th failure | See [Student endpoint down](#student-endpoint-down-or-restarted) |
| `fault` | demo only | Fault injection via `/ratchet/demo/break-next` | Mock mode only |
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

**Symptoms:** `/v1/chat/completions` returns **502** ("teacher unavailable"). Every cold-start, escalated and audited request needs the teacher.

**Impact:** requests the student would serve with confidence still fail, if they happen to be picked for an audit.

**Steps:**

1. Check the Token Factory status page and `scripts/day1_check.py`.
2. Short outage: tell your app to retry. Long outage: set `RATCHET_AUDIT_RATE=0` and restart the gateway, so confident student answers stop calling the teacher. Escalations will still fail.
3. Don't run Night School during the outage.

**Planned fix (v0.3):** when the student has a confident answer and only the audit failed, serve the student answer and skip the audit instead of returning 502.

### Student endpoint down or restarted

**Symptoms:** `student_error` events in the release log (logged on the 1st, 10th, 100th and 1000th failure), escalation rate jumps to ~100%, and cost goes back to the all-teacher baseline. Requests still succeed: student failures fall back to the teacher automatically. After an endpoint restart vLLM returns 404 "model not found", because runtime-loaded adapters are gone.

**Steps:**

1. Restart the endpoint (`nebius ai endpoint` commands, or the console).
2. Reload the adapters of every version in `live`, `canary` and `shadow`:

   ```bash
   python -m ratchet.live reload
   ```

3. **Emergency fallback:** unset `RATCHET_STUDENT_BASE` and restart the gateway. Student calls then go to the hosted, untuned Nano 30B on Token Factory. Precision may drop, and audits will show it.

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

### Cost spike

**Checks:**

1. Is `escalation_rate` up? That means drift, or a broken student.
2. Is shadow running? Shadow adds student sampling and arbitration calls to every request.
3. Is `RATCHET_AUDIT_RATE` too high?
4. Is the vLLM endpoint left running (GPU-hours)?
5. Are teacher outputs long because of reasoning? Look at token usage on Token Factory.

**Mitigations:** lower the audit rate (but not to 0 while a canary is in flight, since audits drive promotion and rollback), stop the endpoint when idle, cap `max_tokens` in prompts.

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
