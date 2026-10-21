"""What each API key and the gateway spent today and this month, kept in memory for the request path (ADR 0063).

The daily request quota used to count the key's rows in the ledger on every request. `SpendMeter` keeps the counts and
the US dollars instead, per (key, UTC day), per (key, UTC month) and for the whole gateway per UTC day, for gateway API
requests only: the rows the gateway logged with its request id. Scout's agents and the simulator call the cascade without
one, so their spend stays under their own guard (INV-11).

- It reads the ledger once, when it starts (`Ledger.api_usage_since`): today's answered requests and dollars, the spend
  of failed requests included, and this month's dollars when a monthly cap is set. A restart forgets nothing.
- After that the gateway tells it what each request cost (`add`), and a job whose cost for a request lands later tells it
  again with `requests=0`. Lookups (`usage`) read memory only, so the quota check costs no query.
- A new UTC day starts from zero, and so does a new month. Re-reading the ledger then could count a request twice: one
  logged before the read whose `add` comes after it. A clock that moves back to an earlier day reads the ledger again,
  because memory holds nothing for that day. A request is counted on the day its `add` runs, so one that ends just after
  midnight counts toward the new day here and toward the old one after a restart.
- Budget alerts: the first time a key or the gateway reaches each RATCHET_BUDGET_ALERT_PCT share of a cap in its period
  (the UTC day or month), the meter writes a `budget` event (`acme reached 80% of $5.00 daily budget`). It reads the
  period's `budget` events when it starts, so a restart does not write one again.

One lock guards the counters; events are written after it is released. Standard library only.
"""

from __future__ import annotations

import calendar
import math
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, NamedTuple

from .ledger import Ledger

DAY_S = 86400
GATEWAY = "the gateway"  # who a gateway alert names; a key name has no space, so it can never be a key's
ALERT_KIND = "budget"
DAILY, MONTHLY = "daily", "monthly"
_ALERT = re.compile(r"(?P<who>the gateway|\S+) reached (?P<pct>\d+)% of \$(?P<cap>[0-9.]+) (?P<period>daily|monthly) budget")


class BudgetExceeded(Exception):
    """A request that needs the teacher while the gateway's daily budget is spent and RATCHET_BUDGET_MODE is
    `student_only` (`RequestMeta.student_only`): the router raises it instead of calling the teacher, and the gateway
    answers 429 `insufficient_quota` / `gateway_budget_exceeded`. `need` says why the request needed the teacher:
    `escalation` (the student was unsure or failed, or no version serves yet), `pinned` (a pinned task, or a request the
    student cannot take), `unknown_task` (RATCHET_UNKNOWN_TASKS=teacher) or `tools` (the student does not see tools)."""

    def __init__(self, need: str) -> None:
        super().__init__(f"the gateway's daily budget is spent and this request needs the teacher ({need})")
        self.need = need


def money(usd: float) -> str:
    """A cap as an alert or a 429 names it: two decimals (`5.00`), or up to twelve significant digits for a cap that needs
    more (`0.005`, `0.0000375`, `0.0000001`; float noise past them dropped), never in exponent form, so `_ALERT` reads it
    back."""
    text = f"{usd:.2f}"
    if float(text) == usd:
        return text
    return format(Decimal(f"{usd:.12g}"), "f")


def public_alert(detail: str, names: Mapping[str, str]) -> str:
    """A `budget` event's detail as a scrubbed /ratchet/stats read shows it (ADR 0061): the key under its pseudonym
    (`client` for a name `names` does not hold) and no dollar amount, since a scrubbed copy names no cap
    (`acme reached 80% of $5.00 daily budget` reads `client-1 reached 80% of its daily budget`). A detail that is not an
    alert reads `budget alert`, so nothing in it can name a key."""
    m = _ALERT.fullmatch(detail)
    if m is None:
        return "budget alert"
    who = GATEWAY if m["who"] == GATEWAY else names.get(m["who"], "client")
    return f"{who} reached {m['pct']}% of its {m['period']} budget"


