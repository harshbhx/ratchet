"""Retention, erasure and export (FR-13.3 to FR-13.5, FR-4.4, NFR-4, ADR 0081).

- Erasure: by client, end user, hashes or stored text; the text goes, the hash stays as a tombstone that is never stored
  again; the erasure is logged with hashes only; the versions that may hold the data are reported and their signed BOMs
  stay as written (INV-14); RATCHET_ERASURE_SHARED decides an example another client also sent.
- Retention: examples not seen for RATCHET_EXAMPLE_RETENTION_DAYS lose their text (a storable duplicate restores it);
  request rows older than RATCHET_REQUEST_RETENTION_DAYS fold into daily sums every stats reader adds back; a release in
  flight keeps its rows; the live gateway runs it hourly off the request path; `python -m ratchet.live purge`.
- Night School counts new data so that deletions never hide it, and a purged variant still counts toward its request's
  frequency (INV-3).
- Export: NDJSON per table, examples with provenance, no keys and no end-user hashes; `python -m ratchet.admin`.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import random
import sqlite3
import threading
import time
from dataclasses import replace
from typing import Any

import pytest
from fakes import (
    BASE_MODEL,
    FALLBACK_TEACHER,
    OPEN_TEACHER,
    REJECTED_EVAL,
    FakeClock,
    FakeOpenAIServer,
    RecordingEvaluator,
    RecordingProvider,
    RecordingTrainer,
    _examples,
    _version,
)
from fastapi.testclient import TestClient

from ratchet import admin, bom, licenses, live, privacy
from ratchet.config import Retention, Settings
from ratchet.ledger import DAY_SECONDS, EXPORT_KINDS, GOVERNANCE_TABLES, MIGRATIONS, Ledger, example_hash, target_params
from ratchet.nightschool import dataset
from ratchet.nightschool.orchestrate import run_night
from ratchet.release import ReleaseGate, ReleasePolicy
from ratchet.router import Ratchet, RequestMeta

CHAT = "/v1/chat/completions"
ERASE = "/ratchet/admin/privacy/erase"
EXAMPLES = "/v1/ratchet/examples"
ACME_KEY = "acme-erasure-key-0123456789abcdef"
GLOBEX_KEY = "globex-erasure-key-0123456789abcd"
ADMIN_KEY = "ops-erasure-admin-key-0123456789ab"
END_USER_KEY = "end-user-hmac-key-for-erasure-tests-0123"
TRIAGE = "ticket-triage"
ANSWER = json.dumps({"category": "billing", "priority": "high", "reply": "I am checking the charge now."})
KEY = b"k" * 32
NOW = float(int(time.time()))  # retention compares stored timestamps with the wall clock, as in production
# What these tests depend on, pinned so the shell's variables cannot move a result.
PINNED_ENV = {
    "RATCHET_API_KEYS": "",
    "RATCHET_ADMIN_KEYS": "",
    "RATCHET_KEY_LIMITS": "",
    "RATCHET_STORE_EXAMPLES": "1",
    "RATCHET_NO_STORE_TASKS": "",
    "RATCHET_END_USER_KEY": END_USER_KEY,
    "RATCHET_REDACT_PII": "1",
    "RATCHET_TEACHER_MODEL": OPEN_TEACHER,
    "RATCHET_SAMPLES": "3",
    "RATCHET_AUDIT_RATE": "0.05",
    "RATCHET_CONFIDENCE_THRESHOLD": "0.80",
    "RATCHET_API_KEY_DAILY_REQUESTS": "0",
    "RATCHET_UNKNOWN_TASKS": "distill",
    "RATCHET_EXAMPLE_RETENTION_DAYS": "0",
    "RATCHET_REQUEST_RETENTION_DAYS": "0",
    "RATCHET_ERASURE_SHARED": "delete",
}


# ---------- helpers ----------
def _msgs(text: str) -> list[dict[str, Any]]:
    return [{"role": "user", "content": text}]


def _store(
    led: Ledger,
    text: str,
    *,
    client: str | None = None,
    end_user: str | None = None,
    task: str = "default",
    completion: str | None = None,
    params: dict[str, Any] | None = None,
    teacher: str = OPEN_TEACHER,
) -> str:
    """Store a teacher answer to `text` (or bump it) and return the example's hash."""
    lic = licenses.lookup(teacher)
    assert lic is not None
    answer = completion if completion is not None else f"answer to {text}"
    led.add_example(task, _msgs(text), answer, teacher, lic.name, lic.url, params=params, client=client, end_user=end_user)
    return example_hash(task, _msgs(text), target_params(params))


def _row(led: Ledger, h: str) -> sqlite3.Row:
    return led.db.execute("SELECT * FROM examples WHERE hash=?", (h,)).fetchone()


def _dump(led: Ledger) -> list[str]:
    """Every text value in every table of the ledger."""
    out = []
    for (table,) in led.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        for row in led.db.execute(f"SELECT * FROM {table}"):  # noqa: S608 - names come from sqlite_master
            out += [v for v in tuple(row) if isinstance(v, str)]
    return out


def _erase(led: Ledger, **kw: Any) -> dict[str, Any]:
    return privacy.erase(led, **{"actor": "ops", "reason": "test", "dry_run": False, **kw})


def _age(led: Ledger, hashes: list[str], days: float) -> None:
    """Make examples look last seen `days` ago (retention counts from last_seen_ts)."""
    for h in hashes:
        led.db.execute("UPDATE examples SET ts=?, last_seen_ts=? WHERE hash=?", (NOW - days * DAY_SECONDS,) * 2 + (h,))
    led.db.commit()


class _FixedHasher(privacy.EndUsers):
    """An EndUsers whose hash is fixed, for rows stored with a made-up end-user hash."""

    def __init__(self, value: str) -> None:
        super().__init__(KEY, "env")
        self.value = value

    def hash(self, value: str, client: str | None = None) -> str:
        return self.value


def _gateway(gw_env: Any, **env: str) -> Any:
    return gw_env(**{**PINNED_ENV, **env})


def _escalating(gw: Any) -> RecordingProvider:
    """A live version whose student is never confident enough: every request escalates and its answer is stored."""
    _version(gw.rt.ledger, "live", {"threshold": 0.99})
    student, teacher = RecordingProvider(ANSWER), RecordingProvider(ANSWER)
    gw.rt.ratchet.teacher = teacher
    gw.rt.ratchet.resolve_student = lambda v: (student, v.adapter_uri)
    return teacher


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _close(a: Any, b: Any) -> bool:
    """Equal, with floats within a relative 1e-9 (sums folded in another order)."""
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_close(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_close(x, y) for x, y in zip(a, b, strict=True))
    if isinstance(a, float) or isinstance(b, float):
        return a is not None and b is not None and math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)
    return a == b


def _lines(text: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in text.splitlines()]


# ---------- erasure (FR-13.3) ----------
@pytest.mark.req("FR-13.3", "NFR-4")
@pytest.mark.parametrize("shared", ["delete", "keep"])
def test_erase_a_client_purges_its_examples_and_shared_ones_by_policy(shared):
    led = Ledger(":memory:", golden_fraction=0.0)
    acme = _store(led, "acme only", client="acme", end_user="aaaa000011112222")
    globex = _store(led, "globex only", client="globex")
    both = _store(led, "sent by both", client="acme", end_user="aaaa000011112222")
    _store(led, "sent by both", client="globex")
    globex_first = _store(led, "globex first", client="globex")
    _store(led, "globex first", client="acme")

    out = _erase(led, client="acme", shared=shared)

    assert _row(led, acme)["messages"] is None and _row(led, acme)["purge_reason"] == "erasure"
    assert _row(led, globex)["messages"] is not None  # another client's own example is never touched
    if shared == "delete":
        assert (out["matched"], out["purged"], out["kept"]) == (3, 3, 0)
        assert _row(led, both)["messages"] is None and _row(led, globex_first)["messages"] is None
    else:  # kept for globex, with acme unlinked: globex is now the first sender, without acme's end user
        assert (out["matched"], out["purged"], out["kept"]) == (3, 1, 2)
        assert _row(led, both)["messages"] is not None and led.example_clients(both) == ["globex"]
        assert _row(led, both)["end_user"] is None
        assert led.example_clients(globex_first) == ["globex"]
        assert privacy.erase(led, client="acme", actor="ops", reason="again")["matched"] == 0  # nothing of acme's left
    erasure = dict(led.db.execute("SELECT * FROM erasures").fetchone())
    assert (erasure["actor"], erasure["purged"], erasure["kept"]) == ("ops", out["purged"], out["kept"])
    assert json.loads(erasure["scope"]) == {"client": "acme", "shared": shared}
    (event,) = [e for e in led.events() if e["kind"] == "erase"]
    assert event["detail"].startswith(f"erasure {out['id']} (client) by ops: {out['purged']} examples purged")


