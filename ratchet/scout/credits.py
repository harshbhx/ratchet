"""Tavily credits for the public Scout demo (ADR 0120): a monthly budget counted per search, paced per UTC day so the
public cannot spend the month early, with a share held back for judges and kept in the ledger so a restart never resets
it.

`CreditMeter` reserves a search's credits before the call (`reserve`) and replaces the reservation with what the call
cost (`settle`), writing one `tavily` spend row per call (`usd` 0, the amounts in `detail`). `MeteredSearch` wraps the
Tavily client for one Scout run. A refused reservation never refuses the run: Scout answers without web sources,
labelled (`SearchUnavailable`).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx

from .search import CREDITS, CREDITS_USED, PUBLIC_CREDITS_USED, Hit, SearchUnavailable

LOG = logging.getLogger(__name__)
DAY_S = 86400
EXHAUST_S = 3600.0  # after Tavily answers 432 or 433, no search is sent for this long
EXHAUSTED_STATUSES = (432, 433)  # "Key limit or Plan Limit exceeded", "PayGo limit exceeded" (Tavily's search reference)
# Errors raised before a request reached Tavily, so it cannot have billed one. Anything else after the call started (a
# read timeout, a dropped connection) may have been billed: Tavily does not say, so the table amount is charged.
NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout, httpx.ProxyError, httpx.UnsupportedProtocol)
POOLS = ("public", "judge")


def _utc_date(ts: float) -> date:
    return datetime.fromtimestamp(ts, timezone.utc).date()


def _midnight(d: date) -> float:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()


def _add_month(d: date) -> date:
    return date(d.year + d.month // 12, d.month % 12 + 1, d.day)


def cycle_bounds(today: date, cycle_day: int) -> tuple[date, date]:
    """(first day, first day of the next cycle) of the credit cycle `today` falls in. It starts at 00:00 UTC on
    `cycle_day` (1 to 28, so every month has it)."""
    if not 1 <= cycle_day <= 28:
        raise ValueError(f"cycle_day must be 1 to 28, not {cycle_day!r}")
    start = date(today.year, today.month, cycle_day)
    if today < start:
        start = date(today.year - (today.month == 1), (today.month - 2) % 12 + 1, cycle_day)
    return start, _add_month(start)


def settlement(table: int, *, http: int | None, sent: bool = True, usage: int | None = None) -> tuple[int, str]:
    """(credits charged, how they were counted) for one finished call. A 2xx charges what Tavily reported (`usage`) or
    the table amount; a 4xx (432 and 433 included) charges nothing; a 5xx, or a failure after the request was sent,
    charges the table amount, because whether Tavily bills those is not documented; a request never sent charges
    nothing."""
    if http is not None and 200 <= http < 300:
        return (usage, "usage") if usage is not None else (table, "table")
    if http is not None and 400 <= http < 500:
        return 0, "none"
    if http is not None or sent:
        return table, "table"
    return 0, "none"


class CreditMeter:
    """Credits per cycle (`monthly`, 0 = counted without a cap), of which `judge_reserve` is held back for judge runs.

    Public use is paced: today's allowance is what the public pool has left at 00:00 UTC, spread evenly over the days
    until `pace_until` (when it falls in this cycle and is today or later) or the cycle's last day; unused credits roll
    forward. Judges draw on their reserve first, then on today's public allowance, so public and judge use together
    never pass `monthly`. Thread-safe: Scout runs search from worker threads."""

    def __init__(
        self,
        monthly: int,
        judge_reserve: int,
        cycle_day: int = 1,
        pace_until: date | None = None,
        clock: Callable[[], float] = time.time,
        ledger: Any = None,
    ) -> None:
        for what, value in (("monthly", monthly), ("judge_reserve", judge_reserve)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{what} must be a whole number of credits, 0 or more, not {value!r}")
        if judge_reserve > monthly:
            raise ValueError(f"judge_reserve ({judge_reserve}) cannot be more than monthly ({monthly})")
        cycle_bounds(date(2026, 1, 1), cycle_day)  # checks the day
        self.monthly = monthly
        self.judge_reserve = judge_reserve
        self.cycle_day = cycle_day
        self.pace_until = pace_until
        self.clock = clock
        self.ledger = ledger
        self._lock = threading.Lock()
        self._cycle: tuple[date, date] | None = None
        self._by_day: dict[date, list[int]] = {}  # charged credits per UTC day of this cycle: [public, judge]
        self._pending = [0, 0]  # credits reserved by searches in flight: [public, judge]
        self.exhausted_until = 0.0

    # ----- the cycle and the pool arithmetic (call with the lock held) -----
    def _roll(self) -> date:
        today = _utc_date(self.clock())
        bounds = cycle_bounds(today, self.cycle_day)
        if bounds != self._cycle:
            self._cycle = bounds
            self._by_day = {d: v for d, v in self._by_day.items() if bounds[0] <= d < bounds[1]}
        return today

    def _numbers(self, today: date) -> dict[str, int]:
        """P and U (public and judge use this cycle, reservations included), Q (public-pool use: P plus judge use past
        the reserve), Q0 (the same before today) and today's allowance."""
        assert self._cycle is not None
        p = sum(v[0] for v in self._by_day.values()) + self._pending[0]
        u = sum(v[1] for v in self._by_day.values()) + self._pending[1]
        now = self._by_day.get(today, [0, 0])
        p0, u0 = p - now[0] - self._pending[0], u - now[1] - self._pending[1]
        cj = self.judge_reserve
        q, q0 = p + max(0, u - cj), p0 + max(0, u0 - cj)
        start, nxt = self._cycle
        last = nxt - timedelta(days=1)
        pace = self.pace_until
        end = pace if pace is not None and start <= pace <= last and pace >= today else last
        days_left = (end - today).days + 1
        allowance = max(0, (self.monthly - cj - q0) // days_left)
        return {"p": p, "u": u, "q": q, "q0": q0, "allowance": allowance}

    # ----- searches -----
    def reserve(self, judge: bool, k: int) -> str | None:
        """Reserve `k` credits for one search and return None, or return why the search may not run (a reason from
        `search.UNAVAILABLE_REASONS`). Never refuses with `monthly` 0, except while Tavily reports the credits used up."""
        with self._lock:
            today = self._roll()
            if self.clock() < self.exhausted_until:
                return CREDITS_USED
            if self.monthly > 0:
                n = self._numbers(today)
                cj = self.judge_reserve
                if judge:
                    ok = n["u"] + k <= cj or n["p"] + max(0, n["u"] + k - cj) - n["q0"] <= n["allowance"]
                    if not ok:
                        return CREDITS_USED
                elif n["q"] - n["q0"] + k > n["allowance"]:
                    left = self.monthly - cj - n["q"]  # the public pool's credits for the rest of the cycle
                    return CREDITS_USED if left < k else PUBLIC_CREDITS_USED
            self._pending[judge] += k
            return None

    def take(self, judge: bool, k: int) -> bool:
        """Reserve `k` credits for one search; False when it may not run."""
        return self.reserve(judge, k) is None

    def settle(
        self,
        judge: bool,
        reserved: int,
        credits: int,
        *,
        counted: str,
        http: int | None,
        run_id: str = "",
        depth: str = "basic",
    ) -> None:
        """Replace a reservation of `reserved` credits with the `credits` the call cost, and write its `tavily` row
        (`usd` 0; judge, credits, depth, HTTP status and how the credits were counted; nothing about the query).
        A 432 or 433 also pauses searching for an hour (`exhaust`). A ledger failure is logged, never raised: the
        in-memory count still holds."""
        now = self.clock()
        with self._lock:
            today = self._roll()
            self._pending[judge] = max(0, self._pending[judge] - reserved)
            if credits > 0:
                self._by_day.setdefault(today, [0, 0])[judge] += credits
        if self.ledger is not None:
            detail = {"judge": bool(judge), "credits": credits, "depth": depth, "http": http, "counted": counted}
            try:
                self.ledger.add_spend("tavily", run_id, detail=detail, ts=now)
            except Exception as e:  # noqa: BLE001 - counting must never fail a search
                LOG.warning("could not record a Tavily search in the ledger (%s)", type(e).__name__)
        if http in EXHAUSTED_STATUSES:
            self.exhaust(EXHAUST_S, http)

    def exhaust(self, seconds: float = EXHAUST_S, http: int | None = None) -> None:
        """Send no search for `seconds`: Tavily said the key's credits are used up. Writes one `tavily_exhausted` event
        per exhaustion (not one per refused call)."""
        now = self.clock()
        with self._lock:
            fresh = now >= self.exhausted_until
            self.exhausted_until = max(self.exhausted_until, now + seconds)
        if fresh and self.ledger is not None:
            why = f"HTTP {http}" if http is not None else "credits used up"
            try:
                self.ledger.event("tavily_exhausted", f"Tavily refused a search ({why}); no searches for {seconds / 60:.0f} min")
            except Exception as e:  # noqa: BLE001
                LOG.warning("could not record tavily_exhausted (%s)", type(e).__name__)

    def restore(self) -> None:
        """Count this cycle's `tavily` rows from the ledger (at startup, so a restart never resets the month), and a
        432 or 433 from the last hour as a pause still running. Writes no event."""
        if self.ledger is None:
            return
        with self._lock:
            self._roll()
            assert self._cycle is not None
            since = _midnight(self._cycle[0])
        rows = self.ledger.spend_rows(("tavily",), since)
        now = self.clock()
        with self._lock:
            self._by_day = {}
            for r in rows:
                d = r["detail"]
                credits = d.get("credits")
                if isinstance(credits, int) and not isinstance(credits, bool) and credits > 0:
                    self._by_day.setdefault(_utc_date(r["ts"]), [0, 0])[d.get("judge") is True] += credits
                if d.get("http") in EXHAUSTED_STATUSES and r["ts"] + EXHAUST_S > now:
                    self.exhausted_until = max(self.exhausted_until, r["ts"] + EXHAUST_S)
            self._roll()

    def status(self) -> dict[str, Any]:
        """The meter for `/scout/status`: credits per cycle, used (searches in flight included) and left, the public's
        allowance left today, the judge reserve and what is left of it, the cycle's first day, the pacing date and
        the end of a pause after a 432 or 433 (ISO dates and times, UTC). With `monthly` 0 the limits are null."""
        with self._lock:
            today = self._roll()
            n = self._numbers(today)
            assert self._cycle is not None
            capped = self.monthly > 0
            used = n["p"] + n["u"]
            exhausted = self.exhausted_until if self.clock() < self.exhausted_until else None
            return {
                "monthly": self.monthly,
                "used_this_cycle": used,
                "left": max(0, self.monthly - used) if capped else None,
                "public_left_today": max(0, n["allowance"] - (n["q"] - n["q0"])) if capped else None,
                "judge_reserve": self.judge_reserve,
                "judge_left": max(0, self.judge_reserve - n["u"]),
                "cycle_start": self._cycle[0].isoformat(),
                "pace_until": self.pace_until.isoformat() if self.pace_until is not None else None,
                "exhausted_until": (
                    datetime.fromtimestamp(exhausted, timezone.utc).isoformat(timespec="seconds") if exhausted else None
                ),
            }


class MeteredSearch:
    """A search tool for one Scout run: each search reserves its credits first (`SearchUnavailable` when refused), and
    every call that started is settled, also when it fails, with its HTTP status. The error is raised again for Scout to
    label the run (ADR 0120)."""

    def __init__(self, inner: Any, meter: CreditMeter, judge: bool, run_id: str) -> None:
        self.inner = inner
        self.meter = meter
        self.judge = judge
        self.run_id = run_id
        self.name = inner.name
        self.depth = getattr(inner, "depth", "basic")
        self.cost = getattr(inner, "credits_per_search", CREDITS.get(self.depth, 1))

    def search(self, query: str, max_results: int = 4) -> list[Hit]:
        reason = self.meter.reserve(self.judge, self.cost)
        if reason is not None:
            raise SearchUnavailable(reason)
        http: int | None = None
        try:
            if hasattr(self.inner, "search_with_usage"):
                hits, used, counted = self.inner.search_with_usage(query, max_results)
            else:
                hits, used, counted = self.inner.search(query, max_results), self.cost, "table"
        except httpx.HTTPStatusError as e:
            http = e.response.status_code
            used, counted = settlement(self.cost, http=http)
            self._settle(used, counted, http)
            raise
        except BaseException as e:
            used, counted = settlement(self.cost, http=None, sent=not isinstance(e, NOT_SENT))
            self._settle(used, counted, None)
            raise
        used, counted = settlement(self.cost, http=200, usage=used if counted == "usage" else None)
        self._settle(used, counted, 200)
        return hits

    def _settle(self, used: int, counted: str, http: int | None) -> None:
        self.meter.settle(self.judge, self.cost, used, counted=counted, http=http, run_id=self.run_id, depth=self.depth)
