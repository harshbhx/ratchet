"""Day-1 check: confirm the Token Factory pieces Ratchet depends on actually work, and probe the rest of the live setup.

    NEBIUS_API_KEY=... python scripts/day1_check.py                       # the teacher check (the default)
    python scripts/day1_check.py [CHECK ...] [--json PATH] [--adapter-path PATH] [--adapter-name NAME] [--query TEXT]
    CHECK: teacher | choices | reasoning | student | tavily | cli | all

- teacher (the default): the model IDs exist, the teacher answers, logprobs come back, JSON mode works, and the teacher
  accepts the generation parameters the gateway sends it (`max_tokens` at RATCHET_TEACHER_MAX_TOKENS, `stop`, `seed`,
  `temperature`, `response_format`; ADR 0017). Costs a few cents.
- choices: one Ultra call with n=2 and logprobs, as an escalation asks for them (DAY1 row 10).
- reasoning: per listed Token Factory model, whether `chat_template_kwargs` (top-level or inside `extra_body`) and
  `reasoning_effort: none` are accepted, with status, reasoning tokens and latency. Facts only, no setting.
- student (RATCHET_STUDENT_BASE, RATCHET_STUDENT_KEY): loads `--adapter-path` at runtime twice (the second is vLLM's 400
  "already"), samples it as production does, compares the base model's and the adapter's answers, sends the thinking
  switch, asks for `student-v0` and runs the 404 through the router's own predicate (`resilience.adapter_missing`), then
  unloads. The adapter must be trained by train.py with its default --targets (the Mamba in_proj and out_proj included)
  and --rank at most the endpoint's --max-lora-rank (16): DAY1 row 4's `bash deploy/train_job.sh test` is that adapter.
- tavily (TAVILY_API_KEY): one basic search (1 credit). Never Extract.
- cli: the `nebius` flags the deploy scripts use, against the installed CLI's `--help` (warnings only, read-only).

Exit codes: 0 no check failed, 1 a check failed, 2 bad input or a missing prerequisite. `--json PATH` writes every
measured fact without keys, URLs or hosts; review it before committing it to the evidence pack (evidence/day1/).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple

import httpx

ROOT = Path(__file__).resolve().parents[1]
BASE = os.environ.get("RATCHET_TEACHER_BASE", "https://api.tokenfactory.nebius.com/v1").rstrip("/")
KEY = os.environ.get("NEBIUS_API_KEY", "")
# Every priced model in ratchet/config.py (DEFAULT_PRICES, except "student") must be here: test_contract.py checks it.
WANT = [
    "nvidia/Nemotron-3-Ultra-550b-a55b",
    "nvidia/nemotron-3-super-120b-a12b",
    "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
    "nvidia/Nemotron-3_5-Lightning",
]
# The teacher the gateway uses, and the output cap every teacher call carries (ratchet/config.py).
TEACHER = os.environ.get("RATCHET_TEACHER_MODEL", "") or WANT[0]
TEACHER_MAX_TOKENS = int(os.environ.get("RATCHET_TEACHER_MAX_TOKENS", "") or 8192)
# A client's parameters, as an escalation forwards them: a stop string the answer never contains, a seed, a temperature.
GEN_PARAMS = {"stop": ["<|day1-stop|>"], "seed": 7, "temperature": 0.2, "response_format": {"type": "json_object"}}
PROMPT = [
    {"role": "system", "content": 'Reply with JSON only: {"category": "billing|technical|account", "priority": "low|high"}'},
    {"role": "user", "content": "I was charged twice for my Pro plan this month."},
]

CHECKS = ("teacher", "choices", "reasoning", "student", "tavily", "cli")
# The Nemotron 3 thinking switch (model cards): sent raw, so a provider's field handling never hides what the server does.
THINKING_OFF = {"enable_thinking": False}
REASONING_VARIANTS: dict[str, dict[str, Any]] = {
    "default": {},
    "chat_template_kwargs": {"chat_template_kwargs": THINKING_OFF},
    "extra_body": {"extra_body": {"chat_template_kwargs": THINKING_OFF}},
    "reasoning_effort_none": {"reasoning_effort": "none"},
}
REASONING_MAX_TOKENS = 1024
MISSING_ADAPTER = "student-v0"  # Night School names versions student-v<n> from 1, so this one is never served
PRODUCTION_ADAPTER = re.compile(r"student-v\d+")
DEFAULT_ADAPTER_NAME = "day1-probe"
DEFAULT_QUERY = "Nemotron 3 Nano LoRA fine-tuning"
DEPLOY_SCRIPTS = ("deploy/student_endpoint.sh", "deploy/train_job.sh")
GENERATOR = "ratchet.day1_check/1"
NOTE = (
    "facts measured by scripts/day1_check.py; keys, URLs and hosts removed; list prices in ratchet/config.py stay "
    "unverified until RATCHET_PRICES_VERIFIED=1"
)
MODULES_NOTE = (
    "vLLM v0.18.0 names no modules on a successful load; a module it cannot use fails the load and the message lists "
    "them; a module it skips is only logged ('will be ignored'): read the endpoint's log in the Nebius console"
)
OUTCOMES = ("skipped", "ok", "warn", "fail")  # from best to worst
TAGS = {"skipped": "[skip]", "ok": "[ok]", "warn": "[warn]", "fail": "[FAIL]"}
TEACHER_SUMMARY = "Some checks failed: update ratchet/config.py before building on them."
SUMMARY = "Some checks failed: see the [FAIL] lines; docs/DAY1.md says what to do for each."


# ---------- shared helpers ----------
_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s\"'<>]*")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
MAX_TEXT = 300


def scrub(text: Any, secrets: Sequence[str] = (), hosts: Sequence[str] = (), limit: int = MAX_TEXT) -> str:
    """Text from an upstream (an error body, the nebius CLI's output) made safe to print and record: each secret of 4
    characters or more becomes [KEY], any URL [URL], each host [HOST], control characters spaces; at most `limit`."""
    out = str(text)
    for secret in sorted((s for s in secrets if s and len(s) >= 4), key=len, reverse=True):
        out = out.replace(secret, "[KEY]")
    out = _URL.sub("[URL]", out)
    for host in hosts:
        if host:
            out = re.sub(re.escape(host), "[HOST]", out, flags=re.IGNORECASE)
    out = _CONTROL.sub(" ", out).strip()
    return out if len(out) <= limit else out[: limit - 3] + "..."


def _host(url: str) -> str:
    try:
        return httpx.URL(url).host or ""
    except (httpx.InvalidURL, TypeError, ValueError):
        return ""


@dataclass
class Report:
    """What the run measured, per check, as plain JSON values. Every upstream string goes through `scrub` first."""

    secrets: tuple[str, ...] = ()
    hosts: tuple[str, ...] = ()
    checks: dict[str, dict[str, Any]] = field(default_factory=dict)
    model_ids: set[str] | None = None  # the teacher check's GET /models, reused by the reasoning check

    def section(self, name: str) -> dict[str, Any]:
        return self.checks.setdefault(name, {"outcome": "skipped"})

    def scrub(self, text: Any) -> str:
        return scrub(text, self.secrets, self.hosts)

    def line(self, outcome: str, text: str) -> None:
        print(f"{TAGS[outcome]} {self.scrub(text)}")

    def outcome(self, name: str, value: str) -> None:
        """Keep the worst outcome of a check: fail > warn > ok > skipped."""
        sec = self.section(name)
        if OUTCOMES.index(value) > OUTCOMES.index(sec["outcome"]):
            sec["outcome"] = value

    def failed(self) -> bool:
        return any(sec.get("outcome") == "fail" for sec in self.checks.values())


def failure(e: BaseException, report: Report) -> dict[str, Any]:
    """What may be recorded about a failed call: the class and status, and a scrubbed message. Never `str(e)`, because an
    httpx message carries the URL."""
    from ratchet.resilience import UpstreamError, describe_failure

    out: dict[str, Any] = {"error": describe_failure(e)}
    if isinstance(e, UpstreamError):
        out.update(status=e.status, type=e.type, code=e.code, param=e.param, message=report.scrub(e.message))
    elif isinstance(e, httpx.HTTPStatusError):
        out.update(status=e.response.status_code, message=report.scrub(e.response.text))
    return out


def _ratchet() -> None:
    """`python scripts/day1_check.py` puts scripts/ on sys.path, not the repo: add the repo root for `import ratchet`."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)


def _usage(data: Any) -> dict[str, Any]:
    """`completion_tokens` and `reasoning_tokens` of a raw chat body; reasoning None when not reported (not 0)."""
    usage = data.get("usage") if isinstance(data, dict) and isinstance(data.get("usage"), dict) else {}
    details = usage.get("completion_tokens_details")
    reasoning = details.get("reasoning_tokens") if isinstance(details, dict) else None
    completion = usage.get("completion_tokens")
    return {
        "completion_tokens": completion if isinstance(completion, int) else None,
        "reasoning_tokens": reasoning if isinstance(reasoning, int) and not isinstance(reasoning, bool) else None,
    }


def _choice(data: Any) -> dict[str, Any]:
    choices = data.get("choices") if isinstance(data, dict) else None
    return choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}