@pytest.mark.req("FR-13.3", "NFR-4", "INV-24")
def test_erasure_records_hold_hashes_and_no_text():
    led = Ledger(":memory:")
    hasher = privacy.EndUsers(KEY, "env")
    user = hasher.hash("jane.roe.4471", "acme")
    h = _store(led, "Jane Roe says order 991 arrived broken", client="acme", end_user=user)
    other = _store(led, "Someone else's order 992", client="acme", end_user=hasher.hash("other-user", "acme"))
    led.log_request(task="default", client="acme", end_user=user, served_by="teacher", cost_usd=0.01, baseline_cost_usd=0.01)
    led.log_request(task="default", client="acme", end_user="ffff000011112222", served_by="teacher", cost_usd=0.01)

    out = _erase(
        led,
        client="acme",
        end_user_raw="jane.roe.4471",
        contains="Jane Roe",
        reason="request from jane.roe@example.org",
        hasher=hasher,
    )

    assert (out["purged"], out["requests_unlinked"]) == (1, 1)
    assert out["scope"] == {
        "client": "acme",
        "end_users": 1,
        "contains_sha256": hashlib.sha256(b"Jane Roe").hexdigest(),
        "shared": "delete",
    }
    assert _row(led, other)["messages"] is not None
    text = "\n".join(_dump(led))
    for secret in ("Jane Roe", "jane.roe.4471", "arrived broken", "jane.roe@example.org"):
        assert secret not in text
    erasure = dict(led.db.execute("SELECT * FROM erasures").fetchone())
    assert erasure["reason"] == "request from [EMAIL]"  # redacted like stored text
    assert json.loads(erasure["scope"])["end_users"] == [user]  # the HMAC, never the value
    assert [tuple(r) for r in led.db.execute("SELECT * FROM erasure_items")] == [(out["id"], h, "default", _row(led, h)["split"])]
    assert [tuple(r)[:2] for r in led.db.execute("SELECT key, erasure_id FROM tombstones")] == [(h, out["id"])]
    requests = [tuple(r) for r in led.db.execute("SELECT end_user FROM requests ORDER BY id")]
    assert requests == [(None,), ("ffff000011112222",)]  # only the erased end user is unlinked
    details = " ".join(e["detail"] for e in led.events())
    assert user not in details and h not in details and "jane" not in details.lower()


@pytest.mark.req("FR-13.3")
def test_dry_run_reports_and_writes_nothing():
    led = Ledger(":memory:", golden_fraction=0.0)
    h = _store(led, "dry run me", client="acme")
    _version(led, "live")
    before = (_dump(led), led.events(), led.db.execute("SELECT COUNT(*) FROM erasures").fetchone()[0])
    out = privacy.erase(led, client="acme", actor="ops", reason="preview")
    assert (out["dry_run"], out["id"], out["matched"], out["purged"]) == (True, None, 1, 1)
    assert [v["id"] for v in out["affected_versions"]] == [1]  # created after the example was stored
    assert (_dump(led), led.events(), led.db.execute("SELECT COUNT(*) FROM erasures").fetchone()[0]) == before
    assert _row(led, h)["messages"] is not None


@pytest.mark.req("FR-13.3", "INV-14", "INV-24")
def test_affected_versions_are_reported_and_their_signed_boms_stay_unchanged():
    led = Ledger(":memory:", golden_fraction=0.3)
    early = _examples(led, train=60, golden=10, prefix="early")
    seed, public_key = bom.keygen()
    signer = bom.Signer(seed)
    trainer, evaluator, gate = RecordingTrainer(), RecordingEvaluator(REJECTED_EVAL), ReleaseGate(led)
    assert run_night(led, trainer, evaluator, gate, BASE_MODEL, signer=signer).trained  # v1, rejected: nothing in flight
    later = _examples(led, train=60, prefix="later")
    assert run_night(led, trainer, evaluator, gate, BASE_MODEL, signer=signer).trained  # v2
    boms = {v.id: led.version_bom(v.id) for v in led.versions()}
    early_train = next(e for e in early if e.split == "train")
    early_golden = [e for e in early if e.split == "golden"]

    out = _erase(led, hashes=[early_train.hash, early_golden[0].hash, later[0].hash])

    assert [(v["id"], v["state"], v["adapter_uri"], v["examples"]) for v in out["affected_versions"]] == [
        (1, "rejected", "student-v1", 1),  # trained on the early train row only
        (2, "rejected", "student-v2", 2),  # on both train rows; the golden row was held out of both
    ]
    erasure = dict(led.db.execute("SELECT tainted, status FROM erasures").fetchone())
    assert (json.loads(erasure["tainted"]), erasure["status"]) == ([1, 2], "pending")
    for vid, doc in boms.items():  # INV-14: reported, never edited
        assert led.version_bom(vid) == doc and bom.verify(doc, public_key)[0]
    golden_only = _erase(led, hashes=[early_golden[1].hash])
    assert golden_only["affected_versions"] == []
    assert led.db.execute("SELECT status FROM erasures WHERE id=?", (golden_only["id"],)).fetchone()[0] == "complete"


@pytest.mark.req("FR-13.3")
def test_affected_versions_use_committed_training_sets_when_a_version_has_them():
    led = Ledger(":memory:", golden_fraction=0.0)
    a, b = _store(led, "first row"), _store(led, "second row")
    _version(led, "retired")  # created after both rows: by time, it may hold both
    v2 = _version(led, "live")
    led.db.execute("INSERT INTO version_leaves VALUES (?, 'train', ?, 'ff')", (v2, a))  # v2 committed to `a` only
    led.db.commit()
    rows = [(h, _row(led, h)["ts"], True) for h in (a, b)]
    assert [(v["id"], v["examples"]) for v in led.affected_versions(rows)] == [(1, 2), (v2, 1)]
    assert led.affected_versions([(b, _row(led, b)["ts"], True)]) == [
        {"id": 1, "state": "retired", "adapter_uri": "student-v1", "examples": 1}
    ]
    assert led.affected_versions([(a, _row(led, a)["ts"], False)])[0]["id"] == v2  # leaves win over the time rule


@pytest.mark.req("FR-13.3", "FR-3.5")
def test_bom_fingerprint_covers_kept_examples_and_counts_purged_ones(gw_env):
    gw = _gateway(gw_env)
    led = gw.rt.ledger
    hashes = [_store(led, f"bom example {i}") for i in range(3)]
    c = TestClient(gw.app)
    before = c.get("/ratchet/bom").json()
    assert before["purged_examples"] == 0
    _erase(led, hashes=[hashes[0]])
    after = c.get("/ratchet/bom").json()
    assert after["examples_sha256"] != before["examples_sha256"] and after["purged_examples"] == 1
    assert after["examples_sha256"] == hashlib.sha256("".join(sorted(hashes[1:])).encode()).hexdigest()
    assert sum(s["n"] for s in after["sources"]) == 2
    privacy_block = c.get("/ratchet/stats").json()["privacy"]
    assert (privacy_block["examples"], privacy_block["purged_examples"], privacy_block["erasures"]) == (2, 1, 1)
    assert privacy_block["purged_by_reason"] == {"retention": 0, "erasure": 1}
    assert (privacy_block["example_retention_days"], privacy_block["erasure_shared"]) == (0, "delete")


@pytest.mark.req("FR-13.3", "FR-13.4", "INV-24")
def test_an_erased_hash_is_never_stored_again_and_a_retention_purged_one_is_restored():
    led = Ledger(":memory:", golden_fraction=0.0)
    erased = _store(led, "erase me", client="acme")
    expired = _store(led, "expire me", client="acme")
    _erase(led, hashes=[erased])
    assert led.purge_examples({expired: True}, "retention") == 1
    lic, fallback = licenses.lookup(OPEN_TEACHER), licenses.lookup(FALLBACK_TEACHER)
    assert lic is not None and fallback is not None

    # Erased: refused from any client; nothing stored or bumped, only the tombstone counts the refusal.
    assert led.add_example("default", _msgs("erase me"), "again", OPEN_TEACHER, lic.name, lic.url, client="globex") is None
    row = _row(led, erased)
    assert (row["messages"], row["seen"], row["purge_reason"]) == (None, 1, "erasure")
    assert led.example_clients(erased) == ["acme"]
    assert led.db.execute("SELECT suppressed FROM tombstones WHERE key=?", (erased,)).fetchone()[0] == 1

    # Retention-purged: a storable duplicate brings the text back, with its own teacher and license.
    assert dataset.rows(led, "train") == []
    added = led.add_example(
        "default", _msgs("expire me"), "fresh", FALLBACK_TEACHER, fallback.name, fallback.url, client="globex"
    )
    assert added is False
    row = _row(led, expired)
    assert json.loads(row["messages"]) == _msgs("expire me") and row["completion"] == "fresh"
    assert (row["teacher_model"], row["license"], row["seen"]) == (FALLBACK_TEACHER, fallback.name, 2)
    assert (row["purged_ts"], row["purge_reason"], row["purged_train"]) == (None, None, None)
    assert led.example_clients(expired) == ["acme", "globex"]
    assert [r["provenance"]["hash"] for r in dataset.rows(led, "train")] == [expired]
    assert led.retention_stats()["erasure_refusals"] == 1


