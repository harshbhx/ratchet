"""Storage opt-outs and pseudonymous end users (FR-13.1, FR-13.2, ADR 0080).

A request's teacher answers are training data unless something opts it out. The opt-outs, broadest first
(`OPT_OUTS`, recorded per request in `requests.store_opt_out`):

- `global`: RATCHET_STORE_EXAMPLES=0, zero retention;
- `task`: the task's spec says `store: false`, or RATCHET_NO_STORE_TASKS names it;
- `key`: the API key has `store=0` in RATCHET_KEY_LIMITS;
- `request`: the body's `store: false`, `X-Ratchet-Store: none` (or `0`, `false`), or `metadata.ratchet_store` "false"
  or "0";
- `end_user`: the end user is registered as no-train (`POST /v1/ratchet/no-train`).

An absent `store` means store (OpenAI's own default for chat completions is not to store; Ratchet exists to distill, so
its default is the other way, and the docs say so). An opt-out only ever removes storage: nothing in a request can make
an opted-out answer stored (docs/lanes/e-invariants.md).

End users are attributed by HMAC only. `safety_identifier` (else `user`) is hashed with a per-deployment key, scoped to
the API key name that sent it, and cut to 16 hex digits (`EndUsers.hash`). The raw value is never stored or logged; the
same person seen by two deployments, or by two API clients of one deployment, gets unrelated hashes. A no-train
registration made with either value applies (`end_user_ids`, `EndUsers.aliases`), and an admin's registration for every
client is one hash in the `EVERY_CLIENT` scope, so it covers API keys added later. Registrations scoped to a key name
that is no longer configured match nothing; `check_no_train_scopes` says so at startup.

What was stored can be removed (FR-13.3 to FR-13.5, ADR 0081):

- `erase` purges the examples of a client, an end user, a list of hashes or a piece of stored text for good: the text
  goes, the hash stays as a tombstone that is never stored again, and the erasure is logged with hashes only, with the
  versions that may have been trained on them (reported, never edited; signed BOMs stay as they are, INV-14).
- `apply_retention` purges examples not seen for RATCHET_EXAMPLE_RETENTION_DAYS (a storable duplicate brings the text
  back) and folds request rows older than RATCHET_REQUEST_RETENTION_DAYS into daily sums.
- `export_lines` streams a table as NDJSON, examples with their provenance.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import re
import secrets
import time
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from typing import Any

from .auth import valid_name
from .config import ERASURE_SHARED, MAX_RETENTION_DAYS, Retention
from .ledger import DAY_SECONDS, EXPORT_FILTERS, EXPORT_KINDS, PURGE_CHUNK, Ledger
from .nightschool import dataset
from .openai_compat import STORE_METADATA
from .redact import redact

# Why a request's teacher answers could not be stored, broadest first (`requests.store_opt_out`).
OPT_OUTS = ("global", "task", "key", "request", "end_user")
# `X-Ratchet-Store` values, in any case: the first set opts the request out, the second is the default spelled out.
STORE_HEADER_OFF = frozenset({"none", "0", "false"})
STORE_HEADER_ON = frozenset({"1", "true"})

# The scope of an admin's no-train registration for every client, now and later: never a key name (`auth.valid_name`).
EVERY_CLIENT = "*"
END_USER_HASH_HEX = 16  # 64 bits: unique enough for any one deployment's end users, short enough to index
MIN_KEY_CHARS = 16  # RATCHET_END_USER_KEY
KEY_META = "end_user_key"  # meta row holding the generated key (hex), when RATCHET_END_USER_KEY is not set
KEY_ID_META = "end_user_key_id"  # meta row holding the id of the key last used, to notice a change


def store_header(value: str | None) -> bool | None:
    """`X-Ratchet-Store` as False (opt out), True (store, the default) or None (not sent, or sent empty, which some
    clients do for a header they leave unset). ValueError for any other value, so a typo cannot quietly store what the
    client meant to keep out."""
    if value is None or not value.strip():
        return None
    word = value.strip().lower()
    if word in STORE_HEADER_OFF:
        return False
    if word in STORE_HEADER_ON:
        return True
    raise ValueError("expected none, 0 or false (do not store), or 1 or true")


def request_opts_out(store: bool | None, metadata: Mapping[str, str] | None, header: bool | None) -> bool:
    """Whether the request itself asks not to be stored: `store: false`, `X-Ratchet-Store: none` or
    `metadata.ratchet_store` "false"/"0". Any one is enough; none of them can undo another opt-out."""
    meta = (metadata or {}).get("ratchet_store")
    return store is False or header is False or (meta is not None and STORE_METADATA.get(meta.strip().lower()) is False)


def end_user_of(user: str | None, safety_identifier: str | None) -> str | None:
    """The request's end user: `safety_identifier`, else `user` (OpenAI's newer field wins). An empty string is no end
    user. The value is only ever hashed (`EndUsers.hash`), whole, whatever its length."""
    ids = end_user_ids(user, safety_identifier)
    return ids[0] if ids else None


def end_user_ids(user: str | None, safety_identifier: str | None) -> list[str]:
    """Every end-user identifier the request carries, the one it is attributed to first (`end_user_of`). A no-train
    registration made with any of them applies, so a client that starts sending `safety_identifier` beside `user`
    (OpenAI's advice) keeps the opt-outs it registered by `user`."""
    ids = [v for v in (safety_identifier, user) if v]
    return ids[:1] if len(ids) == 2 and ids[0] == ids[1] else ids


def _key_id(key: bytes) -> str:
    """A name for the key that says nothing about it: an HMAC of a fixed label under the key."""
    return hmac.new(key, b"ratchet end-user key id", hashlib.sha256).hexdigest()[:16]


class EndUsers:
    """Hashes end-user identifiers with one deployment's key. `source` is `env` (RATCHET_END_USER_KEY) or `ledger` (32
    random bytes generated once and kept in the ledger's meta table, so hashes survive restarts)."""

    def __init__(self, key: bytes, source: str):
        if len(key) < MIN_KEY_CHARS:
            raise ValueError(f"the end-user key must be at least {MIN_KEY_CHARS} bytes")
        self._key = key
        self.source = source
        self.key_id = _key_id(key)

    def hash(self, value: str, client: str | None = None) -> str:
        """HMAC-SHA256 of `value` under the deployment's key, scoped to `client` (the API key name, None without one),
        as 16 hex digits. The scope keeps one client's end users apart from another's: the same identifier sent by two
        clients is two people as far as the ledger can tell, and one client cannot opt out (or, later, erase) another's
        users. Key names never contain a NUL, so the message is unambiguous."""
        message = f"{client or ''}\x00{value}".encode("utf-8", "surrogatepass")
        return hmac.new(self._key, message, hashlib.sha256).hexdigest()[:END_USER_HASH_HEX]

    def hashes(self, value: str, clients: Iterable[str | None]) -> list[tuple[str, str | None]]:
        """(hash, client) of `value` in each client's scope: what an admin removes (or erases) for every client."""
        return [(self.hash(value, c), c) for c in clients]

    def aliases(self, ids: Sequence[str], client: str | None) -> tuple[str, ...]:
        """The other hashes a no-train registration of this request's end user may be stored under, beyond the attributed
        one (`hash(ids[0], client)`): each further identifier in `client`'s scope, and every identifier in the
        `EVERY_CLIENT` scope (an admin's registration for every client, also those added after it). Looked up, never
        stored (`RequestMeta.end_user_aliases`)."""
        return tuple([self.hash(v, client) for v in ids[1:]] + [self.hash(v, EVERY_CLIENT) for v in ids])


def end_users(ledger: Ledger, configured: str = "") -> EndUsers:
    """The deployment's `EndUsers`: RATCHET_END_USER_KEY when set (at least 16 characters; ValueError otherwise, so a
    short key stops startup), else the ledger's generated key, created on first use. When the key differs from the one
    the ledger last saw, an `end_user_key` event says how many no-train registrations stopped matching: their hashes
    were made with the old key, so those end users' requests would be stored again until they register again."""
    if not isinstance(configured, str):
        raise ValueError("RATCHET_END_USER_KEY must be a string")
    if configured:
        if len(configured) < MIN_KEY_CHARS:
            raise ValueError(f"RATCHET_END_USER_KEY must be at least {MIN_KEY_CHARS} characters")
        out = EndUsers(configured.encode("utf-8"), "env")
    else:
        stored = ledger.meta_setdefault(KEY_META, secrets.token_hex(32))
        try:
            key = bytes.fromhex(stored)
        except ValueError:
            message = f"the ledger's {KEY_META} is not hex; restore it from a backup or set RATCHET_END_USER_KEY"
            raise ValueError(message) from None
        out = EndUsers(key, "ledger")
    previous = ledger.meta_get(KEY_ID_META)
    if previous != out.key_id:
        if previous is not None:
            n = ledger.no_train_count()
            ledger.event(
                "end_user_key",
                f"the end-user key changed (key id {previous} -> {out.key_id}): {n} no-train registrations and earlier "
                "end-user hashes no longer match",
            )
        ledger.meta_set(KEY_ID_META, out.key_id)
    return out


def recorded_end_users(ledger: Ledger, configured: str = "") -> EndUsers:
    """The gateway's `EndUsers` for a command run beside it (`python -m ratchet.admin erase --end-user`), which never
    writes: RATCHET_END_USER_KEY when this process has it, else the ledger's generated key, and only when its id is the
    one the ledger last hashed with (`KEY_ID_META`). ValueError otherwise: a shell without the gateway's key would hash
    with another one, match nothing and record an erasure that looks done, and `end_users` would also store a new key
    and log a key change that is not one."""
    if not isinstance(configured, str):
        raise ValueError("RATCHET_END_USER_KEY must be a string")
    recorded = ledger.meta_get(KEY_ID_META)
    if recorded is None:
        raise ValueError("this ledger has never hashed an end user (no end-user key id in its meta table): nothing to find")
    if configured:
        if len(configured) < MIN_KEY_CHARS:
            raise ValueError(f"RATCHET_END_USER_KEY must be at least {MIN_KEY_CHARS} characters")
        out = EndUsers(configured.encode("utf-8"), "env")
    else:
        stored = ledger.meta_get(KEY_META)
        if stored is None:
            raise ValueError(
                f"RATCHET_END_USER_KEY is not set here, but the gateway hashes end users with a key of its own (key id "
                f"{recorded}): run this with the gateway's environment"
            )
        try:
            out = EndUsers(bytes.fromhex(stored), "ledger")
        except ValueError:
            raise ValueError(f"the ledger's {KEY_META} is not a valid key; restore it from a backup") from None
    if out.key_id != recorded:
        where = "RATCHET_END_USER_KEY here" if configured else f"the ledger's {KEY_META}"
        fix = "run this with the gateway's RATCHET_END_USER_KEY"
        stored = ledger.meta_get(KEY_META) if configured else None
        if stored is not None and _key_id_of_hex(stored) == recorded:
            fix = "the gateway uses the key generated into the ledger: unset RATCHET_END_USER_KEY here"
        raise ValueError(
            f"{where} (key id {out.key_id}) is not the key the gateway hashes end users with (key id {recorded}): {fix}"
        )
    return out


def _key_id_of_hex(stored: str) -> str | None:
    try:
        return _key_id(bytes.fromhex(stored))
    except ValueError:
        return None


def no_train_scopes(key_names: Sequence[str] | None) -> list[str | None]:
    """The scopes a request's end user can be hashed in, so the scopes in which a no-train registration can match:
    each configured API key name (`key_names`; None when keys are off, and every request is keyless) or the keyless
    scope (None), and `EVERY_CLIENT`."""
    return [*key_names, EVERY_CLIENT] if key_names is not None else [None, EVERY_CLIENT]


UNMATCHED_META = "no_train_unmatched"  # meta row: the unmatched registrations last reported, so each change is said once


def check_no_train_scopes(ledger: Ledger, key_names: Sequence[str] | None) -> int:
    """How many no-train registrations match no request under the current keys, because their scope is a key name no
    longer in RATCHET_API_KEYS (renamed or removed), or the keyless scope while keys are on, or a key name while they
    are off. Those end users' requests are stored again until the name is back or they register again, so at startup a
    `no_train_unmatched` event says how many and in which scopes (names only), once per change."""
    unmatched = ledger.no_train_unmatched(no_train_scopes(key_names))
    n = sum(unmatched.values())
    seen = f"{n}:" + ",".join(f"{c}={k}" for c, k in sorted(unmatched.items(), key=lambda kv: str(kv[0])))
    previous = ledger.meta_get(UNMATCHED_META)
    if previous != seen and (n or previous is not None):
        if n:
            names = sorted("keyless" if c is None else c for c in unmatched)
            shown = ", ".join(names[:5]) + (f" and {len(names) - 5} more" if len(names) > 5 else "")
            ledger.event(
                "no_train_unmatched",
                f"{n} no-train registrations match no request under the current API keys (scopes: {shown}): those end "
                "users' requests are stored again until the scope is configured again or they register again",
            )
        ledger.meta_set(UNMATCHED_META, seen)
    return n


# ---------- erasure, retention and export (FR-13.3 to FR-13.5, ADR 0081) ----------
MAX_ERASE_HASHES = 10000  # example hashes one erasure may name
MAX_CONTAINS_CHARS = 1000
MAX_REASON_CHARS = 500
MAX_ACTOR_CHARS = 64
EXAMPLE_HASH = re.compile(r"[0-9a-f]{64}")
EXPORT_PAGE = 500  # rows read per ledger call while an export streams; the lock is free between pages
SPLITS = ("train", "golden")


def _check_erasure(
    client: str | None, end_user_raw: str | None, hashes: Iterable[str] | None, contains: str | None, reason: str, actor: str
) -> tuple[list[str] | None, str]:
    """The checked hashes and the reason as it is stored (redacted); ValueError otherwise. Never quotes an end user or
    the text to match, which may identify someone."""
    if client is not None and not valid_name(client):
        raise ValueError("client must be an API key name (letters, digits, '_', '.', '-'; 1 to 64)")
    if end_user_raw is not None and (not isinstance(end_user_raw, str) or not end_user_raw):
        raise ValueError("end_user must be a non-empty string, as sent in 'user' or 'safety_identifier'")
    wanted: list[str] | None = None
    if hashes is not None:
        if not isinstance(hashes, (list, tuple, set, frozenset)) or any(not isinstance(h, str) for h in hashes):
            raise ValueError("hashes must be a list of example hashes")
        wanted = sorted(set(hashes))
        if not wanted or len(wanted) > MAX_ERASE_HASHES:
            raise ValueError(f"hashes must list 1 to {MAX_ERASE_HASHES} example hashes")
        if any(EXAMPLE_HASH.fullmatch(h) is None for h in wanted):
            raise ValueError("each hash must be 64 lowercase hex characters (an example's hash)")
    if contains is not None and (not isinstance(contains, str) or not contains.strip() or len(contains) > MAX_CONTAINS_CHARS):
        raise ValueError(f"contains must be 1 to {MAX_CONTAINS_CHARS} characters of text, not only spaces")
    if client is None and end_user_raw is None and wanted is None and contains is None:
        raise ValueError("name at least one of client, end_user, hashes or contains")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > MAX_REASON_CHARS:
        raise ValueError(f"reason must be 1 to {MAX_REASON_CHARS} characters")
    if not isinstance(actor, str) or not valid_name(actor.strip()):
        # A name, never free text: the actor goes into the `erase` event, which /ratchet/stats shows to anyone.
        raise ValueError(f"actor must be a name (letters, digits, '_', '.', '-'; 1 to {MAX_ACTOR_CHARS}), such as an admin key's")
    return wanted, redact(reason.strip())


def _selectors(scope: Mapping[str, Any]) -> str:
    names = [("client", "client"), ("end_users", "end user"), ("hashes", "hashes"), ("contains_sha256", "content")]
    return ", ".join(label for key, label in names if scope.get(key) is not None)


def erase(
    led: Ledger,
    *,
    client: str | None = None,
    end_user_raw: str | None = None,
    hashes: Iterable[str] | None = None,
    contains: str | None = None,
    actor: str,
    reason: str,
    dry_run: bool = True,
    shared: str = "delete",
    hasher: EndUsers | None = None,
    end_user_clients: Sequence[str | None] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Erase stored examples for good (FR-13.3). The scope is every selector given, each narrowing it: `client` (an API
    key name: the examples it sent, first or later), `end_user_raw` (hashed here with `hasher`, in the scope of each of
    `end_user_clients`, default `[client]`; the value goes no further), `hashes` (example hashes) and `contains` (a LIKE
    match on the stored, already-redacted text, case-insensitive for ASCII; an identifier the redactor replaced is not
    there to find). With `dry_run` (the default) nothing is written and the result says what would happen.

    Each selected example loses its text and keeps its hash as a tombstone that `Ledger.add_example` refuses from then
    on, also from another client. When the scope names a client or an end user, `shared` (RATCHET_ERASURE_SHARED)
    decides an example another client also sent: `delete` purges it too, `keep` keeps it for that client and unlinks the
    erased party. Erasing an end user also clears that end user's hash from its request rows. The erasure is logged in
    `erasures` and `erasure_items` (hashes, never text; the end user as its HMAC, `contains` as its SHA-256, the reason
    redacted) and as an `erase` event with counts. `affected_versions` lists the versions that may have been trained on
    the erased examples: reported, not edited, and their signed BOMs stay as they were written (INV-14); unlearning
    from them is out of scope (docs/THREAT_MODEL.md). ValueError for a bad scope."""
    if shared not in ERASURE_SHARED:
        raise ValueError(f"shared must be {' or '.join(ERASURE_SHARED)}, not {shared!r}")
    wanted, stored_reason = _check_erasure(client, end_user_raw, hashes, contains, reason, actor)
    end_users: list[str] | None = None
    if end_user_raw is not None:
        if hasher is None:
            raise ValueError("erasing an end user needs the deployment's end-user key (EndUsers)")
        scopes = list(end_user_clients) if end_user_clients is not None else [client]
        end_users = sorted({h for h, _ in hasher.hashes(end_user_raw, scopes)})
    scope: dict[str, Any] = {
        "client": client,
        "end_users": end_users,
        "hashes": len(wanted) if wanted is not None else None,
        "contains_sha256": hashlib.sha256(contains.encode("utf-8", "surrogatepass")).hexdigest() if contains else None,
        "shared": shared,
    }
    scope = {k: v for k, v in scope.items() if v is not None}
    party = client is not None or end_users is not None
    with led.locked():  # one writer: the selection, the rules and the write see the same rows
        rows = led.erasure_candidates(client=client, end_users=end_users, hashes=wanted, contains=contains)
        seen = led.prompt_counts() if any(r["purged_ts"] is None for r in rows) else Counter()
        purge: dict[str, bool] = {}
        unlink: list[tuple[str, str, str | None]] = []
        for r in rows:
            erased_client = client if client is not None else r["client"]
            if party and shared == "keep" and set(led.example_clients(r["hash"])) - {erased_client}:
                unlink.append((r["hash"], "end_user", None) if end_users is not None else (r["hash"], "client", client))
                continue
            counted = bool(r["purged_train"]) if r["purged_ts"] is not None else dataset.counts_as_train(r, seen)
            purge[r["hash"]] = counted
        stamps = {r["hash"]: r["ts"] for r in rows}
        affected = led.affected_versions((h, stamps[h], counted) for h, counted in purge.items())
        result: dict[str, Any] = {
            "object": "ratchet.erasure",
            "id": None,
            "dry_run": bool(dry_run),
            "matched": len(rows),
            "purged": len(purge),
            "kept": len(unlink),
            "requests_unlinked": led.requests_of_end_users(end_users) if end_users else 0,
            "affected_versions": affected,
            "scope": {k: (len(v) if k == "end_users" else v) for k, v in scope.items()},
        }
        if dry_run:
            return result
        done = led.record_erasure(
            actor=actor.strip(),
            reason=stored_reason,
            scope=scope,
            purge=purge,
            unlink=unlink,
            unlink_end_users=end_users or (),
            affected=[v["id"] for v in affected],
            now=now,
        )
        result.update(id=done["id"], purged=done["purged"], kept=done["kept"], requests_unlinked=done["requests_unlinked"])
        held = ", ".join(f"v{v['id']}" for v in affected)
        led.event(
            "erase",
            f"erasure {done['id']} ({_selectors(scope)}) by {actor.strip()}: {done['purged']} examples purged"
            + (f", {done['kept']} shared kept" if done["kept"] else "")
            + (f", {done['requests_unlinked']} request rows unlinked" if done["requests_unlinked"] else "")
            + (f"; may be held by {held} (reported, not changed)" if held else ""),
        )
    return result


def _check_days(days: object, what: str) -> int:
    if isinstance(days, bool) or not isinstance(days, int) or not 0 <= days <= MAX_RETENTION_DAYS:
        raise ValueError(f"{what} must be a whole number of days, 0 to {MAX_RETENTION_DAYS}, not {days!r}")
    return days


def apply_retention(
    led: Ledger,
    retention: Retention,
    *,
    now: float | None = None,
    client: str | None = None,
    example_days: int | None = None,
    request_days: int | None = None,
    dry_run: bool = False,
    chunk: int = PURGE_CHUNK,
) -> dict[str, Any]:
    """Apply the retention windows (FR-13.4). Examples last stored or seen more than `example_days` ago (default
    `retention.example_days`; golden ones too) lose their text with reason `retention`: a later duplicate that may be
    stored brings it back. Request rows logged more than `request_days` ago (default `retention.request_days`) are folded
    into `daily_rollup` and deleted, except rows of a release in flight (`Ledger.fold_requests`). 0 skips a window.
    `client` limits both to one API client's rows; `dry_run` only counts. Examples are purged `chunk` at a time, as
    request rows are folded (`Ledger.fold_requests`). Writes a `purge` event when anything went.
    Runs hourly from the live gateway's tick, at the start of every live night, and from `python -m ratchet.live purge`."""
    if client is not None and not valid_name(client):
        raise ValueError("client must be an API key name (letters, digits, '_', '.', '-'; 1 to 64)")
    ex_days = _check_days(retention.example_days if example_days is None else example_days, "example_days")
    rq_days = _check_days(retention.request_days if request_days is None else request_days, "request_days")
    if isinstance(chunk, bool) or not isinstance(chunk, int) or chunk < 1:
        raise ValueError(f"chunk must be a whole number, 1 or more, not {chunk!r}")
    now = time.time() if now is None else float(now)
    out: dict[str, Any] = {
        "dry_run": bool(dry_run),
        "client": client,
        "example_days": ex_days,
        "request_days": rq_days,
        "examples_purged": 0,
        "requests_folded": 0,
    }
    if ex_days and dry_run:
        out["examples_purged"] = len(led.retention_candidates(now - ex_days * DAY_SECONDS, client))
    elif ex_days:
        # `chunk` examples per transaction, the lock free between them, so the first run after a window is set never
        # stalls the request path (every request logs to the ledger); each chunk reads, decides and writes under the lock.
        while True:
            with led.locked():
                rows = led.retention_candidates(now - ex_days * DAY_SECONDS, client, limit=chunk)
                if not rows:
                    break
                seen = led.prompt_counts()
                counted = {r["hash"]: dataset.counts_as_train(r, seen) for r in rows}
                n = led.purge_examples(counted, "retention", now=now)
            out["examples_purged"] += n
            if n == 0 or len(rows) < chunk:
                break
    if rq_days:
        out["requests_folded"] = led.fold_requests(now - rq_days * DAY_SECONDS, client, dry_run=dry_run, now=now)
    if not dry_run and (out["examples_purged"] or out["requests_folded"]):
        parts = []
        if out["examples_purged"]:
            parts.append(f"{out['examples_purged']} examples not seen for {ex_days} days lost their text")
        if out["requests_folded"]:
            parts.append(f"{out['requests_folded']} request rows older than {rq_days} days folded into daily sums")
        led.event("purge", "retention" + (f" (client {client})" if client else "") + ": " + "; ".join(parts))
    return out


def parse_since(value: object) -> float | None:
    """An export's `since` as seconds since the epoch: a number, or an ISO 8601 date (`2026-10-01`) or date-time
    (`2026-10-01T12:00:00Z`, UTC unless it names an offset). None for None or empty; ValueError otherwise."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise ValueError("since must be seconds since the epoch or an ISO 8601 date")
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip()
        try:
            number = float(text)
        except ValueError:
            try:
                when = dt.datetime.fromisoformat(text.replace("Z", "+00:00").replace("z", "+00:00"))
            except ValueError:
                raise ValueError("since must be seconds since the epoch or an ISO 8601 date such as 2026-10-01") from None
            if when.tzinfo is None:
                when = when.replace(tzinfo=dt.timezone.utc)
            number = when.timestamp()
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError("since must be a finite time")
    return number


def check_export(
    kind: str, *, task: str | None = None, split: str | None = None, since: object = None, client: str | None = None
) -> dict[str, Any]:
    """The checked filters of an export (FR-13.5), ready for `export_lines`; ValueError for an unknown kind, a filter the
    kind does not take, or a bad value. Checked before the first line, so a bad request never starts a stream."""
    if kind not in EXPORT_KINDS:
        raise ValueError(f"export kind must be one of {', '.join(EXPORT_KINDS)}")
    filters = {"task": task or None, "split": split or None, "since": parse_since(since), "client": client or None}
    given = {k for k, v in filters.items() if v is not None}
    extra = sorted(given - EXPORT_FILTERS[kind])
    if extra:
        allowed = ", ".join(sorted(EXPORT_FILTERS[kind])) or "no filters"
        raise ValueError(f"{kind} take {allowed}, not {', '.join(extra)}")
    if filters["split"] is not None and filters["split"] not in SPLITS:
        raise ValueError("split must be train or golden")
    if filters["task"] is not None and (not isinstance(filters["task"], str) or len(filters["task"]) > 64):
        raise ValueError("task must be a task name")
    if filters["client"] is not None and not valid_name(filters["client"]):
        raise ValueError("client must be an API key name")
    return filters


def export_lines(led: Ledger, kind: str, page: int = EXPORT_PAGE, **filters: Any) -> Iterator[str]:
    """One table as NDJSON lines (FR-13.5), in id order, a page at a time, so the ledger is never locked for the whole
    export (rows written meanwhile may appear; for a snapshot, back up the file). `filters` come from `check_export`.
    API keys are never stored, so no line can hold one; end-user hashes are left out (`Ledger.export_page`)."""
    after = 0
    while True:
        rows = led.export_page(kind, after, page, **filters)
        for row in rows:
            yield json.dumps(row, ensure_ascii=False) + "\n"
        if len(rows) < page:
            return
        after = int(rows[-1]["id"])
