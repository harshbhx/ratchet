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
from .record import DEFAULT_DIR, RecordError, record, write
from .search import MockSearch, TavilySearch


def record_main(argv: list[str], client: httpx.Client | None = None) -> int:
    """`python -m ratchet.scout record "<question>" --base-url URL`: save one run of a gateway as a recording that the
    gateway plays back (FR-10.40). Writes nothing and exits 1 for a run that errors, does not finish or is a simulation."""
    ap = argparse.ArgumentParser(prog="python -m ratchet.scout record", description="Record one Scout run for playback")
    ap.add_argument("question")
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--judge-code", default=os.environ.get("RATCHET_SCOUT_JUDGE_CODE") or None)
    ap.add_argument("--out", default=DEFAULT_DIR)
    ap.add_argument("--allow-mock", action="store_true", help="keep a run from a mock-mode gateway (labelled as a simulation)")
    args = ap.parse_args(argv)
    try:
        rec = record(args.base_url, args.question, client=client, judge_code=args.judge_code, allow_mock=args.allow_mock)
        path = write(rec, args.out)
    except RecordError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"recorded {rec['mode']} run, {rec['duration_ms'] / 1000:.1f} s, {len(rec['events'])} events -> {path}")
    return 0


def main(argv: list[str] | None = None, client: httpx.Client | None = None) -> int:
    """Returns the exit code. `client` replaces the HTTP client that talks to the gateway (tests only)."""
    if argv and argv[0] == "record":
        return record_main(argv[1:], client)
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
                "searcher": lambda: (
                    f"{d['query']} -> search unavailable ({d['unavailable']})"
                    if "unavailable" in d
                    else f"{d['query']} -> {len(d['results'])} results"
                ),
                "triage": lambda: f"#{d['id']} relevant={d['relevant']} ({d['source_type']})",
                "reader": lambda: f"#{d['id']} {d['fact_type']}: {d['key_fact'][:70]}",
                "writer": lambda: "answer ready",
            }[ev["agent"]]()
            print(f"  {ev['agent']:<8} [{who:<7}] ${ev['cost_usd']:.5f}  {info}")
        elif ev["type"] == "done":
            t = ev["totals"]
            search_state = ev.get("search") or {}
            if search_state.get("available") is False:
                what = "with partial web sources" if search_state.get("partial") else "without web sources"
                print(f"warning: search unavailable ({search_state.get('reason')}); answered {what}", file=sys.stderr)
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
