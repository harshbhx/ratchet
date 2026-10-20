"""Requirements traceability: PRD rows and CLAUDE.md invariants, mapped to code and tests.

    python scripts/reqtrace.py            # regenerate docs/REQUIREMENTS.md
    python scripts/reqtrace.py --check    # print every problem; exit 1 if there is one (make lint, CI)

Sources of truth:

- docs/PRD.md: requirement ids, text and Status (the FR, NFR and SM tables; the US paragraphs).
- CLAUDE.md, the numbered '## Invariants' list: INV-n is the list number.
- tests/: `@pytest.mark.req("FR-2.1", "INV-7")` on a test, or a module- or class-level `pytestmark`.
- This file: CODE (where each requirement is implemented), EXEMPT (checked by hand or only live)
  and KNOWN_GAPS (done but untested when this check landed; it may only shrink).

Standard library only. Parsing is AST and regex; only the CODE, route and env checks import the
package. It is not called trace.py because that would shadow the standard library module.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import dataclasses
import importlib
import os
import re
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # `python scripts/reqtrace.py` puts scripts/, not the repo, on the path
    sys.path.insert(0, str(ROOT))

PRD = "docs/PRD.md"
CLAUDE = "CLAUDE.md"
TESTS = "tests"
OUT = "docs/REQUIREMENTS.md"
API_DOC = "docs/API.md"
CONFIG_DOC = "docs/CONFIGURATION.md"
# Docs that state the size of the suite ("N tests"). Release history (CHANGELOG, PRD §9, ROADMAP) is not checked.
COUNT_DOCS = (
    "CLAUDE.md",
    "tests/CLAUDE.md",
    "docs/TESTING.md",
    "README.md",
    "docs/DEPLOYMENT.md",
    "docs/HACKATHON_SUBMISSION.md",
)
CLIP = 100  # characters of requirement text in the matrix
# The gateway builds its Runtime at import time; import it as an offline demo with no warm-up.
MOCK_ENV = {"RATCHET_MODE": "mock", "RATCHET_MOCK_WARMUP_DAYS": "0"}

KINDS = ("US", "FR", "NFR", "SM", "INV")  # document order
ID_RE = re.compile(r"FR-\d+\.\d+|NFR-\d+|US-\d+|SM-\d+|INV-\d+")
_ID_LIKE = re.compile(r"(?:FR|NFR|US|SM|INV)\b")

# ---------------------------------------------------------------------------
# Status grammar. Every status must match one pattern exactly; anything else is an error.
# ---------------------------------------------------------------------------
DONE, LIVE_UNVERIFIED, PARTIAL_PLANNED = "DONE", "LIVE_UNVERIFIED", "PARTIAL_PLANNED"
PARTIAL, PLANNED, REPLACED = "PARTIAL", "PLANNED", "REPLACED"
MET_SIM, NOT_MET, UNCHECKED = "MET_SIM", "NOT_MET", "UNCHECKED"
STORY, INVARIANT = "STORY", "INVARIANT"  # US and INV ids have no status column

_V = r"v\d+(?:\.\d+)*"
_FEATURE, _METRIC = frozenset({"FR", "NFR"}), frozenset({"SM"})
STATUS_GRAMMAR: tuple[tuple[re.Pattern[str], frozenset[str], frozenset[str]], ...] = (
    (re.compile(rf"Done \({_V}\)"), frozenset({DONE}), _FEATURE),
    (re.compile(rf"Done \({_V}\), untested \((?:live|on GPU|on Nebius)\)"), frozenset({DONE, LIVE_UNVERIFIED}), _FEATURE),
    (re.compile(rf"Done \({_V}\); .+ planned"), frozenset({DONE, PARTIAL_PLANNED}), _FEATURE),
    (re.compile(r"Partial: .+"), frozenset({PARTIAL}), _FEATURE),
    (re.compile(r"Planned"), frozenset({PLANNED}), _FEATURE),
    (re.compile(r"Replaced by (?:FR-\d+\.\d+|NFR-\d+)"), frozenset({REPLACED}), _FEATURE),
    (re.compile(r"Met in simulation \(.+\)"), frozenset({MET_SIM}), _METRIC),
    (re.compile(r"Not met \(.+\)"), frozenset({NOT_MET}), _METRIC),
    (re.compile(r"Not checked"), frozenset({UNCHECKED}), _METRIC),
)
STATUS_HELP = {
    "FR": "Done (vX) | Done (vX), untested (live|on GPU|on Nebius) | Done (vX); <text> planned | Partial: <text> | "
    "Planned | Replaced by FR-a.b",
    "SM": "Met in simulation (<text>) | Not met (<text>) | Not checked",
}
STATUS_HELP["NFR"] = STATUS_HELP["FR"]


def parse_status(kind: str, status: str) -> tuple[frozenset[str], str | None]:
    """(flags, error). Statuses are matched whole, so a typo is an error instead of a silent 'not done'."""
    if not status:
        return frozenset(), f"has no status (use: {STATUS_HELP.get(kind, '?')})"
    for pattern, flags, kinds in STATUS_GRAMMAR:
        if pattern.fullmatch(status):
            if kind not in kinds:
                return frozenset(), f"status {status!r} is not allowed on {kind} rows (use: {STATUS_HELP.get(kind, '?')})"
            return flags, None
    return frozenset(), f"status {status!r} does not match the grammar (use: {STATUS_HELP.get(kind, '?')})"


# ---------------------------------------------------------------------------
# Requirements
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Req:
    id: str
    kind: str
    section: str
    text: str
    status: str
    flags: frozenset[str] = frozenset()
    line: int = 0
    source: str = PRD

    @property
    def needs_check(self) -> bool:
        """Done, partial and met claims, every user story and every invariant must be backed by evidence."""
        return bool(self.flags & {DONE, PARTIAL, MET_SIM}) or self.kind in ("US", "INV")

    @property
    def replaced_by(self) -> str | None:
        return self.status.removeprefix("Replaced by ") if REPLACED in self.flags else None


@dataclass
class Parsed:
    reqs: list[Req]
    sections: list[str]  # requirement sections in document order, including empty §6.x ones
    errors: list[str]


_HEADING = re.compile(r"^(#{2,3})\s+(.*\S)\s*$")
_SEPARATOR = re.compile(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
_STORY = re.compile(r"^\*\*(US-[^.\s*]*)\.\s+(.+?)\*\*\s*(.*)$")
_FUNCTIONAL = re.compile(r"^6\.(\d+)\s")


def _cells(row: str) -> list[str]:
    s = row.strip()
    s = s[1:] if s.startswith("|") else s
    s = s[:-1] if s.endswith("|") and not s.endswith("\\|") else s
    return [c.strip() for c in re.split(r"(?<!\\)\|", s)]


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _outside_fences(lines: list[str]) -> list[str]:
    """Blank out fenced code blocks, keeping line numbers, so an example table or list is never parsed as a requirement."""
    out, fenced = [], False
    for line in lines:
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            out.append("")
        else:
            out.append("" if fenced else line)
    return out


def parse_prd(path: Path, root: Path = ROOT) -> Parsed:
    """FR, NFR and SM rows come from tables with an ID column; US ids from the bold story paragraphs."""
    rel = _rel(path, root)
    if not path.exists():
        return Parsed([], [], [f"{rel}: not found"])
    lines = _outside_fences(path.read_text(encoding="utf-8").splitlines())
    reqs: list[Req] = []
    sections: list[str] = []
    errors: list[str] = []
    section = ""

    def add(req: Req) -> None:
        reqs.append(req)
        if req.section not in sections:
            sections.append(req.section)

    i = 0
    while i < len(lines):
        line = lines[i]
        heading = _HEADING.match(line)
        if heading:
            section = heading.group(2)
            if _FUNCTIONAL.match(section) and section not in sections:
                sections.append(section)
            i += 1
            continue
        if line.startswith("|"):
            start = i
            while i < len(lines) and lines[i].startswith("|"):
                i += 1
            _parse_table(lines[start:i], start + 1, section, rel, add, errors)
            continue
        story = _STORY.match(line)
        if story:
            rid, title, rest = story.groups()
            if ID_RE.fullmatch(rid):
                add(Req(rid, "US", section, f"{title} {rest}".strip(), "", frozenset({STORY}), i + 1, rel))
            else:
                errors.append(f"{rel}:{i + 1}: malformed requirement id {rid!r}")
        i += 1
    return Parsed(reqs, sections, errors)


def _parse_table(
    block: list[str], first_line: int, section: str, rel: str, add: Callable[[Req], None], errors: list[str]
) -> None:
    if len(block) < 2 or not _SEPARATOR.match(block[1].strip()):
        return
    cols = [c.lower() for c in _cells(block[0])]
    no_status_reported = False
    for offset, row in enumerate(block[2:], start=2):
        lineno = first_line + offset
        cells = _cells(row)
        rid = cells[0].strip("*` ") if cells else ""
        if not _ID_LIKE.match(rid):
            continue  # goals, risks, questions and other tables without requirement ids
        if not ID_RE.fullmatch(rid):
            errors.append(f"{rel}:{lineno}: malformed requirement id {rid!r} (use FR-a.b, NFR-n, US-n, SM-n or INV-n)")
            continue
        if len(cells) != len(cols):
            errors.append(f"{rel}:{lineno}: {rid} has {len(cells)} cells but the header has {len(cols)}")
            continue
        row_by = dict(zip(cols, cells, strict=True))
        kind = rid.split("-")[0]
        if kind in ("US", "INV"):
            errors.append(f"{rel}:{lineno}: {rid}: {kind} ids are not defined in tables")
            continue
        if kind == "SM":
            text = f"{row_by.get('metric', '')}: {row_by.get('target', '')}"
        elif kind == "NFR":
            text = f"{row_by.get('area', '')}: {row_by.get('requirement', '')}"
        else:
            text = row_by.get("requirement", "")
        status = row_by.get("status")
        flags: frozenset[str] = frozenset()
        if status is None:
            if not no_status_reported:
                errors.append(f"{rel}:{lineno}: the table with {rid} has no Status column")
                no_status_reported = True
            status = ""
        else:
            flags, err = parse_status(kind, status)
            if err:
                errors.append(f"{rel}:{lineno}: {rid}: {err}")
        fr = re.match(r"FR-(\d+)\.", rid)
        if fr and not section.startswith(f"6.{fr.group(1)} "):
            errors.append(f"{rel}:{lineno}: {rid} belongs in section 6.{fr.group(1)}, not under {section!r}")
        add(Req(rid, kind, section, text, status, flags, lineno, rel))


def parse_invariants(path: Path, root: Path = ROOT) -> Parsed:
    """INV-n is the number of the n-th item of the '## Invariants' list, so invariants are never renumbered."""
    rel = _rel(path, root)
    if not path.exists():
        return Parsed([], [], [f"{rel}: not found"])
    lines = _outside_fences(path.read_text(encoding="utf-8").splitlines())
    start = next((i for i, line in enumerate(lines) if line.startswith("## Invariants")), None)
    if start is None:
        return Parsed([], [], [f"{rel}: no '## Invariants' section"])
    section = lines[start][3:].strip()
    items: list[list] = []  # [number, text, line]
    for i in range(start + 1, len(lines)):
        line = lines[i]
        if line.startswith("## "):
            break
        m = re.match(r"^(\d+)\.\s+(.*)$", line)
        if m:
            items.append([int(m.group(1)), m.group(2).strip(), i + 1])
        elif items and line[:1] in (" ", "\t") and line.strip():
            items[-1][1] += " " + line.strip()
    reqs, errors, expected = [], [], 1
    for number, text, lineno in items:
        if number != expected:
            errors.append(f"{rel}:{lineno}: invariant numbered {number}, expected {expected} (INV-n is the list number)")
        expected = number + 1
        reqs.append(Req(f"INV-{number}", "INV", section, text, "", frozenset({INVARIANT}), lineno, rel))
    if not items:
        errors.append(f"{rel}:{start + 1}: the '## Invariants' section has no numbered items")
    return Parsed(reqs, [section] if reqs else [], errors)


# ---------------------------------------------------------------------------
# Tests: @pytest.mark.req markers, xfail and parametrize, read with ast
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TestInfo:
    __test__ = False  # not a pytest class

    nodeid: str  # tests/<file>::<Class>::<func>
    ids: tuple[str, ...]
    xfail: bool
    strict_xfail: bool
    cases: int | None  # parametrize-expanded count; None when it cannot be read statically
    line: int
    skip: bool = False  # an unconditional @pytest.mark.skip

    @property
    def proves(self) -> bool:
        """An expected failure or a skipped test is collected but proves nothing."""
        return not (self.xfail or self.skip)


@dataclass
class TestScan:
    __test__ = False

    tests: list[TestInfo]
    errors: list[str]

    def by_id(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for t in self.tests:
            for rid in t.ids:
                out.setdefault(rid, []).append(t.nodeid)
        return out

    def counts(self) -> dict[str, int | None]:
        """Collected tests per file, as pytest counts them (each parametrize case is one test)."""
        out: dict[str, int | None] = {}
        for t in self.tests:
            path = t.nodeid.split("::")[0]
            have = out.get(path, 0)
            out[path] = None if have is None or t.cases is None else have + t.cases
        return out


@dataclass
class _Marks:
    ids: list[str] = field(default_factory=list)
    xfail: bool = False
    strict: bool = False
    skip: bool = False
    factor: int | None = 1
    fixture: bool = False


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else ""
    return ""


def _mark_exprs(value: ast.expr) -> list[ast.expr]:
    return list(value.elts) if isinstance(value, (ast.List, ast.Tuple)) else [value]


def _pytestmark(body: list[ast.stmt]) -> list[ast.expr]:
    exprs: list[ast.expr] = []
    for node in body:  # the last assignment wins, as in pytest
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        else:
            continue
        if value is not None and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in targets):
            exprs = _mark_exprs(value)
    return exprs


def _param_count(call: ast.Call, consts: Mapping[str, ast.expr]) -> int | None:
    values = call.args[1] if len(call.args) > 1 else next((k.value for k in call.keywords if k.arg == "argvalues"), None)
    if isinstance(values, ast.Name):
        values = consts.get(values.id)
    if isinstance(values, (ast.List, ast.Tuple)) and not any(isinstance(e, ast.Starred) for e in values.elts):
        return max(1, len(values.elts))  # an empty parameter set still collects one (skipped) test
    return None


def _read_marks(exprs: Sequence[ast.expr], consts: Mapping[str, ast.expr], where: str, errors: list[str]) -> _Marks:
    m = _Marks()
    for expr in exprs:
        call = expr if isinstance(expr, ast.Call) else None
        name = _dotted(call.func if call is not None else expr)
        here = f"{where}:{expr.lineno}"
        if name == "pytest.mark.req":
            if call is None or not call.args or call.keywords:
                errors.append(f"{here}: write @pytest.mark.req('FR-a.b', ...) with one or more ids and no keywords")
                continue
            for arg in call.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    if arg.value not in m.ids:
                        m.ids.append(arg.value)
                else:
                    errors.append(f"{here}: @pytest.mark.req takes string literals only, so this check can read it")
        elif name.split(".")[-1] == "req" and "mark" in name.split("."):
            errors.append(f"{here}: write the marker as @pytest.mark.req(...) so this check can read it")
        elif name == "pytest.mark.xfail":
            m.xfail = True
            m.strict = call is not None and any(
                k.arg == "strict" and isinstance(k.value, ast.Constant) and k.value.value is True for k in call.keywords
            )
        elif name == "pytest.mark.skip":
            m.skip = True
        elif name == "pytest.mark.parametrize" and call is not None:
            n = _param_count(call, consts)
            m.factor = None if n is None or m.factor is None else m.factor * n
        elif name == "pytest.fixture":
            m.fixture = True
    return m


def _combine(chain: Sequence[_Marks]) -> _Marks:
    """`chain` runs from the module down to the function. The closest xfail mark decides strictness, as in pytest."""
    out = _Marks()
    for m in chain:
        out.ids += [i for i in m.ids if i not in out.ids]
        out.skip = out.skip or m.skip
        out.factor = None if out.factor is None or m.factor is None else out.factor * m.factor
    for m in reversed(chain):
        if m.xfail:
            out.xfail, out.strict = True, m.strict
            break
    return out


def scan(tests_dir: Path, root: Path = ROOT) -> TestScan:
    """Collect test functions the way pytest does by default: test_*.py files, test* functions, Test* classes."""
    tests: list[TestInfo] = []
    errors: list[str] = []
    for path in sorted(tests_dir.rglob("test_*.py")):
        rel = _rel(path, root)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        except SyntaxError as e:
            errors.append(f"{rel}:{e.lineno}: cannot parse: {e.msg}")
            continue
        consts: dict[str, ast.expr] = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                consts[node.targets[0].id] = node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
                consts[node.target.id] = node.value
        module = _read_marks(_pytestmark(tree.body), consts, rel, errors)
        _scan_body(tree.body, [module], rel, consts, rel, tests, errors)
    return TestScan(tests, errors)


def _scan_body(
    body: list[ast.stmt],
    chain: list[_Marks],
    prefix: str,
    consts: Mapping[str, ast.expr],
    rel: str,
    tests: list[TestInfo],
    errors: list[str],
) -> None:
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            own = _read_marks(node.decorator_list, consts, rel, errors)
            if own.fixture:
                continue
            m = _combine([*chain, own])
            nodeid = f"{prefix}::{node.name}"
            tests.append(TestInfo(nodeid, tuple(m.ids), m.xfail, m.strict and m.xfail, m.factor, node.lineno, m.skip))
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            if any(isinstance(n, ast.FunctionDef) and n.name == "__init__" for n in node.body):
                continue  # pytest does not collect classes with __init__
            cls = _read_marks([*node.decorator_list, *_pytestmark(node.body)], consts, rel, errors)
            _scan_body(node.body, [*chain, cls], f"{prefix}::{node.name}", consts, rel, tests, errors)


def scan_tests(tests_dir: Path, root: Path = ROOT) -> dict[str, list[str]]:
    """Requirement id -> node ids of the tests that cite it."""
    return scan(tests_dir, root).by_id()


# ---------------------------------------------------------------------------
# Registries: where each requirement lives, what is checked outside pytest, and the known gaps
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Exempt:
    """A requirement checked outside the offline suite: 'manual' (by hand, e.g. UI) or 'live' (needs Nebius)."""

    kind: str  # "manual" | "live"
    evidence: str  # repo path of the script, workflow or doc that holds the check
    reason: str


# 'pkg.mod:Qual.name' (imported and resolved with getattr) or a repo path for non-Python parts (UI, shell, Docker).
CODE: dict[str, tuple[str, ...]] = {
    # 6.1 Gateway
    "FR-1.1": ("ratchet.gateway:chat",),
    "FR-1.2": ("ratchet.gateway:models",),
    "FR-1.3": ("ratchet.gateway:chat", "ratchet.tasks:get"),
    "FR-1.5": ("ratchet.gateway:chat", "ratchet.openai_compat:validate_chat_request"),
    "FR-1.6": ("ratchet.gateway:chat", "ratchet.gateway:_chunks"),
    "FR-1.7": (
        "ratchet.auth:KeyRing",
        "ratchet.gateway:_client",
        "ratchet.gateway:_quota",
        "ratchet.ledger:Ledger.stats_by_client",
    ),
    "FR-1.8": ("ratchet.router:Ratchet.handle", "ratchet.gateway:chat", "ratchet.gateway:_map_upstream"),
    "FR-1.9": ("ratchet.gateway:landing", "ratchet.gateway:scout_page", "ratchet.gateway:dashboard"),
    "FR-1.10": (
        "ratchet.openai_compat:validate_chat_request",
        "ratchet.openai_compat:normalize_messages",
        "ratchet.openai_compat:APIError",
        "ratchet.gateway:chat",
        "ratchet.gateway:BodyLimitMiddleware",
        "ratchet.gateway:_http_error",
        "ratchet.gateway:_unhandled_error",
        "ratchet.router:Ratchet.teacher_only",
    ),
    "FR-1.11": (
        "ratchet.resilience:retry_call",
        "ratchet.resilience:UpstreamError",
        "ratchet.providers:OpenAICompatProvider.complete",
        "ratchet.config:Settings.teacher_retry",
        "ratchet.gateway:_map_upstream",
        "ratchet.gateway:_quota",
    ),
    "FR-1.12": (
        "ratchet.router:Result",
        "ratchet.router:fingerprint",
        "ratchet.router:_usage",
        "ratchet.router:_breakdown",
        "ratchet.providers:Completion",
        "ratchet.openai_compat:usage_object",
        "ratchet.openai_compat:ChatRequest.include_usage",
        "ratchet.gateway:_complete",
        "ratchet.gateway:_envelope",
        "sim.mock_models:MockTeacher",
    ),
    "FR-1.13": (
        "ratchet.providers:GenParams",
        "ratchet.providers:GenPolicy",
        "ratchet.providers:OpenAICompatProvider.complete",
        "ratchet.config:Settings.gen_policy",
        "ratchet.router:Ratchet._ask_teacher",
        "ratchet.router:Ratchet._store_example",
        "ratchet.ledger:example_hash",
        "ratchet.gateway:chat",
        "sim.mock_models:MockTeacher",
    ),
    # 6.2 Cascade and confidence
    "FR-2.1": ("ratchet.router:Ratchet._sample", "ratchet.confidence:STUDENT_SAMPLING"),
    "FR-2.2": ("ratchet.confidence:score",),
    "FR-2.3": ("ratchet.router:Ratchet.threshold", "ratchet.nightschool.evaluate:cascade_eval"),
    "FR-2.4": ("ratchet.router:Ratchet.handle",),
    "FR-2.5": ("ratchet.router:Ratchet.handle",),
    "FR-2.6": ("ratchet.tasks:Task.agree", "ratchet.tasks:Task.canonical"),
    "FR-2.7": ("ratchet.tasks:strip_reasoning", "ratchet.tasks:clean_answer", "ratchet.tasks:parse_json"),
    "FR-2.8": ("ratchet.router:Ratchet._pick_version",),
    "FR-2.9": ("ratchet.router:Ratchet._pinned", "ratchet.tasks:Task"),
    "FR-2.10": (
        "ratchet.router:Ratchet.handle",
        "ratchet.router:Ratchet._arbitrate",
        "ratchet.router:Ratchet.health",
        "ratchet.ledger:Ledger.shadow_quality",
    ),
    "FR-2.11": (
        "ratchet.resilience:Breaker",
        "ratchet.resilience:endpoint_down",
        "ratchet.router:Ratchet._sample",
        "ratchet.router:Ratchet._breaker",
        "ratchet.live:AdapterReloader",
        "ratchet.ledger:Ledger.stats_by_task",
        "ratchet.gateway:demo_student_outage",
        "sim.mock_models:MockFleet.student_outage",
    ),
    "FR-2.12": (
        "ratchet.router:Ratchet._teacher_complete",
        "ratchet.router:Ratchet._serve_teacher",
        "ratchet.router:Ratchet._may_degrade",
        "ratchet.router:Ratchet._store_example",
        "ratchet.resilience:teacher_unavailable",
        "ratchet.resilience:Breaker.retry_in",
        "ratchet.config:Settings.serve_student_on_teacher_failure",
        "ratchet.config:Settings.teacher_breaker",
        "ratchet.gateway:_map_upstream",
        "ratchet.gateway:_complete",
        "ratchet.ledger:Ledger.stats_by_day",
    ),
    "FR-2.13": (
        "ratchet.router:answer_problem",
        "ratchet.router:Ratchet._store_example",
        "ratchet.router:Ratchet.handle",
        "ratchet.confidence:score",
        "ratchet.providers:_completions",
        "ratchet.ledger:SCHEMA",
    ),
    # 6.3 Provenance and licensing
    "FR-3.1": ("ratchet.licenses:REGISTRY", "ratchet.licenses:require_distillable"),
    "FR-3.2": ("ratchet.licenses:CLOSED", "ratchet.licenses:require_distillable"),
    "FR-3.3": ("ratchet.ledger:Ledger.add_example", "ratchet.router:Ratchet._store_example"),
    "FR-3.4": ("ratchet.ledger:Ledger.add_example",),
    "FR-3.5": ("ratchet.ledger:Ledger.bill_of_materials", "ratchet.gateway:bom"),
    "FR-3.6": ("ratchet.bom:document", "ratchet.bom:verify", "ratchet.bom:main", "ratchet.gateway:version_bom"),
    # 6.4 Night School
    "FR-4.1": ("ratchet.nightschool.dataset:rows", "ratchet.nightschool.dataset:build"),
    "FR-4.2": ("ratchet.nightschool.orchestrate:run_night",),
    "FR-4.3": ("ratchet.ledger:Ledger.add_example", "ratchet.nightschool.dataset:rows"),
    "FR-4.4": ("ratchet.nightschool.orchestrate:run_night",),
    "FR-4.5": (
        "ratchet.nightschool.train:main",
        "ratchet.nightschool.train:parse_args",
        "ratchet.nightschool.train:manifest",
        "deploy/Dockerfile.train",
    ),
    "FR-4.6": ("ratchet.live:NebiusTrainer", "deploy/train_job.sh"),
    "FR-4.7": ("ratchet.redact:redact", "ratchet.redact:redact_messages", "ratchet.router:Ratchet._store_example"),
    # 6.5 Evaluation
    "FR-5.1": (
        "ratchet.nightschool.evaluate:cascade_eval",
        "ratchet.nightschool.evaluate:LiveEvaluator.evaluate",
        "ratchet.confidence:STUDENT_SAMPLING",
        "ratchet.providers:GenPolicy.student",
        "ratchet.nightschool.dataset:rows",
        "sim.mock_models:MockFleet.evaluate",
    ),
    "FR-5.2": ("ratchet.nightschool.evaluate:cascade_eval", "ratchet.confidence:calibrate_threshold"),
    "FR-5.3": ("ratchet.nightschool.evaluate:cascade_eval",),
    "FR-5.4": ("ratchet.nightschool.orchestrate:run_night",),
    "FR-5.5": ("ratchet.nightschool.evaluate:LiveEvaluator.judge_score",),
    # 6.6 Release gate
    "FR-6.1": ("ratchet.release:ReleaseGate.admit",),
    "FR-6.2": ("ratchet.release:ReleaseGate.admit",),
    "FR-6.3": ("ratchet.router:Ratchet.handle", "ratchet.ledger:Ledger.shadow_quality"),
    "FR-6.4": ("ratchet.release:ReleaseGate.tick",),
    "FR-6.5": ("ratchet.release:ReleaseGate.tick", "ratchet.release:CANARY_STEPS"),
    "FR-6.6": ("ratchet.release:wilson", "ratchet.release:ReleaseGate.tick"),
    "FR-6.7": ("ratchet.release:ReleaseGate.tick",),
    "FR-6.8": ("ratchet.release:ReleaseGate.admit",),
    # 6.7 Dashboard
    "FR-7.1": ("web/src/pages/dashboard/kpis.tsx", "ratchet.ledger:Ledger.totals"),
    "FR-7.2": ("web/src/pages/dashboard/charts.tsx", "ratchet.ledger:Ledger.stats_by_day"),
    "FR-7.3": ("web/src/pages/dashboard/tables.tsx", "ratchet.ledger:Ledger.versions"),
    "FR-7.4": ("web/src/pages/dashboard/release-log.tsx", "web/src/pages/dashboard/provenance.tsx"),
    "FR-7.5": ("ratchet.gateway:demo_day", "ratchet.gateway:demo_break_next", "ratchet.gateway:demo_reset"),
    "FR-7.6": ("web/src/styles/globals.css", "web/src/components/layout/site-header.tsx"),
    "FR-7.7": ("ratchet.ledger:Ledger.stats_by_task", "web/src/pages/dashboard/tables.tsx"),
    "FR-7.8": ("ratchet.gateway:demo_day", "ratchet.gateway:demo_reset", "web/src/pages/dashboard/controls.tsx"),
    # 6.8 Live operations on Nebius
    "FR-8.1": ("ratchet.providers:OpenAICompatProvider", "ratchet.gateway:Runtime.reset", "ratchet.live:main"),
    "FR-8.2": ("ratchet.live:load_adapter", "deploy/student_endpoint.sh"),
    "FR-8.3": ("ratchet.gateway:Runtime.reset", "ratchet.router:Ratchet.threshold"),
    "FR-8.4": ("ratchet.live:main",),
    "FR-8.5": ("scripts/day1_check.py",),
    # 6.9 Simulation
    "FR-9.1": ("sim.tickets:TicketStream",),
    "FR-9.2": ("sim.mock_models:MockTeacher", "sim.mock_models:MockStudent"),
    "FR-9.3": ("sim.mock_models:MockFleet",),
    "FR-9.4": ("sim.simulate:main",),
    "FR-9.5": ("sim.research:ResearchStream", "sim.world:make_stream"),
    "FR-9.6": ("ratchet.gateway:Runtime.warm_up",),
    # 6.10 Scout
    "FR-10.1": ("ratchet.scout.agents:Scout.run",),
    "FR-10.2": ("ratchet.tasks:REGISTRY",),
    "FR-10.3": ("ratchet.scout.search:TavilySearch", "ratchet.scout.search:MockSearch"),
    "FR-10.4": ("ratchet.gateway:scout_stream", "ratchet.gateway:scout_run"),
    "FR-10.5": ("ratchet.scout.guard:Guard",),
    "FR-10.6": ("ratchet.scout.__main__:main",),
    # 6.11 Demo hosting
    "FR-11.1": ("deploy/vm/create_vm.sh", "deploy/vm/cloud-init.yaml"),
    "FR-11.2": ("deploy/vm/backup.sh", "deploy/vm/update.sh"),
    # 7 Non-functional
    "NFR-1": ("ratchet.router:Ratchet._sample",),
    "NFR-2": (
        "ratchet.router:Ratchet.handle",
        "ratchet.router:Ratchet._teacher_complete",
        "ratchet.resilience:retry_call",
        "ratchet.resilience:Breaker",
        "ratchet.live:AdapterReloader",
        "ratchet.live:reload_adapters",
    ),
    "NFR-3": ("ratchet.router:Ratchet.handle", "ratchet.nightschool.orchestrate:run_night", "ratchet.providers:GenPolicy"),
    "NFR-4": ("ratchet.redact:redact_messages",),
    "NFR-5": ("ratchet.auth:KeyRing.identify", "ratchet.gateway:_quota"),
    "NFR-6": ("ratchet.ledger:Ledger.log_request", "ratchet.ledger:Ledger.event", "ratchet.gateway:stats"),
    "NFR-7": ("ratchet.providers:OpenAICompatProvider",),
    "NFR-8": ("ratchet.release:wilson", "ratchet.nightschool.evaluate:cascade_eval"),
    "NFR-9": ("ratchet.bom:manifest", "LICENSE"),
    "NFR-10": ("scripts/reqtrace.py",),
    # Invariants (CLAUDE.md)
    "INV-1": ("ratchet.licenses:require_distillable",),
    "INV-2": ("ratchet.router:Ratchet._store_example", "ratchet.nightschool.dataset:rows"),
    "INV-3": ("ratchet.nightschool.dataset:rows", "ratchet.nightschool.dataset:FREQUENT"),
    "INV-4": ("ratchet.nightschool.orchestrate:run_night",),
    "INV-5": ("ratchet.nightschool.orchestrate:run_night",),
    "INV-6": ("ratchet.nightschool.evaluate:cascade_eval",),
    "INV-7": ("ratchet.router:Ratchet.handle", "ratchet.gateway:chat", "ratchet.gateway:_map_upstream"),
    "INV-8": ("ratchet.gateway:_mock_only",),
    "INV-9": ("ratchet.config:DEFAULT_PRICES", "scripts/day1_check.py"),
    "INV-10": ("ratchet.router:Ratchet._pinned",),
    "INV-11": ("ratchet.gateway:_scout_precheck", "ratchet.gateway:_run_scout", "ratchet.scout.guard:Guard.admit"),
    "INV-12": ("ratchet.redact:redact_messages", "ratchet.redact:redact_json", "ratchet.router:Ratchet._store_example"),
    "INV-13": ("ratchet.auth:KeyRing.identify",),
    "INV-14": ("ratchet.bom:document", "ratchet.bom:manifest"),
    "INV-15": ("ratchet.router:Ratchet.handle", "ratchet.router:Ratchet._arbitrate"),
    "INV-16": ("ratchet.router:Ratchet._store_example", "ratchet.router:answer_problem", "ratchet.tasks:clean_answer"),
}

_UI = "dashboard UI: checked in light and dark mode at 1440 and 390 px with Playwright; CI typechecks and builds it"
EXEMPT: dict[str, Exempt] = {
    "FR-4.5": Exempt("live", "deploy/Dockerfile.train", "needs a GPU: runs in the training image on a Serverless Job"),
    "FR-4.6": Exempt("live", "deploy/train_job.sh", "needs a Nebius Serverless Job and a bucket"),
    "FR-5.5": Exempt("live", "scripts/day1_check.py", "the judge's JSON mode is checked against Token Factory"),
    "FR-7.6": Exempt("manual", "web/CLAUDE.md", _UI),
    "FR-8.1": Exempt("live", "scripts/day1_check.py", "model ids, logprobs and JSON mode on Token Factory"),
    "FR-8.2": Exempt("live", "deploy/student_endpoint.sh", "needs a vLLM Serverless Endpoint with runtime LoRA loading"),
    "FR-8.4": Exempt("live", "docs/RUNBOOK.md", "runs against the live ledger, a Serverless Job and the student endpoint"),
    "FR-8.5": Exempt("live", "docs/DAY1.md", "the script is the live check; it needs NEBIUS_API_KEY"),
    "FR-10.3": Exempt(
        "live", "docs/DEPLOY_NEBIUS_VM.md", "Tavily needs TAVILY_API_KEY; checked when the demo VM switches to live"
    ),
    "FR-11.1": Exempt("live", "deploy/vm/create_vm.sh", "needs a Nebius project; DRY_RUN=1 prints the commands"),
    "FR-11.2": Exempt("live", "deploy/vm/backup.sh", "runs on the demo VM"),
    "NFR-1": Exempt("live", "docs/DEPLOYMENT.md", "p50 latency needs the real student endpoint"),
    "US-10": Exempt("live", "deploy/train_job.sh", "the adapter reaches the customer's bucket only in live mode"),
    "INV-9": Exempt("live", "scripts/day1_check.py", "model ids and prices are confirmed against Token Factory with a real key"),
}

# Done requirements that had no test when this check landed. Frozen: never add to it.
INITIAL_KNOWN_GAPS: frozenset[str] = frozenset(
    {
        "FR-1.2", "FR-2.1", "FR-2.8", "FR-4.1", "FR-4.2", "FR-4.4", "FR-4.5", "FR-4.6", "FR-5.1", "FR-5.3",
        "FR-5.4", "FR-5.5", "FR-6.3", "FR-6.4", "FR-6.8", "FR-8.2", "FR-8.4", "FR-8.5", "FR-9.6", "FR-10.6",
        "FR-11.1", "FR-11.2", "NFR-1", "NFR-3", "INV-2", "INV-3", "INV-4", "INV-5", "INV-6", "INV-8",
    }
)  # fmt: skip
# The gaps still open. Remove an id when a test marks it; the check fails until you do. All closed: keep it empty.
KNOWN_GAPS: frozenset[str] = frozenset()


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------
@dataclass
class Context:
    root: Path
    reqs: list[Req]
    sections: list[str]
    parse_errors: list[str]
    tests: TestScan
    code: Mapping[str, tuple[str, ...]]
    exempt: Mapping[str, Exempt]
    known_gaps: frozenset[str]
    initial_known_gaps: frozenset[str]
    by_id: dict[str, Req] = field(init=False)
    tests_by_id: dict[str, list[TestInfo]] = field(init=False)

    def __post_init__(self) -> None:
        self.by_id = {}
        for r in self.reqs:
            self.by_id.setdefault(r.id, r)
        self.tests_by_id = {}
        for t in self.tests.tests:
            for rid in t.ids:
                self.tests_by_id.setdefault(rid, []).append(t)

    def unique(self) -> list[Req]:
        return [r for r in self.reqs if self.by_id[r.id] is r]

    def proving(self, rid: str) -> list[TestInfo]:
        """Tests that prove a requirement: an expected failure or a skipped test proves nothing."""
        return [t for t in self.tests_by_id.get(rid, []) if t.proves]


def load(
    root: Path = ROOT,
    *,
    code: Mapping[str, tuple[str, ...]] | None = None,
    exempt: Mapping[str, Exempt] | None = None,
    known_gaps: frozenset[str] | None = None,
    initial_known_gaps: frozenset[str] | None = None,
) -> Context:
    prd = parse_prd(root / PRD, root)
    inv = parse_invariants(root / CLAUDE, root)
    return Context(
        root=root,
        reqs=prd.reqs + inv.reqs,
        sections=prd.sections + inv.sections,
        parse_errors=prd.errors + inv.errors,
        tests=scan(root / TESTS, root),
        code=CODE if code is None else code,
        exempt=EXEMPT if exempt is None else exempt,
        known_gaps=KNOWN_GAPS if known_gaps is None else known_gaps,
        initial_known_gaps=INITIAL_KNOWN_GAPS if initial_known_gaps is None else initial_known_gaps,
    )


@contextlib.contextmanager
def _mock_env() -> Iterator[None]:
    saved = {k: os.environ.get(k) for k in MOCK_ENV}
    os.environ.update(MOCK_ENV)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _import(module: str):
    with _mock_env():
        return importlib.import_module(module)


_REF = re.compile(r"[A-Za-z_][\w.]*:[A-Za-z_][\w.]*")


def resolve(ref: str, root: Path = ROOT) -> str | None:
    """None when the reference points at something real, else why not."""
    if ":" not in ref:
        return None if (root / ref).exists() else "is not a file in the repo"
    if not _REF.fullmatch(ref):
        return "is neither 'pkg.mod:Qual.name' nor a repo path"
    module, _, qual = ref.partition(":")
    try:
        obj = _import(module)
    except Exception as e:  # noqa: BLE001 - report any import failure as a finding
        return f"does not import: {type(e).__name__}: {e}"
    for part in qual.split("."):
        if not hasattr(obj, part):
            return f"does not resolve: no {part!r}"
        obj = getattr(obj, part)
    return None


# ---------------------------------------------------------------------------
# Rules: each returns a list of problems
# ---------------------------------------------------------------------------
def rule_ids_and_statuses(ctx: Context) -> list[str]:
    """(a) Ids are well formed (checked while parsing), unique and in their section; statuses parse."""
    errors = list(ctx.parse_errors)
    first: dict[str, str] = {}
    for r in ctx.reqs:
        here = f"{r.source}:{r.line}"
        if r.id in first:
            errors.append(f"{here}: duplicate id {r.id} (first defined at {first[r.id]})")
            continue
        first[r.id] = here
        target = r.replaced_by
        if target is not None and (target == r.id or target not in ctx.by_id):
            errors.append(f"{here}: {r.id} is replaced by {target}, which is not a requirement")
    return errors


def rule_coverage(ctx: Context) -> list[str]:
    """(b) Every done, partial or met claim, story and invariant has a test, an EXEMPT entry or a known gap."""
    errors = []
    for r in ctx.unique():
        if not r.needs_check or ctx.proving(r.id) or r.id in ctx.exempt or r.id in ctx.known_gaps:
            continue
        label = f"'{r.status}'" if r.status else ("a user story" if r.kind == "US" else "an invariant")
        if ctx.tests_by_id.get(r.id):
            errors.append(f"{r.id} is {label} but only xfail or skipped tests cite it; it needs a passing test")
        else:
            errors.append(
                f"{r.id} is {label} but no test proves it: add @pytest.mark.req({r.id!r}) to the test that does, "
                "or an EXEMPT entry in scripts/reqtrace.py if it can only be checked by hand or live"
            )
    return errors


def rule_registries(ctx: Context) -> list[str]:
    """(c) KNOWN_GAPS only shrinks; known gaps and manual exemptions that now have a test must be removed."""
    errors = []
    for rid in sorted(ctx.known_gaps - ctx.initial_known_gaps):
        errors.append(f"{rid}: KNOWN_GAPS may only shrink (it is not in INITIAL_KNOWN_GAPS); write the test instead")
    for rid in sorted(ctx.known_gaps):
        r = ctx.by_id.get(rid)
        if r is None:
            errors.append(f"KNOWN_GAPS lists {rid}, which is not a requirement")
        elif ctx.proving(rid):
            errors.append(f"{rid}: a test marks it now; remove it from KNOWN_GAPS")
        elif not r.needs_check:
            errors.append(f"{rid}: '{r.status}' needs no test; remove it from KNOWN_GAPS")
        elif rid in ctx.exempt and LIVE_UNVERIFIED not in r.flags:
            errors.append(f"{rid}: its EXEMPT entry covers it; remove it from KNOWN_GAPS")
    for rid, ex in sorted(ctx.exempt.items()):
        r = ctx.by_id.get(rid)
        if r is None:
            errors.append(f"EXEMPT lists {rid}, which is not a requirement")
            continue
        if ex.kind not in ("manual", "live"):
            errors.append(f"{rid}: EXEMPT kind must be 'manual' or 'live', not {ex.kind!r}")
        if not ex.reason.strip():
            errors.append(f"{rid}: EXEMPT entry needs a reason")
        if not (ctx.root / ex.evidence.split("#")[0]).exists():
            errors.append(f"{rid}: EXEMPT evidence {ex.evidence} does not exist")
        if not r.needs_check:
            errors.append(f"{rid}: '{r.status}' needs no check; remove its EXEMPT entry")
        elif ex.kind == "manual" and ctx.proving(rid):
            errors.append(f"{rid}: a test marks it now; remove its manual EXEMPT entry")
    return errors


def rule_markers_known(ctx: Context) -> list[str]:
    """(d) Markers are readable and cite existing, current ids."""
    errors = list(ctx.tests.errors)
    for t in ctx.tests.tests:
        for rid in t.ids:
            r = ctx.by_id.get(rid)
            if r is None:
                errors.append(f"{t.nodeid}: cites {rid!r}, which is not a requirement in {PRD} or {CLAUDE}")
            elif REPLACED in r.flags:
                errors.append(f"{t.nodeid}: cites {rid}, which is '{r.status}'; cite {r.replaced_by} instead")
    return errors


def rule_planned_in_strict_xfail(ctx: Context) -> list[str]:
    """(d) A test for something not built yet must be a strict xfail; an unmeasured metric cannot have tests."""
    errors = []
    for t in ctx.tests.tests:
        for rid in t.ids:
            r = ctx.by_id.get(rid)
            if r is None:
                continue
            if PLANNED in r.flags and not t.strict_xfail:
                errors.append(
                    f"{t.nodeid}: cites {rid}, which is 'Planned'; mark the test @pytest.mark.xfail(strict=True, reason=...) "
                    "or update the PRD status if it is built"
                )
            elif UNCHECKED in r.flags:
                errors.append(
                    f"{t.nodeid}: cites {rid}, which is 'Not checked'; set it to 'Met in simulation (...)' or 'Not met (...)'"
                )
    return errors


def rule_code_refs(ctx: Context) -> list[str]:
    """(e) Every done requirement and every invariant points at its code, and every reference resolves."""
    errors = []
    for rid in sorted(ctx.code):
        r = ctx.by_id.get(rid)
        if r is None:
            errors.append(f"CODE lists {rid}, which is not a requirement")
        elif REPLACED in r.flags:
            errors.append(f"CODE lists {rid}, which is '{r.status}'; move the entry to {r.replaced_by}")
    for r in ctx.unique():
        if (DONE in r.flags or r.kind == "INV") and not ctx.code.get(r.id):
            errors.append(f"{r.id}: no CODE entry in scripts/reqtrace.py; add the function, class or file that implements it")
    for rid, refs in sorted(ctx.code.items()):
        for ref in refs:
            problem = resolve(ref, ctx.root)
            if problem:
                errors.append(f"{rid}: CODE ref {ref!r} {problem}")
    return errors


def rule_live_unverified(ctx: Context) -> list[str]:
    """(f) 'untested (live)' needs both a 'live' EXEMPT entry and an offline test (or a known gap)."""
    errors = []
    for r in ctx.unique():
        if LIVE_UNVERIFIED not in r.flags:
            continue
        ex = ctx.exempt.get(r.id)
        if ex is None or ex.kind != "live":
            errors.append(f"{r.id} is '{r.status}': add a 'live' EXEMPT entry naming the script or doc that checks it live")
        if ex is not None and not ctx.proving(r.id) and r.id not in ctx.known_gaps:
            errors.append(f"{r.id} is '{r.status}': it still needs an offline test (httpx.MockTransport or fakes)")
    return errors


def rule_not_met(ctx: Context) -> list[str]:
    """(g) A metric marked 'Not met' has a strict-xfail test, which fails loudly once the target is reached."""
    errors = []
    for r in ctx.unique():
        if NOT_MET in r.flags and not any(t.strict_xfail for t in ctx.tests_by_id.get(r.id, [])):
            errors.append(f"{r.id} is '{r.status}': add a test of the target marked @pytest.mark.xfail(strict=True)")
    return errors


def rule_generated(ctx: Context) -> list[str]:
    """(h) docs/REQUIREMENTS.md is exactly what render() produces."""
    path = ctx.root / OUT
    if not path.exists():
        return [f"{OUT} is missing; run python scripts/reqtrace.py"]
    have, want = path.read_text(encoding="utf-8").splitlines(), render(ctx).splitlines()
    if have == want:
        return []
    first = next((i for i, (a, b) in enumerate(zip(have, want, strict=False)) if a != b), min(len(have), len(want)))
    return [f"{OUT} is out of date (first difference at line {first + 1}); run python scripts/reqtrace.py and commit it"]


def _norm_path(path: str) -> str:
    return re.sub(r"\{[^}]*\}", "{}", path.split("?")[0])


def api_routes() -> list[tuple[str, str]]:
    """(method, path) of every route the gateway puts in its OpenAPI schema; path parameters become {}."""
    gateway = _import("ratchet.gateway")
    out = set()
    for route in gateway.app.routes:
        if not getattr(route, "include_in_schema", False):
            continue
        for method in (getattr(route, "methods", None) or set()) - {"HEAD", "OPTIONS"}:
            out.add((method, _norm_path(route.path)))
    return sorted(out)


def rule_routes_documented(ctx: Context) -> list[str]:
    """(i) Every route appears in docs/API.md with its method on the same line (the endpoint table or a heading)."""
    doc = ctx.root / API_DOC
    if not doc.exists():
        return [f"{API_DOC}: not found"]
    try:
        routes = api_routes()
    except Exception as e:  # noqa: BLE001 - a broken gateway import is a finding, not a crash
        return [f"cannot list the gateway's routes: {type(e).__name__}: {e}"]
    documented = set()
    for line in doc.read_text(encoding="utf-8").splitlines():
        methods = set(re.findall(r"\b(GET|POST|PUT|PATCH|DELETE)\b", line))
        for span in re.findall(r"`(/[^`\s]*)`", line):
            documented.update((m, _norm_path(span)) for m in methods)
    return [f"{m} {p}: not in {API_DOC}; add it to the endpoint table" for m, p in routes if (m, p) not in documented]


ENV_HELPERS = {"_env", "_int", "_float", "_bool"}  # config.py readers: _env(name, default), _int(name, default), ...


def _env_name(node: ast.AST) -> str | None:
    """The variable name if `node` reads the environment with a literal name."""
    if (
        isinstance(node, ast.Call)
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    ):
        name = _dotted(node.func)
        if name in ENV_HELPERS or name in ("os.getenv", "getenv") or name.endswith("environ.get"):
            return node.args[0].value
    if isinstance(node, ast.Subscript) and _dotted(node.value).endswith("environ"):
        key = node.slice
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            return key.value
    return None


def env_reads(root: Path = ROOT) -> dict[str, str]:
    """Environment variable -> where it is read, for everything under ratchet/."""
    found: dict[str, str] = {}
    for path in sorted((root / "ratchet").rglob("*.py")):
        rel = _rel(path, root)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=rel)):
            name = _env_name(node)
            if name:
                found.setdefault(name, f"{rel}:{node.lineno}")
    for name in getattr(_import("ratchet.config"), "KNOWN_ENV", ()):
        found.setdefault(name, "ratchet/config.py:KNOWN_ENV")
    for f in dataclasses.fields(_import("ratchet.release").ReleasePolicy):  # read as RATCHET_<FIELD> by from_env()
        found.setdefault(f"RATCHET_{f.name.upper()}", "ratchet/release.py:ReleasePolicy.from_env")
    return found


def rule_env_documented(ctx: Context) -> list[str]:
    """(i) Every environment variable the package reads has a row in docs/CONFIGURATION.md."""
    doc = ctx.root / CONFIG_DOC
    if not doc.exists():
        return [f"{CONFIG_DOC}: not found"]
    try:
        reads = env_reads(ctx.root)
    except Exception as e:  # noqa: BLE001 - a syntax or import error is a finding, not a crash
        return [f"cannot list the environment variables the package reads: {type(e).__name__}: {e}"]
    documented = set(re.findall(r"`([A-Z][A-Z0-9_]*)`", doc.read_text(encoding="utf-8")))
    return [
        f"{name}: read at {where} but not documented in {CONFIG_DOC}" for name, where in reads.items() if name not in documented
    ]


def rule_test_counts(ctx: Context) -> list[str]:
    """(i) 'N tests' claims and per-file counts in the docs match the parametrize-expanded suite."""
    errors = [
        f"{t.nodeid}: cannot count its parametrize cases statically; use a literal list"
        for t in ctx.tests.tests
        if t.cases is None
    ]
    if errors:
        return errors
    per_file = {k: v or 0 for k, v in ctx.tests.counts().items()}
    total = sum(per_file.values())
    for doc in COUNT_DOCS:
        path = ctx.root / doc
        if not path.exists():
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            here = f"{doc}:{lineno}"
            for m in re.finditer(r"\b(\d+) tests\b", line):
                if int(m.group(1)) != total:
                    errors.append(f"{here}: says {m.group(1)} tests; the suite has {total}")
            row = re.match(r"^\|\s*`(tests/[\w/]+\.py)`\s*\|\s*(\d+)\b", line)
            if row and per_file.get(row.group(1)) != int(row.group(2)):
                errors.append(f"{here}: says {row.group(1)} has {row.group(2)} tests; it has {per_file.get(row.group(1), 0)}")
            total_row = re.match(r"^\|\s*\*\*Total\*\*\s*\|\s*\*\*(\d+)\*\*", line)
            if total_row and int(total_row.group(1)) != total:
                errors.append(f"{here}: says the total is {total_row.group(1)}; the suite has {total}")
            breakdown = re.search(r"\btests in total \(([^)]*)\)", line)
            for n, word in re.findall(r"(\d+) (\w+)", breakdown.group(1)) if breakdown else ():
                f = f"tests/test_{word.lower()}.py"
                if per_file.get(f) != int(n):
                    errors.append(f"{here}: says {n} {word}; {f} has {per_file.get(f, 0)}")
    return errors


CORE_RULES: tuple[Callable[[Context], list[str]], ...] = (
    rule_ids_and_statuses,
    rule_coverage,
    rule_registries,
    rule_markers_known,
    rule_planned_in_strict_xfail,
    rule_code_refs,
    rule_live_unverified,
    rule_not_met,
)
DOC_RULES: tuple[Callable[[Context], list[str]], ...] = (
    rule_routes_documented,
    rule_env_documented,
    rule_test_counts,
)
RULES = CORE_RULES + (rule_generated,) + DOC_RULES


def check(ctx: Context, rules: Sequence[Callable[[Context], list[str]]] = RULES) -> list[str]:
    return [e for rule in rules for e in rule(ctx)]


# ---------------------------------------------------------------------------
# Rendering: deterministic Markdown, no timestamps
# ---------------------------------------------------------------------------
_CATEGORIES = (
    ("Done", lambda r: r.flags == {DONE}),
    ("Done, untested live", lambda r: LIVE_UNVERIFIED in r.flags),
    ("Done, more planned", lambda r: PARTIAL_PLANNED in r.flags),
    ("Partial", lambda r: PARTIAL in r.flags),
    ("Planned", lambda r: PLANNED in r.flags),
    ("Replaced", lambda r: REPLACED in r.flags),
    ("Met in simulation", lambda r: MET_SIM in r.flags),
    ("Not met", lambda r: NOT_MET in r.flags),
    ("Not checked", lambda r: UNCHECKED in r.flags),
    ("User story", lambda r: STORY in r.flags),
    ("Invariant", lambda r: INVARIANT in r.flags),
    ("Status does not parse", lambda r: not r.flags),
)
_VERIFICATION = ("Tested", "Known gap", "Live check only", "Manual check", "Missing", "No check needed")


def _verification(ctx: Context, r: Req) -> str:
    if ctx.proving(r.id):
        return "Tested"
    if r.needs_check and r.id in ctx.known_gaps:
        return "Known gap"
    ex = ctx.exempt.get(r.id)
    if r.needs_check and ex is not None:
        return "Live check only" if ex.kind == "live" else "Manual check"
    return "Missing" if r.needs_check else "No check needed"


def _anchor(heading: str) -> str:
    """GitHub's heading anchor: lower case, punctuation dropped, spaces to hyphens."""
    return re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")


