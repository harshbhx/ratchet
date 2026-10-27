"""The demo watchdog (FR-11.32 to FR-11.34, ADR 0132): keep the live demo answering until judging ends.

Runs on the VM host from cron, outside the gateway container, because a hung gateway cannot restart or alert itself.
That is the Nebius VM kit's host (deploy/vm); the public demo runs on Cloudflare Workers with Containers, where this
watchdog does not run.
At module level this file imports the standard library only (the host has Python but none of the project's packages);
the `inside` commands import the gateway's modules inside their functions and run in the container.

- `tick` (every 2 minutes): liveness and the restart rule. The container is restarted after N failed liveness checks
  in a row, or N failed ledger checks, within a cooldown and an hourly cap. A teacher or student failure never counts:
  a restart would not fix Token Factory and would throw away the breakers' state.
- `probe` (hourly): one tiny completion through the public URL with a dedicated key on an operator-pinned task, so the
  answer is never stored (INV-10). The reply is classified by its error code and the upstream status the gateway
  names, never by HTTP status alone, and sets a content-free banner flag (`meta['watch.live']`, read by
  `/scout/status`).
- Alerts: a signed JSON webhook (`RATCHET_ALERT_WEBHOOK_URL`), built here because the gateway's process cannot see a
  dead gateway. One alert per failing check, repeated every `RATCHET_WATCH_REALERT_S`, and once on recovery.

Nothing printed, logged, stored or sent holds a key, a URL, a request or answer text, or an upstream message: codes,
statuses, model ids, timestamps and counts only. Which status Token Factory returns for exhausted credit is UNVERIFIED,
so the probe names the upstream status instead of claiming a cause.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

PROBE_TASK = "watchdog-probe"  # the pinned task the probe names (deploy/vm/config/tasks.json)
META_KEY = "watch.live"  # the ledger `meta` row the banner flag lives in
STATES = ("ok", "failing", "unknown")
SIDES = ("teacher", "gateway", "config", "edge", "fallback")
DEFAULT_ALERT_KINDS = (
    "alert",
    "rollback",
    "train_failed",
    "circuit",
    "budget",
    "admin",
    "task_disabled",
    "probe",
    "watchdog_restart",
)
PROBE_BODY = {
    "model": "ratchet",
    "messages": [{"role": "user", "content": "Reply with the single word: ok"}],
    "max_tokens": 16,
    "temperature": 0,
    "stream": False,
}
# code -> (side, flag state). The flag is `failing` only for a teacher-side or gateway-side failure the demo's users feel.
CODES: dict[str, tuple[str, str]] = {
    "ok": ("teacher", "ok"),
    "fallback_teacher": ("fallback", "ok"),
    "probe_task_not_pinned": ("config", "unknown"),
    "bad_response": ("gateway", "failing"),
    "watchdog_key_refused": ("config", "unknown"),
    "edge_guard_refused": ("edge", "unknown"),
    "probe_task_not_allowed": ("config", "unknown"),
    "probe_task_unknown": ("config", "unknown"),
    "watchdog_quota": ("config", "unknown"),
    "watchdog_budget": ("config", "unknown"),
    "gateway_budget": ("gateway", "failing"),
    "gateway_rate_limited": ("config", "unknown"),
    "upstream_rate_limited": ("teacher", "failing"),
    "upstream_auth": ("teacher", "failing"),
    "upstream_not_found": ("teacher", "failing"),
    "upstream_402": ("teacher", "failing"),
    "teacher_unavailable": ("teacher", "failing"),
    "upstream_400": ("teacher", "failing"),
    "teacher_circuit_open": ("teacher", "failing"),
    "teacher_timeout": ("teacher", "failing"),
    "gateway_overloaded": ("gateway", "failing"),
    "gateway_error": ("gateway", "failing"),
    "edge_upstream_down": ("edge", "unknown"),
    "unreachable": ("edge", "unknown"),
    "probe_timeout": ("edge", "unknown"),
    "other": ("gateway", "failing"),
}
RETRY_CODES = frozenset(
    {"bad_response", "gateway_rate_limited", "upstream_rate_limited", "teacher_unavailable", "teacher_circuit_open"}
    | {"teacher_timeout", "gateway_overloaded", "gateway_error", "edge_upstream_down", "unreachable", "probe_timeout"}
)
EXIT_BY_SIDE = {"gateway": 3, "teacher": 4, "config": 5, "edge": 5}
BUSY = frozenset({"locked", "lock_timeout", "timeout"})  # /readyz's ledger errors that mean "busy", not "broken"
_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_UPSTREAM = re.compile(r"\bupstream (\d{3})\b")
_STARTED = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)")
LIVENESS_SNIPPET = (
    "import sys, urllib.error, urllib.request\n"
    "try:\n"
    "    r = urllib.request.urlopen('http://127.0.0.1:8000' + sys.argv[1], timeout=5)\n"
    "    code, body = r.status, r.read().decode()\n"
    "except urllib.error.HTTPError as e:\n"
    "    code, body = e.code, e.read().decode()\n"
    "except Exception as e:\n"
    "    code, body = 0, type(e).__name__\n"
    "print(code, body)\n"
)


# ----- settings -----
def read_env_file(path: str | Path) -> dict[str, str]:
    """`KEY=VALUE` lines of `deploy/vm/.env`: blanks and `#` lines skipped, the last occurrence wins, one pair of matching
    quotes stripped (as Compose does). A missing file is an empty mapping."""
    out: dict[str, str] = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        out[key.strip()] = value
    return out


def _env(name: str, default: str = "", *, env: Mapping[str, str]) -> str:
    return str(env.get(name, default)).strip()


def _int(name: str, default: str, lo: int, hi: int, *, env: Mapping[str, str]) -> int:
    raw = _env(name, default, env=env)
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be a whole number from {lo} to {hi}") from None
    if not lo <= value <= hi:
        raise ValueError(f"{name} must be from {lo} to {hi}")
    return value


def api_key_for(spec: str, name: str) -> str | None:
    """The key named `name` in `RATCHET_API_KEYS` (`name:key,name:key`, as `auth.KeyRing` reads it), or None."""
    for item in spec.split(","):
        n, sep, key = item.strip().partition(":")
        if sep and n.strip() == name and key.strip():
            return key.strip()
    return None


@dataclass(frozen=True)
class WatchConfig:
    """The watchdog's settings (docs/CONFIGURATION.md#live-gateway-watchdog). Secrets are never in a `repr`."""

    enabled: bool = True
    client: str = "watchdog"
    failures: int = 3
    cooldown_s: int = 600
    max_restarts_per_hour: int = 2
    start_grace_s: int = 240
    probe_timeout_s: int = 200
    realert_s: int = 21600
    connect: str = "public"
    state_dir: str = "/var/lib/ratchet"
    mode: str = "mock"
    domain: str = ""
    fallback_model: str = ""
    guarded: bool = False
    instance: str = ""
    kinds: tuple[str, ...] = DEFAULT_ALERT_KINDS
    api_key: str = field(default="", repr=False)
    admin_token: str = field(default="", repr=False)
    webhook_url: str = field(default="", repr=False)
    webhook_secret: str = field(default="", repr=False)
    heartbeat_url: str = field(default="", repr=False)

    @classmethod
    def load(cls, env: Mapping[str, str]) -> WatchConfig:
        """Read and check every `RATCHET_WATCH_*` and `RATCHET_ALERT_*` setting; a bad value raises ValueError naming the
        variable, never echoing a secret."""
        switch = _env("RATCHET_WATCH", "on", env=env).lower()
        if switch not in ("on", "off"):
            raise ValueError("RATCHET_WATCH must be on or off")
        client = _env("RATCHET_WATCH_CLIENT", "watchdog", env=env)
        if not _NAME.match(client):
            raise ValueError("RATCHET_WATCH_CLIENT must be 1 to 64 letters, digits, _, . or -")
        connect = _env("RATCHET_WATCH_CONNECT", "public", env=env).lower()
        if connect not in ("public", "local"):
            raise ValueError("RATCHET_WATCH_CONNECT must be public or local")
        kinds_raw = _env("RATCHET_ALERT_KINDS", "", env=env)
        kinds = tuple(k.strip() for k in kinds_raw.split(",") if k.strip()) or DEFAULT_ALERT_KINDS
        unknown = [k for k in kinds if k not in DEFAULT_ALERT_KINDS]
        if unknown:
            raise ValueError(f"RATCHET_ALERT_KINDS names an unknown kind: {unknown[0]}")
        url = _env("RATCHET_ALERT_WEBHOOK_URL", "", env=env)
        if url and not url.startswith("https://"):
            raise ValueError("RATCHET_ALERT_WEBHOOK_URL must start with https://")
        beat = _env("RATCHET_WATCH_HEARTBEAT_URL", "", env=env)
        if beat and not beat.startswith("https://"):
            raise ValueError("RATCHET_WATCH_HEARTBEAT_URL must start with https://")
        return cls(
            enabled=switch == "on",
            client=client,
            failures=_int("RATCHET_WATCH_FAILURES", "3", 2, 20, env=env),
            cooldown_s=_int("RATCHET_WATCH_RESTART_COOLDOWN_S", "600", 120, 86400, env=env),
            max_restarts_per_hour=_int("RATCHET_WATCH_MAX_RESTARTS_PER_HOUR", "2", 1, 10, env=env),
            start_grace_s=_int("RATCHET_WATCH_START_GRACE_S", "240", 0, 3600, env=env),
            probe_timeout_s=_int("RATCHET_WATCH_PROBE_TIMEOUT_S", "200", 10, 900, env=env),
            realert_s=_int("RATCHET_WATCH_REALERT_S", "21600", 600, 604800, env=env),
            connect=connect,
            state_dir=_env("RATCHET_WATCH_STATE_DIR", "/var/lib/ratchet", env=env),
            mode=_env("RATCHET_MODE", "mock", env=env).lower(),
            domain=_env("RATCHET_DOMAIN", "", env=env),
            fallback_model=_env("RATCHET_TEACHER_FALLBACK_MODEL", "", env=env),
            guarded=_env("RATCHET_API_GUARD", "open", env=env).lower() == "guarded",
            instance=_env("RATCHET_INSTANCE_NAME", "", env=env) or socket.gethostname(),
            kinds=kinds,
            api_key=api_key_for(_env("RATCHET_API_KEYS", "", env=env), client) or "",
            admin_token=_env("RATCHET_ADMIN_TOKEN", "", env=env),
            webhook_url=url,
            webhook_secret=_env("RATCHET_ALERT_WEBHOOK_SECRET", "", env=env),
            heartbeat_url=beat,
        )


