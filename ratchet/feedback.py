"""Ground truth from people: verdicts on served answers, corrections and imported labels (ADR 0091).

Three ways a person's judgement reaches Ratchet, none of which lets the student's own text become a training target
(invariant 2):

- A verdict (`submit`, `POST /v1/ratchet/feedback`): 'correct' or 'incorrect' on an answer the gateway served, named by
  the request id the response carried (`ratchet.request_id`, `req_` + 32 hex; the completion id `chatcmpl-` + the same
  digits works too). An API key may give verdicts on its own requests only. Verdicts feed each task's
  `human_agreement` and each version's human precision on the answers it served itself, which is reported and alerted
  on (`check_human_alert`) but never used to promote or roll back: a client's verdicts are biased toward what it notices.
- A correction: an 'incorrect' verdict with the right answer and the request's messages (request rows hold no text).
  It is stored as an example with provenance `human` (`licenses.SOURCES`), `verified` = 1, cleaned, checked against
  the task's spec and redacted exactly as a teacher answer is, in the split its hash gives (invariant 3). A 'correct'
  verdict never stores anything: the gateway never kept the student's text, and a verdict is not a label.
- An import (`ratchet.importer`): labelled rows from a file, as human labels under an attestation or as an open
  teacher's outputs (`licenses.require_distillable`), optionally held out for good (`golden_only`).

Every human or imported label goes through `store_label`, the one writer besides the router (the INV-2 allowlist in
tests/test_nightschool.py). A human label beats a teacher's on the same example hash; an operator's import beats a
client's correction; otherwise the first label stands (`store_label`).
"""

from __future__ import annotations

import math
import re
import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from . import licenses, privacy, tasks
from .config import Settings
from .ledger import STORED_PARAM_KEYS, Ledger, example_hash, target_params
from .nightschool.dataset import FREQUENT
from .providers import GenParams, GenPolicy
from .redact import redact, redact_json, redact_messages
from .router import Ratchet, RequestMeta
from .stats import log_e_bernoulli

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .release import ReleasePolicy

VERDICTS = ("correct", "incorrect")
REQUEST_PREFIX = "req_"
COMPLETION_PREFIX = "chatcmpl-"
# A request id as feedback takes it: the server's `req_<hex>`, or its completion's `chatcmpl-<hex>`.
_REQUEST_ID = re.compile(r"(?:req_|chatcmpl-)([0-9a-f]{32})")
FEEDBACK_ORIGIN, IMPORT_ORIGIN = "feedback", "import"  # examples.origin (ledger.LABEL_ORIGINS)
HUMAN_ALERT = "human_alert"  # the event kind `check_human_alert` writes
ALERT_META = "human_alert:v{}"  # meta row: the version was alerted on, once
# The human alert's level: the mixture e-value against 'human precision >= live_floor' must reach 1 / ALERT_ALPHA. The
# check runs after every verdict, so it needs an anytime-valid rule (ADR 0025); 0.025 matches the one-sided level of
# the 95% Wilson bound reported beside it.
ALERT_ALPHA = 0.025
# Why a correction or an imported row was not stored. The storage opt-outs (`privacy.OPT_OUTS`) come first, then:
# corrections switched off (RATCHET_FEEDBACK_CORRECTIONS=0), a pinned task (invariant 10), a request the student was never
# meant to learn (`unknown_task`, `teacher_only`), a label redaction broke, an equal or stronger human label already
# stored, an erased example (FR-13.3), and a golden-only row whose prompt may already have been trained on.
NOT_STORED = (
    "global",
    "task",
    "key",
    "request",
    "end_user",
    "corrections_off",
    "pinned",
    "unknown_task",
    "teacher_only",
    "invalid after redaction",
    "human_label_exists",
    "erased",
    "trained",
)


def parse_request_id(value: object) -> str | None:
    """The request id `value` names (`req_<hex>`, from `req_<hex>` or `chatcmpl-<hex>`), or None for anything else."""
    if not isinstance(value, str):
        return None
    m = _REQUEST_ID.fullmatch(value)
    return REQUEST_PREFIX + m.group(1) if m else None


# ---------------------------------------------------------------------------------------------------------------------
# Labels


class LabelError(ValueError):
    """A label the task's spec refuses (`Task.check`); the message is the reason (`empty answer`, `invalid field
    category`), which names a path of the spec, never the text."""


