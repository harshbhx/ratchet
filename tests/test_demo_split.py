"""The live demo's split (FR-12.34, ADR 0147): a random share of eligible requests answered by the hosted Nano whatever
its confidence, always labelled route `demo_split`. Offline: `RecordingProvider` and `FakeOpenAIServer`, never a real
call."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fakes import OPEN_TEACHER, FakeOpenAIServer, RecordingProvider, _version
from fastapi.testclient import TestClient
from test_hosted_student import (
    CHAT,
    READ_MSGS,
    TRIAGE_ANSWER,
    TRIAGE_MSGS,
    _answer,
    _router,
    _row,
    _settings,
    _tf,
)

from ratchet import hosted, tasks, taskspec
from ratchet.config import ERROR, Settings
from ratchet.ledger import HOSTED_STATE, Ledger
from ratchet.providers import GenParams
from ratchet.router import DEMO_SPLIT_ROUTE, ROUTES, Ratchet, known_route
from ratchet.scout.agents import Call, _step

FREE = [{"role": "user", "content": "In one sentence, what is LoRA?"}]
OTHER_ANSWER = json.dumps({"relevant": False, "source_type": "other", "reason": "Off topic."})
TOOLS = [{"type": "function", "function": {"name": "get_weather", "parameters": {"type": "object", "properties": {}}}}]


def _split_router(
    share: float = 1.0, audit: float = 0.0, **kw: Any
) -> tuple[Ledger, Ratchet, RecordingProvider, RecordingProvider]:
    """A router with the hosted row as shipped (no task serves: the normal cascade sends everything to the teacher)."""
    led, r, teacher, nano = _router(demo_split=share, demo_split_audit=audit, **kw)
    led.ensure_hosted(hosted.MODEL, hosted.eval_record())
    return led, r, teacher, nano


def _examples(led: Ledger) -> list[dict[str, Any]]:
    return led.examples()


@pytest.mark.req("FR-12.34")
def test_split_zero_changes_nothing_and_draws_nothing():
    """With the split off the cascade is the hosted student's, as shipped: nothing is sampled for a task its records do
    not serve, and the split's generator is never drawn."""
    led, r, teacher, nano = _split_router(0.0)

    def boom():
        raise AssertionError("the split drew a number with the split off")

    r.split_rng.random = boom  # type: ignore[method-assign]
    for msgs, task in ((TRIAGE_MSGS, "source-triage"), (READ_MSGS, "page-read"), (FREE, "default")):
        res = r.handle(msgs, task)
        assert (res.served_by, res.route) == ("teacher", "task_disabled")
    assert nano.calls == []
    assert led.demo_split_stats()["requests"] == 0


@pytest.mark.req("FR-12.34")
def test_split_one_serves_every_eligible_request_from_the_nano_and_says_so():
    """Triage, reader and free text are eligible: the hosted Nano answers each with one sample, whatever its
    confidence, route `demo_split`, naming the model and no version; the teacher is not called, nothing is stored, and the
    row is held to no threshold."""
    led, r, teacher, nano = _split_router(1.0)
    for msgs, task in ((TRIAGE_MSGS, "source-triage"), (READ_MSGS, "page-read"), (FREE, "default")):
        res = r.handle(msgs, task)
        assert (res.served_by, res.route, res.version, res.model) == ("student", DEMO_SPLIT_ROUTE, None, hosted.MODEL)
        assert res.escalated is False and res.stored is False
        row = _row(led)
        assert (row["route"], row["served_by"], row["student_model"], row["threshold"], row["stored"]) == (
            DEMO_SPLIT_ROUTE,
            "student",
            hosted.MODEL,
            None,
            0,
        )
        assert row["student_version"] == led.by_state(HOSTED_STATE).id
    assert teacher.calls == [] and _examples(led) == []
    assert [c.kwargs["n"] for c in nano.calls] == [1, 1, 1]
    # Real Nano price against the all-Ultra baseline: the saving the page shows.
    stats = led.demo_split_stats()
    assert stats["requests"] == 3 and stats["share"] == 1.0
    assert 0 < stats["cost_usd"] < stats["baseline_usd"] and stats["saved_usd"] > 0


@pytest.mark.req("FR-12.34", "INV-10")
def test_pinned_tool_teacher_only_and_other_requests_are_never_split():
    """Pinned tasks (Scout's planner and writer), tool requests, `n` choices, an unregistered task under
    RATCHET_UNKNOWN_TASKS=teacher, a task with critical classes and a student an operator turned off never go to the
    hosted model, whatever the split."""
    led, r, teacher, nano = _split_router(1.0)
    assert tasks.get("research-plan").pinned and tasks.get("research-write").pinned
    assert r.handle(FREE, "research-plan").route == "pinned"
    assert r.handle(FREE, "research-write").route == "pinned"
    assert r.handle(FREE, "default", params=GenParams(tools=TOOLS, tool_choice="auto")).route == "tools"
    assert r.handle(FREE, "default", n=2).route != DEMO_SPLIT_ROUTE

    spec = {
        "name": "moderation",
        "fields": {"flagged": {"type": "bool"}},
        "routing": {"critical": [{"field": "flagged", "equals": True, "max_miss_rate": 0.1}]},
    }
    tasks.register(taskspec.parse_spec(spec, "file"))
    assert r.handle(FREE, "moderation").route != DEMO_SPLIT_ROUTE

    led.set_flag("student_disabled", True, "cli:test")
    assert r.handle(FREE, "default").route == "student_disabled"
    led.set_flag("student_disabled", None, "cli:test")

    r.unknown_tasks = "teacher"
    assert r.handle(FREE, "no-such-task").route == "unknown_task"
    assert led.demo_split_stats()["requests"] == 0
    assert nano.calls == []