def demo_check_keys(env: Mapping[str, str]) -> dict[str, str]:
    """The two keys `scripts/demo_check.py` takes from the environment (never a flag): the `watchdog` key for its live
    completion and the judge key for its free `GET /v1/models`."""
    return {
        "RATCHET_WATCH_KEY": _env("RATCHET_WATCH_KEY", "", env=env),
        "RATCHET_DEMO_CHECK_KEY": _env("RATCHET_DEMO_CHECK_KEY", "", env=env),
    }


def config_issues(env: Mapping[str, str]) -> list[str]:
    """The problems `WatchConfig.load` finds, as strings (empty: none)."""
    try:
        WatchConfig.load(env)
    except ValueError as e:
        return [str(e)]
    return []


# ----- transport -----
@dataclass
class Reply:
    """An HTTP answer. A failure to get one is `status` 0 with the exception class in `error`; nothing is raised."""

    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    error: str = ""


Fetch = Callable[..., Reply]


def urllib_fetch(
    method: str,
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    body: bytes | None = None,
    timeout: float = 10.0,
    connect: str | None = None,
) -> Reply:
    """A standard-library HTTP client that never raises. `connect="local"` opens the TCP connection to 127.0.0.1:443 and
    keeps the URL's host for SNI and `Host`, with certificate checks on, for a VM that cannot reach its own public IP
    (hairpin NAT; whether Nebius needs this is UNVERIFIED)."""
    try:
        if connect == "local":
            return _fetch_local(method, url, headers or {}, body, timeout)
        req = urllib.request.Request(url, data=body, method=method, headers=dict(headers or {}))
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - https URLs from the operator's .env
                return Reply(r.status, {k.lower(): v for k, v in r.headers.items()}, r.read())
        except urllib.error.HTTPError as e:
            return Reply(e.code, {k.lower(): v for k, v in e.headers.items()}, e.read())
    except TimeoutError:
        return Reply(0, error="timeout")
    except Exception as e:  # noqa: BLE001 - every failure is an outcome, named by its class
        return Reply(0, error="timeout" if "timed out" in str(e) else type(e).__name__)


