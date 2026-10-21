"""Scout, the multi-agent research demo: pipeline, pinned tasks, research workload, gateway routes, guard, Tavily, and
the judge access, persistent guard and Tavily credit meter of ADR 0120."""

from __future__ import annotations

import dataclasses
import json
import logging
import random
from argparse import Namespace
from datetime import date, datetime, timezone
from typing import Any

import httpx
import pytest
from fakes import OPEN_TEACHER, AppTransport, FakeClock, FakeOpenAIServer
from fastapi.testclient import TestClient

from ratchet import tasks
from ratchet.config import Settings
from ratchet.ledger import Ledger
from ratchet.scout.agents import Call, Scout
from ratchet.scout.credits import CreditMeter, MeteredSearch, cycle_bounds
from ratchet.scout.guard import BUDGET_USED, JUDGE_USED, PUBLIC_RUNS_USED, Guard
from ratchet.scout.judge import AttemptLimiter, JudgeAccess
from ratchet.scout.prompts import EXAMPLE_QUESTIONS
from ratchet.scout.search import CREDITS_USED, PUBLIC_CREDITS_USED, Hit, MockSearch, SearchUnavailable, TavilySearch
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


# ---------- judge access, a persistent guard and Tavily credits (FR-10.20 to FR-10.22, ADR 0120) ----------
JUDGE_CODE = "0123456789abcdef0123"  # 20 hex characters
DEC1 = datetime(2026, 12, 1, tzinfo=timezone.utc).timestamp()  # judging starts
TEACHER_BASE = "https://teacher.test/v1"
SECRET_URL = "https://api.tavily.com/search?secret=tvly-SECRET"
# One teacher answer that every Scout agent can read: the planner's queries, triage's and the reader's labels, the writer's.
SCOUT_JSON = json.dumps(
    {
        "queries": ["lora basics", "lora paper", "lora vs full fine-tuning"],
        "relevant": True,
        "source_type": "docs",
        "reason": "on topic",
        "answers_question": True,
        "fact_type": "definition",
        "key_fact": "LoRA trains small low-rank matrices.",
        "answer": "LoRA trains small low-rank matrices [1].",
        "used": [1],
    }
)
SPEND_DETAIL_KEYS = {"scout_run": {"judge", "llm_usd", "status"}, "tavily": {"judge", "credits", "depth", "http", "counted"}}


def _scout_settings(**overrides: Any) -> Settings:
    """Settings with every Scout value these tests depend on set explicitly, so the shell cannot move a result."""
    base: dict[str, Any] = {
        "scout_judge_code": "",
        "scout_judge_runs_per_day": 0,
        "scout_judge_budget_usd": None,
        "scout_judge_session_s": 604800.0,
        "scout_daily_budget_usd": 3.0,
        "scout_max_runs_per_day": 30,
        "scout_runs_per_hour": 6,
        "tavily_monthly_credits": 0,
        "tavily_judge_credits": None,
        "tavily_cycle_day": 1,
        "tavily_pace_until": "",
        "gateway_lease": "warn",
        "trust_proxy": False,
    }
    return Settings(**{**base, **overrides})


def _live_scout_settings(tmp_path, **overrides: Any) -> Settings:
    """Live mode with the teacher on a fake Token Factory, no student endpoint (cold start: the teacher answers every
    call) and a Tavily key whose calls go to `transports={"tavily": ...}`."""
    live = {
        "mode": "live",
        "db_path": str(tmp_path / "scout.db"),
        "teacher_base": TEACHER_BASE,
        "teacher_api_key": "tf-teacher-key",
        "teacher_model": OPEN_TEACHER,
        "student_base": "",
        "audit_rate": 0.0,
        "tavily_api_key": "tvly-test",
        "api_keys": "",
        "admin_keys": "",
        "bom_signing_key": "",
        "tasks_file": "",
        "mock_warmup_days": 0,
    }
    return _scout_settings(**{**live, **overrides})


class TavilyStub:
    """Tavily behind `httpx.MockTransport`: two distinct results per query and `usage.credits` (None leaves it out);
    `status` other than 200 answers that status. Records every request body."""

    def __init__(self, status: int = 200, usage: int | None = 1) -> None:
        self.status = status
        self.usage = usage
        self.bodies: list[dict[str, Any]] = []

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["include_usage"] is True
        self.bodies.append(body)
        if self.status != 200:
            return httpx.Response(self.status, json={"detail": {"error": "refused"}})
        n = len(self.bodies)
        results = [
            {"title": f"Result {n}.{i}", "url": f"https://r{n}.example.org/{i}", "content": "LoRA adapts low-rank weights."}
            for i in range(2)
        ]
        out: dict[str, Any] = {"results": results}
        if self.usage is not None:
            out["usage"] = {"credits": self.usage}
        return httpx.Response(200, json=out)


def _live_gateway(gw, monkeypatch, settings: Settings, tavily: TavilyStub, clock: FakeClock):
    """A live `Runtime` on fake Token Factory and Tavily transports, installed as the gateway's `rt`. Without API keys
    it starts only with `allow_unauthenticated` (ADR 0061), which these tests set unless they say otherwise."""
    teacher = FakeOpenAIServer(SCOUT_JSON)
    if settings.allow_unauthenticated is None:
        settings = dataclasses.replace(
            settings, allow_unauthenticated=True, api_guard="guarded", admin_token="0123456789abcdef0123456789abcdef"
        )
    if settings.public_dashboard is None:  # /ratchet/stats read without a key
        settings = dataclasses.replace(settings, public_dashboard=True)
    rt = gw.Runtime(settings, transports={"teacher": teacher.transport, "tavily": tavily.transport}, wall_clock=clock)
    monkeypatch.setattr(gw, "rt", rt)
    return rt, teacher


def _tasks_called(rt: Any) -> list[str]:
    """The task of every model call a run made through `Ratchet.handle`, in order (the ledger's request rows)."""
    return [r["task"] for r in rt.ledger.db.execute("SELECT task FROM requests ORDER BY id")]


def _stream(client: TestClient, question: str, **params: str) -> list[dict[str, Any]]:
    with client.stream("GET", "/scout/stream", params={"q": question, **params}) as s:
        assert s.status_code == 200
        return [json.loads(line[5:]) for line in s.iter_lines() if line.startswith("data:")]