def _think_in(text: Any) -> bool:
    return isinstance(text, str) and ("<think>" in text or "</think>" in text)


# ---------- teacher (the default; unchanged requests and output) ----------
def check_teacher(c: httpx.Client, auth: dict[str, str], report: Report) -> bool:
    """Today's day-1 check, line for line: the model IDs, one call per listed model, then the generation parameters."""
    sec = report.section("teacher")
    ok = True

    ids = {m["id"] for m in c.get(f"{BASE}/models", headers=auth).json().get("data", [])}
    report.model_ids = ids
    for m in WANT:
        found = m in ids
        ok &= found
        print(f"[{'ok' if found else 'MISSING'}] model {m}")
    nvidia = sorted(i for i in ids if "emotron" in i.lower())
    if not all(m in ids for m in WANT):
        print("  NVIDIA models available:", nvidia)
    missing = [m for m in WANT if m not in ids]
    case = {m: i for m in missing for i in ids if i.lower() == m.lower()}
    for wanted, listed in case.items():
        print(
            f"[hint] {wanted} is listed as {listed} (case differs): set RATCHET_TEACHER_MODEL and fix DEFAULT_PRICES and "
            "licenses.REGISTRY"
        )
    sec["models"] = {"wanted": list(WANT), "missing": missing, "nvidia_listed": nvidia, "case_mismatch": case}
    per_model: dict[str, Any] = {}
    sec["per_model"] = per_model

    for model in WANT:
        if model not in ids:
            continue
        r = c.post(
            f"{BASE}/chat/completions",
            headers=auth,
            json={
                "model": model,
                "messages": PROMPT,
                "max_tokens": 400,
                "logprobs": True,
                "response_format": {"type": "json_object"},
            },
        )
        if r.status_code != 200:
            print(f"[FAIL] {model}: HTTP {r.status_code} {report.scrub(r.text[:200])}")
            per_model[model] = {"http": r.status_code, "error": report.scrub(r.text)}
            ok = False
            continue
        d = r.json()
        ch = d["choices"][0]
        lp = (ch.get("logprobs") or {}).get("content") or []
        text = ch["message"].get("content") or ""
        try:
            json.loads(text.split("</think>")[-1].strip().strip("`").removeprefix("json"))
            js = "json ok"
        except Exception:
            js = "json NOT parseable"
        # Only answers that end with finish_reason "stop" (or "tool_calls") are stored as training data (INV-16).
        finish = ch.get("finish_reason")
        print(f"[ok] {model}: {js}, logprobs={'yes' if lp else 'NO'}, finish_reason={finish}, usage={d.get('usage')}")
        per_model[model] = {
            "http": 200,
            "json": js == "json ok",
            "logprobs": bool(lp),
            "finish_reason": finish if isinstance(finish, str) else None,
            "usage": _usage(d),
        }
    if TEACHER in ids:
        # Every escalation carries max_tokens (8,192 by default) and the client's fields: a 4xx here would fail them all.
        body = {"model": TEACHER, "messages": PROMPT, "max_tokens": TEACHER_MAX_TOKENS, **GEN_PARAMS}
        r = c.post(f"{BASE}/chat/completions", headers=auth, json=body)
        if r.status_code != 200:
            print(
                f"[FAIL] {TEACHER} with max_tokens={TEACHER_MAX_TOKENS}, stop, seed: HTTP {r.status_code} "
                f"{report.scrub(r.text[:200])}"
            )
            print("  Lower RATCHET_TEACHER_MAX_TOKENS or narrow RATCHET_TEACHER_PARAMS until this passes.")
            sec["gen_params"] = {"http": r.status_code, "max_tokens": TEACHER_MAX_TOKENS, "error": report.scrub(r.text)}
            ok = False
        else:
            finish = r.json()["choices"][0].get("finish_reason")
            print(f"[ok] {TEACHER} accepts max_tokens={TEACHER_MAX_TOKENS}, stop, seed, temperature: finish_reason={finish}")
            sec["gen_params"] = {"http": 200, "max_tokens": TEACHER_MAX_TOKENS, "finish_reason": finish}
    report.outcome("teacher", "ok" if ok else "fail")
    return ok