def _fetch_local(method: str, url: str, headers: Mapping[str, str], body: bytes | None, timeout: float) -> Reply:
    import http.client
    import urllib.parse

    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    raw = socket.create_connection(("127.0.0.1", 443), timeout=timeout)
    sock = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
    conn = http.client.HTTPSConnection(host, timeout=timeout)
    conn.sock = sock
    try:
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        conn.request(method, path, body=body, headers={"Host": host, **dict(headers)})
        r = conn.getresponse()
        return Reply(r.status, {k.lower(): v for k, v in r.getheaders()}, r.read())
    finally:
        conn.close()


# ----- classification -----
@dataclass(frozen=True)
class Outcome:
    code: str
    upstream_status: int | None = None
    retry_after: int = 0

    @property
    def side(self) -> str:
        return CODES.get(self.code, CODES["other"])[0]

    @property
    def state(self) -> str:
        return CODES.get(self.code, CODES["other"])[1]

    @property
    def retry(self) -> bool:
        return self.code in RETRY_CODES

    @property
    def ok(self) -> bool:
        return self.code in ("ok", "fallback_teacher")


def _envelope(body: bytes) -> dict[str, Any] | None:
    try:
        doc = json.loads(body)
    except (ValueError, RecursionError):
        return None
    err = doc.get("error") if isinstance(doc, dict) else None
    return err if isinstance(err, dict) else None


