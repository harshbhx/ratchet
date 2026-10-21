"""Search tools for Scout's searcher agent: Tavily in live mode, simulated results in mock mode."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

TAVILY_URL = "https://api.tavily.com/search"
# Credits per search by depth, from Tavily's search reference (https://docs.tavily.com/documentation/api-reference/endpoint/
# search, checked Oct 20, 2026). Charged when a response does not report its own `usage.credits`.
CREDITS = {"basic": 1, "fast": 1, "ultra-fast": 1, "advanced": 2}
MAX_REPORTED_CREDITS = 10  # a `usage.credits` above this is not believed; the table amount is charged instead
# Why a search was not attempted. A fixed set, so an event or a page never shows text a visitor or an upstream wrote.
PUBLIC_CREDITS_USED = "public search credits for today are used up"
CREDITS_USED = "search credits are used up"
UNAVAILABLE_REASONS = (PUBLIC_CREDITS_USED, CREDITS_USED)


class SearchUnavailable(Exception):
    """A search that was not attempted, because the credit meter refused it (ADR 0120). `reason` is one of
    UNAVAILABLE_REASONS. Scout then answers without (more) web sources, labelled."""

    def __init__(self, reason: str) -> None:
        if reason not in UNAVAILABLE_REASONS:
            raise ValueError(f"unknown search-unavailable reason {reason!r}")
        super().__init__(reason)
        self.reason = reason


@dataclass
class Hit:
    title: str
    url: str
    snippet: str  # what the triage agent sees
    excerpt: str  # what the reader agent sees
    id: int = 0


class SearchTool(Protocol):
    name: str

    def search(self, query: str, max_results: int = 4) -> list[Hit]: ...


class TavilySearch:
    """Tavily Search API (https://docs.tavily.com). `basic` depth costs 1 credit per query, `advanced` 2 (CREDITS)."""

    name = "tavily"

    def __init__(
        self,
        api_key: str,
        client: httpx.Client | None = None,
        url: str = TAVILY_URL,
        timeout: float = 30.0,
        depth: str = "basic",
    ):
        if not api_key:
            raise ValueError("TAVILY_API_KEY is not set")
        if depth not in CREDITS:
            raise ValueError(f"search depth must be one of {sorted(CREDITS)}, not {depth!r}")
        self.api_key = api_key
        self.url = url
        self.depth = depth
        self.client = client or httpx.Client(timeout=timeout)

    @property
    def credits_per_search(self) -> int:
        """What one search costs by Tavily's table: reserved before each call, and charged when Tavily does not say."""
        return CREDITS[self.depth]

    def search(self, query: str, max_results: int = 4) -> list[Hit]:
        return self.search_with_usage(query, max_results)[0]

    def search_with_usage(self, query: str, max_results: int = 4) -> tuple[list[Hit], int, str]:
        """(hits, credits, counted): the credits Tavily reports in `usage.credits` (counted "usage"), or the table amount
        for this depth when it reports none or an implausible one ("table"). A failed call raises an `httpx.HTTPError`:
        `HTTPStatusError` for a status other than 2xx, `DecodingError` for a body that is not JSON."""
        r = self.client.post(
            self.url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "query": query,
                "search_depth": self.depth,
                "max_results": max_results,
                "topic": "general",
                "include_usage": True,
            },
        )
        r.raise_for_status()
        try:
            data: Any = r.json()
        except ValueError:
            raise httpx.DecodingError("the search response is not JSON", request=r.request) from None
        data = data if isinstance(data, dict) else {}
        results = data.get("results")
        hits = []
        for res in results[:max_results] if isinstance(results, list) else []:
            url = res.get("url") if isinstance(res, dict) else None
            if not isinstance(url, str) or not url:
                continue
            content = " ".join(str(res.get("content") or "").split())
            title = res.get("title")
            title = title if isinstance(title, str) and title else url
            hits.append(Hit(title=title, url=url, snippet=content[:500], excerpt=content[:1500]))
        usage = data.get("usage")
        credits = usage.get("credits") if isinstance(usage, dict) else None
        if isinstance(credits, int) and not isinstance(credits, bool) and 0 <= credits <= MAX_REPORTED_CREDITS:
            return hits, credits, "usage"
        return hits, self.credits_per_search, "table"


class MockSearch:
    """Simulated results from sim/research.py. Only the example questions' queries return anything."""

    name = "mock-search"

    def search(self, query: str, max_results: int = 4) -> list[Hit]:
        from sim import research

        return [Hit(h.title, h.url, h.snippet, h.excerpt) for h in research.search(query, max_results)]