# ---------- choices (DAY1 row 10) ----------
def check_choices(report: Report, s: Any, transport: httpx.BaseTransport | None) -> bool:
    """One escalation that asks for 2 choices with logprobs, sent as `Ratchet._ask_teacher` sends it, with no retry."""
    from ratchet.providers import GenParams, OpenAICompatProvider

    sec = report.section("choices")
    serve = s.gen_policy().plan(GenParams(logprobs=True), 2).serve
    sec.update(asked=2, temperature=serve.temperature, max_tokens=serve.max_tokens)
    provider = OpenAICompatProvider(BASE, KEY, timeout=s.teacher_timeout(), transport=transport)
    try:
        cs = provider.complete(PROMPT, TEACHER, n=2, temperature=serve.temperature, logprobs=bool(serve.logprobs), params=serve)
    except httpx.HTTPError as e:
        sec.update(failure(e, report))
        report.outcome("choices", "fail")
        report.line("fail", f"choices: {TEACHER} with n=2: {sec['error']} {sec.get('message', '')}")
        return False
    lps = [bool(c.logprobs_content) for c in cs]
    sec.update(
        returned=len(cs),
        logprobs_per_choice=lps,
        finish_reasons=[c.finish_reason for c in cs],
        usage={"in_tokens": cs[0].in_tokens, "out_tokens": cs[0].out_tokens, "reasoning_tokens": cs[0].reasoning_tokens},
        latency_ms=round(cs[0].latency_ms, 1),
    )
    if len(cs) < 2:
        report.outcome("choices", "warn")
        report.line(
            "warn",
            f"choices: the teacher returned {len(cs)} of 2 choices: escalated requests get fewer; set RATCHET_MAX_N=1 "
            "to refuse n > 1",
        )
    if not all(lps):
        report.outcome("choices", "warn")
        report.line(
            "warn",
            "choices: no logprobs: remove logprobs and top_logprobs from RATCHET_TEACHER_PARAMS; teacher-served choices "
            "then carry logprobs.content: null",
        )
    if len(cs) >= 2 and all(lps):
        report.outcome("choices", "ok")
        report.line("ok", f"choices: {TEACHER} returned 2 choices, each with token logprobs")
    return True


# ---------- reasoning (acceptance only) ----------
def _reasoning_cell(name: str, row: dict[str, Any]) -> str:
    """One variant in the printed line: `chat_template_kwargs 200 (reasoning 0)` or `reasoning_effort none 400`."""
    label = name.replace("_none", " none")
    if not row["accepted"]:
        return f"{label} {row['http'] if row['http'] is not None else row.get('error')}"
    tokens = row.get("reasoning_tokens")
    return f"{label} 200 (reasoning {tokens if tokens is not None else 'not reported'})"


