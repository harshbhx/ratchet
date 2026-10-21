"""Teacher watch (FR-16.7, ADR 0033): who watches the teacher?

Every number Ratchet reports is measured against the teacher: training targets, golden references, audits. The code
assumes the teacher is right, stable and deterministic. A hosted model at temperature 0 is not quite deterministic, and
it can change without notice (another quantisation or checkpoint behind the same name); Night School would then train
on shifted labels and grade candidates against them.

The cascade already pays for a check. Requests repeat, and a repeated request the student does not serve is labelled by
the teacher again (an escalation or an audit). Comparing that label with the stored one tests the teacher for free:

- Checks. A stored request's first re-test in a check epoch is compared with its stored label (`Ledger.add_example`,
  `Ledger._check_label`); Night School adds probes for traffic with few repeats (`probe`, FR-5.40). Each check is a
  `teacher_checks` row.
- Quarantine. A label a re-test disagreed with is `contested`: left out of training and of the golden set
  (`dataset.rows`) until Night School adjudicates it (`adjudicate`, FR-5.41) with two more answers from the same teacher;
  a strict majority of the three becomes the label, so its provenance and license do not change (INV-1, INV-2).
- Freeze (FR-6.42). H0: a fresh teacher label of a request disagrees with an independent earlier label of it with
  probability at most `teacher_noise_max` (5%). The mixture e-value `stats.e_bernoulli(D, N, eps, 'above')` over the
  epoch's checks is then a supermartingale, so it reaches 1/`alpha_teacher` (100) with probability at most
  `alpha_teacher` (1%) however often it is checked. When it does, the release gate writes `teacher_drift` and Night School
  stops training until an operator acknowledges (`POST /ratchet/teacher/ack`, which starts a new epoch).
- Model ids. A change in the model id the teacher's responses report starts a new epoch (`Ledger.teacher_model_seen`).
- Ceiling (FR-6.43). A teacher that agrees with itself at rate A lets no student agree with it more than sqrt(A) of the
  time at full coverage (`ceiling`); the golden gate warns when its precision floor is above that.

Why at most one check per request per epoch: a stored label is one draw, and comparing it with several re-tests re-uses
it, so those checks are dependent (a minority stored label makes every re-test disagree). The first re-test alone
compares two independent draws, whose disagreement probability under H0 is at most eps whatever happened to other
requests before, which is the condition Ville's inequality needs. For the same reason a contested label is not compared
again in a later epoch (a disagreement selected it), nor is a label an adjudication chose (`ledger.UNCHECKABLE`); a
label that agreed before may be, because conditioning on an agreement only lowers the chance of the next disagreement
(sum p^3 >= (sum p^2)^2). The epoch's checks also assume that whether a request gets re-tested does not depend on which
label is stored: true for probes (hash order), audits (a random draw) and cold-start escalations, and nearly so for
escalations by confidence (ADR 0033).

A label the teacher sampled above temperature 0 is never compared (`ledger.UNCHECKABLE`): two such labels may differ by
design. Recording checks costs one indexed lookup per stored duplicate; `RATCHET_TEACHER_WATCH=0` turns off everything
done with them (the quarantine, the freeze, the warning, the probes and the adjudications).
"""

from __future__ import annotations

import json
import math
import threading
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, NamedTuple, Protocol

from . import tasks
from .config import Price
from .ledger import CHECK_SOURCES, TEACHER_MODEL, Ledger
from .providers import COMPLETE, Completion, GenParams, GenPolicy, Provider, UpstreamError
from .redact import redact
from .resilience import BAD_RESPONSE, describe_failure
from .stats import LOG_E_CAP, log_e_bernoulli, wilson

# Checks the golden gate's ceiling warning needs: with fewer, the interval on A is too wide to say anything.
MIN_CEILING_CHECKS = 30
# How many of a night's teacher answers a contested label needs besides its own to be adjudicated.
ADJUDICATION_DRAWS = 2


class _Policy(Protocol):
    """The `ReleasePolicy` fields the watch reads."""

    teacher_watch: int
    teacher_noise_max: float
    alpha_teacher: float
    min_precision: float


# ---------------------------------------------------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------------------------------------------------
def self_agreement(probs: Iterable[float]) -> float:
    """The probability that two independent teacher answers to one request agree, when the teacher gives label y with
    probability p_y: sum p_y^2."""
    return float(sum(p * p for p in probs))


