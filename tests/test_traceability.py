"""The traceability check (scripts/reqtrace.py): PRD rows, invariants, code references and test markers stay in step.

The repo tests run each rule on the real repo. The mini-world tests write a tiny PRD, AGENTS.md and test file
to a temporary folder and check that each kind of mistake gives exactly one error, so messages stay actionable.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import textwrap
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.req("NFR-10")


def _load_reqtrace():
    spec = importlib.util.spec_from_file_location("reqtrace", ROOT / "scripts" / "reqtrace.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up while the class is created
    spec.loader.exec_module(module)
    return module


rq = _load_reqtrace()


@pytest.fixture(scope="module")
def repo():
    return rq.load()


# ---------- the real repo ----------
def test_ids_unique_and_statuses_parse(repo):
    assert rq.rule_ids_and_statuses(repo) == []
    assert {r.kind for r in repo.reqs} == set(rq.KINDS)
    assert all(r.flags for r in repo.reqs)  # every status matched the grammar
    invariants = [r.id for r in repo.reqs if r.kind == "INV"]
    assert len(invariants) >= 14 and invariants == [f"INV-{n}" for n in range(1, len(invariants) + 1)]
    assert {f"SM-{n}" for n in range(1, 6)} <= set(repo.by_id)
    assert all(r.status for r in repo.reqs if r.kind == "NFR")  # §7 has a Status column
    assert repo.by_id["FR-1.4"].replaced_by == "FR-1.6"
    assert {"6.12", "6.13", "6.14"} <= {s.split()[0] for s in repo.sections}  # listed even before they have rows


def test_done_requirements_have_tests_or_known_gap(repo):
    assert rq.rule_coverage(repo) == []


def test_known_gaps_and_exemptions_only_shrink(repo):
    assert isinstance(rq.INITIAL_KNOWN_GAPS, frozenset) and isinstance(rq.KNOWN_GAPS, frozenset)
    assert rq.KNOWN_GAPS <= rq.INITIAL_KNOWN_GAPS
    assert rq.rule_registries(repo) == []


def test_markers_cite_known_ids(repo):
    assert rq.rule_markers_known(repo) == []
    assert len({rid for t in repo.tests.tests for rid in t.ids}) > 50  # the suite is marked, not just this file


def test_planned_ids_only_in_strict_xfail(repo):
    assert rq.rule_planned_in_strict_xfail(repo) == []


def test_not_met_metrics_have_strict_xfail(repo):
    assert rq.rule_not_met(repo) == []


def test_code_refs_resolve(repo):
    assert rq.rule_code_refs(repo) == []


def test_live_unverified_have_offline_test_and_live_exemption(repo):
    assert rq.rule_live_unverified(repo) == []


def test_requirements_md_is_current(repo):
    assert rq.rule_generated(repo) == []
    assert rq.render(repo) == rq.render(rq.load())  # deterministic: no timestamps, no set ordering


def test_every_route_documented_in_api_md(repo):
    assert rq.rule_routes_documented(repo) == []
    routes = rq.api_routes()
    assert ("POST", "/v1/chat/completions") in routes and ("GET", "/ratchet/versions/{}/bom") in routes
    assert not {p for _, p in routes} & {"/docs", "/openapi.json", "/favicon.svg"}  # not in the schema


def test_every_env_var_documented(repo):
    assert rq.rule_env_documented(repo) == []
    assert rq.rule_env_known(repo) == []  # config.KNOWN_ENV is exactly what the package reads (ADR 0070)
    found = rq.env_reads()
    assert {"RATCHET_MODE", "RATCHET_DATA_DIR", "RATCHET_API_KEY", "RATCHET_MIN_AUDITS", "RATCHET_LIVE_FLOOR"} <= set(found)


@pytest.mark.req("FR-12.30")
def test_env_known_rule_catches_drift(tmp_path):
    """A variable read under ratchet/ but missing from config.KNOWN_ENV is a finding (the startup check would call it
    unknown), and so is a KNOWN_ENV entry nothing reads; ReleasePolicy's RATCHET_<FIELD> need no entry."""
    (tmp_path / "ratchet").mkdir()
    (tmp_path / "ratchet" / "new.py").write_text(
        "import os\nos.environ.get('RATCHET_NEW_THING')\nos.environ.get('RATCHET_MODE')\nos.environ.get('RATCHET_MIN_AUDITS')\n",
        encoding="utf-8",
    )
    ctx = rq.load(tmp_path, code={}, exempt={}, known_gaps=frozenset(), initial_known_gaps=frozenset())
    errors = rq.rule_env_known(ctx)
    assert errors[0] == "RATCHET_NEW_THING: read at ratchet/new.py:2 but not in config.KNOWN_ENV; add it there"
    assert not [e for e in errors if e.startswith(("RATCHET_MODE:", "RATCHET_MIN_AUDITS:"))]
    assert "RATCHET_DB: in config.KNOWN_ENV but nothing under ratchet/ reads it" in errors
    assert (
        rq.rule_env_known(rq.load(tmp_path / "empty", code={}, exempt={}, known_gaps=frozenset(), initial_known_gaps=frozenset()))
        == []
    )  # no package: nothing to compare


