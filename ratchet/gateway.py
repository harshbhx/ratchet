"""OpenAI-compatible gateway, dashboard API, landing page and the Scout multi-agent demo.

    RATCHET_MODE=mock uvicorn ratchet.gateway:app --port 8000     # offline demo, no keys
    RATCHET_MODE=live NEBIUS_API_KEY=... uvicorn ratchet.gateway:app  # real Token Factory teacher

Point any OpenAI SDK at http://localhost:8000/v1. Choose the task with the
`X-Ratchet-Task` header (or `metadata.ratchet_task` in the body). With `RATCHET_API_KEYS`
set, `/v1/*` needs `Authorization: Bearer <key>`.
Pages: `/` landing, `/scout` multi-agent demo, `/dashboard` Ratchet dashboard.
"""

from __future__ import annotations

import calendar
import json
import queue
import re
import threading
import time
import uuid
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
from fastapi import Body, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from . import bom as bomdoc
from .auth import KeyRing, bearer
from .config import Settings
from .ledger import Ledger, Version
from .nightschool.orchestrate import NightReport, run_night
from .providers import OpenAICompatProvider
from .redact import redact
from .release import ReleaseGate, ReleasePolicy
from .router import Ratchet
from .scout.agents import Call, Scout
from .scout.guard import Guard
from .scout.prompts import EXAMPLE_QUESTIONS
from .scout.search import MockSearch, SearchTool, TavilySearch

HERE = Path(__file__).parent
DASHBOARD = HERE / "dashboard.html"
LANDING = HERE / "web" / "landing.html"
SCOUT_PAGE = HERE / "web" / "scout.html"
# The React app (web/, built into ratchet/web/dist). When it is missing (e.g. a source checkout
# without `npm run build`), the hand-written pages above are served instead.
DIST = HERE / "web" / "dist"
SPA_INDEX = DIST / "index.html"
WORKLOADS = ("tickets", "research", "mixed")
MAX_QUESTION_CHARS = 300
MAX_REDACT_CHARS = 5000


TRANSPORT_KEYS = ("teacher", "student")