def _cell(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text).replace("**", "")
    return re.sub(r"(?<!\\)\|", r"\\|", " ".join(text.split()))


def _clip(text: str, limit: int = CLIP) -> str:
    """At most `limit` characters, ending in an ellipsis, without an unclosed code span or half an escaped pipe."""
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rstrip()
    if cut.count("`") % 2:
        cut = cut[: cut.rfind("`")].rstrip()
    return cut.rstrip("\\").rstrip() + "…"


def _note(ctx: Context, r: Req) -> str:
    parts = []
    tests = ctx.tests_by_id.get(r.id, [])
    if r.replaced_by:
        parts.append(f"replaced by {r.replaced_by}")
    if PLANNED in r.flags:
        parts.append("not built yet" + ("; strict xfail test" if any(t.strict_xfail for t in tests) else ""))
    if UNCHECKED in r.flags:
        parts.append("no check yet")
    if NOT_MET in r.flags:
        parts.append("target not met" + ("; strict xfail test" if any(t.strict_xfail for t in tests) else ""))
    kind = _verification(ctx, r)
    if kind == "Tested":
        parts.append("offline test" if LIVE_UNVERIFIED in r.flags else "tested")
    elif kind == "Known gap":
        parts.append("**known gap**: no offline test yet" if LIVE_UNVERIFIED in r.flags else "**known gap**: no test yet")
    elif kind == "Missing":
        parts.append("**missing**")
    ex = ctx.exempt.get(r.id)
    if ex is not None:
        parts.append(f"{'live' if ex.kind == 'live' else 'manual'} check: `{ex.evidence}` ({_cell(ex.reason)})")
    return "; ".join(parts) or "—"