def check_reasoning(c: httpx.Client, auth: dict[str, str], report: Report) -> bool:
    """Per listed model, four raw calls: no switch, `chat_template_kwargs`, the same inside `extra_body`, and
    `reasoning_effort: none`. Records what Token Factory does; NEMOTRON-REASONING-CONTROL maps it to settings."""
    sec = report.section("reasoning")
    per_model: dict[str, Any] = {}
    sec["per_model"] = per_model
    ids = report.model_ids
    if ids is None:
        r = c.get(f"{BASE}/models", headers=auth)
        if r.status_code != 200:
            sec.update(http=r.status_code, error=report.scrub(r.text))
            report.outcome("reasoning", "fail")
            report.line("fail", f"reasoning: GET /models: HTTP {r.status_code} {r.text}")
            return False
        ids = {m["id"] for m in r.json().get("data", [])}
        report.model_ids = ids
    ok = True
    for model in [m for m in WANT if m in ids]:
        rows: dict[str, Any] = {}
        for name, extra in REASONING_VARIANTS.items():
            body = {"model": model, "messages": PROMPT, "max_tokens": REASONING_MAX_TOKENS, "temperature": 0, **extra}
            t0 = time.perf_counter()
            try:
                r = c.post(f"{BASE}/chat/completions", headers=auth, json=body)
            except httpx.HTTPError as e:
                rows[name] = {"http": None, "accepted": False, "latency_ms": _ms(t0), **failure(e, report)}
                continue
            row: dict[str, Any] = {"http": r.status_code, "accepted": r.status_code == 200, "latency_ms": _ms(t0)}
            if r.status_code == 200:
                try:
                    data = r.json()
                except ValueError:
                    data = {}
                ch = _choice(data)
                message = ch.get("message") if isinstance(ch.get("message"), dict) else {}
                row.update(_usage(data))
                row.update(
                    finish_reason=ch.get("finish_reason") if isinstance(ch.get("finish_reason"), str) else None,
                    think_in_content=_think_in(message.get("content")),
                    reasoning_in_message=bool(message.get("reasoning_content") or message.get("reasoning")),
                )
            else:
                row["error"] = report.scrub(r.text)
            rows[name] = row
        per_model[model] = rows
        text = f"reasoning {model}: " + " | ".join(_reasoning_cell(n, rows[n]) for n in REASONING_VARIANTS)
        if not rows["default"]["accepted"]:
            report.outcome("reasoning", "fail")
            report.line("fail", text)
            ok = False
        elif not all(r["accepted"] for r in rows.values()) or rows["default"].get("reasoning_tokens") is None:
            report.outcome("reasoning", "warn")
            report.line("warn", text)
        else:
            report.outcome("reasoning", "ok")
            report.line("ok", text)
    return ok


# ---------- student ----------
class _Student(NamedTuple):
    report: Report
    sec: dict[str, Any]
    s: Any
    c: httpx.Client
    provider: Any
    headers: dict[str, str]
    name: str
    path: str


def _step(st: _Student, outcome: str, text: str) -> None:
    st.report.outcome("student", outcome)
    st.report.line(outcome, f"student: {text}")


def _load_hint(status: int | None, message: str) -> str:
    low = message.lower()
    if status == 404:
        return "the endpoint runs without VLLM_ALLOW_RUNTIME_LORA_UPDATING=True or --enable-lora"
    if "expected target modules" in low:
        return "vLLM refused a module train.py trained: the message lists the modules it expected and received"
    if "max_lora_rank" in low:
        return "train with --rank at most the endpoint's --max-lora-rank"
    return "see docs/DAY1.md row 6"


def student_load(st: _Student) -> bool:
    """Load the probe adapter, then load it again: vLLM v0.18.0 answers the second with 400 "... already been loaded"."""
    from ratchet import live

    try:
        r = live.load_adapter(st.s.student_base, st.s.student_api_key, st.name, st.path, client=st.c)
    except httpx.HTTPError as e:
        info = failure(e, st.report)
        st.sec["load"] = {"http": info.get("status"), "message": info.get("message"), **info}
        hint = _load_hint(info.get("status"), str(info.get("message") or ""))
        _step(st, "fail", f"loading {st.name} from {st.path}: {info['error']} {info.get('message', '')} ({hint})")
        return False
    st.sec["load"] = {"http": r.status_code, "message": st.report.scrub(r.text)}
    if r.status_code >= 400:
        _step(st, "warn", f"{st.name} was already loaded before this run (an earlier probe?); it is unloaded at the end")
    else:
        _step(st, "ok", f"loaded {st.name} from {st.path} at runtime")
    try:
        again = live.load_adapter(st.s.student_base, st.s.student_api_key, st.name, st.path, client=st.c)
    except httpx.HTTPError as e:
        st.sec["load_again"] = failure(e, st.report)
        _step(st, "fail", f"a second load of {st.name} raised {st.sec['load_again']['error']}: a reload would fail too")
        return True
    st.sec["load_again"] = {"http": again.status_code, "message": st.report.scrub(again.text)}
    if again.status_code == 400 and "already" in again.text.lower():
        _step(st, "ok", "a second load is vLLM v0.18.0's 400 'already loaded', which live.load_adapter accepts")
    else:
        _step(st, "warn", f"a second load answered {again.status_code}, not v0.18.0's 400: is the pinned image running?")
    st.sec["modules"] = None
    st.sec["modules_note"] = MODULES_NOTE
    print(f"  {MODULES_NOTE}")
    return True


def student_samples(st: _Student) -> None:
    """The adapter sampled as `Ratchet._sample` samples it: RATCHET_SAMPLES choices, temperature 0.7, logprobs on."""
    from ratchet.confidence import STUDENT_SAMPLING

    n = st.s.samples
    try:
        cs = st.provider.complete(PROMPT, st.name, n=n, params=st.s.gen_policy().plan(None).student, **STUDENT_SAMPLING)
    except httpx.HTTPError as e:
        info = failure(e, st.report)
        st.sec["samples"] = {"asked": n, **info}
        hint = "; raise RATCHET_STUDENT_TIMEOUT_S or turn the student's reasoning off" if info.get("status") == 504 else ""
        _step(st, "fail", f"samples: {info['error']} {info.get('message', '')}{hint}")
        return
    with_lp = sum(1 for c in cs if isinstance(c.logprobs_content, list) and c.logprobs_content)
    finishes = [c.finish_reason for c in cs]
    st.sec["samples"] = {
        "asked": n,
        "returned": len(cs),
        "with_logprobs": with_lp,
        "finish_reasons": finishes,
        "reasoning_tokens": [c.reasoning_tokens for c in cs],
        "think_in_content": [_think_in(c.text) for c in cs],
        "latency_ms": round(cs[0].latency_ms, 1) if cs else None,
    }
    if len(cs) < n or with_lp < len(cs):
        _step(st, "fail", f"samples: {len(cs)} of {n} choices, {with_lp} with token logprobs; the confidence score needs all")
        return
    if any(f != "stop" for f in finishes):
        _step(
            st,
            "warn",
            f"samples: finish_reason {finishes}: the student wrote past its token cap; it reasons by default until "
            "thinking is turned off",
        )
        return
    _step(st, "ok", f"samples: {n} choices with token logprobs in {st.sec['samples']['latency_ms']} ms")