def _search_state(events: list[dict[str, Any]]) -> dict[str, Any]:
    return events[-1]["search"]


@pytest.mark.req("FR-10.21", "INV-11")
def test_guard_judge_reserve_is_held_back_from_the_public():
    clock = FakeClock()
    g = Guard(1.0, 100, max_runs_per_day=10, judge_runs_per_day=3, judge_budget_usd=0.3, clock=clock)
    for i in range(7):
        assert g.admit(f"p{i}") is None
    assert g.admit("p7") == PUBLIC_RUNS_USED  # the public's 10 - 3 runs are used
    for i in range(3):
        assert g.admit(f"j{i}", judge=True) is None
    assert g.admit("j3", judge=True) == JUDGE_USED
    st = g.status()
    assert (st["runs_today"], st["judge_runs_today"], st["public_runs_left_today"]) == (10, 3, 0)
    assert all(word not in PUBLIC_RUNS_USED + BUDGET_USED for word in ("judge", "code"))  # no hint that a code exists

    clock.advance(86400)  # a new UTC day: the budget
    assert g.admit("p", run_id="r1") is None
    g.add_spend(0.7, run_id="r1")
    assert g.admit("p") == BUDGET_USED  # the public's 1.0 - 0.3
    assert g.status()["public_budget_left_usd"] == 0
    assert g.admit("j", judge=True, run_id="j1") is None
    g.add_spend(0.25, judge=True, run_id="j1")
    assert g.admit("j", judge=True, run_id="j2") is None  # 0.95 spent: still under the total
    g.add_spend(0.05, judge=True, run_id="j2")
    assert g.admit("j", judge=True) == JUDGE_USED and g.admit("p") == BUDGET_USED
    assert g.status()["spent_today_usd"] == pytest.approx(1.0)
    shared = Guard(None, 1, clock=FakeClock())  # a venue NAT: one address for the public and a judge
    assert shared.admit("203.0.113.9") is None and "Rate limit" in shared.admit("203.0.113.9")
    assert shared.admit("203.0.113.9", judge=True) is None  # the judge's own hourly window


@pytest.mark.req("FR-10.21", "INV-11")
def test_judge_overflow_never_breaks_the_totals():
    rng = random.Random(7)
    clock = FakeClock()
    runs, j_runs, budget, j_usd = 20, 6, 1.0, 0.3
    g = Guard(budget, 1000, max_runs_per_day=runs, judge_runs_per_day=j_runs, judge_budget_usd=j_usd, clock=clock)
    running: list[tuple[str, bool]] = []
    admitted = {False: 0, True: 0}
    for i in range(500):
        if i % 50 == 49:
            clock.advance(86400)  # runs still open finish tomorrow and count against the day they started
        if running and rng.random() < 0.5:
            rid, judge = running.pop(rng.randrange(len(running)))
            g.add_spend(rng.uniform(0.0, 0.12), judge=judge, run_id=rid)
        else:
            judge = rng.random() < 0.4
            spent_before = g.status()["spent_today_usd"]
            if g.admit(f"ip{i % 7}", judge=judge, run_id=f"r{i}") is None:
                assert spent_before < budget  # no run starts once the day's total reached the budget
                running.append((f"r{i}", judge))
                admitted[judge] += 1
        p, u = g.runs["public"], g.runs["judge"]
        assert p + u <= runs
        assert p + max(0, u - j_runs) <= runs - j_runs
    assert admitted[False] > 20 and admitted[True] > 20  # both pools ran on many days


@pytest.mark.req("FR-10.21")
def test_public_margin_protects_the_judge_usd_share():
    g = Guard(1.0, 100, max_runs_per_day=100, judge_runs_per_day=10, judge_budget_usd=0.3, clock=FakeClock())
    spent = 0
    while (refused := g.admit("a", run_id=f"r{spent}")) is None:
        g.add_spend(0.08, run_id=f"r{spent}")
        spent += 1
    # 8 runs of 0.08: 0.64 < 0.7 = T - J, but one more run like the largest finished one would reach the judges' share.
    assert refused == BUDGET_USED and spent == 8 and g.status()["spent_today_usd"] == pytest.approx(0.64)
    assert g.admit("j", judge=True) is None
    # Without a judge budget there is no share to protect, so no margin: the budget is reached exactly as before.
    plain = Guard(1.0, 100, clock=FakeClock())
    for i in range(13):  # the 13th starts at 0.96 spent, where a margin of 0.08 would have refused it
        assert plain.admit("a", run_id=f"r{i}") is None
        plain.add_spend(0.08, run_id=f"r{i}")
    assert plain.admit("a") == BUDGET_USED  # 1.04 >= 1.0


@pytest.mark.req("FR-10.20", "INV-11")
def test_guard_state_survives_a_restart(tmp_path):
    db, clock = str(tmp_path / "scout.db"), FakeClock()

    def guard() -> Guard:
        return Guard(1.0, 10, max_runs_per_day=10, judge_runs_per_day=2, judge_budget_usd=0.2, clock=clock, ledger=Ledger(db))

    g = guard()
    assert g.admit("a", run_id="r1") is None and g.admit("b", judge=True, run_id="r2") is None
    g.add_spend(0.4, run_id="r1")
    g.add_spend(0.1, judge=True, run_id="r2")
    after = guard()
    assert after.status()["runs_today"] == 0  # nothing until it restores
    after.restore()
    after.restore()  # idempotent
    st = after.status()
    assert (st["runs_today"], st["spent_today_usd"], st["judge_runs_today"], st["persistent"]) == (2, 0.5, 1, True)
    assert after.max_run_usd == pytest.approx(0.4) and after.status()["public_runs_left_today"] == 7
    clock.advance(86400)
    for g in (after, guard()):
        g.restore()
        st = g.status()
        assert (st["runs_today"], st["spent_today_usd"], st["judge_runs_today"]) == (0, 0, 0)


