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


BAD_DEPLOY = ("--days", "6", "--requests", "1500", "--drift-day", "0", "--bad-deploy-version", "3")


@pytest.fixture(scope="module")
def bad_deploy() -> dict:
    """The corrupted-v3 run under each canary rule (the same seed), shared by the tests below: about 4 s each."""
    return {rule: sim(*BAD_DEPLOY, "--release", rule) for rule in ("wilson", "sequential")}


@pytest.mark.req("FR-9.3", "US-6")
def test_bad_deploy_is_rolled_back_and_users_return_to_live(bad_deploy):
    res = bad_deploy["wilson"]
    states = {v["id"]: v["state"] for v in res["versions"]}
    kinds = [e["kind"] for e in res["events"]]
    if states.get(3) == "rejected":
        pytest.skip("v3 was rejected before deploy in this seed; nothing to roll back")
    assert states.get(3) == "rolled_back"
    assert states.get(1) == "live"  # traffic went back to the previous live version
    assert "rollback" in kinds


@pytest.mark.req("FR-16.1", "FR-9.4")
def test_sequential_gate_reduces_bad_deploy_exposure(bad_deploy):
    """The same corrupted deploy under the anytime-valid rule (simulation, mock models): rolled back as under Wilson,
    after serving no more answers, with the evidence in the event; and the run records each rollback's exposure."""
    wilson, sequential = bad_deploy["wilson"], bad_deploy["sequential"]
    states = {rule: {v["id"]: v["state"] for v in res["versions"]} for rule, res in bad_deploy.items()}
    if "rejected" in (states["wilson"].get(3), states["sequential"].get(3)):
        pytest.skip("v3 was rejected before deploy in this seed; nothing to roll back")
    assert states["wilson"][3] == states["sequential"][3] == "rolled_back"
    assert sequential["exposure"][3] <= wilson["exposure"][3]
    for res in (wilson, sequential):
        (row,) = [r for r in res["rollbacks"] if r["version"] == 3]
        assert row["served"] == res["exposure"][3] == res["served"][3] > 0 and 0 < row["agreed"] < row["audits"]
    (detail,) = [e["detail"] for e in sequential["events"] if e["kind"] == "rollback"]
    assert detail.startswith("v3 rolled back: ") and "e-value" in detail and "95% CS [" in detail
    assert not any(e["kind"] == "inconclusive" for e in wilson["events"])


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


# ---------- certified precision (FR-16.3, ADR 0026) ----------
CERT_RUN = ("--days", "3", "--requests", "700", "--drift-day", "0")


@pytest.fixture(scope="module")
def cert_run():
    """A short run and its ledger (its BOMs are read back), shared by the tests below: about 1 s."""
    import sim.simulate as simulate
    from ratchet.ledger import Ledger

    made: list[Ledger] = []

    class Kept(Ledger):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            made.append(self)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(simulate, "Ledger", Kept)
        res = sim(*CERT_RUN)
    return res, made[0]


@pytest.mark.req("FR-16.3", "INV-14")
def test_every_version_carries_a_certificate_in_its_eval_and_bom(cert_run):
    from ratchet import certificate

    res, led = cert_run
    assert res["versions"] and len(led.versions()) == len(res["versions"])
    for v in led.versions():
        signed = led.version_bom(v.id)["manifest"]["eval"]
        for ev in (v.eval, signed):
            cert = ev["certificate"]
            assert cert["lcb"] <= cert["precision"] and cert["served"] == len(ev["test_vector"]) - ev["test_vector"].count("E")
            assert len(ev["frontier"]) == 10 and ev["per_task"] and len(ev["test_vector"]) == ev["test_n"]
            assert certificate.recheck(ev)[1] == []
        if "rechecked_day" not in v.eval:  # never re-scored: the eval is the one signed at training
            assert v.eval == signed


@pytest.mark.req("FR-16.3", "FR-9.2")
def test_certificates_move_no_simulation_number(cert_run, monkeypatch):
    """The certificate code draws no random number and decides nothing by default, so the same run with it taken out
    of the golden eval has the same days, versions and decisions (simulation, mock models)."""
    from types import SimpleNamespace

    from ratchet.nightschool import evaluate

    res, _ = cert_run
    stub = SimpleNamespace(
        certificate=lambda *a, **k: {},
        frontier=lambda *a, **k: [],
        per_task=lambda *a, **k: {},
        outcome_vector=lambda *a, **k: "",
    )
    monkeypatch.setattr(evaluate, "certificate", stub)
    bare = sim(*CERT_RUN)
    assert bare["days"] == res["days"] and bare["totals"] == res["totals"]
    keys = ("id", "state", "traffic_pct", "train_examples", "dataset_hash")
    assert [{k: v[k] for k in keys} for v in bare["versions"]] == [{k: v[k] for k in keys} for v in res["versions"]]
    assert [v["eval"]["threshold"] for v in bare["versions"]] == [v["eval"]["threshold"] for v in res["versions"]]
    assert all(v["eval"]["certificate"] == {} for v in bare["versions"])
    # Events only gain appended text (the certificate of a certified version).
    assert [(e["day"], e["kind"]) for e in bare["events"]] == [(e["day"], e["kind"]) for e in res["events"]]
    assert all(e["detail"].startswith(b["detail"]) for b, e in zip(bare["events"], res["events"], strict=True))


@pytest.mark.req("FR-16.3")
def test_ltt_thresholds_come_from_the_fixed_grid():
    from ratchet.stats import CERT_GRID, SERVE_NOTHING

    res = sim(*CERT_RUN, "--calibration", "ltt", "--cert-target", "0.9")
    assert res["versions"]
    for v in res["versions"]:
        ev = v["eval"]
        assert ev["threshold"] in (*CERT_GRID, SERVE_NOTHING)
        assert ev["certificate"]["calibration"] == "ltt" and ev["certificate"]["target"] == 0.9
        assert ev["certificate"]["ltt_trace"][0]["threshold"] == max(CERT_GRID)


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