@pytest.mark.req("FR-13.3", "INV-16", "INV-24")
def test_the_cascade_skips_a_tombstoned_answer_as_erased():
    led = Ledger(":memory:")
    _version(led, "live", {"threshold": 0.99})
    teacher = RecordingProvider(ANSWER)
    s = Settings(
        teacher_model=OPEN_TEACHER,
        samples=3,
        audit_rate=0.0,
        confidence_threshold=0.8,
        redact_pii=True,
        store_examples=True,
        no_store_tasks="",
        teacher_params="",
        student_params="",
        on_teacher_failure="error",
        teacher_fallback_model="",
        unknown_tasks="distill",
    )
    r = Ratchet(s, led, teacher, lambda v: (RecordingProvider(ANSWER), v.adapter_uri), seed=1)
    ask = [{"role": "user", "content": "I was charged twice for order 77"}]
    first = r.handle(ask, TRIAGE, client="acme", meta=RequestMeta())
    assert first.stored
    (ex,) = led.examples()
    _erase(led, hashes=[ex["hash"]])
    again = r.handle(ask, TRIAGE, client="acme", meta=RequestMeta())
    assert (again.served_by, again.stored) == ("teacher", False)  # answered as usual, never stored again
    assert [tuple(x) for x in led.db.execute("SELECT stored FROM requests ORDER BY id")] == [(1,), (0,)]
    assert any(e["kind"] == "skip_example" and e["detail"] == f"{TRIAGE}: erased" for e in led.events())


@pytest.mark.req("FR-13.3", "INV-24")
def test_erasure_makes_a_retention_tombstone_final():
    led = Ledger(":memory:", golden_fraction=0.0)
    h = _store(led, "old and then erased", client="acme", end_user="aaaa000011112222")
    led.purge_examples({h: True}, "retention", now=NOW)
    assert _row(led, h)["end_user"] == "aaaa000011112222"  # retention keeps who sent it, so an erasure can find it
    out = _erase(led, end_user_raw="u", hasher=_FixedHasher("aaaa000011112222"))
    assert out["purged"] == 1
    row = _row(led, h)
    assert (row["purge_reason"], row["end_user"], row["purged_ts"]) == ("erasure", None, NOW)
    lic = licenses.lookup(OPEN_TEACHER)
    assert lic is not None
    assert led.add_example("default", _msgs("old and then erased"), "x", OPEN_TEACHER, lic.name, lic.url) is None


@pytest.mark.req("FR-13.3")
def test_contains_matches_the_stored_text_literally():
    led = Ledger(":memory:", golden_fraction=0.0)
    hashes = {
        "percent": _store(led, "100% off today"),
        "percent_lookalike": _store(led, "1000 people came"),  # what '100%' would match as a wildcard
        "underscore": _store(led, "use a_b please"),
        "underscore_lookalike": _store(led, "use axb please"),
        "quote": _store(led, 'He said "hi" twice'),
        "backslash": _store(led, "saved in C:\\temp\\x"),
        "accent": _store(led, "a café order"),
        "plain": _store(led, "a plain request"),
        "answer": _store(led, "nothing special", completion="secret code XYZ-9"),
        "params": _store(
            led, "fill the form", params={"response_format": {"type": "json_schema", "json_schema": {"name": "invoice_total"}}}
        ),
    }

    def found(needle: str) -> set[str]:
        out = privacy.erase(led, contains=needle, actor="ops", reason="find")
        rows = led.erasure_candidates(contains=needle)
        assert out["matched"] == len(rows)
        return {name for name, h in hashes.items() if h in {r["hash"] for r in rows}}

    assert found("100%") == {"percent"} and found("a_b") == {"underscore"}  # wildcards are literal
    assert found('"hi"') == {"quote"} and found("C:\\temp") == {"backslash"}
    assert found("CAFÉ") == set() and found("Café") == {"accent"}  # LIKE folds ASCII case only
    assert found("PLAIN REQ") == {"plain"} and found("xyz-9") == {"answer"} and found("invoice_total") == {"params"}


@pytest.mark.req("FR-13.3")
@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({}, "at least one of client, end_user, hashes or contains"),
        ({"hashes": ["abc"]}, "64 lowercase hex"),
        ({"hashes": "a" * 64}, "list of example hashes"),
        ({"hashes": [5]}, "list of example hashes"),
        ({"hashes": []}, "1 to 10000"),
        ({"contains": "   "}, "not only spaces"),
        ({"client": "not a name!"}, "API key name"),
        ({"client": "acme", "reason": ""}, "reason must be"),
        ({"client": "acme", "actor": ""}, "actor must be"),
        ({"end_user_raw": "secret-person-77"}, "needs the deployment's end-user key"),
        ({"end_user_raw": "", "hasher": privacy.EndUsers(KEY, "env")}, "non-empty string"),
        ({"end_user_raw": 5, "hasher": privacy.EndUsers(KEY, "env")}, "non-empty string"),
        ({"client": "acme", "shared": "maybe"}, "shared must be delete or keep"),
    ],
)
def test_erase_refuses_a_bad_scope_without_quoting_it(kwargs, match):
    led = Ledger(":memory:")
    with pytest.raises(ValueError, match=match) as info:
        privacy.erase(led, **{"actor": "ops", "reason": "test", **kwargs})
    assert "secret-person-77" not in str(info.value) and "xxxxx" not in str(info.value)
    assert led.db.execute("SELECT COUNT(*) FROM erasures").fetchone()[0] == 0


@pytest.mark.req("FR-13.3", "INV-24")
def test_erasure_holds_under_concurrent_stores():
    led = Ledger(":memory:", golden_fraction=0.0)
    hashes = [_store(led, f"acme request {i}", client="acme") for i in range(150)]
    errors: list[BaseException] = []
    stop = threading.Event()

    def writer(k: int) -> None:
        i = k
        while not stop.is_set():
            try:
                _store(led, f"acme request {i % 150}", client="acme")  # duplicates, before and after the erasure
            except BaseException as e:  # noqa: BLE001 - collected and asserted below
                errors.append(e)
            i += 3

    threads = [threading.Thread(target=writer, args=(k,)) for k in range(3)]
    for t in threads:
        t.start()
    out = _erase(led, client="acme")
    time.sleep(0.05)
    stop.set()
    for t in threads:
        t.join()
    assert errors == [] and out["purged"] == 150
    assert {(r["purge_reason"], r["messages"]) for r in led.examples()} == {("erasure", None)}  # nothing revived
    assert {r[0] for r in led.db.execute("SELECT key FROM tombstones")} == set(hashes)


# ---------- the HTTP API ----------
@pytest.mark.req("FR-13.3")
def test_admin_erase_endpoint_needs_an_admin_key_and_is_a_dry_run_by_default(gw_env):
    gw = _gateway(gw_env, RATCHET_API_KEYS=f"acme:{ACME_KEY}", RATCHET_ADMIN_KEYS=f"ops:{ADMIN_KEY}")
    led = gw.rt.ledger
    h = _store(led, "an acme example", client="acme")
    keyed = _store(led, "an end user's example", client="acme", end_user=gw.rt.end_users.hash("person-9", "acme"))
    keyless = _store(led, "a keyless end user's example", end_user=gw.rt.end_users.hash("person-9", None))
    c = TestClient(gw.app)
    body = {"client": "acme", "reason": "contract ended"}
    assert c.post(ERASE, json=body).status_code == 401
    assert c.post(ERASE, json=body, headers=_auth(ACME_KEY)).status_code == 403

    r = c.post(ERASE, json=body, headers=_auth(ADMIN_KEY))
    assert r.status_code == 200, r.text
    assert (r.json()["dry_run"], r.json()["id"], r.json()["purged"]) == (True, None, 2)
    assert _row(led, h)["messages"] is not None

    for bad in (
        {"reason": "x"},
        {"client": "acme"},
        {"client": "acme", "reason": "x", "bogus": 1},
        {"client": "acme", "reason": "x", "dry_run": "no"},
        {"hashes": "abc", "reason": "x"},
        {"hashes": [{"a": 1}], "reason": "x"},
        {"end_user": "", "reason": "x"},
        {"end_user": ["person-9"], "reason": "x"},
        ["not", "an", "object"],
    ):
        r = c.post(ERASE, json=bad, headers=_auth(ADMIN_KEY))
        assert r.status_code == 400, (bad, r.text)
        assert "person-9" not in r.text

    # An end user without a client: every API key's scope and the keyless one (the scopes requests are attributed in).
    r = c.post(ERASE, json={"end_user": "person-9", "reason": "DSR 7", "dry_run": False}, headers=_auth(ADMIN_KEY))
    assert r.json()["purged"] == 2 and r.json()["scope"]["end_users"] == 2
    assert _row(led, keyed)["messages"] is None and _row(led, keyless)["messages"] is None
    r = c.post(ERASE, json={**body, "dry_run": False}, headers=_auth(ADMIN_KEY))
    assert r.json()["purged"] == 1 and isinstance(r.json()["id"], int) and _row(led, h)["messages"] is None
    assert [e["detail"].split(":")[0] for e in led.events() if e["kind"] == "erase"] == [
        "erasure 1 (end user) by ops",
        "erasure 2 (client) by ops",
    ]


