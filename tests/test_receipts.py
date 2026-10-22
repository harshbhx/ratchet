"""Verified savings receipts (FR-15.5, ADR 0029): leaves that hold metadata only, the design-based ratio estimator and
its lower bound (synthetic populations, seeded), billing on the bound, exact integer sums, sealing once with no UPDATE,
signatures and the chain across days, per-client receipts and key scoping over HTTP, inclusion proofs for the request id
a client saw, edits after sealing caught, the mock teacher's reasoning that the receipts measure, the simulator's
`--receipts` (no other number moves) and the `python -m ratchet.receipt` and `python -m ratchet.live seal` commands."""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fakes import _request
from fastapi.testclient import TestClient

from ratchet import bom, live, merkle, receipts, router
from ratchet import ledger as ledger_module
from ratchet.config import Settings
from ratchet.ledger import Ledger, ReceiptExists
from sim import simulate
from sim.mock_models import MockTeacher
from sim.world import make_stream

pytestmark = pytest.mark.req("FR-15.5")

TASK = "ticket-triage"
ROOT = Path(__file__).resolve().parents[1]
KEYS = {"alice": "alice-key-0123456789", "bob": "bob-key-0123456789abc"}
ADMIN = "ops-key-0123456789abcdef"


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _population(
    led: Ledger, rng: random.Random, n: int = 400, rate: float = 0.15, ratio: float = 1.3, noise: float = 0.1, **kw: Any
) -> float:
    """`n` answers the student served on the ledger's current day, each audited with probability `rate`: x (the formula)
    uniform in $0.0008 to $0.0012, y (the real teacher bill) = ratio * x * (1 + noise * z). An audited row's baseline is
    its bill, the others' the formula, as the router logs them. Returns the true total of y in USD."""
    true = 0.0
    for _ in range(n):
        x = rng.uniform(0.0008, 0.0012)
        y = ratio * x * (1 + noise * rng.gauss(0.0, 1.0))
        true += y
        audited = rng.random() < rate
        _request(led, audited=int(audited), baseline_cost_usd=y if audited else x, baseline_est_usd=x, label_p=rate, **kw)
    return true


def _leaves(
    rng: random.Random, n: int = 300, rate: float = 0.2, ratio: float = 1.3, noise: float = 0.1
) -> tuple[list[dict], int]:
    """`_population` as leaves only, without a ledger (fast enough for a Monte Carlo run): (leaves, true total nano-USD)."""
    leaves, true = [], 0
    for i in range(n):
        x = receipts.nusd(rng.uniform(0.0008, 0.0012))
        y = round(x * ratio * (1 + noise * rng.gauss(0.0, 1.0)))
        true += y
        audited = rng.random() < rate
        leaves.append(
            {
                **dict.fromkeys(receipts.LEAF_KEYS),
                "id": i + 1,
                "day": 1,
                "task": TASK,
                "served_by": "student",
                "route": "student",
                "audited": int(audited),
                "label_p_ppb": round(rate * receipts.NANO),
                "cost_nusd": 200_000,
                "baseline_nusd": y if audited else x,
                "baseline_est_nusd": x,
            }
        )
    return leaves, true


def _sealed_ledger(days: int = 2, clients: tuple[str | None, ...] = (None,)) -> Ledger:
    """A ledger with `days` days of synthetic traffic from each client (served, audited and escalated answers), then
    moved to the next day, so every one of those days can be sealed."""
    led = Ledger(":memory:")
    rng = random.Random(5)
    for day in range(1, days + 1):
        led.day = day
        for client in clients:
            _population(led, rng, n=120, rate=0.4, client=client)
            for _ in range(10):
                _request(led, served_by="teacher", cost_usd=0.0011, baseline_cost_usd=0.001, client=client)
    led.day = days + 1
    return led


# ---------- leaves ----------
@pytest.mark.req("INV-12", "INV-13")
def test_leaves_hold_only_metadata_and_the_key_name(gw_env):
    """A request's leaf has exactly LEAF_KEYS, whole numbers and strings only; no receipt, leaf or proof holds the
    request's text, and a client is named by its key's name, never the key."""
    gw = gw_env(RATCHET_API_KEYS=f"alice:{KEYS['alice']}")
    c = TestClient(gw.app)
    item = make_stream("tickets", seed=3).sample(1)
    text = item.messages()[-1]["content"]
    r = c.post(
        "/v1/chat/completions", json={"messages": item.messages()}, headers={**_bearer(KEYS["alice"]), "X-Ratchet-Task": TASK}
    )
    assert r.status_code == 200
    rid = int(r.headers["X-Ratchet-Request-Id"])
    gw.rt.ledger.day += 1  # the day is over
    doc = c.get("/ratchet/receipts/0", headers=_bearer(KEYS["alice"])).json()
    leaves = [json.loads(line) for line in c.get("/ratchet/receipts/0/leaves", headers=_bearer(KEYS["alice"])).text.splitlines()]
    p = c.get(f"/ratchet/receipts/0/proof/{rid}", headers=_bearer(KEYS["alice"])).json()
    assert [lf["id"] for lf in leaves] == [rid] and set(leaves[0]) == set(receipts.LEAF_KEYS)
    assert all(v is None or (isinstance(v, (int, str)) and not isinstance(v, bool)) for v in leaves[0].values())
    assert leaves[0]["client"] == "alice" and leaves[0]["task"] == TASK
    for blob in (json.dumps(doc), json.dumps(leaves), json.dumps(p)):
        assert text not in blob and item.text not in blob and KEYS["alice"] not in blob
        for word in ("name", "product"):
            value = getattr(item, word, None)
            assert not isinstance(value, str) or value not in blob
    assert receipts.check_manifest(doc) == []  # no float anywhere in what is signed


def test_leaf_turns_amounts_into_whole_nano_usd():
    row = {
        "id": 7,
        "ts": 1.2345678,
        "day": 3,
        "task": TASK,
        "client": "",
        "served_by": "student",
        "route": "student",
        "student_version": 2,
        "escalated": 0,
        "audited": 1,
        "label_p": 0.05,
        "cost_usd": 0.0012345678,
        "student_cost_usd": float("nan"),
        "teacher_cost_usd": None,
        "baseline_cost_usd": 1e-9 * 2.5,
        "baseline_est_usd": 3,
    }
    lf = receipts.leaf(row)
    assert lf["ts_ms"] == 1235 and lf["client"] is None and lf["label_p_ppb"] == 50_000_000
    assert (lf["cost_nusd"], lf["student_cost_nusd"], lf["teacher_cost_nusd"]) == (1234568, None, None)
    assert (lf["baseline_nusd"], lf["baseline_est_nusd"]) == (2, 3_000_000_000)  # round half even, an int in USD
    assert receipts.leaf_bytes(lf) == bom.canonical(lf) and b" " not in receipts.leaf_bytes(lf)


