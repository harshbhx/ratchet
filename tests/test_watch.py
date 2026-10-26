"""The demo watchdog (FR-11.32 to FR-11.34, NFR-66, ADR 0132), run offline.

`ratchet.watch` takes its network, its subprocesses, its clock and its sleeping as default arguments, so every test here
passes fakes: no socket, no Docker, no real clock. The shell scripts run with fake `docker` and `sleep` programs first on
PATH. Which status Token Factory returns for exhausted credit and whether the VM reaches its own public IP are UNVERIFIED;
nothing here asserts either.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ratchet import watch
from ratchet.watch import Env, Reply, WatchConfig

# The watchdog's lock and these tests' shell scripts need a Unix host; on Windows the module skips itself, so
# `pytest -q` still collects.
fcntl = pytest.importorskip("fcntl")

ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")
HOOK = "https://hooks.example/T000/SECRET-TOKEN"
KEY = "wd-secret-key-123"
ADMIN = "a" * 40
T0 = 1_800_000_000.0


class Clock:
    def __init__(self, t: float = T0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


def cfg(tmp_path: Path, **kw: Any) -> WatchConfig:
    base: dict[str, Any] = {
        "state_dir": str(tmp_path),
        "webhook_url": HOOK,
        "webhook_secret": "shh",
        "instance": "demo",
        "mode": "live",
        "domain": "demo.example",
        "api_key": KEY,
    }
    base.update(kw)
    return WatchConfig(**base)


class Net:
    """A fake `fetch`: routes by (method, path); records every request. The webhook is any POST to HOOK."""

    def __init__(self, routes: dict[tuple[str, str], Any] | None = None, hook_status: int = 200):
        self.routes = routes or {}
        self.calls: list[tuple[str, str, dict[str, str], bytes | None]] = []
        self.hook_status = hook_status

    def __call__(self, method: str, url: str, *, headers=None, body=None, timeout=10.0, connect=None) -> Reply:
        self.calls.append((method, url, dict(headers or {}), body))
        if url == HOOK:
            return Reply(self.hook_status)
        path = "/" + url.split("://", 1)[1].split("/", 1)[1] if url.count("/") > 2 else "/"
        r = self.routes.get((method, path), Reply(404))
        return r(url) if callable(r) else r

    def alerts(self) -> list[dict[str, Any]]:
        return [json.loads(b) for m, u, h, b in self.calls if u == HOOK and b]

    def posts(self, path: str) -> list[tuple[dict[str, str], bytes]]:
        return [(h, b or b"") for m, u, h, b in self.calls if m == "POST" and u.endswith(path)]


def completion(model: str = "teacher-model", served_by: str = "teacher") -> Reply:
    body = json.dumps({"object": "chat.completion", "model": model, "choices": [{"message": {"content": "ok"}}]}).encode()
    return Reply(200, {"x-ratchet-served-by": served_by}, body)


def err(status: int, code: str, message: str = "", headers: dict[str, str] | None = None) -> Reply:
    body = json.dumps({"error": {"code": code, "type": "api_error", "message": message}}).encode()
    return Reply(status, headers or {}, body)


# ---------- the module itself ----------
@pytest.mark.req("NFR-66")
def test_watch_imports_only_the_standard_library():
    """The host has Python and nothing else: importing `ratchet.watch` (and `ratchet/__init__`) must not load httpx, FastAPI or
    any project module that does. Checked in a fresh interpreter without site-packages."""
    code = (
        f"import sys; sys.path.insert(0, {str(ROOT)!r}); import ratchet.watch, runpy; "
        "bad = [m for m in ('httpx', 'fastapi', 'pydantic', 'ratchet.config', 'ratchet.ledger') if m in sys.modules]; "
        "print(bad)"
    )
    r = subprocess.run([sys.executable, "-S", "-c", code], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "[]"


@pytest.mark.req("NFR-66")
def test_watch_never_touches_demo_controls_or_the_example_store():
    """INV-8 and INV-10: neither tool requests a mock-only `/ratchet/demo/*` path, and neither can store an example."""
    for path in ("ratchet/watch.py", "scripts/demo_check.py"):
        text = (ROOT / path).read_text(encoding="utf-8")
        assert "/ratchet/demo" not in text and "add_example" not in text, path


@pytest.mark.req("FR-11.32")
def test_config_reads_env_file_and_rejects_bad_values(tmp_path):
    f = tmp_path / ".env"
    f.write_text("# c\nRATCHET_WATCH_FAILURES=4\nRATCHET_WATCH_FAILURES='5'\nRATCHET_API_KEYS=a:1,watchdog:xyz\nBAD LINE\n")
    env = watch.read_env_file(f)
    assert env["RATCHET_WATCH_FAILURES"] == "5" and watch.read_env_file(tmp_path / "none") == {}
    c = WatchConfig.load(env)
    assert (c.failures, c.client, c.api_key, c.enabled) == (5, "watchdog", "xyz", True)
    assert "xyz" not in repr(c) and "RATCHET_API_KEYS" not in repr(c)
    for var, bad in (
        ("RATCHET_WATCH_FAILURES", "1"),
        ("RATCHET_WATCH_FAILURES", "x"),
        ("RATCHET_WATCH", "maybe"),
        ("RATCHET_WATCH_CLIENT", "a b"),
        ("RATCHET_WATCH_CONNECT", "lan"),
        ("RATCHET_ALERT_KINDS", "alert,nope"),
        ("RATCHET_ALERT_WEBHOOK_URL", "http://insecure.example"),
        ("RATCHET_WATCH_HEARTBEAT_URL", "ftp://x"),
    ):
        assert watch.config_issues({var: bad}), (var, bad)
        with pytest.raises(ValueError) as e:
            WatchConfig.load({var: bad})
        assert bad not in str(e.value) or var == "RATCHET_ALERT_KINDS"  # a secret value is never echoed
    assert watch.config_issues({}) == []
    assert watch.api_key_for("a:1, watchdog : k2 ,b:3", "watchdog") == "k2" and watch.api_key_for("a:1", "watchdog") is None


@pytest.mark.req("FR-11.32")
def test_gateway_flags_a_bad_watch_setting_at_startup(monkeypatch):
    """`python -m ratchet.config check` (update.sh runs it in the new image) refuses a bad RATCHET_WATCH_* value in live mode."""
    from ratchet.config import ERROR, WARNING, Settings

    monkeypatch.setenv("RATCHET_WATCH_FAILURES", "1")
    live = Settings(mode="live", api_keys="a:b").validate()
    mock = Settings(mode="mock").validate()
    assert [i.level for i in live if i.var == "RATCHET_WATCH"] == [ERROR]
    assert [i.level for i in mock if i.var == "RATCHET_WATCH"] == [WARNING]


# ---------- alerts ----------
@pytest.mark.req("FR-11.32")
def test_alert_body_signature_and_retries(tmp_path):
    body = watch.alert_body("probe", "x ü", "2026-10-21T00:00:00Z", None, "demo")
    assert json.loads(body) == {
        "kind": "probe",
        "detail": "x ü",
        "ts": "2026-10-21T00:00:00Z",
        "day": None,
        "gateway": "demo",
        "text": "[ratchet] probe: x ü",
    }
    assert b" " not in body.replace("x ü".encode(), b"").replace(b"[ratchet] probe: ", b"")  # compact, sorted
    sig = watch.signature(body, "shh")
    assert sig == "sha256=" + hmac.new(b"shh", body, hashlib.sha256).hexdigest()
    # 5xx is retried after 1, 2 and 4 s; then dropped without raising.
    net, naps, out = Net(hook_status=503), [], io.StringIO()
    env = Env(cfg=cfg(tmp_path), fetch=net, clock=Clock(), sleep=naps.append, out=out)
    assert watch.send_alert(env, "probe", "boom") is False
    assert naps == [1, 2, 4] and len(net.calls) == 4
    h = net.calls[0][2]
    assert h["X-Ratchet-Signature"] == watch.signature(net.calls[0][3], "shh")
    assert HOOK not in out.getvalue() and "SECRET-TOKEN" not in out.getvalue()
    # A kind that is not enabled, or no webhook: log only.
    quiet = Net()
    env = Env(cfg=cfg(tmp_path, kinds=("alert",)), fetch=quiet, clock=Clock(), out=io.StringIO())
    assert watch.send_alert(env, "probe", "x") is False and quiet.calls == []
    env = Env(cfg=cfg(tmp_path, webhook_url=""), fetch=quiet, clock=Clock(), out=io.StringIO())
    assert watch.send_alert(env, "probe", "x") is False and quiet.calls == []


def test_default_kinds_include_the_two_new_ones():
    assert {"probe", "watchdog_restart"} <= set(watch.DEFAULT_ALERT_KINDS) and "alert" in watch.DEFAULT_ALERT_KINDS


# ---------- classification ----------
@pytest.mark.req("FR-11.33")
@pytest.mark.parametrize(
    ("reply", "code", "side", "state"),
    [
        (completion(), "ok", "teacher", "ok"),
        (completion("Fallback-M"), "fallback_teacher", "fallback", "ok"),
        (completion(served_by="student"), "probe_task_not_pinned", "config", "unknown"),
        (Reply(200, {}, b"<html>"), "bad_response", "gateway", "failing"),
        (err(401, "invalid_api_key"), "watchdog_key_refused", "config", "unknown"),
        (Reply(403, {}, b"Forbidden: this path needs X-Ratchet-Admin-Token"), "edge_guard_refused", "edge", "unknown"),
        (err(403, "task_not_allowed"), "probe_task_not_allowed", "config", "unknown"),
        (err(400, "unknown_task"), "probe_task_unknown", "config", "unknown"),
        (err(429, "daily_quota_exceeded"), "watchdog_quota", "config", "unknown"),
        (err(429, "budget_exceeded"), "watchdog_budget", "config", "unknown"),
        (err(429, "gateway_budget_exceeded"), "gateway_budget", "gateway", "failing"),
        (err(429, "upstream_rate_limited", "teacher rate limited: upstream 429"), "upstream_rate_limited", "teacher", "failing"),
        (err(502, "upstream_auth_error", "teacher authentication failed: upstream 401"), "upstream_auth", "teacher", "failing"),
        (err(502, "upstream_auth_error", "teacher authentication failed: upstream 403"), "upstream_auth", "teacher", "failing"),
        (err(502, "upstream_not_found", "teacher model not found: upstream 404"), "upstream_not_found", "teacher", "failing"),
        (err(502, "teacher_unavailable", "teacher unavailable: upstream 402"), "upstream_402", "teacher", "failing"),
        (err(502, "teacher_unavailable", "teacher unavailable: upstream 500"), "teacher_unavailable", "teacher", "failing"),
        (err(400, "context_length_exceeded", "too long"), "upstream_400", "teacher", "failing"),
        (err(503, "teacher_circuit_open", "retry after 7 s", {"retry-after": "7"}), "teacher_circuit_open", "teacher", "failing"),
        (err(504, "teacher_timeout", "teacher timeout: upstream 504"), "teacher_timeout", "teacher", "failing"),
        (err(503, "overloaded"), "gateway_overloaded", "gateway", "failing"),
        (err(500, "api_error"), "gateway_error", "gateway", "failing"),
        (Reply(502, {}, b"Bad Gateway"), "edge_upstream_down", "edge", "unknown"),
        (Reply(0, error="ConnectError"), "unreachable", "edge", "unknown"),
        (Reply(0, error="timeout"), "probe_timeout", "edge", "unknown"),
        (err(418, "teapot"), "other", "gateway", "failing"),
    ],
)
def test_probe_classification_is_by_code_not_http_status(reply, code, side, state):
    out = watch.classify(reply, "fallback-m")
    assert (out.code, out.side, out.state) == (code, side, state)
    assert out.code in watch.CODES and out.ok == (code in ("ok", "fallback_teacher"))


@pytest.mark.req("FR-11.33")
def test_classification_reads_the_upstream_status_and_caps_retry_after():
    out = watch.classify(err(502, "teacher_unavailable", "teacher unavailable: upstream 402"))
    assert out.upstream_status == 402 and not out.retry
    out = watch.classify(err(503, "teacher_circuit_open", "", {"retry-after": "900"}))
    assert out.retry and out.retry_after == 60
    # The same 502 is two different codes by upstream status: not a status-only rule.
    assert watch.classify(err(502, "teacher_unavailable", "upstream 503")).code == "teacher_unavailable"


# ---------- tick: the restart rule ----------
class FakeDocker:
    """A fake `run=`: answers `ps`, `inspect`, `exec` (the liveness snippet) and `restart`; records the rest."""

    def __init__(self, started: str = "2026-01-01T00:00:00.123456789Z", **kw: Any):
        self.status = kw.get("status", "running")
        self.started = started
        self.health: tuple[int, str] | None = (200, '{"status":"ok"}')
        self.ready: tuple[int, str] = (200, json.dumps({"status": "ready", "checks": {"ledger": {"ok": True}}}))
        self.restart_rc = 0
        self.calls: list[list[str]] = []

    def __call__(self, cmd, *, timeout=30, cwd=None):
        cmd = list(cmd)
        self.calls.append(cmd)
        rc, out = 0, ""
        if cmd[:4] == ["docker", "compose", "ps", "-q"]:
            out = "cid123\n"
        elif cmd[:2] == ["docker", "inspect"]:
            out = f"{self.status}|{self.started}\n"
        elif "exec" in cmd and "-c" in cmd:
            reply = self.health if cmd[-1] == "/healthz" else self.ready
            rc, out = (124, "") if reply is None else (0, f"{reply[0]} {reply[1]}\n")
        elif cmd[:3] == ["docker", "compose", "restart"]:
            rc = self.restart_rc
        return subprocess.CompletedProcess(cmd, rc, out, "")

    def restarts(self) -> int:
        return sum(1 for c in self.calls if c[:3] == ["docker", "compose", "restart"])

    def mutations(self) -> list[list[str]]:
        return [
            c
            for c in self.calls
            if c[:3] in (["docker", "compose", "up"], ["docker", "compose", "start"], ["docker", "start", "cid123"])
        ]


def make_env(tmp_path: Path, docker: FakeDocker, net: Net | None = None, clock: Clock | None = None, **kw: Any) -> Env:
    return Env(
        cfg=cfg(tmp_path, **kw),
        fetch=net or Net(),
        run=docker,
        clock=clock or Clock(),
        sleep=lambda s: None,
        out=io.StringIO(),
        compose_dir=str(tmp_path),
    )


@pytest.mark.req("FR-11.32")
def test_three_failed_liveness_checks_restart_once_and_alert(tmp_path):
    d, net = FakeDocker(), Net()
    d.health = None  # hung: the exec times out
    env = make_env(tmp_path, d, net)
    for _ in range(2):
        watch.tick(env)
    assert d.restarts() == 0
    watch.tick(env)
    assert d.restarts() == 1
    (a,) = net.alerts()
    assert a["kind"] == "watchdog_restart" and "after 3 failed liveness checks" in a["detail"] and KEY not in json.dumps(a)
    # The streak restarts from zero, and the cooldown holds the next restart back.
    for _ in range(5):
        watch.tick(env)
    assert d.restarts() == 1


@pytest.mark.req("FR-11.32")
def test_a_teacher_outage_never_restarts_the_gateway(tmp_path):
    """/readyz answers 503 for the teacher's sake while the ledger is fine: a restart would fix nothing."""
    d = FakeDocker()
    d.ready = (
        503,
        json.dumps({"status": "not_ready", "checks": {"ledger": {"ok": True}, "teacher": {"ok": False, "available": False}}}),
    )
    env = make_env(tmp_path, d)
    for _ in range(12):
        watch.tick(env)
    assert d.restarts() == 0