def ceiling(agreement: float) -> float:
    """The most any student can agree with a fresh teacher label at full coverage when the teacher agrees with itself at
    rate `agreement`: sqrt(agreement). Per request a student's agreement is sum_y s_y p_y <= max_y p_y, and
    max_y p_y <= sqrt(sum_y p_y^2) because sum_y p_y^2 >= (max_y p_y)^2. Over requests, E[max p] <= E[sqrt(a)] <=
    sqrt(E[a]) = sqrt(A) by Jensen's inequality (sqrt is concave). A student that abstains can do better on what it
    serves, so this bounds full coverage only."""
    return math.sqrt(min(max(float(agreement), 0.0), 1.0))


def summary(checks: int, disagreements: int) -> dict[str, Any]:
    """The rates the watch reports for a set of checks: `checks`, `disagreements`, `self_agreement` (1 - D/N), its 95%
    Wilson `interval` and the `ceiling` at the interval's upper end (the optimistic end, so a warning is never premature).
    The rates are None without checks."""
    if checks == 0:
        return {"checks": 0, "disagreements": 0, "self_agreement": None, "interval": None, "ceiling": None}
    lo, hi = wilson(checks - disagreements, checks)
    return {
        "checks": checks,
        "disagreements": disagreements,
        "self_agreement": 1 - disagreements / checks,
        "interval": [lo, hi],
        "ceiling": ceiling(hi),
    }


class Evidence(NamedTuple):
    """The freeze rule's evidence (`evidence`): the log e-value and the stretch of days that weighs most in it."""

    log_e: float
    from_day: int | None  # the ledger day the strongest stretch starts (None without checks)
    checks: int  # that stretch's checks
    disagreements: int

    @property
    def e_value(self) -> float:
        """The e-value, capped like `stats.e_bernoulli` so it stays a finite float."""
        return math.exp(min(self.log_e, LOG_E_CAP))


def day_weight(j: int) -> float:
    """The weight of the e-process that starts on the j-th day of an epoch (j >= 1): 1/(j(j+1)), which sums to 1."""
    return 1 / (j * (j + 1))


def _logsumexp(xs: list[float]) -> float:
    top = max(xs)
    return top + math.log(sum(math.exp(x - top) for x in xs))


def evidence(days: Iterable[tuple[int, int, int]], noise_max: float, since_day: int = 1) -> Evidence:
    """The epoch's e-value against H0 'a fresh label disagrees with an earlier one with probability at most noise_max',
    from its checks by ledger day (`days`: (day, checks, disagreements), any order, a day may repeat) and the day the
    epoch started.

    It is a mixture, over the days of the epoch, of the e-process that starts at the beginning of each day: the j-th
    day's (j = day - since_day + 1) is `stats.e_bernoulli` over every check from that day on, with weight 1/(j(j+1)).
    Each of them is 1 until its day starts and a nonnegative supermartingale after (a check's conditional chance of
    disagreeing is at most noise_max under H0, given the earlier checks), and the start times are calendar days, fixed in
    advance, so the mixture is a supermartingale that starts at 1 (the weights sum to 1; a day's e-process that has not
    started counts 1). Ville's inequality then bounds the chance that it ever reaches 1/alpha by alpha, however often it is
    checked, as for a single e-process. The restarts are what lets a teacher that changes in the middle of a long epoch be
    caught: a single e-process over the whole epoch would first have to win back what weeks of agreeing checks cost it.
    The price is a factor 2 on the first day's e-process (weight 1/2) and j(j+1) on the j-th day's."""
    per_day: dict[int, list[int]] = {}
    for day, n, d in days:
        c = per_day.setdefault(int(day), [0, 0])
        c[0], c[1] = c[0] + int(n), c[1] + int(d)
    suffix: list[tuple[int, int, int]] = []
    n = d = 0
    for day in sorted(per_day, reverse=True):
        n, d = n + per_day[day][0], d + per_day[day][1]
        suffix.append((day, n, d))
    terms: list[float] = []
    best: tuple[float, int, int, int] | None = None
    j_prev = 0
    for day, n, d in reversed(suffix):  # days in order, each with the counts from it to the end
        # The days since the previous day with checks share this day's counts; their weights add up to this.
        j = max(day - since_day + 1, j_prev + 1)
        term = math.log(1 / (j_prev + 1) - 1 / (j + 1)) + log_e_bernoulli(d, n, noise_max, "above")
        terms.append(term)
        if best is None or term > best[0]:
            best = (term, day, n, d)
        j_prev = j
    terms.append(-math.log(j_prev + 1))  # the e-processes of later days, not started: 1 each
    if best is None:
        return Evidence(0.0, None, 0, 0)
    return Evidence(_logsumexp(terms), best[1], best[2], best[3])


