"""Regression tests for issues found while documenting v0.1, the v0.4 hardening (privacy, auth, streaming, BOMs), and
issues found while testing the live entry points offline (v0.5)."""

import hashlib
import json
import math
import os
import random
import sqlite3
import threading
import time
from dataclasses import replace

import httpx
import pytest
from fakes import OPEN_TEACHER, FakeOpenAIServer, RecordingProvider, RecordingRun, _request, _version

from ratchet import live, receipts, tasks
from ratchet.config import Settings
from ratchet.ledger import DAY_SECONDS, Ledger
from ratchet.nightschool import dataset
from ratchet.providers import Completion, GenParams, OpenAICompatProvider, served_logprobs
from ratchet.release import ReleaseGate, ReleasePolicy
from ratchet.router import Ratchet


class FakeTeacher:
    def __init__(self, fail=False):
        self.fail = fail

    def complete(self, messages, model, n=1, temperature=0.0, logprobs=False, response_format=None, params=None, **_):
        if self.fail:
            raise httpx.ConnectError("teacher down")
        return [
            Completion(
                text='{"category": "billing", "priority": "low"}', model=model, in_tokens=50, out_tokens=20, latency_ms=1000
            )
        ]


class BrokenStudent:
    def complete(self, *a, **k):
        raise httpx.ConnectError("endpoint down")


def test_old_database_file_is_migrated(tmp_path):
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE requests (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, day INTEGER, task TEXT);
        CREATE TABLE examples (id INTEGER PRIMARY KEY AUTOINCREMENT, hash TEXT UNIQUE);
        CREATE TABLE versions (id INTEGER PRIMARY KEY AUTOINCREMENT, state TEXT);
    """)
    con.commit()
    con.close()
    led = Ledger(str(db))
    cols = {r["name"] for r in led.db.execute("PRAGMA table_info(versions)")}
    assert "stage_since" in cols
    assert "seen" in {r["name"] for r in led.db.execute("PRAGMA table_info(examples)")}


def test_auto_day_counts_days_from_first_request():
    led = Ledger(":memory:", auto_day=True)
    t0 = 1_800_000_000.0
    led.log_request(ts=t0, task="t", served_by="teacher", cost_usd=0, baseline_cost_usd=0)
    led.log_request(ts=t0 + DAY_SECONDS + 5, task="t", served_by="teacher", cost_usd=0, baseline_cost_usd=0)
    assert [d["day"] for d in led.stats_by_day()] == [1, 2]


@pytest.mark.req("FR-1.8", "INV-7", "NFR-2")
def test_student_failure_falls_back_to_teacher():
    led = Ledger(":memory:")
    vid = led.create_version("b", "student-v1", 10, "h", {"threshold": 0.5}, state="live")
    r = Ratchet(Settings(), led, FakeTeacher(), lambda v: (BrokenStudent(), v.adapter_uri), seed=1)
    out = r.handle([{"role": "user", "content": "x"}], "ticket-triage")
    assert out.served_by == "teacher" and out.escalated
    assert any(e["kind"] == "student_error" for e in led.events())
    assert vid == 1


@pytest.mark.req("FR-2.3", "FR-8.3")
def test_fallback_model_uses_default_threshold():
    s = Settings()
    led = Ledger(":memory:")
    led.create_version("b", "student-v1", 10, "h", {"threshold": 0.2}, state="live")
    r = Ratchet(s, led, FakeTeacher(), lambda v: (None, s.fallback_student_model))
    v = led.live()
    assert r.threshold(v, s.fallback_student_model) == s.confidence_threshold
    assert r.threshold(v, "student-v1") == 0.2


@pytest.mark.req("FR-1.8", "INV-7", "NFR-2")
def test_teacher_failure_returns_502(monkeypatch):
    from fastapi.testclient import TestClient

    import ratchet.gateway as gw

    gw.rt.ratchet.teacher = FakeTeacher(fail=True)
    gw.rt.ratchet.resolve_student = lambda v: (BrokenStudent(), "x")
    c = TestClient(gw.app)
    resp = c.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 502
    gw.rt.reset()


@pytest.mark.req("FR-1.3")
def test_unknown_tasks_do_not_grow_registry():
    before = len(tasks.REGISTRY)
    for i in range(50):
        tasks.get(f"client-task-{i}")
    tasks.get("bad name with spaces!")
    assert len(tasks.REGISTRY) == before
    assert tasks.get("bad name with spaces!").name == "default"


@pytest.mark.req("FR-1.3")
def test_task_names_match_in_full():
    """`^...$` with `re.match` accepted a trailing newline, so `metadata.ratchet_task: "ticket-triage\n"` became a new
    task with a newline in its name. Names now match in full; the gateway refuses such a name with a 400."""
    assert tasks.valid_name("ticket-triage") and tasks.valid_name("a" * 64)
    for bad in ("ticket-triage\n", "a" * 65, "", "two words", "tab\t"):
        assert not tasks.valid_name(bad), repr(bad)
        assert tasks.get(bad).name == "default"


@pytest.mark.req("FR-6.7", "FR-16.2")
@pytest.mark.parametrize("alert_eprocess", [1, 0])  # the e-process (the default since FR-16.2) and the Wilson rule
def test_live_alert_fires_once_per_version(alert_eprocess):
    led = Ledger(":memory:")
    gate = ReleaseGate(led, ReleasePolicy(min_audits=5, alert_eprocess=alert_eprocess))
    vid = led.create_version("b", "a", 1, "h", {}, state="live")
    for _ in range(10):
        led.log_request(
            task="t", served_by="student", student_version=vid, audited=1, audit_agree=0, cost_usd=0, baseline_cost_usd=0
        )
    for _ in range(5):
        gate.tick()
    assert sum(e["kind"] == "alert" for e in led.events()) == 1


def test_policy_from_env(monkeypatch):
    monkeypatch.setenv("RATCHET_MIN_AUDITS", "42")
    monkeypatch.setenv("RATCHET_LIVE_FLOOR", "0.9")
    p = ReleasePolicy.from_env()
    assert p.min_audits == 42 and p.live_floor == pytest.approx(0.9)


@pytest.mark.req("FR-16.1", "FR-16.2")
def test_anytime_valid_settings_parse(monkeypatch):
    """The anytime-valid gate's variables (ADR 0025) parse into the policy and the settings, `defaults` fills only what
    is unset, and a bad value stops startup naming the variable."""
    for env in ("RATCHET_SEQUENTIAL", "RATCHET_ALPHA_ROLLBACK", "RATCHET_MIN_AUDITS", "RATCHET_ALERT_EPROCESS"):
        monkeypatch.delenv(env, raising=False)
    assert (ReleasePolicy().sequential, ReleasePolicy().alert_eprocess) == (0, 1)  # opt-in rule; the alert's default
    monkeypatch.setenv("RATCHET_SEQUENTIAL", "1")
    monkeypatch.setenv("RATCHET_ALPHA_ROLLBACK", "0.01")
    monkeypatch.setenv("RATCHET_ALERT_EPROCESS", "0")
    p = ReleasePolicy.from_env(defaults={"min_audits": 15})
    assert (p.sequential, p.alpha_rollback, p.alert_eprocess, p.min_audits) == (1, 0.01, 0, 15)
    monkeypatch.setenv("RATCHET_MIN_AUDITS", "30")
    assert ReleasePolicy.from_env(defaults={"min_audits": 15}).min_audits == 30  # the variable wins
    for env, value, says in (
        ("RATCHET_SEQUENTIAL", "2", "0 or 1"),
        ("RATCHET_SEQUENTIAL", "yes", "whole number"),
        ("RATCHET_ALPHA_ROLLBACK", "1", "strictly between 0 and 1"),
    ):
        monkeypatch.setenv(env, value)
        with pytest.raises(ValueError, match=f"{env}.*{says}"):
            ReleasePolicy.from_env()
        monkeypatch.setenv(env, {"RATCHET_SEQUENTIAL": "1", "RATCHET_ALPHA_ROLLBACK": "0.01"}[env])

    monkeypatch.delenv("RATCHET_CANARY_AUDIT_RATE", raising=False)
    assert Settings().canary_audit_rate is None and Settings().checked_canary_audit_rate() is None
    monkeypatch.setenv("RATCHET_CANARY_AUDIT_RATE", "0.5")
    assert Settings().checked_canary_audit_rate() == 0.5
    Ratchet(Settings(teacher_model=OPEN_TEACHER), Ledger(":memory:"), FakeTeacher(), lambda v: (BrokenStudent(), ""))
    for value in ("0", "1.5", "-0.1", "nan"):
        monkeypatch.setenv("RATCHET_CANARY_AUDIT_RATE", value)
        with pytest.raises(ValueError, match="RATCHET_CANARY_AUDIT_RATE must be more than 0 and at most 1"):
            Ratchet(Settings(teacher_model=OPEN_TEACHER), Ledger(":memory:"), FakeTeacher(), lambda v: (BrokenStudent(), ""))
    monkeypatch.setenv("RATCHET_CANARY_AUDIT_RATE", "lots")
    with pytest.raises(ValueError, match="RATCHET_CANARY_AUDIT_RATE must be a number"):
        Settings()


@pytest.mark.req("FR-16.3", "FR-6.30", "FR-6.31")
def test_certified_precision_settings_parse(monkeypatch, tmp_path):
    """Certified precision's variables (ADR 0026) parse into the settings and the policy; a bad value stops the gateway,
    the simulator's settings and the nightly job at startup, naming the variable, before any ledger is opened."""
    from ratchet.config import CertPolicy
    from ratchet.gateway import Runtime

    names = ("CALIBRATION", "CERT_TARGET", "CERT_DELTA", "MIN_CERTIFIED_PRECISION", "PAIRED_GATE", "REGRESSION_ALPHA", "MAX_NFR")
    for name in names:
        monkeypatch.delenv(f"RATCHET_{name}", raising=False)
    assert Settings().cert_policy() == CertPolicy("point", 0.95, 0.05)
    p = ReleasePolicy.from_env()
    assert (p.min_certified_precision, p.paired_gate, p.regression_alpha, p.max_nfr) == (0.0, 0, 0.05, 1.0)  # all off
    monkeypatch.setenv("RATCHET_CALIBRATION", " LTT ")
    monkeypatch.setenv("RATCHET_CERT_TARGET", "0.9")
    monkeypatch.setenv("RATCHET_CERT_DELTA", "0.01")
    assert Settings().cert_policy() == CertPolicy("ltt", 0.9, 0.01)
    assert Runtime(Settings(mode="mock", mock_warmup_days=0)).fleet.calibration == "ltt"  # the mock fleet's eval
    monkeypatch.setenv("RATCHET_CALIBRATION", "")  # set but empty: the default
    assert Settings().cert_policy().calibration == "point"
    for env, value, says in (
        ("RATCHET_CALIBRATION", "conformal", "RATCHET_CALIBRATION must be point or ltt"),
        ("RATCHET_CERT_TARGET", "1", "RATCHET_CERT_TARGET must be a number strictly between 0 and 1"),
        ("RATCHET_CERT_TARGET", "nan", "RATCHET_CERT_TARGET must be a number strictly between 0 and 1"),
        ("RATCHET_CERT_DELTA", "0", "RATCHET_CERT_DELTA must be a number strictly between 0 and 1"),
    ):
        monkeypatch.setenv(env, value)
        with pytest.raises(ValueError, match=says):
            Settings().cert_policy()
        with pytest.raises(ValueError, match=says):
            Runtime(Settings(mode="mock", mock_warmup_days=0))
        monkeypatch.setenv("RATCHET_DB", str(tmp_path / "never.db"))
        with pytest.raises(ValueError, match=says):
            live.main(["tick"])
        assert not (tmp_path / "never.db").exists()
        monkeypatch.delenv(env)
    monkeypatch.setenv("RATCHET_CERT_DELTA", "small")
    with pytest.raises(ValueError, match="RATCHET_CERT_DELTA must be a number"):
        Settings()
    monkeypatch.delenv("RATCHET_CERT_DELTA")

    for name, value in (
        ("MIN_CERTIFIED_PRECISION", "0.9"),
        ("PAIRED_GATE", "1"),
        ("REGRESSION_ALPHA", "0.1"),
        ("MAX_NFR", "0.05"),
    ):
        monkeypatch.setenv(f"RATCHET_{name}", value)
    p = ReleasePolicy.from_env()
    assert (p.min_certified_precision, p.paired_gate, p.regression_alpha, p.max_nfr) == (0.9, 1, 0.1, 0.05)
    for env, value, says in (
        ("RATCHET_MIN_CERTIFIED_PRECISION", "1.5", "from 0 to 1"),
        ("RATCHET_PAIRED_GATE", "2", "0 or 1"),
        ("RATCHET_PAIRED_GATE", "yes", "whole number"),
        ("RATCHET_REGRESSION_ALPHA", "1", "strictly between 0 and 1"),
        ("RATCHET_MAX_NFR", "0", "more than 0 and at most 1"),
    ):
        good = os.environ[env]
        monkeypatch.setenv(env, value)
        with pytest.raises(ValueError, match=f"{env}.*{says}"):
            ReleasePolicy.from_env()
        monkeypatch.setenv(env, good)