@pytest.mark.req("FR-13.3")
def test_a_client_erases_its_own_end_users_examples(gw_env):
    gw = _gateway(gw_env, RATCHET_API_KEYS=f"acme:{ACME_KEY},globex:{GLOBEX_KEY}", RATCHET_ADMIN_KEYS=f"ops:{ADMIN_KEY}")
    _escalating(gw)
    c = TestClient(gw.app)
    sent = [(ACME_KEY, "u-1", "order 1"), (ACME_KEY, "u-1", "order 2"), (GLOBEX_KEY, "u-1", "order 3"), (ACME_KEY, "u-2", "x")]
    for key, user, text in sent:
        body = {"messages": _msgs(f"I was charged twice, {text}"), "user": user, "metadata": {"ratchet_task": TRIAGE}}
        assert c.post(CHAT, json=body, headers=_auth(key)).json()["ratchet"]["stored"]
    led = gw.rt.ledger
    acme_u1 = gw.rt.end_users.hash("u-1", "acme")

    assert c.request("DELETE", EXAMPLES, json={"end_user": "u-1"}).status_code == 401
    r = c.request("DELETE", EXAMPLES, json={"end_user": "u-2"}, params={"end_user": "u-2"}, headers=_auth(ACME_KEY))
    assert r.status_code == 400 and r.json()["error"]["code"] == "end_user_in_url" and "u-2" not in r.json()["error"]["message"]
    assert c.request("DELETE", EXAMPLES, json={"end_user": "u-1", "client": "globex"}, headers=_auth(ACME_KEY)).status_code == 403
    r = c.request("DELETE", EXAMPLES, json={"end_user": "u-1", "dry_run": True}, headers=_auth(ACME_KEY))
    assert (r.json()["dry_run"], r.json()["purged"]) == (True, 2)
    for bad in ({"end_user": "u-1", "dry_run": "yes"}, {"end_user": "u-1", "reason": 3}, {"end_user": "u-1", "hashes": []}):
        assert c.request("DELETE", EXAMPLES, json=bad, headers=_auth(ACME_KEY)).status_code == 400

    r = c.request("DELETE", EXAMPLES, json={"end_user": "u-1"}, headers=_auth(ACME_KEY))
    assert r.status_code == 200, r.text
    assert (r.json()["dry_run"], r.json()["purged"], r.json()["requests_unlinked"]) == (False, 2, 2)
    kept = {r["client"]: r for r in led.examples() if r["purged_ts"] is None}
    assert sorted(kept) == ["acme", "globex"]  # acme's u-2 and globex's own u-1 (another scope)
    assert led.db.execute("SELECT COUNT(*) FROM requests WHERE end_user=?", (acme_u1,)).fetchone()[0] == 0
    erasure = dict(led.db.execute("SELECT actor, reason FROM erasures").fetchone())
    assert erasure == {"actor": "acme", "reason": "end user request"}

    r = c.request("DELETE", EXAMPLES, json={"end_user": "u-1"}, headers=_auth(ADMIN_KEY))  # an admin: every client
    assert r.json()["purged"] == 1 and "globex" not in {r["client"] for r in led.examples() if r["purged_ts"] is None}


@pytest.mark.req("FR-13.3")
def test_keyless_erasure_callers_learn_nothing_and_admins_reach_removed_keys(gw_env):
    gw = _gateway(gw_env, RATCHET_ADMIN_KEYS=f"ops:{ADMIN_KEY}")  # API keys off: chat and erasure are open
    led = gw.rt.ledger
    stored = _store(led, "a keyless end user's example", end_user=gw.rt.end_users.hash("person-5", None))
    c = TestClient(gw.app)
    counts = ("matched", "purged", "kept", "requests_unlinked", "affected_versions")
    # Without a key the reply never says whether an identifier had anything stored: the same for one that had none.
    for user in ("person-5", "nobody-at-all"):
        r = c.request("DELETE", EXAMPLES, json={"end_user": user, "dry_run": True})
        assert r.status_code == 200, r.text
        assert {k: r.json()[k] for k in counts} == dict.fromkeys(counts) and r.json()["dry_run"] is True
    assert _row(led, stored)["messages"] is not None
    r = c.request("DELETE", EXAMPLES, json={"end_user": "person-5"})
    assert r.json()["purged"] is None and isinstance(r.json()["id"], int) and _row(led, stored)["messages"] is None

    # An admin erasing an end user for every client also reaches a key no longer configured (renamed or removed).
    old = _store(led, "an old key's example", client="initech", end_user=gw.rt.end_users.hash("person-6", "initech"))
    r = c.post(ERASE, json={"end_user": "person-6", "reason": "DSR 9"}, headers=_auth(ADMIN_KEY))
    assert (r.json()["matched"], r.json()["scope"]["end_users"]) == (1, 2)  # keyless and initech
    r = c.request("DELETE", EXAMPLES, json={"end_user": "person-6", "dry_run": True}, headers=_auth(ADMIN_KEY))
    assert r.json()["matched"] == 1  # an admin is told
    r = c.request("DELETE", EXAMPLES, json={"end_user": "person-6"}, headers=_auth(ADMIN_KEY))
    assert r.json()["purged"] == 1 and _row(led, old)["messages"] is None


# ---------- retention (FR-13.4) ----------
@pytest.mark.req("FR-13.4", "NFR-4")
def test_retention_purges_examples_not_seen_within_the_window_golden_included():
    led = Ledger(":memory:", golden_fraction=0.3)
    stored = _examples(led, train=6, golden=4, prefix="aging")
    train_rows = [e for e in stored if e.split == "train"]
    golden_rows = [e for e in stored if e.split == "golden"]
    old = [e.hash for e in train_rows] + [e.hash for e in golden_rows[:2]]
    _age(led, old, days=40)
    _age(led, [e.hash for e in golden_rows[2:]], days=5)
    # Seen again recently: a duplicate moves last_seen_ts, so the example stays.
    revisit = train_rows[0]
    lic = licenses.lookup(OPEN_TEACHER)
    assert lic is not None
    led.add_example(revisit.task, revisit.messages, "again", OPEN_TEACHER, lic.name, lic.url)

    out = privacy.apply_retention(led, Retention(example_days=30), now=NOW)
    expired = set(old) - {revisit.hash}
    assert out["examples_purged"] == len(expired) == 7
    purged = {r["hash"] for r in led.examples() if r["purged_ts"] is not None}
    assert purged == expired and {_row(led, h)["purge_reason"] for h in purged} == {"retention"}
    assert {_row(led, h)["split"] for h in purged} == {"train", "golden"}  # golden rows expire too
    assert out["requests_folded"] == 0
    (event,) = [e for e in led.events() if e["kind"] == "purge"]
    assert event["detail"] == "retention: 7 examples not seen for 30 days lost their text"
    again = privacy.apply_retention(led, Retention(example_days=30))
    assert again["examples_purged"] == 0 and len([e for e in led.events() if e["kind"] == "purge"]) == 1


@pytest.mark.req("FR-13.4")
def test_retention_purges_examples_a_chunk_per_transaction():
    def aged() -> tuple[Ledger, list[str]]:
        led = Ledger(":memory:", golden_fraction=0.3)
        hashes = [e.hash for e in _examples(led, train=5, golden=2, prefix="chunked")]
        _age(led, hashes, days=40)
        return led, hashes

    whole, hashes = aged()
    out = privacy.apply_retention(whole, Retention(example_days=30), now=NOW)
    led, _ = aged()
    lock = led.locked()

    def free_elsewhere() -> bool:
        """Whether another thread could take the ledger's lock now (an RLock: only when no thread holds it)."""
        got: list[bool] = []

        def probe() -> None:
            got.append(lock.acquire(blocking=False))
            if got[0]:
                lock.release()

        t = threading.Thread(target=probe)
        t.start()
        t.join()
        return got[0]

    sizes: list[int] = []
    starts: list[bool] = []
    purge, locked = led.purge_examples, led.locked

    def recording(rows: Any, reason: str, now: float | None = None) -> int:
        assert not free_elsewhere()  # each chunk reads, decides and writes under the lock
        sizes.append(len(rows))
        return purge(rows, reason, now=now)

    def checked() -> Any:
        starts.append(free_elsewhere())  # the lock is free before each chunk: request threads get their turn
        return locked()

    led.purge_examples = recording  # type: ignore[method-assign]
    led.locked = checked  # type: ignore[method-assign]
    assert privacy.apply_retention(led, Retention(example_days=30), now=NOW, chunk=2) == out
    assert sizes == [2, 2, 2, 1] and out["examples_purged"] == 7 and starts == [True] * 4
    columns = "hash, purged_ts, purge_reason, purged_train, messages"
    assert [tuple(r) for r in led.db.execute(f"SELECT {columns} FROM examples ORDER BY hash")] == [
        tuple(r) for r in whole.db.execute(f"SELECT {columns} FROM examples ORDER BY hash")
    ]
    assert [e["detail"] for e in led.events()] == [e["detail"] for e in whole.events()]  # one purge event
    with pytest.raises(ValueError, match="chunk must be a whole number, 1 or more"):
        privacy.apply_retention(led, Retention(example_days=30), chunk=0)


