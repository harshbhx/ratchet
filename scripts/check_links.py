#!/usr/bin/env python3
"""Check the links in README.md, docs/ and the docs site's content. Standard library only.

    python scripts/check_links.py [--offline] [--cache FILE] [--timeout 20]

Fails (exit 1) only on a broken repo-internal link (missing file or heading) and on a 404 or 410 from our own domains
(ratchet.dualmindlab.tech and its subdomains, github.com/harshbhx/*, huggingface.co/harshbhx/*). Any other external problem
is a warning, so a flaky third-party site never breaks the build. 429 and other bot-blocking answers count as alive.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ["README.md", "docs/**/*.md", "docs-site/README.md", "docs-site/src/content/**/*.md", "docs-site/src/content/**/*.mdx"]
SKIP_SCHEMES = ("mailto:", "tel:", "javascript:", "data:")
# Never fetched: local addresses, private or judge-only items, and sites that refuse bots.
EXCLUDED_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "linkedin.com", "twitter.com", "x.com", "example.com", "example.org")
EXCLUDED_PARTS = ("judge", "<", "{", "$")
OWN_DOMAIN = "ratchet.dualmindlab.tech"
OWN_PATHS = (("github.com", "/harshbhx/"), ("huggingface.co", "/harshbhx/"))
# Links to this repo's main branch are checked against the checkout, so a file added in the same change does not 404.
REPO_MAIN = re.compile(r"^https://github\.com/harshbhx/ratchet/(?:tree|blob)/main/([^?#]*)(#.*)?$")
ALIVE_ON = {401, 403, 405, 406, 429, 451, 999}
UA = "Mozilla/5.0 (compatible; ratchet-link-check/1)"
CACHE_TTL = 24 * 3600

FENCE = re.compile(r"^\s*(```|~~~)")
MD_LINK = re.compile(r"!?\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
HTML_ATTR = re.compile(r"""(?:href|src)\s*=\s*["']([^"']+)["']""", re.I)
AUTOLINK = re.compile(r"<(https?://[^>\s]+)>")
BARE = re.compile(r"""(?<![(\"'<\[=])\bhttps?://[^\s)>\]"'`]+""")
REF_DEF = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*<?(\S+?)>?(?:\s|$)")
HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_ANCHORS: dict[Path, set[str]] = {}


def slug(text: str) -> str:
    """GitHub's heading anchor: lower case, punctuation dropped, spaces to hyphens."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"<[^>]+>|[`*]", "", text).lower().strip()
    return re.sub(r"[^\w\- ]", "", text).replace(" ", "-")


def anchors(path: Path) -> set[str]:
    if path not in _ANCHORS:
        raw = path.read_text(encoding="utf-8", errors="replace")
        seen: dict[str, int] = {}
        out: set[str] = set(re.findall(r"""(?:id|name)\s*=\s*["']([^"']+)["']""", raw))
        fenced = False
        for line in raw.splitlines():
            if FENCE.match(line):
                fenced = not fenced
            m = None if fenced else HEADING.match(line)
            if m:
                s = slug(m.group(2))
                n = seen.get(s, 0)
                seen[s] = n + 1
                out.add(s if n == 0 else f"{s}-{n}")
        _ANCHORS[path] = out
    return _ANCHORS[path]


def extract(path: Path) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    fenced = False
    for no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        plain = re.sub(r"`[^`]*`", "", line)
        urls = [m.group(1) for m in MD_LINK.finditer(plain)]
        urls += HTML_ATTR.findall(plain) + AUTOLINK.findall(plain) + BARE.findall(plain)
        ref = REF_DEF.match(plain)
        if ref:
            urls.append(ref.group(1))
        found += [(no, u.rstrip(".,;:")) for u in dict.fromkeys(urls)]
    return found


def check_internal(src: Path, url: str) -> str | None:
    target, _, frag = url.partition("#")
    target = urllib.parse.unquote(target.partition("?")[0])
    if target.startswith("/"):
        return None  # a docs-site route: `npm run build` validates the docs site's own links
    dest = src if not target else (src.parent / target).resolve()
    if target and not dest.exists():
        return f"missing file {target}"
    docs_site = "docs-site" in src.parts
    if frag and dest.is_file() and dest.suffix == ".md" and not docs_site:
        if urllib.parse.unquote(frag).lower() not in anchors(dest):
            return f"missing heading #{frag} in {dest.relative_to(ROOT).as_posix()}"
    return None


def own(url: str) -> bool:
    p = urllib.parse.urlparse(url)
    host = (p.hostname or "").lower()
    if host == OWN_DOMAIN or host.endswith("." + OWN_DOMAIN):
        return True
    return any(host == h and p.path.lower().startswith(pre) for h, pre in OWN_PATHS)


def fetch(url: str, timeout: float) -> tuple[int, str]:
    """(status, note). Status 0 is a network failure. Retries with backoff; a 429 is accepted as alive."""
    last = (0, "no answer")
    for attempt in range(3):
        for method in ("HEAD", "GET"):
            req = urllib.request.Request(url.split("#")[0], method=method, headers={"User-Agent": UA, "Accept": "*/*"})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return r.status, ""
            except urllib.error.HTTPError as e:
                last = (e.code, str(e.reason or ""))
                if e.code == 429:
                    return 429, "rate limited"
                if method == "HEAD" and e.code in (400, 403, 404, 405, 501):
                    continue  # some servers refuse HEAD: ask again with GET
                break
            except Exception as e:  # noqa: BLE001 (timeouts, DNS, TLS: all the same to us)
                last = (0, type(e).__name__)
                break
        if 0 < last[0] < 500:
            return last
        time.sleep(2 * (attempt + 1))
    return last


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="check repo-internal links only")
    ap.add_argument("--cache", default="", help="JSON file of earlier external results")
    ap.add_argument("--timeout", type=float, default=20)
    a = ap.parse_args()

    files = sorted({p for pat in SOURCES for p in ROOT.glob(pat) if "node_modules" not in p.parts})
    failures: list[str] = []
    warnings: list[str] = []
    external: dict[str, list[str]] = {}
    for f in files:
        rel = f.relative_to(ROOT).as_posix()
        for no, url in extract(f):
            if url.startswith(SKIP_SCHEMES):
                continue
            on_main = REPO_MAIN.match(url)
            if on_main:
                problem = check_internal(ROOT / "README.md", on_main[1].rstrip("/") + (on_main[2] or ""))
                if problem:
                    failures.append(f"{rel}:{no}: {url}: {problem}")
                continue
            if url.startswith(("http://", "https://")):
                host = (urllib.parse.urlparse(url).hostname or "").lower()
                if any(host == h or host.endswith("." + h) for h in EXCLUDED_HOSTS):
                    continue
                if any(x in url.lower() for x in EXCLUDED_PARTS):
                    continue
                external.setdefault(url, []).append(f"{rel}:{no}")
            else:
                problem = check_internal(f, url)
                if problem:
                    failures.append(f"{rel}:{no}: {url}: {problem}")

    cache: dict[str, list] = {}
    if a.cache and Path(a.cache).exists():
        try:
            cache = json.loads(Path(a.cache).read_text())
        except ValueError:
            cache = {}
    now = time.time()
    fresh = lambda u: u in cache and now - cache[u][0] < CACHE_TTL and 200 <= cache[u][1] < 400  # noqa: E731
    todo = [] if a.offline else [u for u in external if not fresh(u)]
    with ThreadPoolExecutor(8) as pool:
        for url, (status, note) in zip(todo, pool.map(lambda u: fetch(u, a.timeout), todo), strict=True):
            cache[url] = [now, status, note]
    if not a.offline:
        for url, where in external.items():
            _, status, note = cache[url]
            if 200 <= status < 400 or status in ALIVE_ON:
                continue
            msg = f"{where[0]}: {url}: {status or 'no answer'} {note}".rstrip()
            (failures if own(url) and status in (404, 410) else warnings).append(msg)
    if a.cache:
        Path(a.cache).write_text(json.dumps(cache))

    print(f"checked {len(files)} files, {sum(len(v) for v in external.values())} external links ({len(external)} distinct)")
    for w in warnings:
        print(f"WARN  {w}")
    for f in failures:
        print(f"FAIL  {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
