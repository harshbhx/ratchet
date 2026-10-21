"""Gateway keys: API keys for `/v1/*`, admin keys for management writes, dashboard keys and the metrics token.

API keys come from `RATCHET_API_KEYS` and admin keys from `RATCHET_ADMIN_KEYS`, both as `name:key,name:key`. The name is
what the ledger records (per request, or as `updated_by` of a task an admin wrote), so cost and changes can be reported
per name; the key itself is never stored or logged (INV-13). Keys are compared as SHA-256 digests with
`hmac.compare_digest`, so lookup time does not depend on how much of a guessed key is right.

What each API key may use (`KeyLimits`: requests and US dollars per UTC day, US dollars per UTC month, requests per minute
and in flight, the tasks it may send, whether its requests may become training data) comes from `RATCHET_KEY_LIMITS` over
the `RATCHET_API_KEY_*` defaults (`parse_key_limits`, ADR 0063). Limits are keyed by the key's name, never the key.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, replace

from . import tasks

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


# ---------- per-key limits (FR-12.2, FR-12.3, ADR 0063) ----------
LIMIT_FIELDS = ("daily_requests", "daily_usd", "monthly_usd", "rpm", "concurrency", "tasks", "store")
MAX_DAILY_REQUESTS = 100_000_000
MAX_RPM = 1_000_000
MAX_CONCURRENCY = 10_000
_WHOLE = re.compile(r"[0-9]+")
_USD = re.compile(r"[0-9]+(?:\.[0-9]*)?|\.[0-9]+")  # a plain decimal: no sign, exponent, nan or inf


@dataclass(frozen=True)
class KeyLimits:
    """What one API key may use; 0 turns a limit off. `daily_requests`: chat completions per UTC day; `daily_usd` and
    `monthly_usd`: US dollars per UTC day and month (answered and failed requests, per-token prices); `rpm`: requests a
    minute (a token bucket, `ratelimit.burst`); `concurrency`: requests in flight; `tasks`: the task names it may send
    (None: any task the task's own `clients` allow); `store`: whether its requests may become training examples (False is
    `RequestMeta.storable=False`: served, audited and logged, never stored)."""

    daily_requests: int = 0
    daily_usd: float = 0.0
    monthly_usd: float = 0.0
    rpm: int = 0
    concurrency: int = 0
    tasks: frozenset[str] | None = None
    store: bool = True

    @property
    def usd_capped(self) -> bool:
        """Whether a daily or monthly US dollar cap applies (the spend headers are sent only then)."""
        return self.daily_usd > 0 or self.monthly_usd > 0

    def allows_task(self, name: str) -> bool:
        return self.tasks is None or name in self.tasks

    def check(self, env: str) -> KeyLimits:
        """These limits, or ValueError naming `env` for a value out of range (the defaults come from five variables, so
        `env` names the whole group)."""
        for name, value, top in (
            ("daily_requests", self.daily_requests, MAX_DAILY_REQUESTS),
            ("rpm", self.rpm, MAX_RPM),
            ("concurrency", self.concurrency, MAX_CONCURRENCY),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= top:
                raise ValueError(f"{env}: {name} must be a whole number from 0 (off) to {top}, not {value!r}")
        for name, value in (("daily_usd", self.daily_usd), ("monthly_usd", self.monthly_usd)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{env}: {name} must be US dollars, 0 (off) or more, not {value!r}")
        return self


def _limit_value(env: str, name: str, field: str, raw: str) -> object:
    """One `field=value` of RATCHET_KEY_LIMITS, parsed and checked; ValueError names the key and the field."""
    where = f"{env}: {name}.{field}"
    if field in ("daily_requests", "rpm", "concurrency"):
        top = {"daily_requests": MAX_DAILY_REQUESTS, "rpm": MAX_RPM, "concurrency": MAX_CONCURRENCY}[field]
        if not _WHOLE.fullmatch(raw) or int(raw) > top:
            raise ValueError(f"{where} must be a whole number from 0 (off) to {top}, not {raw!r}")
        return int(raw)
    if field in ("daily_usd", "monthly_usd"):
        if not _USD.fullmatch(raw):
            raise ValueError(f"{where} must be US dollars, 0 (off) or more, not {raw!r}")
        return float(raw)
    if field == "store":
        if raw not in ("0", "1"):
            raise ValueError(f"{where} must be 0 or 1, not {raw!r}")
        return raw == "1"
    names = [t.strip() for t in raw.split("|")]  # tasks
    bad = [t for t in names if not tasks.valid_name(t)]
    if not raw or bad:
        raise ValueError(f"{where} must be task names separated by '|' (letters, digits, '_', '.', '-'), not {raw!r}")
    return frozenset(names)


def parse_key_limits(
    spec: str, names: Iterable[str], defaults: KeyLimits | None = None, env: str = "RATCHET_KEY_LIMITS"
) -> dict[str, KeyLimits]:
    """Every API key name in `names` -> its limits: `defaults` (the RATCHET_API_KEY_* values), with the fields `spec`
    gives it replaced. `spec` reads `name:field=value,field=value;name:...`, the fields LIMIT_FIELDS (`tasks` takes names
    separated by '|'). A name that is not a key, an unknown field, a field or a key given twice, or a bad value raises
    ValueError naming `env`, so a typo stops startup, as in KeyRing. An unknown name is not echoed (it may be a key pasted
    by mistake): the message lists the key names instead."""
    base = defaults if defaults is not None else KeyLimits()
    out = dict.fromkeys(names, base)
    seen: set[str] = set()
    for i, entry in enumerate((e.strip() for e in spec.split(";")), start=1):
        if not entry:
            continue
        name, sep, body = entry.partition(":")
        name = name.strip()
        if name not in out:
            known = ", ".join(sorted(out)) or "none: RATCHET_API_KEYS is empty"
            raise ValueError(f"{env}: entry {i} does not start with the name of a key in RATCHET_API_KEYS ({known})")
        if not sep or not body.strip():
            raise ValueError(f"{env}: {name} must read {name}:field=value,field=value")
        if name in seen:
            raise ValueError(f"{env}: {name} appears twice")
        seen.add(name)
        changes: dict[str, object] = {}
        for item in (x.strip() for x in body.split(",")):
            if not item:
                continue
            field, eq, raw = item.partition("=")
            field, raw = field.strip(), raw.strip()
            if not eq:
                raise ValueError(f"{env}: {name}: {item!r} must read field=value")
            if field not in LIMIT_FIELDS:
                raise ValueError(f"{env}: {name}: unknown field {field!r}; use {', '.join(LIMIT_FIELDS)}")
            if field in changes:
                raise ValueError(f"{env}: {name}.{field} appears twice")
            changes[field] = _limit_value(env, name, field, raw)
        out[name] = replace(base, **changes)  # type: ignore[arg-type]
    return out
