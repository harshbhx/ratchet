"""Data governance: storage opt-outs and pseudonymous end users (FR-13.1, FR-13.2, NFR-4, ADR 0080).

- Opt-outs: the body's `store: false`, `X-Ratchet-Store`, `metadata.ratchet_store`, a key's `store=0`
  (RATCHET_KEY_LIMITS), no-store tasks (spec `store: false`, RATCHET_NO_STORE_TASKS), no-train end users and zero
  retention (RATCHET_STORE_EXAMPLES=0). The cascade runs as usual, audits still count, nothing is inserted or bumped,
  and each request records why (`requests.store_opt_out`). Nothing in a request can undo an opt-out.
- End users: `safety_identifier` (else `user`) is kept only as a 16-hex HMAC scoped to the API key, under a key that
  survives restarts; the raw value is in no table. `POST` / `DELETE /v1/ratchet/no-train` manage opt-outs per end user.
- Examples record their first client and end user; a duplicate from another client is in `example_clients`.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from typing import Any

import pytest
from fakes import OPEN_TEACHER, RecordingEvaluator, RecordingProvider, RecordingRun, RecordingTrainer, _version
from fastapi.testclient import TestClient

from ratchet import live, privacy, tasks
from ratchet.auth import KeyLimits, parse_key_limits
from ratchet.config import Settings
from ratchet.ledger import GOVERNANCE_TABLES, INDEXES, Ledger
from ratchet.nightschool.orchestrate import run_night
from ratchet.release import ReleaseGate
from ratchet.router import Ratchet, RequestMeta

CHAT = "/v1/chat/completions"
NO_TRAIN = "/v1/ratchet/no-train"
ACME_KEY = "acme-privacy-key-0123456789abcdef"
GLOBEX_KEY = "globex-privacy-key-0123456789abcd"
ADMIN_KEY = "ops-privacy-admin-key-0123456789ab"
TRIAGE = "ticket-triage"
A = json.dumps({"category": "billing", "priority": "high", "reply": "I am checking the charge now."})
B = json.dumps({"category": "refund", "priority": "low", "reply": "Your refund is on its way."})
HEX16 = re.compile(r"[0-9a-f]{16}")
# What these tests depend on, pinned so the shell's variables cannot move a result.
PINNED_ENV = {
    "RATCHET_API_KEYS": "",
    "RATCHET_ADMIN_KEYS": "",
    "RATCHET_KEY_LIMITS": "",
    "RATCHET_STORE_EXAMPLES": "1",
    "RATCHET_NO_STORE_TASKS": "",
    "RATCHET_END_USER_KEY": "",
    "RATCHET_REDACT_PII": "1",
    "RATCHET_TEACHER_MODEL": OPEN_TEACHER,
    "RATCHET_SAMPLES": "3",
    "RATCHET_AUDIT_RATE": "0.05",
    "RATCHET_CONFIDENCE_THRESHOLD": "0.80",
    "RATCHET_API_KEY_DAILY_REQUESTS": "0",
    "RATCHET_TEACHER_PARAMS": "",
    "RATCHET_STUDENT_PARAMS": "",
    "RATCHET_ON_TEACHER_FAILURE": "error",
    "RATCHET_TEACHER_FALLBACK_MODEL": "",
    "RATCHET_UNKNOWN_TASKS": "distill",
    "RATCHET_TEACHER_MODALITIES": "text",
}


# ---------- helpers ----------
def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "teacher_model": OPEN_TEACHER,
        "samples": 3,
        "audit_rate": 0.0,
        "confidence_threshold": 0.8,
        "redact_pii": True,
        "store_examples": True,
        "no_store_tasks": "",
        "teacher_params": "",
        "student_params": "",
        "on_teacher_failure": "error",
        "teacher_fallback_model": "",
        "unknown_tasks": "distill",
    }
    return Settings(**{**base, **overrides})


def _cascade(
    student: RecordingProvider, teacher: RecordingProvider | None = None, *, threshold: float = 0.5, **settings: Any
) -> tuple[Ledger, Ratchet, RecordingProvider]:
    """A ledger with one live version (calibrated at `threshold`) served by `student`."""
    led = Ledger(":memory:")
    _version(led, "live", {"threshold": threshold})
    teacher = teacher or RecordingProvider(A)
    r = Ratchet(_settings(**settings), led, teacher, lambda v: (student, v.adapter_uri), seed=1)
    return led, r, teacher


def _ask(tag: object = 0) -> list[dict[str, Any]]:
    return [{"role": "user", "content": f"I was charged twice this month, order {tag}"}]


def _rows(led: Ledger) -> list[dict[str, Any]]:
    return [dict(r) for r in led.db.execute("SELECT * FROM requests ORDER BY id")]


def _gateway(gw_env: Any, **env: str) -> Any:
    return gw_env(**{**PINNED_ENV, **env})


def _escalating(gw: Any, student_answer: str = A, threshold: float = 0.99) -> tuple[RecordingProvider, RecordingProvider]:
    """Put a live version in the gateway's ledger whose student is never confident enough, and record both models: every
    request escalates to the teacher (route `confidence`)."""
    _version(gw.rt.ledger, "live", {"threshold": threshold})
    student, teacher = RecordingProvider(student_answer), RecordingProvider(A)
    gw.rt.ratchet.teacher = teacher
    gw.rt.ratchet.resolve_student = lambda v: (student, v.adapter_uri)
    return student, teacher


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _error(r: Any) -> dict[str, Any]:
    return r.json()["error"]


def _dump(led: Ledger) -> list[str]:
    """Every text value in every table of the ledger."""
    out = []
    for (table,) in led.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        for row in led.db.execute(f"SELECT * FROM {table}"):  # noqa: S608 - names come from sqlite_master
            out += [v for v in tuple(row) if isinstance(v, str)]
    return out


# ---------- the cascade (router) ----------
@pytest.mark.req("FR-13.1", "INV-2")
def test_store_false_escalation_calls_the_teacher_and_stores_nothing():
    led, r, teacher = _cascade(RecordingProvider(A), threshold=0.99)
    out = r.handle(_ask(), TRIAGE, meta=RequestMeta(storable=False))
    assert (out.served_by, out.route, out.stored, out.store_opt_out) == ("teacher", "confidence", False, "request")
    assert len(teacher.calls) == 1 and led.examples() == []
    row = _rows(led)[0]
    assert (row["stored"], row["store_opt_out"], row["escalated"]) == (0, "request", 1)
    # The same request without the opt-out is stored; an opted-out duplicate neither inserts nor bumps it.
    assert r.handle(_ask(), TRIAGE).stored is True
    r.handle(_ask(), TRIAGE, meta=RequestMeta(storable=False, store_opt_out="key"))
    assert [e["seen"] for e in led.examples()] == [1]
    assert [(x["stored"], x["store_opt_out"]) for x in _rows(led)] == [(0, "request"), (1, None), (0, "key")]
    assert not [e for e in led.events() if e["kind"] == "skip_example"]  # an opt-out is not a hygiene skip


@pytest.mark.req("FR-13.1", "INV-15")
def test_store_false_still_audits_and_arbitrates_without_storing():
    """Release safety does not depend on storage: an opted-out request is audited and its shadow disagreement is
    arbitrated as usual; neither teacher answer becomes an example."""
    led, r, teacher = _cascade(RecordingProvider(A), audit_rate=1.0)
    out = r.handle(_ask(), TRIAGE, meta=RequestMeta(storable=False))
    assert out.served_by == "student" and len(teacher.calls) == 1
    row = _rows(led)[0]
    assert (row["audited"], row["audit_agree"], row["stored"], row["store_opt_out"]) == (1, 1, 0, "request")
    assert led.examples() == []

    # A shadow that disagrees with the served answer: the teacher arbitrates, the verdict is recorded, nothing is stored.
    led = Ledger(":memory:")
    _version(led, "live", {"threshold": 0.5})
    _version(led, "shadow", {"threshold": 0.5})
    live, shadow, teacher = RecordingProvider(A), RecordingProvider(B), RecordingProvider(A)
    r = Ratchet(_settings(), led, teacher, lambda v: (live if v.state == "live" else shadow, v.adapter_uri), seed=1)
    r.handle(_ask(), TRIAGE, meta=RequestMeta(storable=False))
    row = _rows(led)[0]
    assert (row["shadow_serve"], row["shadow_agree"], row["stored"]) == (1, 0, 0) and len(teacher.calls) == 1
    assert led.examples() == []
    r.handle(_ask(1), TRIAGE)  # the same disagreement on a storable request stores the arbitration
    assert len(led.examples()) == 1 and _rows(led)[1]["stored"] == 1


@pytest.mark.req("FR-13.1", "NFR-4")
def test_zero_retention_stores_nothing_for_any_caller():
    """RATCHET_STORE_EXAMPLES=0: every teacher answer (cold start, escalation, audit) is served and none is stored,
    for the gateway's requests and for internal callers that pass no meta (Scout, the simulator)."""
    led = Ledger(":memory:")
    teacher = RecordingProvider(A)
    r = Ratchet(_settings(store_examples=False, audit_rate=1.0), led, teacher, lambda v: (RecordingProvider(A), v.adapter_uri))
    assert r.handle(_ask(), TRIAGE).route == "cold_start"  # Scout's call shape
    assert r.handle(_ask(1), TRIAGE, meta=RequestMeta(end_user="0" * 16)).store_opt_out == "global"
    _version(led, "live", {"threshold": 0.5})
    out = r.handle(_ask(2), TRIAGE)
    assert out.served_by == "student" and _rows(led)[-1]["audited"] == 1  # audited all the same
    assert led.examples() == [] and len(teacher.calls) == 3
    assert {x["store_opt_out"] for x in _rows(led)} == {"global"} and {x["stored"] for x in _rows(led)} == {0}


