"""Ground truth from people (FR-13.30, FR-13.31, FR-3.54, FR-4.50, ADR 0091).

- Feedback: every chat response carries `ratchet.request_id`; `POST /v1/ratchet/feedback` records a verdict on one of
  the caller's own requests, and an 'incorrect' verdict with a correction and the request's messages stores a
  human-labelled example (provenance `human`, `verified`), redacted and in its hash's split; a 'correct' verdict never
  stores anything (invariant 2). The storage opt-outs and pinning still apply; human precision is reported and alerted
  on, never used by the release gate.
- Provenance: `human` is a source in `licenses.SOURCES`, never a teacher (invariant 1); a human label beats a teacher's
  on the same hash, an operator's import beats a client's correction; BOMs list human labels apart.
- Import: `python -m ratchet.importer` reads OpenAI fine-tuning JSONL and Ratchet's train.jsonl, as attested human labels
  or an open teacher's outputs (closed families refused, the clause quoted), validated, redacted and deduplicated;
  `--golden-only` rows are never trained on, even past FREQUENT.
"""

from __future__ import annotations

import io
import json
import math
import re
import sqlite3
import threading
import time
from typing import Any

import pytest
from fakes import OPEN_TEACHER, RecordingEvaluator, RecordingProvider, RecordingTrainer, _version
from fastapi.testclient import TestClient

from ratchet import bom, feedback, importer, licenses, privacy, tasks
from ratchet.config import Settings
from ratchet.ledger import FEEDBACK_TABLES, Ledger, example_hash
from ratchet.nightschool import dataset, notices
from ratchet.nightschool.orchestrate import run_night
from ratchet.redact import redact, redact_messages
from ratchet.release import ReleaseGate, ReleasePolicy
from ratchet.router import Ratchet
from ratchet.stats import log_e_bernoulli, wilson
from ratchet.tasks import Task

CHAT = "/v1/chat/completions"
FEEDBACK = "/v1/ratchet/feedback"
NO_TRAIN = "/v1/ratchet/no-train"
ACME_KEY = "acme-feedback-key-0123456789abcdef"
GLOBEX_KEY = "globex-feedback-key-0123456789abcd"
TRIAGE = "ticket-triage"
A = json.dumps({"category": "billing", "priority": "high", "reply": "I am checking the charge now."})
B = json.dumps({"category": "refund", "priority": "low", "reply": "Your refund is on its way."})
C = json.dumps({"category": "account", "priority": "medium", "reply": "Write to jane.roe@example.com for access."})
D = json.dumps({"category": "shipping", "priority": "urgent", "reply": "Your parcel left the depot."})
REQUEST_ID = re.compile(r"req_([0-9a-f]{32})")
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
    "RATCHET_AUDIT_RATE": "0.0",
    "RATCHET_CONFIDENCE_THRESHOLD": "0.80",
    "RATCHET_API_KEY_DAILY_REQUESTS": "0",
    "RATCHET_TEACHER_PARAMS": "",
    "RATCHET_STUDENT_PARAMS": "",
    "RATCHET_ON_TEACHER_FAILURE": "error",
    "RATCHET_TEACHER_FALLBACK_MODEL": "",
    "RATCHET_UNKNOWN_TASKS": "distill",
    "RATCHET_TEACHER_MODALITIES": "text",
    "RATCHET_FEEDBACK_CORRECTIONS": "1",
    "RATCHET_MERGE_SYSTEM": "0",
    "RATCHET_GOLDEN_FRACTION": "0.10",
    "RATCHET_TASKS_FILE": "",
    "RATCHET_TEACHER_LICENSES": "",
}


# ---------- helpers ----------
def _gateway(gw_env: Any, **env: str) -> Any:
    return gw_env(**{**PINNED_ENV, **env})


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _msgs(text: str) -> list[dict[str, str]]:
    return [{"role": "system", "content": "Triage the ticket."}, {"role": "user", "content": text}]


def _cascade(
    gw: Any, *, student: str = A, teacher: str = B, threshold: float = 0.5
) -> tuple[RecordingProvider, RecordingProvider]:
    """A live version whose student answers `student` with confidence about 0.95: it serves when `threshold` is below
    that, and every request escalates to the teacher (which answers `teacher`) when it is above. No audits."""
    _version(gw.rt.ledger, "live", {"threshold": threshold})
    s, t = RecordingProvider(student), RecordingProvider(teacher)
    gw.rt.ratchet.teacher = t
    gw.rt.ratchet.resolve_student = lambda v: (s, v.adapter_uri)
    gw.rt.settings.audit_rate = 0.0
    return s, t


def _chat(c: TestClient, text: str, key: str | None = None, **extra: Any) -> dict[str, Any]:
    body = {"messages": _msgs(text), "metadata": {"ratchet_task": TRIAGE}, **extra}
    r = c.post(CHAT, json=body, headers=_auth(key) if key else {})
    assert r.status_code == 200, r.text
    return r.json()


def _feedback(c: TestClient, body: dict[str, Any], key: str | None = None) -> Any:
    return c.post(FEEDBACK, json=body, headers=_auth(key) if key else {})


def _examples(led: Ledger) -> list[dict[str, Any]]:
    return [dict(r) for r in led.examples()]


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "teacher_model": OPEN_TEACHER,
        "redact_pii": True,
        "store_examples": True,
        "no_store_tasks": "",
        "teacher_params": "",
        "student_params": "",
        "on_teacher_failure": "error",
        "teacher_fallback_model": "",
        "unknown_tasks": "distill",
        "merge_system": False,
        "feedback_corrections": True,
        "golden_fraction": 0.10,
        "max_body_bytes": 2_097_152,
    }
    return Settings(**{**base, **overrides})


def _jsonl(*rows: Any) -> io.BytesIO:
    return io.BytesIO(b"".join((r if isinstance(r, bytes) else json.dumps(r).encode()) + b"\n" for r in rows))


def _ft(prompt: str, answer: str, **extra: Any) -> dict[str, Any]:
    """One row of OpenAI's chat fine-tuning format."""
    return {"messages": [*_msgs(prompt), {"role": "assistant", "content": answer}], **extra}


def _import(led: Ledger, *rows: Any, settings: Settings | None = None, **kw: Any) -> importer.Report:
    options: dict[str, Any] = {"task": TRIAGE, "source": "human", "attest": "written by our support leads", **kw}
    return importer.run_import(led, settings or _settings(), _jsonl(*rows), file_name="labels.jsonl", **options)


def _prompt_in(split: str, prefix: str, golden_fraction: float = 0.10, skip: int = 0) -> str:
    """A ticket text whose example (task ticket-triage, `_msgs`, no conditions) lands in `split`."""
    probe = Ledger(":memory:", golden_fraction)
    found = 0
    for i in range(5000):
        text = f"{prefix} {i}"
        if probe._split(example_hash(TRIAGE, _msgs(text))) == split:
            if found == skip:
                return text
            found += 1
    raise AssertionError(f"no prompt lands in {split}")


