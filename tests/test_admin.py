"""The admin API and CLI (FR-12.1) and compare-and-set release transitions (FR-12.9), ADR 0064: the ledger's
`transition` and `promote_live`, the release gate racing itself and an admin across two ledgers on one file, the admin
routes' keys, the transition table and its 409s, the audit trail, and what each runtime switch does to requests, the
gate and Night School without a restart."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest
from fakes import (
    BASE_MODEL,
    OPEN_TEACHER,
    REJECTED_EVAL,
    FakeOpenAIServer,
    RecordingEvaluator,
    RecordingProvider,
    RecordingTrainer,
    _audit,
    _examples,
    _shadow,
    _version,
)
from fastapi.testclient import TestClient

from ratchet import access, admin, tasks
from ratchet.config import Settings
from ratchet.ledger import Ledger
from ratchet.nightschool import dataset
from ratchet.nightschool.orchestrate import run_night
from ratchet.release import ReleaseGate, ReleasePolicy
from ratchet.router import Ratchet

ADMIN_KEY = "ops-admin-key-0123456789abcdef"
ACME_KEY = "acme-api-key-0123456789abcdef"
DASH_KEY = "dash-board-key-0123456789abcd"
ADMIN = {"Authorization": f"Bearer {ADMIN_KEY}"}
ACME = {"Authorization": f"Bearer {ACME_KEY}"}
DASH = {"X-API-Key": DASH_KEY}
CHAT = "/v1/chat/completions"
TRIAGE = "ticket-triage"
A = json.dumps({"category": "billing", "priority": "high", "reply": "I am checking the charge now."})
GOOD = {"precision": 0.98, "coverage": 0.60, "threshold": 0.5}
REASON = {"reason": "refund labels are wrong since 09:00"}


def _kinds(led: Ledger, *kinds: str) -> list[dict[str, Any]]:
    return [e for e in led.events() if e["kind"] in kinds]


def _actions(led: Ledger) -> list[dict[str, Any]]:
    return [dict(r) for r in led.db.execute("SELECT * FROM admin_actions ORDER BY id")]


def _state(led: Ledger, vid: int) -> tuple[str, int]:
    v = led.version(vid)
    assert v is not None
    return v.state, v.traffic_pct


def _ask(tag: object = 0) -> list[dict[str, Any]]:
    return [{"role": "user", "content": f"I was charged twice this month, order {tag}"}]


def _live(tmp_path: Path, **overrides: Any) -> Settings:
    """A live gateway's settings with every value these tests depend on set: an API key, an admin key and a dashboard
    key, a private dashboard, the teacher on a fake endpoint, no student endpoint unless asked, no audits unless asked."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    base: dict[str, Any] = {
        "mode": "live",
        "db_path": str(tmp_path / "live.db"),
        "teacher_base": "https://tokenfactory.test/v1",
        "teacher_api_key": "tf-teacher-key-0123456789",
        "teacher_model": OPEN_TEACHER,
        "student_base": "",
        "student_api_key": "",
        "samples": 3,
        "audit_rate": 0.0,
        "teacher_retries": 0,
        "api_keys": f"acme:{ACME_KEY}",
        "admin_keys": f"ops:{ADMIN_KEY}",
        "dashboard_keys": f"wall:{DASH_KEY}",
        "metrics_token": "",
        "api_key_daily_requests": 0,
        "key_limits": "",
        "daily_budget_usd": 0.0,
        "ip_rpm": 0,
        "tasks_file": "",
        "unknown_tasks": "distill",
        "bom_signing_key": "",
        "tavily_api_key": "",
        "mock_warmup_days": 0,
        "public_dashboard": False,
        "allow_unauthenticated": None,
        "log_format": "",
        "log_level": "",
        "teacher_reasoning_tokens": 0,
        "stats_ttl_s": 0.0,
    }
    return Settings(**{**base, **overrides})


def _gateway(gw_env: Any, monkeypatch: pytest.MonkeyPatch, settings: Settings, **transports: Any) -> tuple[Any, Any]:
    gw = gw_env()
    rt = gw.Runtime(settings, transports=transports or {"teacher": FakeOpenAIServer(A).transport}, sleep=lambda s: None)
    monkeypatch.setattr(gw, "rt", rt)
    return gw, rt


def _student_gateway(gw_env: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **overrides: Any):
    """A live gateway with a student endpoint (a fake vLLM that answers A, unanimous and confident) and a live version
    at threshold 0.5, so a ticket-triage request is the student's unless something stops it."""
    teacher, student = FakeOpenAIServer(A), FakeOpenAIServer(A)
    settings = _live(tmp_path, student_base="https://vllm.test/v1", student_api_key="vllm-key-0123456789", **overrides)
    gw, rt = _gateway(gw_env, monkeypatch, settings, teacher=teacher.transport, student=student.transport)
    live = _version(rt.ledger, "live", GOOD)
    return gw, rt, live, teacher, student


def _chat(c: TestClient, tag: object = 0) -> dict[str, Any]:
    r = c.post(CHAT, json={"messages": _ask(tag)}, headers={**ACME, "X-Ratchet-Task": TRIAGE})
    assert r.status_code == 200, r.text
    return r.json()["ratchet"]


# ---------- the ledger: compare-and-set moves ----------
@pytest.mark.req("FR-12.9")
def test_transition_moves_a_version_only_from_where_the_caller_saw_it():
    led = Ledger(":memory:")
    canary = _version(led, "canary", GOOD)  # canary 10%
    _audit(led, canary, 3)
    assert not led.transition(canary, "canary", 50, "canary", 50, ("canary", f"v{canary} canary 50%"))  # stale share
    assert not led.transition(canary, "shadow", 10, "canary", 50, ("canary", f"v{canary} canary 50%"))  # stale state
    assert _state(led, canary) == ("canary", 10) and led.events() == []  # nothing moved, nothing written
    assert led.transition(canary, "canary", 10, "canary", 50, ("canary", f"v{canary} canary 50%"))
    assert _state(led, canary) == ("canary", 50) and [e["detail"] for e in led.events()] == [f"v{canary} canary 50%"]
    assert led.version(canary).stage_since == 3  # a new stage starts after the last request logged
    assert not led.transition(canary, "canary", 10, "rolled_back", 0)  # the same decision a second time: refused

    # At most one version in flight: a move into shadow or canary is refused while another is there.
    other = _version(led, "candidate", GOOD)
    assert not led.transition(other, "candidate", 0, "shadow", 0, ("shadow", "x")) and _state(led, other) == ("candidate", 0)
    assert led.transition(canary, "canary", 50, "retired", 0)
    assert led.transition(other, "candidate", None, "shadow", 0)  # a share stored as NULL reads as 0
    assert _state(led, other) == ("shadow", 0)

    # A version becomes live only with the old live version retired in the same transaction (promote_live).
    with pytest.raises(ValueError, match="promote_live"):
        led.transition(other, "shadow", 0, "live", 100)
    with pytest.raises(ValueError, match="state must be one of"):
        led.transition(other, "shadow", 0, "staging", 0)
    with pytest.raises(ValueError, match="traffic_pct must be a whole number from 0 to 100"):
        led.transition(other, "shadow", 0, "canary", 101)


