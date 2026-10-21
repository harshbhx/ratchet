"""Recorded Scout runs (FR-10.40, ADR 0140): the operator records one live run, and the gateway plays it back read-only.

A judge who arrives after the day's runs are used up, or while Token Factory or Tavily fails, can still watch a real run
at its real pace, labelled as a recording. The file holds the question the operator chose, the run's events with their
offsets, the host name and the totals; never a header, a key or a judge code (INV-12, INV-13). The gateway only reads
these files; it never writes one and never stores a visitor's question.
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
MAX_LISTED = 20
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
        raise RecordError("the gateway is in mock mode: this would record a simulation (use --allow-mock to keep it)")
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


# ------------------------------------------------------------------ reading (the gateway)

_cache: dict[str, tuple[int, int, dict[str, Any] | None]] = {}


def _valid(rec: Any) -> bool:
    if not isinstance(rec, dict) or rec.get("schema") != SCHEMA:
        return False
    if rec.get("mode") not in ("live", "mock") or not isinstance(rec.get("question"), str):
        return False
    if not isinstance(rec.get("recorded_at"), str) or not isinstance(rec.get("duration_ms"), int):
        return False
    evs = rec.get("events")
    if not isinstance(evs, list) or not evs or not all(isinstance(e, dict) and isinstance(e.get("t_ms"), int) for e in evs):
        return False
    return evs[-1].get("type") == "done" and isinstance(rec.get("totals"), dict)


def _load(path: Path) -> dict[str, Any] | None:
    """The parsed recording at `path`, or None for a file that is too large, not JSON or not this schema. Cached by
    modification time and size, so a new or changed file appears without a restart."""
    try:
        st = path.stat()
    except OSError:
        return None
    key = str(path)
    hit = _cache.get(key)
    if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
        return hit[2]
    rec: dict[str, Any] | None = None
    if st.st_size <= MAX_BYTES:
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
            rec = parsed if _valid(parsed) else None
        except (OSError, ValueError, RecursionError):
            rec = None
    if len(_cache) > 256:
        _cache.clear()
    _cache[key] = (st.st_mtime_ns, st.st_size, rec)
    return rec


def get(directory: str, rid: str) -> dict[str, Any] | None:
    """One recording by id, or None. The id must match `ID_RE` and the resolved path must stay inside the directory."""
    if not directory or not isinstance(rid, str) or not ID_RE.fullmatch(rid):
        return None
    base = Path(directory).resolve()
    path = (base / f"{rid}.json").resolve()
    if path.parent != base or not path.is_file():
        return None
    return _load(path)


def listing(directory: str) -> list[dict[str, Any]]:
    """Up to 20 recordings, newest first: id, recorded_at, question, duration_ms, mode, totals."""
    if not directory:
        return []
    try:
        names = sorted((p for p in Path(directory).iterdir() if p.suffix == ".json" and ID_RE.fullmatch(p.stem)), reverse=True)
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for p in names:
        rec = _load(p.resolve())
        if rec is None:
            continue
        out.append({"id": p.stem, **{k: rec[k] for k in ("recorded_at", "question", "duration_ms", "mode", "totals")}})
        if len(out) >= MAX_LISTED:
            break
    return out
