"""End-to-end tests with mock models: the full loop, the gateway API, and the provider client."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from ratchet.providers import OpenAICompatProvider
from sim.simulate import parser, run


def sim(*argv):
    args = parser().parse_args(list(argv))
    if args.drift_day == 0:
        args.drift_day = None
    return run(args)


@pytest.mark.req("US-2", "US-3", "US-4", "US-9", "FR-2.4", "FR-2.5", "FR-9.2")
def test_loop_learns_and_saves_money():
    res = sim("--days", "6", "--requests", "1500", "--drift-day", "0")
    days = res["days"]
    assert days[0]["student_share"] == 0  # cold start: all teacher
    assert days[-1]["student_share"] > 0.8  # student takes over
    assert days[-1]["savings_pct"] > 0.6
    assert res["totals"]["saved_usd"] > 0
    assert any(v["state"] == "live" for v in res["versions"])
    # audited quality of what the student served stays high
    audits = [d["audit_agreement"] for d in days if d["audit_agreement"] is not None]
    assert min(audits) >= 0.85


@pytest.mark.req("FR-9.3", "US-6")
def test_bad_deploy_is_rolled_back_and_users_return_to_live():
    res = sim("--days", "6", "--requests", "1500", "--drift-day", "0", "--bad-deploy-version", "3")
    states = {v["id"]: v["state"] for v in res["versions"]}
    kinds = [e["kind"] for e in res["events"]]
    if states.get(3) == "rejected":
        pytest.skip("v3 was rejected before deploy in this seed; nothing to roll back")
    assert states.get(3) == "rolled_back"
    assert states.get(1) == "live"  # traffic went back to the previous live version
    assert "rollback" in kinds


@pytest.mark.req("FR-9.1")
def test_drift_raises_escalations_then_recovers():
    res = sim("--days", "9", "--requests", "1500", "--drift-day", "5")
    d = {x["day"]: x for x in res["days"]}
    assert d[5]["escalation_rate"] > d[4]["escalation_rate"]
    assert d[9]["escalation_rate"] < d[5]["escalation_rate"]


def test_reference_simulation_unchanged(reference_run, sim_reference):
    """The one exact-number test: the pinned reference run (seed 7, 10 days x 2,000, drift day 5, corrupted v3)
    must reproduce tests/fixtures/sim_reference_seed7.json, so a change can show it leaves legacy behaviour
    byte-identical. If a change is meant to move it, run scripts/regen_sim_reference.py and add a CHANGELOG line."""
    want, have = sim_reference.load(), sim_reference.snapshot(reference_run)
    # A changed or removed simulator default, not a behaviour change. A flag added later is not compared, so an option
    # whose default keeps the old behaviour does not force a regeneration.
    pinned = want["inputs"]
    assert sim_reference.pinned_inputs(pinned, have["inputs"]) == pinned
    assert sim_reference.pinned_inputs(pinned, {**have["inputs"], "a_new_flag": "default"}) == pinned
    assert sim_reference.pinned_inputs(pinned, {**pinned, "seed": 8}) != pinned
    assert sim_reference.pinned_inputs(pinned, {k: v for k, v in pinned.items() if k != "workload"}) != pinned
    assert have["events"] == want["events"]
    assert have["versions"] == want["versions"]
    assert have["days"] == want["days"]
    assert set(have) == set(want)
    assert sim_reference.matches(want, have)  # the same rule `regen_sim_reference.py --check` applies


def test_reference_workloads_reproduce_v040(reference_run, reference_check):
    """scripts/reference_check.py pins three workloads captured from v0.4.0 (tickets, mixed, research). The tickets run
    is the session's reference simulation, so it is checked here at no extra cost; CI and `make reference` run all
    three. Every input the fixture pins is still the simulator's default, for all three."""
    doc = reference_check.load()
    assert doc["captured_from"].startswith("v0.4.0") and set(doc["configs"]) == set(reference_check.CONFIGS)
    fresh = reference_check.summarize("tickets-seed7", reference_run)
    assert reference_check.compare("tickets-seed7", doc["configs"]["tickets-seed7"], fresh) == []
    for name, entry in doc["configs"].items():
        assert entry["argv"] == list(reference_check.CONFIGS[name])
        assert reference_check.compare(name, entry, {**entry, "inputs": reference_check.inputs(name)}) == []


