"""One lookup over every mock workload: support tickets, Scout's research calls and, for anything else, the generic world.

Every mock item exposes the same interface, which is all the mock models need:
`text`, `template`, `difficulty`, `task`, `params`, `labels`, `label_space`, `fragile`, `answer(labels)`, `messages()`.
"""

from __future__ import annotations

import random
from typing import Any, Protocol

from ratchet.providers import GenParams

from . import generic, research, tickets


class Item(Protocol):
    text: str
    template: str
    difficulty: str
    task: str
    # The generation parameters the request is sent with (`response_format`, `tools`); None for tickets and research.
    params: GenParams | None

    @property
    def labels(self) -> dict[str, Any]: ...

    @property
    def label_space(self) -> dict[str, list[Any]]: ...

    @property
    def fragile(self) -> bool: ...

    def answer(self, labels: dict[str, Any]) -> str: ...

    def messages(self) -> list[dict[str, Any]]: ...


def lookup(messages: list[dict[str, Any]], params: GenParams | None = None, *, strict: bool = False) -> Item:
    """The mock item for a request: the ticket or research item its last user message names, else the generic world's
    item for its messages and `params` (`sim.generic.item`), so it never raises for a request the gateway accepts. With
    `strict` (RATCHET_MOCK_STRICT=1) it raises KeyError for anything but a ticket or research text, as before the generic
    world: the gateway's 400 in mock mode."""
    user = next((m.get("content") for m in reversed(messages) if m.get("role") == "user"), None)
    if isinstance(user, str):
        if user in tickets.REGISTRY:
            return tickets.REGISTRY[user]
        if user in research.REGISTRY:
            return research.REGISTRY[user]
    if strict:
        if not isinstance(user, str):  # no user message, or content parts only a multimodal teacher would read
            raise KeyError("no plain-text user message")
        raise KeyError(user[:80])
    return generic.item(messages, params)


WORKLOADS = ("tickets", "research", "mixed", "schema", "tools")


class MixedStream:
    """Half support tickets, half Scout research calls, as one company might send both."""

    def __init__(self, seed: int = 7, drift_day: int | None = 5):
        self.rng = random.Random(seed + 1)
        self.tickets = tickets.TicketStream(seed=seed, drift_day=drift_day)
        self.research = research.ResearchStream(seed=seed, drift_day=drift_day)

    def sample(self, day: int):
        return (self.tickets if self.rng.random() < 0.5 else self.research).sample(day)


def make_stream(workload: str = "tickets", seed: int = 7, drift_day: int | None = 5):
    """The request stream of a workload: `tickets`, `research`, `mixed` (half each), `schema` (structured outputs,
    `sim.generic.SchemaStream`) or `tools` (support requests with the tool catalogue, `sim.generic.ToolStream`). Each has
    its own seeded RNG, so one never moves another's draws."""
    if workload == "tickets":
        return tickets.TicketStream(seed=seed, drift_day=drift_day)
    if workload == "research":
        return research.ResearchStream(seed=seed, drift_day=drift_day)
    if workload == "mixed":
        return MixedStream(seed=seed, drift_day=drift_day)
    if workload == "schema":
        return generic.SchemaStream(seed=seed, drift_day=drift_day)
    if workload == "tools":
        return generic.ToolStream(seed=seed, drift_day=drift_day)
    raise ValueError(f"unknown workload {workload!r}; expected one of {WORKLOADS}")


def night_task(workload: str) -> str | None:
    """Tickets-only runs train on ticket-triage (as before); other workloads train on every task."""
    return "ticket-triage" if workload == "tickets" else None
