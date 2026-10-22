"""Live and simulation side by side (FR-11.30, FR-11.31, ADR 0133): the content-free record of the last upstream call per
role, the limits and self-resets of the shared simulation, the link and demo settings and the `/ratchet/stats` blocks. All
offline: live mode runs over `httpx.MockTransport`, and a fake clock times the janitor. The deploy files are in test_live.py,
and INV-8 (a live gateway answers every demo call 403 before any limit) in test_contract.py."""

from __future__ import annotations

import itertools
import json
import threading
from pathlib import Path
from typing import Any

import httpx
import pytest
from fakes import FALLBACK_TEACHER, OPEN_TEACHER, FakeClock, FakeOpenAIServer, _version
from fastapi.testclient import TestClient

from ratchet import config, upstream
from ratchet.config import Settings
from ratchet.demo import DemoJanitor, DemoPolicy
from ratchet.providers import OpenAICompatProvider
from ratchet.resilience import NO_RETRY
from ratchet.scout.search import TavilySearch
from ratchet.upstream import UpstreamRecorder, classify, host_of, model_id

CHAT = "/v1/chat/completions"
SENTINEL = "ZZSENTINEL-7f3a"
TF_BASE = "https://api.tokenfactory.nebius.com/v1"
ADMIN = "root:admin-key-0123456789abcdef0123456789"
CALL_KEYS = {
    "role",
    "host",
    "requested_model",
    "model",
    "ok",
    "status",
    "error",
    "reason",
    "latency_ms",
    "at",
    "age_s",
    "last_ok_age_s",
}


def _live(tmp_path: Path, **overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "mode": "live",
        "db_path": str(tmp_path / "live.db"),
        "teacher_base": TF_BASE,
        "teacher_api_key": "tf-secret-key-value",
        "teacher_model": OPEN_TEACHER,
        "student_base": "",
        "samples": 3,
        "audit_rate": 0.0,
        "confidence_threshold": 0.8,
        "redact_pii": True,
        "tavily_api_key": "",
        "api_keys": "",
        "admin_keys": ADMIN,
        "mock_warmup_days": 0,
        "allow_unauthenticated": True,
        "api_guard": "guarded",
        "admin_token": "0123456789abcdef0123456789abcdef",
        "public_dashboard": True,
        "stats_ttl_s": 0.0,
        "sidecar_workers": 0,
    }
    return Settings(**{**base, **overrides})


def _ask(text: str = "Where is my refund?") -> dict[str, Any]:
    return {"messages": [{"role": "user", "content": text}]}


def _client(gw, monkeypatch, rt) -> TestClient:
    monkeypatch.setattr(gw, "rt", rt)
    return TestClient(gw.app)


def _stats(c: TestClient, admin: bool = True) -> dict[str, Any]:
    return c.get(
        "/ratchet/stats", headers={"Authorization": "Bearer admin-key-0123456789abcdef0123456789"} if admin else {}
    ).json()