def _requests(led: Ledger, n: int = 360, seed: int = 3, ts0: float = NOW - 400 * 3600) -> None:
    """n request rows over several days, with every column a stats reader sums, NULLs included."""
    rng = random.Random(seed)
    for i in range(n):
        served = rng.choice(["student", "teacher", "student"])
        audited = int(served == "student" and rng.random() < 0.4)
        led.log_request(
            ts=ts0 + i * 3600,
            day=1 + i // 50,
            task=rng.choice(["alpha", "beta", TRIAGE]),
            served_by=served,
            client=rng.choice(["acme", "globex", None]),
            route=rng.choice(["student", "confidence", "degraded", "fallback_teacher", "cold_start", None]),
            escalated=int(served == "teacher"),
            audited=audited,
            audit_agree=rng.choice([0, 1, None]) if audited else None,
            latency_ms=rng.choice([None, rng.uniform(5, 900)]),
            cost_usd=rng.choice([None, rng.uniform(0, 0.01)]),
            baseline_cost_usd=rng.uniform(0, 0.02),
            student_cost_usd=rng.choice([None, rng.uniform(0, 0.001)]),
            teacher_cost_usd=rng.choice([None, rng.uniform(0, 0.01)]),
            student_in_tokens=rng.choice([None, rng.randint(1, 900)]),
            student_out_tokens=rng.choice([None, rng.randint(1, 300)]),
            stored=rng.choice([None, 0, 1]),
            store_opt_out=rng.choice([None, None, "request", "key", "end_user"]),
            end_user=rng.choice([None, "e1", "e2", "e3"]),
        )


def _snapshot(led: Ledger) -> dict[str, Any]:
    """Every request stat a reader shows, end users aside (a fold keeps none)."""
    return {
        "days": led.stats_by_day(),
        "tasks": {t["task"]: t for t in led.stats_by_task()},
        "clients": {c["client"]: {k: v for k, v in c.items() if k != "end_users"} for c in led.stats_by_client()},
        "totals": led.totals(),
        "opt_outs": led.opt_out_stats(),
        "cost": led.cost_by_day(),
        "cost_window": led.cost_by_day(2, 5),
    }


@pytest.mark.req("FR-13.4", "INV-25")
@pytest.mark.parametrize("chunk", [20000, 7])
def test_request_rollup_keeps_every_stat_identical(chunk):
    led = Ledger(":memory:")
    _requests(led)
    before = _snapshot(led)
    total = led.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0]

    half = led.fold_requests(NOW - 220 * 3600, chunk=chunk)  # mid-day: some days end up half folded, half kept
    assert 0 < half < total
    assert _close(_snapshot(led), before)
    rest = led.fold_requests(NOW, chunk=chunk)
    assert half + rest == total and led.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 0
    after = _snapshot(led)
    assert _close(after, before), json.dumps({"before": before["days"][:2], "after": after["days"][:2]}, default=str)
    assert [r["end_users"] for r in led.stats_by_client()] == [0, 0]  # end users are not kept in the sums
    assert led.fold_requests(NOW) == 0 and led.retention_stats()["rolled_up_requests"] == total


@pytest.mark.req("FR-13.4")
def test_fold_dry_run_counts_and_writes_nothing():
    led = Ledger(":memory:")
    _requests(led, n=40)
    assert led.fold_requests(NOW, dry_run=True) == 40
    assert led.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 40
    assert led.db.execute("SELECT COUNT(*) FROM daily_rollup").fetchone()[0] == 0


@pytest.mark.req("FR-13.4", "INV-25")
def test_retention_never_folds_rows_of_a_release_in_flight():
    led = Ledger(":memory:")
    live = _version(led, "live", {"threshold": 0.5})
    for i in range(10):
        led.log_request(
            ts=NOW - 50 * DAY_SECONDS + i, task="t", served_by="student", student_version=live, audited=1, audit_agree=1
        )
    canary = _version(led, "canary")  # its stage starts after row 10
    for i in range(6):
        led.log_request(
            ts=NOW - 40 * DAY_SECONDS + i, task="t", served_by="student", student_version=canary, audited=1, audit_agree=1
        )
    quality = led.version_live_quality(canary)
    assert led.fold_requests(NOW - 30 * DAY_SECONDS) == 10  # the canary's rows are older than the window, and stay
    assert led.version_live_quality(canary) == quality == (6, 1.0)
    led.set_version(canary, "live", 100)  # promoted: no release in flight any more
    assert led.fold_requests(NOW - 30 * DAY_SECONDS) == 6


@pytest.mark.req("FR-13.4", "FR-16.1", "INV-25")
def test_retention_keeps_every_stage_of_a_canary_for_the_anytime_valid_rule():
    """The anytime-valid canary rule counts every audit since the canary began, not only its current stage (ADR 0025),
    so a fold keeps the rows of its earlier stages too: its whole-life counts, e-values and confidence sequence are the
    same after a fold."""
    led = Ledger(":memory:")
    live = _version(led, "live", {"threshold": 0.5})
    for i in range(10):
        led.log_request(
            ts=NOW - 60 * DAY_SECONDS + i, task="t", served_by="student", student_version=live, audited=1, audit_agree=1
        )
    canary = _version(led, "canary")
    for i in range(8):  # its 10% stage: two disagreements
        led.log_request(
            ts=NOW - 50 * DAY_SECONDS + i,
            task="t",
            served_by="student",
            student_version=canary,
            audited=1,
            audit_agree=int(i > 1),
        )
    led.set_version(canary, "canary", 50)  # a new stage: stage_since moves past the 10% rows
    for i in range(4):  # interleaved: the live version's rows and the canary's 50% stage
        led.log_request(ts=NOW - 45 * DAY_SECONDS + i, task="t", served_by="student", student_version=live)
        led.log_request(
            ts=NOW - 45 * DAY_SECONDS + i, task="t", served_by="student", student_version=canary, audited=1, audit_agree=1
        )
    gate = ReleaseGate(led, ReleasePolicy(sequential=1))
    before = gate.evidence(led.version(canary))
    assert (before["n"], before["s"], before["n_stage"]) == (12, 10, 4)
    assert led.fold_requests(NOW - 30 * DAY_SECONDS) == 10  # only the live version's rows before the canary's stage
    assert led.audit_counts(canary) == (12, 10) and led.audit_counts(canary, led.version(canary).stage_since) == (4, 4)
    assert gate.evidence(led.version(canary)) == before
    led.set_version(canary, "live", 100)  # promoted: its rows may go now, and the live version's later ones
    assert led.fold_requests(NOW - 30 * DAY_SECONDS) == 16


@pytest.mark.req("FR-13.3", "FR-16.4", "INV-17", "INV-24")
def test_an_erased_committed_example_keeps_its_leaf_and_is_never_sampled():
    """A version's committed leaves outlive an erasure of the examples behind them (append-only, INV-17; hashes only,
    INV-24), and the mock demo's sample of a committed example skips one that has no text left instead of failing."""
    led = Ledger(":memory:")
    hashes = sorted(_store(led, f"order {n} arrived broken") for n in range(3))
    vid = _version(led, "live")
    leaves = [(h, hashlib.sha256(h.encode()).hexdigest()) for h in hashes]
    led.set_version_leaves(vid, "train", leaves)
    assert led.leaf_example(vid, "train")["hash"] == hashes[0]
    _erase(led, hashes=[hashes[0]])
    assert led.version_leaves(vid, "train") == leaves  # the commitment is unchanged
    assert led.leaf_example(vid, "train")["hash"] == hashes[1]  # the next leaf that still has its text
    led.purge_examples({h: True for h in hashes[1:]}, "retention")
    assert led.leaf_example(vid, "train") is None