def classify(reply: Reply, fallback_model: str = "") -> Outcome:
    """What a probe reply means, by the gateway's error `code` and the upstream status its message names (never by HTTP
    status alone: the gateway maps the teacher's 401, 402 and 403 to 502s). Which upstream status exhausted credit
    produces is UNVERIFIED, so a 402 and an upstream 401 or 403 are named, not explained."""
    status = reply.status
    if status == 0:
        return Outcome("probe_timeout" if reply.error == "timeout" else "unreachable")
    wait = 0
    try:
        wait = min(60, max(0, int(reply.headers.get("retry-after", "0"))))
    except ValueError:
        pass
    if status == 200:
        try:
            doc = json.loads(reply.body)
        except (ValueError, RecursionError):
            doc = None
        if not isinstance(doc, dict) or not isinstance(doc.get("choices"), list):
            return Outcome("bad_response")
        if reply.headers.get("x-ratchet-served-by", "") != "teacher":
            return Outcome("probe_task_not_pinned")
        model = str(doc.get("model", ""))
        if fallback_model and model.lower() == fallback_model.lower():
            return Outcome("fallback_teacher")
        return Outcome("ok")
    err = _envelope(reply.body)
    if err is None:
        if status == 403:
            return Outcome("edge_guard_refused")
        if status in (502, 503, 504):
            return Outcome("edge_upstream_down", retry_after=wait)
        return Outcome("other")
    code = str(err.get("code") or "")
    message = str(err.get("message") or "")
    found = _UPSTREAM.search(message)
    up = int(found.group(1)) if found else None
    table = {
        (401, "invalid_api_key"): "watchdog_key_refused",
        (403, "task_not_allowed"): "probe_task_not_allowed",
        (400, "unknown_task"): "probe_task_unknown",
        (429, "daily_quota_exceeded"): "watchdog_quota",
        (429, "budget_exceeded"): "watchdog_budget",
        (429, "gateway_budget_exceeded"): "gateway_budget",
        (429, "rate_limit_exceeded"): "gateway_rate_limited",
        (429, "upstream_rate_limited"): "upstream_rate_limited",
        (502, "upstream_auth_error"): "upstream_auth",
        (502, "upstream_not_found"): "upstream_not_found",
        (503, "teacher_circuit_open"): "teacher_circuit_open",
        (504, "teacher_timeout"): "teacher_timeout",
        (503, "overloaded"): "gateway_overloaded",
        (500, "api_error"): "gateway_error",
    }
    name = table.get((status, code))
    if name is None and status == 502 and code == "teacher_unavailable":
        name = "upstream_402" if up == 402 else "teacher_unavailable"
    if name is None and status == 400:
        name = "upstream_400"
    return Outcome(name or "other", up, wait)


# ----- alerts -----
def alert_body(kind: str, detail: str, ts: str, day: int | None, gateway: str) -> bytes:
    """The alert's JSON body (the format the gateway's notifier will share): compact, sorted keys, `text` for Slack."""
    doc = {"kind": kind, "detail": detail, "ts": ts, "day": day, "gateway": gateway, "text": f"[ratchet] {kind}: {detail}"}
    return json.dumps(doc, separators=(",", ":"), sort_keys=True, ensure_ascii=False).encode("utf-8")


def signature(body: bytes, secret: str) -> str:
    """`X-Ratchet-Signature`: `sha256=` and the hex HMAC-SHA256 of the body."""
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Env:
    """What a command needs from outside, as seams for tests: HTTP, subprocesses, the clock, sleeping and output."""

    cfg: WatchConfig
    fetch: Fetch = urllib_fetch
    run: Callable[..., Any] = None  # type: ignore[assignment]
    clock: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep
    out: TextIO = field(default_factory=lambda: sys.stdout)
    compose_dir: str = ""

    def __post_init__(self) -> None:
        if self.run is None:
            self.run = _run
        if not self.compose_dir:
            self.compose_dir = str(Path(__file__).resolve().parents[1] / "deploy" / "vm")

    def log(self, line: str) -> None:
        print(f"{_iso(self.clock())} {line}", file=self.out)


def _run(cmd: Sequence[str], *, timeout: float = 30, cwd: str | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout, cwd=cwd, check=False)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(list(cmd), 124, "", "timeout")
    except OSError as e:
        return subprocess.CompletedProcess(list(cmd), 127, "", type(e).__name__)


def send_alert(env: Env, kind: str, detail: str) -> bool:
    """Post one alert when the kind is enabled and a webhook is set; log it always. 5 s timeout, 3 retries (1, 2, 4 s) on a
    network error, 429 or 5xx, then dropped. Never raises; the log line holds the kind and detail, never the URL."""
    cfg = env.cfg
    env.log(f"alert {kind}: {detail}")
    if not cfg.webhook_url or kind not in cfg.kinds:
        return False
    body = alert_body(kind, detail, _iso(env.clock()), None, cfg.instance)
    headers = {"Content-Type": "application/json", "User-Agent": "ratchet-watch/1"}
    if cfg.webhook_secret:
        headers["X-Ratchet-Signature"] = signature(body, cfg.webhook_secret)
    for wait in (1, 2, 4, None):
        r = env.fetch("POST", cfg.webhook_url, headers=headers, body=body, timeout=5)
        if 200 <= r.status < 300:
            return True
        if wait is None or not (r.status == 0 or r.status == 429 or r.status >= 500):
            break
        env.sleep(wait)
    env.log(f"alert {kind}: webhook did not accept it")
    return False


# ----- state and locks -----
class State:
    """A small JSON file in the state directory, written atomically with mode 0600. Counters and codes only."""

    def __init__(self, env: Env, name: str):
        self.path = Path(env.cfg.state_dir) / name
        self.env = env
        self.data: dict[str, Any] = {}
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(self.data, dict):
                raise ValueError
        except FileNotFoundError:
            self.data = {}
        except (OSError, ValueError):
            try:
                self.path.rename(self.path.with_name(f"{name}.bad-{int(env.clock())}"))
            except OSError:
                pass
            env.log(f"{name} was unreadable; starting afresh")
            self.data = {}

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=self.path.name + ".")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.data, f, sort_keys=True)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except OSError as e:
            self.env.log(f"cannot write {self.path.name}: {type(e).__name__}")


