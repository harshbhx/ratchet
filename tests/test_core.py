import json
import math
from collections import Counter

import pytest
from fakes import _audit, _version, token_entries

from ratchet import licenses, tasks
from ratchet.confidence import calibrate_threshold, majority, score
from ratchet.ledger import STORED_PARAM_KEYS, Ledger, example_hash, target_params
from ratchet.openai_compat import validate_chat_request
from ratchet.providers import (
    CHECK_DROPS,
    CHOICES_TEMPERATURE,
    DEFAULT_STUDENT_PARAMS,
    DEFAULT_TEACHER_PARAMS,
    MIN_LOGPROB,
    NOT_YET_FORWARDED,
    PARAM_FIELDS,
    Completion,
    GenParams,
    GenPolicy,
    parse_param_allowlist,
    served_logprobs,
)
from ratchet.redact import redact_json
from ratchet.release import ReleaseGate, ReleasePolicy, wilson


def comp(text, p=0.9, n=5, finish_reason="stop"):
    return Completion(
        text=text, model="m", in_tokens=10, out_tokens=n, token_logprobs=[math.log(p)] * n, finish_reason=finish_reason
    )


TRIAGE = tasks.get("ticket-triage")
A = json.dumps({"category": "billing", "priority": "high", "reply": "x"})
B = json.dumps({"category": "refund", "priority": "high", "reply": "y"})


# ---------- tasks ----------
@pytest.mark.req("FR-2.6", "FR-2.7")
def test_task_agree_ignores_free_text_and_reasoning():
    a = '<think>hmm</think>```json\n{"category": "Billing", "priority": "high", "reply": "hello"}\n```'
    b = json.dumps({"category": "billing", "priority": "high", "reply": "totally different"})
    assert TRIAGE.agree(a, b)
    assert not TRIAGE.agree(a, B)


@pytest.mark.req("FR-2.6")
def test_invalid_json_never_agrees():
    assert not TRIAGE.agree("not json", "not json")


@pytest.mark.req("FR-2.7")
@pytest.mark.parametrize(
    ("text", "want"),
    [
        pytest.param('<think>billing, surely</think>{"a": 1}', '{"a": 1}', id="full-pair"),
        pytest.param('Let me think about it</think>\n{"a": 1}', '{"a": 1}', id="closing-only"),
        # The fence stays: strip_reasoning removes reasoning only; clean_answer unwraps fences for structured tasks.
        pytest.param('Let me think</think>\n```json\n{"a":1}\n```', '```json\n{"a":1}\n```', id="closing-only-fenced"),
        pytest.param("<think>the answer is billing, high", "", id="unterminated"),
        pytest.param('<THINK>hmm</THINK> {"a": 1} ', '{"a": 1}', id="uppercase"),
        pytest.param("<Think>hmm</tHiNk>answer", "answer", id="mixed-case"),
        pytest.param("A plain answer, no tags.", "A plain answer, no tags.", id="no-tags"),
        pytest.param("  padded  ", "padded", id="no-tags-padded"),
        # An unclosed tag after answer text is content: a model opens its reasoning before it answers (review finding).
        pytest.param("billing <think>on second thought", "billing <think>on second thought", id="answer-then-unterminated"),
        pytest.param("Wrap it in a <think> tag.", "Wrap it in a <think> tag.", id="mentions-opening-tag"),
        pytest.param("  <think>cut off", "", id="unterminated-after-whitespace"),
        pytest.param("<think>a</think>\n<think>cut off", "", id="pair-then-unterminated"),
        pytest.param("plan</think>a<think>b</think>c", "ac", id="closing-then-pair"),
        pytest.param("plan</think>answer<think>cut", "answer<think>cut", id="closing-then-answer-then-unterminated"),
        pytest.param("plan</think> <think>cut", "", id="closing-then-unterminated"),
        pytest.param("<think>a<think>b</think>c</think>answer", "answer", id="nested"),
        pytest.param("<think>a</think>first</think>second", "second", id="last-closing-wins"),
        pytest.param("", "", id="empty"),
        pytest.param(None, "", id="none"),
    ],
)
def test_strip_reasoning_cases(text, want):
    assert tasks.strip_reasoning(text) == want
    assert tasks.strip_reasoning(want) == want  # idempotent: nothing left to strip


@pytest.mark.req("FR-2.7", "FR-2.13")
def test_clean_answer_unwraps_fences_only_for_structured_tasks():
    free = tasks.get("default")
    obj = '{"category": "billing", "priority": "high"}'
    for fenced in (f"```json\n{obj}\n```", f"```\n{obj}\n```", f"```JSON\n{obj}\n```", f"```{obj}```", f"```json\n{obj}```"):
        assert tasks.clean_answer(TRIAGE, fenced) == obj, fenced
        assert tasks.clean_answer(free, fenced) == fenced  # a code block is content in a free-text answer
    assert tasks.clean_answer(TRIAGE, f"<think>billing</think>\n```json\n{obj}\n```") == obj
    assert tasks.clean_answer(TRIAGE, f"Let me see.</think>```json\n{obj}\n```") == obj
    assert tasks.clean_answer(free, "<think>x</think>```py\nprint(1)\n```") == "```py\nprint(1)\n```"
    # Unwrapped only when one fence is the whole answer and its body is JSON.
    for kept in (
        "```json\nbilling, high\n```",  # body is not JSON
        f"```json\n{obj}\n```\nand\n```json\n{obj}\n```",  # two fences
        f"Here it is: ```json\n{obj}\n```",  # prose around the fence
        f"````json\n{obj}\n````",  # a four-backtick fence is not unwrapped
        "``````",
        obj,
    ):
        assert tasks.clean_answer(TRIAGE, kept) == kept, kept
    deep = "```\n" + "[" * 100_000 + "]" * 100_000 + "\n```"  # too deep for the JSON parser: kept, never a crash
    assert tasks.clean_answer(TRIAGE, deep) == deep
    assert TRIAGE.canonical(f"```json\n{obj}\n```") == TRIAGE.canonical(obj) != tasks.INVALID