@dataclass(frozen=True)
class Prepared:
    """A label ready to store: cleaned (`tasks.clean_answer`), valid under the task's spec, and redacted when redaction
    is on, with its example hash and its prompt key (the hash without conditions, `ledger.prompt_key`)."""

    task: tasks.Task
    messages: list[dict[str, Any]]
    completion: str
    params: dict[str, Any] | None
    redactions: int
    hash: str
    prompt: str


@dataclass(frozen=True)
class Stored:
    """What `store_label` did. `stored` False comes with `reason` (one of NOT_STORED). `inserted`: a new example;
    `replaced`: whose label this one replaced ('teacher' or 'human'); neither: an existing example kept its label (a
    duplicate, not counted as stored)."""

    stored: bool
    reason: str | None = None
    hash: str | None = None
    split: str | None = None
    inserted: bool = False
    replaced: str | None = None


def conditions(policy: GenPolicy, params: GenParams | None) -> dict[str, Any] | None:
    """The conditions a teacher answer of a request with these parameters is stored under (`ledger.target_params` of
    the serving teacher's parameters, `GenPolicy.teacher`), so a label for the same request has the same example hash
    as the teacher's (`Ratchet._store_example` does the same)."""
    if params is None:
        return None
    serve = policy.teacher(params, "serve")
    return target_params({k: getattr(serve, k) for k in STORED_PARAM_KEYS})


def prepare(
    task: tasks.Task,
    messages: list[dict[str, Any]],
    completion: str,
    *,
    params: dict[str, Any] | None = None,
    redact_pii: bool = True,
) -> Prepared | str:
    """A label as it would be stored: `completion` cleaned the way an answer is served, checked against the task's spec
    (LabelError when it is not valid: empty, does not parse, a required field missing or a label outside its
    vocabulary), then with `redact_pii` the messages, the label and the conditions redacted (invariant 12). Returns the
    reason 'invalid after redaction' when a placeholder breaks a structured label, as the router skips such an answer.
    `messages` are already validated and normalised as the chat route does it (`openai_compat.normalize_messages`);
    `params` are the stored conditions (`conditions`). String work only, so callers run it outside the ledger's lock."""
    clean = tasks.clean_answer(task, completion)
    problem = task.check(clean)
    if problem is not None:
        raise LabelError(problem)
    stored, cond, found = messages, target_params(params), Counter()
    if redact_pii:
        stored, clean = redact_messages(messages, found), redact(clean, found)
        if cond:
            cond = redact_json(cond, found)
        if found and task.check(clean) is not None:
            return "invalid after redaction"
    return Prepared(
        task, stored, clean, cond, sum(found.values()), example_hash(task.name, stored, cond), example_hash(task.name, stored)
    )


def _rank(row: Any) -> int:
    """How strong a stored label is: a teacher's 0, a client's correction 1, an operator's import of human labels 2."""
    if row["teacher_model"] not in licenses.SOURCES:
        return 0
    return 2 if row["origin"] == IMPORT_ORIGIN else 1


def held_out_ok(ledger: Ledger, prepared: Prepared) -> bool:
    """Whether a golden-only label may hold its prompt out for good: no stored variant of the prompt is in the train
    split and the prompt was not seen FREQUENT times (when it was, its variants are training rows already), as the
    ledger holds it now (`Ledger.prompt_status`; `store_label` asks inside the transaction that writes the label). A
    prompt that may already have been trained on is no held-out evidence (invariant 6)."""
    seen, in_train = ledger.prompt_status(prepared.task.name, prepared.prompt)
    return not in_train and seen < FREQUENT