@pytest.mark.req("FR-13.4", "INV-25")
def test_day_numbers_survive_folding_the_rows_that_defined_day_one(tmp_path):
    db = str(tmp_path / "live.db")
    led = Ledger(db, auto_day=True)
    first = NOW - 10 * DAY_SECONDS
    for d in range(10):
        led.log_request(ts=first + d * DAY_SECONDS + 60, task="t", served_by="teacher", cost_usd=0.01)
    assert led.day == 10 and led.day_of(first + 3 * DAY_SECONDS + 61) == 4
    assert led.fold_requests(first + 5 * DAY_SECONDS) == 5
    led.db.close()
    again = Ledger(db, auto_day=True)
    assert again.day == 10 and again.day_of(first + 3 * DAY_SECONDS + 61) == 4  # not renumbered from the oldest row left
    assert [d["day"] for d in again.stats_by_day()] == list(range(1, 11))
    again.fold_requests(NOW)
    again.db.close()
    empty = Ledger(db, auto_day=True)
    assert empty.day == 10 and empty.day_start(1) == first + 60


@pytest.mark.req("FR-13.4", "FR-4.4")
def test_run_night_applies_retention_before_reading_the_ledger():
    led = Ledger(":memory:", golden_fraction=0.3)
    stored = _examples(led, train=70, golden=8, prefix="night")
    expired = [e.hash for e in stored if e.split == "train"][:10]
    _age(led, expired, days=10)
    trainer = RecordingTrainer()
    rep = run_night(
        led, trainer, RecordingEvaluator(REJECTED_EVAL), ReleaseGate(led), BASE_MODEL, retention=Retention(example_days=7)
    )
    assert rep.trained and len(trainer.calls[0].rows) == 60
    assert not expired_in(trainer.calls[0].rows, expired)
    kinds = [e["kind"] for e in led.events()]
    assert kinds.index("purge") < kinds.index("trained")


def expired_in(rows: list[dict[str, Any]], hashes: list[str]) -> bool:
    return bool({r["provenance"]["hash"] for r in rows} & set(hashes))


@pytest.mark.req("FR-13.4")
def test_live_gateway_purges_hourly_off_the_request_path(gw_env, tmp_path, monkeypatch):
    gw = gw_env()
    clock = FakeClock()
    settings = Settings(
        mode="live",
        db_path=str(tmp_path / "live.db"),
        teacher_base="https://teacher.test/v1",
        teacher_api_key="tf-key",
        teacher_model=OPEN_TEACHER,
        student_base="",
        api_keys="",
        admin_keys="",
        bom_signing_key="",
        mock_warmup_days=0,
        end_user_key=END_USER_KEY,
        example_retention_days=30,
        request_retention_days=60,
    )
    rt = gw.Runtime(settings, transports={"teacher": FakeOpenAIServer("hi").transport}, clock=clock)
    led = rt.ledger
    h = _store(led, "an old example")
    _age(led, [h], days=45)
    led.log_request(ts=time.time() - 90 * DAY_SECONDS, task="t", served_by="teacher", cost_usd=0.01)

    thread = rt.maybe_purge()
    assert thread is not None
    thread.join(5)
    assert _row(led, h)["purge_reason"] == "retention" and led.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 0
    assert [e["kind"] for e in led.events()].count("purge") == 1
    assert rt.maybe_purge() is None  # within the hour
    clock.advance(gw.RETENTION_INTERVAL_S + 1)

    def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("disk on fire: /secret/path")

    monkeypatch.setattr(gw.privacy, "apply_retention", broken)
    rt.tick()  # the tick a request runs starts it, and does not wait for it
    assert rt.purge_thread is not None
    rt.purge_thread.join(5)
    (failed,) = [e for e in led.events() if e["kind"] == "purge_failed"]
    assert failed["detail"] == "retention: RuntimeError"  # never the message
    assert gw.rt.maybe_purge() is None  # mock mode never purges on a schedule
    off = replace(settings, db_path=str(tmp_path / "off.db"), example_retention_days=0, request_retention_days=0)
    assert gw.Runtime(off, transports={"teacher": FakeOpenAIServer("hi").transport}).maybe_purge() is None


@pytest.mark.req("FR-13.4")
@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"example_retention_days": -1}, "RATCHET_EXAMPLE_RETENTION_DAYS"),
        ({"request_retention_days": 40000}, "RATCHET_REQUEST_RETENTION_DAYS"),
        ({"example_retention_days": True}, "RATCHET_EXAMPLE_RETENTION_DAYS"),
        ({"erasure_shared": "maybe"}, "RATCHET_ERASURE_SHARED"),
    ],
)
def test_bad_retention_settings_stop_startup(overrides, match, gw_env, tmp_path):
    gw = gw_env()
    with pytest.raises(ValueError, match=match):
        Settings(**overrides).retention()
    with pytest.raises(ValueError, match=match):
        gw.Runtime(Settings(mode="mock", mock_warmup_days=0, **overrides))
    assert Settings(erasure_shared=" KEEP ").retention().erasure_shared == "keep"
    assert Settings(erasure_shared="").retention().erasure_shared == "delete"


@pytest.mark.req("FR-13.4")
def test_retention_days_must_be_whole_numbers_in_the_environment(monkeypatch):
    monkeypatch.setenv("RATCHET_REQUEST_RETENTION_DAYS", "ten")
    with pytest.raises(ValueError, match="RATCHET_REQUEST_RETENTION_DAYS must be a whole number"):
        Settings()