@pytest.mark.req("FR-2.7", "FR-2.13")
def test_answers_that_mention_think_tags_keep_their_text():
    """Review finding: cutting at any tag served complete answers that quote one (a support reply about a stray tag, a
    page Scout read about reasoning models) truncated, structured ones as broken JSON, and stored none of them. A JSON
    object can hold a tag only inside a string, so a structured answer that is one object, before or after its reasoning
    and in a fence or not, is kept whole."""
    opening = '{"category": "technical", "priority": "high", "reply": "Your model output shows a stray <think> tag; we fix it."}'
    closing = '{"category":"technical","priority":"high","reply":"Strip everything up to the </think> tag before parsing."}'
    for answer in (opening, closing):
        for said in (answer, f"```json\n{answer}\n```", f"<think>bug, high</think>\n{answer}", f"bug, high</think>{answer}"):
            assert tasks.clean_answer(TRIAGE, said) == answer, said
            assert TRIAGE.canonical(said) == '{"category": "technical", "priority": "high"}', said
            assert tasks.parse_json(said) == json.loads(answer), said
        assert tasks.clean_answer(TRIAGE, answer) == answer  # cleaning twice changes nothing
    free = tasks.get("default")
    prose = "Reasoning models open their chain of thought with a <think> tag. Strip it before showing the answer."
    assert tasks.clean_answer(free, prose) == tasks.clean_answer(free, f"<think>explain</think>\n{prose}") == prose
    # Reasoning that was cut off is still nothing, even when it holds a JSON object or quotes a tag.
    for cut in (f"<think>{opening}", f"plan</think> <think>{opening}", f"<think>maybe {closing}"):
        assert TRIAGE.canonical(cut) == tasks.INVALID, cut
    assert tasks.clean_answer(TRIAGE, f"<think>{opening}") == tasks.clean_answer(TRIAGE, f"x</think> <think>{opening}") == ""


@pytest.mark.req("FR-2.2", "FR-2.13")
def test_empty_answers_are_invalid_for_every_task():
    free = tasks.get("default")
    for empty in ("", "   \n", "<think>still thinking", "<think>done</think>"):
        assert free.canonical(empty) == TRIAGE.canonical(empty) == tasks.INVALID
        assert not free.agree(empty, empty)
    s = score(free, [comp("<think>never finished")] * 3)
    assert (s.confidence, s.agreement, s.valid) == (0.0, 0.0, False)


# ---------- confidence ----------
@pytest.mark.req("FR-2.2")
def test_unanimous_confident_samples_score_high():
    s = score(TRIAGE, [comp(A, 0.95)] * 3)
    assert s.agreement == 1.0 and s.confidence > 0.9


@pytest.mark.req("FR-2.2")
def test_disagreement_lowers_confidence():
    s = score(TRIAGE, [comp(A, 0.95), comp(B, 0.95), comp(A, 0.5)])
    assert s.agreement == pytest.approx(2 / 3)
    assert s.confidence < 0.85
    assert TRIAGE.agree(s.answer.text, A)


@pytest.mark.req("FR-2.2")
def test_all_invalid_scores_zero():
    assert score(TRIAGE, [comp("nope")] * 3).confidence == 0.0


@pytest.mark.req("FR-2.2", "FR-2.13")
def test_length_samples_are_invalid_in_score():
    """A sample that did not end by itself (the token limit, a content filter, or any `finish_reason` but `stop` and
    `tool_calls`, the rule a teacher answer must pass to be stored) gets no vote and is never the answer, even when its
    visible text parses."""
    cut = score(TRIAGE, [comp(A, 0.99, finish_reason="length")] * 3)
    assert (cut.confidence, cut.agreement, cut.valid) == (0.0, 0.0, False)
    for reason in ("content_filter", "abort", "unknown", "eos_token"):
        assert not score(TRIAGE, [comp(A, 0.99, finish_reason=reason)] * 3).valid, reason
    # Two cut-off samples of A and one complete B: B is the only vote, with agreement 1/3.
    s = score(TRIAGE, [comp(A, 0.99, finish_reason="length"), comp(B, 0.9), comp(A, 0.99, finish_reason="length")])
    assert s.answer.text == B and s.agreement == pytest.approx(1 / 3) and s.valid
    # Complete answers vote as before, including ones that end in a tool call.
    full = score(TRIAGE, [comp(A, 0.95), comp(A, 0.95, finish_reason="tool_calls"), comp(A, 0.95)])
    assert full.agreement == 1.0 and full.confidence > 0.9


@pytest.mark.req("FR-5.2", "FR-2.3")
def test_calibrate_threshold_hits_target_precision():
    pts = [(0.99, True)] * 50 + [(0.9, True)] * 30 + [(0.85, False)] * 5 + [(0.6, False)] * 20
    thr = calibrate_threshold(pts, 0.95)
    served = [ok for c, ok in pts if c >= thr]
    assert sum(served) / len(served) >= 0.95
    assert thr <= 0.9


# ---------- licenses ----------
@pytest.mark.req("FR-3.2", "US-8", "INV-1")
@pytest.mark.parametrize("model", ["gpt-5.6-sol", "openai/gpt-4o", "claude-opus-5-5", "anthropic/claude-haiku", "gemini-3.1-pro"])
def test_closed_teachers_are_refused(model):
    with pytest.raises(licenses.LicenseError):
        licenses.require_distillable(model)


@pytest.mark.req("FR-3.1", "FR-3.2", "US-8", "INV-1")
def test_open_teacher_allowed_and_unknown_refused():
    assert licenses.require_distillable("nvidia/Nemotron-3-Ultra-550b-a55b").name == "OpenMDW-1.1"
    with pytest.raises(licenses.LicenseError):
        licenses.require_distillable("someone/unknown-model")


# ---------- ledger ----------
@pytest.mark.req("FR-3.4", "FR-4.3")
def test_examples_dedupe_and_count_and_stable_split():
    led = Ledger(":memory:", golden_fraction=0.5)
    msgs = [{"role": "user", "content": "hi"}]
    assert led.add_example("t", msgs, "a", "mock-teacher", "mock", "")
    assert not led.add_example("t", msgs, "a", "mock-teacher", "mock", "")
    rows = led.examples()
    assert len(rows) == 1 and rows[0]["seen"] == 2
    led2 = Ledger(":memory:", golden_fraction=0.5)
    led2.add_example("t", msgs, "a", "mock-teacher", "mock", "")
    assert led2.examples()[0]["split"] == rows[0]["split"]


@pytest.mark.req("FR-3.5", "US-7")
def test_bill_of_materials_lists_licenses():
    led = Ledger(":memory:")
    led.add_example("t", [{"role": "user", "content": "q"}], "a", "nvidia/Nemotron-3-Ultra-550b-a55b", "OpenMDW-1.1", "u")
    bom = led.bill_of_materials()
    assert bom["sources"][0]["license"] == "OpenMDW-1.1"
    assert len(bom["examples_sha256"]) == 64


# ---------- release gate ----------
def _pc(precision, coverage):
    return {"precision": precision, "coverage": coverage}


@pytest.mark.req("FR-6.6", "NFR-8")
def test_wilson_bounds():
    lo, hi = wilson(19, 20)
    assert lo < 0.95 < hi
    assert wilson(0, 0) == (0.0, 1.0)