def test_reference_check_rules(reference_check):
    """What counts as unchanged: floats within a relative 1e-9; new day keys, new totals keys and advisory event kinds
    ignored; appended event text allowed; anything else reported, one line per difference."""
    want = reference_check.load()["configs"]["mixed-seed1"]
    compare = reference_check.compare

    def changed(**edits: object) -> dict:
        have = json.loads(json.dumps(want))
        for key, fn in edits.items():
            fn(have[key])  # type: ignore[operator]
        return have

    assert compare("m", want, changed()) == []
    noisy = changed(days=lambda d: d[3].update(cost_per_1k=d[3]["cost_per_1k"] * (1 + 1e-12), new_key=1))
    assert compare("m", want, noisy) == []
    assert compare("m", want, changed(totals=lambda t: t.update(another=2))) == []
    advisory = changed(events=lambda e: e.insert(2, [3, "drift", 1, "v1 (ticket-triage): traffic changed"]))
    assert compare("m", want, advisory) == []
    appended = changed(events=lambda e: e[0].__setitem__(3, e[0][3] + "; certified >= 90%"))
    assert compare("m", want, appended) == []
    share = want["days"][3]["student_share"]
    moved = changed(days=lambda d: d[3].update(student_share=share + 0.001))
    assert compare("m", want, moved) == [f"m: day 4 student_share: pinned {share!r}, now {share + 0.001!r}"]
    assert len(compare("m", want, changed(days=lambda d: d[2].pop("audit_agreement")))) == 1
    assert len(compare("m", want, changed(days=lambda d: d.pop()))) == 1
    reworded = changed(events=lambda e: e[0].__setitem__(3, "v1 on many examples"))
    assert ["text changed" in line for line in compare("m", want, reworded)] == [True]
    extra = changed(events=lambda e: e.append([8, "alert", 2, "live v2 audited precision 80.0% (n=20) below floor"]))
    assert len(compare("m", want, extra)) == 1
    assert len(compare("m", want, changed(versions=lambda v: v[0].update(state="rolled_back")))) == 1
    assert len(compare("m", want, changed(inputs=lambda i: i.update(audit_rate=0.2)))) == 1
    assert compare("m", want, changed(inputs=lambda i: i.update(a_new_flag=True))) == []


@pytest.mark.req("FR-3.3", "US-7", "INV-1")
def test_provenance_only_lists_distillable_teachers():
    res = sim("--days", "2", "--requests", "300", "--drift-day", "0")
    for src in res["bill_of_materials"]["sources"]:
        assert src["teacher_model"] == "nvidia/Nemotron-3-Ultra-550b-a55b"
        assert src["license"] == "OpenMDW-1.1"


# ---------- gateway ----------
@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("RATCHET_MODE", "mock")
    import importlib

    import ratchet.gateway as gw

    importlib.reload(gw)
    return TestClient(gw.app), gw


@pytest.mark.req("FR-1.1", "FR-1.3", "US-1", "US-11", "FR-7.5", "NFR-6")
def test_gateway_openai_shape_and_demo_flow(client):
    c, gw = client
    assert c.get("/").status_code == 200
    r = c.post("/ratchet/demo/day?requests=400")
    assert r.status_code == 200
    t = gw.rt.stream.sample(gw.rt.ledger.day)
    resp = c.post(
        "/v1/chat/completions", json={"model": "ratchet", "messages": t.messages()}, headers={"X-Ratchet-Task": "ticket-triage"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "chat.completion"
    assert json.loads(body["choices"][0]["message"]["content"])["category"] == t.category
    assert body["ratchet"]["served_by"] in ("student", "teacher")
    stats = c.get("/ratchet/stats").json()
    assert stats["totals"]["requests"] == 401
    assert c.post("/ratchet/demo/break-next").json()["corrupt"].startswith("student-v")
    assert c.post("/ratchet/demo/reset").json() == {"status": "reset"}


@pytest.mark.req("FR-1.5", "FR-1.6")
def test_gateway_rejects_bad_requests(client):
    c, _ = client
    assert c.post("/v1/chat/completions", json={"messages": []}).status_code == 400
    # Streaming is supported since v0.4, but errors still arrive as HTTP errors before the stream starts.
    r = c.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "x"}], "stream": True})
    assert r.status_code == 400 and "mock mode only answers" in r.json()["detail"]


# ---------- provider client against a fake Token Factory ----------
@pytest.mark.req("FR-8.1", "NFR-7")
def test_openai_compat_provider_parses_logprobs_and_usage():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["logprobs"] is True and body["n"] == 2
        assert request.headers["authorization"] == "Bearer k"
        return httpx.Response(
            200,
            json={
                "model": body["model"],
                "usage": {"prompt_tokens": 12, "completion_tokens": 8},
                "choices": [
                    {"message": {"content": "a"}, "logprobs": {"content": [{"logprob": -0.1}, {"logprob": -0.2}]}},
                    {"message": {"content": "b"}, "logprobs": None},
                ],
            },
        )

    p = OpenAICompatProvider("https://example.test/v1", "k")
    p.client = httpx.Client(transport=httpx.MockTransport(handler), headers={"Authorization": "Bearer k"})
    out = p.complete([{"role": "user", "content": "hi"}], "nvidia/x", n=2, logprobs=True)
    assert [o.text for o in out] == ["a", "b"]
    assert out[0].token_logprobs == [-0.1, -0.2] and out[1].token_logprobs == []
    assert out[0].in_tokens == 12 and out[0].out_tokens == 4
    assert 0.8 < out[0].mean_prob < 0.9
