#!/usr/bin/env python3
"""Scan the repository for credential-shaped strings. Standard library only; fails on any match.

    python scripts/scan_secrets.py [--history]

Scans every tracked text file (and with --history every line ever added on any ref). A match is reported as file and line
(history: the commit too) and never printed. The bare prefix `ratchet-judge-` is fine; a suffix after it is a match.

A line is allowed only when the matched value is an obvious placeholder (see PLACEHOLDER) or when (path, rule) is in ALLOW.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

RULES: dict[str, re.Pattern[str]] = {
    "nvidia-api-key": re.compile(r"\bnvapi-[A-Za-z0-9_\-]{20,}"),
    "tavily-key": re.compile(r"\btvly-[A-Za-z0-9_\-]{16,}"),
    "huggingface-token": re.compile(r"\bhf_[A-Za-z0-9]{30,}"),
    "anthropic-key": re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{10,}"),
    "openai-style-key": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{32,}"),
    "bearer-token": re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=\-]{20,}"),
    "judge-code": re.compile(r"ratchet-judge-[A-Za-z0-9_\-]+"),
    "github-token": re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{30,}"),
    "aws-access-key": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "private-key": re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"),
}
# A value that is plainly not a credential: made of x's, "fake", "example", "placeholder", "your", "..." and the like.
PLACEHOLDER = re.compile(
    r"(x{4,}|fake|dummy|example|placeholder|sample|your[-_]|<[^>]*>|\.\.\.|0{8,}|1234567|abcdef|\$\{|\$[A-Z_]+)", re.I
)
# (path prefix, rule) pairs allowed beyond placeholders. Keep this list short and narrow.
ALLOW: set[tuple[str, str]] = set()
# A Bearer value made only of lower-case words and hyphens (a test's "some-fake-key" spelled out) is a test double, not a token.
WORDS_ONLY = re.compile(r"Bearer\s+[a-z]+(?:-[a-z]+)+$")
BINARY_EXT = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".svg",
    ".woff",
    ".woff2",
    ".pdf",
    ".safetensors",
    ".db",
    ".zip",
    ".mp4",
}
SELF = {"scripts/scan_secrets.py"}


def hits(path: str, line: str) -> list[str]:
    out = []
    for rule, rx in RULES.items():
        for m in rx.finditer(line):
            if rule != "private-key" and PLACEHOLDER.search(m.group(0)):
                continue
            if rule == "bearer-token" and WORDS_ONLY.match(m.group(0)) and path.startswith("tests/"):
                continue
            if any(path.startswith(p) and rule == r for p, r in ALLOW):
                continue
            out.append(rule)
    return out


def scan_tree() -> list[str]:
    found = []
    names = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout.decode().split("\0")
    for name in filter(None, names):
        p = ROOT / name
        if name in SELF or p.suffix.lower() in BINARY_EXT or not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for no, line in enumerate(text.splitlines(), 1):
            for rule in hits(name, line):
                found.append(f"{name}:{no}: {rule}")
    return found


def scan_history() -> list[str]:
    found = []
    proc = subprocess.Popen(
        ["git", "log", "--all", "-p", "--no-color", "--format=commit %h", "--no-ext-diff", "--text"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
    )
    commit = path = ""
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.decode("utf-8", "replace").rstrip("\n")
        if line.startswith("commit ") and len(line) < 20:
            commit = line[7:]
        elif line.startswith("+++ b/"):
            path = line[6:]
        elif line.startswith("+") and not line.startswith("+++") and path not in SELF:
            if Path(path).suffix.lower() in BINARY_EXT:
                continue
            for rule in hits(path, line[1:]):
                found.append(f"{path} (commit {commit}): {rule}")
    proc.wait()
    return sorted(set(found))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", action="store_true", help="also scan every line added on any ref")
    a = ap.parse_args()
    found = scan_tree() + (scan_history() if a.history else [])
    for f in found:
        print(f"SECRET-SHAPED  {f}")
    print(f"scan_secrets: {len(found)} match(es)" + (" (working tree and history)" if a.history else " (working tree)"))
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
