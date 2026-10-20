"""Synthetic support tickets for the demo workload (category + priority triage).

Fully synthetic so the repo has no data-licensing questions. Each ticket comes from a
template; some templates are deliberately ambiguous ("hard"). From day `drift_day`,
a new product launch adds templates the student has never seen (distribution drift).
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from typing import Any

from ratchet.vocab import TICKET_CATEGORIES, TICKET_PRIORITIES

# The ticket-triage task's vocabularies (ratchet/vocab.py), as lists: the prompt offers exactly the labels the task
# accepts, and the mock student draws from them in this order.
CATEGORIES = list(TICKET_CATEGORIES)
PRIORITIES = list(TICKET_PRIORITIES)

SYSTEM = (
    "You triage customer support tickets. Reply with JSON only: "
    '{"category": one of '
    + json.dumps(CATEGORIES)
    + ', "priority": one of '
    + json.dumps(PRIORITIES)
    + ', "reply": a one-sentence first response to the customer}'
)

NAMES = ["Aarav", "Maya", "Liam", "Sofia", "Kenji", "Fatima", "Noah", "Priya", "Lucas", "Amara"]
PRODUCTS = ["Pro plan", "Team plan", "mobile app", "desktop app", "API", "smart speaker", "headphones"]

# (template_id, category, priority, difficulty, text)
TEMPLATES: list[tuple[str, str, str, str, str]] = [
    ("b1", "billing", "medium", "easy", "Hi, I was charged twice for my {product} this month. Order {oid}. Can you fix it?"),
    ("b2", "billing", "low", "easy", "Where can I download the invoice for order {oid}? Need it for accounting. - {name}"),
    (
        "b3",
        "billing",
        "high",
        "hard",
        "My card was declined for the {product} renewal but the money still left my account. Order {oid}.",
    ),
    ("b4", "billing", "medium", "easy", "Can I switch my {product} from monthly to yearly billing? Thanks, {name}"),
    ("t1", "technical", "high", "easy", "The {product} crashes every time I open it since the last update. Please help, {name}."),
    ("t2", "technical", "urgent", "easy", "Your {product} is returning 500 errors for all our requests. Production is down!"),
    (
        "t3",
        "technical",
        "medium",
        "hard",
        "The {product} feels slow lately, sometimes it takes ages to sync. Not sure if it's my wifi.",
    ),
    ("t4", "technical", "low", "easy", "Is there a dark mode in the {product}? Couldn't find it in settings."),
    ("a1", "account", "medium", "easy", "I can't remember the email I used to sign up for {product}. My name is {name}."),
    ("a2", "account", "low", "easy", "How do I change the username on my {product} account?"),
    (
        "a3",
        "account",
        "high",
        "hard",
        "I got logged out and now it says my {product} account doesn't exist. I have years of data!",
    ),
    ("s1", "shipping", "medium", "easy", "My {product} order {oid} was supposed to arrive yesterday. Where is it?"),
    ("s2", "shipping", "low", "easy", "Can I change the delivery address for order {oid}? - {name}"),
    (
        "s3",
        "shipping",
        "high",
        "hard",
        "Tracking for {oid} says delivered but nothing came and my neighbour didn't get it either.",
    ),
    ("r1", "refund", "medium", "easy", "I'd like to return my {product} (order {oid}), it's not what I expected."),
    ("r2", "refund", "medium", "hard", "I cancelled the {product} within the trial but was billed anyway. I want my money back."),
    ("r3", "refund", "low", "easy", "How long does a refund for order {oid} usually take?"),
    ("x1", "security", "urgent", "easy", "Someone changed my {product} password and email. I did not do this. Help now!"),
    (
        "x2",
        "security",
        "high",
        "hard",
        "I got an email asking me to confirm my {product} login with a code, is that really from you?",
    ),
    ("x3", "security", "medium", "easy", "How do I turn on two-factor authentication for {product}?"),
]

# Arrives with a product launch on `drift_day`: unseen wording, the student must escalate then learn.
DRIFT_TEMPLATES: list[tuple[str, str, str, str, str]] = [
    ("n1", "technical", "high", "easy", "The new Ratchet Watch won't pair with my phone at all. Tried resetting. {name}"),
    ("n2", "shipping", "medium", "easy", "Pre-ordered the Ratchet Watch ({oid}), any update on when it ships?"),
    ("n3", "billing", "medium", "hard", "I was charged the launch price for the Ratchet Watch but the promo said 20% off. {oid}"),
]


REPLIES = {
    "billing": "Sorry about the billing trouble, I'm checking your charges now.",
    "technical": "Thanks for reporting this, our engineers are looking into it.",
    "account": "I'll help you get back into your account right away.",
    "shipping": "Let me check where your order is and get back to you shortly.",
    "refund": "I've started looking at your refund request.",
    "security": "We're securing your account now; please don't share any codes.",
}


@dataclass(frozen=True)
class Ticket:
    text: str
    template: str
    category: str
    priority: str
    difficulty: str

    task = "ticket-triage"
    params = None  # sent without generation parameters (sim/world.py `Item.params`)

    def messages(self) -> list[dict[str, str]]:
        return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": self.text}]

    # Generic item interface used by the mock models (see sim/world.py).
    @property
    def labels(self) -> dict[str, Any]:
        return {"category": self.category, "priority": self.priority}

    @property
    def label_space(self) -> dict[str, list[Any]]:
        return {"category": CATEGORIES, "priority": PRIORITIES}

    @property
    def fragile(self) -> bool:
        """Slices a corrupted deploy breaks (fault injection)."""
        return self.category in ("refund", "security", "billing")

    def answer(self, labels: dict[str, Any]) -> str:
        return json.dumps({"category": labels["category"], "priority": labels["priority"], "reply": REPLIES[labels["category"]]})


REGISTRY: dict[str, Ticket] = {}


class TicketStream:
    def __init__(self, seed: int = 7, drift_day: int | None = 5, drift_share: float = 0.2):
        self.rng = random.Random(seed)
        self.drift_day = drift_day
        self.drift_share = drift_share

    def sample(self, day: int) -> Ticket:
        pool = TEMPLATES
        if self.drift_day is not None and day >= self.drift_day and self.rng.random() < self.drift_share:
            pool = DRIFT_TEMPLATES
        tid, cat, pri, diff, tmpl = self.rng.choice(pool)
        text = tmpl.format(
            name=self.rng.choice(NAMES),
            product=self.rng.choice(PRODUCTS),
            oid=f"#{self.rng.randint(10000, 99999)}",
        )
        t = Ticket(text, tid, cat, pri, diff)
        REGISTRY[text] = t
        return t


def lookup(messages: list[dict[str, str]]) -> Ticket:
    user = next(m["content"] for m in reversed(messages) if m["role"] == "user")
    return REGISTRY[user]
