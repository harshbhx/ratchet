"""Release gate: candidate -> shadow -> canary 10% -> canary 50% -> live, with automatic rollback.

What matters for a cascade is not raw accuracy but the quality of what the student
chooses to serve, so every gate looks at two numbers:
  - precision: how often the answers it would serve agree with the teacher
  - coverage:  how much traffic it would serve (= how much money it saves)

Rules, in order:
  1. Golden gate (after training): precision >= min_precision and coverage >= min_coverage
     on the held-out golden set, using the version's own calibrated threshold, and no
     regression against the live version on either number. Opt-in (ADR 0026): a floor on the
     certified lower bound of precision, and a paired regression check on the same golden rows
     (exact McNemar tests and a negative-flip-rate bound) instead of the two point estimates.
  2. Shadow: it scores real traffic silently. Disagreements with the live student are
     arbitrated by the teacher; one the teacher could not arbitrate counts as a disagreement.
     Promote when precision and coverage hold on real traffic.
  3. Canary 10% -> 50% -> 100%: the teacher audits a sample of what it serves.
  4. Rollback: if the 95% upper confidence bound of audited precision falls below the
     floor (strong evidence it is worse), traffic returns to the previous live version.

With `sequential=1` (opt-in, ADR 0025) the canary rolls back instead when a mixture e-value over every audit it has had
reaches 1/alpha_rollback, which stays valid however often `tick` runs (a fixed-sample Wilson bound checked after every
request does not), with no minimum audit count; it is promoted a stage on the point estimate, or with
`promote_evidence=1` on an e-value that precision is above the floor; and it is retired when a stage reaches
`max_stage_audits` audits without a decision. The live alert uses the same e-process by default (`alert_eprocess`).
Every decision is computed from two integers per version (audits, agreements), so anyone can re-check it.

Each tick starts with the label-free drift monitor (FR-16.6, ADR 0032): a conformal test martingale on every version's
own confidence stream, read from the ledger. It decides no release; an alarm writes a `drift` event and suspends the
version's certificate, and opt-in it boosts that version's audits and lets Night School train sooner.

Each tick ends with the teacher watch (FR-16.7, FR-6.42, ADR 0033): when the teacher's labels disagree with earlier labels
of the same requests too often (an e-process over the epoch's first re-tests), Night School is frozen until an operator
acknowledges. The golden gate also warns when the teacher agrees with itself too little for any student to reach the
precision floor at full coverage (FR-6.43). Neither changes a release decision.
"""

from __future__ import annotations

import math
import os
import threading
from collections.abc import Mapping
from dataclasses import MISSING, dataclass, fields
from typing import Any

from . import certificate, teacher_watch
from .drift import Alarm, DriftMonitor
from .ledger import Ledger, Version
from .stats import LOG_E_CAP, confidence_sequence, cp_lower, e_bernoulli, log_e_bernoulli, wilson  # `release.wilson` too

CANARY_STEPS = [10, 50]
# The confidence sequence every gate event and `ReleaseGate.evidence` report: 95%, anytime-valid (ADR 0025).
CS_ALPHA = 0.05
# The most audits `ReleasePolicy` searches when it tells how many agreeing audits evidence-based promotion needs.
_PROMOTION_SEARCH = 100_000

# ReleasePolicy fields declared for the evidence features (ADR 0024) before the items that read them are built: field ->
# the feature. Their defaults keep today's decisions; until a feature lands, any other value stops startup (from
# `ReleasePolicy.__post_init__`), so nobody relies on a rule that does nothing yet. The item that builds a feature
# removes its fields here; every declared feature is built now, and the check stays for the next one.
RESERVED_POLICY: dict[str, str] = {}


