"""Scout's agents: planner → searcher → triage (parallel) → reader (parallel) → writer.

Every LLM call goes through one function, `call(messages, task)`. In the gateway that is
`Ratchet.handle` in-process; from outside it is any OpenAI client pointed at Ratchet
(`http_caller`). The task name tells Ratchet what kind of call it is: triage and reader calls
are distillable, planner and writer calls are pinned to the teacher.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import httpx

from .. import tasks
from ..resilience import describe_failure
from .prompts import plan_messages, read_messages, triage_messages, write_messages
from .search import Hit, SearchTool, SearchUnavailable

Emit = Callable[[dict[str, Any]], None]


@dataclass
class Call:
    """What an agent learns back from one call through Ratchet."""

    text: str
    served_by: str
    model: str
    confidence: float | None
    escalated: bool
    cost_usd: float
    baseline_usd: float
    latency_ms: float

    @classmethod
    def from_result(cls, r: Any) -> Call:
        return cls(r.text, r.served_by, r.model, r.confidence, r.escalated, r.cost_usd, r.baseline_usd, r.latency_ms)


CallFn = Callable[[list[dict[str, Any]], str], Call]


def http_caller(base_url: str, api_key: str = "unused", timeout: float = 120.0, client: httpx.Client | None = None) -> CallFn:
    """Call a Ratchet gateway over HTTP, exactly as any OpenAI client would.

    `client` (tests: one on an `httpx.MockTransport` or an in-process transport) replaces the default client, whose
    `timeout` is then the given client's own. Scout's agents call from several threads; an `httpx.Client` allows that."""
    client = client if client is not None else httpx.Client(timeout=timeout)
    url = base_url.rstrip("/") + "/chat/completions"

    def call(messages: list[dict[str, Any]], task: str) -> Call:
        r = client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "X-Ratchet-Task": task},
            json={"model": "ratchet", "messages": messages},
        )
        r.raise_for_status()
        body = r.json()
        meta = body.get("ratchet", {})
        return Call(
            body["choices"][0]["message"]["content"],
            meta.get("served_by", "teacher"),
            body.get("model", ""),
            meta.get("confidence"),
            bool(meta.get("escalated")),
            float(meta.get("cost_usd") or 0.0),
            float(meta.get("all_teacher_cost_usd") or 0.0),
            float(meta.get("latency_ms") or 0.0),
        )

    return call


def _step(agent: str, task: str | None, c: Call | None, detail: dict[str, Any], **tool: Any) -> dict[str, Any]:
    if c is None:  # a tool call (search)
        return {
            "type": "step",
            "agent": agent,
            "task": None,
            "served_by": "tool",
            "model": tool.get("model", ""),
            "confidence": None,
            "escalated": False,
            "cost_usd": 0.0,
            "baseline_usd": 0.0,
            "latency_ms": round(tool.get("latency_ms", 0.0), 1),
            "detail": detail,
        }
    return {
        "type": "step",
        "agent": agent,
        "task": task,
        "served_by": c.served_by,
        "model": c.model,
        "confidence": None if c.confidence is None else round(c.confidence, 4),
        "escalated": c.escalated,
        "cost_usd": c.cost_usd,
        "baseline_usd": c.baseline_usd,
        "latency_ms": round(c.latency_ms, 1),
        "detail": detail,
    }


class Scout:
    def __init__(
        self,
        call: CallFn,
        search: SearchTool,
        max_queries: int = 3,
        max_results: int = 4,
        max_triage: int = 12,
        max_reads: int = 6,
        workers: int = 6,
    ):
        self.call = call
        self.search = search
        self.max_queries = max_queries
        self.max_results = max_results
        self.max_triage = max_triage
        self.max_reads = max_reads
        self.workers = workers

    def run(self, question: str, emit: Emit, mode: str = "mock", run_id: str | None = None) -> dict[str, Any]:
        """Run the team on one question. Emits events (docs/SCOUT.md) and returns the `done` event. A search that fails
        or is refused ends the searching and labels the run (`done.search`); every other exception propagates, and the
        caller turns it into an `error` event."""
        run_id = run_id or uuid.uuid4().hex[:8]
        lock = threading.Lock()
        steps: list[dict[str, Any]] = []
        t0 = time.time()

        def send(ev: dict[str, Any]) -> None:
            with lock:
                if ev["type"] == "step":
                    steps.append(ev)
                emit(ev)

        send({"type": "start", "run_id": run_id, "question": question, "mode": mode})

        # 1. Planner (pinned to the teacher).
        c = self.call(plan_messages(question), "research-plan")
        plan = tasks.parse_json(c.text) or {}
        queries = [q for q in plan.get("queries", []) if isinstance(q, str) and q.strip()][: self.max_queries] or [question]
        send(_step("planner", "research-plan", c, {"queries": queries}))

        # 2. Searcher (a tool, not an LLM). A failed or refused search ends the searching, not the run: the planner has
        # already called the teacher, and the writer answers from what was found, or without sources (ADR 0120). The
        # reason names a fixed refusal, or the error's class and status only: an httpx message carries the URL.
        hits: list[Hit] = []
        seen: set[str] = set()
        unavailable: str | None = None
        for q in queries:
            s0 = time.time()
            try:
                found = self.search.search(q, self.max_results)
            except SearchUnavailable as e:
                unavailable = e.reason
            except httpx.HTTPError as e:
                unavailable = describe_failure(e)
            if unavailable is not None:
                detail = {"query": q, "results": [], "unavailable": unavailable}
                send(_step("searcher", None, None, detail, model=self.search.name, latency_ms=1000 * (time.time() - s0)))
                break
            shown = []
            for h in found:
                if h.url in seen or len(hits) >= self.max_triage:
                    continue
                seen.add(h.url)
                h.id = len(hits) + 1
                hits.append(h)
                shown.append({"id": h.id, "title": h.title, "url": h.url, "snippet": h.snippet})
            send(
                _step(
                    "searcher",
                    None,
                    None,
                    {"query": q, "results": shown},
                    model=self.search.name,
                    latency_ms=1000 * (time.time() - s0),
                )
            )

        # 3. Triage every result in parallel (distillable).
        def triage(h: Hit) -> tuple[Hit, bool]:
            c = self.call(triage_messages(question, h.title, h.url, h.snippet), "source-triage")
            obj = tasks.parse_json(c.text) or {}
            relevant = obj.get("relevant") is True
            send(
                _step(
                    "triage",
                    "source-triage",
                    c,
                    {
                        "id": h.id,
                        "url": h.url,
                        "title": h.title,
                        "relevant": relevant,
                        "source_type": str(obj.get("source_type", "other")),
                        "reason": str(obj.get("reason", "")),
                    },
                )
            )
            return h, relevant

        with ThreadPoolExecutor(self.workers) as pool:
            triaged = list(pool.map(triage, hits))
        relevant = [h for h, ok in sorted(triaged, key=lambda x: x[0].id) if ok][: self.max_reads]

        # 4. Read the relevant ones in parallel (distillable).
        def read(h: Hit) -> tuple[Hit, dict[str, Any]]:
            c = self.call(read_messages(question, h.title, h.url, h.excerpt), "page-read")
            obj = tasks.parse_json(c.text) or {}
            detail = {
                "id": h.id,
                "url": h.url,
                "answers_question": obj.get("answers_question") is True,
                "fact_type": str(obj.get("fact_type", "none")),
                "key_fact": str(obj.get("key_fact", "") or ""),
            }
            send(_step("reader", "page-read", c, detail))
            return h, detail

        with ThreadPoolExecutor(self.workers) as pool:
            reads = sorted(pool.map(read, relevant), key=lambda x: x[0].id)

        facts = []
        for h, d in reads:
            if d["answers_question"] and d["key_fact"].strip():
                facts.append({"n": len(facts) + 1, "fact": d["key_fact"].strip(), "title": h.title, "url": h.url})

        # 5. Writer (pinned to the teacher).
        c = self.call(write_messages(question, facts), "research-write")
        out = tasks.parse_json(c.text) or {}
        answer = str(out.get("answer") or tasks.strip_reasoning(c.text)).strip()
        used = {n for n in out.get("used", []) if isinstance(n, int)} or {f["n"] for f in facts}
        citations = [{"n": f["n"], "title": f["title"], "url": f["url"]} for f in facts if f["n"] in used]
        detail = {"answer": answer, "citations": citations, "search_unavailable": unavailable is not None}
        send(_step("writer", "research-write", c, detail))

        llm = [s for s in steps if s["served_by"] != "tool"]
        cost = sum(s["cost_usd"] for s in llm)
        base = sum(s["baseline_usd"] for s in llm)
        done = {
            "type": "done",
            "run_id": run_id,
            "answer": answer,
            "citations": citations,
            "totals": {
                "calls": len(steps),
                "llm_calls": len(llm),
                "student_calls": sum(s["served_by"] == "student" for s in llm),
                "teacher_calls": sum(s["served_by"] == "teacher" for s in llm),
                "cost_usd": cost,
                "baseline_usd": base,
                "savings_pct": 0.0 if base == 0 else 1 - cost / base,
                "wall_ms": round(1000 * (time.time() - t0), 1),
            },
            # Whether every planned search ran; `partial`: some found results before one failed or was refused.
            "search": {
                "available": unavailable is None,
                "partial": unavailable is not None and bool(hits),
                "reason": unavailable,
            },
        }
        send(done)
        return done
