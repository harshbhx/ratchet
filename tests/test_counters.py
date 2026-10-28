"""GET /ratchet/counters: the cumulative aggregates a keeper outside the container adds up (the live Worker does).

Each number only grows while the instance is the same, the instance id changes with the process, and nothing in the payload
is text, an address or a client.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

KEYS = {
    "instance", "started", "mode", "requests", "requests_split", "requests_student", "requests_teacher",
    "student_tokens_in", "student_tokens_out", "cost_usd", "errors", "scout_runs",
}  # fmt: skip


def _chat(c, text):
    return c.post("/v1/chat/completions", json={"model": "ratchet", "messages": [{"role": "user", "content": text}]})


def test_counters_start_at_zero_and_only_grow(gw):
    c = TestClient(gw.app)
    first = c.get("/ratchet/counters").json()
    assert set(first) == KEYS
    assert first["requests"] == 0 and first["cost_usd"] == 0 and first["errors"] == 0 and first["scout_runs"] == 0
    for i in range(3):
        assert _chat(c, f"hello {i}").status_code == 200
    second = c.get("/ratchet/counters").json()
    assert second["instance"] == first["instance"] and second["started"] == first["started"]
    assert second["requests"] == 3
    assert second["requests_teacher"] + second["requests_split"] + second["requests_student"] == second["requests"]
    assert all(second[k] >= first[k] for k in KEYS - {"instance", "started", "mode"})


def test_counters_count_scout_runs(gw):
    from ratchet.scout.prompts import EXAMPLE_QUESTIONS

    c = TestClient(gw.app)
    before = c.get("/ratchet/counters").json()
    assert c.post("/scout/run", json={"question": EXAMPLE_QUESTIONS[0]}).status_code == 200
    after = c.get("/ratchet/counters").json()
    assert after["scout_runs"] == before["scout_runs"] + 1
    assert after["requests"] > before["requests"]  # Scout's model calls are gateway calls
    assert after["instance"] == before["instance"]


def test_counters_carry_no_text_or_names(gw):
    c = TestClient(gw.app)
    secret = "my-very-private-question-text"
    _chat(c, secret)
    body = c.get("/ratchet/counters").json()
    assert secret not in json.dumps(body)
    assert all(isinstance(v, (int, float, str)) for v in body.values())


def test_a_new_process_has_a_new_instance(gw_env):
    a = TestClient(gw_env().app).get("/ratchet/counters").json()
    b = TestClient(gw_env().app).get("/ratchet/counters").json()
    assert a["instance"] != b["instance"]
