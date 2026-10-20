"""Gateway API keys for `/v1/*`.

Keys come from `RATCHET_API_KEYS` as `name:key,name:key`. The name is what the ledger records
per request (so cost and savings can be reported per client); the key itself is never stored
or logged. Keys are compared as SHA-256 digests with `hmac.compare_digest`, so lookup time does
not depend on how much of a guessed key is right.
"""

from __future__ import annotations

import hashlib
import hmac
import re

MIN_KEY_CHARS = 16
_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def _digest(key: str) -> bytes:
    return hashlib.sha256(key.encode()).digest()


class KeyRing:
    def __init__(self, spec: str = ""):
        self._keys: list[tuple[bytes, str]] = []
        for i, entry in enumerate(e.strip() for e in spec.split(",")):
            if not entry:
                continue
            name, sep, key = entry.partition(":")
            if not sep:  # a bare key gets a generated name
                name, key = f"key{i + 1}", entry
            name, key = name.strip(), key.strip()
            if not _NAME.match(name):
                raise ValueError(f"RATCHET_API_KEYS: bad client name {name!r} (letters, digits, '_', '.', '-')")
            if len(key) < MIN_KEY_CHARS:
                raise ValueError(f"RATCHET_API_KEYS: the key for {name!r} is shorter than {MIN_KEY_CHARS} characters")
            if any(n == name for _, n in self._keys):
                raise ValueError(f"RATCHET_API_KEYS: client name {name!r} appears twice")
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


def bearer(authorization: str | None, x_api_key: str | None = None) -> str | None:
    """The key from `Authorization: Bearer <key>` (what OpenAI SDKs send) or `X-API-Key`."""
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer" and value.strip():
            return value.strip()
    return x_api_key.strip() if x_api_key else None
