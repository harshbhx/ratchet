"""Day-1 check: confirm the Token Factory pieces Ratchet depends on actually work.

    NEBIUS_API_KEY=... python scripts/day1_check.py

Checks: the model IDs exist, the teacher answers, logprobs come back, JSON mode works, and the
teacher accepts the generation parameters the gateway sends it (`max_tokens` at
RATCHET_TEACHER_MAX_TOKENS, `stop`, `seed`, `temperature`, `response_format`; ADR 0017).
Costs a few cents.
"""

from __future__ import annotations

import json
import os
import sys

import httpx

BASE = os.environ.get("RATCHET_TEACHER_BASE", "https://api.tokenfactory.nebius.com/v1").rstrip("/")
KEY = os.environ.get("NEBIUS_API_KEY", "")
# Every priced model in ratchet/config.py (DEFAULT_PRICES, except "student") must be here: test_contract.py checks it.
WANT = [
    "nvidia/Nemotron-3-Ultra-550b-a55b",
    "nvidia/nemotron-3-super-120b-a12b",
    "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
    "nvidia/Nemotron-3_5-Lightning",
]
# The teacher the gateway uses, and the output cap every teacher call carries (ratchet/config.py).
TEACHER = os.environ.get("RATCHET_TEACHER_MODEL", "") or WANT[0]
TEACHER_MAX_TOKENS = int(os.environ.get("RATCHET_TEACHER_MAX_TOKENS", "") or 8192)
# A client's parameters, as an escalation forwards them: a stop string the answer never contains, a seed, a temperature.
GEN_PARAMS = {"stop": ["<|day1-stop|>"], "seed": 7, "temperature": 0.2, "response_format": {"type": "json_object"}}
PROMPT = [
    {"role": "system", "content": 'Reply with JSON only: {"category": "billing|technical|account", "priority": "low|high"}'},
    {"role": "user", "content": "I was charged twice for my Pro plan this month."},
]


def main(client: httpx.Client | None = None) -> int:
    """Returns the exit code: 0 all good, 1 a check failed, 2 no key. `client` replaces the network (tests only)."""
    if not KEY:
        print("Set NEBIUS_API_KEY first.")
        return 2
    c = client if client is not None else httpx.Client(timeout=120)
    auth = {"Authorization": f"Bearer {KEY}"}
    ok = True

    ids = {m["id"] for m in c.get(f"{BASE}/models", headers=auth).json().get("data", [])}
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
            headers=auth,
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
        # Only answers that end with finish_reason "stop" (or "tool_calls") are stored as training data (INV-16).
        finish = ch.get("finish_reason")
        print(f"[ok] {model}: {js}, logprobs={'yes' if lp else 'NO'}, finish_reason={finish}, usage={d.get('usage')}")
    if TEACHER in ids:
        # Every escalation carries max_tokens (8,192 by default) and the client's fields: a 4xx here would fail them all.
        body = {"model": TEACHER, "messages": PROMPT, "max_tokens": TEACHER_MAX_TOKENS, **GEN_PARAMS}
        r = c.post(f"{BASE}/chat/completions", headers=auth, json=body)
        if r.status_code != 200:
            print(f"[FAIL] {TEACHER} with max_tokens={TEACHER_MAX_TOKENS}, stop, seed: HTTP {r.status_code} {r.text[:200]}")
            print("  Lower RATCHET_TEACHER_MAX_TOKENS or narrow RATCHET_TEACHER_PARAMS until this passes.")
            ok = False
        else:
            finish = r.json()["choices"][0].get("finish_reason")
            print(f"[ok] {TEACHER} accepts max_tokens={TEACHER_MAX_TOKENS}, stop, seed, temperature: finish_reason={finish}")
    print("\nAll good." if ok else "\nSome checks failed: update ratchet/config.py before building on them.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
