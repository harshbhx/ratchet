"""Gateway keys: API keys for `/v1/*` and admin keys for management writes.

API keys come from `RATCHET_API_KEYS` and admin keys from `RATCHET_ADMIN_KEYS`, both as `name:key,name:key`. The name is
what the ledger records (per request, or as `updated_by` of a task an admin wrote), so cost and changes can be reported
per name; the key itself is never stored or logged (INV-13). Keys are compared as SHA-256 digests with
`hmac.compare_digest`, so lookup time does not depend on how much of a guessed key is right.

`RATCHET_KEY_LIMITS` adds per-key settings by name (`parse_key_limits`); so far only `store`.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Collection
from dataclasses import dataclass

MIN_KEY_CHARS = 16
_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def valid_name(name: object) -> bool:
    """Whether `name` could name a key: letters, digits, `_`, `.` and `-`, 1 to 64 characters (as RATCHET_API_KEYS and
    RATCHET_ADMIN_KEYS require). An erasure or export may name a client whose key was since removed."""
    return isinstance(name, str) and _NAME.fullmatch(name) is not None


def _digest(key: str) -> bytes:
    return hashlib.sha256(key.encode()).digest()


class KeyRing:
    """Named keys from one variable (`env`, which error messages name). A bad spec raises ValueError, so a typo stops
    the gateway at startup."""

    def __init__(self, spec: str = "", env: str = "RATCHET_API_KEYS"):
        self.env = env
        self._keys: list[tuple[bytes, str]] = []
        for i, entry in enumerate(e.strip() for e in spec.split(",")):
            if not entry:
                continue
            name, sep, key = entry.partition(":")
            if not sep:  # a bare key gets a generated name
                name, key = f"key{i + 1}", entry
            name, key = name.strip(), key.strip()
            if not _NAME.match(name):
                raise ValueError(f"{env}: bad name {name!r} (letters, digits, '_', '.', '-')")
            if len(key) < MIN_KEY_CHARS:
                raise ValueError(f"{env}: the key for {name!r} is shorter than {MIN_KEY_CHARS} characters")
            if any(n == name for _, n in self._keys):
                raise ValueError(f"{env}: name {name!r} appears twice")
            self._keys.append((_digest(key), name))

    @property
    def enabled(self) -> bool:
        return bool(self._keys)

    @property
    def names(self) -> list[str]:
        return [n for _, n in self._keys]

    def identify(self, key: str | None) -> str | None:
        """The client name for `key`, or None when it matches no configured key."""
        if not key:
            return None
        d = _digest(key)
        found = None
        for digest, name in self._keys:  # check every key, so timing does not reveal which one matched
            if hmac.compare_digest(d, digest):
                found = name
        return found

    def shared(self, other: KeyRing) -> list[str]:
        """The names in this ring whose key is also a key in `other`: an admin key that is also an API key would let a
        client change what the gateway does, so the gateway refuses that at startup."""
        theirs = {digest for digest, _ in other._keys}
        return [name for digest, name in self._keys if digest in theirs]


# The fields RATCHET_KEY_LIMITS takes per key. Only `store` is built here (FR-13.1); spend and rate limits are planned in
# the same syntax and are refused until they are built, so no limit looks active before it is.
KEY_LIMIT_FIELDS = ("store",)


@dataclass(frozen=True)
class KeyLimits:
    """What one API key may do beyond calling the API. `store` False: the teacher answers of its requests are never
    stored as training examples (FR-13.1), whatever the request says."""

    store: bool = True


def parse_key_limits(spec: str, names: Collection[str], env: str = "RATCHET_KEY_LIMITS") -> dict[str, KeyLimits]:
    """{key name: KeyLimits} from `name:field=value,field=value;name:...` (`store=0` or `store=1`). A key not listed
    gets the defaults. A malformed entry, a name that is not in `names` (the API keys' names), a name given twice, or an
    unknown field raises ValueError, so a typo stops startup. An unknown name is not quoted in the message: a key
    pasted where its name belongs must not reach the logs (INV-13)."""
    known = set(names)
    out: dict[str, KeyLimits] = {}
    for i, entry in enumerate(e.strip() for e in spec.split(";")):
        if not entry:
            continue
        name, sep, rest = entry.partition(":")
        name = name.strip()
        if not sep or not name:
            raise ValueError(f"{env}: entry {i + 1} must read name:field=value,field=value")
        if name not in known:
            raise ValueError(f"{env}: entry {i + 1} names no key in RATCHET_API_KEYS (use the key's name, never the key)")
        if name in out:
            raise ValueError(f"{env}: {name!r} appears twice")
        values: dict[str, str] = {}
        for item in (part.strip() for part in rest.split(",")):
            if not item:
                continue
            field, eq, value = (x.strip() for x in item.partition("="))
            if not eq or not field:
                raise ValueError(f"{env}: {name!r}: {item!r} must read field=value")
            if field not in KEY_LIMIT_FIELDS:
                raise ValueError(f"{env}: {name!r}: unknown field {field!r} (known: {', '.join(KEY_LIMIT_FIELDS)})")
            if field in values:
                raise ValueError(f"{env}: {name!r}: {field!r} appears twice")
            values[field] = value
        store = values.get("store", "1")
        if store not in ("0", "1"):
            raise ValueError(f"{env}: {name!r}: store must be 0 or 1, not {store!r}")
        out[name] = KeyLimits(store=store == "1")
    return out


def bearer(authorization: str | None, x_api_key: str | None = None) -> str | None:
    """The key from `Authorization: Bearer <key>` (what OpenAI SDKs send) or `X-API-Key`."""
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer" and value.strip():
            return value.strip()
    return x_api_key.strip() if x_api_key else None