def first_alarm(disagreed: Iterable[bool], noise_max: float, alpha: float, days: Iterable[int] | None = None) -> int | None:
    """The number of checks after which the freeze rule's e-value (`evidence`) first reaches 1/alpha, or None.
    `disagreed` has one bool per check in order; `days` the ledger day of each (all day 1 when None). The e-value only
    rises on a disagreement (an agreement lowers every e-process in it), so only those steps are tested. The release
    gate's rule, for tests and simulations."""
    need = math.log(1 / alpha)
    counts: dict[int, list[int]] = {}
    day_of = iter(days) if days is not None else None
    n = 0
    for bad in disagreed:
        n += 1
        day = next(day_of) if day_of is not None else 1
        c = counts.setdefault(day, [0, 0])
        c[0] += 1
        if bad:
            c[1] += 1
            if evidence(((k, v[0], v[1]) for k, v in counts.items()), noise_max).log_e >= need:
                return n
    return None


def _pct(x: float) -> str:
    """A rate as a percentage without rounding it away: 0.05 -> '5%', 0.025 -> '2.5%'."""
    return f"{x * 100:.3g}%"


def drift_detail(ev: Evidence, epoch: int, noise_max: float, alpha: float) -> str:
    """The `teacher_drift` event's text."""
    return (
        f"teacher labels disagree with an earlier label for the same request in {ev.disagreements} of {ev.checks} first "
        f"re-tests since day {ev.from_day} of epoch {epoch} (e-value {ev.e_value:.3g} >= {1 / alpha:.0f}; H0: "
        f"disagreement <= {_pct(noise_max)}); Night School paused until an operator acks (POST /ratchet/teacher/ack)"
    )


# ---------------------------------------------------------------------------------------------------------------------
# The release gate's side: the freeze and the ceiling warning
# ---------------------------------------------------------------------------------------------------------------------
class Watch:
    """The release gate's running counts of the current epoch's checks, by ledger day. Each `check` reads only the check
    rows added since the last one (a primary-key range) and the epoch from `meta`, so it costs two indexed lookups on a
    quiet ledger; a new epoch (an ack or a model change, in any process) starts the counts again."""

    def __init__(self) -> None:
        self.epoch = 0
        self.since_day = 1
        self.last_id = 0
        self.days: dict[int, list[int]] = {}  # ledger day -> [checks, disagreements]
        self._tested: tuple[int, int] | None = None  # (epoch, last check id) last tested and found quiet
        self._lock = threading.Lock()

    def refresh(self, ledger: Ledger) -> bool:
        """Bring the counts up to date; returns whether Night School is frozen."""
        state = ledger.teacher_state()
        if state.epoch != self.epoch:
            self.epoch, self.since_day, self.last_id, self.days = state.epoch, state.since_day, 0, {}
        self.last_id, rows = ledger.teacher_check_days(state.epoch, self.last_id)
        for _, day, n, d in rows:
            c = self.days.setdefault(day, [0, 0])
            c[0], c[1] = c[0] + n, c[1] + d
        return state.frozen

    def evidence(self, noise_max: float) -> Evidence:
        return evidence(((day, n, d) for day, (n, d) in self.days.items()), noise_max, self.since_day)

    def check(self, ledger: Ledger, policy: _Policy) -> str | None:
        """Freeze Night School when the epoch's e-value reaches 1/alpha_teacher (`Ledger.teacher_freeze`, which writes
        the `teacher_drift` event once across processes); returns the event's text when this call froze it."""
        if not policy.teacher_watch:
            return None
        with self._lock:
            frozen = self.refresh(ledger)
            seen = (self.epoch, self.last_id)
            if frozen or not self.days or seen == self._tested:
                return None
            ev = self.evidence(policy.teacher_noise_max)
            if ev.log_e < math.log(1 / policy.alpha_teacher):
                self._tested = seen
                return None
            detail = drift_detail(ev, self.epoch, policy.teacher_noise_max, policy.alpha_teacher)
            return detail if ledger.teacher_freeze(self.epoch, detail) else None