@pytest.mark.req("FR-5.2", "INV-6", "FR-16.3")
def test_stored_threshold_serves_what_the_eval_measured():
    """Before v0.5 the threshold was stored rounded to 4 decimals, which could round up past the confidence it was
    calibrated at: with every row at confidence 0.4 exp(-0.05) + 0.6 = 0.98049 it stored 0.9805, and a version whose eval
    measured 100% coverage served nothing. It is stored exactly now, so the router serves every row the eval counted."""
    from ratchet.nightschool.evaluate import cascade_eval

    answer = '{"category": "billing", "priority": "high"}'
    rows = [
        {
            "task": "ticket-triage",
            "messages": [{"role": "user", "content": f"ticket {i}"}, {"role": "assistant", "content": answer}],
        }
        for i in range(6)
    ]
    student = RecordingProvider(answer)  # every sample at logprob -0.05, unanimous

    res = cascade_eval(lambda p, k, params=None: student.complete(p, "m", n=k, temperature=0.7, logprobs=True), rows)
    assert res["coverage"] == 1.0 and res["certificate"]["served"] == 3
    assert round(res["threshold"], 4) > res["threshold"]  # the old rounding would have served none of them
    led = Ledger(":memory:")
    _version(led, "live", res)
    router = Ratchet(
        Settings(teacher_model=OPEN_TEACHER, samples=3, audit_rate=0.0),
        led,
        RecordingProvider(answer),
        lambda v: (student, v.adapter_uri),
    )
    assert all(router.handle(r["messages"][:-1], "ticket-triage").served_by == "student" for r in rows)


# ---------- v0.4 hardening: PII redaction, API keys and quotas, streaming, signed BOMs ----------
from collections import Counter  # noqa: E402

from ratchet import bom  # noqa: E402
from ratchet.auth import KeyRing, bearer  # noqa: E402
from ratchet.redact import redact, redact_messages  # noqa: E402

KEY_A = "a" * 32
KEY_B = "b-" + "x" * 30


@pytest.mark.req("FR-4.7", "NFR-4")
def test_redact_finds_each_kind_and_checks_checksums():
    c = Counter()
    text = (
        "Mail jane.doe@acme.co.uk, call +1 (415) 555-0132 or 98765 43210. Card 4111 1111 1111 1111. "
        "IBAN DE89 3704 0044 0532 0130 00. SSN 123-45-6789. PAN ABCPE1234F. Aadhaar 2345 6789 0124. "
        "Host 192.168.1.20. Key sk-abcdefghijklmnopqrstuvwxyz123."
    )
    out = redact(text, c)
    for label in ("EMAIL", "PHONE", "CARD", "IBAN", "SSN", "PAN", "AADHAAR", "IP", "SECRET"):
        assert f"[{label}]" in out, label
    assert "jane" not in out and "4111" not in out and "555" not in out
    assert c["PHONE"] == 2 and sum(c.values()) == 10
    assert redact("Card 4111 1111 1111 1112") == "Card 4111 1111 1111 1112"  # fails Luhn: not a card


@pytest.mark.req("FR-4.7")
def test_redact_leaves_ordinary_text_alone():
    text = (
        "Order #48213 on 2026-10-20 at 10:30 cost $1,299.00. Python 3.10, v0.3.0, page 12 of 300, "
        "1500 requests/day, see https://example.org/a/12345 and ISBN 978-3-16-148410-0."
    )
    assert redact(text) == text


@pytest.mark.req("FR-4.7", "INV-12")
def test_redact_messages_handles_content_parts():
    msgs = [
        {"role": "system", "content": "triage"},
        {"role": "user", "content": [{"type": "text", "text": "I am bob@example.com"}, {"type": "image_url", "image_url": {}}]},
    ]
    out = redact_messages(msgs)
    assert out[1]["content"][0]["text"] == "I am [EMAIL]" and out[1]["content"][1]["type"] == "image_url"
    assert msgs[1]["content"][0]["text"] == "I am bob@example.com"  # the caller's copy is untouched


@pytest.mark.req("FR-4.7", "INV-12")
def test_redact_messages_covers_names_refusals_and_call_arguments():
    """Everything a message can carry from the user to the ledger is redacted, not only its content."""
    call = {"id": "c1", "type": "function", "function": {"name": "mail", "arguments": '{"to": "bob@example.com"}'}}
    msgs = [
        {"role": "user", "content": "hi", "name": "bob@example.com"},
        {"role": "assistant", "content": None, "refusal": "I will not mail bob@example.com", "tool_calls": [call, "junk"]},
        {"role": "assistant", "content": None, "function_call": {"name": "mail", "arguments": '{"to": "+44 20 7946 0958"}'}},
    ]
    snapshot = json.loads(json.dumps(msgs))
    c = Counter()
    out = redact_messages(msgs, c)
    assert out[0]["name"] == "[EMAIL]" and out[1]["refusal"] == "I will not mail [EMAIL]"
    assert out[1]["tool_calls"] == [{**call, "function": {"name": "mail", "arguments": '{"to": "[EMAIL]"}'}}, "junk"]
    assert out[2]["function_call"] == {"name": "mail", "arguments": '{"to": "[PHONE]"}'}
    assert c == Counter(EMAIL=3, PHONE=1) and msgs == snapshot  # the caller's messages are untouched


@pytest.mark.req("FR-4.7")
def test_redaction_is_a_no_op_on_the_mock_world():
    """Mock models look requests up by exact text, so redaction must never change a synthetic request."""
    from sim.mock_models import MockTeacher
    from sim.world import make_stream

    stream, teacher = make_stream("mixed", seed=3), MockTeacher()
    for day in range(1, 9):
        for _ in range(250):
            item = stream.sample(day)
            msgs = item.messages()
            assert redact_messages(msgs) == msgs
            answer = teacher.complete(msgs, "t")[0].text
            assert redact(answer) == answer


class EchoTeacher:
    def __init__(self):
        self.seen = []

    def complete(self, messages, model, n=1, temperature=0.0, logprobs=False, response_format=None, params=None, **_):
        self.seen.append(messages)
        text = '{"category": "account", "priority": "high", "reply": "Mailed you at bob@example.com"}'
        return [Completion(text=text, model=model)]


@pytest.mark.req("FR-4.7", "NFR-4", "INV-12")
def test_examples_are_stored_redacted_but_the_teacher_sees_the_original():
    led, teacher = Ledger(":memory:"), EchoTeacher()
    r = Ratchet(Settings(), led, teacher, lambda v: (None, "x"), seed=1)
    msgs = [{"role": "user", "content": "Locked out. I'm bob@example.com, phone +44 20 7946 0958"}]
    r.handle(msgs, "ticket-triage")
    assert "bob@example.com" in teacher.seen[0][0]["content"]
    ex = led.examples()[0]
    assert "bob@example.com" not in ex["messages"] and "[EMAIL]" in ex["messages"] and "[PHONE]" in ex["messages"]
    assert "[EMAIL]" in ex["completion"] and ex["redactions"] == 3
    assert led.privacy_stats() == {"examples": 1, "redacted_examples": 1, "placeholders": 3}

    raw = Ledger(":memory:")
    Ratchet(Settings(redact_pii=False), raw, EchoTeacher(), lambda v: (None, "x")).handle(msgs, "ticket-triage")
    assert "bob@example.com" in raw.examples()[0]["messages"]


@pytest.mark.req("FR-1.7", "INV-13")
def test_keyring_parses_and_identifies():
    ring = KeyRing(f"acme:{KEY_A}, {KEY_B}")
    assert ring.enabled and ring.names == ["acme", "key2"]
    assert ring.identify(KEY_A) == "acme" and ring.identify(KEY_B) == "key2"
    assert ring.identify("a" * 31) is None and ring.identify(None) is None
    assert not KeyRing("").enabled
    for bad in ("acme:short", f"bad name:{KEY_A}", f"acme:{KEY_A},acme:{KEY_B}"):
        with pytest.raises(ValueError):
            KeyRing(bad)
    assert bearer(f"Bearer {KEY_A}") == KEY_A and bearer("Basic abc", "k") == "k" and bearer(None) is None


