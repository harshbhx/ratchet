"""Every measured number the judges page and the dashboard show, read from evidence/ at build time.

    python scripts/measured_numbers.py            # write web/src/data/measured.json
    python scripts/measured_numbers.py --check    # exit 1 if the file differs from what the evidence gives

Nothing here is typed from memory: each value is read from the saved files named in its `source`, and the section's `date` is the
day the evidence says it was measured (a recording's timestamp, a manifest's `made`, a run's start). tests/test_measured_numbers.py
runs --check and also recomputes a few values by hand. The exam and tool-calling scores come from web/src/data/exams.json and
toolcall.json, which tests/test_exam_numbers.py and tests/test_toolcall_results.py hold to the saved answers.

The Night School run on Nebius (28 Oct) is evidence another change adds (evidence/nightschool-nebius). Until that folder exists the
section says `present: false` and the page hides its tile; when it exists the section carries its manifest's own words and file
list, never a number typed here.

Standard library only; no network.
"""

# ruff: noqa: E501  (the docstring and the evidence descriptions are page text, kept as written)

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "web" / "src" / "data" / "measured.json"


def read(rel: str):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def utc_day(epoch: float) -> str:
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).strftime("%Y-%m-%d")


def exams() -> dict:
    e = read("web/src/data/exams.json")
    models = {m["id"]: m["short"] for m in e["models"]}
    out = []
    for x in e["exams"]:
        rows = e["scores"][x["id"]]
        out.append(
            {
                "id": x["id"],
                "name": x["name"],
                "rows": x["rows"],
                "built": x["built"],
                "evidence": x["evidence"],
                "scores": [
                    {"model": m, "label": models[m], "agree": s["agree"], "n": s["n"], "rate": s["rate"]} for m, s in rows.items()
                ],
            }
        )
    return {
        "date": "2026-10-24",
        "source": "evidence/text2sql-round2",
        "what": "Text-to-SQL, strict scoring: a query counts only if it returns exactly the reference rows",
        "exams": out,
    }


def toolcall() -> dict:
    t = read("web/src/data/toolcall.json")
    out = []
    for key, x in t["exams"].items():
        out.append(
            {
                "id": key,
                "label": x["label"],
                "rows": x["rows"],
                "scores": [{"model": m, "agree": s["agree"], "n": s["n"], "rate": s["rate"]} for m, s in x["scores"].items()],
            }
        )
    return {
        "date": t["date"],
        "source": "evidence/toolcall",
        "what": "Tool calling: the model's call agrees with the reference call, on two exams",
        "exams": out,
    }


def retrain() -> dict:
    r = read("evidence/text2sql-round2-nebius/results/results_nebius.json")
    f = read("evidence/text2sql-round2-nebius/results/run_facts.json")
    m = read("evidence/text2sql-round2-nebius/MANIFEST.json")
    ex = []
    for key in ("exam1", "exam2"):
        e = r["exams"][key]
        ex.append(
            {
                "id": key,
                "nebius": {
                    "agree": e["nebius_retrain"]["agree"],
                    "n": e["nebius_retrain"]["n"],
                    "rate": e["nebius_retrain"]["rate"],
                },
                "original": {
                    "agree": e["original_round2_borrowed_gpu"]["agree"],
                    "n": e["original_round2_borrowed_gpu"]["n"],
                    "rate": e["original_round2_borrowed_gpu"]["rate"],
                },
            }
        )
    return {
        "date": m["made"],
        "source": "evidence/text2sql-round2-nebius",
        "what": m["what"],
        "exams": ex,
        "machine_hours": f["machine_hours_total"],
        "cost_usd": f["cost_usd_total_estimate"],
        "jobs": len(f["per_job"]),
        "adapter_sha256": m["adapter_model.safetensors_sha256"],
    }