@pytest.mark.req("FR-10.20")
def test_guard_counts_a_run_killed_midway(tmp_path):
    db, clock = str(tmp_path / "scout.db"), FakeClock()
    g = Guard(1.0, 10, clock=clock, ledger=Ledger(db))
    assert g.admit("a", run_id="r1") is None
    g.progress("r1", 0.1)
    g.progress("r1", 0.2)  # then the process dies: no add_spend
    g.progress("unknown-run", 5.0)  # a run this guard never admitted changes nothing
    after = Guard(1.0, 10, clock=clock, ledger=Ledger(db))
    after.restore()
    assert (after.status()["runs_today"], after.status()["spent_today_usd"]) == (1, 0.2)
    assert after.max_run_usd == 0  # an unfinished run sets no margin
    (row,) = Ledger(db).spend_rows(["scout_run"], 0)
    assert row["detail"] == {"judge": False, "llm_usd": 0.2, "status": "admitted"} and row["ref"] == "r1"


@pytest.mark.req("FR-10.20")
def test_guard_charges_a_run_to_the_day_it_started(tmp_path):
    clock = FakeClock(DEC1 - 30)  # 23:59:30 UTC
    db = str(tmp_path / "scout.db")
    g = Guard(1.0, 10, clock=clock, ledger=Ledger(db))
    assert g.admit("a", run_id="late") is None
    clock.advance(60)  # it finishes after midnight
    g.add_spend(0.3, run_id="late", outcome="done")
    assert (g.status()["runs_today"], g.status()["spent_today_usd"]) == (0, 0)  # today starts clean, as after a restart
    after = Guard(1.0, 10, clock=clock, ledger=Ledger(db))
    after.restore()
    assert after.status()["spent_today_usd"] == 0
    with pytest.raises(ValueError, match="outcome"):
        g.add_spend(0.1, outcome="maybe")


@pytest.mark.req("FR-10.20", "INV-12")
def test_scout_spend_rows_are_not_billable_and_hold_no_request_data(gw, monkeypatch, tmp_path):
    settings = _live_scout_settings(
        tmp_path, scout_judge_code=JUDGE_CODE, scout_judge_runs_per_day=2, tavily_monthly_credits=1000, trust_proxy=True
    )
    rt, _ = _live_gateway(gw, monkeypatch, settings, TavilyStub(), FakeClock(DEC1 + 3600))
    question, ip = "What is LoRA fine-tuning? zebra-marker-1234", "198.51.100.23"
    public, judge = TestClient(gw.app), TestClient(gw.app, base_url="https://testserver")
    headers = {"X-Forwarded-For": ip}
    assert judge.post("/scout/judge", json={"code": JUDGE_CODE}, headers=headers).status_code == 200
    assert public.post("/scout/run", json={"question": question}, headers=headers).status_code == 200
    assert judge.post("/scout/run", json={"question": question}, headers=headers).status_code == 200
    rows = [dict(r) for r in rt.ledger.db.execute("SELECT * FROM spend")]
    assert rt.ledger.db.execute("SELECT SUM(usd) FROM spend").fetchone()[0] == 0
    assert {r["kind"] for r in rows} == {"scout_run", "tavily"}
    for r in rows:
        assert set(json.loads(r["detail"])) == SPEND_DETAIL_KEYS[r["kind"]]
        assert all(secret not in json.dumps(r) for secret in (question, "zebra-marker", ip, JUDGE_CODE))
    runs = [json.loads(r["detail"]) for r in rows if r["kind"] == "scout_run"]
    assert [(d["judge"], d["status"]) for d in runs] == [(False, "done"), (True, "done")] and all(d["llm_usd"] > 0 for d in runs)
    searches = [json.loads(r["detail"]) for r in rows if r["kind"] == "tavily"]
    assert len(searches) == 6 and {(d["credits"], d["counted"], d["http"]) for d in searches} == {(1, "usage", 200)}
    assert sum(d["judge"] for d in searches) == 3
    assert all(secret not in json.dumps(rt.ledger.events()) for secret in (question, ip, JUDGE_CODE))
    real = rt.ratchet.handle

    def writer_down(messages: Any, task: str | None = None, **kw: Any) -> Any:
        if task == "research-write":
            raise httpx.ConnectError(f"cannot reach {TEACHER_BASE}")
        return real(messages, task, **kw)

    monkeypatch.setattr(rt.ratchet, "handle", writer_down)
    assert judge.post("/scout/run", json={"question": question}, headers=headers).status_code == 502
    runs = [r["detail"] for r in rt.ledger.spend_rows(["scout_run"], 0)]
    assert [(d["judge"], d["status"]) for d in runs][-1] == (True, "error") and runs[-1]["llm_usd"] > 0  # spent, then failed


@pytest.mark.req("FR-10.20")
def test_mock_scout_writes_no_spend_rows(gw):
    c = TestClient(gw.app)
    assert c.post("/scout/run", json={"question": EXAMPLE_QUESTIONS[0]}).status_code == 200
    assert gw.rt.ledger.db.execute("SELECT COUNT(*) FROM spend").fetchone()[0] == 0
    st = c.get("/scout/status").json()
    assert st["persistent"] is False and st["search"]["credits"] is None and gw.rt.credits is None


