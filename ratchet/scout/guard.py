"""Protects the public Scout demo: a daily spend cap, a daily run cap and a per-IP hourly run limit (INV-11).

With a ledger (live mode) every admitted run writes a `scout_run` spend row (`usd` 0; pool, cost so far and outcome in
`detail`), and `restore()` rebuilds today's counters from those rows at startup, so a restart or an update never resets
the daily caps. Judges have a reserved share of runs and spend that the public cannot use (ADR 0120). Per-IP windows stay
in memory: no IP is ever stored.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections import OrderedDict, defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

LOG = logging.getLogger(__name__)
DAY_S = 86400
POOLS = ("public", "judge")
OUTCOMES = ("done", "error", "search_unavailable")  # how a run ended (`scout_run` rows' final status)
MAX_TRACKED_RUNS = 1000  # run ids whose spend row this guard still updates; older ones are forgotten
MAX_TRACKED_IPS = 4096  # past this many per-IP windows, those with no run in the last hour are dropped
# Refusals. None of them hints that a judge code exists.
PUBLIC_RUNS_USED = "The public demo has reached its run limit for today. Try again tomorrow (UTC), or run Ratchet yourself."
BUDGET_USED = "The daily demo budget is used up. Try again tomorrow (UTC), or run Ratchet yourself."
JUDGE_USED = "Today's demo allowance, judges' share included, is used up."


@dataclass
class _Run:
    row: int | None  # its spend row, when a ledger is attached
    judge: bool
    day: str  # the UTC day it was admitted on, which its spend counts against


def _amount(value: Any) -> float:
    """A spend amount read back from a row: a finite number, 0 or more, else 0."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return 0.0
    return float(value)


