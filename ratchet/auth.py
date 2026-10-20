"""Gateway keys: API keys for `/v1/*`, admin keys for management writes, dashboard keys and the metrics token.

API keys come from `RATCHET_API_KEYS` and admin keys from `RATCHET_ADMIN_KEYS`, both as `name:key,name:key`. The name is
what the ledger records (per request, or as `updated_by` of a task an admin wrote), so cost and changes can be reported
per name; the key itself is never stored or logged (INV-13). Keys are compared as SHA-256 digests with
`hmac.compare_digest`, so lookup time does not depend on how much of a guessed key is right.
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


class Token:
    """One shared secret from `env` (RATCHET_METRICS_TOKEN: what a Prometheus scraper sends as its bearer token). Kept
    as a SHA-256 digest and compared in constant time, like a key; never stored or logged. Empty: no token. A token
    shorter than MIN_KEY_CHARS raises ValueError, so a weak one stops startup."""

    def __init__(self, value: str = "", env: str = "RATCHET_METRICS_TOKEN"):
        self.env = env
        value = value.strip()
        if value and len(value) < MIN_KEY_CHARS:
            raise ValueError(f"{env} is shorter than {MIN_KEY_CHARS} characters")
        self._digest = _digest(value) if value else None

    @property
    def enabled(self) -> bool:
        return self._digest is not None

    def matches(self, key: str | None) -> bool:
        if not key or self._digest is None:
            return False
        return hmac.compare_digest(_digest(key), self._digest)


def bearer(authorization: str | None, x_api_key: str | None = None) -> str | None:
    """The key from `Authorization: Bearer <key>` (what OpenAI SDKs send) or `X-API-Key`."""
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer" and value.strip():
            return value.strip()
    return x_api_key.strip() if x_api_key else None
