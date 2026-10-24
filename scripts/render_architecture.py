"""Render docs/img/architecture.svg and docs/img/where-it-runs.svg to the PNG exports with resvg and the real Inter font.

Usage: python scripts/render_architecture.py --tools DIR [--fonts DIR]
DIR holds node_modules/@resvg/resvg-js (npm install @resvg/resvg-js there, outside the repo).
Fonts come from web/node_modules/@fontsource-variable (npm ci in web/, or pass --fonts);
static instances are cut into a temp folder.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMG = ROOT / "docs" / "img"
BLOCK = re.compile(r"[ \t]*@media \(prefers-color-scheme: dark\)\{\s*/\*dark:start\*/(.*?)/\*dark:end\*/\s*\}", re.S)

JS = """
const {Resvg} = require(process.argv[2] + '/node_modules/@resvg/resvg-js');
const fs = require('fs');
const [svgPath, pngPath, width, fontDir] = process.argv.slice(3);
const files = fs.readdirSync(fontDir).map(f => fontDir + '/' + f);
const r = new Resvg(fs.readFileSync(svgPath, 'utf8'), {
  fitTo: {mode: 'width', value: Number(width)},
  font: {fontFiles: files, loadSystemFonts: false, defaultFontFamily: 'Inter'},
});
fs.writeFileSync(pngPath, r.render().asPng());
"""


def themed(svg: str, dark: bool) -> str:
    """The light SVG drops the dark block; the dark one applies it after the base rules."""
    return BLOCK.sub((lambda m: m.group(1)) if dark else "", svg)


def cut_fonts(src: Path, out: Path) -> None:
    from fontTools.ttLib import TTFont
    from fontTools.varLib.instancer import instantiateVariableFont

    jobs = [("inter/files/inter-latin-wght-normal.woff2", "Inter", w) for w in (400, 500, 600)]
    jobs.append(("jetbrains-mono/files/jetbrains-mono-latin-wght-normal.woff2", "JetBrains Mono", 500))
    for rel, family, weight in jobs:
        font = instantiateVariableFont(TTFont(src / rel), {"wght": weight})
        for rec in font["name"].names:
            if rec.nameID in (1, 16):
                rec.string = family
            elif rec.nameID in (2, 17):
                rec.string = "Regular"
        font["OS/2"].usWeightClass = weight
        font.flavor = None
        font.save(out / f"{family.replace(' ', '')}-{weight}.ttf")


def devpost(dark_svg: str) -> str:
    """1200x800 (3:2): the 16:9 diagram letterboxed on the dark background."""
    body = re.sub(r"^<svg[^>]*>", "", dark_svg.strip()).removesuffix("</svg>")
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="800" viewBox="0 0 1200 800">'
        '<rect width="1200" height="800" fill="#0b0b0c"/>'
        f'<g transform="translate(0 62.5) scale(0.75)" font-family="\'Inter\', system-ui, sans-serif">{body}</g></svg>'
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tools", required=True, type=Path)
    ap.add_argument("--fonts", type=Path, default=ROOT / "web" / "node_modules" / "@fontsource-variable")
    args = ap.parse_args()
    src = (IMG / "architecture.svg").read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as tmp_s:
        tmp = Path(tmp_s)
        (tmp / "fonts").mkdir()
        cut_fonts(args.fonts, tmp / "fonts")
        (tmp / "r.js").write_text(JS, encoding="utf-8")
        dark = themed(src, True)
        jobs = [
            (themed(src, False), "architecture-light.png", 1600),
            (dark, "architecture-dark.png", 1600),
            (devpost(dark), "architecture-devpost-1200x800.png", 1200),
        ]
        where = (IMG / "where-it-runs.svg").read_text(encoding="utf-8")
        jobs += [(themed(where, False), "where-it-runs-light.png", 1600), (themed(where, True), "where-it-runs-dark.png", 1600)]
        for i, (svg, name, width) in enumerate(jobs):
            (tmp / f"{i}.svg").write_text(svg, encoding="utf-8")
            cmd = [
                "node",
                str(tmp / "r.js"),
                str(args.tools),
                str(tmp / f"{i}.svg"),
                str(IMG / name),
                str(width),
                str(tmp / "fonts"),
            ]
            subprocess.run(cmd, check=True)
            print("wrote", IMG / name)


if __name__ == "__main__":
    main()
