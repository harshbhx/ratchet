"""Draw docs/img/toolcall-results.svg, the tool-calling results chart, from evidence/toolcall/results.json.

That file is computed from the saved answers by `scripts/toolcall_results.py`. Run this again after it changes:
`python scripts/toolcall_results_svg.py` (add `--check` to compare only).

Same look as docs/img/results.svg (`scripts/readme_results_svg.py`): a plain SVG with its own dark card, so it reads the same on
light and dark themes. Every number is read from the JSON; none is typed here. Stdlib only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "evidence" / "toolcall" / "results.json"
OUT = ROOT / "docs" / "img" / "toolcall-results.svg"

BARS = [
    ("base", "Untrained", "#94a3b8"),
    ("adapter", "Adapter", "#2563eb"),
    ("ultra", "Teacher (Ultra)", "#f97316"),
]
PANELS = [("hard", "Hard exam"), ("bfcl", "BFCL exam")]
W, H = 960, 410
PLOT_TOP, PLOT_BOTTOM = 96, 316
YMAX = 1.0


def y(rate: float) -> float:
    return PLOT_BOTTOM - (PLOT_BOTTOM - PLOT_TOP) * min(rate, YMAX) / YMAX


def panel(x0: float, width: float, title: str, sub: str, scores: dict) -> list[str]:
    out = [f'<text x="{x0}" y="62" class="h">{title}</text>', f'<text x="{x0}" y="82" class="s">{sub}</text>']
    for tick in (0, 0.25, 0.5, 0.75, 1.0):
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
        out.append(f'<text x="{cx:.1f}" y="{PLOT_BOTTOM + 38}" class="t2" text-anchor="middle">{s["agree"]}/{s["n"]}</text>')
    return out


def build() -> str:
    d = json.loads(DATA.read_text(encoding="utf-8-sig"))
    sc = d["scores"]
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" '
        'aria-label="Strict tool-calling scores for the untrained model, the tool-calling adapter and the teacher '
        'on a hard exam and a BFCL exam, with the 95% range">',
        "<title>Tool-calling results: strict scores with the 95% range on two exams</title>",
        "<style>"
        "text{font-family:Inter,Segoe UI,Helvetica,Arial,sans-serif}"
        ".h{fill:#f8fafc;font-size:17px;font-weight:600}.s{fill:#94a3b8;font-size:12.5px}"
        ".t{fill:#cbd5e1;font-size:12px}.t2{fill:#94a3b8;font-size:11px}.v{fill:#f8fafc;font-size:13px;font-weight:600}"
        ".g{stroke:#1e293b;stroke-width:1}.w{stroke:#e2e8f0;stroke-width:1.5}.n{fill:#94a3b8;font-size:12px}"
        "</style>",
        f'<rect width="{W}" height="{H}" rx="16" fill="#0b1220"/>',
    ]
    subs = {
        "hard": f"{d['exams']['hard']['rows']} questions, {d['exams']['hard']['tools']} tools never seen in training",
        "bfcl": f"{d['exams']['bfcl']['rows']} questions from the Berkeley Function Calling Leaderboard",
    }
    for (key, title), x0 in zip(PANELS, (70, 540), strict=True):
        parts += panel(x0, 380, title, subs[key], sc[key])
    parts.append(
        f'<text x="{W / 2}" y="{H - 34}" class="n" text-anchor="middle">'
        "Tool calling: the adapter against the untrained model and the teacher, with the 95% range. "
        f"Measured on real models, {d['date']}.</text>"
    )
    parts.append(
        f'<text x="{W / 2}" y="{H - 14}" class="n" text-anchor="middle">'
        "Strict scoring, one decoder setting, rented Modal T4, synthetic training data.</text>"
    )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def main() -> int:
    svg = build()
    if "--check" in sys.argv:
        same = OUT.exists() and OUT.read_text(encoding="utf-8") == svg
        print("ok" if same else "docs/img/toolcall-results.svg is out of date: run python scripts/toolcall_results_svg.py")
        return 0 if same else 1
    OUT.write_text(svg, encoding="utf-8", newline="\n")
    print(f"wrote {OUT.relative_to(ROOT)} ({len(svg)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