@pytest.mark.req("FR-13.1")
def test_zero_retention_mode_night_skips(gw_env):
    led = Ledger(":memory:")
    for i in range(60):  # stored before zero retention was switched on: enough to train
        led.add_example(TRIAGE, _ask(i), A, OPEN_TEACHER, "OpenMDW-1.1", "https://example.org/license")
    trainer, evaluator = RecordingTrainer(), RecordingEvaluator({"golden_agreement": 1.0, "golden_n": 5})
    rep = run_night(led, trainer, evaluator, ReleaseGate(led), "base", min_new_examples=1, store_examples=False)
    assert (rep.trained, rep.version, rep.reason) == (False, None, "storage disabled")
    assert trainer.calls == [] and evaluator.calls == [] and led.versions() == []
    assert led.events()[-1]["kind"] == "skip" and "storage disabled" in led.events()[-1]["detail"]
    assert run_night(led, trainer, evaluator, ReleaseGate(led), "base", min_new_examples=1).trained  # the default trains

    # The demo's simulated day under RATCHET_STORE_EXAMPLES=0: traffic is served, nothing is stored, the night skips.
    gw = _gateway(gw_env, RATCHET_STORE_EXAMPLES="0")
    rep = gw.rt.simulate_day(100, "tickets")
    assert rep.reason == "storage disabled" and gw.rt.ledger.examples() == []
    assert gw.rt.ledger.totals()["requests"] == 100


