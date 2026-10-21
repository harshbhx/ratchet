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


def sim_kept(*argv):
    """`sim`, and the run's ledger (its BOMs and committed leaves are read back)."""
    import sim.simulate as simulate
    from ratchet.ledger import Ledger

    made: list[Ledger] = []

    class Kept(Ledger):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            made.append(self)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(simulate, "Ledger", Kept)
        res = sim(*argv)
    return res, made[0]


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
def bad_deploy_runs() -> dict:
    """The corrupted-v3 run under each canary rule (the same seed) and its ledger, shared by the tests below: about 4 s
    each."""
    return {rule: sim_kept(*BAD_DEPLOY, "--release", rule) for rule in ("wilson", "sequential")}


@pytest.fixture(scope="module")
def bad_deploy(bad_deploy_runs) -> dict:
    return {rule: res for rule, (res, _) in bad_deploy_runs.items()}


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


@pytest.mark.req("INV-3", "INV-6", "FR-16.4")
def test_every_version_proves_its_golden_rows_were_never_trained_on(bad_deploy_runs):
    """INV-3 machine-checked over six simulated days with a corrupted deploy: for every version, the committed leaves
    rebuild the roots its BOM signs, and every golden key has a proof of absence from that version's signed training
    root (simulation, mock models)."""
    from ratchet import bom, merkle, proof

    res, led = bad_deploy_runs["wilson"]
    versions = led.versions()
    assert len(versions) == len(res["versions"]) >= 3 and {v.state for v in versions} & {"rolled_back", "live"}
    for v in versions:
        doc = led.version_bom(v.id)
        train = merkle.KeySet(led.version_leaves(v.id, "train"))
        golden = merkle.KeySet(led.version_leaves(v.id, "golden"))
        assert (train.root_hex, train.size) == tuple(bom.commitment(doc["manifest"], "train")[k] for k in ("root", "size"))
        assert (golden.root_hex, golden.size) == tuple(bom.commitment(doc["manifest"], "golden")[k] for k in ("root", "size"))
        assert golden.size == doc["manifest"]["golden"]["examples"] > 0 and train.size == v.train_examples
        assert not set(train.keys()) & set(golden.keys())  # brute force
        cert = {
            "golden": {"leaves": [[k.hex(), val.hex()] for k, val in golden.items()]},
            "proofs": {k.hex(): train.prove(k) for k in golden.keys()},
        }
        ok, lines = proof.verify_heldout(cert, doc)  # from the signed roots alone
        assert ok, (v.id, lines)


@pytest.mark.req("FR-16.5", "FR-6.5")
def test_replay_reproduces_each_versions_logged_coverage_and_audits(bad_deploy_runs):
    """The ledger replay over six simulated days with a corrupted deploy (simulation, mock models): for every version,
    replaying each request at the threshold it was logged with serves exactly the requests the student served, every
    group of requests logged at one threshold reproduces its coverage at that threshold, and the replayed precision is
    the audited precision the release gate counted, inside its interval. Nothing was left out."""
    from ratchet import replay

    _, led = bad_deploy_runs["wilson"]
    checked = 0
    for v in led.versions():
        h = replay.load(led, v.id)
        assert h.excluded == {"before_logging": 0, "no_student_answer": 0, "pinned_now": 0, "malformed": 0}
        if not h.rows:
            continue
        served = [r for r in h.rows if r.route == "student"]
        actual = replay.at(h.rows)
        assert actual["served"] == len(served) and actual["coverage"] == len(served) / len(h.rows)
        for t in {r.threshold for r in h.rows}:
            group = [r for r in h.rows if r.threshold == t]
            assert replay.at(group, t)["served"] == sum(1 for r in group if r.route == "student"), (v.id, t)
        n, s = led.audit_counts(v.id)
        assert actual["labelled"] == n > 0 and actual["precision"] == pytest.approx(s / n, abs=1e-12)
        assert actual["interval"][0] <= s / n <= actual["interval"][1]
        checked += 1
    assert checked >= 2  # the live version and the rolled-back v3


