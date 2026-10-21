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

Every move is compare-and-set (`Ledger.transition`, `Ledger.promote_live`; ADR 0064), so the gateway's tick and the
cron tick can run at once without promoting twice. An operator can hold releases (`releases_frozen`, or an audit rate of
0): no version starts taking traffic or takes more, while rollback, rejections and the live alert keep running.

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
from collections.abc import Mapping
from dataclasses import MISSING, dataclass, fields
from typing import Any

from . import calibration, certificate, tasks
from .confidence import SERVE_NOTHING
from .config import ERROR, ConfigIssue
from .ledger import Ledger, Version
from .stats import LOG_E_CAP, confidence_sequence, cp_lower, e_bernoulli, log_e_bernoulli, wilson  # `release.wilson` too

CANARY_STEPS = [10, 50]
# The confidence sequence every gate event and `ReleaseGate.evidence` report: 95%, anytime-valid (ADR 0025).
CS_ALPHA = 0.05
# The most audits `ReleasePolicy` searches when it tells how many agreeing audits evidence-based promotion needs.
_PROMOTION_SEARCH = 100_000
# What the golden gate does with a task that fails its own check (FR-14.1): `disable` it on the candidate, which ships for
# its other tasks, or `reject` the candidate.
TASK_MODES = ("disable", "reject")