def _test_note(t: TestInfo) -> str:
    return " (xfail)" if t.xfail else " (skipped)" if t.skip else ""


def _row(ctx: Context, r: Req) -> str:
    code = "<br>".join(f"`{ref}`" for ref in ctx.code.get(r.id, ())) or "—"
    tests = "<br>".join(f"`{t.nodeid}`" + _test_note(t) for t in ctx.tests_by_id.get(r.id, [])) or "—"
    status = _cell(r.status) if r.status else "—"
    return f"| {r.id} | {_clip(_cell(r.text))} | {status} | {code} | {tests} | {_note(ctx, r)} |"


def _count_table(title: str, labels: Sequence[str], classify: Callable[[Req], str], reqs: list[Req]) -> list[str]:
    head = f"| {title} | " + " | ".join(KINDS) + " | Total |"
    lines = [head, "|" + "---|" * (len(KINDS) + 2)]
    for label in labels:
        row = [sum(1 for r in reqs if r.kind == k and classify(r) == label) for k in KINDS]
        if sum(row):
            lines.append(f"| {label} | " + " | ".join(str(n) for n in row) + f" | {sum(row)} |")
    totals = [sum(1 for r in reqs if r.kind == k) for k in KINDS]
    lines.append("| **Total** | " + " | ".join(f"**{n}**" for n in totals) + f" | **{sum(totals)}** |")
    return lines


