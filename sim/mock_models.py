"""Mock teacher, student, trainer and evaluator for the offline simulation.

The mock student's skill on a request template (a ticket or a Scout research call) grows with the number of teacher examples
it was trained on (a saturating learning curve), capped lower for "hard" templates.
When it does not know a request, its samples sometimes agree on the same wrong answer
with high confidence: the dangerous case that audits and shadow mode exist to catch.

The fleet can also take the production endpoint down for a while (`MockFleet.student_outage`, the demo's
`POST /ratchet/demo/student-outage`), so the student circuit breaker can be shown opening and closing again.

The mock models honour `n`, `stop` (the answer ends before the first stop string) and `max_tokens` (cut at about 4
characters per token, `finish_reason` `length`). Ticket and research answers are short and contain no stop string, so
the simulation is unaffected. Asked for logprobs, the mock student also lists its tokens (`_tokens`), so the gateway can
return them (FR-1.14); the mock teacher lists none.

This is a simulation for demos and tests. Real numbers come from live mode.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import time
from collections import Counter
from collections.abc import Callable
from typing import Any

import httpx

from ratchet.confidence import STUDENT_SAMPLING
from ratchet.ledger import Version
from ratchet.nightschool.evaluate import cascade_eval
from ratchet.providers import Completion, GenParams, GenPolicy, approx_tokens
from ratchet.scout.prompts import PLAN_SYSTEM, WRITE_SYSTEM

from . import research
from .world import Item, lookup

# The mock endpoints, as `base_url`s: the router keeps one student breaker per endpoint (ratchet/router.py).
PRODUCTION = "mock://production"  # canary and live versions
STAGING = "mock://staging"  # shadow candidates and the golden eval


def _rng(*parts: Any) -> random.Random:
    h = hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()
    return random.Random(int(h[:16], 16))


def _tokens(text: str, logprob: float, top: int) -> list[dict[str, Any]]:
    """`text` as `approx_tokens(text)` tokens of about 4 characters (the last takes the rest), each with `logprob`, in
    OpenAI's `logprobs.content` shape, so they spell the text exactly. With `top` > 0 (the client's `top_logprobs`),
    each token lists itself as its one alternative."""
    count = approx_tokens(text)
    pieces = [text[i * 4 : (i + 1) * 4] for i in range(count - 1)]
    pieces.append(text[(count - 1) * 4 :])
    return [
        {
            "token": p,
            "logprob": logprob,
            "bytes": None,
            "top_logprobs": [{"token": p, "logprob": logprob, "bytes": None}] if top > 0 else [],
        }
        for p in pieces
    ]


def _generate(text: str, params: GenParams | None) -> tuple[str, str]:
    """What a model under `params` returns of the answer it would write, and why it stopped: the text before the first
    stop string (`stop`), then at most `max_tokens` tokens at about 4 characters each (`length`)."""
    if params is None:
        return text, "stop"
    if params.stop:
        cuts = [i for i in (text.find(s) for s in params.stop if s) if i >= 0]
        if cuts:
            text = text[: min(cuts)]
    if params.max_tokens is not None and approx_tokens(text) > params.max_tokens:
        return text[: params.max_tokens * 4], "length"
    return text, "stop"


def _pinned_answer(messages: list[dict[str, Any]]) -> str | None:
    """Scout's planner and writer prompts (pinned to the teacher). A writer request whose facts the mock cannot read
    (not JSON, nested too deeply to parse, or the wrong shape) raises KeyError, like any request the mock world does not
    know (the gateway's 400 in mock mode)."""
    system = messages[0]["content"] if messages and messages[0]["role"] == "system" else ""
    user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    if not isinstance(user, str):
        return None
    if system == PLAN_SYSTEM:
        return research.plan_answer(user.removeprefix("Question: ").strip())
    if system == WRITE_SYSTEM:
        try:
            return research.write_answer(json.loads(user).get("facts", []))
        except (ValueError, AttributeError, TypeError, RecursionError) as e:
            raise KeyError("writer request without readable facts") from e
    return None


class MockTeacher:
    """Plays Nemotron 3 Ultra: always right on labels, slow, reasoning tokens billed. Like Token Factory, it reports them
    in `completion_tokens_details.reasoning_tokens` (`Completion.reasoning_tokens`), included in `out_tokens`."""

    def __init__(self, reasoning_tokens: int = 300, latency_ms: float = 2400.0):
        self.reasoning_tokens = reasoning_tokens
        self.latency_ms = latency_ms

    def complete(self, messages, model, n=1, temperature=0.0, logprobs=False, response_format=None, params=None, **_):
        text = _pinned_answer(messages)
        if text is None:
            item = lookup(messages)
            text = item.answer(item.labels)
        text, finish = _generate(text, params)
        return [
            Completion(
                text=text,
                model=model,
                in_tokens=approx_tokens(json.dumps(messages)),
                out_tokens=approx_tokens(text) + self.reasoning_tokens,
                latency_ms=self.latency_ms,
                finish_reason=finish,
                reasoning_tokens=self.reasoning_tokens,
            )
        ] * n


class MockStudent:
    """Plays a LoRA-tuned Nemotron 3 Nano 4B served by vLLM."""

    PRIOR = 0.35  # untuned zero-shot skill
    CURVE = 6.0  # examples to reach ~63% of the way to the ceiling
    CEIL = {"easy": 0.985, "hard": 0.86}

    def __init__(self, name: str, knowledge: dict[str, Any], corrupted: bool = False, base_url: str = STAGING):
        self.name = name
        self.knowledge = knowledge  # {"templates": {template: unique examples}, "texts": set}
        self.corrupted = corrupted
        self.base_url = base_url
        self.calls = 0

    def skill(self, t: Item) -> float:
        if t.text in self.knowledge["texts"]:
            return self.CEIL["easy"]  # trained on this exact request
        n = self.knowledge["templates"].get(t.template, 0)
        learned = self.CEIL[t.difficulty] * (1 - math.exp(-n / self.CURVE))
        return max(self.PRIOR, learned)

    def complete(self, messages, model, n=1, temperature=0.0, logprobs=False, response_format=None, params=None, **_):
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
        top = (params.top_logprobs or 0) if params is not None else 0
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
            text, finish = _generate(t.answer(labels), params)
            toks = approx_tokens(text)
            lp = math.log(prob)
            out.append(
                Completion(
                    text=text,
                    model=model,
                    in_tokens=approx_tokens(json.dumps(messages)),
                    out_tokens=toks,
                    latency_ms=280 + 60 * s.random(),
                    token_logprobs=[lp] * toks if logprobs else [],
                    finish_reason=finish,
                    logprobs_content=_tokens(text, lp, top) if logprobs else None,
                )
            )
        return out


class DownStudent:
    """The production endpoint during a demo outage: every call fails the way an unreachable endpoint does."""

    base_url = PRODUCTION

    def complete(self, messages, model, n=1, temperature=0.0, logprobs=False, response_format=None, params=None, **_):
        raise httpx.ConnectError("the mock student endpoint is down (demo outage)")


class MockFleet:
    """Holds trained mock adapters; acts as trainer, evaluator and student resolver. `clock` times demo outages;
    `policy` is the router's `GenPolicy`, so the golden eval sends the student what production would. `calibration`,
    `cert_target` and `cert_delta` go to `cascade_eval` (certified precision, ADR 0026; `Settings.cert_policy()`)."""

    def __init__(
        self,
        corrupt_on_deploy: set[str] | None = None,
        clock: Callable[[], float] = time.monotonic,
        policy: GenPolicy | None = None,
        calibration: str = "point",
        cert_target: float = 0.95,
        cert_delta: float = 0.05,
    ):
        self.knowledge: dict[str, dict[str, Any]] = {}
        self.corrupt_on_deploy = corrupt_on_deploy or set()
        self.clock = clock
        self.policy = policy or GenPolicy()
        self.calibration, self.cert_target, self.cert_delta = calibration, cert_target, cert_delta
        self.outage_until: float | None = None  # on `clock`; None = never down

    def student_outage(self, seconds: float) -> float:
        """Take the production endpoint down for `seconds` from now (demo fault injection); returns when it comes back,
        on `clock`. Shadow candidates run on staging and are not affected."""
        self.outage_until = self.clock() + seconds
        return self.outage_until

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

        def sample(prompt, n, params=None):
            gen = self.policy.student(GenParams.from_stored(params))
            return student.complete(prompt, adapter_uri, n=n, params=gen, **STUDENT_SAMPLING)

        return cascade_eval(
            sample,
            golden_rows,
            samples=3,
            target_precision=0.97,
            calibration=self.calibration,
            cert_target=self.cert_target,
            cert_delta=self.cert_delta,
        )

    # Resolver: shadow runs on staging; canary/live run on the production endpoint.
    def resolve(self, version: Version):
        production = version.state in ("canary", "live")
        if production and self.outage_until is not None and self.clock() < self.outage_until:
            return DownStudent(), version.adapter_uri
        corrupted = version.adapter_uri in self.corrupt_on_deploy and production
        student = MockStudent(
            version.adapter_uri, self.knowledge[version.adapter_uri], corrupted, PRODUCTION if production else STAGING
        )
        return student, version.adapter_uri