@dataclass
class ReleasePolicy:
    """The release gate's thresholds. Every field can be set with RATCHET_<FIELD> (`from_env`). A value out of range
    raises ValueError naming the variable, at construction, so a typo stops startup instead of changing decisions."""

    min_precision: float = 0.95
    min_coverage: float = 0.25
    tolerance: float = 0.02
    min_shadow: int = 100  # shadow answers it would have served
    min_audits: int = 20
    live_floor: float = 0.92  # audited precision floor for canary/live
    # The anytime-valid gate (FR-16.1, FR-16.2, ADR 0025): the opt-in sequential canary (rollback by an e-process over the
    # canary's life at alpha_rollback; promotion on evidence at alpha_promote, also opt-in; a canary stage with
    # max_stage_audits audits and no decision retired, 0 = never), and the live alert's rule (1 = the e-process, the
    # default; 0 = the Wilson bound) and rate. alpha_rollback, alpha_promote and max_stage_audits apply only with
    # sequential=1.
    sequential: int = 0
    alpha_rollback: float = 0.05
    promote_evidence: int = 0
    alpha_promote: float = 0.05
    max_stage_audits: int = 400
    alert_eprocess: int = 1
    alpha_alert: float = 0.01
    # Certified precision (FR-16.3, ADR 0026): a floor on the certified lower bound (0 = off), and the paired gate (1 =
    # compare the candidate with the live version row by row: reject a regression beyond `tolerance` only when McNemar's
    # exact test is below regression_alpha, or when the negative flip rate's 95% lower bound exceeds max_nfr, 1 = off).
    min_certified_precision: float = 0.0
    paired_gate: int = 0
    regression_alpha: float = 0.05
    max_nfr: float = 1.0
    # The drift alarm (FR-16.6, ADR 0032): the test martingale's threshold (a false alarm has probability at most
    # 1/drift_alarm per stream; 0 = the monitor is off), and the opt-in actions on an alarm: the alarmed version's audit
    # rate multiplied by drift_audit_boost (1 = off, FR-6.40), and Night School needing half the new examples
    # (drift_early_night=1, FR-6.41).
    drift_alarm: float = 100.0
    drift_audit_boost: float = 1.0
    drift_early_night: int = 0
    # The teacher watch (FR-16.7, ADR 0033): the rate at which a fresh teacher label may disagree with an earlier label
    # of the same request (H0), the freeze's error rate (Night School stops when the e-value reaches 1/alpha_teacher,
    # FR-6.42), and the switch (0 = no quarantine, no freeze, no ceiling warning, no probes or adjudications; checks are
    # still recorded).
    teacher_noise_max: float = 0.05
    alpha_teacher: float = 0.01
    teacher_watch: int = 1

    def __post_init__(self) -> None:
        def bad(name: str, value: object, want: str) -> ValueError:
            return ValueError(f"ReleasePolicy.{name} (RATCHET_{name.upper()}) must be {want}, not {value!r}")

        for f in fields(self):
            value = getattr(self, f.name)
            if f.type in (int, "int"):
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise bad(f.name, value, "a whole number, 0 or more")
            elif isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise bad(f.name, value, "a finite number")
        for name in ("min_precision", "min_coverage", "tolerance", "min_certified_precision"):
            if not 0 <= getattr(self, name) <= 1:
                raise bad(name, getattr(self, name), "from 0 to 1")
        for name in (
            "live_floor",
            "alpha_rollback",
            "alpha_promote",
            "alpha_alert",
            "regression_alpha",
            "teacher_noise_max",
            "alpha_teacher",
        ):
            if not 0 < getattr(self, name) < 1:
                raise bad(name, getattr(self, name), "strictly between 0 and 1")
        for name in ("sequential", "promote_evidence", "alert_eprocess", "paired_gate", "drift_early_night", "teacher_watch"):
            if getattr(self, name) not in (0, 1):
                raise bad(name, getattr(self, name), "0 or 1")
        if not 0 < self.max_nfr <= 1:
            raise bad("max_nfr", self.max_nfr, "more than 0 and at most 1")
        if not (self.drift_alarm == 0 or self.drift_alarm > 1):
            raise bad("drift_alarm", self.drift_alarm, "more than 1, or 0 (off)")
        if not self.drift_audit_boost >= 1:
            raise bad("drift_audit_boost", self.drift_audit_boost, "1 or more")
        if self.drift_alarm == 0 and (self.drift_audit_boost > 1 or self.drift_early_night):
            on = "RATCHET_DRIFT_AUDIT_BOOST" if self.drift_audit_boost > 1 else "RATCHET_DRIFT_EARLY_NIGHT"
            raise ValueError(f"{on} acts on drift alarms, but RATCHET_DRIFT_ALARM=0 turns the monitor off")
        defaults = {f.name: f.default for f in fields(self) if f.default is not MISSING}
        for name, feature in RESERVED_POLICY.items():
            if getattr(self, name) != defaults[name]:
                raise ValueError(
                    f"RATCHET_{name.upper()}={getattr(self, name)!r} has no effect yet: it is reserved for {feature}, "
                    f"not built yet; unset it or set it to {defaults[name]}"
                )
        self._check_sequential()

    def _check_sequential(self) -> None:
        """Refuse sequential settings that would do nothing or decide nothing (ADR 0025): evidence-based promotion
        without the sequential rule (the Wilson rule never reads it); a futility cap below the audits a promotion needs,
        which would retire every canary; and evidence-based promotion that cannot reach 1/alpha_promote before the cap,
        even with every audit agreeing."""
        if self.promote_evidence and not self.sequential:
            raise ValueError(
                "RATCHET_PROMOTE_EVIDENCE=1 needs RATCHET_SEQUENTIAL=1: the Wilson rule promotes on the point estimate"
            )
        if not self.sequential or not self.max_stage_audits:
            return
        if self.max_stage_audits < self.min_audits:
            raise ValueError(
                f"RATCHET_MAX_STAGE_AUDITS={self.max_stage_audits} is below RATCHET_MIN_AUDITS={self.min_audits}: every "
                "canary would be retired before it could be promoted; raise it, or set it to 0 (no cap)"
            )
        if self.promote_evidence:
            m, need = self.max_stage_audits, math.log(1 / self.alpha_promote)

            def passes(k: int) -> bool:  # k agreeing audits of k: increasing in k, every alternative being above the floor
                return log_e_bernoulli(k, k, self.live_floor, "above") >= need

            if not passes(m):
                lo, hi = m + 1, _PROMOTION_SEARCH
                fewest = None
                if passes(hi):
                    while lo < hi:  # the fewest audits that pass, by bisection
                        mid = (lo + hi) // 2
                        lo, hi = (lo, mid) if passes(mid) else (mid + 1, hi)
                    fewest = lo
                needed = f"{fewest} audits that all agree" if fewest else f"more than {_PROMOTION_SEARCH:,} audits"
                raise ValueError(
                    f"RATCHET_MAX_STAGE_AUDITS={m} is too few for RATCHET_PROMOTE_EVIDENCE=1: at a floor of "
                    f"{self.live_floor} and RATCHET_ALPHA_PROMOTE={self.alpha_promote} a stage needs {needed}, so every "
                    "canary would be retired; raise it, or set it to 0 (no cap)"
                )

    @classmethod
    def from_env(cls, defaults: Mapping[str, float] | None = None) -> ReleasePolicy:
        """Override any field with RATCHET_<FIELD> (e.g. RATCHET_MIN_AUDITS=30). Integer fields take whole numbers, the
        rest numbers; ValueError names the variable otherwise. `defaults` replaces a field's default when its variable is
        unset (mock mode's `min_audits=15`)."""
        kw: dict[str, float] = dict(defaults or {})
        for f in fields(cls):
            env = f"RATCHET_{f.name.upper()}"
            raw = os.environ.get(env)
            if raw is not None:
                integer = f.type in (int, "int")
                try:
                    kw[f.name] = int(raw) if integer else float(raw)
                except ValueError:
                    raise ValueError(f"{env} must be {'a whole number' if integer else 'a number'}, not {raw!r}") from None
        return cls(**kw)


