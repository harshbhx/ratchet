"""Scout, the multi-agent research demo: pipeline, pinned tasks, research workload, gateway routes, guard, Tavily."""

from __future__ import annotations

import json
from argparse import Namespace

import httpx
import pytest
from fakes import FakeClock
from fastapi.testclient import TestClient

from ratchet import tasks
from ratchet.scout.agents import Call, Scout
from ratchet.scout.guard import Guard
from ratchet.scout.prompts import EXAMPLE_QUESTIONS
from ratchet.scout.search import MockSearch, TavilySearch
from sim import research
from sim.simulate import parser, run


def _events(gw, question=EXAMPLE_QUESTIONS[0]):
    out = []
    gw.rt.scout().run(question, out.append)
    return out


@pytest.mark.req("FR-9.5")
def test_examples_match_the_research_world():
    assert research.EXAMPLES == EXAMPLE_QUESTIONS
    for q in research.QUESTIONS:  # every planned query returns simulated results
        assert all(research.search(qq) for qq in q.queries)


@pytest.mark.req("FR-10.1", "FR-10.4")
def test_scout_run_emits_the_documented_events(gw):
    ev = _events(gw)
    assert ev[0]["type"] == "start" and ev[-1]["type"] == "done"
    steps = [e for e in ev if e["type"] == "step"]
    agents = [s["agent"] for s in steps]
    assert agents[0] == "planner" and agents[-1] == "writer"
    assert {"planner", "searcher", "triage", "reader", "writer"} <= set(agents)
    for s in steps:
        keys = "type agent task served_by model confidence escalated cost_usd baseline_usd latency_ms detail"
        assert set(s) == set(keys.split())
        assert s["served_by"] in ("student", "teacher", "tool")
    triage = [s for s in steps if s["agent"] == "triage"]
    reads = [s for s in steps if s["agent"] == "reader"]
    assert reads and len(reads) == sum(s["detail"]["relevant"] for s in triage)
    done = ev[-1]
    assert done["answer"] and done["citations"]
    assert done["totals"]["llm_calls"] == len(steps) - agents.count("searcher")
    assert done["totals"]["cost_usd"] == pytest.approx(sum(s["cost_usd"] for s in steps))


@pytest.mark.req("FR-2.9", "FR-10.2", "INV-10")
def test_pinned_tasks_go_to_teacher_and_are_never_training_data(gw):
    assert tasks.get("research-plan").pinned and tasks.get("research-write").pinned
    assert not tasks.get("source-triage").pinned
    _events(gw)
    stored = {r["task"] for r in gw.rt.ledger.examples()}
    assert stored == {"source-triage", "page-read"}
    by_task = {t["task"]: t for t in gw.rt.ledger.stats_by_task()}
    assert by_task["research-plan"]["student_share"] == 0 and by_task["research-write"]["requests"] == 1


@pytest.mark.req("FR-10.2", "FR-9.5")
def test_student_learns_scout_calls_over_simulated_days(gw):
    for _ in range(4):
        gw.rt.simulate_day(1500, "mixed")
    assert gw.rt.ledger.live() is not None
    done = _events(gw)[-1]["totals"]
    assert done["student_calls"] >= 0.6 * done["llm_calls"]
    assert done["savings_pct"] > 0.3


@pytest.mark.req("FR-9.5")
def test_research_workload_simulation_learns():
    args = parser().parse_args(["--workload", "research", "--days", "6", "--requests", "1200", "--out", "/dev/null"])
    days = run(Namespace(**{**vars(args), "drift_day": 5}))["days"]
    assert days[0]["student_share"] == 0
    assert days[-1]["student_share"] > 0.8 and days[-1]["savings_pct"] > 0.6


@pytest.mark.req("FR-1.9", "FR-10.4", "FR-7.7", "FR-7.8")
def test_gateway_scout_routes(gw):
    c = TestClient(gw.app)
    for page in ("/", "/scout", "/dashboard"):
        assert c.get(page).status_code == 200
    ex = c.get("/scout/examples").json()
    assert ex["mode"] == "mock" and ex["free_text"] is False and ex["examples"] == EXAMPLE_QUESTIONS
    r = c.post("/scout/run", json={"question": EXAMPLE_QUESTIONS[2]})
    assert r.status_code == 200 and r.json()["events"][-1]["type"] == "done"
    assert c.post("/scout/run", json={"question": "Who won the match yesterday?"}).status_code == 400
    assert c.post("/scout/run", json={"question": "  "}).status_code == 400
    gw.rt.settings.mock_pace = 0
    with c.stream("GET", "/scout/stream", params={"q": EXAMPLE_QUESTIONS[1]}) as s:
        data = [json.loads(line[5:]) for line in s.iter_lines() if line.startswith("data:")]
    assert s.status_code == 200 and data[0]["type"] == "start" and data[-1]["type"] == "done"
    with c.stream("GET", "/scout/stream", params={"q": "not an example"}) as s:
        data = [json.loads(line[5:]) for line in s.iter_lines() if line.startswith("data:")]
    assert s.status_code == 200 and [d["type"] for d in data] == ["error"]
    st = c.get("/scout/status").json()
    assert st["mode"] == "mock" and st["budget_usd"] is None and st["runs_today"] >= 2
    assert "tasks" in c.get("/ratchet/stats").json()
    assert c.post("/ratchet/demo/day?requests=200&workload=research").json()["workload"] == "research"
    assert c.post("/ratchet/demo/day?workload=nope").status_code == 400


