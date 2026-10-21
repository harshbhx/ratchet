"""Day-1 check: confirm the Token Factory pieces Ratchet depends on actually work.

    NEBIUS_API_KEY=... python scripts/day1_check.py

Checks: the model IDs exist, the teacher answers, logprobs come back, JSON mode works, the
teacher accepts the generation parameters the gateway sends it (`max_tokens` at
RATCHET_TEACHER_MAX_TOKENS, `stop`, `seed`, `temperature`, `response_format`; ADR 0017), whether
it takes `response_format` json_schema (strict) with an answer that matches the schema (ADR 0042),
and whether the embeddings model behind `/v1/embeddings` answers (RATCHET_EMBEDDINGS_MODEL at
RATCHET_EMBEDDINGS_BASE, skipped when that is `off`; FR-1.51). A teacher that refuses json_schema is
a warning, not a failure: the gateway then sends json_object with the schema in the system message.
Costs a few cents.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]

BASE = os.environ.get("RATCHET_TEACHER_BASE", "https://api.tokenfactory.nebius.com/v1").rstrip("/")
KEY = os.environ.get("NEBIUS_API_KEY", "")
# Every priced model in ratchet/config.py (DEFAULT_PRICES, except "student") must be here: test_contract.py checks it.
WANT = [
    "nvidia/Nemotron-3-Ultra-550b-a55b",
    "nvidia/nemotron-3-super-120b-a12b",
    "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
    "nvidia/Nemotron-3_5-Lightning",
]
# Every embeddings model in ratchet/config.py (EMBEDDING_PRICES) must be here: test_adjacent_endpoints.py checks it.
WANT_EMBEDDINGS = ["Qwen/Qwen3-Embedding-8B"]
# The embeddings model and base the gateway's /v1/embeddings uses (empty base: the teacher's; `off`: no check).
EMBEDDINGS = os.environ.get("RATCHET_EMBEDDINGS_MODEL", "") or WANT_EMBEDDINGS[0]
EMBEDDINGS_BASE = os.environ.get("RATCHET_EMBEDDINGS_BASE", "").strip().rstrip("/")
EMBEDDINGS_KEY = os.environ.get("RATCHET_EMBEDDINGS_KEY", "")
# The teacher the gateway uses, and the output cap every teacher call carries (ratchet/config.py).
TEACHER = os.environ.get("RATCHET_TEACHER_MODEL", "") or WANT[0]
TEACHER_MAX_TOKENS = int(os.environ.get("RATCHET_TEACHER_MAX_TOKENS", "") or 8192)
# A client's parameters, as an escalation forwards them: a stop string the answer never contains, a seed, a temperature.
GEN_PARAMS = {"stop": ["<|day1-stop|>"], "seed": 7, "temperature": 0.2, "response_format": {"type": "json_object"}}
PROMPT = [
    {"role": "system", "content": 'Reply with JSON only: {"category": "billing|technical|account", "priority": "low|high"}'},
    {"role": "user", "content": "I was charged twice for my Pro plan this month."},
]
# Structured outputs (ADR 0042): what an OpenAI SDK's `.parse()` sends for a small Pydantic model.
SCHEMA_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "ticket",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": ["billing", "technical", "account"]},
                "priority": {"type": "string", "enum": ["low", "high"]},
            },
            "required": ["category", "priority"],
            "additionalProperties": False,
        },
    },
}


def schema_errors(text: str) -> list[str]:
    """Why a json_schema answer does not match SCHEMA_FORMAT's schema (`ratchet.jsonschema_lite`, as the gateway checks
    it, after the same cleaning: reasoning and a fence removed), or [] when it does."""
    if str(ROOT) not in sys.path:  # `python scripts/day1_check.py` puts scripts/, not the repo, on the path
        sys.path.insert(0, str(ROOT))
    from ratchet import schema_tasks, tasks

    task = tasks.Task("day1-schema").bound(schema_tasks.contract_for(SCHEMA_FORMAT))
    assert task.contract is not None
    return task.contract.errors(tasks.clean_answer(task, text))


def check_json_schema(c: httpx.Client, auth: dict[str, str]) -> bool:
    """The teacher with `response_format` json_schema: `[ok]` when it answers with JSON that matches the schema;
    `[warn]` when it refuses the format with a 400 (the gateway asks again with json_object and the schema in the system
    message, ADR 0042) or its answer does not match (the gateway repairs it once, RATCHET_SCHEMA_REPAIR); `[FAIL]`
    (returns False) for any other status or a body without an answer."""
    body = {"model": TEACHER, "messages": PROMPT, "max_tokens": 400, "response_format": SCHEMA_FORMAT}
    r = c.post(f"{BASE}/chat/completions", headers=auth, json=body)
    if r.status_code == 400:
        print(f"[warn] {TEACHER} refused response_format json_schema: HTTP 400 {r.text[:200]}")
        print("  The gateway asks again with json_object and the schema in the system message (ADR 0042).")
        return True
    if r.status_code != 200:
        print(f"[FAIL] {TEACHER} with response_format json_schema: HTTP {r.status_code} {r.text[:200]}")
        return False
    try:
        text = (r.json()["choices"][0].get("message") or {}).get("content") or ""
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        print(f"[FAIL] {TEACHER} with response_format json_schema: no answer in the body {r.text[:200]}")
        return False
    errors = schema_errors(text)
    if errors:
        print(f"[warn] {TEACHER} took json_schema, but its answer does not match: {errors[0]}")
        print("  The gateway repairs such an answer once (RATCHET_SCHEMA_REPAIR) and never stores one that does not match.")
    else:
        print(f"[ok] {TEACHER} accepts response_format json_schema (strict): the answer matches the schema")
    return True


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
        ok &= check_json_schema(c, auth)
    ok &= check_embeddings(c, ids, auth)
    print("\nAll good." if ok else "\nSome checks failed: update ratchet/config.py before building on them.")
    return 0 if ok else 1


def check_embeddings(c: httpx.Client, ids: set[str], auth: dict[str, str]) -> bool:
    """The /v1/embeddings pass-through's model: listed (on the teacher's endpoint) and answering with one vector of
    floats per input. Skipped, as passed, when RATCHET_EMBEDDINGS_BASE is `off`."""
    if EMBEDDINGS_BASE.lower() == "off":
        print("[skip] embeddings: RATCHET_EMBEDDINGS_BASE=off")
        return True
    base = EMBEDDINGS_BASE or BASE
    if base == BASE:
        listed = EMBEDDINGS in ids
        print(f"[{'ok' if listed else 'MISSING'}] model {EMBEDDINGS}")
        if not listed:
            print("  Embedding models available:", sorted(i for i in ids if "embed" in i.lower()))
            return False
    # NEBIUS_API_KEY goes only to Token Factory, as in the gateway: another base gets RATCHET_EMBEDDINGS_KEY or no key.
    headers = {"Authorization": f"Bearer {EMBEDDINGS_KEY}"} if EMBEDDINGS_KEY else (auth if base == BASE else {})
    r = c.post(f"{base}/embeddings", headers=headers, json={"model": EMBEDDINGS, "input": ["Ratchet day-1 check", "refund"]})
    if r.status_code != 200:
        print(f"[FAIL] embeddings {EMBEDDINGS}: HTTP {r.status_code} {r.text[:200]}")
        return False
    data = r.json().get("data") or []
    vectors = [d.get("embedding") for d in data if isinstance(d, dict)]
    good = len(vectors) == 2 and all(isinstance(v, list) and v and len(v) == len(vectors[0]) for v in vectors)
    if not good:
        print(f"[FAIL] embeddings {EMBEDDINGS}: expected two float vectors of one length, got {str(data)[:200]}")
        return False
    print(f"[ok] embeddings {EMBEDDINGS}: {len(vectors[0])} dimensions, usage={r.json().get('usage')}")
    return True


if __name__ == "__main__":
    sys.exit(main())
