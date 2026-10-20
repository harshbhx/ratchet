"""Shared test doubles and ledger builders.

- `RecordingTrainer`, `RecordingEvaluator`, `RecordingProvider`: stand-ins for Night School's trainer and evaluator
  and for any OpenAI-compatible model. Each records every call, so a test can assert on exactly what crossed the
  boundary (which rows reached the trainer, which golden rows were scored, what the teacher was asked).
- `FakeClock`: a clock the test moves by hand (`Guard(..., clock=FakeClock())`), never `time.sleep`.
- `FakeOpenAIServer`, `RecordingRun`, `AppTransport`: the live-mode boundary without the network. An OpenAI-compatible
  server (Token Factory, or vLLM with runtime LoRA loading) behind `httpx.MockTransport`; a stand-in for
  `subprocess.run`; and a transport that sends an `httpx.Client`'s requests to the gateway app in-process.
- `_version`, `_audit`, `_shadow`, `_examples`, `_repeat`: put versions, teacher audits, shadow answers and teacher
  examples into a ledger through its public writers (`create_version` + `set_version`, `log_request`, `add_example`),
  the way the router and the release gate write them, so tests exercise the real queries.

Import with `from fakes import ...`: pytest puts tests/ on `sys.path` because it holds conftest.py.
"""

from __future__ import annotations

import copy
import json
import subprocess
import threading
from collections.abc import Callable, Collection, Mapping
from types import MappingProxyType
from typing import Any, NamedTuple

import httpx
from fastapi.testclient import TestClient

from ratchet import licenses
from ratchet.ledger import Ledger
from ratchet.providers import Completion
from ratchet.release import CANARY_STEPS

OPEN_TEACHER = "nvidia/Nemotron-3-Ultra-550b-a55b"  # distillable (OpenMDW-1.1), the default teacher
CLOSED_TEACHER = "openai/gpt-4o"  # refused by licenses.require_distillable
BASE_MODEL = "base-model"

Messages = list[dict[str, Any]]


# ---------- Night School ----------
class TrainCall(NamedTuple):
    rows: list[dict[str, Any]]
    base_model: str
    name: str


class EvalCall(NamedTuple):
    adapter_uri: str
    golden_rows: list[dict[str, Any]]


class RecordingTrainer:
    """Trains nothing. Records the rows, base model and name of each call and returns `name` as the adapter, as the
    vLLM runtime-LoRA setup does (adapter name = model name)."""

    def __init__(self) -> None:
        self.calls: list[TrainCall] = []

    def train(self, train_rows: list[dict[str, Any]], base_model: str, name: str) -> str:
        self.calls.append(TrainCall(list(train_rows), base_model, name))
        return name


class RecordingEvaluator:
    """Returns `result_fn(adapter_uri, golden_rows)` (or a copy of a fixed mapping) and records each call."""

    def __init__(
        self, result_fn: Callable[[str, list[dict[str, Any]]], Mapping[str, Any]] | Mapping[str, Any] | None = None
    ) -> None:
        self.result_fn = result_fn if result_fn is not None else {}
        self.calls: list[EvalCall] = []

    def evaluate(self, adapter_uri: str, golden_rows: list[dict[str, Any]]) -> dict[str, Any]:
        self.calls.append(EvalCall(adapter_uri, list(golden_rows)))
        fn = self.result_fn
        return dict(fn(adapter_uri, golden_rows) if callable(fn) else fn)


# A golden result the gate always rejects (precision 0): no release is left in flight, so the next night trains again.
# Read-only, so no test can change it for the others.
REJECTED_EVAL: Mapping[str, Any] = MappingProxyType(
    {"precision": 0.0, "coverage": 0.0, "threshold": 1.01, "golden_agreement": 0.0, "golden_n": 0}
)


# ---------- models ----------
class ProviderCall(NamedTuple):
    messages: Messages
    model: str
    kwargs: dict[str, Any]


_POSITIONAL = ("n", "temperature", "logprobs", "response_format")  # Provider.complete's optional parameters, in order