@pytest.mark.req("FR-6.1", "FR-6.2", "US-5")
def test_cold_start_goes_to_canary_and_bad_candidates_rejected():
    led = Ledger(":memory:")
    gate = ReleaseGate(led)
    bad = _version(led, "candidate", _pc(0.80, 0.9))
    assert not gate.admit(bad)
    assert led.version(bad).state == "rejected"
    good = _version(led, "candidate", _pc(0.97, 0.8))
    assert gate.admit(good)
    assert led.version(good).state == "canary" and led.version(good).traffic_pct == 10


@pytest.mark.req("FR-6.1", "US-5")
def test_regression_vs_live_is_rejected_and_good_one_shadows():
    led = Ledger(":memory:")
    gate = ReleaseGate(led)
    live = _version(led, "live", _pc(0.98, 0.90))
    worse = _version(led, "candidate", _pc(0.97, 0.80))
    assert not gate.admit(worse)
    better = _version(led, "candidate", _pc(0.98, 0.92))
    assert gate.admit(better)
    assert led.version(better).state == "shadow"
    assert led.live().id == live


@pytest.mark.req("FR-6.5", "US-5")
def test_canary_advances_then_goes_live():
    led = Ledger(":memory:")
    gate = ReleaseGate(led, ReleasePolicy(min_audits=10))
    old = _version(led, "live", _pc(0.97, 0.9))
    v = _version(led, "candidate", _pc(0.97, 0.9))
    led.set_version(v, "canary", 10)
    _audit(led, v, 12, 0)
    gate.tick()
    assert led.version(v).traffic_pct == 50
    gate.tick()  # no new audits in this stage: must hold
    assert led.version(v).traffic_pct == 50
    _audit(led, v, 12, 0)
    gate.tick()
    assert led.version(v).state == "live" and led.version(old).state == "retired"


@pytest.mark.req("FR-6.6", "US-6")
def test_canary_rolls_back_on_strong_evidence():
    led = Ledger(":memory:")
    gate = ReleaseGate(led, ReleasePolicy(min_audits=10))
    live = _version(led, "live", _pc(0.97, 0.9))
    v = _version(led, "candidate", _pc(0.97, 0.9))
    led.set_version(v, "canary", 10)
    _audit(led, v, 20, 2)  # 50% precision
    gate.tick()
    assert led.version(v).state == "rolled_back"
    assert led.live().id == live
    assert any(e["kind"] == "rollback" for e in led.events())


@pytest.mark.req("FR-16.1")
def test_audit_counts_and_served_counts():
    """The two integers per version every release decision reads (`audit_counts`, over its life or after a request id)
    and the answers each version served (`served_counts`, its exposure): only student-served, audited rows count as
    audits; a stand-in (degraded) answer is still an answer it served; teacher-served rows count for neither."""
    led = Ledger(":memory:")
    v = _version(led, "canary")
    w = _version(led, "live")
    _audit(led, v, 10, 5)  # 8 of 10
    since = led.version(v).stage_since
    row = dict(task="t", student_version=v, cost_usd=0.0, baseline_cost_usd=0.0)
    led.log_request(served_by="student", audited=0, route="student", **row)
    led.log_request(served_by="student", audited=0, route="degraded", **row)
    led.log_request(served_by="teacher", audited=1, audit_agree=1, route="confidence", **row)  # not a student answer
    last = led.log_request(served_by="student", audited=1, audit_agree=1, route="student", **row)
    assert led.audit_counts(v) == (11, 9) and led.audit_counts(v, since) == (11, 9)
    assert led.audit_counts(v, last - 1) == (1, 1) and led.audit_counts(v, last) == (0, 0) and led.audit_counts(w) == (0, 0)
    assert led.version_live_quality(v) == (11, 9 / 11) and led.version_live_quality(w) == (0, None)
    assert led.served_counts() == {v: 13}
    led.log_request(served_by="student", audited=1, audit_agree=None, route="student", **row)  # never written by the router
    assert led.audit_counts(v) == (12, 9)  # an audit without a verdict counts against the version


# ---------- generation parameters (FR-1.13, ADR 0017) ----------
FULL_PARAMS = {
    "max_completion_tokens": 50,
    "max_tokens": 900,  # max_completion_tokens wins
    "temperature": 0.9,
    "top_p": 0.8,
    "stop": "\n\n",
    "seed": 7,
    "presence_penalty": 0.5,
    "frequency_penalty": -0.5,
    "logit_bias": {"50256": -100},
    "logprobs": True,
    "top_logprobs": 2,
    "response_format": {"type": "json_object"},
    "tools": [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}],
    "tool_choice": "auto",
    "parallel_tool_calls": False,
    "reasoning_effort": "low",
    "user": "end-user-7",
    "safety_identifier": "hashed-user",
    "store": False,
    "metadata": {"ratchet_task": "default"},
    "top_k": 20,  # a vLLM field: kept in `extra`
}


@pytest.mark.req("FR-1.13")
def test_gen_params_from_a_validated_request():
    req = validate_chat_request({"messages": [{"role": "user", "content": "hi"}], **FULL_PARAMS})
    p = GenParams.from_request(req)
    assert (p.max_tokens, p.temperature, p.stop, p.seed, p.extra) == (50, 0.9, ("\n\n",), 7, {"top_k": 20})
    body = p.to_body()
    # Every standard field, `max_completion_tokens` sent as `max_tokens`, `stop` as a list; never the end user's
    # identity, the storage choice or the metadata (they are not generation parameters at all).
    assert set(body) == set(PARAM_FIELDS) | {"top_k"}
    assert body["max_tokens"] == 50 and body["stop"] == ["\n\n"] and "max_completion_tokens" not in body
    assert not {"user", "safety_identifier", "store", "metadata", "model", "messages", "n"} & set(body)
    assert p.to_body(frozenset({"seed", "top_k"})) == {"seed": 7, "top_k": 20}
    # A request without parameters has none; empty lists and objects count as not sent.
    bare = GenParams.from_request(validate_chat_request({"messages": [{"role": "user", "content": "hi"}]}))
    assert bare == GenParams() and bare.to_body() == {}
    empty = validate_chat_request({"messages": [{"role": "user", "content": "hi"}], "tools": [], "logit_bias": {}})
    assert GenParams.from_request(empty) == GenParams()
    # `only` keeps the object when nothing is dropped (the request path allocates nothing for it).
    assert p.only(frozenset(PARAM_FIELDS) | {"top_k"}) is p
    assert p.only({"stop"}) == GenParams(stop=("\n\n",))
    # An extra key never replaces a standard field or sets one Ratchet controls.
    odd = GenParams(seed=1, extra={"seed": 2, "model": "gpt-4o", "n": 9, "messages": []})
    assert odd.to_body() == {"seed": 1}