# ---------- request ids ----------
@pytest.mark.req("FR-13.30")
def test_response_has_request_id(gw_env):
    gw = _gateway(gw_env)
    c = TestClient(gw.app)
    _cascade(gw)
    body = _chat(c, "Where is my parcel?")
    m = REQUEST_ID.fullmatch(body["ratchet"]["request_id"])
    assert m is not None and body["id"] == f"chatcmpl-{m.group(1)}"
    row = gw.rt.ledger.db.execute("SELECT request_id FROM requests ORDER BY id DESC LIMIT 1").fetchone()
    assert row["request_id"] == body["ratchet"]["request_id"]
    again = _chat(c, "Where is my parcel?")  # every request gets its own id
    assert again["ratchet"]["request_id"] != body["ratchet"]["request_id"]

    # A stream carries the same id in its chunks' `id` and in the ratchet block of its last chunk.
    r = c.post(CHAT, json={"messages": _msgs("Refund please"), "metadata": {"ratchet_task": TRIAGE}, "stream": True})
    chunks = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: {")]
    rid = chunks[-1]["ratchet"]["request_id"]
    assert {ch["id"] for ch in chunks} == {"chatcmpl-" + rid[4:]}
    # Internal calls (Scout, the simulator) have no gateway id.
    gw.rt.ratchet.handle(_msgs("internal"), TRIAGE)
    assert gw.rt.ledger.db.execute("SELECT request_id FROM requests ORDER BY id DESC LIMIT 1").fetchone()[0] is None
    assert feedback.parse_request_id(rid) == rid and feedback.parse_request_id("chatcmpl-" + rid[4:]) == rid
    for bad in ("req_" + "g" * 32, rid.upper(), rid + " ", 42, None, "req_123"):
        assert feedback.parse_request_id(bad) is None


# ---------- verdicts ----------
@pytest.mark.req("FR-13.30", "INV-13", "INV-26")
def test_feedback_only_on_own_requests(gw_env):
    gw = _gateway(gw_env, RATCHET_API_KEYS=f"acme:{ACME_KEY},globex:{GLOBEX_KEY}")
    c = TestClient(gw.app)
    _cascade(gw)
    rid = _chat(c, "I was charged twice", ACME_KEY)["ratchet"]["request_id"]
    verdict = {"request_id": rid, "verdict": "incorrect"}

    other = _feedback(c, verdict, GLOBEX_KEY)
    unknown = _feedback(c, {**verdict, "request_id": "req_" + "0" * 32}, GLOBEX_KEY)
    assert other.status_code == unknown.status_code == 404
    assert other.json() == unknown.json()  # another key's request is answered exactly like one that does not exist
    assert other.json()["error"]["code"] == "request_not_found" and rid not in other.text
    assert _feedback(c, verdict).status_code == 401  # keys are on: no key, no feedback
    assert gw.rt.ledger.db.execute("SELECT COUNT(*) FROM feedback").fetchone()[0] == 0

    ok = _feedback(c, {**verdict, "request_id": "chatcmpl-" + rid[4:]}, ACME_KEY)  # the completion id works too
    assert ok.status_code == 200, ok.text
    assert ok.json() == {
        "object": "ratchet.feedback",
        "request_id": rid,
        "verdict": "incorrect",
        "updated": False,
        "correction": None,
    }
    row = gw.rt.ledger.feedback_row(rid)
    assert (row["client"], row["verdict"], row["task"], row["served_by"], row["route"]) == (
        "acme",
        "incorrect",
        TRIAGE,
        "student",
        "student",
    )
    # A later verdict replaces the earlier one: one row per request.
    again = _feedback(c, {**verdict, "verdict": "correct"}, ACME_KEY).json()
    assert again["updated"] is True and gw.rt.ledger.feedback_row(rid)["verdict"] == "correct"
    assert gw.rt.ledger.db.execute("SELECT COUNT(*) FROM feedback").fetchone()[0] == 1
    # Nothing in the feedback table is text or a key.
    dump = json.dumps([dict(r) for r in gw.rt.ledger.db.execute("SELECT * FROM feedback")])
    assert ACME_KEY not in dump and "charged" not in dump


@pytest.mark.req("FR-13.30", "INV-2")
def test_correct_verdict_never_creates_example(gw_env):
    gw = _gateway(gw_env)
    c = TestClient(gw.app)
    student, teacher = _cascade(gw)
    body = _chat(c, "My card was charged twice")
    assert body["ratchet"]["served_by"] == "student" and not body["ratchet"]["stored"]
    rid = body["ratchet"]["request_id"]
    calls = (len(student.calls), len(teacher.calls))

    r = _feedback(c, {"request_id": rid, "verdict": "correct"})
    assert r.status_code == 200 and r.json()["correction"] is None
    assert _examples(gw.rt.ledger) == []  # the student's answer never becomes a training example
    assert (len(student.calls), len(teacher.calls)) == calls  # a verdict calls no model
    # A 'correct' verdict cannot carry a label, even the student's own text.
    refused = _feedback(
        c, {"request_id": rid, "verdict": "correct", "corrected": A, "messages": _msgs("My card was charged twice")}
    )
    assert refused.status_code == 400 and refused.json()["error"]["param"] == "corrected"
    assert _examples(gw.rt.ledger) == []
    stats = c.get("/ratchet/stats").json()
    assert stats["feedback"]["verdicts"] == 1 and stats["feedback"]["correct"] == 1 and stats["feedback"]["human_examples"] == 0
    task = next(t for t in stats["tasks"] if t["task"] == TRIAGE)
    assert (task["human_feedback"], task["human_agreement"]) == (1, 1.0)


@pytest.mark.req("FR-13.30", "FR-3.54", "INV-2", "INV-3", "INV-12")
def test_corrected_feedback_stored_with_human_provenance_redacted_and_verified(gw_env):
    gw = _gateway(gw_env, RATCHET_API_KEYS=f"acme:{ACME_KEY}")
    c = TestClient(gw.app)
    _cascade(gw)
    text = "Please mail jane.roe@example.com about my locked account"
    rid = _chat(c, text, ACME_KEY)["ratchet"]["request_id"]
    r = _feedback(c, {"request_id": rid, "verdict": "incorrect", "corrected": C, "messages": _msgs(text)}, ACME_KEY)
    assert r.status_code == 200, r.text
    assert r.json()["correction"]["stored"] is True and r.json()["correction"]["replaced"] is None

    (ex,) = _examples(gw.rt.ledger)
    assert (ex["teacher_model"], ex["license"], ex["verified"], ex["origin"], ex["client"]) == (
        "human",
        "customer-provided labels",
        1,
        "feedback",
        "acme",
    )
    # Redacted like a teacher answer: the messages and the label lose the address, and the placeholders are counted.
    assert "jane.roe@example.com" not in ex["messages"] + ex["completion"] and "[EMAIL]" in ex["completion"]
    assert ex["redactions"] == 2 and json.loads(ex["messages"]) == redact_messages(_msgs(text))
    # The hash a teacher answer of this request would have, and the split that hash gives (invariant 3).
    assert ex["hash"] == example_hash(TRIAGE, redact_messages(_msgs(text)))
    assert ex["split"] == gw.rt.ledger._split(ex["hash"])
    # It is a training example like any other: Night School reads it (provenance human) when its split is train.
    assert licenses.lookup_source("human") is licenses.SOURCES["human"]
    rows = dataset.rows(gw.rt.ledger, ex["split"])
    assert [r["provenance"]["teacher"] for r in rows] == ["human"]
    fb = c.get("/ratchet/stats").json()["feedback"]
    assert (fb["corrections_sent"], fb["corrections_stored"], fb["human_examples"]) == (1, 1, 1)
    assert gw.rt.ledger.feedback_row(rid)["example_hash"] == ex["hash"]


@pytest.mark.req("FR-3.54", "FR-13.30")
def test_human_overrides_teacher_on_same_hash(gw_env):
    gw = _gateway(gw_env)
    c = TestClient(gw.app)
    _cascade(gw, threshold=0.99)  # every request escalates: the teacher's answer (B) is stored
    text = "Where is my refund?"
    first = _chat(c, text)
    assert first["ratchet"]["served_by"] == "teacher" and first["ratchet"]["stored"]
    (teacher_row,) = _examples(gw.rt.ledger)
    assert teacher_row["teacher_model"] == OPEN_TEACHER and teacher_row["seen"] == 1

    r = _feedback(
        c, {"request_id": first["ratchet"]["request_id"], "verdict": "incorrect", "corrected": A, "messages": _msgs(text)}
    )
    assert r.json()["correction"] == {"stored": True, "not_stored": None, "split": teacher_row["split"], "replaced": "teacher"}
    (row,) = _examples(gw.rt.ledger)
    assert (row["hash"], row["teacher_model"], row["completion"], row["verified"]) == (teacher_row["hash"], "human", A, 1)
    assert row["seen"] == 1  # the same request: not counted twice

    # The teacher answers the same request again later: the human label stays, the request is counted.
    _chat(c, text)
    (row,) = _examples(gw.rt.ledger)
    assert (row["teacher_model"], row["completion"], row["seen"]) == ("human", A, 2)

    # The request's conditions are part of the example: a JSON-mode request's correction must send them to replace it.
    fmt = {"type": "json_object"}
    jm = _chat(c, "Refund status?", response_format=fmt)
    plain = _feedback(
        c,
        {"request_id": jm["ratchet"]["request_id"], "verdict": "incorrect", "corrected": A, "messages": _msgs("Refund status?")},
    )
    assert plain.json()["correction"]["replaced"] is None  # a variant without conditions, in the same split
    with_fmt = _feedback(
        c,
        {
            "request_id": jm["ratchet"]["request_id"],
            "verdict": "incorrect",
            "corrected": A,
            "messages": _msgs("Refund status?"),
            "response_format": fmt,
        },
    )
    assert with_fmt.status_code == 409  # the request already has a correction of other conditions
    jm2 = _chat(c, "Refund status?", response_format=fmt)
    fixed = _feedback(
        c,
        {
            "request_id": jm2["ratchet"]["request_id"],
            "verdict": "incorrect",
            "corrected": A,
            "messages": _msgs("Refund status?"),
            "response_format": fmt,
        },
    )
    assert fixed.json()["correction"]["replaced"] == "teacher"
    variants = [r for r in _examples(gw.rt.ledger) if json.loads(r["messages"]) == _msgs("Refund status?")]
    assert {(r["params"], r["teacher_model"]) for r in variants} == {
        (None, "human"),
        (json.dumps({"response_format": fmt}), "human"),
    }
    assert len({r["split"] for r in variants}) == 1


@pytest.mark.req("FR-3.54", "INV-1")
def test_teacher_model_human_refused_at_startup(tmp_path):
    def resolver(v):
        return RecordingProvider(A), v.adapter_uri

    for name in ("human", "Human", "HUMAN"):
        with pytest.raises(licenses.LicenseError, match="provenance source"):
            Ratchet(_settings(teacher_model=name), Ledger(":memory:"), RecordingProvider(A), resolver)
    with pytest.raises(licenses.LicenseError, match="provenance source"):
        Ratchet(_settings(teacher_fallback_model="human"), Ledger(":memory:"), RecordingProvider(A), resolver)
    # The registry never holds it, and configuration cannot add it (invariant 1): human labels are not a teacher.
    assert licenses.lookup("human") is None and "human" not in licenses.REGISTRY
    with pytest.raises(licenses.LicenseError):
        licenses.require_distillable("human")
    path = tmp_path / "teachers.json"
    path.write_text(json.dumps([{"model": "Human", "license": "Apache-2.0", "url": "https://example.com/l"}]))
    with pytest.raises(licenses.LicenseConfigError, match="reserved for customer-provided labels"):
        licenses.install(path)
    assert "Human" not in licenses.REGISTRY
    # A stored row that says `human` is read as a human label by Night School, never as a teacher.
    assert licenses.lookup_source("human").name == "customer-provided labels" and licenses.lookup_source("nobody") is None


@pytest.mark.req("FR-13.30", "INV-16")
def test_feedback_body_is_checked(gw_env):
    gw = _gateway(gw_env)
    c = TestClient(gw.app)
    _cascade(gw)
    rid = _chat(c, "Hello")["ratchet"]["request_id"]
    cases = [
        ({"request_id": rid}, "verdict", "missing_required_parameter"),
        ({"verdict": "correct"}, "request_id", "missing_required_parameter"),
        ({"request_id": rid, "verdict": "maybe"}, "verdict", "invalid_value"),
        ({"request_id": "abc", "verdict": "correct"}, "request_id", "invalid_value"),
        ({"request_id": rid, "verdict": "incorrect", "corection": A}, "corection", "unknown_parameter"),
        ({"request_id": rid, "verdict": "incorrect", "corrected": 3}, "corrected", "invalid_type"),
        ({"request_id": rid, "verdict": "incorrect", "corrected": A}, "messages", "missing_required_parameter"),
        ({"request_id": rid, "verdict": "incorrect", "messages": _msgs("Hello")}, "messages", "invalid_value"),
        ({"request_id": rid, "verdict": "incorrect", "corrected": A, "messages": []}, "messages", "invalid_value"),
        (
            {
                "request_id": rid,
                "verdict": "incorrect",
                "corrected": json.dumps({"category": "weather"}),
                "messages": _msgs("Hello"),
            },
            "corrected",
            "invalid_correction",
        ),
        (
            {"request_id": rid, "verdict": "incorrect", "corrected": "", "messages": _msgs("Hello")},
            "corrected",
            "invalid_correction",
        ),
    ]
    for body, param, code in cases:
        r = _feedback(c, body)
        assert r.status_code == 400, (body, r.text)
        assert (r.json()["error"]["param"], r.json()["error"]["code"]) == (param, code), body
    assert c.post(FEEDBACK, content=b"[1]", headers={"Content-Type": "application/json"}).status_code == 400
    assert (
        c.post(FEEDBACK, content=b"{nope", headers={"Content-Type": "application/json"}).json()["error"]["code"] == "invalid_json"
    )
    image = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://example.com/a.png"}}]}]
    r = _feedback(c, {"request_id": rid, "verdict": "incorrect", "corrected": A, "messages": image})
    assert r.status_code == 400 and r.json()["error"]["code"] == "unsupported_content_type"
    # Nothing was recorded by a refused call.
    assert gw.rt.ledger.feedback_row(rid) is None and _examples(gw.rt.ledger) == []


@pytest.mark.req("FR-13.30", "FR-13.1", "INV-10")
def test_correction_storage_rules_still_apply(gw_env):
    gw = _gateway(gw_env, RATCHET_API_KEYS=f"acme:{ACME_KEY},globex:{GLOBEX_KEY}", RATCHET_KEY_LIMITS="globex:store=0")
    c = TestClient(gw.app)
    _cascade(gw)

    def correct(text: str, key: str, **request: Any) -> dict[str, Any]:
        rid = _chat(c, text, key, **request)["ratchet"]["request_id"]
        extra = {k: v for k, v in request.items() if k in ("user", "safety_identifier")}
        r = _feedback(c, {"request_id": rid, "verdict": "incorrect", "corrected": A, "messages": _msgs(text), **extra}, key)
        assert r.status_code == 200, r.text
        return r.json()["correction"]

    assert correct("opted out", ACME_KEY, store=False)["not_stored"] == "request"  # the request's own opt-out
    assert correct("key opted out", GLOBEX_KEY)["not_stored"] == "key"  # RATCHET_KEY_LIMITS store=0
    # An end user registered as no-train after the request: the correction carries the user, so the opt-out applies.
    rid = _chat(c, "user ticket", ACME_KEY, user="u-1")["ratchet"]["request_id"]
    assert c.post(NO_TRAIN, json={"end_user": "u-1"}, headers=_auth(ACME_KEY)).status_code == 200
    base = {"request_id": rid, "verdict": "incorrect", "corrected": A, "messages": _msgs("user ticket")}
    missing = _feedback(c, base, ACME_KEY)
    assert missing.status_code == 400 and missing.json()["error"]["code"] == "end_user_required"
    wrong = _feedback(c, {**base, "user": "u-2"}, ACME_KEY)
    assert wrong.status_code == 400 and wrong.json()["error"]["code"] == "end_user_mismatch"
    assert _feedback(c, {**base, "user": "u-1"}, ACME_KEY).json()["correction"]["not_stored"] == "end_user"
    # A pinned task's requests (invariant 10) and a switched-off feature store nothing either.
    tasks.register(Task("contracts", pinned=True), "api")
    rid = c.post(
        CHAT, json={"messages": _msgs("clause"), "metadata": {"ratchet_task": "contracts"}}, headers=_auth(ACME_KEY)
    ).json()
    pinned = _feedback(
        c,
        {"request_id": rid["ratchet"]["request_id"], "verdict": "incorrect", "corrected": "x", "messages": _msgs("clause")},
        ACME_KEY,
    )
    assert pinned.json()["correction"]["not_stored"] == "pinned"
    gw.rt.settings.feedback_corrections = False
    assert correct("feature off", ACME_KEY)["not_stored"] == "corrections_off"
    gw.rt.settings.feedback_corrections = True
    gw.rt.settings.store_examples = False
    assert correct("zero retention", ACME_KEY)["not_stored"] == "global"
    gw.rt.settings.store_examples = True
    assert _examples(gw.rt.ledger) == []  # every verdict above was kept, no correction stored
    assert gw.rt.ledger.db.execute("SELECT COUNT(*) FROM feedback WHERE corrected_present=1 AND stored=0").fetchone()[0] == 6


@pytest.mark.req("FR-13.30", "FR-13.2")
def test_correction_needs_every_end_user_identifier(gw_env):
    """A request that sent both `user` and `safety_identifier` logs the second identifier's keyed hash too
    (`requests.end_user_secondary`), and a correction must send both: with only one, a no-train registration made with
    the other (the client's, or an admin's for every client) could not be found and the prompt would be stored."""
    admin_key = "ops-feedback-admin-key-0123456789ab"
    gw = _gateway(gw_env, RATCHET_API_KEYS=f"acme:{ACME_KEY}", RATCHET_ADMIN_KEYS=f"ops:{admin_key}")
    c = TestClient(gw.app)
    _cascade(gw)
    led, hasher = gw.rt.ledger, gw.rt.end_users

    def ask(text: str, **ids: str) -> tuple[str, dict[str, Any]]:
        rid = _chat(c, text, ACME_KEY, **ids)["ratchet"]["request_id"]
        return rid, {"request_id": rid, "verdict": "incorrect", "corrected": A, "messages": _msgs(text)}

    rid, base = ask("both ids", user="u-1", safety_identifier="s-1")
    row = led.feedback_request(rid)
    assert (row["end_user"], row["end_user_secondary"]) == (hasher.hash("s-1", "acme"), hasher.hash("u-1", "acme"))
    assert c.post(NO_TRAIN, json={"end_user": "u-1"}, headers=_auth(ACME_KEY)).status_code == 200  # by `user`
    for ids in ({"safety_identifier": "s-1"}, {}):  # the documented pattern: only the identifier the request is under
        r = _feedback(c, {**base, **ids}, ACME_KEY)
        assert r.status_code == 400 and r.json()["error"]["code"] == "end_user_required", r.text
        assert "both" in r.json()["error"]["message"]
    wrong = _feedback(c, {**base, "safety_identifier": "s-1", "user": "u-2"}, ACME_KEY)
    assert wrong.status_code == 400 and wrong.json()["error"]["code"] == "end_user_mismatch"
    assert _feedback(c, {**base, "safety_identifier": "s-1", "user": "u-1"}, ACME_KEY).json()["correction"] == {
        "stored": False,
        "not_stored": "end_user",
        "split": None,
        "replaced": None,
    }
    # An admin's registration for every client, made with `user` after the request: the same.
    rid2, base2 = ask("admin scope", user="u-3", safety_identifier="s-3")
    assert c.post(NO_TRAIN, json={"end_user": "u-3"}, headers=_auth(admin_key)).status_code == 200
    assert _feedback(c, {**base2, "safety_identifier": "s-3"}, ACME_KEY).json()["error"]["code"] == "end_user_required"
    both = _feedback(c, {**base2, "safety_identifier": "s-3", "user": "u-3"}, ACME_KEY).json()
    assert both["correction"]["not_stored"] == "end_user"
    assert _examples(led) == []
    # One identifier on the request: one is enough. Two, and nobody opted out: both are needed and the correction stored.
    _, single = ask("one id", safety_identifier="s-4")
    assert led.feedback_request(single["request_id"])["end_user_secondary"] is None
    assert _feedback(c, {**single, "safety_identifier": "s-4"}, ACME_KEY).json()["correction"]["stored"] is True
    _, pair = ask("two ids", user="u-5", safety_identifier="s-5")
    assert _feedback(c, {**pair, "user": "u-5"}, ACME_KEY).json()["error"]["code"] == "end_user_required"
    assert _feedback(c, {**pair, "user": "u-5", "safety_identifier": "s-5"}, ACME_KEY).json()["correction"]["stored"] is True
    # The second hash is identifying data like the first: never exported, and an erasure by either identifier clears both.
    lines = [json.loads(line) for line in privacy.export_lines(led, "requests")]
    assert lines and all("end_user" not in r and "end_user_secondary" not in r for r in lines)
    out = privacy.erase(led, client="acme", end_user_raw="u-1", hasher=hasher, actor="ops", reason="test", dry_run=False)
    assert out["requests_unlinked"] == 1
    cleared = led.feedback_request(rid)
    assert (cleared["end_user"], cleared["end_user_secondary"]) == (None, None)


@pytest.mark.req("FR-13.30")
def test_correction_conflicts_and_fixes(gw_env):
    gw = _gateway(gw_env)
    c = TestClient(gw.app)
    _cascade(gw)
    rid = _chat(c, "Card declined")["ratchet"]["request_id"]
    base = {"request_id": rid, "verdict": "incorrect", "messages": _msgs("Card declined")}
    assert _feedback(c, {**base, "corrected": A}).json()["correction"]["stored"] is True
    # The same request's correction again (a fix): it replaces its own label, and the request still counts once.
    fixed = _feedback(c, {**base, "corrected": B}).json()
    assert fixed["updated"] is True and fixed["correction"]["replaced"] == "human"
    (row,) = _examples(gw.rt.ledger)
    assert (row["completion"], row["seen"]) == (B, 1)
    other = _feedback(c, {**base, "corrected": A, "messages": _msgs("Another prompt")})
    assert other.status_code == 409 and other.json()["error"]["code"] == "correction_exists"
    correct = _feedback(c, {"request_id": rid, "verdict": "correct"})
    assert correct.status_code == 409 and gw.rt.ledger.feedback_row(rid)["verdict"] == "incorrect"
    assert len(_examples(gw.rt.ledger)) == 1


@pytest.mark.req("FR-3.54", "FR-13.30", "FR-13.31", "INV-16")
def test_label_precedence_human_teacher_import(tmp_path):
    led = Ledger(":memory:")
    s = _settings()
    t = tasks.get(TRIAGE)

    def stored(text: str) -> dict[str, Any]:
        return dict(led.example(example_hash(TRIAGE, _msgs(text))))

    # A client's correction beats the teacher; another client's correction does not replace it; a teacher never does.
    led.add_example(TRIAGE, _msgs("p1"), B, OPEN_TEACHER, "OpenMDW-1.1", "")
    first = feedback.store_human_example(led, t, _msgs("p1"), A, "acme", s)
    assert first.replaced == "teacher" and stored("p1")["completion"] == A
    second = feedback.store_human_example(led, t, _msgs("p1"), D, "globex", s)
    assert (second.stored, second.reason) == (False, "human_label_exists") and stored("p1")["completion"] == A
    led.add_example(TRIAGE, _msgs("p1"), B, OPEN_TEACHER, "OpenMDW-1.1", "")  # the router stores a teacher duplicate
    assert stored("p1")["completion"] == A and stored("p1")["teacher_model"] == "human"
    # An operator's import beats every client correction; a later correction does not replace the import.
    rep = _import(led, _ft("p1", B))
    assert rep.replaced_human == 1 and stored("p1")["completion"] == B and stored("p1")["origin"] == "import"
    assert feedback.store_human_example(led, t, _msgs("p1"), A, "acme", s).reason == "human_label_exists"
    # A later import replaces the earlier one (the operator's latest word); a teacher import never replaces a label.
    assert _import(led, _ft("p1", D)).replaced_human == 1 and stored("p1")["completion"] == D
    rep = _import(led, _ft("p1", A), source=f"teacher:{OPEN_TEACHER}", attest=None)
    assert rep.duplicates == 1 and stored("p1")["completion"] == D
    # Retention purged a human label: a later teacher answer restores the text as the teacher's, unverified.
    h = example_hash(TRIAGE, _msgs("p1"))
    led.purge_examples({h: True}, "retention")
    led.add_example(TRIAGE, _msgs("p1"), B, OPEN_TEACHER, "OpenMDW-1.1", "")
    assert (stored("p1")["teacher_model"], stored("p1")["verified"], stored("p1")["origin"]) == (OPEN_TEACHER, 0, None)
    # An erased example is never stored again, by a correction either.
    privacy.erase(led, hashes=[h], actor="ops", reason="test", dry_run=False)
    assert feedback.store_human_example(led, t, _msgs("p1"), A, "acme", s).reason == "erased"


@pytest.mark.req("FR-3.54", "FR-13.31", "INV-16")
def test_store_label_is_atomic_across_processes(tmp_path):
    """The importer CLI and the gateway share the ledger file: between `store_label`'s read of the stored label and its
    write, another process cannot write (a teacher answer stored in between would otherwise make the import's human
    label a mere duplicate), and a refusal leaves no transaction open."""
    path = str(tmp_path / "shared.db")
    importer_side, gateway_side = Ledger(path), Ledger(path)
    gateway_side.db.execute("PRAGMA busy_timeout = 0")  # fail at once instead of waiting for the lock
    blocked: list[str] = []
    read = importer_side.example

    def example(h: str) -> Any:
        if not blocked:  # on store_label's first read, the gateway stores the teacher's answer to the same prompt
            try:
                gateway_side.add_example(TRIAGE, _msgs("shared prompt"), B, OPEN_TEACHER, "OpenMDW-1.1", "")
                blocked.append("not blocked")
            except sqlite3.OperationalError as e:
                blocked.append(str(e))
        return read(h)

    importer_side.example = example  # type: ignore[method-assign]
    rep = _import(importer_side, _ft("shared prompt", A))
    assert blocked and "locked" in blocked[0] and rep.inserted == 1
    # The gateway's answer arrives after the import committed: it bumps `seen` (the import was no occurrence of the
    # request, so this is its first), and the human label stands.
    assert gateway_side.add_example(TRIAGE, _msgs("shared prompt"), B, OPEN_TEACHER, "OpenMDW-1.1", "") is False
    row = gateway_side.example(example_hash(TRIAGE, _msgs("shared prompt")))
    assert (row["teacher_model"], row["completion"], row["seen"]) == ("human", A, 1)
    # A refused golden-only row (its prompt is a training row) commits nothing and holds no lock afterwards.
    led_settings = _settings()
    trained = _prompt_in("train", "atomic")
    gateway_side.add_example(TRIAGE, _msgs(trained), B, OPEN_TEACHER, "OpenMDW-1.1", "")
    prepared = feedback.prepare(tasks.get(TRIAGE), _msgs(trained), A)
    assert isinstance(prepared, feedback.Prepared)
    out = feedback.store_label(importer_side, prepared, source="human", settings=led_settings, origin="import", golden_only=True)
    assert out.reason == "trained" and not importer_side.db.in_transaction
    assert gateway_side.add_example(TRIAGE, _msgs("after"), B, OPEN_TEACHER, "OpenMDW-1.1", "") is True
    # An error inside the transaction rolls it back.
    with pytest.raises(RuntimeError), importer_side.atomic():
        importer_side.db.execute("UPDATE examples SET seen = 99")
        raise RuntimeError("boom")
    assert not importer_side.db.in_transaction and 99 not in {r["seen"] for r in gateway_side.examples()}


@pytest.mark.req("FR-13.30", "INV-26")
def test_human_precision_alerts_and_never_moves_a_release(gw_env):
    gw = _gateway(gw_env)
    c = TestClient(gw.app)
    _cascade(gw)
    live = gw.rt.ledger.live()
    floor, need = gw.rt.gate.policy.live_floor, gw.rt.gate.policy.min_audits
    for i in range(need + 5):
        rid = _chat(c, f"ticket {i}")["ratchet"]["request_id"]
        assert _feedback(c, {"request_id": rid, "verdict": "incorrect"}).status_code == 200
        alerts = [e for e in gw.rt.ledger.events() if e["kind"] == feedback.HUMAN_ALERT]
        assert len(alerts) == (1 if i + 1 >= need else 0), i  # only from min_audits verdicts on, and only once
    assert f"v{live.id} human precision 0.0%" in alerts[0]["detail"] and f"{floor:.0%}" in alerts[0]["detail"]
    fb = c.get("/ratchet/stats").json()["feedback"]
    (v,) = fb["by_version"]
    assert (v["version"], v["verdicts"], v["correct"], v["precision"]) == (live.id, need + 5, 0, 0.0) and v["upper"] < floor
    # Never a promotion or rollback signal: the version is still live and the gate wrote no decision about it.
    assert gw.rt.ledger.live().id == live.id
    assert not [e for e in gw.rt.ledger.events() if e["kind"] in ("rollback", "alert", "retired")]
    # Teacher-served answers count toward the task's agreement, not toward a version's precision.
    gw.rt.ledger.set_version(live.id, "live", 100)
    _cascade(gw, threshold=0.99)
    rid = _chat(c, "escalated ticket")["ratchet"]["request_id"]
    _feedback(c, {"request_id": rid, "verdict": "correct"})
    task = next(t for t in c.get("/ratchet/stats").json()["tasks"] if t["task"] == TRIAGE)
    assert task["human_feedback"] == need + 6 and task["human_agreement"] == pytest.approx(1 / (need + 6))
    assert sum(v["verdicts"] for v in c.get("/ratchet/stats").json()["feedback"]["by_version"]) == need + 5
    assert gw.rt.ledger.meta_get(feedback.ALERT_META.format(live.id)) is not None  # a restart does not alert again
    policy = ReleasePolicy(min_audits=need)
    assert feedback.check_human_alert(gw.rt.ledger, live.id, policy) is False


@pytest.mark.req("FR-13.30", "INV-26")
def test_human_alert_is_anytime_valid():
    """The alert is checked after every verdict, so its rule is the mixture e-value against 'precision >= live_floor'
    reaching 1 / ALERT_ALPHA, never a fixed-sample Wilson bound looked at again and again: 14 correct of 20 puts the
    Wilson upper bound under a 0.92 floor, but is not yet strong evidence."""
    led = Ledger(":memory:")
    policy = ReleasePolicy(min_audits=20, live_floor=0.92)
    request = {"task": TRIAGE, "student_version": 1, "served_by": "student", "route": "student"}
    verdicts = ["correct"] * 14 + ["incorrect"] * 6
    for i, verdict in enumerate(verdicts):
        led.record_feedback(f"req_{i:032x}", "acme", verdict, request)
    assert wilson(14, 20)[1] < policy.live_floor
    assert feedback.check_human_alert(led, 1, policy) is False
    n, ok, fired = 20, 14, False
    while not fired:
        led.record_feedback(f"req_{n:032x}", "acme", "incorrect", request)
        n += 1
        expected = log_e_bernoulli(ok, n, policy.live_floor, "below") >= math.log(1 / feedback.ALERT_ALPHA)
        fired = feedback.check_human_alert(led, 1, policy)
        assert fired == expected, n
    (event,) = [e for e in led.events() if e["kind"] == feedback.HUMAN_ALERT]
    assert "anytime-valid" in event["detail"] and f"n={n}" in event["detail"]


@pytest.mark.req("FR-13.30", "FR-13.4")
def test_feedback_outlives_folded_request_rows(gw_env):
    """Request retention folds request rows into sums; the verdicts keep their statistics (the feedback row copies the
    request's task, version and route), and a request whose row is gone is a 404 like an unknown one."""
    gw = _gateway(gw_env)
    c = TestClient(gw.app)
    _cascade(gw)
    rid = _chat(c, "Folded ticket")["ratchet"]["request_id"]
    assert _feedback(c, {"request_id": rid, "verdict": "incorrect"}).status_code == 200
    assert gw.rt.ledger.fold_requests(time.time() + 1) == 1
    assert gw.rt.ledger.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 0
    stats = c.get("/ratchet/stats").json()
    (v,) = stats["feedback"]["by_version"]
    assert (v["verdicts"], v["correct"]) == (1, 0)
    task = next(t for t in stats["tasks"] if t["task"] == TRIAGE)
    assert (task["requests"], task["human_feedback"], task["human_agreement"]) == (1, 1, 0.0)
    again = _feedback(c, {"request_id": rid, "verdict": "correct"})
    assert again.status_code == 404 and again.json()["error"]["code"] == "request_not_found"
    assert gw.rt.ledger.feedback_row(rid)["verdict"] == "incorrect"


@pytest.mark.req("FR-13.30")
def test_concurrent_verdicts_keep_one_row_and_one_label(gw_env):
    gw = _gateway(gw_env)
    c = TestClient(gw.app)
    _cascade(gw)
    rid = _chat(c, "Concurrent ticket")["ratchet"]["request_id"]
    body = {"request_id": rid, "verdict": "incorrect", "corrected": A, "messages": _msgs("Concurrent ticket")}
    results: list[int] = []

    def send() -> None:
        results.append(TestClient(gw.app).post(FEEDBACK, json=body).status_code)

    threads = [threading.Thread(target=send) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [200] * 8
    (row,) = _examples(gw.rt.ledger)
    assert row["seen"] == 1 and row["completion"] == A
    assert gw.rt.ledger.db.execute("SELECT COUNT(*) FROM feedback").fetchone()[0] == 1


def test_old_ledger_file_gains_feedback_schema(tmp_path):
    """An older ledger file (no feedback table, no request id or label columns) is migrated on open."""
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE requests (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, day INTEGER, task TEXT)")
    db.execute("CREATE TABLE examples (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, day INTEGER, task TEXT, hash TEXT UNIQUE)")
    db.execute("INSERT INTO examples (ts, day, task, hash) VALUES (1, 1, 't', 'h')")
    db.commit()
    db.close()
    led = Ledger(str(path))
    cols = {t: {r["name"] for r in led.db.execute(f"PRAGMA table_info({t})")} for t in ("requests", "examples")}
    assert {"request_id", "end_user_secondary"} <= cols["requests"] and {"verified", "golden_only"} <= cols["examples"]
    assert all(led.db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (t,)).fetchone() for t in FEEDBACK_TABLES)
    assert dict(led.db.execute("SELECT verified, golden_only FROM examples").fetchone()) == {"verified": 0, "golden_only": 0}
    names = {r["name"] for r in led.db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"requests_request_id", "feedback_version", "requests_end_user_secondary"} <= names


# ---------- import ----------
@pytest.mark.req("FR-13.31", "FR-3.54", "INV-12", "INV-16")
def test_import_openai_finetune_jsonl():
    led = Ledger(":memory:")
    multi_turn = {
        "messages": [
            {"role": "system", "content": "Triage the ticket."},
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello, what happened?", "weight": 0},
            {"role": "user", "content": "Charged twice, mail jane.roe@example.com"},
            {"role": "assistant", "content": [{"type": "text", "text": A}], "weight": 1},
        ]
    }
    rows = [
        _ft("Card charged twice", A),
        multi_turn,
        _ft("Card charged twice", A),  # the same row again: stored once
        _ft("Card charged twice", B),  # the same prompt with another label: the first one is kept
        _ft("Weather?", json.dumps({"category": "weather", "priority": "low"})),  # outside the task's vocabulary
        {"prompt": "legacy", "completion": "format"},
        {
            "messages": [
                *_msgs("tool"),
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}],
                },
            ]
        },
        {"messages": [*_msgs("weightless"), {"role": "assistant", "content": A, "weight": 0}]},
        {"messages": [{"role": "user", "content": "no answer"}]},
        b"{not json",
        _ft("Locked out", C, tools=[{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}]),
    ]
    rep = _import(led, *rows)
    assert (rep.rows, rep.inserted, rep.in_file_duplicates, rep.in_file_conflicts) == (11, 3, 1, 1)
    assert dict(rep.invalid) == {
        "invalid label": 1,
        "unknown keys": 1,
        "tool call answer": 1,
        "weight 0": 1,
        "no answer": 1,
        "not json": 1,
    }
    assert {p["line"] for p in rep.problems} >= {5, 6, 7, 8, 9, 10}
    stored = _examples(led)
    # An import is no occurrence of the request: `seen` 0, so it never counts toward FREQUENT (invariant 3).
    assert {(r["teacher_model"], r["license"], r["verified"], r["origin"], r["seen"]) for r in stored} == {
        ("human", "customer-provided labels", 1, "import", 0)
    }
    # The multi-turn row keeps its earlier turns as context, its label is the last answer, and its address is redacted.
    mt = next(r for r in stored if "Charged twice" in r["messages"])
    assert mt["completion"] == A and "jane.roe@example.com" not in mt["messages"] and mt["redactions"] == 1
    assert [m["role"] for m in json.loads(mt["messages"])] == ["system", "user", "assistant", "user"]
    assert "weight" not in mt["messages"]
    # The default teacher allowlist forwards no tools, so a live request's teacher answer would have no tools condition:
    # the imported row hashes like it, and dedupes against it.
    locked = next(r for r in stored if "Locked out" in r["messages"])
    assert locked["params"] is None and locked["hash"] == example_hash(TRIAGE, _msgs("Locked out"))
    (event,) = [e for e in led.events() if e["kind"] == "import"]
    assert event["detail"].startswith("3 of 11 rows imported into ticket-triage as human labels: 3 new")
    assert "attested: 'written by our support leads'" in event["detail"] and f"sha256 {rep.sha256[:16]}" in event["detail"]
    assert "Card charged twice" not in event["detail"]
    # Importing the same file again stores nothing new and counts nothing twice.
    again = _import(led, *rows)
    assert again.inserted == 0 and again.replaced_human == 3 and [r["seen"] for r in _examples(led)] == [0, 0, 0]