# ---------- the recorder ----------
@pytest.mark.req("FR-11.31", "INV-12", "INV-13", "INV-15")
def test_upstream_recorder_is_content_free(gw_env, tmp_path, monkeypatch):
    """A prompt, an upstream error message and code, a hostile `model` and the teacher's URL and key reach nothing in
    `/ratchet/stats`, read as an admin or as the anonymous, scrubbed reader of a public dashboard."""
    gw = gw_env()
    server = FakeOpenAIServer(lambda body: f"echo {body['messages'][-1]['content']}")
    n = itertools.count(1)

    def handler(request: httpx.Request) -> httpx.Response:
        i = next(n)
        if i == 2:
            return httpx.Response(400, json={"error": {"message": f"{SENTINEL} refused", "code": "ZZSENTINEL_code"}})
        resp = server(request)
        if i == 3:
            data = resp.json()
            data["model"] = f"https://evil.example/{SENTINEL}"
            return httpx.Response(200, json=data)
        return resp

    rt = gw.Runtime(_live(tmp_path), transports={"teacher": httpx.MockTransport(handler)})
    c = _client(gw, monkeypatch, rt)
    seen: list[str] = []

    assert c.post(CHAT, json=_ask(f"please {SENTINEL}")).status_code == 200
    first = _stats(c)["upstream"]["calls"][0]
    assert (first["role"], first["host"], first["ok"], first["status"], first["model"]) == (
        "teacher",
        "token_factory",
        True,
        200,
        OPEN_TEACHER,
    )
    seen.append(json.dumps(_stats(c)))

    assert c.post(CHAT, json=_ask("again")).status_code == 400  # a client-caused failure, mapped
    (failed,) = _stats(c)["upstream"]["calls"]
    assert (failed["ok"], failed["error"], failed["reason"], failed["status"]) == (False, "UpstreamError", "http", 400)
    assert failed["model"] is None and failed["last_ok_age_s"] is not None  # the first success is kept
    seen.append(json.dumps(_stats(c)))

    assert c.post(CHAT, json=_ask("third")).status_code == 200
    (third,) = _stats(c)["upstream"]["calls"]
    assert third["model"] is None and third["requested_model"] == OPEN_TEACHER  # a URL-shaped model id is dropped
    seen.append(json.dumps(_stats(c)))
    seen.append(json.dumps(_stats(c, admin=False)))
    blocks = [json.dumps(_stats(c, admin=x)["upstream"]) for x in (True, False)]
    assert _stats(c, admin=False)["security"]["scrubbed"] is True
    assert _stats(c, admin=False)["upstream"]["calls"][0]["model"] is None

    for text in seen:  # nothing the upstream or the prompt said, and no key, anywhere in the payload
        for secret in ("ZZSENTINEL_code", f"{SENTINEL} refused", "tf-secret-key-value", f"please {SENTINEL}"):
            assert secret not in text, secret
    for text in blocks:  # the record itself holds no URL, not even the teacher's host, and no upstream-controlled text
        for secret in (SENTINEL, "evil.example", "://", "tokenfactory"):
            assert secret not in text, secret


@pytest.mark.req("FR-11.31")
def test_upstream_calls_shape_and_roles(gw_env, tmp_path, monkeypatch):
    """Each call item has exactly the documented keys; the teacher, the endpoint student and the hosted Nano are told
    apart by role and host; a fallback teacher on the teacher's client is `fallback_teacher`."""
    gw = gw_env()
    teacher = FakeOpenAIServer("teacher says hi")
    student = FakeOpenAIServer("teacher says hi")

    rt = gw.Runtime(
        _live(tmp_path, student_base="https://student.test/v1", student_api_key="s" * 12),
        transports={"teacher": teacher.transport, "student": student.transport},
    )
    c = _client(gw, monkeypatch, rt)
    assert _stats(c)["upstream"] == {
        "recording": True,
        "since": _stats(c)["upstream"]["since"],
        "token_factory": True,
        "calls": [],
    }
    assert c.post(CHAT, json=_ask("cold")).status_code == 200  # no version yet: the teacher
    vid = _version(rt.ledger, "live", {"threshold": 0.5})
    assert c.post(CHAT, json=_ask("warm")).status_code == 200  # a trained version: the endpoint student
    by_role = {x["role"]: x for x in _stats(c)["upstream"]["calls"]}
    assert set(by_role) == {"teacher", "student"} and all(set(x) == CALL_KEYS for x in by_role.values())
    assert (by_role["teacher"]["host"], by_role["student"]["host"]) == ("token_factory", "student_endpoint")
    assert by_role["student"]["requested_model"] == rt.ledger.version(vid).adapter_uri
    assert isinstance(by_role["teacher"]["latency_ms"], float) and isinstance(by_role["teacher"]["age_s"], float)

    # The hosted Nano (no endpoint): the student role on Token Factory, under the fallback model's id.
    hosted = gw.Runtime(_live(tmp_path, db_path=str(tmp_path / "h.db")), transports={"teacher": teacher.transport})
    c = _client(gw, monkeypatch, hosted)
    _version(hosted.ledger, "live", {"threshold": 0.5})
    assert c.post(CHAT, json=_ask("hosted")).status_code == 200
    got = {x["role"]: x for x in _stats(c)["upstream"]["calls"]}
    assert (
        got["student"]["host"] == "token_factory" and got["student"]["requested_model"] == hosted.settings.fallback_student_model
    )

    # A fallback teacher on the teacher's endpoint answers for a failing teacher and is recorded under its own role.
    def handler(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content)["model"] == OPEN_TEACHER:
            return httpx.Response(500, text="down")
        return teacher(request)

    fb = gw.Runtime(
        _live(tmp_path, db_path=str(tmp_path / "f.db"), teacher_fallback_model=FALLBACK_TEACHER),
        transports={"teacher": httpx.MockTransport(handler)},
        sleep=lambda s: None,
    )
    c = _client(gw, monkeypatch, fb)
    assert c.post(CHAT, json=_ask("fallback")).status_code == 200
    roles = {x["role"]: x for x in _stats(c)["upstream"]["calls"]}
    assert roles["teacher"]["ok"] is False and roles["fallback_teacher"]["ok"] is True
    assert roles["fallback_teacher"]["requested_model"] == FALLBACK_TEACHER

    other = gw.Runtime(
        _live(tmp_path, db_path=str(tmp_path / "o.db"), teacher_base="https://other.example/v1"),
        transports={"teacher": teacher.transport},
    )
    assert (
        _client(gw, monkeypatch, other)
        .get("/ratchet/stats", headers={"Authorization": "Bearer admin-key-0123456789abcdef0123456789"})
        .json()["upstream"]["token_factory"]
        is False
    )