@pytest.mark.req("FR-16.5", "FR-6.4", "INV-15")
def test_label_p_and_student_agree_follow_the_design(bad_deploy_runs):
    """Over the same run: `label_p` is the audit rate on every answer the student served and 1.0 on every escalation
    with a valid student answer; `student_agree` equals `audit_agree` on audited rows, is set on escalations the teacher
    answered, and never on a served row where only a shadow arbitration called the teacher (not a random draw)."""
    _, led = bad_deploy_runs["wilson"]
    rows = [dict(r) for r in led.db.execute("SELECT * FROM requests WHERE student_version IS NOT NULL")]
    arbitrated = 0
    for r in rows:
        if r["route"] == "student":
            assert r["label_p"] == 0.10 and r["threshold"] is not None and r["confidence"] >= r["threshold"]
            if r["audited"]:
                assert r["student_agree"] == r["audit_agree"] is not None
            else:
                assert r["student_agree"] is None
                arbitrated += r["teacher_cost_usd"] > 0  # the teacher ran for the shadow's disagreement only
        else:
            assert r["route"] == "confidence" and r["label_p"] == 1.0 and r["confidence"] < r["threshold"]
            assert (r["student_agree"] is not None) == (r["confidence"] > 0)  # the mock teacher's answers are valid
        assert r["baseline_est_usd"] > 0
    assert arbitrated > 0 and sum(r["route"] == "confidence" for r in rows) > 0


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
    return sim_kept(*CERT_RUN)


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


@pytest.mark.req("FR-16.5", "NFR-11")
def test_replay_endpoints_on_the_mock_gateway(gw_env):
    """GET /ratchet/replay and /ratchet/replay/frontier on the mock gateway after two simulated days: one version at a
    time (the live one by default, else named), what happened next to the counterfactual, the calibrated baseline, the
    frontier with its advisory suggestion, labelled as simulation, finite JSON. Bad parameters are 400s."""
    gw = gw_env()
    c = TestClient(gw.app)
    assert c.get("/ratchet/replay").status_code == 400  # cold start: no live version
    for _ in range(2):
        assert c.post("/ratchet/demo/day?requests=300").status_code == 200
    (canary,) = gw.rt.ledger.versions(["canary"])  # the first student is in its canary stage, so there is no live one
    assert gw.rt.ledger.live() is None and c.get("/ratchet/replay").json()["detail"].startswith("no live version")
    live = canary
    r = c.get(f"/ratchet/replay?version={live.id}")
    assert r.status_code == 200
    body = json.loads(r.text, parse_constant=lambda x: pytest.fail(f"non-finite JSON number {x}"))
    assert body["version"] == live.id and body["state"] == "canary" and body["label"] == "simulation (mock models)"
    assert body["n"] > 0 and body["excluded"] == 0 and body["cost_model"] == "per_token"
    assert body["current_threshold"] == gw.rt.ratchet.threshold(live) == body["counterfactual"]["threshold"]
    assert body["actual"]["threshold"] is None and 0 < body["actual"]["coverage"] <= 1
    assert body["calibrated_baseline"]["method"].startswith("ratio estimator")
    low = c.get(f"/ratchet/replay?version={live.id}&threshold=0.4&audit_rate=0&since_day=1&until_day=9").json()
    assert low["counterfactual"]["threshold"] == 0.4 and low["counterfactual"]["audit_rate"] == 0.0
    assert low["counterfactual"]["coverage"] >= body["counterfactual"]["coverage"]
    f = c.get(f"/ratchet/replay/frontier?version={live.id}&target=0.9").json()
    assert f["version"] == live.id and f["target"] == 0.9 and len(f["rows"]) == 71
    assert [row["threshold"] for row in f["rows"]][:3] == [0.3, 0.31, 0.32] and f["label"] == "simulation (mock models)"
    assert f["suggested"] is None or f["suggested"]["advisory"].startswith("approximate interval, not a certificate")
    v = f"version={live.id}"
    for query, status in (
        (f"{v}&threshold=0", 400),
        (f"{v}&threshold=1.5", 400),
        (f"{v}&audit_rate=2", 400),
        (f"{v}&since_day=3&until_day=2", 400),
        (f"{v}&since_day=-1", 400),
        ("version=all", 400),
        ("version=99999", 404),
    ):
        assert c.get(f"/ratchet/replay?{query}").status_code == status, query
    assert c.get(f"/ratchet/replay/frontier?version={live.id}&target=1").status_code == 400


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