def _one(st: _Student, model: str) -> tuple[Any, dict[str, Any]]:
    from ratchet.providers import GenParams

    try:
        c = st.provider.complete(PROMPT, model, n=1, temperature=0.0, logprobs=True, params=GenParams(max_tokens=64))[0]
    except httpx.HTTPError as e:
        return None, failure(e, st.report)
    return c, {
        "http": 200,
        "completion_tokens": c.out_tokens,
        "reasoning_tokens": c.reasoning_tokens,
        "think_in_content": _think_in(c.text),
        "finish_reason": c.finish_reason,
    }


def student_base_and_adapter(st: _Student, loaded: bool) -> None:
    """The base model must answer (DAY1 row 5); the adapter's answer should differ from it; the thinking switch."""
    base, st.sec["base"] = _one(st, st.s.student_base_model)
    if base is None:
        _step(st, "fail", f"the base model {st.s.student_base_model} did not answer: {st.sec['base']['error']}")
    else:
        _step(st, "ok", f"the base model {st.s.student_base_model} answers")
    if not loaded:
        for key in ("adapter", "adapter_effect", "thinking"):
            st.sec[key] = {"skipped": "the adapter did not load"}
        return
    adapter, st.sec["adapter"] = _one(st, st.name)
    if adapter is None:
        _step(st, "fail", f"the adapter {st.name} did not answer: {st.sec['adapter']['error']}")
    elif base is not None:
        effect = {"text_differs": adapter.text != base.text, "logprobs_differ": adapter.token_logprobs != base.token_logprobs}
        st.sec["adapter_effect"] = effect
        if not any(effect.values()):
            _step(
                st,
                "warn",
                "the adapter's answer and token logprobs equal the base model's at temperature 0; vLLM may have skipped "
                "its weights",
            )
        else:
            _step(st, "ok", "the adapter's answer differs from the base model's")
    body = {
        "model": st.name,
        "messages": PROMPT,
        "temperature": 0,
        "max_tokens": 512,
        "chat_template_kwargs": dict(THINKING_OFF),
    }
    t0 = time.perf_counter()
    try:
        r = st.c.post(f"{st.s.student_base.rstrip('/')}/chat/completions", headers=st.headers, json=body)
    except httpx.HTTPError as e:
        st.sec["thinking"] = {"latency_ms": _ms(t0), **failure(e, st.report)}
        _step(st, "warn", f"thinking switch: {st.sec['thinking']['error']}")
        return
    row: dict[str, Any] = {"http": r.status_code, "latency_ms": _ms(t0)}
    if r.status_code == 200:
        try:
            data = r.json()
        except ValueError:
            data = {}
        row.update(_usage(data))
        message = _choice(data).get("message")
        row["think_in_content"] = _think_in(message.get("content") if isinstance(message, dict) else None)
        st.sec["thinking"] = row
        _step(
            st,
            "ok",
            f"thinking switch accepted: {row['completion_tokens']} completion tokens (reasoning {row['reasoning_tokens']}), "
            f"<think> in content: {row['think_in_content']}",
        )
    else:
        row["error"] = st.report.scrub(r.text)
        st.sec["thinking"] = row
        _step(st, "warn", f"thinking switch refused: HTTP {r.status_code} {r.text}")


def student_missing_adapter(st: _Student) -> None:
    """A request for `student-v0`, judged by the router's own predicate: does a lost adapter get reloaded (FR-2.11)?"""
    from ratchet.confidence import STUDENT_SAMPLING
    from ratchet.resilience import UpstreamError, adapter_missing

    record: dict[str, Any] = {"model": MISSING_ADAPTER}
    st.sec["adapter_missing"] = record
    try:
        st.provider.complete(PROMPT, MISSING_ADAPTER, n=1, params=st.s.gen_policy().plan(None).student, **STUDENT_SAMPLING)
    except httpx.HTTPError as e:
        reloads = adapter_missing(e, MISSING_ADAPTER)
        if isinstance(e, UpstreamError):
            record.update(http=e.status, type=e.type, param=e.param, message=st.report.scrub(e.message))
        else:
            record.update(http=None, **failure(e, st.report))
        record["router_reloads"] = reloads
        if reloads:
            _step(
                st,
                "ok",
                f"adapter_missing ok: a request for {MISSING_ADAPTER} is a 404 that names it, so the router reloads a "
                "lost adapter (FR-2.11)",
            )
            return
        _step(
            st,
            "fail",
            f"adapter_missing: HTTP {record.get('http')} {record.get('message', '')}: after an endpoint restart every "
            "request would go to the teacher and the adapter would never reload: see DAY1 row 6",
        )
        return
    record.update(http=200, router_reloads=False)
    _step(st, "fail", "adapter_missing: HTTP 200: the endpoint served a model it should not have (a LoRA resolver?)")