class ReleaseGate:
    def __init__(self, ledger: Ledger, policy: ReleasePolicy | None = None):
        self.ledger = ledger
        self.policy = policy or ReleasePolicy()
        self._alerted: set[int] = set()  # live versions already alerted on (one alert per version)
        # The label-free drift monitor (FR-16.6, ADR 0032), fed from the ledger by `_tick_drift`; None when
        # RATCHET_DRIFT_ALARM=0. A fresh gate replays the ledger on its first tick.
        self.drift: DriftMonitor | None = DriftMonitor(self.policy.drift_alarm) if self.policy.drift_alarm else None
        # {version id: audit-rate multiplier} for serving versions with an open drift alarm, when drift_audit_boost > 1
        # (FR-6.40). The router reads this very dict (`Ratchet.audit_boost`); the gateway and the simulator share it.
        self.audit_boost: dict[int, float] = {}
        # A mock night (`early_night`) and the gateway's ticks may check drift at once; one at a time keeps the boosts
        # consistent. The monitor has its own lock for its streams.
        self._drift_lock = threading.Lock()
        # Alarms whose `drift` event could not be written yet (the ledger raised); the next check writes them first. The
        # monitor latches an alarm when it sees it, so without this a failed write would never be retried.
        self._unwritten: list[Alarm] = []
        # The teacher watch's running counts of the current epoch (FR-16.7, ADR 0033), read by `_tick_watchers`.
        self.teacher = teacher_watch.Watch()

    # ---- step 1: after training ----
    def admit(self, vid: int) -> bool:
        """The golden gate, in order: the precision and coverage floors; the certified floor (`min_certified_precision`,
        when set); then no regression against the live version: row by row with `paired_gate=1` and the candidate's
        paired comparison against that live version (`_paired_regression`), else on the two point estimates within
        `tolerance`. With the defaults this is v0.4's gate exactly; events only append the paired counts and the
        certificate. Before any rule, an advisory `warning` when the teacher agrees with itself too little for any student
        to reach `min_precision` at full coverage (FR-6.43, `teacher_watch.ceiling_warning`); it decides nothing."""
        p, v = self.policy, self.ledger.version(vid)
        assert v is not None
        ceiling = teacher_watch.ceiling_warning(self.ledger, p)
        if ceiling:
            self.ledger.event("warning", f"v{v.id}: {ceiling}")
        prec, cov = v.eval.get("precision", 0.0), v.eval.get("coverage", 0.0)
        live = self.ledger.live()
        if prec < p.min_precision:
            return self._reject(v, f"golden precision {prec:.1%} < {p.min_precision:.0%}")
        if cov < p.min_coverage:
            return self._reject(v, f"golden coverage {cov:.1%} < {p.min_coverage:.0%}")
        if p.min_certified_precision > 0:
            short = _below_certified_floor(v.eval.get("certificate"), p.min_certified_precision)
            if short:
                return self._reject(v, short)
        paired_note = ""
        if live:
            counts = _paired_counts(v.eval.get("paired"), live.id) if p.paired_gate else None
            if counts is not None:
                why = self._paired_regression(counts, live)
                if why:
                    return self._reject(v, why)
                n, b, c, nf, pf = (counts[k] for k in ("n", "cov_only_live", "cov_only_cand", "neg_flips", "pos_flips"))
                paired_note = (
                    f"; serves {c - b:+d} of {n} vs live v{live.id} (McNemar p={counts['p_cov']:.2f}); "
                    f"{nf} negative flips, {pf} positive flips"
                )
            else:
                lp, lc = live.eval.get("precision", 0.0), live.eval.get("coverage", 0.0)
                if prec < lp - p.tolerance or cov < lc - p.tolerance:
                    return self._reject(
                        v, f"regresses vs live v{live.id} (precision {prec:.1%} vs {lp:.1%}, coverage {cov:.1%} vs {lc:.1%})"
                    )
        for old in self.ledger.versions(["shadow", "canary"]):
            self.ledger.set_version(old.id, "retired", 0)
            self.ledger.event("retired", f"v{old.id} replaced by newer candidate v{v.id}")
        certified = _certified_text(v.eval.get("certificate"))
        if live is None:
            # Cold start: nothing to shadow against, so a small canary audited by the teacher.
            self.ledger.set_version(v.id, "canary", CANARY_STEPS[0])
            self.ledger.event(
                "canary",
                f"v{v.id} first student: golden precision {prec:.1%}, coverage {cov:.1%}; canary {CANARY_STEPS[0]}%{certified}",
            )
        else:
            self.ledger.set_version(v.id, "shadow", 0)
            self.ledger.event(
                "shadow",
                f"v{v.id} passed golden gate (precision {prec:.1%}, coverage {cov:.1%}); shadowing live v{live.id}"
                f"{paired_note}{certified}",
            )
        return True

    def _paired_regression(self, pair: dict[str, Any], live: Version) -> str | None:
        """Why the candidate regresses against `live` on the same held-out rows, or None. A regression must be both
        large and significant: more than `tolerance` of the rows lost from coverage with McNemar's exact one-sided p below
        `regression_alpha`, or more than `tolerance` of the rows newly answered wrong (negative flips minus positive
        flips) with p below it; or, when `max_nfr` < 1, the 95% lower bound of the negative flip rate (answers live
        served right that the candidate serves wrong) above `max_nfr`. Unlike the unpaired rule, a difference within
        noise never rejects."""
        p = self.policy
        n, b, c = pair["n"], pair["cov_only_live"], pair["cov_only_cand"]
        nf, pf = pair["neg_flips"], pair["pos_flips"]
        if n and (b - c) / n > p.tolerance and pair["p_cov"] < p.regression_alpha:
            return (
                f"serves {b - c} fewer of {n} golden rows than live v{live.id} ({b} only live, {c} only the candidate; "
                f"McNemar p={pair['p_cov']:.3g} < {p.regression_alpha:g})"
            )
        if n and (nf - pf) / n > p.tolerance and pair["p_err"] < p.regression_alpha:
            return (
                f"{nf} negative flips vs {pf} positive flips on {n} golden rows against live v{live.id} "
                f"(McNemar p={pair['p_err']:.3g} < {p.regression_alpha:g})"
            )
        lo = pair["nfr_95"][0]
        if p.max_nfr < 1 and lo > p.max_nfr:
            return (
                f"negative flip rate {pair['nfr']:.1%} (95% lower bound {lo:.1%} > {p.max_nfr:.1%}): "
                f"{pair['neg_flips_served']} of the {pair['live_served_ok']} answers live v{live.id} served right"
            )
        return None

    def _reject(self, v: Version, why: str) -> bool:
        self.ledger.set_version(v.id, "rejected", 0)
        self.ledger.event("rejected", f"v{v.id}: {why}")
        return False

    # ---- steps 2-4: call periodically (e.g. every few minutes in production) ----
    def tick(self) -> None:
        """One pass of the gate, in a fixed order: the drift monitor, the live version's alert, the shadow decision, the
        canary decision, then the watchers. `live` is read once, before any step, so every step sees the live version
        the tick started with (a canary promoted in this tick retires that one). Each step belongs to one feature and
        changes only its own function (ADR 0024)."""
        live = self.ledger.live()
        self._tick_drift()
        self._tick_live(live)
        self._tick_shadow(live)
        self._tick_canary(live)
        self._tick_watchers()

    def _tick_drift(self) -> None:
        """The label-free drift monitor (FR-16.6, ADR 0032), first: one primary-key range query for the confidences
        logged since the last tick, each fed to its (version, task) stream (`check_drift`). It decides nothing about a
        release; an alarm writes a `drift` event, suspends the version's certificate, and with the opt-in actions boosts
        its audits and lets Night School train sooner."""
        self.check_drift()

    def check_drift(self) -> list[Alarm]:
        """Bring the drift monitor up to date with the ledger and act on the alarms raised: a `drift` event for each,
        written once per (version, task) across processes and restarts (`Ledger.event_once`), and the audit boosts
        (`audit_boost`) refreshed. Returns the new alarms (none while RATCHET_DRIFT_ALARM=0).

        The event is dated with the alarm's own ledger day, and `meta` records its request and day, because a monitor
        that replays the ledger (a restart, a lowered threshold, a nightly job in its own process) can find an alarm
        days after it happened. An event the ledger refused to write stays queued and is written by the next check; the
        error still propagates."""
        if self.drift is None:
            return []
        with self._drift_lock:
            alarms = self.drift.update(self.ledger)
            self._unwritten.extend(alarms)
            while self._unwritten:
                alarm = self._unwritten[0]
                meta = {"request": alarm.request_id, "day": alarm.day}
                self.ledger.event_once(alarm.key, "drift", alarm.detail(), day=alarm.day, meta=meta)
                self._unwritten.pop(0)
            if self.policy.drift_audit_boost > 1:
                self._refresh_audit_boost()
        return alarms

    def drift_alarm_since(self, ts: float | None) -> bool:
        """Whether some stream alarmed on a request logged after `ts` (any alarm when `ts` is None). The alarm's own
        request decides, not when its event was written, so an alarm a replay finds late does not count as new (FR-6.41).
        Both times are the ledger's wall-clock `ts`, also in simulations."""
        if self.drift is None:
            return False
        latest = self.drift.alarm_requests()[:1]
        if not latest:
            return False
        if ts is None:
            return True
        when = self.ledger.request_ts(latest[0])
        return when is not None and when > ts

    def _refresh_audit_boost(self) -> None:
        """Boost the audits of every version that has an open drift alarm and still serves (canary or live); drop the
        boost of one that no longer does, which is how a newer version going live clears it. In place, so the router's
        reference to the dict stays valid; it only ever reads it with `.get`. Reads the ledger only while some version
        has an alarm or a boost."""
        assert self.drift is not None
        alarmed = self.drift.alarmed_versions()
        if not alarmed and not self.audit_boost:
            return
        states = self.ledger.version_states()
        want = {vid: self.policy.drift_audit_boost for vid in alarmed if states.get(vid) in ("canary", "live")}
        for vid in [v for v in self.audit_boost if v not in want]:
            del self.audit_boost[vid]
        self.audit_boost.update(want)

    def drift_status(self, version: int | None = None, trajectory: bool = True) -> dict[str, Any]:
        """`GET /ratchet/drift`: whether the monitor is on, its threshold and false-alarm bounds, and every stream (or
        `version`'s) with its capital and trajectory (`DriftMonitor.status`)."""
        if self.drift is None:
            return {**self.drift_counts(), "streams": []}
        return {"enabled": True, **self.drift.status(version, trajectory)}

    def drift_counts(self) -> dict[str, Any]:
        """`drift_status` without the streams: what `/ratchet/stats` shows on every poll, without a row per stream."""
        if self.drift is None:
            off = dict.fromkeys(("threshold", "false_alarm_bound", "family_bound"))
            return {"enabled": False, **off, "monitored": 0, "alarms": 0}
        return {"enabled": True, **self.drift.counts()}

    def drift_view(self, version: int) -> dict[str, Any] | None:
        """The certificate endpoint's `drift` for `version`: `alarm` (open or not), the threshold and its streams; None
        when the monitor is off or the version never logged a confidence."""
        return self.drift.version_view(version) if self.drift is not None else None

    def _tick_live(self, live: Version | None) -> None:
        """Alert once per live version when its audited precision since it went live is strongly below the floor (it is
        never rolled back automatically; see docs/RUNBOOK.md). By default (`alert_eprocess=1`, FR-16.2) when the mixture
        e-value against 'precision >= floor' reaches 1/alpha_alert: valid however often the gate looks, so a version at or
        above the floor is alerted on with probability at most alpha_alert, and no minimum audit count is needed. With
        `alert_eprocess=0`, when the Wilson upper bound after at least `min_audits` audits is below the floor."""
        if live is None:
            return
        p = self.policy
        if p.alert_eprocess:
            if live.id in self._alerted:
                return
            n, s = self.ledger.audit_counts(live.id, live.stage_since)
            if n == 0:
                return
            log_e = log_e_bernoulli(s, n, p.live_floor, "below")
            if log_e >= math.log(1 / p.alpha_alert):
                self._alerted.add(live.id)
                self.ledger.event(
                    "alert",
                    f"live v{live.id}: {s}/{n} audits agree; {_e_text(log_e, p.alpha_alert)}; {_cs_text(s, n)}; "
                    "investigate or roll back manually (docs/RUNBOOK.md)",
                )
            return
        n, prec = self.ledger.version_live_quality(live.id)
        if live.id not in self._alerted and n >= p.min_audits and prec is not None and wilson(prec * n, n)[1] < p.live_floor:
            self._alerted.add(live.id)
            self.ledger.event(
                "alert",
                f"live v{live.id} audited precision {prec:.1%} (n={n}) below floor; "
                "investigate or roll back manually (see docs/RUNBOOK.md)",
            )

    def _tick_shadow(self, live: Version | None) -> None:
        """Decide on the shadow once it would have served `min_shadow` answers: reject it, or promote it to canary."""
        p = self.policy
        shadow = self.ledger.by_state("shadow")
        if not shadow:
            return
        s = self.ledger.shadow_quality(shadow.id)
        if s["served"] >= p.min_shadow:
            live_cov = s["live_coverage"] if s["live_coverage"] is not None else 0.0
            # Disagreements the teacher could not arbitrate already count against the candidate; say so.
            unarbitrated = f" ({s['unarbitrated']} unarbitrated counted as disagreements)" if s["unarbitrated"] else ""
            if s["precision"] < p.min_precision:
                self._reject(shadow, f"shadow precision {s['precision']:.1%} < {p.min_precision:.0%}{unarbitrated}")
            elif s["coverage"] < live_cov - p.tolerance:
                self._reject(shadow, f"shadow coverage {s['coverage']:.1%} < live {live_cov:.1%}")
            else:
                self.ledger.set_version(shadow.id, "canary", CANARY_STEPS[0])
                self.ledger.event(
                    "canary",
                    f"v{shadow.id} shadow precision {s['precision']:.1%}{unarbitrated}, coverage "
                    f"{s['coverage']:.1%} (live {live_cov:.1%}); canary {CANARY_STEPS[0]}%",
                )

    def _tick_canary(self, live: Version | None) -> None:
        """Roll the canary back when the Wilson upper bound of its audited precision in this stage is below the floor;
        hold while the point estimate is below it; otherwise advance a stage, or promote it to live over `live`. With
        `sequential=1` the anytime-valid rule decides instead (`_sequential_canary`)."""
        p = self.policy
        canary = self.ledger.by_state("canary")
        if not canary:
            return
        if p.sequential:
            self._sequential_canary(canary, live)
            return
        n, prec = self.ledger.version_live_quality(canary.id)
        if n >= p.min_audits and prec is not None:
            lo, hi = wilson(prec * n, n)
            if hi < p.live_floor:
                self.ledger.set_version(canary.id, "rolled_back", 0)
                back = f"live v{live.id}" if live else "the teacher"
                self.ledger.event(
                    "rollback",
                    f"v{canary.id} audited precision {prec:.1%} (n={n}, upper bound "
                    f"{hi:.1%} < {p.live_floor:.0%}); traffic back to {back}",
                )
                return
            if prec < p.live_floor:
                return  # not enough evidence either way yet: hold this stage, keep auditing
            nxt = [s for s in CANARY_STEPS if s > canary.traffic_pct]
            if nxt:
                self.ledger.set_version(canary.id, "canary", nxt[0])
                self.ledger.event("canary", f"v{canary.id} audited precision {prec:.1%} (n={n}); canary {nxt[0]}%")
            else:
                if live:
                    self.ledger.set_version(live.id, "retired", 0)
                self.ledger.set_version(canary.id, "live", 100)
                self.ledger.event("live", f"v{canary.id} promoted to 100% (audited precision {prec:.1%}, n={n})")

    def _sequential_canary(self, canary: Version, live: Version | None) -> None:
        """The anytime-valid canary rule (FR-16.1, ADR 0025), in order:

        1. Rollback: the mixture e-value against 'precision >= floor' over every audit the canary has had (a canary has
           only ever served as a canary: a shadow never serves) reaches 1/alpha_rollback. One e-process over the whole
           life, never restarted per stage, so P(ever rolling back a version whose precision stays at or above the
           floor) <= alpha_rollback however often the gate runs, with no union bound over stages and no minimum count.
        2. Promotion, on this stage's audits once there are `min_audits`: the point estimate at or above the floor, or
           with `promote_evidence=1` the e-value against 'precision <= floor' reaching 1/alpha_promote, so a version at
           or below the floor reaches live with probability at most alpha_promote (it must pass the last stage's own
           test). Advancing and promoting are as in the Wilson rule; the event appends the evidence.
        3. Futility: `max_stage_audits` audits in this stage and no decision: retired, traffic back as on a rollback.
           It counts audits, not time, so a canary without audits holds, as under the Wilson rule.

        No audit means no decision."""
        p = self.policy
        back = f"live v{live.id}" if live else "the teacher"
        n_st, s_st = self.ledger.audit_counts(canary.id, canary.stage_since)  # first, so the life counts include them
        n, s = self.ledger.audit_counts(canary.id)
        if n > 0:
            log_e = log_e_bernoulli(s, n, p.live_floor, "below")
            if log_e >= math.log(1 / p.alpha_rollback):
                self.ledger.set_version(canary.id, "rolled_back", 0)
                self.ledger.event(
                    "rollback",
                    f"v{canary.id} rolled back: {s}/{n} audits agree; {_e_text(log_e, p.alpha_rollback)}; "
                    f"{_cs_text(s, n)}; traffic back to {back}",
                )
                return
        if n_st > 0 and n_st >= p.min_audits:
            prec = s_st / n_st
            if p.promote_evidence:
                log_ep = log_e_bernoulli(s_st, n_st, p.live_floor, "above")
                earned = log_ep >= math.log(1 / p.alpha_promote)
                why = f"{_e_text(log_ep, p.alpha_promote)} that precision is above {p.live_floor:.0%}"
            else:
                earned = prec >= p.live_floor
                why = f"point estimate at or above {p.live_floor:.0%}"
            if earned:
                evidence = f"{why}; {_cs_text(s_st, n_st)}"
                nxt = [step for step in CANARY_STEPS if step > canary.traffic_pct]
                if nxt:
                    self.ledger.set_version(canary.id, "canary", nxt[0])
                    self.ledger.event(
                        "canary", f"v{canary.id} audited precision {prec:.1%} (n={n_st}); canary {nxt[0]}%; {evidence}"
                    )
                else:
                    if live:
                        self.ledger.set_version(live.id, "retired", 0)
                    self.ledger.set_version(canary.id, "live", 100)
                    self.ledger.event(
                        "live", f"v{canary.id} promoted to 100% (audited precision {prec:.1%}, n={n_st}); {evidence}"
                    )
                return
        if p.max_stage_audits and n_st >= p.max_stage_audits:
            self.ledger.set_version(canary.id, "retired", 0)
            self.ledger.event(
                "inconclusive",
                f"v{canary.id} retired after {n_st} audits at canary {canary.traffic_pct}% without enough evidence either "
                f"way ({s_st}/{n_st} agree); {_cs_text(s_st, n_st)}; traffic back to {back}",
            )

    def evidence(self, v: Version) -> dict[str, Any]:
        """What the gate's rules read for version `v`, from its counts alone (no stored state), so anyone can re-check a
        decision: the rules in force, the floor, the audits and agreements over its life (`n`, `s`) and in its current
        stage (`n_stage`, `s_stage`; for a live version, since it went live), the e-values (`e_rollback`: against
        'precision >= floor' over its life, what the sequential rollback reads; `e_alert`: the same over this stage, what
        the live alert reads; `e_promote`: against 'precision <= floor' over this stage), the thresholds they are compared
        with, the 95% confidence sequence over its life (`cs`, anytime-valid, on a 0.001 grid) and the 95% Wilson interval
        of this stage (`wilson`, what the Wilson rule reads; valid only for one look). Every number is finite."""
        p = self.policy
        n_st, s_st = self.ledger.audit_counts(v.id, v.stage_since)  # first, so the life counts include these
        n, s = self.ledger.audit_counts(v.id)
        lo, hi = confidence_sequence(s, n, CS_ALPHA)
        w_lo, w_hi = wilson(s_st, n_st)
        return {
            "rule": "sequential" if p.sequential else "wilson",
            "promote_rule": "evidence" if p.sequential and p.promote_evidence else "point",
            "alert_rule": "eprocess" if p.alert_eprocess else "wilson",
            "floor": p.live_floor,
            "n": n,
            "s": s,
            "e_rollback": e_bernoulli(s, n, p.live_floor, "below"),
            "n_stage": n_st,
            "s_stage": s_st,
            "e_promote": e_bernoulli(s_st, n_st, p.live_floor, "above"),
            "e_alert": e_bernoulli(s_st, n_st, p.live_floor, "below"),
            "thresholds": {"rollback": 1 / p.alpha_rollback, "promote": 1 / p.alpha_promote, "alert": 1 / p.alpha_alert},
            "cs": [lo, hi],
            "wilson": [w_lo, w_hi],
        }

    def _tick_watchers(self) -> None:
        """The teacher watch (FR-16.7, FR-6.42, ADR 0033), last: the current epoch's new checks are added to the running
        counts (`check_teacher`), and when their e-value reaches 1/alpha_teacher Night School is frozen with one
        `teacher_drift` event. It changes no release decision."""
        self.check_teacher()

    def check_teacher(self) -> str | None:
        """Freeze Night School when the teacher's labels disagree with earlier labels of the same requests too often
        (`teacher_watch.Watch.check`); returns the `teacher_drift` event's text when this call froze it. Night School
        calls it too, right after its probes. None while RATCHET_TEACHER_WATCH=0."""
        return self.teacher.check(self.ledger, self.policy)

    def teacher_status(self) -> dict[str, Any]:
        """`GET /ratchet/teacher` (`teacher_watch.status`)."""
        return teacher_watch.status(self.ledger, self.policy)