class RecordingProvider:
    """An OpenAI-compatible model that answers `text` (or `text(messages, model)`) and records every call.

    It returns `n` identical choices, so a student built from it is unanimous and its confidence depends on `logprob`
    (-0.05 is a token probability of about 0.95: confident). Like a real endpoint, it returns token logprobs only when
    asked (`logprobs=True`); `logprob=None` plays an endpoint that never returns them. A list for `text` or `logprob`
    scripts the choices instead: choice i gets item i (cycling), so `RecordingProvider([A, A, B], [log(.6), log(.9),
    log(.99)])` is a 2-of-3 majority with known token probabilities. Calls to a model in `raise_on` are recorded and
    then raise `exc(message)`: `httpx.ConnectError` by default, the way an unreachable endpoint fails, or another error
    such as `exc=httpx.ReadTimeout` for a slow one. Thread-safe, because Scout calls the router from several threads."""

    def __init__(
        self,
        text: str | list[str] | Callable[[Messages, str], str],
        logprob: float | list[float | None] | None = -0.05,
        in_tokens: int = 10,
        out_tokens: int = 5,
        raise_on: Collection[str] = frozenset(),
        latency_ms: float = 1.0,
        exc: Callable[[str], BaseException] = httpx.ConnectError,
    ) -> None:
        if (isinstance(text, list) and not text) or (isinstance(logprob, list) and not logprob):
            raise ValueError("a scripted text or logprob list needs at least one item")
        self.text = text
        self.logprob = logprob
        self.in_tokens = in_tokens
        self.out_tokens = out_tokens
        self.raise_on = set(raise_on)
        self.latency_ms = latency_ms
        self.exc = exc
        self.calls: list[ProviderCall] = []
        self._lock = threading.Lock()

    def answer(self, messages: Messages, model: str) -> str:
        """What this provider says to `messages` (lets a test compute the expected output of a recorded call); the
        first choice when the choices are scripted."""
        if callable(self.text):
            return self.text(messages, model)
        return _nth(self.text, 0)

    def complete(self, messages: Messages, model: str, *args: Any, **kwargs: Any) -> list[Completion]:
        if len(args) > len(_POSITIONAL):
            raise TypeError(f"complete() takes at most {len(_POSITIONAL)} optional positional arguments")
        kwargs = {**dict(zip(_POSITIONAL, args, strict=False)), **kwargs}
        with self._lock:
            self.calls.append(ProviderCall(copy.deepcopy(messages), model, dict(kwargs)))
        if model in self.raise_on:
            raise self.exc(f"{model} is down")
        said = self.text(messages, model) if callable(self.text) else self.text
        out = []
        for i in range(int(kwargs.get("n") or 1)):
            lp = _nth(self.logprob, i)
            logprobs = [lp] * self.out_tokens if kwargs.get("logprobs") and lp is not None else []
            out.append(Completion(_nth(said, i), model, self.in_tokens, self.out_tokens, self.latency_ms, logprobs))
        return out


def _nth(value: Any, i: int) -> Any:
    """Item i of a scripted list (cycling), or the value itself for every choice."""
    return value[i % len(value)] if isinstance(value, list) else value


# ---------- the live-mode boundary: HTTP endpoints and commands ----------
class HttpCall(NamedTuple):
    method: str
    url: str
    headers: dict[str, str]  # lower-case names
    json: Any  # the parsed JSON body, or None


class FakeOpenAIServer:
    """An OpenAI-compatible server behind `httpx.MockTransport`: Token Factory, or vLLM with runtime LoRA loading.

    `POST .../chat/completions` returns `n` choices with `text` (or `text(body)`), token logprobs only when the body
    asks for them (`logprob=None` plays a server that never returns them), and usage of `in_tokens` and `out_tokens`
    per choice. `POST .../load_lora_adapter` answers `load(body)` as `(status, text)`, 200 by default. `GET .../models`
    lists `models`. Anything else is a 404, as on a server without that route. Every request is recorded as an
    `HttpCall`; `calls_to(suffix)` picks the ones to one route. Thread-safe, because Scout calls from several threads.
    Pass `server.transport` to `OpenAICompatProvider(transport=...)`, `Runtime(transports=...)` or `httpx.Client`."""

    def __init__(
        self,
        text: str | Callable[[dict[str, Any]], str] = "ok",
        logprob: float | None = -0.05,
        *,
        in_tokens: int = 10,
        out_tokens: int = 5,
        load: Callable[[dict[str, Any]], tuple[int, str]] | None = None,
        models: Collection[str] = (),
    ) -> None:
        self.text = text
        self.logprob = logprob
        self.in_tokens = in_tokens
        self.out_tokens = out_tokens
        self.load = load or (lambda body: (200, ""))
        self.models = list(models)
        self.calls: list[HttpCall] = []
        self._lock = threading.Lock()

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def calls_to(self, suffix: str) -> list[HttpCall]:
        with self._lock:
            return [c for c in self.calls if httpx.URL(c.url).path.endswith(suffix)]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        with self._lock:
            self.calls.append(HttpCall(request.method, str(request.url), dict(request.headers), body))
        path = request.url.path
        if request.method == "POST" and path.endswith("/chat/completions"):
            return httpx.Response(200, json=self.completion(body))
        if request.method == "POST" and path.endswith("/load_lora_adapter"):
            status, text = self.load(body)
            return httpx.Response(status, text=text)
        if request.method == "GET" and path.endswith("/models"):
            return httpx.Response(200, json={"object": "list", "data": [{"id": m, "object": "model"} for m in self.models]})
        return httpx.Response(404, json={"detail": "Not Found"})

    def completion(self, body: dict[str, Any]) -> dict[str, Any]:
        text = self.text(body) if callable(self.text) else self.text
        n = int(body.get("n") or 1)
        lp = None
        if body.get("logprobs") and self.logprob is not None:
            lp = {"content": [{"token": "t", "logprob": self.logprob}] * self.out_tokens}
        choice = {"message": {"role": "assistant", "content": text}, "logprobs": lp, "finish_reason": "stop"}
        return {
            "object": "chat.completion",
            "model": body["model"],
            "choices": [{"index": i, **choice} for i in range(n)],
            "usage": {"prompt_tokens": self.in_tokens, "completion_tokens": self.out_tokens * n},
        }