def _purge_env(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    for name in list(os.environ):
        if name.startswith("RATCHET_") or name in ("NEBIUS_API_KEY", "TAVILY_API_KEY"):
            monkeypatch.delenv(name)
    for name, value in env.items():
        monkeypatch.setenv(name, value)


@pytest.mark.req("FR-13.4")
def test_live_purge_cli(tmp_path, monkeypatch, capsys):
    db = tmp_path / "live.db"
    _purge_env(monkeypatch, RATCHET_DB=str(db), RATCHET_EXAMPLE_RETENTION_DAYS="30", RATCHET_REQUEST_RETENTION_DAYS="90")
    led = Ledger(str(db), auto_day=True)
    old = [_store(led, "acme old", client="acme"), _store(led, "globex old", client="globex")]
    young = _store(led, "acme young", client="acme")
    _age(led, old, days=45)
    _age(led, [young], days=3)
    for client, days in (("acme", 100), ("globex", 100), ("acme", 10)):
        led.log_request(ts=time.time() - days * DAY_SECONDS, task="t", client=client, served_by="teacher", cost_usd=0.01)
    led.db.close()

    live.main(["purge", "--dry-run"])
    assert "would purge: 2 examples (window 30 days), 2 request rows folded (window 90 days)" in capsys.readouterr().out
    assert Ledger(str(db)).retention_stats()["purged_examples"] == 0
    live.main(["purge", "--client", "acme"])
    assert "purged: 1 examples (window 30 days), 1 request rows folded (window 90 days)" in capsys.readouterr().out
    live.main(["purge", "--older-than-days", "2"])
    assert "purged: 2 examples (window 2 days), 2 request rows folded (window 2 days)" in capsys.readouterr().out
    after = Ledger(str(db))
    assert (
        after.retention_stats()["purged_examples"] == 3 and after.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 0
    )
    assert [e["detail"] for e in after.events() if e["kind"] == "purge"][0].startswith("retention (client acme): ")
    with pytest.raises(SystemExit, match="--older-than-days must be 1 or more"):
        live.main(["purge", "--older-than-days", "0"])
    # Bad values the settings' checks catch: a message, never a traceback.
    with pytest.raises(SystemExit, match="purge: client must be an API key name"):
        live.main(["purge", "--client", "bad name"])
    with pytest.raises(SystemExit, match="purge: example_days must be a whole number of days, 0 to 36500"):
        live.main(["purge", "--older-than-days", "99999"])
    _purge_env(monkeypatch, RATCHET_DB=str(db))
    live.main(["purge"])
    assert "nothing to purge" in capsys.readouterr().out


# ---------- Night School (FR-4.4, INV-3) ----------
@pytest.mark.req("FR-4.4", "FR-13.3", "FR-13.4")
@pytest.mark.parametrize("how", ["erasure", "retention"])
def test_run_night_trains_again_after_deletions_once_50_new_examples_arrive(how):
    led = Ledger(":memory:", golden_fraction=0.3)
    first = _examples(led, train=100, golden=10, prefix="first")
    trainer, evaluator, gate = RecordingTrainer(), RecordingEvaluator(REJECTED_EVAL), ReleaseGate(led)
    assert run_night(led, trainer, evaluator, gate, BASE_MODEL).trained  # v1 on 100
    gone = [e.hash for e in first if e.split == "train"][:60]
    if how == "erasure":
        _erase(led, hashes=gone)
    else:
        _age(led, gone, days=40)
        privacy.apply_retention(led, Retention(example_days=30), now=time.time())
    assert led.train_rows_kept() == 40

    second = _examples(led, train=49, prefix="second")
    rep = run_night(led, trainer, evaluator, gate, BASE_MODEL)
    assert (rep.trained, rep.reason) == (False, "not enough new data")
    assert "only 49 new examples (< 50)" in led.events()[-1]["detail"]  # v0.4's rule said -11 and never trained again
    _examples(led, train=1, prefix="third")
    rep = run_night(led, trainer, evaluator, gate, BASE_MODEL)
    assert rep.trained and len(trainer.calls[-1].rows) == 90
    assert not expired_in(trainer.calls[-1].rows, gone)

    # v2 trained on fewer rows than v1: with no new data, no night trains again. Measured against v1's purges, the 60
    # rows would come back as new every night (a paid GPU job each time in live mode).
    for _ in range(3):
        rep = run_night(led, trainer, evaluator, gate, BASE_MODEL)
        assert (rep.trained, rep.reason) == (False, "not enough new data")
        assert "only 0 new examples (< 50)" in led.events()[-1]["detail"]
    assert [v.train_examples for v in led.versions()] == [100, 90] and len(trainer.calls) == 2
    # A deletion of rows only v2 had is not new data either; 50 new rows are.
    later = [e.hash for e in second[:10]]
    if how == "erasure":
        _erase(led, hashes=later)
    else:
        _age(led, later, days=40)
        privacy.apply_retention(led, Retention(example_days=30), now=time.time())
    assert led.train_rows_kept() == 80
    rep = run_night(led, trainer, evaluator, gate, BASE_MODEL)
    assert (rep.trained, rep.reason) == (False, "not enough new data") and "only 0 new" in led.events()[-1]["detail"]
    _examples(led, train=50, prefix="fourth")
    rep = run_night(led, trainer, evaluator, gate, BASE_MODEL)
    assert rep.trained and len(trainer.calls[-1].rows) == 130
    assert not expired_in(trainer.calls[-1].rows, gone + later)


@pytest.mark.req("INV-3", "FR-13.4")
def test_a_purged_variant_still_counts_toward_its_requests_frequency():
    led = Ledger(":memory:", golden_fraction=1.0)  # every prompt is golden by hash
    bare = _store(led, "repeat me")
    stop = {"stop": ["\n\n"]}
    variant = _store(led, "repeat me", params=stop)
    _store(led, "repeat me", params=stop)  # seen 2 + 1 = 3: FREQUENT, so the request trains (its stop variant cannot)
    train = {r["provenance"]["hash"] for r in dataset.rows(led, "train")}
    assert train == {bare} and dataset.rows(led, "golden") == []
    led.purge_examples({variant: False}, "retention")
    row = _row(led, variant)
    assert row["params"] is None and row["prompt_hash"] == example_hash("default", _msgs("repeat me"))
    assert {r["provenance"]["hash"] for r in dataset.rows(led, "train")} == {bare}  # still frequent: never back to golden
    assert dataset.rows(led, "golden") == []
    assert led.prompt_counts()[row["prompt_hash"]] == 3


# ---------- export (FR-13.5) ----------
@pytest.mark.req("FR-13.5", "NFR-4", "INV-13")
def test_export_round_trips_with_provenance_and_holds_no_keys(gw_env):
    gw = _gateway(gw_env, RATCHET_API_KEYS=f"acme:{ACME_KEY},globex:{GLOBEX_KEY}", RATCHET_ADMIN_KEYS=f"ops:{ADMIN_KEY}")
    _escalating(gw)
    c = TestClient(gw.app)
    for key, user, text in [(ACME_KEY, "u-1", "a"), (ACME_KEY, "u-2", "b"), (GLOBEX_KEY, "u-1", "a"), (GLOBEX_KEY, "u-3", "c")]:
        body = {"messages": _msgs(f"Refund my order {text}"), "user": user, "metadata": {"ratchet_task": TRIAGE}}
        assert c.post(CHAT, json=body, headers=_auth(key)).status_code == 200
    led = gw.rt.ledger
    gone = led.examples()[-1]["hash"]
    c.post(ERASE, json={"hashes": [gone], "reason": "test", "dry_run": False}, headers=_auth(ADMIN_KEY))

    texts = {}
    for kind in EXPORT_KINDS:
        r = c.get(f"/ratchet/admin/export/{kind}", headers=_auth(ADMIN_KEY))
        assert r.status_code == 200 and r.headers["content-type"].startswith("application/x-ndjson"), r.text
        assert r.headers["content-disposition"] == f'attachment; filename="ratchet-{kind}.ndjson"'
        texts[kind] = r.text
    examples = _lines(texts["examples"])
    kept = [r for r in led.examples() if r["purged_ts"] is None]
    assert [e["hash"] for e in examples] == [r["hash"] for r in kept] and gone not in texts["examples"]
    for e, r in zip(examples, kept, strict=True):
        assert e["messages"] == json.loads(r["messages"]) and e["completion"] == r["completion"]
        assert e["provenance"] == {
            "teacher": OPEN_TEACHER,
            "license": r["license"],
            "license_url": r["license_url"],
            "hash": r["hash"],
            "redactions": r["redactions"],
            "origin": "live",
        }
        assert (e["split"], e["seen"], e["client"], e["clients"]) == (
            r["split"],
            r["seen"],
            r["client"],
            led.example_clients(r["hash"]),
        )
    assert examples[0]["clients"] == ["acme", "globex"]  # the same request from both clients
    assert len(_lines(texts["requests"])) == 4 and all("end_user" not in r for r in _lines(texts["requests"]))
    versions = _lines(texts["versions"])
    assert versions[0]["state"] == "live" and versions[0]["eval"] == {"threshold": 0.99}
    (erasure,) = _lines(texts["erasures"])
    assert erasure["items"] == [{"hash": gone, "task": TRIAGE, "split": led.examples()[-1]["split"]}]
    assert any(e["kind"] == "erase" for e in _lines(texts["events"]))
    everything = "\n".join(texts.values())
    for secret in (ACME_KEY, GLOBEX_KEY, ADMIN_KEY, END_USER_KEY, gw.rt.end_users.hash("u-1", "acme"), '"u-1"'):
        assert secret not in everything


@pytest.mark.req("FR-13.5")
def test_export_filters_and_refusals(gw_env):
    gw = _gateway(gw_env, RATCHET_API_KEYS=f"acme:{ACME_KEY}", RATCHET_ADMIN_KEYS=f"ops:{ADMIN_KEY}")
    led = gw.rt.ledger
    led.golden_fraction = 0.3
    stored = _examples(led, train=4, golden=3, prefix="export")
    _store(led, "acme's own", client="acme", task=TRIAGE, completion=ANSWER)
    c = TestClient(gw.app)

    def hashes(path: str) -> list[str]:
        r = c.get(path, headers=_auth(ADMIN_KEY))
        assert r.status_code == 200, r.text
        return [e["hash"] for e in _lines(r.text)]

    assert len(hashes("/ratchet/admin/export/examples")) == 8
    assert hashes("/ratchet/admin/export/examples?split=golden") == [e.hash for e in stored if e.split == "golden"]
    assert len(hashes(f"/ratchet/admin/export/examples?task={TRIAGE}")) == 1
    assert len(hashes("/ratchet/admin/export/examples?client=acme")) == 1
    assert len(hashes("/ratchet/admin/export/examples?since=2000-01-01")) == 8
    assert hashes(f"/ratchet/admin/export/examples?since={time.time() + 3600:.0f}") == []
    assert hashes("/ratchet/admin/export/examples?since=2999-01-01T00:00:00Z") == []
    assert c.get("/ratchet/admin/export/examples").status_code == 401
    assert c.get("/ratchet/admin/export/examples", headers=_auth(ACME_KEY)).status_code == 403
    for path, words in (
        ("/ratchet/admin/export/secrets", "export kind"),
        ("/ratchet/admin/export/requests?split=train", "requests take client, since, task, not split"),
        ("/ratchet/admin/export/events?client=acme", "not client"),
        ("/ratchet/admin/export/examples?since=yesterday", "ISO 8601"),
        ("/ratchet/admin/export/examples?split=test", "train or golden"),
        ("/ratchet/admin/export/examples?client=no%20such", "API key name"),
    ):
        r = c.get(path, headers=_auth(ADMIN_KEY))
        assert r.status_code == 400 and words in r.json()["detail"], (path, r.text)


@pytest.mark.req("FR-13.5")
def test_export_pages_through_large_tables():
    led = Ledger(":memory:", golden_fraction=0.0)
    for i in range(23):
        _store(led, f"paged {i}")
    lines = list(privacy.export_lines(led, "examples", page=5))
    assert [json.loads(line)["messages"][0]["content"] for line in lines] == [f"paged {i}" for i in range(23)]
    assert all(line.endswith("\n") and "\n" not in line[:-1] for line in lines)


@pytest.mark.req("FR-13.3", "FR-13.5")
def test_admin_cli_erases_and_exports(tmp_path, monkeypatch):
    db = tmp_path / "live.db"
    _purge_env(monkeypatch, RATCHET_DB=str(db), RATCHET_END_USER_KEY=END_USER_KEY, RATCHET_API_KEYS=f"acme:{ACME_KEY}")
    led = Ledger(str(db), auto_day=True)
    hasher = privacy.end_users(led, END_USER_KEY)
    mine = _store(led, "acme person's request", client="acme", end_user=hasher.hash("person-1", "acme"))
    keyless = _store(led, "keyless person's request", end_user=hasher.hash("person-1", None))
    other = _store(led, "another request", client="acme")
    led.db.close()

    out = io.StringIO()
    assert admin.main(["erase", "--reason", "DSR 12", "--client", "acme"], stdout=out) == 0
    preview = json.loads(out.getvalue().split("\ndry run")[0])
    assert (preview["dry_run"], preview["purged"]) == (True, 2) and "add --apply to erase" in out.getvalue()
    out = io.StringIO()
    assert (
        admin.main(["erase", "--reason", "DSR 12", "--end-user", "-", "--apply"], stdin=io.StringIO("person-1\n"), stdout=out)
        == 0
    )
    result = json.loads(out.getvalue())
    assert (result["dry_run"], result["purged"], result["scope"]["end_users"]) == (False, 2, 2)
    check = Ledger(str(db))
    assert _row(check, mine)["messages"] is None and _row(check, keyless)["messages"] is None
    assert _row(check, other)["messages"] is not None
    assert check.db.execute("SELECT actor FROM erasures").fetchone()[0] == "cli"
    with pytest.raises(SystemExit, match="erase: name at least one"):
        admin.main(["erase", "--reason", "x"], stdout=io.StringIO())
    with pytest.raises(SystemExit, match="no value on standard input"):
        admin.main(["erase", "--reason", "x", "--end-user", "-"], stdin=io.StringIO(""), stdout=io.StringIO())

    target = tmp_path / "examples.ndjson"
    assert admin.main(["export", "examples", "--out", str(target)]) == 0
    assert [e["hash"] for e in _lines(target.read_text(encoding="utf-8"))] == [other]
    out = io.StringIO()
    assert admin.main(["export", "erasures"], stdout=out) == 0
    assert [e["purged"] for e in _lines(out.getvalue())] == [2]
    with pytest.raises(SystemExit, match="export: events take since, not task"):
        admin.main(["export", "events", "--task", "x"], stdout=io.StringIO())


@pytest.mark.req("FR-13.3")
def test_admin_cli_erases_end_users_only_with_the_gateways_key_and_never_writes_otherwise(tmp_path, monkeypatch):
    db = tmp_path / "live.db"
    led = Ledger(str(db), auto_day=True)
    gateway = privacy.end_users(led, END_USER_KEY)  # the gateway's startup, with RATCHET_END_USER_KEY set
    acme = _store(led, "acme person's request", client="acme", end_user=gateway.hash("person-1", "acme"))
    # A key removed from RATCHET_API_KEYS since: its rows still name the end user in its scope.
    initech = _store(led, "initech person's request", client="initech", end_user=gateway.hash("person-1", "initech"))
    led.db.close()

    def snapshot() -> tuple[Any, ...]:
        check = Ledger(str(db))
        try:
            meta = dict(check.db.execute("SELECT key, value FROM meta").fetchall())
            erasures = check.db.execute("SELECT COUNT(*) FROM erasures").fetchone()[0]
            return meta, [(e["kind"], e["detail"]) for e in check.events()], erasures, _row(check, acme)["messages"]
        finally:
            check.db.close()

    before = snapshot()
    erase = ["erase", "--reason", "DSR 31", "--end-user", "-"]
    # The operator's shell lacks the gateway's key, or has another one: refused, for a dry run and with --apply, and
    # nothing in the ledger changes (no generated key, no key-change event, no erasure that erased nothing).
    for env, words in (
        ({}, "RATCHET_END_USER_KEY is not set here, but the gateway hashes end users with a key of its own"),
        ({"RATCHET_END_USER_KEY": "another-end-user-key-0123456789"}, "is not the key the gateway hashes end users with"),
        ({"RATCHET_END_USER_KEY": "short"}, "at least 16 characters"),
    ):
        _purge_env(monkeypatch, RATCHET_DB=str(db), **env)
        for apply in ([], ["--apply"]):
            with pytest.raises(SystemExit, match=words) as stop:
                admin.main(erase + apply, stdin=io.StringIO("person-1\n"), stdout=io.StringIO())
            assert "person-1" not in str(stop.value)
            assert snapshot() == before

    # With the gateway's key and no RATCHET_API_KEYS at all, every scope the ledger holds end users in is searched.
    _purge_env(monkeypatch, RATCHET_DB=str(db), RATCHET_END_USER_KEY=END_USER_KEY)
    out = io.StringIO()
    assert admin.main(erase, stdin=io.StringIO("person-1\n"), stdout=out) == 0
    preview = json.loads(out.getvalue().split("\ndry run")[0])
    assert (preview["matched"], preview["scope"]["end_users"]) == (2, 3)  # keyless, acme and initech
    assert snapshot() == before  # a dry run writes nothing
    out = io.StringIO()
    assert admin.main([*erase, "--apply"], stdin=io.StringIO("person-1\n"), stdout=out) == 0
    assert json.loads(out.getvalue())["purged"] == 2
    check = Ledger(str(db))
    assert _row(check, acme)["messages"] is None and _row(check, initech)["messages"] is None
    assert [e["kind"] for e in check.events()] == ["erase"]  # no end_user_key event
    # An actor is a name, never free text: it is shown in the public events.
    with pytest.raises(SystemExit, match="actor must be a name"):
        admin.main(["erase", "--reason", "x", "--client", "acme", "--actor", "jane@example.com"], stdout=io.StringIO())

    # A gateway without RATCHET_END_USER_KEY keeps a generated key in the ledger: a shell without one uses it.
    fresh = tmp_path / "generated.db"
    led = Ledger(str(fresh), auto_day=True)
    keyless = _store(led, "a keyless request", end_user=privacy.end_users(led).hash("person-2", None))
    led.db.close()
    _purge_env(monkeypatch, RATCHET_DB=str(fresh), RATCHET_END_USER_KEY=END_USER_KEY)
    with pytest.raises(SystemExit, match="the gateway uses the key generated into the ledger: unset RATCHET_END_USER_KEY"):
        admin.main(erase, stdin=io.StringIO("person-2\n"), stdout=io.StringIO())
    _purge_env(monkeypatch, RATCHET_DB=str(fresh))
    out = io.StringIO()
    admin.main([*erase, "--apply"], stdin=io.StringIO("person-2\n"), stdout=out)
    assert json.loads(out.getvalue())["purged"] == 1 and _row(Ledger(str(fresh)), keyless)["messages"] is None
    # A ledger that never hashed an end user has none to find.
    empty = tmp_path / "empty.db"
    Ledger(str(empty)).db.close()
    _purge_env(monkeypatch, RATCHET_DB=str(empty), RATCHET_END_USER_KEY=END_USER_KEY)
    with pytest.raises(SystemExit, match="never hashed an end user"):
        admin.main(erase, stdin=io.StringIO("person-2\n"), stdout=io.StringIO())
    assert Ledger(str(empty)).meta_get(privacy.KEY_ID_META) is None


# ---------- schema ----------
@pytest.mark.req("FR-13.3", "FR-13.4")
def test_a_ledger_from_before_retention_gains_its_columns_and_tables(tmp_path):
    db = tmp_path / "pre-retention.db"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE examples (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, day INTEGER, task TEXT, hash TEXT UNIQUE, messages TEXT,
            completion TEXT, teacher_model TEXT, license TEXT, license_url TEXT, split TEXT, seen INTEGER DEFAULT 1,
            client TEXT, end_user TEXT
        );
        CREATE TABLE erasures (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, day INTEGER, key TEXT, tainted TEXT, status TEXT,
            replacement INTEGER, certificate TEXT
        );
        INSERT INTO examples (ts, day, task, hash, messages, completion, teacher_model, license, license_url, split)
            VALUES (1700000000, 1, 'default', 'h1', '[]', 'a', 'm', 'l', 'u', 'train');
    """)
    con.commit()
    con.close()
    for _ in range(2):  # opening twice changes nothing more
        led = Ledger(str(db))
        tables = {r[0] for r in led.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert set(GOVERNANCE_TABLES) <= tables
        for table in ("examples", "erasures"):
            assert set(MIGRATIONS[table]) <= {r["name"] for r in led.db.execute(f"PRAGMA table_info({table})")}
        led.db.close()
    led = Ledger(str(db))
    row = led.examples()[0]
    assert (row["last_seen_ts"], row["purged_ts"], row["purge_reason"]) == (1700000000.0, None, None)  # backfilled from ts
    assert led.bill_of_materials()["purged_examples"] == 0
    out = privacy.apply_retention(led, Retention(example_days=1), now=1700000000.0 + 2 * DAY_SECONDS)
    assert out["examples_purged"] == 1 and led.erasure_candidates(hashes=["h1"])[0]["purge_reason"] == "retention"
