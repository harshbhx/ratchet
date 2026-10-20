"""Operator commands on the ledger (RATCHET_DB): erase stored data and export tables (FR-13.3, FR-13.5, ADR 0081).

    python -m ratchet.admin erase --reason "GDPR request 42" --client acme            # dry run: what would go
    python -m ratchet.admin erase --reason "..." --end-user - --client acme --apply   # end user read from stdin
    python -m ratchet.admin erase --reason "..." --hash <64 hex> --hash <64 hex> --apply
    python -m ratchet.admin erase --reason "..." --contains "Jane Roe" --apply
    python -m ratchet.admin export examples --task ticket-triage --split train --out train.ndjson
    python -m ratchet.admin export requests --since 2026-10-01

`erase` is a dry run unless `--apply` is given, as `POST /ratchet/admin/privacy/erase` is. An end user given without
`--client` is erased in every API key's scope (RATCHET_API_KEYS) and the keyless scope, as an admin's no-train
registration is. Pass `--end-user -` to read the value from standard input, so it stays out of the shell history.
`export` writes NDJSON to standard output or `--out`. Both open the same ledger file as the gateway; the ledger's
writes are transactions, so they can run while the gateway serves.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, TextIO

from . import privacy
from .auth import KeyRing
from .config import Settings
from .ledger import EXPORT_KINDS, Ledger

CLI_ACTOR = "cli"


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m ratchet.admin", description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", help="ledger file (default RATCHET_DB)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("erase", help="erase stored examples for good (a dry run without --apply)")
    e.add_argument("--reason", required=True, help="why (stored redacted in the erasure log)")
    e.add_argument("--client", help="an API key name: the examples it sent (and the scope of --end-user)")
    e.add_argument("--end-user", help="the value a client sends as user or safety_identifier; '-' reads it from stdin")
    e.add_argument("--hash", action="append", dest="hashes", help="an example hash (repeat for more)")
    e.add_argument("--contains", help="text in the stored (already redacted) messages, answer or conditions")
    e.add_argument("--apply", action="store_true", help="erase; without it nothing changes")
    e.add_argument("--actor", default=CLI_ACTOR, help=f"who asks, for the erasure log (default {CLI_ACTOR!r})")
    x = sub.add_parser("export", help="one table as NDJSON")
    x.add_argument("kind", choices=EXPORT_KINDS)
    x.add_argument("--task")
    x.add_argument("--split", choices=privacy.SPLITS)
    x.add_argument("--since", help="seconds since the epoch, or an ISO 8601 date or date-time (UTC)")
    x.add_argument("--client", help="an API key name")
    x.add_argument("--out", help="file to write (default standard output)")
    return ap


def _end_user(value: str | None, stdin: TextIO) -> str | None:
    if value != "-":
        return value
    line = stdin.readline().rstrip("\r\n")
    if not line:
        raise SystemExit("--end-user -: no value on standard input")
    return line


def run_erase(led: Ledger, s: Settings, args: argparse.Namespace, stdin: TextIO = sys.stdin) -> dict[str, Any]:
    end_user = _end_user(args.end_user, stdin)
    clients = None
    if end_user is not None and args.client is None:
        clients = [*KeyRing(s.api_keys).names, None]  # every API key's scope and the keyless one
    hasher = privacy.end_users(led, s.end_user_key) if end_user is not None else None
    return privacy.erase(
        led,
        client=args.client,
        end_user_raw=end_user,
        hashes=args.hashes,
        contains=args.contains,
        actor=args.actor,
        reason=args.reason,
        dry_run=not args.apply,
        shared=s.retention().erasure_shared,
        hasher=hasher,
        end_user_clients=clients,
    )


def run_export(led: Ledger, args: argparse.Namespace, out: TextIO) -> int:
    filters = privacy.check_export(args.kind, task=args.task, split=args.split, since=args.since, client=args.client)
    n = 0
    for line in privacy.export_lines(led, args.kind, **filters):
        out.write(line)
        n += 1
    return n


def main(argv: list[str] | None = None, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> int:
    args = parser().parse_args(argv)
    s = Settings(mode="live")
    led = Ledger(args.db or s.db_path, s.golden_fraction, auto_day=True)
    try:
        if args.cmd == "erase":
            try:
                result = run_erase(led, s, args, stdin)
            except ValueError as e:
                raise SystemExit(f"erase: {e}") from None
            stdout.write(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
            if result["dry_run"]:
                stdout.write("dry run: nothing changed; add --apply to erase\n")
            return 0
        try:
            if args.out:
                with Path(args.out).open("w", encoding="utf-8") as f:
                    n = run_export(led, args, f)
                print(f"wrote {n} {args.kind} rows to {args.out}", file=sys.stderr)
            else:
                run_export(led, args, stdout)
        except ValueError as e:
            raise SystemExit(f"export: {e}") from None
        return 0
    finally:
        led.db.close()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