@pytest.mark.req("FR-11.32")
def test_a_broken_ledger_restarts_but_a_busy_one_does_not(tmp_path):
    busy = FakeDocker()
    busy.ready = (200, json.dumps({"checks": {"ledger": {"ok": False, "error": "locked", "busy_runs": 1}}}))
    env = make_env(tmp_path, busy)
    for _ in range(6):
        watch.tick(env)
    assert busy.restarts() == 0
    broken = FakeDocker()
    broken.ready = (503, json.dumps({"checks": {"ledger": {"ok": False, "error": "read_only"}}}))
    net = Net()
    env = make_env(tmp_path / "b", broken, net)
    for _ in range(3):
        watch.tick(env)
    assert broken.restarts() == 1 and "3 failed ledger checks" in net.alerts()[0]["detail"]


@pytest.mark.req("FR-11.32")
def test_restart_cap_start_grace_pause_and_update_lock(tmp_path):
    clock = Clock()
    d = FakeDocker()
    d.health = None
    net = Net()
    env = make_env(tmp_path, d, net, clock, cooldown_s=120)
    for _ in range(3):
        watch.tick(env)
    assert d.restarts() == 1
    for _round in range(2):  # past the cooldown: a second restart, then the hourly cap of 2
        clock.advance(200)
        for _ in range(3):
            watch.tick(env)
    assert d.restarts() == 2
    capped = [a for a in net.alerts() if "not restarting until" in a["detail"]]
    assert len(capped) == 1 and "needs a person" in capped[0]["detail"]
    # An hour later it may restart again.
    clock.advance(3700)
    for _ in range(3):
        watch.tick(env)
    assert d.restarts() == 3
    # While the container is younger than the grace period nothing counts.
    young = FakeDocker(started=watch._iso(T0 - 30).replace("Z", ".5Z"))
    young.health = None
    e2 = make_env(tmp_path / "g", young)
    for _ in range(5):
        watch.tick(e2)
    assert young.restarts() == 0 and not [c for c in young.calls if "exec" in c]
    # Paused: checks run, no restart.
    p = FakeDocker()
    p.health = None
    e3 = make_env(tmp_path / "p", p)
    assert watch._pause(e3, 5) == 0
    for _ in range(5):
        watch.tick(e3)
    assert p.restarts() == 0
    # An update in progress holds update.lock: no restart.
    u = FakeDocker()
    u.health = None
    e4 = make_env(tmp_path / "u", u)
    (tmp_path / "u").mkdir(exist_ok=True)
    fd = os.open(tmp_path / "u" / "update.lock", os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        for _ in range(5):
            watch.tick(e4)
    finally:
        os.close(fd)
    assert u.restarts() == 0


@pytest.mark.req("FR-11.32")
def test_a_stopped_container_is_never_started_and_a_recovery_is_announced(tmp_path):
    d, net = FakeDocker(status="exited"), Net()
    env = make_env(tmp_path, d, net)
    for _ in range(5):
        watch.tick(env)
    assert d.restarts() == 0 and d.mutations() == []
    assert len(net.alerts()) == 1 and "not running" in net.alerts()[0]["detail"]  # de-duplicated
    d.status = "running"
    watch.tick(env)
    assert "recovered" in net.alerts()[-1]["detail"]
    # A failed restart command is reported, not hidden.
    f = FakeDocker()
    f.health, f.restart_rc = None, 1
    net2 = Net()
    e2 = make_env(tmp_path / "f", f, net2)
    for _ in range(3):
        watch.tick(e2)
    assert "restart of ratchet failed (exit 1)" in net2.alerts()[0]["detail"]


# ---------- probe ----------
def inside_runner(task_ok: bool = True, ok: bool = True) -> Any:
    calls: list[list[str]] = []

    def run(cmd, *, timeout=30, cwd=None):
        cmd = list(cmd)
        calls.append(cmd)
        if cmd[-1] == "inspect":
            doc = {
                "mode": "live",
                "probe_task": {"registered": task_ok, "pinned": task_ok, "store": False, "clients_ok": task_ok},
            }
            return subprocess.CompletedProcess(cmd, 0 if ok else 1, json.dumps(doc) + "\n", "")
        return subprocess.CompletedProcess(cmd, 0 if ok else 1, '{"ok": true}\n', "")

    run.calls = calls  # type: ignore[attr-defined]
    return run


def probe_net(chat: Any, scout: dict[str, Any] | None = None, mode: str = "live") -> Net:
    return Net(
        {
            ("GET", "/healthz"): Reply(200, {}, json.dumps({"status": "ok", "mode": mode}).encode()),
            ("GET", "/scout/status"): Reply(
                200, {}, json.dumps(scout or {"tavily": True, "search": {"available": True}}).encode()
            ),
            ("POST", "/v1/chat/completions"): chat,
        }
    )


def probe_env(tmp_path: Path, net: Net, run: Any = None, clock: Clock | None = None, **kw: Any) -> Env:
    return Env(
        cfg=cfg(tmp_path, **kw),
        fetch=net,
        run=run or inside_runner(),
        clock=clock or Clock(),
        sleep=lambda s: None,
        out=io.StringIO(),
        compose_dir=str(tmp_path),
    )


@pytest.mark.req("FR-11.33")
def test_probe_sends_one_tiny_request_on_the_pinned_task_and_sets_the_flag_ok(tmp_path):
    net = probe_net(completion())
    run = inside_runner()
    env = probe_env(tmp_path, net, run, guarded=True, admin_token=ADMIN)
    assert watch.probe(env) == 0
    ((headers, body),) = net.posts("/v1/chat/completions")
    assert headers["X-Ratchet-Task"] == "watchdog-probe" and headers["Authorization"] == f"Bearer {KEY}"
    assert headers["X-Ratchet-Admin-Token"] == ADMIN and headers["X-Client-Request-Id"].startswith("watchdog-")
    sent = json.loads(body)
    assert sent["max_tokens"] == 16 and sent["stream"] is False and sent["temperature"] == 0 and len(sent["messages"]) == 1
    flag = [c for c in run.calls if "flag" in c][0]
    assert flag[flag.index("--state") + 1] == "ok" and flag[flag.index("--code") + 1] == "ok"
    # Open gateway: no admin token header. Nothing was alerted.
    net2 = probe_net(completion())
    watch.probe(probe_env(tmp_path / "o", net2))
    assert "X-Ratchet-Admin-Token" not in net2.posts("/v1/chat/completions")[0][0] and net.alerts() == []


@pytest.mark.req("FR-11.33")
def test_probe_refuses_to_send_unless_the_task_is_pinned_and_the_key_exists(tmp_path):
    net = probe_net(completion())
    assert watch.probe(probe_env(tmp_path, net, inside_runner(task_ok=False))) == 2
    assert net.posts("/v1/chat/completions") == [] and "probe_task_not_pinned" in net.alerts()[0]["detail"]
    # The container cannot be asked (an image without this module): fail closed too.
    net = probe_net(completion())
    assert watch.probe(probe_env(tmp_path / "x", net, inside_runner(ok=False))) == 2 and net.posts("/v1/chat/completions") == []
    net = probe_net(completion())
    assert watch.probe(probe_env(tmp_path / "k", net, api_key="")) == 2 and net.posts("/v1/chat/completions") == []
    assert "probe not configured" in net.alerts()[0]["detail"]
    # A gateway in the wrong mode: Stage One needs live.
    net = probe_net(completion(), mode="mock")
    assert watch.probe(probe_env(tmp_path / "m", net)) == 5 and net.posts("/v1/chat/completions") == []
    assert "mode_mismatch" in net.alerts()[0]["detail"]


@pytest.mark.req("FR-11.33")
def test_mock_mode_probes_nothing(tmp_path):
    net = probe_net(completion())
    env = probe_env(tmp_path, net, mode="mock")
    assert watch.probe(env) == 0 and net.calls == [] and "mock mode" in env.out.getvalue()


@pytest.mark.req("FR-11.33")
def test_a_credit_style_failure_alerts_names_the_status_flags_failing_and_never_leaks(tmp_path):
    chat = err(502, "teacher_unavailable", "teacher unavailable: upstream 402 payment details for sk-LEAK")
    net, run = probe_net(chat), inside_runner()
    env = probe_env(tmp_path, net, run)
    assert watch.probe(env) == 4  # teacher side
    (a,) = net.alerts()
    assert (
        a["kind"] == "probe" and "upstream_402" in a["detail"] and "upstream 402" in a["detail"] and "UNVERIFIED" in a["detail"]
    )
    flag = [c for c in run.calls if "flag" in c][0]
    assert flag[flag.index("--state") + 1] == "failing" and flag[flag.index("--side") + 1] == "teacher"
    seen = json.dumps(net.alerts()) + env.out.getvalue() + " ".join(" ".join(c) for c in run.calls)
    assert "sk-LEAK" not in seen and KEY not in seen and "SECRET-TOKEN" not in seen
    # The same failure an hour later is not alerted again; a recovery is, once.
    clock = Clock()
    env2 = probe_env(tmp_path, net, run, clock)
    clock.advance(3600)
    watch.probe(env2)
    assert len(net.alerts()) == 1
    net.routes[("POST", "/v1/chat/completions")] = completion()
    clock.advance(3600)
    assert watch.probe(env2) == 0
    assert len(net.alerts()) == 2 and "chat recovered" in net.alerts()[1]["detail"]


@pytest.mark.req("FR-11.33")
def test_config_side_failures_alert_but_do_not_show_the_banner(tmp_path):
    for chat, code, exit_code in (
        (err(401, "invalid_api_key"), "watchdog_key_refused", 5),
        (Reply(403, {}, b"Forbidden"), "edge_guard_refused", 5),
        (err(429, "gateway_budget_exceeded"), "gateway_budget", 3),
    ):
        net, run = probe_net(chat), inside_runner()
        assert watch.probe(probe_env(tmp_path / code, net, run)) == exit_code
        assert code in net.alerts()[0]["detail"]
        flag = [c for c in run.calls if "flag" in c][0]
        assert flag[flag.index("--state") + 1] == watch.CODES[code][1]


@pytest.mark.req("FR-11.33")
def test_a_transient_failure_is_retried_once_after_the_wait(tmp_path):
    answers = iter([err(503, "teacher_circuit_open", "", {"retry-after": "9"}), completion()])
    naps: list[float] = []
    net = probe_net(lambda url: next(answers))
    env = probe_env(tmp_path, net)
    env.sleep = naps.append
    assert watch.probe(env) == 0 and naps == [9] and len(net.posts("/v1/chat/completions")) == 2 and net.alerts() == []


@pytest.mark.req("FR-11.33")
def test_probe_flags_a_fallback_teacher_as_ok_but_alerts_on_scout_search(tmp_path):
    net = probe_net(completion("fb-model"), scout={"tavily": True, "search": {"available": False}})
    run = inside_runner()
    assert watch.probe(probe_env(tmp_path, net, run, fallback_model="FB-model")) == 0
    flag = [c for c in run.calls if "flag" in c][0]
    assert flag[flag.index("--state") + 1] == "ok" and flag[flag.index("--search") + 1] == "failing"
    assert [a["detail"] for a in net.alerts() if "scout" in a["detail"]]


@pytest.mark.req("FR-11.33")
def test_probe_options_and_heartbeat(tmp_path):
    net, run = probe_net(completion()), inside_runner()
    beat = "https://hc-ping.example/uuid"
    env = probe_env(tmp_path, net, run, heartbeat_url=beat)
    assert watch.probe(env, no_flag=True, no_alert=True) == 0
    assert not [c for c in run.calls if "flag" in c] and not [c for c in net.calls if c[1] == beat]  # no healthy tick yet
    State = watch.State(env, "watch-tick.json")
    State.data["healthy_at"] = env.clock()
    State.save()
    assert watch.probe(env) == 0 and [c for c in net.calls if c[1] == beat]
    net2 = probe_net(err(502, "teacher_unavailable", "upstream 402"))
    watch.probe(probe_env(tmp_path / "q", net2), no_alert=True)
    assert net2.alerts() == []  # --no-alert
    assert WatchConfig.load({}).failures == 3


# ---------- the flag, in the gateway ----------
@pytest.mark.req("FR-11.33")
def test_flag_view_is_content_free_and_goes_stale():
    now = T0
    iso = watch._iso(now - 60)
    raw = json.dumps(
        {"v": 1, "state": "failing", "side": "teacher", "code": "upstream_402", "since": iso, "checked": iso, "search": "ok"}
    )
    v = watch.flag_view(raw, now, 10800)
    assert v == {"state": "failing", "since": iso, "checked": iso, "search": "ok"} and "code" not in v and "side" not in v
    assert watch.flag_view(raw, now + 4 * 3600, 10800)["state"] == "stale"
    for bad in (None, "", "not json", "[]", '{"checked": "yesterday"}', '{"state": "failing"}'):
        assert watch.flag_view(bad, now, 10800)["state"] == "unknown"
    weird = json.dumps({"state": "<script>", "checked": iso, "since": "x" * 500, "search": {"a": 1}})
    assert watch.flag_view(weird, now, 10800) == {"state": "unknown", "since": None, "checked": iso, "search": "unknown"}


@pytest.mark.req("FR-11.33")
def test_scout_status_carries_live_check_and_reads_the_meta_row(gw_env, monkeypatch):
    from fastapi.testclient import TestClient

    gw = gw_env()
    c = TestClient(gw.app)
    assert c.get("/scout/status").json()["live_check"] == {"state": "off", "since": None, "checked": None, "search": "unknown"}
    rt = gw.rt
    iso = watch._iso(rt.wall_clock() - 5)
    rt.ledger.meta_set(
        watch.META_KEY, json.dumps({"state": "failing", "side": "teacher", "code": "upstream_402", "since": iso, "checked": iso})
    )
    monkeypatch.setattr(rt, "settings", SimpleNamespace(mode="live", watch_flag_max_age_s=10800))
    view = rt.live_check()
    assert view["state"] == "failing" and "code" not in view
    # Cached for 30 s: a new row is not read until then.
    rt.ledger.meta_set(watch.META_KEY, json.dumps({"state": "ok", "checked": iso, "since": iso}))
    assert rt.live_check()["state"] == "failing"
    rt.live_check_cache = (0.0, None)
    assert rt.live_check()["state"] == "ok"


@pytest.mark.req("FR-11.33")
def test_inside_commands_validate_and_write_the_flag(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith("RATCHET_"):
            monkeypatch.delenv(k)
    out = io.StringIO()
    assert watch.main(["inside", "inspect"], out=out) == 0 and json.loads(out.getvalue()) == {
        "mode": "mock"
    }  # the ledger is in memory
    with pytest.raises(SystemExit):
        watch.main(
            ["inside", "flag", "--state", "evil", "--side", "teacher", "--code", "ok", "--checked", "x"], out=io.StringIO()
        )
    monkeypatch.setenv("RATCHET_MODE", "live")
    monkeypatch.setenv("RATCHET_DB", str(tmp_path / "w.db"))
    monkeypatch.setenv("RATCHET_TASKS_FILE", str(ROOT / "deploy" / "vm" / "config" / "tasks.json"))
    stamp = watch._iso(T0)
    args = [
        "inside",
        "flag",
        "--state",
        "failing",
        "--side",
        "teacher",
        "--code",
        "upstream_402",
        "--search",
        "ok",
        "--checked",
        stamp,
    ]
    assert watch.main(args, out=io.StringIO()) == 0
    assert watch.main(["inside", "flag", *args[2:-1], "yesterday"], out=io.StringIO()) == 2
    from ratchet.ledger import Ledger

    led = Ledger(str(tmp_path / "w.db"))
    stored = json.loads(led.meta_get(watch.META_KEY))
    assert stored["state"] == "failing" and stored["code"] == "upstream_402" and stored["since"] == stamp
    assert [e["kind"] for e in led.events() if e["kind"] == "probe"] == ["probe"]
    led.close()
    out = io.StringIO()
    assert watch.main(["inside", "inspect"], out=out) == 0
    info = json.loads(out.getvalue())["probe_task"]
    assert info == {"registered": True, "pinned": True, "store": False, "clients_ok": True}


@pytest.mark.req("FR-11.33")
def test_the_shipped_probe_task_is_pinned_unstored_and_for_one_client():
    from ratchet import taskspec

    (task,) = taskspec.load_file(str(ROOT / "deploy" / "vm" / "config" / "tasks.json"), ["watchdog"])
    assert (task.name, task.pinned, task.store, list(task.clients)) == (watch.PROBE_TASK, True, False, ["watchdog"])


# ---------- commands ----------
@pytest.mark.req("FR-11.32")
def test_cli_status_notify_test_alert_and_off(tmp_path):
    env = {"RATCHET_WATCH_STATE_DIR": str(tmp_path), "RATCHET_ALERT_WEBHOOK_URL": HOOK, "RATCHET_ALERT_WEBHOOK_SECRET": "s"}
    net, out = Net(), io.StringIO()
    assert watch.main(["status"], environ=env, out=out, fetch=net) == 0
    assert "webhook set" in out.getvalue() and HOOK not in out.getvalue()
    assert (
        watch.main(["test-alert"], environ=env, out=io.StringIO(), fetch=net) == 0
        and "test alert from" in net.alerts()[0]["detail"]
    )
    assert (
        watch.main(
            ["notify", "--kind", "watchdog_restart", "--check", "update", "--code", "rolled_back", "--sha", "abc1234"],
            environ=env,
            out=io.StringIO(),
            fetch=net,
        )
        == 0
    )
    assert net.alerts()[-1]["detail"] == "update: rolled_back (abc1234)"
    assert (
        watch.main(["notify", "--kind", "alert", "--check", "x y", "--code", "z"], environ=env, out=io.StringIO(), fetch=net) == 2
    )
    assert watch.main(["test-alert"], environ={**env, "RATCHET_ALERT_WEBHOOK_URL": ""}, out=io.StringIO(), fetch=net) == 1
    off = io.StringIO()
    assert (
        watch.main(["tick"], environ={**env, "RATCHET_WATCH": "off"}, out=off, fetch=net, run=FakeDocker()) == 0
        and "off" in off.getvalue()
    )
    assert watch.main(["tick"], environ={**env, "RATCHET_WATCH_FAILURES": "1"}, out=io.StringIO()) == 2


# ---------- demo_check ----------
def load_demo_check() -> Any:
    import importlib.util

    spec = importlib.util.spec_from_file_location("demo_check", ROOT / "scripts" / "demo_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def site(over: dict[Any, Any] | None = None) -> Net:
    html = Reply(200, {"content-type": "text/html; charset=utf-8"}, b"<html>")
    routes: dict[tuple[str, str], Any] = {
        ("GET", "/"): html,
        ("GET", "/scout"): html,
        ("GET", "/dashboard"): html,
        ("GET", "/healthz"): Reply(200, {}, b'{"status":"ok","version":"9","mode":"live"}'),
        ("GET", "/readyz"): Reply(200, {}, b'{"status":"ready","checks":{}}'),
        ("GET", "/scout/status"): Reply(200, {}, b'{"tavily":true,"search":{"available":true},"live_check":{"state":"ok"}}'),
        ("GET", "/v1/models"): Reply(200, {}, b"{}"),
        ("POST", "/v1/chat/completions"): completion(),
    }
    routes.update(over or {})
    return Net(routes)


@pytest.mark.req("FR-11.34")
def test_demo_check_passes_a_healthy_demo_and_never_prints_a_key():
    dc = load_demo_check()
    out = io.StringIO()
    env = {"MY_KEY": "k-secret", "RATCHET_WATCH_KEY": "w-secret", "RATCHET_ADMIN_TOKEN": ADMIN}
    net = site()
    assert dc.main(["https://demo.example", "--key-env", "MY_KEY", "--chat"], fetch=net, environ=env, out=out) == 0
    text = out.getvalue()
    assert "FAIL" not in text and text.count("OK") >= 7
    assert "k-secret" not in text and "w-secret" not in text and ADMIN not in text
    assert all("/ratchet/demo" not in c[1] for c in net.calls)
    models = [c for c in net.calls if c[1].endswith("/v1/models")][0]
    assert models[2]["Authorization"] == "Bearer k-secret" and models[2]["X-Ratchet-Admin-Token"] == ADMIN
    # The chat check is the probe's request on the pinned task, one POST.
    assert (
        len(net.posts("/v1/chat/completions")) == 1
        and net.posts("/v1/chat/completions")[0][0]["X-Ratchet-Task"] == "watchdog-probe"
    )


@pytest.mark.req("FR-11.34")
@pytest.mark.parametrize(
    ("over", "needle"),
    [
        ({("GET", "/healthz"): Reply(200, {}, b'{"mode":"mock"}')}, "mode 'mock' (want 'live')"),
        ({("GET", "/readyz"): Reply(503, {}, b'{"status":"not_ready"}')}, "/readyz"),
        ({("GET", "/scout/status"): Reply(200, {}, b'{"tavily":false}')}, "tavily configured: False"),
        (
            {("GET", "/scout/status"): Reply(200, {}, b'{"tavily":true,"live_check":{"state":"failing"}}')},
            "live model calls are failing",
        ),
        ({("GET", "/"): Reply(200, {"content-type": "application/json"}, b"{}")}, "not html"),
        ({("GET", "/v1/models"): Reply(401, {}, b"{}")}, "key refused"),
        ({("GET", "/v1/models"): Reply(403, {}, b"Forbidden")}, "edge guard"),
        ({("POST", "/v1/chat/completions"): err(502, "teacher_unavailable", "upstream 402")}, "upstream_402"),
    ],
)
def test_demo_check_fails_on_what_a_judge_would_hit(over, needle):
    dc = load_demo_check()
    out = io.StringIO()
    env = {"MY_KEY": "k", "RATCHET_WATCH_KEY": "w"}
    assert dc.main(["https://demo.example", "--key-env", "MY_KEY", "--chat"], fetch=site(over), environ=env, out=out) == 1
    assert needle in out.getvalue()


@pytest.mark.req("FR-11.34")
def test_demo_check_warns_and_validates_input():
    dc = load_demo_check()
    out = io.StringIO()
    net = site(
        {
            ("GET", "/readyz"): Reply(404),
            ("GET", "/scout/status"): Reply(200, {}, b'{"tavily":true,"live_check":{"state":"stale"}}'),
        }
    )
    assert dc.main(["https://demo.example", "--json"], fetch=net, environ={}, out=out) == 0
    assert {r["status"] for r in json.loads(out.getvalue())} == {"OK", "WARN"}
    assert dc.main(["demo.example"], fetch=net, environ={}, out=io.StringIO()) == 2
    assert dc.main(["https://demo.example", "--chat"], fetch=net, environ={}, out=io.StringIO()) == 1  # no $RATCHET_WATCH_KEY


# ---------- deploy files ----------
@pytest.mark.req("FR-11.32")
def test_cron_file_is_valid_and_cloud_init_installs_the_same_lines():
    cron = (ROOT / "deploy" / "vm" / "ratchet-watch.cron").read_text(encoding="utf-8")
    jobs = [ln for ln in cron.splitlines() if ln and not ln.startswith("#") and "=" not in ln.split()[0]]
    assert len(jobs) == 2 and all(ln.split()[5] == "ratchet" for ln in jobs)  # cron.d lines name their user
    assert "ratchet.watch tick" in jobs[0] and "ratchet.watch probe" in jobs[1] and jobs[0].startswith("*/2 ")
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(
        (ROOT / "deploy" / "vm" / "cloud-init.yaml").read_text(encoding="utf-8").replace("__SSH_PUBLIC_KEY__", "k")
    )
    files = {f["path"]: f for f in doc["write_files"]}
    installed = files["/etc/cron.d/ratchet-watch"]
    assert (
        installed["permissions"] == "0644" and [ln for ln in installed["content"].splitlines() if "ratchet.watch" in ln] == jobs
    )
    compose = (ROOT / "deploy" / "vm" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "./config:/config:ro" in compose


@pytest.mark.skipif(BASH is None, reason="needs bash")
@pytest.mark.req("FR-11.34")
def test_backup_tag_is_validated_and_restore_refuses_a_running_gateway(tmp_path):
    vm = ROOT / "deploy" / "vm"
    env = {**os.environ, "RATCHET_MODE": "mock"}
    r = subprocess.run(
        [BASH, str(vm / "backup.sh")], env={**env, "RATCHET_BACKUP_TAG": "pre-update"}, capture_output=True, text=True
    )
    assert r.returncode == 0 and "nothing to back up" in r.stdout
    live = {**env, "RATCHET_MODE": "live", "RATCHET_BACKUP_TAG": "../evil"}
    r = subprocess.run([BASH, str(vm / "backup.sh")], env=live, capture_output=True, text=True)
    assert r.returncode == 1 and "RATCHET_BACKUP_TAG" in r.stderr
    bin_ = fake_bin(tmp_path)
    (tmp_path / "b.db.gz").write_bytes(b"x")
    r = subprocess.run(
        [BASH, str(vm / "restore.sh"), str(tmp_path / "b.db.gz")],
        env={
            **os.environ,
            "PATH": f"{bin_}:{os.environ['PATH']}",
            "FAKE_DOCKER_RUNNING": "1",
            "FAKE_DOCKER_LOG": str(tmp_path / "log"),
        },
        capture_output=True,
        text=True,
    )
    assert r.returncode == 1 and "stop the gateway first" in r.stderr
    assert subprocess.run([BASH, str(vm / "restore.sh")], capture_output=True, text=True).returncode == 2


def fake_bin(tmp_path: Path) -> Path:
    """First on PATH: a `docker` that logs its calls and answers from FAKE_DOCKER_*, and a `sleep` that returns at once."""
    b = tmp_path / "bin"
    b.mkdir(parents=True, exist_ok=True)
    (b / "sleep").write_text("#!/bin/sh\nexit 0\n")
    (b / "docker").write_text(
        r"""#!/bin/bash
echo "docker $*" >> "$FAKE_DOCKER_LOG"
case "$1 $2" in
  "image inspect") echo sha256:old ;;
  "image prune") ;;
  "inspect -f")
    if [[ -n "${FAKE_DOCKER_HEALTHY_AFTER_ROLLBACK:-}" ]] && grep -q "tag ratchet-gateway:previous ratchet-gateway:local" \
      "$FAKE_DOCKER_LOG"; then echo healthy
    else echo "${FAKE_DOCKER_HEALTH:-healthy}"; fi ;;
  "compose ps")
    if [[ "$*" == *"--status running"* ]]; then [[ -n "${FAKE_DOCKER_RUNNING:-}" ]] && echo cid1; exit 0; fi
    echo cid1 ;;
  "compose exec")
    if [[ "$*" == *ratchet-sim* ]]; then  # the simulation container (ADR 0133): mock mode, ledger-only readiness
      if [[ "$*" == *readyz* ]]; then echo "${FAKE_DOCKER_SIM_READY:-200 ledger_ok}"; exit 0; fi
      if [[ "$*" == *healthz* ]]; then echo "${FAKE_DOCKER_SIM_MODE:-mock}"; exit 0; fi
    fi
    if [[ "$*" == *readyz* ]]; then echo "${FAKE_DOCKER_READY:-200 ledger_ok}"; exit 0; fi
    if [[ "$*" == *healthz* ]]; then echo "${FAKE_DOCKER_MODE:-live}"; exit 0; fi ;;
  "compose run") [[ "$*" == *ratchet.config* ]] && exit "${FAKE_DOCKER_CHECK_RC:-0}"; exit 0 ;;
  "compose cp") : > "$4" ;;
esac
exit 0
"""
    )
    for f in ("sleep", "docker"):
        (b / f).chmod(0o755)
    return b


def update_repo(tmp_path: Path, mode: str = "live") -> Path:
    repo = tmp_path / "repo"
    shutil.copytree(ROOT / "deploy" / "vm", repo / "deploy" / "vm")
    (repo / "deploy" / "vm" / ".env").write_text(f"RATCHET_MODE={mode}\nRATCHET_WATCH_STATE_DIR={tmp_path}/state\n")
    git = ["git", "-c", "user.email=a@b", "-c", "user.name=n"]
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run([*git, "-C", str(repo), "add", "-A"], check=True)
    subprocess.run([*git, "-C", str(repo), "commit", "-qm", "old"], check=True)
    branch = subprocess.run(
        ["git", "-C", str(repo), "symbolic-ref", "--short", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    origin = tmp_path / "origin.git"  # so `git pull --ff-only` has somewhere to pull from
    subprocess.run(["git", "clone", "-q", "--bare", str(repo), str(origin)], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(origin)], check=True)
    subprocess.run(["git", "-C", str(repo), "fetch", "-q", "origin"], check=True)
    subprocess.run(["git", "-C", str(repo), "branch", "-q", "-u", f"origin/{branch}"], check=True)
    return repo


def run_update(tmp_path: Path, repo: Path, *args: str, **env: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    log = tmp_path / "docker.log"
    log.write_text("")
    full = {
        **os.environ,
        "PATH": f"{fake_bin(tmp_path)}:{os.environ['PATH']}",
        "FAKE_DOCKER_LOG": str(log),
        "RATCHET_BACKUP_DIR": str(tmp_path / "backups"),
        **env,
    }
    r = subprocess.run(
        [BASH, str(repo / "deploy" / "vm" / "update.sh"), *args], env=full, capture_output=True, text=True, timeout=120
    )
    return r, log.read_text().splitlines()


@pytest.mark.skipif(BASH is None, reason="needs bash")
@pytest.mark.req("FR-11.34")
def test_update_dry_run_prints_every_change_and_runs_none(tmp_path):
    repo = update_repo(tmp_path)
    r, log = run_update(tmp_path, repo, DRY_RUN="1")
    assert r.returncode == 0, r.stderr
    for step in (
        "RATCHET_BACKUP_TAG=pre-update",
        "+ docker tag ratchet-gateway:local ratchet-gateway:previous",
        "+ git -C",
        "+ docker compose build ratchet ratchet-sim",
        "+ docker compose run --rm --no-deps -T ratchet-sim python -m ratchet.config check",
        "+ docker compose up -d --remove-orphans",
    ):
        assert step in r.stdout, step
    assert not [ln for ln in log if " build" in ln or " up " in ln or "tag " in ln]
    assert run_update(tmp_path, repo, "--nope", DRY_RUN="1")[0].returncode == 1


@pytest.mark.skipif(BASH is None, reason="needs bash")
@pytest.mark.req("FR-11.34")
def test_update_backs_up_first_tags_the_image_and_succeeds(tmp_path):
    repo = update_repo(tmp_path)
    r, log = run_update(tmp_path, repo)
    assert r.returncode == 0, r.stdout + r.stderr
    order = [
        i
        for i, ln in enumerate(log)
        if "compose cp" in ln or "tag ratchet-gateway:local ratchet-gateway:previous" in ln or "compose build" in ln
    ]
    assert "compose cp" in log[order[0]] and "tag ratchet-gateway:local" in log[order[1]] and "compose build" in log[order[2]]
    assert list((tmp_path / "backups").glob("ratchet-*-pre-update.db.gz")) and "verify: ok" in r.stdout
    assert not [ln for ln in log if "force-recreate" in ln]  # no rollback


@pytest.mark.skipif(BASH is None, reason="needs bash")
@pytest.mark.req("FR-11.34")
def test_update_rolls_back_the_checkout_and_the_image_when_unhealthy(tmp_path):
    repo = update_repo(tmp_path)
    old = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    r, log = run_update(tmp_path, repo, FAKE_DOCKER_HEALTH="unhealthy", FAKE_DOCKER_HEALTHY_AFTER_ROLLBACK="1")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "rolling back to" in r.stderr and "after the rollback: ok" in r.stdout
    tags = [ln for ln in log if "docker tag" in ln]
    assert "ratchet-gateway:previous ratchet-gateway:local" in tags[-1]
    assert [ln for ln in log if "up -d --no-build --force-recreate" in ln]
    assert subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip() == old
    assert not [ln for ln in log if "compose stop" in ln]  # no restore: the old image is fine


@pytest.mark.skipif(BASH is None, reason="needs bash")
@pytest.mark.req("FR-11.34")
def test_update_restores_the_ledger_only_when_the_old_image_cannot_open_it(tmp_path):
    repo = update_repo(tmp_path)
    r, log = run_update(tmp_path, repo, FAKE_DOCKER_READY="503 ledger_bad")
    assert r.returncode == 1
    joined = "\n".join(log)
    assert "compose stop ratchet" in joined and "compose start ratchet" in joined and "restored the ledger from" in r.stderr
    assert "requests logged after it are lost" in r.stderr
    # --no-rollback leaves everything alone.
    r2, log2 = run_update(tmp_path / "n", update_repo(tmp_path / "n"), "--no-rollback", FAKE_DOCKER_READY="503 ledger_bad")
    assert r2.returncode == 1 and not [ln for ln in log2 if "force-recreate" in ln or "compose stop" in ln]


@pytest.mark.skipif(BASH is None, reason="needs bash")
@pytest.mark.req("FR-11.34")
def test_update_never_rolls_back_for_a_teacher_outage_or_restores_for_the_wrong_mode(tmp_path):
    repo = update_repo(tmp_path)
    r, log = run_update(tmp_path, repo, FAKE_DOCKER_READY="503 ledger_ok")  # not ready for the teacher's sake only
    assert r.returncode == 1 and "Token Factory is not answering" in r.stderr
    assert not [ln for ln in log if "force-recreate" in ln or "compose stop" in ln]
    r, log = run_update(
        tmp_path / "m", update_repo(tmp_path / "m"), FAKE_DOCKER_MODE="mock"
    )  # the wrong mode rolls back, never restores
    assert r.returncode == 1 and "(mode)" in r.stderr and [ln for ln in log if "force-recreate" in ln]
    assert not [ln for ln in log if "compose stop" in ln]


@pytest.mark.skipif(BASH is None, reason="needs bash")
@pytest.mark.req("FR-11.34")
def test_a_refused_config_check_puts_the_old_image_back_and_changes_nothing_else(tmp_path):
    repo = update_repo(tmp_path)
    r, log = run_update(tmp_path, repo, FAKE_DOCKER_CHECK_RC="2")
    assert r.returncode == 1 and "configuration errors" in r.stderr
    assert "ratchet-gateway:previous ratchet-gateway:local" in [ln for ln in log if "docker tag" in ln][-1]
    assert not [ln for ln in log if "compose up" in ln]
    r, log = run_update(tmp_path / "s", update_repo(tmp_path / "s"), "--skip-backup")
    assert r.returncode == 0 and "without a backup" in r.stderr and not [ln for ln in log if "compose cp" in ln]


@pytest.mark.skipif(BASH is None, reason="needs bash")
@pytest.mark.req("FR-11.34", "FR-11.30")
def test_update_rolls_back_when_the_simulation_container_is_not_ready_or_not_mock(tmp_path):
    """The simulation host is the same image: if it cannot come up healthy, ready and in mock mode, the update is rolled back."""
    for i, env in enumerate(({"FAKE_DOCKER_SIM_READY": "503 ledger_bad"}, {"FAKE_DOCKER_SIM_MODE": "live"})):
        t = tmp_path / str(i)
        r, log = run_update(t, update_repo(t), **env)
        assert r.returncode == 1 and "rolling back" in r.stderr, r.stdout + r.stderr
        assert [ln for ln in log if "force-recreate" in ln]
