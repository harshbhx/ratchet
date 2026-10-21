"""Manual interventions with an audit trail (ADR 0064): roll back, retire or promote a version, turn the student off,
freeze releases, pause training, change the audit rate, pin a task to the teacher.

    python -m ratchet.admin versions                                       # versions, switches and task pins
    python -m ratchet.admin rollback 7 --reason "wrong refunds category"   # a canary back out
    python -m ratchet.admin retire 6 --reason "..."                        # a live, canary or shadow version out
    python -m ratchet.admin promote 5 --to canary [--traffic-pct 10] [--force] [--load-adapter] --reason "..."
    python -m ratchet.admin promote 5 --to live --reason "..."             # retires the live version with it
    python -m ratchet.admin flag                                           # the runtime switches
    python -m ratchet.admin flag student_disabled on --reason "..."        # also releases_frozen, training_paused
    python -m ratchet.admin flag audit_rate 0.2 --reason "..."             # `default`: back to RATCHET_AUDIT_RATE
    python -m ratchet.admin pin ticket-triage --reason "..."               # and `unpin`
    python -m ratchet.admin audit-log [--limit 50] [--json]

The functions here are pure on a `Ledger` and shared by the gateway's `/ratchet/admin/*` routes and this CLI. Each one
checks its reason (3 to 500 characters, redacted before it is stored), checks the move against an explicit table
(`MOVES`), and writes the change, an `admin_actions` row and an `admin` event in one transaction, compare-and-set like
the release gate's own moves, so a change made meanwhile by the cron tick or another admin is a conflict (409, exit 1),
never a lost update. A request that changes nothing (a switch already set) writes nothing. The CLI works on RATCHET_DB
(or --db) with the actor `cli:<OS user>`; a running gateway sees its changes on its next request (PRAGMA data_version).
"""

from __future__ import annotations

import argparse
import getpass
import json
import math
import os
import re
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from types import MappingProxyType
from typing import Any, TextIO

from . import tasks, taskspec
from .config import Settings
from .ledger import BOOL_FLAGS, FLAG_NAMES, IN_FLIGHT_STATES, MAX_ADMIN_ACTIONS, ROUTED_STATES, Ledger, Version
from .redact import redact
from .release import CANARY_STEPS

ACTIONS = ("rollback", "retire", "promote", "set_flag", "pin_task", "unpin_task", "tick")
MIN_REASON, MAX_REASON = 3, 500
MAX_ACTOR = 128
# The moves an admin may make: (from state, to state) -> whether only `force` allows it. Every other move is refused
# with AdminConflict. A version that was rejected, rolled back or retired, or a candidate the golden gate never admitted,
# goes back to canary only with force; nothing becomes live except a canary.
MOVES: Mapping[tuple[str, str], bool] = MappingProxyType(
    {
        ("canary", "rolled_back"): False,  # rollback
        ("canary", "retired"): False,  # retire
        ("canary", "canary"): False,  # promote to another canary stage
        ("canary", "live"): False,  # promote to live; the live version is retired with it
        ("live", "retired"): False,  # take the live version out: the teacher answers everything
        ("shadow", "retired"): False,  # cancel a shadow, so tonight's training can run
        ("shadow", "canary"): False,  # end the shadow early
        ("rejected", "canary"): True,
        ("rolled_back", "canary"): True,
        ("retired", "canary"): True,  # restore an earlier version: canary first, then live
        ("candidate", "canary"): True,  # skip the golden gate's admission and the shadow
    }
)
_ACTOR = re.compile(r"[\w.@:+-]{1,128}")  # a name: letters, digits and . _ @ : + -
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class AdminError(ValueError):
    """A request the admin API refuses: 400 (`status`) for a bad input, and the subclasses below."""

    status = 400


class AdminNotFound(AdminError):
    """No such version or task (404)."""

    status = 404


class AdminConflict(AdminError):
    """The move is not allowed from where the target is now, or it changed meanwhile (409; the CLI exits 1)."""

    status = 409