def student_unload(st: _Student) -> None:
    from ratchet import live

    try:
        r = live.unload_adapter(st.s.student_base, st.s.student_api_key, st.name, client=st.c)
    except httpx.HTTPError as e:
        st.sec["unload"] = failure(e, st.report)
        _step(st, "warn", f"unload: {st.sec['unload']['error']}: the probe adapter stays loaded until the endpoint restarts")
        return
    st.sec["unload"] = {"http": r.status_code, "message": st.report.scrub(r.text)}
    if r.status_code == 200:
        _step(st, "ok", f"unloaded {st.name}")
    elif r.status_code == 404 and "cannot be found" in r.text.lower():
        _step(st, "ok", f"unload: {st.name} was already gone")
    else:
        _step(
            st,
            "warn",
            f"unload answered {r.status_code}: the probe adapter stays loaded until the endpoint restarts. It uses a slot "
            "but serves nothing",
        )


def check_student(
    c: httpx.Client, report: Report, s: Any, transport: httpx.BaseTransport | None, adapter_path: str, adapter_name: str
) -> bool:
    """Steps 6.2 to 6.6 of LIVE-DAY1-PROBES, each its own function, so a later probe (tool calls) slots in while the
    adapter is loaded. Lines name the adapter and its path, never the endpoint URL."""
    from ratchet.nightschool import train
    from ratchet.providers import OpenAICompatProvider

    sec = report.section("student")
    sec.update(
        endpoint="student",
        key_set=bool(s.student_api_key),
        adapter={"name": adapter_name, "path": adapter_path},
        samples_n=s.samples,
        train_default_targets=train.parse_args(["--data", "-", "--out", "-"]).targets.split(","),
    )
    provider = OpenAICompatProvider(s.student_base, s.student_api_key, timeout=s.student_timeout(), transport=transport)
    headers = {"Authorization": f"Bearer {s.student_api_key}"} if s.student_api_key else {}
    st = _Student(report, sec, s, c, provider, headers, adapter_name, adapter_path)
    loaded = student_load(st)
    try:
        if loaded:
            student_samples(st)
        else:
            sec["samples"] = {"skipped": "the adapter did not load"}
        student_base_and_adapter(st, loaded)
        student_missing_adapter(st)
    finally:
        if loaded:
            student_unload(st)
        else:
            sec["unload"] = {"skipped": "the adapter did not load"}
    return sec["outcome"] != "fail"


# ---------- tavily (one basic search) ----------
TAVILY_HINTS = {
    401: "the key",
    429: "rate limited",
    432: "plan or pay-as-you-go limit reached, so no credits are left for judging",
    433: "plan or pay-as-you-go limit reached, so no credits are left for judging",
}


def check_tavily(c: httpx.Client, report: Report, s: Any, query: str) -> bool:
    from ratchet.scout.search import TavilySearch

    sec = report.section("tavily")
    sec.update(depth="basic", query=query)
    search = TavilySearch(s.tavily_api_key, client=c)
    t0 = time.perf_counter()
    try:
        if hasattr(search, "search_with_usage"):
            hits, credits, counted = search.search_with_usage(query, s.scout_max_results)
            source = counted if isinstance(counted, str) else ("usage" if counted else "table")
        else:
            hits, credits, source = search.search(query, s.scout_max_results), 1, "table"
    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        try:
            detail = e.response.json().get("detail")
            text = detail.get("error") if isinstance(detail, dict) else detail
        except (ValueError, AttributeError):
            text = e.response.text
        sec.update(status=status, error=f"HTTPStatusError {status}", message=report.scrub(text or ""), latency_ms=_ms(t0))
        report.outcome("tavily", "fail")
        report.line("fail", f"tavily: HTTP {status} {text or ''} ({TAVILY_HINTS.get(status, 'see docs/DAY1.md')})")
        return False
    except httpx.HTTPError as e:
        sec.update(latency_ms=_ms(t0), **failure(e, report))
        report.outcome("tavily", "fail")
        report.line("fail", f"tavily: {sec['error']}")
        return False
    sec.update(
        hits=len(hits),
        hits_with_url=sum(1 for h in hits if getattr(h, "url", "")),
        credits=credits,
        credits_source=source,
        latency_ms=_ms(t0),
    )
    if sec["hits_with_url"]:
        report.outcome("tavily", "ok")
        report.line("ok", f"tavily: {len(hits)} hits for one basic search ({credits} credit)")
    else:
        report.outcome("tavily", "warn")
        report.line("warn", "tavily: the search answered 200 with no hits")
    return True


# ---------- cli (warnings only, read-only) ----------
class NebiusCommand(NamedTuple):
    line: int  # 1-based line where the command starts
    path: tuple[str, ...]  # ("ai", "endpoint", "stop")
    flags: tuple[str, ...]  # the nebius flags it passes, in order, without repeats


_FLAG = re.compile(r"--[a-z][a-z0-9-]*")
_WORD = re.compile(r"[a-z][a-z0-9-]*")
_HELP_FLAG = re.compile(r"(?<![\w-])--[a-z][a-z0-9-]*")
_INTERACTIVE = re.compile(r"(?<!non-)\binteractive(ly)?\b", re.IGNORECASE)