@pytest.mark.req("FR-13.1")
def test_live_night_skips_under_zero_retention(tmp_path, monkeypatch, capsys):
    """`python -m ratchet.live night` with RATCHET_STORE_EXAMPLES=0 starts no training job, whatever the ledger holds."""
    for name in list(os.environ):
        if name.startswith("RATCHET_") or name in ("NEBIUS_API_KEY", "TAVILY_API_KEY"):
            monkeypatch.delenv(name)
    db = tmp_path / "live.db"
    for name, value in {"RATCHET_DB": str(db), "RATCHET_DATA_DIR": str(tmp_path), "RATCHET_STORE_EXAMPLES": "0"}.items():
        monkeypatch.setenv(name, value)
    led = Ledger(str(db))
    for i in range(60):
        led.add_example(TRIAGE, _ask(i), A, OPEN_TEACHER, "OpenMDW-1.1", "https://example.org/license")
    led.db.close()
    run = RecordingRun()
    live.main(["night"], provider_factory=lambda base, key, **options: RecordingProvider(A), run=run)
    out = capsys.readouterr().out
    assert "storage disabled" in out and run.calls == [] and Ledger(str(db)).versions() == []


@pytest.mark.req("FR-13.1")
def test_no_store_tasks_from_the_setting_and_the_spec():
    led, r, teacher = _cascade(RecordingProvider(A), threshold=0.99, no_store_tasks=" ticket-triage ,not-registered-yet")
    assert r.no_store_tasks == frozenset({TRIAGE, "not-registered-yet"})
    assert r.handle(_ask(), TRIAGE).store_opt_out == "task"
    assert r.handle(_ask(), "default").stored is True  # other tasks are stored as before
    assert [e["task"] for e in led.examples()] == ["default"]
    # A spec's `store: false` is the same opt-out, with the same reason.
    tasks.register(tasks.Task("private-notes", store=False, source="api"))
    assert r.handle(_ask(), "private-notes").store_opt_out == "task" and len(led.examples()) == 1
    # A name that is not a task name (a space-separated list) stops startup.
    for bad in ("ticket-triage default", "a,b c", "x" * 65):
        with pytest.raises(ValueError, match="RATCHET_NO_STORE_TASKS"):
            Ratchet(_settings(no_store_tasks=bad), led, teacher, lambda v: (teacher, ""))


@pytest.mark.req("FR-13.2", "FR-3.3")
def test_examples_record_client():
    """An example records the API client and end user of the request it was first stored from; a duplicate from another
    client is recorded in example_clients, so a client's data can be found later; its own duplicates add nothing."""
    led, r, _ = _cascade(RecordingProvider(A), threshold=0.99)
    r.handle(_ask(), TRIAGE, client="acme", meta=RequestMeta(end_user="a" * 16))
    (ex,) = led.examples()
    assert (ex["client"], ex["end_user"], ex["seen"]) == ("acme", "a" * 16, 1)
    r.handle(_ask(), TRIAGE, client="globex", meta=RequestMeta(end_user="b" * 16))
    r.handle(_ask(), TRIAGE, client="acme")
    r.handle(_ask(), TRIAGE)  # an internal call: no client to record
    r.handle(_ask(), TRIAGE, client="initech", meta=RequestMeta(storable=False))  # opted out: not a contributor
    (ex,) = led.examples()
    assert (ex["client"], ex["end_user"], ex["seen"]) == ("acme", "a" * 16, 4)  # the first sender stays
    assert led.example_clients(ex["hash"]) == ["acme", "globex"]
    assert [tuple(x) for x in led.db.execute("SELECT hash, client FROM example_clients")] == [(ex["hash"], "globex")]

    # An example first stored without a client records the first client that sends it again.
    r.handle(_ask(1), TRIAGE)
    r.handle(_ask(1), TRIAGE, client="acme")
    second = led.examples()[1]
    assert second["client"] is None and led.example_clients(second["hash"]) == ["acme"]
    assert led.example_clients("0" * 64) == []


@pytest.mark.req("FR-13.2")
def test_no_train_end_users_are_never_stored_even_when_registered_mid_request():
    led, r, _ = _cascade(RecordingProvider(A), threshold=0.99)
    led.no_train_add([("c" * 16, None)])
    out = r.handle(_ask(), TRIAGE, meta=RequestMeta(end_user="c" * 16))
    assert (out.served_by, out.stored, out.store_opt_out) == ("teacher", False, "end_user")
    assert r.handle(_ask(), TRIAGE, meta=RequestMeta(end_user="d" * 16)).stored is True  # another end user is stored

    # Registered while the request waits for the teacher: the store-time check still keeps it out.
    led2 = Ledger(":memory:")
    _version(led2, "live", {"threshold": 0.99})

    def answer(messages: Any, model: str) -> str:
        led2.no_train_add([("e" * 16, None)])
        return A

    r2 = Ratchet(_settings(), led2, RecordingProvider(answer), lambda v: (RecordingProvider(A), v.adapter_uri))
    out = r2.handle(_ask(), TRIAGE, meta=RequestMeta(end_user="e" * 16))
    assert out.stored is False and led2.examples() == [] and _rows(led2)[0]["stored"] == 0