class Runtime:
    """Wires settings, ledger, providers and the release gate together.

    `transports` replaces the network under the live-mode providers ({"teacher": ..., "student": ...}, each an
    `httpx.BaseTransport` such as `httpx.MockTransport`), so live mode can run offline in tests. Mock mode has no HTTP
    providers and ignores it."""

    def __init__(self, settings: Settings | None = None, transports: dict[str, httpx.BaseTransport] | None = None):
        unknown = set(transports or {}) - set(TRANSPORT_KEYS)
        if unknown:
            raise ValueError(f"unknown transport keys {sorted(unknown)}; use {list(TRANSPORT_KEYS)}")
        self.settings = settings or Settings()
        self.transports = dict(transports or {})
        self.reset()

    def reset(self) -> None:
        s = self.settings
        if s.mode == "mock":
            from sim.mock_models import MockFleet, MockTeacher
            from sim.world import make_stream

            self.ledger = Ledger(":memory:", s.golden_fraction)
            self.fleet = MockFleet()
            self.stream = make_stream("tickets", seed=7, drift_day=5)
            self.streams = {"tickets": self.stream, **{w: make_stream(w, seed=7, drift_day=5) for w in ("research", "mixed")}}
            teacher = MockTeacher(s.teacher_reasoning_tokens)
            resolver = self.fleet.resolve
            s.audit_rate = max(s.audit_rate, 0.10)
            policy = ReleasePolicy(min_audits=15)
            self.search: SearchTool | None = MockSearch()
            self.guard = Guard(None, 10 * s.scout_runs_per_hour)
        else:
            self.ledger = Ledger(s.db_path, s.golden_fraction, auto_day=True)
            teacher = OpenAICompatProvider(s.teacher_base, s.teacher_api_key, transport=self.transports.get("teacher"))
            if s.student_base:
                student = OpenAICompatProvider(s.student_base, s.student_api_key, transport=self.transports.get("student"))

                def resolver(v: Version):
                    return student, v.adapter_uri  # vLLM serves each LoRA adapter under its own name
            else:

                def resolver(v: Version):  # endpoint offline: hosted, untuned fallback
                    return teacher, s.fallback_student_model

            policy = ReleasePolicy.from_env()
            self.search = TavilySearch(s.tavily_api_key) if s.tavily_api_key else None
            self.guard = Guard(s.scout_daily_budget_usd, s.scout_runs_per_hour, s.scout_max_runs_per_day)
        self.keys = KeyRing(s.api_keys)
        self.signer = bomdoc.Signer(s.bom_signing_key) if s.bom_signing_key else None
        self.ratchet = Ratchet(s, self.ledger, teacher, resolver, seed=7)
        self.gate = ReleaseGate(self.ledger, policy)
        self.gate_lock = threading.Lock()
        if s.mode == "mock" and s.mock_warmup_days > 0:
            self.warm_up(s.mock_warmup_days, s.mock_warmup_requests)

    # ----- mock-mode simulation helpers -----
    def simulate_day(self, requests: int, workload: str = "tickets") -> NightReport:
        """One simulated day of traffic, then one night of Night School (mock mode only)."""
        from sim.world import night_task

        stream = self.streams[workload]
        self.ledger.day += 1
        ticks = max(1, requests // 10)
        for i in range(requests):
            item = stream.sample(self.ledger.day)
            self.ratchet.handle(item.messages(), item.task)
            if (i + 1) % ticks == 0:
                self.tick()
        return run_night(
            self.ledger,
            self.fleet,
            self.fleet,
            self.gate,
            self.settings.student_base_model,
            task=night_task(workload),
            signer=self.signer,
        )

    def warm_up(self, days: int, requests: int) -> None:
        for _ in range(days):
            self.simulate_day(requests, "mixed")
        self.ledger.event("warmup", f"demo warm-up: {days} simulated days of mixed traffic ({requests} requests/day)")

    def tick(self) -> None:
        with self.gate_lock:
            self.gate.tick()

    def scout(self, pace: float = 0.0) -> Scout:
        """`pace` > 0 (mock streams only) sleeps for that share of each call's simulated latency."""
        assert self.search is not None

        def call(messages: list[dict[str, Any]], task: str) -> Call:
            c = Call.from_result(self.ratchet.handle(messages, task))
            if pace > 0:
                time.sleep(pace * c.latency_ms / 1000)
            return c

        return Scout(call, self.search, max_results=self.settings.scout_max_results)


rt = Runtime()
app = FastAPI(title="Ratchet", version=__version__)
if (DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")


def _page(path: Path, title: str) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8")
    return (  # placeholder until the page exists
        f"<!doctype html><meta charset=utf-8><title>{title}</title>"
        f"<h1>{title}</h1><p><a href='/'>Home</a> · <a href='/scout'>Scout</a> · <a href='/dashboard'>Dashboard</a></p>"
    )


def _spa_or(path: Path, title: str) -> HTMLResponse:
    """The React app when built, else the lite page."""
    if SPA_INDEX.exists():
        return HTMLResponse(SPA_INDEX.read_text(encoding="utf-8"), headers={"Cache-Control": "no-cache"})
    return HTMLResponse(_page(path, title))


@app.get("/", response_class=HTMLResponse)
def landing() -> HTMLResponse:
    return _spa_or(LANDING, "Ratchet")


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard() -> HTMLResponse:
    return _spa_or(DASHBOARD, "Ratchet dashboard")


@app.get("/scout", response_class=HTMLResponse)
def scout_page() -> HTMLResponse:
    return _spa_or(SCOUT_PAGE, "Scout")


# The original single-file pages stay available (no JavaScript build, no fonts).
@app.get("/lite", response_class=HTMLResponse)
def lite_landing() -> str:
    return _page(LANDING, "Ratchet")


@app.get("/lite/dashboard", response_class=HTMLResponse)
def lite_dashboard() -> str:
    return _page(DASHBOARD, "Ratchet dashboard")


@app.get("/lite/scout", response_class=HTMLResponse)
def lite_scout() -> str:
    return _page(SCOUT_PAGE, "Scout")


@app.get("/favicon.svg", include_in_schema=False)
def favicon() -> FileResponse:
    if not (DIST / "favicon.svg").exists():
        raise HTTPException(404)
    return FileResponse(DIST / "favicon.svg", media_type="image/svg+xml")


# ----- OpenAI-compatible API -----
def _client(authorization: str | None, x_api_key: str | None) -> str | None:
    """The API client's name, or None when keys are off. 401 when keys are on and this one is wrong."""
    if not rt.keys.enabled:
        return None
    name = rt.keys.identify(bearer(authorization, x_api_key))
    if name is None:
        raise HTTPException(401, "invalid or missing API key", headers={"WWW-Authenticate": "Bearer"})
    return name


def _utc_midnight(now: float) -> float:
    return float(calendar.timegm(time.gmtime(now)[:3] + (0, 0, 0)))


def _quota(client: str | None) -> dict[str, str]:
    """Rate-limit headers for this client; 429 when its daily quota is used up."""
    limit = rt.settings.api_key_daily_requests
    if client is None or limit <= 0:
        return {}
    used = rt.ledger.client_requests_since(client, _utc_midnight(time.time()))
    if used >= limit:
        headers = {"X-RateLimit-Limit-Requests": str(limit), "X-RateLimit-Remaining-Requests": "0"}
        raise HTTPException(429, f"daily request quota of {limit} used up for {client!r}; resets at 00:00 UTC", headers=headers)
    return {"X-RateLimit-Limit-Requests": str(limit), "X-RateLimit-Remaining-Requests": str(limit - used - 1)}


def _chunks(text: str, words: int = 4) -> list[str]:
    """Split an answer into a few words per streamed chunk, keeping every character."""
    parts = re.findall(r"\s*\S+\s*|\s+", text)
    return ["".join(parts[i : i + words]) for i in range(0, len(parts), words)] or [""]


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    """Liveness for Docker and load balancers. Public, even when API keys protect /v1/*."""
    return {"status": "ok", "version": __version__, "mode": rt.settings.mode}


@app.get("/v1/models")
def models(authorization: str | None = Header(default=None), x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
    _client(authorization, x_api_key)
    return {"object": "list", "data": [{"id": "ratchet", "object": "model", "owned_by": "you"}]}


@app.post("/v1/chat/completions", response_model=None)
def chat(
    body: dict[str, Any] = Body(...),
    x_ratchet_task: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> JSONResponse | StreamingResponse:
    client = _client(authorization, x_api_key)
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise HTTPException(400, "messages is required")
    headers = _quota(client)
    task = x_ratchet_task or (body.get("metadata") or {}).get("ratchet_task")
    try:
        r = rt.ratchet.handle(messages, task, client=client)
    except KeyError as e:  # mock mode only knows the synthetic tickets
        raise HTTPException(400, f"mock mode only answers demo tickets: {e}") from e
    except httpx.HTTPError as e:  # the teacher itself failed: nothing left to fall back to
        raise HTTPException(502, f"teacher unavailable: {type(e).__name__}") from e
    rt.tick()
    meta = {
        "served_by": r.served_by,
        "student_version": r.version,
        "confidence": r.confidence,
        "escalated": r.escalated,
        "cost_usd": r.cost_usd,
        "all_teacher_cost_usd": r.baseline_usd,
        "latency_ms": r.latency_ms,
    }
    cid, created = f"chatcmpl-{uuid.uuid4().hex[:24]}", int(time.time())
    headers["X-Ratchet-Served-By"] = r.served_by
    if not body.get("stream"):
        payload = {
            "id": cid,
            "object": "chat.completion",
            "created": created,
            "model": r.model,
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": r.text}}],
            "ratchet": meta,
        }
        return JSONResponse(payload, headers=headers)

    # Streaming. The cascade must see the student's whole answer to score its confidence, so the
    # answer is complete before the first chunk; the stream format is what clients need from it.
    def chunk(delta: dict[str, Any], finish: str | None = None, **extra: Any) -> str:
        ev = {
            "id": cid,
            "object": "chat.completion.chunk",
            "created": created,
            "model": r.model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            **extra,
        }
        return f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    def events() -> Iterator[str]:
        yield chunk({"role": "assistant", "content": ""})
        for piece in _chunks(r.text):
            yield chunk({"content": piece})
        yield chunk({}, "stop", ratchet=meta)
        yield "data: [DONE]\n\n"

    headers.update({"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    return StreamingResponse(events(), media_type="text/event-stream", headers=headers)


@app.get("/ratchet/stats")
def stats() -> dict[str, Any]:
    led = rt.ledger
    boms = led.bom_status()
    return {
        "mode": rt.settings.mode,
        "day": led.day,
        "teacher": rt.settings.teacher_model,
        "student_base": rt.settings.student_base_model,
        "days": led.stats_by_day(),
        "tasks": led.stats_by_task(),
        "versions": [{**v.__dict__, "bom": boms.get(v.id)} for v in led.versions()],
        "events": led.events()[-40:],
        "totals": led.totals(),
        "bill_of_materials": led.bill_of_materials(),
        "clients": led.stats_by_client(),
        "privacy": {"redaction": rt.settings.redact_pii, **led.privacy_stats()},
        "security": {
            "api_keys": rt.keys.enabled,
            "daily_requests_per_key": rt.settings.api_key_daily_requests or None,
            "bom_signing": rt.signer is not None,
            "bom_key_id": rt.signer.key_id if rt.signer else None,
        },
    }


@app.get("/ratchet/bom")
def bom() -> dict[str, Any]:
    """Model bill of materials: proof of which teachers and licenses produced the training data."""
    return rt.ledger.bill_of_materials()


@app.post("/ratchet/redact")
def redact_preview(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Show what the redactor would store for a piece of text. Nothing is logged or stored."""
    text = body.get("text")
    if not isinstance(text, str):
        raise HTTPException(400, "text is required")
    if len(text) > MAX_REDACT_CHARS:
        raise HTTPException(400, f"text is limited to {MAX_REDACT_CHARS} characters")
    found: Counter[str] = Counter()
    return {"text": redact(text, found), "found": dict(found), "enabled": rt.settings.redact_pii}


@app.get("/ratchet/bom/public-key")
def bom_public_key() -> dict[str, Any]:
    """The Ed25519 public key that verifies per-adapter BOMs (null when signing is off)."""
    if rt.signer is None:
        return {"alg": bomdoc.ALG, "public_key": None, "key_id": None}
    return {"alg": bomdoc.ALG, "public_key": rt.signer.public_key, "key_id": rt.signer.key_id}


@app.get("/ratchet/versions/{vid}/bom")
def version_bom(vid: int) -> dict[str, Any]:
    """One adapter's bill of materials, written when it was trained. Check it with `python -m ratchet.bom verify`."""
    doc = rt.ledger.version_bom(vid)
    if doc is None:
        raise HTTPException(404, f"no bill of materials for version {vid}")
    return doc


# ----- demo controls (mock mode only) -----
# The public demo is shared by every visitor, so controls run one at a time and reset has a cooldown.
_demo_lock = threading.Lock()
_last_reset = [0.0]
RESET_COOLDOWN_S = 30.0


def _mock_only() -> None:
    if rt.settings.mode != "mock":
        raise HTTPException(403, "demo controls are only available in mock mode")


def _demo_busy() -> HTTPException:
    return HTTPException(409, "another demo action is running; try again in a moment")


@app.post("/ratchet/demo/day")
def demo_day(requests: int = 1500, workload: str = "tickets") -> dict[str, Any]:
    """Simulate one day of traffic (support tickets, Scout research calls, or both), then one night of training."""
    _mock_only()
    if workload not in WORKLOADS:
        raise HTTPException(400, f"workload must be one of {list(WORKLOADS)}")
    if not _demo_lock.acquire(blocking=False):
        raise _demo_busy()
    try:
        rep = rt.simulate_day(max(100, min(requests, 5000)), workload)
    finally:
        _demo_lock.release()
    return {"day": rt.ledger.day, "workload": workload, "night": rep.__dict__}


@app.post("/ratchet/demo/break-next")
def demo_break_next() -> dict[str, Any]:
    """Fault injection: the next trained student is deployed corrupted, to show automatic rollback."""
    _mock_only()
    nxt = f"student-v{len(rt.ledger.versions()) + 1}"
    rt.fleet.corrupt_on_deploy.add(nxt)
    rt.ledger.event("fault", f"{nxt} will be deployed corrupted (demo fault injection)")
    return {"corrupt": nxt}


@app.post("/ratchet/demo/reset")
def demo_reset() -> dict[str, str]:
    _mock_only()
    if time.time() - _last_reset[0] < RESET_COOLDOWN_S:
        raise HTTPException(429, f"the demo was reset less than {int(RESET_COOLDOWN_S)} seconds ago")
    if not _demo_lock.acquire(blocking=False):
        raise _demo_busy()
    try:
        rt.reset()
        _last_reset[0] = time.time()
    finally:
        _demo_lock.release()
    return {"status": "reset"}


# ----- Scout: the multi-agent research demo (docs/SCOUT.md) -----
def _client_ip(request: Request) -> str:
    if rt.settings.trust_proxy:
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _scout_precheck(question: str, ip: str) -> tuple[int, str] | None:
    """(HTTP status, message) when a run cannot start, else None. Records the run in the guard."""
    q = (question or "").strip()
    if not q:
        return 400, "Ask a question."
    if len(q) > MAX_QUESTION_CHARS:
        return 400, f"Questions are limited to {MAX_QUESTION_CHARS} characters."
    if rt.settings.mode == "mock" and q not in EXAMPLE_QUESTIONS:
        return 400, "This demo runs in mock mode with simulated search results, so it answers the example questions only."
    if rt.search is None:
        return 503, "Live search is not configured (TAVILY_API_KEY is not set)."
    refused = rt.guard.admit(ip)
    if refused:
        return 429, refused
    return None


def _run_scout(question: str, emit, pace: float = 0.0) -> dict[str, Any] | None:
    """Run Scout, turning failures into an `error` event. Returns the `done` event or None.
    Spend is counted per step, so a run that fails halfway still counts against the budget."""
    spent = [0.0]

    def counting_emit(ev: dict[str, Any]) -> None:
        if ev.get("type") == "step":
            spent[0] += float(ev.get("cost_usd") or 0.0)
        emit(ev)

    try:
        done = rt.scout(pace).run(question.strip(), counting_emit, mode=rt.settings.mode)
    except httpx.HTTPError as e:
        emit({"type": "error", "message": f"a model or search call failed: {type(e).__name__}"})
        return None
    except Exception as e:  # noqa: BLE001 - report, never crash the server
        emit({"type": "error", "message": f"run failed: {type(e).__name__}: {e}"[:300]})
        return None
    finally:
        rt.guard.add_spend(spent[0])
        rt.tick()
    return done


@app.get("/scout/examples")
def scout_examples() -> dict[str, Any]:
    return {"mode": rt.settings.mode, "examples": EXAMPLE_QUESTIONS, "free_text": rt.settings.mode == "live"}


@app.get("/scout/status")
def scout_status() -> dict[str, Any]:
    return {"mode": rt.settings.mode, **rt.guard.status(), "tavily": isinstance(rt.search, TavilySearch)}


@app.get("/scout/stream")
def scout_stream(request: Request, q: str = "") -> StreamingResponse:
    """Server-Sent Events. Always HTTP 200: problems arrive as an `error` event (EventSource cannot read status codes)."""
    problem = _scout_precheck(q, _client_ip(request))

    def sse(ev: dict[str, Any]) -> str:
        return f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    def events() -> Iterator[str]:
        if problem is not None:
            yield sse({"type": "error", "message": problem[1]})
            return
        box: queue.Queue[dict[str, Any] | None] = queue.Queue()
        pace = rt.settings.mock_pace if rt.settings.mode == "mock" else 0.0
        worker = threading.Thread(target=lambda: (_run_scout(q, box.put, pace), box.put(None)), daemon=True)
        worker.start()
        while True:
            try:
                ev = box.get(timeout=15)
            except queue.Empty:
                yield ": keep-alive\n\n"
                continue
            if ev is None:
                return
            yield sse(ev)

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(events(), media_type="text/event-stream", headers=headers)


@app.post("/scout/run")
def scout_run(request: Request, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    question = str(body.get("question", ""))
    problem = _scout_precheck(question, _client_ip(request))
    if problem is not None:
        raise HTTPException(*problem)
    events: list[dict[str, Any]] = []
    done = _run_scout(question, events.append)
    if done is None:
        raise HTTPException(502, events[-1]["message"] if events else "run failed")
    return {"events": events}