def _paired_counts(pair: object, live_id: int) -> dict[str, Any] | None:
    """The candidate's paired comparison against live version `live_id`, recomputed from its counts
    (`certificate.paired_stats`), so the gate never acts on a stored p-value it did not derive. None when there is no
    comparison against that version, or its counts are missing or impossible: the gate then falls back to the unpaired
    rule, as for an eval without one."""
    if not isinstance(pair, dict) or pair.get("against") != live_id:
        return None
    keys = ("n", "cov_only_live", "cov_only_cand", "neg_flips", "pos_flips", "neg_flips_served", "live_served_ok")
    vals = [pair.get(k) for k in keys]
    if any(isinstance(x, bool) or not isinstance(x, int) or x < 0 for x in vals):
        return None
    n, b, c, nf, pf, nf_served, live_ok = vals
    if b + c > n or nf + pf > n or live_ok > n or nf_served > min(nf, live_ok):
        return None
    return certificate.paired_stats(n, b, c, nf, pf, nf_served, live_ok)


def _cert_counts(cert: object) -> tuple[int, int, float] | None:
    """(served, errors, delta) of a certificate, or None when it is missing or malformed."""
    if not isinstance(cert, dict):
        return None
    served, errors, delta = cert.get("served"), cert.get("errors"), cert.get("delta")
    if any(isinstance(x, bool) or not isinstance(x, int) or x < 0 for x in (served, errors)) or errors > served:
        return None
    if isinstance(delta, bool) or not isinstance(delta, (int, float)) or not 0 < delta < 1:
        return None
    return served, errors, float(delta)