@pytest.mark.req("FR-12.9")
def test_promote_live_retires_the_old_live_version_in_the_same_transaction():
    led = Ledger(":memory:")
    old = _version(led, "live", GOOD)
    new = _version(led, "canary", GOOD, pct=50)
    stale = led.promote_live(new, "canary", 50, ("live", "x"), old_live=None)  # the caller saw no live version
    assert not stale and _state(led, new) == ("canary", 50) and led.events() == []
    assert led.promote_live(
        new, "canary", 50, ("live", f"v{new} live"), old_live=old, retired_event=("live_retired", f"v{old} retired")
    )
    assert [v.id for v in led.versions(["live"])] == [new] and _state(led, old) == ("retired", 0)
    assert [(e["kind"], e["detail"]) for e in led.events()] == [("live_retired", f"v{old} retired"), ("live", f"v{new} live")]
    assert not led.promote_live(new, "canary", 50, ("live", "again"), old_live=old)  # the same promotion again
    assert len(led.events()) == 2
    with pytest.raises(ValueError, match="live already"):
        led.promote_live(new, "live", 100, old_live=None)


@pytest.mark.req("FR-12.9")
def test_a_transaction_commits_its_moves_and_events_together_or_not_at_all():
    led = Ledger(":memory:")
    heard: list[str] = []
    led.add_event_listener(lambda row: heard.append(row["detail"]))
    canary = _version(led, "canary", GOOD)
    with pytest.raises(RuntimeError, match="boom"):
        with led.transaction():
            assert led.transition(canary, "canary", 10, "rolled_back", 0, ("rollback", "inside"))
            assert led.by_state("canary") is None  # the transaction sees its own move
            raise RuntimeError("boom")
    assert _state(led, canary) == ("canary", 10) and led.events() == [] and heard == []  # all rolled back
    assert led.by_state("canary").id == canary  # the routing snapshot built inside was dropped
    with led.transaction():
        with led.transaction():  # nested: joins the outer one
            led.transition(canary, "canary", 10, "canary", 50, ("canary", "advanced"))
        assert heard == []  # listeners hear an event once it is committed
    assert heard == ["advanced"] and _state(led, canary) == ("canary", 50)
    with pytest.raises(RuntimeError, match="committed early"):  # a writer that commits by itself splits a transaction
        with led.transaction():
            led.log_request(task="t", served_by="teacher", cost_usd=0.0, baseline_cost_usd=0.0, latency_ms=1.0)


# ---------- the release gate racing itself and an admin (two processes on one file) ----------
def _two_gates(tmp_path: Path, policy: ReleasePolicy) -> tuple[Ledger, ReleaseGate, Ledger, ReleaseGate]:
    """Two ledgers on one file, as the gateway's tick and the cron tick (`python -m ratchet.live tick`) have."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = str(tmp_path / "shared.db")
    a, b = Ledger(path), Ledger(path)
    return a, ReleaseGate(a, policy), b, ReleaseGate(b, policy)


def _first_call_runs(led: Ledger, method: str, before: Any) -> None:
    """Make `led.<method>` run `before()` the first time it is called, then do its job: another process acting between
    this gate's reads and its write."""
    real, done = getattr(led, method), []

    def wrapped(*args: Any, **kwargs: Any) -> Any:
        if not done:
            done.append(True)
            before()
        return real(*args, **kwargs)

    setattr(led, method, wrapped)