def _units(text: str) -> list[tuple[int, str]]:
    """Logical lines with their first line number: backslash continuations joined, and a bash array `NAME=(` ... `)`
    that spans lines joined into one."""
    lines = text.splitlines()
    out: list[tuple[int, str]] = []
    i = 0
    while i < len(lines):
        start, unit = i + 1, lines[i]
        while unit.rstrip().endswith("\\") and i + 1 < len(lines):
            i += 1
            unit = unit.rstrip()[:-1] + " " + lines[i]
        code = unit.split("#", 1)[0]
        if "=(" in code and code.count("(") > code.count(")"):
            while i + 1 < len(lines) and unit.split("#", 1)[0].count("(") > unit.split("#", 1)[0].count(")"):
                i += 1
                unit = unit.rstrip().removesuffix("\\") + " " + lines[i].split("#", 1)[0]
        out.append((start, unit))
        i += 1
    return out


def nebius_commands(text: str) -> list[NebiusCommand]:
    """The `nebius` commands a shell script runs: a word `nebius` (also first in a bash array, `cmd=(nebius ...`), the
    words after it up to the first option, and every `--flag` after it. A quoted value is one word, so vLLM's flags inside
    `--args "..."` never count. Commands inside `$(...)` and comments are not found."""
    found: list[NebiusCommand] = []
    for start, unit in _units(text):
        try:
            words = shlex.split(unit, comments=True)
        except ValueError:
            continue
        for k, word in enumerate(words):
            if word != "nebius" and not re.fullmatch(r"\w+=\(nebius", word):
                continue
            path: list[str] = []
            j = k + 1
            while j < len(words) and _WORD.fullmatch(words[j].rstrip(")")) and len(path) < 4:
                path.append(words[j].rstrip(")"))
                j += 1
            if not path:
                continue
            flags: list[str] = []
            for w in words[j:]:
                name = w.rstrip(")").split("=", 1)[0]
                if _FLAG.fullmatch(name) and name not in flags:
                    flags.append(name)
            found.append(NebiusCommand(start, tuple(path), tuple(flags)))
            break
    return found


def check_cli(report: Report, run: Callable[..., Any], which: Callable[[str], str | None]) -> bool:
    """Compare every flag the deploy scripts pass to `nebius` with the installed CLI's `--help`. Runs only
    `nebius version` and `nebius ... --help`; never a script, a create or a stop. Always True: warnings only."""
    sec = report.section("cli")
    if which("nebius") is None:
        sec["skipped"] = "nebius is not installed here"
        report.line("skipped", "cli: nebius is not installed here; run this check where the deploy scripts run")
        return True

    def call(argv: list[str]) -> tuple[int | None, str, str]:
        try:
            p = run(argv, capture_output=True, text=True, timeout=30, check=False)
        except (subprocess.TimeoutExpired, OSError) as e:
            return None, "", type(e).__name__
        return p.returncode, p.stdout or "", p.stderr or ""

    code, out, err = call(["nebius", "version"])
    sec["version"] = report.scrub((out or err).strip().splitlines()[0]) if (out or err).strip() else None
    commands: list[dict[str, Any]] = []
    sec["commands"] = commands
    helps: dict[tuple[str, ...], tuple[int | None, str]] = {}
    report.outcome("cli", "ok")
    for script in DEPLOY_SCRIPTS:
        try:
            text = (ROOT / script).read_text(encoding="utf-8")
        except OSError:
            report.outcome("cli", "warn")
            report.line("warn", f"cli: cannot read {script}")
            continue
        for cmd in nebius_commands(text):
            if cmd.path not in helps:
                code, out, err = call(["nebius", *cmd.path, "--help"])
                helps[cmd.path] = (code, out + "\n" + err)
            code, help_text = helps[cmd.path]
            name = "nebius " + " ".join(cmd.path)
            present = set(_HELP_FLAG.findall(help_text))
            interactive = bool(_INTERACTIVE.search(help_text))
            head = [ln.strip() for ln in help_text.splitlines() if ln.strip()][:3]
            commands.append(
                {
                    "script": script,
                    "line": cmd.line,
                    "command": name,
                    "help_exit": code,
                    "help_head": report.scrub(" / ".join(head)),
                    "flags": {f: f in present for f in cmd.flags},
                    "help_flags": sorted(present),
                    "interactive": interactive,
                }
            )
            where = f"{script}:{cmd.line} {name}"
            if code != 0:
                first = (help_text.strip().splitlines() or [""])[0]
                report.outcome("cli", "warn")
                report.line("warn", f"cli: {name} --help exited {code}: {first}")
                continue
            missing = [f for f in cmd.flags if f not in present]
            for f in missing:
                report.outcome("cli", "warn")
                report.line("warn", f"cli: {where}: {f} is not in its --help")
            if interactive:
                report.outcome("cli", "warn")
                report.line("warn", f"cli: {name} describes itself as interactive; {script} runs it from a cron night")
            if not missing and not interactive:
                report.line("ok", f"cli: {name} takes every flag {script} uses ({len(cmd.flags)})")
    if not commands:
        report.outcome("cli", "warn")
        report.line("warn", "cli: found no nebius command in the deploy scripts")
    return True


# ---------- command line and JSON ----------
def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """The command line (public for tests). An unknown check or bad flag raises SystemExit(2); --help SystemExit(0)."""
    ap = argparse.ArgumentParser(
        prog="day1_check.py",
        description="Day-1 probes for Token Factory, the student endpoint, Tavily and the nebius CLI.",
        epilog="The student check's adapter must be trained by train.py with its default --targets (the Mamba in_proj "
        "and out_proj included) and --rank at most the endpoint's --max-lora-rank (16), so the probe catches vLLM "
        "rejecting or skipping those weights.",
    )
    ap.add_argument("checks", nargs="*", metavar="CHECK", help=f"{', '.join(CHECKS)} or all (default: teacher)")
    ap.add_argument("--json", dest="json_path", metavar="PATH", help="write every measured fact here (no keys)")
    ap.add_argument("--adapter-path", help="where the endpoint sees the probe adapter (default <adapter root>/test)")
    ap.add_argument("--adapter-name", default=DEFAULT_ADAPTER_NAME, help="the name to load it under")
    ap.add_argument("--query", default=DEFAULT_QUERY, help="the Tavily probe's query")
    args = ap.parse_args(list(argv))
    bad = [c for c in args.checks if c not in (*CHECKS, "all")]
    if bad:
        ap.error(f"unknown check {bad[0]!r}: choose from {', '.join(CHECKS)} or all")
    return args