def day_number(ts: float) -> int:
    return int(ts // DAY_S)


def month_bounds(ts: float) -> tuple[float, float]:
    """The UTC month around `ts`: (its first second, the next month's first second)."""
    y, m = time.gmtime(ts)[:2]
    start = calendar.timegm((y, m, 1, 0, 0, 0))
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    return float(start), float(calendar.timegm((ny, nm, 1, 0, 0, 0)))


class Usage(NamedTuple):
    """One key's spend as the quota checks read it: answered requests and dollars today, dollars this month, the
    gateway's dollars today, and the clock reading they are for."""

    day_requests: int
    day_usd: float
    month_usd: float
    gateway_day_usd: float
    now: float


@dataclass(frozen=True)
class Caps:
    """The US dollar caps alerts are measured against: per key name per UTC day and month (only the ones above 0), the
    gateway's per UTC day (0: none), and the shares of a cap that write an alert (`alert_pct`, ascending; empty: none)."""

    daily_usd: Mapping[str, float] = field(default_factory=dict)
    monthly_usd: Mapping[str, float] = field(default_factory=dict)
    gateway_daily_usd: float = 0.0
    alert_pct: tuple[int, ...] = ()


def _wall() -> float:
    return time.time()  # looked up on every call, so a test that replaces time.time moves this clock too


class SpendMeter:
    """Requests and US dollars per key and for the gateway, per UTC day and month (module docstring). `clock` is the
    wall clock in seconds since the epoch (default `time.time`, read at each call). The key None counts requests sent
    without a key (a gateway with RATCHET_API_KEYS unset)."""

    def __init__(self, ledger: Ledger, caps: Caps | None = None, clock: Callable[[], float] | None = None) -> None:
        self.ledger = ledger
        self.caps = caps or Caps()
        self.clock = clock if clock is not None else _wall
        self.months = bool(self.caps.monthly_usd)  # this month's spend is read from the ledger only when a cap needs it
        self._lock = threading.Lock()
        self._requests: dict[str | None, int] = {}
        self._day_usd: dict[str | None, float] = {}
        self._month_usd: dict[str | None, float] = {}
        self._gateway_usd = 0.0
        self._alerted: set[tuple[str, str, int, str]] = set()  # (who, period, pct, cap) written in the current periods
        self._day = 0
        self._day_start = self._day_end = 0.0
        self._month_start = self._month_end = 0.0
        with self._lock:
            self._seed(self.clock())
        self._write(self._due_all())  # crossings already reached at startup that no event records yet

    # ----- periods -----
    def _set_periods(self, now: float) -> None:
        self._day = day_number(now)
        self._day_start, self._day_end = float(self._day * DAY_S), float((self._day + 1) * DAY_S)
        self._month_start, self._month_end = month_bounds(now)

    def _seed(self, now: float) -> None:
        """Read today's (and with a monthly cap this month's) spend and budget events from the ledger. Under the lock."""
        self._set_periods(now)
        day = self.ledger.api_usage_since(self._day_start)
        self._requests = {c: n for c, (n, _) in day.items() if n}
        self._day_usd = {c: usd for c, (_, usd) in day.items()}
        self._gateway_usd = float(sum(self._day_usd.values()))
        if self.months:
            self._month_usd = {c: usd for c, (_, usd) in self.ledger.api_usage_since(self._month_start).items()}
        else:
            self._month_usd = dict(self._day_usd)  # only read for a monthly cap, which seeds it above
        since = self._month_start if self.months else self._day_start
        self._alerted = set()
        for ev in self.ledger.events_since(ALERT_KIND, since):
            m = _ALERT.fullmatch(str(ev["detail"]))
            if m is None:
                continue
            period = m["period"]
            if period == DAILY and ev["ts"] < self._day_start:
                continue  # yesterday's daily alert, read for the month
            self._alerted.add((m["who"], period, int(m["pct"]), m["cap"]))

    def _sync(self, now: float) -> None:
        """Start a new day (and month) when `now` left the current one. Under the lock."""
        if self._day_start <= now < self._day_end:
            return
        if now < self._day_start:  # the clock moved back: memory holds nothing for that day
            self._seed(now)
            return
        new_month = not (self._month_start <= now < self._month_end)
        self._set_periods(now)
        self._requests, self._day_usd, self._gateway_usd = {}, {}, 0.0
        self._alerted = {a for a in self._alerted if a[1] == MONTHLY and not new_month}
        if new_month:
            self._month_usd = {}

    # ----- reads and writes -----
    def usage(self, client: str | None, now: float | None = None) -> Usage:
        """The spend the quota checks read for `client`, as of `now` (default: the clock). Memory only."""
        with self._lock:
            now = self.clock() if now is None else now
            self._sync(now)
            return self._usage(client, now)

    def _usage(self, client: str | None, now: float) -> Usage:
        return Usage(
            self._requests.get(client, 0),
            self._day_usd.get(client, 0.0),
            self._month_usd.get(client, 0.0),
            self._gateway_usd,
            now,
        )

    def add(self, client: str | None, usd: float, requests: int = 1) -> Usage:
        """Count `requests` answered requests (0 for spend that lands later, or for a failed request's spend) and `usd`
        dollars for `client`, then write any budget alert this crossed. A negative, NaN or infinite cost (a misconfigured
        price) counts as 0 and never raises on the request path. Returns the client's usage after it."""
        usd = float(usd) if isinstance(usd, (int, float)) and math.isfinite(usd) and usd > 0 else 0.0
        with self._lock:
            now = self.clock()
            self._sync(now)
            if requests:
                self._requests[client] = self._requests.get(client, 0) + requests
            if usd:
                self._day_usd[client] = self._day_usd.get(client, 0.0) + usd
                self._month_usd[client] = self._month_usd.get(client, 0.0) + usd
                self._gateway_usd += usd
            due = self._due(client) if usd else []
            out = self._usage(client, now)
        self._write(due)
        return out

    def snapshot(self) -> dict[str, Any]:
        """Today's spend for /ratchet/stats: the gateway's and each client's dollars, and the UTC day it is for."""
        with self._lock:
            self._sync(self.clock())
            return {"day_start": self._day_start, "gateway_usd": self._gateway_usd, "clients": dict(self._day_usd)}

    # ----- alerts -----
    def _due(self, client: str | None) -> list[tuple[str, str, int, str]]:
        """The alerts `client`'s and the gateway's spend have reached that are not written yet, marked as written. Under
        the lock."""
        caps = self.caps
        checks: list[tuple[str, str, float, float]] = []
        if caps.gateway_daily_usd > 0:
            checks.append((GATEWAY, DAILY, caps.gateway_daily_usd, self._gateway_usd))
        if client is not None:
            if client in caps.daily_usd:
                checks.append((client, DAILY, caps.daily_usd[client], self._day_usd.get(client, 0.0)))
            if client in caps.monthly_usd:
                checks.append((client, MONTHLY, caps.monthly_usd[client], self._month_usd.get(client, 0.0)))
        due = []
        for who, period, cap, spent in checks:
            for pct in caps.alert_pct:
                key = (who, period, pct, money(cap))
                if spent >= cap * pct / 100 and key not in self._alerted:
                    self._alerted.add(key)
                    due.append(key)
        return due

    def _due_all(self) -> list[tuple[str, str, int, str]]:
        with self._lock:
            due = self._due(None)
            for client in {*self.caps.daily_usd, *self.caps.monthly_usd}:
                due += self._due(client)
        return due

    def _write(self, due: list[tuple[str, str, int, str]]) -> None:
        """Write each alert as a `budget` event. One that cannot be written is marked unwritten again, so the next `add`
        tries again; an alert never fails the request that crossed it."""
        for key in due:
            who, period, pct, cap = key
            try:
                self.ledger.event(ALERT_KIND, f"{who} reached {pct}% of ${cap} {period} budget")
            except Exception:  # noqa: BLE001 - the request that crossed the line is already answered
                with self._lock:
                    self._alerted.discard(key)
