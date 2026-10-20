"""Run Scout against any Ratchet gateway, as an outside client would.

    python -m ratchet.scout "What is LoRA fine-tuning?" --base-url http://localhost:8000/v1

Every agent call goes to the gateway's /v1/chat/completions with an X-Ratchet-Task header.
Search runs locally: Tavily when TAVILY_API_KEY is set, otherwise the simulated results
(which only cover the example questions, and only make sense against a mock-mode gateway).
"""

from __future__ import annotations

import argparse
import os
import sys

import httpx

from .agents import Scout, http_caller
from .search import MockSearch, TavilySearch


def main(argv: list[str] | None = None, client: httpx.Client | None = None) -> int:
    """Returns the exit code. `client` replaces the HTTP client that talks to the gateway (tests only)."""
    ap = argparse.ArgumentParser(description="Scout: multi-agent research through Ratchet")
    ap.add_argument("question")
    ap.add_argument("--base-url", default="http://localhost:8000/v1")
    ap.add_argument("--api-key", default=os.environ.get("RATCHET_API_KEY", "unused"))
    ap.add_argument("--max-results", type=int, default=4)
    args = ap.parse_args(argv)

    key = os.environ.get("TAVILY_API_KEY", "")
    search = TavilySearch(key) if key else MockSearch()
    scout = Scout(http_caller(args.base_url, args.api_key, client=client), search, max_results=args.max_results)

    def show(ev: dict) -> None:
        if ev["type"] == "start":
            print(f"question: {ev['question']}  (search: {search.name})")
        elif ev["type"] == "step":
            who = ev["served_by"] if ev["served_by"] != "tool" else ev["model"]
            d = ev["detail"]
            info = {
                "planner": lambda: "; ".join(d["queries"]),
                "searcher": lambda: f"{d['query']} -> {len(d['results'])} results",
                "triage": lambda: f"#{d['id']} relevant={d['relevant']} ({d['source_type']})",
                "reader": lambda: f"#{d['id']} {d['fact_type']}: {d['key_fact'][:70]}",
                "writer": lambda: "answer ready",
            }[ev["agent"]]()
            print(f"  {ev['agent']:<8} [{who:<7}] ${ev['cost_usd']:.5f}  {info}")
        elif ev["type"] == "done":
            t = ev["totals"]
            print(f"\n{ev['answer']}\n")
            for c in ev["citations"]:
                print(f"  [{c['n']}] {c['title']} - {c['url']}")
            print(
                f"\n{t['student_calls']}/{t['llm_calls']} LLM calls answered by the student; "
                f"${t['cost_usd']:.4f} vs ${t['baseline_usd']:.4f} all-Ultra ({t['savings_pct']:.0%} saved)"
            )

    try:
        scout.run(args.question, show, mode="client")
    except Exception as e:  # noqa: BLE001
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
