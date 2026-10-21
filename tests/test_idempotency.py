"""Retry de-duplication (FR-1.70, ADR 0071): Idempotency-Key answers, the SDK's retry count and the ledger's seen window.

A client's retry must neither pay for a second cascade nor count as a second occurrence of its request: three retries of
one escalated request would otherwise reach the frequent-request rule (`dataset.FREQUENT`) and move a golden example
into training (INV-3). Every test runs offline: the mock gateway with a recording teacher, fake clocks for the TTL and
the seen window, and the real OpenAI SDK through `tests/sdk_bridge.py` for what it sends on a retry.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from fakes import FakeClock, RecordingProvider
from fastapi.testclient import TestClient
from sdk_bridge import make_openai_client

from ratchet import idem, metrics, obs
from ratchet.config import ConfigError, Settings
from ratchet.ledger import Ledger
from ratchet.nightschool import dataset
from ratchet.router import RequestMeta

CHAT = "/v1/chat/completions"
ACME_KEY = "acme-api-key-0123456789abcdef"
BETA_KEY = "beta-api-key-0123456789abcdef"
KEYS = f"acme:{ACME_KEY},beta:{BETA_KEY}"
ACME = {"Authorization": f"Bearer {ACME_KEY}"}
BETA = {"Authorization": f"Bearer {BETA_KEY}"}
ANSWER = "Your order ships on Monday."


def _msgs(text: str = "When does order 7 ship?") -> list[dict[str, Any]]:
    return [{"role": "user", "content": text}]


def _golden(led: Ledger, task: str = "default") -> list[dict[str, Any]]:
    """Messages whose example lands in the golden split (fixed by its hash, `Ledger._split`)."""
    for i in range(10_000):
        msgs = _msgs(f"Where is parcel {i}?")
        if led.split_of(task, msgs) == "golden":
            return msgs
    raise AssertionError("no golden prompt in 10,000 tries")


def _gateway(gw_env: Any, monkeypatch: pytest.MonkeyPatch, clock: FakeClock | None = None, **env: str) -> Any:
    """The mock gateway (with `env`) answered by a recording teacher; with `clock`, a runtime on that clock."""
    gw = gw_env(**env)
    if clock is not None:
        monkeypatch.setattr(gw, "rt", gw.Runtime(Settings(), clock=clock))
    gw.rt.ratchet.teacher = RecordingProvider(ANSWER)
    return gw


def _rows(led: Ledger, sql: str, *args: Any) -> list[dict[str, Any]]:
    return [dict(r) for r in led.db.execute(sql, args)]


def _until(check: Callable[[], bool], what: str, seconds: float = 10.0) -> None:
    """Poll `check` until it holds (a thread reached a known point), failing after `seconds` of real time."""
    deadline = time.monotonic() + seconds
    while not check():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        time.sleep(0.002)


def _counts(counter: metrics.Counter, *labels: str) -> dict[str, float]:
    return {label: counter.value(label) for label in labels}


def _delta(before: dict[str, float], counter: metrics.Counter) -> dict[str, float]:
    return {k: counter.value(k) - v for k, v in before.items() if counter.value(k) != v}


OUTCOMES = ("first", "uncached", "failed", "replayed", "joined", "reused", "in_flight", "invalid")


class BlockingTeacher(RecordingProvider):
    """A recording teacher whose calls wait for `release`; `entered` is set when the first call arrives."""

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.entered = threading.Event()
        self.release = threading.Event()

    def complete(self, *args: Any, **kwargs: Any) -> Any:
        self.entered.set()
        assert self.release.wait(10), "the test never released the teacher"
        return super().complete(*args, **kwargs)


# ---------- the pieces ----------
@pytest.mark.req("FR-1.70")
def test_keys_retry_counts_and_fingerprints_parse_strictly():
    assert idem.check_key("  order-7-attempt  ") == "order-7-attempt"
    assert idem.check_key("k" * 255) == "k" * 255
    for bad in ("", "   ", "k" * 256, "caf\xe9", "tab\there", "line\nbreak"):
        with pytest.raises(ValueError, match="1 to 255 printable ASCII"):
            idem.check_key(bad)
    # The SDK's attempt number: telemetry, so anything but a plain count reads as a first attempt.
    cases = {None: 0, "0": 0, "2": 2, " 1 ": 1, "-1": 0, "1.5": 0, "two": 0, "": 0, "1000000": 1_000_000, "1000001": 0}
    assert {k: idem.retry_count(k) for k in cases} == cases
    # The same request in another key order matches; another task header or body does not.
    a = idem.fingerprint({"messages": _msgs(), "temperature": 0.7}, None)
    assert a == idem.fingerprint({"temperature": 0.7, "messages": _msgs()}, None)
    assert a != idem.fingerprint({"messages": _msgs(), "temperature": 0.7}, "ticket-triage")
    assert a != idem.fingerprint({"messages": _msgs(), "temperature": 0.8}, None)


def _cached(text: str = "x", size: int = 1) -> idem.Cached:
    return idem.Cached((text.encode() * size,), "application/json", False, task="default")


@pytest.mark.req("FR-1.70")
def test_idem_cache_keeps_expires_evicts_and_releases():
    clock = FakeClock()
    cache = idem.IdemCache(max_entries=2, ttl_s=600, clock=clock, entry_overhead=0, max_bytes=10)
    first = cache.begin("acme", "k1", "fp1", wait_s=1)
    assert first.owner and first.cached is None and not first.joined
    cache.finish(first, _cached("a"))
    replay = cache.begin("acme", "k1", "fp1", wait_s=1)
    assert replay.cached == _cached("a") and not replay.owner
    with pytest.raises(idem.KeyReused):
        cache.begin("acme", "k1", "another body", wait_s=1)
    assert cache.begin("beta", "k1", "another body", wait_s=1).owner  # keys are per client: no collision, no leak
    cache.finish(first, _cached("b"))  # a second finish for the same claim changes nothing
    assert cache.begin("acme", "k1", "fp1", wait_s=1).cached == _cached("a")

    # TTL: kept 600 s after it finished, on the cache's clock; then the key runs again.
    clock.advance(599.9)
    assert cache.begin("acme", "k1", "fp1", wait_s=1).cached is not None
    clock.advance(0.2)
    again = cache.begin("acme", "k1", "fp1", wait_s=1)
    assert again.owner
    cache.finish(again, None)  # failed: the key is released, nothing kept
    assert cache.begin("acme", "k1", "fp1", wait_s=1).owner

    # Entries: the least recently used finished answer goes first; a running entry is never dropped.
    cache = idem.IdemCache(max_entries=2, ttl_s=600, clock=clock, entry_overhead=0, max_bytes=100)
    for k in ("a", "b"):
        cache.finish(cache.begin("s", k, "fp", wait_s=1), _cached(k))
    assert cache.begin("s", "a", "fp", wait_s=1).cached is not None  # 'a' used last now
    running = cache.begin("s", "c", "fp", wait_s=1)
    assert cache.begin("s", "b", "fp", wait_s=1).owner  # 'b' was evicted for 'c'; claiming it evicts 'a'
    assert cache.stats() == {"entries": 2, "in_flight": 2, "waiting": 0, "bytes": 0}
    cache.finish(running, _cached("c"))

    # Bytes: an answer larger than the budget is not kept; smaller ones push the oldest out.
    cache = idem.IdemCache(max_entries=10, ttl_s=600, clock=clock, entry_overhead=0, max_bytes=10)
    cache.finish(cache.begin("s", "big", "fp", wait_s=1), _cached("x", 11))
    assert cache.begin("s", "big", "fp", wait_s=1).owner
    for k in ("p", "q"):
        cache.finish(cache.begin("s", k, "fp", wait_s=1), _cached("x", 6))
    assert cache.begin("s", "p", "fp", wait_s=1).owner and cache.begin("s", "q", "fp", wait_s=1).cached is not None

    # Expired answers are swept without being asked for again.
    cache = idem.IdemCache(max_entries=10, ttl_s=10, clock=clock)
    cache.finish(cache.begin("s", "old", "fp", wait_s=1), _cached())
    clock.advance(11)
    cache.begin("s", "new", "fp", wait_s=1)
    assert len(cache) == 1

    with pytest.raises(ValueError, match="max_entries"):
        idem.IdemCache(max_entries=-1)
    with pytest.raises(ValueError, match="ttl_s"):
        idem.IdemCache(ttl_s=float("nan"))
    assert not idem.IdemCache(max_entries=0).enabled


@pytest.mark.req("FR-1.70")
def test_idem_cache_byte_budget_counts_each_entry_not_only_its_body():
    """Each finished answer counts its body plus ENTRY_OVERHEAD (the entry, its Event, headers and log fields), so the
    byte budget bounds the cache's memory even at RATCHET_IDEMPOTENCY_CACHE's largest value: tiny answers are capped by
    max_bytes / ENTRY_OVERHEAD, not by the entry count alone."""
    assert idem.IdemCache().entry_overhead == idem.ENTRY_OVERHEAD == 2048
    assert idem.MAX_CACHE_BYTES // idem.ENTRY_OVERHEAD == 32_768  # what the default budget allows of empty answers
    clock = FakeClock()
    cache = idem.IdemCache(max_entries=1_000_000, ttl_s=600, clock=clock, max_bytes=3 * idem.ENTRY_OVERHEAD + 10)
    for k in ("a", "b", "c", "d"):
        cache.finish(cache.begin("s", k, "fp", wait_s=1), _cached(k, 2))
    assert cache.stats() == {"entries": 3, "in_flight": 0, "waiting": 0, "bytes": 3 * (idem.ENTRY_OVERHEAD + 2)}
    assert cache.begin("s", "a", "fp", wait_s=1).owner  # the oldest went for the fourth, whatever max_entries says
    assert all(cache.begin("s", k, "fp", wait_s=1).cached is not None for k in ("b", "c", "d"))
    # One whose body fits but whose entry does not is shared with its waiters, never kept.
    cache = idem.IdemCache(max_entries=10, ttl_s=600, clock=clock, max_bytes=idem.ENTRY_OVERHEAD + 5)
    cache.finish(cache.begin("s", "fits", "fp", wait_s=1), _cached("x", 5))
    cache.finish(cache.begin("s", "over", "fp", wait_s=1), _cached("x", 6))
    assert cache.begin("s", "fits", "fp", wait_s=1).cached is not None and cache.begin("s", "over", "fp", wait_s=1).owner


@pytest.mark.req("FR-1.70")
def test_idem_cache_waiters_share_the_answer_or_take_over_after_a_failure():
    cache = idem.IdemCache(max_entries=10, ttl_s=0)  # 0: nothing kept, so only a request that waited shares the answer
    owner = cache.begin("s", "k", "fp", wait_s=1)
    with ThreadPoolExecutor(2) as pool:
        waiter = pool.submit(cache.begin, "s", "k", "fp", 10)
        reused = pool.submit(cache.begin, "s", "k", "other", 10)
        with pytest.raises(idem.KeyReused):  # another body is refused at once, also while the first runs
            reused.result(5)
        _until(lambda: cache.stats()["waiting"] == 1, "the waiter")
        cache.finish(owner, _cached("shared"))
        got = waiter.result(5)
    assert got.joined and got.cached == _cached("shared")
    assert cache.begin("s", "k", "fp", wait_s=1).owner  # ttl 0: nothing was kept for later requests

    # The first fails: one waiter takes the key over and runs; the other waits for it in turn.
    cache = idem.IdemCache(max_entries=10, ttl_s=60)
    owner = cache.begin("s", "k", "fp", wait_s=1)
    with ThreadPoolExecutor(2) as pool:
        waiters = [pool.submit(cache.begin, "s", "k", "fp", 10) for _ in range(2)]
        _until(lambda: cache.stats()["waiting"] == 2, "both waiters")
        cache.finish(owner, None)
        _until(lambda: sum(w.done() for w in waiters) == 1, "the new owner")
        (new_owner,) = [w.result() for w in waiters if w.done()]
        assert new_owner.owner and new_owner.joined
        cache.finish(new_owner, _cached("second try"))
        claims = [w.result(5) for w in waiters]
    assert [c.cached for c in claims if not c.owner] == [_cached("second try")]

    # A wait that runs out is InFlight, and leaves the running entry alone.
    cache = idem.IdemCache(max_entries=10, ttl_s=60)
    owner = cache.begin("s", "k", "fp", wait_s=1)
    with pytest.raises(idem.InFlight):
        cache.begin("s", "k", "fp", wait_s=0.01)
    assert cache.stats()["waiting"] == 0
    cache.finish(owner, _cached())
    assert cache.begin("s", "k", "fp", wait_s=0).cached == _cached()


@pytest.mark.req("FR-1.70")
def test_waiters_per_key_are_bounded():
    """A waiter holds a worker thread and uses no quota, so copies of one request cannot hold the thread pool: past
    `max_waiters` a duplicate is InFlight at once (the gateway's 409), and another key is not affected."""
    assert idem.IdemCache().max_waiters == idem.MAX_WAITERS_PER_KEY == 4
    cache = idem.IdemCache(max_entries=10, ttl_s=60, max_waiters=2)
    owner = cache.begin("s", "k", "fp", wait_s=1)
    with ThreadPoolExecutor(2) as pool:
        waiters = [pool.submit(cache.begin, "s", "k", "fp", 10) for _ in range(2)]
        _until(lambda: cache.stats()["waiting"] == 2, "both waiters")
        started = time.monotonic()
        with pytest.raises(idem.InFlight):
            cache.begin("s", "k", "fp", wait_s=10)
        assert time.monotonic() - started < 5  # refused at once, not after the wait
        other = cache.begin("s", "other", "fp", wait_s=1)  # another key has its own waiters
        assert other.owner
        cache.finish(owner, _cached("shared"))
        assert [w.result(5).cached for w in waiters] == [_cached("shared")] * 2
    assert cache.stats()["waiting"] == 0
    assert cache.begin("s", "k", "fp", wait_s=0).cached == _cached("shared")  # the slots were given back
    cache.finish(other, None)


@pytest.mark.req("FR-1.70", "INV-3")
def test_seen_window_counts_once_per_window_per_sender():
    clock = FakeClock()
    window = idem.SeenWindow(300, max_entries=2, clock=clock)
    window.note("h1", "acme")
    assert not window.count("h1", "acme")  # counted just now
    assert window.count("h1", "beta")  # another sender
    clock.advance(299)
    assert not window.count("h1", "acme")  # a skipped repeat does not restart the window
    clock.advance(1)
    assert window.count("h1", "acme") and not window.count("h1", "acme")
    window.note("h2", "acme")  # a third pair: the least recently counted ('h1', 'beta') is forgotten
    assert len(window) == 2 and window.count("h1", "beta")
    with pytest.raises(ValueError, match="window_s"):
        idem.SeenWindow(-1)


# ---------- the ledger ----------
@pytest.mark.req("FR-1.70", "INV-3")
def test_add_example_skips_retries_and_repeats_within_the_window():
    clock = FakeClock()
    led = Ledger(":memory:")
    args = ("default", _msgs(), ANSWER, "teacher", "lic", "")

    def seen() -> int:
        return int(led.db.execute("SELECT seen FROM examples").fetchone()[0])

    before = _counts(metrics.SEEN_SKIPPED, "retry", "window")
    assert led.add_example(*args, sender="acme")  # no window yet: every repeat counts, as in v0.4
    assert not led.add_example(*args, sender="acme") and seen() == 2
    assert not led.add_example(*args, count_seen=False) and seen() == 2  # a retry never counts
    assert not led.db.in_transaction  # the refused INSERT's transaction is closed even when nothing is written

    led.seen_window = idem.SeenWindow(300, clock=clock)
    assert not led.add_example(*args, sender="acme") and seen() == 3  # first count in the window
    for sender in ("acme", "acme"):
        assert not led.add_example(*args, sender=sender)
    assert seen() == 3
    assert not led.add_example(*args, sender="beta") and seen() == 4  # another client asking the same thing counts
    assert not led.add_example(*args, sender="") and seen() == 5  # a keyless request is its own sender
    for _ in range(3):  # internal traffic (sender None: Scout, the simulator, the warm-up) always counts
        led.add_example(*args)
    assert seen() == 8
    clock.advance(300)
    assert not led.add_example(*args, sender="acme") and seen() == 9  # a repeat minutes later counts
    assert _delta(before, metrics.SEEN_SKIPPED) == {"retry": 1, "window": 2}

    # A new example starts its window: an immediate repeat from the same sender does not count.
    other = ("default", _msgs("Cancel order 8."), ANSWER, "teacher", "lic", "")
    assert led.add_example(*other, sender="acme") and not led.add_example(*other, sender="acme")
    assert [r["seen"] for r in led.examples()] == [9, 1]


@pytest.mark.req("FR-1.70", "INV-3")
def test_concurrent_retries_count_once():
    """The window is checked and the row written under the ledger's lock: eight threads repeating one example for the
    same client count once (the insert), and eight more clients count once each."""
    led = Ledger(":memory:")
    led.seen_window = idem.SeenWindow(300, clock=FakeClock())
    args = ("default", _msgs(), ANSWER, "teacher", "lic", "")
    with ThreadPoolExecutor(8) as pool:
        inserted = list(pool.map(lambda _: led.add_example(*args, sender="acme"), range(8)))
        list(pool.map(lambda i: led.add_example(*args, sender=f"client-{i}"), range(8)))
    assert inserted.count(True) == 1
    assert [r["seen"] for r in led.examples()] == [9]


@pytest.mark.req("FR-1.70")
def test_old_ledger_gains_retry_count(tmp_path):
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.executescript("CREATE TABLE requests (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, day INTEGER, task TEXT);")
    con.execute("INSERT INTO requests (ts, day, task) VALUES (1.0, 1, 'default')")
    con.commit()
    con.close()
    led = Ledger(str(db))
    assert _rows(led, "SELECT retry_count FROM requests") == [{"retry_count": 0}]  # old rows read as first attempts
    led.log_request(task="default", retry_count=2)
    assert [r["retry_count"] for r in _rows(led, "SELECT retry_count FROM requests ORDER BY id")] == [0, 2]


# ---------- the gateway ----------
@pytest.mark.req("FR-1.70")
def test_same_idempotency_key_replays_without_a_cascade(gw_env, monkeypatch):
    """The spec's first case: one ledger row, one teacher call, and the replay is the same answer (same completion id)
    with `Idempotent-Replayed: true`. A replay uses no quota: the key's daily quota of 1 still serves it."""
    gw = _gateway(gw_env, monkeypatch, RATCHET_API_KEYS=KEYS, RATCHET_API_KEY_DAILY_REQUESTS="1")
    teacher, led = gw.rt.ratchet.teacher, gw.rt.ledger
    c = TestClient(gw.app)
    before = _counts(metrics.IDEMPOTENT, *OUTCOMES)
    body = {"messages": _msgs()}
    first = c.post(CHAT, json=body, headers={**ACME, "Idempotency-Key": "order-7"})
    second = c.post(CHAT, json=body, headers={**ACME, "Idempotency-Key": "order-7"})
    assert first.status_code == second.status_code == 200
    assert "idempotent-replayed" not in first.headers and second.headers["idempotent-replayed"] == "true"
    assert second.content == first.content and second.json()["id"] == first.json()["id"]
    assert second.headers["x-ratchet-served-by"] == "teacher" and "x-ratchet-served-by" in first.headers
    assert "x-ratelimit-remaining-requests" in first.headers and "x-ratelimit-remaining-requests" not in second.headers
    assert second.headers["x-request-id"] != first.headers["x-request-id"]  # each HTTP exchange has its own id
    assert second.json()["ratchet"]["request_id"] == first.headers["x-request-id"]  # the body names the one that ran
    assert len(teacher.calls) == 1 and led.totals()["requests"] == 1
    (example,) = led.examples()
    assert example["seen"] == 1
    # The quota of 1 is used: a new key is refused, while the replay above went through.
    refused = c.post(CHAT, json=body, headers={**ACME, "Idempotency-Key": "order-7-b"})
    assert refused.status_code == 429 and refused.json()["error"]["code"] == "daily_quota_exceeded"
    assert c.post(CHAT, json=body, headers={**ACME, "Idempotency-Key": "order-7"}).status_code == 200
    # Another client's key space is its own: the same key with another body is a new request for beta.
    other = c.post(CHAT, json={"messages": _msgs("Cancel order 8.")}, headers={**BETA, "Idempotency-Key": "order-7"})
    assert other.status_code == 200 and "idempotent-replayed" not in other.headers
    assert _delta(before, metrics.IDEMPOTENT) == {"first": 2, "replayed": 2, "failed": 1}  # the 429 released its key


@pytest.mark.req("FR-1.70")
def test_concurrent_duplicates_call_the_teacher_once(gw_env, monkeypatch):
    """The spec's second case: a duplicate that arrives while the first is still with the teacher waits for it and gets
    the same answer; the teacher is called once and the ledger holds one row."""
    gw = _gateway(gw_env, monkeypatch)
    teacher = gw.rt.ratchet.teacher = BlockingTeacher(ANSWER)
    c = TestClient(gw.app)
    before = _counts(metrics.IDEMPOTENT, *OUTCOMES)
    headers = {"Idempotency-Key": "k-concurrent"}
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(c.post, CHAT, json={"messages": _msgs()}, headers=headers)
        assert teacher.entered.wait(10)
        second = pool.submit(c.post, CHAT, json={"messages": _msgs()}, headers=headers)
        _until(lambda: gw.rt.idem.stats()["waiting"] == 1, "the duplicate to wait")
        teacher.release.set()
        a, b = first.result(10), second.result(10)
    assert a.status_code == b.status_code == 200 and a.json()["id"] == b.json()["id"]
    assert b.headers["idempotent-replayed"] == "true" and "idempotent-replayed" not in a.headers
    assert len(teacher.calls) == 1 and gw.rt.ledger.totals()["requests"] == 1
    assert _delta(before, metrics.IDEMPOTENT) == {"first": 1, "joined": 1}


@pytest.mark.req("FR-1.70")
def test_key_reused_with_another_body_or_malformed_is_a_400(gw_env, monkeypatch):
    """The spec's third case. Not a 409, which OpenAI's SDKs retry: no retry can fix a reused key. A key that is not 1 to
    255 printable ASCII characters is a 400 as well. Both are counted as refusals, not cascades."""
    gw = _gateway(gw_env, monkeypatch)
    c = TestClient(gw.app)
    rejections = _counts(metrics.REJECTIONS, "idempotency")
    assert c.post(CHAT, json={"messages": _msgs()}, headers={"Idempotency-Key": "k1"}).status_code == 200
    for body, task in (({"messages": _msgs("Cancel order 8.")}, None), ({"messages": _msgs()}, "ticket-triage")):
        headers = {"Idempotency-Key": "k1", **({"X-Ratchet-Task": task} if task else {})}
        r = c.post(CHAT, json=body, headers=headers)
        assert r.status_code == 400 and r.headers["x-should-retry"] == "false"
        err = r.json()["error"]
        assert (err["type"], err["code"], err["param"]) == ("invalid_request_error", "idempotency_key_reused", None)
    for bad in ("k" * 256, " "):
        r = c.post(CHAT, json={"messages": _msgs()}, headers={"Idempotency-Key": bad})
        assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_idempotency_key"
    assert c.post(CHAT, content=b"{not json", headers={"Idempotency-Key": "k2"}).json()["error"]["code"] == "invalid_json"
    assert len(gw.rt.ratchet.teacher.calls) == 1 and gw.rt.ledger.totals()["requests"] == 1
    assert _delta(rejections, metrics.REJECTIONS) == {"idempotency": 4}
    assert gw.rt.idem.begin("", "k2", "anything", 0).owner  # bad JSON never claimed its key


@pytest.mark.req("FR-1.70", "FR-1.10")
def test_a_body_too_deep_to_fingerprint_is_a_400_and_claims_nothing(gw_env, monkeypatch):
    """Regression: a body nested just under what parse_body accepts parsed on the key path but could not be encoded again
    for its fingerprint (json.dumps adds a level), so adding an Idempotency-Key turned the 400 into a 500 (RecursionError).
    It is now the same 400 `invalid_json` as without a key, before the key is claimed."""
    gw = _gateway(gw_env, monkeypatch)
    c = TestClient(gw.app, raise_server_exceptions=False)
    led = gw.rt.ledger

    def boom(*_: Any, **__: Any) -> str:
        raise RecursionError("maximum recursion depth exceeded while encoding a JSON object")

    monkeypatch.setattr(idem, "fingerprint", boom)
    r = c.post(CHAT, json={"messages": _msgs()}, headers={"Idempotency-Key": "deep"})
    assert r.status_code == 400
    assert (r.json()["error"]["type"], r.json()["error"]["code"]) == ("invalid_request_error", "invalid_json")
    assert "nested too deeply" in r.json()["error"]["message"]
    assert gw.rt.idem.stats()["entries"] == 0 and not gw.rt.ratchet.teacher.calls
    assert led.totals()["requests"] == 0 and _rows(led, "SELECT COUNT(*) AS n FROM failures") == [{"n": 0}]
    monkeypatch.undo()
    _gateway(gw_env, monkeypatch)  # the real fingerprint again, on a fresh runtime
    c = TestClient(gw.app, raise_server_exceptions=False)

    # The real boundary, wherever this Python's recursion limit puts it: the first depth that parse_body refuses without
    # a key (bisection), and the depths around it with a key, are 400s, never 500s, and leave no key claimed.
    def post(depth: int, key: str | None = None) -> Any:
        raw = ('{"messages":' + "[" * depth + "]" * depth + "}").encode()
        headers = {"Content-Type": "application/json", **({"Idempotency-Key": key} if key else {})}
        return c.post(CHAT, content=raw, headers=headers)

    def too_deep(depth: int) -> bool:
        r = post(depth)
        assert r.status_code == 400, (depth, r.status_code)
        return r.json()["error"]["code"] == "invalid_json"

    lo, hi = 1, 100_000
    assert not too_deep(lo) and too_deep(hi)
    while hi - lo > 1:
        mid = (lo + hi) // 2
        lo, hi = (lo, mid) if too_deep(mid) else (mid, hi)
    for depth in range(hi - 3, hi + 4):
        r = post(depth, key=f"deep-{depth}")
        assert r.status_code == 400, (depth, r.status_code, r.text[:200])
        assert r.json()["error"]["type"] == "invalid_request_error"
    assert gw.rt.idem.stats()["entries"] == 0 and not gw.rt.ratchet.teacher.calls
    assert _rows(gw.rt.ledger, "SELECT COUNT(*) AS n FROM failures") == [{"n": 0}]


@pytest.mark.req("FR-1.70")
def test_in_flight_wait_is_bounded_by_the_teacher_deadline(gw_env, monkeypatch):
    """A duplicate waits at most RATCHET_TEACHER_DEADLINE_S for the first, then gets a 409 OpenAI's SDKs retry after
    `Retry-After`, by which time the first has usually finished."""
    gw = _gateway(gw_env, monkeypatch, RATCHET_TEACHER_DEADLINE_S="0.05")
    teacher = gw.rt.ratchet.teacher = BlockingTeacher(ANSWER)
    c = TestClient(gw.app)
    with ThreadPoolExecutor(1) as pool:
        first = pool.submit(c.post, CHAT, json={"messages": _msgs()}, headers={"Idempotency-Key": "slow"})
        assert teacher.entered.wait(10)
        r = c.post(CHAT, json={"messages": _msgs()}, headers={"Idempotency-Key": "slow"})
        assert r.status_code == 409 and r.headers["retry-after"] == "1"
        assert r.json()["error"]["code"] == "idempotency_key_in_use" and "x-should-retry" not in r.headers
        teacher.release.set()
        assert first.result(10).status_code == 200
    replay = c.post(CHAT, json={"messages": _msgs()}, headers={"Idempotency-Key": "slow"})
    assert replay.headers["idempotent-replayed"] == "true" and len(teacher.calls) == 1


@pytest.mark.req("FR-1.70")
def test_idempotency_ttl_expiry_runs_again(gw_env, monkeypatch):
    """The spec's fourth case, on a fake clock: RATCHET_IDEMPOTENCY_TTL_S after the answer, the key runs again."""
    clock = FakeClock()
    gw = _gateway(gw_env, monkeypatch, clock, RATCHET_IDEMPOTENCY_TTL_S="600")
    c = TestClient(gw.app)
    send = lambda: c.post(CHAT, json={"messages": _msgs()}, headers={"Idempotency-Key": "ttl"})  # noqa: E731
    assert "idempotent-replayed" not in send().headers
    clock.advance(599)
    assert send().headers["idempotent-replayed"] == "true"
    clock.advance(2)
    again = send()
    assert again.status_code == 200 and "idempotent-replayed" not in again.headers
    assert len(gw.rt.ratchet.teacher.calls) == 2 and gw.rt.ledger.totals()["requests"] == 2


@pytest.mark.req("FR-1.70", "FR-1.6")
def test_streamed_answer_is_replayed_as_the_same_events(gw_env, monkeypatch):
    gw = _gateway(gw_env, monkeypatch)
    c = TestClient(gw.app)
    body = {"messages": _msgs(), "stream": True, "stream_options": {"include_usage": True}}
    first = c.post(CHAT, json=body, headers={"Idempotency-Key": "stream-1"})
    second = c.post(CHAT, json=body, headers={"Idempotency-Key": "stream-1"})
    assert first.status_code == second.status_code == 200
    assert second.headers["content-type"].startswith("text/event-stream") and second.headers["cache-control"] == "no-cache"
    assert second.headers["idempotent-replayed"] == "true" and second.text == first.text
    events = [line[len("data: ") :] for line in second.text.split("\n\n") if line]
    assert events[-1] == "[DONE]" and json.loads(events[0])["id"] == json.loads(events[-2])["id"]
    assert len(gw.rt.ratchet.teacher.calls) == 1
    # The same key without `stream` is another request.
    r = c.post(CHAT, json={"messages": _msgs()}, headers={"Idempotency-Key": "stream-1"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "idempotency_key_reused"


@pytest.mark.req("FR-1.70", "INV-7")
def test_failed_first_request_releases_the_key(gw_env, monkeypatch):
    """An error is never kept: the client's retry with the same key runs again, which is what a retry is for."""
    gw = _gateway(gw_env, monkeypatch)
    gw.rt.ratchet.teacher = RecordingProvider(ANSWER, raise_on={gw.rt.settings.teacher_model})
    c = TestClient(gw.app, raise_server_exceptions=False)
    failed = c.post(CHAT, json={"messages": _msgs()}, headers={"Idempotency-Key": "flaky"})
    assert failed.status_code == 502 and failed.json()["error"]["code"] == "teacher_unavailable"
    gw.rt.ratchet.teacher = RecordingProvider(ANSWER)
    ok = c.post(CHAT, json={"messages": _msgs()}, headers={"Idempotency-Key": "flaky"})
    assert ok.status_code == 200 and "idempotent-replayed" not in ok.headers
    assert len(gw.rt.ratchet.teacher.calls) == 1 and gw.rt.idem.stats()["in_flight"] == 0


@pytest.mark.req("FR-1.70")
def test_idempotency_off_ignores_the_header(gw_env, monkeypatch):
    gw = _gateway(gw_env, monkeypatch, RATCHET_IDEMPOTENCY_CACHE="0")
    c = TestClient(gw.app)
    for key in ("same", "same", "k" * 300):  # not even checked when the cache is off
        r = c.post(CHAT, json={"messages": _msgs()}, headers={"Idempotency-Key": key})
        assert r.status_code == 200 and "idempotent-replayed" not in r.headers
    assert len(gw.rt.ratchet.teacher.calls) == 3


@pytest.mark.req("FR-1.70", "NFR-6")
def test_replay_log_line_says_it_replayed(gw_env, monkeypatch, caplog):
    gw = _gateway(gw_env, monkeypatch, RATCHET_LOG_LEVEL="INFO")
    logger = logging.getLogger("ratchet")
    logger.addHandler(caplog.handler)
    try:
        c = TestClient(gw.app)
        headers = {"Idempotency-Key": "secret-key-7f3a", "x-stainless-retry-count": "1"}
        for _ in range(2):
            assert c.post(CHAT, json={"messages": _msgs()}, headers=headers).status_code == 200
    finally:
        logger.removeHandler(caplog.handler)
    fmt = obs.JsonFormatter()
    chats = [json.loads(fmt.format(r)) for r in caplog.records if r.name == "ratchet" and r.getMessage() == "chat"]
    assert [ch.get("idempotent_replay") for ch in chats] == [None, True]
    assert [ch["retry_count"] for ch in chats] == [1, 1] and chats[1]["served_by"] == "teacher"
    # The replay names the request it replays and cost nothing, so a sum over the log lines counts one cascade.
    assert "replay_of" not in chats[0] and chats[1]["replay_of"] == chats[0]["request_id"] != chats[1]["request_id"]
    assert chats[0]["cost_usd"] > 0 and chats[1]["cost_usd"] == 0.0
    assert chats[1]["task"] == chats[0]["task"] and chats[1].get("wall_ms") is None
    assert "secret-key-7f3a" not in json.dumps(chats)  # the client's key is never logged


# ---------- retries and the golden set (INV-3) ----------
@pytest.mark.req("FR-1.70", "INV-3")
def test_sdk_retries_do_not_drain_the_golden_set(gw_env, monkeypatch):
    """The spec's fifth case: three escalated retries (`x-stainless-retry-count: 1`) of a golden request within 300 s
    leave `seen` at 1, so the example stays golden (FREQUENT is 3). Each retry's ledger row records its attempt number.
    Without the header, the seen window does the same for the same client; another client's request still counts."""
    clock = FakeClock()
    gw = _gateway(gw_env, monkeypatch, clock, RATCHET_API_KEYS=KEYS, RATCHET_SEEN_DEDUP_S="300")
    led = gw.rt.ledger
    msgs = _golden(led)
    c = TestClient(gw.app)
    for _ in range(3):
        r = c.post(CHAT, json={"messages": msgs}, headers={**ACME, "x-stainless-retry-count": "1"})
        assert r.status_code == 200 and r.json()["ratchet"]["served_by"] == "teacher"
    (example,) = led.examples()
    assert example["split"] == "golden" and example["seen"] == 1
    assert [row["provenance"]["hash"] for row in dataset.rows(led, "golden")] == [example["hash"]]
    assert dataset.rows(led, "train") == []
    assert [r["retry_count"] for r in _rows(led, "SELECT retry_count FROM requests ORDER BY id")] == [1, 1, 1]
    assert [r["stored"] for r in _rows(led, "SELECT stored FROM requests")] == [1, 1, 1]  # the example is in the set

    # A client without an SDK retrying within the window: not counted either.
    clock.advance(10)
    for _ in range(3):
        assert c.post(CHAT, json={"messages": msgs}, headers=ACME).status_code == 200
    assert led.examples()[0]["seen"] == 1 and len(dataset.rows(led, "golden")) == 1
    # Another client asking the same thing is a genuine repeat.
    assert c.post(CHAT, json={"messages": msgs}, headers=BETA).status_code == 200
    assert led.examples()[0]["seen"] == 2


@pytest.mark.req("FR-1.70", "INV-3")
def test_the_same_request_minutes_later_still_counts(gw_env, monkeypatch):
    """The spec's sixth case: genuine repeats minutes apart count, so frequent requests still reach training."""
    clock = FakeClock()
    gw = _gateway(gw_env, monkeypatch, clock, RATCHET_SEEN_DEDUP_S="300")
    led = gw.rt.ledger
    msgs = _golden(led)
    c = TestClient(gw.app)
    seen = []
    for _ in range(3):
        assert c.post(CHAT, json={"messages": msgs}).status_code == 200
        seen.append(led.examples()[0]["seen"])
        clock.advance(600)  # ten minutes later
    assert seen == [1, 2, 3]
    assert dataset.rows(led, "golden") == [] and len(dataset.rows(led, "train")) == 1  # FREQUENT: it trains now


@pytest.mark.req("FR-1.70", "INV-3")
def test_openai_sdk_retry_after_a_server_error_is_not_a_repeat(gw_env, monkeypatch):
    """What the real OpenAI SDK sends: its retry of a request that failed after the example was stored (a 500 from the
    gateway's own code) carries `x-stainless-retry-count: 1`, so the example's `seen` stays 1 in any mode."""
    gw = _gateway(gw_env, monkeypatch)
    real_tick = gw.rt.tick
    failures = iter([RuntimeError("tick failed")])

    def tick(force: bool = False) -> bool:  # fails once, after the cascade stored the teacher's answer
        for e in failures:
            raise e
        return real_tick(force)

    monkeypatch.setattr(gw.rt, "tick", tick)
    calls: list[Any] = []
    client = make_openai_client(gw.app, calls=calls, max_retries=1)
    monkeypatch.setattr("openai._base_client.BaseClient._calculate_retry_timeout", lambda *a, **k: 0.0)
    out = client.chat.completions.create(model="ratchet", messages=_msgs())
    assert out.choices[0].message.content == ANSWER
    assert [r.headers["x-stainless-retry-count"] for r in calls] == ["0", "1"]
    led = gw.rt.ledger
    assert led.examples()[0]["seen"] == 1
    assert [r["retry_count"] for r in _rows(led, "SELECT retry_count FROM requests ORDER BY id")] == [0, 1]


@pytest.mark.req("FR-1.70")
def test_internal_traffic_and_mock_mode_count_every_repeat(gw_env, monkeypatch):
    """The window is for clients: the simulator, Scout and the mock warm-up call the cascade without a request context,
    and mock mode leaves the window off unless RATCHET_SEEN_DEDUP_S is set, so the demo and the reference runs keep
    v0.4's counts. A RequestMeta outside a gateway request is not a sender either."""
    gw = _gateway(gw_env, monkeypatch)
    assert gw.rt.ledger.seen_window is None
    c = TestClient(gw.app)
    for _ in range(3):
        assert c.post(CHAT, json={"messages": _msgs()}).status_code == 200
    assert gw.rt.ledger.examples()[0]["seen"] == 3

    gw = _gateway(gw_env, monkeypatch, RATCHET_SEEN_DEDUP_S="300")
    for meta in (None, RequestMeta(request_id="req_not_in_flight")):
        gw.rt.ratchet.handle(_msgs(), "default", meta=meta)
    gw.rt.ratchet.handle(_msgs(), "default", meta=RequestMeta(retry_count=1))  # an explicit retry is still skipped
    assert gw.rt.ledger.examples()[0]["seen"] == 2


@pytest.mark.req("FR-1.70", "FR-12.30")
def test_retry_settings_are_checked_at_startup():
    assert Settings(mode="live", seen_dedup_s=None).seen_dedup() == 300.0
    assert Settings(mode="mock", seen_dedup_s=None).seen_dedup() == 0.0
    assert Settings(mode="mock", seen_dedup_s=30).seen_dedup() == 30.0
    assert Settings(idempotency_cache=0, idempotency_ttl_s=0).idempotency() == (0, 0.0)
    for field, value, var in (
        ("idempotency_cache", -1, "RATCHET_IDEMPOTENCY_CACHE"),
        ("idempotency_cache", 2_000_000, "RATCHET_IDEMPOTENCY_CACHE"),
        ("idempotency_ttl_s", -5.0, "RATCHET_IDEMPOTENCY_TTL_S"),
        ("idempotency_ttl_s", float("inf"), "RATCHET_IDEMPOTENCY_TTL_S"),
        ("seen_dedup_s", 90_000.0, "RATCHET_SEEN_DEDUP_S"),
        ("seen_dedup_s", float("nan"), "RATCHET_SEEN_DEDUP_S"),
    ):
        issues = Settings(mode="mock", **{field: value}).validate({})
        assert [i.var for i in issues if i.level == "error"] == [var], (field, value, issues)
    # One reader checks both idempotency settings; each bad one is still its own error.
    issues = Settings(mode="live", idempotency_cache=-1, idempotency_ttl_s=-1.0).validate({})
    assert {"RATCHET_IDEMPOTENCY_CACHE", "RATCHET_IDEMPOTENCY_TTL_S"} <= {i.var for i in issues if i.level == "error"}


@pytest.mark.req("FR-1.70", "FR-12.30")
def test_bad_retry_setting_stops_the_gateway(gw, monkeypatch):
    monkeypatch.setenv("RATCHET_IDEMPOTENCY_TTL_S", "ten minutes")
    monkeypatch.setenv("RATCHET_SEEN_DEDUP_S", "-1")
    with pytest.raises(ConfigError) as caught:
        gw.Runtime()
    assert {i.var for i in caught.value.issues} == {"RATCHET_IDEMPOTENCY_TTL_S", "RATCHET_SEEN_DEDUP_S"}