def store_label(
    ledger: Ledger,
    prepared: Prepared,
    *,
    source: str,
    settings: Settings,
    origin: str,
    client: str | None = None,
    end_user: str | None = None,
    golden_only: bool = False,
    bump: bool = True,
    replace_same_rank: bool = False,
) -> Stored:
    """Store one human or imported label: the only caller of `Ledger.add_example` besides the router (invariant 2).

    `source` is `licenses.HUMAN` (a label a person wrote: `verified` = 1) or an open teacher's model id, which must
    pass `licenses.require_distillable` (LicenseError otherwise; invariant 1). Refused, with a reason: a pinned task
    (invariant 10), zero retention and a no-store task (FR-13.1; the caller checks the request's own opt-outs), an erased
    example (FR-13.3), and for `golden_only` a prompt that may already have been trained on (`held_out_ok`, read in the
    same transaction as the write). The split is the hash's (invariant 3) unless `golden_only`.

    On an existing example a human label replaces the label when it is stronger (`_rank`: teacher < correction < import),
    or as strong and `replace_same_rank` (the same request's correction again, an operator's later import). Anything else
    keeps the stored label: a teacher answer never replaces a human one, and one client's correction never replaces
    another's. `bump` False is for a label that is no occurrence of its request (an import, or a correction of a request
    already counted): it leaves a stored example's `seen` alone and inserts a new one with `seen` 0, so it never counts
    toward FREQUENT (invariant 3)."""
    task = prepared.task
    if task.pinned:  # invariant 10: never a training example, whatever writes it
        return Stored(False, "pinned")
    opt_out = privacy.store_opt_out(settings, task)  # zero retention, a no-store task (FR-13.1)
    if opt_out is not None:
        return Stored(False, opt_out)
    human = source == licenses.HUMAN
    lic = licenses.SOURCES[source] if human else licenses.require_distillable(source)
    new_rank = (2 if origin == IMPORT_ORIGIN else 1) if human else 0
    with ledger.atomic():  # the decision and the write see the same row, also against another process's writes
        if golden_only and not held_out_ok(ledger, prepared):
            return Stored(False, "trained", prepared.hash)
        existing = ledger.example(prepared.hash)
        alive = existing is not None and existing["purged_ts"] is None
        restored = existing is not None and existing["purge_reason"] == "retention"  # add_example puts this label back
        old_rank = _rank(existing) if alive else -1
        replace = alive and human and (new_rank > old_rank or (new_rank == old_rank and replace_same_rank))
        added = ledger.add_example(
            task.name,
            prepared.messages,
            prepared.completion,
            source,
            lic.name,
            lic.url,
            redactions=prepared.redactions,
            params=prepared.params,
            client=client,
            end_user=end_user,
            verified=human,
            golden_only=golden_only,
            origin=origin,
            replace=replace,
            bump=bump,
        )
        if added is None:  # an erasure tombstoned this example: never stored again
            return Stored(False, "erased", prepared.hash)
        row = ledger.example(prepared.hash)
    split = row["split"] if row is not None else None
    if added or restored:
        return Stored(True, None, prepared.hash, split, inserted=True)
    if replace:
        return Stored(True, None, prepared.hash, split, replaced="human" if old_rank > 0 else "teacher")
    if human and old_rank > 0:
        return Stored(False, "human_label_exists", prepared.hash, split)
    return Stored(False, None, prepared.hash, split)  # a duplicate: the stored label stands


def store_human_example(
    ledger: Ledger,
    task: tasks.Task,
    messages: list[dict[str, Any]],
    corrected: str,
    client: str | None,
    settings: Settings,
    *,
    params: dict[str, Any] | None = None,
    end_user: str | None = None,
    bump: bool = True,
    replace_same_rank: bool = False,
) -> Stored:
    """A client's correction as a human-labelled example (`prepare`, then `store_label` with source `human`, origin
    `feedback`). LabelError when the task's spec refuses `corrected`."""
    prepared = prepare(task, messages, corrected, params=params, redact_pii=settings.redact_pii)
    if isinstance(prepared, str):
        return Stored(False, prepared)
    return store_label(
        ledger,
        prepared,
        source=licenses.HUMAN,
        settings=settings,
        origin=FEEDBACK_ORIGIN,
        client=client,
        end_user=end_user,
        bump=bump,
        replace_same_rank=replace_same_rank,
    )


# ---------------------------------------------------------------------------------------------------------------------
# Verdicts


class FeedbackNotFound(LookupError):
    """No request with that id for this caller: never logged, folded by retention, or another API key's (the same
    answer for all three, so the endpoint tells nobody whether another key's request exists)."""