def check(ledger: Ledger, policy: _Policy, watch: Watch | None = None) -> str | None:
    """The freeze rule once (`Watch.check`): the release gate keeps one `Watch` across ticks; without one, the epoch's
    counts are read in full."""
    return (watch or Watch()).check(ledger, policy)


def _epoch_counts(ledger: Ledger) -> tuple[int, int, int]:
    """(epoch, checks, disagreements) of the current epoch."""
    epoch = ledger.teacher_state().epoch
    n = d = 0
    for checks, disagreements in ledger.teacher_check_counts(epoch).values():
        n, d = n + checks, d + disagreements
    return epoch, n, d


def ceiling_warning(ledger: Ledger, policy: _Policy) -> str | None:
    """The golden gate's warning (FR-6.43), or None: with at least MIN_CEILING_CHECKS checks in the current epoch and a
    precision floor above the ceiling (sqrt of the self-agreement's upper 95% bound), no student can meet the floor at
    full coverage against this teacher. Advisory: no decision changes."""
    if not policy.teacher_watch:
        return None
    epoch, n, d = _epoch_counts(ledger)
    if n < MIN_CEILING_CHECKS:
        return None
    s = summary(n, d)
    if policy.min_precision <= s["ceiling"]:
        return None
    return (
        f"no student can show more than {s['ceiling']:.1%} agreement at full coverage with this teacher (golden floor "
        f"{_pct(policy.min_precision)}): it agrees with itself in {n - d} of {n} first re-tests in epoch {epoch} (95% "
        f"upper bound {s['interval'][1]:.1%})"
    )


def eligible_tasks() -> list[str]:
    """The registered tasks whose labels can be checked: they name fields (`Task.verifiable`) and are not pinned."""
    return sorted(t.name for t in tasks.registered() if t.verifiable and not t.pinned)


def status(ledger: Ledger, policy: _Policy) -> dict[str, Any]:
    """`GET /ratchet/teacher`: whether the watch acts, the epoch and the day it started, whether Night School is frozen,
    the model id the teacher last reported, the rule (H0's rate, alpha, the threshold), the epoch's checks with the
    teacher's self-agreement, its interval and ceiling, the freeze rule's e-value and its strongest stretch of days
    (`evidence`), the checks by source and by task (each task's e-value is diagnostic only: the freeze tests the pooled
    checks), and the contested labels (of every epoch, still quarantined). Four small queries."""
    state = ledger.teacher_state()
    eps = policy.teacher_noise_max
    _, rows = ledger.teacher_check_days(state.epoch, by_task=True)
    by_task: dict[str, list[tuple[int, int, int]]] = {}
    for task, day, n, d in rows:
        by_task.setdefault(task, []).append((day, n, d))
    by_source = ledger.teacher_check_counts(state.epoch, by="source")
    contested = ledger.contested_counts()
    n = sum(r[2] for r in rows)
    d = sum(r[3] for r in rows)
    ev = evidence(((day, c, x) for _, day, c, x in rows), eps, state.since_day)
    sources = {src: dict(zip(("checks", "disagreements"), by_source.get(src, (0, 0)), strict=True)) for src in CHECK_SOURCES}
    tasks_out = []
    for t in sorted(set(by_task) | set(contested)):
        days = by_task.get(t, [])
        tasks_out.append(
            {
                "task": t,
                **summary(sum(r[1] for r in days), sum(r[2] for r in days)),
                "e_value": evidence(days, eps, state.since_day).e_value,
                "contested": contested.get(t, 0),
            }
        )
    return {
        "enabled": bool(policy.teacher_watch),
        "epoch": state.epoch,
        "epoch_started_day": state.since_day,
        "frozen": state.frozen,
        "model": ledger.meta_value(TEACHER_MODEL),
        "noise_max": eps,
        "alpha": policy.alpha_teacher,
        "threshold": 1 / policy.alpha_teacher,
        **summary(n, d),
        "e_value": ev.e_value,
        "strongest": None
        if ev.from_day is None
        else {"from_day": ev.from_day, "checks": ev.checks, "disagreements": ev.disagreements},
        "contested": sum(contested.values()),
        "sources": sources,
        "tasks": tasks_out,
    }


def stats_block(ledger: Ledger, policy: _Policy) -> dict[str, Any]:
    """`/ratchet/stats`' `teacher_watch`: `status` without the per-source and per-task rows."""
    s = status(ledger, policy)
    keys = ("enabled", "epoch", "frozen", "checks", "disagreements", "self_agreement", "ceiling", "e_value", "contested")
    return {k: s[k] for k in keys}