def _below_certified_floor(cert: object, floor: float) -> str | None:
    """Why a candidate fails `min_certified_precision`, or None. The lower bound is recomputed from the certificate's
    counts (`stats.cp_lower`); a candidate without a readable certificate fails, because the operator asked for one."""
    got = _cert_counts(cert)
    if got is None:
        return f"no certificate to check against RATCHET_MIN_CERTIFIED_PRECISION={floor:g} (the evaluator wrote none)"
    served, errors, delta = got
    lcb = cp_lower(served - errors, served, delta)
    if lcb >= floor:
        return None
    return f"certified lower bound {lcb:.1%} < {_pct(floor)} ({served - errors}/{served} served answers agree, delta {delta:g})"


def _certified_text(cert: object) -> str:
    """'; certified >= 95% (lcb 96.4%, delta 0.05)' for a candidate whose certificate reaches its target, else ''."""
    got = _cert_counts(cert)
    if got is None or not isinstance(cert, dict) or cert.get("certified") is not True:
        return ""
    target = cert.get("target")
    if isinstance(target, bool) or not isinstance(target, (int, float)) or not 0 < target < 1:
        return ""
    served, errors, delta = got
    lcb = cp_lower(served - errors, served, delta)
    if lcb < target:  # the stated verdict does not survive a recomputation: say nothing rather than repeat it
        return ""
    return f"; certified >= {_pct(target)} (lcb {lcb:.1%}, delta {delta:g})"


def _pct(alpha: float) -> str:
    """An error rate as a percentage without rounding it away: 0.05 -> '5%', 0.025 -> '2.5%'."""
    return f"{alpha * 100:.3g}%"


def _e_text(log_e: float, alpha: float) -> str:
    """'e-value 29.8 >= 20 (anytime-valid, alpha 5%)': the e-value (capped like `stats.e_bernoulli`) and its threshold."""
    return f"e-value {math.exp(min(log_e, LOG_E_CAP)):.3g} >= {1 / alpha:.3g} (anytime-valid, alpha {_pct(alpha)})"


def _cs_text(s: int, n: int) -> str:
    lo, hi = confidence_sequence(s, n, CS_ALPHA)
    return f"95% CS [{lo:.3f}, {hi:.3f}]"
