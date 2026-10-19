"""OpenAI-compatible gateway + dashboard API.

    RATCHET_MODE=mock uvicorn ratchet.gateway:app --port 8000     # offline demo, no keys
    RATCHET_MODE=live NEBIUS_API_KEY=... uvicorn ratchet.gateway:app  # real Token Factory teacher

Point any OpenAI SDK at http://localhost:8000/v1. Choose the task with the
`X-Ratchet-Task` header (or `metadata.ratchet_task` in the body).
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import Body, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from .config import Settings
from .ledger import Ledger, Version
from .providers import OpenAICompatProvider
from .release import ReleaseGate, ReleasePolicy
from .router import Ratchet

DASHBOARD = Path(__file__).with_name("dashboard.html")


class Runtime:
    """Wires settings, ledger, providers and the release gate together."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()
        self.reset()

    def reset(self) -> None:
        s = self.settings
        if s.mode == "mock":
            from sim.mock_models import MockFleet, MockTeacher
            from sim.tickets import TicketStream

            self.ledger = Ledger(":memory:", s.golden_fraction)
            self.fleet = MockFleet()
            self.stream = TicketStream(seed=7, drift_day=5)
            teacher = MockTeacher(s.teacher_reasoning_tokens)
            resolver = self.fleet.resolve
            s.audit_rate = max(s.audit_rate, 0.10)
            policy = ReleasePolicy(min_audits=15)
        else:
            self.ledger = Ledger(s.db_path, s.golden_fraction, auto_day=True)
            teacher = OpenAICompatProvider(s.teacher_base, s.teacher_api_key)
            if s.student_base:
                student = OpenAICompatProvider(s.student_base, s.student_api_key)

                def resolver(v: Version):
                    return student, v.adapter_uri  # vLLM serves each LoRA adapter under its own name
            else:

                def resolver(v: Version):  # endpoint offline: hosted, untuned fallback
                    return teacher, s.fallback_student_model

            policy = ReleasePolicy.from_env()
        self.ratchet = Ratchet(s, self.ledger, teacher, resolver, seed=7)
        self.gate = ReleaseGate(self.ledger, policy)


rt = Runtime()
app = FastAPI(title="Ratchet", version="0.1.1")


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return DASHBOARD.read_text(encoding="utf-8")


@app.get("/v1/models")
def models() -> dict[str, Any]:
    return {"object": "list", "data": [{"id": "ratchet", "object": "model", "owned_by": "you"}]}


@app.post("/v1/chat/completions")
def chat(body: dict[str, Any] = Body(...), x_ratchet_task: str | None = Header(default=None)) -> JSONResponse:
    if body.get("stream"):
        raise HTTPException(400, "streaming is not supported yet")
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise HTTPException(400, "messages is required")
    task = x_ratchet_task or (body.get("metadata") or {}).get("ratchet_task")
    try:
        r = rt.ratchet.handle(messages, task)
    except KeyError as e:  # mock mode only knows the synthetic tickets
        raise HTTPException(400, f"mock mode only answers demo tickets: {e}") from e
    except httpx.HTTPError as e:  # the teacher itself failed: nothing left to fall back to
        raise HTTPException(502, f"teacher unavailable: {type(e).__name__}") from e
    rt.gate.tick()
    payload = {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": r.model,
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": r.text}}],
        "ratchet": {
            "served_by": r.served_by,
            "student_version": r.version,
            "confidence": r.confidence,
            "escalated": r.escalated,
            "cost_usd": r.cost_usd,
            "all_teacher_cost_usd": r.baseline_usd,
            "latency_ms": r.latency_ms,
        },
    }
    return JSONResponse(payload, headers={"X-Ratchet-Served-By": r.served_by})


@app.get("/ratchet/stats")
def stats() -> dict[str, Any]:
    led = rt.ledger
    return {
        "mode": rt.settings.mode,
        "day": led.day,
        "teacher": rt.settings.teacher_model,
        "student_base": rt.settings.student_base_model,
        "days": led.stats_by_day(),
        "versions": [v.__dict__ for v in led.versions()],
        "events": led.events()[-40:],
        "totals": led.totals(),
        "bill_of_materials": led.bill_of_materials(),
    }


@app.get("/ratchet/bom")
def bom() -> dict[str, Any]:
    """Model bill of materials: proof of which teachers and licenses produced the training data."""
    return rt.ledger.bill_of_materials()


# ----- demo controls (mock mode only) -----
def _mock_only() -> None:
    if rt.settings.mode != "mock":
        raise HTTPException(403, "demo controls are only available in mock mode")


@app.post("/ratchet/demo/day")
def demo_day(requests: int = 1500) -> dict[str, Any]:
    """Simulate one day of support-ticket traffic, then one night of training."""
    _mock_only()
    from .nightschool.orchestrate import run_night

    requests = max(100, min(requests, 5000))
    rt.ledger.day += 1
    ticks = max(1, requests // 10)
    for i in range(requests):
        t = rt.stream.sample(rt.ledger.day)
        rt.ratchet.handle(t.messages(), "ticket-triage")
        if (i + 1) % ticks == 0:
            rt.gate.tick()
    rep = run_night(rt.ledger, rt.fleet, rt.fleet, rt.gate, rt.settings.student_base_model, task="ticket-triage")
    return {"day": rt.ledger.day, "night": rep.__dict__}


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
    rt.reset()
    return {"status": "reset"}
