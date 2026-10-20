"""Label vocabularies of the built-in tasks.

The built-in task specs (`tasks.REGISTRY`) refuse a label outside these lists, and the prompts that ask for the labels
(`sim/tickets.py` for support tickets, `scout/prompts.py` for Scout's triage and reader agents) list them. Both import
them from here, so a prompt can never offer a label its task refuses. They live in the core package because `ratchet/`
must not import `sim/`.
"""

from __future__ import annotations

# ticket-triage (sim/tickets.py)
TICKET_CATEGORIES: tuple[str, ...] = ("billing", "technical", "account", "shipping", "refund", "security")
TICKET_PRIORITIES: tuple[str, ...] = ("low", "medium", "high", "urgent")

# source-triage and page-read (Scout, scout/prompts.py)
SOURCE_TYPES: tuple[str, ...] = ("docs", "paper", "blog", "forum", "news", "other")
FACT_TYPES: tuple[str, ...] = ("definition", "number", "date", "name", "none")
