"""Success metrics (PRD §8.1, FR-7.9, `ratchet/kpis.py`): each metric from synthetic day rows, the ledger reads behind
them (also over folded request rows and while requests are logged), `GET /ratchet/kpis` and the `kpis` block of
`/ratchet/stats`.

The reference simulation's metrics, and the PRD statuses they back, are checked in tests/test_system.py.
"""

from __future__ import annotations

import json
import math
import random
import threading
from typing import Any

import pytest
from fakes import _version
from fastapi.testclient import TestClient

from ratchet import kpis
from ratchet.config import Settings
from ratchet.ledger import Ledger
from ratchet.stats import wilson

DAY = 86_400.0
T0 = 1_800_000_000.0
METRIC_KEYS = {"name", "value", "target", "op", "unit", "met", "detail"}
REPORT_IDS = ["SM-1", "SM-2", "SM-3", "SM-4", "SM-5", "NFR-1"]


def _day(d: int, rate: float = 0.02, *, requests: int = 1000, answered: int | None = None, cost: float = 1.0) -> dict:
    """One ledger day as `Ledger.kpi_days` returns it: `rate` of the requests the student judged were escalated."""
    judged = requests if answered is None else answered
    esc = round(rate * judged)
    return {
        "day": d,
        "requests": requests,
        "student_served": requests - esc,
        "escalated": esc,
        "answered": judged,
        "answered_escalated": esc,
        "cost": cost,
        "baseline": 5.0,
    }


def _series(rates: list[float], start: int = 1) -> list[dict]:
    return [_day(start + i, r) for i, r in enumerate(rates)]


@pytest.mark.req("SM-1", "SM-2", "SM-3", "SM-5", "FR-7.9")
def test_kpis_unit():
    # Window: the last W days with traffic. A quiet day takes no place in it, so the busy days before it stay in.
    days = [_day(1), _day(2), _day(3), {**_day(4), "requests": 0}, _day(5), _day(6)]
    assert [d["day"] for d in kpis.window(days, 3)] == [3, 5, 6]
    assert [d["day"] for d in kpis.window(list(reversed(days)), 10)] == [1, 2, 3, 5, 6]  # fewer days: all of them
    assert kpis.window([], 3) == []
    for bad in (0, -1, 2.0, True, None, kpis.MAX_WINDOW_DAYS + 1):
        with pytest.raises(ValueError, match="KPI window"):
            kpis.window(days, bad)  # type: ignore[arg-type]

    # SM-1: 1 - (cost + failed cost) / baseline over the window, failed requests' spend included.
    win = [_day(1, cost=1.0), _day(2, cost=2.0)]
    m = kpis.cost_reduction(win)
    assert set(m) == METRIC_KEYS and (m["target"], m["op"], m["unit"]) == (0.70, ">=", "fraction")
    assert m["value"] == pytest.approx(1 - 3.0 / 10.0) and m["met"] is True
    assert m["detail"]["cost_model"] == "per_token" and m["detail"]["requests"] == 2000
    failed = kpis.cost_reduction(win, failed_cost=0.5)
    assert failed["value"] == pytest.approx(1 - 3.5 / 10.0) and failed["met"] is False  # 65% < 70%
    assert failed["detail"]["failed_cost_usd"] == 0.5
    assert kpis.SM_TARGETS["SM-1"].reached(0.7 - 1e-12)  # a share at the target counts, whatever the float rounding
    assert not kpis.SM_TARGETS["SM-1"].reached(0.6999) and kpis.SM_TARGETS["SM-4"].reached(0)
    assert kpis.cost_reduction([])["value"] is None and kpis.cost_reduction([])["met"] is None
    assert kpis.cost_reduction([{**_day(1), "baseline": 0.0}])["detail"]["reason"] == "no baseline cost"

    # SM-3: the share of the window's requests the student answered.
    share = kpis.student_share([_day(1, 0.1), _day(2, 0.3)])
    assert share["value"] == pytest.approx(0.8) and share["met"] is True and share["detail"]["student_served"] == 1600
    assert kpis.student_share([])["met"] is None

    # SM-2: pooled audits with a 95% Wilson interval; met on the point estimate, once there are enough audits.
    p = kpis.audited_precision(200, 194, min_audits=20)
    assert p["value"] == pytest.approx(0.97) and p["met"] is True
    assert (p["detail"]["lower"], p["detail"]["upper"]) == wilson(194, 200)
    assert p["detail"]["lower"] < 0.95 < p["value"]  # the point estimate decides, the interval is reported
    few = kpis.audited_precision(10, 10, min_audits=20)
    assert few["value"] == 1.0 and few["met"] is None and "fewer than 20" in few["detail"]["reason"]
    assert kpis.audited_precision(100, 90)["met"] is False
    assert kpis.audited_precision(20, 19)["met"] is True  # exactly 95%
    none = kpis.audited_precision(0, 0)
    assert none["value"] is None and none["met"] is None and (none["detail"]["lower"], none["detail"]["upper"]) == (0.0, 1.0)
    for n, agree in ((5, 6), (5, -1), (5.0, 4), (True, 1), (5, None)):
        with pytest.raises(ValueError):
            kpis.audited_precision(n, agree)  # type: ignore[arg-type]

    # SM-5: an episode starts 5 points above the lowest of the three days before it and ends at most 2 points above.
    rec = kpis.drift_recovery(_series([0.02, 0.02, 0.03, 0.12, 0.10, 0.06, 0.04, 0.02]))
    assert rec["value"] == 3 and rec["met"] is True and (rec["target"], rec["unit"]) == (3, "nights")
    assert rec["detail"]["episodes"] == [{"start": 4, "end": 7, "nights": 3, "baseline": 0.02, "peak": 0.12}]
    late = kpis.drift_recovery(_series([0.02, 0.02, 0.03, 0.12, 0.10, 0.06, 0.05, 0.03]))
    assert late["value"] == 4 and late["met"] is False  # 0.05 is more than 2 points above 0.02: day 8 ends it
    # Unrecovered: no value while the episode is open; not met once it is already longer than the target.
    open_ep = kpis.drift_recovery(_series([0.02, 0.02, 0.02, 0.12, 0.11, 0.10]))
    assert open_ep["value"] is None and open_ep["met"] is None and open_ep["detail"]["open_nights"] == 2
    assert "still open" in open_ep["detail"]["reason"]
    long_open = kpis.drift_recovery(_series([0.02, 0.02, 0.02, 0.12, 0.11, 0.10, 0.10, 0.09]))
    assert long_open["value"] is None and long_open["met"] is False and long_open["detail"]["open_nights"] == 4
    # The longest of several episodes; none at all is 0 nights.
    two = kpis.drift_recovery(_series([0.02, 0.02, 0.02, 0.09, 0.03, 0.02, 0.02, 0.10, 0.08, 0.02]))
    assert [e["nights"] for e in two["detail"]["episodes"]] == [1, 2] and two["value"] == 2
    flat = kpis.drift_recovery(_series([0.02] * 6))
    assert flat["value"] == 0 and flat["met"] is True and flat["detail"]["episodes"] == []
    short = kpis.drift_recovery(_series([0.02, 0.02, 0.2]))
    assert short["value"] is None and short["met"] is None and "drift needs 4" in short["detail"]["reason"]


