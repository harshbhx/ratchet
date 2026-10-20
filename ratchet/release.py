"""Release gate: candidate -> shadow -> canary 10% -> canary 50% -> live, with automatic rollback.

What matters for a cascade is not raw accuracy but the quality of what the student
chooses to serve, so every gate looks at two numbers:
  - precision: how often the answers it would serve agree with the teacher
  - coverage:  how much traffic it would serve (= how much money it saves)

Rules, in order:
  1. Golden gate (after training): precision >= min_precision and coverage >= min_coverage
     on the held-out golden set, using the version's own calibrated threshold, and no
     regression against the live version on either number.
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
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import MISSING, dataclass, fields
from typing import Any

from .ledger import Ledger, Version
from .stats import LOG_E_CAP, confidence_sequence, e_bernoulli, log_e_bernoulli, wilson  # `release.wilson` still imports

CANARY_STEPS = [10, 50]
# The confidence sequence every gate event and `ReleaseGate.evidence` report: 95%, anytime-valid (ADR 0025).
CS_ALPHA = 0.05
# The most audits `ReleasePolicy` searches when it tells how many agreeing audits evidence-based promotion needs.
_PROMOTION_SEARCH = 100_000

# ReleasePolicy fields declared for the evidence features (ADR 0024) before the items that read them are built: field ->
# the feature. Their defaults keep today's decisions; until a feature lands, any other value stops startup (from
# `ReleasePolicy.__post_init__`), so nobody relies on a rule that does nothing yet. The item that builds a feature
# removes its fields here.
RESERVED_POLICY: dict[str, str] = {
    "min_certified_precision": "certified precision (FR-16.3)",
    "paired_gate": "certified precision (FR-16.3)",
    "regression_alpha": "certified precision (FR-16.3)",
    "max_nfr": "certified precision (FR-16.3)",
    "drift_alarm": "the drift alarm (FR-16.6)",
    "drift_audit_boost": "the drift alarm (FR-16.6)",
    "drift_early_night": "the drift alarm (FR-16.6)",
    "teacher_noise_max": "the teacher watch (FR-16.7)",
    "alpha_teacher": "the teacher watch (FR-16.7)",
}


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
    # Certified precision (FR-16.3): a floor on the certified lower bound (0 = off) and the paired regression gate.
    min_certified_precision: float = 0.0
    paired_gate: int = 0
    regression_alpha: float = 0.05
    max_nfr: float = 1.0
    # The drift alarm (FR-16.6): the martingale's threshold and the opt-in actions on an alarm.
    drift_alarm: float = 100.0
    drift_audit_boost: float = 1.0
    drift_early_night: int = 0
    # The teacher watch (FR-16.7): the disagreement rate it tolerates and its error rate.
    teacher_noise_max: float = 0.05
    alpha_teacher: float = 0.01

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
        for name in ("live_floor", "alpha_rollback", "alpha_promote", "alpha_alert", "regression_alpha", "teacher_noise_max"):
            if not 0 < getattr(self, name) < 1:
                raise bad(name, getattr(self, name), "strictly between 0 and 1")
        for name in ("sequential", "promote_evidence", "alert_eprocess", "paired_gate", "drift_early_night"):
            if getattr(self, name) not in (0, 1):
                raise bad(name, getattr(self, name), "0 or 1")
        if not 0 < self.max_nfr <= 1:
            raise bad("max_nfr", self.max_nfr, "more than 0 and at most 1")
        if not self.drift_alarm > 1:
            raise bad("drift_alarm", self.drift_alarm, "more than 1")
        if not self.drift_audit_boost >= 1:
            raise bad("drift_audit_boost", self.drift_audit_boost, "1 or more")
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

    # ---- step 1: after training ----
    def admit(self, vid: int) -> bool:
        p, v = self.policy, self.ledger.version(vid)
        assert v is not None
        prec, cov = v.eval.get("precision", 0.0), v.eval.get("coverage", 0.0)
        live = self.ledger.live()
        if prec < p.min_precision:
            return self._reject(v, f"golden precision {prec:.1%} < {p.min_precision:.0%}")
        if cov < p.min_coverage:
            return self._reject(v, f"golden coverage {cov:.1%} < {p.min_coverage:.0%}")
        if live:
            lp, lc = live.eval.get("precision", 0.0), live.eval.get("coverage", 0.0)
            if prec < lp - p.tolerance or cov < lc - p.tolerance:
                return self._reject(
                    v, f"regresses vs live v{live.id} (precision {prec:.1%} vs {lp:.1%}, coverage {cov:.1%} vs {lc:.1%})"
                )
        for old in self.ledger.versions(["shadow", "canary"]):
            self.ledger.set_version(old.id, "retired", 0)
            self.ledger.event("retired", f"v{old.id} replaced by newer candidate v{v.id}")
        if live is None:
            # Cold start: nothing to shadow against, so a small canary audited by the teacher.
            self.ledger.set_version(v.id, "canary", CANARY_STEPS[0])
            self.ledger.event(
                "canary", f"v{v.id} first student: golden precision {prec:.1%}, coverage {cov:.1%}; canary {CANARY_STEPS[0]}%"
            )
        else:
            self.ledger.set_version(v.id, "shadow", 0)
            self.ledger.event(
                "shadow", f"v{v.id} passed golden gate (precision {prec:.1%}, coverage {cov:.1%}); shadowing live v{live.id}"
            )
        return True

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
        """The label-free drift monitor (FR-16.6) will update here, first, from the ledger. Nothing yet."""

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
        """The teacher watch (FR-16.7) will check here, last. Nothing yet."""


def _pct(alpha: float) -> str:
    """An error rate as a percentage without rounding it away: 0.05 -> '5%', 0.025 -> '2.5%'."""
    return f"{alpha * 100:.3g}%"


def _e_text(log_e: float, alpha: float) -> str:
    """'e-value 29.8 >= 20 (anytime-valid, alpha 5%)': the e-value (capped like `stats.e_bernoulli`) and its threshold."""
    return f"e-value {math.exp(min(log_e, LOG_E_CAP)):.3g} >= {1 / alpha:.3g} (anytime-valid, alpha {_pct(alpha)})"


def _cs_text(s: int, n: int) -> str:
    lo, hi = confidence_sequence(s, n, CS_ALPHA)
    return f"95% CS [{lo:.3f}, {hi:.3f}]"