def check_reason(reason: object) -> str:
    """The reason as stored: one line of 3 to 500 characters (surrounding space stripped), PII redacted, because it lands
    in the release log, which dashboards show."""
    if not isinstance(reason, str):
        raise AdminError(f"reason is required: say why, in {MIN_REASON} to {MAX_REASON} characters")
    text = reason.strip()
    if not MIN_REASON <= len(text) <= MAX_REASON:
        raise AdminError(f"reason must be {MIN_REASON} to {MAX_REASON} characters, not {len(text)}")
    if _CONTROL.search(text):
        raise AdminError("reason must be one line of text, without control characters")
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        raise AdminError("reason must be valid text (it holds an unpaired surrogate)") from None
    return redact(text)


def check_actor(actor: object) -> str:
    """Who acts: `api:<admin key name>` or `cli:<OS user>`. A name, never a key (INV-13)."""
    if not isinstance(actor, str) or not _ACTOR.fullmatch(actor):
        raise AdminError(f"actor must be 1 to {MAX_ACTOR} letters, digits or . _ @ : + -, not {actor!r}")
    return actor


def cli_actor() -> str:
    """`cli:` and the OS user name, with any character a name may not hold dropped (`cli:unknown` when there is none)."""
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001 - no USER variable and no passwd entry (a bare container)
        user = ""
    user = re.sub(r"[^\w.@+-]", "", user)[:64]
    return f"cli:{user or 'unknown'}"


def _state(v: Version) -> dict[str, Any]:
    return {"state": v.state, "traffic_pct": v.traffic_pct}


def _version(led: Ledger, vid: object) -> Version:
    if isinstance(vid, bool) or not isinstance(vid, int):
        raise AdminError(f"a version id is a whole number, not {vid!r}")
    v = led.version(vid)
    if v is None:
        raise AdminNotFound(f"no version {vid}")
    return v


def _in_flight_other(led: Ledger, vid: int) -> Version | None:
    for v in led.versions(IN_FLIGHT_STATES):
        if v.id != vid:
            return v
    return None


def _result(
    action: str, target: str, before: Any, after: Any, action_id: int | None, changed: bool | None = None, **extra: Any
) -> dict[str, Any]:
    """An admin call's answer: what it acted on, its state before and after, and its admin_actions row (None when it
    changed nothing, so nothing was recorded)."""
    changed = action_id is not None if changed is None else changed
    return {"action": action, "target": target, "changed": changed, "before": before, "after": after,
            "action_id": action_id, **extra}  # fmt: skip


def _changed_meanwhile(v: Version) -> AdminConflict:
    return AdminConflict(f"v{v.id} changed while this ran (another admin or the release gate moved it); look again")


# ---------- versions ----------
def rollback(led: Ledger, vid: int, reason: str, actor: str, request_id: str | None = None) -> dict[str, Any]:
    """Roll a canary back: its traffic returns to the live version (or the teacher)."""
    reason, actor = check_reason(reason), check_actor(actor)
    with led.transaction():
        v = _version(led, vid)
        if v.state != "canary":
            hint = "; retire the live version instead" if v.state == "live" else ""
            raise AdminConflict(f"v{v.id} is {v.state}; only a canary is rolled back{hint}")
        if not led.transition(v.id, v.state, v.traffic_pct, "rolled_back", 0):
            raise _changed_meanwhile(v)
        before, after = {f"v{v.id}": _state(v)}, {f"v{v.id}": {"state": "rolled_back", "traffic_pct": 0}}
        detail = f"v{v.id} rolled back from canary {v.traffic_pct}% by {actor}: {reason}"
        aid = led.record_admin(actor, "rollback", f"v{v.id}", reason, before, after, request_id, event=detail)
    return _result("rollback", f"v{v.id}", before, after, aid)


def retire(led: Ledger, vid: int, reason: str, actor: str, request_id: str | None = None) -> dict[str, Any]:
    """Retire a live, canary or shadow version. With the live version retired, the teacher answers every request (and a
    canary, if any, keeps its share) until a version is promoted again."""
    reason, actor = check_reason(reason), check_actor(actor)
    with led.transaction():
        v = _version(led, vid)
        if (v.state, "retired") not in MOVES:
            raise AdminConflict(f"v{v.id} is {v.state}; only a live, canary or shadow version is retired")
        if not led.transition(v.id, v.state, v.traffic_pct, "retired", 0):
            raise _changed_meanwhile(v)
        before, after = {f"v{v.id}": _state(v)}, {f"v{v.id}": {"state": "retired", "traffic_pct": 0}}
        detail = f"v{v.id} retired from {v.state} by {actor}: {reason}"
        aid = led.record_admin(actor, "retire", f"v{v.id}", reason, before, after, request_id, event=detail)
    return _result("retire", f"v{v.id}", before, after, aid)