@pytest.mark.req("FR-1.13", "INV-1")
def test_param_allowlists_parse_and_refuse_what_ratchet_never_forwards():
    default = frozenset({"stop"})
    assert parse_param_allowlist("", default) is default and parse_param_allowlist("  ", default) is default
    assert parse_param_allowlist("none", default) == frozenset() and parse_param_allowlist("NONE", default) == frozenset()
    assert parse_param_allowlist("all", default) == frozenset(PARAM_FIELDS)
    assert parse_param_allowlist("all, top_k", default) == frozenset(PARAM_FIELDS) | {"top_k"}
    assert parse_param_allowlist("stop, max_completion_tokens,,seed", default) == {"stop", "max_tokens", "seed"}
    # The model is always Settings.teacher_model (INV-1), and the end user, storage, metadata and billing tier never go
    # upstream.
    for bad in (
        "model",
        "messages",
        "n",
        "stream",
        "user",
        "safety_identifier",
        "store",
        "metadata",
        "service_tier",
        "functions",
        "none,stop",
        "Model",
    ):
        with pytest.raises(ValueError, match="cannot be forwarded"):
            parse_param_allowlist(bad, default, "RATCHET_TEACHER_PARAMS")
    for bad in ("max tokens", "stop;seed", "9lives", "x" * 65):
        with pytest.raises(ValueError):
            parse_param_allowlist(bad, default)
    # Names are matched in any case; a near miss of a standard field is a typo and stops startup, other names are
    # vendor fields.
    assert parse_param_allowlist("Stop,MAX_TOKENS,Top_K", default) == {"stop", "max_tokens", "top_k"}
    for typo, meant in (("max_token", "max_tokens"), ("stops", "stop"), ("temprature", "temperature"), ("tool", "tools")):
        with pytest.raises(ValueError, match=f"did you mean '{meant}'"):
            parse_param_allowlist(f"seed,{typo}", default, "RATCHET_STUDENT_PARAMS")
    vendor = ("top_k", "min_p", "min_tokens", "repetition_penalty", "max_new_tokens", "guided_json")
    assert parse_param_allowlist(",".join(vendor), default) == frozenset(vendor)
    # Tool calls are not forwarded by default: nothing reads them back yet. The teacher's logprobs are, since responses
    # return them (FR-1.14); a client that does not ask sends none.
    assert DEFAULT_TEACHER_PARAMS == frozenset(PARAM_FIELDS) - {"tools", "tool_choice", "parallel_tool_calls"}
    assert {"logprobs", "top_logprobs"} <= DEFAULT_TEACHER_PARAMS
    assert DEFAULT_STUDENT_PARAMS == {"max_tokens", "stop", "seed", "response_format", "logit_bias", "top_logprobs"}


@pytest.mark.req("FR-1.13", "NFR-3")
def test_gen_policy_gives_each_role_its_parameters_and_caps():
    client = GenParams.from_request(validate_chat_request({"messages": [{"role": "user", "content": "hi"}], **FULL_PARAMS}))
    policy = GenPolicy()

    # Student: what shapes the answer, capped; never the client's temperature or logprobs (STUDENT_SAMPLING fixes them).
    st = policy.student(client)
    assert st.to_body() == {
        "max_tokens": 50,
        "stop": ["\n\n"],
        "seed": 7,
        "logit_bias": {"50256": -100},
        "response_format": {"type": "json_object"},
        "top_logprobs": 2,
    }
    assert policy.student(None) == policy.student(GenParams()) == GenParams(max_tokens=1024)
    assert policy.student(GenParams(max_tokens=10**6)).max_tokens == 1024

    # Teacher serving the answer: every standard field as sent but the tool-call fields (a vendor field only when listed),
    # logprobs included; temperature 0 when the client set none.
    serve = policy.teacher(client, "serve")
    assert serve.to_body() == {k: v for k, v in client.to_body().items() if k not in NOT_YET_FORWARDED | {"top_k"}}
    everything = GenPolicy(teacher_allow=frozenset(PARAM_FIELDS))
    assert everything.teacher(client).to_body() == {k: v for k, v in client.to_body().items() if k != "top_k"}
    vendor = GenPolicy(teacher_allow=parse_param_allowlist("all,top_k", frozenset()))
    assert vendor.teacher(client).extra == vendor.teacher(client, "audit").extra == {"top_k": 20}
    assert policy.teacher(None).to_body() == {"max_tokens": 8192, "temperature": 0.0}
    assert policy.teacher(GenParams(max_tokens=10**6)).max_tokens == 8192  # capped

    # Audit and arbitration: deterministic, under the same conditions (length, stops, format, tools, effort).
    for role in ("audit", "arbitrate"):
        check = everything.teacher(client, role)
        assert check.temperature == 0.0 and check.max_tokens == 50
        assert (check.stop, check.response_format, check.tools, check.tool_choice) == (
            ("\n\n",),
            {"type": "json_object"},
            client.tools,
            "auto",
        )
        assert all(getattr(check, name) is None for name in CHECK_DROPS)
        assert check.reasoning_effort == "low" and check.extra == {}
    with pytest.raises(ValueError, match="role"):
        policy.teacher(client, "student")

    # The reasoning headroom: the teacher may write past the client's budget; visible_limit says how far it may show.
    roomy = GenPolicy(reasoning_headroom=300)
    assert roomy.teacher(GenParams(max_tokens=50)).max_tokens == 350
    assert roomy.teacher(GenParams(max_tokens=8000)).max_tokens == 8192  # still capped
    assert (roomy.visible_limit(GenParams(max_tokens=50)), roomy.visible_limit(GenParams(max_tokens=8000))) == (50, 8000)
    assert roomy.visible_limit(GenParams(max_tokens=9000)) is None  # the cap is below the client's budget anyway
    assert roomy.visible_limit(None) is None and roomy.teacher(None).max_tokens == 8192  # no budget: the cap alone
    assert policy.visible_limit(GenParams(max_tokens=50)) is None  # no headroom: the teacher stops at the budget itself
    plan = roomy.plan(GenParams(max_tokens=50))
    assert (plan.student.max_tokens, plan.serve.max_tokens, plan.check.max_tokens, plan.visible_max) == (50, 350, 350, 50)

    # Allowlists: a field outside one is never sent, but the caps still apply. 0 = no cap.
    narrow = GenPolicy(teacher_allow=frozenset({"stop"}), student_allow=frozenset())
    assert narrow.teacher(client).to_body() == {"stop": ["\n\n"], "max_tokens": 8192, "temperature": 0.0}
    assert narrow.student(client) == GenParams(max_tokens=1024)
    assert narrow.visible_limit(GenParams(max_tokens=50)) is None
    uncapped = GenPolicy(teacher_max_tokens=0, student_max_tokens=0)
    assert uncapped.teacher(None).max_tokens is None and uncapped.student(None).max_tokens is None
    assert uncapped.teacher(GenParams(max_tokens=10**6)).max_tokens == 10**6
    for bad in ({"teacher_max_tokens": -1}, {"student_max_tokens": 1.5}, {"reasoning_headroom": True}):
        with pytest.raises(ValueError, match="whole number of tokens"):
            GenPolicy(**bad)