def endpoint() -> dict:
    base = "evidence/student-endpoint-nebius/results/"
    cold = read(base + "session2_cold_start.json")
    restart = read(base + "session2_restart.json")
    bench = read(base + "bench_adapters.json")["cold"]
    rows = [
        {
            "adapter": r["adapter"],
            "concurrency": r["concurrency"],
            "requests": r["requests"],
            "ok": r["ok"],
            "errors": r["errors"],
            "lat_p50_s": r["lat_p50_s"],
            "lat_p95_s": r["lat_p95_s"],
            "tokens_per_s_total": r["tokens_per_s_total"],
            "requests_per_s": r["requests_per_s"],
            "usd_per_1000": r["cost_usd_per_1000_requests"],
        }
        for r in bench["results"]
    ]
    return {
        "date": "2026-10-27",
        "date_end": "2026-10-28",
        "source": "evidence/student-endpoint-nebius",
        "what": "The student served by hand on a Nebius Serverless Endpoint for speed tests; no accuracy was scored and it serves no live traffic",
        "gpu_usd_per_hour": bench["gpu_usd_per_hour"],
        "max_tokens": bench["max_tokens"],
        "cold_start_s": cold["seconds"]["total_until_ready"],
        "cold_running_s": cold["seconds"]["state_running"],
        "restart_s": restart["seconds"]["total_until_ready"],
        "restart_running_s": restart["seconds"]["state_running"],
        "adapter_load_s": max(v["seconds"] for v in cold["adapter_load"].values()),
        "bench": rows,
    }


def scout() -> dict:
    runs = []
    for p in sorted((ROOT / "evidence" / "scout").glob("*.json")):
        r = json.loads(p.read_text(encoding="utf-8"))
        done = [e for e in r["events"] if e["type"] == "done"][-1]
        t = done["totals"]
        runs.append(
            {
                "file": f"evidence/scout/{p.name}",
                "recorded_at": r["recorded_at"],
                "question": r["question"],
                "duration_s": round(r["duration_ms"] / 1000, 1),
                "calls": t["calls"],
                "llm_calls": t["llm_calls"],
                "teacher_calls": t["teacher_calls"],
                "student_calls": t["student_calls"],
                "cost_usd": t["cost_usd"],
                "citations": len(done["citations"]),
            }
        )
    return {
        "date": min(r["recorded_at"][:10] for r in runs),
        "date_end": max(r["recorded_at"][:10] for r in runs),
        "source": "evidence/scout",
        "what": "Three recorded Scout runs on Token Factory with live search; the teacher answered every model call",
        "runs": runs,
    }


def smoke() -> dict:
    r = read("evidence/live-smoke-2026-10-23/results.json")
    v = r["values"]
    return {
        "date": utc_day(r["started"]),
        "source": "evidence/live-smoke-2026-10-23",
        "what": "One live smoke run on Token Factory, no student endpoint: the teacher answered every request",
        "run_id": r["run_id"],
        "requests": int(v["live.run.requests"]),
        "total_usd": v["live.run.total_usd"],
        "teacher_p50_wall_ms": v["live.final.teacher_p50_wall_ms"],
        "ultra_p50_client_ms": v["live.final.ultra_p50_client_ms"],
        "wall_s": round(r["finished"] - r["started"], 1),
        "student_share_pct": v["live.final.student_share_pct"],
    }


def labelling() -> dict:
    r1 = read("evidence/text2sql-round1/data/teacher_manifest.json")
    r2 = read("evidence/text2sql-round2/data/teacher_manifest_round2.json")
    return {
        "date": "2026-10-23",
        "source": "evidence/text2sql-round2/data/teacher_manifest_round2.json",
        "what": "What the teacher's text-to-SQL labels cost on Token Factory, at list prices",
        "rounds": [
            {
                "id": "round1",
                "date": "2026-10-22",
                "usd": r1["spent_usd_estimate"],
                "calls": r1["teacher_calls"],
                "rows": r1["rows"]["train"] + r1["rows"]["golden"],
                "source": "evidence/text2sql-round1/data/teacher_manifest.json",
            },
            {
                "id": "round2",
                "date": "2026-10-23",
                "usd": r2["spent_usd_estimate"],
                "calls": r2["teacher_calls"],
                "rows": r2["rows"]["train"],
                "source": "evidence/text2sql-round2/data/teacher_manifest_round2.json",
            },
        ],
    }


