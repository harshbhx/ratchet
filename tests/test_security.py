"""Endpoint access, scrubbed public stats, the OpenAPI switch, the live-mode auth guard and security headers (ADR 0061).

Who may call each route is one table (`ratchet/access.py`), enforced for the dashboard API by the gateway's `_access`
dependency: these tests check every route in the app against it and against the Access column of docs/API.md, then
call every reader-level route of a private live gateway with no key, a wrong key, an API key, a dashboard key and an
admin key. A public live dashboard shows anonymous readers client pseudonyms and no URLs or error messages; keys and
key names never leak (invariant 13).
"""

from __future__ import annotations

import copy
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fakes import OPEN_TEACHER, FakeOpenAIServer, _version
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from ratchet import access
from ratchet.config import Access, Settings

ROOT = Path(__file__).resolve().parents[1]
CHAT = "/v1/chat/completions"
ACME_KEY = "acme-api-key-0123456789abcdef"
GLOBEX_KEY = "globex-api-key-0123456789abcd"
ADMIN_KEY = "ops-admin-key-0123456789abcdef"
DASH_KEY = "screen-dash-key-0123456789abcd"
ACME, GLOBEX = {"Authorization": f"Bearer {ACME_KEY}"}, {"Authorization": f"Bearer {GLOBEX_KEY}"}
ADMIN, DASH = {"Authorization": f"Bearer {ADMIN_KEY}"}, {"X-API-Key": DASH_KEY}
SECURITY = {"x-content-type-options": "nosniff", "referrer-policy": "no-referrer"}
# What a v0.4 ledger holds: `student_error` events with httpx's text, which names the endpoint (ADR 0061).
OLD_EVENT = "v1: ConnectError: [Errno 111] Connection refused (https://student.internal:8000/v1/chat/completions)"


def _live(tmp_path: Path, **overrides: Any) -> Settings:
    """A live-mode Settings with every value these tests depend on set: three kinds of keys, a private dashboard."""
    base: dict[str, Any] = {
        "mode": "live",
        "db_path": str(tmp_path / "live.db"),
        "teacher_base": "https://tokenfactory.test/v1",
        "teacher_api_key": "tf-key",
        "teacher_model": OPEN_TEACHER,
        "student_base": "",
        "samples": 3,
        "audit_rate": 0.0,
        "api_keys": f"acme:{ACME_KEY},globex:{GLOBEX_KEY}",
        "admin_keys": f"ops:{ADMIN_KEY}",
        "dashboard_keys": f"screen:{DASH_KEY}",
        "api_key_daily_requests": 0,
        "tasks_file": "",
        "unknown_tasks": "distill",
        "bom_signing_key": "",
        "tavily_api_key": "",
        "mock_warmup_days": 0,
        "public_dashboard": None,
        "public_bom": None,
        "scrub_stats": None,
        "openapi": None,
        "allow_unauthenticated": None,
        "csp": "",
        "stats_ttl_s": 3600.0,  # one cached payload for every reader: scrubbing must never change it
    }
    return Settings(**{**base, **overrides})