@pytest.mark.req("FR-13.31", "INV-1")
def test_import_refuses_closed_model_source(tmp_path, capsys):
    led = Ledger(":memory:")
    for model, clause in [
        ("gpt-4o", "Section 3.3(e)"),
        ("openai/o3-mini", "Section 3.3(e)"),
        ("o1", "Section 3.3(e)"),
        ("o4-mini", "Section 3.3(e)"),
        ("azure-gpt-4o", "Section 3.3(e)"),
        ("claude-3-5-sonnet", "Section D.4(a)"),
        ("anthropic/claude-sonnet-4", "Section D.4(a)"),
        ("gemini-2.5-pro", "Gemini API Terms"),
        ("google/gemini-1.5-flash", "Gemini API Terms"),
        ("davinci-002", "Section 3.3(e)"),  # OpenAI's legacy names and the o-series after o4 (licenses.closed_family)
        ("o5-mini", "Section 3.3(e)"),
        ("openrouter/auto", "router alias"),  # the answering model may be closed
    ]:
        with pytest.raises(importer.ImportRefused, match=re.escape(clause)) as e:
            _import(led, _ft("x", A), source=f"teacher:{model}", attest=None)
        assert "invariant 1" in str(e.value), model
    for source, why in [
        ("teacher:unknown/model", "Unknown teacher"),
        ("teacher:human", "provenance source"),
        ("teacher:", "--source"),
        ("people", "--source"),
    ]:
        with pytest.raises(importer.ImportRefused, match=re.escape(why)):
            _import(led, _ft("x", A), source=source, attest=None)
    assert _examples(led) == [] and led.events() == []
    # The CLI exits with the clause too, before it reads the file.
    data = tmp_path / "rows.jsonl"
    data.write_bytes(_jsonl(_ft("x", A)).getvalue())
    with pytest.raises(SystemExit, match=re.escape("Section 3.3(e) forbids using Output to develop competing AI models")):
        importer.main([str(data), "--task", TRIAGE, "--source", "teacher:gpt-4o", "--db", str(tmp_path / "l.db")])
    # An open teacher in the registry is accepted, and its rows keep its own license (not verified).
    rep = _import(led, _ft("x", A), source=f"teacher:{OPEN_TEACHER}", attest=None)
    (row,) = _examples(led)
    assert rep.inserted == 1 and (row["teacher_model"], row["license"], row["verified"]) == (OPEN_TEACHER, "OpenMDW-1.1", 0)