@pytest.mark.req("FR-13.1", "INV-10")
def test_pinned_and_teacher_only_requests_record_the_end_user_and_opt_out():
    led, r, _ = _cascade(RecordingProvider(A))
    r.handle(_ask(), "research-plan", client="acme", meta=RequestMeta(storable=False, end_user="f" * 16))
    r.teacher_only(_ask(), TRIAGE, client="acme", meta=RequestMeta(end_user="f" * 16))
    rows = _rows(led)
    assert [(x["route"], x["end_user"], x["store_opt_out"], x["stored"]) for x in rows] == [
        ("pinned", "f" * 16, "request", 0),
        ("pinned", "f" * 16, None, 0),
    ]
    assert led.examples() == []


@pytest.mark.req("FR-13.1")
def test_opt_outs_hold_under_concurrent_requests():
    """Eight threads, a quarter of the requests opted out three different ways: exactly the storable ones are stored,
    and every row records its own opt-out."""
    led, r, _ = _cascade(RecordingProvider(A), threshold=0.99)
    led.no_train_add([("9" * 16, None)])
    metas = [None, RequestMeta(storable=False), RequestMeta(end_user="9" * 16), RequestMeta(storable=False, store_opt_out="key")]
    errors: list[BaseException] = []

    def work(t: int) -> None:
        try:
            for i in range(10):
                r.handle(_ask(f"{t}-{i}"), TRIAGE, meta=metas[i % 4])
        except BaseException as e:  # noqa: BLE001 - reported below
            errors.append(e)

    threads = [threading.Thread(target=work, args=(t,)) for t in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert errors == []
    rows = _rows(led)
    assert len(rows) == 80 and len(led.examples()) == 8 * 3  # i % 4 == 0 for i in 0, 4, 8
    assert sorted({x["store_opt_out"] for x in rows}, key=str) == [None, "end_user", "key", "request"]
    assert all((x["stored"] == 1) == (x["store_opt_out"] is None) for x in rows)


# ---------- the gateway ----------
@pytest.mark.req("FR-13.1")
def test_store_false_over_http_escalation(gw_env):
    gw = _gateway(gw_env)
    student, teacher = _escalating(gw)
    c = TestClient(gw.app)
    msgs = _ask()
    r = c.post(CHAT, json={"messages": msgs, "store": False})
    assert r.status_code == 200
    body = r.json()["ratchet"]
    assert (body["served_by"], body["escalated"], body["stored"], body["store_opt_out"]) == ("teacher", True, False, "request")
    assert len(student.calls) == 1 and len(teacher.calls) == 1 and gw.rt.ledger.examples() == []
    assert _rows(gw.rt.ledger)[0]["stored"] == 0
    r = c.post(CHAT, json={"messages": msgs, "store": True})  # `store: true` (or absent) is the default: stored
    assert (r.json()["ratchet"]["stored"], r.json()["ratchet"]["store_opt_out"]) == (True, None)
    assert len(gw.rt.ledger.examples()) == 1


@pytest.mark.req("FR-13.1")
def test_store_header_metadata_and_key_opt_outs(gw_env):
    gw = _gateway(gw_env, RATCHET_API_KEYS=f"acme:{ACME_KEY},globex:{GLOBEX_KEY}", RATCHET_KEY_LIMITS="acme:store=0")
    assert gw.rt.key_limits == {"acme": KeyLimits(store=False)}
    _escalating(gw)
    c = TestClient(gw.app)
    led = gw.rt.ledger

    def ask(i: int, key: str = GLOBEX_KEY, headers: dict[str, str] | None = None, **body: Any) -> Any:
        return c.post(CHAT, json={"messages": _ask(i), **body}, headers={**_auth(key), **(headers or {})})

    cases = [
        ask(0, headers={"X-Ratchet-Store": "none"}),
        ask(1, headers={"X-Ratchet-Store": "0"}),
        ask(2, headers={"X-Ratchet-Store": " FALSE "}),
        ask(3, metadata={"ratchet_store": "false"}),
        ask(4, metadata={"ratchet_store": "0"}),
        ask(5, key=ACME_KEY),  # the key opts out whatever the request says
        ask(6, key=ACME_KEY, store=True, metadata={"ratchet_store": "true"}, headers={"X-Ratchet-Store": "1"}),
    ]
    assert [x.status_code for x in cases] == [200] * 7
    assert [x.json()["ratchet"]["store_opt_out"] for x in cases] == ["request"] * 5 + ["key", "key"]
    assert led.examples() == []
    # Opting in explicitly changes nothing about the default; any one opt-out wins over the others' "store".
    assert ask(7, headers={"X-Ratchet-Store": "true"}, metadata={"ratchet_store": "1"}).json()["ratchet"]["stored"] is True
    assert ask(8, store=True, headers={"X-Ratchet-Store": "none"}).json()["ratchet"]["store_opt_out"] == "request"
    assert ask(9, store=False, headers={"X-Ratchet-Store": "1"}).json()["ratchet"]["store_opt_out"] == "request"
    assert len(led.examples()) == 1
    n = len(_rows(led))
    # A value that is neither is a 400 before anything is logged.
    r = ask(10, headers={"X-Ratchet-Store": "nope"})
    assert r.status_code == 400 and _error(r)["code"] == "invalid_value" and "X-Ratchet-Store" in _error(r)["message"]
    r = ask(11, metadata={"ratchet_store": "no"})
    assert r.status_code == 400 and _error(r)["param"] == "metadata.ratchet_store"
    assert len(_rows(led)) == n
    stats = c.get("/ratchet/stats").json()
    assert stats["privacy"]["no_store_clients"] == ["acme"]
    assert stats["privacy"]["not_stored_by_reason"] == {"request": 7, "key": 2}


@pytest.mark.req("FR-13.2", "INV-12", "INV-13", "NFR-4")
def test_end_user_is_an_hmac_and_the_raw_value_is_stored_nowhere(gw_env):
    """The raw `user` / `safety_identifier` is in no table, even with redaction off (so no placeholder could hide it):
    requests and examples hold a 16-hex HMAC scoped to the API key; a no-train registration holds the same."""
    gw = _gateway(gw_env, RATCHET_API_KEYS=f"acme:{ACME_KEY},globex:{GLOBEX_KEY}", RATCHET_REDACT_PII="0")
    _escalating(gw)
    c = TestClient(gw.app)
    led, users = gw.rt.ledger, gw.rt.end_users
    raw, sid = "bob@example.com", "sid-ZQX-unique-7731"
    assert c.post(CHAT, json={"messages": _ask(0), "user": raw}, headers=_auth(ACME_KEY)).status_code == 200
    body = {"messages": _ask(1), "user": raw, "safety_identifier": sid}  # safety_identifier wins
    assert c.post(CHAT, json=body, headers=_auth(ACME_KEY)).status_code == 200
    assert c.post(CHAT, json={"messages": _ask(2), "user": raw}, headers=_auth(GLOBEX_KEY)).status_code == 200
    assert c.post(CHAT, json={"messages": _ask(3), "user": ""}, headers=_auth(GLOBEX_KEY)).status_code == 200
    assert c.post(NO_TRAIN, json={"end_user": "carol-raw-id-99"}, headers=_auth(ACME_KEY)).status_code == 200
    long = "q" * 129
    r = c.post(CHAT, json={"messages": _ask(4), "user": long}, headers=_auth(ACME_KEY))
    assert r.status_code == 400 and _error(r)["param"] == "user" and long not in r.text  # never echoed

    rows = _rows(led)
    hashes = [x["end_user"] for x in rows]
    assert hashes == [users.hash(raw, "acme"), users.hash(sid, "acme"), users.hash(raw, "globex"), None]
    assert all(HEX16.fullmatch(h) for h in hashes[:3]) and len(set(hashes[:3])) == 3  # scoped per key
    assert [e["end_user"] for e in led.examples()] == hashes
    for value in _dump(led):
        for secret in (raw, sid, "carol-raw-id-99", long, ACME_KEY, GLOBEX_KEY):
            assert secret not in value
    events = " ".join(e["detail"] for e in led.events())
    assert users.hash("carol-raw-id-99", "acme") not in events  # counts only: /ratchet/stats is public
    stats = c.get("/ratchet/stats").json()
    assert [(x["client"], x["end_users"]) for x in stats["clients"]] == [("acme", 2), ("globex", 1)]
    assert stats["privacy"]["no_train_end_users"] == 1 and stats["privacy"]["end_user_key"] == "ledger"


@pytest.mark.req("FR-13.2")
def test_end_user_hash_survives_reopening_the_file_ledger(tmp_path):
    path = str(tmp_path / "ratchet.db")
    led = Ledger(path)
    first = privacy.end_users(led)
    h = first.hash("bob@example.com", "acme")
    led.db.close()
    led = Ledger(path)
    again = privacy.end_users(led)
    assert again.hash("bob@example.com", "acme") == h and again.source == "ledger"
    assert not [e for e in led.events() if e["kind"] == "end_user_key"]  # same key: nothing to report
    assert privacy.end_users(Ledger(str(tmp_path / "other.db"))).hash("bob@example.com", "acme") != h  # another deployment
    # The deployment's own key (RATCHET_END_USER_KEY) gives the same hashes in any file, and differs from the ledger's.
    key = "k" * 32
    configured = privacy.end_users(led, key)
    assert configured.source == "env" and configured.hash("bob@example.com", "acme") != h
    elsewhere = privacy.end_users(Ledger(":memory:"), key)
    assert elsewhere.hash("bob@example.com", "acme") == configured.hash("bob@example.com", "acme")
    # Switching keys unlinks earlier hashes, and the ledger says so once, with the registrations that stopped matching.
    (event,) = [e for e in led.events() if e["kind"] == "end_user_key"]
    assert "0 no-train registrations" in event["detail"] and key not in event["detail"]
    privacy.end_users(led, key)
    assert len([e for e in led.events() if e["kind"] == "end_user_key"]) == 1
    with pytest.raises(ValueError, match="at least 16"):
        privacy.end_users(led, "short")
    led.meta_set(privacy.KEY_META, "not hex")
    with pytest.raises(ValueError, match="not hex"):
        privacy.end_users(led)


@pytest.mark.req("FR-13.2")
def test_after_no_train_registration_escalations_are_not_stored(gw_env):
    gw = _gateway(gw_env, RATCHET_API_KEYS=f"acme:{ACME_KEY},globex:{GLOBEX_KEY}", RATCHET_ADMIN_KEYS=f"ops:{ADMIN_KEY}")
    _escalating(gw)
    c = TestClient(gw.app)
    led, users = gw.rt.ledger, gw.rt.end_users

    def chat(i: int, user: str, key: str = ACME_KEY) -> dict[str, Any]:
        r = c.post(CHAT, json={"messages": _ask(i), "user": user}, headers=_auth(key))
        assert r.status_code == 200, r.text
        return r.json()["ratchet"]

    r = c.post(NO_TRAIN, json={"end_user": "bob"}, headers=_auth(ACME_KEY))
    assert r.status_code == 200
    assert r.json() == {"object": "ratchet.no_train", "end_users": [{"client": "acme", "created": True}]}
    assert led.is_no_train(users.hash("bob", "acme")) and users.hash("bob", "acme") not in r.text  # no hash oracle
    assert c.post(NO_TRAIN, json={"end_user": "bob"}, headers=_auth(ACME_KEY)).json()["end_users"][0]["created"] is False
    assert (chat(0, "bob")["stored"], chat(1, "bob")["store_opt_out"]) == (False, "end_user")
    assert chat(2, "bob", GLOBEX_KEY)["stored"] is True  # another client's "bob" is another person
    assert chat(3, "alice")["stored"] is True

    # A client manages its own end users only; an admin may name a client, or register for every client.
    r = c.post(NO_TRAIN, json={"end_user": "bob", "client": "globex"}, headers=_auth(ACME_KEY))
    assert r.status_code == 403 and _error(r)["type"] == "permission_error"
    assert c.post(NO_TRAIN, json={"end_user": "bob", "client": "acme"}, headers=_auth(ACME_KEY)).status_code == 200
    r = c.post(NO_TRAIN, json={"end_user": "carol"}, headers=_auth(ADMIN_KEY))
    assert [(x["client"], x["created"]) for x in r.json()["end_users"]] == [("acme", True), ("globex", True), (None, True)]
    r = c.post(NO_TRAIN, json={"end_user": "dave", "client": "globex"}, headers=_auth(ADMIN_KEY))
    assert [x["client"] for x in r.json()["end_users"]] == ["globex"]
    r = c.post(NO_TRAIN, json={"end_user": "dave", "client": "nobody"}, headers=_auth(ADMIN_KEY))
    assert r.status_code == 400 and _error(r)["param"] == "client"
    assert chat(4, "carol", GLOBEX_KEY)["store_opt_out"] == "end_user"
    assert led.no_train_count() == 5

    # Removing: a client cannot remove what an admin registered; an admin can remove anything.
    r = c.request("DELETE", NO_TRAIN, json={"end_user": "carol"}, headers=_auth(ACME_KEY))
    assert r.status_code == 200 and r.json()["end_users"][0]["status"] == "kept"
    r = c.request("DELETE", NO_TRAIN, json={"end_user": "bob"}, headers=_auth(ACME_KEY))
    assert r.json()["end_users"] == [{"client": "acme", "status": "removed"}] and not led.is_no_train(users.hash("bob", "acme"))
    assert chat(5, "bob")["stored"] is True  # storage is back on for bob
    r = c.request("DELETE", NO_TRAIN, json={"end_user": "carol"}, headers=_auth(ADMIN_KEY))
    assert [x["status"] for x in r.json()["end_users"]] == ["removed", "removed", "removed"]
    r = c.request("DELETE", NO_TRAIN, json={"end_user": "nobody-at-all"}, headers=_auth(GLOBEX_KEY))
    assert r.json()["end_users"][0]["status"] == "absent"
    assert led.no_train_count() == 1  # dave, for globex
    # An admin registration over a client's own marks it as the admin's.
    c.post(NO_TRAIN, json={"end_user": "erin"}, headers=_auth(ACME_KEY))
    c.post(NO_TRAIN, json={"end_user": "erin", "client": "acme"}, headers=_auth(ADMIN_KEY))
    r = c.request("DELETE", NO_TRAIN, json={"end_user": "erin"}, headers=_auth(ACME_KEY))
    assert r.json()["end_users"][0]["status"] == "kept"

    details = [e["detail"] for e in led.events() if e["kind"] == "no_train"]
    assert details and all("bob" not in d and "carol" not in d and users.hash("bob", "acme") not in d for d in details)
    assert any("by admin ops" in d for d in details) and any("by acme" in d for d in details)
    assert c.post(NO_TRAIN, json={"end_user": "bob"}).status_code == 401  # keys are on
    assert c.post(NO_TRAIN, json={"end_user": "bob"}, headers=_auth("wrong-key-0123456789abcdef")).status_code == 401


@pytest.mark.req("FR-13.2")
def test_no_train_requests_are_validated(gw_env):
    gw = _gateway(gw_env)
    c = TestClient(gw.app)
    for body, code, param in (
        ({}, "missing_required_parameter", "end_user"),
        ({"end_user": 5}, "invalid_type", "end_user"),
        ({"end_user": ""}, "invalid_value", "end_user"),
        ({"end_user": "x" * 129}, "invalid_value", "end_user"),
        ({"end_user": "bob", "extra": 1}, "unknown_parameter", "extra"),
        ({"end_user": "bob", "client": 3}, "invalid_type", "client"),
    ):
        r = c.post(NO_TRAIN, json=body)
        assert (r.status_code, _error(r)["code"], _error(r)["param"]) == (400, code, param), body
        assert "x" * 129 not in r.text
    assert _error(c.post(NO_TRAIN, json=["bob"]))["code"] == "invalid_type"
    assert _error(c.post(NO_TRAIN, content=b"{bad json"))["code"] == "invalid_json"
    # Without keys, a caller is in the keyless scope and may not name a client.
    assert c.post(NO_TRAIN, json={"end_user": "bob", "client": "acme"}).status_code == 403
    r = c.post(NO_TRAIN, json={"end_user": "bob"})
    assert r.json()["end_users"][0]["client"] is None
    # The shared mock demo holds a bounded number of registrations.
    gw.MOCK_MAX_NO_TRAIN = 2
    assert c.post(NO_TRAIN, json={"end_user": "two"}).status_code == 200
    assert c.post(NO_TRAIN, json={"end_user": "three"}).status_code == 409
    assert gw.rt.ledger.no_train_count() == 2


@pytest.mark.req("FR-13.2", "NFR-5")
def test_removing_a_registration_needs_a_key_in_live_mode(gw_env, tmp_path):
    """Opting out is open wherever chat completions are; opting someone back in is not: without API keys, a live
    gateway removes a registration only for an admin key."""
    live = {"RATCHET_MODE": "live", "RATCHET_DB": str(tmp_path / "live.db"), "RATCHET_STUDENT_BASE": "", "NEBIUS_API_KEY": ""}
    gw = _gateway(gw_env, **live, RATCHET_ADMIN_KEYS=f"ops:{ADMIN_KEY}")
    c = TestClient(gw.app)
    assert c.post(NO_TRAIN, json={"end_user": "bob"}).status_code == 200
    r = c.request("DELETE", NO_TRAIN, json={"end_user": "bob"})
    assert r.status_code == 403 and gw.rt.ledger.no_train_count() == 1
    r = c.request("DELETE", NO_TRAIN, json={"end_user": "bob"}, headers=_auth(ADMIN_KEY))
    assert r.status_code == 200 and [x["status"] for x in r.json()["end_users"]] == ["removed"]


@pytest.mark.req("FR-13.1", "FR-7.7")
def test_metadata_is_echoed_and_the_ratchet_block_says_whether_it_was_stored(gw_env):
    gw = _gateway(gw_env)
    _escalating(gw)
    c = TestClient(gw.app)
    meta = {"ratchet_task": TRIAGE, "order": "A-1"}
    r = c.post(CHAT, json={"messages": _ask(), "metadata": meta})
    assert r.json()["metadata"] == meta and r.json()["ratchet"]["stored"] is True
    assert "metadata" not in c.post(CHAT, json={"messages": _ask(1)}).json()

    def events(body: dict[str, Any]) -> list[dict[str, Any]]:
        r = c.post(CHAT, json=body)
        return [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: {")]

    chunks = events({"messages": _ask(2), "metadata": meta, "stream": True})
    (last,) = [ch for ch in chunks if "ratchet" in ch]
    assert last["metadata"] == meta and all("metadata" not in ch for ch in chunks if "ratchet" not in ch)
    chunks = events({"messages": _ask(3), "metadata": meta, "stream": True, "stream_options": {"include_usage": True}})
    (last,) = [ch for ch in chunks if "ratchet" in ch]
    assert last["choices"] == [] and last["metadata"] == meta and last["usage"]["total_tokens"] > 0
    assert all("metadata" not in ch for ch in events({"messages": _ask(4), "stream": True}))
    assert gw.rt.ledger.db.execute("SELECT COUNT(*) FROM examples WHERE messages LIKE '%A-1%'").fetchone()[0] == 0


@pytest.mark.req("FR-13.1", "FR-13.2", "FR-7.7")
def test_privacy_stats_report_opt_outs_and_clients(gw_env):
    gw = _gateway(
        gw_env,
        RATCHET_API_KEYS=f"acme:{ACME_KEY},globex:{GLOBEX_KEY}",
        RATCHET_NO_STORE_TASKS="invoice-extract",
        RATCHET_KEY_LIMITS="globex:store=0",
    )
    tasks.register(tasks.Task("private-notes", store=False, source="api"))
    c = TestClient(gw.app)
    fresh = c.get("/ratchet/stats").json()["privacy"]
    assert {k: fresh[k] for k in ("not_stored_requests", "not_stored_by_reason", "no_train_end_users")} == {
        "not_stored_requests": 0,
        "not_stored_by_reason": {},
        "no_train_end_users": 0,
    }
    assert (fresh["store_examples"], fresh["no_store_tasks"], fresh["no_store_clients"]) == (
        True,
        ["invoice-extract", "private-notes"],
        ["globex"],
    )
    _escalating(gw)
    for i, user in enumerate(["u1", "u2", "u1"]):
        c.post(CHAT, json={"messages": _ask(i), "user": user}, headers=_auth(ACME_KEY))
    c.post(CHAT, json={"messages": _ask(9), "store": False}, headers=_auth(ACME_KEY))
    c.post(CHAT, json={"messages": _ask(10)}, headers=_auth(GLOBEX_KEY))
    c.post(CHAT, json={"messages": _ask(11)}, headers={**_auth(ACME_KEY), "X-Ratchet-Task": "invoice-extract"})
    stats = c.get("/ratchet/stats").json()
    clients = {x["client"]: x for x in stats["clients"]}
    assert (clients["acme"]["end_users"], clients["acme"]["stored_share"]) == (2, 0.6)  # 3 of 5 stored
    assert (clients["globex"]["end_users"], clients["globex"]["stored_share"]) == (0, 0.0)
    p = stats["privacy"]
    assert p["not_stored_requests"] == 3 and p["not_stored_by_reason"] == {"key": 1, "request": 1, "task": 1}
    gw0 = _gateway(gw_env, RATCHET_STORE_EXAMPLES="0", RATCHET_END_USER_KEY="e" * 20)
    p0 = TestClient(gw0.app).get("/ratchet/stats").json()["privacy"]
    assert (p0["store_examples"], p0["end_user_key"]) == (False, "env")


# ---------- settings and schema ----------
@pytest.mark.req("FR-13.1", "INV-13")
def test_key_limits_parse_and_refuse_typos():
    names = ["acme", "globex"]
    assert parse_key_limits("", names) == {}
    assert parse_key_limits("acme:store=0", names) == {"acme": KeyLimits(store=False)}
    assert parse_key_limits(" acme : store = 1 ; globex:store=0 ;", names) == {
        "acme": KeyLimits(store=True),
        "globex": KeyLimits(store=False),
    }
    assert parse_key_limits("acme:", names) == {"acme": KeyLimits()}
    secret = "sk-0123456789abcdef0123"
    for spec, message in (
        (f"{secret}:store=0", "names no key in RATCHET_API_KEYS"),
        ("acme:daily_usd=5", "unknown field 'daily_usd'"),
        ("acme:store=yes", "store must be 0 or 1"),
        ("acme:store=0;acme:store=1", "appears twice"),
        ("acme:store=0,store=1", "'store' appears twice"),
        ("acme store=0", "must read name:field=value"),
        ("acme:store", "must read field=value"),
    ):
        with pytest.raises(ValueError, match=message) as e:
            parse_key_limits(spec, names)
        assert secret not in str(e.value)  # a key pasted where its name belongs never reaches the logs
    with pytest.raises(ValueError, match="names no key"):
        parse_key_limits("acme:store=0", [])  # keys off: nothing to name


@pytest.mark.req("FR-13.1", "FR-13.2")
def test_storage_settings_refuse_typos(gw_env, monkeypatch):
    for raw, want in (("1", True), ("0", False), ("FALSE", False), (" off ", False), ("yes", True), ("", True)):
        monkeypatch.setenv("RATCHET_STORE_EXAMPLES", raw)
        assert Settings().store_examples is want, raw
    for bad in ("nope", "2", "store"):
        monkeypatch.setenv("RATCHET_STORE_EXAMPLES", bad)
        with pytest.raises(ValueError, match="RATCHET_STORE_EXAMPLES"):
            Settings()
    monkeypatch.setenv("RATCHET_STORE_EXAMPLES", "1")
    with pytest.raises(ValueError, match="RATCHET_STORE_EXAMPLES must be true or false"):
        Ratchet(_settings(store_examples="0"), Ledger(":memory:"), RecordingProvider(A), lambda v: (None, ""))
    with pytest.raises(ValueError, match="RATCHET_END_USER_KEY must be at least 16"):
        _gateway(gw_env, RATCHET_END_USER_KEY="short")
    with pytest.raises(ValueError, match="RATCHET_KEY_LIMITS"):
        _gateway(gw_env, RATCHET_KEY_LIMITS="acme:store=0")  # no API keys: no such name


@pytest.mark.req("FR-13.2")
def test_ledger_from_before_opt_outs_gains_the_columns_and_tables(tmp_path):
    """A v0.5 file from before this change opens, gains the end-user and opt-out columns, the two governance tables
    and the end-user index; old rows read NULL ("not recorded"), and new rows fill them."""
    db = tmp_path / "pre-optout.db"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, day INTEGER, task TEXT, served_by TEXT, client TEXT,
            route TEXT, stored INTEGER, cost_usd REAL, baseline_cost_usd REAL
        );
        CREATE TABLE examples (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, day INTEGER, task TEXT, hash TEXT UNIQUE, messages TEXT,
            completion TEXT, teacher_model TEXT, license TEXT, license_url TEXT, split TEXT, seen INTEGER DEFAULT 1
        );
        INSERT INTO requests (ts, day, task, served_by, client, route, stored, cost_usd, baseline_cost_usd)
            VALUES (1800000000, 1, 'ticket-triage', 'teacher', 'acme', 'cold_start', 1, 0.01, 0.01);
        INSERT INTO examples (ts, day, task, hash, messages, completion, teacher_model, license, license_url, split)
            VALUES (1800000000, 1, 'ticket-triage', 'h1', '[]', '{}', 'm', 'l', 'u', 'train');
    """)
    con.commit()
    con.close()
    for _ in range(2):  # opening twice changes nothing more
        led = Ledger(str(db))
        tables = {r[0] for r in led.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert set(GOVERNANCE_TABLES) <= tables
        assert {"end_user", "store_opt_out"} <= {r["name"] for r in led.db.execute("PRAGMA table_info(requests)")}
        assert {"client", "end_user"} <= {r["name"] for r in led.db.execute("PRAGMA table_info(examples)")}
        indexes = {r[0] for r in led.db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert "requests_end_user_ts" in indexes and any("requests_end_user_ts" in ddl for ddl in INDEXES)
        led.db.close()
    led = Ledger(str(db))
    assert tuple(led.db.execute("SELECT end_user, store_opt_out FROM requests").fetchone()) == (None, None)
    assert (led.examples()[0]["client"], led.examples()[0]["end_user"]) == (None, None)
    assert led.stats_by_client()[0]["end_users"] == 0 and led.stats_by_client()[0]["stored_share"] == 1.0
    plan = " ".join(r[3] for r in led.db.execute("EXPLAIN QUERY PLAN SELECT * FROM requests WHERE end_user='x' AND ts>0"))
    assert "requests_end_user_ts" in plan
