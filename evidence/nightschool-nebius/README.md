# Night School ran end to end once on Nebius, by hand (28 Oct 2026)

Night School ran end to end **once**, by hand, on 28 Oct 2026 (UTC): the gateway on a Nebius CPU VM, the training job on a spot L40S, the adapter served from a Nebius Serverless Endpoint. The golden gate **rejected** the new student (81.8% precision against the 95% target), so it was **never made live**.

What this is not:

- **The public live site is not connected to any of this.** Its own gateway is still on Cloudflare, and no live request ever reached this endpoint or this adapter.
- **The loop has not run nightly on live traffic.** This was one run, started by a person, on rows that already existed.
- **No saving is claimed.** No teacher calls were made to build the data, and nothing here measures money saved.

Everything in this folder is sanitised: IPs, URLs, ids and keys are replaced by placeholders such as `<aijob-id>`, `<bucket>` and `<s3-endpoint>`. The only addresses left are the public licence links inside the adapter's bill of materials. The raw logs stay private.

## What ran (times are UTC, 28 Oct 2026)

| Time | What |
|---|---|
| 15:25 | A small CPU VM (2 vCPU, 8 GB) was created in Nebius `eu-west1` and ran the Ratchet gateway in live mode. (`eu-north1` had no CPU quota; `eu-west1` did.) A smoke check used `/readyz` only. No chat request was sent to the teacher. |
| about 15:55 | **Seed.** 4,322 text-to-SQL rows that already existed, labelled earlier by Nemotron 3 Ultra (licence OpenMDW-1.1), were imported with `python -m ratchet.importer`: 4,322 stored, 0 invalid, 0 refused, 0 duplicates. No new teacher calls, and no exam rows (the importer's own check found no overlap). See `logs/seed_import.txt`. |
| 16:08 to 16:15 | A student endpoint (vLLM, Nemotron 3 Nano 4B base, LoRA switched on, a spot L40S in `eu-north1`) was created. It reported RUNNING at 16:13:32 (283 s after the create command) and answered `/v1/models` at 16:15:22. |
| 16:15 to 17:08 | First night attempts: the training job was submitted, failed, and was resubmitted (see "Four jobs, not one" below). |
| 17:10 | The night was started again with the save fix. It split the rows into **3,864 training rows and 458 golden rows** and synced them to the bucket. |
| about 17:11 to 17:48 | **Training job 3 (spot L40S):** LoRA, 2 epochs, 966 steps, 1,478 s of training. Loss fell from about 0.15 to about 0.08 (average 0.116), mean token accuracy about 0.975. The adapter was saved at 17:47. |
| 17:46 to 17:53 | The endpoint had been stopped to save GPU money. It was started again, same address, and answered `/v1/models` at 17:52:52 (about 6.6 minutes). |
| 17:53 to 18:03 | The night hot-loaded the adapter into the endpoint (`/v1/load_lora_adapter`) and began the golden eval. At **18:03:53** it died: the student call timed out (the 15 s default) on the first request after the hot-load. |
| 18:05 to 18:14 | The eval and the gate were **run again on the saved adapter**, without training again, with a 120 s student timeout (see "The gate result came from a re-run" below). Finished 18:14:02. |
| 18:14:40, 18:17:42 | Endpoint deleted, then VM and disk deleted. The inventory of every regional project afterwards was empty except for the storage bucket (`data/final_inventory.txt`). |

## The numbers

| | |
|---|---|
| Rows imported | 4,322 (all existing Ultra-labelled text-to-SQL rows) |
| Training rows | 3,864 |
| Golden rows | 458 (held out; none were trained on) |
| Training | 2 epochs, 966 steps, 1,478 s, spot L40S |
| Loss | about 0.15 at the start, about 0.08 at the end (average 0.1164) |
| Mean token accuracy | about 0.975 (0.9752 at the end) |
| Adapter | `student-v1`, 40,490,208 bytes, sha256 `dc9ad4b9d2575e7a41120cb30bb023ee7aec35e997f4735ac819b545baddd00b` |

The adapter's hash and the start of its unsigned bill of materials (no signing key was set) are in `data/adapter_hash_bom.txt`. **That file is a cut-off excerpt**: the capture stopped inside the first `NOTICE.md` text (2,566 bytes) and the VM that held the full file was deleted, so the whole BOM was not kept and cannot be signed or verified afterwards. A signature over a rebuilt copy would not be the run's own record, so none was made. The bill lists the teacher (Nemotron 3 Ultra, OpenMDW-1.1, distillation allowed) and the base model's licence.

## The gate: rejected

The golden gate compared the student's SQL with the teacher's answer **as text**. Harsh accepted that comparison for this run; it is stricter than judging whether two queries return the same rows, so it undercounts answers that are right in a different spelling. The judge (a Token Factory model) was only asked about the answers the gate would serve, which were **11**.

| | |
|---|---|
| Golden agreement | 17.9% (458 rows) |
| Threshold the gate chose | 0.996 |
| Coverage at that threshold | 4.8% (the student would answer 11 of 229 test questions) |
| Precision at that threshold | **81.8%** (9 of 11 right) |
| Target | 95% |
| Verdict | **Rejected.** Version 2 state: `rejected`. Never admitted, never shadow, never canary, never live. |

The certificate's lower confidence bound on that precision is 53%, so even before the target the evidence is thin: 11 answers is a small sample. The full evaluation, the precision frontier by threshold and the events are in `data/ledger_dump.txt`.

## Caveats, all of them

### Four jobs, not one

The night's training step was submitted four times (`data/final_inventory.txt` lists them: one completed, two failed, one cancelled):

1. **An accidental on-demand job.** `deploy/train_job.sh` submitted the job on demand, not as a spot job. It was cancelled after about 2 minutes, about 16:16. No training happened.
2. **A missing `einops`.** The image's kernel check failed because the Mamba wheels are installed with `--no-deps` and `einops` was not in the image. No training happened.
3. **A save that failed.** Training finished (1,472 s, loss 0.1162) but saving onto the bucket mount failed with `EPERM` (`logs/training_job2_FAILED_SAVE.log`). The weights were recovered afterwards from the temporary file (sha256 `b1b0f3a1203f0dffc8bcd6d61f2af8612f1c53c2c25076ac320047188b24061e`). They were **not used** for the gate. The two runs differ because training is not bit-for-bit repeatable.
4. **The job that saved.** Job 3 above, with the save fix, produced `dc9ad4b9...`.

So two jobs trained on the GPU for about 25 minutes each. A separate script mistake (a line-ending slip in the VM's local copy) made two more night starts fail within seconds, before any job existed; they appear in `data/gpu_intervals_and_spend.txt` as 2-second "training failed" rows.

### The gate result came from a re-run

The night's own evaluation timed out at 15 s on the first call after the hot-load (`logs/night_run1.log`, which ends in the 504 traceback). The gate result in this folder was produced by **running the eval and the gate again on the saved adapter**, with a student timeout of 120 s, using a one-off driver, `scripts/reuse_night.py`. The driver replaces only the trainer (it reloads the saved adapter and trains nothing) and asks the night to run with `min_new_examples=0`; everything else is the stock `python -m ratchet.live night`. The re-run's log is `logs/night_eval_rerun.log`.

Because the failed first attempt had already created version 1 (a candidate, never evaluated), the finished version is **version 2**, which points at the same adapter folder. The 120 s timeout was set then with `RATCHET_STUDENT_TIMEOUT_S`; the pull request that adds this folder gives the night its own setting, `RATCHET_NIGHT_STUDENT_TIMEOUT_S` (default 120 s).

### Fixes made on the VM, now upstreamed

Five problems were fixed by hand on the VM during the run (only the first three are in `fixes/train_job_vm_local_fixes.diff`; the diff shows the VM's local copy of `deploy/train_job.sh`). The pull request that adds this folder puts all five into the repository, each with a test:

1. The job is submitted as spot (`--preemptible --follows-spot-price`) by default; on demand is an explicit opt-in (`RATCHET_TRAIN_ON_DEMAND=1`).
2. `einops` is pinned in `deploy/train-versions.txt` and installed by `deploy/Dockerfile.train` (the image has to be rebuilt by the build-train-image workflow; the run itself used `pip install einops` in the job's command).
3. The adapter is saved to the job's local disk and copied into the bucket mount; nothing is written in place on the mount.
4. `train_job.sh` waits for the adapter with `aws`, which was not on the night process's PATH, so it waited for an adapter that had already been saved (17:47 to 18:04). The night now stops with a message if `aws` is not on PATH.
5. The night's student timeout is its own setting (120 s by default) instead of the request path's 15 s. The student endpoint still has to be running before the hot-load: the night does not start or wake it.

### Other things worth knowing

- A student endpoint that has been stopped needs a restart before the adapter can be loaded; the restart took about 4.7 minutes to RUNNING and about 6.6 minutes to serve models. Pulling the training image took 7 to 9 minutes each time. These waits are part of a night's wall-clock time.
- The teacher watch was switched off for the run (`RATCHET_TEACHER_PROBES=0`, `RATCHET_ADJUDICATE_MAX=0`). The only Token Factory calls from the night were the judge's 11. The spend table is empty and the request count is 0 (`data/gpu_intervals_and_spend.txt`; its first query failed on a column name, left as it was).
- The ledger's compute intervals are the night script's own wall-clock records. Two have no end time. They are not Nebius billing.
- This is one run, one seed, one model, on one task (text-to-SQL), on rows that were already labelled. It says the pipeline runs end to end on Nebius. It says nothing about how a later night would score.

## Cost (estimates; the Nebius bill decides)

| Item | Estimate |
|---|---|
| Training GPU time, all four jobs | about 1.3 hours on one L40S (two training jobs of about 25 minutes each plus image pulls and the cancelled job) |
| Student endpoint (spot L40S) | about 1.4 hours (16:08 to 17:08, then 17:46 to 18:14, plus start-up) |
| CPU VM | about 2.9 hours of a 2 vCPU machine (15:25 to 18:17) |
| Judge calls | 11 (Token Factory) |

At the L40S list rate this repo uses for its own cost report ($1.55 an hour), about 2.7 GPU hours is at most **about $4**; spot pricing should make it lower, and the CPU VM is a few cents an hour. These are estimates from the times above, not a bill. **The Nebius bill decides.**

## Files

- `logs/night_run1.log`: the night's first attempt of the save-fixed run, ending in the timeout.
- `logs/night_eval_rerun.log`: the eval and gate re-run on the saved adapter, with the night's report.
- `logs/training_job2_FAILED_SAVE.log`, `logs/training_job3_SAVED.log`: the two jobs that trained (progress bars removed).
- `logs/seed_import.txt`: the importer's report for the 4,322 rows.
- `data/ledger_dump.txt`: versions, the full evaluation (with the precision frontier and the per-row outcome vector), events.
- `data/gpu_intervals_and_spend.txt`: the ledger's compute intervals and spend table.
- `data/adapter_hash_bom.txt`: the adapter's sha256 and bill of materials.
- `data/final_inventory.txt`: what was left in the cloud at the end.
- `fixes/train_job_vm_local_fixes.diff`: the change to `deploy/train_job.sh` made on the VM.
- `scripts/reuse_night.py`: the one-off driver for the re-run (it has no addresses or keys in it; only the import layout was reformatted by the repository's formatter, the code is as it ran).
- `MANIFEST.json`: the sha256 of every file here. `tests/test_nightschool_nebius_evidence.py` checks the hashes, the numbers in this page against the data files, and that no address, id or key is in the folder.

Not here: the adapter weights (not in git; the hash identifies them) and the raw, unsanitised logs.