def hosted_nano() -> dict:
    c = read("evidence/hosted-nano/collect.json")
    return {
        "date": c["collected"][:10],
        "source": "evidence/hosted-nano",
        "what": "The untuned hosted Nano scored as a stand-in student on Scout's two distillable calls, against the teacher's labels",
        "rows": c["rows"],
        "spend_usd": round(sum(c["spend_usd"].values()), 4),
        "student_model": c["student_model"],
        "teacher_model": c["teacher_model"],
    }


def readme_numbers(text: str) -> dict:
    """The gate's numbers as the run's own README prints them in its tables; a number it does not print is left out."""
    wanted = {
        "golden_agreement": r"\|\s*Golden agreement\s*\|\s*([\d.]+%)",
        "coverage": r"\|\s*Coverage at that threshold\s*\|\s*([\d.]+%)",
        "precision": r"\|\s*Precision at that threshold\s*\|\s*\**([\d.]+%)",
        "target": r"\|\s*Target\s*\|\s*([\d.]+%)",
        "training_rows": r"\|\s*Training rows\s*\|\s*([\d,]+)",
        "golden_rows": r"\|\s*Golden rows\s*\|\s*([\d,]+)",
        "rows_imported": r"\|\s*Rows imported\s*\|\s*([\d,]+)",
        "steps": r"\|\s*Training\s*\|[^|\n]*?([\d,]+) steps",
        "loss_start": r"\|\s*Loss\s*\|\s*about ([\d.]+) at the start",
        "loss_end": r"\|\s*Loss\s*\|[^|\n]*?about ([\d.]+) at the end",
        "gate_right": r"\(([\d,]+) of [\d,]+ right\)",
        "gate_answered": r"would answer ([\d,]+) of [\d,]+ test questions",
        "gate_questions": r"would answer [\d,]+ of ([\d,]+) test questions",
    }
    out = {}
    for key, pat in wanted.items():
        found = re.search(pat, text)
        if found:
            out[key] = found.group(1)
    return out


def nightschool() -> dict:
    rel = "evidence/nightschool-nebius"
    folder = ROOT / rel
    if not folder.is_dir():
        return {"present": False, "source": rel}
    out: dict = {"present": True, "source": rel, "date": None, "what": None}
    manifest = folder / "MANIFEST.json"
    if manifest.is_file():
        m = json.loads(manifest.read_text(encoding="utf-8"))
        out["date"] = m.get("made")
        out["what"] = m.get("what")
    if manifest.is_file():
        out["verdict"] = m.get("verdict")
        out["adapter_name"] = (m.get("adapter") or {}).get("name")
    readme = folder / "README.md"
    out["has_readme"] = readme.is_file()
    if readme.is_file():
        out["numbers"] = readme_numbers(readme.read_text(encoding="utf-8"))
    out["files"] = sorted(p.relative_to(ROOT).as_posix() for p in folder.rglob("*") if p.is_file())[:200]
    return out


def build() -> dict:
    return {
        "generator": "scripts/measured_numbers.py",
        "exams": exams(),
        "toolcall": toolcall(),
        "retrain": retrain(),
        "endpoint": endpoint(),
        "scout": scout(),
        "smoke": smoke(),
        "labelling": labelling(),
        "hosted_nano": hosted_nano(),
        "nightschool": nightschool(),
    }


def render() -> str:
    return json.dumps(build(), indent=1, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="exit 1 if web/src/data/measured.json differs from the evidence")
    a = ap.parse_args(argv)
    text = render()
    if a.check:
        have = OUT.read_text(encoding="utf-8") if OUT.is_file() else ""
        if have.replace("\r\n", "\n") != text:
            print("web/src/data/measured.json is out of date: run python scripts/measured_numbers.py", file=sys.stderr)
            return 1
        print("measured.json matches the evidence")
        return 0
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