def _category(r: Req) -> str:
    return next(label for label, test in _CATEGORIES if test(r))


def render(ctx: Context) -> str:
    reqs = ctx.unique()
    gaps = [r.id for r in reqs if _verification(ctx, r) == "Known gap"]
    out = [
        "# Requirements traceability",
        "",
        "Generated by `python scripts/reqtrace.py` from [PRD.md](PRD.md), the invariants in [CLAUDE.md](../CLAUDE.md) and the "
        "`@pytest.mark.req` markers in `tests/`. Do not edit it by hand: `python scripts/reqtrace.py --check` (run by "
        "`make lint` and CI) fails when it is out of date. How the check works: [TESTING.md](TESTING.md#traceability).",
        "",
        "Columns: **Code** is where the requirement is implemented (`module:name` or a file). **Tests** are the tests marked "
        "with its id. **Verification** says how it is checked: by those tests, by a live or manual check outside the "
        "offline suite, or not yet (a known gap).",
        "",
        "## Summary",
        "",
        *_count_table("Status", [label for label, _ in _CATEGORIES], _category, reqs),
        "",
        *_count_table("Verification", _VERIFICATION, lambda r: _verification(ctx, r), reqs),
        "",
    ]
    if gaps:
        out += [
            f"Known gaps ({len(gaps)}): {', '.join(gaps)}. These are done requirements without an offline test. "
            "`KNOWN_GAPS` in `scripts/reqtrace.py` may only shrink: remove an id when a test marks it.",
            "",
        ]
    for section in ctx.sections:
        rows = [r for r in reqs if r.section == section]
        source = rows[0].source if rows else PRD
        link = f"[PRD §{section.split()[0].rstrip('.')}](PRD.md#{_anchor(section)})"
        if source == CLAUDE:
            link = f"[CLAUDE.md](../CLAUDE.md#{_anchor(section)})"
        out += [f"## {section}", "", f"From {link}.", ""]
        if not rows:
            out += ["No requirements in this section yet.", ""]
            continue
        out += ["| ID | Requirement | Status | Code | Tests | Verification/notes |", "|---|---|---|---|---|---|"]
        out += [_row(ctx, r) for r in rows]
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Requirements traceability: PRD rows and invariants to code and tests.")
    ap.add_argument("--check", action="store_true", help="print every problem and exit 1 if there is one; writes nothing")
    ap.add_argument("--out", type=Path, default=None, help=f"where to write the matrix (default {OUT})")
    args = ap.parse_args(argv)
    ctx = load()
    if args.check:
        errors = check(ctx)
    else:
        out = args.out or ROOT / OUT
        out.write_text(render(ctx), encoding="utf-8")
        print(f"reqtrace: wrote {_rel(out, ROOT)}")
        errors = check(ctx, CORE_RULES + DOC_RULES)
    for e in errors:
        print(f"reqtrace: {e}")
    if errors:
        print(f"reqtrace: {len(errors)} problem{'s' * (len(errors) != 1)}; see docs/TESTING.md#traceability")
        return 1
    kinds = ", ".join(f"{sum(r.kind == k for r in ctx.unique())} {k}" for k in KINDS)
    marked = sum(1 for t in ctx.tests.tests if t.ids)
    print(
        f"reqtrace: OK: {len(ctx.unique())} requirements ({kinds}); {marked} of {len(ctx.tests.tests)} test functions "
        f"carry a requirement id; {len(ctx.known_gaps)} known gaps"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