# ---------- the estimator and the bill ----------
def test_ratio_estimator_recovers_the_ratio_and_its_lower_bound_covers():
    """y = 1.3 x (1 + noise), audited at random: R is close to 1.3, and the one-sided 95% lower bound on the served
    answers' total covers the true total in at least 90% of 200 seeded populations (it should be about 95%)."""
    ratios, covered = [], 0
    for trial in range(200):
        leaves, true = _leaves(random.Random(trial))
        task = receipts.calibrate(leaves, min_audits=30)[TASK]
        assert task["status"] == receipts.OK
        ratios.append(float(task["ratio"]))
        lower = task["point_nusd"] - receipts.margin([task["se_nusd"]])
        covered += lower <= true
    mean = sum(ratios) / len(ratios)
    assert abs(mean - 1.3) < 0.01 and min(ratios) > 1.2 and max(ratios) < 1.4
    assert covered >= 180, covered


def test_weights_follow_label_p_and_the_finite_population_correction():
    """Rows audited at two rates are weighted 1 / label_p (Horvitz-Thompson); with every served answer audited the
    variance is 0 and the point is the exact total."""
    rng = random.Random(9)
    led = Ledger(":memory:")
    for i in range(200):
        x = rng.uniform(0.0008, 0.0012)
        rate = 0.5 if i % 2 else 0.1
        audited = rng.random() < rate
        y = (1.6 if rate == 0.5 else 1.1) * x
        _request(led, audited=int(audited), baseline_cost_usd=y if audited else x, baseline_est_usd=x, label_p=rate)
    leaves = [receipts.leaf(r) for r in led.period_rows(0)]
    est = receipts.calibrate(leaves, min_audits=10)[TASK]
    sample = [lf for lf in leaves if lf["audited"]]
    w = [receipts.NANO / lf["label_p_ppb"] for lf in sample]
    want = sum(wi * lf["baseline_nusd"] for wi, lf in zip(w, sample, strict=True)) / sum(
        wi * lf["baseline_est_nusd"] for wi, lf in zip(w, sample, strict=True)
    )
    assert float(est["ratio"]) == pytest.approx(want, abs=1e-6) and float(est["n_eff"]) < est["n_audits"]
    every = Ledger(":memory:")
    true = 0
    for _ in range(50):
        x = rng.uniform(0.0008, 0.0012)
        _request(every, audited=1, baseline_cost_usd=1.3 * x, baseline_est_usd=x, label_p=1.0)
        true += receipts.nusd(1.3 * x)
    full = receipts.calibrate([receipts.leaf(r) for r in every.period_rows(0)], min_audits=10)[TASK]
    assert full["se_nusd"] == 0 and abs(full["point_nusd"] - true) <= 50  # rounding of the per-row amounts only


def test_insufficient_tasks_are_not_billed_and_the_fee_is_a_share_of_the_lower_bound():
    led = Ledger(":memory:")
    rng = random.Random(2)
    _population(led, rng, n=300, rate=0.2)  # ticket-triage: about 60 audits
    _population(led, rng, n=40, rate=0.1, task="page-read")  # about 4 audits: insufficient
    for _ in range(20):
        _request(led, served_by="teacher", cost_usd=0.0012, baseline_cost_usd=0.001)
    _request(led, route="degraded", baseline_est_usd=0.001)  # a stand-in answer: never billed
    leaves = [receipts.leaf(r) for r in led.period_rows(0)]
    out = receipts.compute(leaves, share=0.25, min_audits=30)
    tasks, bill, sav = out["calibration"]["tasks"], out["billing"], out["savings"]
    assert (tasks[TASK]["status"], tasks["page-read"]["status"]) == (receipts.OK, receipts.INSUFFICIENT)
    assert bill["billable"]["tasks"] == [TASK] and bill["basis"] == receipts.BASIS
    assert bill["billable"]["requests"] == 300 + 20 and out["totals"]["unverified"] == 1
    teacher_rows = [lf for lf in leaves if lf["served_by"] == "teacher"]
    served = [lf for lf in leaves if lf["task"] == TASK and lf["served_by"] == "student" and lf["route"] != "degraded"]
    baseline = sum(lf["baseline_nusd"] for lf in teacher_rows) + tasks[TASK]["point_nusd"]
    cost = sum(lf["cost_nusd"] for lf in teacher_rows + served)
    assert (bill["billable"]["baseline_nusd"], bill["billable"]["cost_nusd"]) == (baseline, cost)
    assert sav["margin_nusd"] == math.ceil(1.645 * tasks[TASK]["se_nusd"])
    assert sav["billable_lo_nusd"] == baseline - cost - sav["margin_nusd"] == sav["net_billable_lo_nusd"]
    assert bill["fee_nusd"] == sav["net_billable_lo_nusd"] // 4 > 0
    # the insufficient task and the stand-in count at their recorded formula in the calibrated baseline, unbilled
    page = sum(lf["baseline_nusd"] for lf in leaves if lf["task"] == "page-read")
    degraded = sum(lf["baseline_nusd"] for lf in leaves if lf["route"] == "degraded")
    assert out["totals"]["baseline_calibrated_nusd"] == baseline + page + degraded
    # spend outside requests comes off the bound; a negative bound bills nothing
    spent = receipts.compute(leaves, share=0.25, min_audits=30, spend_nusd=sav["billable_lo_nusd"] + 1)
    assert spent["savings"]["net_billable_lo_nusd"] == -1 and spent["billing"]["fee_nusd"] == 0
    assert receipts.compute(leaves, share="0.3", min_audits=30)["billing"]["fee_nusd"] == sav["net_billable_lo_nusd"] * 3 // 10
    # no calibrated task: billed on nothing, whatever the formula says
    formula = receipts.compute(leaves, share=0.25, min_audits=1000)
    assert formula["billing"]["basis"] == receipts.FORMULA and formula["billing"]["fee_nusd"] == 0
    assert formula["billing"]["billable"]["tasks"] == [] and formula["savings"]["margin_nusd"] == 0
    for bad in ({"share": 1.5}, {"share": "x"}, {"min_audits": 1}, {"spend_nusd": -1}):
        with pytest.raises(ValueError):
            receipts.compute(leaves, **{"share": 0.25, "min_audits": 30, **bad})