@pytest.mark.req("FR-1.7", "NFR-5", "INV-13")
def test_api_keys_protect_v1_and_name_the_client(gw_env):
    from fastapi.testclient import TestClient

    gw = gw_env(RATCHET_API_KEYS=f"acme:{KEY_A},globex:{KEY_B}")
    c = TestClient(gw.app)
    msgs = gw.rt.stream.sample(1).messages()
    assert c.post("/v1/chat/completions", json={"messages": msgs}).status_code == 401
    assert c.post("/v1/chat/completions", json={"messages": msgs}, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert c.get("/v1/models").status_code == 401
    a = {"Authorization": f"Bearer {KEY_A}"}
    assert c.post("/v1/chat/completions", json={"messages": msgs}, headers=a).status_code == 200
    assert c.post("/v1/chat/completions", json={"messages": msgs}, headers={"X-API-Key": KEY_B}).status_code == 200
    stats = c.get("/ratchet/stats").json()
    assert {x["client"] for x in stats["clients"]} == {"acme", "globex"}
    assert stats["security"]["api_keys"] is True
    assert KEY_A not in str(stats) and KEY_B not in str(stats)  # names only, never keys
    assert c.get("/scout/examples").status_code == 200  # pages and Scout stay public


@pytest.mark.req("FR-1.7", "NFR-5")
def test_daily_quota_per_key(gw_env):
    from fastapi.testclient import TestClient

    gw = gw_env(RATCHET_API_KEYS=f"acme:{KEY_A},globex:{KEY_B}", RATCHET_API_KEY_DAILY_REQUESTS="2")
    c = TestClient(gw.app)
    msgs = gw.rt.stream.sample(1).messages()
    a = {"Authorization": f"Bearer {KEY_A}"}
    first = c.post("/v1/chat/completions", json={"messages": msgs}, headers=a)
    assert first.headers["X-RateLimit-Remaining-Requests"] == "1"
    assert c.post("/v1/chat/completions", json={"messages": msgs}, headers=a).status_code == 200
    over = c.post("/v1/chat/completions", json={"messages": msgs}, headers=a)
    assert over.status_code == 429 and over.headers["X-RateLimit-Remaining-Requests"] == "0"
    b = {"Authorization": f"Bearer {KEY_B}"}
    assert c.post("/v1/chat/completions", json={"messages": msgs}, headers=b).status_code == 200


@pytest.mark.req("FR-1.6")
def test_streaming_matches_the_non_streaming_answer(gw_env):
    import json as _json

    from fastapi.testclient import TestClient

    gw = gw_env()
    c = TestClient(gw.app)
    msgs = gw.rt.stream.sample(1).messages()
    plain = c.post("/v1/chat/completions", json={"messages": msgs}).json()
    resp = c.post("/v1/chat/completions", json={"messages": msgs, "stream": True})
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers["X-Ratchet-Served-By"] in ("student", "teacher")
    lines = [ln[6:] for ln in resp.text.splitlines() if ln.startswith("data: ")]
    assert lines[-1] == "[DONE]"
    chunks = [_json.loads(x) for x in lines[:-1]]
    assert all(ch["object"] == "chat.completion.chunk" for ch in chunks)
    assert chunks[0]["choices"][0]["delta"]["role"] == "assistant"
    text = "".join(ch["choices"][0]["delta"].get("content", "") for ch in chunks)
    assert text == plain["choices"][0]["message"]["content"]
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop" and "served_by" in chunks[-1]["ratchet"]
    assert len(chunks) > 3


@pytest.mark.req("FR-3.6", "US-7", "NFR-9", "INV-14")
def test_signed_bom_per_adapter_verifies_and_detects_tampering(gw_env):
    import copy

    from fastapi.testclient import TestClient

    seed, pub = bom.keygen()
    gw = gw_env(RATCHET_BOM_SIGNING_KEY=seed)
    rep = gw.rt.simulate_day(600, "tickets")
    assert rep.trained and rep.version is not None
    c = TestClient(gw.app)
    doc = c.get(f"/ratchet/versions/{rep.version}/bom").json()
    m = doc["manifest"]
    assert m["schema"] == bom.SCHEMA and m["adapter"]["version"] == rep.version
    assert m["training"]["examples"] > 0 and m["training"]["targets"] == "teacher answers only"
    assert all(s["distill_ok"] for s in m["sources"]) and m["golden"]["held_out"]
    assert bom.verify(doc, pub)[0]
    assert c.get("/ratchet/bom/public-key").json()["public_key"] == pub
    assert c.get("/ratchet/stats").json()["versions"][0]["bom"] == "signed"

    forged = copy.deepcopy(doc)
    forged["manifest"]["sources"][0]["license"] = "something else"
    assert not bom.verify(forged, pub)[0]
    forged["sha256"] = hashlib.sha256(bom.canonical(forged["manifest"])).hexdigest()  # re-hash: signature still fails
    assert bom.verify(forged, pub) == (False, "bad signature")
    _, other = bom.keygen()
    assert not bom.verify(doc, other)[0]
    assert c.get("/ratchet/versions/999/bom").status_code == 404


@pytest.mark.req("FR-3.6", "INV-14")
def test_bom_is_stored_unsigned_without_a_key_and_cli_verifies(tmp_path, capsys):
    import json as _json

    from ratchet.nightschool.orchestrate import run_night
    from sim.simulate import build, parser
    from sim.world import make_stream

    settings, led, fleet, ratchet, gate = build(parser().parse_args([]))
    stream = make_stream("tickets", seed=7)
    led.day = 1
    for _ in range(500):
        t = stream.sample(1)
        ratchet.handle(t.messages(), t.task)
    rep = run_night(led, fleet, fleet, gate, settings.student_base_model)
    assert rep.trained and rep.version is not None
    doc = led.version_bom(rep.version)
    assert doc is not None and doc["signature"] is None and bom.verify(doc) == (True, "unsigned; sha256 matches")
    assert doc["manifest"]["training"]["dataset_sha256"] == led.version(rep.version).dataset_hash
    path = tmp_path / "bom.json"
    path.write_text(_json.dumps(doc))
    assert bom.main(["verify", str(path)]) == 0
    assert bom.main(["keygen"]) == 0
    assert "RATCHET_BOM_SIGNING_KEY=" in capsys.readouterr().out


@pytest.mark.req("FR-3.6")
def test_bad_signing_key_fails_at_startup():
    with pytest.raises(ValueError):
        bom.Signer("not base64!!")
    with pytest.raises(ValueError):
        bom.Signer("c2hvcnQ=")


def test_v03_database_gets_v04_columns(tmp_path):
    db = tmp_path / "v03.db"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE requests (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, day INTEGER, task TEXT, shadow_serve INTEGER);
        CREATE TABLE examples (id INTEGER PRIMARY KEY AUTOINCREMENT, hash TEXT UNIQUE, seen INTEGER DEFAULT 1);
        CREATE TABLE versions (id INTEGER PRIMARY KEY AUTOINCREMENT, state TEXT, stage_since INTEGER DEFAULT 0);
    """)
    con.commit()
    con.close()
    led = Ledger(str(db))
    assert "client" in {r["name"] for r in led.db.execute("PRAGMA table_info(requests)")}
    assert "redactions" in {r["name"] for r in led.db.execute("PRAGMA table_info(examples)")}
    assert "bom" in {r["name"] for r in led.db.execute("PRAGMA table_info(versions)")}
    assert led.client_requests_since("acme", 0) == 0


@pytest.mark.req("FR-2.11", "NFR-6")
def test_v04_database_gets_the_route_column(tmp_path):
    """A v0.4 ledger (requests with `client`, no `route`) opens, gains the column, and reports its old rows' route as
    `unrecorded` next to the routes of new ones."""
    db = tmp_path / "v04.db"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, day INTEGER, task TEXT, served_by TEXT, student_version INTEGER,
            confidence REAL, escalated INTEGER, audited INTEGER, audit_agree INTEGER, shadow_version INTEGER,
            shadow_serve INTEGER, shadow_agree INTEGER, latency_ms REAL, cost_usd REAL, baseline_cost_usd REAL, client TEXT
        );
        CREATE TABLE examples (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, day INTEGER, task TEXT, hash TEXT UNIQUE, messages TEXT,
            completion TEXT, teacher_model TEXT, license TEXT, license_url TEXT, split TEXT, seen INTEGER DEFAULT 1,
            redactions INTEGER DEFAULT 0
        );
        CREATE TABLE versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, created_ts REAL, day INTEGER, base_model TEXT, adapter_uri TEXT,
            train_examples INTEGER, dataset_hash TEXT, eval TEXT, state TEXT, traffic_pct INTEGER DEFAULT 0,
            stage_since INTEGER DEFAULT 0, bom TEXT
        );
        CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, day INTEGER, kind TEXT, detail TEXT);
        INSERT INTO requests (ts, day, task, served_by, escalated, cost_usd, baseline_cost_usd, latency_ms)
            VALUES (1800000000, 1, 'ticket-triage', 'teacher', 1, 0.01, 0.01, 900),
                   (1800000001, 1, 'ticket-triage', 'student', 0, 0.001, 0.01, 300);
    """)
    con.commit()
    con.close()
    led = Ledger(str(db))
    assert "route" in {r["name"] for r in led.db.execute("PRAGMA table_info(requests)")}
    r = Ratchet(Settings(teacher_model="nvidia/Nemotron-3-Ultra-550b-a55b"), led, FakeTeacher(), lambda v: (BrokenStudent(), ""))
    assert r.handle([{"role": "user", "content": "new"}], "ticket-triage").route == "cold_start"
    (task,) = led.stats_by_task()
    assert task["requests"] == 3 and task["routes"] == {"unrecorded": 2, "cold_start": 1}