@pytest.mark.req("FR-12.9")
def test_two_ticks_on_one_ledger_file_advance_a_canary_once(tmp_path):
    a, gate_a, b, gate_b = _two_gates(tmp_path, ReleasePolicy(min_audits=20))
    canary = _version(a, "canary", GOOD)
    _audit(a, canary, 20)  # ready to go to 50%
    # Gate B has read the canary at 10% and its 20 audits; gate A advances it before B writes (B's last read before its
    # move is the freeze switch).
    _first_call_runs(b, "flags", gate_a.tick)
    gate_b.tick()
    assert _state(a, canary) == _state(b, canary) == ("canary", 50)
    assert [e["detail"] for e in _kinds(a, "canary")] == [f"v{canary} audited precision 100.0% (n=20); canary 50%"]

    # The same race with real threads, many times: one advance each time, whoever wins.
    for round_ in range(6):
        a, gate_a, b, gate_b = _two_gates(tmp_path / str(round_), ReleasePolicy(min_audits=20))
        canary = _version(a, "canary", GOOD)
        _audit(a, canary, 20)
        start = threading.Barrier(2)
        errors: list[BaseException] = []

        def run(gate: ReleaseGate, start: threading.Barrier = start, errors: list[BaseException] = errors) -> None:
            try:
                start.wait()
                gate.tick()
            except BaseException as e:  # noqa: BLE001 - reported below
                errors.append(e)

        threads = [threading.Thread(target=run, args=(g,)) for g in (gate_a, gate_b)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == [] and _state(a, canary) == ("canary", 50) and len(_kinds(b, "canary")) == 1


@pytest.mark.req("FR-12.9")
def test_a_promotion_race_leaves_exactly_one_live_version(tmp_path):
    a, gate_a, b, gate_b = _two_gates(tmp_path, ReleasePolicy(min_audits=20))
    old = _version(a, "live", GOOD)
    new = _version(a, "canary", GOOD, pct=50)
    _audit(a, new, 20)  # ready to replace the live version
    _first_call_runs(b, "flags", gate_a.tick)  # gate A promotes while gate B is about to
    gate_b.tick()
    assert [v.id for v in b.versions(["live"])] == [new] and _state(b, old) == ("retired", 0)
    assert len(_kinds(b, "live")) == 1 and len(_kinds(b, "live_retired")) == 1

    # An admin promotes a canary to live (the CLI, another process) while the gate's tick is about to do the same.
    a, gate_a, b, _ = _two_gates(tmp_path / "admin", ReleasePolicy(min_audits=20))
    old = _version(a, "live", GOOD)
    new = _version(a, "canary", GOOD, pct=50)
    _audit(a, new, 20)
    _first_call_runs(a, "flags", lambda: admin.promote(b, new, "live", reason="ship it before the demo", actor="cli:ops"))
    gate_a.tick()
    assert [v.id for v in a.versions(["live"])] == [new] and _state(a, old) == ("retired", 0)
    assert _kinds(a, "live", "live_retired") == []  # the gate's promotion found it done and wrote nothing
    (row,) = _actions(a)
    assert (row["actor"], row["action"], row["target"]) == ("cli:ops", "promote", f"v{new}")


@pytest.mark.req("FR-12.9", "FR-6.8")
def test_admit_leaves_a_version_an_admin_moved_alone():
    """`admit` is one transaction and acts only on a candidate: one an admin promoted while it was being scored is not
    retired as an 'older' canary, nor moved again."""
    led = Ledger(":memory:")
    gate = ReleaseGate(led)
    v = _version(led, "candidate", GOOD)
    admin.promote(led, v, "canary", force=True, reason="skip the gate for the demo", actor="cli:ops")
    assert not gate.admit(v)
    assert _state(led, v) == ("canary", 10)
    assert [e["detail"] for e in _kinds(led, "conflict")] == [
        f"v{v} left as canary: changed by another process while the gate decided"
    ]


# ---------- the admin API: keys ----------
def _admin_routes(app: Any) -> list[tuple[str, str]]:
    out = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if path.startswith("/ratchet/admin/"):
            out += [(m, path) for m in sorted(getattr(route, "methods", ()) or ()) if m != "HEAD"]
    return out


def _call(c: TestClient, method: str, path: str, headers: dict[str, str]) -> Any:
    url = path.replace("{vid}", "1").replace("{name}", TRIAGE)
    body = {"reason": "a test call, never applied", **({"audit_rate": 0.5} if path.endswith("/settings") else {})}
    return c.request(method, url, headers=headers, json=None if method == "GET" else body)


@pytest.mark.req("FR-12.1", "INV-8", "INV-13")
def test_every_admin_route_needs_an_admin_key(gw_env, tmp_path, monkeypatch):
    gw, rt = _gateway(gw_env, monkeypatch, _live(tmp_path))
    c = TestClient(gw.app)
    routes = _admin_routes(gw.app)
    assert len(routes) == 16 and all(access.route_access(m, p) == access.ADMIN for m, p in routes)
    v = _version(rt.ledger, "canary", GOOD)
    assert v == 1
    for method, path in routes:
        r = _call(c, method, path, {})
        assert (r.status_code, r.json()["detail"]) == (401, "invalid or missing admin key"), (method, path)
        assert _call(c, method, path, ACME).status_code == 403, (method, path)  # an API key
        assert _call(c, method, path, DASH).status_code == 403, (method, path)  # a dashboard key only reads
    assert _actions(rt.ledger) == [] and _state(rt.ledger, v) == ("canary", 10) and rt.ledger.flags().as_dict() == {
        "student_disabled": False, "releases_frozen": False, "training_paused": False, "audit_rate": None, "pinned_tasks": [],
    }  # fmt: skip

    # Live mode without admin keys: the admin API is off for everyone, and the demo controls stay mock-only (INV-8).
    _, rt = _gateway(gw_env, monkeypatch, _live(tmp_path / "off", admin_keys=""))
    _version(rt.ledger, "canary", GOOD)
    for method, path in routes:
        r = _call(c, method, path, ADMIN)
        assert (r.status_code, r.json()) == (403, {"detail": "admin API disabled: set RATCHET_ADMIN_KEYS"}), (method, path)
    assert _actions(rt.ledger) == []
    _, rt = _gateway(gw_env, monkeypatch, _live(tmp_path / "demo"))
    for path in ("/ratchet/demo/day", "/ratchet/demo/break-next", "/ratchet/demo/reset"):
        assert c.post(path, headers=ADMIN).status_code == 403  # an admin key does not open the mock-only controls
    dump = "\n".join(rt.ledger.db.iterdump())
    assert ADMIN_KEY not in dump and ACME_KEY not in dump and DASH_KEY not in dump


# ---------- the admin API: versions ----------
@pytest.mark.req("FR-12.1", "INV-13")
def test_canary_rollback_over_http_writes_the_move_an_audit_row_and_an_event(gw_env, tmp_path, monkeypatch):
    gw, rt = _gateway(gw_env, monkeypatch, _live(tmp_path))
    c = TestClient(gw.app)
    led = rt.ledger
    live = _version(led, "live", GOOD)
    canary = _version(led, "canary", GOOD, pct=50)
    r = c.post(f"/ratchet/admin/versions/{canary}/rollback", json=REASON, headers=ADMIN)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {
        "action": "rollback", "target": f"v{canary}", "changed": True, "action_id": 1,
        "before": {f"v{canary}": {"state": "canary", "traffic_pct": 50}},
        "after": {f"v{canary}": {"state": "rolled_back", "traffic_pct": 0}},
    }  # fmt: skip
    assert _state(led, canary) == ("rolled_back", 0) and _state(led, live) == ("live", 100)
    (row,) = _actions(led)
    assert (row["actor"], row["action"], row["target"], row["reason"]) == ("api:ops", "rollback", f"v{canary}", REASON["reason"])
    assert json.loads(row["before"]) == body["before"] and json.loads(row["after"]) == body["after"]
    assert row["request_id"] == r.headers["x-request-id"]  # the admin call's own id ties the row to the gateway log
    (event,) = _kinds(led, "admin")
    assert event["detail"] == f"v{canary} rolled back from canary 50% by api:ops: {REASON['reason']}"
    assert led.by_state("canary") is None  # routing sees it on the next request

    # Done is done: a second rollback is a conflict, and changes and records nothing.
    r = c.post(f"/ratchet/admin/versions/{canary}/rollback", json=REASON, headers=ADMIN)
    assert (r.status_code, r.json()["detail"]) == (409, f"v{canary} is rolled_back; only a canary is rolled back")
    log = c.get("/ratchet/admin/audit-log", headers=ADMIN).json()["actions"]
    assert [(a["id"], a["actor"], a["action"], a["after"]) for a in log] == [(1, "api:ops", "rollback", body["after"])]
    assert c.get("/ratchet/admin/audit-log?limit=0", headers=ADMIN).status_code == 400
    assert c.get("/ratchet/admin/audit-log?limit=1001", headers=ADMIN).status_code == 400


@pytest.mark.req("FR-12.1", "FR-12.9")
def test_promote_to_live_over_http_retires_the_old_live_version_atomically(gw_env, tmp_path, monkeypatch):
    gw, rt = _gateway(gw_env, monkeypatch, _live(tmp_path))
    c = TestClient(gw.app)
    led = rt.ledger
    old = _version(led, "live", GOOD)
    new = _version(led, "canary", GOOD)  # 10%
    r = c.post(f"/ratchet/admin/versions/{new}/promote", json={**REASON, "to": "canary"}, headers=ADMIN)
    assert r.json()["after"] == {f"v{new}": {"state": "canary", "traffic_pct": 50}}  # the next stage by default
    r = c.post(f"/ratchet/admin/versions/{new}/promote", json={**REASON, "to": "canary", "traffic_pct": 50}, headers=ADMIN)
    assert (r.status_code, r.json()["changed"], r.json()["action_id"]) == (200, False, None)  # already there
    r = c.post(f"/ratchet/admin/versions/{new}/promote", json={**REASON, "to": "live"}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["before"] == {
        f"v{new}": {"state": "canary", "traffic_pct": 50}, f"v{old}": {"state": "live", "traffic_pct": 100},
    } and r.json()["after"] == {
        f"v{new}": {"state": "live", "traffic_pct": 100}, f"v{old}": {"state": "retired", "traffic_pct": 0},
    }  # fmt: skip
    assert [v.id for v in led.versions(["live"])] == [new] and led.live().id == new
    assert [a["action"] for a in _actions(led)] == ["promote", "promote"]
    detail = f"v{new} promoted from canary 50% to live by api:ops; v{old} retired: {REASON['reason']}"
    assert _kinds(led, "admin")[-1]["detail"] == detail
    versions = c.get("/ratchet/admin/versions", headers=ADMIN).json()
    assert [(v["id"], v["state"], v["traffic_pct"]) for v in versions["versions"]] == [(new, "live", 100), (old, "retired", 0)]
    assert versions["versions"][0]["golden"] == {"precision": 0.98, "coverage": 0.6, "threshold": 0.5, "golden_n": None}
    assert versions["versions"][0]["stage_audits"] == {"n": 0, "precision": None}


@pytest.mark.req("FR-12.1")
@pytest.mark.parametrize(
    "state, body, status, detail",
    [
        ("rejected", {"to": "live"}, 409, "is rejected; it cannot become live; promote it to canary first"),
        ("rejected", {"to": "canary"}, 409, "needs force=true"),
        ("rolled_back", {"to": "canary"}, 409, "needs force=true"),
        ("candidate", {"to": "canary"}, 409, "needs force=true"),
        ("live", {"to": "canary"}, 409, "is live; it cannot become canary"),
        ("shadow", {"to": "live"}, 409, "promote it to canary first"),
        ("canary", {"to": "canary", "traffic_pct": 100}, 400, "a canary takes 1 to 99% of traffic"),
        ("canary", {"to": "canary", "traffic_pct": "50"}, 400, "traffic_pct must be a whole number"),
        ("canary", {"to": "live", "traffic_pct": 50}, 400, "a live version takes 100% of traffic"),
        ("canary", {"to": "staging"}, 400, "to must be 'canary' or 'live'"),
        ("canary", {"to": "live", "force": "yes"}, 400, "force must be true or false"),
        ("canary", {"to": "live", "load_adapter": 1}, 400, "load_adapter must be true or false"),
        ("canary", {"to": "live", "color": "red"}, 400, "unknown keys ['color']"),
    ],
)
def test_refused_promotions(gw_env, tmp_path, monkeypatch, state, body, status, detail):
    gw, rt = _gateway(gw_env, monkeypatch, _live(tmp_path))
    c = TestClient(gw.app)
    v = _version(rt.ledger, state, GOOD)
    before = _state(rt.ledger, v)
    r = c.post(f"/ratchet/admin/versions/{v}/promote", json={**REASON, **body}, headers=ADMIN)
    assert r.status_code == status and detail in r.json()["detail"], r.text
    assert _state(rt.ledger, v) == before and _actions(rt.ledger) == [] and _kinds(rt.ledger, "admin") == []


@pytest.mark.req("FR-12.1")
def test_the_transition_table(tmp_path):
    led = Ledger(":memory:")
    who = {"reason": "the operator's reason", "actor": "cli:ops"}
    live = _version(led, "live", GOOD)
    # A forced move brings a rejected, rolled-back or retired version back as a canary; never one without an eval.
    for state in ("rejected", "rolled_back", "retired", "candidate"):
        v = _version(led, state, GOOD)
        assert admin.promote(led, v, "canary", force=True, **who)["forced"] is True
        assert _state(led, v) == ("canary", 10)
        with pytest.raises(admin.AdminConflict, match="in canary; retire or roll it back first"):
            admin.promote(led, _version(led, "rejected", GOOD), "canary", force=True, **who)  # one in flight at a time
        admin.retire(led, v, **who)
    bare = _version(led, "candidate", {})
    with pytest.raises(admin.AdminConflict, match="no golden eval"):
        admin.promote(led, bare, "canary", force=True, **who)
    shadow = _version(led, "shadow", GOOD)
    assert admin.promote(led, shadow, "canary", **who)["after"] == {f"v{shadow}": {"state": "canary", "traffic_pct": 10}}
    admin.promote(led, shadow, "canary", 50, **who)
    with pytest.raises(admin.AdminConflict, match="last canary stage"):
        admin.promote(led, shadow, "canary", **who)
    with pytest.raises(admin.AdminConflict, match="only a canary is rolled back; retire the live version instead"):
        admin.rollback(led, live, **who)
    admin.retire(led, live, **who)  # the teacher answers what the canary does not
    assert led.live() is None and _state(led, shadow) == ("canary", 50)
    with pytest.raises(admin.AdminConflict, match="only a live, canary or shadow version is retired"):
        admin.retire(led, live, **who)
    with pytest.raises(admin.AdminNotFound, match="no version 99"):
        admin.rollback(led, 99, **who)
    with pytest.raises(admin.AdminError, match="a version id is a whole number"):
        admin.rollback(led, "1", **who)
    assert admin.promote(led, shadow, "live", **who)["after"] == {f"v{shadow}": {"state": "live", "traffic_pct": 100}}
    assert [a["action"] for a in _actions(led)].count("promote") == 7  # 4 forced, the shadow, 50% and live


@pytest.mark.req("FR-12.1")
def test_a_reason_is_required_checked_and_redacted():
    led = Ledger(":memory:")
    v = _version(led, "canary", GOOD)
    for bad, why in (
        (None, "reason is required"),
        ("ok", "3 to 500 characters, not 2"),
        ("  x  ", "not 1"),
        ("x" * 501, "not 501"),
        ("two\nlines", "one line of text"),
        (42, "reason is required"),
    ):
        with pytest.raises(admin.AdminError, match=why):
            admin.rollback(led, v, bad, "cli:ops")
    for actor in ("", "cli:o ps", "Bearer sk-0123456789", "x" * 129, None):
        with pytest.raises(admin.AdminError, match="actor must be"):
            admin.rollback(led, v, "a good reason", actor)
    assert _state(led, v) == ("canary", 10) and _actions(led) == []
    admin.rollback(led, v, "  customer jane@example.com says refunds are wrong  ", "cli:ops")
    (row,) = _actions(led)
    assert row["reason"] == "customer [EMAIL] says refunds are wrong"
    assert "jane@example.com" not in "\n".join(led.db.iterdump())


@pytest.mark.req("FR-12.1")
def test_load_adapter_before_a_live_promotion(gw_env, tmp_path, monkeypatch):
    teacher, student = FakeOpenAIServer(A), FakeOpenAIServer(A)
    settings = _live(tmp_path, student_base="https://vllm.test/v1", student_api_key="vllm-key-0123456789")
    gw, rt = _gateway(gw_env, monkeypatch, settings, teacher=teacher.transport, student=student.transport)
    c = TestClient(gw.app)
    led = rt.ledger
    canary = _version(led, "canary", GOOD, pct=50)
    url = f"/ratchet/admin/versions/{canary}/promote"
    student.load = lambda body: (500, "out of GPU memory")
    r = c.post(url, json={**REASON, "to": "live", "load_adapter": True}, headers=ADMIN)
    failed = f"loading adapter student-v{canary} failed: HTTPStatusError 500; nothing changed"
    assert (r.status_code, r.json()["detail"]) == (502, failed)
    assert _state(led, canary) == ("canary", 50) and _actions(led) == []
    student.load = lambda body: (200, "")
    r = c.post(url, json={**REASON, "to": "live", "load_adapter": True}, headers=ADMIN)
    assert r.status_code == 200 and _state(led, canary) == ("live", 100)
    loads = student.calls_to("/load_lora_adapter")
    want = {"lora_name": f"student-v{canary}", "lora_path": f"/data/adapters/student-v{canary}"}
    assert [call.json for call in loads][-1] == want
    assert loads[-1].headers["authorization"] == "Bearer vllm-key-0123456789"

    # Without a student endpoint there is nothing to load into; in mock mode the fleet serves every version already.
    gw, rt = _gateway(gw_env, monkeypatch, _live(tmp_path / "no-student"))
    v = _version(rt.ledger, "canary", GOOD)
    r = c.post(f"/ratchet/admin/versions/{v}/promote", json={**REASON, "to": "live", "load_adapter": True}, headers=ADMIN)
    assert r.status_code == 409 and "RATCHET_STUDENT_BASE is not set" in r.json()["detail"]
    mock = gw_env()
    v = _version(mock.rt.ledger, "canary", GOOD)
    r = TestClient(mock.app).post(f"/ratchet/admin/versions/{v}/promote", json={**REASON, "to": "live", "load_adapter": True})
    assert r.status_code == 200 and mock.rt.ledger.live().id == v


# ---------- runtime switches ----------
def _router(led: Ledger, **settings: Any) -> tuple[Ratchet, RecordingProvider, RecordingProvider, list[int]]:
    """A router whose student (unanimous, confident) and teacher both answer A; `resolved` lists every version the
    student resolver was asked for."""
    teacher, student = RecordingProvider(A), RecordingProvider(A)
    resolved: list[int] = []

    def resolve(v):
        resolved.append(v.id)
        return student, v.adapter_uri

    s = Settings(teacher_model=OPEN_TEACHER, samples=3, audit_rate=0.0, **settings)
    return Ratchet(s, led, teacher, resolve, seed=3), teacher, student, resolved


@pytest.mark.req("FR-12.1")
def test_student_disabled_sends_every_request_to_the_teacher_and_stores_as_usual(tmp_path):
    led = Ledger(":memory:")
    r, teacher, student, resolved = _router(led)
    live = _version(led, "live", GOOD)
    _version(led, "shadow", GOOD)
    assert r.handle(_ask(1), TRIAGE).served_by == "student" and len(resolved) == 2  # the live student and the shadow
    resolved.clear()
    admin.set_flag(led, "student_disabled", True, reason="vLLM returns garbage after the upgrade", actor="cli:ops")
    stored = len(led.examples())
    res = r.handle(_ask(2), TRIAGE)
    assert (res.served_by, res.route, res.version) == ("teacher", "student_disabled", None)
    assert resolved == []  # no student and no shadow call: the switch stops every student call
    assert len(led.examples()) == stored + 1  # the teacher's answer is stored as on a cold start
    row = led.db.execute("SELECT route, student_version, shadow_version FROM requests ORDER BY id DESC").fetchone()
    assert tuple(row) == ("student_disabled", None, None)
    admin.set_flag(led, "student_disabled", False, reason="vLLM rolled back to 0.6", actor="cli:ops")
    assert r.handle(_ask(3), TRIAGE).served_by == "student" and live in resolved


@pytest.mark.req("FR-12.1")
def test_student_kill_switch_over_http(gw_env, tmp_path, monkeypatch):
    gw, rt, live, teacher, student = _student_gateway(gw_env, monkeypatch, tmp_path)
    c = TestClient(gw.app)
    assert _chat(c, 1)["served_by"] == "student"
    r = c.post("/ratchet/admin/student/disable", json=REASON, headers=ADMIN)
    assert r.json() | {"action_id": None} == {
        "action": "set_flag", "target": "student_disabled", "changed": True, "action_id": None,
        "before": {"value": False}, "after": {"value": True},
    }  # fmt: skip
    calls = len(student.calls_to("/chat/completions"))
    assert _chat(c, 2)["served_by"] == "teacher" and len(student.calls_to("/chat/completions")) == calls
    r = c.post("/ratchet/admin/student/disable", json=REASON, headers=ADMIN)
    assert (r.json()["changed"], r.json()["action_id"]) == (False, None)  # already off: nothing written
    stats = c.get("/ratchet/stats", headers=DASH).json()
    assert stats["controls"]["student_disabled"] is True
    assert stats["tasks"][0]["routes"] == {"student": 1, "student_disabled": 1}
    assert c.post("/ratchet/admin/student/enable", json=REASON, headers=ADMIN).json()["after"] == {"value": False}
    assert _chat(c, 3)["served_by"] == "student"
    assert [a["action"] for a in _actions(rt.ledger)] == ["set_flag", "set_flag"]
    flags = c.get("/ratchet/admin/flags", headers=ADMIN).json()
    assert flags["set_by"]["student_disabled"]["actor"] == "api:ops" and flags["set_by"]["student_disabled"]["value"] == "0"


@pytest.mark.req("FR-12.1", "INV-10", "INV-2")
def test_a_runtime_pin_keeps_a_task_on_the_teacher_and_out_of_training():
    led = Ledger(":memory:", golden_fraction=0.3)
    r, teacher, student, resolved = _router(led)
    _version(led, "live", GOOD)
    _examples(led, train=6, golden=4, task=TRIAGE)  # stored before the pin
    assert r.handle(_ask(1), TRIAGE).served_by == "student"
    resolved.clear()
    res = admin.pin_task(led, TRIAGE, reason="labels drift after the price change", actor="api:ops")
    assert res["after"] == {"pinned": True, "by_spec": False} and tasks.get(TRIAGE).pinned
    examples = [tuple(e) for e in led.examples()]
    for i in range(3):
        out = r.handle(_ask(f"pinned {i}"), TRIAGE)
        assert (out.served_by, out.route) == ("teacher", "pinned")
    out = r.handle(_ask("as an object"), tasks.REGISTRY[TRIAGE])  # Scout passes the Task itself: pinned all the same
    assert out.route == "pinned"
    assert resolved == [] and [tuple(e) for e in led.examples()] == examples  # no student call, nothing stored (INV-10)
    counts: dict[str, int] = {}
    assert dataset.rows(led, "train", counts=counts) == [] and counts == {"excluded_pinned": 6}  # left out of training
    assert tasks.get(TRIAGE).spec_hash == tasks.REGISTRY[TRIAGE].spec_hash  # who answers changed, not how answers compare

    # Spec-pinned tasks and unknown names.
    assert admin.pin_task(led, "research-plan", reason="already pinned", actor="api:ops")["changed"] is False
    with pytest.raises(admin.AdminConflict, match="pinned by its spec"):
        admin.unpin_task(led, "research-plan", reason="try to unpin a spec", actor="api:ops")
    with pytest.raises(admin.AdminNotFound, match="no registered task is named 'nope'"):
        admin.pin_task(led, "nope", reason="not a task", actor="api:ops")
    with pytest.raises(admin.AdminError, match="is not a task name"):
        admin.pin_task(led, "bad name!", reason="not a name", actor="api:ops")

    admin.unpin_task(led, TRIAGE, reason="new price list deployed", actor="api:ops")
    assert not tasks.get(TRIAGE).pinned and tasks.get(TRIAGE) is tasks.REGISTRY[TRIAGE]
    assert r.handle(_ask(2), TRIAGE).served_by == "student" and resolved
    counts = {}
    dataset.rows(led, "train", counts=counts)
    assert "excluded_pinned" not in counts  # back in Night School's sets (where the spec's own checks apply)
    assert [a["action"] for a in _actions(led)] == ["pin_task", "unpin_task"]


@pytest.mark.req("FR-12.1")
def test_task_pins_over_http_show_in_stats_and_the_task_list(gw_env, tmp_path, monkeypatch):
    gw, rt, live, teacher, student = _student_gateway(gw_env, monkeypatch, tmp_path)
    c = TestClient(gw.app)
    r = c.post(f"/ratchet/admin/tasks/{TRIAGE}/pin", json=REASON, headers=ADMIN)
    assert r.status_code == 200 and r.json()["after"]["pinned"] is True
    calls = len(student.calls_to("/chat/completions"))
    assert _chat(c, 1)["served_by"] == "teacher" and len(student.calls_to("/chat/completions")) == calls
    assert c.get("/ratchet/stats", headers=DASH).json()["controls"]["pinned_tasks"] == [TRIAGE]
    entry = c.get(f"/ratchet/tasks/{TRIAGE}", headers=DASH).json()
    assert (entry["pinned"], entry["runtime_pinned"]) == (False, True)  # the spec is unchanged; the pin is on top
    assert c.post("/ratchet/admin/tasks/not-a-task/pin", json=REASON, headers=ADMIN).status_code == 404
    assert c.request("DELETE", f"/ratchet/admin/tasks/{TRIAGE}/pin", json=REASON, headers=ADMIN).status_code == 200
    assert _chat(c, 2)["served_by"] == "student"
    assert c.get(f"/ratchet/tasks/{TRIAGE}", headers=DASH).json()["runtime_pinned"] is False


@pytest.mark.req("FR-12.1")
def test_put_audit_rate_changes_behaviour_without_a_restart(gw_env, tmp_path, monkeypatch):
    gw, rt, live, teacher, student = _student_gateway(gw_env, monkeypatch, tmp_path)  # RATCHET_AUDIT_RATE=0
    c = TestClient(gw.app)

    def audited(tag: object) -> int:
        assert _chat(c, tag)["served_by"] == "student"
        return int(rt.ledger.db.execute("SELECT audited FROM requests ORDER BY id DESC").fetchone()[0])

    assert [audited(i) for i in range(3)] == [0, 0, 0]
    r = c.put("/ratchet/admin/settings", json={"audit_rate": 1, **REASON}, headers=ADMIN)
    assert r.json()["after"] == {"value": 1.0}
    assert [audited(f"after {i}") for i in range(3)] == [1, 1, 1]  # every answer audited, no restart
    assert c.put("/ratchet/admin/settings", json={"audit_rate": None, **REASON}, headers=ADMIN).json()["after"] == {"value": None}
    assert audited("default again") == 0
    for bad in (1.5, -0.1, "0.5", True):
        r = c.put("/ratchet/admin/settings", json={"audit_rate": bad, **REASON}, headers=ADMIN)
        assert r.status_code == 400 and "audit_rate must be a number from 0 to 1" in r.json()["detail"]
    assert c.put("/ratchet/admin/settings", json=REASON, headers=ADMIN).status_code == 400  # audit_rate is required

    # 0 while a canary is in flight would leave it with no evidence either way: refused unless forced.
    canary = _version(rt.ledger, "canary", GOOD)
    r = c.put("/ratchet/admin/settings", json={"audit_rate": 0, **REASON}, headers=ADMIN)
    assert r.status_code == 409 and f"v{canary} is a canary" in r.json()["detail"]
    r = c.put("/ratchet/admin/settings", json={"audit_rate": 0, "force": True, **REASON}, headers=ADMIN)
    assert r.status_code == 200 and rt.ledger.flags().audit_rate == 0.0
    # And with audits off, a new canary needs force too.
    admin.retire(rt.ledger, canary, reason="make room for the next one", actor="cli:ops")
    shadow = _version(rt.ledger, "shadow", GOOD)
    with pytest.raises(admin.AdminConflict, match="the audit rate is 0"):
        admin.promote(rt.ledger, shadow, "canary", reason="end the shadow early", actor="cli:ops")


@pytest.mark.req("FR-12.1", "INV-5")
def test_training_paused_skips_the_night_before_anything_else():
    led = Ledger(":memory:", golden_fraction=0.3)
    _examples(led, train=200, golden=20)
    admin.set_flag(led, "training_paused", True, reason="GPU quota spent for the month", actor="cli:ops")
    trainer, evaluator = RecordingTrainer(), RecordingEvaluator(REJECTED_EVAL)
    rep = run_night(led, trainer, evaluator, ReleaseGate(led), BASE_MODEL)
    assert (rep.trained, rep.version, rep.reason) == (False, None, "training paused")
    assert trainer.calls == [] and evaluator.calls == [] and led.versions() == []
    assert _kinds(led, "skip")[-1]["detail"] == "training paused by an admin; no training tonight"
    admin.set_flag(led, "training_paused", False, reason="new month", actor="cli:ops")
    assert run_night(led, trainer, evaluator, ReleaseGate(led), BASE_MODEL).trained and len(trainer.calls) == 1


@pytest.mark.req("FR-12.1", "FR-6.6", "FR-6.7")
def test_frozen_releases_hold_every_promotion_but_never_a_rollback_or_an_alert():
    policy = ReleasePolicy(min_audits=20, min_shadow=5)
    led = Ledger(":memory:")
    gate = ReleaseGate(led, policy)
    admin.set_flag(led, "releases_frozen", True, reason="demo day: nothing moves", actor="cli:ops")

    # A cold start while frozen shadows the teacher instead of taking 10% of traffic.
    first = led.create_version(BASE_MODEL, "student-v1", 100, "h1", dict(GOOD))
    assert gate.admit(first) and _state(led, first) == ("shadow", 0)
    assert _kinds(led, "shadow")[-1]["detail"].endswith("shadowing the teacher (releases frozen)")
    _shadow(led, first, 10)
    gate.tick()
    assert _state(led, first) == ("shadow", 0)  # ready, but held
    admin.set_flag(led, "releases_frozen", False, reason="demo is over", actor="cli:ops")
    gate.tick()
    assert _state(led, first) == ("canary", 10)  # promoted on the evidence it gathered while held

    admin.set_flag(led, "releases_frozen", True, reason="freeze again", actor="cli:ops")
    _audit(led, first, 20)
    gate.tick()
    assert _state(led, first) == ("canary", 10)  # a good canary does not advance
    led.set_version(first, "live", 100)
    bad = _version(led, "canary", GOOD)
    _audit(led, bad, 20, agree_every=2)  # 10 of 20: strong evidence it is worse
    _audit(led, first, 20, agree_every=2)
    gate.tick()
    assert _state(led, bad) == ("rolled_back", 0)  # rollback runs whatever the operator holds
    assert len(_kinds(led, "rollback")) == 1 and len(_kinds(led, "alert")) == 1  # and so does the live alert

    # An audit rate of 0 holds new canaries too: with no audits they could neither advance nor be rolled back.
    admin.set_flag(led, "releases_frozen", False, reason="unfreeze", actor="cli:ops")
    admin.set_flag(led, "audit_rate", 0.0, reason="audits off for a cost test", actor="cli:ops")
    shadow = _version(led, "shadow", GOOD)
    _shadow(led, shadow, 10, live_vid=first)
    gate.tick()
    assert _state(led, shadow) == ("shadow", 0)


@pytest.mark.req("FR-12.1", "FR-9.3", "FR-6.6")
def test_a_frozen_gate_in_a_corrupt_deploy_simulation(tmp_path, monkeypatch):
    """The simulator on a ledger file, with an operator freezing releases from another connection (as the CLI does)
    the moment v1 becomes a canary on its cold start: deployed corrupted, it is still rolled back and the next student
    only shadows the teacher; deployed sound, it stays at 10% instead of advancing to 50%."""
    import sim.simulate as simulate

    def frozen_run(target: str, *argv: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        db = str(tmp_path / f"{target}-{'-'.join(argv)}.db")
        build, cli = simulate.build, []

        def build_with_operator(args: Any, db: str = db) -> Any:
            settings, ledger, fleet, ratchet, gate = build(args, db)
            real_tick = gate.tick

            def tick() -> None:
                real_tick()
                canary = ledger.by_state("canary")
                if canary is not None and canary.adapter_uri == target and not cli:
                    cli.append(Ledger(db))  # another connection: the gateway sees the switch through data_version
                    admin.set_flag(cli[0], "releases_frozen", True, reason=f"hold {target} where it is", actor="cli:ops")

            gate.tick = tick
            return settings, ledger, fleet, ratchet, gate

        monkeypatch.setattr(simulate, "build", build_with_operator)
        args = simulate.parser().parse_args(["--drift-day", "0", *argv])
        args.drift_day = None
        res = simulate.run(args, db)
        monkeypatch.setattr(simulate, "build", build)
        assert cli, f"{target} never became a canary"
        return res, res["events"]

    res, events = frozen_run("student-v1", "--days", "3", "--requests", "600", "--bad-deploy-version", "1")
    states = {v["id"]: v["state"] for v in res["versions"]}
    frozen_at = next(i for i, e in enumerate(events) if e["kind"] == "admin")
    assert states[1] == "rolled_back"
    assert any(e["kind"] == "rollback" and e["detail"].startswith("v1 ") for e in events[frozen_at:])
    assert not any(e["kind"] in ("canary", "live", "live_retired") for e in events[frozen_at:])  # nothing promoted
    assert all(state in ("shadow", "rejected") for vid, state in states.items() if vid != 1)

    # Without the freeze this run advances v1 to 50% on day 3 (simulation).
    res, events = frozen_run("student-v1", "--days", "3", "--requests", "600")
    frozen_at = next(i for i, e in enumerate(events) if e["kind"] == "admin")
    assert {v["id"]: (v["state"], v["traffic_pct"]) for v in res["versions"]} == {1: ("canary", 10)}
    assert not any(e["kind"] in ("canary", "live") for e in events[frozen_at:])
    assert [e["detail"] for e in events[frozen_at:] if e["kind"] == "skip"]  # nights skip: a release is in flight (INV-5)


@pytest.mark.req("FR-12.1")
def test_the_tick_endpoint_runs_the_gate_and_records_who_ran_it(gw_env, tmp_path, monkeypatch):
    gw, rt = _gateway(gw_env, monkeypatch, _live(tmp_path))
    c = TestClient(gw.app)
    canary = _version(rt.ledger, "canary", GOOD)
    _audit(rt.ledger, canary, 20, agree_every=2)
    r = c.post("/ratchet/admin/tick", json={"reason": "check the canary now"}, headers=ADMIN)
    assert r.status_code == 200
    assert r.json() | {"action_id": None} == {
        "action": "tick", "target": "gate", "changed": True, "action_id": None, "moved": [f"v{canary}"],
        "before": {f"v{canary}": {"state": "canary", "traffic_pct": 10}},
        "after": {f"v{canary}": {"state": "rolled_back", "traffic_pct": 0}},
    }  # fmt: skip
    assert [e["kind"] for e in rt.ledger.events()][-2:] == ["rollback", "admin"]
    r = c.post("/ratchet/admin/tick", headers=ADMIN)  # no body: the reason is optional for a tick
    assert (r.json()["changed"], r.json()["moved"]) == (False, [])
    assert [(a["action"], a["reason"]) for a in _actions(rt.ledger)] == [("tick", "check the canary now"), ("tick", None)]


# ---------- the CLI ----------
@pytest.mark.req("FR-12.1")
def test_cli_changes_reach_a_running_ledger_through_data_version(tmp_path, capsys, monkeypatch):
    db = str(tmp_path / "ratchet.db")
    gateway = Ledger(db)  # the running gateway's ledger, never reopened below
    r, teacher, student, resolved = _router(gateway)
    live = _version(gateway, "live", GOOD)
    canary = _version(gateway, "canary", GOOD)
    assert gateway.flags().student_disabled is False and gateway.by_state("canary").id == canary  # both cached now
    monkeypatch.setattr(admin.getpass, "getuser", lambda: "harsh")
    settings = Settings(mode="live", db_path=db, tasks_file="")

    def cli(*argv: str) -> int:
        return admin.main(list(argv), settings=settings)

    assert cli("flag", "student_disabled", "on", "--reason", "endpoint upgrade at 22:00") == 0
    assert gateway.flags().student_disabled is True  # another connection's commit, seen without a restart
    assert r.handle(_ask(1), TRIAGE).route == "student_disabled"
    assert cli("rollback", str(canary), "--reason", "audits look wrong") == 0
    assert gateway.by_state("canary") is None
    assert cli("pin", TRIAGE, "--reason", "keep it on the teacher") == 0
    assert TRIAGE in gateway.flags().pinned
    capsys.readouterr()
    assert cli("audit-log", "--json") == 0
    log = json.loads(capsys.readouterr().out)
    assert [(a["actor"], a["action"], a["target"]) for a in log] == [
        ("cli:harsh", "pin_task", TRIAGE), ("cli:harsh", "rollback", f"v{canary}"), ("cli:harsh", "set_flag", "student_disabled"),
    ]  # fmt: skip
    assert all(a["request_id"] is None for a in log)

    assert cli("versions") == 0
    out = capsys.readouterr().out
    assert f"{live:>4}  live" in out and "switches: student_disabled" in out and f"pinned at runtime: {TRIAGE}" in out
    assert cli("rollback", str(canary), "--reason", "again") == 1  # a refused move exits 1
    assert "refused: " in capsys.readouterr().err
    assert cli("flag", "audit_rate", "2", "--reason", "too many") == 2  # a bad input exits 2
    assert cli("flag", "audit_rate", "nan", "--reason", "not a number") == 2
    assert cli("rollback", str(live), "--reason", "x") == 2  # reason too short
    assert cli("flag", "audit_rate", "0.25", "--reason", "more audits this week") == 0
    assert gateway.flags().audit_rate == 0.25
    assert cli("flag", "audit_rate", "default", "--reason", "back to the configured rate") == 0
    assert gateway.flags().audit_rate is None
    assert admin.main(["--db", str(tmp_path / "missing.db"), "versions"], settings=settings) == 2
    assert not (tmp_path / "missing.db").exists()  # the CLI never creates a ledger where none was


@pytest.mark.req("FR-12.1")
def test_bad_flag_rows_written_by_hand_never_fail_a_request():
    led = Ledger(":memory:")
    led.db.execute("INSERT INTO runtime_flags (name, value) VALUES ('audit_rate', 'lots'), ('student_disabled', 'yes')")
    led.db.execute("INSERT INTO runtime_flags (name, value) VALUES ('unknown_flag', '1')")
    led.db.commit()
    assert led.flags().as_dict() == {
        "student_disabled": False, "releases_frozen": False, "training_paused": False, "audit_rate": None, "pinned_tasks": [],
    }  # fmt: skip
    with pytest.raises(ValueError, match="flag must be one of"):
        led.set_flag("kill_everything", True, "cli:ops")
    with pytest.raises(ValueError, match="audit_rate must be a number from 0 to 1"):
        led.set_flag("audit_rate", float("nan"), "cli:ops")
    with pytest.raises(ValueError, match="limit must be a whole number"):
        led.admin_actions(True)


@pytest.mark.req("FR-12.1")
def test_requests_from_many_threads_while_an_operator_flips_switches():
    """Scout calls the router from several threads: switches and pins flipped meanwhile never fail a request, and each
    request sees one snapshot of them (a route that matches a switch's state)."""
    led = Ledger(":memory:")
    r, *_ = _router(led)
    _version(led, "live", GOOD)
    errors: list[BaseException] = []
    routes: list[str] = []

    def client(n: int) -> None:
        try:
            for i in range(25):
                routes.append(r.handle(_ask(f"{n}-{i}"), TRIAGE).route)
        except BaseException as e:  # noqa: BLE001 - reported below
            errors.append(e)

    threads = [threading.Thread(target=client, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for i in range(10):
        admin.set_flag(led, "student_disabled", i % 2 == 0, reason=f"flip {i}", actor="cli:ops")
        (admin.pin_task if i % 3 == 0 else admin.unpin_task)(led, TRIAGE, reason=f"pin flip {i}", actor="cli:ops")
    for t in threads:
        t.join()
    assert errors == [] and len(routes) == 100 and set(routes) <= {"student", "student_disabled", "pinned"}
    n = led.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
    assert n == 100


def test_admin_tables_exist_on_an_old_ledger_file(tmp_path):
    """New tables need no migration: an old file gains them when it is opened."""
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE versions (id INTEGER PRIMARY KEY, state TEXT)")
    con.commit()
    con.close()
    led = Ledger(str(path))
    names = {r[0] for r in led.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"admin_actions", "runtime_flags", "task_overrides"} <= names
