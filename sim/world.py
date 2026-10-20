"""One lookup over every mock workload (support tickets and Scout's research calls).

Every mock item exposes the same interface, which is all the mock models need:
`text`, `template`, `difficulty`, `task`, `labels`, `label_space`, `fragile`, `answer(labels)`.
"""

from __future__ import annotations

from typing import Any, Protocol

from . import research, tickets


class Item(Protocol):
    text: str
    template: str
    difficulty: str
    task: str

    @property
    def labels(self) -> dict[str, Any]: ...

    @property
    def label_space(self) -> dict[str, list[Any]]: ...

    @property
    def fragile(self) -> bool: ...

    def answer(self, labels: dict[str, Any]) -> str: ...


def lookup(messages: list[dict[str, str]]) -> Item:
    """The mock item for the last user message. KeyError for anything else (the gateway's 400 in mock mode)."""
    user = next((m.get("content") for m in reversed(messages) if m.get("role") == "user"), None)
    if not isinstance(user, str):  # no user message, or content parts only a multimodal teacher would read
        raise KeyError("no plain-text user message")
    if user in tickets.REGISTRY:
        return tickets.REGISTRY[user]
    if user in research.REGISTRY:
        return research.REGISTRY[user]
    raise KeyError(user[:80])


WORKLOADS = ("tickets", "research", "mixed")


class MixedStream:
    """Half support tickets, half Scout research calls, as one company might send both."""

    def __init__(self, seed: int = 7, drift_day: int | None = 5):
        import random

        self.rng = random.Random(seed + 1)
        self.tickets = tickets.TicketStream(seed=seed, drift_day=drift_day)
        self.research = research.ResearchStream(seed=seed, drift_day=drift_day)

    def sample(self, day: int):
        return (self.tickets if self.rng.random() < 0.5 else self.research).sample(day)


def make_stream(workload: str = "tickets", seed: int = 7, drift_day: int | None = 5):
    if workload == "tickets":
        return tickets.TicketStream(seed=seed, drift_day=drift_day)
    if workload == "research":
        return research.ResearchStream(seed=seed, drift_day=drift_day)
    if workload == "mixed":
        return MixedStream(seed=seed, drift_day=drift_day)
    raise ValueError(f"unknown workload {workload!r}; expected one of {WORKLOADS}")


def night_task(workload: str) -> str | None:
    """Tickets-only runs train on ticket-triage (as before); other workloads train on every task."""
    return "ticket-triage" if workload == "tickets" else None