@pytest.mark.req("FR-10.5")
def test_rate_limit_returns_429(gw):
    c = TestClient(gw.app)
    gw.rt.guard = Guard(None, 2)
    for _ in range(2):
        assert c.post("/scout/run", json={"question": EXAMPLE_QUESTIONS[0]}).status_code == 200
    assert c.post("/scout/run", json={"question": EXAMPLE_QUESTIONS[0]}).status_code == 429


@pytest.mark.req("FR-10.5", "INV-11")
def test_guard_budget_and_hourly_window():
    clock = FakeClock()
    g = Guard(1.0, 2, clock=clock)
    assert g.admit("a") is None and g.admit("a") is None
    assert "Rate limit" in g.admit("a")
    assert g.admit("b") is None
    clock.advance(3601)
    assert g.admit("a") is None
    g.add_spend(1.5)
    assert "budget" in g.admit("c")
    clock.advance(86400)  # a new UTC day resets the budget
    assert g.admit("c") is None


@pytest.mark.req("FR-10.3")
def test_tavily_search_sends_bearer_and_parses_results():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer tvly-test"
        body = json.loads(request.content)
        assert body["query"] == "lora" and body["search_depth"] == "basic" and body["max_results"] == 2
        return httpx.Response(
            200,
            json={
                "results": [
                    {"title": "LoRA", "url": "https://a.example/1", "content": "Low-rank   adaptation " * 100, "score": 0.9},
                    {"title": "", "url": "https://a.example/2", "content": "x"},
                    {"title": "no url", "content": "skipped"},
                ]
            },
        )

    t = TavilySearch("tvly-test", client=httpx.Client(transport=httpx.MockTransport(handler)))
    hits = t.search("lora", max_results=2)
    assert [h.url for h in hits] == ["https://a.example/1", "https://a.example/2"]
    assert hits[1].title == "https://a.example/2"
    assert len(hits[0].snippet) <= 500 and len(hits[0].excerpt) <= 1500 and "  " not in hits[0].snippet
    with pytest.raises(ValueError):
        TavilySearch("")


@pytest.mark.req("FR-10.1")
def test_scout_survives_bad_json_from_models():
    def call(messages, task):
        return Call("not json", "teacher", "m", None, False, 0.001, 0.001, 10.0)

    ev = []
    done = Scout(call, MockSearch()).run(EXAMPLE_QUESTIONS[0], ev.append)
    assert done["answer"] == "not json"  # writer output falls back to raw text
    assert not [e for e in ev if e.get("agent") == "reader"]  # nothing judged relevant, nothing read


@pytest.mark.req("FR-10.5")
def test_guard_caps_runs_per_day():
    clock = FakeClock()
    g = Guard(None, 100, max_runs_per_day=2, clock=clock)
    assert g.admit("a") is None and g.admit("b") is None
    assert "run limit" in g.admit("c")
    clock.advance(86400)
    assert g.admit("c") is None


@pytest.mark.req("FR-7.8")
def test_demo_controls_are_serialised_and_reset_has_a_cooldown(gw):
    c = TestClient(gw.app)
    assert gw._demo_lock.acquire(blocking=False)
    try:  # another visitor's action is running
        assert c.post("/ratchet/demo/day?requests=100").status_code == 409
    finally:
        gw._demo_lock.release()
    assert c.post("/ratchet/demo/reset").status_code == 200
    assert c.post("/ratchet/demo/reset").status_code == 429


@pytest.mark.req("FR-10.5", "INV-11")
def test_failed_run_returns_502_and_its_spend_still_counts(gw, monkeypatch):
    c = TestClient(gw.app)
    gw.rt.guard = Guard(5.0, 10)  # a budget, as in live mode
    real = gw.rt.ratchet.handle

    def flaky(messages, task=None):
        if task == "research-write":
            raise httpx.ConnectError("teacher down")
        return real(messages, task)

    monkeypatch.setattr(gw.rt.ratchet, "handle", flaky)
    r = c.post("/scout/run", json={"question": EXAMPLE_QUESTIONS[2]})
    assert r.status_code == 502
    assert gw.rt.guard.status()["spent_today_usd"] > 0  # planner/triage/reader calls were paid for


@pytest.mark.req("FR-7.7", "FR-2.9")
def test_stats_mark_pinned_tasks(gw):
    _events(gw)
    by_task = {t["task"]: t for t in TestClient(gw.app).get("/ratchet/stats").json()["tasks"]}
    assert by_task["research-plan"]["pinned"] and by_task["research-write"]["pinned"]
    assert not by_task["source-triage"]["pinned"]


@pytest.mark.req("FR-1.9")
def test_react_app_is_served_with_lite_fallback(gw, monkeypatch, tmp_path):
    c = TestClient(gw.app)
    if gw.SPA_INDEX.exists():  # built UI is committed: every page route returns the SPA shell
        for path in ("/", "/scout", "/dashboard"):
            r = c.get(path)
            assert r.status_code == 200 and '<div id="root">' in r.text
        asset = next((gw.DIST / "assets").glob("*.js")).name
        assert c.get(f"/assets/{asset}").status_code == 200
    for path in ("/lite", "/lite/scout", "/lite/dashboard"):  # the no-build pages stay available
        r = c.get(path)
        assert r.status_code == 200 and '<div id="root">' not in r.text
    monkeypatch.setattr(gw, "SPA_INDEX", tmp_path / "missing.html")  # source checkout without a build
    assert '<div id="root">' not in c.get("/").text