@pytest.mark.req("FR-13.31")
def test_import_requires_attest_for_human():
    led = Ledger(":memory:")
    for attest in (None, "", "   "):
        with pytest.raises(importer.ImportRefused, match="--attest"):
            _import(led, _ft("x", A), attest=attest)
    with pytest.raises(importer.ImportRefused, match="one line"):
        _import(led, _ft("x", A), attest="line one\nline two")
    with pytest.raises(importer.ImportRefused, match="limited"):
        _import(led, _ft("x", A), attest="x" * (importer.MAX_ATTEST_CHARS + 1))
    assert _examples(led) == []
    assert _import(led, _ft("x", A)).inserted == 1  # with an attestation it imports


@pytest.mark.req("FR-13.31", "INV-10", "FR-13.1")
def test_import_refuses_pinned_task():
    led = Ledger(":memory:")
    with pytest.raises(importer.ImportRefused, match="invariant 10"):
        _import(led, _ft("x", "plan"), task="research-plan")
    tasks.register(Task("private", store=False), "api")
    with pytest.raises(importer.ImportRefused, match="no-store"):
        _import(led, _ft("x", "y"), task="private")
    with pytest.raises(importer.ImportRefused, match="no-store"):
        _import(led, _ft("x", A), settings=_settings(no_store_tasks=TRIAGE))
    with pytest.raises(importer.ImportRefused, match="zero retention"):
        _import(led, _ft("x", A), settings=_settings(store_examples=False))
    with pytest.raises(importer.ImportRefused, match="not registered"):
        _import(led, _ft("x", A), task="ticket-triag")
    assert _examples(led) == [] and led.events() == []
    # A task pinned after rows were imported keeps them out of Night School (invariant 10 at read time).
    _import(led, _ft("x", "an answer"), task="default")
    tasks.register(Task("default", pinned=True), "file")
    assert dataset.rows(led, "train") == dataset.rows(led, "golden") == []
    assert feedback.store_human_example(led, tasks.get("default"), _msgs("y"), "z", None, _settings()).reason == "pinned"


