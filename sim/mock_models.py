"""Mock teacher, student, trainer and evaluator for the offline simulation.

The mock student's skill on a request template (a ticket or a Scout research call) grows with the number of teacher examples
it was trained on (a saturating learning curve), capped lower for "hard" templates.
When it does not know a request, its samples sometimes agree on the same wrong answer
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

from ratchet.confidence import STUDENT_SAMPLING
from ratchet.ledger import Version
from ratchet.nightschool.evaluate import cascade_eval
from ratchet.providers import Completion, approx_tokens
from ratchet.scout.prompts import PLAN_SYSTEM, WRITE_SYSTEM

from . import research
from .world import Item, lookup


def _rng(*parts: Any) -> random.Random:
    h = hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()
    return random.Random(int(h[:16], 16))


def _pinned_answer(messages: list[dict[str, Any]]) -> str | None:
    """Scout's planner and writer prompts (pinned to the teacher). A writer request whose facts the mock cannot read
    raises KeyError, like any request the mock world does not know (the gateway's 400 in mock mode)."""
    system = messages[0]["content"] if messages and messages[0]["role"] == "system" else ""
    user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    if not isinstance(user, str):
        return None
    if system == PLAN_SYSTEM:
        return research.plan_answer(user.removeprefix("Question: ").strip())
    if system == WRITE_SYSTEM:
        try:
            return research.write_answer(json.loads(user).get("facts", []))
        except (ValueError, AttributeError, TypeError) as e:
            raise KeyError("writer request without readable facts") from e
    return None


class MockTeacher:
    """Plays Nemotron 3 Ultra: always right on labels, slow, reasoning tokens billed."""

    def __init__(self, reasoning_tokens: int = 300, latency_ms: float = 2400.0):
        self.reasoning_tokens = reasoning_tokens
        self.latency_ms = latency_ms

    def complete(self, messages, model, n=1, temperature=0.0, logprobs=False, response_format=None):
        text = _pinned_answer(messages)
        if text is None:
            item = lookup(messages)
            text = item.answer(item.labels)
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

    def skill(self, t: Item) -> float:
        if t.text in self.knowledge["texts"]:
            return self.CEIL["easy"]  # trained on this exact request
        n = self.knowledge["templates"].get(t.template, 0)
        learned = self.CEIL[t.difficulty] * (1 - math.exp(-n / self.CURVE))
        return max(self.PRIOR, learned)

    def complete(self, messages, model, n=1, temperature=0.0, logprobs=False, response_format=None):
        t = lookup(messages)
        self.calls += 1
        r = _rng(self.name, t.text)  # same request -> same "understanding"
        knows = r.random() < self.skill(t)
        if self.corrupted and t.fragile:
            knows = False  # bad deploy: whole slices broken
        right, space = t.labels, t.label_space
        keys = list(space)
        wrong = {k: r.choice([v for v in space[k] if v != right[k]]) for k in keys}
        confidently_wrong = (not knows) and (self.corrupted or r.random() < 0.15)
        out = []
        for i in range(n):
            s = _rng(self.name, t.text, i, self.calls if temperature > 0 else 0)
            if knows:
                ok = s.random() < 0.97
                labels = dict(right) if ok else {**right, keys[0]: wrong[keys[0]]}
                prob = 0.80 + 0.17 * s.random()
            elif confidently_wrong:
                labels = dict(wrong)
                prob = 0.62 + 0.3 * s.random()  # overlaps with correct answers: calibration can't fully separate
            else:
                labels = {k: s.choice(space[k]) for k in keys}
                prob = 0.35 + 0.3 * s.random()
            text = t.answer(labels)
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
            return student.complete(prompt, adapter_uri, n=n, **STUDENT_SAMPLING)

        return cascade_eval(sample, golden_rows, samples=3, target_precision=0.97)

    # Resolver: shadow runs on staging; canary/live run on the production endpoint.
    def resolve(self, version: Version):
        corrupted = version.adapter_uri in self.corrupt_on_deploy and version.state in ("canary", "live")
        return MockStudent(version.adapter_uri, self.knowledge[version.adapter_uri], corrupted), version.adapter_uri