@pytest.mark.req("FR-12.34")
def test_split_needs_a_hosted_row_and_follows_the_draw():
    """Without the hosted row there is nothing to send a request to; with it, a draw under the share picks the Nano
    and a draw over it runs the normal cascade."""
    led, r, teacher, nano = _router(demo_split=0.4)
    assert r.handle(FREE, "default").route == "cold_start"  # no row: the normal cascade
    led.ensure_hosted(hosted.MODEL, hosted.eval_record())
    # One draw picks the request; a forced answer takes a second for its audit (the share 0 never audits).
    draws = iter([0.39, 0.9, 0.4, 0.99, 0.0, 0.9])
    r.split_rng.random = lambda: next(draws)  # type: ignore[method-assign]
    routes = [r.handle(FREE, "default").route for _ in range(4)]
    assert routes == [DEMO_SPLIT_ROUTE, "task_disabled", "task_disabled", DEMO_SPLIT_ROUTE]


@pytest.mark.req("FR-12.34", "INV-2", "INV-16")
def test_forced_answers_are_audited_and_only_ultras_answer_is_stored():
    """A forced answer is audited at RATCHET_DEMO_SPLIT_AUDIT: Ultra's answer judges the Nano's and is the only one
    stored (INV-2). A Nano answer Ultra disagrees with is counted as a disagreement; the Nano's text is never a label."""
    led, r, teacher, nano = _split_router(1.0, audit=1.0)
    nano.text = lambda m, model: OTHER_ANSWER
    res = r.handle(TRIAGE_MSGS, "source-triage")
    assert (res.route, res.audited) == (DEMO_SPLIT_ROUTE, True) and json.loads(res.text) == json.loads(OTHER_ANSWER)
    row = _row(led)
    assert (row["audited"], row["audit_agree"], row["stored"], row["label_p"]) == (1, 0, 1, 1.0)
    (ex,) = _examples(led)
    assert json.loads(ex["completion"]) == json.loads(TRIAGE_ANSWER) and ex["teacher_model"] == OPEN_TEACHER
    stats = led.demo_split_stats()
    assert (stats["audits"], stats["agreed"], stats["agreement"]) == (1, 0, 0.0)
    nano.text = lambda m, model: _answer(m, model)
    r.handle(TRIAGE_MSGS, "source-triage")
    assert led.demo_split_stats()["agreement"] == 0.5


@pytest.mark.req("FR-12.34")
def test_audit_rate_is_sampled_from_the_splits_own_generator():
    """RATCHET_DEMO_SPLIT_AUDIT picks the audited share from the split's own draws: with the audit off the teacher is
    not called, and the router's seeded generator is never advanced (the reference runs must not move)."""
    led, r, teacher, nano = _split_router(1.0, audit=0.25)
    state = r.rng.getstate()
    draws = iter([0.0, 0.1, 0.0, 0.24, 0.0, 0.25])  # (split, audit) pairs: audited under 0.25, not at it
    r.split_rng.random = lambda: next(draws)  # type: ignore[method-assign]
    audited = [r.handle(TRIAGE_MSGS, "source-triage").audited for _ in range(3)]
    assert audited == [True, True, False]
    assert r.rng.getstate() == state and len(teacher.calls) == 2
    led2, r2, teacher2, _ = _split_router(1.0, audit=0.0)
    assert r2.handle(TRIAGE_MSGS, "source-triage").audited is False and teacher2.calls == []


@pytest.mark.req("FR-12.34")
def test_forced_answers_never_reach_a_versions_release_statistics():
    """With a live version and a split of 1 the hosted row answers: the live version's audit tallies stay empty (the row's
    student_version is the hosted row's, its model the Nano)."""
    led, r, teacher, nano = _split_router(1.0, audit=1.0)
    live = _version(led, "live", {"threshold": 0.5})
    r.handle(TRIAGE_MSGS, "source-triage")
    row = _row(led)
    assert row["student_version"] != live and row["route"] == DEMO_SPLIT_ROUTE
    assert led.version_live_quality(live) == (0, None)
    assert led.version_live_quality(led.by_state(HOSTED_STATE).id) == (0, None)
    assert not [e for e in led.events() if e["kind"] == "unattributed"]


@pytest.mark.req("FR-12.34")
def test_an_invalid_forced_answer_goes_to_the_teacher():
    """A Nano answer the task refuses is never served: the teacher answers as for any unsure student."""
    led, r, teacher, nano = _split_router(1.0)
    nano.text = lambda m, model: "not json"
    res = r.handle(TRIAGE_MSGS, "source-triage")
    assert (res.served_by, res.route, res.escalated) == ("teacher", "confidence", True)
    assert led.demo_split_stats()["requests"] == 0