@pytest.mark.req("FR-4.50", "FR-13.31", "INV-3", "INV-6")
def test_golden_only_rows_never_trained():
    led = Ledger(":memory:")
    s = _settings()
    train_prompt, golden_prompt = _prompt_in("train", "held"), _prompt_in("golden", "held")
    rep = _import(led, _ft(train_prompt, A), _ft(golden_prompt, B), golden_only=True)
    assert rep.inserted == 2
    assert {(r["split"], r["golden_only"]) for r in _examples(led)} == {("golden", 1)}  # golden whatever the hash says
    # Seen many times, and asked again under other conditions: still never a training row, with every variant.
    for _ in range(dataset.FREQUENT + 2):
        led.add_example(TRIAGE, _msgs(train_prompt), A, OPEN_TEACHER, "OpenMDW-1.1", "")
    led.add_example(TRIAGE, _msgs(train_prompt), A, OPEN_TEACHER, "OpenMDW-1.1", "", params={"stop": ["\n"]})
    led.add_example(
        TRIAGE, _msgs(train_prompt), A, OPEN_TEACHER, "OpenMDW-1.1", "", params={"response_format": {"type": "json_object"}}
    )
    variant = led.example(example_hash(TRIAGE, _msgs(train_prompt), {"response_format": {"type": "json_object"}}))
    assert variant["split"] == "train" and not variant["golden_only"]  # its own split says train...
    assert dataset.rows(led, "train") == []  # ...and still it is held out
    golden = dataset.rows(led, "golden")
    assert len(golden) == 4 and {r["provenance"]["teacher"] for r in golden} == {"human", OPEN_TEACHER}
    assert dataset.effective_split(led.example(example_hash(TRIAGE, _msgs(train_prompt))), led.prompt_counts(TRIAGE)) == "golden"
    # A prompt that may already have been trained on cannot become held-out evidence.
    led.add_example(TRIAGE, _msgs(_prompt_in("train", "seen")), A, OPEN_TEACHER, "OpenMDW-1.1", "")
    frequent = _prompt_in("golden", "often")
    for _ in range(dataset.FREQUENT):
        led.add_example(TRIAGE, _msgs(frequent), A, OPEN_TEACHER, "OpenMDW-1.1", "")
    rep = _import(led, _ft(_prompt_in("train", "seen"), B), _ft(frequent, B), golden_only=True)
    assert rep.inserted == 0 and dict(rep.refused) == {"trained": 2}
    # Night School trains on none of them, and the BOM counts the human references in the golden set.
    trainer = RecordingTrainer()
    for i in range(60):
        led.add_example(TRIAGE, _msgs(_prompt_in("train", "bulk", skip=i)), A, OPEN_TEACHER, "OpenMDW-1.1", "")
    rep = run_night(
        led, trainer, RecordingEvaluator({"precision": 0.0, "coverage": 0.0}), ReleaseGate(led), "base", min_new_examples=1
    )
    assert rep.trained
    trained = {json.dumps(r["messages"][:-1]) for r in trainer.calls[0].rows}
    assert json.dumps(_msgs(train_prompt)) not in trained and json.dumps(_msgs(golden_prompt)) not in trained
    assert led.version_bom(rep.version)["manifest"]["golden"]["human_labels"] == 2
    assert feedback.store_human_example(led, tasks.get(TRIAGE), _msgs(train_prompt), C, "acme", s).reason == "human_label_exists"