def _promotion(led: Ledger, v: Version, to: str, traffic_pct: object, force: bool) -> tuple[int, bool]:
    """Check a promotion of `v` against MOVES and the ledger: (the traffic it gets, whether it is a change at all).
    Raises AdminError for a bad input and AdminConflict for a refused move."""
    if to not in ("canary", "live"):
        raise AdminError(f"to must be 'canary' or 'live', not {to!r}")
    if not isinstance(force, bool):
        raise AdminError(f"force must be true or false, not {force!r}")
    if traffic_pct is not None and (isinstance(traffic_pct, bool) or not isinstance(traffic_pct, int)):
        raise AdminError(f"traffic_pct must be a whole number, not {traffic_pct!r}")
    if not isinstance(v.eval.get("threshold"), (int, float)) or isinstance(v.eval.get("threshold"), bool):
        raise AdminConflict(
            f"v{v.id} has no golden eval (no calibrated threshold), so it cannot serve; force does not change that"
        )
    move = (v.state, to)
    if move not in MOVES:
        hint = "; promote it to canary first" if to == "live" else ""
        raise AdminConflict(f"v{v.id} is {v.state}; it cannot become {to}{hint}")
    if MOVES[move] and not force:
        raise AdminConflict(f"v{v.id} is {v.state}; making it a canary again needs force=true (the gate did not pass it)")
    if to == "live":
        if traffic_pct not in (None, 100):
            raise AdminError("a live version takes 100% of traffic; leave traffic_pct out")
        return 100, True
    if traffic_pct is None:
        later = [s for s in CANARY_STEPS if s > v.traffic_pct] if v.state == "canary" else CANARY_STEPS[:1]
        if not later:
            raise AdminConflict(f"v{v.id} is at the last canary stage ({v.traffic_pct}%); promote it to live")
        pct = later[0]
    else:
        pct = traffic_pct
    if not 1 <= pct <= 99:
        raise AdminError(f"a canary takes 1 to 99% of traffic, not {pct}% (promote to live for 100%, retire for 0%)")
    if led.flags().audit_rate == 0 and not force:
        raise AdminConflict(
            "the audit rate is 0: a canary would get no audits, so it could neither advance nor be rolled back; set the "
            "audit rate first, or pass force=true"
        )
    other = _in_flight_other(led, v.id)
    if other is not None:
        raise AdminConflict(f"v{other.id} is in {other.state}; retire or roll it back first (one version in flight at a time)")
    return pct, not (v.state == "canary" and pct == v.traffic_pct)


def promote(
    led: Ledger,
    vid: int,
    to: str,
    traffic_pct: int | None = None,
    force: bool = False,
    *,
    reason: str,
    actor: str,
    request_id: str | None = None,
    load: Callable[[Version], object] | None = None,
) -> dict[str, Any]:
    """Promote a version to a canary stage (`traffic_pct`, default the next stage: 10% from outside canary, 10 -> 50 in
    canary) or to live, which retires the live version in the same transaction. A canary needs audits, so with the
    runtime audit rate at 0 it needs `force`, which also allows the moves MOVES marks. `load` (the gateway's or CLI's
    `live.load_adapter` for `load_adapter`) runs after the checks and before the move, outside the transaction, so a slow
    endpoint never holds the ledger; a failure there changes nothing, and a promotion that changes nothing loads
    nothing."""
    reason, actor = check_reason(reason), check_actor(actor)
    _, change = _promotion(led, _version(led, vid), to, traffic_pct, force)  # refuse before loading anything
    if load is not None and change:
        load(_version(led, vid))
    with led.transaction():
        v = _version(led, vid)
        pct, change = _promotion(led, v, to, traffic_pct, force)
        target = f"v{v.id}"
        if not change:
            return _result("promote", target, {target: _state(v)}, {target: _state(v)}, None)
        forced = " (forced)" if MOVES[(v.state, to)] else ""
        if to == "live":
            live = led.live()
            before = {target: _state(v), **({f"v{live.id}": _state(live)} if live else {})}
            if not led.promote_live(v.id, v.state, v.traffic_pct, old_live=live.id if live else None):
                raise _changed_meanwhile(v)
            after = {target: {"state": "live", "traffic_pct": 100}}
            retired = ""
            if live is not None:
                after[f"v{live.id}"] = {"state": "retired", "traffic_pct": 0}
                retired = f"; v{live.id} retired"
            detail = f"v{v.id} promoted from canary {v.traffic_pct}% to live by {actor}{retired}: {reason}"
        else:
            before = {target: _state(v)}
            if not led.transition(v.id, v.state, v.traffic_pct, "canary", pct):
                raise _changed_meanwhile(v)
            after = {target: {"state": "canary", "traffic_pct": pct}}
            where = f"canary {v.traffic_pct}%" if v.state == "canary" else v.state
            detail = f"v{v.id} promoted from {where} to canary {pct}% by {actor}{forced}: {reason}"
        aid = led.record_admin(actor, "promote", target, reason, before, after, request_id, event=detail)
    return _result("promote", target, before, after, aid, forced=bool(forced))