@pytest.mark.req("SM-5")
def test_drift_episode_boundaries_and_what_counts_as_a_day():
    # Exactly 5 points above the baseline starts an episode, exactly 2 points above ends it (rates from integer counts).
    eps = kpis.drift_episodes([(1, 0.02), (2, 0.03), (3, 0.04), (4, 0.07), (5, 0.04)])
    assert [(e.start, e.end, e.baseline) for e in eps] == [(4, 5, 0.02)]
    assert kpis.drift_episodes([(1, 0.02), (2, 0.03), (3, 0.04), (4, 0.069)]) == []
    # The baseline is the lowest of the three days just before; a slow climb never starts one.
    assert kpis.drift_episodes([(d, 0.02 + 0.01 * d) for d in range(1, 12)]) == []
    assert kpis.drift_episodes([(1, 0.5), (2, 0.5), (3, 0.5), (4, 0.9)], baseline_days=3)[0].baseline == 0.5
    # Days are the ones the student judged enough requests on; nights count calendar days between them.
    days = _series([0.02, 0.02, 0.02, 0.12]) + [_day(5, 0.5, answered=10), _day(7, 0.02)]
    assert [d for d, _ in kpis.escalation_rates(days)] == [1, 2, 3, 4, 7]
    rec = kpis.drift_recovery(days)
    assert rec["value"] == 3 and rec["detail"]["episodes"][0]["end"] == 7 and rec["detail"]["days_measured"] == 5
    # The rate is over the requests the student judged: an outage (escalated, never answered) is not drift.
    outage = {**_day(4, 0.02), "escalated": 600, "student_served": 380}
    rates = kpis.escalation_rates(_series([0.02, 0.02, 0.02]) + [outage])
    assert rates[-1] == (4, pytest.approx(0.02))
    assert kpis.escalation_rates([_day(1, answered=0)], min_answered=0) == []  # nothing judged: no rate, no division