def _git_sha(run: Callable[..., Any]) -> str | None:
    try:
        p = run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10, check=False, cwd=str(ROOT))
    except (subprocess.TimeoutExpired, OSError):
        return None
    sha = (p.stdout or "").strip() if getattr(p, "returncode", 1) == 0 else ""
    return sha if re.fullmatch(r"[0-9a-f]{7,64}", sha) else None


def _version() -> str | None:
    try:
        _ratchet()
        from ratchet import __version__

        return str(__version__)
    except Exception:  # best effort: a broken checkout still writes its facts
        return None


def write_json(
    path: str, report: Report, checks: list[str], code: int, run: Callable[..., Any], now: float, reason: str | None = None
) -> None:
    """The evidence file: written once, after every check (also on exit 1, and on exit 2 with the reason)."""
    doc = {
        "generator": GENERATOR,
        "checked_at": datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "checks_run": checks,
        "exit_code": code,
        "ratchet_version": _version(),
        "git_sha": _git_sha(run),
        "note": NOTE,
        "checks": report.checks,
    }
    if reason:
        doc["reason"] = report.scrub(reason)
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def main(
    client: httpx.Client | None = None,
    argv: Sequence[str] | None = None,
    *,
    transport: httpx.BaseTransport | None = None,
    run: Callable[..., Any] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
    now: Callable[[], float] = time.time,
) -> int:
    """Returns the exit code: 0 all good, 1 a check failed, 2 bad input or no key. `argv` None means the teacher check
    (never sys.argv). Test seams, never passed in production: `client` replaces the network for raw calls, `transport`
    the providers' network, `run` and `which` the nebius CLI and git, `now` the JSON timestamp."""
    try:
        args = parse_args(list(argv or []))
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2
    asked = list(args.checks) or ["teacher"]
    every = "all" in asked
    checks = [c for c in CHECKS if every or c in asked]
    report = Report(secrets=(KEY,), hosts=(_host(BASE),))

    def finish(code: int, reason: str | None = None) -> int:
        if args.json_path:
            write_json(args.json_path, report, ran, code, run, now(), reason)
        return code

    ran: list[str] = []
    if PRODUCTION_ADAPTER.fullmatch(args.adapter_name):
        msg = "the probe unloads its adapter at the end; use a name production does not serve"
        print(f"--adapter-name {args.adapter_name}: {msg}")
        return finish(2, msg)

    s = None
    if checks != ["teacher"]:
        _ratchet()
        from ratchet.config import Settings
        from ratchet.scout.search import TAVILY_URL

        try:
            s = Settings()
            s.gen_policy()
            s.student_timeout()
            s.teacher_timeout()
        except ValueError as e:
            print(f"Settings: {e}")
            return finish(2, f"Settings: {e}")
        report.secrets = (KEY, s.teacher_api_key, s.student_api_key, s.tavily_api_key)
        report.hosts = (_host(BASE), _host(s.student_base), _host(TAVILY_URL))

    need = {
        "teacher": (bool(KEY), "Set NEBIUS_API_KEY first."),
        "choices": (bool(KEY), "Set NEBIUS_API_KEY first."),
        "reasoning": (bool(KEY), "Set NEBIUS_API_KEY first."),
        "student": (bool(s and s.student_base), "Set RATCHET_STUDENT_BASE first (the endpoint's /v1 URL)."),
        "tavily": (bool(s and s.tavily_api_key), "Set TAVILY_API_KEY first."),
        "cli": (which("nebius") is not None, "nebius is not installed here"),
    }
    runnable = []
    for name in checks:
        met, message = need[name]
        if met:
            runnable.append(name)
        elif name == "cli":
            runnable.append(name)  # check_cli reports the skip itself, and never fails
        elif not every:
            print(message)
            return finish(2, message)
        else:
            report.section(name)["skipped"] = message
            report.line("skipped", f"{name}: {message}")
    if every and not [n for n in runnable if n != "cli" or need["cli"][0]]:
        print("Nothing to check: set NEBIUS_API_KEY, RATCHET_STUDENT_BASE or TAVILY_API_KEY first.")
        return finish(2, "nothing to check")

    c = client if client is not None else httpx.Client(timeout=120)
    auth = {"Authorization": f"Bearer {KEY}"}
    for name in runnable:
        ran.append(name)
        if name == "teacher":
            check_teacher(c, auth, report)
        elif name == "choices":
            check_choices(report, s, transport)
        elif name == "reasoning":
            check_reasoning(c, auth, report)
        elif name == "student":
            from ratchet import live

            path = args.adapter_path or f"{live.adapter_root()}/test"
            check_student(c, report, s, transport, path, args.adapter_name)
        elif name == "tavily":
            check_tavily(c, report, s, args.query)
        elif name == "cli":
            check_cli(report, run, which)
    failed = report.failed()
    if checks == ["teacher"]:
        print("\nAll good." if not failed else f"\n{TEACHER_SUMMARY}")
    else:
        print("\nAll good." if not failed else f"\n{SUMMARY}")
    return finish(1 if failed else 0)


if __name__ == "__main__":
    sys.exit(main(argv=sys.argv[1:]))
