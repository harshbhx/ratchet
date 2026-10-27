"""Recorded Scout runs (FR-10.40, ADR 0140): the operator can save one live run as a file.

The gateway does not serve these files. The file holds the question the operator chose, the run's events with their
offsets, the host name and the totals; never a header, a key or a judge code (INV-12, INV-13). The gateway
never writes one. A visitor's own run is not recorded here; its triage and reader calls quote the
question and, like any distillable call, a teacher answer to one of them is stored as a redacted training example.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

SCHEMA = "ratchet.scout-recording/v1"
ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[a-z0-9-]{1,40}$")
MAX_BYTES = 2 * 1024 * 1024
DEFAULT_DIR = "evidence/scout"


class RecordError(Exception):
    """A run that cannot be recorded; the message never holds a header, a key or a code."""


def slug(question: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", question.lower()).strip("-")[:40].strip("-")
    return s or "run"


def _base(base_url: str) -> str:
    base = base_url.rstrip("/")
    return base[:-3] if base.endswith("/v1") else base


def record(
    base_url: str,
    question: str,
    *,
    client: httpx.Client | None = None,
    judge_code: str | None = None,
    allow_mock: bool = False,
    clock: Callable[[], float] = time.monotonic,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, Any]:
    """Read one run from `GET {base_url}/scout/stream` and return the recording. Raises `RecordError` for a stream that
    ends without `done`, holds an `error` event, or reports mock mode without `allow_mock`. A judge code is posted once to
    `/scout/judge` (the cookie stays in the client) and is not kept."""
    base = _base(base_url)
    own = client is None
    http = client or httpx.Client(timeout=httpx.Timeout(30.0, read=180.0))
    try:
        if judge_code:
            try:
                r = http.post(f"{base}/scout/judge", json={"code": judge_code})
            except httpx.HTTPError as e:
                raise RecordError(f"judge login failed: {type(e).__name__}") from None
            if r.status_code != 200:
                raise RecordError(f"judge login refused (HTTP {r.status_code})")
        events: list[dict[str, Any]] = []
        t0: float | None = None
        try:
            with http.stream("GET", f"{base}/scout/stream", params={"q": question}) as resp:
                if resp.status_code != 200:
                    raise RecordError(f"the stream answered HTTP {resp.status_code}")
                for line in resp.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        ev = json.loads(line[5:].strip())
                    except ValueError:
                        continue
                    if not isinstance(ev, dict):
                        continue
                    t = clock()
                    t0 = t if t0 is None else t0
                    events.append({**ev, "t_ms": max(0, round((t - t0) * 1000))})
                    if ev.get("type") in ("done", "error"):
                        break
        except httpx.HTTPError as e:
            raise RecordError(f"the stream failed: {type(e).__name__}") from None
    finally:
        if own:
            http.close()
    for ev in events:
        if ev.get("type") == "error":
            raise RecordError(f"the run reported an error: {str(ev.get('message', ''))[:200]}")
    if not events or events[-1].get("type") != "done":
        raise RecordError("the stream ended without a done event")
    if events[0].get("type") != "start":
        raise RecordError("the stream did not begin with a start event")
    mode = events[0].get("mode")
    if mode not in ("live", "mock"):
        raise RecordError(f"unknown mode {mode!r}")
    if mode == "mock" and not allow_mock:
        raise RecordError("the gateway is in mock mode: this would record a mock-mode run (use --allow-mock to keep it)")
    totals = events[-1].get("totals")
    rec = {
        "schema": SCHEMA,
        "recorded_at": now().astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "host": urlparse(base).hostname or "",
        "mode": mode,
        "question": question,
        "duration_ms": events[-1]["t_ms"],
        "events": events,
        "totals": totals if isinstance(totals, dict) else {},
    }
    if len(json.dumps(rec, ensure_ascii=False).encode()) > MAX_BYTES:
        raise RecordError("the recording is over 2 MB")
    return rec


def recording_id(rec: dict[str, Any]) -> str:
    stamp = re.sub(r"[-:]", "", str(rec["recorded_at"]))
    return f"{stamp}-{slug(str(rec['question']))}"


def write(rec: dict[str, Any], out_dir: str | Path) -> Path:
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{recording_id(rec)}.json"
    path.write_text(json.dumps(rec, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return path