@pytest.mark.req("FR-1.13", "FR-4.1")
def test_v04_database_gets_the_examples_params_column(tmp_path):
    """A v0.4 ledger's examples (no `params`) open unchanged: the column is added, old rows have no conditions, their
    hash and split stay, a repeat of an old request bumps it, and the same request under conditions is a new example."""
    db = tmp_path / "v04.db"
    msgs = [{"role": "user", "content": "Where is my refund?"}]
    old_hash = hashlib.sha256(
        json.dumps({"task": "default", "messages": msgs}, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()  # how v0.4 hashed an example
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE examples (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, day INTEGER, task TEXT, hash TEXT UNIQUE, messages TEXT,
            completion TEXT, teacher_model TEXT, license TEXT, license_url TEXT, split TEXT, seen INTEGER DEFAULT 1,
            redactions INTEGER DEFAULT 0
        );
    """)
    con.execute(
        "INSERT INTO examples (ts, day, task, hash, messages, completion, teacher_model, license, license_url, split)"
        " VALUES (1800000000, 1, 'default', ?, ?, 'On its way.', ?, 'NVIDIA Open Model License', 'u', 'train')",
        (old_hash, json.dumps(msgs), OPEN_TEACHER),
    )
    con.commit()
    con.close()
    led = Ledger(str(db))
    assert "params" in {r["name"] for r in led.db.execute("PRAGMA table_info(examples)")}
    assert led.add_example("default", msgs, "On its way.", OPEN_TEACHER, "l", "u") is False  # the same example
    assert led.add_example("default", msgs, "{}", OPEN_TEACHER, "l", "u", params={"response_format": {"type": "json_object"}})
    old, new = led.examples()
    assert (old["hash"], old["seen"], old["params"]) == (old_hash, 2, None)
    assert new["hash"] != old_hash and json.loads(new["params"]) == {"response_format": {"type": "json_object"}}
    rows = dataset.rows(led, "train") + dataset.rows(led, "golden")
    assert {r["provenance"]["hash"]: r["params"] for r in rows} == {
        old_hash: None,
        new["hash"]: {"response_format": {"type": "json_object"}},
    }


@pytest.mark.req("FR-1.7", "INV-13")
def test_healthz_stays_public_when_keys_are_on(gw_env):
    from fastapi.testclient import TestClient

    gw = gw_env(RATCHET_API_KEYS=f"acme:{KEY_A}")
    c = TestClient(gw.app)
    assert c.get("/healthz").json()["status"] == "ok"
    assert c.get("/v1/models").status_code == 401


def test_redact_preview_endpoint(gw_env):
    from fastapi.testclient import TestClient

    c = TestClient(gw_env().app)
    r = c.post("/ratchet/redact", json={"text": "I'm bob@example.com, card 4111 1111 1111 1111"}).json()
    assert r == {"text": "I'm [EMAIL], card [CARD]", "found": {"EMAIL": 1, "CARD": 1}, "enabled": True}
    assert c.post("/ratchet/redact", json={"text": "x" * 5001}).status_code == 400
    assert c.post("/ratchet/redact", json={}).status_code == 400


@pytest.mark.req("FR-4.6", "NFR-2")
def test_live_night_checks_the_student_endpoint_before_paid_work(tmp_path, monkeypatch):
    """Without RATCHET_STUDENT_BASE, `NebiusTrainer` used to sync the data and run the GPU job before finding it had no
    endpoint to load the adapter into, and `ratchet.live reload` failed on an empty URL. Both now stop first, clearly."""
    monkeypatch.setenv("RATCHET_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("RATCHET_SYNC_CMD", "aws s3 sync ./data s3://bucket/ratchet")
    s = Settings(mode="live", student_base="", student_api_key="")
    run, vllm = RecordingRun(), FakeOpenAIServer()
    trainer = live.NebiusTrainer(s, run=run, http=httpx.Client(transport=vllm.transport))
    rows = [{"task": "default", "messages": [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]}]
    with pytest.raises(RuntimeError, match="RATCHET_STUDENT_BASE is not set"):
        trainer.train(rows, "base-model", "student-v1")
    assert run.calls == [] and vllm.calls == [] and not (tmp_path / "data").exists()  # no sync, no job, nothing written

    led = Ledger(":memory:")
    assert live.reload_adapters(s, led) == []  # nothing to load: not an error
    _version(led, "live")
    with pytest.raises(RuntimeError, match="RATCHET_STUDENT_BASE is not set"):
        live.reload_adapters(s, led, http=httpx.Client(transport=vllm.transport))
    assert vllm.calls == []


# ---------- answer hygiene (v0.5, FR-2.7, FR-2.13, INV-16) ----------
# Before v0.5 a lone `</think>` (the chat template opened the reasoning in the prompt), a code fence and an answer cut
# off by the token limit were all stored as training targets, and the reasoning before a lone `</think>` was served.
TRIAGE_OBJ = '{"category": "billing", "priority": "high", "reply": "We are on it."}'


def _hygiene(teacher, student=None, threshold=None, shadow=None, **settings):
    """A ledger and a cascade with `teacher`. With `student`, a live version at `threshold` serves through it (and a
    shadow version through `shadow`, when given); without, every request is a cold start."""
    led = Ledger(":memory:")
    s = Settings(
        **{
            "teacher_model": OPEN_TEACHER,
            "samples": 3,
            "audit_rate": 0.0,
            "redact_pii": True,
            "teacher_reasoning_tokens": 0,
            **settings,
        }
    )
    models = {}
    if student is not None:
        models[_version(led, "live", {"threshold": threshold})] = student
    if shadow is not None:
        models[_version(led, "shadow", {"threshold": threshold})] = shadow
    return led, Ratchet(s, led, teacher, lambda v: (models[v.id], v.adapter_uri), seed=1)


def _row(led):
    return dict(led.db.execute("SELECT * FROM requests ORDER BY id DESC LIMIT 1").fetchone())


def _skips(led):
    return [e["detail"] for e in led.events() if e["kind"] == "skip_example"]


def _ask(text="I was charged twice this month"):
    return [{"role": "user", "content": text}]


@pytest.mark.req("FR-2.7", "FR-2.13", "INV-2", "INV-16")
@pytest.mark.parametrize(
    "said",
    [
        pytest.param(f"<think>Charged twice: billing, high.</think>\n```json\n{TRIAGE_OBJ}\n```", id="think-block"),
        pytest.param(f"Charged twice, so billing and high.</think>\n\n```json\n{TRIAGE_OBJ}\n```", id="lone-closing-tag"),
    ],
)
def test_teacher_reasoning_and_fences_are_not_stored_or_served(said):
    led, r = _hygiene(RecordingProvider(said))
    out = r.handle(_ask(), "ticket-triage")
    assert out.served_by == "teacher" and out.text == TRIAGE_OBJ and "</think>" not in out.text
    (ex,) = led.examples()
    assert ex["completion"] == TRIAGE_OBJ  # bare JSON: the training target the student should learn to write
    assert _row(led)["stored"] == 1 and _skips(led) == []


@pytest.mark.req("FR-2.7", "INV-10")
def test_served_text_is_clean_on_the_student_and_pinned_paths():
    fenced = f"<think>billing</think>\n```json\n{TRIAGE_OBJ}\n```"
    led, r = _hygiene(RecordingProvider("unused"), student=RecordingProvider(fenced), threshold=0.5)
    out = r.handle(_ask(), "ticket-triage")
    assert (out.served_by, out.text) == ("student", TRIAGE_OBJ)

    plan = '```json\n{"queries": ["LoRA rank", "LoRA alpha"]}\n```'  # a free-text task: the fence is content
    led, r = _hygiene(RecordingProvider(f"Plan the search.</think>\n{plan}"))
    for out in (r.handle(_ask("Question: LoRA?"), "research-plan"), r.teacher_only(_ask("look at this"), "default")):
        assert (out.route, out.text) == ("pinned", plan)
        assert _row(led)["stored"] == 0
    assert led.examples() == [] and _skips(led) == []  # pinned answers are never stored, and that is not a skip


@pytest.mark.req("FR-2.13", "INV-16", "FR-1.8")
def test_truncated_teacher_answer_is_not_stored():
    """A teacher answer cut off by the token limit is served (it is all there is) but never stored, even when the
    visible part parses; the provider reads `finish_reason` from Token Factory's response."""
    server = FakeOpenAIServer(f"<think>billing</think>{TRIAGE_OBJ}", finish_reason="length")
    teacher = OpenAICompatProvider("https://teacher.test/v1", "tf-key", transport=server.transport)
    led, r = _hygiene(teacher)
    out = r.handle(_ask(), "ticket-triage")
    assert (out.served_by, out.route, out.text) == ("teacher", "cold_start", TRIAGE_OBJ)
    out = r.handle(_ask("Where is my order?"), "default")
    assert out.served_by == "teacher" and out.text == TRIAGE_OBJ
    assert led.examples() == []
    assert [row["stored"] for row in led.db.execute("SELECT stored FROM requests")] == [0, 0]
    assert _skips(led) == ["ticket-triage: finish_reason length"]  # the second is the 2nd of that reason: no event
    assert len(server.calls_to("/chat/completions")) == 2


@pytest.mark.req("FR-2.13", "INV-16")
@pytest.mark.parametrize(
    "said",
    [
        pytest.param("", id="empty"),
        pytest.param("  \n\t ", id="whitespace"),
        pytest.param("<think>The customer was charged twice, so", id="never-left-its-reasoning"),
        pytest.param("<think>billing, high</think>", id="reasoning-only"),
    ],
)
def test_empty_teacher_answer_not_stored(said):
    for task in ("ticket-triage", "default"):
        led, r = _hygiene(RecordingProvider(said))
        out = r.handle(_ask(), task)
        assert (out.served_by, out.text) == ("teacher", "")
        assert led.examples() == [] and _row(led)["stored"] == 0
        assert _skips(led) == [f"{task}: empty answer"]


@pytest.mark.req("FR-2.13", "INV-16")
def test_invalid_json_teacher_answer_for_ticket_triage_not_stored():
    for said in ("Category: billing. Priority: high.", '{"category": "billing", "priority": "hi', '["billing", "high"]'):
        led, r = _hygiene(RecordingProvider(said))
        assert r.handle(_ask(), "ticket-triage").text == said  # still served: the client asked
        assert led.examples() == [] and _row(led)["stored"] == 0
        assert _skips(led) == ["ticket-triage: invalid answer"]
        # The same text is a fine answer for a free-text task.
        assert r.handle(_ask(), "default").text == said
        assert [ex["completion"] for ex in led.examples()] == [said] and _row(led)["stored"] == 1


@pytest.mark.req("FR-2.13", "INV-16", "INV-12", "FR-4.7")
def test_redaction_that_breaks_a_structured_answer_is_not_stored():
    """A card-like number outside quotes becomes `[CARD]`, which is not JSON: the redacted answer is never stored."""
    said = '{"category": "billing", "priority": "high", "order": 4111111111111111}'
    led, r = _hygiene(RecordingProvider(said))
    assert r.handle(_ask(), "ticket-triage").text == said
    assert led.examples() == [] and _skips(led) == ["ticket-triage: invalid after redaction"]
    assert _row(led)["stored"] == 0
    raw, r = _hygiene(RecordingProvider(said), redact_pii=False)
    r.handle(_ask(), "ticket-triage")
    assert [ex["completion"] for ex in raw.examples()] == [said]


@pytest.mark.req("FR-2.13", "INV-16", "FR-2.5")
def test_unusable_teacher_answer_is_not_an_audit():
    """An audit whose teacher answer was cut off counts neither for nor against the student: `audited=0`. The teacher
    was still paid, so the cost includes the call."""
    teacher = RecordingProvider(TRIAGE_OBJ, finish_reason="length", in_tokens=50, out_tokens=20)
    led, r = _hygiene(teacher, student=RecordingProvider(TRIAGE_OBJ), threshold=0.5, audit_rate=1.0)
    live = led.live().id
    out = r.handle(_ask(), "ticket-triage")
    assert out.served_by == "student" and len(teacher.calls) == 1
    row = _row(led)
    assert (row["audited"], row["audit_agree"], row["stored"]) == (0, None, 0)
    s = r.s
    assert row["cost_usd"] == pytest.approx(s.price("student").cost(10, 5 * 3) + s.price(OPEN_TEACHER).cost(50, 20))
    assert led.version_live_quality(live) == (0, None) and led.examples() == []
    assert _skips(led) == ["ticket-triage: finish_reason length"]

    teacher.finish_reason = "stop"  # a complete answer is an audit again, and a training example
    r.handle(_ask("again"), "ticket-triage")
    assert (_row(led)["audited"], _row(led)["audit_agree"], _row(led)["stored"]) == (1, 1, 1)
    assert led.version_live_quality(live) == (1, 1.0)


@pytest.mark.req("FR-2.13", "INV-16", "FR-6.3", "FR-2.10")
def test_invalid_shadow_reference_leaves_shadow_agree_null():
    """The shadow disagrees with the live student, and the teacher's arbitration is not valid JSON: nothing settles the
    disagreement, so `shadow_agree` is NULL (counted against the shadow) and nothing is stored. The same holds when the
    teacher's escalation answer was cut off."""
    other = '{"category": "refund", "priority": "low", "reply": "x"}'
    teacher = RecordingProvider("billing, I think")
    led, r = _hygiene(teacher, student=RecordingProvider(TRIAGE_OBJ), threshold=0.5, shadow=RecordingProvider(other))
    cand = led.by_state("shadow").id
    assert r.handle(_ask(), "ticket-triage").served_by == "student" and len(teacher.calls) == 1
    row = _row(led)
    assert (row["shadow_version"], row["shadow_serve"], row["shadow_agree"], row["stored"]) == (cand, 1, None, 0)
    assert led.shadow_quality(cand)["unarbitrated"] == 1 and led.examples() == []

    teacher.text = other  # a valid arbitration settles it: the shadow was right, and the answer is stored
    r.handle(_ask("again"), "ticket-triage")
    assert (_row(led)["shadow_agree"], _row(led)["stored"]) == (1, 1)

    teacher.text, teacher.finish_reason = other, "length"  # an escalation whose answer was cut off
    led.update_eval(led.live().id, {"threshold": 0.99})
    out = r.handle(_ask("third"), "ticket-triage")
    assert out.served_by == "teacher" and out.escalated
    row = _row(led)
    assert (row["shadow_serve"], row["shadow_agree"], row["stored"]) == (1, None, 0)
    assert led.shadow_quality(cand)["unarbitrated"] == 2


@pytest.mark.req("FR-2.13", "INV-16", "FR-6.3", "FR-2.10")
def test_unusable_audit_answer_is_no_shadow_reference():
    """Review finding: a cut-off audit answer left `shadow_agree` NULL, so a shadow that agreed with the served answer
    counted as a disagreement, while the same request without the audit scored 1. Such an audit is now no reference at
    all: the shadow is compared with the served answer, and a disagreement still goes to the teacher, whose cut-off
    ruling settles nothing (NULL)."""
    teacher = RecordingProvider(TRIAGE_OBJ, finish_reason="length")
    student = RecordingProvider(TRIAGE_OBJ)
    led, r = _hygiene(teacher, student=student, threshold=0.5, shadow=RecordingProvider(TRIAGE_OBJ), audit_rate=1.0)
    cand = led.by_state("shadow").id
    assert r.handle(_ask(), "ticket-triage").served_by == "student"
    row = _row(led)
    assert (row["audited"], row["shadow_serve"], row["shadow_agree"], row["stored"]) == (0, 1, 1, 0)
    assert len(teacher.calls) == 1  # the audit only: there was no disagreement to arbitrate
    assert (led.shadow_quality(cand)["precision"], led.shadow_quality(cand)["unarbitrated"]) == (1.0, 0)

    other = '{"category": "refund", "priority": "low", "reply": "x"}'
    teacher = RecordingProvider(TRIAGE_OBJ, finish_reason="length")
    led, r = _hygiene(teacher, student=student, threshold=0.5, shadow=RecordingProvider(other), audit_rate=1.0)
    r.handle(_ask(), "ticket-triage")
    row = _row(led)
    assert (row["audited"], row["shadow_serve"], row["shadow_agree"], row["stored"]) == (0, 1, None, 0)
    assert len(teacher.calls) == 2  # the audit, then the arbitration, cut off too
    assert led.shadow_quality(led.by_state("shadow").id)["unarbitrated"] == 1


@pytest.mark.req("FR-2.7", "FR-2.13", "INV-16")
def test_answers_that_quote_think_tags_are_served_and_stored_whole():
    """Review finding: cutting at any `<think>` or `</think>` served a complete triage reply that mentions the tag as
    broken JSON and stored nothing (`invalid answer`), took the vote from student samples quoting `</think>`, and cut a
    free-text answer short."""
    reply = '{"category": "technical", "priority": "high", "reply": "Your model output shows a stray <think> tag; we fix it."}'
    led, r = _hygiene(RecordingProvider(reply))
    assert r.handle(_ask("My model prints a stray tag"), "ticket-triage").text == reply
    assert [ex["completion"] for ex in led.examples()] == [reply]
    assert _row(led)["stored"] == 1 and _skips(led) == []

    closing = '{"category":"technical","priority":"high","reply":"Strip everything up to the </think> tag before parsing."}'
    led, r = _hygiene(RecordingProvider("unused"), student=RecordingProvider(closing), threshold=0.5)
    out = r.handle(_ask("How do I parse it?"), "ticket-triage")
    assert (out.served_by, out.text, out.confidence > 0.9) == ("student", closing, True)

    prose = "Reasoning models open their chain of thought with a <think> tag. Strip it before showing the answer."
    led, r = _hygiene(RecordingProvider(prose))
    assert r.handle(_ask("What is a think tag?"), "default").text == prose
    assert [ex["completion"] for ex in led.examples()] == [prose]


@pytest.mark.req("FR-2.13", "INV-16", "INV-12")
def test_skip_example_events_are_throttled_per_reason():
    """Like the failure events: the 1st, 10th, 100th and 1000th skip of each reason, counted exactly under threads (Scout
    calls handle from several), naming the task and the reason, never the answer."""
    teacher = RecordingProvider("secret-answer@example.com", finish_reason="length")
    led, r = _hygiene(teacher)

    def worker(k):
        for i in range(25):
            r.handle(_ask(f"thread {k} request {i}"), "default")

    threads = [threading.Thread(target=worker, args=(k,)) for k in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert _skips(led) == ["default: finish_reason length"] * 3  # 200 skips: the 1st, 10th and 100th
    teacher.finish_reason, teacher.text = "stop", ""
    r.handle(_ask(), "ticket-triage")
    teacher.finish_reason = "eos_token"  # a value the router does not name is "other", so reasons stay a short list
    r.handle(_ask(), "default")
    assert _skips(led)[3:] == ["ticket-triage: empty answer", "default: finish_reason other"]
    assert led.examples() == [] and "secret" not in json.dumps(led.events())
    assert led.db.execute("SELECT COUNT(*), SUM(stored) FROM requests").fetchone()[:] == (202, 0)


@pytest.mark.req("FR-2.13", "FR-3.4")
def test_stored_column_marks_requests_that_stored_an_example():
    teacher = RecordingProvider(TRIAGE_OBJ)
    led, r = _hygiene(teacher)
    r.handle(_ask(), "ticket-triage")
    r.handle(_ask(), "ticket-triage")  # a duplicate bumps `seen`: still stored
    assert [row[0] for row in led.db.execute("SELECT stored FROM requests ORDER BY id")] == [1, 1]
    assert [ex["seen"] for ex in led.examples()] == [2]
    r.handle(_ask("plan"), "research-plan")  # pinned: never stored
    assert _row(led)["stored"] == 0

    led, r = _hygiene(teacher, student=RecordingProvider(TRIAGE_OBJ), threshold=0.5)
    r.handle(_ask(), "ticket-triage")  # served by the student, no audit
    assert (_row(led)["served_by"], _row(led)["stored"]) == ("student", 0)
    r.s.audit_rate = 1.0
    r.handle(_ask(), "ticket-triage")  # audited: the teacher's answer is stored
    assert (_row(led)["audited"], _row(led)["stored"]) == (1, 1)


@pytest.mark.req("FR-2.13", "NFR-6")
def test_v05_database_gets_the_stored_column(tmp_path):
    """A ledger from before answer hygiene (requests with `route`, no `stored`) opens and gains the column; its old rows
    keep NULL, which means "not recorded"."""
    db = tmp_path / "v05-pre.db"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, day INTEGER, task TEXT, served_by TEXT, student_version INTEGER,
            confidence REAL, escalated INTEGER, audited INTEGER, audit_agree INTEGER, shadow_version INTEGER,
            shadow_serve INTEGER, shadow_agree INTEGER, latency_ms REAL, cost_usd REAL, baseline_cost_usd REAL,
            client TEXT, route TEXT
        );
        INSERT INTO requests (ts, day, task, served_by, cost_usd, baseline_cost_usd, route)
            VALUES (1800000000, 1, 'ticket-triage', 'teacher', 0.01, 0.01, 'cold_start');
    """)
    con.commit()
    con.close()
    led = Ledger(str(db))
    assert "stored" in {r["name"] for r in led.db.execute("PRAGMA table_info(requests)")}
    Ratchet(Settings(teacher_model=OPEN_TEACHER), led, FakeTeacher(), lambda v: (BrokenStudent(), "")).handle(
        _ask(), "ticket-triage"
    )
    assert [row[0] for row in led.db.execute("SELECT stored FROM requests ORDER BY id")] == [None, 1]


@pytest.mark.req("FR-2.2", "FR-2.7")
def test_answers_nested_too_deeply_are_invalid_not_a_crash():
    """`json.loads` raises RecursionError on very deep nesting; `parse_json` let it out, so a student answering
    `[[[[...` crashed `confidence.score` and the request was a 500 instead of an escalation."""
    deep = "[" * 100_000 + "]" * 100_000
    triage = tasks.get("ticket-triage")
    assert tasks.parse_json(deep) is None and tasks.parse_json("{" + deep + "}") is None
    assert triage.canonical(deep) == tasks.INVALID
    led, r = _hygiene(RecordingProvider(TRIAGE_OBJ), student=RecordingProvider(deep), threshold=0.5)
    out = r.handle(_ask(), "ticket-triage")
    assert (out.served_by, out.route, out.confidence) == ("teacher", "confidence", 0.0)


@pytest.mark.req("FR-2.7", "NFR-1")
def test_answer_cleaning_is_linear_on_adversarial_answers():
    """Each of these took seconds on the request path (about 10 s for 20,000 unclosed `<think>` tags, 14 s for 100,000
    spaces in a structured answer), because a regex rescanned from every position: an answer a client can make a model
    write. Each now takes milliseconds; the bound leaves a wide margin for a slow CI machine."""
    triage = tasks.get("ticket-triage")
    answers = [
        "<think>" * 20_000,
        "a" + " " * 100_000 + "b",
        "{" * 100_000,
        " " * 50_000 + "x```",
        "x</think>" * 20_000 + "<think>" * 20_000,
    ]
    for answer in answers:
        t0 = time.perf_counter()
        assert triage.canonical(answer) == tasks.INVALID and tasks.parse_json(answer) is None
        tasks.get("default").canonical(answer)
        assert time.perf_counter() - t0 < 2.0, answer[:20]
    assert tasks.parse_json('Here it is:\n```json\n{"category": "billing"}\n```\n') == {"category": "billing"}


@pytest.mark.req("FR-2.13", "FR-1.11", "NFR-7")
def test_provider_parses_finish_reason_reasoning_tokens_and_per_choice_tokens():
    """Per choice: the text is `content` only (vLLM's and Token Factory's `reasoning_content` / `reasoning` never reach
    it), `finish_reason`, and output tokens from the logprob counts, so the parts add up to `usage.completion_tokens`;
    reasoning tokens from `completion_tokens_details` are split evenly (no per-choice figure exists)."""
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=bodies.pop(0))

    def lp(n):
        return {"content": [{"token": "t", "logprob": -0.1}] * n}

    p = OpenAICompatProvider("https://student.test/v1", "k", transport=httpx.MockTransport(handler))
    usage = {"prompt_tokens": 12, "completion_tokens": 8, "completion_tokens_details": {"reasoning_tokens": 5}}
    bodies.append(
        {
            "usage": usage,
            "choices": [
                {"message": {"content": "a", "reasoning_content": "SECRET plan"}, "logprobs": lp(3), "finish_reason": "stop"},
                {"message": {"content": None, "reasoning": "SECRET plan"}, "logprobs": lp(5), "finish_reason": "length"},
            ],
        }
    )
    out = p.complete(_ask(), "student-v1", n=2, logprobs=True)
    assert [(c.text, c.finish_reason, c.out_tokens, c.reasoning_tokens, c.in_tokens) for c in out] == [
        ("a", "stop", 3, 3, 12),
        ("", "length", 5, 2, 12),
    ]
    assert "SECRET" not in repr(out)

    # Logprob counts that do not add up to the bill (a server that leaves reasoning out of them) split it in proportion.
    bodies.append(
        {
            "usage": {"completion_tokens": 12},
            "choices": [{"message": {"content": "a"}, "logprobs": lp(3)}, {"message": {"content": "b"}, "logprobs": lp(5)}],
        }
    )
    assert [c.out_tokens for c in p.complete(_ask(), "m", n=2, logprobs=True)] == [5, 7]
    # A choice without logprobs: an even split, the remainder on the first choices. No usage: the counts as they are.
    bodies.append(
        {
            "usage": {"completion_tokens": 9},
            "choices": [{"message": {"content": "a"}, "logprobs": lp(3)}, {"message": {"content": "b"}}],
        }
    )
    assert [c.out_tokens for c in p.complete(_ask(), "m", n=2)] == [5, 4]
    bodies.append(
        {"choices": [{"message": {"content": "a"}, "logprobs": lp(4)}, {"message": {"content": "b"}, "logprobs": lp(2)}]}
    )
    assert [(c.out_tokens, c.reasoning_tokens) for c in p.complete(_ask(), "m", n=2)] == [(4, 0), (2, 0)]
    # A teacher answer (n=1) with no logprobs gets the whole bill and every reasoning token.
    bodies.append(
        {
            "usage": {"completion_tokens": 340, "completion_tokens_details": {"reasoning_tokens": 300}},
            "choices": [{"message": {"content": "a"}, "finish_reason": "stop"}],
        }
    )
    (c,) = p.complete(_ask(), OPEN_TEACHER)
    assert (c.out_tokens, c.reasoning_tokens, c.finish_reason) == (340, 300, "stop")


@pytest.mark.req("FR-2.13", "FR-1.11")
@pytest.mark.parametrize(
    ("value", "want"),
    [
        pytest.param("absent", "stop", id="absent"),
        pytest.param(None, "stop", id="null"),
        pytest.param("", "stop", id="empty"),  # a missing value, not an unknown one, which would store nothing
        pytest.param("  ", "stop", id="blank"),
        pytest.param("stop", "stop", id="stop"),
        pytest.param(" LENGTH ", "length", id="upper-case-padded"),
        pytest.param("tool_calls", "tool_calls", id="tool-calls"),
        pytest.param("eos_token", "eos_token", id="other-word"),
        pytest.param(123, "unknown", id="number"),
        pytest.param("stop sequence hit", "unknown", id="not-a-word"),
        pytest.param("x" * 41, "unknown", id="too-long"),
    ],
)
def test_finish_reason_values_are_read_safely(value, want):
    """Missing, null or empty is `stop` (servers that leave it out); anything that is not a short word is `unknown`, which
    is never stored, and nothing a server sends reaches an event unbounded."""
    choice = {"message": {"content": "a"}}
    if value != "absent":
        choice["finish_reason"] = value
    p = OpenAICompatProvider(
        "https://m.test/v1", "k", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"choices": [choice]}))
    )
    assert p.complete(_ask(), "m")[0].finish_reason == want


@pytest.mark.req("FR-1.14", "FR-1.11")
def test_logprobs_too_large_for_a_float_are_ignored_not_a_crash():
    """Regression (FR-1.14 makes the teacher's logprobs reachable): `json.loads` accepts an integer of hundreds of
    digits, and `math.isfinite` raised OverflowError on it, so such a logprob crashed the parse (a 500 on a teacher
    answer). It is now one malformed value: left out of the confidence's logprobs, and the served logprobs are None."""
    huge = 10**400
    body = {
        "choices": [
            {
                "message": {"content": "ab"},
                "logprobs": {"content": [{"token": "a", "logprob": -0.5}, {"token": "b", "logprob": huge}]},
            }
        ]
    }
    p = OpenAICompatProvider("https://m.test/v1", "k", transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)))
    (c,) = p.complete(_ask(), "m", logprobs=True)
    assert c.token_logprobs == [-0.5] and c.mean_prob == pytest.approx(math.exp(-0.5))
    assert served_logprobs(c, "ab") is None
    body["choices"][0]["logprobs"]["content"][1]["logprob"] = -0.25
    (c,) = p.complete(_ask(), "m", logprobs=True)
    assert [t["logprob"] for t in served_logprobs(c, "ab")] == [-0.5, -0.25]


@pytest.mark.req("FR-1.13", "FR-1.11")
def test_provider_sends_params_and_its_explicit_arguments_win():
    """`OpenAICompatProvider.complete(params=...)` adds the request's fields to the body; the explicit `n`,
    `temperature`, `logprobs` and `response_format` win (logprobs off drops top_logprobs too); `allow` narrows."""
    server = FakeOpenAIServer("ok")
    p = OpenAICompatProvider("https://m.test/v1", "k", transport=server.transport)
    params = GenParams(
        max_tokens=50,
        temperature=0.9,
        stop=("\n\n",),
        logprobs=True,
        top_logprobs=2,
        response_format={"type": "json_object"},
        extra={"top_k": 40},
    )
    schema = {"type": "json_schema", "json_schema": {"name": "x"}}
    p.complete(_ask(), "m", params=params, response_format=schema)
    p.complete(_ask(), "m", n=3, temperature=0.7, logprobs=True, params=params)
    p.complete(_ask(), "m", some_later_argument=1)  # no params: the v0.4 body; an unknown keyword is ignored
    first, second, plain = (c.json for c in server.calls)
    assert first == {
        "model": "m",
        "messages": _ask(),
        "n": 1,
        "temperature": 0.0,
        "max_tokens": 50,
        "stop": ["\n\n"],
        "response_format": schema,
        "top_k": 40,
    }
    assert (second["n"], second["temperature"], second["logprobs"], second["top_logprobs"]) == (3, 0.7, True, 2)
    assert second["response_format"] == {"type": "json_object"}
    assert plain == {"model": "m", "messages": _ask(), "n": 1, "temperature": 0.0}
    narrow = OpenAICompatProvider("https://m.test/v1", "k", transport=server.transport, allow=frozenset({"stop"}))
    narrow.complete(_ask(), "m", params=params)
    assert server.calls[-1].json == {"model": "m", "messages": _ask(), "n": 1, "temperature": 0.0, "stop": ["\n\n"]}


# ---------- generation parameters (v0.5, FR-1.13, ADR 0017) ----------
def _gen_settings(**overrides):
    base = dict(
        teacher_model=OPEN_TEACHER,
        samples=3,
        audit_rate=0.0,
        teacher_params="",
        student_params="",
        teacher_max_tokens=8192,
        student_max_tokens=1024,
        teacher_reasoning_headroom=0,
    )
    return Settings(**{**base, **overrides})


@pytest.mark.req("FR-1.13", "INV-2", "FR-4.1")
@pytest.mark.parametrize(
    ("client", "reason"),
    [
        pytest.param(GenParams(logit_bias={"123": 100}), "client logit_bias", id="logit-bias"),
        pytest.param(GenParams(presence_penalty=2.0), "client penalty", id="presence-penalty"),
        pytest.param(GenParams(frequency_penalty=-0.5), "client penalty", id="negative-frequency-penalty"),
        pytest.param(GenParams(temperature=2.0), "client temperature above 1", id="temperature-2"),
        pytest.param(GenParams(extra={"guided_choice": ["refund"]}), "client vendor field", id="vendor-field"),
        pytest.param(
            GenParams(temperature=2.0, logit_bias={"123": 100}, presence_penalty=2.0), "client logit_bias", id="all-three"
        ),
    ],
)
def test_answers_under_label_altering_sampling_are_served_not_stored(client, reason):
    """The serving teacher gets the client's sampling, so a `logit_bias` of +100, a penalty, a temperature of 2 or a vendor
    field (which may constrain the answer, like vLLM's `guided_choice`) shapes its answer. The client gets that answer;
    it is not stored, because whoever holds an API key could otherwise write the label in the shared training set and
    the golden references (threat model T2). Before, it was stored under teacher provenance with no record of how."""
    teacher = RecordingProvider("Refunds take five days.")
    led = Ledger(":memory:")
    r = Ratchet(_gen_settings(teacher_params="all,guided_choice"), led, teacher, lambda v: (RecordingProvider("x"), "m"))
    out = r.handle(_ask(), "default", params=client)
    assert (out.served_by, out.text) == ("teacher", "Refunds take five days.")
    sent = teacher.calls[0].kwargs["params"]
    assert (sent.logit_bias, sent.extra) == (client.logit_bias, client.extra)  # served as the client asked
    assert led.examples() == [] and led.db.execute("SELECT stored FROM requests").fetchone()[0] == 0
    assert [e["detail"] for e in led.events() if e["kind"] == "skip_example"] == [f"default: {reason}"]

    # The teacher's own sampling stores as before: top_p, a seed, a temperature up to 1, a zero penalty.
    neutral = GenParams(temperature=1.0, top_p=0.5, seed=3, presence_penalty=0.0, frequency_penalty=0.0)
    r.handle(_ask(), "default", params=neutral)
    assert len(led.examples()) == 1

    # An audit drops these fields (temperature 0, providers.CHECK_DROPS), so its answer is stored.
    led = Ledger(":memory:")
    _version(led, "live", {"threshold": 0.5})
    student = RecordingProvider("Refunds take five days.")
    r = Ratchet(
        _gen_settings(audit_rate=1.0), led, RecordingProvider("Refunds take five days."), lambda v: (student, v.adapter_uri)
    )
    assert r.handle(_ask(), "default", params=replace(client, extra={})).served_by == "student"
    assert len(led.examples()) == 1


# ---------- v0.5 evidence foundations (ADR 0024): schema, append-only tables, reserved settings ----------
V040_SCHEMA = """
CREATE TABLE requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, day INTEGER, task TEXT, served_by TEXT, student_version INTEGER,
    confidence REAL, escalated INTEGER, audited INTEGER, audit_agree INTEGER,
    shadow_version INTEGER, shadow_serve INTEGER, shadow_agree INTEGER, latency_ms REAL,
    cost_usd REAL, baseline_cost_usd REAL, client TEXT
);
CREATE TABLE examples (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, day INTEGER, task TEXT, hash TEXT UNIQUE, messages TEXT, completion TEXT,
    teacher_model TEXT, license TEXT, license_url TEXT, split TEXT, seen INTEGER DEFAULT 1, redactions INTEGER DEFAULT 0
);
CREATE TABLE versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_ts REAL, day INTEGER, base_model TEXT, adapter_uri TEXT,
    train_examples INTEGER, dataset_hash TEXT, eval TEXT, state TEXT, traffic_pct INTEGER DEFAULT 0,
    stage_since INTEGER DEFAULT 0, bom TEXT
);
CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, day INTEGER, kind TEXT, detail TEXT);
INSERT INTO requests (ts, day, task, served_by, student_version, escalated, audited, audit_agree, cost_usd,
                      baseline_cost_usd, latency_ms)
    VALUES (1800000000, 1, 'ticket-triage', 'student', 1, 0, 1, 1, 0.001, 0.01, 300);
INSERT INTO examples (ts, day, task, hash, messages, completion, teacher_model, license, license_url, split)
    VALUES (1800000000, 1, 'ticket-triage', 'h1', '[]', '{}', 'nvidia/Nemotron-3-Ultra-550b-a55b', 'OpenMDW-1.1', 'u', 'train');
INSERT INTO versions (created_ts, day, base_model, adapter_uri, train_examples, dataset_hash, eval, state, traffic_pct)
    VALUES (1800000000, 1, 'base', 'student-v1', 1, 'dh', '{}', 'live', 100);
"""  # ratchet/ledger.py's SCHEMA at v0.4.0 (commit 1f9b18c), with one row in each table


def _objects(led: Ledger, kind: str) -> set[str]:
    return {r["name"] for r in led.db.execute("SELECT name FROM sqlite_master WHERE type=?", (kind,))}


def _append_only_refusals(led: Ledger) -> None:
    """Both append-only tables accept new rows and refuse every way of changing or removing one (INV-17)."""
    led.db.execute("INSERT INTO version_leaves VALUES (1, 'train', 'aa', 'bb'), (1, 'golden', 'aa', 'cc')")
    led.db.execute("INSERT INTO receipts VALUES (3, '', 1.0, '{}'), (3, 'acme', 1.0, '{}')")
    for sql in (
        "UPDATE version_leaves SET value='dd'",
        "DELETE FROM version_leaves WHERE split='golden'",
        "INSERT OR REPLACE INTO version_leaves VALUES (1, 'train', 'aa', 'dd')",
        "INSERT OR IGNORE INTO version_leaves VALUES (1, 'train', 'aa', 'dd')",
        "INSERT INTO version_leaves VALUES (1, 'train', 'aa', 'dd')",
        "UPDATE receipts SET doc='{\"edited\": true}' WHERE client='acme'",
        "DELETE FROM receipts",
        "INSERT OR REPLACE INTO receipts VALUES (3, '', 2.0, '{}')",
    ):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            led.db.execute(sql)
    for sql in ("INSERT INTO version_leaves VALUES (NULL, 'train', 'ab', 'cd')", "INSERT INTO receipts VALUES (4, NULL, 1, 'd')"):
        with pytest.raises(sqlite3.IntegrityError):
            led.db.execute(sql)  # a NULL key column would slip past the primary key
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        led.db.execute("INSERT INTO version_leaves VALUES (1, 'test', 'ab', 'cd')")
    leaves = [tuple(r) for r in led.db.execute("SELECT * FROM version_leaves ORDER BY split")]
    receipts = [tuple(r) for r in led.db.execute("SELECT period, client, doc FROM receipts ORDER BY client")]
    assert leaves == [(1, "golden", "aa", "cc"), (1, "train", "aa", "bb")]
    assert receipts == [(3, "", "{}"), (3, "acme", "{}")]


def test_v04_file_gains_the_evidence_schema(tmp_path):
    from ratchet.ledger import APPEND_ONLY_TABLES, EVIDENCE_TABLES, INDEXES, MIGRATIONS

    db = tmp_path / "v04.db"
    con = sqlite3.connect(db)
    con.executescript(V040_SCHEMA)
    con.commit()
    con.close()
    for _ in range(2):  # opening twice changes nothing more
        led = Ledger(str(db))
        for table, cols in MIGRATIONS.items():
            assert set(cols) <= {r["name"] for r in led.db.execute(f"PRAGMA table_info({table})")}, table
        assert set(EVIDENCE_TABLES) <= _objects(led, "table")
        assert {f"{t}_no_{w}" for t in APPEND_ONLY_TABLES for w in ("update", "delete", "replace")} <= _objects(led, "trigger")
        assert {ddl.split()[5] for ddl in INDEXES} <= _objects(led, "index")
        led.db.close()
    led = Ledger(str(db))
    ex = led.examples()[0]
    assert (ex["contested"], ex["checked_epoch"], ex["origin"], ex["job"]) == (0, 0, None, None)  # old rows: defaults
    req = dict(led.db.execute("SELECT * FROM requests").fetchone())
    assert {req[c] for c in ("threshold", "label_p", "student_agree", "baseline_est_usd", "optional_failed")} == {None}
    assert led.version_live_quality(1) == (1, 1.0) and led.live().adapter_uri == "student-v1"  # old data reads as before
    rid = led.log_request(task="t", served_by="student", threshold=0.8, label_p=0.05, optional_failed=1, cost_usd=0)
    row = led.db.execute("SELECT threshold, label_p, optional_failed FROM requests WHERE id=?", (rid,)).fetchone()
    assert tuple(row) == (0.8, 0.05, 1)
    _append_only_refusals(led)


def test_every_index_exists_on_a_new_ledger():
    """A misspelled column would make `_migrate` skip an index everywhere (it skips indexes on columns a hand-made old
    file lacks), so every entry must exist on a fresh ledger."""
    from ratchet.ledger import _INDEX_DDL, INDEXES

    led = Ledger(":memory:")
    names = [_INDEX_DDL.fullmatch(ddl).group(1) for ddl in INDEXES]  # type: ignore[union-attr]
    assert set(names) <= _objects(led, "index") and len(set(names)) == len(INDEXES)
    query = "EXPLAIN QUERY PLAN SELECT COUNT(*) FROM requests WHERE student_version=1 AND audited=1 AND id>0"
    plan = " ".join(r[3] for r in led.db.execute(query))
    assert "requests_version_audited" in plan  # the gate's audit counts use it


@pytest.mark.req("INV-17")
def test_committed_leaves_and_sealed_receipts_are_append_only():
    _append_only_refusals(Ledger(":memory:"))


# A value other than the default for every reserved setting (config.RESERVED_SETTINGS).
RESERVED_ENV = {
    "RATCHET_TEACHER_PROBES": "5",
    "RATCHET_ADJUDICATE_MAX": "7",
    "RATCHET_BATCH_API": "openai",
    "RATCHET_BATCH_DISCOUNT": "0.4",
    "RATCHET_BACKFILL_MAX_TOKENS": "1024",
    "RATCHET_DB_SYNC": "NORMAL",
}


def test_reserved_settings_stop_startup_until_their_feature_lands(monkeypatch, tmp_path):
    """A setting declared for a feature that is not built yet would do nothing, so any value but its default stops the
    cascade, the gateway and the nightly job at startup, naming the variable; its default, spelled any way, is fine."""
    from ratchet.config import KNOWN_ENV, RESERVED_SETTINGS

    assert set(RESERVED_ENV) == {env for env, _, _ in RESERVED_SETTINGS.values()} == set(KNOWN_ENV)
    for env in RESERVED_ENV:
        monkeypatch.delenv(env, raising=False)
    Settings().check_reserved()
    for env, value in {"RATCHET_BATCH_DISCOUNT": "0.50", "RATCHET_BATCH_API": " Operations ", "RATCHET_DB_SYNC": "full"}.items():
        monkeypatch.setenv(env, value)
    Settings().check_reserved()
    for env, value in RESERVED_ENV.items():
        monkeypatch.setenv(env, value)
        with pytest.raises(ValueError, match=env):
            Settings().check_reserved()
        monkeypatch.delenv(env)
    monkeypatch.setenv("RATCHET_BATCH_API", "openai")
    with pytest.raises(ValueError, match="RATCHET_BATCH_API.*clean-room backfill"):
        Ratchet(Settings(), Ledger(":memory:"), FakeTeacher(), lambda v: (BrokenStudent(), ""))
    monkeypatch.setenv("RATCHET_DB", str(tmp_path / "never.db"))
    with pytest.raises(ValueError, match="RATCHET_BATCH_API"):
        live.main(["tick"])
    assert not (tmp_path / "never.db").exists()  # refused before the ledger was opened
    monkeypatch.setenv("RATCHET_BATCH_DISCOUNT", "lots")
    with pytest.raises(ValueError, match="RATCHET_BATCH_DISCOUNT must be a number"):
        Settings()


# A valid value other than the default for every reserved ReleasePolicy field (release.RESERVED_POLICY).
RESERVED_POLICY_ENV = {
    "drift_alarm": "50",
    "drift_audit_boost": "3",
    "drift_early_night": "1",
    "teacher_noise_max": "0.1",
    "alpha_teacher": "0.05",
}


def test_reserved_release_policy_fields_stop_startup(monkeypatch):
    from ratchet.release import RESERVED_POLICY

    assert set(RESERVED_POLICY_ENV) == set(RESERVED_POLICY)
    for name in RESERVED_POLICY:
        monkeypatch.delenv(f"RATCHET_{name.upper()}", raising=False)
    assert ReleasePolicy.from_env() == ReleasePolicy()
    monkeypatch.setenv("RATCHET_ALPHA_TEACHER", "0.01")  # the default, set explicitly
    monkeypatch.setenv("RATCHET_DRIFT_ALARM", "100")
    assert ReleasePolicy.from_env() == ReleasePolicy()
    for name, value in RESERVED_POLICY_ENV.items():
        env = f"RATCHET_{name.upper()}"
        monkeypatch.setenv(env, value)
        with pytest.raises(ValueError, match=f"{env}.*no effect yet"):
            ReleasePolicy.from_env()
        monkeypatch.delenv(env)


@pytest.mark.parametrize(
    "env, value, says",
    [
        ("RATCHET_MIN_AUDITS", "lots", "whole number"),
        ("RATCHET_MIN_SHADOW", "1.5", "whole number"),
        ("RATCHET_MIN_AUDITS", "-1", "0 or more"),
        ("RATCHET_LIVE_FLOOR", "1.0", "strictly between 0 and 1"),
        ("RATCHET_LIVE_FLOOR", "0", "strictly between 0 and 1"),
        ("RATCHET_MIN_PRECISION", "nan", "finite"),
        ("RATCHET_TOLERANCE", "-0.1", "from 0 to 1"),
        ("RATCHET_ALPHA_ROLLBACK", "high", "a number"),
    ],
)
def test_policy_from_env_names_a_bad_value(monkeypatch, env, value, says):
    monkeypatch.setenv(env, value)
    with pytest.raises(ValueError, match=f"{env}.*{says}"):
        ReleasePolicy.from_env()


# ---------- ledger replay (FR-16.5, ADR 0028) ----------
def _replay_cascade(led: Ledger, audit_rate: float = 0.5) -> Ratchet:
    student = RecordingProvider('{"category": "billing", "priority": "high"}')
    return Ratchet(
        Settings(teacher_model=OPEN_TEACHER, samples=3, audit_rate=audit_rate),
        led,
        RecordingProvider('{"category": "billing", "priority": "high"}'),
        lambda v: (student, v.adapter_uri),
        seed=3,
    )


@pytest.mark.req("FR-16.5")
def test_replay_migrates_an_old_file_and_counts_the_rows_it_cannot_replay(tmp_path):
    """A v0.4.0 file migrates on open. Its rows have a confidence but no designed labelling probability, so the replay
    leaves them out and says how many (`before_logging`); rows the gateway writes afterwards are replayed."""
    from ratchet import replay

    db = tmp_path / "v04.db"
    con = sqlite3.connect(db)
    con.executescript(V040_SCHEMA)
    con.executemany(
        "INSERT INTO requests (ts, day, task, served_by, student_version, confidence, escalated, audited, cost_usd,"
        " baseline_cost_usd) VALUES (1800000000, 1, 'ticket-triage', ?, 1, ?, ?, 0, 0.001, 0.01)",
        [("student", 0.95, 0), ("teacher", 0.40, 1), ("teacher", None, 1)],
    )
    con.commit()
    con.close()
    led = Ledger(str(db))
    old = replay.load(led, 1)
    # V040_SCHEMA's own row has no confidence either: two rows without a student answer.
    assert old.rows == () and old.excluded == {"before_logging": 2, "no_student_answer": 2, "pinned_now": 0, "malformed": 0}
    assert replay.at(old.rows)["coverage"] is None and replay.at(old.rows)["savings"] is None  # nothing to replay
    led.update_eval(1, {"threshold": 0.5})
    r = _replay_cascade(led)
    for i in range(6):
        r.handle([{"role": "user", "content": f"ticket {i}"}], "ticket-triage")
    new = replay.load(led, 1)
    assert len(new.rows) == 6 and new.excluded["before_logging"] == 2 and new.n_excluded == 4
    assert all(row.threshold == 0.5 and row.label_p == 0.5 for row in new.rows)
    assert replay.load(led, 1, since_day=2).excluded["before_logging"] == 0  # the old rows are on day 1
    # A row edited outside Ratchet (a probability above 1, a negative cost) is left out, never a NaN or a crash.
    led.log_request(task="ticket-triage", student_version=1, confidence=0.9, threshold=0.5, label_p=1.5, route="student")
    led.log_request(task="ticket-triage", student_version=1, confidence=0.9, threshold=0.5, label_p=0.5, student_cost_usd=-1)
    edited = replay.load(led, 1)
    assert len(edited.rows) == 6 and edited.excluded["malformed"] == 2
    assert json.dumps(replay.frontier_report(edited, 0.5), allow_nan=False)
    led.db.close()


@pytest.mark.req("FR-16.5")
def test_replay_endpoint_refuses_to_mix_versions(gw_env):
    """Confidence scales differ between versions, so a replay reads one: a second `version`, a list or a word is a 400,
    a version that does not exist a 404, and without one the live version is read."""
    from fastapi.testclient import TestClient

    gw = gw_env()
    c = TestClient(gw.app)
    led = gw.rt.ledger
    first = _version(led, "retired", {"threshold": 0.5})
    live = _version(led, "live", {"threshold": 0.5})
    r = _replay_cascade(led)
    for i in range(5):
        r.handle([{"role": "user", "content": f"ticket {i}"}], "ticket-triage")
    # Superscript and circled digits pass str.isdigit() but not int(): a 400 too, never a 500.
    queries = (f"version={first}&version={live}", f"version={first},{live}", "version=all", "version=-1", "version=1.5")
    for query in (*queries, "version=²", "version=①", "version=1²"):
        for path in ("/ratchet/replay", "/ratchet/replay/frontier"):
            resp = c.get(f"{path}?{query}")
            assert resp.status_code == 400, (path, query)
            assert "version" in resp.json()["detail"] and "pool" in resp.json()["detail"], (path, query)
    assert c.get("/ratchet/replay?version=999").status_code == 404
    assert c.get("/ratchet/replay").json()["version"] == live and c.get("/ratchet/replay").json()["n"] == 5
    assert c.get(f"/ratchet/replay?version={first}").json()["n"] == 0


@pytest.mark.req("FR-16.5", "INV-10")
def test_pinned_tasks_never_appear_in_replay_rows():
    """A pinned task never reaches the student, so it has no replay row; and the rows of a task pinned after they were
    logged are left out too (counted as `pinned_now`): the student will never answer it again."""
    from ratchet import replay

    led = Ledger(":memory:")
    vid = _version(led, "live", {"threshold": 0.5})
    r = _replay_cascade(led)
    for i in range(4):
        r.handle([{"role": "user", "content": f"plan {i}"}], "research-plan")  # pinned (built in)
        r.handle([{"role": "user", "content": f"ticket {i}"}], "ticket-triage")
    assert {row.task for row in replay.load(led, vid).rows} == {"ticket-triage"}
    assert len(replay.load(led, vid).rows) == 4
    tasks.register(tasks.Task("ticket-triage", pinned=True, source="file"))  # pinned now, after the traffic
    h = replay.load(led, vid)
    assert h.rows == () and h.excluded["pinned_now"] == 4


@pytest.mark.req("FR-16.5")
def test_replay_reads_a_long_history_in_chunks_up_to_the_id_it_started_at(monkeypatch):
    """A version's history is read in chunks, each under the ledger lock, so the request path's `log_request` (another
    thread) never waits for the whole read; a request logged meanwhile is not part of the replay."""
    import ratchet.ledger as ledger_module

    led = Ledger(":memory:")
    vid = _version(led, "live", {"threshold": 0.5})
    r = _replay_cascade(led)
    for i in range(7):
        r.handle([{"role": "user", "content": f"ticket {i}"}], "ticket-triage")
    whole, excluded = led.replay_rows(vid)
    monkeypatch.setattr(ledger_module, "REPLAY_CHUNK", 2)
    original = Ledger._replay_chunk
    between: list[bool] = []

    def chunk_then_log(self, *args, **kwargs):
        rows = original(self, *args, **kwargs)
        writer = threading.Thread(target=r.handle, args=([{"role": "user", "content": "ticket 0"}], "ticket-triage"))
        writer.start()
        writer.join(timeout=5)
        between.append(not writer.is_alive())  # the other thread logged a request between two chunks
        return rows

    monkeypatch.setattr(Ledger, "_replay_chunk", chunk_then_log)
    chunked, chunked_excluded = led.replay_rows(vid)
    assert chunked == whole and chunked_excluded == excluded and len(chunked) == 7
    assert between == [True] * 4  # 2 + 2 + 2 + 1 rows: four reads, the lock free after each
    assert len(led.replay_rows(vid)[0]) == 11  # the requests logged during the read are there for the next one


@pytest.mark.req("FR-16.5")
def test_replay_endpoints_run_one_at_a_time(gw_env):
    """A replay holds a version's history in memory, so the gateway runs one at a time: another request meanwhile is a
    429 with Retry-After instead of a second copy (or a wait on the threads chat completions share)."""
    from fastapi.testclient import TestClient

    gw = gw_env()
    c = TestClient(gw.app)
    _version(gw.rt.ledger, "live", {"threshold": 0.5})
    with gw.rt.replay_lock:
        for path in ("/ratchet/replay", "/ratchet/replay/frontier"):
            resp = c.get(path)
            assert resp.status_code == 429 and resp.headers["Retry-After"] == "1", path
            assert "replay" in resp.json()["detail"], path
    assert c.get("/ratchet/replay").status_code == 200 and c.get("/ratchet/replay/frontier").status_code == 200


@pytest.mark.req("FR-16.5", "FR-2.15")
def test_replay_leaves_rows_held_by_a_changed_spec_unservable():
    """While a task's spec is changed for a version, the router holds its answers to SERVE_NOTHING whatever the
    threshold, so the replay never serves the rows logged that way; rows logged before the change replay as usual, and
    once a night re-scores the version under the new spec, the held rows are servable again."""
    from ratchet import replay
    from ratchet.confidence import SERVE_NOTHING

    led = Ledger(":memory:")
    vid = _version(led, "live", {"threshold": 0.5})
    r = _replay_cascade(led)
    ask = [{"role": "user", "content": "ticket 0"}]
    for _ in range(3):
        r.handle(ask, "ticket-triage")
    current = tasks.get("ticket-triage").spec_hash
    led.update_eval(vid, {"threshold": 0.5, "spec_hashes": {"ticket-triage": "scored-under-another-spec"}})
    for _ in range(2):
        assert r.handle(ask, "ticket-triage").route == "confidence"
    rows = replay.load(led, vid).rows
    assert [row.threshold for row in rows] == [0.5] * 3 + [SERVE_NOTHING] * 2
    assert [row.spec_changed for row in rows] == [False] * 3 + [True] * 2
    assert replay.at(rows, 0.3)["served"] == 3 and replay.at(rows)["served"] == 3
    led.update_eval(vid, {"threshold": 0.5, "spec_hashes": {"ticket-triage": current}})  # re-scored by a night
    assert replay.at(replay.load(led, vid).rows, 0.3)["served"] == 5


@pytest.mark.req("FR-16.5")
def test_replay_current_threshold_is_the_one_the_router_uses(gw_env, tmp_path, monkeypatch):
    """The replay's current threshold is the one the router holds answers to: the version's own in mock mode, the
    configured default for the hosted fallback student in live mode without a student endpoint (never calibrated)."""
    from fastapi.testclient import TestClient

    gw = gw_env()
    _version(gw.rt.ledger, "live", {"threshold": 0.9})
    assert TestClient(gw.app).get("/ratchet/replay").json()["current_threshold"] == 0.9
    settings = Settings(
        mode="live",
        db_path=str(tmp_path / "live.db"),
        teacher_base="https://tokenfactory.test/v1",
        teacher_api_key="tf-key",
        teacher_model=OPEN_TEACHER,
        student_base="",
        confidence_threshold=0.7,
        admin_keys="",
        api_keys="",
        tasks_file="",
        bom_signing_key="",
        tavily_api_key="",
        mock_warmup_days=0,
    )
    rt = gw.Runtime(settings, transports={"teacher": FakeOpenAIServer("ok").transport})
    monkeypatch.setattr(gw, "rt", rt)
    _version(rt.ledger, "live", {"threshold": 0.9})
    c = TestClient(gw.app)
    assert c.get("/ratchet/replay").json()["current_threshold"] == 0.7
    assert c.get("/ratchet/replay/frontier").json()["current_threshold"] == 0.7


# ---------- savings receipts (FR-15.5, ADR 0029): review fixes ----------
def _two_receipt_days() -> tuple[Ledger, list[dict], dict]:
    """A ledger with two sealed days of served, audited and escalated answers: (ledger, day 2's leaves, the inclusion
    proof of day 2's first request)."""
    led = Ledger(":memory:")
    for day in (1, 2):
        led.day = day
        for i in range(60):
            _request(led, audited=int(i % 3 == 0), baseline_cost_usd=0.0013 if i % 3 == 0 else 0.001)
        _request(led, served_by="teacher", cost_usd=0.0011)
    led.day = 3
    receipts.seal_due(led, receipts.SealConfig(min_audits=5))
    leaves = [receipts.leaf(r) for r in led.period_rows(2)]
    index, path = receipts.proof_for([lf["id"] for lf in leaves], receipts.tree(leaves), leaves[0]["id"])
    return led, leaves, {"leaf": leaves[0], "index": index, "path": path}


@pytest.mark.req("FR-15.5")
@pytest.mark.parametrize(
    "where, section, value",
    [
        ("receipt", "period", "2"),
        ("receipt", "period", 5),
        ("receipt", "period", [1]),
        ("receipt", "period", {"day": True}),
        ("receipt", "merkle", [1]),
        ("previous", "period", "2"),
        ("previous", "period", 5.0),
        ("previous", "merkle", "x"),
    ],
)
def test_receipt_verifiers_never_raise_on_nested_junk(tmp_path, capsys, where, section, value):
    """A receipt (or the previous one) whose `period` is a string, a number or a list made `verify_chain` and
    `verify_proof` raise AttributeError, and `python -m ratchet.receipt verify` print a traceback instead of exiting 1.
    Every verifier now answers FAIL, and the CLI exits 1."""
    led, leaves, proof = _two_receipt_days()
    doc, prev = led.receipt(2), led.receipt(1)
    assert receipts.verify_chain(doc, prev)[0] and receipts.verify_proof(doc, proof)[0]  # intact, both pass
    if where == "receipt":
        doc = json.loads(json.dumps(doc))
        doc["manifest"][section] = value
        assert not receipts.verify_proof(doc, proof)[0]
        assert not receipts.verify_rows(doc, leaves)[0] and receipts.recheck(doc, leaves)
        assert receipts.check_manifest(doc) and not receipts.verify_receipt(doc)[0]
    else:
        prev = json.loads(json.dumps(prev))
        prev["manifest"][section] = value
    if section == "period" or where == "previous":  # a junk merkle section on the receipt leaves its chain intact
        assert not receipts.verify_chain(doc, prev)[0]
    assert not receipts.verify_chain(prev, doc)[0]  # the wrong way round
    paths = {}
    for name, obj in (("r", doc), ("p", prev), ("x", proof)):
        paths[name] = tmp_path / f"{name}.json"
        paths[name].write_text(json.dumps(obj))
    rows = tmp_path / "leaves.jsonl"
    rows.write_text("\n".join(json.dumps(lf) for lf in leaves))
    argv = ["verify", str(paths["r"]), "--prev", str(paths["p"]), "--proof", str(paths["x"]), "--rows", str(rows)]
    assert receipts.main(argv) == 1 and "FAIL" in capsys.readouterr().out


@pytest.mark.req("FR-15.5")
def test_receipt_verifiers_never_raise_on_seeded_nested_mutations():
    """Up to three fields anywhere in a receipt, the previous receipt, a proof or the leaves replaced by junk of another
    type (or removed): every verifier returns a result, none raises (seeded; 72 raises in 2,000 trials before the fix)."""
    led, leaves, proof = _two_receipt_days()
    doc, prev = led.receipt(2), led.receipt(1)
    junk = [None, "2", 5, -1, 2**70, 1.5, True, [], [1], {}, {"day": "x"}]
    rng = random.Random(29)

    def paths(obj, pre=()):
        yield pre
        children = obj.items() if isinstance(obj, dict) else enumerate(obj[:3]) if isinstance(obj, list) else ()
        for k, v in children:
            yield from paths(v, (*pre, k))

    def mutate(obj):
        obj = json.loads(json.dumps(obj))
        where = [p for p in paths(obj) if p]
        for _ in range(rng.randint(1, 3)):
            *parents, last = rng.choice(where)
            cur = obj
            try:
                for k in parents:
                    cur = cur[k]
                if isinstance(cur, dict) and rng.random() < 0.15:
                    del cur[last]
                else:
                    cur[last] = json.loads(json.dumps(rng.choice(junk)))
            except (KeyError, IndexError, TypeError):
                pass
        return obj

    for _ in range(300):
        which = rng.choice(("doc", "prev", "proof", "leaves", "all"))
        d = mutate(doc) if which in ("doc", "all") else doc
        p = mutate(prev) if which in ("prev", "all") else prev
        x = mutate(proof) if which in ("proof", "all") else proof
        lv = mutate(leaves) if which in ("leaves", "all") else leaves
        receipts.verify_receipt(d)
        receipts.verify_chain(d, p)
        receipts.verify_chain(p, d)
        receipts.verify_proof(d, x)
        receipts.verify_rows(d, lv)
        receipts.check_manifest(d)
        receipts.recheck(d, lv)


@pytest.mark.req("FR-15.5")
def test_a_stand_in_that_loses_money_lowers_the_fee():
    """A fallback teacher's answer after the student answered costs the student's samples and the fallback's bill
    against the primary teacher's price on the same tokens: with teachers priced alike, it loses about the student's cost.
    Leaving stand-ins out of the billable set dropped that loss, so the fee could exceed the share of the true billable
    savings. Their losses now count (`stand_in_loss_nusd`); their gains still never do."""
    led = Ledger(":memory:")
    rng = random.Random(7)
    for _ in range(300):
        audited = rng.random() < 0.3
        x = rng.uniform(0.0008, 0.0012)
        _request(led, audited=int(audited), baseline_cost_usd=1.3 * x if audited else x, baseline_est_usd=x, label_p=0.3)
    plain = receipts.compute([receipts.leaf(r) for r in led.period_rows(0)], min_audits=30)
    assert plain["savings"]["stand_in_loss_nusd"] == 0 and plain["billing"]["fee_nusd"] > 0
    # a loss: $0.0002 of student samples, then the fallback teacher's $0.001 against a $0.001 baseline
    _request(led, served_by="teacher", route="fallback_teacher", cost_usd=0.0012, baseline_cost_usd=0.001, label_p=1.0)
    # gains, never billed: a cheaper fallback teacher, and a degraded answer
    _request(led, served_by="teacher", route="fallback_teacher", cost_usd=0.0001, baseline_cost_usd=0.009)
    _request(led, route="degraded", escalated=1, label_p=1.0, baseline_est_usd=0.005, baseline_cost_usd=0.005)
    out = receipts.compute([receipts.leaf(r) for r in led.period_rows(0)], min_audits=30)
    sav, bill = out["savings"], out["billing"]
    assert sav["stand_in_loss_nusd"] == -200_000 and out["totals"]["unverified"] == 3
    assert bill["billable"] == plain["billing"]["billable"]  # still outside the billable set
    assert sav["billable_nusd"] == plain["savings"]["billable_nusd"] - 200_000
    assert sav["net_billable_lo_nusd"] == plain["savings"]["net_billable_lo_nusd"] - 200_000
    assert bill["fee_nusd"] == sav["net_billable_lo_nusd"] // 4 < plain["billing"]["fee_nusd"]
    led.day += 1  # the day is over: its sealed receipt rechecks from the leaves, the loss included
    doc = receipts.seal(led, 0, config=receipts.SealConfig(min_audits=30))
    assert doc["manifest"]["savings"] == sav and doc["manifest"]["billing"] == bill
    assert receipts.recheck(doc, [receipts.leaf(r) for r in led.period_rows(0)]) == []


@pytest.mark.req("FR-15.5")
def test_mock_reasoning_mean_out_of_range_stops_live_startup_too(gw_env, tmp_path, monkeypatch):
    """CONFIGURATION.md says a RATCHET_MOCK_TEACHER_REASONING_MEAN outside (0, 100,000] stops startup; it was checked in
    mock mode only, so a live gateway and `python -m ratchet.live` started with -5 or 1e9 as if it were unset."""
    gw = gw_env()
    settings = Settings(
        mode="live",
        db_path=str(tmp_path / "live.db"),
        teacher_base="https://tokenfactory.test/v1",
        teacher_api_key="tf-key",
        teacher_model=OPEN_TEACHER,
        student_base="",
        admin_keys="",
        api_keys="",
        tasks_file="",
        bom_signing_key="",
        tavily_api_key="",
        mock_warmup_days=0,
        mock_teacher_reasoning_mean=-5.0,
    )
    with pytest.raises(ValueError, match="RATCHET_MOCK_TEACHER_REASONING_MEAN must be more than 0"):
        gw.Runtime(settings, transports={"teacher": FakeOpenAIServer("ok").transport})
    assert not (tmp_path / "live.db").exists()  # refused before the ledger is opened
    for name in list(os.environ):
        if name.startswith("RATCHET_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("RATCHET_DB", str(tmp_path / "live.db"))
    monkeypatch.setenv("RATCHET_MOCK_TEACHER_REASONING_MEAN", "1e9")
    with pytest.raises(ValueError, match="RATCHET_MOCK_TEACHER_REASONING_MEAN must be more than 0"):
        live.main(["seal"])
    assert not (tmp_path / "live.db").exists()
    monkeypatch.setenv("RATCHET_MOCK_TEACHER_REASONING_MEAN", "420")  # in range: accepted, and nothing reads it
    live.main(["seal"])