@pytest.mark.req("SM-4")
def test_bad_deploys_count_versions_found_bad_at_full_traffic():
    def ev(day: int, kind: str, detail: str) -> dict[str, Any]:
        return {"day": day, "kind": kind, "detail": detail}

    promoted = [ev(2, "live", "v1 promoted to 100% (audited precision 100.0%, n=20)")]
    canary_rollback = ev(4, "rollback", "v3 audited precision 41.2% (n=17, upper bound 64.0% < 92%); traffic back to live v1")
    m = kpis.bad_deploys([*promoted, canary_rollback], {1: "live", 3: "rolled_back"})
    assert m["value"] == 0 and m["met"] is True and m["detail"]["reached_full_traffic"] == 1  # caught before 100%
    assert (m["target"], m["op"], m["unit"]) == (0, "==", "versions")

    alert = ev(6, "alert", "live v1 audited precision 80.0% (n=40) below floor; investigate or roll back manually")
    m = kpis.bad_deploys([*promoted, alert], {1: "live"})
    assert m["value"] == 1 and m["met"] is False
    assert m["detail"]["bad_versions"] == [{"version": 1, "evidence": "alert", "day": 6}]
    # The anytime-valid alert's wording, an alert without a logged promotion, and an alert repeated: one version each.
    eprocess = ev(7, "alert", "live v2: 30/40 audits agree; e-value 120 >= 20; investigate or roll back manually")
    m = kpis.bad_deploys([*promoted, alert, alert, eprocess], {2: "live"})
    assert m["value"] == 2 and [b["version"] for b in m["detail"]["bad_versions"]] == [1, 2]
    # A version rolled back by hand after reaching 100%: by its event, or by its state when the event names no version.
    manual = ev(8, "rollback", "v1 rolled back by the operator")
    assert kpis.bad_deploys([*promoted, manual], {1: "rolled_back"})["detail"]["bad_versions"][0]["evidence"] == "rollback"
    by_state = kpis.bad_deploys(promoted, {1: "rolled_back"})
    assert by_state["value"] == 1 and by_state["detail"]["bad_versions"][0]["evidence"] == "rolled_back"
    # Nothing reached 100% yet: not known. Events that name no version are ignored.
    early = kpis.bad_deploys([canary_rollback, ev(1, "live", "promoted"), {"kind": "live"}], {3: "rolled_back"})
    assert early["value"] == 0 and early["met"] is None and "reached 100%" in early["detail"]["reason"]
    assert kpis.bad_deploys([], {4: "live"})["met"] is True  # live now (a restored ledger): it reached 100%


def _log_days(led: Ledger, n_days: int = 6, per_day: int = 60, seed: int = 11) -> list[dict[str, Any]]:
    """Request rows over `n_days` days, with every column the metrics read, NULLs included. Returns what was logged."""
    rng = random.Random(seed)
    rows = []
    for d in range(1, n_days + 1):
        for i in range(per_day):
            served = rng.choice(["student", "student", "teacher"])
            judged = rng.random() < 0.9
            audited = int(served == "student" and rng.random() < 0.3)
            row = {
                "ts": T0 + (d - 1) * DAY + i * DAY / per_day,
                "day": d,
                "task": rng.choice(["alpha", "beta"]),
                "served_by": served,
                "route": "student" if served == "student" else ("confidence" if judged else "student_error"),
                "confidence": rng.random() if judged or served == "student" else None,
                "escalated": int(served == "teacher"),
                "audited": audited,
                "audit_agree": rng.choice([0, 1, 1, 1, None]) if audited else None,
                "latency_ms": rng.choice([None, round(rng.uniform(50, 900), 3)]),
                "cost_usd": rng.uniform(0, 0.002),
                "baseline_cost_usd": rng.uniform(0.002, 0.01),
            }
            led.log_request(**row)
            rows.append(row)
    return rows


def _window_truth(rows: list[dict[str, Any]], lo: int, hi: int) -> dict[str, Any]:
    win = [r for r in rows if lo <= r["day"] <= hi]
    audits = [r for r in win if r["audited"] == 1 and r["audit_agree"] is not None]
    lat = sorted(r["latency_ms"] for r in win if r["served_by"] == "student" and r["latency_ms"] is not None)
    return {
        "cost": sum(r["cost_usd"] for r in win),
        "baseline": sum(r["baseline_cost_usd"] for r in win),
        "share": sum(r["served_by"] == "student" for r in win) / len(win),
        "audits": (len(audits), sum(r["audit_agree"] for r in audits)),
        "p50": lat[math.ceil(0.5 * len(lat)) - 1] if lat else None,
        "timed": len(lat),
    }