class Lock:
    """A non-blocking `flock`: `held` is False when another process has it."""

    def __init__(self, env: Env, name: str):
        self.path = Path(env.cfg.state_dir) / name
        self.fd: int | None = None
        self.held = False

    def __enter__(self) -> Lock:
        import fcntl

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.held = True
        except OSError:
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None
        return self

    def __exit__(self, *exc: object) -> None:
        if self.fd is not None:
            os.close(self.fd)  # closing releases the lock


def _free(env: Env, name: str) -> bool:
    """Whether nobody holds the named lock (an update in progress holds `update.lock`)."""
    with Lock(env, name) as lock:
        return lock.held


def track(env: Env, state: State, check: str, code: str | None, detail: str, kind: str) -> None:
    """De-duplicated alerting for one check: alert on its first failure, on a changed code, every `realert_s` while it keeps
    failing, and once on recovery (`code` None)."""
    alerts = state.data.setdefault("alerts", {})
    now = env.clock()
    prev = alerts.get(check)
    if code is None:
        if prev:
            send_alert(env, kind, f"{check} recovered after {_span(now - prev['since'])}")
            del alerts[check]
        return
    if prev is None or prev["code"] != code or now - prev["last"] >= env.cfg.realert_s:
        send_alert(env, kind, detail)
        alerts[check] = {"code": code, "since": prev["since"] if prev and prev["code"] == code else now, "last": now}