def bom_section(ledger: Ledger, contested_excluded: int) -> dict[str, Any]:
    """The BOM's `teacher` section (`bom.EXTRA_KEYS`): the current epoch, its checks, disagreements and self-agreement
    (recomputable from the two counts), and how many contested labels tonight's sets left out."""
    epoch, n, d = _epoch_counts(ledger)
    return {
        "epoch": epoch,
        "checks": n,
        "disagreements": d,
        "self_agreement": 1 - d / n if n else None,
        "contested_excluded": int(contested_excluded),
    }


# ---------------------------------------------------------------------------------------------------------------------
# Night School's side: probes and adjudication
# ---------------------------------------------------------------------------------------------------------------------
# One deterministic teacher answer to a stored example: (its task, its stored messages, its stored conditions).
Ask = Callable[[tasks.Task, list[dict[str, Any]], dict[str, Any] | None], Completion]


@dataclass(frozen=True)
class Relabel:
    """How Night School re-asks the teacher: `ask` for one answer, the configured teacher `model` it asks (only labels
    that model wrote are re-asked), how many probes and adjudications a night may run (RATCHET_TEACHER_PROBES,
    RATCHET_ADJUDICATE_MAX), whether redaction is on (an answer that would need a placeholder is not used), and the
    teacher's `price`, for the night's event."""

    ask: Ask
    model: str
    probes: int = 20
    adjudicate: int = 50
    redact_pii: bool = True
    price: Price | None = None


def relabel_with(
    provider: Provider,
    model: str,
    policy: GenPolicy,
    probes: int = 20,
    adjudicate: int = 50,
    *,
    redact_pii: bool = True,
    price: Price | None = None,
) -> Relabel:
    """A `Relabel` that asks `provider` for `model`'s answer the way an audit does (`GenPolicy.teacher(..., 'audit')`:
    temperature 0, one answer) under the example's stored conditions (its format, stop and tools), so the answer is
    comparable with the stored label. Nothing is stored or logged by the call itself."""

    def ask(task: tasks.Task, messages: list[dict[str, Any]], stored: dict[str, Any] | None) -> Completion:
        gen = policy.teacher(GenParams.from_stored(stored), "audit")
        out = provider.complete(messages, model, n=1, temperature=0.0, params=gen)
        if not out:
            raise UpstreamError(502, "the teacher returned no choices", code=BAD_RESPONSE)
        return out[0]

    return Relabel(ask, model, probes, adjudicate, redact_pii, price)


def usable_label(task: tasks.Task, c: Completion, redact_pii: bool = True) -> str | None:
    """A night's teacher answer as a label, or None when it could not be stored (INV-16): it did not end by itself, it is
    not valid under the task's spec once cleaned (`tasks.clean_answer`), or, with redaction on, it would need a
    placeholder (a stored label needs none here, so it never has to be redacted before it is stored, INV-12)."""
    if c.finish_reason not in COMPLETE:
        return None
    clean = tasks.clean_answer(task, c.text)
    if task.check(clean) is not None:
        return None
    if redact_pii:
        found: Counter[str] = Counter()
        if redact(clean, found) != clean or found:
            return None
    return clean


def majority(task: tasks.Task, stored: str, answers: list[str]) -> str | None:
    """The label a strict majority of `stored` and the new `answers` (at most two) agree on: `stored` when a new answer
    agrees with it, else a new answer another new answer agrees with, else None (1-1-1, or too few answers)."""
    if any(task.agree(stored, a) for a in answers):
        return stored
    for i, a in enumerate(answers):
        if any(task.agree(a, b) for b in answers[i + 1 :]):
            return a
    return None


