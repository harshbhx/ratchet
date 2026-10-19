"""System prompts and message builders for Scout's agents.

The simulator builds its mock requests with these same functions, so a mock teacher or
student can look a request up by its exact user text.
"""

from __future__ import annotations

import json
from typing import Any

SOURCE_TYPES = ["docs", "paper", "blog", "forum", "news", "other"]
FACT_TYPES = ["definition", "number", "date", "name", "none"]

PLAN_SYSTEM = (
    "You are the planner in a research team. Given a question, write 3 short web search queries "
    "that together find authoritative sources. Reply with JSON only: "
    '{"queries": ["...", "...", "..."]}'
)

TRIAGE_SYSTEM = (
    "You triage one web search result for a research question. A result is relevant only if it is "
    "likely to help answer the question. Reply with JSON only: "
    '{"relevant": true or false, "source_type": one of ' + json.dumps(SOURCE_TYPES) + ', "reason": one short sentence}'
)

READ_SYSTEM = (
    "You read one source excerpt for a research question. Use only the excerpt. Reply with JSON only: "
    '{"answers_question": true or false, "fact_type": one of '
    + json.dumps(FACT_TYPES)
    + ', "key_fact": the single most useful fact from the excerpt, closely paraphrased, or "" if none}'
)

WRITE_SYSTEM = (
    "You write the final answer for a research team. Use only the numbered facts given. Write 2 to 4 "
    "plain sentences and cite facts as [n]. If the facts do not answer the question, say so. "
    'Reply with JSON only: {"answer": "...", "used": [n, ...]}'
)


def _msgs(system: str, user: Any) -> list[dict[str, str]]:
    content = user if isinstance(user, str) else json.dumps(user, ensure_ascii=False)
    return [{"role": "system", "content": system}, {"role": "user", "content": content}]


def plan_messages(question: str) -> list[dict[str, str]]:
    return _msgs(PLAN_SYSTEM, f"Question: {question}")


def triage_messages(question: str, title: str, url: str, snippet: str) -> list[dict[str, str]]:
    return _msgs(TRIAGE_SYSTEM, {"question": question, "title": title, "url": url, "snippet": snippet})


def read_messages(question: str, title: str, url: str, excerpt: str) -> list[dict[str, str]]:
    return _msgs(READ_SYSTEM, {"question": question, "title": title, "url": url, "excerpt": excerpt})


def write_messages(question: str, facts: list[dict[str, Any]]) -> list[dict[str, str]]:
    return _msgs(WRITE_SYSTEM, {"question": question, "facts": facts})


# Suggested questions shown in the UI. In mock mode these are the only ones the simulated
# world can answer (sim/research.py holds their facts and search results).
EXAMPLE_QUESTIONS = [
    "What is LoRA fine-tuning?",
    "What is knowledge distillation?",
    "How does a model cascade cut LLM costs?",
    "What is the Wilson score interval?",
    "What is PagedAttention in vLLM?",
    "What is a Mamba state-space model?",
    "What is self-consistency decoding?",
    "What is model collapse?",
]