# ---------- runtime switches ----------
def _flag_value(name: str, value: object) -> bool | float | None:
    if name in BOOL_FLAGS:
        if not isinstance(value, bool):
            raise AdminError(f"{name} must be true or false, not {value!r}")
        return value
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise AdminError(f"{name} must be a number from 0 to 1, or null for the configured rate, not {value!r}")
    return float(value)


def set_flag(
    led: Ledger,
    name: str,
    value: bool | float | None,
    *,
    reason: str,
    actor: str,
    force: bool = False,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Set a runtime switch (`ledger.FLAG_NAMES`): `student_disabled`, `releases_frozen`, `training_paused` (true or
    false) or `audit_rate` (0 to 1, or None for RATCHET_AUDIT_RATE). Every process sharing the ledger applies it on its
    next request, tick or night. An audit rate of 0 while a canary is in flight needs `force`: with no audits the
    canary can neither advance nor be rolled back."""
    reason, actor = check_reason(reason), check_actor(actor)
    if name not in FLAG_NAMES:
        raise AdminError(f"flag must be one of {', '.join(FLAG_NAMES)}, not {name!r}")
    if not isinstance(force, bool):
        raise AdminError(f"force must be true or false, not {force!r}")
    new = _flag_value(name, value)
    with led.transaction():
        old = getattr(led.flags(), name)
        before, after = {"value": old}, {"value": new}
        if old == new:
            return _result("set_flag", name, before, after, None)
        if name == "audit_rate" and new == 0 and not force:
            canary = led.by_state("canary")
            if canary is not None:
                raise AdminConflict(
                    f"v{canary.id} is a canary: with no audits it can neither advance nor be rolled back; "
                    "pass force=true to set the audit rate to 0 anyway"
                )
        led.set_flag(name, new, actor)
        shown = "the configured rate" if new is None else json.dumps(new)
        detail = f"{name} set to {shown} by {actor}: {reason}"
        aid = led.record_admin(actor, "set_flag", name, reason, before, after, request_id, event=detail)
    return _result("set_flag", name, before, after, aid)


# ---------- task pins ----------
def pin_task(led: Ledger, name: str, *, reason: str, actor: str, request_id: str | None = None) -> dict[str, Any]:
    """Pin a registered task (built-in, file or API) to the teacher at runtime: its requests get the teacher alone and
    nothing is stored (INV-10), and Night School leaves its stored rows out from the next night on. Needs the registry
    the gateway has (the CLI installs RATCHET_TASKS_FILE and the stored API tasks first)."""
    reason, actor = check_reason(reason), check_actor(actor)
    if not isinstance(name, str) or not tasks.valid_name(name):
        raise AdminError(f"{name!r} is not a task name (1 to 64 letters, digits, '_', '.' or '-')")
    task = tasks.lookup(name)
    if task is None:
        raise AdminNotFound(
            f"no registered task is named {name!r}; register it first, or set RATCHET_UNKNOWN_TASKS=teacher for "
            "unregistered names"
        )
    if task.source not in tasks.PINNABLE_SOURCES:
        raise AdminConflict(f"task {name!r} is an {task.source} task; only an operator's task can be pinned")
    with led.transaction():
        pinned_now = name in led.flags().pinned or task.pinned
        before = {"pinned": pinned_now, "by_spec": task.pinned}
        if pinned_now:
            return _result("pin_task", name, before, before, None)
        led.set_task_override(name, actor, reason)
        after = {"pinned": True, "by_spec": False}
        detail = f"task {name} pinned to the teacher by {actor}: {reason}"
        aid = led.record_admin(actor, "pin_task", name, reason, before, after, request_id, event=detail)
    led.flags()  # committed: publish the pins to this process's `tasks` now, not on its next request
    return _result("pin_task", name, before, after, aid)


def unpin_task(led: Ledger, name: str, *, reason: str, actor: str, request_id: str | None = None) -> dict[str, Any]:
    """Remove a runtime pin. A task its spec pins stays pinned: change the spec (the tasks file or the tasks API)."""
    reason, actor = check_reason(reason), check_actor(actor)
    if not isinstance(name, str) or not tasks.valid_name(name):
        raise AdminError(f"{name!r} is not a task name (1 to 64 letters, digits, '_', '.' or '-')")
    task = tasks.lookup(name)
    with led.transaction():
        if name not in led.flags().pinned:
            if task is None:
                raise AdminNotFound(f"no registered task is named {name!r}, and none is pinned at runtime")
            if task.pinned:
                raise AdminConflict(f"task {name!r} is pinned by its spec ({task.source}); change the spec to unpin it")
            state = {"pinned": False, "by_spec": False}
            return _result("unpin_task", name, state, state, None)
        before = {"pinned": True, "by_spec": bool(task and task.pinned)}
        led.clear_task_override(name)
        after = {"pinned": bool(task and task.pinned), "by_spec": bool(task and task.pinned)}
        detail = f"task {name} unpinned by {actor}: {reason}"
        aid = led.record_admin(actor, "unpin_task", name, reason, before, after, request_id, event=detail)
    led.flags()  # committed: publish the pins to this process's `tasks` now
    return _result("unpin_task", name, before, after, aid)


# ---------- the release gate ----------
def _release_states(led: Ledger, also: Iterable[str] = ()) -> dict[str, dict[str, Any]]:
    """The routed versions (live, canary, shadow) and the ones named in `also`, by name (`v7`): state and traffic."""
    wanted = set(also)
    return {f"v{v.id}": _state(v) for v in led.versions() if v.state in ROUTED_STATES or f"v{v.id}" in wanted}


def tick(
    led: Ledger,
    run: Callable[[], object],
    actor: str,
    reason: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Run the release gate now (`run`: the gateway's forced tick), as the cron tick does, with an admin_actions row and
    an `admin` event saying who ran it. The gate's own events say what it decided; `before` and `after` are the routed
    versions around it (a version that left them, such as a rolled-back canary, is in both). `reason` is optional here,
    since a tick only applies the gate's own rules."""
    actor = check_actor(actor)
    checked = check_reason(reason) if reason is not None else None
    before = _release_states(led)
    run()
    after = _release_states(led, before)
    detail = f"release gate run by {actor}" + (f": {checked}" if checked else "")
    aid = led.record_admin(actor, "tick", "gate", checked, before, after, request_id, event=detail)
    moved = sorted(k for k in after if before.get(k) != after[k])
    return _result("tick", "gate", before, after, aid, changed=bool(moved), moved=moved)


# ---------- reads ----------
def versions(led: Ledger) -> list[dict[str, Any]]:
    """Every version, newest first, with what an operator decides on: its state and traffic, its golden eval (precision,
    coverage, threshold, rows), its audits in the current stage (live and canary) or its shadow verdicts (shadow)."""
    out = []
    for v in reversed(led.versions()):
        ev = v.eval
        row: dict[str, Any] = {
            "id": v.id,
            "day": v.day,
            "state": v.state,
            "traffic_pct": v.traffic_pct,
            "adapter": v.adapter_uri,
            "train_examples": v.train_examples,
            "golden": {k: ev.get(k) for k in ("precision", "coverage", "threshold", "golden_n")} if ev else None,
        }
        if v.state in ("live", "canary"):
            n, prec = led.version_live_quality(v.id)
            row["stage_audits"] = {"n": n, "precision": prec}
        elif v.state == "shadow":
            row["shadow"] = led.shadow_quality(v.id)
        out.append(row)
    return out


def flags(led: Ledger) -> dict[str, Any]:
    """The runtime switches (with who set each one last, and when) and the task pins (with who and why)."""
    current = led.flags()
    return {
        **current.as_dict(),
        "set_by": led.flag_rows(),
        "task_pins": led.task_overrides(),
    }


def audit_log(led: Ledger, limit: int = 50) -> list[dict[str, Any]]:
    """The newest `limit` admin actions, newest first."""
    try:
        return led.admin_actions(limit)
    except ValueError as e:
        raise AdminError(str(e)) from None


# ---------- CLI ----------
SWITCH_WORDS = {"on": True, "true": True, "1": True, "yes": True, "off": False, "false": False, "0": False, "no": False}


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m ratchet.admin", description="Manual interventions, with an audit trail.")
    ap.add_argument("--db", help="the ledger file (default: RATCHET_DB)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("versions", help="every version, the runtime switches and the task pins")
    v.add_argument("--json", action="store_true")

    def reasoned(p: argparse.ArgumentParser) -> None:
        p.add_argument("--reason", required=True, help=f"why, in {MIN_REASON} to {MAX_REASON} characters")

    for cmd, what in (("rollback", "roll a canary back"), ("retire", "retire a live, canary or shadow version")):
        p = sub.add_parser(cmd, help=what)
        p.add_argument("id", type=int)
        reasoned(p)
    p = sub.add_parser("promote", help="promote a version to a canary stage or to live")
    p.add_argument("id", type=int)
    p.add_argument("--to", choices=("canary", "live"), required=True)
    p.add_argument("--traffic-pct", type=int, help="the canary's share, 1 to 99 (default: the next stage)")
    p.add_argument("--force", action="store_true", help="allow a rejected, rolled-back or retired version or a candidate")
    p.add_argument("--load-adapter", action="store_true", help="load the adapter into the student endpoint first")
    reasoned(p)
    f = sub.add_parser("flag", help="show the runtime switches, or set one")
    f.add_argument("name", nargs="?", choices=FLAG_NAMES)
    f.add_argument("value", nargs="?", help="on|off for a switch; 0 to 1 or 'default' for audit_rate")
    f.add_argument("--reason")
    f.add_argument("--force", action="store_true", help="allow an audit rate of 0 while a canary is in flight")
    f.add_argument("--json", action="store_true")
    for cmd, what in (("pin", "pin a task to the teacher"), ("unpin", "remove a task's runtime pin")):
        p = sub.add_parser(cmd, help=what)
        p.add_argument("task")
        reasoned(p)
    a = sub.add_parser("audit-log", help="the newest admin actions")
    a.add_argument("--limit", type=int, default=50, help=f"1 to {MAX_ADMIN_ACTIONS} (default 50)")
    a.add_argument("--json", action="store_true")
    return ap


def _cli_value(name: str, text: str | None) -> bool | float | None:
    if text is None:
        raise AdminError(f"give a value for {name}: " + ("on or off" if name in BOOL_FLAGS else "0 to 1, or default"))
    word = text.strip().lower()
    if name in BOOL_FLAGS:
        if word not in SWITCH_WORDS:
            raise AdminError(f"{name} takes on or off, not {text!r}")
        return SWITCH_WORDS[word]
    if word == "default":
        return None
    try:
        rate = float(word)
    except ValueError:
        rate = math.nan
    if not 0 <= rate <= 1:  # NaN fails too
        raise AdminError(f"audit_rate takes a number from 0 to 1 or 'default', not {text!r}")
    return rate


def _ledger(path: str, s: Settings) -> Ledger:
    """The live ledger at `path`, which must exist: the CLI never creates an empty ledger where it was not asked to."""
    if path != ":memory:" and not os.path.exists(path):
        raise FileNotFoundError(f"no ledger at {path!r}; pass --db or set RATCHET_DB")
    return Ledger(path, s.golden_fraction, auto_day=True, sync=s.ledger_sync())


def _print(out: TextIO, obj: Any) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str), file=out)


