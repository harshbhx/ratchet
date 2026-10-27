"""Total GPU machine time, cost and training time of the whole Nebius effort, computed from logs/attempts.json and the logs.

Run from the repository root:  python evidence/text2sql-round2-nebius/tally.py          (prints the numbers)
                               python evidence/text2sql-round2-nebius/tally.py --write  (writes results/run_facts.json)
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent


def ts(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def step_seconds(path):
    return step_seconds_text(path.read_text(encoding="utf-8", errors="replace"))


def step_seconds_text(text):
    """Last step shown and the seconds of training steps in one train log: the elapsed time on the last progress bar line."""
    text = text.replace(chr(13), chr(10))
    last = re.findall(r"(\d+)/1082 \[(?:(\d+):)?(\d+):(\d+)<", text)
    if not last:
        return 0, 0
    step, h, m, s = last[-1]
    return int(step), int(h or 0) * 3600 + int(m) * 60 + int(s)


def build():
    att = json.loads((HERE / "logs" / "attempts.json").read_text(encoding="utf-8"))["attempts"]
    rows, total_s, total_usd, by_rate = [], 0.0, 0.0, {}
    for a in att:
        if not a.get("machine_start"):
            rows.append({"job": a["job"], "machine_seconds": 0, "usd": 0.0})
            continue
        sec = (ts(a["machine_end"]) - ts(a["machine_start"])).total_seconds()
        usd = sec / 3600 * a["rate_usd_per_h"]
        total_s += sec
        total_usd += usd
        by_rate[a["platform"]] = round(by_rate.get(a["platform"], 0) + sec, 0)
        rows.append({"job": a["job"], "machine_seconds": sec, "usd": round(usd, 4)})
    steps, prev = [], ""
    for p in sorted((HERE / "logs").glob("train-*.log")):
        text = p.read_text(encoding="utf-8", errors="replace")
        last_step, secs = step_seconds(p)
        # A restarted container appends to the log it left: do not count the earlier run's steps twice.
        if prev and text.startswith(prev):
            _, before = step_seconds_text(prev)
            secs = max(0, secs - before) if last_step == step_seconds_text(prev)[0] else secs
        steps.append({"log": p.name, "last_step_seen": last_step, "step_seconds": secs})
        prev = text
    train_s = sum(s["step_seconds"] for s in steps)
    # First training step to the adapter being saved: the run logs' names carry the epoch second each run started.
    first, saved = None, None
    for p in sorted((HERE / "logs").glob("train-*.log")):
        t0 = int(p.stem.split("-")[1])
        text = p.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"\[stage\s+(\d+)s\] training", text)
        run = (HERE / "logs" / f"run-{t0}.log").read_text(encoding="utf-8", errors="replace")
        r = re.search(r"\[run (\d+)s\] train start", run)
        if m and r and re.search(r"\d+/1082 \[", text) and first is None:
            first = t0 + int(r.group(1)) + int(m.group(1))
        a = re.search(r"\[run (\d+)s\] adapter copied", run)
        if a:
            saved = t0 + int(a.group(1))
    return {
        "machine_seconds_total": round(total_s),
        "machine_hours_total": round(total_s / 3600, 2),
        "cost_usd_total_estimate": round(total_usd, 2),
        "per_job": rows,
        "training_step_seconds_by_attempt": steps,
        "training_step_seconds_total": train_s,
        "first_training_step_utc": datetime.fromtimestamp(first, timezone.utc).strftime("%H:%M:%S") if first else None,
        "adapter_saved_utc_approx": datetime.fromtimestamp(saved, timezone.utc).strftime("%H:%M:%S") if saved else None,
        "wall_clock_first_step_to_adapter_saved_seconds": (saved - first) if first and saved else None,
        "note": "cost is machine seconds at the list price (per-second billing); disks and anything else the Nebius bill adds are not included",  # noqa: E501
    }


if __name__ == "__main__":
    res = build()
    print(json.dumps(res, indent=1))
    if "--write" in sys.argv:
        (HERE / "results" / "run_facts.json").write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8", newline="\n")