@pytest.mark.req("FR-13.31", "FR-13.30", "INV-3")
def test_labels_that_are_no_occurrence_never_reach_frequent():
    """An import, and a correction of a request already counted, are no occurrence of the request: a new example starts
    at `seen` 0, so a twice-seen golden prompt does not reach FREQUENT through a label (only real requests move a prompt
    to training), and an imported prompt needs FREQUENT real requests like any other."""
    led = Ledger(":memory:")
    s, t = _settings(), tasks.get(TRIAGE)
    json_mode = {"response_format": {"type": "json_object"}}
    p = _prompt_in("golden", "occ")
    for _ in range(2):  # two real requests
        led.add_example(TRIAGE, _msgs(p), A, OPEN_TEACHER, "OpenMDW-1.1", "")
    rep = _import(led, {"task": TRIAGE, "messages": _ft(p, B)["messages"], "params": json_mode})  # a variant
    assert rep.inserted == 1 and led.example(example_hash(TRIAGE, _msgs(p), json_mode))["seen"] == 0
    assert (len(dataset.rows(led, "golden")), len(dataset.rows(led, "train"))) == (2, 0)
    assert led.prompt_counts(TRIAGE)[example_hash(TRIAGE, _msgs(p))] == 2
    # A correction of a request whose teacher answer was stored in another variant: no occurrence either.
    out = feedback.store_human_example(led, t, _msgs(p), D, "acme", s, params={"stop": ["END"]}, bump=False)
    assert out.inserted and led.example(out.hash)["seen"] == 0 and dataset.rows(led, "train") == []
    # The third real request is what makes the prompt frequent: every variant moves to training (INV-3).
    led.add_example(TRIAGE, _msgs(p), A, OPEN_TEACHER, "OpenMDW-1.1", "", params=json_mode)
    assert dataset.rows(led, "golden") == [] and len(dataset.rows(led, "train")) == 2  # the stop variant stays out
    # An imported new prompt counts no request; FREQUENT real requests of it are needed, as for any prompt.
    q = _prompt_in("golden", "fresh")
    _import(led, _ft(q, B))
    for n in range(dataset.FREQUENT):
        assert any(json.dumps(_msgs(q))[:-1] in json.dumps(r["messages"]) for r in dataset.rows(led, "golden")), n
        led.add_example(TRIAGE, _msgs(q), A, OPEN_TEACHER, "OpenMDW-1.1", "")
    assert led.example(example_hash(TRIAGE, _msgs(q)))["seen"] == dataset.FREQUENT
    assert any(r["provenance"]["hash"] == example_hash(TRIAGE, _msgs(q)) for r in dataset.rows(led, "train"))
    # The export reports the real count (0 for an imported row nobody asked yet).
    _import(led, _ft(_prompt_in("golden", "unasked"), A))
    assert 0 in {json.loads(line)["seen"] for line in privacy.export_lines(led, "examples")}