def test_stand_in_answers_are_never_billed():
    """A degraded answer (the student standing in for an unavailable teacher, never audited) and a fallback teacher's
    answer (its baseline is the primary teacher's price on another model's tokens) are outside the audit design: counted
    at their recorded baseline, never billed, and never part of a task's calibration."""
    assert receipts.UNVERIFIED_ROUTES == router.STAND_IN_ROUTES
    led = Ledger(":memory:")
    _population(led, random.Random(3), n=200, rate=0.3)
    plain = receipts.compute([receipts.leaf(r) for r in led.period_rows(0)], min_audits=30)
    _request(led, route="degraded", escalated=1, label_p=1.0, baseline_est_usd=0.005, baseline_cost_usd=0.005)
    _request(led, served_by="teacher", route="fallback_teacher", cost_usd=0.0001, baseline_cost_usd=0.009)
    out = receipts.compute([receipts.leaf(r) for r in led.period_rows(0)], min_audits=30)
    assert out["totals"]["unverified"] == 2 and out["totals"]["teacher_served"] == 0
    assert out["calibration"]["tasks"] == plain["calibration"]["tasks"]
    assert out["billing"] == plain["billing"] and out["savings"]["billable_lo_nusd"] == plain["savings"]["billable_lo_nusd"]
    extra = 5_000_000 + 9_000_000
    assert out["totals"]["baseline_calibrated_nusd"] == plain["totals"]["baseline_calibrated_nusd"] + extra


def test_unlogged_formula_estimates_are_never_calibrated():
    """Served answers logged before `baseline_est_usd` existed have no formula estimate to scale: no interval, no bill."""
    led = Ledger(":memory:")
    for i in range(60):
        _request(led, audited=int(i % 2 == 0), baseline_est_usd=None)
    task = receipts.calibrate([receipts.leaf(r) for r in led.period_rows(0)], min_audits=2)[TASK]
    assert task["status"] == receipts.UNLOGGED and task["point_nusd"] is None


def test_integer_sums_are_exact_over_100k_rows():
    rng = random.Random(4)
    leaves = []
    usd = []
    for i in range(100_000):
        c = rng.uniform(0, 0.01)
        usd.append(c)
        leaves.append(
            {
                **dict.fromkeys(receipts.LEAF_KEYS),
                "id": i + 1,
                "day": 1,
                "task": TASK,
                "served_by": "teacher",
                "cost_nusd": receipts.nusd(c),
                "baseline_nusd": receipts.nusd(c / 2),
            }
        )
    totals = receipts.compute(leaves, min_audits=30)["totals"]
    assert totals["cost_nusd"] == sum(receipts.nusd(c) for c in usd)
    assert totals["baseline_recorded_nusd"] == sum(receipts.nusd(c / 2) for c in usd)
    assert isinstance(totals["cost_nusd"], int) and totals["requests"] == 100_000
    assert abs(totals["cost_nusd"] - math.fsum(usd) * receipts.NANO) <= 100_000 / 2  # half a nano-USD per row at most


