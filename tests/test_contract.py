"""Contract tests: what clients, the dashboard and the license rules rely on, pinned before `Ratchet.handle` and
`gateway.chat` are rewritten.

- Gateway: `/v1/models`, task selection (the header wins over `metadata`), request validation, `/ratchet/bom`, the
  `/ratchet/stats` keys the dashboard reads (tests/fixtures/stats_keys.json: add keys, never remove them) and the demo
  controls refusing live mode.
- Cascade: one student call per request with the production sampling parameters, the confidence formula, the threshold
  boundary, the audit rate, cost accounting to the token, and a consistent ledger under concurrent requests. The
  client's generation parameters as each call receives them (student, serving teacher, audit, arbitration, pinned),
  the conditions stored with an example, the reasoning headroom, and `RequestMeta.storable`.
- Licensing: closed teachers refused at startup (OpenAI's o-series too), examples recording their license, and every
  priced model licensed and on the day-1 check list.

Known bugs are strict xfails that name the item fixing them. When it lands they pass, strict mode fails the run, and
the marker comes off.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fakes import OPEN_TEACHER, RecordingProvider, _version
from fastapi.testclient import TestClient

from ratchet import licenses, tasks
from ratchet.confidence import score
from ratchet.config import DEFAULT_PRICES, Settings
from ratchet.ledger import Ledger, example_hash
from ratchet.providers import Completion, GenParams, GenPolicy, approx_tokens
from ratchet.release import wilson
from ratchet.router import ROUTES, USAGE_PARTS, Ratchet, RequestMeta, fingerprint

STATS_KEYS = Path(__file__).resolve().parent / "fixtures" / "stats_keys.json"
CHAT = "/v1/chat/completions"
KEY = "contract-test-key-0123456789abcdef"
TRIAGE = "ticket-triage"
A = json.dumps({"category": "billing", "priority": "high", "reply": "I am checking the charge now."})
B = json.dumps({"category": "refund", "priority": "high", "reply": "Your refund is on its way."})
Z99 = 2.576  # two-sided 99% normal quantile, as in test_canary_traffic_split
# What the gateway tests depend on, pinned so the shell's variables cannot move a result (see `_gateway`).
PINNED_ENV = {
    "RATCHET_API_KEYS": "",
    "RATCHET_TEACHER_MODEL": OPEN_TEACHER,
    "RATCHET_SAMPLES": "3",
    "RATCHET_AUDIT_RATE": "0.05",
    "RATCHET_CONFIDENCE_THRESHOLD": "0.80",
    "RATCHET_GOLDEN_FRACTION": "0.10",
    "RATCHET_BOM_SIGNING_KEY": "",
    "RATCHET_API_KEY_DAILY_REQUESTS": "0",
    "RATCHET_TEACHER_PARAMS": "",
    "RATCHET_STUDENT_PARAMS": "",
    "RATCHET_TEACHER_MAX_TOKENS": "8192",
    "RATCHET_STUDENT_MAX_TOKENS": "1024",
    "RATCHET_TEACHER_REASONING_HEADROOM": "0",
    "RATCHET_ON_TEACHER_FAILURE": "error",
    "RATCHET_TEACHER_FALLBACK_MODEL": "",
    "RATCHET_TEACHER_BREAKER_FAILURES": "5",
}


# ---------- helpers ----------
def _settings(**overrides: Any) -> Settings:
    """Every value these tests depend on, set explicitly, so RATCHET_* variables in the shell cannot move a result."""
    base: dict[str, Any] = {
        "teacher_model": OPEN_TEACHER,
        "samples": 3,
        "audit_rate": 0.0,
        "confidence_threshold": 0.8,
        "teacher_reasoning_tokens": 300,
        "redact_pii": True,
        "teacher_params": "",
        "student_params": "",
        "teacher_max_tokens": 8192,
        "student_max_tokens": 1024,
        "teacher_reasoning_headroom": 0,
        "on_teacher_failure": "error",
        "teacher_fallback_model": "",
        "teacher_breaker_failures": 5,
    }
    return Settings(**{**base, **overrides})


def _cascade(
    student: RecordingProvider,
    teacher: RecordingProvider | None = None,
    *,
    eval: dict[str, Any] | None = None,
    threshold: float = 0.5,
    seed: int = 1,
    **settings: Any,
) -> tuple[Ledger, int, Ratchet, RecordingProvider]:
    """A ledger with one live version (calibrated at `threshold` unless `eval` is given) served by `student`."""
    led = Ledger(":memory:")
    vid = _version(led, "live", {"threshold": threshold} if eval is None else eval)
    teacher = teacher or RecordingProvider(A)
    r = Ratchet(_settings(**settings), led, teacher, lambda v: (student, v.adapter_uri), seed=seed)
    return led, vid, r, teacher


def _gateway(gw_env: Any, **env: str) -> Any:
    """The gateway reloaded in mock mode with `PINNED_ENV`, then `env` on top (restored after the test)."""
    return gw_env(**{**PINNED_ENV, **env})


def _ask(tag: object = 0) -> list[dict[str, Any]]:
    return [{"role": "user", "content": f"I was charged twice this month, order {tag}"}]


def _rows(led: Ledger) -> list[dict[str, Any]]:
    return [dict(r) for r in led.db.execute("SELECT * FROM requests ORDER BY id")]


def _answer(resp: Any) -> str:
    return resp.json()["choices"][0]["message"]["content"]


# ---------- gateway: /v1 ----------
@pytest.mark.req("FR-1.2")
def test_v1_models_lists_ratchet(gw_env):
    c = TestClient(_gateway(gw_env).app)
    r = c.get("/v1/models")
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "list"
    assert {"id": "ratchet", "object": "model"}.items() <= next(m for m in body["data"] if m["id"] == "ratchet").items()

    # With API keys on, the model list needs a key like every other /v1 route, and answers the same with one.
    keyed = TestClient(_gateway(gw_env, RATCHET_API_KEYS=f"acme:{KEY}").app)
    assert keyed.get("/v1/models").status_code == 401
    r = keyed.get("/v1/models", headers={"Authorization": f"Bearer {KEY}"})
    assert r.status_code == 200 and "ratchet" in {m["id"] for m in r.json()["data"]}


@pytest.mark.req("FR-1.3")
def test_task_from_metadata_and_header_precedence(gw_env):
    gw = _gateway(gw_env)
    c = TestClient(gw.app)
    msgs = gw.rt.stream.sample(1).messages()
    cases = [  # (extra body fields, headers, the task the ledger must record)
        ({}, {}, "default"),
        ({"metadata": {"ratchet_task": TRIAGE}}, {}, TRIAGE),
        ({}, {"X-Ratchet-Task": TRIAGE}, TRIAGE),
        ({"metadata": {"ratchet_task": "page-read"}}, {"X-Ratchet-Task": TRIAGE}, TRIAGE),
        ({"metadata": {"ratchet_task": TRIAGE}}, {"X-Ratchet-Task": "page-read"}, "page-read"),
        ({"metadata": {"user_id": "u-1"}}, {}, "default"),
    ]
    for extra, headers, _ in cases:
        r = c.post(CHAT, json={"messages": msgs, **extra}, headers=headers)
        assert r.status_code == 200, (extra, headers, r.text)
    logged = [row["task"] for row in _rows(gw.rt.ledger)]
    assert logged == [want for _, _, want in cases]


@pytest.mark.req("FR-1.5")
@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="missing"),
        pytest.param({"messages": None}, id="null"),
        pytest.param({"messages": []}, id="empty"),
        pytest.param({"messages": "hi"}, id="string"),
        pytest.param({"messages": {"role": "user", "content": "hi"}}, id="object"),
        pytest.param({"messages": 3}, id="number"),
        pytest.param({"messages": [{"role": "robot", "content": "hi"}]}, id="unknown-role"),
        pytest.param({"messages": [{"role": "user", "content": 5}]}, id="number-content"),
        pytest.param({"messages": [{"role": "user", "content": None}]}, id="null-user-content"),
    ],
)
def test_missing_or_malformed_messages(gw_env, body):
    gw = _gateway(gw_env)
    c = TestClient(gw.app, raise_server_exceptions=False)
    r = c.post(CHAT, json=body)
    assert r.status_code == 400, r.text
    assert gw.rt.ledger.totals()["requests"] == 0  # refused before the cascade: nothing logged, nothing billed


@pytest.mark.req("FR-1.5")
def test_messages_items_must_be_objects_400(gw_env):
    gw = _gateway(gw_env)
    c = TestClient(gw.app, raise_server_exceptions=False)
    for items in (["hi"], [None], [1], [["user", "hi"]]):
        r = c.post(CHAT, json={"messages": items})
        assert r.status_code == 400, (items, r.status_code)
    assert gw.rt.ledger.totals()["requests"] == 0


@pytest.mark.req("FR-1.3")
def test_bad_metadata_is_400(gw_env):
    gw = _gateway(gw_env)
    c = TestClient(gw.app, raise_server_exceptions=False)
    msgs = gw.rt.stream.sample(1).messages()
    for metadata in ("x", ["ratchet_task"], {"ratchet_task": 5}):
        r = c.post(CHAT, json={"messages": msgs, "metadata": metadata})
        assert r.status_code == 400, (metadata, r.status_code)
    assert gw.rt.ledger.totals()["requests"] == 0


@pytest.mark.req("FR-1.1")
def test_text_content_parts_answered_in_mock(gw_env):
    gw = _gateway(gw_env)
    c = TestClient(gw.app, raise_server_exceptions=False)
    msgs = gw.rt.stream.sample(1).messages()
    plain = c.post(CHAT, json={"messages": msgs})
    assert plain.status_code == 200
    parts = [*msgs[:-1], {"role": "user", "content": [{"type": "text", "text": msgs[-1]["content"]}]}]
    r = c.post(CHAT, json={"messages": parts})
    assert r.status_code == 200, r.text
    assert _answer(r) == _answer(plain)
    (example,) = gw.rt.ledger.examples()  # the same request in another shape: one example, seen twice
    assert example["seen"] == 2


# ---------- gateway: provenance and the dashboard's stats ----------
@pytest.mark.req("FR-3.5", "US-7")
def test_bom_endpoint_counts_by_split_and_fingerprint_changes(gw_env):
    gw = _gateway(gw_env)
    c = TestClient(gw.app)
    led = gw.rt.ledger
    lic = licenses.require_distillable(gw.rt.settings.teacher_model)

    def send(msgs: list[dict[str, Any]]) -> None:
        assert c.post(CHAT, json={"messages": msgs}).status_code == 200

    # Cold start: the teacher answers every request and each new one becomes an example. Send distinct tickets until
    # both splits hold some, keeping one unseen ticket back.
    unique: dict[str, list[dict[str, Any]]] = {}
    for _ in range(2000):
        item = gw.rt.stream.sample(1)
        unique.setdefault(item.text, item.messages())
    *pool, held_back = unique.values()
    sent: list[list[dict[str, Any]]] = []
    for msgs in pool:
        send(msgs)
        sent.append(msgs)
        if len(sent) >= 20 and led.examples("golden") and led.examples("train"):
            break
    assert led.examples("golden") and led.examples("train"), "the seeded stream should fill both splits"

    def per_split(doc: dict[str, Any]) -> Counter[str]:
        counts: Counter[str] = Counter()
        for src in doc["sources"]:
            assert (src["teacher_model"], src["license"], src["license_url"]) == (gw.rt.settings.teacher_model, lic.name, lic.url)
            counts[src["split"]] += src["n"]
        return counts

    bom = c.get("/ratchet/bom").json()
    by_split = per_split(bom)
    assert by_split == {"train": len(led.examples("train")), "golden": len(led.examples("golden"))}
    assert sum(by_split.values()) == len(sent)

    # The fingerprint is the documented SHA-256 over every example hash in sorted order (docs/API.md).
    hashes = sorted(r["hash"] for r in led.examples())
    fp = bom["examples_sha256"]
    assert fp == hashlib.sha256("".join(hashes).encode()).hexdigest()
    assert c.get("/ratchet/bom").json()["examples_sha256"] == fp  # stable between reads
    assert c.get("/ratchet/stats").json()["bill_of_materials"]["examples_sha256"] == fp

    send(sent[0])  # a repeat raises `seen` and changes neither the counts nor the fingerprint
    again = c.get("/ratchet/bom").json()
    assert (again["examples_sha256"], per_split(again)) == (fp, by_split)
    assert max(r["seen"] for r in led.examples()) == 2

    send(held_back)  # a new example changes the fingerprint and adds one to its split
    new = c.get("/ratchet/bom").json()
    assert new["examples_sha256"] != fp
    assert sum(per_split(new).values()) == len(sent) + 1


TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "number": (int, float),
    "bool": (bool,),
    "null": (type(None),),
    "object": (dict,),
    "list": (list,),
}
# The keys this contract started with: stats_keys.json may grow, but it must keep these.
STATS_MINIMUM = [
    "totals.saved_usd",
    "totals.savings_pct",
    "days[].cost_per_1k",
    "days[].baseline_per_1k",
    "days[].student_share",
    "days[].escalation_rate",
    "days[].audit_agreement",
    "versions[].state",
    "versions[].eval",
    "versions[].bom",
    "events[]",
    "bill_of_materials.sources[]",
    "tasks[].pinned",
    "clients[]",
    "privacy",
    "security",
]


def _has_type(value: Any, spec: str) -> bool:
    for name in spec.split("|"):
        if name == "number" and isinstance(value, bool):  # bool is an int subclass, but not a number in JSON
            continue
        if isinstance(value, TYPES[name]):
            return True
    return False


def _spec_paths(spec: dict[str, Any], prefix: str = "") -> Iterator[str]:
    for key, sub in spec.items():
        path = f"{prefix}.{key}" if prefix else key
        yield path
        if isinstance(sub, dict):
            yield from _spec_paths(sub, path)


def _spec_errors(spec: dict[str, Any], where: str = "keys") -> list[str]:
    """The fixture itself: leaves name known types, and a list key ([]) or a nested object maps keys to specs."""
    errors = []
    for key, sub in spec.items():
        if isinstance(sub, dict):
            errors += _spec_errors(sub, f"{where}.{key}")
        elif key.endswith("[]") or not isinstance(sub, str) or not all(t in TYPES for t in sub.split("|")):
            errors.append(f"{where}.{key}: {sub!r} is not a type spec")
    return errors


def _contract_errors(spec: dict[str, Any], value: Any, where: str = "stats", *, allow_empty: bool = False) -> list[str]:
    """Every key in `spec`, recursively, with its type. A `[]` key is a list whose every item is checked; an empty list
    is an error unless `allow_empty`, because it would leave its items unchecked."""
    if not isinstance(value, dict):
        return [f"{where}: expected an object, got {type(value).__name__}"]
    errors = []
    for key, sub in spec.items():
        name = key.removesuffix("[]")
        path = f"{where}.{name}"
        if name not in value:
            errors.append(f"{path}: missing")
            continue
        got = value[name]
        if key.endswith("[]"):
            if not isinstance(got, list):
                errors.append(f"{path}: expected a list, got {type(got).__name__}")
            elif not got and not allow_empty:
                errors.append(f"{path}: empty, so its items went unchecked")
            for i, item in enumerate(got if isinstance(got, list) else []):
                errors += _contract_errors(sub, item, f"{path}[{i}]", allow_empty=allow_empty)
        elif isinstance(sub, dict):
            errors += _contract_errors(sub, got, path, allow_empty=allow_empty)
        elif not _has_type(got, sub):
            errors.append(f"{path}: expected {sub}, got {type(got).__name__} {got!r:.60}")
    return errors


@pytest.mark.req("FR-7.1", "FR-7.2", "FR-7.3", "FR-7.4", "FR-7.7", "US-9")
def test_stats_contract(gw_env):
    spec = json.loads(STATS_KEYS.read_text(encoding="utf-8"))["keys"]
    assert _spec_errors(spec) == []
    lost = sorted(set(STATS_MINIMUM) - set(_spec_paths(spec)))
    assert not lost, f"stats_keys.json lost {lost}: add keys, never remove them"

    gw = _gateway(gw_env, RATCHET_API_KEYS=f"acme:{KEY}")
    c = TestClient(gw.app)
    # A fresh gateway, as live mode looks at first boot: every key is there and the lists are empty.
    fresh = c.get("/ratchet/stats").json()
    assert _contract_errors(spec, fresh, allow_empty=True) == []
    assert fresh["days"] == fresh["versions"] == fresh["clients"] == []

    # Two simulated days and nights (a trained version with a BOM, events, examples, student-served traffic) and one
    # request with a key: now every list has items, and each item is checked.
    for _ in range(2):
        gw.rt.simulate_day(600, "tickets")
    msgs = gw.rt.stream.sample(gw.rt.ledger.day).messages()
    assert c.post(CHAT, json={"messages": msgs}, headers={"Authorization": f"Bearer {KEY}"}).status_code == 200
    stats = c.get("/ratchet/stats").json()
    assert _contract_errors(spec, stats) == []

    # The receipts add up (US-9): totals, days, tasks and clients describe the same requests and dollars.
    t = stats["totals"]
    assert t["saved_usd"] > 0 and t["saved_usd"] == pytest.approx(t["baseline_usd"] - t["cost_usd"])
    assert t["savings_pct"] == pytest.approx(1 - t["cost_usd"] / t["baseline_usd"])
    assert t["requests"] == sum(d["requests"] for d in stats["days"]) == sum(x["requests"] for x in stats["tasks"])
    assert t["cost_usd"] == pytest.approx(sum(d["cost_per_1k"] * d["requests"] / 1000 for d in stats["days"]))
    assert t["baseline_usd"] == pytest.approx(sum(d["baseline_per_1k"] * d["requests"] / 1000 for d in stats["days"]))
    assert [(x["client"], x["requests"]) for x in stats["clients"]] == [("acme", 1)]
    for task in stats["tasks"]:  # every request has a route (FR-2.11)
        assert sum(task["routes"].values()) == task["requests"] and set(task["routes"]) <= set(ROUTES), task
    assert stats["versions"][0]["bom"] == "unsigned"


# ---------- gateway: mock-only controls ----------
@pytest.mark.req("INV-8")
def test_demo_endpoints_are_403_in_live_mode(gw_env, tmp_path):
    gw = _gateway(
        gw_env,
        RATCHET_MODE="live",
        RATCHET_DB=str(tmp_path / "live.db"),
        RATCHET_STUDENT_BASE="",
        NEBIUS_API_KEY="",
        TAVILY_API_KEY="",
    )
    assert gw.rt.settings.mode == "live" and not hasattr(gw.rt, "fleet")
    led = gw.rt.ledger
    before = (led.day, led.events(), led.versions(), led.totals())
    c = TestClient(gw.app)
    for path in (
        "/ratchet/demo/day",
        "/ratchet/demo/day?workload=bogus&requests=1",  # refused for the mode before the arguments are looked at
        "/ratchet/demo/break-next",
        "/ratchet/demo/student-outage",
        "/ratchet/demo/student-outage?seconds=5",
        "/ratchet/demo/reset",
    ):
        r = c.post(path)
        assert r.status_code == 403 and "mock mode" in r.json()["detail"], (path, r.status_code, r.text)
    assert (led.day, led.events(), led.versions(), led.totals()) == before
    assert gw.rt.ledger is led  # reset did not rebuild the live runtime


# ---------- cascade ----------
@pytest.mark.req("FR-2.1", "NFR-1")
def test_student_sampled_once_with_production_params():
    for samples in (3, 5):  # n comes from Settings.samples, not a constant
        student = RecordingProvider(A)
        led, vid, r, teacher = _cascade(student, samples=samples)
        out = r.handle(_ask(), TRIAGE)
        assert (out.served_by, out.version) == ("student", vid)
        assert len(student.calls) == 1 and teacher.calls == []
        call = student.calls[0]
        assert (call.kwargs["n"], call.kwargs["temperature"], call.kwargs["logprobs"]) == (samples, 0.7, True)
        assert call.model == led.version(vid).adapter_uri and call.messages == _ask()

    # Escalated or audited, the student is still asked once (all samples in one request), plus one teacher call.
    for kwargs in ({"threshold": 0.99}, {"audit_rate": 1.0}):
        student = RecordingProvider(A)
        _, _, r, teacher = _cascade(student, **kwargs)
        r.handle(_ask(), TRIAGE)
        assert (len(student.calls), len(teacher.calls)) == (1, 1), kwargs


@pytest.mark.req("FR-2.2")
def test_confidence_formula_exact():
    want = 0.4 * 0.9 + 0.6 * 2 / 3
    probs = [0.6, 0.9, 0.99]
    samples = [Completion(text, "m", 10, 5, 1.0, [math.log(p)] * 5) for text, p in zip([A, A, B], probs, strict=True)]
    s = score(tasks.get(TRIAGE), samples)
    assert s.agreement == 2 / 3
    # The token probability is the best majority sample's: not the minority's 0.99, not the weaker majority 0.6.
    assert s.answer is samples[1] and s.token_prob == pytest.approx(0.9, abs=1e-12)
    assert s.confidence == pytest.approx(want, abs=1e-12)

    # The router reports and logs the same number for the same samples.
    student = RecordingProvider([A, A, B], [math.log(p) for p in probs])
    led, _, r, _ = _cascade(student, threshold=0.5)
    out = r.handle(_ask(), TRIAGE)
    assert out.confidence == pytest.approx(want, abs=1e-12)
    assert _rows(led)[0]["confidence"] == out.confidence
    assert (out.served_by, out.text) == ("student", A)


@pytest.mark.req("FR-2.2")
def test_confidence_without_logprobs_is_agreement():
    s = score(tasks.get(TRIAGE), [Completion(A, "m"), Completion(A, "m"), Completion(B, "m")])
    assert s.token_prob is None and s.confidence == s.agreement == 2 / 3

    # An endpoint that ignores logprobs=True: a unanimous student scores exactly 1.0, a split one its agreement.
    for text, want, served_by in ((A, 1.0, "student"), ([A, A, B], 2 / 3, "teacher")):
        student = RecordingProvider(text, logprob=None)
        _, _, r, _ = _cascade(student, threshold=0.8)
        out = r.handle(_ask(), TRIAGE)
        assert student.calls[0].kwargs["logprobs"] is True  # asked for, not returned
        assert (out.confidence, out.served_by) == (want, served_by)


@pytest.mark.req("FR-2.3", "FR-2.4", "US-3")
def test_threshold_boundary_serves_at_equality_and_escalates_below():
    _, _, probe, _ = _cascade(RecordingProvider(A), threshold=0.0)
    conf = probe.handle(_ask(), TRIAGE).confidence
    assert conf is not None and 0.9 < conf < 1.0
    just_above = math.nextafter(conf, math.inf)  # the smallest threshold this confidence is below

    # The version's calibrated threshold (FR-2.3), then a version without one, which falls back to the global default.
    for setup in ("calibrated", "default"):
        for threshold, served in ((conf, True), (just_above, False)):
            student, teacher = RecordingProvider(A), RecordingProvider(B)
            if setup == "calibrated":
                led, vid, r, _ = _cascade(student, teacher, threshold=threshold, confidence_threshold=0.0)
            else:
                led, vid, r, _ = _cascade(student, teacher, eval={}, confidence_threshold=threshold)
            out = r.handle(_ask(), TRIAGE)
            row = _rows(led)[0]
            assert out.confidence == row["confidence"] == conf, setup
            if served:  # at equality the student serves: no teacher call, nothing stored
                assert (out.served_by, out.version, out.escalated, out.text) == ("student", vid, False, A), setup
                assert teacher.calls == [] and led.examples() == []
            else:  # a hair below, it escalates: the teacher answers once and its answer is stored
                assert (out.served_by, out.version, out.escalated, out.text) == ("teacher", None, True, B), setup
                assert len(teacher.calls) == 1 and [e["completion"] for e in led.examples()] == [B]
            assert (row["served_by"], row["escalated"], row["student_version"]) == (out.served_by, int(not served), vid)


@pytest.mark.req("FR-2.5", "US-4")
def test_audit_rate_zero_and_one():
    n = 40
    student = RecordingProvider(A)
    led, _, r, teacher = _cascade(student, audit_rate=0.0)
    assert all(r.handle(_ask(i), TRIAGE).served_by == "student" for i in range(n))
    assert teacher.calls == [] and led.examples() == [] and not any(row["audited"] for row in _rows(led))

    # Rate 1: the teacher double-checks every served answer; the verdict is logged and its answer stored.
    teacher = RecordingProvider(lambda msgs, _model: A if "same" in msgs[-1]["content"] else B)
    led, vid, r, _ = _cascade(student, teacher, audit_rate=1.0)
    for i in range(n):
        out = r.handle([{"role": "user", "content": f"{'same' if i % 2 else 'other'} answer, order {i}"}], TRIAGE)
        assert out.served_by == "student" and out.text == A  # an audit never changes what the user gets
    rows = _rows(led)
    assert all(row["audited"] == 1 and row["served_by"] == "student" for row in rows)
    assert [row["audit_agree"] for row in rows] == [i % 2 for i in range(n)]
    assert len(teacher.calls) == len(led.examples()) == n
    assert led.version_live_quality(vid, current_stage_only=False) == (n, 0.5)  # what the release gate reads

    # An escalated request is never counted as an audit, even at rate 1, and costs no second teacher call.
    led, _, r, teacher = _cascade(RecordingProvider(A), audit_rate=1.0, threshold=0.99)
    r.handle(_ask(), TRIAGE)
    row = _rows(led)[0]
    assert (row["served_by"], row["escalated"], row["audited"], row["audit_agree"]) == ("teacher", 1, 0, None)
    assert len(teacher.calls) == 1


@pytest.mark.req("FR-2.5", "US-4")
def test_audit_rate_is_respected_statistically():
    n, rate = 2000, 0.2
    student, teacher = RecordingProvider(A), RecordingProvider(A)
    led, _, r, _ = _cascade(student, teacher, audit_rate=rate, seed=11)
    for i in range(n):
        assert r.handle(_ask(i), TRIAGE).served_by == "student"
    audited = sum(row["audited"] for row in _rows(led))
    lo, hi = wilson(audited, n, z=Z99)
    assert lo <= rate <= hi, f"{audited} of {n} audited"
    assert len(teacher.calls) == audited == len(led.examples())


@pytest.mark.req("NFR-3")
def test_cost_includes_audit_shadow_and_arbitration():
    s = _settings()
    student, teacher = s.price("student"), s.price(OPEN_TEACHER)
    # Distinct token counts per model, so a term billed with the wrong tokens, price or multiplicity shows.
    live = student.cost(30, 7 * s.samples)  # one call: the prompt billed once, every sample's output billed
    shadow = student.cost(31, 9 * s.samples)
    call = teacher.cost(120, 45)
    estimate = teacher.cost(30, 7 + s.teacher_reasoning_tokens)  # all-teacher baseline when the teacher never ran

    def run(*, audit=0.0, threshold=0.5, shadow_says=None, live_down=False, cold=False, task=TRIAGE):
        led = Ledger(":memory:")
        models: dict[int, RecordingProvider] = {}
        if not cold:
            vid = _version(led, "live", {"threshold": threshold})
            models[vid] = RecordingProvider(A, in_tokens=30, out_tokens=7, raise_on={f"student-v{vid}"} if live_down else ())
        if shadow_says is not None:
            models[_version(led, "shadow", {"threshold": 0.5})] = RecordingProvider(shadow_says, in_tokens=31, out_tokens=9)
        t = RecordingProvider(A, in_tokens=120, out_tokens=45)
        r = Ratchet(_settings(audit_rate=audit), led, t, lambda v: (models[v.id], v.adapter_uri), seed=1)
        out = r.handle(_ask(), task)
        (row,) = _rows(led)
        assert (row["cost_usd"], row["baseline_cost_usd"]) == (out.cost_usd, out.baseline_usd)
        return out, len(t.calls)

    cases = [  # (name, run(...) arguments, cost, all-teacher baseline, teacher calls)
        ("served", {}, live, estimate, 0),
        ("served and audited", {"audit": 1.0}, live + call, call, 1),
        ("escalated", {"threshold": 0.99}, live + call, call, 1),
        ("student endpoint down", {"live_down": True}, call, call, 1),
        ("cold start", {"cold": True}, call, call, 1),
        ("pinned task", {"task": "research-plan"}, call, call, 1),
        ("shadow agrees", {"shadow_says": A}, live + shadow, estimate, 0),
        ("shadow unsure", {"shadow_says": "not json"}, live + shadow, estimate, 0),
        ("shadow disagrees: arbitration", {"shadow_says": B}, live + shadow + call, estimate, 1),
        ("audit is the shadow's reference", {"audit": 1.0, "shadow_says": B}, live + call + shadow, call, 1),
        ("escalation is the shadow's reference", {"threshold": 0.99, "shadow_says": B}, live + call + shadow, call, 1),
    ]
    for name, kwargs, cost, baseline, calls in cases:
        out, teacher_calls = run(**kwargs)
        assert out.cost_usd == pytest.approx(cost, rel=1e-12, abs=0), name
        assert out.baseline_usd == pytest.approx(baseline, rel=1e-12, abs=0), name
        assert teacher_calls == calls, name


@pytest.mark.req("FR-1.12", "NFR-3")
def test_usage_is_the_served_answer_and_the_breakdown_every_call():
    """On every path, `Result`'s usage is the served answer's, as if its model had answered alone (the student's chosen
    sample, or the teacher's call with its reasoning and cached tokens), and `usage_breakdown` lists every call the
    request made, so `cost_usd` is exactly the price of those calls. The fingerprint names who served."""
    s = _settings()
    student, teacher = s.price("student"), s.price(OPEN_TEACHER)
    sampled = {"in": 30, "out": 7 * s.samples}  # one call: the prompt once, every sample's output
    one = {"in": 30, "out": 7}  # the sample served
    shadowed = {"in": 31, "out": 9 * s.samples}
    call = {"in": 120, "out": 45}
    as_student = (30, 7, 0, 0)
    as_teacher = (120, 45, 40, 100)  # reasoning and cached tokens are the teacher's own counts

    def run(*, audit=0.0, threshold=0.5, shadow_says=None, live_down=False, cold=False, task=TRIAGE):
        led = Ledger(":memory:")
        models: dict[int, RecordingProvider] = {}
        if not cold:
            vid = _version(led, "live", {"threshold": threshold})
            models[vid] = RecordingProvider(A, in_tokens=30, out_tokens=7, raise_on={f"student-v{vid}"} if live_down else ())
        if shadow_says is not None:
            models[_version(led, "shadow", {"threshold": 0.5})] = RecordingProvider(shadow_says, in_tokens=31, out_tokens=9)
        t = RecordingProvider(A, in_tokens=120, out_tokens=45, reasoning_tokens=40, cached_tokens=100)
        r = Ratchet(_settings(audit_rate=audit), led, t, lambda v: (models[v.id], v.adapter_uri), seed=1)
        return r.handle(_ask(), task), led

    cases = [  # (name, run(...) arguments, usage, breakdown without the empty parts)
        ("served", {}, as_student, {"student_samples": sampled, "served": one}),
        ("served and audited", {"audit": 1.0}, as_student, {"student_samples": sampled, "served": one, "audit": call}),
        ("escalated", {"threshold": 0.99}, as_teacher, {"student_samples": sampled, "served": call}),
        ("student endpoint down", {"live_down": True}, as_teacher, {"served": call}),
        ("cold start", {"cold": True}, as_teacher, {"served": call}),
        ("pinned task", {"task": "research-plan"}, as_teacher, {"served": call}),
        ("shadow agrees", {"shadow_says": A}, as_student, {"student_samples": sampled, "served": one, "shadow": shadowed}),
        (
            "shadow unsure",
            {"shadow_says": "not json"},
            as_student,
            {"student_samples": sampled, "served": one, "shadow": shadowed},
        ),
        (
            "shadow disagrees: arbitration",
            {"shadow_says": B},
            as_student,
            {"student_samples": sampled, "served": one, "shadow": shadowed, "arbitration": call},
        ),
        (
            "audit is the shadow's reference",
            {"audit": 1.0, "shadow_says": B},
            as_student,
            {"student_samples": sampled, "served": one, "audit": call, "shadow": shadowed},
        ),
        (
            "escalation is the shadow's reference",
            {"threshold": 0.99, "shadow_says": B},
            as_teacher,
            {"student_samples": sampled, "served": call, "shadow": shadowed},
        ),
    ]
    for name, kwargs, usage, parts in cases:
        out, led = run(**kwargs)
        assert (out.prompt_tokens, out.completion_tokens, out.reasoning_tokens, out.cached_tokens) == usage, name
        assert out.total_tokens == usage[0] + usage[1], name
        assert out.usage_breakdown == {k: parts.get(k) for k in USAGE_PARTS}, name
        b = out.usage_breakdown
        priced = sum(student.cost(b[k]["in"], b[k]["out"]) for k in ("student_samples", "shadow") if b[k])
        priced += sum(teacher.cost(b[k]["in"], b[k]["out"]) for k in ("audit", "arbitration") if b[k])
        if out.served_by == "teacher":  # the student's served sample is already in student_samples
            priced += teacher.cost(b["served"]["in"], b["served"]["out"])
        assert out.cost_usd == pytest.approx(priced, rel=1e-12, abs=0), name
        if out.served_by == "student":
            (v,) = [v for v in led.versions() if v.state == "live"]
            assert out.fingerprint == fingerprint("student", v.adapter_uri, v.id, v.dataset_hash, 0.5), name
        else:
            assert out.fingerprint == fingerprint("teacher", OPEN_TEACHER), name


@pytest.mark.req("FR-1.12")
def test_usage_estimates_missing_counts_and_keeps_details_inside_totals():
    """A model that reports no usage still gives the client counts: about 4 characters a token, from the messages as
    JSON and from the answer as served; the breakdown keeps the zeros the model reported. Reasoning and cached tokens
    never exceed the totals they are part of, whatever an upstream says."""
    led = Ledger(":memory:")
    teacher = RecordingProvider(f"<think>{'x' * 400}</think>{A}", in_tokens=0, out_tokens=0)
    r = Ratchet(_settings(), led, teacher, lambda v: (teacher, v.adapter_uri), seed=1)
    msgs = [{"role": "user", "content": "Ich wurde zweimal belastet, Bestellnummer 4711, bitte prüfen."}]
    out = r.handle(msgs, TRIAGE)  # cold start: the teacher answers
    assert out.prompt_tokens == approx_tokens(json.dumps(msgs, ensure_ascii=False))
    assert (out.text, out.completion_tokens) == (A, approx_tokens(A))  # the answer as served, without the reasoning
    assert out.usage_breakdown["served"] == {"in": 0, "out": 0}
    teacher.text = ""  # an empty answer with no count: no completion tokens
    out = r.handle(_ask(1), "default")
    assert (out.text, out.completion_tokens) == ("", 0) and out.total_tokens == out.prompt_tokens > 0

    teacher = RecordingProvider(A, in_tokens=10, out_tokens=5, reasoning_tokens=300, cached_tokens=64)
    out = Ratchet(_settings(), Ledger(":memory:"), teacher, lambda v: (teacher, v.adapter_uri)).handle(_ask(), TRIAGE)
    assert (out.prompt_tokens, out.completion_tokens, out.reasoning_tokens, out.cached_tokens) == (10, 5, 5, 10)


@pytest.mark.req("NFR-6")
def test_concurrent_requests_keep_the_ledger_consistent():
    """Scout's agents call `handle` from several threads over one SQLite connection: no request may be lost, logged
    twice or billed for another request's path."""
    threads, per = 8, 40
    student, teacher = RecordingProvider(A), RecordingProvider(A)
    led, _, r, _ = _cascade(student, teacher, audit_rate=0.5, seed=5)
    start, errors = threading.Barrier(threads), []

    def work(t: int) -> None:
        start.wait()
        try:
            for i in range(per):
                r.handle(_ask(f"{t}-{i}"), TRIAGE)
        except Exception as e:  # noqa: BLE001 - reported below
            errors.append(e)

    pool = [threading.Thread(target=work, args=(t,)) for t in range(threads)]
    for th in pool:
        th.start()
    for th in pool:
        th.join(timeout=60)
    assert not errors and not any(th.is_alive() for th in pool)

    s = _settings()
    live, call = s.price("student").cost(10, 5 * s.samples), s.price(OPEN_TEACHER).cost(10, 5)
    rows = _rows(led)
    audited = sum(row["audited"] for row in rows)
    assert len(rows) == len(student.calls) == threads * per
    assert len(teacher.calls) == audited == len(led.examples()) and 0 < audited < len(rows)
    for row in rows:
        assert row["cost_usd"] == pytest.approx(live + call if row["audited"] else live)
    assert led.totals()["cost_usd"] == pytest.approx(sum(row["cost_usd"] for row in rows))


def test_each_result_names_its_ledger_row():
    """`Result.request_id` is the id of the row `log_request` wrote for that request (what receipts and replays name
    a request by): on the student, pinned, teacher-only and cold-start paths, and still under concurrent requests."""
    student, teacher = RecordingProvider(A), RecordingProvider(A)
    led, vid, r, _ = _cascade(student, teacher)
    results = [
        r.handle(_ask(1), TRIAGE),
        r.handle([{"role": "user", "content": "plan a search"}], "research-plan"),
        r.teacher_only(_ask(2), TRIAGE),
    ]
    led.set_version(vid, "retired", 0)
    results.append(r.handle(_ask(3), TRIAGE))
    rows = _rows(led)
    assert [res.request_id for res in results] == [row["id"] for row in rows]
    assert [res.route for res in results] == [row["route"] for row in rows] == ["student", "pinned", "pinned", "cold_start"]
    led.set_version(vid, "live", 100)
    out: list[Any] = []
    lock = threading.Lock()

    def work(t: int) -> None:
        for i in range(25):
            res = r.handle(_ask(f"{t}-{i}"), TRIAGE)
            with lock:
                out.append(res)

    pool = [threading.Thread(target=work, args=(t,)) for t in range(4)]
    for th in pool:
        th.start()
    for th in pool:
        th.join(timeout=60)
    new_rows = {row["id"]: row for row in _rows(led)[len(rows) :]}
    assert sorted(res.request_id for res in out) == sorted(new_rows) and len(new_rows) == 100
    assert all(new_rows[res.request_id]["route"] == res.route for res in out)


@pytest.mark.req("FR-2.2", "FR-2.6", "FR-2.14")
def test_empty_json_answer_scores_zero():
    """Was a known bug until field comparators: '{}' is a valid object, so every compare key was None == None and three
    '{}' samples scored 0.98 and were served. The built-in fields are required now, so '{}' is invalid."""
    triage = tasks.get(TRIAGE)
    assert score(triage, [Completion("{}", "m", 10, 2, 1.0, [math.log(0.95)] * 2)] * 3).confidence == 0.0
    assert not triage.agree("{}", "{}")


# ---------- cascade: generation parameters (FR-1.13, ADR 0017) ----------
CLIENT = GenParams(
    max_tokens=50,
    temperature=0.9,
    top_p=0.5,
    stop=("\n\n",),
    seed=7,
    presence_penalty=0.4,
    logit_bias={"11": 5},
    response_format={"type": "json_object"},
    logprobs=True,
    top_logprobs=3,
)
# What each call gets of CLIENT under the default policy.
STUDENT_GETS = GenParams(
    max_tokens=50, stop=("\n\n",), seed=7, logit_bias={"11": 5}, response_format={"type": "json_object"}, top_logprobs=3
)
# Every standard field as sent (max_tokens already under the 8,192 cap), the logprobs included: the response returns them
# (FR-1.14). Only the tool-call fields stay off by default.
SERVE_GETS = CLIENT
CHECK_GETS = GenParams(max_tokens=50, temperature=0.0, stop=("\n\n",), response_format={"type": "json_object"})


def _teacher_calls(teacher: RecordingProvider) -> list[tuple[float, bool, GenParams]]:
    return [(c.kwargs["temperature"], c.kwargs["logprobs"], c.kwargs["params"]) for c in teacher.calls]


@pytest.mark.req("FR-1.13", "FR-2.1", "FR-2.5", "FR-2.9", "INV-1")
def test_each_call_gets_the_parameters_its_role_allows():
    # A confident student, audited: the student samples as in production under the client's shaping fields; the audit
    # is deterministic under the same conditions.
    student = RecordingProvider(A)
    led, vid, r, teacher = _cascade(student, audit_rate=1.0)
    assert r.handle(_ask(), TRIAGE, params=CLIENT).served_by == "student"
    (call,) = student.calls
    assert call.kwargs == {"n": 3, "temperature": 0.7, "logprobs": True, "params": STUDENT_GETS}
    assert [c.model for c in teacher.calls] == [OPEN_TEACHER]  # whatever the client asked for (INV-1)
    assert _teacher_calls(teacher) == [(0.0, False, CHECK_GETS)]

    # Escalated: the teacher's answer is served, so it gets everything the client sent, at the client's temperature.
    student, teacher = RecordingProvider(A), RecordingProvider(A)
    _, _, r, _ = _cascade(student, teacher, threshold=0.99)
    assert r.handle(_ask(), TRIAGE, params=CLIENT).route == "confidence"
    assert _teacher_calls(teacher) == [(0.9, True, SERVE_GETS)]

    # Cold start, a pinned task and a teacher-only request serve the teacher's answer the same way.
    teacher = RecordingProvider(A)
    r = Ratchet(_settings(), Ledger(":memory:"), teacher, lambda v: (RecordingProvider(A), v.adapter_uri))
    assert r.handle(_ask(), TRIAGE, params=CLIENT).route == "cold_start"
    assert r.handle(_ask(), "research-plan", params=CLIENT).route == "pinned"
    assert r.teacher_only(_ask(), TRIAGE, params=CLIENT).route == "pinned"
    assert _teacher_calls(teacher) == [(0.9, True, SERVE_GETS)] * 3

    # The shadow samples like the live student; the arbitration of its disagreement is a check.
    led = Ledger(":memory:")
    _version(led, "live", {"threshold": 0.5})
    _version(led, "shadow", {"threshold": 0.5})
    live_student, shadow_student, teacher = RecordingProvider(A), RecordingProvider(B), RecordingProvider(A)
    students = {"student-v1": live_student, "student-v2": shadow_student}
    r = Ratchet(_settings(), led, teacher, lambda v: (students[v.adapter_uri], v.adapter_uri))
    r.handle(_ask(), TRIAGE, params=CLIENT)
    assert live_student.calls[0].kwargs["params"] == shadow_student.calls[0].kwargs["params"] == STUDENT_GETS
    assert _teacher_calls(teacher) == [(0.0, False, CHECK_GETS)]

    # Scout and the simulator send no parameters: the caps alone, the teacher at temperature 0.
    student = RecordingProvider(A)
    _, _, r, teacher = _cascade(student, threshold=0.99)
    r.handle(_ask(), tasks.get(TRIAGE))  # a Task object works as well as its name
    assert student.calls[0].kwargs["params"] == GenParams(max_tokens=1024)
    assert _teacher_calls(teacher) == [(0.0, False, GenParams(max_tokens=8192, temperature=0.0))]


@pytest.mark.req("FR-1.13", "FR-4.3", "INV-2", "INV-12")
def test_examples_store_the_conditions_the_teacher_answered_under():
    student = RecordingProvider(A)
    led, _, r, teacher = _cascade(student, threshold=0.99)
    shaped = GenParams(
        stop=("\n\nbob@example.com",), seed=7, temperature=0.9, max_tokens=500, response_format={"type": "json_object"}
    )
    r.handle(_ask(), TRIAGE, params=shaped)
    (ex,) = led.examples()
    conditions = {"response_format": {"type": "json_object"}, "stop": ["\n\n[EMAIL]"]}  # redacted like the messages
    assert json.loads(ex["params"]) == conditions and ex["redactions"] == 1
    assert ex["hash"] == example_hash(TRIAGE, _ask(), conditions) != example_hash(TRIAGE, _ask())
    assert ex["completion"] == teacher.answer(_ask(), OPEN_TEACHER)  # the teacher's answer, never the student's (INV-2)

    # The same messages without conditions are another example; sampling parameters alone change nothing stored.
    r.handle(_ask(), TRIAGE)
    r.handle(_ask(), TRIAGE, params=GenParams(temperature=0.8, top_p=0.9, seed=3, presence_penalty=0.0, max_tokens=900))
    rows = led.examples()
    assert [(json.loads(e["params"]) if e["params"] else None, e["seen"]) for e in rows] == [(conditions, 1), (None, 2)]

    # An audit stores what the audit was asked under: the check parameters.
    student = RecordingProvider(A)
    led, _, r, _ = _cascade(student, audit_rate=1.0)
    r.handle(_ask("audited"), TRIAGE, params=CLIENT)
    (ex,) = led.examples()
    assert json.loads(ex["params"]) == {"response_format": {"type": "json_object"}, "stop": ["\n\n"]}


@pytest.mark.req("FR-1.13", "FR-2.13", "INV-16")
def test_reasoning_headroom_marks_answers_over_the_clients_budget():
    """With RATCHET_TEACHER_REASONING_HEADROOM, the teacher may write past the client's max_tokens to finish its
    reasoning; a visible answer longer than the budget (by approx_tokens) is then `length`: served, not stored."""
    long_answer = json.dumps({"category": "billing", "priority": "high", "reply": "x" * 80})  # ~25 tokens
    short_answer = json.dumps({"category": "billing", "priority": "high"})  # ~11 tokens
    teacher = RecordingProvider(f"<think>{'r' * 2000}</think>{long_answer}")
    led = Ledger(":memory:")
    r = Ratchet(_settings(teacher_reasoning_headroom=600), led, teacher, lambda v: (RecordingProvider(A), v.adapter_uri))
    out = r.handle(_ask(), TRIAGE, params=GenParams(max_tokens=20))
    assert teacher.calls[0].kwargs["params"].max_tokens == 620
    assert (out.text, out.finish_reason) == (long_answer, "length")  # not cut: the count is an estimate
    assert led.examples() == [] and _rows(led)[0]["stored"] == 0
    assert [e["detail"] for e in led.events() if e["kind"] == "skip_example"] == [f"{TRIAGE}: finish_reason length"]

    teacher.text = f"<think>{'r' * 2000}</think>{short_answer}"  # the reasoning is not part of the budget
    out = r.handle(_ask(), TRIAGE, params=GenParams(max_tokens=20))
    assert out.finish_reason == "stop" and len(led.examples()) == 1

    # An over-budget audit answer is not an audit (INV-16).
    student = RecordingProvider(A)
    led, _, r, teacher = _cascade(student, RecordingProvider(long_answer), audit_rate=1.0, teacher_reasoning_headroom=600)
    out = r.handle(_ask(), TRIAGE, params=GenParams(max_tokens=20))
    assert (out.served_by, out.finish_reason) == ("student", "stop")
    assert _rows(led)[0]["audited"] == 0 and led.examples() == []

    # Without headroom the teacher stopped at the budget itself: its own finish_reason stands, whatever the estimate.
    led = Ledger(":memory:")
    r = Ratchet(_settings(), led, RecordingProvider(long_answer), lambda v: (RecordingProvider(A), v.adapter_uri))
    out = r.handle(_ask(), TRIAGE, params=GenParams(max_tokens=20))
    assert out.finish_reason == "stop" and len(led.examples()) == 1


@pytest.mark.req("FR-1.13", "FR-2.13", "INV-2")
def test_a_request_that_is_not_storable_stores_nothing():
    """`RequestMeta(storable=False)`: answered, audited and logged as usual; no example is inserted or bumped."""
    meta = RequestMeta(storable=False)
    student = RecordingProvider(A)
    led, _, r, _ = _cascade(student, audit_rate=1.0)
    out = r.handle(_ask(), TRIAGE, meta=meta)
    assert out.served_by == "student"
    assert (_rows(led)[0]["audited"], _rows(led)[0]["audit_agree"], _rows(led)[0]["stored"]) == (1, 1, 0)
    led2, _, r2, teacher = _cascade(RecordingProvider(A), threshold=0.99)
    out = r2.handle(_ask(), TRIAGE, meta=meta)
    assert (out.served_by, out.route) == ("teacher", "confidence") and len(teacher.calls) == 1
    r2.handle(_ask(), TRIAGE)  # a storable request stores the same answer
    r2.handle(_ask(), TRIAGE, meta=meta)  # and an opted-out duplicate does not bump it
    assert led.examples() == [] and [e["seen"] for e in led2.examples()] == [1]
    assert [row["stored"] for row in _rows(led2)] == [0, 1, 0]
    assert not [e for e in led2.events() if e["kind"] == "skip_example"]  # not a hygiene skip


@pytest.mark.req("FR-1.13")
def test_bad_generation_settings_stop_startup():
    led = Ledger(":memory:")
    for bad in (
        {"teacher_params": "model"},
        {"student_params": "user,stop"},
        {"teacher_params": "max tokens"},
        {"teacher_max_tokens": -1},
        {"student_max_tokens": -5},
        {"teacher_reasoning_headroom": -1},
    ):
        with pytest.raises(ValueError):
            Ratchet(_settings(**bad), led, RecordingProvider(A), lambda v: (None, ""))
    assert _settings(teacher_params="stop,seed").gen_policy() == GenPolicy(teacher_allow=frozenset({"stop", "seed"}))


# ---------- cascade: n choices and token logprobs (FR-1.14, ADR 0020) ----------
A2 = json.dumps({"category": "billing", "priority": "high", "reply": "Checking your account."})
A3 = json.dumps({"category": "billing", "priority": "high", "reply": "Looking into the double charge."})
# Four scripted samples: A, A2 and A3 share the label (billing, high), B does not. Over the first three, as calibrated,
# 2 of 3 agree and the best is A2 (p 0.9): confidence 0.4 * 0.9 + 0.6 * 2 / 3.
SAMPLES = ([A, A2, B, A3], [math.log(0.6), math.log(0.9), math.log(0.99), math.log(0.95)])
CONF = 0.4 * 0.9 + 0.6 * 2 / 3


@pytest.mark.req("FR-1.14", "FR-2.1", "FR-2.2", "NFR-3")
def test_n_choices_are_scored_as_calibrated_and_choice_zero_is_the_one_choice_answer():
    prices = _settings()
    student, teacher = prices.price("student"), prices.price(OPEN_TEACHER)
    cases = [  # (n, samples drawn, the student's choices or None when it escalates)
        (1, 3, [A2]),
        (2, 3, [A2, A]),  # the best scored sample, then the others that agree, by token probability
        (3, 3, None),  # 3 choices, but only 2 of the 3 samples agree
        (4, 4, None),  # a 4th sample is drawn; 3 of 4 agree
    ]
    for n, drawn, want in cases:
        stu = RecordingProvider(*SAMPLES)
        led, _, r, tea = _cascade(stu, RecordingProvider([A, A2, A3, B]), threshold=0.5)
        out = r.handle(_ask(), TRIAGE, n=n)
        (call,) = stu.calls
        assert (call.kwargs["n"], call.kwargs["temperature"], call.kwargs["logprobs"]) == (drawn, 0.7, True), n
        # The confidence is the one-choice request's, whatever n: the extra samples only fill choices.
        assert out.confidence == pytest.approx(CONF, abs=1e-12) and _rows(led)[0]["confidence"] == out.confidence, n
        if want is not None:
            assert (out.served_by, out.route, out.escalated, tea.calls) == ("student", "student", False, []), n
            assert [ch.text for ch in out.choices] == want and out.text == want[0], n
            assert (out.prompt_tokens, out.completion_tokens) == (10, 5 * n), n  # the prompt once, every choice
            assert out.usage_breakdown["student_samples"] == {"in": 10, "out": 5 * drawn}
            assert out.usage_breakdown["served"] == {"in": 10, "out": 5 * n}
            assert out.cost_usd == pytest.approx(student.cost(10, 5 * drawn), rel=1e-12)
            assert out.baseline_usd == pytest.approx(teacher.cost(10, 5 * n + 300 * n), rel=1e-12)
            continue
        # A confident student that cannot fill n choices escalates: route n_choices, the teacher writes n choices at
        # 0.7 (the client set no temperature), and only its choice 0 is stored.
        assert (out.served_by, out.route, out.escalated) == ("teacher", "n_choices", True), n
        (tcall,) = tea.calls
        assert (tcall.kwargs["n"], tcall.kwargs["temperature"]) == (n, 0.7), n
        assert [ch.text for ch in out.choices] == [A, A2, A3, B][:n] and out.text == A, n
        assert (out.prompt_tokens, out.completion_tokens) == (10, 5 * n), n
        assert out.usage_breakdown["served"] == {"in": 10, "out": 5 * n}, n
        assert out.cost_usd == pytest.approx(student.cost(10, 5 * drawn) + teacher.cost(10, 5 * n), rel=1e-12), n
        assert out.baseline_usd == pytest.approx(teacher.cost(10, 5 * n), rel=1e-12), n
        assert [(e["completion"], e["seen"]) for e in led.examples()] == [(A, 1)], n
        assert _rows(led)[0]["route"] == "n_choices"

    # A student that is unsure escalates on confidence, as for one choice.
    _, _, r, _ = _cascade(RecordingProvider(*SAMPLES), threshold=0.99)
    assert r.handle(_ask(), TRIAGE, n=2).route == "confidence"


@pytest.mark.req("FR-1.14", "FR-4.3", "INV-2", "INV-10", "FR-2.5")
def test_teacher_writes_n_choices_and_only_choice_zero_is_stored():
    # Escalated: n choices from the teacher, at 0.7 without a client temperature, at the client's otherwise.
    teacher = RecordingProvider([A, A2, A3])
    led, _, r, _ = _cascade(RecordingProvider(A), teacher, threshold=0.99)
    out = r.handle(_ask(), TRIAGE, n=3)
    assert [ch.text for ch in out.choices] == [A, A2, A3] and teacher.calls[-1].kwargs["n"] == 3
    assert teacher.calls[-1].kwargs["temperature"] == 0.7
    r.handle(_ask(), TRIAGE, params=GenParams(temperature=0.2), n=3)
    assert teacher.calls[-1].kwargs["temperature"] == 0.2
    # One example, bumped once per request whatever n: seen still counts requests.
    assert [(e["completion"], e["seen"]) for e in led.examples()] == [(A, 2)]
    assert [row["stored"] for row in _rows(led)] == [1, 1]

    # Cold start and pinned: n choices from the teacher; a pinned answer is never stored (INV-10).
    teacher = RecordingProvider([A, A2])
    led = Ledger(":memory:")
    r = Ratchet(_settings(), led, teacher, lambda v: (RecordingProvider(A), v.adapter_uri))
    assert len(r.handle(_ask("cold"), TRIAGE, n=2).choices) == 2
    pinned = r.handle([{"role": "user", "content": "Question: what is LoRA?"}], "research-plan", n=2)
    only = r.teacher_only(_ask("image"), TRIAGE, n=2)
    assert (pinned.route, len(pinned.choices), only.route, len(only.choices)) == ("pinned", 2, "pinned", 2)
    assert [c.kwargs["n"] for c in teacher.calls] == [2, 2, 2] and len(led.examples()) == 1

    # A teacher that returns fewer choices than asked gives fewer; never padded with copies.
    _, _, r, _ = _cascade(RecordingProvider(A), _OneChoice(A), threshold=0.99)
    assert [ch.text for ch in r.handle(_ask(), TRIAGE, n=3).choices] == [A]

    # Student-served and audited: the audit is one deterministic answer judged against choice 0.
    stu = RecordingProvider([A2, A, A], [math.log(0.9), math.log(0.8), math.log(0.7)])
    led, _, r, teacher = _cascade(stu, RecordingProvider(A3), audit_rate=1.0)
    out = r.handle(_ask(), TRIAGE, n=2)
    assert out.served_by == "student" and [ch.text for ch in out.choices] == [A2, A]
    (audit,) = teacher.calls
    assert (audit.kwargs["n"], audit.kwargs["temperature"], audit.kwargs["logprobs"]) == (1, 0.0, False)
    assert (_rows(led)[0]["audited"], _rows(led)[0]["audit_agree"]) == (1, 1)
    # An audited request's baseline: the teacher alone would have written one answer like the audit's per choice.
    assert out.baseline_usd == pytest.approx(_settings().price(OPEN_TEACHER).cost(10, 5 * 2), rel=1e-12)

    # The shadow samples as calibrated (RATCHET_SAMPLES), whatever n, and is compared with choice 0.
    led = Ledger(":memory:")
    _version(led, "live", {"threshold": 0.5})
    _version(led, "shadow", {"threshold": 0.5})
    live_student, shadow_student = RecordingProvider(A), RecordingProvider(B)
    students = {"student-v1": live_student, "student-v2": shadow_student}
    teacher = RecordingProvider(A)
    r = Ratchet(_settings(), led, teacher, lambda v: (students[v.adapter_uri], v.adapter_uri))
    out = r.handle(_ask(), TRIAGE, n=4)
    assert (live_student.calls[0].kwargs["n"], shadow_student.calls[0].kwargs["n"]) == (4, 3)
    assert out.served_by == "student" and len(out.choices) == 4
    (arbitration,) = teacher.calls
    assert (arbitration.kwargs["n"], arbitration.kwargs["temperature"]) == (1, 0.0)
    row = _rows(led)[0]
    assert (row["shadow_serve"], row["shadow_agree"]) == (1, 0)


class _OneChoice(RecordingProvider):
    """A server that ignores `n` and always returns one choice."""

    def complete(self, messages, model, *args, **kwargs):
        return super().complete(messages, model, *args, **kwargs)[:1]


@pytest.mark.req("FR-1.14", "FR-2.2")
def test_logprobs_come_back_only_when_asked_and_match_the_score():
    asked = GenParams(logprobs=True, top_logprobs=2)
    student = RecordingProvider([A, A, B], [math.log(0.6), math.log(0.9), math.log(0.99)])
    _, _, r, teacher = _cascade(student, threshold=0.5)
    out = r.handle(_ask(), TRIAGE, params=asked)
    sc = score(tasks.get(TRIAGE), student.complete(_ask(), "student-v1", n=3, logprobs=True))
    (choice,) = out.choices
    content = choice.logprobs
    assert content and "".join(t["token"] for t in content) == choice.text == out.text
    # The served sample's token probability, as the confidence score saw it.
    assert math.exp(sum(t["logprob"] for t in content) / len(content)) == pytest.approx(sc.token_prob, abs=1e-12)
    assert sc.token_prob == pytest.approx(0.9, abs=1e-12)
    assert all(len(t["top_logprobs"]) == 2 for t in content)  # the student got the client's top_logprobs
    assert student.calls[0].kwargs["params"].top_logprobs == 2

    # Not asked: none in the result, and the student is sampled exactly as before.
    plain = r.handle(_ask(), TRIAGE)
    assert plain.choices[0].logprobs is None and plain.choices[0].text == out.text
    assert (student.calls[-1].kwargs["logprobs"], student.calls[-1].kwargs["params"].top_logprobs) == (True, None)

    # Each of n choices carries its own tokens.
    two = r.handle(_ask(), TRIAGE, params=asked, n=2)
    for ch, p in zip(two.choices, (0.9, 0.6), strict=True):
        assert "".join(t["token"] for t in ch.logprobs) == ch.text
        assert math.exp(sum(t["logprob"] for t in ch.logprobs) / len(ch.logprobs)) == pytest.approx(p, abs=1e-12)

    # The teacher gets logprobs and top_logprobs only when the client asked; an audit never.
    stu = RecordingProvider(A)
    _, _, r, teacher = _cascade(stu, RecordingProvider(A2), threshold=0.99)
    served = r.handle(_ask(), TRIAGE, params=asked)
    call = teacher.calls[-1]
    assert (call.kwargs["logprobs"], call.kwargs["params"].top_logprobs) == (True, 2)
    assert "".join(t["token"] for t in served.choices[0].logprobs) == A2
    r.handle(_ask(), TRIAGE)
    assert (teacher.calls[-1].kwargs["logprobs"], teacher.calls[-1].kwargs["params"].top_logprobs) == (False, None)
    _, _, r, teacher = _cascade(RecordingProvider(A), audit_rate=1.0)
    assert r.handle(_ask(), TRIAGE, params=asked).served_by == "student"
    assert (teacher.calls[0].kwargs["logprobs"], teacher.calls[0].kwargs["params"].top_logprobs) == (False, None)
    # A teacher that sends no logprobs: the choice says so with None, the request still succeeds.
    _, _, r, _ = _cascade(RecordingProvider(A), RecordingProvider(A2, logprob=None), threshold=0.99)
    assert r.handle(_ask(), TRIAGE, params=asked).choices[0].logprobs is None


# ---------- licensing ----------
@pytest.mark.req("FR-3.2", "US-8", "INV-1")
@pytest.mark.parametrize("model", ["o1-preview", "o3-mini", "o4-mini", "openai/o3-mini"])
def test_o_series_prefixes_refused(model):
    with pytest.raises(licenses.LicenseError, match="OpenAI Business Terms"):
        licenses.require_distillable(model)
    lic = licenses.lookup(model)
    assert lic is not None and not lic.distill_ok and lic.url.startswith("https://openai.com/")


@pytest.mark.req("FR-3.2", "INV-1")
def test_closed_teacher_fails_at_startup(gw_env, tmp_path):
    led = Ledger(":memory:")
    with pytest.raises(licenses.LicenseError, match="not distillable"):
        Ratchet(_settings(teacher_model="gpt-4o"), led, RecordingProvider(A), lambda v: (None, ""))
    with pytest.raises(licenses.LicenseError, match="Unknown teacher"):
        Ratchet(_settings(teacher_model="someone/new-model"), led, RecordingProvider(A), lambda v: (None, ""))

    # The gateway's runtime refuses to start, in either mode (RATCHET_TEACHER_MODEL=gpt-4o stops the process).
    gw = _gateway(gw_env)
    for mode in ("live", "mock"):
        settings = Settings(
            mode=mode,
            teacher_model="gpt-4o",
            db_path=str(tmp_path / f"{mode}.db"),
            student_base="",
            tavily_api_key="",
            api_keys="",
            bom_signing_key="",
            mock_warmup_days=0,
        )
        with pytest.raises(licenses.LicenseError, match="gpt-4o"):
            gw.Runtime(settings)


@pytest.mark.req("FR-3.2", "INV-1", "FR-2.12")
def test_closed_fallback_teacher_fails_at_startup(gw_env, tmp_path):
    """A fallback teacher (RATCHET_TEACHER_FALLBACK_MODEL) is a teacher: a closed or unknown model stops `Ratchet` and the
    gateway's runtime at startup, long before the primary is ever down. A distillable one is licensed up front, so the
    examples it may write carry its own license."""
    led = Ledger(":memory:")
    for model, match in (
        ("gpt-4o", "OpenAI Business Terms"),
        ("openai/gpt-4o", "not distillable"),
        ("o3-mini", "OpenAI Business Terms"),
        ("claude-3-5-sonnet", "Anthropic Commercial Terms"),
        ("google/gemini-2.5-pro", "Google Gemini API Terms"),
        ("someone/new-model", "Unknown teacher"),
    ):
        with pytest.raises(licenses.LicenseError, match=match):
            Ratchet(_settings(teacher_fallback_model=model), led, RecordingProvider(A), lambda v: (None, ""))

    gw = _gateway(gw_env)
    for mode in ("live", "mock"):
        settings = Settings(
            mode=mode,
            teacher_model=OPEN_TEACHER,
            teacher_fallback_model="gpt-4o",
            db_path=str(tmp_path / f"{mode}.db"),
            student_base="",
            tavily_api_key="",
            api_keys="",
            bom_signing_key="",
            mock_warmup_days=0,
        )
        with pytest.raises(licenses.LicenseError, match="gpt-4o"):
            gw.Runtime(settings)

    fallback = "nvidia/nemotron-3-super-120b-a12b"
    r = Ratchet(_settings(teacher_fallback_model=fallback), led, RecordingProvider(A), lambda v: (None, ""))
    assert r.licenses == {OPEN_TEACHER: licenses.OPEN_MDW, fallback: licenses.NEMOTRON_OPEN}
    assert Ratchet(_settings(), led, RecordingProvider(A), lambda v: (None, "")).licenses == {OPEN_TEACHER: licenses.OPEN_MDW}


@pytest.mark.req("FR-3.3")
def test_examples_record_license_url():
    for i, teacher_model in enumerate([OPEN_TEACHER, "nvidia/nemotron-3-super-120b-a12b"]):
        lic = licenses.REGISTRY[teacher_model]
        led = Ledger(":memory:")
        r = Ratchet(_settings(teacher_model=teacher_model), led, RecordingProvider(A), lambda v: (None, ""))
        msgs = _ask(i)
        r.handle(msgs, TRIAGE)  # cold start: the teacher answers and its answer is stored
        (ex,) = led.examples()
        assert (ex["teacher_model"], ex["license"], ex["license_url"]) == (teacher_model, lic.name, lic.url)
        assert re.match(r"https://\S+$", ex["license_url"])
        assert (ex["task"], ex["hash"], ex["completion"]) == (TRIAGE, example_hash(TRIAGE, msgs), A)
        assert ex["split"] in ("train", "golden")
        want = {"teacher_model": teacher_model, "license": lic.name, "license_url": lic.url, "split": ex["split"], "n": 1}
        assert led.bill_of_materials()["sources"] == [want]


@pytest.mark.req("INV-9")
def test_configured_models_are_licensed_and_day1_checked(day1_check, monkeypatch):
    priced = set(DEFAULT_PRICES) - {"student"}
    want = list(day1_check.WANT)
    assert priced and len(want) == len(set(want))
    for model in sorted(priced):
        assert model in licenses.REGISTRY, f"{model} has a price in config.py but no license in licenses.REGISTRY"
        assert model in want, f"{model} has a price in config.py but scripts/day1_check.py does not check it"

    # The Token Factory models Settings uses by default are among them.
    for var in ("RATCHET_TEACHER_MODEL", "RATCHET_JUDGE_MODEL", "RATCHET_FALLBACK_STUDENT"):
        monkeypatch.delenv(var, raising=False)
    s = Settings()
    for model in (s.teacher_model, s.judge_model, s.fallback_student_model):
        assert model in priced, f"{model} is a default in Settings but has no price in DEFAULT_PRICES"
