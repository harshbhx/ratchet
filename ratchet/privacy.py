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
same person seen by two deployments, or by two API clients of one deployment, gets unrelated hashes.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Iterable, Mapping

from .ledger import Ledger
from .openai_compat import MAX_END_USER, STORE_METADATA

# Why a request's teacher answers could not be stored, broadest first (`requests.store_opt_out`).
OPT_OUTS = ("global", "task", "key", "request", "end_user")
# `X-Ratchet-Store` values, in any case: the first set opts the request out, the second is the default spelled out.
STORE_HEADER_OFF = frozenset({"none", "0", "false"})
STORE_HEADER_ON = frozenset({"1", "true"})

END_USER_MAX_CHARS = MAX_END_USER  # `user` and `safety_identifier`: a longer value is a 400 (openai_compat)
END_USER_HASH_HEX = 16  # 64 bits: unique enough for any one deployment's end users, short enough to index
MIN_KEY_CHARS = 16  # RATCHET_END_USER_KEY
KEY_META = "end_user_key"  # meta row holding the generated key (hex), when RATCHET_END_USER_KEY is not set
KEY_ID_META = "end_user_key_id"  # meta row holding the id of the key last used, to notice a change


def store_header(value: str | None) -> bool | None:
    """`X-Ratchet-Store` as False (opt out), True (store, the default) or None (not sent). ValueError for any other
    value, so a typo cannot quietly store what the client meant to keep out."""
    if value is None:
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
    user. The value is only ever hashed (`EndUsers.hash`)."""
    for value in (safety_identifier, user):
        if value:
            return value
    return None


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
        """(hash, client) of `value` in each client's scope: what an admin registers (or erases) for every client."""
        return [(self.hash(value, c), c) for c in clients]


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