@pytest.mark.req("FR-4.50", "FR-13.31", "INV-3")
def test_golden_only_check_reads_the_ledger_at_each_row():
    """The golden-only check runs per row inside the transaction that stores it, against the ledger as it is then: a
    variant the gateway stores in the train split while a long import runs keeps that row out."""
    led = Ledger(":memory:")
    first, second = _prompt_in("golden", "live"), _prompt_in("train", "live")

    class Gateway(io.BytesIO):
        """The file being read while the gateway stores the teacher's answer to the second row's prompt."""

        calls = 0

        def readline(self, size: int | None = -1) -> bytes:
            self.calls += 1
            if self.calls == 2:
                led.add_example(TRIAGE, _msgs(second), B, OPEN_TEACHER, "OpenMDW-1.1", "")
            return super().readline(-1 if size is None else size)

    stream = Gateway(_jsonl(_ft(first, A), _ft(second, A)).getvalue())
    options = {"task": TRIAGE, "source": "human", "attest": "ours", "golden_only": True}
    rep = importer.run_import(led, _settings(), stream, file_name="golden.jsonl", **options)
    assert (rep.inserted, dict(rep.refused)) == (1, {"trained": 1})
    row = led.example(example_hash(TRIAGE, _msgs(second)))
    assert (row["teacher_model"], row["golden_only"], row["split"]) == (OPEN_TEACHER, 0, "train")


