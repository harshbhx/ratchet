"""Export the reference data the docs site renders, straight from the code, so the reference pages cannot drift.

    python docs-site/scripts/export-reference.py           # write the files below
    python docs-site/scripts/export-reference.py --check   # exit 1 if a committed file is out of date

Writes (all committed, so building the site needs no Python):
    docs-site/openapi/ratchet.json     the gateway's OpenAPI schema (`app.openapi()` in mock mode), with tags by area
    docs-site/generated/env.json       every environment variable the package and the deploy kit read (ratchet/config.py)
    docs-site/generated/cli.json       every command line tool with its --help output, and the Make targets
    docs-site/generated/routes.json    routes the schema leaves out (`include_in_schema=False`, static mounts)

Run it from anywhere after changing a route, a setting or a CLI; the docs build then renders the new data. It imports
the gateway in mock mode (no keys, no network) and runs each CLI's --help in a temporary folder.

The --help text is argparse's, and argparse wraps usage lines differently from one Python version to the next, so the
committed files are written with the Python the `docs` CI job uses (3.12). Export with that version or --check fails.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

SITE = Path(__file__).resolve().parents[1]
REPO = SITE.parent
OPENAPI_OUT = SITE / "openapi" / "ratchet.json"
GENERATED = SITE / "generated"
DOCS_URL = "https://docs.ratchet.dualmindlab.tech"
REPO_URL = "https://github.com/harshbhx/ratchet"

# The OpenAPI tags, in the order the site lists them: (tag, description, path predicate).
TAGS: list[tuple[str, str]] = [
    ("OpenAI-compatible", "Chat completions and models: the routes your app calls. Any OpenAI SDK works."),
    ("End users and feedback", "Opt-outs, erasure of an end user's examples, and feedback on served answers."),
    ("Health and metrics", "Liveness, readiness and Prometheus metrics."),
    ("Stats and provenance", "The dashboard's data: stats, the bill of materials, request lookups and the redactor."),
    ("Versions and evidence", "Per-version bills of materials, certificates and committed training sets."),
    ("Cost", "Savings per token and by GPU-hours paid, and endpoint uptime records."),
    ("Ledger replay", "Past traffic at another threshold or audit rate."),
    ("Savings receipts", "Signed daily receipts per client and their inclusion proofs."),
    ("Tasks", "The task registry: list, inspect, validate, add and remove tasks."),
    ("Admin", "Operator actions with an audit trail: versions, switches, pins, erasure and export."),
    ("Mock-mode controls (mock only)", "Buttons of the mock-mode gateway. They answer 404 in live mode."),
    ("Scout", "The multi-agent research tool: runs, the event stream and the budget guard's status."),
    ("Pages", "The web pages the gateway serves."),
]


def tag_for(path: str) -> str:
    p = path
    if p.startswith("/v1/ratchet/") or p.startswith("/openai/v1/ratchet/"):
        return "End users and feedback"
    if p.startswith("/v1/") or p.startswith("/openai/"):
        return "OpenAI-compatible"
    if p in ("/healthz", "/livez", "/readyz", "/metrics"):
        return "Health and metrics"
    if p.startswith("/ratchet/admin/"):
        return "Admin"
    if p.startswith("/ratchet/demo/"):
        return "Mock-mode controls (mock only)"
    if p.startswith("/ratchet/versions/"):
        return "Versions and evidence"
    if p.startswith("/ratchet/cost"):
        return "Cost"
    if p.startswith("/ratchet/replay"):
        return "Ledger replay"
    if p.startswith("/ratchet/receipts"):
        return "Savings receipts"
    if p.startswith("/ratchet/tasks"):
        return "Tasks"
    if p.startswith("/scout/"):
        return "Scout"
    if p.startswith("/ratchet/"):
        return "Stats and provenance"
    return "Pages"


def version() -> str:
    import tomllib  # Python 3.11+; ruff targets 3.10, where it would sort as third party  # noqa: PLC0415

    with open(REPO / "pyproject.toml", "rb") as f:
        return tomllib.load(f)["project"]["version"]


def import_gateway() -> Any:
    os.environ["RATCHET_MODE"] = "mock"
    for name in ("RATCHET_MOCK_WARMUP_DAYS", "RATCHET_DB", "NEBIUS_API_KEY", "TAVILY_API_KEY"):
        os.environ.pop(name, None)
    sys.path.insert(0, str(REPO))
    from ratchet import gateway  # noqa: PLC0415 - after the environment is set

    return gateway


def _stable(node: Any) -> None:
    """Drop what newer FastAPI and pydantic versions add to the schema without changing its meaning.

    `additionalProperties: true` is JSON Schema's default for an object, and ValidationError's optional `input` and
    `ctx` are newer additions. Without this, `--check` fails on any machine whose versions differ from the ones the
    committed file was written with.
    """
    if isinstance(node, dict):
        if node.get("additionalProperties") is True:
            del node["additionalProperties"]
        if node.get("title") == "ValidationError" and isinstance(node.get("properties"), dict):
            node["properties"].pop("input", None)
            node["properties"].pop("ctx", None)
        for value in node.values():
            _stable(value)
    elif isinstance(node, list):
        for value in node:
            _stable(value)


METHODS = ["get", "put", "post", "delete", "options", "head", "patch", "trace"]  # the order OpenAPI lists them in


def _own_operation_ids(schema: dict[str, Any]) -> None:
    """Give each method of a multi-method route its own operationId, in a fixed order.

    `app.api_route(path, methods=[...])` makes FastAPI name every method after one of them (`list(methods)[0]`) and list
    the methods in set order, which changes with the hash seed. The result is a duplicate operationId and a file that
    differs from run to run, so `--check` would fail at random.
    """
    paths = schema.get("paths", {})
    count: dict[str, int] = {}
    for item in paths.values():
        for method in METHODS:
            op_id = (item.get(method) or {}).get("operationId")
            if op_id:
                count[op_id] = count.get(op_id, 0) + 1
    for path, item in paths.items():
        if not any(count.get((item.get(m) or {}).get("operationId"), 0) > 1 for m in METHODS):
            continue
        for method in METHODS:
            if method in item and item[method].get("operationId") in count and count[item[method]["operationId"]] > 1:
                item[method]["operationId"] = re.sub(r"_[a-z]+$", f"_{method}", item[method]["operationId"])
        paths[path] = {
            **{m: item[m] for m in METHODS if m in item},
            **{k: v for k, v in item.items() if k not in METHODS},
        }


def export_openapi(gateway: Any) -> tuple[dict[str, Any], list[dict[str, str]]]:
    app = gateway.app
    schema = app.openapi()
    schema = json.loads(json.dumps(schema))  # a copy, so the app's cached schema stays as it was
    _stable(schema)
    _own_operation_ids(schema)
    schema["info"] = {
        "title": "Ratchet gateway",
        "version": version(),
        "description": (
            "The HTTP API of the Ratchet gateway (`ratchet.gateway:app`), generated from the code with "
            "`python docs-site/scripts/export-reference.py`. The chat route speaks OpenAI's format, so any OpenAI SDK "
            f"works. Examples, error envelopes and access levels are in the [hand-written API guide]({DOCS_URL}/reference/api/). "
            "Mock-only controls answer 404 in live mode, and in live mode most `/ratchet/*` routes need an admin or "
            "dashboard key unless the dashboard is public."
        ),
        "license": {"name": "Apache-2.0", "url": f"{REPO_URL}/blob/main/LICENSE"},
    }
    schema["servers"] = [
        {"url": "http://localhost:8000", "description": "Local mock mode (make demo)"},
        {
            "url": "https://{host}",
            "description": "Your gateway (the live gateway's host by default)",
            "variables": {"host": {"default": "ratchet.dualmindlab.tech"}},
        },
    ]
    used: set[str] = set()
    for path, item in schema.get("paths", {}).items():
        for method, op in item.items():
            if not isinstance(op, dict) or method not in ("get", "post", "put", "patch", "delete"):
                continue
            tag = tag_for(path)
            op["tags"] = [tag]
            used.add(tag)
    schema["tags"] = [{"name": n, "description": d} for n, d in TAGS if n in used]

    hidden: list[dict[str, str]] = []
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = sorted(getattr(route, "methods", None) or [])
        if getattr(route, "include_in_schema", True) is False:
            hidden.append({"path": path, "methods": ",".join(methods), "kind": "route"})
        elif route.__class__.__name__ == "Mount":
            hidden.append({"path": path + "/*", "methods": "GET", "kind": "static files"})
    return schema, hidden


def export_env() -> dict[str, Any]:
    from ratchet import config  # noqa: PLC0415
    from ratchet.release import policy_env  # noqa: PLC0415

    return {
        "source": "ratchet/config.py (KNOWN_ENV, DEPLOY_ENV, MOCK_ONLY_ENV, TOOL_ENV, RESERVED_SETTINGS) and ratchet/release.py",
        "package": sorted(config.KNOWN_ENV),
        "deploy": sorted(config.DEPLOY_ENV),
        "release": sorted(policy_env()),
        "mock_only": dict(sorted(config.MOCK_ONLY_ENV.items())),
        "tool": sorted(config.TOOL_ENV),
        "reserved": {env: {"default": d, "feature": f} for env, d, f in sorted(config.RESERVED_SETTINGS.values())},
    }


# The command line tools, with a "when to use it" line each. The help text comes from running the tool.
CLIS: list[dict[str, Any]] = [
    {
        "id": "live",
        "cmd": "python -m ratchet.live",
        "doc": "ratchet/live.py",
        "subs": [],
        "when": (
            "Live mode on Nebius: run Night School (night), advance the release gate (tick), "
            "reload adapters after an endpoint restart (reload), seal savings receipts (seal), "
            "apply retention now (purge) or export an adapter with its licenses (export)."
        ),
    },
    {
        "id": "live-export",
        "cmd": "python -m ratchet.live export",
        "parser": "ratchet.live:export_parser",
        "when": "Hand an adapter to someone else with its bill of materials and NOTICE.md.",
    },
    {
        "id": "live-purge",
        "cmd": "python -m ratchet.live purge",
        "parser": "ratchet.live:purge_parser",
        "when": "Apply the retention windows now instead of waiting for the gateway's hourly run.",
    },
    {
        "id": "config",
        "cmd": "python -m ratchet.config",
        "module": "ratchet.config",
        "when": "Check the environment before you start or update a gateway: exit 2 means the gateway would not start.",
    },
    {
        "id": "scout",
        "cmd": "python -m ratchet.scout",
        "module": "ratchet.scout",
        "when": "Run Scout from a terminal against any gateway, as an outside client would.",
    },
    {
        "id": "cost",
        "cmd": "python -m ratchet.cost",
        "module": "ratchet.cost",
        "when": "Print the cost report (savings per token and by GPU-hours paid) and record when the student endpoint was up.",
    },
    {
        "id": "bom",
        "cmd": "python -m ratchet.bom",
        "module": "ratchet.bom",
        "when": "Make a signing key, and verify an adapter's signed bill of materials and its NOTICE.md offline.",
    },
    {
        "id": "stats",
        "cmd": "python -m ratchet.stats",
        "module": "ratchet.stats",
        "when": "Plan how many golden rows a certificate needs, compute exact bounds, and recheck every number in a signed BOM.",
    },
    {
        "id": "proof",
        "cmd": "python -m ratchet.proof",
        "module": "ratchet.proof",
        "when": (
            "Check offline that a request was, or was not, in an adapter's training set, and that no golden row was trained on."
        ),
    },
    {
        "id": "receipt",
        "cmd": "python -m ratchet.receipt",
        "module": "ratchet.receipt",
        "when": "Verify a savings receipt, its chain and an inclusion proof offline, or seal a day from a ledger file.",
    },
    {
        "id": "replay",
        "cmd": "python -m ratchet.replay",
        "module": "ratchet.replay",
        "when": "Ask what past traffic would have cost and served at another threshold or audit rate, from a ledger file.",
    },
    {
        "id": "admin",
        "cmd": "python -m ratchet.admin",
        "module": "ratchet.admin",
        "when": "Operator actions from a shell, with an audit trail: erase an end user's data or export the ledger's tables.",
    },
    {
        "id": "importer",
        "cmd": "python -m ratchet.importer",
        "module": "ratchet.importer",
        "when": "Import labelled examples (human labels or an allowed teacher's answers) from a JSONL file.",
    },
    {
        "id": "liverun",
        "cmd": "python -m ratchet.liverun",
        "module": "ratchet.liverun",
        "when": (
            "Measure a live run: rounds of synthetic traffic with Night School between them "
            "and a control arm sent straight to the teacher."
        ),
    },
    {
        "id": "watch",
        "cmd": "python -m ratchet.watch",
        "module": "ratchet.watch",
        "when": (
            "On the VM's host, from cron: restart a hung gateway, probe it through its public URL "
            "and send signed alerts. Not needed to run a gateway."
        ),
    },
    {
        "id": "demo-check",
        "cmd": "python scripts/demo_check.py",
        "script": "scripts/demo_check.py",
        "when": "Check a live gateway from the outside, as a visitor sees it: OK, WARN or FAIL per check.",
    },
    {
        "id": "train",
        "cmd": "python -m ratchet.nightschool.train",
        "module": "ratchet.nightschool.train",
        "when": (
            "Inside the training job's container only: fine-tune a LoRA adapter on train.jsonl. Night School starts it for you."
        ),
    },
    {
        "id": "simulate",
        "cmd": "python -m sim.simulate",
        "module": "sim.simulate",
        "when": (
            "Run the whole loop offline with mock models (simulation): "
            "days of traffic, nights of training, releases and rollbacks."
        ),
    },
    {
        "id": "day1",
        "cmd": "python scripts/day1_check.py",
        "doc": "scripts/day1_check.py",
        "when": (
            "Before live mode: check the model IDs, logprobs and JSON mode on Token Factory with a real key (costs a few cents)."
        ),
    },
    {
        "id": "reference-check",
        "cmd": "python scripts/reference_check.py",
        "script": "scripts/reference_check.py",
        "when": "Check (or rewrite) the pinned reference runs that hold the simulation's behaviour still.",
    },
    {
        "id": "regen-sim",
        "cmd": "python scripts/regen_sim_reference.py",
        "script": "scripts/regen_sim_reference.py",
        "when": "Regenerate (or check) the seed-7 reference simulation fixture the web app's numbers are pinned to.",
    },
]

# Subcommands as argparse prints them in the usage line: `{report,endpoint,close} ...` (choices of an option have no `...`).
# A long usage line wraps before the `...` on some Python versions, so any whitespace may sit between the two.
SUB_RE = re.compile(r"\{([a-z0-9_,-]+)\}\s+\.\.\.")


def one_line_usage(text: str) -> str:
    """Join the usage paragraph into one line: argparse wraps it differently from one Python version to the next."""
    head, sep, rest = text.partition("\n\n")
    return re.sub(r"\s*\n\s+", " ", head) + sep + rest


def run_help(args: list[str], cwd: str) -> tuple[int, str]:
    env = {**os.environ, "RATCHET_MODE": "mock", "COLUMNS": "100", "PYTHONPATH": str(REPO), "PYTHONIOENCODING": "utf-8"}
    for name in ("NEBIUS_API_KEY", "TAVILY_API_KEY", "RATCHET_DB"):
        env.pop(name, None)
    p = subprocess.run(
        [sys.executable, *args, "--help"], cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120
    )
    return p.returncode, one_line_usage((p.stdout or p.stderr).replace("\r\n", "\n").rstrip())


def docstring(rel: str) -> str:
    tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
    return (ast.get_docstring(tree) or "").rstrip()


def parser_help(spec: str) -> str:
    mod, fn = spec.split(":")
    sys.path.insert(0, str(REPO))
    module = __import__(mod, fromlist=[fn])
    os.environ["COLUMNS"] = "100"
    return getattr(module, fn)().format_help().rstrip()


def export_cli() -> dict[str, Any]:
    out: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as tmp:
        for c in CLIS:
            entry = {k: c[k] for k in ("id", "cmd", "when")}
            if "doc" in c:
                entry["help"] = docstring(c["doc"])
                entry["help_from"] = f"the docstring of {c['doc']}"
            elif "parser" in c:
                entry["help"] = parser_help(c["parser"])
                entry["help_from"] = "--help"
            else:
                args = ["-m", c["module"]] if "module" in c else [str(REPO / c["script"])]
                code, text = run_help(args, tmp)
                # A parser without `prog` prints the file name (`usage: __main__.py`); show the command people type.
                text = re.sub(r"^usage: [\w.-]+\.py\b", f"usage: {c['cmd']}", text)
                entry["help"] = text
                entry["help_from"] = "--help" if code == 0 else f"--help (exit {code})"
                subs = SUB_RE.search(text.split("\n\n")[0] if text else "")
                entry["subcommands"] = []
                if subs and code == 0:
                    for sub in subs.group(1).split(","):
                        sc, st = run_help([*args, sub], tmp)
                        st = re.sub(r"^usage: [\w.-]+\.py\b", f"usage: {c['cmd']}", st)
                        entry["subcommands"].append({"name": sub, "help": st, "ok": sc == 0})
            out.append(entry)
    return {"source": "docs-site/scripts/export-reference.py", "commands": out, "make": make_targets()}


def make_targets() -> list[dict[str, str]]:
    """Each Make target with its comment (the lines just above it) and its recipe."""
    lines = (REPO / "Makefile").read_text(encoding="utf-8").splitlines()
    targets: list[dict[str, str]] = []
    comment: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^([A-Za-z0-9_.-]+):(?!=)(.*)$", line)
        if line.startswith("#"):
            comment.append(line.lstrip("# ").rstrip())
        elif m and not line.startswith(".PHONY"):
            recipe: list[str] = []
            j = i + 1
            while j < len(lines) and lines[j].startswith("\t"):
                recipe.append(lines[j].strip())
                j += 1
            if m.group(1) == "trace":  # the maintainers' requirements matrix, not part of the public docs
                comment, i = [], j
                continue
            targets.append({"name": m.group(1), "comment": " ".join(comment).strip(), "recipe": "\n".join(recipe)})
            comment = []
            i = j
            continue
        elif not line.strip():
            comment = []
        i += 1
    return targets


REF = r"(?:ADR|N?FR|INV|SM)[- ]?[\d.]+(?: to (?:ADR|N?FR|INV|SM)[- ]?[\d.]+)?"
REF_PAREN = re.compile(rf"\s*\((?:see )?(?:{REF}(?:,\s*|\s+and\s+|;\s*)?)+\)")
REF_TAIL = re.compile(rf"\s*[,;]\s*{REF}")
REF_HEAD = re.compile(rf"(?<=\()(?:{REF}[,;]\s*)+")


def public_text(text: str) -> str:
    """Drop the pointers to requirement ids, decision records and the product spec from text the code wrote for
    maintainers ("(FR-16.4, ADR 0027)", "; ADR 0017)"). The site publishes none of them."""
    text = REF_PAREN.sub("", text)
    text = REF_TAIL.sub("", text)
    text = REF_HEAD.sub("", text)
    text = text.replace("NFR-1's latency target", "the latency target").replace(
        "SM-1 to SM-3 and NFR-1", "The first three metrics and the latency target"
    )
    text = text.replace("PRD §8.1's success metrics", "The success metrics")
    return re.sub(r"\(\s*\)", "", text)


def scrub(node: Any) -> Any:
    """`public_text` on every string of a JSON-like value (keys and identifiers are left alone)."""
    if isinstance(node, str):
        return public_text(node)
    if isinstance(node, dict):
        return {k: (v if k in ("operationId", "$ref", "url") else scrub(v)) for k, v in node.items()}
    if isinstance(node, list):
        return [scrub(v) for v in node]
    return node


def dump(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="exit 1 if a committed file differs from a fresh export")
    args = ap.parse_args(argv)

    gateway = import_gateway()
    schema, hidden = export_openapi(gateway)
    files = {
        OPENAPI_OUT: dump(scrub(schema)),
        GENERATED / "routes.json": dump({"source": "ratchet/gateway.py", "not_in_schema": hidden}),
        GENERATED / "env.json": dump(export_env()),
        GENERATED / "cli.json": dump(scrub(export_cli())),
    }
    stale = []
    for path, text in files.items():
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                stale.append(path.relative_to(REPO).as_posix())
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {path.relative_to(REPO).as_posix()}")
    if stale:
        print("out of date (run python docs-site/scripts/export-reference.py):", *stale, sep="\n  ")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