def _render_versions(rows: list[dict[str, Any]], controls: Mapping[str, Any]) -> str:
    lines = [f"{'id':>4}  {'state':<11} {'traffic':>7}  {'precision':>9}  {'coverage':>8}  audits/shadow"]
    for r in rows:
        g = r["golden"] or {}

        def pct(x: Any) -> str:
            return f"{x:.1%}" if isinstance(x, (int, float)) else "-"

        extra = ""
        if "stage_audits" in r:
            extra = f"{r['stage_audits']['n']} audits, {pct(r['stage_audits']['precision'])}"
        elif "shadow" in r:
            extra = f"{r['shadow']['served']} would-serve, {pct(r['shadow']['precision'])}"
        lines.append(
            f"{r['id']:>4}  {r['state']:<11} {r['traffic_pct'] or 0:>6}%  {pct(g.get('precision')):>9}  "
            f"{pct(g.get('coverage')):>8}  {extra}"
        )
    on = [k for k in BOOL_FLAGS if controls.get(k)]
    rate = controls.get("audit_rate")
    lines.append(
        f"switches: {', '.join(on) or 'none on'}; audit rate {'configured' if rate is None else rate}; "
        f"pinned at runtime: {', '.join(controls.get('pinned_tasks') or []) or 'none'}"
    )
    return "\n".join(lines)