def test_doc_test_counts_match(repo):
    assert rq.rule_test_counts(repo) == []


@pytest.mark.req("FR-7.9")
def test_metric_targets_match_the_prd(repo):
    assert rq.rule_metric_targets(repo) == []


def test_ast_count_matches_pytest_collection(repo):
    """The doc-count rule trusts the AST count; pytest's own collection must agree with it, file by file."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("COV_CORE", "PYTEST_ADDOPTS"))}
    cmd = [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", "-p", "no:warnings", "tests"]
    out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=300, env=env)
    assert out.returncode == 0, out.stdout[-2000:] + out.stderr[-2000:]
    collected = Counter(line.split("::")[0] for line in out.stdout.splitlines() if line.startswith("tests/") and "::" in line)
    expected = dict(repo.tests.counts())
    if importlib.util.find_spec("fcntl") is None:  # Windows: tests/test_watch.py skips itself (pytest.importorskip)
        expected.pop("tests/test_watch.py", None)
    assert dict(collected) == expected


def test_check_cli_passes_and_writes_the_committed_matrix(tmp_path, capsys):
    assert rq.main(["--check"]) == 0
    assert "reqtrace: OK" in capsys.readouterr().out
    out = tmp_path / "REQUIREMENTS.md"
    assert rq.main(["--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == (ROOT / rq.OUT).read_text(encoding="utf-8")


# ---------- a mini world in a temporary folder ----------
MINI_PRD = """\
# Mini PRD

## 5. User stories

**US-1. Try it.** As a user, I want answers.

## 6. Functional requirements

### 6.1 Gateway

| ID | Requirement | Status |
|---|---|---|
| FR-1.1 | Answer requests | Done (v0.1) |
| FR-1.2 | The old way | Replaced by FR-1.1 |
| FR-1.3 | The next thing | Planned |
| FR-1.4 | Talks to the real service | Done (v0.1), untested (live) |

### 6.2 Empty

No rows yet. An example in a code block is not a requirement:

```
| FR-2.9 | Example only | Done (v0.1) |
```

## 8. Success metrics

### 8.1 Product metrics

| ID | Metric | Target | Status |
|---|---|---|---|
| SM-1 | Savings | ≥ 70% | Not met (simulation: 60%) |
"""

MINI_CLAUDE = """\
# Mini

## Invariants

1. **Keep it simple.** Always.

## Conventions
"""

MINI_TESTS = """\
import pytest


@pytest.mark.req("FR-1.1", "US-1", "INV-1")
def test_answers():
    pass


@pytest.mark.req("FR-1.4")
def test_live_thing_offline():
    pass


@pytest.mark.req("FR-1.3")
@pytest.mark.xfail(strict=True, reason="not built")
def test_next_thing():
    raise AssertionError


@pytest.mark.req("SM-1")
@pytest.mark.xfail(strict=True, reason="60% in simulation")
def test_savings_target():
    raise AssertionError
