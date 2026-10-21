"""Judge access for the public Scout demo (ADR 0120): an access code, sent once in a POST body, opens a session carried
by a signed cookie, and judge runs draw on a reserved share of the daily allowance (`Guard`).

The code is handled like an API key (INV-13): only its SHA-256 digest and a key derived from it are kept, comparisons are
constant-time, and it is never stored, logged or echoed. It never travels in a URL or a custom header, because uvicorn
and Caddy write both to their access logs; Caddy redacts the Cookie header by default. The cookie key comes from the
code, so sessions survive a restart, and rotating the code revokes every session.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable

COOKIE = "ratchet_judge"
COOKIE_PATH = "/scout"
MAX_CODE_CHARS = 256  # longer input is refused before it is hashed
JUDGE_ATTEMPTS_PER_HOUR = 10  # code attempts per client IP per rolling hour (POST /scout/judge)
_CONTEXT = b"ratchet.scout.judge/v1"
_COOKIE_RE = re.compile(r"v1\.([0-9]{1,12})\.([0-9a-f]{16})\.([A-Za-z0-9_-]{43})")


def _sha256(text: str) -> bytes:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).digest()


class JudgeAccess:
    """Checks a judge code and issues and verifies session cookies (`v1.<expires>.<nonce>.<signature>`, HMAC-SHA256
    under a key derived from the code). Keeps no copy of the code."""

    def __init__(self, code: str, session_s: float, clock: Callable[[], float] = time.time) -> None:
        if not isinstance(code, str) or not code:
            raise ValueError("judge access needs a code")  # never echo it
        self._digest = _sha256(code)
        self._key = hmac.new(code.encode("utf-8"), _CONTEXT, hashlib.sha256).digest()
        self.session_s = float(session_s)
        self.clock = clock

    def __repr__(self) -> str:
        return f"JudgeAccess(session_s={self.session_s:g})"

    def check(self, given: object) -> bool:
        """Whether `given` is the code: constant-time over digests, as `KeyRing.identify` compares keys."""
        if not isinstance(given, str) or not given or len(given) > MAX_CODE_CHARS:
            return False
        return hmac.compare_digest(_sha256(given), self._digest)

    def _sign(self, payload: str) -> str:
        mac = hmac.new(self._key, payload.encode("ascii"), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(mac).decode("ascii").rstrip("=")

    def issue(self) -> tuple[str, int]:
        """(cookie value, expiry as a Unix time)."""
        expires = int(self.clock() + self.session_s)
        payload = f"v1.{expires}.{secrets.token_hex(8)}"
        return f"{payload}.{self._sign(payload)}", expires

    def verify(self, value: object) -> bool:
        """Whether a cookie value is a session this code issued and it has not expired. Anything malformed is False."""
        if not isinstance(value, str):
            return False
        m = _COOKIE_RE.fullmatch(value)
        if m is None:
            return False
        expires, nonce, sig = m.groups()
        if not hmac.compare_digest(self._sign(f"v1.{expires}.{nonce}"), sig):
            return False
        return int(expires) > self.clock()


class AttemptLimiter:
    """At most `per_hour` attempts per key (a client IP) in any rolling hour, in memory only, so no IP is stored. Past
    `max_keys` tracked keys, only the most recently seen half is kept, which bounds memory under a flood of addresses
    (whose attempts cannot guess a code of 16 or more characters anyway)."""

    def __init__(self, per_hour: int = JUDGE_ATTEMPTS_PER_HOUR, clock: Callable[[], float] = time.time, max_keys: int = 4096):
        self.per_hour = per_hour
        self.clock = clock
        self.max_keys = max_keys
        self._lock = threading.Lock()
        self._seen: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        """Count one attempt and return True, or return False when the key has used its hour."""
        now = self.clock()
        with self._lock:
            if len(self._seen) > self.max_keys:
                live = sorted((q[-1], k) for k, q in self._seen.items() if q and now - q[-1] <= 3600)
                self._seen = {k: self._seen[k] for _, k in live[-(self.max_keys // 2) :]}  # the most recent half
            q = self._seen.setdefault(key, deque())
            while q and now - q[0] > 3600:
                q.popleft()
            if len(q) >= self.per_hour:
                return False
            q.append(now)
            return True
