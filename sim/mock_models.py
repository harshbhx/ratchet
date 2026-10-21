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

A request the ticket and research worlds do not know gets the generic world's answer (sim/generic.py): free text, an
instance of its `response_format` schema, or a tool call when it is sent `tools` (`Completion.tool_calls`, finish
`tool_calls`). With `strict` (RATCHET_MOCK_STRICT=1) the teacher and the serving students raise KeyError for such a
request instead, as before the generic world (the gateway's 400 in mock mode); the trainer and the golden eval never do,
since what they read was stored already.

`MockEmbedder` plays the /v1/embeddings model: a deterministic unit vector per input (FR-1.51).

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
from dataclasses import replace
from typing import Any

import httpx

from ratchet.calibration import MIN_TASK_GOLDEN
from ratchet.confidence import STUDENT_SAMPLING
from ratchet.ledger import Version
from ratchet.nightschool.evaluate import cascade_eval
from ratchet.providers import Completion, Embeddings, GenParams, GenPolicy, approx_tokens
from ratchet.scout.prompts import PLAN_SYSTEM, WRITE_SYSTEM

from . import research
from .generic import GenericItem
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


def _effective(params: GenParams | None, response_format: dict[str, Any] | None) -> GenParams | None:
    """The parameters a mock model answers under: `params`, with an explicit `response_format` winning over its own (the
    `Provider` contract)."""
    if response_format is None:
        return params
    return replace(params or GenParams(), response_format=response_format)


def _pinned_answer(messages: list[dict[str, Any]], strict: bool = False) -> str | None:
    """Scout's planner and writer prompts (pinned to the teacher). A writer request whose facts the mock cannot read
    (not JSON, nested too deeply to parse, or the wrong shape) gets the generic world's answer, or with `strict` raises
    KeyError, like any request the mock world does not know (the gateway's 400 in mock mode)."""
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
            if strict:
                raise KeyError("writer request without readable facts") from e
    return None


class MockTeacher:
    """Plays Nemotron 3 Ultra: always right on labels, slow, reasoning tokens billed. Like Token Factory, it reports them
    in `completion_tokens_details.reasoning_tokens` (`Completion.reasoning_tokens`), included in `out_tokens`. Sent
    `tools`, it calls one when the generic world says so (`tool_calls`, finish `tool_calls`, no content). `strict`
    (RATCHET_MOCK_STRICT=1) answers only the demo's own requests (KeyError otherwise)."""

    def __init__(self, reasoning_tokens: int = 300, latency_ms: float = 2400.0, strict: bool = False):
        self.reasoning_tokens = reasoning_tokens
        self.latency_ms = latency_ms
        self.strict = strict

    def complete(self, messages, model, n=1, temperature=0.0, logprobs=False, response_format=None, params=None, **_):
        text = _pinned_answer(messages, self.strict)
        calls = None
        if text is None:
            item = lookup(messages, _effective(params, response_format), strict=self.strict)
            text = item.answer(item.labels)
            if isinstance(item, GenericItem):
                calls = item.calls(item.labels, "teacher")
        if calls is not None:  # a tool call has no content; its arguments are its output
            text, finish, out = "", "tool_calls", approx_tokens(json.dumps(calls))
        else:
            text, finish = _generate(text, params)
            out = approx_tokens(text)
        return [
            Completion(
                text=text,
                model=model,
                in_tokens=approx_tokens(json.dumps(messages)),
                out_tokens=out + self.reasoning_tokens,
                latency_ms=self.latency_ms,
                finish_reason=finish,
                reasoning_tokens=self.reasoning_tokens,
                tool_calls=calls,
            )
        ] * n


class MockStudent:
    """Plays a LoRA-tuned Nemotron 3 Nano 4B served by vLLM. On a generic request it learns per template as on a ticket
    (`skill`); a wrong answer to a schema changes enum values first, and to a tool request picks another tool or reason
    (`tool_calls`, finish `tool_calls`). `strict` (RATCHET_MOCK_STRICT=1) raises KeyError for anything but the demo's
    own requests."""

    PRIOR = 0.35  # untuned zero-shot skill
    CURVE = 6.0  # examples to reach ~63% of the way to the ceiling
    CEIL = {"easy": 0.985, "hard": 0.86}

    def __init__(
        self, name: str, knowledge: dict[str, Any], corrupted: bool = False, base_url: str = STAGING, strict: bool = False
    ):
        self.name = name
        self.knowledge = knowledge  # {"templates": {template: unique examples}, "texts": set}
        self.corrupted = corrupted
        self.base_url = base_url
        self.strict = strict
        self.calls = 0

    def skill(self, t: Item) -> float:
        if t.text in self.knowledge["texts"]:
            return self.CEIL["easy"]  # trained on this exact request
        n = self.knowledge["templates"].get(t.template, 0)
        learned = self.CEIL[t.difficulty] * (1 - math.exp(-n / self.CURVE))
        return max(self.PRIOR, learned)

    def complete(self, messages, model, n=1, temperature=0.0, logprobs=False, response_format=None, params=None, **_):
        t = lookup(messages, _effective(params, response_format), strict=self.strict)
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
                labels = dict(right) if ok or not keys else {**right, keys[0]: wrong[keys[0]]}
                prob = 0.80 + 0.17 * s.random()
            elif confidently_wrong:
                labels = dict(wrong)
                prob = 0.62 + 0.3 * s.random()  # overlaps with correct answers: calibration can't fully separate
            else:
                labels = {k: s.choice(space[k]) for k in keys}
                prob = 0.35 + 0.3 * s.random()
            calls = t.calls(labels, f"{self.name}|{i}|{self.calls}") if isinstance(t, GenericItem) else None
            if calls is not None:  # a tool call: no content, so no content tokens to list
                text, finish, toks = "", "tool_calls", approx_tokens(json.dumps(calls))
            else:
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
                    logprobs_content=_tokens(text, lp, top) if logprobs and calls is None else None,
                    tool_calls=calls,
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
    `policy` is the router's `GenPolicy`, so the golden eval sends the student what production would; `min_task_n` is
    RATCHET_MIN_TASK_GOLDEN and `per_task` RATCHET_PER_TASK_CALIBRATION, as `LiveEvaluator` takes them; `strict`
    (RATCHET_MOCK_STRICT=1) goes to the students it serves, never to training or the golden eval, which read rows
    already stored."""

    def __init__(
        self,
        corrupt_on_deploy: set[str] | None = None,
        clock: Callable[[], float] = time.monotonic,
        policy: GenPolicy | None = None,
        min_task_n: int = MIN_TASK_GOLDEN,
        strict: bool = False,
        per_task: bool = True,
    ):
        self.knowledge: dict[str, dict[str, Any]] = {}
        self.corrupt_on_deploy = corrupt_on_deploy or set()
        self.clock = clock
        self.policy = policy or GenPolicy()
        self.min_task_n = min_task_n  # RATCHET_MIN_TASK_GOLDEN: calibration rows a task needs for its own threshold
        self.per_task = per_task  # RATCHET_PER_TASK_CALIBRATION: False writes no per-task records (v0.4's evals)
        self.strict = strict
        self.outage_until: float | None = None  # on `clock`; None = never down

    def student_outage(self, seconds: float) -> float:
        """Take the production endpoint down for `seconds` from now (demo fault injection); returns when it comes back,
        on `clock`. Shadow candidates run on staging and are not affected."""
        self.outage_until = self.clock() + seconds
        return self.outage_until

    # Trainer
    def train(self, train_rows: list[dict[str, Any]], base_model: str, name: str) -> str:
        """Counts each row's template, found as a model would see the request: its messages and its stored conditions
        (`row["params"]`: a schema or tools put a generic row in that sub-world). Never raises on a row the demo did not
        write; it lands in the generic world."""
        counts: Counter[str] = Counter()
        texts: set[str] = set()
        for row in train_rows:
            t = lookup(row["messages"][:-1], GenParams.from_stored(row.get("params")))
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
            sample, golden_rows, samples=3, target_precision=0.97, min_task_n=self.min_task_n, per_task=self.per_task
        )

    # Resolver: shadow runs on staging; canary/live run on the production endpoint.
    def resolve(self, version: Version):
        production = version.state in ("canary", "live")
        if production and self.outage_until is not None and self.clock() < self.outage_until:
            return DownStudent(), version.adapter_uri
        corrupted = version.adapter_uri in self.corrupt_on_deploy and production
        student = MockStudent(
            version.adapter_uri,
            self.knowledge[version.adapter_uri],
            corrupted,
            PRODUCTION if production else STAGING,
            strict=self.strict,
        )
        return student, version.adapter_uri


MOCK_EMBEDDING_DIMS = 256  # the mock embeddings model's vector length when a request sets no `dimensions`


class MockEmbedder:
    """Plays the /v1/embeddings model (FR-1.51): a deterministic unit vector per input, seeded from SHA-256 of its text
    (a token array: of its JSON), so the same input always gets the same vector and different inputs almost orthogonal
    ones. `dimensions` keeps the first that many values of the full vector (at least 256 long) and scales them back to
    length 1, as OpenAI's shortened embeddings do. Prompt tokens are counted at about 4 characters a token (a token
    array: its length). No network, no failures, no state; the gateway refuses a request for more than `max_values`
    floats, so the public demo stays responsive."""

    base_url = "mock://embeddings"
    default_dims = MOCK_EMBEDDING_DIMS
    max_values = 1 << 20  # floats per request (inputs x dimensions), so one request cannot tie up the shared demo

    def __init__(self, latency_ms: float = 20.0) -> None:
        self.latency_ms = latency_ms  # simulated

    @staticmethod
    def vector(item: str | list[int], dims: int = MOCK_EMBEDDING_DIMS) -> list[float]:
        text = item if isinstance(item, str) else json.dumps(item)
        rng = random.Random(hashlib.sha256(text.encode("utf-8", "surrogatepass")).digest())
        full = [rng.gauss(0.0, 1.0) for _ in range(max(dims, MOCK_EMBEDDING_DIMS))]
        values = full[:dims]
        norm = math.sqrt(sum(v * v for v in values)) or 1.0
        return [v / norm for v in values]

    def embed(self, inputs: list[Any], model: str, dimensions: int | None = None) -> Embeddings:
        dims = dimensions or MOCK_EMBEDDING_DIMS
        tokens = sum(approx_tokens(i) if isinstance(i, str) else len(i) for i in inputs)
        return Embeddings([self.vector(i, dims) for i in inputs], model, tokens, self.latency_ms)