@dataclass
class NightResult:
    """What one night's probes and adjudications did."""

    probed: int = 0  # probes asked
    agreed: int = 0
    contested: int = 0  # probes that disagreed (the label is now contested)
    unusable: int = 0  # probe answers that could not be a label (cut off, invalid, needing a placeholder)
    skipped: int = 0  # probes that ran no check (re-tested meanwhile)
    adjudicated: int = 0  # contested labels given two more answers
    kept: int = 0  # ... whose stored label won
    relabelled: int = 0  # ... whose label the two new answers replaced
    still_contested: int = 0  # ... with no strict majority
    usd: float = 0.0
    failure: str | None = None  # the teacher error that stopped the night's watch work (class and status only)

    @property
    def news(self) -> bool:
        """Whether anything disagreed, was adjudicated or failed: the `teacher_watch` event is written only then, so a
        quiet night adds no event."""
        return bool(self.contested or self.adjudicated or self.failure)

    def detail(self) -> str:
        parts = []
        if self.probed:
            parts.append(
                f"probes: {self.probed} stored labels re-asked, {self.agreed} agree, {self.contested} disagree (now "
                f"contested), {self.unusable} unusable answers"
            )
        if self.adjudicated:
            parts.append(
                f"adjudication: {self.adjudicated} contested labels, {self.kept} kept, {self.relabelled} relabelled by a "
                f"2-of-3 majority, {self.still_contested} still contested"
            )
        if self.usd:
            parts.append(f"teacher cost ${self.usd:.4f}")
        if self.failure:
            parts.append(f"stopped after a teacher error: {self.failure}")
        return "; ".join(parts) or "nothing to probe or adjudicate"


def _stored(row: Any) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    return json.loads(row["messages"]), (json.loads(row["params"]) if row["params"] else None)


def night(ledger: Ledger, relabel: Relabel) -> NightResult:
    """One night's watch work, before the dataset is built: probes, then adjudication. Probes (FR-5.40): up to
    `relabel.probes` stored labels not yet re-tested in this epoch (`Ledger.probe_candidates`) are asked again and
    compared, each as that example's first re-test, so traffic with few repeats is checked too. Adjudication (FR-5.41):
    up to `relabel.adjudicate` contested labels get two more answers each, and a strict majority of the three ends the
    quarantine (`majority`; the stored label stays, or the new majority replaces it); without one the label stays
    quarantined as UNRESOLVED, behind the labels never adjudicated (`Ledger.contested_examples`). The first teacher
    error stops both (the teacher is likely down; training goes on). A `teacher_watch` event says what happened when
    anything did."""
    out = NightResult()
    names = eligible_tasks()
    try:
        _probe(ledger, relabel, names, out)
        _adjudicate(ledger, relabel, names, out)
    except Exception as e:  # noqa: BLE001 - optional evidence: a failing teacher must not stop the night
        out.failure = describe_failure(e)
    if out.news:
        ledger.event("teacher_watch", out.detail())
    return out


def _cost(relabel: Relabel, c: Completion) -> float:
    return relabel.price.cost(c.in_tokens, c.out_tokens) if relabel.price is not None else 0.0


def _probe(ledger: Ledger, relabel: Relabel, names: list[str], out: NightResult) -> None:
    epoch = ledger.teacher_state().epoch
    for row in ledger.probe_candidates(epoch, relabel.model, names, relabel.probes):
        task = tasks.lookup(row["task"])
        if task is None:
            continue
        messages, stored = _stored(row)
        c = relabel.ask(task, messages, stored)
        out.probed += 1
        out.usd += _cost(relabel, c)
        label = usable_label(task, c, relabel.redact_pii)
        if label is None:
            out.unusable += 1
            continue
        agree = ledger.record_teacher_check(row["hash"], label, relabel.model, "probe")
        if agree is None:
            out.skipped += 1
        elif agree:
            out.agreed += 1
        else:
            out.contested += 1


def _adjudicate(ledger: Ledger, relabel: Relabel, names: list[str], out: NightResult) -> None:
    for row in ledger.contested_examples(relabel.model, names, relabel.adjudicate):
        task = tasks.lookup(row["task"])
        if task is None or not task.valid(row["completion"]):
            # The spec refuses the stored label now: nothing to keep, and the row stays out of training, behind the
            # labels never adjudicated.
            ledger.mark_unresolved(row["hash"])
            continue
        messages, stored = _stored(row)
        answers = []
        for _ in range(ADJUDICATION_DRAWS):
            c = relabel.ask(task, messages, stored)
            out.usd += _cost(relabel, c)
            label = usable_label(task, c, relabel.redact_pii)
            if label is not None:
                answers.append(label)
        out.adjudicated += 1
        winner = majority(task, row["completion"], answers)
        if winner is None:
            ledger.mark_unresolved(row["hash"])
            out.still_contested += 1
        elif winner == row["completion"]:
            out.kept += int(ledger.resolve_contested(row["hash"]))
        else:
            out.relabelled += int(ledger.resolve_contested(row["hash"], winner))