# ---------- sealing ----------
@pytest.mark.req("INV-17")
def test_seal_is_idempotent_and_never_updates():
    led = _sealed_ledger(days=1)
    first = receipts.seal(led, 1)
    statements: list[str] = []
    led.db.set_trace_callback(statements.append)
    again = receipts.seal(led, 1)
    assert receipts.seal_due(led) == []
    led.db.set_trace_callback(None)
    assert again == first and first is not None
    assert not [s for s in statements if s.lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE", "REPLACE"))]
    with pytest.raises(ReceiptExists):
        led.add_receipt(1, None, {"manifest": {}})
    assert led.receipt(1) == first and led.receipt_periods() == [(1, None)]


def test_period_rows_are_read_in_chunks_with_the_lock_free_between_them(monkeypatch):
    """A busy day is read RECEIPT_CHUNK rows at a time, each under the lock, so the request path waits for one chunk at
    most; the chunks add up to the day, in id order, with only the client's rows for a client."""
    led = Ledger(":memory:")
    for day in (1, 2, 1):
        led.day = day
        for i in range(9):
            _request(led, client="alice" if i % 3 else None)
    whole = led.period_rows(1)
    monkeypatch.setattr(ledger_module, "RECEIPT_CHUNK", 4)
    original = Ledger._period_chunk
    between: list[bool] = []

    def chunk_then_log(self, *args, **kwargs):
        rows = original(self, *args, **kwargs)
        writer = threading.Thread(target=_request, args=(led,), kwargs={"day": 3})
        writer.start()
        writer.join(timeout=5)
        between.append(not writer.is_alive())  # another thread logged a request between two chunks
        return rows

    monkeypatch.setattr(Ledger, "_period_chunk", chunk_then_log)
    chunked = led.period_rows(1)
    assert chunked == whole and len(whole) == 18 and [r["id"] for r in whole] == sorted(r["id"] for r in whole)
    assert between == [True] * 5  # 4 + 4 + 4 + 4 + 2 rows: five reads, the lock free after each
    alice = led.period_rows(1, "alice")
    assert len(alice) == 12 and {r["client"] for r in alice} == {"alice"}
    assert led.receipt_row(whole[0]["id"]) == whole[0] and led.receipt_row(10**6) is None


def test_concurrent_sealing_writes_each_receipt_once(tmp_path):
    """Threads sealing at once (the gateway's lazy GETs and its demo day) and two processes on one file (the gateway and
    `python -m ratchet.live seal`) write each (day, client) receipt once; a loser keeps the stored document."""
    led = _sealed_ledger(days=2, clients=("alice", None))
    results: list[list[tuple[int, str | None]]] = []
    errors: list[BaseException] = []

    def seal() -> None:
        try:
            results.append(receipts.seal_due(led))
        except BaseException as e:  # noqa: BLE001 - reported below
            errors.append(e)

    threads = [threading.Thread(target=seal) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    sealed = sorted((period, client or "") for r in results for period, client in r)
    assert not errors and sealed == [(1, ""), (1, "alice"), (2, ""), (2, "alice")] and len(led.receipt_periods()) == 4
    db = tmp_path / "shared.db"
    first = Ledger(str(db))
    first.day = 1
    _request(first, client="alice")
    first.day = 2
    second = Ledger(str(db))
    second.day = 2
    stored = receipts.seal(first, 1, "alice")
    real = second.receipt
    second.receipt = lambda period, client=None: None  # type: ignore[method-assign]  # it has not seen the receipt yet
    try:
        assert receipts._seal_one(second, 1, "alice", receipts.SealConfig(label="other"), 0) is False
    finally:
        second.receipt = real  # type: ignore[method-assign]
    assert second.receipt(1, "alice") == stored and stored["manifest"]["label"] is None


def test_a_day_is_sealed_only_once_it_is_over():
    led = _sealed_ledger(days=1)
    with pytest.raises(receipts.PeriodOpen):
        receipts.seal(led, 2)  # the current day
    assert receipts.seal(led, 0) is None and led.receipt_periods() == []  # day 0 had no requests
    assert receipts.seal(led, 1) is not None and led.receipt_periods() == [(1, None)]
    live_led = Ledger(":memory:", auto_day=True)
    now = time.time()
    _request(live_led, ts=now - 2 * 86400 - 100)  # day 1 starts here, so day 2 ended 100 s ago
    _request(live_led, ts=now - 86400)  # day 2: within the grace period, so not sealed yet
    assert receipts.closed_through(live_led) == 1
    assert receipts.seal_due(live_led) == [(1, None)]


def test_signature_verifies_and_edits_break_it_and_the_chain():
    seed, pub = bom.keygen()
    led = _sealed_ledger(days=3)
    config = receipts.SealConfig(signer=bom.Signer(seed), share=0.25, min_audits=30, label="test")
    sealed = receipts.seal_due(led, config)
    assert sealed == [(1, None), (2, None), (3, None)]
    docs = [led.receipt(d) for d in (1, 2, 3)]
    assert docs[0]["manifest"]["prev_sha256"] == receipts.ZERO_SHA256
    for prev, doc in zip(docs, docs[1:], strict=False):
        assert doc["manifest"]["prev_sha256"] == prev["sha256"]
        assert receipts.verify_chain(doc, prev)[0]
    ok, lines = receipts.verify_receipt(docs[2], pub)
    assert ok and "signature valid" in lines[0]
    assert not receipts.verify_receipt(docs[2], bom.keygen()[1])[0]  # another key
    edited = json.loads(json.dumps(docs[2]))
    edited["manifest"]["totals"]["cost_nusd"] += 1
    assert not receipts.verify_receipt(edited, pub)[0]
    rehashed = json.loads(json.dumps(edited))  # re-hashed after the edit: the signature still fails
    rehashed["sha256"] = hashlib.sha256(bom.canonical(rehashed["manifest"])).hexdigest()
    assert not receipts.verify_receipt(rehashed, pub)[0]
    earlier = json.loads(json.dumps(docs[1]))
    earlier["manifest"]["billing"]["fee_nusd"] -= 1  # someone lowers yesterday's fee
    ok, line = receipts.verify_chain(docs[2], earlier)
    assert not ok and "prev_sha256" in line
    assert not receipts.verify_chain(docs[2], docs[0])[0]  # the chain names day 2, not day 1
    assert receipts.verify_receipt(docs[0], None)[0]  # the embedded key alone still checks the hash and signature


def test_receipts_are_chained_per_client_and_hold_only_that_clients_ids():
    led = _sealed_ledger(days=2, clients=("alice", "bob", None))
    sealed = receipts.seal_due(led)
    assert sealed == [(1, "alice"), (1, "bob"), (1, None), (2, "alice"), (2, "bob"), (2, None)]
    for day in (1, 2):
        everyone = {r["id"] for r in led.period_rows(day)}
        seen: set[int] = set()
        for client in ("alice", "bob"):
            doc = led.receipt(day, client)
            m = doc["manifest"]
            ids = [r["id"] for r in led.period_rows(day, client)]
            assert m["client"] == client and (m["first_id"], m["last_id"], m["n"]) == (ids[0], ids[-1], len(ids))
            assert {r["client"] for r in led.period_rows(day, client)} == {client}
            assert receipts.recheck(doc, [receipts.leaf(r) for r in led.period_rows(day, client)]) == []
            seen |= set(ids)
            if day == 2:
                assert m["prev_sha256"] == led.receipt(1, client)["sha256"]
        g = led.receipt(day)["manifest"]
        assert g["client"] is None and g["n"] == len(everyone) and seen < everyone  # the unnamed rows are global only
        assert g["cost_views"]["gpu_hours_note"] == "not computed"
    assert led.receipt(2, "alice")["manifest"]["cost_views"]["gpu_hours_note"] == receipts.CLIENT_GPU_NOTE


def test_spend_outside_requests_is_netted_and_shared_by_request_count():
    led = _sealed_ledger(days=1, clients=("alice", None))
    led.db.execute("INSERT INTO spend (ts, day, kind, ref, usd) VALUES (0, 1, 'backfill', 'job-1', 0.003)")
    led.db.commit()
    receipts.seal_due(led)
    counts = led.period_counts(1)
    g, a = led.receipt(1)["manifest"], led.receipt(1, "alice")["manifest"]
    assert g["savings"]["spend_nusd"] == 3_000_000
    assert a["savings"]["spend_nusd"] == 3_000_000 * counts["alice"] // sum(counts.values())
    assert g["savings"]["net_billable_lo_nusd"] == g["savings"]["billable_lo_nusd"] - 3_000_000


def test_recheck_recomputes_everything_from_the_leaves():
    led = _sealed_ledger(days=1)
    doc = receipts.seal(led, 1)
    leaves = [receipts.leaf(r) for r in led.period_rows(1)]
    assert receipts.recheck(doc, leaves) == []
    assert receipts.verify_rows(doc, leaves)[0]
    changed = [dict(lf) for lf in leaves]
    changed[3]["cost_nusd"] += 1
    problems = receipts.recheck(doc, changed)
    assert any("root" in p for p in problems) and any("cost_nusd" in p for p in problems)
    assert any("order" in p for p in receipts.recheck(doc, leaves[::-1]))
    assert receipts.recheck(doc, leaves[:-1])  # a leaf left out
    bad = json.loads(json.dumps(doc))
    bad["manifest"]["billing"]["fee_nusd"] += 3  # beyond the tolerance for estimated amounts
    assert any("fee_nusd" in p for p in receipts.recheck(bad, leaves))
    near = json.loads(json.dumps(doc))
    near["manifest"]["calibration"]["tasks"][TASK]["point_nusd"] += receipts.TOLERANCE_NUSD
    assert not any("point_nusd" in p for p in receipts.recheck(near, leaves))  # floating point elsewhere may differ


@pytest.mark.parametrize(
    "junk",
    [None, 3, "x", [], {}, {"manifest": None}, {"manifest": {"schema": receipts.SCHEMA}}, {"manifest": [], "signature": 5}],
)
def test_verifiers_never_raise_on_malformed_input(junk):
    led = _sealed_ledger(days=1)
    doc = receipts.seal(led, 1)
    leaves = [receipts.leaf(r) for r in led.period_rows(1)]
    assert not receipts.verify_receipt(junk)[0]
    assert not receipts.verify_chain(doc, junk)[0] and not receipts.verify_chain(junk, doc)[0]
    assert not receipts.verify_proof(doc, junk)[0] and not receipts.verify_proof(junk, {"leaf": leaves[0]})[0]
    assert not receipts.verify_rows(junk, leaves)[0]
    assert not receipts.verify_rows(doc, [junk])[0]
    assert receipts.check_manifest(junk)


# ---------- over HTTP ----------
def test_gateway_returns_the_request_id_and_its_proof_verifies(gw_env):
    seed, pub = bom.keygen()
    gw = gw_env(RATCHET_BOM_SIGNING_KEY=seed)
    c = TestClient(gw.app)
    msgs = make_stream("tickets", seed=3).sample(1).messages()
    r = c.post("/v1/chat/completions", json={"messages": msgs}, headers={"X-Ratchet-Task": TASK})
    rid = int(r.headers["X-Ratchet-Request-Id"])
    assert r.json()["ratchet"]["row_id"] == rid  # ratchet.request_id is the gateway's req_ id, for feedback (FR-13.30)
    streamed = c.post("/v1/chat/completions", json={"messages": msgs, "stream": True}, headers={"X-Ratchet-Task": TASK})
    chunks = [json.loads(line[6:]) for line in streamed.text.splitlines() if line.startswith("data: {")]
    sid = int(streamed.headers["X-Ratchet-Request-Id"])
    assert chunks[-1]["ratchet"]["row_id"] == sid == rid + 1
    assert c.get("/ratchet/receipts/0").status_code == 409  # day 0 is still open
    assert c.get("/ratchet/receipts").json()["receipts"] == []
    assert c.post("/ratchet/demo/day?requests=100").status_code == 200  # day 1, a night, and day 0 sealed
    listed = c.get("/ratchet/receipts").json()
    assert listed["receipts"] == [{"period": 0, "global": True, "clients": []}] and listed["public_key"] == pub
    assert listed["label"] == "simulation (mock models)" and listed["sealed_through"] == 0
    doc = c.get("/ratchet/receipts/0").json()
    assert receipts.verify_receipt(doc, pub)[0] and doc["manifest"]["n"] == 2
    gpu = doc["manifest"]["cost_views"]["gpu_hours"]
    assert gpu["basis"] == receipts.GPU_VIEW_BASIS and isinstance(gpu["total_nusd"], int)
    m = doc["manifest"]["merkle"]
    for request_id in (rid, sid):
        p = c.get(f"/ratchet/receipts/0/proof/{request_id}").json()
        assert p["leaf"]["id"] == request_id and p["root"] == m["root"] and p["receipt_sha256"] == doc["sha256"]
        assert merkle.verify_inclusion(receipts.leaf_bytes(p["leaf"]), p["index"], m["size"], p["path"], m["root"])
        assert receipts.verify_proof(doc, p)[0]
    assert c.get("/ratchet/receipts/0/proof/999999").status_code == 404
    assert c.get("/ratchet/receipts/7").status_code == 409 and c.get("/ratchet/receipts/-1").status_code == 400
    leaves = receipts.read_leaves(c.get("/ratchet/receipts/0/leaves").text)
    assert receipts.recheck(doc, leaves) == []
    # every demo day seals the one before it
    c.post("/ratchet/demo/day?requests=100")
    assert [e["period"] for e in c.get("/ratchet/receipts").json()["receipts"]] == [0, 1]
    assert c.get("/ratchet/receipts/1").json()["manifest"]["prev_sha256"] == doc["sha256"]


def test_api_keys_read_only_their_own_receipts(gw_env):
    gw = gw_env(
        RATCHET_API_KEYS=",".join(f"{n}:{k}" for n, k in KEYS.items()),
        RATCHET_ADMIN_KEYS=f"ops:{ADMIN}",
    )
    c = TestClient(gw.app)
    msgs = make_stream("tickets", seed=3).sample(1).messages()
    ids = {}
    for name, key in KEYS.items():
        r = c.post("/v1/chat/completions", json={"messages": msgs}, headers={**_bearer(key), "X-Ratchet-Task": TASK})
        ids[name] = int(r.headers["X-Ratchet-Request-Id"])
    gw.rt.ledger.day += 1
    alice, bob, ops = _bearer(KEYS["alice"]), _bearer(KEYS["bob"]), _bearer(ADMIN)
    assert c.get("/ratchet/receipts/0?client=alice", headers=bob).status_code == 403
    assert c.get("/ratchet/receipts/0", headers={}).status_code == 401
    assert c.get("/ratchet/receipts/0", headers=_bearer("not-a-key-0123456789")).status_code == 401
    own = c.get("/ratchet/receipts/0", headers=alice).json()["manifest"]
    assert own["client"] == "alice" and (own["first_id"], own["n"]) == (ids["alice"], 1)
    assert c.get("/ratchet/receipts/0?client=alice", headers=alice).json()["manifest"] == own
    assert c.get(f"/ratchet/receipts/0/proof/{ids['alice']}", headers=bob).status_code == 404  # not in bob's receipt
    assert c.get(f"/ratchet/receipts/0/proof/{ids['alice']}?client=alice", headers=bob).status_code == 403
    assert c.get("/ratchet/receipts/0/leaves?client=alice", headers=bob).status_code == 403
    assert c.get(f"/ratchet/receipts/0/proof/{ids['alice']}", headers=alice).json()["leaf"]["client"] == "alice"
    assert c.get("/ratchet/receipts", headers=bob).json()["receipts"] == [{"period": 0, "global": False, "clients": ["bob"]}]
    assert c.get("/ratchet/receipts", headers=ops).json()["receipts"] == [
        {"period": 0, "global": True, "clients": ["alice", "bob"]}
    ]
    assert c.get("/ratchet/receipts/0", headers=ops).json()["manifest"]["n"] == 2  # the gateway's
    assert c.get("/ratchet/receipts/0?client=bob", headers=ops).json()["manifest"]["client"] == "bob"
    assert c.get("/ratchet/receipts/0?client=carol", headers=ops).status_code == 404


def test_live_mode_without_keys_serves_no_receipts(gw):
    c = TestClient(gw.app)
    gw.rt.settings.mode = "live"
    try:
        assert c.get("/ratchet/receipts").status_code == 403
        assert c.get("/ratchet/receipts/0").status_code == 403
    finally:
        gw.rt.settings.mode = "mock"
    assert c.get("/ratchet/receipts").status_code == 200  # the mock demo is open


def test_rows_changed_after_sealing_are_caught(gw_env):
    gw = gw_env()
    c = TestClient(gw.app)
    msgs = make_stream("tickets", seed=3).sample(1).messages()
    rid = int(
        c.post("/v1/chat/completions", json={"messages": msgs}, headers={"X-Ratchet-Task": TASK}).headers["X-Ratchet-Request-Id"]
    )
    gw.rt.ledger.day += 1
    doc = c.get("/ratchet/receipts/0").json()
    _request(gw.rt.ledger, day=0)  # a row slipped into a sealed day
    client = TestClient(gw.app, raise_server_exceptions=False)
    for _ in range(2):
        assert client.get(f"/ratchet/receipts/0/proof/{rid}").status_code == 500
        assert client.get("/ratchet/receipts/0/leaves").status_code == 500
    events = [e for e in gw.rt.ledger.events() if e["kind"] == "integrity"]
    assert len(events) == 1 and "day 0" in events[0]["detail"]
    assert c.get("/ratchet/receipts/0").json() == doc  # the sealed receipt itself never changes (INV-17)
    # A proof served from a cached tree reads its row again: a row edited after the tree was cached is caught too.
    first = _request(gw.rt.ledger)  # day 1
    _request(gw.rt.ledger)
    gw.rt.ledger.day += 1
    assert c.get(f"/ratchet/receipts/1/proof/{first}").status_code == 200  # the tree is cached now
    gw.rt.ledger.db.execute("UPDATE requests SET cost_usd = cost_usd / 2 WHERE id=?", (first,))
    gw.rt.ledger.db.commit()
    assert client.get(f"/ratchet/receipts/1/proof/{first}").status_code == 500
    assert client.get("/ratchet/receipts/1/leaves").status_code == 500
    assert sum(e["kind"] == "integrity" for e in gw.rt.ledger.events()) == 2


@pytest.mark.req("FR-13.4")
def test_request_retention_never_breaks_a_receipt(gw_env):
    """Request retention folds rows into daily sums (FR-13.4). A receipt sealed before keeps verifying, and its leaves
    and proofs answer 410 (gone by design, no integrity failure); a day folded before it was sealed is never sealed from
    the rows that are left (one `receipt_skipped` event, then 410), and the next day chains to the last sealed one."""
    seed, pub = bom.keygen()
    gw = gw_env(RATCHET_BOM_SIGNING_KEY=seed)
    c = TestClient(gw.app)
    led, t = gw.rt.ledger, time.time()
    first = _request(led, ts=t - 300)  # day 0
    _request(led, ts=t - 290)
    led.day += 1
    doc = c.get("/ratchet/receipts/0").json()
    assert c.get(f"/ratchet/receipts/0/proof/{first}").status_code == 200  # the tree is cached now
    _request(led, ts=t - 200)  # day 1, still open: one row old enough to fold, one not
    _request(led, ts=t - 10)
    assert led.fold_requests(t - 100) == 3 and led.period_folded(0) and led.period_folded(1)
    assert c.get("/ratchet/receipts/0").json() == doc and receipts.verify_receipt(doc, pub)[0]
    for path in (f"/ratchet/receipts/0/proof/{first}", "/ratchet/receipts/0/leaves"):
        r = c.get(path)
        assert r.status_code == 410 and "folded by request retention" in r.json()["detail"], path
    led.day += 1
    _request(led)  # day 2
    led.day += 1
    day2 = c.get("/ratchet/receipts/2").json()
    assert receipts.verify_chain(day2, doc)[0]  # chained to day 0, the last receipt sealed
    for _ in range(2):
        r = c.get("/ratchet/receipts/1")
        assert r.status_code == 410 and "before it was sealed" in r.json()["detail"]
    skipped = [e for e in led.events() if e["kind"] == "receipt_skipped"]
    assert len(skipped) == 1 and "day 1" in skipped[0]["detail"]
    assert not [e for e in led.events() if e["kind"] == "integrity"]


@pytest.mark.req("FR-13.4")
def test_live_purge_seals_every_ended_day_before_it_folds(tmp_path, monkeypatch, capsys):
    for name in list(os.environ):
        if name.startswith("RATCHET_"):
            monkeypatch.delenv(name)
    db = tmp_path / "live.db"
    led = Ledger(str(db), auto_day=True)
    now = time.time()
    for ts in (now - 5 * 86400, now - 5 * 86400 + 60, now - 4 * 86400 + 10, now - 600):
        _request(led, ts=ts, client="alice")
    led.db.close()
    monkeypatch.setenv("RATCHET_DB", str(db))
    monkeypatch.setenv("RATCHET_REQUEST_RETENTION_DAYS", "2")
    live.main(["purge"])
    assert "3 request rows folded (window 2 days)" in capsys.readouterr().out
    after = Ledger(str(db), auto_day=True)
    assert set(after.receipt_periods()) == {(1, "alice"), (1, None), (2, "alice"), (2, None)}  # sealed before the fold
    assert [after.receipt(p)["manifest"]["merkle"]["size"] for p in (1, 2)] == [2, 1]  # from every row of each day
    assert not [e for e in after.events() if e["kind"] == "receipt_skipped"]


@pytest.mark.req("FR-13.4", "FR-15.5", "INV-17")
def test_the_gateways_purge_never_folds_a_day_that_has_not_been_sealed(monkeypatch):
    """The hourly purge seals every day that has ended and then applies the request window. A day that ended inside the
    sealing grace (SEAL_GRACE_S) is not sealed yet; folding its first rows would make its receipt impossible for good
    (`receipt_skipped`). The purge caps the fold at the start of the first day that is not closed, and the next one
    seals the day from all of its rows."""
    import types
    from unittest import mock

    from ratchet import gateway as gw
    from ratchet.config import Retention

    day = ledger_module.DAY_SECONDS
    t0 = 1_800_000_000.0
    led = Ledger(":memory:", auto_day=True)

    def req(ts: float) -> None:
        led.log_request(ts=ts, task="t", served_by="teacher", route="confidence", cost_usd=0.001, baseline_cost_usd=0.001)

    for ts in (t0, t0 + 50_000, t0 + day + 5, t0 + day + 60_000, t0 + 2 * day + 30):  # days 1, 1, 2, 2, 3
        req(ts)
    lock = threading.Lock()
    lock.acquire()  # a tick is running: the purge thread holds no lock of its own and must leave this one alone
    runtime = types.SimpleNamespace(
        ledger=led, receipt_config=receipts.SealConfig(), retention=Retention(request_days=1), gate_lock=lock
    )
    now = t0 + 2 * day + 100  # day 2 ended 100 s ago, inside the 300 s grace
    with mock.patch("time.time", return_value=now):
        assert receipts.closed_through(led) == 1 and receipts.fold_floor(led) == t0 + day
        gw.Runtime._purge(runtime)  # type: ignore[arg-type]
    assert set(led.receipt_periods()) == {(1, None)}  # day 1 sealed; day 2 is not closed yet
    assert not led.period_folded(2) and led.period_folded(1)  # day 1 folded, day 2 untouched
    with mock.patch("time.time", return_value=now + 3600):
        gw.Runtime._purge(runtime)  # type: ignore[arg-type]
    assert lock.locked()  # both purges left the tick's lock held (they used to release it, and the test worked around that)
    assert led.receipt(2) is not None and led.receipt(2)["manifest"]["n"] == 2  # from both of its rows
    assert not [e for e in led.events() if e["kind"] == "receipt_skipped"]


# ---------- settings and the mock teacher ----------
@pytest.mark.parametrize(
    "env, value, says",
    [
        ("RATCHET_TAKE_RATE", "1.5", "RATCHET_TAKE_RATE must be a share from 0 to 1"),
        ("RATCHET_TAKE_RATE", "nan", "RATCHET_TAKE_RATE must be a share"),
        ("RATCHET_RECEIPT_MIN_AUDITS", "1", "RATCHET_RECEIPT_MIN_AUDITS must be a whole number from 2"),
        ("RATCHET_MOCK_TEACHER_REASONING_MEAN", "-5", "RATCHET_MOCK_TEACHER_REASONING_MEAN must be more than 0"),
        ("RATCHET_MOCK_TEACHER_REASONING_MEAN", "1e9", "RATCHET_MOCK_TEACHER_REASONING_MEAN must be more than 0"),
    ],
)
def test_bad_receipt_settings_stop_the_gateway(gw_env, env, value, says):
    with pytest.raises(ValueError, match=says):
        gw_env(**{env: value})


def test_receipt_settings_are_read_and_checked(monkeypatch):
    for env in ("RATCHET_TAKE_RATE", "RATCHET_RECEIPT_MIN_AUDITS", "RATCHET_MOCK_TEACHER_REASONING_MEAN"):
        monkeypatch.delenv(env, raising=False)
    policy = Settings().receipt_policy()
    assert (policy.share, policy.min_audits) == (0.25, 30) and Settings().checked_mock_reasoning_mean() is None
    monkeypatch.setenv("RATCHET_TAKE_RATE", "0.1")
    monkeypatch.setenv("RATCHET_RECEIPT_MIN_AUDITS", "50")
    monkeypatch.setenv("RATCHET_MOCK_TEACHER_REASONING_MEAN", "420")
    s = Settings()
    s.check_reserved()  # built now: no longer reserved
    config = receipts.SealConfig.from_settings(s)
    assert (config.share, config.min_audits) == (0.1, 50) and s.checked_mock_reasoning_mean() == 420.0
    assert receipts.share_text(0.1) == "0.1" and receipts.share_text(1) == "1" and receipts.share_text(1e-7) == "0.0000001"


def test_mock_teacher_reasoning_varies_around_its_mean_deterministically():
    stream = make_stream("tickets", seed=11)
    requests = [stream.sample(1).messages() for _ in range(400)]
    fixed, varied = MockTeacher(300), MockTeacher(300, reasoning_mean=420, reasoning_cv=0.3)
    assert {fixed.reasoning(m) for m in requests} == {300}  # the default never moves a number
    tokens = [varied.reasoning(m) for m in requests]
    assert tokens == [varied.reasoning(m) for m in requests]  # the same request always costs the same
    assert 395 < sum(tokens) / len(tokens) < 445 and len(set(tokens)) > 20
    c = varied.complete(requests[0], "teacher")[0]
    assert (
        c.reasoning_tokens == tokens[0] and c.out_tokens == fixed.complete(requests[0], "teacher")[0].out_tokens - 300 + tokens[0]
    )
    assert {MockTeacher(300, reasoning_mean=420).reasoning(m) for m in requests[:20]} == {420}  # cv 0: the mean exactly
    with pytest.raises(ValueError):
        MockTeacher(reasoning_mean=0)


# ---------- the simulator ----------
def _sim_args(*extra: str) -> Any:
    return simulate.parser().parse_args(["--days", "3", "--requests", "400", "--out", os.devnull, *extra])


def _decisions(res: dict[str, Any]) -> tuple[Any, ...]:
    events = [(e["day"], e["kind"], e["detail"]) for e in res["events"]]
    # The latency percentiles are wall-clock time of this machine (ADR 0060), not simulation numbers.
    days = [{k: v for k, v in d.items() if k not in ("p50_student_ms", "p95_ms")} for d in res["days"]]
    return days, res["totals"], res["versions"], res["nights"], events, res["served"]


def test_sealing_moves_no_simulation_number(monkeypatch):
    sealed = simulate.run(_sim_args("--receipts"))
    plain = simulate.run(_sim_args())
    monkeypatch.setattr(receipts, "seal_due", lambda *a, **k: [])
    patched = simulate.run(_sim_args("--receipts"))
    assert _decisions(sealed) == _decisions(plain) == _decisions(patched)
    assert [d["day"] for d in sealed["receipts"]] == [1, 2, 3] and patched["receipts"] == [] and "receipts" not in plain
    for d in sealed["receipts"]:  # the mock teacher bills the formula's 300 tokens: the bound sits on the truth
        assert d["held"] and abs(d["baseline_calibrated_usd"] - d["true_baseline_usd"]) < 0.02 * d["true_baseline_usd"]
        assert all(0.97 < float(r) < 1.03 for t, r in d["ratio"].items() if d["status"][t] == receipts.OK)
    assert "savings receipts (simulation, mock models)" in simulate.table(sealed)


def test_receipts_measure_and_correct_a_teacher_that_reasons_more():
    """The mock teacher writes 420 reasoning tokens on average where the formula assumes 300: each calibrated day's
    ticket-triage ratio is above 1.2, the calibrated baseline is near the truth while the formula is far below it, and
    the lower bound is at or below the true cost on most days (simulation)."""
    res = simulate.run(
        simulate.parser().parse_args(
            ["--days", "5", "--requests", "600", "--audit-rate", "0.2", "--out", os.devnull, "--receipts"]
            + ["--teacher-reasoning-mean", "420"]
        )
    )
    days = [d for d in res["receipts"] if d["status"].get(TASK) == receipts.OK]
    assert len(days) >= 3
    for d in days:
        assert float(d["ratio"][TASK]) > 1.2
        assert d["baseline_recorded_usd"] < 0.95 * d["true_baseline_usd"]
        assert abs(d["baseline_calibrated_usd"] - d["true_baseline_usd"]) < 0.1 * d["true_baseline_usd"]
        assert d["fee_usd"] > 0 and d["basis"] == receipts.BASIS
    assert sum(d["held"] for d in days) >= len(days) - 1


# ---------- command line ----------
def _bundle(tmp_path: Path) -> tuple[str, dict[str, str]]:
    seed, pub = bom.keygen()
    led = _sealed_ledger(days=2, clients=("alice",))
    receipts.seal_due(led, receipts.SealConfig(signer=bom.Signer(seed)))
    doc, prev = led.receipt(2, "alice"), led.receipt(1, "alice")
    leaves = [receipts.leaf(r) for r in led.period_rows(2, "alice")]
    t = receipts.tree(leaves)
    index, path = receipts.proof_for([lf["id"] for lf in leaves], t, leaves[5]["id"])
    files = {
        "receipt": json.dumps(doc, indent=2),
        "prev": json.dumps(prev),
        "proof": json.dumps({"leaf": leaves[5], "index": index, "path": path}),
        "rows": "".join(receipts.leaf_bytes(lf).decode() + "\n" for lf in leaves),
    }
    paths = {}
    for name, text in files.items():
        (tmp_path / f"{name}.json").write_text(text, encoding="utf-8")
        paths[name] = str(tmp_path / f"{name}.json")
    return pub, paths


def test_cli_verifies_a_bundle_and_fails_after_a_one_digit_edit(tmp_path, capsys):
    pub, p = _bundle(tmp_path)
    argv = ["verify", p["receipt"], "--public-key", pub, "--prev", p["prev"], "--proof", p["proof"], "--rows", p["rows"]]
    assert receipts.main(argv) == 0
    out = capsys.readouterr().out
    assert out.count("OK:") == 4 and "FAIL" not in out
    text = Path(p["receipt"]).read_text(encoding="utf-8")
    doc = json.loads(text)
    fee = str(doc["manifest"]["billing"]["fee_nusd"])
    digit = "1" if fee[-1] != "1" else "2"
    edited = text.replace(f'"fee_nusd": {fee}', f'"fee_nusd": {fee[:-1]}{digit}', 1)
    assert edited != text
    Path(p["receipt"]).write_text(edited, encoding="utf-8")
    assert receipts.main(argv) == 1
    assert "FAIL: sha256 does not match" in capsys.readouterr().out
    Path(p["receipt"]).write_text("{not json", encoding="utf-8")
    assert receipts.main(["verify", p["receipt"]]) == 1
    assert receipts.main(["verify", str(tmp_path / "missing.json")]) == 2


def test_cli_module_entry_point(tmp_path):
    pub, p = _bundle(tmp_path)
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    done = subprocess.run(
        [sys.executable, "-m", "ratchet.receipt", "verify", p["receipt"], "--public-key", pub, "--proof", p["proof"]],
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
        timeout=60,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "OK: proof: request" in done.stdout and "chain: not checked" in done.stdout


def test_offline_seal_of_a_simulated_ledger(tmp_path, capsys, monkeypatch):
    db = tmp_path / "sim.db"
    simulate.run(simulate.parser().parse_args(["--days", "2", "--requests", "150", "--out", os.devnull]), db=str(db))
    monkeypatch.delenv("RATCHET_BOM_SIGNING_KEY", raising=False)
    assert receipts.main(["seal", "--db", str(db), "--period", "3"]) == 2  # the run logged days 1 and 2 only
    assert receipts.main(["seal", "--db", str(db), "--period", "2"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["manifest"]["period"] == {"day": 2} and doc["manifest"]["label"] == "simulation (mock models)"
    led = Ledger(str(db))
    assert led.receipt_periods() == [(1, None), (2, None)] and led.receipt(2) == doc
    assert receipts.main(["seal", "--db", str(db)]) == 0  # nothing left to seal
    assert receipts.main(["seal", "--db", str(tmp_path / "none.db")]) == 2
    assert receipts.main(["seal", "--db", str(db), "--client", "alice"]) == 2  # --client needs --period


def test_live_seal_command_seals_every_day_that_ended(tmp_path, monkeypatch, capsys):
    for name in list(os.environ):
        if name.startswith("RATCHET_"):
            monkeypatch.delenv(name)
    db = tmp_path / "live.db"
    led = Ledger(str(db), auto_day=True)
    now = time.time()
    for ts in (now - 3 * 86400, now - 3 * 86400 + 60, now - 2 * 86400 + 10, now - 600):
        _request(led, ts=ts, client="alice")
    led.db.close()
    monkeypatch.setenv("RATCHET_DB", str(db))
    live.main(["seal"])
    out = capsys.readouterr().out
    assert "sealed the receipt of day 1 (alice)" in out and "sealed the receipt of day 2 (gateway)" in out
    assert not [line for line in out.splitlines() if line.startswith("sealed") and "day 3" in line]  # today is open
    live.main(["seal"])
    assert "nothing to seal" in capsys.readouterr().out
    assert Ledger(str(db), auto_day=True).receipt(1)["manifest"]["label"] == "live traffic"