@pytest.mark.req("FR-1.13", "FR-4.3")
def test_stored_conditions_join_the_hash_only_when_set():
    msgs = [{"role": "user", "content": "Where is my refund?"}]
    assert STORED_PARAM_KEYS == ("response_format", "tools", "tool_choice", "stop")
    plain = example_hash("default", msgs)
    # No conditions, or only ones that do not define the target (or the default text format): the v0.4 hash.
    for params in (None, {}, {"temperature": 0.9, "seed": 7, "max_tokens": 5}, {"response_format": {"type": "text"}}):
        assert target_params(params) is None
        assert example_hash("default", msgs, params) == plain
    assert target_params({"stop": ("a",), "tools": [], "tool_choice": None, "seed": 1}) == {"stop": ["a"]}
    as_json = example_hash("default", msgs, {"response_format": {"type": "json_object"}})
    assert as_json != plain
    assert example_hash("default", msgs, {"stop": ["\n"]}) not in (plain, as_json)
    # The stored form decides the hash: a tuple and a list of the same stops are one example.
    assert example_hash("default", msgs, {"stop": ("\n",)}) == example_hash("default", msgs, {"stop": ["\n"]})
    # Read back for the golden eval; a value of the wrong shape (a hand-edited row) is left out, never an error.
    assert GenParams.from_stored(None) is None and GenParams.from_stored({}) is None
    stored = {"stop": ["\n"], "response_format": {"type": "json_object"}, "tools": [{"type": "function"}], "tool_choice": "auto"}
    assert GenParams.from_stored(stored) == GenParams(
        stop=("\n",), response_format={"type": "json_object"}, tools=[{"type": "function"}], tool_choice="auto"
    )
    junk = {"stop": "\n", "response_format": "json", "tools": [], "tool_choice": 5, "seed": 7}
    assert GenParams.from_stored(junk) == GenParams() and GenParams.from_stored({"stop": [1]}) == GenParams()


@pytest.mark.req("FR-1.13", "FR-4.3")
def test_v04_example_hash_unchanged():
    """Literals computed with the v0.4 code before generation parameters existed: an example stored without conditions
    keeps its hash, so its dedup identity and its golden or train split never move."""
    msgs = [
        {"role": "system", "content": "Classify the ticket."},
        {"role": "user", "content": "I was charged twice. Café ☕ bob"},
    ]
    assert example_hash("ticket-triage", msgs) == "dc8a3597e61c73429b47697e27835bd9002d602a5d51739e0ceceb85cdcbc902"
    assert example_hash("default", [{"role": "user", "content": "hi"}]) == (
        "1cddc5c783b70139db37cd7910a027d4a3a74c7ab5001eb4345b9fd462265ab5"
    )
    led = Ledger(":memory:")
    led.add_example("ticket-triage", msgs, "{}", "t", "l", "u", params={"seed": 7})  # not a stored condition
    (row,) = led.examples()
    assert row["hash"] == "dc8a3597e61c73429b47697e27835bd9002d602a5d51739e0ceceb85cdcbc902" and row["params"] is None


@pytest.mark.req("FR-1.13", "INV-12")
def test_redact_json_redacts_string_values_and_keeps_keys():
    found: Counter[str] = Counter()
    value = {
        "stop": ["write to bob@example.com", "\n"],
        "tools": [{"type": "function", "function": {"name": "mail", "description": "Mail alice@example.com", "x": 1}}],
        "bob@example.com": None,  # a key is a field name: kept
    }
    out = redact_json(value, found)
    assert out == {
        "stop": ["write to [EMAIL]", "\n"],
        "tools": [{"type": "function", "function": {"name": "mail", "description": "Mail [EMAIL]", "x": 1}}],
        "bob@example.com": None,
    }
    assert found == {"EMAIL": 2} and value["stop"][0] == "write to bob@example.com"  # a copy; the input is untouched
    assert redact_json("call +44 20 7946 0958") == "call [PHONE]" and redact_json(5) == 5
    deep: object = "bob@example.com"
    for _ in range(5000):  # deeper than the recursion limit
        deep = [deep]
    out = redact_json(deep)
    for _ in range(5000):
        out = out[0]
    assert out == "[EMAIL]"


# ---------- n choices and token logprobs (FR-1.14, ADR 0020) ----------
def _lp(tokens, logprob=-0.1, **extra):
    """A completion whose `logprobs.content` lists `tokens` (strings, or full entries) and whose text is theirs."""
    content = [t if isinstance(t, dict) else {"token": t, "logprob": logprob, "bytes": None, "top_logprobs": []} for t in tokens]
    text = "".join(t["token"] for t in content if isinstance(t.get("token"), str))
    return Completion(text, "m", logprobs_content=content, **extra)


def _spelled(entries):
    return "".join(e["token"] for e in entries)


@pytest.mark.req("FR-1.14")
def test_served_logprobs_spell_the_served_text_and_drop_reasoning():
    plain = _lp(['{"a"', ": ", "1}"])
    out = served_logprobs(plain, '{"a": 1}')
    assert _spelled(out) == '{"a": 1}' and [e["token"] for e in out] == ['{"a"', ": ", "1}"]
    assert out[0] == {"token": '{"a"', "logprob": -0.1, "bytes": list(b'{"a"'), "top_logprobs": []}  # OpenAI's shape
    assert plain.logprobs_content[0]["bytes"] is None  # the provider's list is never changed

    cases = [
        # (tokens, task, what the client gets as content, the tokens it gets)
        (["<think>", "billing", "</think>", '{"a": 1}'], TRIAGE, '{"a": 1}', ['{"a": 1}']),  # reasoning dropped
        (["<think>x</think>", "\n\n", "The", " answer"], tasks.get("default"), "The answer", ["The", " answer"]),
        (["</think>", " The", " answer", ".\n"], tasks.get("default"), "The answer.", ["The", " answer", "."]),  # trimmed
        (["```json\n", '{"a": 1}', "\n```"], TRIAGE, '{"a": 1}', ['{"a": 1}']),  # a fence unwrapped
        (["Hi", " there\n"], tasks.get("default"), "Hi there", ["Hi", " there"]),  # trailing whitespace stripped
        (["  ", "Hi"], tasks.get("default"), "Hi", ["Hi"]),
        (["  Hi  "], tasks.get("default"), "Hi", ["Hi"]),  # one token trimmed on both sides
    ]
    for tokens, task, want_text, want_tokens in cases:
        c = _lp(tokens)
        text = tasks.clean_answer(task, c.text)
        assert text == want_text, tokens
        out = served_logprobs(c, text)
        assert out is not None and [e["token"] for e in out] == want_tokens, tokens
        assert _spelled(out) == text and all(e["bytes"] == list(e["token"].encode()) for e in out), tokens

    # A server that lists the reasoning tokens although its content leaves them out (a vLLM reasoning parser): only
    # the answer's tokens come back, so reasoning never reaches the client this way either.
    listed = _lp(["<think>", "secret plan", "</think>", '{"a": 1}']).logprobs_content
    parsed = Completion('{"a": 1}', "m", logprobs_content=listed)
    assert [e["token"] for e in served_logprobs(parsed, '{"a": 1}')] == ['{"a": 1}']

    # Tokens that cannot be told apart from what was dropped: None, never a token list that does not spell the answer.
    for tokens, task in (
        (["<think>x</think>{", '"a": 1}'], TRIAGE),  # the answer's first character shares a token with the tag
        (["A ", "<think>x</think>", " B"], tasks.get("default")),  # reasoning removed from the middle of the answer
    ):
        c = _lp(tokens)
        assert served_logprobs(c, tasks.clean_answer(task, c.text)) is None, tokens
    # A character split over tokens that do not decode alone (vLLM shows each part as U+FFFD): they do not spell it.
    split = Completion("café", "m", logprobs_content=_lp(["caf", "\ufffd", "\ufffd"]).logprobs_content)
    assert served_logprobs(split, "café") is None
    assert served_logprobs(Completion("x", "m"), "x") is None  # the model sent no logprobs
    assert served_logprobs(_lp(["<think>only reasoning", "</think>"]), "") == []  # nothing served, no tokens


