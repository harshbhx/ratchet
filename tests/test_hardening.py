"""Regression tests for issues found while documenting v0.1, the v0.4 hardening (privacy, auth, streaming, BOMs), and
issues found while testing the live entry points offline (v0.5)."""

import hashlib
import json
import sqlite3

import httpx
import pytest
from fakes import FakeOpenAIServer, RecordingRun, _version

from ratchet import live, tasks
from ratchet.config import Settings
from ratchet.ledger import DAY_SECONDS, Ledger
from ratchet.providers import Completion
from ratchet.release import ReleaseGate, ReleasePolicy
from ratchet.router import Ratchet


class FakeTeacher:
    def __init__(self, fail=False):
        self.fail = fail

    def complete(self, messages, model, n=1, temperature=0.0, logprobs=False, response_format=None):
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


@pytest.mark.req("FR-6.7")
def test_live_alert_fires_once_per_version():
    led = Ledger(":memory:")
    gate = ReleaseGate(led, ReleasePolicy(min_audits=5))
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

    def complete(self, messages, model, n=1, temperature=0.0, logprobs=False, response_format=None):
        self.seen.append(messages)
        return [Completion(text='{"category": "account", "reply": "Mailed you at bob@example.com"}', model=model)]


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