@pytest.mark.req("SM-1", "SM-2", "SM-3", "NFR-1", "FR-7.9", "FR-13.4")
def test_report_reads_the_ledger_and_survives_folding():
    led = Ledger(":memory:")
    rows = _log_days(led)
    rep = kpis.report(led, 3, min_audits=10)
    assert rep["window"] == {"from": 4, "to": 6, "days": 3, "complete": True} and rep["simulated"] is False
    want = _window_truth(rows, 4, 6)
    m = rep["metrics"]
    assert m["SM-1"]["value"] == pytest.approx(1 - want["cost"] / want["baseline"])
    assert m["SM-3"]["value"] == pytest.approx(want["share"])
    assert (m["SM-2"]["detail"]["audits"], m["SM-2"]["detail"]["agree"]) == want["audits"]
    assert (m["NFR-1"]["value"], m["NFR-1"]["detail"]["requests"]) == (want["p50"], want["timed"])
    assert m["NFR-1"]["detail"]["column"] == "latency_ms" and m["NFR-1"]["met"] is True
    assert list(m) == REPORT_IDS and rep["summary"]["met"] + rep["summary"]["not_met"] + rep["summary"]["unknown"] == 6
    # Per day: student-judged requests and how many of those escalated, for SM-5.
    by_day = {d["day"]: d for d in led.kpi_days()}
    for d in range(1, 7):
        day_rows = [r for r in rows if r["day"] == d]
        judged = [r for r in day_rows if r["confidence"] is not None]
        assert by_day[d]["answered"] == len(judged)
        assert by_day[d]["answered_escalated"] == sum(r["escalated"] for r in judged)
    assert led.audit_totals() == (
        sum(1 for r in rows if r["audited"] and r["audit_agree"] is not None),
        sum(r["audit_agree"] or 0 for r in rows if r["audited"]),
    )

    # Folding old request rows into daily sums (retention, FR-13.4) leaves every day's sums and the audits as they were;
    # latencies are not kept in the sums, so NFR-1 reads only the rows still there.
    before_days, before_audits = led.kpi_days(), led.audit_totals(1, 6)
    assert led.fold_requests(T0 + 2.5 * DAY) > 0  # days 1 and 2 whole, day 3 in half
    after_days = led.kpi_days()
    assert [d["day"] for d in after_days] == [d["day"] for d in before_days]
    for a, b in zip(after_days, before_days, strict=True):
        assert {k: a[k] for k in ("requests", "student_served", "escalated", "answered", "answered_escalated")} == {
            k: b[k] for k in ("requests", "student_served", "escalated", "answered", "answered_escalated")
        }
        assert (a["cost"], a["baseline"]) == pytest.approx((b["cost"], b["baseline"]))
    assert led.audit_totals(1, 6) == before_audits and led.audit_totals(3, 3) == _window_truth(rows, 3, 3)["audits"]
    kept = [r for r in rows if r["ts"] >= T0 + 2.5 * DAY]
    assert led.latency_quantile(0.5, 3, 3, served_by="student")[1] == _window_truth(kept, 3, 3)["timed"]
    assert kpis.report(led, 3, min_audits=10)["metrics"]["SM-1"]["value"] == pytest.approx(m["SM-1"]["value"])
    led.fold_requests(T0 + 7 * DAY)  # everything folded: no latency left anywhere
    folded = kpis.report(led, 3, min_audits=10)["metrics"]
    assert folded["SM-3"]["value"] == pytest.approx(m["SM-3"]["value"]) and folded["NFR-1"]["value"] is None
    assert folded["NFR-1"]["met"] is None and "no timed" in folded["NFR-1"]["detail"]["reason"]

    # Failed requests' spend (the `failures` table, when the ledger has one) counts against SM-1 in the window only.
    led.db.execute("CREATE TABLE failures (id INTEGER PRIMARY KEY, ts REAL, day INTEGER, cost_usd REAL)")
    led.db.executemany("INSERT INTO failures (day, cost_usd) VALUES (?, ?)", [(5, 0.25), (6, 0.5), (2, 9.0), (6, None)])
    rep = kpis.report(led, 3, min_audits=10)
    assert led.failed_cost(4, 6) == 0.75 and rep["metrics"]["SM-1"]["detail"]["failed_cost_usd"] == 0.75
    assert rep["metrics"]["SM-1"]["value"] == pytest.approx(1 - (want["cost"] + 0.75) / want["baseline"])