@pytest.mark.req("FR-1.14")
def test_served_logprobs_check_every_entry_and_keep_the_clients_top_logprobs():
    alts = [{"token": t, "logprob": lp, "bytes": None} for t, lp in (("a", -0.1), ("b", -2.0), ("c", -3.0))]
    entry = {"token": "a", "logprob": -0.1, "bytes": [97], "top_logprobs": alts}
    c = _lp([entry])
    assert served_logprobs(c, "a", top=None)[0]["top_logprobs"] == []  # the client asked for none
    assert served_logprobs(c, "a", top=0)[0]["top_logprobs"] == []
    two = served_logprobs(c, "a", top=2)[0]["top_logprobs"]
    assert two == [{"token": "a", "logprob": -0.1, "bytes": [97]}, {"token": "b", "logprob": -2.0, "bytes": [98]}]
    # Malformed alternatives are left out; a malformed entry makes the whole list unusable.
    messy = {**entry, "top_logprobs": ["x", {"token": 5, "logprob": -1}, {"token": "d", "logprob": float("nan")}, alts[2]]}
    assert served_logprobs(_lp([messy]), "a", top=4)[0]["top_logprobs"] == [{"token": "c", "logprob": -3.0, "bytes": [99]}]
    for bad in (
        {"token": "a"},
        {"token": "a", "logprob": "low"},
        {"token": "a", "logprob": True},
        {"token": "a", "logprob": float("nan")},
        {"token": "a", "logprob": float("inf")},
        {"token": None, "logprob": -0.1},
        {"token": "a\ud800", "logprob": -0.1},  # UTF-8 cannot carry it: the response could not be written
        "a",
    ):
        c = Completion("a", "m", logprobs_content=[bad])
        assert served_logprobs(c, "a") is None, bad
    # -Infinity (a token the model could not have sampled) is reported as vLLM reports it, since JSON has no -Infinity.
    sure = served_logprobs(Completion("a", "m", logprobs_content=[{"token": "a", "logprob": float("-inf")}]), "a")
    assert sure == [{"token": "a", "logprob": MIN_LOGPROB, "bytes": [97], "top_logprobs": []}] and MIN_LOGPROB == -9999.0
    # The server's bytes are kept when they are bytes (a character split over tokens), else the token's own.
    split = [{"token": "\\xe2", "logprob": -1, "bytes": [226]}, {"token": "x", "logprob": -1, "bytes": [300]}]
    out = served_logprobs(Completion("\\xe2x", "m", logprobs_content=split), "\\xe2x")
    assert [e["bytes"] for e in out] == [[226], [120]]
    # The fakes' tokens spell their text, with the alternatives a client asked for.
    fake = Completion("hello world", "m", logprobs_content=token_entries("hello world", -0.2, 3, top=2))
    out = served_logprobs(fake, "hello world", top=1)
    assert _spelled(out) == "hello world" and all(len(e["top_logprobs"]) == 1 for e in out)


@pytest.mark.req("FR-1.14", "FR-2.2")
def test_majority_gives_n_agreeing_choices_with_the_best_sample_first():
    samples = [comp(A, 0.6), comp(A, 0.9), comp(B, 0.99), comp(A, 0.95), comp(A, 0.7)]
    scored = score(TRIAGE, samples[:3])  # as calibrated: the first RATCHET_SAMPLES samples
    assert scored.answer is samples[1] and scored.agreement == 2 / 3
    assert majority(TRIAGE, scored, samples, 1) == [samples[1]]
    # The best scored sample first, then the other agreeing samples by token probability, the minority never.
    assert majority(TRIAGE, scored, samples, 4) == [samples[1], samples[3], samples[4], samples[0]]
    assert majority(TRIAGE, scored, samples, 2) == [samples[1], samples[3]]
    assert majority(TRIAGE, scored, samples, 5) is None  # only 4 agree
    # A sample cut off by its token limit, or invalid, never fills a choice; no valid sample, no choices.
    cut = [comp(A), comp(A), comp(A, finish_reason="length")]
    assert majority(TRIAGE, score(TRIAGE, cut), cut, 3) is None
    assert majority(TRIAGE, score(TRIAGE, cut), cut, 2) == cut[:2]
    junk = [comp("not json")] * 3
    assert majority(TRIAGE, score(TRIAGE, junk), junk, 1) is None
    # The same object listed twice (a provider that repeats a choice) still counts as two samples.
    same = comp(A)
    assert majority(TRIAGE, score(TRIAGE, [same, same]), [same, same], 2) == [same, same]


@pytest.mark.req("FR-1.14", "FR-1.13")
def test_gen_plan_for_n_choices():
    policy = GenPolicy()
    one, two = policy.plan(None), policy.plan(None, 2)
    assert (one.n, two.n) == (1, 2) and one.serve.temperature == 0.0
    # n > 1 without a client temperature: the serving teacher samples, so its choices differ; audits stay at 0.
    assert two.serve.temperature == CHOICES_TEMPERATURE == 0.7 and two.check.temperature == 0.0
    assert two.student == one.student  # the student's sampling never changes
    assert policy.plan(GenParams(temperature=0.2), 3).serve.temperature == 0.2  # the client's wins
    assert policy.plan(GenParams(temperature=0.0), 3).serve.temperature == 0.0
    narrow = GenPolicy(teacher_allow=frozenset({"stop"}))  # the client's temperature does not reach the teacher
    assert narrow.plan(GenParams(temperature=0.2), 2).serve.temperature == 0.7
    assert narrow.plan(GenParams(temperature=0.2)).serve.temperature == 0.0
    # The teacher's logprobs only when the client asked; never in an audit.
    asked = policy.plan(GenParams(logprobs=True, top_logprobs=3))
    assert (asked.serve.logprobs, asked.serve.top_logprobs) == (True, 3)
    assert (asked.check.logprobs, asked.check.top_logprobs) == (None, None)
    assert (one.serve.logprobs, one.serve.top_logprobs) == (None, None)
    for bad in (0, -1, 1.5, True, "2"):
        with pytest.raises(ValueError, match="whole number of choices"):
            policy.plan(None, bad)


