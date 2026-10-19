"""Search tools for Scout's searcher agent: Tavily in live mode, simulated results in mock mode."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx

TAVILY_URL = "https://api.tavily.com/search"


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
    """Tavily Search API (https://docs.tavily.com). `basic` depth costs 1 credit per query."""

    name = "tavily"

    def __init__(self, api_key: str, client: httpx.Client | None = None, url: str = TAVILY_URL, timeout: float = 30.0):
        if not api_key:
            raise ValueError("TAVILY_API_KEY is not set")
        self.api_key = api_key
        self.url = url
        self.client = client or httpx.Client(timeout=timeout)

    def search(self, query: str, max_results: int = 4) -> list[Hit]:
        r = self.client.post(
            self.url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"query": query, "search_depth": "basic", "max_results": max_results, "topic": "general"},
        )
        r.raise_for_status()
        hits = []
        for res in r.json().get("results", [])[:max_results]:
            content = " ".join((res.get("content") or "").split())
            if not res.get("url"):
                continue
            hits.append(Hit(title=res.get("title") or res["url"], url=res["url"], snippet=content[:500], excerpt=content[:1500]))
        return hits


class MockSearch:
    """Simulated results from sim/research.py. Only the example questions' queries return anything."""

    name = "mock-search"

    def search(self, query: str, max_results: int = 4) -> list[Hit]:
        from sim import research

        return [Hit(h.title, h.url, h.snippet, h.excerpt) for h in research.search(query, max_results)]