def _load_hook(s: Settings, http: Any) -> Callable[[Version], object]:
    from . import live  # the live module pulls in Night School; only a promote that loads an adapter needs it

    if not s.student_base:
        raise AdminError("RATCHET_STUDENT_BASE is not set: no student endpoint to load the adapter into")

    def load(v: Version) -> None:
        live.load_adapter(s.student_base, s.student_api_key, v.adapter_uri, f"{live.adapter_root()}/{v.adapter_uri}", http)

    return load


def main(argv: Sequence[str] | None = None, out: TextIO | None = None, settings: Settings | None = None, http: Any = None) -> int:
    """The CLI. Exit 0 on success, 1 when the move is refused or the version changed meanwhile (AdminConflict), 2 on a
    usage or input error. `http` replaces the client that talks to vLLM for `--load-adapter` (tests)."""
    out = out or sys.stdout
    args = _parser().parse_args(argv)
    try:
        s = settings or Settings()
        led = _ledger(args.db or s.db_path, s)
        actor = cli_actor()
        if args.cmd in ("pin", "unpin"):  # the gateway's registry: the tasks file and the stored API tasks
            for line in taskspec.install(led, s.tasks_file):
                print(f"task skipped: {line}", file=sys.stderr)
        if args.cmd == "versions":
            rows, controls = versions(led), led.flags().as_dict()
            if args.json:
                _print(out, {"versions": rows, "controls": controls})
            else:
                print(_render_versions(rows, controls), file=out)
            return 0
        if args.cmd == "audit-log":
            rows = audit_log(led, args.limit)
            if args.json:
                _print(out, rows)
            else:
                for r in rows:
                    print(f"#{r['id']} {r['actor']} {r['action']} {r['target']}: {r['reason']}", file=out)
            return 0
        if args.cmd == "flag" and args.name is None:
            _print(out, flags(led))
            return 0
        if args.cmd == "rollback":
            res = rollback(led, args.id, args.reason, actor)
        elif args.cmd == "retire":
            res = retire(led, args.id, args.reason, actor)
        elif args.cmd == "promote":
            hook = _load_hook(s, http) if args.load_adapter else None
            res = promote(led, args.id, args.to, args.traffic_pct, args.force, reason=args.reason, actor=actor, load=hook)
        elif args.cmd == "flag":
            res = set_flag(led, args.name, _cli_value(args.name, args.value), reason=args.reason, actor=actor, force=args.force)
        elif args.cmd == "pin":
            res = pin_task(led, args.task, reason=args.reason, actor=actor)
        else:
            res = unpin_task(led, args.task, reason=args.reason, actor=actor)
    except AdminConflict as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    except (AdminError, ValueError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    _print(out, res)
    if not res["changed"]:
        print("nothing changed", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