class Guard:
    """Admission control for Scout runs (INV-11: every run is admitted here before it starts and its spend added after
    it ends). Thread-safe.

    `daily_budget_usd` and `max_runs_per_day` are the day's totals T (None: no cap, as in mock mode). Of each, the judges'
    reserve J is held back: with public use P and judge use U today, a public run is admitted while P + max(0, U - J) + m
    < T - J and a judge run while P + U < T. For runs m is 0; for spend m is the largest finished run today, when a judge
    budget is reserved, so public runs in flight cannot spill into the judges' share. Totals stay at or under T (spend:
    plus the runs in flight when it is reached)."""

    def __init__(
        self,
        daily_budget_usd: float | None,
        runs_per_hour: int,
        max_runs_per_day: int | None = None,
        clock: Callable[[], float] = time.time,
        *,
        judge_runs_per_day: int = 0,
        judge_budget_usd: float = 0.0,
        ledger: Any = None,
    ):
        if max_runs_per_day is not None and not 0 <= judge_runs_per_day <= max_runs_per_day:
            raise ValueError(f"judge_runs_per_day must be 0 to max_runs_per_day ({max_runs_per_day}), not {judge_runs_per_day!r}")
        if judge_runs_per_day < 0:
            raise ValueError(f"judge_runs_per_day must be 0 or more, not {judge_runs_per_day!r}")
        if daily_budget_usd is not None and not 0 <= judge_budget_usd <= daily_budget_usd:
            raise ValueError(f"judge_budget_usd must be 0 to daily_budget_usd ({daily_budget_usd}), not {judge_budget_usd!r}")
        self.budget = daily_budget_usd  # None = no budget (mock mode)
        self.runs_per_hour = runs_per_hour
        self.max_runs_per_day = max_runs_per_day  # caps search credits (each run makes ~3 Tavily calls)
        self.judge_runs_per_day = judge_runs_per_day
        self.judge_budget_usd = float(judge_budget_usd) if daily_budget_usd is not None else 0.0
        self.clock = clock
        self.ledger = ledger
        self._lock = threading.Lock()
        self._windows: dict[str, deque[float]] = defaultdict(deque)
        self._open: OrderedDict[str, _Run] = OrderedDict()  # run id -> its row, pool and day, until add_spend
        self._day = ""
        self.runs = {"public": 0, "judge": 0}
        self.usd = {"public": 0.0, "judge": 0.0}
        self.max_run_usd = 0.0  # the largest finished run today

    @property
    def spent_today(self) -> float:
        return self.usd["public"] + self.usd["judge"]

    @property
    def runs_today(self) -> int:
        return self.runs["public"] + self.runs["judge"]

    def _today(self) -> str:
        return datetime.fromtimestamp(self.clock(), timezone.utc).strftime("%Y-%m-%d")

    def _roll_day(self) -> None:
        day = self._today()
        if day != self._day:
            self._day = day
            self.runs = {"public": 0, "judge": 0}
            self.usd = {"public": 0.0, "judge": 0.0}
            self.max_run_usd = 0.0

    def _refusal(self, judge: bool) -> str | None:
        """Why the pool rule refuses a run now (call with the lock held), or None."""
        p_usd, u_usd = self.usd["public"], self.usd["judge"]
        p_runs, u_runs = self.runs["public"], self.runs["judge"]
        if judge:
            if self.budget is not None and p_usd + u_usd >= self.budget:
                return JUDGE_USED
            if self.max_runs_per_day is not None and p_runs + u_runs >= self.max_runs_per_day:
                return JUDGE_USED
            return None
        if self.budget is not None:
            j = self.judge_budget_usd
            margin = self.max_run_usd if j > 0 else 0.0
            if p_usd + max(0.0, u_usd - j) + margin >= self.budget - j:
                return BUDGET_USED
        if self.max_runs_per_day is not None:
            j_runs = self.judge_runs_per_day
            if p_runs + max(0, u_runs - j_runs) >= self.max_runs_per_day - j_runs:
                return PUBLIC_RUNS_USED
        return None

    def admit(self, ip: str, *, judge: bool = False, run_id: str | None = None) -> str | None:
        """Record a run and return None, or return why it is refused: the pool rule first, then the per-IP hourly
        window (judges have their own window per IP, so public use from a shared address cannot use up a judge's hour).
        With a ledger, writes the run's `scout_run` row; a ledger failure is logged and the run still counts in memory."""
        with self._lock:
            self._roll_day()
            refused = self._refusal(judge)
            if refused:
                return refused
            now = self.clock()
            if len(self._windows) > MAX_TRACKED_IPS:  # bound memory under a flood of addresses: keep the active ones
                self._windows = defaultdict(deque, {k: q for k, q in self._windows.items() if q and now - q[-1] <= 3600})
            window = self._windows[f"judge|{ip}" if judge else ip]
            while window and now - window[0] > 3600:
                window.popleft()
            if len(window) >= self.runs_per_hour:
                return f"Rate limit: {self.runs_per_hour} runs per hour. Please try again later."
            window.append(now)
            self.runs["judge" if judge else "public"] += 1
            row = None
            if self.ledger is not None:
                try:
                    row = self.ledger.add_spend(
                        "scout_run", run_id or "", detail={"judge": judge, "llm_usd": 0.0, "status": "admitted"}, ts=now
                    )
                except Exception as e:  # noqa: BLE001 - the in-memory caps still hold
                    LOG.warning("could not record a Scout run in the ledger (%s)", type(e).__name__)
            if run_id:
                self._open[run_id] = _Run(row, judge, self._day)
                self._open.move_to_end(run_id)
                while len(self._open) > MAX_TRACKED_RUNS:
                    self._open.popitem(last=False)
            return None

    def progress(self, run_id: str, usd: float) -> None:
        """Persist a run's cost so far (after each model call), so a process killed mid-run still counts it after a
        restart. In memory, a run's spend is added once, by `add_spend`."""
        with self._lock:
            run = self._open.get(run_id)
        if run is None or run.row is None:
            return
        self._write(run, {"judge": run.judge, "llm_usd": round(_amount(usd), 9), "status": "admitted"})

    def add_spend(self, usd: float, *, judge: bool = False, run_id: str | None = None, outcome: str = "done") -> None:
        """Add a finished run's total spend (called once, when the run ends, also on failure: INV-11). A run admitted on
        an earlier UTC day counts against that day, as its row does after a restart."""
        if outcome not in OUTCOMES:
            raise ValueError(f"outcome must be one of {list(OUTCOMES)}, not {outcome!r}")
        usd = _amount(usd)
        with self._lock:
            self._roll_day()
            run = self._open.pop(run_id, None) if run_id else None
            if run is not None:
                judge = run.judge
            if run is None or run.day == self._day:
                self.usd["judge" if judge else "public"] += usd
                self.max_run_usd = max(self.max_run_usd, usd)
        if run is not None and run.row is not None:
            self._write(run, {"judge": run.judge, "llm_usd": round(usd, 9), "status": outcome})

    def _write(self, run: _Run, detail: dict[str, Any]) -> None:
        try:
            self.ledger.update_spend_detail(run.row, detail)
        except Exception as e:  # noqa: BLE001 - never fail a run over its bookkeeping
            LOG.warning("could not update a Scout run in the ledger (%s)", type(e).__name__)

    def restore(self) -> None:
        """Rebuild today's counters from the ledger's `scout_run` rows since 00:00 UTC (live mode, at startup). A row
        still `admitted` (a process killed mid-run) counts its last recorded cost. Idempotent; writes no event."""
        if self.ledger is None:
            return
        since = math.floor(self.clock() / DAY_S) * DAY_S
        rows = self.ledger.spend_rows(("scout_run",), since)
        with self._lock:
            self._day = ""
            self._roll_day()
            for r in rows:
                d = r["detail"]
                pool = "judge" if d.get("judge") is True else "public"
                usd = _amount(d.get("llm_usd"))
                self.runs[pool] += 1
                self.usd[pool] += usd
                if d.get("status") != "admitted":
                    self.max_run_usd = max(self.max_run_usd, usd)

    def _left(self) -> tuple[int | None, float | None, int | None]:
        """(public runs left, public budget left, judge runs left) today (call with the lock held); None without a cap."""
        p_runs, u_runs = self.runs["public"], self.runs["judge"]
        public_runs = judge_runs = None
        if self.max_runs_per_day is not None:
            j = self.judge_runs_per_day
            public_runs = max(0, self.max_runs_per_day - j - p_runs - max(0, u_runs - j))
            judge_runs = max(0, self.max_runs_per_day - p_runs - u_runs)
        public_usd = None
        if self.budget is not None:
            j_usd = self.judge_budget_usd
            used = self.usd["public"] + max(0.0, self.usd["judge"] - j_usd)
            public_usd = round(max(0.0, self.budget - j_usd - used), 6)
        return public_runs, public_usd, judge_runs

    def judge_runs_left(self) -> int | None:
        """Runs a judge can still start today (their reserve plus public room left), or None without a run cap."""
        with self._lock:
            self._roll_day()
            return self._left()[2]

    def judge_status(self) -> dict[str, int | None]:
        """The judges' reserve: runs held back per day, judge runs today and runs a judge can still start today."""
        with self._lock:
            self._roll_day()
            return {
                "reserved_runs_per_day": self.judge_runs_per_day,
                "runs_today": self.runs["judge"],
                "runs_left_today": self._left()[2],
            }

    def status(self) -> dict[str, float | int | bool | None]:
        """The v0.2 keys, unchanged, plus the public pool's runs and budget left today, judge runs today and whether
        the counters are kept in the ledger (`persistent`)."""
        with self._lock:
            self._roll_day()
            public_runs, public_usd, _ = self._left()
            return {
                "budget_usd": self.budget,
                "spent_today_usd": round(self.spent_today, 6),
                "runs_today": self.runs_today,
                "runs_per_hour_per_ip": self.runs_per_hour,
                "max_runs_per_day": self.max_runs_per_day,
                "public_runs_left_today": public_runs,
                "public_budget_left_usd": public_usd,
                "judge_runs_today": self.runs["judge"],
                "persistent": self.ledger is not None,
            }
