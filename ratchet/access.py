"""Who may call each gateway route, and what an anonymous reader of a public dashboard sees (ADR 0061).

Every route has one access level, looked up by method and path template (`route_access`). The gateway's `_access`
dependency enforces the reader levels (`DASHBOARD`, `BOM`, `METRICS`); the other routes check their own callers (`_client` for
`API`, `_admin` for `ADMIN`, `_mock_only` for `MOCK`) or take anyone (`PUBLIC`). `METRICS` is `DASHBOARD` that also
takes RATCHET_METRICS_TOKEN, so a Prometheus scraper needs no dashboard key. A route this table does not know is
treated as `DASHBOARD`, so a new endpoint is private in live mode until someone classifies it, and
tests/test_security.py names it.

Scrubbing (`scrub_stats`, `scrub_tasks`) is for dashboards shown to anyone: client names become pseudonyms (`client-1`
... by sorted name), also in budget alerts, event details lose URLs, and an error message after an exception's class is
cut, because v0.4 wrote `ConnectError: <httpx text with the student URL>` into events and old ledgers still hold them.
Dollar caps (a key's, the gateway's budget) are left out too. Standard library only.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from functools import lru_cache
from typing import Any

from .spend import ALERT_KIND, public_alert

PUBLIC = "public"  # anyone: the pages, health, the BOM public key, Scout (behind its own budget guard)
DASHBOARD = "dashboard"  # read-only management data: an admin or dashboard key unless RATCHET_PUBLIC_DASHBOARD=1
BOM = "bom"  # bills of materials: as DASHBOARD, and public with RATCHET_PUBLIC_BOM=1, for auditors
METRICS = "metrics"  # Prometheus metrics: as DASHBOARD, and RATCHET_METRICS_TOKEN reads it too (a scraper's token)
API = "api"  # the OpenAI-compatible API: an API key when RATCHET_API_KEYS is set (`gateway._client`)
ADMIN = "admin"  # management writes: an admin key (`gateway._admin`)
MOCK = "mock"  # demo controls: mock mode only (`gateway._mock_only`, invariant 8)
LEVELS = (PUBLIC, DASHBOARD, BOM, METRICS, API, ADMIN, MOCK)
READER_LEVELS = frozenset({DASHBOARD, BOM, METRICS})  # the levels `_access` checks itself

# (method, path template) -> level. HEAD is looked up as GET. Some rows name routes other work packages add
# (/livez, /readyz, /metrics, /ratchet/kpis), so they get the right level the moment they exist.
ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/"): PUBLIC,
    ("GET", "/dashboard"): PUBLIC,
    ("GET", "/scout"): PUBLIC,
    ("GET", "/lite"): PUBLIC,
    ("GET", "/lite/dashboard"): PUBLIC,
    ("GET", "/lite/scout"): PUBLIC,
    ("GET", "/favicon.svg"): PUBLIC,
    ("GET", "/healthz"): PUBLIC,
    ("GET", "/livez"): PUBLIC,
    ("GET", "/readyz"): PUBLIC,
    ("GET", "/ratchet/bom/public-key"): PUBLIC,
    ("GET", "/ratchet/stats"): DASHBOARD,
    ("GET", "/ratchet/kpis"): DASHBOARD,
    ("GET", "/ratchet/drift"): DASHBOARD,
    ("GET", "/ratchet/teacher"): DASHBOARD,
    ("POST", "/ratchet/teacher/ack"): ADMIN,
    ("GET", "/metrics"): METRICS,
    ("GET", "/ratchet/cost"): DASHBOARD,
    # The ledger replay reads like /ratchet/stats (FR-16.5); savings receipts check their callers themselves
    # (`gateway._receipt_reader`: an API key reads its own, an admin key all; FR-15.5), as the API does.
    ("GET", "/ratchet/replay"): DASHBOARD,
    ("GET", "/ratchet/replay/frontier"): DASHBOARD,
    ("GET", "/ratchet/receipts"): API,
    ("GET", "/ratchet/receipts/{period}"): API,
    ("GET", "/ratchet/receipts/{period}/proof/{request_id}"): API,
    ("GET", "/ratchet/receipts/{period}/leaves"): API,
    # Membership proofs are an oracle for guessable texts (FR-16.4, ADR 0027): an admin's, checked by `gateway._admin`.
    ("POST", "/ratchet/versions/{vid}/proof"): ADMIN,
    ("GET", "/ratchet/versions/{vid}/heldout"): ADMIN,
    ("POST", "/ratchet/redact"): DASHBOARD,
    ("GET", "/ratchet/tasks"): DASHBOARD,
    ("GET", "/ratchet/tasks/{name}"): DASHBOARD,
    ("GET", "/ratchet/bom"): BOM,
    ("GET", "/ratchet/versions/{vid}/bom"): BOM,
    ("POST", "/ratchet/tasks/validate"): ADMIN,
    ("PUT", "/ratchet/tasks/{name}"): ADMIN,
    ("DELETE", "/ratchet/tasks/{name}"): ADMIN,
    ("POST", "/ratchet/cost/endpoint"): ADMIN,
}
# Path prefixes for any method, checked in order after ROUTES. The /ratchet/requests/ and /ratchet/versions/ rows cover
# read endpoints under them (a request's record, a version's commitments); /v1/ covers an API client's own lookups.
# Everything under /ratchet/admin/ (ADR 0064), its reads included, is an admin's: the version list and the audit log say
# who changed what, and why.
PREFIXES: tuple[tuple[str, str], ...] = (
    ("/ratchet/admin/", ADMIN),
    ("/ratchet/requests/", DASHBOARD),
    ("/ratchet/versions/", DASHBOARD),
    ("/ratchet/demo/", MOCK),
    ("/scout/", PUBLIC),
    ("/v1/", API),
    ("/openai/", API),  # the Azure-style chat route (ADR 0048): an API client's, as /v1/
)


@lru_cache(maxsize=1024)
def route_access(method: str, path: str) -> str | None:
    """The access level of the route with this method and path template (`/ratchet/tasks/{name}`, as FastAPI declares
    it), or None when neither ROUTES nor PREFIXES classify it (the gateway then treats it as DASHBOARD)."""
    method = "GET" if method.upper() == "HEAD" else method.upper()
    level = ROUTES.get((method, path))
    if level is not None:
        return level
    for prefix, level in PREFIXES:
        if path.startswith(prefix):
            return level
    return None


# ----- scrubbing -----
URL = re.compile(r"https?://\S+")
# An exception's class followed by its message: `ConnectError: [Errno 111] ...`, as v0.4 wrote failures into events. The
# message can quote an endpoint URL or request text, so a scrubbed detail keeps the class only. Today's events already
# name the class and status only (`resilience.describe_failure`), which this leaves as it is.
ERROR_TEXT = re.compile(r"\b([A-Z][A-Za-z0-9_]*(?:Error|Exception|Timeout|Exit|Interrupt|Warning)):\s.*", re.DOTALL)


def scrub_detail(text: str) -> str:
    """An event detail as an anonymous reader may see it: an error message cut to its exception class, then every
    http(s) URL left replaced with `<url>`."""
    return URL.sub("<url>", ERROR_TEXT.sub(r"\1", text))


def pseudonyms(names: Iterable[str | None]) -> dict[str, str]:
    """Stable pseudonyms for client names: `client-1`, `client-2`, ... in sorted order of the names. Pass every name a
    reader could see (the configured keys and the names in the ledger), so one name gets the same pseudonym in every
    response while the keys stay the same."""
    return {name: f"client-{i}" for i, name in enumerate(sorted({n for n in names if n}), start=1)}


def scrub_event(event: Mapping[str, Any], names: Mapping[str, str]) -> dict[str, Any]:
    """An event for an anonymous reader: its detail scrubbed (`scrub_detail`); a `budget` alert also names its key by
    pseudonym and no dollar cap (`spend.public_alert`, ADR 0063). A new dict."""
    detail = str(event.get("detail", ""))
    if event.get("kind") == ALERT_KIND:
        detail = public_alert(detail, names)
    return {**event, "detail": scrub_detail(detail)}


def scrub_stats(payload: Mapping[str, Any], names: Mapping[str, str]) -> dict[str, Any]:
    """A /ratchet/stats payload for an anonymous reader: clients under their pseudonyms (`names`, from `pseudonyms`),
    event details scrubbed (`scrub_event`), no dollar caps (each key's `daily_cap_usd`, the gateway's `budget.daily_usd`
    and `remaining_usd` are null, as the gateway's 429s never name its budget; ADR 0063) and `security.scrubbed` true. A
    new dict; `payload` (the cached one) is not changed."""
    out = dict(payload)
    out["clients"] = [
        {**c, "client": names.get(c.get("client"), "client"), **({"daily_cap_usd": None} if "daily_cap_usd" in c else {})}
        for c in payload.get("clients", [])
    ]
    out["events"] = [scrub_event(e, names) for e in payload.get("events", [])]
    if isinstance(payload.get("budget"), Mapping):
        out["budget"] = {**payload["budget"], "daily_usd": None, "remaining_usd": None}
    out["security"] = {**payload.get("security", {}), "scrubbed": True}
    return out


def scrub_task(entry: Mapping[str, Any], names: Mapping[str, str]) -> dict[str, Any]:
    """A task as GET /ratchet/tasks lists it, for an anonymous reader: its `clients` allow-list under pseudonyms. Who
    changed an API task (`updated_by`) is an admin's name, which stays."""
    clients = entry.get("clients")
    if not clients:
        return dict(entry)
    return {**entry, "clients": [names.get(c, "client") for c in clients]}


def scrub_client(row: Mapping[str, Any], names: Mapping[str, str]) -> dict[str, Any]:
    """A row that names a client (a request's or a failure's, from the request lookup) for an anonymous reader: the name
    under its pseudonym (`client` for one `names` does not hold). A new dict."""
    out = dict(row)
    if out.get("client"):
        out["client"] = names.get(out["client"], "client")
    return out