class RecordingRun:
    """Stands in for `subprocess.run`: records each command (argv list) and its keyword arguments and runs nothing.
    `on_call(argv)` runs first, to look at the world at that moment or to raise as a failing command would."""

    def __init__(self, on_call: Callable[[list[str]], None] | None = None) -> None:
        self.on_call = on_call
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    @property
    def commands(self) -> list[str]:
        return [" ".join(argv) for argv, _ in self.calls]

    def __call__(self, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((list(args), dict(kwargs)))
        if self.on_call is not None:
            self.on_call(list(args))
        return subprocess.CompletedProcess(list(args), 0)


class AppTransport(httpx.BaseTransport):
    """Sends each request of an `httpx.Client` to an ASGI app in-process, through Starlette's `TestClient`, so code
    that takes an `httpx.Client` (the Scout CLI's `http_caller`) talks to the real gateway with no network. Recent
    Starlette builds `TestClient` on its httpx fork (httpx2) when installed, so it is not always an `httpx.Client`."""

    def __init__(self, app: Any) -> None:
        self.client = TestClient(app)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")}
        r = self.client.request(request.method, str(request.url), headers=headers, content=request.read())
        keep = [(k, v) for k, v in r.headers.multi_items() if k.lower() not in ("content-length", "content-encoding")]
        return httpx.Response(r.status_code, headers=keep, content=r.content, request=request)


class FakeClock:
    """A clock the test moves by hand: `clock = FakeClock(); Guard(..., clock=clock); clock.advance(3601)`."""

    def __init__(self, start: float = 1_800_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> float:
        self.now += seconds
        return self.now


# ---------- ledger builders ----------
_STAGE_PCT = {"live": 100, "canary": CANARY_STEPS[0]}  # what the release gate sets on entering each state


def _version(
    led: Ledger,
    state: str = "candidate",
    eval: Mapping[str, Any] | None = None,
    pct: int | None = None,
    *,
    adapter_uri: str | None = None,
    train_examples: int = 100,
    base_model: str = BASE_MODEL,
) -> int:
    """Create a version and move it to `state` with `set_version`, as the gate does (which also starts its stage).

    `pct` defaults to what the gate sets: 100 for live, the first canary step for canary, 0 otherwise. Unless
    `adapter_uri` is given, the adapter is `student-v<n>` with n = the number of versions so far + 1, the rule Night
    School uses; n is the new id only while no version row has been deleted."""
    uri = adapter_uri or f"student-v{len(led.versions()) + 1}"
    vid = led.create_version(base_model, uri, train_examples, f"dataset-{uri}", dict(eval or {}), state="candidate")
    led.set_version(vid, state, _STAGE_PCT.get(state, 0) if pct is None else pct)
    return vid


def _every(i: int, k: int) -> bool:
    """True for the 1st, (k+1)th, (2k+1)th ... item; never when k is 0."""
    return bool(k) and i % k == 0


def _audit(led: Ledger, vid: int, n: int, agree_every: int = 0, *, task: str = "t") -> None:
    """Log `n` student-served requests from `vid` that the teacher audited.

    Every `agree_every`-th audit, starting with the first, disagrees (0 = all agree): `_audit(led, v, 20, 10)` gives
    18 of 20, `_audit(led, v, 20, 2)` gives 10 of 20."""
    for i in range(n):
        led.log_request(
            task=task,
            served_by="student",
            student_version=vid,
            confidence=0.95,
            escalated=0,
            audited=1,
            audit_agree=int(not _every(i, agree_every)),
            cost_usd=0.0,
            baseline_cost_usd=0.0,
            latency_ms=1.0,
        )


def _shadow(
    led: Ledger,
    vid: int,
    n: int,
    serve_every: int = 0,
    agree_every: int = 0,
    *,
    live_vid: int | None = None,
    live_escalate_every: int = 0,
    unarbitrated_every: int = 0,
    task: str = "t",
) -> None:
    """Log `n` requests that shadow version `vid` scored silently.

    Every `serve_every`-th request, starting with the first, is one the shadow would have escalated (0 = it would
    serve all). Among the answers it would serve, every `agree_every`-th disagrees with the reference (0 = all agree),
    counted over served answers only so the two patterns never line up by accident. The live student (`live_vid`)
    serves every request except every `live_escalate_every`-th, which goes to the teacher (0 = it serves all).
    Among the answers it would serve, every `unarbitrated_every`-th is a disagreement the teacher could not arbitrate
    (`shadow_agree` NULL; 0 = none); it takes precedence over `agree_every` on the same answer."""
    served = 0
    for i in range(n):
        serve = not _every(i, serve_every)
        agree = None
        if serve:
            agree = None if _every(served, unarbitrated_every) else int(not _every(served, agree_every))
            served += 1
        escalate = _every(i, live_escalate_every)
        led.log_request(
            task=task,
            served_by="teacher" if escalate else "student",
            student_version=live_vid,
            confidence=0.5 if escalate else 0.95,
            escalated=int(escalate),
            audited=0,
            shadow_version=vid,
            shadow_serve=int(serve),
            shadow_agree=agree,
            cost_usd=0.0,
            baseline_cost_usd=0.0,
            latency_ms=1.0,
        )


class StoredExample(NamedTuple):
    hash: str
    split: str  # as stored ('train' | 'golden'); dataset.rows applies the FREQUENT rule on top
    task: str
    messages: Messages


def _examples(
    led: Ledger,
    train: int = 0,
    golden: int = 0,
    *,
    task: str = "default",
    teacher: str = OPEN_TEACHER,
    prefix: str = "request",
) -> list[StoredExample]:
    """Store exactly `train` train-split and `golden` golden-split teacher examples.

    The split is fixed by each request's hash, so candidate requests ('<prefix> 0', '<prefix> 1', ...) are tried
    on a scratch ledger with the same `golden_fraction` and only the ones that land in a split still short of its
    count are stored. Requests the ledger already holds are skipped, so storing never bumps an existing `seen`.
    The license fields come from the registry (`unknown` for an unregistered teacher)."""
    lic = licenses.lookup(teacher)
    lic_name, lic_url = (lic.name, lic.url) if lic else ("unknown", "")
    probe = Ledger(":memory:", golden_fraction=led.golden_fraction)
    stored = {r["hash"] for r in led.examples()}
    want = {"train": train, "golden": golden}
    have = {"train": 0, "golden": 0}
    out: list[StoredExample] = []
    for i in range(1000 + 100 * (train + golden)):
        if have == want:
            break
        msgs = [{"role": "user", "content": f"{prefix} {i}"}]
        probe.add_example(task, msgs, "", teacher, lic_name, lic_url)
        row = probe.examples()[-1]
        if row["hash"] in stored or have[row["split"]] >= want[row["split"]]:
            continue
        assert led.add_example(task, msgs, f"teacher answer to {prefix} {i}", teacher, lic_name, lic_url), msgs
        have[row["split"]] += 1
        out.append(StoredExample(row["hash"], row["split"], task, msgs))
    if have != want:
        raise AssertionError(f"could not fill {want} at golden_fraction={led.golden_fraction}; got {have}")
    return out


def _repeat(led: Ledger, ex: StoredExample, times: int) -> None:
    """The same request answered again `times` more times: `seen` grows, nothing new is stored."""
    for _ in range(times):
        assert not led.add_example(ex.task, ex.messages, "again", OPEN_TEACHER, "OpenMDW-1.1", "")