@pytest.mark.req("FR-12.34")
def test_demo_split_is_a_known_route():
    assert DEMO_SPLIT_ROUTE in ROUTES and known_route("demo_split")


# ---------- the gateway ----------
@pytest.mark.req("FR-12.34")
def test_live_gateway_labels_forced_answers(gw_env, tmp_path, monkeypatch):
    """The response says `ratchet.route = demo_split` and names the Nano; `/ratchet/stats` carries the split's block and
    `student_serving` names the split; with the split off the route is the normal one."""
    gw = gw_env()
    tf = FakeOpenAIServer(_tf)
    rt = gw.Runtime(_settings(tmp_path, demo_split=1.0, demo_split_audit=1.0), transports={"teacher": tf.transport})
    monkeypatch.setattr(gw, "rt", rt)
    c = TestClient(gw.app)
    body = c.post(CHAT, json={"messages": TRIAGE_MSGS}, headers={"X-Ratchet-Task": "source-triage"}).json()
    assert (body["ratchet"]["route"], body["ratchet"]["served_by"], body["model"]) == (DEMO_SPLIT_ROUTE, "student", hosted.MODEL)
    assert body["ratchet"]["student_version"] is None
    assert [call.json["model"] for call in tf.calls] == [hosted.MODEL, OPEN_TEACHER]  # the Nano, then Ultra's audit
    stats = c.get("/ratchet/stats").json()
    block = stats["demo_split"]
    assert (block["enabled"], block["rate"], block["audit_rate"], block["requests"], block["audits"], block["agreed"]) == (
        True,
        1.0,
        1.0,
        1,
        1,
        1,
    )
    assert block["share"] == 1.0 and block["savings_pct"] is not None
    serving = stats["student_serving"]
    assert (serving["kind"], serving["model"], serving["demo_split"]) == ("hosted_fallback", hosted.MODEL, 1.0)
    assert stats["tasks"][0]["routes"] == {DEMO_SPLIT_ROUTE: 1}


@pytest.mark.req("FR-12.34")
def test_live_gateway_without_the_split_reports_it_off(gw_env, tmp_path, monkeypatch):
    gw = gw_env()
    tf = FakeOpenAIServer(_tf)
    rt = gw.Runtime(_settings(tmp_path), transports={"teacher": tf.transport})
    monkeypatch.setattr(gw, "rt", rt)
    c = TestClient(gw.app)
    body = c.post(CHAT, json={"messages": TRIAGE_MSGS}, headers={"X-Ratchet-Task": "source-triage"}).json()
    assert body["ratchet"]["route"] == "task_disabled"
    block = c.get("/ratchet/stats").json()["demo_split"]
    assert (block["enabled"], block["rate"], block["requests"], block["share"]) == (False, 0.0, 0, None)
    assert "demo_split" not in c.get("/ratchet/stats").json()["student_serving"]


# ---------- Scout ----------
@pytest.mark.req("FR-12.34")
def test_scout_steps_carry_the_route():
    call = Call("{}", "student", hosted.MODEL, 0.9, False, 0.0001, 0.001, 5.0, DEMO_SPLIT_ROUTE)
    step = _step("triage", "source-triage", call, {"id": 1})
    assert step["detail"] == {"id": 1, "route": DEMO_SPLIT_ROUTE}  # inside `detail`: the step's own keys do not change
    assert "route" not in _step("triage", "source-triage", Call("{}", "teacher", "m", None, False, 0, 0, 0), {"id": 1})["detail"]
    assert "route" not in _step("searcher", None, None, {}, model="tavily")


# ---------- configuration ----------
@pytest.mark.req("FR-12.34", "FR-12.30")
def test_demo_split_config_checks(tmp_path):
    """A share from 0 to 1, live mode only, and only with the hosted student on; its audit rate from 0 to 1."""

    def issues(**overrides: Any) -> set[tuple[str, str]]:
        return {(i.level, i.var) for i in _settings(tmp_path, **overrides).validate({}) if "SPLIT" in i.var}

    assert issues() == set() and issues(demo_split=0.4) == set()
    assert (ERROR, "RATCHET_DEMO_SPLIT") in issues(demo_split=1.5)
    assert (ERROR, "RATCHET_DEMO_SPLIT") in issues(demo_split=-0.1)
    assert (ERROR, "RATCHET_DEMO_SPLIT_AUDIT") in issues(demo_split_audit=2.0)
    assert (ERROR, "RATCHET_DEMO_SPLIT") in issues(demo_split=0.4, hosted_student=False)
    assert (ERROR, "RATCHET_DEMO_SPLIT") in issues(demo_split=0.4, mode="mock")
    assert Settings().demo_split == 0 and Settings().demo_split_audit == 0.25
    assert "RATCHET_DEMO_SPLIT" in (Path(__file__).parents[1] / "docs" / "CONFIGURATION.md").read_text(encoding="utf-8")