"""


@dataclass
class World:
    prd: str = MINI_PRD
    claude: str = MINI_CLAUDE
    tests: str = MINI_TESTS
    code: dict = field(
        default_factory=lambda: {"FR-1.1": ("json:dumps",), "FR-1.4": ("json:loads",), "INV-1": ("json:JSONDecoder",)}
    )
    exempt: dict = field(default_factory=lambda: {"FR-1.4": rq.Exempt("live", "evidence.md", "needs the real service")})
    gaps: frozenset = frozenset()
    initial: frozenset = frozenset()

    def edit_prd(self, old: str, new: str) -> None:
        self.prd = _sub(self.prd, old, new)

    def edit_tests(self, old: str, new: str) -> None:
        self.tests = _sub(self.tests, old, new)

    def add_row(self, row: str, code: tuple[str, ...] = ()) -> None:
        self.edit_prd("| FR-1.4 |", f"{row}\n| FR-1.4 |")
        if code:
            self.code[row.split()[1]] = code

    def load(self, root: Path):
        (root / "docs").mkdir(exist_ok=True)
        (root / "tests").mkdir(exist_ok=True)
        (root / "docs" / "PRD.md").write_text(self.prd, encoding="utf-8")
        (root / "AGENTS.md").write_text(self.claude, encoding="utf-8")
        (root / "tests" / "test_mini.py").write_text(self.tests, encoding="utf-8")
        (root / "evidence.md").write_text("checked by hand\n", encoding="utf-8")
        return rq.load(root, code=self.code, exempt=self.exempt, known_gaps=self.gaps, initial_known_gaps=self.initial)


def _sub(text: str, old: str, new: str) -> str:
    assert text.count(old) == 1, old
    return text.replace(old, new)


def _grow_gaps(w: World) -> None:
    w.add_row("| FR-1.5 | Untested | Done (v0.1) |", code=("json:dumps",))
    w.gaps = frozenset({"FR-1.5"})  # not in the (empty) initial set


def _skipped_only(w: World) -> None:
    w.add_row("| FR-1.5 | Later | Done (v0.1) |", code=("json:dumps",))
    w.tests += '\n\n@pytest.mark.req("FR-1.5")\n@pytest.mark.skip(reason="later")\ndef test_later():\n    pass\n'


def _tested_gap(w: World) -> None:
    w.gaps = w.initial = frozenset({"FR-1.1"})


NON_LITERAL = '\n\nIDS = "FR-1.1"\n\n\n@pytest.mark.req(IDS)\ndef test_x():\n    pass\n'
FAULTS = {
    "bad_status": lambda w: w.edit_prd("| Answer requests | Done (v0.1) |", "| Answer requests | Done in v0.1 |"),
    "cites_replaced": lambda w: w.edit_tests('"FR-1.1", "US-1"', '"FR-1.1", "FR-1.2", "US-1"'),
    "duplicate_id": lambda w: w.add_row("| FR-1.1 | Answer requests again | Done (v0.1) |"),
    "unknown_marker": lambda w: w.edit_tests('"FR-1.1", "US-1"', '"FR-1.1", "FR-9.9", "US-1"'),
    "malformed_id": lambda w: w.add_row("| FR-1 | No minor number | Planned |"),
    "wrong_section": lambda w: w.add_row("| FR-2.1 | Misplaced | Planned |"),
    "metric_status_on_fr": lambda w: w.edit_prd("| The next thing | Planned |", "| The next thing | Met in simulation (x) |"),
    "replaced_by_missing": lambda w: w.edit_prd("Replaced by FR-1.1", "Replaced by FR-1.9"),
    "done_without_test": lambda w: w.add_row("| FR-1.5 | Untested | Done (v0.1) |", code=("json:dumps",)),
    "planned_not_strict": lambda w: w.edit_tests('xfail(strict=True, reason="not built")', 'xfail(reason="not built")'),
    "not_met_not_strict": lambda w: w.edit_tests('xfail(strict=True, reason="60%', 'xfail(reason="60%'),
    "unchecked_metric_cited": lambda w: w.edit_prd("Not met (simulation: 60%)", "Not checked"),
    "gap_now_tested": _tested_gap,
    "gaps_grew": _grow_gaps,
    "manual_exempt_tested": lambda w: w.exempt.update({"FR-1.1": rq.Exempt("manual", "evidence.md", "looked at it")}),
    "evidence_missing": lambda w: w.exempt.update({"FR-1.4": rq.Exempt("live", "missing.md", "needs the real service")}),
    "live_without_exemption": lambda w: w.exempt.clear(),
    "live_without_offline_test": lambda w: w.edit_tests('@pytest.mark.req("FR-1.4")\n', ""),
    "code_ref_broken": lambda w: w.code.update({"FR-1.1": ("json:no_such_name",)}),
    "done_without_code": lambda w: w.code.pop("FR-1.1"),
    "non_literal_marker": lambda w: setattr(w, "tests", w.tests + NON_LITERAL),
    "invariant_untested": lambda w: w.edit_tests('"US-1", "INV-1"', '"US-1"'),
    "only_skipped_test": _skipped_only,
}


def test_mini_world_is_clean_and_renders(tmp_path):
    ctx = World().load(tmp_path)
    assert rq.check(ctx, rq.CORE_RULES) == []
    assert [r.id for r in ctx.reqs] == ["US-1", "FR-1.1", "FR-1.2", "FR-1.3", "FR-1.4", "SM-1", "INV-1"]
    md = rq.render(ctx)
    assert md == rq.render(World().load(tmp_path))
    assert "| FR-1.1 | Answer requests | Done (v0.1) | `json:dumps` | `tests/test_mini.py::test_answers` | tested |" in md
    assert "## 6.2 Empty" in md and "No requirements in this section yet." in md
    assert "`tests/test_mini.py::test_next_thing` (xfail)" in md and "live check: `evidence.md`" in md
    assert "[AGENTS.md](../AGENTS.md#invariants)" in md and "[PRD §6.1](PRD.md#61-gateway)" in md


@pytest.mark.parametrize(
    "fault, expected",
    [
        ("bad_status", "FR-1.1: status 'Done in v0.1' does not match the grammar"),
        ("cites_replaced", "cites FR-1.2, which is 'Replaced by FR-1.1'"),
        ("duplicate_id", "duplicate id FR-1.1"),
        ("unknown_marker", "cites 'FR-9.9', which is not a requirement"),
        ("malformed_id", "malformed requirement id 'FR-1'"),
        ("wrong_section", "FR-2.1 belongs in section 6.2"),
        ("metric_status_on_fr", "is not allowed on FR rows"),
        ("replaced_by_missing", "FR-1.2 is replaced by FR-1.9, which is not a requirement"),
        ("done_without_test", "FR-1.5 is 'Done (v0.1)' but no test proves it"),
        ("planned_not_strict", "cites FR-1.3, which is 'Planned'"),
        ("not_met_not_strict", "SM-1 is 'Not met (simulation: 60%)'"),
        ("unchecked_metric_cited", "cites SM-1, which is 'Not checked'"),
        ("gap_now_tested", "FR-1.1: a test marks it now; remove it from KNOWN_GAPS"),
        ("gaps_grew", "FR-1.5: KNOWN_GAPS may only shrink"),
        ("manual_exempt_tested", "remove its manual EXEMPT entry"),
        ("evidence_missing", "EXEMPT evidence missing.md does not exist"),
        ("live_without_exemption", "add a 'live' EXEMPT entry"),
        ("live_without_offline_test", "it still needs an offline test"),
        ("code_ref_broken", "CODE ref 'json:no_such_name' does not resolve"),
        ("done_without_code", "FR-1.1: no CODE entry"),
        ("non_literal_marker", "takes string literals only"),
        ("invariant_untested", "INV-1 is an invariant but no test proves it"),
        ("only_skipped_test", "FR-1.5 is 'Done (v0.1)' but only xfail or skipped tests cite it"),
    ],
)
def test_each_mistake_gives_exactly_one_error(tmp_path, fault, expected):
    world = World()
    FAULTS[fault](world)
    errors = rq.check(world.load(tmp_path), rq.CORE_RULES)
    assert len(errors) == 1 and expected in errors[0], errors


def test_status_grammar():
    table = [
        ("FR", "Done (v0.1.1)", {"DONE"}),
        ("FR", "Done (v0.2), ready to run (on Nebius)", {"DONE", "LIVE_UNVERIFIED"}),
        ("FR", "Done (v0.5), untested (on GPU)", {"DONE", "LIVE_UNVERIFIED"}),
        ("NFR", "Done (v0.4); NER-based detection planned", {"DONE", "PARTIAL_PLANNED"}),
        ("NFR", "Partial: the base-model license is missing", {"PARTIAL"}),
        ("FR", "Planned", {"PLANNED"}),
        ("FR", "Replaced by FR-1.6", {"REPLACED"}),
        ("SM", "Met in simulation (seed 7)", {"MET_SIM"}),
        ("SM", "Not met (simulation: 4 nights)", {"NOT_MET"}),
        ("SM", "Not checked", {"UNCHECKED"}),
        ("FR", "Done (v0.1), untested live", set()),  # the wording before this check
        ("FR", "Done (v0.2), untested (on Nebius)", set()),  # now 'ready to run (on Nebius)'
        ("FR", "Done (v0.4); the demo VM also has a guard", set()),  # free text must end in 'planned'
        ("FR", "done (v0.1)", set()),
        ("FR", "Done (v0.1) ", set()),  # matched whole, so trailing text fails
        ("SM", "Done (v0.1)", set()),
        ("FR", "Not checked", set()),
        ("NFR", "", set()),
    ]
    for kind, status, flags in table:
        got, error = rq.parse_status(kind, status)
        assert set(got) == flags and (error is None) == bool(flags), (kind, status, got, error)


def test_scan_reads_markers_and_counts_cases(tmp_path):
    (tmp_path / "tests").mkdir()
    source = """
        import pytest

        pytestmark = [pytest.mark.req("FR-1.1"), pytest.mark.slow]
        CASES = [1, 2, 3]


        @pytest.mark.parametrize("a", CASES)
        @pytest.mark.parametrize("b", [(1, 2), (3, 4)])
        def test_grid(a, b):
            pass


        @pytest.fixture
        def test_data():
            return 1


        @pytest.mark.req("FR-2.1")
        class TestGroup:
            pytestmark = pytest.mark.xfail(strict=True)

            @pytest.mark.req("FR-2.1", "FR-3.1")
            def test_one(self):
                pass

            class TestInner:
                def test_two(self):
                    pass


        class TestNotCollected:
            def __init__(self):
                pass

            def test_skipped(self):
                pass


        @pytest.mark.parametrize("n", list(range(3)))
        def test_dynamic(n):
            pass


        @pytest.mark.req("FR-4.1")
        @pytest.mark.skip(reason="later")
        def test_later():
            pass
    """
    (tmp_path / "tests" / "test_x.py").write_text(textwrap.dedent(source), encoding="utf-8")
    scan = rq.scan(tmp_path / "tests", tmp_path)
    got = {t.nodeid.removeprefix("tests/test_x.py::"): t for t in scan.tests}
    assert list(got) == ["test_grid", "TestGroup::test_one", "TestGroup::TestInner::test_two", "test_dynamic", "test_later"]
    assert got["test_grid"].cases == 6 and got["test_grid"].ids == ("FR-1.1",) and not got["test_grid"].xfail
    one, two = got["TestGroup::test_one"], got["TestGroup::TestInner::test_two"]
    assert one.ids == ("FR-1.1", "FR-2.1", "FR-3.1") and one.strict_xfail
    assert two.ids == ("FR-1.1", "FR-2.1") and two.strict_xfail and two.cases == 1
    assert got["test_dynamic"].cases is None and scan.counts() == {"tests/test_x.py": None}
    assert got["test_later"].skip and not got["test_later"].proves and got["test_grid"].proves
    assert rq.scan_tests(tmp_path / "tests", tmp_path)["FR-2.1"] == [one.nodeid, two.nodeid]
    assert scan.errors == []


def test_invariants_keep_their_list_numbers(tmp_path):
    path = tmp_path / "AGENTS.md"
    path.write_text("## Invariants\n\n1. One.\n2. Two\n   continues here.\n4. Four.\n\n## Next\n\n5. Not an invariant.\n")
    parsed = rq.parse_invariants(path, tmp_path)
    assert [r.id for r in parsed.reqs] == ["INV-1", "INV-2", "INV-4"]
    assert parsed.reqs[1].text == "Two continues here."
    assert len(parsed.errors) == 1 and "numbered 4, expected 3" in parsed.errors[0]


def test_missing_sources_are_findings_not_crashes(tmp_path):
    ctx = rq.load(tmp_path, code={}, exempt={}, known_gaps=frozenset(), initial_known_gaps=frozenset())
    errors = rq.check(ctx, rq.RULES)
    assert "docs/PRD.md: not found" in errors and "AGENTS.md: not found" in errors
    assert f"{rq.OUT} is missing; run python scripts/reqtrace.py" in errors
    assert f"{rq.API_DOC}: not found" in errors and f"{rq.CONFIG_DOC}: not found" in errors


def test_doc_rules_catch_drift(tmp_path, monkeypatch):
    world = World(claude=MINI_CLAUDE + "\nThe suite has 5 tests.\n")
    ctx = world.load(tmp_path)
    (tmp_path / "docs" / "TESTING.md").write_text("| `tests/test_mini.py` | 3 |\n| **Total** | **4** |\n", encoding="utf-8")
    assert rq.rule_test_counts(ctx) == [
        "AGENTS.md:9: says 5 tests; the suite has 4",
        "docs/TESTING.md:1: says tests/test_mini.py has 3 tests; it has 4",
    ]

    (tmp_path / "docs" / "API.md").write_text(
        "| GET | `/v1/models` |\n### GET `/ratchet/versions/{id}/bom`\nSee `/v1/chat/completions`.\n", encoding="utf-8"
    )
    routes = [("GET", "/v1/models"), ("GET", "/ratchet/versions/{}/bom"), ("POST", "/v1/chat/completions")]
    monkeypatch.setattr(rq, "api_routes", lambda: routes)
    assert rq.rule_routes_documented(ctx) == [f"POST /v1/chat/completions: not in {rq.API_DOC}; add it to the endpoint table"]

    reads = "import os\nfrom os import environ, getenv\nname = 'X'\n" + "\n".join(
        [
            "os.environ.get('A')",
            "getenv('B', '')",
            "_env('C', 'c')",
            "os.environ['D']",
            "environ.get('E')",
            "_int('F', 1)",
            "os.environ.get(name)",
            "os.environ.setdefault('G', 'g')",
        ]
    )
    found = [rq._env_name(n) for n in rq.ast.walk(rq.ast.parse(reads))]
    assert sorted(x for x in found if x) == ["A", "B", "C", "D", "E", "F"]


@pytest.mark.req("FR-7.9")
def test_metric_target_rule_catches_drift(tmp_path, monkeypatch):
    @dataclass(frozen=True)
    class T:
        op: str
        value: float
        unit: str

    ctx = World().load(tmp_path)  # SM-1: "Savings", target "≥ 70%"
    good = T(">=", 0.70, "fraction")
    monkeypatch.setattr(rq, "metric_targets", lambda: {"SM-1": good})
    assert rq.rule_metric_targets(ctx) == []
    differs = "SM-1: the PRD target '≥ 70%' differs from ratchet.kpis.SM_TARGETS"
    for targets, expected in [
        ({"SM-1": T(">=", 0.75, "fraction")}, f"{differs} (>= 0.75 fraction)"),
        ({"SM-1": T("<=", 0.70, "fraction")}, differs),
        ({"SM-1": T(">=", 70, "nights")}, differs),
        ({}, "SM-1: no entry in ratchet.kpis.SM_TARGETS; add its target ('≥ 70%')"),
        ({"SM-1": good, "SM-9": T("==", 0, "")}, "ratchet.kpis.SM_TARGETS lists SM-9, which is not a metric"),
    ]:
        monkeypatch.setattr(rq, "metric_targets", lambda targets=targets: targets)
        errors = rq.rule_metric_targets(ctx)
        assert len(errors) == 1 and expected in errors[0], errors
    monkeypatch.setattr(rq, "metric_targets", lambda: {"SM-1": good})
    world = World()
    world.edit_prd("| ≥ 70% |", "| most of it |")
    assert rq.rule_metric_targets(world.load(tmp_path)) == [
        "SM-1: the PRD target 'most of it' does not read as a comparison and a number"
    ]

    def broken() -> dict:
        raise ImportError("no module")

    monkeypatch.setattr(rq, "metric_targets", broken)
    assert rq.rule_metric_targets(ctx) == ["cannot import ratchet.kpis: ImportError: no module"]
    cells = {
        "≥ 70%": (">=", 0.7, "fraction"),
        "0": ("==", 0.0, ""),
        "≤ 3 nights": ("<=", 3.0, "nights"),
        "< 1 s": ("<", 1.0, "s"),
    }
    assert {cell: rq.parse_target(cell) for cell in cells} == cells
    assert rq.parse_target("about 70%") is None


def test_clip_keeps_table_cells_valid():
    assert rq._clip("Answer `requests`") == "Answer `requests`"
    long = "Accepts `POST /v1/chat/completions` " + "x" * 50 + " and returns `a code span that crosses the limit of the cell`"
    out = rq._clip(long)
    assert len(out) <= rq.CLIP and out.endswith("…") and out.count("`") % 2 == 0
    assert not rq._clip("a" * 98 + "\\|b").removesuffix("…").endswith("\\")  # never half an escaped pipe
    assert rq._cell("a | b [link](x.md) **bold**") == "a \\| b link bold"