@pytest.mark.req("NFR-1", "FR-7.9")
def test_latency_quantile_is_nearest_rank():
    led = Ledger(":memory:")
    assert led.latency_quantile(0.5) == (None, 0, "latency_ms")
    values = [float(v) for v in (7, 3, 9, 1, 5, 5, 8)]
    for i, v in enumerate(values):
        led.log_request(day=1 + i % 2, served_by="student", latency_ms=v, cost_usd=0.0)
    led.log_request(day=1, served_by="teacher", latency_ms=1000.0, cost_usd=0.0)
    led.log_request(day=1, served_by="student", latency_ms=None, cost_usd=0.0)
    ordered = sorted(values)
    for q in (0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0):
        rank = min(len(ordered), max(1, math.ceil(q * len(ordered))))
        assert led.latency_quantile(q, served_by="student") == (ordered[rank - 1], 7, "latency_ms"), q
    assert led.latency_quantile(1.0) == (1000.0, 8, "latency_ms")  # every server
    assert led.latency_quantile(0.5, 2, 2, served_by="student") == (3.0, 3, "latency_ms")  # day 2: 1, 3, 5
    assert led.latency_quantile(0.5, 3, None) == (None, 0, "latency_ms")
    for bad in ({"q": 1.5}, {"q": -0.1}, {"q": True}, {"q": "0.5"}, {"q": 0.5, "served_by": 1}, {"q": 0.5, "day_from": 1.5}):
        with pytest.raises(ValueError):
            led.latency_quantile(**bad)  # type: ignore[arg-type]
    # Once the ledger times the whole cascade (`requests.wall_ms`, a later schema), that is what NFR-1 reads where a row
    # in the range recorded it; older rows keep the providers' latency.
    led.db.execute("ALTER TABLE requests ADD COLUMN wall_ms REAL")
    assert led.latency_quantile(0.5, served_by="student") == (5.0, 7, "latency_ms")
    led.log_request(day=3, served_by="student", latency_ms=1.0, wall_ms=1200.0, cost_usd=0.0)
    assert led.latency_quantile(0.5, 3, 3, served_by="student") == (1200.0, 1, "wall_ms")
    assert led.latency_quantile(0.5, 1, 2, served_by="student") == (5.0, 7, "latency_ms")


@pytest.mark.req("FR-7.9", "SM-4")
def test_report_on_an_empty_or_odd_ledger_stays_valid_json():
    led = Ledger(":memory:")
    rep = kpis.report(led)
    assert rep["window"] == {"from": None, "to": None, "days": 0, "complete": False}
    assert all(m["value"] is None and m["met"] is None for k, m in rep["metrics"].items() if k != "SM-4")
    assert rep["metrics"]["SM-4"]["value"] == 0 and rep["summary"] == {"met": 0, "not_met": 0, "unknown": 6}
    # A version live now and a promotion in the event log count as reaching 100%; an alert on it makes it a bad deploy.
    vid = _version(led, "live")
    led.event("live", f"v{vid} promoted to 100% (audited precision 100.0%, n=20)")
    assert kpis.report(led)["metrics"]["SM-4"]["met"] is True
    led.event("alert", f"live v{vid} audited precision 70.0% (n=40) below floor")
    assert kpis.report(led)["metrics"]["SM-4"]["value"] == 1
    # A cost no ledger should hold (an infinity) gives no number rather than invalid JSON.
    led.log_request(day=1, served_by="student", cost_usd=math.inf, baseline_cost_usd=1.0)
    bad = kpis.report(led)
    assert bad["metrics"]["SM-1"]["value"] is None and bad["metrics"]["SM-1"]["met"] is None
    json.dumps(bad, allow_nan=False)
    for kw in ({"window_days": 0}, {"min_audits": -1}, {"min_audits": 2.5}):
        with pytest.raises(ValueError):
            kpis.report(led, **kw)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="whole number"):
        led.audit_totals(1.5)  # type: ignore[arg-type]


