"""Protects the public Scout demo: a daily spend cap and a per-IP hourly run limit."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from datetime import datetime, timezone


class Guard:
    def __init__(
        self,
        daily_budget_usd: float | None,
        runs_per_hour: int,
        max_runs_per_day: int | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.budget = daily_budget_usd  # None = no budget (mock mode)
        self.runs_per_hour = runs_per_hour
        self.max_runs_per_day = max_runs_per_day  # caps search credits (each run makes ~3 Tavily calls)
        self.clock = clock
        self._lock = threading.Lock()
        self._runs: dict[str, deque[float]] = defaultdict(deque)
        self._day = ""
        self.spent_today = 0.0
        self.runs_today = 0

    def _roll_day(self) -> None:
        day = datetime.fromtimestamp(self.clock(), timezone.utc).strftime("%Y-%m-%d")
        if day != self._day:
            self._day, self.spent_today, self.runs_today = day, 0.0, 0

    def admit(self, ip: str) -> str | None:
        """Record a run and return None, or return why it is refused."""
        with self._lock:
            self._roll_day()
            if self.budget is not None and self.spent_today >= self.budget:
                return "The daily demo budget is used up. Try again tomorrow (UTC), or run Ratchet yourself."
            if self.max_runs_per_day is not None and self.runs_today >= self.max_runs_per_day:
                return "The demo has reached its run limit for today. Try again tomorrow (UTC), or run Ratchet yourself."
            now = self.clock()
            q = self._runs[ip]
            while q and now - q[0] > 3600:
                q.popleft()
            if len(q) >= self.runs_per_hour:
                return f"Rate limit: {self.runs_per_hour} runs per hour. Please try again later."
            q.append(now)
            self.runs_today += 1
            return None

    def add_spend(self, usd: float) -> None:
        with self._lock:
            self._roll_day()
            self.spent_today += max(0.0, usd)

    def status(self) -> dict[str, float | int | None]:
        with self._lock:
            self._roll_day()
            return {
                "budget_usd": self.budget,
                "spent_today_usd": round(self.spent_today, 6),
                "runs_today": self.runs_today,
                "runs_per_hour_per_ip": self.runs_per_hour,
                "max_runs_per_day": self.max_runs_per_day,
            }