@pytest.mark.req("FR-3.54", "FR-13.31", "INV-14")
def test_bom_lists_human_source():
    led = Ledger(":memory:")
    led.add_example(TRIAGE, _msgs("teacher row"), A, OPEN_TEACHER, "OpenMDW-1.1", licenses.OPEN_MDW.url)
    _import(led, _ft("imported row", B), _ft("second import", A))
    t = tasks.get(TRIAGE)
    feedback.store_human_example(led, t, _msgs("corrected row"), C, "acme", _settings())
    train = [{**r, "provenance": {**r["provenance"]}} for r in dataset.rows(led, "train") + dataset.rows(led, "golden")]
    vid = _version(led, "candidate", {"precision": 1.0})
    m = bom.manifest(led.version(vid), train, [])
    assert [s["teacher"] for s in m["sources"]] == [OPEN_TEACHER]  # human labels are not a teacher...
    (label,) = m["label_sources"]  # ...they are listed apart
    assert label == {
        "source": "human",
        "license": "customer-provided labels",
        "note": licenses.SOURCES["human"].note,
        "distill_ok": True,
        "examples": 3,
        "origins": {"feedback": 1, "import": 2},
    }
    assert m["training"]["targets"] == bom.TARGETS_WITH_LABELS and m["golden"]["human_labels"] == 0
    text = m["notices"][0]["text"]
    assert "## Human labels" in text and "customer-provided labels" in text and "teacher outputs only" not in text
    assert notices.bom_notice(bom.document(m)) == text
    # An adapter trained on teacher answers alone gets the NOTICE it got before human labels existed.
    teacher_only = [r for r in train if r["provenance"]["teacher"] == OPEN_TEACHER]
    plain = bom.manifest(led.version(vid), teacher_only, [])
    assert plain["label_sources"] == [] and plain["training"]["targets"] == bom.TARGETS
    assert plain["notices"][0]["text"] == notices.render("base-model", plain["sources"], version=plain["generator"])
    assert "## Human labels" not in plain["notices"][0]["text"] and "teacher outputs only" in plain["notices"][0]["text"]
    # The ledger's bill of materials lists them apart too, and the fingerprint covers every example.
    whole = led.bill_of_materials()
    assert {s["teacher_model"] for s in whole["sources"]} == {OPEN_TEACHER}
    assert sum(s["n"] for s in whole["label_sources"]) == 3 and {s["teacher_model"] for s in whole["label_sources"]} == {"human"}
    # The training job's own NOTICE lists them too (train.write_metadata).
    assert notices.label_sources(train)[0]["examples"] == 3 and notices.sources(train)[0]["teacher"] == OPEN_TEACHER


@pytest.mark.req("FR-13.31")
def test_import_ratchet_train_jsonl_roundtrip(tmp_path):
    src = Ledger(":memory:")
    for i in range(5):
        src.add_example(
            TRIAGE,
            _msgs(f"exported {i}"),
            A,
            OPEN_TEACHER,
            "OpenMDW-1.1",
            "",
            params={"response_format": {"type": "json_object"}} if i == 0 else None,
        )
    rows = dataset.rows(src, "train") + dataset.rows(src, "golden")
    path = dataset.write_jsonl(rows, tmp_path / "train.jsonl")
    dst = Ledger(":memory:")
    with path.open("rb") as f:
        rep = importer.run_import(dst, _settings(teacher_params="all"), f, task=TRIAGE, source=f"teacher:{OPEN_TEACHER}")
    assert rep.inserted == 5 and {r["hash"] for r in dst.examples()} == {r["hash"] for r in src.examples()}
    # A teacher's exported outputs never come back in as human labels, nor under another task.
    with path.open("rb") as f:
        rep = importer.run_import(Ledger(":memory:"), _settings(), f, task=TRIAGE, source="human", attest="ours")
    assert rep.inserted == 0 and dict(rep.invalid) == {"provenance mismatch": 5}
    with path.open("rb") as f:
        rep = importer.run_import(Ledger(":memory:"), _settings(), f, task="default", source=f"teacher:{OPEN_TEACHER}")
    assert dict(rep.invalid) == {"task mismatch": 5}


@pytest.mark.req("FR-13.31")
def test_import_night_school_training_file(tmp_path):
    # Night School's train.jsonl holds prompt-completion rows (dataset.write_training, FR-8.42); the importer reads them
    # as it reads write_jsonl's rows, with the same task and provenance checks.
    src = Ledger(":memory:")
    for i in range(3):
        src.add_example(TRIAGE, _msgs(f"trained {i}"), A, OPEN_TEACHER, "OpenMDW-1.1", "")
    path = dataset.write_training(dataset.rows(src, "train") + dataset.rows(src, "golden"), tmp_path / "train.jsonl")
    dst = Ledger(":memory:")
    with path.open("rb") as f:
        rep = importer.run_import(dst, _settings(), f, task=TRIAGE, source=f"teacher:{OPEN_TEACHER}")
    assert rep.inserted == 3 and {r["hash"] for r in dst.examples()} == {r["hash"] for r in src.examples()}
    with path.open("rb") as f:
        rep = importer.run_import(Ledger(":memory:"), _settings(), f, task=TRIAGE, source="human", attest="ours")
    assert rep.inserted == 0 and dict(rep.invalid) == {"provenance mismatch": 3}


@pytest.mark.req("FR-13.31")
def test_import_cli_dry_run_and_apply(tmp_path, monkeypatch, capsys):
    for k, v in PINNED_ENV.items():
        monkeypatch.setenv(k, v)
    db = tmp_path / "ledger.db"
    monkeypatch.setenv("RATCHET_DB", str(db))
    data = tmp_path / "labels.jsonl"
    data.write_bytes(_jsonl(_ft("cli one", A), _ft("cli two", B), b"", b"   ", _ft("bad", "no json")).getvalue())
    args = [str(data), "--task", TRIAGE, "--source", "human", "--attest", "ours", "--client", "acme"]
    # A dry run against a ledger file that does not exist (a mistyped path) is refused and creates nothing.
    with pytest.raises(SystemExit, match="no ledger file"):
        importer.main([*args, "--dry-run"])
    assert not db.exists()
    # A ledger file written before `requests.end_user_secondary` existed: the dry run opens it read-only and migrates
    # its in-memory copy, never the file.
    Ledger(str(db)).db.close()
    old = sqlite3.connect(db)
    cols = ", ".join(r[1] for r in old.execute("PRAGMA table_info(requests)") if r[1] != "end_user_secondary")
    old.execute(f"CREATE TABLE requests_old AS SELECT {cols} FROM requests")
    old.execute("DROP TABLE requests")
    old.execute("ALTER TABLE requests_old RENAME TO requests")
    old.commit()
    old.close()
    before = db.read_bytes()
    assert importer.main([*args, "--dry-run"]) == 0
    assert db.read_bytes() == before
    out = capsys.readouterr().out
    report = json.loads(out[: out.rindex("}") + 1])
    assert (report["dry_run"], report["rows"], report["inserted"], report["invalid"]) == (True, 3, 2, 1)
    assert "dry run: nothing was written" in out
    led = Ledger(str(db))
    assert led.examples() == [] and led.events() == []
    led.db.close()
    assert importer.main(args) == 0
    assert json.loads(capsys.readouterr().out)["inserted"] == 2
    led = Ledger(str(db))
    assert {(r["client"], r["origin"]) for r in led.examples()} == {("acme", "import")} and len(led.examples()) == 2
    assert [e["kind"] for e in led.events()] == ["import"]
    led.db.close()
    with pytest.raises(SystemExit, match="cannot read"):
        importer.main([str(tmp_path / "missing.jsonl"), "--task", TRIAGE, "--source", "human", "--attest", "x"])
    with pytest.raises(SystemExit, match="--client"):
        importer.main([*args[:-1], "not a name"])
    # A line over the body limit is counted and skipped without being kept in memory.
    monkeypatch.setenv("RATCHET_MAX_BODY_BYTES", "400")
    big = tmp_path / "big.jsonl"
    big.write_bytes(_jsonl(_ft("x" * 2000, A), _ft("small", A)).getvalue())
    assert importer.main([str(big), "--task", TRIAGE, "--source", "human", "--attest", "x", "--dry-run"]) == 0
    report = json.loads(capsys.readouterr().out.split("\ndry run")[0])
    assert report["invalid_by_reason"] == {"too long": 1} and report["inserted"] == 1


def test_counts_as_train_reads_human_labels():
    """A purged human training row still counts as a training row (FR-4.4's new-data count), a golden-only row never."""
    led = Ledger(":memory:")
    text = _prompt_in("train", "purge")
    feedback.store_human_example(led, tasks.get(TRIAGE), _msgs(text), A, None, _settings())
    row = led.example(example_hash(TRIAGE, _msgs(text)))
    assert dataset.counts_as_train(row, led.prompt_counts(TRIAGE)) is True
    _import(led, _ft(_prompt_in("golden", "purge"), A), golden_only=True)
    held = next(r for r in led.examples() if r["golden_only"])
    assert dataset.counts_as_train(held, led.prompt_counts(TRIAGE)) is False


def test_export_carries_label_flags(gw_env):
    gw = _gateway(gw_env)
    c = TestClient(gw.app)
    _import(gw.rt.ledger, _ft("exported human", A), golden_only=True, settings=gw.rt.settings)
    lines = [json.loads(x) for x in c.get("/ratchet/admin/export/examples").text.splitlines()]
    (row,) = lines
    assert (row["verified"], row["golden_only"], row["provenance"]["teacher"], row["provenance"]["origin"]) == (
        True,
        True,
        "human",
        "import",
    )
    assert redact("x") == "x"