class FeedbackConflict(Exception):
    """The verdict contradicts what is stored for the request: `code` names how."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class FeedbackError(ValueError):
    """A mistake in the feedback itself: `code` and the parameter it is about (`param`)."""

    def __init__(self, code: str, message: str, param: str | None = None):
        super().__init__(message)
        self.code = code
        self.param = param


@dataclass(frozen=True)
class Correction:
    """The right answer to a request, with what the request was: its messages, validated and normalised as the chat
    route does it, the conditions it carried (`conditions`), and the end-user identifiers it named (`user`,
    `safety_identifier`; `privacy.end_user_ids`, the attributed one first), only ever hashed."""

    messages: list[dict[str, Any]]
    text: str
    params: dict[str, Any] | None = None
    end_user_ids: Sequence[str] = ()


END_USER_REQUIRED = (
    "This request named an end user: send every end-user identifier it sent ('user', 'safety_identifier', or both when "
    "it sent both) with the correction, so that end user's storage opt-outs apply."
)


def _end_user_check(row: Any, ids: Sequence[str], client: str | None, end_users: privacy.EndUsers | None) -> tuple[str, ...]:
    """The hashes a correction's no-train lookup uses beyond the request's own end user. A request that named an end
    user needs every identifier it carried again (FeedbackError otherwise): the attributed one's hash must match the
    row's `end_user`, and a second one (`user` beside `safety_identifier`) the row's `end_user_secondary`. Only with the
    values can a registration made with either identifier, or an admin's registration for every client (the
    every-client scope), be found, so an opted-out end user's prompt is never stored through a correction. Identifiers
    on a request that named none only add lookups. A row logged before `end_user_secondary` existed recorded only the
    attributed identifier, so only that one is required there."""
    recorded, secondary = row["end_user"], row["end_user_secondary"]
    if not ids or (secondary is not None and len(ids) < 2):
        if recorded is not None:
            raise FeedbackError("end_user_required", END_USER_REQUIRED, "user")
        return ()
    if end_users is None:
        raise FeedbackError("end_user_unsupported", "End-user identifiers cannot be checked here.", "user")
    attributed = end_users.hash(ids[0], client)
    if (recorded is not None and attributed != recorded) or (
        secondary is not None and end_users.hash(ids[1], client) != secondary
    ):
        raise FeedbackError(
            "end_user_mismatch", "'user' or 'safety_identifier' does not match the end user this request named.", "user"
        )
    return (attributed, *end_users.aliases(list(ids), client))


def _policy_reason(ratchet: Ratchet, task: tasks.Task, row: Any, aliases: tuple[str, ...], key_store: bool) -> str | None:
    """Why a correction of this request may not be stored, broadest first, or None: corrections off, a pinned task, a
    request the student was never meant to learn (an unregistered task under RATCHET_UNKNOWN_TASKS teacher or reject,
    or a teacher-only request such as one with an image part), the opt-out the request itself was under, and the
    opt-outs in force now (`Ratchet.store_opt_out`: zero retention, a no-store task, the key's `store=0`, an end user
    registered as no-train under any of its hashes)."""
    if not ratchet.s.feedback_corrections:
        return "corrections_off"
    if task.pinned:
        return "pinned"
    if row["route"] == "unknown_task" or (task.source == "adhoc" and ratchet.unknown_tasks != "distill"):
        return "unknown_task"
    if row["route"] == "pinned":  # the teacher alone answered a task that is not pinned: a part only it reads
        return "teacher_only"
    if row["store_opt_out"]:
        return str(row["store_opt_out"])
    meta = RequestMeta(
        storable=key_store,
        store_opt_out=None if key_store else "key",
        end_user=row["end_user"],
        end_user_aliases=aliases,
    )
    return ratchet.store_opt_out(task, meta)


def submit(
    ledger: Ledger,
    ratchet: Ratchet,
    request_id: str,
    client: str | None,
    verdict: str,
    correction: Correction | None = None,
    *,
    end_users: privacy.EndUsers | None = None,
    key_store: bool = True,
    policy: ReleasePolicy | None = None,
) -> dict[str, Any]:
    """Record a verdict on one of `client`'s requests (`request_id` as `parse_request_id` returns it) and store its
    correction when there is one and it may be stored. Raises FeedbackNotFound (another key's request is not found
    either), FeedbackError for a mistake in the feedback (a correction with a 'correct' verdict, one the task's spec
    refuses, end-user identifiers that do not match the request's) and FeedbackConflict for a verdict that contradicts
    a stored correction ('correct' after one, or a second correction of other messages). Nothing is recorded then.

    One row per request: a later verdict replaces the earlier one. A correction is stored at most once per request; the
    same request's correction of the same messages and conditions may replace it (a fix). A policy that keeps the
    correction out (`NOT_STORED`) still records the verdict, and the answer says why. After a verdict on an answer a
    version served itself, `check_human_alert` may write a `human_alert` event; nothing else reads human precision."""
    if verdict not in VERDICTS:
        raise FeedbackError("invalid_value", f"'verdict' must be one of {list(VERDICTS)}.", "verdict")
    if correction is not None and verdict != "incorrect":
        raise FeedbackError("invalid_value", "A correction goes with the verdict 'incorrect' only.", "corrected")
    row = ledger.feedback_request(request_id)
    if row is None or row["client"] != client:
        raise FeedbackNotFound(request_id)
    task = tasks.get(row["task"])
    prepared: Prepared | str | None = None
    aliases: tuple[str, ...] = ()
    if correction is not None:
        try:  # string work, outside the ledger's lock
            prepared = prepare(
                task, correction.messages, correction.text, params=correction.params, redact_pii=ratchet.s.redact_pii
            )
        except LabelError as e:
            raise FeedbackError(
                "invalid_correction", f"The correction is not a valid answer for task {task.name!r}: {e}.", "corrected"
            ) from None
        aliases = _end_user_check(row, correction.end_user_ids, client, end_users)
    outcome: Stored | None = None
    with ledger.locked():  # one verdict at a time per ledger: the checks and the writes see the same rows
        prior = ledger.feedback_row(request_id)
        stored_before = prior is not None and bool(prior["stored"])
        if stored_before and verdict == "correct":
            raise FeedbackConflict(
                "correction_exists",
                "This request already has a stored correction, so its answer was not correct; erase the example to withdraw it.",
            )
        if isinstance(prepared, Prepared) and stored_before and prior["example_hash"] != prepared.hash:
            raise FeedbackConflict(
                "correction_exists", "This request already has a stored correction of other messages or conditions."
            )
        if correction is not None:
            reason = _policy_reason(ratchet, task, row, aliases, key_store)
            if reason is None and isinstance(prepared, str):
                reason = prepared  # 'invalid after redaction'
            if reason is not None:
                outcome = Stored(False, reason)
            else:
                assert isinstance(prepared, Prepared)
                outcome = store_label(
                    ledger,
                    prepared,
                    source=licenses.HUMAN,
                    settings=ratchet.s,
                    origin=FEEDBACK_ORIGIN,
                    client=client,
                    end_user=row["end_user"],
                    # A request counts once in `seen`: not again when its teacher answer or an earlier correction was stored.
                    bump=not row["stored"] and not stored_before,
                    replace_same_rank=stored_before,
                )
        updated = ledger.record_feedback(
            request_id,
            client,
            verdict,
            row,
            corrected=correction is not None,
            stored_hash=outcome.hash if outcome is not None and outcome.stored else None,
        )
    if policy is not None and row["served_by"] == "student" and row["route"] == "student" and row["student_version"]:
        check_human_alert(ledger, int(row["student_version"]), policy)
    return {
        "object": "ratchet.feedback",
        "request_id": request_id,
        "verdict": verdict,
        "updated": updated,
        "correction": None
        if outcome is None
        else {"stored": outcome.stored, "not_stored": outcome.reason, "split": outcome.split, "replaced": outcome.replaced},
    }


def check_human_alert(ledger: Ledger, version: int, policy: ReleasePolicy) -> bool:
    """Write one `human_alert` event for `version` when its human precision on the answers it served itself has at least
    `policy.min_audits` verdicts and anytime-valid evidence that it is below `policy.live_floor`: the mixture e-value
    against 'precision >= live_floor' (`stats.log_e_bernoulli`, side 'below') reaches 1 / ALERT_ALPHA. The check runs
    after every verdict, and a fixed-sample bound (Wilson) checked that often would alarm far more often than its level;
    the e-value keeps the false-alarm rate under ALERT_ALPHA however often it is looked at (Ville's inequality, ADR
    0025). Once per version (a meta row, so a restart does not repeat it). Returns True when this call wrote it. Human
    precision never promotes or rolls back a version: clients report what they notice, so the sample is biased; the
    audited precision stays the release signal (ADR 0091)."""
    n, ok = ledger.human_precision(version)
    if n < policy.min_audits or not 0 < policy.live_floor < 1:
        return False
    log_e = log_e_bernoulli(ok, n, policy.live_floor, "below")
    if log_e < math.log(1 / ALERT_ALPHA):
        return False
    token = uuid.uuid4().hex
    if ledger.meta_setdefault(ALERT_META.format(version), token) != token:
        return False  # alerted before, here or in another process
    ledger.event(
        HUMAN_ALERT,
        f"v{version} human precision {ok / n:.1%} (n={n}) on answers it served is below {policy.live_floor:.0%} (e-value "
        f"{math.exp(min(log_e, 50.0)):.3g} >= {1 / ALERT_ALPHA:.0f}, anytime-valid); feedback is never used to promote or "
        "roll back: check audits and the runbook",
    )
    return True