# ---------- certified precision (FR-16.3, ADR 0026) ----------
from ratchet import certificate  # noqa: E402
from ratchet.stats import CERT_GRID, cp_lower, mcnemar_one_sided  # noqa: E402

# Held-out points: (confidence, agrees with the teacher), in golden order.
CERT_POINTS = [(0.97, True)] * 40 + [(0.93, False)] * 2 + [(0.88, True)] * 10 + [(0.72, False)] * 6 + [(0.40, True)] * 4


@pytest.mark.req("FR-16.3", "INV-6")
def test_certificate_frontier_and_per_task_from_held_out_points():
    cert = certificate.certificate(CERT_POINTS, 0.85, target=0.85, delta=0.05)
    assert {k: cert[k] for k in ("method", "calibration", "threshold", "served", "errors", "target", "delta")} == {
        "method": "clopper-pearson",
        "calibration": "point",
        "threshold": 0.85,
        "served": 52,
        "errors": 2,
        "target": 0.85,
        "delta": 0.05,
    }
    assert cert["precision"] == 50 / 52 and cert["lcb"] == cp_lower(50, 52, 0.05) < cert["precision"]
    assert cert["certified"] is True and cert["lcb"] == pytest.approx(0.8838, abs=1e-4) and "ltt_trace" not in cert
    assert certificate.certificate(CERT_POINTS, 0.85, 0.9, 0.05)["certified"] is False  # lcb 0.884 < 0.9
    nothing = certificate.certificate(CERT_POINTS, 1.01, 0.9, 0.05, "ltt", trace=[])
    assert (nothing["served"], nothing["precision"], nothing["lcb"], nothing["certified"]) == (0, 0.0, 0.0, False)
    assert nothing["ltt_trace"] == []

    rows = certificate.frontier(CERT_POINTS, 0.05)
    assert [r["threshold"] for r in rows] == sorted(CERT_GRID, reverse=True)
    assert all(r["delta"] == 0.05 / len(CERT_GRID) for r in rows)  # Bonferroni: every row holds at once
    by = {r["threshold"]: r for r in rows}
    assert (by[0.95]["served"], by[0.95]["errors"], by[0.95]["coverage"]) == (40, 0, 40 / 62)
    assert (by[0.70]["served"], by[0.70]["errors"]) == (58, 8)
    assert by[0.85]["lcb"] == cp_lower(50, 52, 0.005) < cert["lcb"]  # the corrected bound is the wider one
    coverage = [r["coverage"] for r in sorted(rows, key=lambda r: r["threshold"])]
    assert all(a >= b for a, b in zip(coverage, coverage[1:], strict=False)), "coverage never rises with the threshold"
    assert all(r["lcb"] <= r["precision"] for r in rows)
    assert certificate.frontier([], 0.05)[0] == {
        "threshold": 0.95,
        "coverage": 0.0,
        "served": 0,
        "errors": 0,
        "precision": 0.0,
        "lcb": 0.0,
        "delta": 0.005,
    }

    names = ["a"] * 42 + ["b"] * 20
    per = certificate.per_task(CERT_POINTS, names, 0.85, 0.05)
    assert list(per) == ["a", "b"]
    assert {k: per["a"][k] for k in ("n_test", "served", "errors")} == {"n_test": 42, "served": 42, "errors": 2}
    assert {k: per["b"][k] for k in ("n_test", "served", "errors", "coverage")} == {
        "n_test": 20,
        "served": 10,
        "errors": 0,
        "coverage": 0.5,
    }
    assert per["a"]["lcb"] == cp_lower(40, 42, 0.05) and per["b"]["lcb"] == cp_lower(10, 10, 0.05)
    with pytest.raises(ValueError, match="differ in length"):
        certificate.per_task(CERT_POINTS, names[:-1], 0.85, 0.05)

    vec = certificate.outcome_vector(CERT_POINTS, 0.85)
    assert vec == "S" * 40 + "W" * 2 + "S" * 10 + "E" * 10 and len(vec) == len(CERT_POINTS)


@pytest.mark.req("FR-16.3", "FR-6.30", "INV-4")
def test_paired_counts_on_hand_built_vectors():
    live = "SSSSWWEEES"
    cand = "SWESSEWSEW"
    p = certificate.paired(live, cand)
    # Row by row: S/S, S/W (negative flip, served), S/E (only live), S/S, W/S (positive flip), W/E (only live, positive),
    # E/W (only the candidate, negative flip), E/S (only the candidate), E/E, S/W (negative flip, served).
    assert {k: p[k] for k in ("n", "cov_only_live", "cov_only_cand", "neg_flips", "pos_flips")} == {
        "n": 10,
        "cov_only_live": 2,
        "cov_only_cand": 2,
        "neg_flips": 3,
        "pos_flips": 2,
    }
    assert (p["neg_flips_served"], p["live_served_ok"], p["nfr"]) == (2, 5, 0.4)
    assert p["p_cov"] == mcnemar_one_sided(2, 2) and p["p_err"] == mcnemar_one_sided(3, 2)
    assert p["nfr_95"] == list(wilson(2, 5))
    assert certificate.paired("S" * 4, "E" * 4)["p_cov"] == mcnemar_one_sided(4, 0) == 0.0625
    same = certificate.paired("SWE", "SWE")
    assert (same["p_cov"], same["p_err"], same["nfr"], same["nfr_95"]) == (1.0, 1.0, 0.0, list(wilson(0, 1)))
    assert certificate.paired("EW", "SS")["nfr_95"] == [0.0, 1.0]  # live served nothing right: no rate to bound
    for bad in (("SS", "S"), ("SX", "SS"), ("S", 1)):
        with pytest.raises(ValueError):
            certificate.paired(*bad)


