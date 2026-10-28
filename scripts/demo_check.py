#!/usr/bin/env python3
"""Check the live gateway from outside, as a judge sees it (FR-11.34). Standard library plus `ratchet.watch`.

    python scripts/demo_check.py https://<domain> [--mode live|mock] [--key-env NAME] [--chat] [--json] [--timeout 30]

One line per check: OK, WARN or FAIL. Exit 0 when nothing failed, 1 on a failure, 2 on bad input. Keys and the admin token
come from the environment only (never a flag: shell history keeps flags) and are never printed. It never calls the mock-only
demo controls and never runs Scout (a Scout run spends budget through `Guard.admit`: run one by hand).

    --key-env NAME   `GET /v1/models` with the key in $NAME (free: no model call), the judge key's check. When the demo is
                     guarded, the admin token is read from $RATCHET_ADMIN_TOKEN.
    --chat           one tiny completion (about $0.0001) with the key in $RATCHET_WATCH_KEY, classified as `ratchet.watch` does
                     on the pinned `watchdog-probe` task.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, TextIO

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ratchet import watch  # noqa: E402

OK, WARN, FAIL = "OK", "WARN", "FAIL"
PAGES = ("/", "/scout", "/dashboard")


def run_checks(
    base: str,
    *,
    mode: str = "live",
    key_env: str = "",
    chat: bool = False,
    timeout: float = 30,
    fetch: watch.Fetch = watch.urllib_fetch,
    environ: Mapping[str, str] | None = None,
) -> list[tuple[str, str, str]]:
    """(status, check, detail) for every check, in order."""
    env = os.environ if environ is None else environ
    base = base.rstrip("/")
    rows: list[tuple[str, str, str]] = []

    def get(path: str, headers: Mapping[str, str] | None = None) -> watch.Reply:
        return fetch("GET", base + path, headers={"User-Agent": "ratchet-demo-check/1", **(headers or {})}, timeout=timeout)

    def doc(r: watch.Reply) -> dict[str, Any]:
        try:
            out = json.loads(r.body)
        except (ValueError, RecursionError):
            return {}
        return out if isinstance(out, dict) else {}

    for path in PAGES:
        r = get(path)
        html = "text/html" in r.headers.get("content-type", "")
        rows.append(
            (OK if r.status == 200 and html else FAIL, path, f"HTTP {r.status or r.error} {'html' if html else 'not html'}")
        )
    r = get("/healthz")
    h = doc(r)
    good = r.status == 200 and h.get("mode") == mode
    rows.append(
        (
            OK if good else FAIL,
            "/healthz",
            f"HTTP {r.status or r.error}, mode {h.get('mode')!r} (want {mode!r}), version {h.get('version')}",
        )
    )
    r = get("/readyz")
    body = doc(r)
    if r.status == 404:
        rows.append((WARN, "/readyz", "not found: this image has no /readyz"))
    elif r.status == 200 and body.get("status") == "ready":
        rows.append((OK, "/readyz", "ready"))
    elif r.status == 200:
        bad = sorted(k for k, v in (body.get("checks") or {}).items() if isinstance(v, dict) and v.get("ok") is False)
        rows.append((WARN, "/readyz", f"{body.get('status')}: failing checks {bad or 'none named'}"))
    else:
        rows.append((FAIL, "/readyz", f"HTTP {r.status or r.error}: {body.get('status', 'no answer')}"))
    r = get("/scout/status")
    s = doc(r)
    if r.status != 200:
        rows.append((FAIL, "/scout/status", f"HTTP {r.status or r.error}"))
    else:
        if mode == "live":
            rows.append((OK if s.get("tavily") is True else FAIL, "scout search", f"tavily configured: {s.get('tavily')}"))
        search = s.get("search")
        if isinstance(search, dict) and search.get("available") is False:
            rows.append((WARN, "scout search", "search is unavailable now (Scout labels it per run)"))
        state = (s.get("live_check") or {}).get("state")
        if state == "failing":
            rows.append((FAIL, "live check", "the watchdog's last probe failed: live model calls are failing"))
        elif state in ("stale", "unknown"):
            rows.append((WARN, "live check", f"{state}: the watchdog has not reported recently"))
        else:
            rows.append((OK, "live check", str(state)))
    if key_env:
        key = env.get(key_env, "")
        if not key:
            rows.append((FAIL, "/v1/models", f"${key_env} is not set"))
        else:
            headers = {"Authorization": f"Bearer {key}"}
            if env.get("RATCHET_ADMIN_TOKEN"):
                headers["X-Ratchet-Admin-Token"] = env["RATCHET_ADMIN_TOKEN"]
            r = get("/v1/models", headers)
            if r.status == 200:
                rows.append((OK, "/v1/models", "the key is accepted"))
            elif r.status == 401:
                rows.append((FAIL, "/v1/models", "key refused (401)"))
            elif r.status == 403 and not watch._envelope(r.body):
                rows.append((FAIL, "/v1/models", "edge guard: set RATCHET_ADMIN_TOKEN in the environment"))
            else:
                rows.append((FAIL, "/v1/models", f"HTTP {r.status or r.error}"))
    if chat:
        key = watch.demo_check_keys(env)["RATCHET_WATCH_KEY"]
        if not key:
            rows.append((FAIL, "chat", "$RATCHET_WATCH_KEY is not set"))
        else:
            cfg = watch.WatchConfig(
                api_key=key,
                domain=base.split("://", 1)[-1],
                guarded=bool(env.get("RATCHET_ADMIN_TOKEN")),
                admin_token=env.get("RATCHET_ADMIN_TOKEN", ""),
            )
            _, headers, payload = watch.probe_request(cfg, "demo-check")
            r = fetch("POST", base + "/v1/chat/completions", headers=headers, body=payload, timeout=timeout)
            out = watch.classify(r, env.get("RATCHET_TEACHER_FALLBACK_MODEL", ""))
            tail = f", upstream {out.upstream_status}" if out.upstream_status else ""
            rows.append((OK if out.ok else FAIL, "chat", f"{out.code} ({out.side} side{tail})"))
    return rows


def main(
    argv: Sequence[str] | None = None,
    *,
    fetch: watch.Fetch = watch.urllib_fetch,
    environ: Mapping[str, str] | None = None,
    out: TextIO | None = None,
) -> int:
    out = out or sys.stdout
    p = argparse.ArgumentParser(description="Check the live gateway from outside.")
    p.add_argument("url")
    p.add_argument("--mode", choices=("live", "mock"), default="live")
    p.add_argument(
        "--key-env", default="RATCHET_DEMO_CHECK_KEY" if watch.demo_check_keys(os.environ)["RATCHET_DEMO_CHECK_KEY"] else ""
    )
    p.add_argument("--chat", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--timeout", type=float, default=30)
    try:
        a = p.parse_args(argv)
    except SystemExit as e:
        return int(e.code or 0) if e.code in (0, None) else 2
    if not a.url.startswith(("https://", "http://")):
        print("error: the URL must start with https://", file=sys.stderr)
        return 2
    rows = run_checks(a.url, mode=a.mode, key_env=a.key_env, chat=a.chat, timeout=a.timeout, fetch=fetch, environ=environ)
    if a.json:
        print(json.dumps([{"status": s, "check": c, "detail": d} for s, c, d in rows]), file=out)
    else:
        for s, c, d in rows:
            print(f"{s:4} {c}: {d}", file=out)
    return 1 if any(s == FAIL for s, _, _ in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