def _span(seconds: float) -> str:
    minutes = int(seconds // 60)
    return f"{minutes // 60} h {minutes % 60} min" if minutes >= 60 else f"{minutes} min"


# ----- tick: liveness and the restart rule -----
def _exec(env: Env, path: str) -> tuple[int, str]:
    r = env.run(
        ["docker", "compose", "exec", "-T", "ratchet", "python", "-c", LIVENESS_SNIPPET, path], timeout=30, cwd=env.compose_dir
    )
    if r.returncode != 0:
        return 0, "exec_failed"
    code, _, body = r.stdout.strip().partition(" ")
    return (int(code) if code.isdigit() else 0), body


def _parse_started(text: str) -> float | None:
    m = _STARTED.match(text)
    if not m:
        return None
    return datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()


def tick(env: Env) -> int:
    """One liveness check. Never starts a stopped container; restarts a running one that fails `failures` liveness checks
    or ledger checks in a row, unless paused, an update is running, the cooldown or the hourly cap says no. Exit 0."""
    cfg = env.cfg
    with Lock(env, "watch-tick.lock") as lock:
        if not lock.held:
            return 0
        st = State(env, "watch-tick.json")
        now = env.clock()
        d = st.data
        cid = env.run(["docker", "compose", "ps", "-q", "ratchet"], timeout=30, cwd=env.compose_dir).stdout.strip()
        info = ""
        if cid:
            info = env.run(
                ["docker", "inspect", "-f", "{{.State.Status}}|{{.State.StartedAt}}", cid], timeout=30, cwd=env.compose_dir
            ).stdout.strip()
        status, _, started_at = info.partition("|")
        if status != "running":
            d["down"] = d.get("down", 0) + 1
            if d["down"] >= cfg.failures:
                text = (
                    "ratchet is not running; the restart policy restarts crashed containers, "
                    "so start it by hand if it was stopped on purpose"
                )
                track(env, st, "liveness", "not_running", text, "watchdog_restart")
            st.save()
            return 0
        d["down"] = 0
        started = _parse_started(started_at)
        if started is not None and now - started < cfg.start_grace_s:
            d["live_fail"] = d["ledger_fail"] = 0
            st.save()
            return 0
        code, body = _exec(env, "/healthz")
        live_ok = code == 200
        d["live_fail"] = 0 if live_ok else d.get("live_fail", 0) + 1
        if live_ok:
            ready_code, ready_body = _exec(env, "/readyz")
            d["ledger_fail"] = d.get("ledger_fail", 0) + 1 if _ledger_broken(ready_code, ready_body) else 0
        else:
            d["ledger_fail"] = 0
        reason = "liveness" if d["live_fail"] >= cfg.failures else "ledger" if d["ledger_fail"] >= cfg.failures else ""
        if not reason:
            if live_ok and d["ledger_fail"] == 0:
                track(env, st, "liveness", None, "", "watchdog_restart")
                if now - d.get("last_ok_log", 0) >= 3600:
                    env.log("tick ok")
                    d["last_ok_log"] = now
                d["healthy_at"] = now
            st.save()
            return 0
        _maybe_restart(env, st, reason, "timeout" if code == 0 else f"http_{code}")
        st.save()
        return 0


def _ledger_broken(code: int, body: str) -> bool:
    """Only `checks.ledger` counts. A 503 for the teacher's sake never does: a restart would not fix Token Factory."""
    if code not in (200, 503):
        return False
    try:
        ledger = json.loads(body)["checks"]["ledger"]
    except (ValueError, KeyError, TypeError):
        return False
    if not isinstance(ledger, dict) or ledger.get("ok") is not False:
        return False
    return ledger.get("error") not in BUSY or int(ledger.get("busy_runs", 0) or 0) >= 2


def _maybe_restart(env: Env, st: State, reason: str, why: str) -> None:
    cfg, d, now = env.cfg, st.data, env.clock()
    recent = [t for t in d.get("restarts", []) if now - t < 3600]
    d["restarts"] = recent
    n = cfg.failures
    if _paused(env):
        env.log(f"ratchet failed {n} {reason} checks; watchdog is paused, not restarting")
        return
    if not _free(env, "update.lock"):
        env.log("an update is running; not restarting")
        return
    if recent and now - recent[-1] < cfg.cooldown_s:
        return
    if len(recent) >= cfg.max_restarts_per_hour:
        until = _iso(recent[0] + 3600)
        text = (
            f"ratchet failed {n} more {reason} checks but was restarted {len(recent)} times in the last hour; "
            f"not restarting until {until}; it needs a person"
        )
        track(env, st, "liveness", "restart_cap", text, "watchdog_restart")
        return
    r = env.run(["docker", "compose", "restart", "ratchet"], timeout=120, cwd=env.compose_dir)
    recent.append(now)
    d["live_fail"] = d["ledger_fail"] = 0
    if r.returncode == 0:
        detail = f"restarted ratchet after {n} failed {reason} checks ({why})"
    else:
        detail = f"restart of ratchet failed (exit {r.returncode})"
    send_alert(env, "watchdog_restart", detail)
    d.get("alerts", {}).pop("liveness", None)


def _paused(env: Env) -> bool:
    path = Path(env.cfg.state_dir) / "watch.paused"
    try:
        return float(path.read_text(encoding="utf-8").strip()) > env.clock()
    except (OSError, ValueError):
        return False


# ----- probe -----
def probe_request(cfg: WatchConfig, stamp: str) -> tuple[str, dict[str, str], bytes]:
    """The probe's URL, headers and body: as a judge would call it, with the watchdog's key on the pinned task."""
    headers = {
        "Authorization": f"Bearer {cfg.api_key}",
        "Content-Type": "application/json",
        "X-Ratchet-Task": PROBE_TASK,
        "X-Client-Request-Id": f"watchdog-{stamp}",
        "User-Agent": "ratchet-watch/1",
    }
    if cfg.guarded and cfg.admin_token:
        headers["X-Ratchet-Admin-Token"] = cfg.admin_token
    return f"https://{cfg.domain}/v1/chat/completions", headers, json.dumps(PROBE_BODY).encode("utf-8")


def _public(env: Env, path: str, timeout: float = 15) -> Reply:
    cfg = env.cfg
    return env.fetch(
        "GET", f"https://{cfg.domain}{path}", headers={"User-Agent": "ratchet-watch/1"}, timeout=timeout, connect=_connect(cfg)
    )


def _connect(cfg: WatchConfig) -> str | None:
    return "local" if cfg.connect == "local" else None


def _inside(env: Env, *args: str) -> tuple[bool, dict[str, Any]]:
    """Run `python -m ratchet.watch inside ...` in the container. (ok, its JSON line); a rolled-back image without this
    module, or a missing container, is (False, {})."""
    r = env.run(
        ["docker", "compose", "exec", "-T", "ratchet", "python", "-m", "ratchet.watch", "inside", *args],
        timeout=60,
        cwd=env.compose_dir,
    )
    if r.returncode != 0:
        return False, {}
    try:
        doc = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return False, {}
    return isinstance(doc, dict), doc if isinstance(doc, dict) else {}


def probe(env: Env, *, once: bool = False, no_flag: bool = False, no_alert: bool = False, force: bool = False) -> int:
    """The hourly live check. Exit 0 ok or fallback, 2 not configured, 3 gateway side, 4 teacher side, 5 config or edge."""
    cfg = env.cfg
    if cfg.mode != "live" and not force:
        print("mock mode: nothing to probe", file=env.out)
        return 0
    if no_alert:
        env = replace(env, cfg=replace(cfg, webhook_url=""))
    with Lock(env, "watch-probe.lock") as lock:
        if not lock.held:
            return 0
        st = State(env, "watch-probe.json")
        code = _probe_locked(env, st, once=once, no_flag=no_flag)
        st.save()
        return code


def _probe_locked(env: Env, st: State, *, once: bool, no_flag: bool) -> int:
    cfg, d = env.cfg, st.data
    if not cfg.domain or not cfg.api_key:
        track(
            env,
            st,
            "config",
            "probe_not_configured",
            f"probe not configured: add {cfg.client}:<key> to RATCHET_API_KEYS and set RATCHET_DOMAIN (see DEPLOY_NEBIUS_VM.md)",
            "probe",
        )
        return 2
    today = _iso(env.clock())[:10]
    if d.get("task_ok_day") != today:
        ok, info = _inside(env, "inspect")
        task = info.get("probe_task") or {}
        good = ok and task.get("registered") and task.get("pinned") and task.get("clients_ok")
        if not good:
            why = "the container could not be asked" if not ok else "the task is missing, not pinned, or open to other keys"
            track(
                env,
                st,
                "config",
                "probe_task_not_pinned",
                f"probe_task_not_pinned: {PROBE_TASK}: {why}; no completion was sent",
                "probe",
            )
            d.pop("task_ok_day", None)
            return 2
        d["task_ok_day"] = today
    health = _public(env, "/healthz")
    try:
        mode = json.loads(health.body).get("mode")
    except (ValueError, AttributeError):
        mode = None
    if health.status == 200 and mode != cfg.mode:
        track(env, st, "config", "mode_mismatch", f"mode_mismatch: the gateway reports {mode!r}, .env says {cfg.mode!r}", "probe")
        return 5
    outcome = _send(env)
    if not outcome.ok and outcome.retry:
        env.sleep(outcome.retry_after or 60)
        outcome = _send(env)
    d["last"] = {"code": outcome.code, "upstream": outcome.upstream_status, "at": env.clock()}
    state = outcome.state
    detail = f"live teacher call failed: {outcome.code} ({outcome.side} side"
    detail += f", upstream {outcome.upstream_status}" if outcome.upstream_status else ""
    detail += ")"
    if outcome.code in ("upstream_402", "upstream_auth", "upstream_rate_limited"):
        detail += "; may be exhausted credit or a suspended account (UNVERIFIED), check the Token Factory console"
    track(env, st, "chat", None if outcome.ok else outcome.code, detail, "probe")
    search = _scout_check(env, st)
    if not no_flag:
        _inside(
            env,
            "flag",
            "--state",
            state,
            "--side",
            outcome.side,
            "--code",
            outcome.code,
            "--search",
            search,
            "--checked",
            _iso(env.clock()),
        )
    if outcome.ok and not once and cfg.heartbeat_url:  # a dead-man's switch: only after a healthy tick and a healthy probe
        if env.clock() - State(env, "watch-tick.json").data.get("healthy_at", 0) < 600:
            env.fetch("GET", cfg.heartbeat_url, timeout=10)
    return 0 if outcome.ok else EXIT_BY_SIDE.get(outcome.side, 3)


def _send(env: Env) -> Outcome:
    cfg = env.cfg
    url, headers, body = probe_request(cfg, datetime.fromtimestamp(env.clock(), timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    reply = env.fetch("POST", url, headers=headers, body=body, timeout=cfg.probe_timeout_s, connect=_connect(cfg))
    return classify(reply, cfg.fallback_model)


def _scout_check(env: Env, st: State) -> str:
    """Scout's search state from the free `/scout/status`: ok, failing or unknown. Costs nothing."""
    reply = _public(env, "/scout/status")
    try:
        doc = json.loads(reply.body)
    except (ValueError, RecursionError):
        doc = None
    if reply.status != 200 or not isinstance(doc, dict):
        return "unknown"
    search = doc.get("search") if isinstance(doc.get("search"), dict) else None
    if search is None:
        failing, code = doc.get("tavily") is False, "tavily_not_configured"
    else:
        failing, code = search.get("available") is False, "search_unavailable"
    track(
        env,
        st,
        "scout_search",
        code if failing else None,
        f"scout search is unavailable ({code}); Scout labels it per run",
        "probe",
    )
    return "failing" if failing else "ok"


# ----- container-side commands -----
def _inside_main(argv: Sequence[str], out: TextIO) -> int:
    p = argparse.ArgumentParser(prog="ratchet.watch inside")
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("flag")
    f.add_argument("--state", choices=STATES, required=True)
    f.add_argument("--side", choices=SIDES, required=True)
    f.add_argument("--code", choices=sorted(CODES), required=True)
    f.add_argument("--search", choices=STATES, default="unknown")
    f.add_argument("--checked", required=True)
    sub.add_parser("inspect")
    a = p.parse_args(list(argv))
    from .config import Settings

    s = Settings()
    if s.mode != "live":
        print(json.dumps({"mode": "mock"}), file=out)  # the ledger is in memory: nothing to write, nothing to read
        return 0
    from .ledger import Ledger

    led = Ledger(s.db_path, s.golden_fraction, auto_day=True, sync=s.ledger_sync())
    if a.cmd == "flag":
        if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", a.checked):
            return 2
        try:
            old = json.loads(led.meta_get(META_KEY) or "{}")
        except ValueError:
            old = {}
        since = old.get("since") if old.get("state") == a.state else a.checked
        doc = {
            "v": 1,
            "state": a.state,
            "side": a.side,
            "code": a.code,
            "since": since or a.checked,
            "checked": a.checked,
            "search": a.search,
        }
        led.meta_set(META_KEY, json.dumps(doc, sort_keys=True))
        if old.get("state") != a.state:
            led.event("probe", f"live check {a.state} ({a.code})")
        print(json.dumps({"ok": True}), file=out)
        return 0
    from . import tasks, taskspec

    taskspec.install(led, s.tasks_file, fixtures_dir=s.fixtures_dir)
    task = tasks.REGISTRY.get(PROBE_TASK)
    client = _env("RATCHET_WATCH_CLIENT", "watchdog", env=os.environ)
    info = {
        "registered": task is not None,
        "pinned": bool(task and task.pinned),
        "store": bool(task and task.store),
        "clients_ok": bool(task and list(task.clients) == [client]),
    }
    print(json.dumps({"mode": "live", "probe_task": info}), file=out)
    return 0


def flag_view(raw: str | None, now: float, max_age_s: int) -> dict[str, Any]:
    """The public view of the flag (`/scout/status`): state, since, checked and search only, never a code or text. `stale`
    when the last check is older than `max_age_s` (the watchdog stopped), `unknown` for no row or a malformed one."""
    out: dict[str, Any] = {"state": "unknown", "since": None, "checked": None, "search": "unknown"}
    try:
        doc = json.loads(raw) if raw else None
        if not isinstance(doc, dict):
            return out
        checked = datetime.strptime(str(doc["checked"]), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
        state = doc.get("state") if doc.get("state") in STATES else "unknown"
        search = doc.get("search") if doc.get("search") in STATES else "unknown"
        since = doc.get("since") if isinstance(doc.get("since"), str) and len(doc["since"]) <= 20 else None
    except (ValueError, KeyError, TypeError):
        return out
    if now - checked > max_age_s:
        state = "stale"
    return {"state": state, "since": since, "checked": str(doc["checked"]), "search": search}


# ----- commands -----
def _status(env: Env) -> int:
    cfg = env.cfg
    tick_st, probe_st = State(env, "watch-tick.json").data, State(env, "watch-probe.json").data
    recent = len([t for t in tick_st.get("restarts", []) if env.clock() - t < 3600])
    print(
        f"watch: {'on' if cfg.enabled else 'off'}; mode {cfg.mode}; alerts: {'webhook set' if cfg.webhook_url else 'log only'}",
        file=env.out,
    )
    print(
        f"paused: {'yes' if _paused(env) else 'no'}; restarts in the last hour: {recent}",
        file=env.out,
    )
    last = probe_st.get("last")
    print(f"last probe: {last['code'] if last else 'none'}" + (f" at {_iso(last['at'])}" if last else ""), file=env.out)
    print(f"failing checks: {sorted({**tick_st.get('alerts', {}), **probe_st.get('alerts', {})}) or 'none'}", file=env.out)
    return 0


def _pause(env: Env, minutes: int) -> int:
    path = Path(env.cfg.state_dir) / "watch.paused"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(env.clock() + 60 * minutes), encoding="utf-8")
    print(f"paused for {minutes} min", file=env.out)
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    fetch: Fetch = urllib_fetch,
    run: Callable[..., Any] | None = None,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
    environ: Mapping[str, str] | None = None,
    out: TextIO | None = None,
) -> int:
    out = out or sys.stdout
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "inside":
        return _inside_main(args[1:], out)
    p = argparse.ArgumentParser(prog="python3 -m ratchet.watch", description=__doc__.splitlines()[0])
    p.add_argument("--env-file", default=str(Path(__file__).resolve().parents[1] / "deploy" / "vm" / ".env"))
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("tick")
    pr = sub.add_parser("probe")
    for flag in ("--once", "--no-flag", "--no-alert", "--force"):
        pr.add_argument(flag, action="store_true")
    sub.add_parser("status")
    pa = sub.add_parser("pause")
    pa.add_argument("--minutes", type=int, default=60)
    sub.add_parser("resume")
    sub.add_parser("test-alert")
    n = sub.add_parser("notify")
    n.add_argument("--kind", required=True)
    n.add_argument("--check", required=True)
    n.add_argument("--code", required=True)
    n.add_argument("--sha", default="")
    a = p.parse_args(args)
    merged = {**read_env_file(a.env_file), **(os.environ if environ is None else environ)}
    try:
        cfg = WatchConfig.load(merged)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    env = Env(cfg=cfg, fetch=fetch, run=run, clock=clock, sleep=sleep, out=out)  # type: ignore[arg-type]
    if not cfg.enabled and a.cmd != "status":
        print("RATCHET_WATCH=off: nothing to do", file=out)
        return 0
    if a.cmd == "tick":
        return tick(env)
    if a.cmd == "probe":
        return probe(env, once=a.once, no_flag=a.no_flag, no_alert=a.no_alert, force=a.force)
    if a.cmd == "status":
        return _status(env)
    if a.cmd == "pause":
        return _pause(env, max(1, min(1440, a.minutes)))
    if a.cmd == "resume":
        (Path(cfg.state_dir) / "watch.paused").unlink(missing_ok=True)
        print("resumed", file=out)
        return 0
    if a.cmd == "test-alert":
        if not cfg.webhook_url:
            print("no RATCHET_ALERT_WEBHOOK_URL: nothing to send", file=out)
            return 1
        ok = send_alert(env, "probe", f"test alert from {cfg.instance}")
        print("webhook accepted the test alert" if ok else "webhook did not accept the test alert", file=out)
        return 0 if ok else 1
    # notify: a vocabulary-checked alert for update.sh (kind, check and code are fixed words, the sha is hex)
    if (
        a.kind not in DEFAULT_ALERT_KINDS
        or not re.fullmatch(r"[a-z_:]{1,32}", a.check)
        or not re.fullmatch(r"[a-z_0-9]{1,32}", a.code)
    ):
        print("error: bad notify arguments", file=sys.stderr)
        return 2
    sha = a.sha if re.fullmatch(r"[0-9a-f]{4,40}", a.sha) else ""
    send_alert(env, a.kind, f"{a.check}: {a.code}" + (f" ({sha})" if sha else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