def test_token_factory_host_is_the_default_teacher_base():
    assert host_of(Settings().teacher_base) == "token_factory" == host_of(f"https://{upstream.TOKEN_FACTORY_HOST}/v1")
    assert host_of("https://other.example/v1") == "other" and host_of("not a url") == "other"
    assert model_id("nvidia/Nemotron-3-Ultra-550b-a55b") and model_id("x" * 200) is None and model_id("a://b") is None


@pytest.mark.req("FR-11.31", "INV-15")
def test_upstream_failures_record_class_status_and_last_success():
    rec = UpstreamRecorder(clock=FakeClock(1000.0))
    answers = iter(
        [
            httpx.Response(
                200, json={"model": "m1", "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}], "usage": {}}
            ),
            httpx.Response(429, json={"error": {"message": "slow down", "code": "rate_limit"}}),
            httpx.ReadTimeout("slow"),
            httpx.ConnectError("no route"),
            httpx.Response(200, text="not json"),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        a = next(answers)
        if isinstance(a, Exception):
            raise a
        return a

    p = OpenAICompatProvider(
        TF_BASE, "k", transport=httpx.MockTransport(handler), retry=NO_RETRY, observe=rec.bind("teacher", "token_factory")
    )
    msgs = [{"role": "user", "content": "q"}]
    assert p.complete(msgs, "m1")
    for expect in (("http", 429), ("timeout", None), ("unreachable", None), ("bad_response", None)):
        with pytest.raises(httpx.HTTPError):
            p.complete(msgs, "m1")
        c = rec.last("teacher")
        assert (c.ok, c.reason, c.status) == (False, *expect)
    assert rec.last_ok_age("teacher", now=rec.last_ok_at("teacher") + 5) == 5

    # An observer that raises changes nothing about the call; a recorder fed junk drops the record and counts it.
    def boom(*a: Any, **k: Any) -> None:
        raise RuntimeError("observer bug")

    ok = httpx.Response(
        200, json={"model": "m1", "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}], "usage": {}}
    )
    q = OpenAICompatProvider(TF_BASE, "k", transport=httpx.MockTransport(lambda r: ok), retry=NO_RETRY, observe=boom)
    assert q.complete(msgs, "m1")
    rec.record("teacher", "token_factory", "m", latency_ms="not a number")  # type: ignore[arg-type]
    rec.record("nobody", "token_factory", "m", latency_ms=1.0)
    assert rec.dropped == 2
    rec.record("teacher", "token_factory", "x" * 10_000, served_model=3, latency_ms=2.0)  # type: ignore[arg-type]
    assert rec.last("teacher").requested_model is None and rec.last("teacher").model is None
    with pytest.raises(ValueError):
        rec.bind("judge", "token_factory")
    with pytest.raises(ValueError):
        rec.bind("teacher", "somewhere")


def test_classify_never_returns_a_message():
    from ratchet.resilience import UpstreamError

    e = UpstreamError(502, "boom ZZSENTINEL", code="upstream_unreachable")
    assert classify(e) == ("UpstreamError", "unreachable", None)
    assert classify(UpstreamError(503, "x ZZSENTINEL", code="ZZSENTINEL_code")) == ("UpstreamError", "http", 503)
    assert classify(ValueError("secret")) == ("ValueError", "other", None)


def test_age_grows_with_the_clock():
    now = [100.0]
    rec = UpstreamRecorder(clock=lambda: now[0])
    rec.record("teacher", "token_factory", "m", served_model="m", status=200, latency_ms=12.34)
    now[0] = 190.0
    (row,) = rec.snapshot()
    assert (row["age_s"], row["last_ok_age_s"], row["latency_ms"]) == (90.0, 90.0, 12.3)
    assert rec.snapshot() == rec.snapshot(now=190.0)


@pytest.mark.req("FR-11.31")
def test_tavily_calls_are_recorded_without_content():
    rec = UpstreamRecorder()
    codes = iter([200, 432])

    def handler(request: httpx.Request) -> httpx.Response:
        code = next(codes)
        if code == 200:
            return httpx.Response(200, json={"results": [{"url": "https://secret.example/page", "title": "t", "content": "c"}]})
        return httpx.Response(432, json={"detail": "ZZSENTINEL plan limit"})

    s = TavilySearch(
        "tvly-secret-key", client=httpx.Client(transport=httpx.MockTransport(handler)), observe=rec.bind("tavily", "tavily")
    )
    assert s.search("ZZSENTINEL query")
    ok = rec.last("tavily")
    assert (ok.ok, ok.status, ok.requested_model, ok.host) == (True, 200, None, "tavily")
    with pytest.raises(httpx.HTTPStatusError):
        s.search("ZZSENTINEL query")
    bad = rec.last("tavily")
    assert (bad.ok, bad.error, bad.status, bad.reason) == (False, "HTTPStatusError", 432, "http")
    text = json.dumps(rec.snapshot())
    for secret in ("ZZSENTINEL", "secret.example", "tvly-secret-key"):
        assert secret not in text


@pytest.mark.req("FR-11.31")
def test_providers_take_observe_as_keyword_only():
    """Without `observe` nothing changes: the same request bodies and the same completions; it cannot be positional."""
    calls: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return httpx.Response(
            200, json={"model": "m", "choices": [{"message": {"content": "x"}, "finish_reason": "stop"}], "usage": {}}
        )

    msgs = [{"role": "user", "content": "q"}]
    plain = OpenAICompatProvider(TF_BASE, "k", transport=httpx.MockTransport(handler))
    seen: list[tuple[Any, ...]] = []
    watched = OpenAICompatProvider(
        TF_BASE, "k", transport=httpx.MockTransport(handler), observe=lambda *a, **k: seen.append((a, k))
    )
    a, b = plain.complete(msgs, "m"), watched.complete(msgs, "m")
    assert calls[0] == calls[1] and [(x.text, x.model) for x in a] == [(x.text, x.model) for x in b]
    assert len(seen) == 1 and seen[0][0] == ("m",)
    assert plain.observe is None
    with pytest.raises(TypeError):
        OpenAICompatProvider(TF_BASE, "k", 120.0, None, None, lambda s: None, lambda *a: None)  # type: ignore[misc]
    with pytest.raises(TypeError):
        TavilySearch("k", None, "https://x", 30.0, "basic", lambda *a: None)  # type: ignore[misc]


# ---------- the shared simulation's limits ----------
@pytest.mark.req("FR-11.30")
def test_demo_actions_are_limited_per_ip(gw_env):
    gw = gw_env(RATCHET_DEMO_RPM="6", RATCHET_TRUST_PROXY="1")
    c = TestClient(gw.app)
    url = "/ratchet/demo/break-next"
    assert c.post(url, headers={"X-Forwarded-For": "198.51.100.7"}).status_code == 200
    events = len(gw.rt.ledger.events())
    r = c.post(url, headers={"X-Forwarded-For": "198.51.100.7"})
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
    assert "6 actions a minute" in r.json()["detail"] and len(gw.rt.ledger.events()) == events  # no `fault` event written
    assert c.post(url, headers={"X-Forwarded-For": "203.0.113.9"}).status_code == 200  # another visitor
    for _ in range(3):  # reads are never limited
        assert c.get("/ratchet/stats", headers={"X-Forwarded-For": "198.51.100.7"}).status_code == 200


@pytest.mark.req("FR-11.30")
def test_demo_limit_off_by_default(gw):
    c = TestClient(gw.app)
    assert [c.post("/ratchet/demo/break-next").status_code for _ in range(8)] == [200] * 8


@pytest.mark.req("FR-11.30", "US-11")
def test_demo_caps_reset_on_click(gw_env):
    gw = gw_env(RATCHET_DEMO_MAX_DAYS="3", RATCHET_DEMO_RPM="600")
    c = TestClient(gw.app)
    days = [c.post("/ratchet/demo/day?requests=100").json() for _ in range(4)]
    assert [d["day"] for d in days] == [1, 2, 3, 1]
    assert [d["auto_reset"] for d in days] == [None, None, None, "max_days"]
    kinds = [e["kind"] for e in gw.rt.ledger.events()]
    assert kinds.count("demo_reset") == 1
    stats = c.get("/ratchet/stats").json()
    assert stats["demo"]["auto_resets"] == 1 and stats["demo"]["last_reset_at"] is not None and stats["demo"]["max_days"] == 3
    assert "3 simulated days" in next(e["detail"] for e in stats["events"] if e["kind"] == "demo_reset")

    gw = gw_env(RATCHET_DEMO_MAX_REQUESTS="250")
    c = TestClient(gw.app)
    days = [c.post("/ratchet/demo/day?requests=100").json() for _ in range(4)]
    assert [d["auto_reset"] for d in days] == [None, None, None, "max_requests"] and days[3]["day"] == 1


@pytest.mark.req("FR-11.30")
def test_janitor_resets_when_idle_and_dirty_only():
    clock = FakeClock(0.0)
    count = [0]
    resets: list[str] = []
    ok = [True]

    def reset(reason: str) -> bool:
        if not ok[0]:
            return False
        resets.append(reason)
        count[0] = 0
        j.mark_reset(0, auto=True)
        return True

    j = DemoJanitor(DemoPolicy(idle_reset_s=300.0, max_requests=1000), reset, lambda: count[0], clock=clock, wall=lambda: 5.0)
    assert j.check() is None  # clean
    clock.advance(1000)
    assert j.check() is None  # clean stays clean however long it idles
    j.touch()
    clock.advance(299)
    assert j.check() is None
    clock.advance(2)
    ok[0] = False  # a demo action holds the lock
    assert j.check() is None and resets == []
    ok[0] = True
    assert j.check() == "idle" and resets == ["idle"]
    clock.advance(1000)
    assert j.check() is None  # not dirty again until something happens
    count[0] = 50  # /v1 traffic is activity and makes it dirty
    clock.advance(200)
    assert j.check() is None
    count[0] = 60
    clock.advance(200)
    assert j.check() is None  # the count moved: still active
    clock.advance(300)
    assert j.check() == "idle"
    count[0] = 1000
    assert j.check() == "max_requests"  # whatever the activity
    assert j.snapshot() == {"auto_resets": 3, "last_reset_at": 5.0}


@pytest.mark.req("FR-11.30")
def test_janitor_thread_starts_once_and_only_when_asked():
    made: list[dict[str, Any]] = []

    class T:
        def __init__(self, **kw: Any) -> None:
            made.append(kw)

        def start(self) -> None:
            made.append({"started": True})

    off = DemoJanitor(DemoPolicy(rpm=30, max_days=5), lambda r: True, lambda: 0, thread_factory=T)
    off.ensure_started()
    assert made == []
    on = DemoJanitor(DemoPolicy(idle_reset_s=600.0), lambda r: True, lambda: 0, thread_factory=T)
    on.ensure_started()
    on.ensure_started()
    assert [m.get("name") for m in made if "name" in m] == ["demo-janitor"] and {"started": True} in made


@pytest.mark.req("FR-11.30")
def test_janitor_stop_ends_its_thread_and_it_is_never_started_again():
    """The thread wakes at once on `stop()` (it waits on an event, it does not sleep) and a stopped janitor stays stopped,
    so the app's shutdown leaves no thread that could still reset the shared simulation."""
    janitor = DemoJanitor(DemoPolicy(idle_reset_s=300.0), lambda reason: True, lambda: 0)  # the real thread factory
    janitor.ensure_started()
    (thread,) = [t for t in threading.enumerate() if t.name == "demo-janitor" and t.is_alive()]
    janitor.stop()
    thread.join(5)
    assert not thread.is_alive()
    janitor.stop()  # twice is fine
    janitor.ensure_started()
    assert not [t for t in threading.enumerate() if t.name == "demo-janitor" and t.is_alive()]
    DemoJanitor(DemoPolicy(idle_reset_s=300.0), lambda reason: True, lambda: 0).stop()  # without a thread is fine too


@pytest.mark.req("FR-11.30")
def test_reloading_the_gateway_ends_the_janitor_it_replaces(gw_env):
    """The janitor's callbacks read the gateway module's globals, which a reload rebinds. A thread that survived it
    (`test_demo_caps_reset_on_click` starts one with RATCHET_DEMO_MAX_REQUESTS=250) reset the next test's gateway a minute
    later, wherever the suite had got: the scout simulation ended without a live version on a slow runner. `gw_env` ends
    the janitor of the module it reloads, and the lifespan ends the running app's."""
    gw = gw_env(RATCHET_DEMO_MAX_REQUESTS="250")
    c = TestClient(gw.app)
    assert c.post("/ratchet/demo/break-next").status_code == 200  # a demo action starts the real thread
    (thread,) = [t for t in threading.enumerate() if t.name == "demo-janitor" and t.is_alive()]
    gw_env()  # the next test's gateway
    thread.join(5)
    assert not thread.is_alive()
    gw = gw_env(RATCHET_DEMO_MAX_REQUESTS="250")
    with TestClient(gw.app) as c:  # the lifespan
        assert c.post("/ratchet/demo/break-next").status_code == 200
        (thread,) = [t for t in threading.enumerate() if t.name == "demo-janitor" and t.is_alive()]
    thread.join(5)
    assert not thread.is_alive()


@pytest.mark.req("FR-11.30")
def test_janitor_thread_starts_in_mock_mode_on_a_demo_action_only(gw_env):
    gw = gw_env(RATCHET_DEMO_IDLE_RESET_S="600")
    started: list[Any] = []
    gw._janitor.thread_factory = lambda **kw: type("T", (), {"start": lambda self: started.append(kw)})()
    c = TestClient(gw.app)
    c.get("/ratchet/stats")
    assert started == []
    c.post("/ratchet/demo/break-next")
    c.post("/ratchet/demo/break-next")
    assert len(started) == 1


@pytest.mark.req("FR-11.30")
def test_demo_lock_held_blocks_the_janitor_reset(gw_env):
    gw = gw_env(RATCHET_DEMO_MAX_REQUESTS="1000")
    assert gw._demo_lock.acquire(blocking=False)
    try:
        assert gw._auto_reset("idle") is False
    finally:
        gw._demo_lock.release()
    assert gw._auto_reset("idle") is True


@pytest.mark.req("FR-11.30")
def test_link_and_demo_settings_are_checked(monkeypatch):
    for bad in (
        "http://x.org",
        "javascript:alert(1)",
        "https://user:pw@x.org",
        "https://x.org/a b",
        "https://x.org/" + "a" * 300,
        "https://",
    ):
        with pytest.raises(ValueError, match="RATCHET_SIM_URL"):
            Settings(sim_url=bad).links()
        with pytest.raises(ValueError, match="RATCHET_LIVE_URL"):
            Settings(live_url=bad).links()
    assert Settings(sim_url="", live_url="  ").links() == (None, None)
    assert Settings(sim_url="https://sim.x.org").links().sim == "https://sim.x.org"
    for kw, var in (
        ({"demo_rpm": 601}, "RATCHET_DEMO_RPM"),
        ({"demo_max_days": 4, "mock_warmup_days": 4}, "RATCHET_DEMO_MAX_DAYS"),
        ({"demo_max_requests": 6000, "mock_warmup_days": 4, "mock_warmup_requests": 1500}, "RATCHET_DEMO_MAX_REQUESTS"),
        ({"demo_idle_reset_s": 60.0}, "RATCHET_DEMO_IDLE_RESET_S"),
        ({"demo_max_days": 4000}, "RATCHET_DEMO_MAX_DAYS"),
    ):
        with pytest.raises(ValueError, match=var):
            Settings(**kw).demo_policy()
    assert Settings().demo_policy() == DemoPolicy()
    # The startup check lists them too, and calls the deploy variables known but a mock-only one in live mode a warning.
    monkeypatch.setenv("RATCHET_SIM_URL", "http://x.org")
    assert any(
        i.var == "RATCHET_SIM_URL" and i.level == config.ERROR
        for i in config.check(Settings(mode="mock", sim_url="http://x.org"))
    )
    env = {"RATCHET_DEMO_RPM": "5", "RATCHET_SIM": "on", "RATCHET_SIM_DOMAIN": "sim.x.org", "RATCHET_SIM_BOM_SIGNING_KEY": ""}
    warned = {i.var for i in config.check(Settings(mode="live", db_path=":memory:"), environ=env) if i.level == config.WARNING}
    assert "RATCHET_DEMO_RPM" in warned and not warned & {"RATCHET_SIM", "RATCHET_SIM_DOMAIN", "RATCHET_SIM_BOM_SIGNING_KEY"}


@pytest.mark.req("FR-11.30", "FR-11.31")
def test_stats_links_demo_and_upstream_blocks(gw_env, monkeypatch):
    gw = gw_env(RATCHET_SIM_URL="https://sim.example.org", RATCHET_LIVE_URL="https://example.org")
    c = TestClient(gw.app)
    assert c.post("/v1/chat/completions", json=_ask()).status_code == 200
    s = c.get("/ratchet/stats").json()
    assert s["links"] == {"live": "https://example.org", "sim": "https://sim.example.org"}
    assert s["demo"]["surface"] == "simulation" and s["demo"]["controls"] is True and s["demo"]["shared"] is True
    assert s["demo"]["actions_per_minute"] is None and s["demo"]["auto_resets"] == 0
    assert s["upstream"] == {"recording": False, "since": None, "token_factory": True, "calls": []}
    monkeypatch.delenv("RATCHET_SIM_URL")
    monkeypatch.delenv("RATCHET_LIVE_URL")
    gw = gw_env()
    assert TestClient(gw.app).get("/ratchet/stats").json()["links"] == {"live": None, "sim": None}