def _live_gateway(gw_env: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> tuple[Any, Any]:
    gw = gw_env()
    rt = gw.Runtime(_live(tmp_path, **overrides), transports={"teacher": FakeOpenAIServer("ok").transport})
    monkeypatch.setattr(gw, "rt", rt)
    return gw, rt


def _routes(app: Any) -> list[tuple[str, str]]:
    """(method, path template) for every API route of the app."""
    return sorted((m, r.path) for r in app.routes if isinstance(r, APIRoute) for m in r.methods)


def _call(
    c: TestClient, method: str, template: str, headers: dict[str, str] | None = None, request_id: str = "req_" + "0" * 32
) -> Any:
    path = template.replace("{vid}", "1").replace("{name}", "ticket-triage").replace("{request_id}", request_id)
    body = {"text": "mail jane@example.com"} if method == "POST" else None
    return c.request(method, path, json=body, headers=headers or {})


# ---------- the table ----------
@pytest.mark.req("NFR-5", "FR-12.7")
def test_every_route_has_an_access_level(gw):
    """Every route the app serves is classified, by the table or a prefix, and docs/API.md shows the same level for it,
    so a new endpoint cannot be public by accident: unclassified, it is DASHBOARD (closed in live mode)."""
    routes = _routes(gw.app)
    assert routes and [r for r in routes if access.route_access(*r) is None] == []
    assert set(access.ROUTES.values()) | {lvl for _, lvl in access.PREFIXES} <= set(access.LEVELS)

    documented: dict[tuple[str, str], str] = {}
    for line in (ROOT / "docs" / "API.md").read_text(encoding="utf-8").splitlines():
        row = re.match(r"^\| (GET|POST|PUT|DELETE) \| (.+?) \| [^|]+ \| \[(\w+)\]\(#access-levels\) \|", line)
        if row:
            for path in re.findall(r"`(/[^`\s?]*)", row.group(2)):
                documented[(row.group(1), re.sub(r"\{[^}]+\}", "{}", path))] = row.group(3)
    for method, path in routes:
        key = (method, re.sub(r"\{[^}]+\}", "{}", path))
        assert documented.get(key) == access.route_access(method, path), (method, path)

    # Lookups: HEAD reads as GET, prefixes cover routes other packages add, and nothing else is guessed.
    assert access.route_access("HEAD", "/ratchet/stats") == access.DASHBOARD
    assert access.route_access("GET", "/ratchet/requests/{rid}") == access.DASHBOARD
    assert access.route_access("GET", "/ratchet/versions/{vid}/commitment") == access.DASHBOARD
    assert access.route_access("POST", "/v1/ratchet/no-train") == access.API
    assert access.route_access("GET", "/livez") == access.route_access("GET", "/readyz") == access.PUBLIC
    assert access.route_access("GET", "/ratchet/kpis") == access.DASHBOARD
    assert access.route_access("GET", "/metrics") == access.METRICS  # a dashboard read that also takes the metrics token
    assert access.route_access("GET", "/ratchet/new-thing") is None


# ---------- a private live dashboard ----------
@pytest.mark.req("NFR-5", "FR-12.7", "FR-1.7", "INV-13")
def test_private_live_dashboard_needs_an_admin_or_dashboard_key(gw_env, tmp_path, monkeypatch):
    gw, rt = _live_gateway(gw_env, tmp_path, monkeypatch)
    vid = _version(rt.ledger, "live", {"threshold": 0.5})
    rt.ledger.set_bom(vid, {"schema": "ratchet.bom/v1", "adapter": "student-v1"})
    c = TestClient(gw.app)
    answered = c.post(CHAT, json={"messages": [{"role": "user", "content": "hi"}]}, headers=ACME)
    assert answered.status_code == 200
    rid = answered.headers["x-request-id"]  # so the request lookup has a request to find (ADR 0062)

    readers = [r for r in _routes(gw.app) if access.route_access(*r) in access.READER_LEVELS]
    assert ("GET", "/ratchet/stats") in readers and ("GET", "/ratchet/versions/{vid}/bom") in readers
    assert ("GET", "/metrics") in readers and ("GET", "/ratchet/requests/{request_id}") in readers
    for method, path in readers:
        r = _call(c, method, path)
        assert (r.status_code, r.headers.get("www-authenticate")) == (401, "Bearer"), (method, path, r.text)
        assert r.json() == {"detail": "invalid or missing dashboard key"}
        assert _call(c, method, path, {"Authorization": "Bearer not-a-key-0123456789"}).status_code == 401
        r = _call(c, method, path, ACME)  # a client's API key reads nothing here
        assert r.status_code == 403 and "API key" in r.json()["detail"], (method, path)
        for key in (ADMIN, DASH, {"X-API-Key": ADMIN_KEY}, {"Authorization": f"Bearer {DASH_KEY}"}):
            assert _call(c, method, path, key, request_id=rid).status_code == 200, (method, path, key)

    # Always public: health, the BOM public key, the pages and Scout.
    for path in ("/healthz", "/ratchet/bom/public-key", "/", "/dashboard", "/scout", "/lite", "/scout/examples"):
        assert c.get(path).status_code == 200, path
    # The admin API is still the admin's alone: a dashboard key reads, never writes.
    r = c.put("/ratchet/tasks/notes", json={}, headers=DASH)
    assert r.status_code == 403 and "dashboard key" in r.json()["detail"]
    assert c.get("/ratchet/tasks/notes", headers=DASH).status_code == 404  # nothing was written
    assert c.post(CHAT, json={"messages": [{"role": "user", "content": "hi"}]}, headers=DASH).status_code == 401

    # Admin and dashboard readers see everything, with names only (invariant 13).
    stats = c.get("/ratchet/stats", headers=DASH)
    body = stats.json()
    assert [x["client"] for x in body["clients"]] == ["acme"] and body["security"]["scrubbed"] is False
    seen = {k: body["security"][k] for k in ("admin_keys", "dashboard_keys", "public_dashboard")}
    assert seen == {"admin_keys": True, "dashboard_keys": True, "public_dashboard": False}
    for key in (ACME_KEY, GLOBEX_KEY, ADMIN_KEY, DASH_KEY):
        assert key not in stats.text and key not in "\n".join(rt.ledger.db.iterdump())

    # RATCHET_PUBLIC_BOM=1 opens the bills of materials for auditors, and nothing else.
    gw, rt = _live_gateway(gw_env, tmp_path, monkeypatch, db_path=str(tmp_path / "bom.db"), public_bom=True)
    vid = _version(rt.ledger, "live", {"threshold": 0.5})
    rt.ledger.set_bom(vid, {"schema": "ratchet.bom/v1", "adapter": "student-v1"})
    c = TestClient(gw.app)
    assert c.get("/ratchet/bom").status_code == 200 and c.get(f"/ratchet/versions/{vid}/bom").json()["adapter"] == "student-v1"
    assert c.get("/ratchet/versions/999/bom").status_code == 404  # past the gate: there is no such version
    for path in ("/ratchet/stats", "/ratchet/tasks", "/ratchet/cost"):
        assert c.get(path).status_code == 401, path


@pytest.mark.req("NFR-5", "FR-12.7")
def test_private_dashboard_without_reader_keys_is_closed_to_everyone(gw_env, tmp_path, monkeypatch):
    """With neither admin nor dashboard keys a private dashboard API has no reader: 403 that says what to set, as the
    admin API does without admin keys."""
    gw, _ = _live_gateway(gw_env, tmp_path, monkeypatch, admin_keys="", dashboard_keys="")
    c = TestClient(gw.app)
    for headers in ({}, ACME):
        r = c.get("/ratchet/stats", headers=headers)
        assert r.status_code == 403 and "RATCHET_DASHBOARD_KEYS" in r.json()["detail"]
    assert c.get("/healthz").status_code == 200


@pytest.mark.req("NFR-5", "FR-12.7")
def test_mock_dashboard_is_public_by_default(gw_env):
    """Mock mode keeps v0.4's open dashboard, with or without admin keys (the existing tests read it without a key); it
    can be closed like a live one."""
    for env in ({}, {"RATCHET_ADMIN_KEYS": f"ops:{ADMIN_KEY}", "RATCHET_API_KEYS": f"acme:{ACME_KEY}"}):
        c = TestClient(gw_env(**env).app)
        r = c.get("/ratchet/stats")
        assert r.status_code == 200 and r.json()["security"]["public_dashboard"] is True, env
        assert r.json()["security"]["scrubbed"] is False
        assert c.get("/ratchet/bom").status_code == c.get("/ratchet/tasks").status_code == 200
        assert c.post("/ratchet/redact", json={"text": "x"}).status_code == 200
    c = TestClient(gw_env(RATCHET_PUBLIC_DASHBOARD="0", RATCHET_DASHBOARD_KEYS=f"screen:{DASH_KEY}").app)
    assert c.get("/ratchet/stats").status_code == 401 and c.get("/ratchet/stats", headers=DASH).status_code == 200


# ---------- scrubbing ----------
@pytest.mark.req("NFR-5", "FR-12.7", "INV-13")
def test_public_live_stats_are_scrubbed_for_anonymous_readers(gw_env, tmp_path, monkeypatch):
    gw, rt = _live_gateway(gw_env, tmp_path, monkeypatch, public_dashboard=True)
    c = TestClient(gw.app)
    ask = {"messages": [{"role": "user", "content": "hi"}]}
    assert c.post(CHAT, json=ask, headers=GLOBEX).status_code == 200
    assert c.post(CHAT, json=ask, headers=ACME).status_code == 200
    assert c.post(CHAT, json=ask, headers=ACME).status_code == 200
    assert c.put("/ratchet/tasks/partner-only", json={"clients": ["globex"]}, headers=ADMIN).status_code == 200
    rt.ledger.event("student_error", OLD_EVENT)
    rt.ledger.event("adapter_reloaded", "v1 student-v1: loaded from http://10.0.0.5:8000/v1/load_lora_adapter")

    public = c.get("/ratchet/stats")
    body = public.json()
    assert [(x["client"], x["requests"]) for x in body["clients"]] == [("client-1", 2), ("client-2", 1)]  # acme, globex
    assert body["security"]["scrubbed"] is True and body["security"]["public_dashboard"] is True
    details = [e["detail"] for e in body["events"]]
    assert "v1: ConnectError" in details and "v1 student-v1: loaded from <url>" in details
    assert not [d for d in details if "http" in d or "Errno" in d or "student.internal" in d]
    for leak in ("acme", "globex", "10.0.0.5", ACME_KEY, GLOBEX_KEY, ADMIN_KEY, DASH_KEY):
        assert leak not in public.text, leak
    entry = c.get("/ratchet/tasks/partner-only").json()
    listed = {t["name"]: t for t in c.get("/ratchet/tasks").json()["tasks"]}
    assert entry["clients"] == listed["partner-only"]["clients"] == ["client-2"]  # the pseudonym stats uses
    assert entry["updated_by"] == "ops"  # an admin's name is not a client's

    # A reader with a key sees the full payload, and the cached one was never changed by the scrubbed copies.
    for key in (DASH, ADMIN):
        full = c.get("/ratchet/stats", headers=key).json()
        assert [x["client"] for x in full["clients"]] == ["acme", "globex"] and full["security"]["scrubbed"] is False
        assert OLD_EVENT in [e["detail"] for e in full["events"]]
        assert c.get("/ratchet/tasks/partner-only", headers=key).json()["clients"] == ["globex"]
    assert c.get("/ratchet/stats").json() == body  # stable pseudonyms between reads
    # On a public dashboard a key is optional: an API key or a wrong key reads as anonymous, never a 401 or 403.
    for key in (ACME, {"Authorization": "Bearer not-a-key-0123456789"}):
        r = c.get("/ratchet/stats", headers=key)
        assert (r.status_code, r.json()) == (200, body)

    # RATCHET_SCRUB_STATS=1 scrubs every read, keys or not; 0 none.
    monkeypatch.setattr(rt, "access", replace(rt.access, scrub_stats=True))
    assert c.get("/ratchet/stats", headers=ADMIN).json()["clients"][0]["client"] == "client-1"
    monkeypatch.setattr(rt, "access", replace(rt.access, scrub_stats=False))
    assert c.get("/ratchet/stats").json()["clients"][0]["client"] == "acme"


@pytest.mark.req("FR-12.7")
def test_scrub_helpers():
    cases = {
        OLD_EVENT: "v1: ConnectError",
        "v3: HTTPStatusError: Client error '404 Not Found' for url 'https://student.test/v1/chat'": "v3: HTTPStatusError",
        "v2: ReadTimeout: ": "v2: ReadTimeout",
        "v1: UpstreamError 502 upstream_timeout": "v1: UpstreamError 502 upstream_timeout",  # today's events: kept
        "v4 arbitration: UpstreamError 400": "v4 arbitration: UpstreamError 400",
        "ticket-triage: invalid answer: category: 'x' is not in the vocabulary": (
            "ticket-triage: invalid answer: category: 'x' is not in the vocabulary"
        ),
        "see https://a.example/x and http://b.example/y?z=1, then stop": "see <url> and <url> then stop",
        "student endpoint up: 1 GPU at $1.55/GPU-hour (api by ops)": "student endpoint up: 1 GPU at $1.55/GPU-hour (api by ops)",
    }
    for raw, want in cases.items():
        assert access.scrub_detail(raw) == want, raw
    assert access.pseudonyms(["globex", None, "acme", "acme", ""]) == {"acme": "client-1", "globex": "client-2"}

    payload = {
        "clients": [{"client": "globex", "requests": 1}],
        "events": [{"id": 1, "kind": "student_error", "detail": OLD_EVENT}],
        "security": {"api_keys": True, "scrubbed": False},
        "days": [],
    }
    before = copy.deepcopy(payload)
    out = access.scrub_stats(payload, {"globex": "client-1"})
    assert payload == before  # the cached payload is never changed
    assert out["clients"] == [{"client": "client-1", "requests": 1}] and out["events"][0]["detail"] == "v1: ConnectError"
    assert out["security"] == {"api_keys": True, "scrubbed": True} and out["days"] is payload["days"]
    task = {"name": "t", "clients": ["globex"], "updated_by": "ops"}
    assert access.scrub_task(task, {"globex": "client-1"}) == {"name": "t", "clients": ["client-1"], "updated_by": "ops"}
    assert access.scrub_task({"name": "u", "clients": []}, {}) == {"name": "u", "clients": []}


# ---------- startup ----------
@pytest.mark.req("NFR-5", "FR-12.7")
def test_live_mode_without_api_keys_does_not_start(gw_env, tmp_path):
    gw = gw_env()
    with pytest.raises(ValueError, match="RATCHET_API_KEYS is empty: in live mode"):
        gw.Runtime(_live(tmp_path, api_keys=""))
    assert not (tmp_path / "live.db").exists()  # refused before the ledger was opened
    assert (
        gw.Runtime(
            _live(
                tmp_path,
                api_keys="",
                allow_unauthenticated=True,
                api_guard="guarded",
                admin_token="0123456789abcdef0123456789abcdef",
            )
        ).keys.enabled
        is False
    )
    assert gw.Runtime(Settings(mode="mock", api_keys="", mock_warmup_days=0)).settings.mode == "mock"  # mock needs none

    # From the environment, as `uvicorn ratchet.gateway:app` starts it.
    live_env = {"RATCHET_MODE": "live", "RATCHET_DB": str(tmp_path / "env.db"), "RATCHET_API_KEYS": "", "NEBIUS_API_KEY": "tf"}
    with pytest.raises(ValueError, match="RATCHET_ALLOW_UNAUTHENTICATED=1"):
        gw_env(**live_env, RATCHET_STUDENT_BASE="")
    assert (
        gw_env(
            **live_env,
            RATCHET_STUDENT_BASE="",
            RATCHET_ALLOW_UNAUTHENTICATED="1",
            RATCHET_API_GUARD="guarded",
            RATCHET_ADMIN_TOKEN="0123456789abcdef0123456789abcdef",
        ).rt.settings.mode
        == "live"
    )
    keyed = gw_env(**{**live_env, "RATCHET_API_KEYS": f"acme:{ACME_KEY}"}, RATCHET_STUDENT_BASE="")
    assert keyed.rt.keys.names == ["acme"]


@pytest.mark.req("NFR-5", "FR-12.7", "FR-12.30")
def test_unauthenticated_live_mode_needs_the_edge_guard_it_relies_on(gw_env, tmp_path):
    """`RATCHET_ALLOW_UNAUTHENTICATED=1` alone is one forgotten variable from an open, paid /v1/*: it also needs
    `RATCHET_API_GUARD=guarded` and a `RATCHET_ADMIN_TOKEN` of 32+ characters (what Caddy checks), at startup and in the
    config check. The refusal never quotes the token."""
    from ratchet import config

    gw = gw_env()
    token = "0123456789abcdef0123456789abcdef"
    for overrides in (
        {"api_guard": "open", "admin_token": ""},  # the switch alone
        {"api_guard": "open", "admin_token": token},
        {"api_guard": "guarded", "admin_token": ""},  # no token
        {"api_guard": "guarded", "admin_token": token[:31]},  # one character short
        {"api_guard": "Guard", "admin_token": token},
    ):
        s = _live(tmp_path, api_keys="", allow_unauthenticated=True, **overrides)
        with pytest.raises(ValueError, match="RATCHET_ALLOW_UNAUTHENTICATED=1 needs the edge guard") as e:
            gw.Runtime(s)
        assert token not in str(e.value) and token[:31] not in str(e.value)
        assert not (tmp_path / "live.db").exists()  # refused before the ledger was opened
    ok = _live(tmp_path, api_keys="", allow_unauthenticated=True, api_guard=" Guarded ", admin_token=token)
    assert gw.Runtime(ok).keys.enabled is False  # case and spaces as in an .env file
    # API keys need no guard.
    assert gw.Runtime(_live(tmp_path, api_keys=f"acme:{ACME_KEY}", api_guard="open", admin_token="")).keys.enabled
    # The config check says the same, once, under the variable the operator set.
    bad = _live(tmp_path, api_keys="", allow_unauthenticated=True, api_guard="guarded", admin_token="short")
    problems = [i for i in config.check(bad, {}) if i.var == "RATCHET_ALLOW_UNAUTHENTICATED" and i.level == config.ERROR]
    assert (
        len(problems) == 1 and "RATCHET_ADMIN_TOKEN of at least 32" in problems[0].message and "short" not in problems[0].message
    )
    assert not [i for i in config.check(ok, {}) if i.var == "RATCHET_ALLOW_UNAUTHENTICATED"]


@pytest.mark.req("NFR-5", "INV-13")
def test_each_key_has_one_role(gw_env, tmp_path):
    gw = gw_env()
    for overrides, message in (
        ({"dashboard_keys": f"screen:{ACME_KEY}"}, "RATCHET_DASHBOARD_KEYS: the key for 'screen' is also an API key"),
        ({"dashboard_keys": f"screen:{ADMIN_KEY}"}, "RATCHET_DASHBOARD_KEYS: the key for 'screen' is also an admin key"),
        ({"dashboard_keys": "screen:short"}, "RATCHET_DASHBOARD_KEYS: the key in entry 1 is shorter than 16"),
        ({"admin_keys": f"ops:{GLOBEX_KEY}"}, "RATCHET_ADMIN_KEYS: the key for 'ops' is also an API key"),
    ):
        with pytest.raises(ValueError, match=message):
            gw.Runtime(_live(tmp_path, **overrides))


@pytest.mark.req("NFR-5", "FR-12.7")
def test_access_switches_resolve_by_mode_and_refuse_typos(monkeypatch):
    for name in ("RATCHET_PUBLIC_DASHBOARD", "RATCHET_PUBLIC_BOM", "RATCHET_SCRUB_STATS", "RATCHET_OPENAPI", "RATCHET_CSP"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("RATCHET_ALLOW_UNAUTHENTICATED", raising=False)
    mock, live = Settings(mode="mock").access(), Settings(mode="live").access()
    assert mock == Access(True, False, None, True, "", False)
    assert live == Access(False, False, None, False, "", False)
    assert Settings(mode="live", public_dashboard=True, openapi=True, public_bom=True).access().public_bom is True

    monkeypatch.setenv("RATCHET_PUBLIC_DASHBOARD", "1")
    monkeypatch.setenv("RATCHET_SCRUB_STATS", "0")
    monkeypatch.setenv("RATCHET_OPENAPI", " ")  # set but empty, as in an .env file: the default
    got = Settings(mode="live").access()
    assert (got.public_dashboard, got.scrub_stats, got.openapi) == (True, False, False)
    for value in ("yes", "true", "2", "on"):
        monkeypatch.setenv("RATCHET_PUBLIC_DASHBOARD", value)
        with pytest.raises(ValueError, match=f"RATCHET_PUBLIC_DASHBOARD must be 0 or 1, not '{value}'"):
            Settings()
    monkeypatch.delenv("RATCHET_PUBLIC_DASHBOARD")
    with pytest.raises(ValueError, match="RATCHET_SCRUB_STATS must be 0 or 1"):
        Settings(scrub_stats="1").access()  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="RATCHET_CSP must be one header value"):
        Settings(csp="default-src 'self'\r\nX-Injected: 1").access()

    # Who gets a scrubbed read: by default, anonymous readers of a public live dashboard only.
    public = Access(True, False, None, False, "", False)
    assert (public.scrubs(live=True, keyed=False), public.scrubs(live=True, keyed=True)) == (True, False)
    assert public.scrubs(live=False, keyed=False) is False
    assert Access(False, False, None, False, "", False).scrubs(live=True, keyed=False) is False  # private: keyed only
    assert Access(False, False, True, False, "", False).scrubs(live=False, keyed=True) is True
    assert Access(True, False, False, False, "", False).scrubs(live=True, keyed=False) is False


# ---------- the OpenAPI switch ----------
@pytest.mark.req("NFR-5", "FR-12.7")
def test_openapi_is_off_in_live_mode(gw_env, tmp_path):
    live_env = {
        "RATCHET_MODE": "live",
        "RATCHET_DB": str(tmp_path / "live.db"),
        "RATCHET_STUDENT_BASE": "",
        "NEBIUS_API_KEY": "tf-key",  # live mode needs a teacher key to start (ADR 0070)
        "RATCHET_API_KEYS": f"acme:{ACME_KEY}",
    }
    c = TestClient(gw_env(**live_env).app)
    for path in ("/openapi.json", "/docs", "/redoc"):
        assert c.get(path).status_code == 404, path
    assert c.get("/healthz").status_code == 200
    on = TestClient(gw_env(**live_env, RATCHET_OPENAPI="1").app)
    assert on.get("/openapi.json").status_code == on.get("/docs").status_code == 200
    mock = TestClient(gw_env().app)
    assert "/ratchet/stats" in mock.get("/openapi.json").json()["paths"]
    assert TestClient(gw_env(RATCHET_OPENAPI="0").app).get("/openapi.json").status_code == 404


# ---------- security headers ----------
@pytest.mark.req("NFR-5", "FR-12.7")
def test_security_headers_on_every_response(gw_env, monkeypatch):
    gw = gw_env(RATCHET_MAX_BODY_BYTES="1000")
    c = TestClient(gw.app, raise_server_exceptions=False)
    msgs = gw.rt.stream.sample(1).messages()
    responses = {
        "page": c.get("/"),
        "health": c.get("/healthz"),
        "stats": c.get("/ratchet/stats"),
        "chat": c.post(CHAT, json={"messages": msgs}),
        "stream": c.post(CHAT, json={"messages": msgs, "stream": True}),
        "400": c.post(CHAT, json={"messages": []}),
        "404": c.get("/no-such-page"),
        "413": c.post(CHAT, content=b"x" * 2000, headers={"Content-Type": "application/json"}),
        "scout": c.get("/scout/examples"),
    }

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(gw.rt.ledger, "stats_by_day", broken)
    responses["500"] = c.get("/ratchet/stats")  # sent outside every middleware
    monkeypatch.setattr(gw.rt.ratchet, "handle", broken)
    responses["500-api"] = c.post(CHAT, json={"messages": msgs})
    want = {"400": 400, "404": 404, "413": 413, "500": 500, "500-api": 500}
    assert {k: r.status_code for k, r in responses.items()} == {k: want.get(k, 200) for k in responses}
    for name, r in responses.items():
        assert {k: r.headers.get(k) for k in SECURITY} == SECURITY, name
        assert "content-security-policy" not in r.headers, name  # opt-in: the pages never break by default

    policy = "default-src 'self'; frame-ancestors 'none'"
    c = TestClient(gw_env(RATCHET_CSP=policy).app)
    for path in ("/", "/healthz", "/ratchet/stats"):
        r = c.get(path)
        assert r.headers["content-security-policy"] == policy and r.headers["x-content-type-options"] == "nosniff", path
