"""Day-1 check: confirm the Token Factory pieces Ratchet depends on actually work.

    NEBIUS_API_KEY=... python scripts/day1_check.py

Checks: the model IDs exist, the teacher answers, logprobs come back, JSON mode works.
Costs a few cents.
"""

from __future__ import annotations

import json
import os
import sys

import httpx

BASE = os.environ.get("RATCHET_TEACHER_BASE", "https://api.tokenfactory.nebius.com/v1").rstrip("/")
KEY = os.environ.get("NEBIUS_API_KEY", "")
WANT = [
    "nvidia/Nemotron-3-Ultra-550b-a55b",
    "nvidia/nemotron-3-super-120b-a12b",
    "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
    "nvidia/Nemotron-3_5-Lightning",
]
PROMPT = [
    {"role": "system", "content": 'Reply with JSON only: {"category": "billing|technical|account", "priority": "low|high"}'},
    {"role": "user", "content": "I was charged twice for my Pro plan this month."},
]


def main() -> int:
    if not KEY:
        print("Set NEBIUS_API_KEY first.")
        return 2
    c = httpx.Client(timeout=120, headers={"Authorization": f"Bearer {KEY}"})
    ok = True

    ids = {m["id"] for m in c.get(f"{BASE}/models").json().get("data", [])}
    for m in WANT:
        found = m in ids
        ok &= found
        print(f"[{'ok' if found else 'MISSING'}] model {m}")
    if not all(m in ids for m in WANT):
        print("  NVIDIA models available:", sorted(i for i in ids if "emotron" in i.lower()))

    for model in WANT:
        if model not in ids:
            continue
        r = c.post(
            f"{BASE}/chat/completions",
            json={
                "model": model,
                "messages": PROMPT,
                "max_tokens": 400,
                "logprobs": True,
                "response_format": {"type": "json_object"},
            },
        )
        if r.status_code != 200:
            print(f"[FAIL] {model}: HTTP {r.status_code} {r.text[:200]}")
            ok = False
            continue
        d = r.json()
        ch = d["choices"][0]
        lp = (ch.get("logprobs") or {}).get("content") or []
        text = ch["message"].get("content") or ""
        try:
            json.loads(text.split("</think>")[-1].strip().strip("`").removeprefix("json"))
            js = "json ok"
        except Exception:
            js = "json NOT parseable"
        print(f"[ok] {model}: {js}, logprobs={'yes' if lp else 'NO'}, usage={d.get('usage')}")
    print("\nAll good." if ok else "\nSome checks failed: update ratchet/config.py before building on them.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