@pytest.mark.req("FR-7.9")
def test_report_while_requests_are_logged():
    """Scout calls the gateway from threads: a report built while requests are logged sees a consistent ledger read by
    read (each under the ledger's lock) and never fails."""
    led = Ledger(":memory:")
    _log_days(led, n_days=4, per_day=20)
    errors: list[BaseException] = []
    seen: list[int] = []

    def writer(k: int) -> None:
        try:
            for i in range(150):
                audited = i % 3 == 0
                led.log_request(
                    day=4, served_by="student", confidence=0.9, escalated=0, audited=int(audited),
                    audit_agree=1 if audited else None, latency_ms=100.0 + k, cost_usd=0.0001, baseline_cost_usd=0.001,
                )  # fmt: skip
        except BaseException as e:  # noqa: BLE001 - reported below
            errors.append(e)

    def reader() -> None:
        try:
            for _ in range(15):
                rep = kpis.report(led, 2, min_audits=5)
                json.dumps(rep, allow_nan=False)
                seen.append(rep["metrics"]["SM-3"]["detail"]["requests"])
        except BaseException as e:  # noqa: BLE001 - reported below
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(k,)) for k in range(4)] + [threading.Thread(target=reader) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert all(40 <= n <= 40 + 600 for n in seen)  # days 3 and 4: the 40 rows logged first, plus some of the 600 new
    assert kpis.report(led, 2)["metrics"]["SM-3"]["detail"]["requests"] == 640


@pytest.mark.req("FR-7.9", "SM-1", "SM-2", "SM-3", "SM-4", "SM-5", "NFR-1")
def test_kpis_endpoint_shape(gw_env):
    gw = gw_env(RATCHET_KPI_WINDOW_DAYS="2", RATCHET_API_KEYS="")
    c = TestClient(gw.app)
    fresh = c.get("/ratchet/kpis")
    assert fresh.status_code == 200
    body = fresh.json()
    assert (body["mode"], body["simulated"], body["window_days"]) == ("mock", True, 2)
    assert body["window"] == {"from": None, "to": None, "days": 0, "complete": False}
    assert list(body["metrics"]) == REPORT_IDS and body["summary"]["unknown"] == 6

    for _ in range(3):
        gw.rt.simulate_day(1000, "tickets")
    body = c.get("/ratchet/kpis").json()
    assert body["window"] == {"from": 2, "to": 3, "days": 2, "complete": True}
    for rid, m in body["metrics"].items():
        assert set(m) == METRIC_KEYS, rid
        assert isinstance(m["name"], str) and m["op"] in (">=", "<=", "==", "<", ">") and isinstance(m["detail"], dict)
        assert m["met"] in (True, False, None) and (m["value"] is None or isinstance(m["value"], (int, float)))
    assert {rid: body["metrics"][rid]["target"] for rid in kpis.SM_TARGETS} == {
        rid: t.value for rid, t in kpis.SM_TARGETS.items()
    }
    assert body["metrics"]["SM-3"]["detail"]["requests"] == 2000  # days 2 and 3
    assert body["metrics"]["SM-2"]["detail"]["min_audits"] == gw.rt.gate.policy.min_audits
    assert body["metrics"]["SM-4"]["detail"]["reached_full_traffic"] >= 1  # the first student went live

    # The same report is the `kpis` block of /ratchet/stats; `days` picks another window.
    assert c.get("/ratchet/stats").json()["kpis"] == body
    one = c.get("/ratchet/kpis", params={"days": 1}).json()
    assert one["window"] == {"from": 3, "to": 3, "days": 1, "complete": True} and one["window_days"] == 1
    assert c.get("/ratchet/kpis", params={"days": 9}).json()["window"]["days"] == 3
    for bad, status in (("0", 400), (str(kpis.MAX_WINDOW_DAYS + 1), 400), ("-2", 400), ("abc", 422)):
        assert c.get("/ratchet/kpis", params={"days": bad}).status_code == status, bad


@pytest.mark.parametrize("value", ["0", "-3", "3651", "two", "1.5"])
@pytest.mark.req("FR-7.9")
def test_bad_kpi_window_stops_startup(gw, monkeypatch, value):
    monkeypatch.setenv("RATCHET_KPI_WINDOW_DAYS", value)
    with pytest.raises(ValueError, match="RATCHET_KPI_WINDOW_DAYS"):  # reading the variable, or checking it at startup
        gw.Runtime(Settings(mode="mock", mock_warmup_days=0))
    monkeypatch.setenv("RATCHET_KPI_WINDOW_DAYS", "7")
    assert Settings().kpi_window() == 7
    monkeypatch.delenv("RATCHET_KPI_WINDOW_DAYS")
    assert Settings().kpi_window() == kpis.DEFAULT_WINDOW_DAYS == 3