def _cert_eval() -> dict:
    """An eval as cascade_eval writes it, from CERT_POINTS, with a paired comparison against live v1."""
    return {
        "test_n": len(CERT_POINTS),
        "threshold": 0.85,
        "certificate": certificate.certificate(CERT_POINTS, 0.85, 0.85, 0.05),
        "frontier": certificate.frontier(CERT_POINTS, 0.05),
        "per_task": certificate.per_task(CERT_POINTS, ["a"] * 42 + ["b"] * 20, 0.85, 0.05),
        "test_vector": certificate.outcome_vector(CERT_POINTS, 0.85),
        "paired": {**certificate.paired("S" * 52 + "E" * 10, certificate.outcome_vector(CERT_POINTS, 0.85)), "against": 1},
    }


@pytest.mark.req("FR-16.3")
def test_recheck_recomputes_every_number_from_the_counts():
    ev = _cert_eval()
    checked, problems = certificate.recheck(json.loads(json.dumps(ev)))  # as read back from a BOM
    assert problems == [] and len(checked) == 4
    assert checked[0].startswith("certificate: 50 of 52 served answers agree at threshold 0.85")
    assert certificate.recheck({"threshold": 0.8}) == ([], [])  # nothing to check

    def problems_after(edit) -> list[str]:
        doc = json.loads(json.dumps(ev))
        edit(doc)
        return certificate.recheck(doc)[1]

    def lcb_up(d):
        d["certificate"]["lcb"] += 1e-6

    assert problems_after(lcb_up) == [
        f"certificate.lcb: stated {ev['certificate']['lcb'] + 1e-6!r}, recomputed {ev['certificate']['lcb']!r}"
    ]

    def tiny(d):
        d["certificate"]["lcb"] += 1e-12  # float rounding is not a mismatch

    assert problems_after(tiny) == []

    def fewer_errors(d):
        d["certificate"]["errors"] = 1

    found = problems_after(fewer_errors)
    assert any(p.startswith("certificate.precision") for p in found) and any("test_vector" in p for p in found)
    assert any(p.startswith("per_task: errors adds up to 2") for p in found)

    def claim(d):
        d["certificate"]["target"] = 0.99  # certified stays True, but 0.884 < 0.99

    assert problems_after(claim) == ["certificate.certified: stated True, recomputed False"]

    def frontier_lcb(d):
        d["frontier"][3]["lcb"] = 0.99

    assert [p.split(":")[0] for p in problems_after(frontier_lcb)] == ["frontier[3].lcb"]

    def uncorrected(d):
        for row in d["frontier"]:
            row["delta"], row["lcb"] = 0.05, cp_lower(row["served"] - row["errors"], row["served"], 0.05)

    assert all("delta / 10" in p for p in problems_after(uncorrected))

    def shuffled(d):
        d["frontier"][0], d["frontier"][1] = d["frontier"][1], d["frontier"][0]

    assert any("thresholds must fall" in p for p in problems_after(shuffled))

    def p_cov(d):
        d["paired"]["p_cov"] = 0.01

    assert [p.split(":")[0] for p in problems_after(p_cov)] == ["paired.p_cov"]

    def impossible(d):
        d["paired"]["cov_only_live"] = 99

    assert any("cannot come from" in p for p in problems_after(impossible))

    def other_rows(d):
        d["paired"]["n"] = 61  # the candidate's test vector has 62

    assert problems_after(other_rows) == ["paired.n: stated 61, the test vector has 62 rows"]

    def more_flips_than_wrong_answers(d):
        d["paired"]["neg_flips"] = 3  # the candidate serves only 2 wrong answers

    found = problems_after(more_flips_than_wrong_answers)
    assert any(p.startswith("paired: counts the test vector rules out (10 escalated and 2 wrong of 62") for p in found)

    def malformed(d):
        d["certificate"]["served"] = "52"

    assert problems_after(malformed)[0].startswith("certificate: certificate.served must be a whole number")

    def bad_vector(d):
        d["test_vector"] = "SSX"

    assert any("test_vector must be a string of S, W and E" in p for p in problems_after(bad_vector))


@pytest.mark.req("FR-16.3")
def test_recheck_follows_the_ltt_trace():
    from ratchet.stats import ltt_fixed_sequence

    calib = [(0.97, True)] * 80 + [(0.72, False)] * 40
    lam, trace = ltt_fixed_sequence(calib, CERT_GRID, 0.95, 0.05)
    ev = {"certificate": certificate.certificate(CERT_POINTS, lam, 0.95, 0.05, "ltt", trace)}
    assert certificate.recheck(json.loads(json.dumps(ev)))[1] == []
    low = json.loads(json.dumps(ev))
    low["certificate"] = certificate.certificate(CERT_POINTS, 0.70, 0.95, 0.05, "ltt", trace)
    assert certificate.recheck(low)[1] == ["certificate.threshold: 0.7 is below 0.75, the lowest threshold Learn-then-Test kept"]
    cooked = json.loads(json.dumps(ev))
    cooked["certificate"]["ltt_trace"][-1]["p_value"] = 0.01  # claims the last test passed
    assert [p.split(":")[0] for p in certificate.recheck(cooked)[1]] == ["certificate.ltt_trace[5].p_value"]


@pytest.mark.req("FR-16.3")
def test_certificate_status_and_section():
    ev = _cert_eval()
    sec = certificate.section(ev)
    assert sec is not None and sec["certificate"] is ev["certificate"] and sec["calibration"] == "point"
    assert sec["paired"]["against"] == 1 and sec["threshold"] == 0.85
    assert certificate.section({}) is None and certificate.section(None) is None
    old = certificate.section({"threshold": 0.8, "precision": 0.97})
    assert old == {"certificate": None, "frontier": None, "per_task": None, "threshold": 0.8, "calibration": None, "paired": None}
    no = {**ev["certificate"], "certified": False}
    assert certificate.status(sec, None, None) == "certified"
    assert certificate.status({"certificate": no}, sec, None) == "not certified"  # the latest re-score decides
    assert certificate.status(old, sec, None) == "certified"  # a re-score without a certificate: the signed one
    assert certificate.status(old, None, None) == certificate.status(None, None, None) == "no certificate"
    assert certificate.status(sec, sec, {"alarm": True}) == "suspended"
    assert certificate.status(sec, sec, {"alarm": False}) == "certified"
    assert len(certificate.ASSUMPTIONS) == 3


@pytest.mark.req("FR-16.3")
def test_versions_filter_by_state_in_order():
    led = Ledger(":memory:")
    ids = [
        _version(led, state, {"threshold": 0.8, "certificate": {"served": i}})
        for i, state in enumerate(["live", "canary", "rejected", "live"])
    ]
    assert [v.id for v in led.versions(["live", "canary"])] == [ids[0], ids[1], ids[3]]
    assert [v.id for v in led.versions(iter(["rejected"]))] == [ids[2]] and led.versions([]) == []
    assert led.version(ids[1]).eval["certificate"] == {"served": 1} and led.version(999) is None
    assert led.live().id == ids[3] and led.by_state("canary").id == ids[1]