@pytest.mark.req("FR-10.21")
def test_judge_code_unlocks_the_reserve_over_post_only(gw_env):
    off = gw_env()
    assert TestClient(off.app).post("/scout/judge", json={"code": JUDGE_CODE}).status_code == 404
    gw = gw_env(RATCHET_SCOUT_JUDGE_CODE=JUDGE_CODE, RATCHET_SCOUT_JUDGE_RUNS_PER_DAY="2")
    gw.rt.guard = Guard(None, 100, max_runs_per_day=3, judge_runs_per_day=2)
    gw.rt.settings.mock_pace = 0
    public, judge = TestClient(gw.app), TestClient(gw.app)
    for bad in (b"not json", b'{"code": 7}', b'["code"]', b'{"code": ""}', b"\xff\xfe", b"[" * 100_000):
        assert judge.post("/scout/judge", content=bad).status_code == 400
    r = judge.post("/scout/judge", json={"code": "f" * 20})
    assert r.status_code == 401 and r.json()["detail"] == "That code is not valid." and "set-cookie" not in r.headers
    r = judge.post("/scout/judge", json={"code": JUDGE_CODE})
    assert r.status_code == 200, r.text
    cookie = r.headers["set-cookie"]
    attrs = {a.strip().split("=")[0].lower(): a.strip() for a in cookie.split(";")}
    assert cookie.startswith("ratchet_judge=v1.") and "httponly" in attrs and attrs["path"] == "Path=/scout"
    assert attrs["samesite"].lower() == "samesite=strict" and "secure" not in attrs  # mock mode: plain http works
    assert attrs["max-age"] == "Max-Age=604800"
    body = r.json()
    assert body["judge"] is True and body["runs_left_today"] == 3 and body["expires"].endswith("+00:00")

    q = {"question": EXAMPLE_QUESTIONS[0]}
    assert public.post("/scout/run", json=q).status_code == 200  # the public's one run
    r = public.post("/scout/run", json=q)
    assert r.status_code == 429 and r.json()["detail"] == PUBLIC_RUNS_USED
    assert public.post("/scout/run", json={**q, "code": JUDGE_CODE}).status_code == 429  # a code in the body is ignored
    events = _stream(public, EXAMPLE_QUESTIONS[1], code=JUDGE_CODE)  # and in the query
    assert [e["type"] for e in events] == ["error"] and events[0]["message"] == PUBLIC_RUNS_USED
    assert judge.post("/scout/run", json=q).status_code == 200
    events = _stream(judge, EXAMPLE_QUESTIONS[1])
    assert events[-1]["type"] == "done" and events[-1]["answer"]
    st = judge.get("/scout/status").json()["judge"]
    assert st == {"enabled": True, "session": True, "reserved_runs_per_day": 2, "runs_today": 2, "runs_left_today": 0}
    assert public.get("/scout/status").json()["judge"]["session"] is False
    assert [e["detail"] for e in gw.rt.ledger.events() if e["kind"] == "judge"] == ["judge session opened"]

    r = judge.delete("/scout/judge")
    assert r.status_code == 204 and "max-age=0" in r.headers["set-cookie"].lower()
    assert judge.get("/scout/status").json()["judge"]["session"] is False


