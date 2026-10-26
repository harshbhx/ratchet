"""Draw docs/img/results.svg, a static chart of the text-to-SQL exam scores (no page embeds it now), from web/src/data/exams.json.

That file is computed from the saved answers by `scripts/text2sql/exam_numbers.py`. Run this again after it changes:
`python scripts/readme_results_svg.py` (add `--check` to compare only).

The chart is a plain SVG with its own dark card, so it reads the same on GitHub's light and dark themes. Every number is read from
the JSON; none is typed here.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "web" / "src" / "data" / "exams.json"
OUT = ROOT / "docs" / "img" / "results.svg"

BARS = [
    ("base", "Untrained", "#94a3b8"),
    ("teacher", "Teacher", "#f97316"),
    ("r1", "Round 1", "#60a5fa"),
    ("r2", "Round 2", "#2563eb"),
]
W, H = 960, 400
PLOT_TOP, PLOT_BOTTOM = 96, 316
YMAX = 0.5


def y(rate: float) -> float:
    return PLOT_BOTTOM - (PLOT_BOTTOM - PLOT_TOP) * min(rate, YMAX) / YMAX


def panel(x0: float, width: float, key: str, title: str, sub: str, scores: dict) -> list[str]:
    out = [f'<text x="{x0}" y="62" class="h">{title}</text>', f'<text x="{x0}" y="82" class="s">{sub}</text>']
    for tick in (0, 0.1, 0.2, 0.3, 0.4, 0.5):
        out.append(f'<line x1="{x0}" x2="{x0 + width}" y1="{y(tick):.1f}" y2="{y(tick):.1f}" class="g"/>')
        out.append(f'<text x="{x0 - 8}" y="{y(tick) + 4:.1f}" class="t" text-anchor="end">{int(tick * 100)}%</text>')
    slot = width / len(BARS)
    bw = slot * 0.56
    for i, (name, label, color) in enumerate(BARS):
        s = scores[name]
        cx = x0 + slot * i + slot / 2
        out.append(
            f'<rect x="{cx - bw / 2:.1f}" y="{y(s["rate"]):.1f}" width="{bw:.1f}" '
            f'height="{PLOT_BOTTOM - y(s["rate"]):.1f}" rx="5" fill="{color}"/>'
        )
        out.append(f'<line x1="{cx:.1f}" x2="{cx:.1f}" y1="{y(s["lo"]):.1f}" y2="{y(s["hi"]):.1f}" class="w"/>')
        out.append(f'<line x1="{cx - 6:.1f}" x2="{cx + 6:.1f}" y1="{y(s["lo"]):.1f}" y2="{y(s["lo"]):.1f}" class="w"/>')
        out.append(f'<line x1="{cx - 6:.1f}" x2="{cx + 6:.1f}" y1="{y(s["hi"]):.1f}" y2="{y(s["hi"]):.1f}" class="w"/>')
        out.append(f'<text x="{cx:.1f}" y="{y(s["hi"]) - 8:.1f}" class="v" text-anchor="middle">{s["rate"] * 100:.1f}%</text>')
        out.append(f'<text x="{cx:.1f}" y="{PLOT_BOTTOM + 22}" class="t" text-anchor="middle">{label}</text>')
        out.append(f'<text x="{cx:.1f}" y="{PLOT_BOTTOM + 38}" class="t2" text-anchor="middle">{s["agree"]} of {s["n"]}</text>')
    return out


def build() -> str:
    d = json.loads(DATA.read_text(encoding="utf-8"))
    exams = {e["id"]: e for e in d["exams"]}
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" '
        'aria-label="Strict exam scores for the untrained model, the teacher and two trained adapters on two text-to-SQL exams">',
        "<style>"
        "text{font-family:Inter,Segoe UI,Helvetica,Arial,sans-serif}"
        ".h{fill:#f8fafc;font-size:17px;font-weight:600}.s{fill:#94a3b8;font-size:12.5px}"
        ".t{fill:#cbd5e1;font-size:12px}.t2{fill:#94a3b8;font-size:11px}.v{fill:#f8fafc;font-size:13px;font-weight:600}"
        ".g{stroke:#1e293b;stroke-width:1}.w{stroke:#e2e8f0;stroke-width:1.5}.n{fill:#94a3b8;font-size:12px}"
        "</style>",
        f'<rect width="{W}" height="{H}" rx="16" fill="#0b1220"/>',
    ]
    parts += panel(
        70, 380, "exam1", "Exam 1", f"{exams['exam1']['rows']} questions, written by the teacher", d["scores"]["exam1"]
    )
    parts += panel(
        540, 380, "exam2", "Exam 2", f"{exams['exam2']['rows']} questions, written by a different model", d["scores"]["exam2"]
    )
    parts.append(
        f'<text x="{W / 2}" y="{H - 22}" class="n" text-anchor="middle">'
        "Strict exact-match score on text-to-SQL, with the 95% range. "
        "Rounds 1 and 2 are the adapter after each training round. Exact rows only. Real models, 22 to 24 Oct 2026.</text>"
    )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def main() -> int:
    svg = build()
    if "--check" in sys.argv:
        same = OUT.exists() and OUT.read_text(encoding="utf-8") == svg
        print("ok" if same else "docs/img/results.svg is out of date: run python scripts/readme_results_svg.py")
        return 0 if same else 1
    OUT.write_text(svg, encoding="utf-8", newline="\n")
    print(f"wrote {OUT.relative_to(ROOT)} ({len(svg)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
