"""Mock teacher, student, trainer and evaluator for the offline simulation.

The mock student's skill on a ticket template grows with the number of teacher examples
it was trained on (a saturating learning curve), capped lower for "hard" templates.
When it does not know a ticket, its samples sometimes agree on the same wrong answer
with high confidence: the dangerous case that audits and shadow mode exist to catch.

This is a simulation for demos and tests. Real numbers come from live mode.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from collections import Counter
from typing import Any

from ratchet.ledger import Version
from ratchet.nightschool.evaluate import cascade_eval
from ratchet.providers import Completion, approx_tokens

from .tickets import CATEGORIES, PRIORITIES, Ticket, lookup

REPLIES = {
    "billing": "Sorry about the billing trouble, I'm checking your charges now.",
    "technical": "Thanks for reporting this, our engineers are looking into it.",
    "account": "I'll help you get back into your account right away.",
    "shipping": "Let me check where your order is and get back to you shortly.",
    "refund": "I've started looking at your refund request.",
    "security": "We're securing your account now; please don't share any codes.",
}


def _answer(category: str, priority: str) -> str:
    return json.dumps({"category": category, "priority": priority, "reply": REPLIES[category]})


def _rng(*parts: Any) -> random.Random:
    h = hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()
    return random.Random(int(h[:16], 16))


class MockTeacher:
    """Plays Nemotron 3 Ultra: always right on labels, slow, reasoning tokens billed."""

    def __init__(self, reasoning_tokens: int = 300, latency_ms: float = 2400.0):
        self.reasoning_tokens = reasoning_tokens
        self.latency_ms = latency_ms

    def complete(self, messages, model, n=1, temperature=0.0, logprobs=False, response_format=None):
        t = lookup(messages)
        text = _answer(t.category, t.priority)
        return [
            Completion(
                text=text,
                model=model,
                in_tokens=approx_tokens(json.dumps(messages)),
                out_tokens=approx_tokens(text) + self.reasoning_tokens,
                latency_ms=self.latency_ms,
            )
        ] * n


class MockStudent:
    """Plays a LoRA-tuned Nemotron 3 Nano 4B served by vLLM."""

    PRIOR = 0.35  # untuned zero-shot skill
    CURVE = 6.0  # examples to reach ~63% of the way to the ceiling
    CEIL = {"easy": 0.985, "hard": 0.86}

    def __init__(self, name: str, knowledge: dict[str, Any], corrupted: bool = False):
        self.name = name
        self.knowledge = knowledge  # {"templates": {template: unique examples}, "texts": set}
        self.corrupted = corrupted
        self.calls = 0

    def skill(self, t: Ticket) -> float:
        if t.text in self.knowledge["texts"]:
            return self.CEIL["easy"]  # trained on this exact request
        n = self.knowledge["templates"].get(t.template, 0)
        learned = self.CEIL[t.difficulty] * (1 - math.exp(-n / self.CURVE))
        return max(self.PRIOR, learned)

    def complete(self, messages, model, n=1, temperature=0.0, logprobs=False, response_format=None):
        t = lookup(messages)
        self.calls += 1
        r = _rng(self.name, t.text)  # same ticket -> same "understanding"
        knows = r.random() < self.skill(t)
        if self.corrupted and t.category in ("refund", "security", "billing"):
            knows = False  # bad deploy: whole categories broken
        wrong_cat = r.choice([c for c in CATEGORIES if c != t.category])
        wrong_pri = r.choice([p for p in PRIORITIES if p != t.priority])
        confidently_wrong = (not knows) and (self.corrupted or r.random() < 0.15)
        out = []
        for i in range(n):
            s = _rng(self.name, t.text, i, self.calls if temperature > 0 else 0)
            if knows:
                ok = s.random() < 0.97
                cat, pri = (t.category, t.priority) if ok else (wrong_cat, t.priority)
                prob = 0.80 + 0.17 * s.random()
            elif confidently_wrong:
                cat, pri = wrong_cat, wrong_pri
                prob = 0.62 + 0.3 * s.random()  # overlaps with correct answers: calibration can't fully separate
            else:
                cat = s.choice(CATEGORIES)
                pri = s.choice(PRIORITIES)
                prob = 0.35 + 0.3 * s.random()
            text = _answer(cat, pri)
            toks = approx_tokens(text)
            out.append(
                Completion(
                    text=text,
                    model=model,
                    in_tokens=approx_tokens(json.dumps(messages)),
                    out_tokens=toks,
                    latency_ms=280 + 60 * s.random(),
                    token_logprobs=[math.log(prob)] * toks,
                )
            )
        return out


class MockFleet:
    """Holds trained mock adapters; acts as trainer, evaluator and student resolver."""

    def __init__(self, corrupt_on_deploy: set[str] | None = None):
        self.knowledge: dict[str, dict[str, Any]] = {}
        self.corrupt_on_deploy = corrupt_on_deploy or set()

    # Trainer
    def train(self, train_rows: list[dict[str, Any]], base_model: str, name: str) -> str:
        counts: Counter[str] = Counter()
        texts: set[str] = set()
        for row in train_rows:
            t = lookup(row["messages"][:-1])
            counts[t.template] += 1
            texts.add(t.text)
        self.knowledge[name] = {"templates": dict(counts), "texts": texts}
        return name

    # Evaluator (runs on the staging copy, which is always the correct adapter)
    def evaluate(self, adapter_uri: str, golden_rows: list[dict[str, Any]]) -> dict[str, Any]:
        student = MockStudent(adapter_uri, self.knowledge[adapter_uri])

        def sample(prompt, n):
            return student.complete(prompt, adapter_uri, n=n, temperature=0.7, logprobs=True)

        return cascade_eval(sample, golden_rows, samples=3, target_precision=0.97)

    # Resolver: shadow runs on staging; canary/live run on the production endpoint.
    def resolve(self, version: Version):
        corrupted = version.adapter_uri in self.corrupt_on_deploy and version.state in ("canary", "live")
        return MockStudent(version.adapter_uri, self.knowledge[version.adapter_uri], corrupted), version.adapter_uri