@pytest.mark.req("FR-10.21", "INV-13")
def test_judge_code_never_reaches_logs_ledger_events_or_status(gw, monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    settings = _live_scout_settings(tmp_path, scout_judge_code=JUDGE_CODE, scout_judge_runs_per_day=2, tavily_monthly_credits=500)
    rt, _ = _live_gateway(gw, monkeypatch, settings, TavilyStub(), FakeClock(DEC1))
    public, judge = TestClient(gw.app), TestClient(gw.app, base_url="https://testserver")
    responses = [
        judge.post("/scout/judge", json={"code": JUDGE_CODE[:-1] + "x"}),
        judge.post("/scout/judge", json={"code": JUDGE_CODE}),
        judge.post("/scout/run", json={"question": "What is LoRA?"}),
        public.post("/scout/run", json={"question": "What is LoRA?", "code": JUDGE_CODE}),
        judge.get("/scout/status"),
        public.get("/scout/status"),
        public.get("/ratchet/stats"),
        judge.delete("/scout/judge"),
    ]
    assert [r.status_code for r in responses] == [401, 200, 200, 200, 200, 200, 200, 204]
    cookie = responses[1].cookies.get("ratchet_judge") or ""
    assert cookie.startswith("v1.")
    texts = [r.text for r in responses] + [json.dumps(dict(r.headers)) for r in responses]
    texts += [caplog.text, cookie, repr(rt.settings), repr(rt.judge), repr(settings.scout_policy())]
    for (table,) in rt.ledger.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        texts.append(json.dumps([list(row) for row in rt.ledger.db.execute(f"SELECT * FROM {table}")], default=str))
    for bad in (
        {"scout_judge_code": JUDGE_CODE},  # without runs
        {"scout_judge_code": JUDGE_CODE[:15], "scout_judge_runs_per_day": 1},
        {"scout_judge_code": JUDGE_CODE + " x", "scout_judge_runs_per_day": 1},
        {"scout_judge_code": JUDGE_CODE, "scout_judge_runs_per_day": 99},
    ):
        with pytest.raises(ValueError) as e:
            _scout_settings(**bad).scout_policy()
        texts.append(str(e.value))
    for text in texts:
        assert JUDGE_CODE not in text and JUDGE_CODE[:15] not in text


@pytest.mark.req("FR-10.21")
def test_judge_cookie_tampering_rotation_and_expiry():
    clock = FakeClock()
    access = JudgeAccess(JUDGE_CODE, 3600, clock)
    value, expires = access.issue()
    assert access.verify(value) and expires == int(clock.now + 3600)
    assert access.issue()[0] != value  # a fresh nonce each time
    version, exp, nonce, sig = value.split(".")
    flipped = ("A" if sig[0] != "A" else "B") + sig[1:]
    for forged in (
        f"{version}.{int(exp) + 86400}.{nonce}.{sig}",  # a later expiry
        f"{version}.{exp}.{nonce}.{flipped}",  # another signature
        f"{version}.{exp}.{'0' * 16}.{sig}",  # another nonce
        f"v2.{exp}.{nonce}.{sig}",
        value + "x",
        "",
        None,
        7,
        "v1.1e9.x.y",
    ):
        assert not access.verify(forged), forged
    assert not JudgeAccess("f" * 20, 3600, clock).verify(value)  # issued under another code: rotating it revokes
    assert JudgeAccess(JUDGE_CODE, 60, clock).verify(value)  # same code after a restart: still valid
    clock.advance(3601)
    assert not access.verify(value)  # expired
    assert access.check(JUDGE_CODE)
    for wrong in (JUDGE_CODE + "0", JUDGE_CODE.upper(), "", None, 12, "a" * 257, "\ud800" * 20):
        assert not access.check(wrong)
    assert JUDGE_CODE not in repr(access) and JUDGE_CODE not in repr(vars(access))
    with pytest.raises(ValueError):
        JudgeAccess("", 3600)


@pytest.mark.req("FR-10.21")
def test_judge_login_attempts_are_limited(gw_env):
    gw = gw_env(RATCHET_SCOUT_JUDGE_CODE=JUDGE_CODE, RATCHET_SCOUT_JUDGE_RUNS_PER_DAY="2", RATCHET_TRUST_PROXY="1")
    c = TestClient(gw.app)
    a, b = {"X-Forwarded-For": "198.51.100.1"}, {"X-Forwarded-For": "198.51.100.2"}
    for _ in range(10):
        assert c.post("/scout/judge", json={"code": "x" * 20}, headers=a).status_code == 401
    assert c.post("/scout/judge", json={"code": JUDGE_CODE}, headers=a).status_code == 429  # even the right code
    assert c.post("/scout/judge", json={"code": "x" * 20}, headers=b).status_code == 401
    assert c.post("/scout/judge", json={"code": JUDGE_CODE}, headers=b).status_code == 200
    clock = FakeClock()
    limiter = AttemptLimiter(2, clock, max_keys=4)
    assert limiter.allow("a") and limiter.allow("a") and not limiter.allow("a")
    clock.advance(3601)  # a rolling hour
    assert limiter.allow("a")
    for i in range(10):  # a flood of addresses keeps memory bounded
        limiter.allow(f"ip{i}")
    assert len(limiter._seen) <= 5


@pytest.mark.req("FR-10.21", "FR-10.22")
def test_judge_and_credit_settings_stop_startup(gw):
    on = {"scout_judge_code": JUDGE_CODE, "scout_judge_runs_per_day": 2}
    cases: list[tuple[dict[str, Any], str]] = [
        ({"scout_judge_code": JUDGE_CODE}, "RATCHET_SCOUT_JUDGE_CODE and RATCHET_SCOUT_JUDGE_RUNS_PER_DAY"),
        ({"scout_judge_runs_per_day": 2}, "RATCHET_SCOUT_JUDGE_CODE and RATCHET_SCOUT_JUDGE_RUNS_PER_DAY"),
        ({**on, "scout_judge_code": JUDGE_CODE[:15]}, "RATCHET_SCOUT_JUDGE_CODE must be"),
        ({**on, "scout_judge_code": "0123456789 abcdef"}, "RATCHET_SCOUT_JUDGE_CODE must be"),
        ({**on, "scout_judge_runs_per_day": 31}, "RATCHET_SCOUT_JUDGE_RUNS_PER_DAY must"),
        ({**on, "scout_judge_budget_usd": 3.5}, "RATCHET_SCOUT_JUDGE_BUDGET_USD"),
        ({"scout_judge_budget_usd": 0.5}, "RATCHET_SCOUT_JUDGE_BUDGET_USD.*RATCHET_SCOUT_JUDGE_CODE"),
        ({**on, "scout_judge_session_s": 30.0}, "RATCHET_SCOUT_JUDGE_SESSION_S"),
        ({"tavily_monthly_credits": -1}, "RATCHET_TAVILY_MONTHLY_CREDITS"),
        ({**on, "tavily_monthly_credits": 100, "tavily_judge_credits": 101.0}, "RATCHET_TAVILY_JUDGE_CREDITS"),
        ({**on, "tavily_monthly_credits": 100, "tavily_judge_credits": 2.5}, "RATCHET_TAVILY_JUDGE_CREDITS"),
        ({"tavily_monthly_credits": 100, "tavily_judge_credits": 10.0}, "RATCHET_TAVILY_JUDGE_CREDITS.*RATCHET_SCOUT_JUDGE_CODE"),
        ({"tavily_cycle_day": 29}, "RATCHET_TAVILY_CYCLE_DAY"),
        ({"tavily_pace_until": "2026-13-01"}, "RATCHET_TAVILY_PACE_UNTIL"),
        ({"tavily_pace_until": "Dec 15"}, "RATCHET_TAVILY_PACE_UNTIL"),
        ({"gateway_lease": "maybe"}, "RATCHET_GATEWAY_LEASE"),
    ]
    for overrides, match in cases:
        with pytest.raises(ValueError, match=match) as e:
            _scout_settings(**overrides).scout_policy()
        assert JUDGE_CODE[:15] not in str(e.value), overrides
    with pytest.raises(ValueError, match="RATCHET_SCOUT_JUDGE_CODE and"):  # the gateway stops at startup
        gw.Runtime(_scout_settings(mode="mock", mock_warmup_days=0, scout_judge_runs_per_day=2))
    with pytest.raises(ValueError, match="RATCHET_GATEWAY_LEASE.*NFR-60"):  # reserved until the leases land
        _scout_settings(gateway_lease="exclusive").check_reserved()
    _scout_settings(gateway_lease=" WARN ").check_reserved()
    # The defaults the deploy guide recommends: judges' shares derived from 7 of 22 runs, rounded down.
    recommended = {"scout_judge_runs_per_day": 7, "scout_max_runs_per_day": 22, "tavily_monthly_credits": 1000}
    pol = _scout_settings(**{**on, **recommended}, tavily_pace_until="2026-12-15").scout_policy()
    assert (pol.judge_budget_usd, pol.judge_credits, pol.pace_until) == (0.95, 318, date(2026, 12, 15))
    assert pol.judge_enabled and JUDGE_CODE not in repr(pol)
    pol = _scout_settings(scout_judge_code=JUDGE_CODE, scout_judge_runs_per_day=30).scout_policy()  # judges only: allowed
    assert pol.judge_budget_usd == 3.0
    assert not _scout_settings().scout_policy().judge_enabled


@pytest.mark.req("FR-10.22")
def test_tavily_credits_settle_by_outcome():
    led = Ledger(":memory:")
    meter = CreditMeter(0, 0, clock=FakeClock(DEC1), ledger=led)

    def search(handler: Any, depth: str = "basic") -> Any:
        tool = TavilySearch("tvly-test", client=httpx.Client(transport=httpx.MockTransport(handler)), depth=depth)
        return MeteredSearch(tool, meter, False, "run1").search("lora", 2)

    def ok(usage: int | None = None, body: bytes | None = None) -> Any:
        def handler(request: httpx.Request) -> httpx.Response:
            assert json.loads(request.content)["include_usage"] is True
            if body is not None:
                return httpx.Response(200, content=body)
            out: dict[str, Any] = {"results": [{"title": "t", "url": "https://a.example.org/1", "content": "c"}]}
            if usage is not None:
                out["usage"] = {"credits": usage}
            return httpx.Response(200, json=out)

        return handler

    def fails(exc: Exception) -> Any:
        def handler(request: httpx.Request) -> httpx.Response:
            raise exc

        return handler

    assert [h.url for h in search(ok(1))] == ["https://a.example.org/1"]
    assert search(ok(2), depth="advanced")
    assert search(ok())  # no usage reported: the table amount
    assert search(ok(99))  # an implausible report: the table amount
    for status in (401, 500):
        with pytest.raises(httpx.HTTPStatusError):
            search(lambda request, status=status: httpx.Response(status, json={"detail": "x"}))
    with pytest.raises(httpx.ConnectError):  # nothing was sent
        search(fails(httpx.ConnectError("refused")))
    with pytest.raises(httpx.ReadTimeout):  # sent, and maybe billed
        search(fails(httpx.ReadTimeout("slow")))
    with pytest.raises(httpx.DecodingError):  # a 2xx whose body is not JSON was billed
        search(ok(body=b"<html>captive portal</html>"))
    rows = [(r["detail"]["credits"], r["detail"]["counted"], r["detail"]["http"]) for r in led.spend_rows(["tavily"], 0)]
    assert rows == [
        (1, "usage", 200),
        (2, "usage", 200),
        (1, "table", 200),
        (1, "table", 200),
        (0, "none", 401),
        (1, "table", 500),
        (0, "none", None),
        (1, "table", None),
        (1, "table", None),
    ]
    assert [r["detail"]["depth"] for r in led.spend_rows(["tavily"], 0)][:2] == ["basic", "advanced"]
    assert tuple(led.db.execute("SELECT SUM(usd), COUNT(*) FROM spend").fetchone()) == (0, 9)
    assert meter.status()["used_this_cycle"] == 8 and meter.status()["left"] is None  # counted, not capped


@pytest.mark.req("FR-10.22")
def test_credit_meter_paces_the_public_and_holds_the_judge_share():
    clock = FakeClock(DEC1)
    m = CreditMeter(100, 20, clock=clock)

    def use(judge: bool, k: int = 1) -> str | None:
        refused = m.reserve(judge, k)
        if refused is None:
            m.settle(judge, k, k, counted="table", http=200)
        return refused

    assert m.status()["public_left_today"] == 2  # floor(80 / 31)
    assert use(False) is None
    for _ in range(20):
        assert use(True) is None  # the judges' 20
    assert use(True) is None  # then the day's allowance: 1 public + 1 judge overflow
    assert use(True) == CREDITS_USED and use(False) == PUBLIC_CREDITS_USED
    st = m.status()
    assert (st["used_this_cycle"], st["judge_left"], st["public_left_today"], st["left"]) == (22, 0, 0, 78)
    clock.advance(86400)  # Dec 2: 80 - 2 left for 30 days
    assert m.status()["public_left_today"] == 2
    clock.advance(17 * 86400)  # Dec 19, nothing used since: unused credits roll forward
    assert m.status()["public_left_today"] == 6  # floor(78 / 13)
    assert m.take(False, 6) and not m.take(False, 1)  # reservations in flight count too
    m.settle(False, 6, 0, counted="none", http=401)  # refused calls cost nothing: the reservation is released
    assert m.status()["public_left_today"] == 6

    nov = FakeClock(datetime(2026, 12, 19, 12, tzinfo=timezone.utc).timestamp())
    cycle = CreditMeter(100, 0, cycle_day=20, clock=nov)
    assert cycle.status()["cycle_start"] == "2026-11-20" and use_all(cycle) > 0
    nov.advance(12 * 3600)  # Dec 20, 00:00 UTC: a new cycle
    assert (cycle.status()["cycle_start"], cycle.status()["used_this_cycle"]) == ("2026-12-20", 0)
    assert cycle_bounds(date(2026, 1, 5), 20) == (date(2025, 12, 20), date(2026, 1, 20))
    assert cycle_bounds(date(2026, 12, 31), 1) == (date(2026, 12, 1), date(2027, 1, 1))
    assert CreditMeter(0, 0).take(False, 50)  # 0 credits a month: counted, never refused


def use_all(m: CreditMeter) -> int:
    """Spend today's public allowance one credit at a time; returns how many were spent."""
    n = 0
    while m.reserve(False, 1) is None:
        m.settle(False, 1, 1, counted="table", http=200)
        n += 1
    return n


@pytest.mark.req("FR-10.22")
def test_credit_meter_paces_until_the_judging_end():
    clock = FakeClock(DEC1)
    m = CreditMeter(100, 20, pace_until=date(2026, 12, 15), clock=clock)
    assert m.status()["public_left_today"] == 5  # floor(80 / 15)
    assert m.status()["pace_until"] == "2026-12-15"
    clock.advance(14 * 86400)  # Dec 15: the last paced day gets whatever is left
    assert m.status()["public_left_today"] == 80
    clock.advance(86400)  # Dec 16: past the date, pacing runs to the cycle's end
    assert m.status()["public_left_today"] == 5  # floor(80 / 16)
    later = CreditMeter(100, 20, pace_until=date(2027, 3, 1), clock=FakeClock(DEC1))  # not in this cycle: ignored
    assert later.status()["public_left_today"] == 2


@pytest.mark.req("FR-10.22")
def test_credit_meter_survives_a_restart(tmp_path):
    db = str(tmp_path / "credits.db")
    clock = FakeClock(DEC1 - 3600)  # Nov 30: the previous cycle
    m = CreditMeter(100, 20, clock=clock, ledger=Ledger(db))
    m.settle(False, 1, 5, counted="table", http=200)
    clock.advance(7200)  # Dec 1
    m.settle(False, 1, 1, counted="usage", http=200)
    m.settle(True, 1, 3, counted="usage", http=200)
    clock.advance(86400)  # Dec 2
    m.settle(False, 1, 1, counted="usage", http=200)
    after = CreditMeter(100, 20, clock=clock, ledger=Ledger(db))
    after.restore()
    st = after.status()
    # This cycle only: 1 + 3 + 1. Today: (100 - 20 - 1) // 30 = 2, of which 1 is used.
    assert (st["used_this_cycle"], st["judge_left"], st["public_left_today"]) == (5, 17, 1)
    m.settle(False, 1, 0, counted="none", http=432)  # Tavily says the credits are used up
    paused = CreditMeter(100, 20, clock=clock, ledger=Ledger(db))
    paused.restore()
    assert paused.reserve(False, 1) == CREDITS_USED and paused.status()["exhausted_until"] is not None
    clock.advance(3601)
    assert paused.reserve(False, 1) is None


@pytest.mark.req("FR-10.22", "INV-11")
def test_public_run_without_credits_still_calls_the_teacher(gw, monkeypatch, tmp_path):
    tavily = TavilyStub()
    settings = _live_scout_settings(
        tmp_path, scout_judge_code=JUDGE_CODE, scout_judge_runs_per_day=5, tavily_monthly_credits=100, tavily_judge_credits=20.0
    )
    rt, teacher = _live_gateway(gw, monkeypatch, settings, tavily, FakeClock(DEC1 + 3600))
    assert use_all(rt.credits) == 2  # today's public allowance, floor(80 / 31), is used up
    public, judge = TestClient(gw.app), TestClient(gw.app, base_url="https://testserver")
    r = public.post("/scout/run", json={"question": "What is LoRA?"})
    assert r.status_code == 200
    events = r.json()["events"]
    assert _search_state(events) == {"available": False, "partial": False, "reason": PUBLIC_CREDITS_USED}
    assert tavily.bodies == [] and _tasks_called(rt) == ["research-plan", "research-write"]
    assert len(teacher.calls) == 2
    status = public.get("/scout/status").json()
    assert status["search"]["available"] is True and status["search"]["errors_today"] == 0  # a refusal is not a failure
    assert status["search"]["credits"]["public_left_today"] == 0
    assert judge.post("/scout/judge", json={"code": JUDGE_CODE}).status_code == 200
    events = judge.post("/scout/run", json={"question": "What is LoRA?"}).json()["events"]
    assert _search_state(events) == {"available": True, "partial": False, "reason": None}
    assert len(tavily.bodies) == 3 and public.get("/scout/status").json()["search"]["credits"]["judge_left"] == 17
    runs = [r["detail"] for r in rt.ledger.spend_rows(["scout_run"], 0)]
    assert [(d["judge"], d["status"]) for d in runs] == [(False, "search_unavailable"), (True, "done")]


@pytest.mark.req("FR-10.22", "INV-11")
def test_search_failure_still_calls_the_teacher():
    called: list[str] = []

    def call(messages: Any, task: str) -> Call:
        called.append(task)
        return Call(SCOUT_JSON, "teacher", "m", None, False, 0.001, 0.001, 5.0)

    class Broken:
        name = "tavily"

        def search(self, query: str, max_results: int = 4) -> list:
            request = httpx.Request("POST", SECRET_URL)
            response = httpx.Response(401, request=request, text=f"invalid key for {SECRET_URL}")
            raise httpx.HTTPStatusError(
                f"Client error '401 Unauthorized' for url '{SECRET_URL}'", request=request, response=response
            )

    events: list[dict[str, Any]] = []
    done = Scout(call, Broken()).run("What is LoRA?", events.append)
    assert called == ["research-plan", "research-write"]
    steps = [e for e in events if e["type"] == "step"]
    keys = set("type agent task served_by model confidence escalated cost_usd baseline_usd latency_ms detail".split())
    assert all(set(s) == keys for s in steps)
    assert [s["agent"] for s in steps] == ["planner", "searcher", "writer"]  # one search, then no more queries
    assert steps[1]["detail"] == {"query": "lora basics", "results": [], "unavailable": "HTTPStatusError 401"}
    assert steps[2]["detail"]["search_unavailable"] is True and steps[2]["detail"]["citations"] == []
    assert done["search"] == {"available": False, "partial": False, "reason": "HTTPStatusError 401"}
    assert done["answer"] == "LoRA trains small low-rank matrices [1]."  # the model's own answer, never rewritten
    assert "secret" not in json.dumps(events) and "tavily.com" not in json.dumps(events)


@pytest.mark.req("FR-10.22")
def test_partial_search_keeps_found_results():
    queries: list[str] = []

    class Flaky:
        name = "tavily"

        def search(self, query: str, max_results: int = 4) -> list[Hit]:
            queries.append(query)
            if len(queries) > 1:
                raise httpx.ConnectError(f"cannot reach {SECRET_URL}")
            return [Hit("LoRA", "https://a.example.org/lora", "snippet", "excerpt")]

    def call(messages: Any, task: str) -> Call:
        return Call(SCOUT_JSON, "teacher", "m", None, False, 0.001, 0.001, 5.0)

    events: list[dict[str, Any]] = []
    done = Scout(call, Flaky()).run("What is LoRA?", events.append)
    assert queries == ["lora basics", "lora paper"]  # no third query after the failure
    agents = [e["agent"] for e in events if e["type"] == "step"]
    assert agents == ["planner", "searcher", "searcher", "triage", "reader", "writer"]
    assert done["search"] == {"available": False, "partial": True, "reason": "ConnectError"}
    assert [c["url"] for c in done["citations"]] == ["https://a.example.org/lora"]


@pytest.mark.req("FR-10.22", "INV-11")
def test_search_unavailable_through_the_gateway_counts_spend_and_events(gw):
    c = TestClient(gw.app)
    gw.rt.guard = Guard(5.0, 10)

    class Unauthorized:
        name = "tavily"

        def search(self, query: str, max_results: int = 4) -> list:
            request = httpx.Request("POST", SECRET_URL)
            raise httpx.HTTPStatusError(SECRET_URL, request=request, response=httpx.Response(401, request=request))

    gw.rt.search = Unauthorized()
    r = c.post("/scout/run", json={"question": EXAMPLE_QUESTIONS[0]})
    assert r.status_code == 200 and _search_state(r.json()["events"])["reason"] == "HTTPStatusError 401"
    assert gw.rt.guard.status()["spent_today_usd"] > 0  # the planner and the writer were paid for
    errors = [e["detail"] for e in gw.rt.ledger.events() if e["kind"] == "search_error"]
    assert errors == ["scout search: HTTPStatusError 401"]
    search = c.get("/scout/status").json()["search"]
    assert (search["available"], search["last_error"], search["errors_today"]) == (False, "HTTPStatusError 401", 1)
    gw.rt.guard = Guard(5.0, 100)
    for _ in range(9):
        assert c.post("/scout/run", json={"question": EXAMPLE_QUESTIONS[0]}).status_code == 200
    assert len([e for e in gw.rt.ledger.events() if e["kind"] == "search_error"]) == 2  # the 1st and the 10th
    assert "secret" not in json.dumps(gw.rt.ledger.events())
    gw.rt.search = MockSearch()  # the next search works again
    assert c.post("/scout/run", json={"question": EXAMPLE_QUESTIONS[0]}).status_code == 200
    search = c.get("/scout/status").json()["search"]
    assert (search["available"], search["last_error"], search["errors_today"]) == (True, None, 10)


@pytest.mark.req("FR-10.22")
def test_tavily_432_marks_credits_exhausted():
    led, clock = Ledger(":memory:"), FakeClock(DEC1)
    tavily = TavilyStub(status=432)
    tool = TavilySearch("tvly-test", client=httpx.Client(transport=tavily.transport))
    meter = CreditMeter(0, 0, clock=clock, ledger=led)
    search = MeteredSearch(tool, meter, False, "run1")
    with pytest.raises(httpx.HTTPStatusError):
        search.search("lora")
    for _ in range(3):  # within the hour: refused without calling Tavily
        with pytest.raises(SearchUnavailable) as e:
            MeteredSearch(tool, meter, True, "run2").search("lora")
        assert e.value.reason == CREDITS_USED
    assert len(tavily.bodies) == 1
    (event,) = [e for e in led.events() if e["kind"] == "tavily_exhausted"]
    assert "432" in event["detail"] and "tavily.com" not in event["detail"]
    assert meter.status()["exhausted_until"] == "2026-12-01T01:00:00+00:00"
    (row,) = led.spend_rows(["tavily"], 0)
    assert (row["detail"]["credits"], row["detail"]["http"]) == (0, 432)
    clock.advance(3601)
    tavily.status = 200
    assert search.search("lora") and len(tavily.bodies) == 2 and meter.status()["exhausted_until"] is None
    with pytest.raises(ValueError):
        SearchUnavailable("anything a visitor wrote")


@pytest.mark.req("FR-10.22")
def test_live_scout_without_tavily_key_is_still_503(gw, monkeypatch, tmp_path):
    rt, teacher = _live_gateway(gw, monkeypatch, _live_scout_settings(tmp_path, tavily_api_key=""), TavilyStub(), FakeClock())
    r = TestClient(gw.app).post("/scout/run", json={"question": "What is LoRA?"})
    assert r.status_code == 503 and "TAVILY_API_KEY" in r.json()["detail"]
    assert rt.credits is None and teacher.calls == [] and rt.guard.status()["runs_today"] == 0
    assert TestClient(gw.app).get("/scout/status").json()["search"]["available"] is False


@pytest.mark.req("FR-10.20")
def test_scout_status_only_adds_keys(gw, monkeypatch, tmp_path):
    old = {
        "mode": str,
        "budget_usd": (float, type(None)),
        "spent_today_usd": (float, int),
        "runs_today": int,
        "runs_per_hour_per_ip": int,
        "max_runs_per_day": (int, type(None)),
        "tavily": bool,
    }  # docs/SCOUT.md's list before v0.5
    new = {"public_runs_left_today", "public_budget_left_usd", "judge_runs_today", "persistent", "judge", "search"}
    new |= {"live_check"}  # the demo watchdog's flag (FR-11.33)
    new |= {"fallback_student", "student_serving"}  # which model answers student calls (FR-7.40, lane W)
    mock = TestClient(gw.app).get("/scout/status").json()
    _live_gateway(gw, monkeypatch, _live_scout_settings(tmp_path, tavily_monthly_credits=1000), TavilyStub(), FakeClock(DEC1))
    live = TestClient(gw.app).get("/scout/status").json()
    for st in (mock, live):
        assert set(st) == set(old) | new
        for key, kind in old.items():
            assert isinstance(st[key], kind), (key, st[key])
        assert set(st["judge"]) == {"enabled", "session", "reserved_runs_per_day", "runs_today", "runs_left_today"}
        assert set(st["search"]) == {"provider", "available", "last_error", "errors_today", "credits"}
    assert (mock["budget_usd"], mock["max_runs_per_day"], mock["public_runs_left_today"], mock["persistent"]) == (
        None,
        None,
        None,
        False,
    )
    assert (live["budget_usd"], live["max_runs_per_day"], live["public_runs_left_today"], live["persistent"]) == (
        3.0,
        30,
        30,
        True,
    )
    assert set(live["search"]["credits"]) == {
        "monthly", "used_this_cycle", "left", "public_left_today", "judge_reserve", "judge_left", "cycle_start",
        "pace_until", "exhausted_until",
    }  # fmt: skip
    assert live["search"]["provider"] == "tavily" and live["search"]["credits"]["public_left_today"] == 32  # 1000 // 31
    assert gw.rt.scout_health() == {
        "search": {k: v for k, v in live["search"].items() if k != "credits"},
        "credits": live["search"]["credits"],
        "lease": None,
    }


@pytest.mark.req("FR-10.22")
def test_scout_cli_labels_search_unavailable(gw, monkeypatch, capsys):
    from ratchet.scout import __main__ as scout_cli

    class Down:
        name = "mock-search"

        def search(self, query: str, max_results: int = 4) -> list:
            raise httpx.ConnectError("unreachable")

    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.setattr(scout_cli, "MockSearch", Down)
    client = httpx.Client(transport=AppTransport(gw.app))
    assert scout_cli.main([EXAMPLE_QUESTIONS[0], "--base-url", "http://testserver/v1"], client=client) == 0
    out, err = capsys.readouterr()
    assert "-> search unavailable (ConnectError)" in out
    assert "warning: search unavailable (ConnectError); answered without web sources" in err