# ReleasePolicy fields declared for the evidence features (ADR 0024) before the items that read them are built: field ->
# the feature. Their defaults keep today's decisions; until a feature lands, any other value stops startup (from
# `ReleasePolicy.__post_init__`), so nobody relies on a rule that does nothing yet. The item that builds a feature
# removes its fields here.
RESERVED_POLICY: dict[str, str] = {
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
        """Override any field with RATCHET_<FIELD> (e.g. RATCHET_MIN_AUDITS=30). Integer fields take whole numbers,
        string fields (`task_mode`) a word, case and surrounding spaces ignored, the rest numbers; ValueError names the
        variable otherwise. `defaults` replaces a field's default when its variable is
        unset (mock mode's `min_audits=15`)."""
        kw: dict[str, float | str] = dict(defaults or {})
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

    @classmethod
    def env_issues(cls, environ: Mapping[str, str] | None = None) -> list[ConfigIssue]:
        """Every problem with the RATCHET_<FIELD> variables in `environ` (default os.environ), all at once: each one that
        does not parse, or is out of range on its own (as building the policy would say, one field at a time), then the
        rules across fields (`validate`) over the ones that are fine. `from_env` stops at the first; the gateway's startup
        and `python -m ratchet.config check` list them all."""
        env = os.environ if environ is None else environ
        issues: list[ConfigIssue] = []
        kw: dict[str, float] = {}
        for f in fields(cls):
            var = f"RATCHET_{f.name.upper()}"
            raw = env.get(var)
            if raw is None:
                continue
            integer = f.type in (int, "int")
            try:
                value = int(raw) if integer else float(raw)
            except ValueError:
                expected = "a whole number" if integer else "a number"
                issues.append(ConfigIssue(ERROR, var, f"{var} must be {expected}, not {raw!r}"))
                continue
            try:
                cls(**{f.name: value})
            except ValueError as e:  # out of range, or reserved for a feature not built yet
                issues.append(ConfigIssue(ERROR, var, str(e)))
                continue
            kw[f.name] = value
        try:
            issues.extend(cls(**kw).validate())
        except ValueError as e:  # pragma: no cover - __post_init__ checks one field at a time, each passed above
            issues.append(ConfigIssue(ERROR, "", str(e)))
        return issues

    def validate(self) -> list[ConfigIssue]:
        """Rules across fields, which the checks at construction cannot see (FR-12.30, ADR 0070): 0 < live_floor <=
        min_precision <= 1 (a rollback floor above the golden gate's bar would roll back versions the gate just admitted),
        min_audits and min_shadow at least 1 (0 would decide on no evidence), tolerance from 0 to MAX_TOLERANCE (more
        would admit a large regression against the live version). The gateway's startup and `python -m ratchet.config
        check` call it; building a policy does not, so tests and simulations may still use any value."""
        issues: list[ConfigIssue] = []
        if not 0 < self.live_floor <= self.min_precision <= 1:
            issues.append(
                ConfigIssue(
                    ERROR,
                    "RATCHET_LIVE_FLOOR",
                    f"RATCHET_LIVE_FLOOR ({self.live_floor:g}) must be above 0 and at most RATCHET_MIN_PRECISION "
                    f"({self.min_precision:g}), itself at most 1: a rollback floor above the golden gate's bar rolls back "
                    "versions the gate admitted",
                )
            )
        for name in ("min_audits", "min_shadow"):
            if getattr(self, name) < 1:
                env = f"RATCHET_{name.upper()}"
                issues.append(
                    ConfigIssue(ERROR, env, f"{env} must be 1 or more, not {getattr(self, name)!r}: 0 decides on no evidence")
                )
        if not 0 <= self.tolerance <= MAX_TOLERANCE:
            issues.append(
                ConfigIssue(
                    ERROR,
                    "RATCHET_TOLERANCE",
                    f"RATCHET_TOLERANCE must be from 0 to {MAX_TOLERANCE:g}, not {self.tolerance!r}: more admits a candidate "
                    "that regresses that far against the live version",
                )
            )
        return issues


MAX_TOLERANCE = 0.2  # ReleasePolicy.validate: the largest regression against live the golden gate may tolerate


def policy_env() -> frozenset[str]:
    """The variables `ReleasePolicy.from_env` reads: RATCHET_<FIELD> for every field."""
    return frozenset(f"RATCHET_{f.name.upper()}" for f in fields(ReleasePolicy))


class ReleaseGate:
    def __init__(self, ledger: Ledger, policy: ReleasePolicy | None = None):
        self.ledger = ledger
        self.policy = policy or ReleasePolicy()
        self._alerted: set[int] = set()  # live versions already alerted on (one alert per version)
        # (version, task) pairs known to have their per-task alert in the ledger: a cache in front of `Ledger.event_once`,
        # which is what makes the alert once-only across gates and processes (FR-14.1).
        self._task_alerted: set[tuple[int, str]] = set()
        # version -> the newest audited request its per-task check has seen: the grouped query runs only after a new audit
        # (`tick` runs after every gateway request).
        self._task_checked: dict[int, int] = {}

    # ---- step 1: after training ----
    def admit(self, vid: int) -> bool:
        """The golden gate, then the candidate's first stage. The gate, in order: the precision and coverage floors; the
        certified floor (`min_certified_precision`, when set); then no regression against the live version: row by row
        with `paired_gate=1` and the candidate's paired comparison against that live version (`_paired_regression`), else
        on the two point estimates within `tolerance`. With the defaults this is v0.4's gate exactly; events only append
        the paired counts and the certificate. One transaction (ADR 0064): the checks, the retirement of an older shadow
        or canary and the candidate's move commit together, so no other process (the gateway's tick, an admin) sees half
        of it or acts in between. A version that is no longer a candidate (an admin moved it while it was scored) is left
        as it is, with a `conflict` event."""
        p = self.policy
        with self.ledger.transaction():
            v = self.ledger.version(vid)
            assert v is not None
            if v.state != "candidate":
                self._conflict(v)
                return False
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
                            v,
                            f"regresses vs live v{live.id} (precision {prec:.1%} vs {lp:.1%}, coverage {cov:.1%} vs {lc:.1%})",
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
                self.ledger.transition(
                    old.id,
                    old.state,
                    old.traffic_pct,
                    "retired",
                    0,
                    ("retired", f"v{old.id} replaced by newer candidate v{v.id}"),
                )
            certified = _certified_text(v.eval.get("certificate"))
            held = self._held()
            if live is None and held is None:
                # Cold start: nothing to shadow against, so a small canary audited by the teacher.
                moved = self.ledger.transition(
                    v.id,
                    v.state,
                    v.traffic_pct,
                    "canary",
                    CANARY_STEPS[0],
                    (
                        "canary",
                        f"v{v.id} first student: golden precision {prec:.1%}, coverage {cov:.1%}; canary {CANARY_STEPS[0]}%"
                        f"{certified}",
                    ),
                )
            else:
                # A cold start while an operator holds releases shadows the teacher instead: no traffic moves until the
                # hold is lifted, and then the shadow is promoted on its evidence as usual.
                against = f"shadowing live v{live.id}" if live is not None else f"shadowing the teacher ({held})"
                moved = self.ledger.transition(
                    v.id,
                    v.state,
                    v.traffic_pct,
                    "shadow",
                    0,
                    (
                        "shadow",
                        f"v{v.id} passed golden gate (precision {prec:.1%}, coverage {cov:.1%}); {against}"
                        f"{paired_note}{certified}",
                    ),
                )
            if not moved:  # cannot happen inside the transaction; kept so a refused move is never silent
                self._conflict(v)
            return moved

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
        """Reject `v` unless another process moved it first (then that process's decision stands)."""
        self.ledger.transition(v.id, v.state, v.traffic_pct, "rejected", 0, ("rejected", f"v{v.id}: {why}"))
        return False

    def _conflict(self, v: Version) -> None:
        """A candidate `admit` passed could not be placed: another process (an admin, the cron tick) moved it, or put
        another version in flight, while the gate decided. Nothing was written, so the log says what happened instead."""
        now = self.ledger.version(v.id)
        self.ledger.event(
            "conflict", f"v{v.id} left as {now.state if now else 'missing'}: changed by another process while the gate decided"
        )

    def _held(self) -> str | None:
        """Why no new version may take traffic now, or None: an operator froze releases (`releases_frozen`), or set the
        audit rate to 0, so a canary could neither advance nor be rolled back (ADR 0064)."""
        flags = self.ledger.flags()
        if flags.releases_frozen:
            return "releases frozen"
        if flags.audit_rate == 0:
            return "audit rate 0"
        return None

    def task_failures(self, v: Version, live: Version | None) -> list[tuple[str, str]]:
        """The tasks of candidate `v` that fail their own golden check (FR-14.1), with why. A task is checked once its
        per-task record has at least `min_task_test_n` held-out rows and the candidate would answer it at all. It fails
        when its held-out precision (over the answers it would serve) is below its floor (its spec's
        `policy.min_precision`, else the release policy's), or below the live version's precision on that task less
        `tolerance`: the global gate's rule on the task's own rows, so a task that shares an adapter is held to what an
        adapter of its own would be. A task that answers none of its held-out rows has no precision to show, so it
        fails too: the student would serve it without evidence. Live is compared only where it answers the task under
        the same spec with `min_task_test_n` rows too. On a few dozen rows a good task can dip under its floor; in
        `disable` mode that costs coverage on that task, never precision, and RATCHET_MIN_TASK_TEST_N asks for more rows.
        A version scored before per-task calibration has no records, so nothing fails."""
        p = self.policy
        live_records = calibration.entries(live.eval) if live is not None else {}
        out: list[tuple[str, str]] = []
        for name, rec in sorted(calibration.entries(v.eval).items()):
            task = tasks.get(name)
            test_n, prec = calibration.number(rec.get("test_n")), calibration.number(rec.get("precision"))
            if task.pinned or not calibration.serves(rec) or test_n is None or prec is None or test_n < p.min_task_test_n:
                continue
            served = calibration.served_rows(rec)
            if served == 0:
                out.append((name, f"golden: answers none of its {test_n:.0f} held-out rows, so its precision is unmeasured"))
                continue
            evidence = f"precision {prec:.1%} on {served} answers"
            floor = calibration.min_precision(task, p.min_precision)
            if prec < floor:
                out.append((name, f"golden {evidence} < {floor:.1%}"))
                continue
            lrec = live_records.get(name)
            if live is None or lrec is None or not calibration.serves(lrec) or lrec.get("spec_hash") != rec.get("spec_hash"):
                continue
            lp, ln = calibration.number(lrec.get("precision")), calibration.number(lrec.get("test_n"))
            if lp is not None and ln is not None and ln >= p.min_task_test_n and prec < lp - p.tolerance:
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

    def _tick_tasks(self, live: Version | None) -> None:
        """Alert once per (version, task) when that task's audited precision in the current stage is strongly below the
        floor (Wilson upper bound < `live_floor`, at least `min_audits` audits), for the live version and the canary
        (FR-14.1). A version whose audits in the stage are all of one task is left to its own alert and rollback, which
        say the same thing. Once-only is read from the ledger's events, in the transaction that writes the alert
        (`Ledger.event_once`), so a new gate (each `python -m ratchet.live tick` builds one) does not repeat an alert, nor
        does the gateway's own tick racing a cron tick on the same file. Nothing is rolled back or disabled here:
        rollback stays per version, and an operator can pin the task to the teacher (docs/RUNBOOK.md)."""
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
                self.ledger.event_once(
                    "alert",
                    key,
                    f"{key} audited precision {prec:.1%} (n={n}, upper bound {hi:.1%} < {p.live_floor:.0%}) while {v.state}; "
                    "investigate, or pin the task to the teacher (see docs/RUNBOOK.md)",
                )

    def _tick_shadow(self, live: Version | None) -> None:
        """Decide on the shadow once it would have served `min_shadow` answers: reject it, or promote it to canary unless
        an operator holds releases (`_held`)."""
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
            elif self._held() is None:  # a hold (ADR 0064) keeps it shadowing; a rejection still runs
                self.ledger.transition(
                    shadow.id,
                    "shadow",
                    shadow.traffic_pct,
                    "canary",
                    CANARY_STEPS[0],
                    (
                        "canary",
                        f"v{shadow.id} shadow precision {s['precision']:.1%}{unarbitrated}, coverage "
                        f"{s['coverage']:.1%} (live {live_cov:.1%}); canary {CANARY_STEPS[0]}%",
                    ),
                )

    def _tick_canary(self, live: Version | None) -> None:
        """Roll the canary back when the Wilson upper bound of its audited precision in this stage is below the floor;
        hold while the point estimate is below it, or while an operator froze releases; otherwise advance a stage, or
        promote it to live over `live`, which is retired with a `live_retired` event in the same transaction. With
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
            if hi < p.live_floor:  # a rollback runs whatever an operator holds (ADR 0064)
                back = f"live v{live.id}" if live else "the teacher"
                self.ledger.transition(
                    canary.id,
                    "canary",
                    canary.traffic_pct,
                    "rolled_back",
                    0,
                    (
                        "rollback",
                        f"v{canary.id} audited precision {prec:.1%} (n={n}, upper bound "
                        f"{hi:.1%} < {p.live_floor:.0%}); traffic back to {back}",
                    ),
                )
                return
            if prec < p.live_floor:
                return  # not enough evidence either way yet: hold this stage, keep auditing
            if self.ledger.flags().releases_frozen:
                return  # an operator froze releases: no advance and no promotion until they lift it (ADR 0064)
            nxt = [s for s in CANARY_STEPS if s > canary.traffic_pct]
            if nxt:
                self.ledger.transition(
                    canary.id,
                    "canary",
                    canary.traffic_pct,
                    "canary",
                    nxt[0],
                    ("canary", f"v{canary.id} audited precision {prec:.1%} (n={n}); canary {nxt[0]}%"),
                )
            else:
                # One transaction retires `live` and promotes the canary, both compare-and-set, so two versions are never
                # live and a second process promotes nothing. The retirement has its own event (ADR 0062), so every
                # version transition is in the log, of a kind of its own: the v0.4 kind `retired` names candidates
                # replaced at admission, which the pinned reference runs count.
                self.ledger.promote_live(
                    canary.id,
                    "canary",
                    canary.traffic_pct,
                    ("live", f"v{canary.id} promoted to 100% (audited precision {prec:.1%}, n={n})"),
                    old_live=live.id if live else None,
                    retired_event=("live_retired", f"v{live.id} retired: v{canary.id} replaces it as live") if live else None,
                )

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
