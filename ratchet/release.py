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

When several tasks share the adapter (FR-14.1, ADR 0040), the golden gate also checks each task's
own held-out numbers once it has `min_task_test_n` test rows: a task below its floor, or behind the
live version on that task, is disabled on the candidate (`task_mode` 'disable': the teacher answers
it, the version ships for its other tasks) or rejects the candidate ('reject'). The live and canary
versions get one `alert` per task whose audited precision is strongly below the floor. Rollback
stays per version.
"""

from __future__ import annotations

import math
import os
from dataclasses import MISSING, dataclass, fields

from . import calibration, tasks
from .confidence import SERVE_NOTHING
from .ledger import Ledger, Version
from .stats import wilson  # defined in stats.py since ADR 0024; `release.wilson` stays importable

CANARY_STEPS = [10, 50]
# What the golden gate does with a task that fails its own check (FR-14.1): `disable` it on the candidate, which ships for
# its other tasks, or `reject` the candidate.
TASK_MODES = ("disable", "reject")

# ReleasePolicy fields declared for the evidence features (ADR 0024) before the items that read them are built: field ->
# the feature. Their defaults keep today's decisions; until a feature lands, any other value stops startup (from
# `ReleasePolicy.__post_init__`), so nobody relies on a rule that does nothing yet. The item that builds a feature
# removes its fields here. `alert_eprocess` is 0 (today's Wilson alert) until the anytime-valid gate makes the e-process
# the default.
RESERVED_POLICY: dict[str, str] = {
    "sequential": "the anytime-valid release gate (FR-16.1)",
    "alpha_rollback": "the anytime-valid release gate (FR-16.1)",
    "promote_evidence": "the anytime-valid release gate (FR-16.1)",
    "alpha_promote": "the anytime-valid release gate (FR-16.1)",
    "max_stage_audits": "the anytime-valid release gate (FR-16.1)",
    "alert_eprocess": "the anytime-valid live alert (FR-16.2)",
    "alpha_alert": "the anytime-valid live alert (FR-16.2)",
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
    # The per-task golden gate (FR-14.1): test rows a task needs before its own numbers are checked, and what a task that
    # fails does (TASK_MODES).
    min_task_test_n: int = 10
    task_mode: str = "disable"
    # The anytime-valid gate (FR-16.1, FR-16.2): opt-in sequential canary, its error rates, the futility cap, and the
    # live alert's rule and rate.
    sequential: int = 0
    alpha_rollback: float = 0.05
    promote_evidence: int = 0
    alpha_promote: float = 0.05
    max_stage_audits: int = 400
    alert_eprocess: int = 0
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
            if f.type in (str, "str"):
                if not isinstance(value, str):
                    raise bad(f.name, value, "a string")
            elif f.type in (int, "int"):
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
        if self.task_mode not in TASK_MODES:
            raise bad("task_mode", self.task_mode, " or ".join(TASK_MODES))
        defaults = {f.name: f.default for f in fields(self) if f.default is not MISSING}
        for name, feature in RESERVED_POLICY.items():
            if getattr(self, name) != defaults[name]:
                raise ValueError(
                    f"RATCHET_{name.upper()}={getattr(self, name)!r} has no effect yet: it is reserved for {feature}, "
                    f"not built yet; unset it or set it to {defaults[name]}"
                )

    @classmethod
    def from_env(cls) -> ReleasePolicy:
        """Override any field with RATCHET_<FIELD> (e.g. RATCHET_MIN_AUDITS=30). Integer fields take whole numbers,
        string fields (`task_mode`) a word, case and surrounding spaces ignored, the rest numbers; ValueError names the
        variable otherwise."""
        kw: dict[str, float | str] = {}
        for f in fields(cls):
            env = f"RATCHET_{f.name.upper()}"
            raw = os.environ.get(env)
            if raw is not None and f.type in (str, "str"):
                if raw.strip():  # set but empty, as in an .env file: the default
                    kw[f.name] = raw.strip().lower()
            elif raw is not None:
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
        # (version, task) pairs known to have their per-task alert in the ledger: a cache of `Ledger.has_event`, which is
        # what makes the alert once-only across gates and processes (FR-14.1).
        self._task_alerted: set[tuple[int, str]] = set()
        # version -> the newest audited request its per-task check has seen: the grouped query runs only after a new audit
        # (`tick` runs after every gateway request).
        self._task_checked: dict[int, int] = {}

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
        failed = self.task_failures(v, live)
        if failed and p.task_mode == "reject":
            return self._reject(v, "; ".join(f"task {name}: {why}" for name, why in failed))
        if calibration.entries(v.eval):
            # What the version would serve at its per-task thresholds, without the tasks it fails: a version that serves
            # (almost) nothing would wait in shadow or canary forever, and no night trains while it does (INV-5).
            left = self._coverage_without(v, {name for name, _ in failed})
            if left < p.min_coverage:
                why = f" once {', '.join(name for name, _ in failed)} disabled" if failed else " at its per-task thresholds"
                return self._reject(v, f"golden coverage {left:.1%} < {p.min_coverage:.0%}{why}")
        if failed:
            for name, why in failed:
                self.ledger.merge_task_eval(v.id, name, {"threshold": SERVE_NOTHING, "disabled": why})
                detail = f"v{v.id} {name}: {why}; the teacher answers it, v{v.id} ships for its other tasks"
                self.ledger.event("task_disabled", detail)
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

    def task_failures(self, v: Version, live: Version | None) -> list[tuple[str, str]]:
        """The tasks of candidate `v` that fail their own golden check (FR-14.1), with why. A task is checked once its
        per-task record has at least `min_task_test_n` held-out rows and the candidate would answer it at all. It fails
        when the 95% Wilson upper bound of its held-out precision (over the answers it would serve) is below its floor
        (its spec's `policy.min_precision`, else the release policy's), or below the live version's precision on that
        task less `tolerance`: strong evidence, as for rollback, because on a few dozen rows a point estimate under the
        floor is mostly noise (a task at 97% shows under 95% on 30 rows about one night in four). Live is compared only
        where it answers the task under the same spec with `min_task_test_n` rows too. A version scored before per-task
        calibration has no records, so nothing fails."""
        p = self.policy
        live_records = calibration.entries(live.eval) if live is not None else {}
        out: list[tuple[str, str]] = []
        for name, rec in sorted(calibration.entries(v.eval).items()):
            task = tasks.get(name)
            test_n, prec = calibration.number(rec.get("test_n")), calibration.number(rec.get("precision"))
            if task.pinned or not calibration.serves(rec) or test_n is None or prec is None or test_n < p.min_task_test_n:
                continue
            served = calibration.served_rows(rec)
            hi = wilson(round(prec * served), served)[1]
            evidence = f"precision {prec:.1%} on {served} answers, upper bound {hi:.1%}"
            floor = calibration.min_precision(task, p.min_precision)
            if hi < floor:
                out.append((name, f"golden {evidence} < {floor:.1%}"))
                continue
            lrec = live_records.get(name)
            if live is None or lrec is None or not calibration.serves(lrec) or lrec.get("spec_hash") != rec.get("spec_hash"):
                continue
            lp, ln = calibration.number(lrec.get("precision")), calibration.number(lrec.get("test_n"))
            if lp is not None and ln is not None and ln >= p.min_task_test_n and hi < lp - p.tolerance:
                out.append((name, f"regresses vs live v{live.id} ({evidence}, vs {lp:.1%})"))
        return out

    @staticmethod
    def _coverage_without(v: Version, disabled: set[str]) -> float:
        """The share of `v`'s held-out rows it would still answer with the tasks in `disabled` on the teacher, each other
        task at its own threshold: what the coverage gate is held to once tasks are disabled."""
        records = calibration.entries(v.eval)
        total = sum(calibration.number(r.get("test_n")) or 0 for r in records.values())
        served = sum(calibration.served_rows(r) for name, r in records.items() if name not in disabled)
        return served / total if total else 0.0

    # ---- steps 2-4: call periodically (e.g. every few minutes in production) ----
    def tick(self) -> None:
        """One pass of the gate, in a fixed order: the drift monitor, the live version's alert, the per-task alerts, the
        shadow decision, the canary decision, then the watchers. `live` is read once, before any step, so every step sees
        the live version the tick started with (a canary promoted in this tick retires that one). Each step belongs to one
        feature and changes only its own function (ADR 0024)."""
        live = self.ledger.live()
        self._tick_drift()
        self._tick_live(live)
        self._tick_tasks(live)
        self._tick_shadow(live)
        self._tick_canary(live)
        self._tick_watchers()

    def _tick_drift(self) -> None:
        """The label-free drift monitor (FR-16.6) will update here, first, from the ledger. Nothing yet."""

    def _tick_live(self, live: Version | None) -> None:
        """Alert once per live version when its audited precision is strongly below the floor (it is never rolled back
        automatically; see docs/RUNBOOK.md)."""
        if live is None:
            return
        p = self.policy
        n, prec = self.ledger.version_live_quality(live.id)
        if live.id not in self._alerted and n >= p.min_audits and prec is not None and wilson(prec * n, n)[1] < p.live_floor:
            self._alerted.add(live.id)
            self.ledger.event(
                "alert",
                f"live v{live.id} audited precision {prec:.1%} (n={n}) below floor; "
                "investigate or roll back manually (see docs/RUNBOOK.md)",
            )

    def _tick_tasks(self, live: Version | None) -> None:
        """Alert once per (version, task) when that task's audited precision in the current stage is strongly below the
        floor (Wilson upper bound < `live_floor`, at least `min_audits` audits), for the live version and the canary
        (FR-14.1). A version whose audits in the stage are all of one task is left to its own alert and rollback, which
        say the same thing. Once-only is read from the ledger's events, so a new gate (each `python -m ratchet.live
        tick` builds one) does not repeat an alert. Nothing is rolled back or disabled here: rollback stays per version,
        and an operator can pin the task to the teacher (docs/RUNBOOK.md)."""
        p = self.policy
        for v in (live, self.ledger.by_state("canary")):
            if v is None:
                continue
            last = self.ledger.last_audit_id(v.id)
            if last is None or self._task_checked.get(v.id) == last:
                continue  # no audit since the last check: nothing can have changed
            self._task_checked[v.id] = last
            quality = self.ledger.task_live_quality(v.id)
            if len(quality) < 2:
                continue
            for name, (n, prec) in quality.items():
                if n < p.min_audits or prec is None or (v.id, name) in self._task_alerted:
                    continue
                hi = wilson(prec * n, n)[1]
                if hi >= p.live_floor:
                    continue
                key = f"v{v.id} task {name}:"
                self._task_alerted.add((v.id, name))
                if self.ledger.has_event("alert", key):
                    continue
                self.ledger.event(
                    "alert",
                    f"{key} audited precision {prec:.1%} (n={n}, upper bound {hi:.1%} < {p.live_floor:.0%}) while {v.state}; "
                    "investigate, or pin the task to the teacher (see docs/RUNBOOK.md)",
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
        hold while the point estimate is below it; otherwise advance a stage, or promote it to live over `live`."""
        p = self.policy
        canary = self.ledger.by_state("canary")
        if not canary:
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

    def _tick_watchers(self) -> None:
        """The teacher watch (FR-16.7) will check here, last. Nothing yet."""
