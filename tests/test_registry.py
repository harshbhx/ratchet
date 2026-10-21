"""The task registry (FR-2.15, ADR 0022): task specs from RATCHET_TASKS_FILE and the admin API, checked with every
error's path; the admin-key primitive; operator-only pinning; the unknown-task policy; the per-task client allow-list;
a pinned task's rows left out of Night School at read time; and a changed spec sending its task to the teacher until a
night scores the version under the new spec."""

from __future__ import annotations

import json
import math
import os
import random
import sys
import threading
from pathlib import Path
from typing import Any

import pytest
from fakes import (
    OPEN_TEACHER,
    REJECTED_EVAL,
    FakeOpenAIServer,
    RecordingEvaluator,
    RecordingProvider,
    RecordingTrainer,
    _examples,
    _version,
)
from fastapi.testclient import TestClient

from ratchet import compare, live, tasks, taskspec, vocab
from ratchet.auth import KeyRing
from ratchet.confidence import SERVE_NOTHING
from ratchet.config import Settings
from ratchet.ledger import Ledger
from ratchet.nightschool import dataset
from ratchet.nightschool.evaluate import cascade_eval
from ratchet.nightschool.orchestrate import run_night
from ratchet.release import ReleaseGate, ReleasePolicy
from ratchet.router import Ratchet
from ratchet.taskspec import SpecError

pytestmark = pytest.mark.req("FR-2.15")

CHAT = "/v1/chat/completions"
ADMIN_KEY = "ops-admin-key-0123456789abcdef"
ACME_KEY = "acme-api-key-0123456789abcdef"
GLOBEX_KEY = "globex-api-key-0123456789abcdef"
ADMIN = {"Authorization": f"Bearer {ADMIN_KEY}"}
ACME = {"Authorization": f"Bearer {ACME_KEY}"}
GLOBEX = {"Authorization": f"Bearer {GLOBEX_KEY}"}
TRIAGE_ANSWER = json.dumps({"category": "billing", "priority": "high", "reply": "Checking."})
# A file or API task with the built-in triage's comparators, so the mock teacher's ticket answers are valid for it.
SUPPORT = {
    "name": "support-triage",
    "description": "Support tickets, labelled like ticket-triage",
    "fields": {
        "category": {"type": "label", "values": list(vocab.TICKET_CATEGORIES)},
        "priority": {"type": "label", "values": list(vocab.TICKET_PRIORITIES)},
    },
}
SUPPORT_BODY = {k: v for k, v in SUPPORT.items() if k != "name"}
INVOICE_BODY = {
    "fields": {
        "vendor": {"type": "text", "strip_suffixes": ["inc", "ltd"]},
        "total": {"type": "number", "abs_tol": 0.01},
    }
}


def _file(tmp_path: Path, doc: Any, name: str = "tasks.json") -> Path:
    path = tmp_path / name
    path.write_text(doc if isinstance(doc, str) else json.dumps(doc), encoding="utf-8")
    return path


def _live(tmp_path: Path, **overrides: Any) -> Settings:
    """A live-mode Settings with everything these tests depend on set explicitly (no student endpoint: the hosted
    fallback student shares the teacher's transport)."""
    base: dict[str, Any] = {
        "mode": "live",
        "db_path": str(tmp_path / "live.db"),
        "teacher_base": "https://tokenfactory.test/v1",
        "teacher_api_key": "tf-key",
        "teacher_model": OPEN_TEACHER,
        "student_base": "",
        "samples": 3,
        "audit_rate": 0.0,
        "api_keys": "",
        "admin_keys": "",
        "api_key_daily_requests": 0,
        "tasks_file": "",
        "unknown_tasks": "distill",
        "bom_signing_key": "",
        "tavily_api_key": "",
        "mock_warmup_days": 0,
        # No API keys and a public dashboard (ADR 0061): these tests call /v1/* and read the task list without a key.
        "allow_unauthenticated": True,
        "public_dashboard": True,
    }
    return Settings(**{**base, **overrides})


def _ask(tag: object = 0) -> list[dict[str, Any]]:
    return [{"role": "user", "content": f"Ticket {tag}: I was charged twice."}]


def _rows(led: Ledger, sql: str) -> list[dict[str, Any]]:
    return [dict(r) for r in led.db.execute(sql)]


# ---------- the tasks file ----------
@pytest.mark.req("FR-1.3", "FR-2.9")
def test_tasks_file_loads_and_registers(gw_env, tmp_path):
    path = _file(
        tmp_path,
        {
            "tasks": [
                SUPPORT,
                {"name": "contract-summary", "pinned": True, "description": "Long summaries stay on the teacher"},
                {"name": "default", "extends": "default", "policy": {"samples": 5}},  # a file may adjust a built-in task
            ]
        },
    )
    gw = gw_env(RATCHET_TASKS_FILE=str(path))
    c = TestClient(gw.app)
    listed = c.get("/ratchet/tasks").json()
    by_name = {t["name"]: t for t in listed["tasks"]}
    assert (listed["unknown_tasks"], listed["max_tasks"]) == ("distill", tasks.MAX_TASKS)
    assert by_name["support-triage"]["source"] == "file" and by_name["support-triage"]["verifiable"]
    assert by_name["support-triage"]["fields"] == SUPPORT["fields"]
    assert by_name["contract-summary"]["pinned"] and not by_name["contract-summary"]["verifiable"]
    assert (by_name["default"]["source"], by_name["default"]["policy"]) == ("file", {"samples": 5})
    assert by_name["ticket-triage"]["source"] == "builtin"
    assert tasks.get("support-triage").fields == tasks.get("ticket-triage").fields

    msgs = gw.rt.stream.sample(1).messages()
    assert c.post(CHAT, json={"messages": msgs}, headers={"X-Ratchet-Task": "support-triage"}).status_code == 200
    assert c.post(CHAT, json={"messages": msgs, "metadata": {"ratchet_task": "contract-summary"}}).status_code == 200
    routes = _rows(gw.rt.ledger, "SELECT task, route, stored FROM requests ORDER BY id")
    assert routes == [
        {"task": "support-triage", "route": "cold_start", "stored": 1},
        {"task": "contract-summary", "route": "pinned", "stored": 0},
    ]
    assert [r["task"] for r in gw.rt.ledger.examples()] == ["support-triage"]
    assert {t["task"]: t["pinned"] for t in c.get("/ratchet/stats").json()["tasks"]} == {
        "support-triage": False,
        "contract-summary": True,
    }
    entry = c.get("/ratchet/tasks/support-triage").json()
    assert (entry["requests"], entry["student_share"], entry["description"]) == (1, 0.0, SUPPORT["description"])
    assert "updated_by" not in entry  # file tasks are not stored in the ledger
    assert c.get("/ratchet/tasks/nope").status_code == 404


def test_tasks_file_in_yaml(tmp_path):
    pytest.importorskip("yaml")
    path = _file(
        tmp_path,
        """
tasks:
  - name: invoice-extract
    description: Vendor and total from an invoice
    fields:
      vendor: {type: text, strip_suffixes: [inc, ltd]}
      total: {type: number, abs_tol: 0.01}
      currency: {type: label, values: [USD, EUR], aliases: {"$": USD}}
    clients: [acme]
""",
        "tasks.yaml",
    )
    (task,) = tasks.load_file(path, known_clients=["acme"])
    assert (task.name, task.source, task.clients, [f.path for f in task.fields]) == (
        "invoice-extract",
        "file",
        ("acme",),
        ["vendor", "total", "currency"],
    )
    written, typed = '{"vendor": "Acme Inc", "total": "$12.50", "currency": "$"}', '{"vendor": "acme", "total": 12.5}'
    assert task.agree(written, typed[:-1] + ', "currency": "USD"}')
    with pytest.raises(SpecError, match=r"clients\[0\]: no API key is named 'acme'"):
        tasks.load_file(path, known_clients=["globex"])
    bad = _file(tmp_path, "tasks: [\n  - name: x\n    fields: {a: {type: bool}\n", "bad.yml")
    with pytest.raises(SpecError, match=r"not valid YAML: .*\(line \d+, column \d+\)"):
        tasks.load_file(bad)
    quoted = _file(tmp_path, "tasks:\n  - name: yn\n    fields:\n      ok: {type: label, values: [yes, no]}\n", "yn.yaml")
    with pytest.raises(SpecError, match=r"tasks\[0\].fields.ok.values: must be a list of strings"):
        tasks.load_file(quoted)  # YAML reads bare yes/no as booleans; quote them


def test_tasks_file_unknown_key_fails_startup_with_path(gw_env, tmp_path, monkeypatch):
    path = _file(
        tmp_path,
        {
            "tasks": [
                {"name": "invoices", "feilds": {"total": {"type": "number"}}},
                {"name": "amounts", "fields": {"total": {"type": "number", "abs_tol": -1}}},
            ]
        },
    )
    gw = gw_env()
    with pytest.raises(ValueError) as caught:
        gw.Runtime(Settings(mode="mock", tasks_file=str(path), mock_warmup_days=0))
    e = caught.value
    assert isinstance(e, SpecError) and e.errors == [
        "tasks[0].feilds: unknown key; use name, description, format, fields, sql, pinned, store, match, routing, policy,"
        " tools, clients, extends",
        "tasks[1].fields.total.abs_tol: must be a number >= 0",
    ]
    assert str(e).startswith(f"RATCHET_TASKS_FILE {path}: invalid task spec (2 errors):")
    # Night School's entry point loads the same file, so a bad file stops it before any work.
    monkeypatch.setenv("RATCHET_TASKS_FILE", str(path))
    monkeypatch.setenv("RATCHET_DB", str(tmp_path / "night.db"))
    with pytest.raises(SpecError, match=r"tasks\[0\]\.feilds"):
        live.main(["tick"])
    missing = tmp_path / "missing.json"
    with pytest.raises(SpecError, match="cannot read the file"):
        gw.Runtime(Settings(mode="mock", tasks_file=str(missing), mock_warmup_days=0))
    for text, message in (("{", r"not valid JSON: .* \(line 1, column 2\)"), ('{"tasks": [], "x": NaN}', "NaN is not JSON")):
        with pytest.raises(SpecError, match=message):
            tasks.load_file(_file(tmp_path, text))


def test_yaml_without_pyyaml_message(tmp_path, monkeypatch):
    path = _file(tmp_path, "tasks: []\n", "tasks.yaml")
    monkeypatch.setitem(sys.modules, "yaml", None)  # `import yaml` raises ImportError
    with pytest.raises(SpecError) as caught:
        tasks.load_file(path)
    assert caught.value.errors == [
        "$: this is a YAML file, but PyYAML is not installed: pip install pyyaml (the yaml extra), or write it as JSON"
    ]
    assert str(path) in str(caught.value)
    assert tasks.load_file(_file(tmp_path, {"tasks": []})) == []  # JSON needs nothing extra


def test_spec_errors_list_every_problem_with_its_path():
    doc = {
        "version": 2,
        "tasks": [
            {"name": "a", "feilds": {}, "pinned": "yes", "format": "tool_calls"},
            {
                "name": "b",
                "fields": {
                    "total": {"type": "number", "abs_tol": -1, "decimal": ";"},
                    "label": {"type": "label", "values": "x", "abs_tol": 1},
                    "customer.city": {"type": "text", "mode": "fuzzy", "colour": "red"},
                    "bad-path": {"type": "bool"},
                    "tags": {"type": "set", "item": {"type": "records"}},
                    "lines": {"type": "records", "key": ["sku"], "fields": {"qty": {"type": "number", "rel_tol": "1%"}}},
                    "summary": {"type": "similarity", "metric": "bleu", "threshold": 0},
                    "x": {"type": "label", "values": ["a"], "aliases": {"b": "c"}},
                    "missing": {},
                },
                "policy": {"samples": 0, "sample": 3, "audit_rate": 1.5},
                "match": {"system_prefix": "", "tools": ["ok", "ok"], "regex": ".*"},
                "routing": {"when": math.nan},
                "clients": ["acme", "bad name"],
                "tools": "student",
            },
            {"name": "bad name!"},
            {"name": "c", "extends": "d"},
            {"name": "d", "extends": "c"},
            {"name": "e", "extends": "nowhere"},
            {"name": "f", "extends": "g"},
            {"name": "g", "fields": {"x": {"type": "nope"}}},
            {"name": "b"},
            "not an object",
            {"name": "h", "format": "text", "fields": {"x": {"type": "label"}}},
            {"name": "deep", "fields": {"r": _nested_records(6)}},
        ],
    }
    with pytest.raises(SpecError) as caught:
        taskspec.parse_document(doc)
    options = ", ".join(sorted(taskspec.OPTIONS))
    types = "exact, label, bool, number, ordinal, text, date, language, set, records, ranking, similarity"
    deep = "tasks[11].fields.r" + ".fields.r" * 4 + ".fields"
    assert caught.value.errors == [
        "version: unknown key; use tasks",
        "tasks[0].feilds: unknown key; use name, description, format, fields, sql, pinned, store, match, routing, policy,"
        " tools, clients, extends",
        "tasks[0].format: 'tool_calls' is reserved for a later release",
        "tasks[0].pinned: must be true or false",
        "tasks[1].fields.total.abs_tol: must be a number >= 0",
        "tasks[1].fields.total.decimal: must be one of '.', ','",
        "tasks[1].fields.label.values: must be a list of strings",
        "tasks[1].fields.label.abs_tol: does not apply to type 'label'",
        "tasks[1].fields[\"customer.city\"].mode: must be one of 'exact', 'loose', 'token_set'",
        f'tasks[1].fields["customer.city"].colour: unknown option; use {options}',
        "tasks[1].fields[\"bad-path\"]: a path is '$' or dot-separated names of letters, digits and '_' (at most 256)",
        "tasks[1].fields.tags.item.type: elements compare by value, so use one of bool, date, exact, label, language,"
        " number, ordinal, text",
        "tasks[1].fields.lines.fields.qty.rel_tol: must be a number >= 0",
        "tasks[1].fields.summary.metric: must be one of 'chrf', 'token_f1', 'rouge_l'",
        "tasks[1].fields.summary.threshold: must be a number more than 0 and at most 1",
        "tasks[1].fields.x: an alias names a label outside the vocabulary",
        f"tasks[1].fields.missing.type: missing; use one of {types}",
        "tasks[1].tools: must be one of teacher, distill",
        "tasks[1].policy.samples: must be a whole number from 1 to 16",
        "tasks[1].policy.sample: unknown key; use samples, audit_rate, target_precision, min_precision",
        "tasks[1].policy.audit_rate: must be a number from 0 to 1",
        "tasks[1].match.system_prefix: must be a non-empty string of at most 2000 characters",
        "tasks[1].match.tools: names a tool twice",
        "tasks[1].match.regex: unknown key; use system_prefix, schema_name, tools",
        "tasks[1].routing: must hold finite numbers",
        "tasks[1].clients: must hold API key names (letters, digits, '_', '.', '-'), not 'bad name'",
        "tasks[2].name: 'bad name!' is not a task name (1 to 64 letters, digits, '_', '.', '-')",
        "tasks[3].extends: 'd' has errors",
        "tasks[4].extends: a cycle: c -> d -> c",
        "tasks[5].extends: no task is named 'nowhere' in this file or the built-in tasks",
        "tasks[6].extends: 'g' has errors",
        f"tasks[7].fields.x.type: 'nope' is not a field type; use one of {types}",
        "tasks[8].name: 'b' is already defined at tasks[1]",
        "tasks[9]: must be an object (one task spec)",
        "tasks[10]: a text task has one field, '$' (the whole answer)",
        f"{deep}: records are nested more than 4 deep",
    ]


def test_spec_parser_never_raises_anything_but_spec_error():
    """Seeded junk in every key and option (wrong types, unhashable values where a name belongs, unknown types, nesting)
    gives a SpecError, never another exception: an admin's typo is a 422, not a 500."""
    rng = random.Random(7)
    words = "x $ a.b label records set ranking text nope similarity sql teacher".split()
    atoms = [None, True, 0, -1, 2.5, 10**30, "", "a" * 300, [], {}, ["a", "a"], {"a": "b"}, [1, 2], *words]
    keys = [*taskspec.INPUT_KEYS, *taskspec.OPTIONS, "type", "feilds", "$"]

    def junk(depth: int = 0) -> Any:
        r = rng.random()
        if depth > 3 or r < 0.5:
            return rng.choice(atoms)
        if r < 0.75:
            return [junk(depth + 1) for _ in range(rng.randint(0, 3))]
        return {rng.choice(keys): junk(depth + 1) for _ in range(rng.randint(0, 4))}

    def field(depth: int = 0) -> dict[str, Any]:
        out: dict[str, Any] = {"type": rng.choice([*compare.TYPES, "nope"])}
        for _ in range(rng.randint(0, 4)):
            option = rng.choice([*taskspec.OPTIONS, "zz"])
            if option == "fields" and depth < 6:
                out[option] = {rng.choice(["a", "$", "c.d"]): field(depth + 1) for _ in range(rng.randint(0, 3))}
            elif option == "item":
                out[option] = rng.choice([field(depth + 1), rng.choice(compare.TYPES), 3])
            else:
                out[option] = junk(2)
        return out

    accepted = 0
    for _ in range(3000):
        spec: dict[str, Any] = {"name": rng.choice(["t", "bad name", 3, None])}
        for _ in range(rng.randint(0, 6)):
            key = rng.choice([*taskspec.INPUT_KEYS, "zzz"])
            spec[key] = {rng.choice(["a", "$", "c.d", "x-y"]): rng.choice([field(), junk()])} if key == "fields" else junk()
        for parse, arg in (
            (taskspec.parse_spec, spec),
            (taskspec.parse_document, {"tasks": [spec, {**spec, "name": "v", "extends": "t"}]}),
        ):
            try:
                parse(arg)
                accepted += 1
            except SpecError:
                pass
    assert accepted > 0  # some junk is a valid spec, so the parser is not refusing everything


def _nested_records(depth: int) -> dict[str, Any]:
    spec: dict[str, Any] = {"type": "number"}
    for _ in range(depth):
        spec = {"type": "records", "fields": {"r": spec}}
    return spec


def test_specs_round_trip_and_every_option_is_checked():
    assert set(taskspec.OPTIONS) == taskspec.option_names()  # a new FieldSpec option needs a check here
    rich = {
        "name": "everything",
        "description": "one field of every type",
        "fields": {
            "e": {"type": "exact", "required": False},
            "l": {"type": "label", "values": ["a", "b"], "aliases": {"x": "a"}},
            "b": {"type": "bool", "nullable": True},
            "n": {"type": "number", "abs_tol": 0.5, "rel_tol": 0.01, "decimal": ",", "percent": "fraction"},
            "o": {"type": "ordinal", "order": ["low", "high"], "tolerance": 1},
            "t": {"type": "text", "mode": "token_set", "strip_suffixes": ["inc"]},
            "d": {"type": "date", "day_first": True, "granularity": "datetime"},
            "lang": {"type": "language", "values": ["en", "de"]},
            "s": {"type": "set", "item": {"type": "number"}, "min_jaccard": 0.5, "split": ",", "empty_values": ["n/a", None]},
            "s2": {"type": "set", "item": "bool"},
            "r": {"type": "records", "key": ["id"], "fields": {"id": {"type": "label"}, "q.v": {"type": "number"}}},
            "rank": {"type": "ranking", "k": 3, "mode": "set"},
            "deep.path": {"type": "label"},
        },
        "pinned": False,
        "store": False,
        "match": {"system_prefix": "You extract", "schema_name": "everything", "tools": ["lookup"]},
        "routing": {
            "student_max_input_tokens": 1800,
            "student_content_types": ["text"],
            "store_ineligible": True,
            "escalate_when": [
                {"field": "b", "equals": True, "action": "teacher"},
                {"field": "n", "in": [100, 200.5], "min_confidence": 0.9},
            ],
            "critical": [{"field": "l", "equals": "x", "max_miss_rate": 0.05}],  # 'x' is an alias of 'a'
        },
        "policy": {"samples": 5, "audit_rate": 0.2},
        "tools": "teacher",
        "clients": ["acme"],
    }
    task = taskspec.parse_spec(rich, "api")
    again = taskspec.parse_spec(taskspec.document(task), "api")
    assert again == task and again.spec_hash == task.spec_hash
    assert taskspec.document(again) == taskspec.document(task)
    assert list(taskspec.document(task)) == list(taskspec.SPEC_KEYS)
    for builtin in tasks.BUILTIN.values():
        copy = taskspec.parse_spec(taskspec.document(builtin), "builtin")
        assert copy == builtin and copy.spec_hash == builtin.spec_hash
    # description, clients and match are not rules: editing them keeps the hash (and so the calibration).
    other = taskspec.parse_spec({**rich, "description": "x", "clients": [], "match": {}}, "api")
    assert other.spec_hash == task.spec_hash and other != task
    reordered = taskspec.parse_spec({**rich, "fields": dict(reversed(list(rich["fields"].items())))}, "api")
    assert reordered.spec_hash != task.spec_hash  # field order is part of the rules, so storage keeps it
    led = Ledger(":memory:")
    led.save_task("everything", taskspec.document(task), task.spec_hash, "api", "ops")
    assert taskspec.parse_spec(led.saved_task("everything")["spec"], "api").spec_hash == task.spec_hash


def test_extends_copies_and_merges():
    doc = {
        "tasks": [
            {
                "name": "eu-invoices",
                "extends": "invoices",  # defined further down: order does not matter
                "description": "EU invoices",
                "fields": {"currency": {"type": "label", "values": ["EUR"]}, "po": None},
                "policy": {"audit_rate": 0.2},
            },
            {
                "name": "invoices",
                "description": "all invoices",
                "fields": {
                    "total": {"type": "number", "abs_tol": 0.01},
                    "currency": {"type": "label", "values": ["USD", "EUR"]},
                    "po": {"type": "text", "required": False},
                },
                "policy": {"samples": 5},
                "clients": ["acme"],
            },
            {"name": "ticket-triage", "extends": "ticket-triage", "pinned": True},  # the built-in one, adjusted
            {"name": "triage-copy", "extends": "ticket-triage"},  # the file's version of it
        ]
    }
    eu, invoices, triage, copy = taskspec.parse_document(doc)
    assert [f.path for f in eu.fields] == ["total", "currency"] and eu.fields[1].values == ("EUR",)
    assert (dict(eu.policy), eu.clients, eu.description) == ({"samples": 5, "audit_rate": 0.2}, ("acme",), "EU invoices")
    assert invoices.description == "all invoices" and [f.path for f in invoices.fields] == ["total", "currency", "po"]
    assert triage.pinned and triage.fields == tasks.BUILTIN["ticket-triage"].fields and triage.description == ""
    assert copy.pinned and copy.source == "file"
    # Through the API: a registered task, the task's own name included (a new version of the current one).
    tasks.register(invoices.__class__("notes", source="api", policy={"samples": 2}))
    newer = taskspec.parse_spec({"name": "notes", "extends": "notes", "policy": {"audit_rate": 0.5}}, "api")
    assert dict(newer.policy) == {"samples": 2, "audit_rate": 0.5}
    with pytest.raises(SpecError, match=r"^invalid task spec \(1 error\):\n  extends: no registered task is named 'nope'"):
        taskspec.parse_spec({"name": "x", "extends": "nope"}, "api")


# ---------- the registry ----------
def test_registry_precedence_and_sources():
    builtin = tasks.BUILTIN["ticket-triage"]
    adjusted = tasks.Task("ticket-triage", pinned=True, source="file")
    assert tasks.register(adjusted) is builtin and tasks.get("ticket-triage") is adjusted  # the file wins over the code
    for name in ("ticket-triage", "default"):  # the API never shadows a file or built-in task
        with pytest.raises(tasks.TaskConflict, match="cannot replace it") as caught:
            tasks.register(tasks.Task(name, source="api"))
        assert caught.value.existing in ("file", "builtin")
    mine = tasks.Task("mine", source="api")
    assert tasks.register(mine) is None
    with pytest.raises(tasks.TaskConflict):  # an auto task never shadows a registered name
        tasks.register(tasks.Task("mine", source="auto"))
    tasks.register(tasks.Task("auto-x", source="auto"))
    tasks.register(tasks.Task("auto-x", description="the operator's own", source="api"))  # the API replaces an auto task
    assert tasks.lookup("auto-x").source == "api"
    for source in ("adhoc", "builtin"):
        with pytest.raises(ValueError, match="cannot be registered"):
            tasks.register(tasks.Task("x", source=source))
    with pytest.raises(ValueError, match="a name is 1 to 64"):
        tasks.register(tasks.Task("two words", source="api"))
    with pytest.raises(tasks.TaskConflict, match="cannot be removed"):
        tasks.unregister("ticket-triage")
    assert tasks.unregister("mine") is mine and tasks.unregister("mine") is None and tasks.lookup("mine") is None
    tasks.restore("mine", mine)
    assert tasks.lookup("mine") is mine
    assert tasks.register(tasks.Task("mine", source="file", description="via register(task, source)"), "api").source == "api"
    tasks.register(tasks.Task("acme-only", clients=("acme",), source="api"))
    assert "acme-only" in tasks.names_for("acme") and "acme-only" not in tasks.names_for("globex")
    assert "acme-only" not in tasks.names_for(None)
    tasks.reset_builtin()
    assert tasks.REGISTRY == dict(tasks.BUILTIN) and tasks.get("mine").source == "adhoc"
    # A request's name never registers anything, and an unknown name's task is never pinned.
    assert tasks.get("brand-new") is not tasks.get("brand-new") and tasks.lookup("brand-new") is None
    with pytest.raises(ValueError, match="only an operator's spec"):
        tasks.Task("x", pinned=True, source="adhoc")


def test_registry_reads_never_see_a_half_made_registry():
    """`get` takes no lock: writers publish a new dict in one assignment, so readers see the old or the new one."""
    stop = threading.Event()
    wrong: list[str] = []

    def read() -> None:
        while not stop.is_set():
            t = tasks.get("ticket-triage")
            if t.source != "builtin" or not t.fields:
                wrong.append(t.source)

    def write(i: int) -> None:
        for j in range(200):
            name = f"w{i}-{j % 5}"
            tasks.register(tasks.Task(name, source="api"))
            tasks.unregister(name)
            tasks.reset_builtin() if j % 50 == 0 else None

    readers = [threading.Thread(target=read) for _ in range(4)]
    writers = [threading.Thread(target=write, args=(i,)) for i in range(4)]
    for t in readers + writers:
        t.start()
    for t in writers:
        t.join()
    stop.set()
    for t in readers:
        t.join()
    assert wrong == [] and all(t.source in ("builtin", "api") for t in tasks.registered())


def test_registry_cap(gw_env, monkeypatch):
    cap = len(tasks.BUILTIN) + 2
    monkeypatch.setattr(tasks, "MAX_TASKS", cap)
    tasks.register(tasks.Task("a", source="file"))
    tasks.register(tasks.Task("b", source="api"))
    with pytest.raises(tasks.RegistryFull, match=f"full \\({cap} tasks\\)"):
        tasks.register(tasks.Task("c", source="api"))
    tasks.register(tasks.Task("b", description="replacing a task at the cap is fine", source="api"))
    assert len(tasks.REGISTRY) == cap
    with pytest.raises(SpecError, match=f"tasks: at most {cap} tasks"):
        taskspec.parse_document({"tasks": [{"name": f"t{i}"} for i in range(cap + 1)]})

    gw = gw_env()  # a fresh registry: the built-in tasks only
    c = TestClient(gw.app)
    monkeypatch.setattr(gw, "MOCK_MAX_API_TASKS", 1)
    assert c.put("/ratchet/tasks/a", json={}).status_code == 200
    r = c.put("/ratchet/tasks/b", json={})
    assert r.status_code == 409 and "at most 1 API tasks" in r.json()["detail"]
    assert c.put("/ratchet/tasks/a", json={"description": "an update is not a new task"}).status_code == 200
    monkeypatch.setattr(gw, "MOCK_MAX_API_TASKS", 50)
    assert c.put("/ratchet/tasks/b", json={}).status_code == 200
    r = c.put("/ratchet/tasks/c", json={})
    assert r.status_code == 409 and "registry is full" in r.json()["detail"]
    assert gw.rt.ledger.saved_task("c") is None and tasks.lookup("c") is None


# ---------- the admin key ----------
@pytest.mark.req("INV-13", "FR-1.7")
def test_put_task_requires_admin_key(gw_env):
    gw = gw_env(RATCHET_ADMIN_KEYS=f"ops:{ADMIN_KEY}", RATCHET_API_KEYS=f"acme:{ACME_KEY}")
    c = TestClient(gw.app)
    url = "/ratchet/tasks/support-triage"
    r = c.put(url, json=SUPPORT_BODY)
    assert (r.status_code, r.json()["detail"], r.headers["www-authenticate"]) == (401, "invalid or missing admin key", "Bearer")
    assert c.put(url, json=SUPPORT_BODY, headers={"Authorization": "Bearer not-a-key-0123456789"}).status_code == 401
    r = c.put(url, json=SUPPORT_BODY, headers=ACME)  # a client's API key is not an admin key
    assert r.status_code == 403 and "admin key" in r.json()["detail"]
    for call in (lambda h: c.delete(url, headers=h), lambda h: c.post("/ratchet/tasks/validate", json=SUPPORT, headers=h)):
        assert call({}).status_code == 401 and call(ACME).status_code == 403
    assert tasks.lookup("support-triage") is None and gw.rt.ledger.saved_tasks() == []
    assert c.get("/ratchet/tasks").status_code == 200  # reads stay open, like /ratchet/stats

    r = c.put(url, json=SUPPORT_BODY, headers={"X-API-Key": ADMIN_KEY})
    assert r.status_code == 200 and r.json()["created"]
    assert gw.rt.ledger.saved_task("support-triage")["updated_by"] == "ops"
    assert c.get(url).json()["updated_by"] == "ops"
    (event,) = [e for e in gw.rt.ledger.events() if e["kind"] == "task_registered"]
    assert event["detail"] == f"support-triage (spec {tasks.get('support-triage').spec_hash}) by ops"
    dump = "\n".join(gw.rt.ledger.db.iterdump())
    assert ADMIN_KEY not in dump and ACME_KEY not in dump  # only names are stored (INV-13)


@pytest.mark.req("INV-13")
def test_admin_keys_are_their_own(gw_env):
    gw = gw_env()
    with pytest.raises(ValueError, match="RATCHET_ADMIN_KEYS: the key for 'ops' is also an API key"):
        gw.Runtime(Settings(mode="mock", api_keys=f"acme:{ACME_KEY}", admin_keys=f"ops:{ACME_KEY}", mock_warmup_days=0))
    with pytest.raises(ValueError, match="RATCHET_ADMIN_KEYS: the key for 'ops' is shorter than 16"):
        gw.Runtime(Settings(mode="mock", admin_keys="ops:short", mock_warmup_days=0))
    with pytest.raises(ValueError, match="RATCHET_API_KEYS: name 'acme' appears twice"):
        KeyRing(f"acme:{ACME_KEY},acme:{GLOBEX_KEY}")
    admins, clients = KeyRing(f"ops:{ADMIN_KEY},bot:{ACME_KEY}", env="RATCHET_ADMIN_KEYS"), KeyRing(f"acme:{ACME_KEY}")
    assert (admins.env, admins.shared(clients), clients.shared(admins)) == ("RATCHET_ADMIN_KEYS", ["bot"], ["acme"])


def test_admin_disabled_in_live_without_keys(gw_env, tmp_path, monkeypatch):
    gw = gw_env()  # mock mode without admin keys: open, as 'mock-admin' (the shared demo has nothing to protect)
    c = TestClient(gw.app)
    assert c.put("/ratchet/tasks/notes", json={}, headers=ACME).status_code == 200
    assert gw.rt.ledger.saved_task("notes")["updated_by"] == gw.MOCK_ADMIN == "mock-admin"

    teacher = FakeOpenAIServer("ok")
    rt = gw.Runtime(_live(tmp_path), transports={"teacher": teacher.transport})
    monkeypatch.setattr(gw, "rt", rt)
    c = TestClient(gw.app)
    for call in (
        lambda: c.put("/ratchet/tasks/notes", json={}, headers=ADMIN),
        lambda: c.delete("/ratchet/tasks/notes"),
        lambda: c.post("/ratchet/tasks/validate", json={"name": "notes"}),
    ):
        r = call()
        assert (r.status_code, r.json()) == (403, {"detail": "admin API disabled: set RATCHET_ADMIN_KEYS"})
    assert c.get("/ratchet/tasks").status_code == 200 and tasks.lookup("notes") is None
    assert teacher.calls == []


@pytest.mark.req("INV-13")
def test_put_builtin_task_forbidden(gw_env, tmp_path):
    gw = gw_env(RATCHET_TASKS_FILE=str(_file(tmp_path, {"tasks": [SUPPORT]})))
    c = TestClient(gw.app)
    builtin = "task 'ticket-triage' is built in; use another name (a spec may extend it)"
    r = c.put("/ratchet/tasks/ticket-triage", json={"pinned": True})
    assert (r.status_code, r.json()["detail"]) == (403, builtin)
    r = c.put("/ratchet/tasks/support-triage", json={"pinned": True})
    assert (r.status_code, r.json()["detail"]) == (403, "task 'support-triage' is defined in RATCHET_TASKS_FILE; edit the file")
    assert c.delete("/ratchet/tasks/ticket-triage").status_code == 403
    assert c.delete("/ratchet/tasks/support-triage").status_code == 403
    assert c.delete("/ratchet/tasks/nope").status_code == 404
    r = c.post("/ratchet/tasks/validate", json={"name": "ticket-triage"}).json()
    assert not r["valid"] and r["errors"] == [f"name: {builtin}"]
    assert not tasks.get("ticket-triage").pinned and tasks.get("support-triage").source == "file"
    assert not tasks.get("support-triage").pinned and gw.rt.ledger.saved_tasks() == []


# ---------- operator-only pinning ----------
@pytest.mark.req("INV-10", "FR-2.9", "FR-1.3")
def test_client_cannot_pin_via_header_metadata_model_or_schema(gw_env):
    gw = gw_env()
    c = TestClient(gw.app)
    msgs = gw.rt.stream.sample(1).messages()
    schema = {
        "type": "json_schema",
        "json_schema": {"name": "triage", "schema": {"type": "object", "x-ratchet": {"pinned": True, "compare": "ignore"}}},
    }
    attempts = [
        ("by-header", {"messages": msgs}, {"X-Ratchet-Task": "by-header", "X-Ratchet-Pinned": "true", "X-Ratchet-Pin": "1"}),
        ("by-metadata", {"messages": msgs, "metadata": {"ratchet_task": "by-metadata", "ratchet_pinned": "true"}}, {}),
        ("by-model", {"messages": msgs, "model": "pinned", "metadata": {"ratchet_task": "by-model"}}, {}),
        (
            "by-schema",
            {"messages": msgs, "response_format": schema, "x-ratchet": {"pinned": True}},
            {"X-Ratchet-Task": "by-schema"},
        ),
    ]
    for name, body, headers in attempts:
        r = c.post(CHAT, json=body, headers=headers)
        assert r.status_code == 200, (name, r.text)
        assert not tasks.get(name).pinned and tasks.lookup(name) is None
    rows = _rows(gw.rt.ledger, "SELECT task, route, stored FROM requests ORDER BY id")
    assert rows == [{"task": name, "route": "cold_start", "stored": 1} for name, _, _ in attempts]  # distilled, not pinned
    assert sorted(r["task"] for r in gw.rt.ledger.examples()) == sorted(name for name, _, _ in attempts)
    # Nor can a request unpin a pinned task.
    c.post(CHAT, json={"messages": msgs}, headers={"X-Ratchet-Task": "research-write", "X-Ratchet-Pinned": "false"})
    last = _rows(gw.rt.ledger, "SELECT route, stored FROM requests ORDER BY id DESC LIMIT 1")
    assert last == [{"route": "pinned", "stored": 0}]
    with pytest.raises(ValueError, match="never pinned"):
        tasks.Task("by-header", pinned=True, source="adhoc")
    with pytest.raises(ValueError, match="never pinned"):
        tasks.Task("auto-triage", pinned=True, source="auto")


# ---------- the API task's life ----------
def test_task_lifecycle_through_the_admin_api(gw_env):
    gw = gw_env(RATCHET_ADMIN_KEYS=f"ops:{ADMIN_KEY}")
    c = TestClient(gw.app)
    url = "/ratchet/tasks/support-triage"
    first = c.put(url, json=SUPPORT_BODY, headers=ADMIN).json()
    assert (first["created"], first["changed"], first["task"]["source"], first["task"]["requests"]) == (True, True, "api", 0)
    old_hash = first["spec_hash"]
    again = c.put(url, json=SUPPORT_BODY, headers=ADMIN).json()
    assert (again["created"], again["changed"], again["spec_hash"]) == (False, False, old_hash)
    events = lambda kind: [e["detail"] for e in gw.rt.ledger.events() if e["kind"] == kind]  # noqa: E731
    assert len(events("task_registered")) == 1 and events("task_changed") == []

    msgs = gw.rt.stream.sample(1).messages()
    assert c.post(CHAT, json={"messages": msgs}, headers={"X-Ratchet-Task": "support-triage"}).status_code == 200
    assert c.get(url).json()["requests"] == 1 and len(gw.rt.ledger.examples(task="support-triage")) == 1

    priority = {"type": "ordinal", "order": list(vocab.TICKET_PRIORITIES), "tolerance": 1}
    ordinal = {**SUPPORT_BODY, "fields": {**SUPPORT_BODY["fields"], "priority": priority}}
    changed = c.put(url, json=ordinal, headers=ADMIN).json()
    new_hash = changed["spec_hash"]
    assert changed["changed"] and not changed["created"] and new_hash != old_hash
    assert events("task_changed") == [f"support-triage: spec {old_hash} -> {new_hash} by ops"]
    c.put(url, json={**ordinal, "description": "now with a priority tolerance"}, headers=ADMIN)
    assert events("task_changed")[-1] == f"support-triage: spec {new_hash} kept; description, clients or match by ops"
    # The live version was scored under the first spec, so the listing says the task is waiting for recalibration.
    _version(gw.rt.ledger, "live", {"threshold": 0.5, "spec_hashes": {"support-triage": old_hash}})
    assert c.get(url).json()["recalibrating"] and not c.get("/ratchet/tasks/ticket-triage").json()["recalibrating"]

    for content, message in (
        (b"{", "$: not valid JSON: Expecting property name enclosed in double quotes (line 1, column 2)"),
        (b'{"policy": {"audit_rate": NaN}}', "$: not valid JSON: NaN is not JSON"),
        (b'{"name": "other"}', "name: the body names 'other' but the URL names 'support-triage'"),
        (b"[]", "$: must be an object (one task spec)"),
        (b'{"description": "cut \\ud83d"}', "description: an unpaired UTF-16 surrogate '\\ud83d' is not valid Unicode"),
        (json.dumps({"fields": {f"f{i}": {"type": "bool"} for i in range(65)}}).encode(), "fields: at most 64 fields"),
        (b'{"fields": {"total": {"type": "number", "abs_tol": "a cent"}}}', "fields.total.abs_tol: must be a number >= 0"),
    ):
        r = c.put(url, content=content, headers=ADMIN)
        assert (r.status_code, r.json()) == (422, {"detail": "invalid task spec", "errors": [message]}), content
    r = c.put("/ratchet/tasks/bad%20name", json={}, headers=ADMIN)
    assert r.status_code == 422 and r.json()["errors"][0].startswith("name: 'bad name' is not a task name")
    assert tasks.get("support-triage").spec_hash != old_hash  # the refused bodies changed nothing

    final_hash = tasks.get("support-triage").spec_hash
    r = c.delete(url, headers=ADMIN)
    assert r.json() == {"deleted": "support-triage", "spec_hash": final_hash, "examples_kept": 1}
    assert c.get(url).status_code == 404 and gw.rt.ledger.saved_task("support-triage") is None
    assert events("task_deleted")[0].endswith("by ops; 1 stored examples kept")
    # The name now follows RATCHET_UNKNOWN_TASKS (distill): an adhoc task, and the old example is still there.
    assert c.post(CHAT, json={"messages": msgs}, headers={"X-Ratchet-Task": "support-triage"}).status_code == 200
    assert len(gw.rt.ledger.examples(task="support-triage")) == 1
    assert c.get("/ratchet/tasks").json()["unregistered"] == [{"name": "support-triage", "requests": 2}]
    assert c.delete(url, headers=ADMIN).status_code == 404


def test_api_task_persists_across_runtime_restart(gw_env, tmp_path, monkeypatch):
    gw = gw_env()
    teacher = FakeOpenAIServer('{"vendor": "Acme Inc", "total": "USD 12.50"}')
    settings = _live(tmp_path, admin_keys=f"ops:{ADMIN_KEY}")
    rt = gw.Runtime(settings, transports={"teacher": teacher.transport})
    monkeypatch.setattr(gw, "rt", rt)
    c = TestClient(gw.app)
    created = c.put("/ratchet/tasks/invoice-extract", json=INVOICE_BODY, headers=ADMIN).json()
    assert created["created"] and created["task"]["fields"] == INVOICE_BODY["fields"]
    r = c.post(CHAT, json={"messages": _ask("inv")}, headers={"X-Ratchet-Task": "invoice-extract"})
    assert r.status_code == 200 and r.json()["ratchet"]["served_by"] == "teacher"
    assert [e["task"] for e in rt.ledger.examples()] == ["invoice-extract"]

    tasks.reset_builtin()  # a new process starts with the built-in tasks only
    rt2 = gw.Runtime(settings, transports={"teacher": teacher.transport})
    monkeypatch.setattr(gw, "rt", rt2)
    restored = tasks.lookup("invoice-extract")
    assert (restored.source, restored.spec_hash) == ("api", created["spec_hash"])
    entry = c.get("/ratchet/tasks/invoice-extract").json()
    assert (entry["updated_by"], entry["requests"]) == ("ops", 1) and entry["created_ts"] <= entry["updated_ts"]
    looser = {"fields": {**INVOICE_BODY["fields"], "total": {"type": "number", "abs_tol": 0.05}}}
    changed = c.put("/ratchet/tasks/invoice-extract", json=looser, headers=ADMIN).json()
    assert changed["changed"] and not changed["created"]
    details = [e["detail"] for e in rt2.ledger.events() if e["kind"] == "task_changed"]
    assert details == [f"invoice-extract: spec {created['spec_hash']} -> {changed['spec_hash']} by ops"]
    assert c.delete("/ratchet/tasks/invoice-extract", headers=ADMIN).json()["examples_kept"] == 1

    tasks.reset_builtin()
    rt3 = gw.Runtime(settings, transports={"teacher": teacher.transport})
    assert tasks.lookup("invoice-extract") is None and len(rt3.ledger.examples(task="invoice-extract")) == 1


def test_stored_task_that_no_longer_parses_is_skipped(gw_env, tmp_path):
    db = str(tmp_path / "live.db")
    led = Ledger(db)
    good = tasks.Task("good", source="api", description="still fine")
    led.save_task("good", taskspec.document(good), good.spec_hash, "api", "ops")
    led.save_task("broken", {"name": "broken", "fields": {"x": {"type": "similarity"}}}, "0" * 16, "api", "ops")
    clash = tasks.Task("support-triage", source="api")
    led.save_task("support-triage", taskspec.document(clash), clash.spec_hash, "api", "ops")
    led.db.execute("INSERT INTO tasks (name, spec, source) VALUES ('garbled', '{not json', 'api')")
    led.db.commit()
    path = _file(tmp_path, {"tasks": [SUPPORT]})  # the file now defines a name the API stored

    skipped = taskspec.install(led, path)
    assert tasks.lookup("good") == good and tasks.lookup("broken") is None and tasks.lookup("garbled") is None
    assert tasks.lookup("support-triage").source == "file"
    assert skipped == [  # oldest first; the hand-written row has no created_ts
        "garbled: the stored spec is no longer valid ($: must be an object (one task spec)); it is not registered",
        "broken: the stored spec is no longer valid (policy.samples: a task with a similarity field needs policy.samples"
        " of at least 2 (one sample always agrees with itself)); it is not registered",
        "support-triage: task 'support-triage' is defined in RATCHET_TASKS_FILE; an api task cannot replace it; "
        "the stored api task is not registered",
    ]
    # The gateway starts anyway and logs each one; the stored rows stay for the operator to fix or delete.
    gw = gw_env()
    rt = gw.Runtime(_live(tmp_path, db_path=db, tasks_file=str(path)), transports={"teacher": FakeOpenAIServer("ok").transport})
    assert [e["detail"] for e in rt.ledger.events() if e["kind"] == "task_skipped"] == skipped
    assert len(rt.ledger.saved_tasks()) == 4


def test_validate_endpoint_has_no_side_effects(gw_env):
    gw = gw_env()
    c = TestClient(gw.app)
    registry, events, stored = tasks.REGISTRY, gw.rt.ledger.events(), gw.rt.ledger.saved_tasks()
    r = c.post("/ratchet/tasks/validate", json=SUPPORT).json()
    spec_hash = taskspec.parse_spec(SUPPORT).spec_hash
    assert r == {"valid": True, "errors": [], "tasks": [{"name": "support-triage", "spec_hash": spec_hash, "registered": None}]}
    bad = {"tasks": [SUPPORT, {"name": "x", "fields": {"a": {"type": "similarity"}}}]}
    assert c.post("/ratchet/tasks/validate", json=bad).json() == {
        "valid": False,
        "errors": [
            "tasks[1].policy.samples: a task with a similarity field needs policy.samples of at least 2 (one sample always"
            " agrees with itself)"
        ],
        "tasks": [],
    }
    r = c.post("/ratchet/tasks/validate", content=b"{nope").json()
    assert not r["valid"] and r["errors"][0].startswith("$: not valid JSON")
    # A document is checked as a tasks file, which may adjust a built-in task; one spec as PUT would take it.
    adjust = {"tasks": [{"name": "ticket-triage", "extends": "ticket-triage", "policy": {"samples": 5}}]}
    r = c.post("/ratchet/tasks/validate", json=adjust).json()
    assert r["valid"] and r["tasks"][0]["registered"] == "builtin"
    assert not c.post("/ratchet/tasks/validate", json={"name": "ticket-triage"}).json()["valid"]
    assert tasks.REGISTRY is registry and gw.rt.ledger.events() == events and gw.rt.ledger.saved_tasks() == stored
    tasks.register(tasks.Task("auto-x", source="auto"))  # PUT replaces an auto task, so validate takes its name too
    assert c.post("/ratchet/tasks/validate", json={"name": "auto-x"}).json()["valid"]
    assert c.put("/ratchet/tasks/auto-x", json={}).status_code == 200


def test_validate_checks_a_document_as_startup_reads_it(gw_env, tmp_path, monkeypatch):
    """A document is valid only when RATCHET_TASKS_FILE would start with it. Startup registers the file before the
    stored API tasks, so `extends` reaches the built-in tasks and the file's own, never an API task or a task of the
    file running now; and the file's tasks must fit in the registry with the built-in tasks they do not replace."""
    gw = gw_env()
    c = TestClient(gw.app)
    assert c.put("/ratchet/tasks/invoices", json=INVOICE_BODY).status_code == 200
    tasks.register(tasks.Task("old-file-task", source="file"))  # the running file's task, which the new file drops
    for base in ("invoices", "old-file-task"):
        doc = {"tasks": [{"name": "eu-invoices", "extends": base}]}
        error = f"tasks[0].extends: no task is named '{base}' in this file or the built-in tasks"
        assert c.post("/ratchet/tasks/validate", json=doc).json() == {"valid": False, "errors": [error], "tasks": []}
        with pytest.raises(SpecError) as caught:  # what the restart would say
            taskspec.install(Ledger(":memory:"), _file(tmp_path, doc))
        assert caught.value.errors == [error]
    good = {
        "tasks": [{"name": "eu", "extends": "base"}, {"name": "base", **INVOICE_BODY}, {"name": "t", "extends": "ticket-triage"}]
    }
    assert c.post("/ratchet/tasks/validate", json=good).json()["valid"]

    cap = len(tasks.BUILTIN) + 2
    monkeypatch.setattr(tasks, "MAX_TASKS", cap)
    fits = {"tasks": [{"name": "a"}, {"name": "b"}, {"name": "default", "extends": "default"}]}  # a replacement takes no room
    too_many = {"tasks": [{"name": "a"}, {"name": "b"}, {"name": "c"}]}
    error = f"tasks: 3 tasks and the {cap - 2} built-in tasks this file does not replace come to {cap + 1}; "
    error += f"the registry holds at most {cap}"
    assert c.post("/ratchet/tasks/validate", json=fits).json()["valid"]
    assert c.post("/ratchet/tasks/validate", json=too_many).json() == {"valid": False, "errors": [error], "tasks": []}
    with pytest.raises(SpecError) as caught:
        taskspec.install(Ledger(":memory:"), _file(tmp_path, too_many))
    assert caught.value.errors == [error]
    assert taskspec.install(Ledger(":memory:"), _file(tmp_path, fits)) == [] and len(tasks.REGISTRY) == cap


def test_install_publishes_the_registry_whole(gw_env, tmp_path, monkeypatch):
    """`install` builds the registry aside and publishes it in one assignment: a request on another thread (during a
    mock demo reset) never sees it without the file's or the stored tasks, and a file that went bad leaves it as it was."""
    path = _file(tmp_path, {"tasks": [{**SUPPORT, "pinned": True}]})
    led = Ledger(":memory:")
    notes = tasks.Task("notes", source="api")
    led.save_task("notes", taskspec.document(notes), notes.spec_hash, "api", "ops")
    assert taskspec.install(led, path) == []
    published = tasks.REGISTRY
    during: list[dict[str, tasks.Task]] = []
    saved_tasks = led.saved_tasks

    def spy() -> list[dict[str, Any]]:  # runs after the file is parsed, before the stored tasks are added
        during.append(tasks.REGISTRY)
        return saved_tasks()

    monkeypatch.setattr(led, "saved_tasks", spy)
    taskspec.install(led, path)
    assert during == [published] and during[0] is published  # what requests saw meanwhile: the old registry, whole
    assert tasks.REGISTRY is not published and set(tasks.REGISTRY) == set(published)
    assert tasks.lookup("support-triage").pinned and tasks.lookup("notes") == notes

    gw = gw_env(RATCHET_TASKS_FILE=str(path))
    before = tasks.REGISTRY
    path.write_text(json.dumps({"tasks": [{"name": "support-triage", "feilds": {}}]}), encoding="utf-8")
    with pytest.raises(SpecError, match="feilds"):
        gw.rt.reset()  # a mock demo reset after the file went bad on disk
    assert tasks.REGISTRY is before and tasks.get("support-triage").pinned  # still the operator's pinned task


def test_concurrent_admin_writes_keep_the_registry_and_the_table_in_step(gw_env):
    gw = gw_env()
    errors: list[BaseException] = []

    def work(i: int) -> None:
        try:
            for j in range(3):
                name = f"t{i}-{j}"
                gw._put_task(name, json.dumps({"description": f"v1 {i}"}).encode(), "mock-admin")
                gw._put_task(name, json.dumps({"description": f"v2 {i}"}).encode(), "mock-admin")
                if j == 2:
                    gw.delete_task(name, "mock-admin")
        except BaseException as e:  # noqa: BLE001 - reported below
            errors.append(e)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    api = sorted(t.name for t in tasks.registered() if t.source == "api")
    assert api == sorted(f"t{i}-{j}" for i in range(8) for j in range(2))
    assert sorted(r["name"] for r in gw.rt.ledger.saved_tasks()) == api
    assert all(tasks.get(name).description.startswith("v2") for name in api)
    kinds = [e["kind"] for e in gw.rt.ledger.events()]
    assert (kinds.count("task_registered"), kinds.count("task_changed"), kinds.count("task_deleted")) == (24, 24, 8)


# ---------- the unknown-task policy and the client allow-list ----------
@pytest.mark.req("FR-1.3", "INV-10")
def test_unknown_tasks_teacher_policy_stores_nothing(gw_env):
    led = Ledger(":memory:")
    student, teacher = RecordingProvider(TRIAGE_ANSWER), RecordingProvider(TRIAGE_ANSWER)
    s = Settings(teacher_model=OPEN_TEACHER, samples=3, audit_rate=1.0, unknown_tasks=" Teacher ")
    r = Ratchet(s, led, teacher, lambda v: (student, v.adapter_uri), seed=1)
    _version(led, "live", {"threshold": 0.5})
    _version(led, "shadow", {"threshold": 0.5})
    res = r.handle(_ask(1), "brand-new")
    assert (res.route, res.served_by, res.version, res.text) == ("unknown_task", "teacher", None, TRIAGE_ANSWER)
    assert student.calls == [] and len(teacher.calls) == 1 and led.examples() == []
    assert _rows(led, "SELECT task, route, stored, served_by FROM requests") == [
        {"task": "brand-new", "route": "unknown_task", "stored": 0, "served_by": "teacher"}
    ]
    # A request only the teacher reads (a RATCHET_TEACHER_MODALITIES part) keeps the policy's route; a pinned task its own.
    assert r.teacher_only(_ask(3), "brand-new").route == "unknown_task"
    assert r.teacher_only(_ask(4), "research-plan").route == "pinned"
    assert [row["route"] for row in _rows(led, "SELECT route FROM requests ORDER BY id")][1:] == ["unknown_task", "pinned"]
    assert student.calls == [] and led.examples() == []
    assert r.handle(_ask(2), "ticket-triage").route == "student"  # a registered task is still distilled
    assert r.task_for("brand-new").source == "adhoc"  # admitted, and answered by the teacher alone
    # A header that is not a task name cannot become 'default' training data under this policy (v0.4 keeps 'default').
    with pytest.raises(tasks.UnknownTask, match=r"^'bad name!' is not a task name \(1 to 64 letters"):
        r.task_for("bad name!")
    with pytest.raises(tasks.UnknownTask, match=r"^'x{37}\.\.\.' is not a task name"):
        r.task_for("x" * 100 + "!")
    distill = Ratchet(Settings(teacher_model=OPEN_TEACHER, unknown_tasks="distill"), led, teacher, lambda v: (student, "x"))
    assert distill.task_for("bad name!").name == "default"
    with pytest.raises(ValueError, match="RATCHET_UNKNOWN_TASKS must be distill, teacher, reject, not 'teach'"):
        Ratchet(Settings(teacher_model=OPEN_TEACHER, unknown_tasks="teach"), led, teacher, lambda v: (student, "x"))
    assert Settings(unknown_tasks="").unknown_task_policy() == "distill"

    gw = gw_env(RATCHET_UNKNOWN_TASKS="teacher")  # the same through the gateway
    c = TestClient(gw.app)
    msgs = gw.rt.stream.sample(1).messages()
    r = c.post(CHAT, json={"messages": msgs}, headers={"X-Ratchet-Task": "brand-new"})
    assert r.status_code == 200 and r.json()["ratchet"]["served_by"] == "teacher"
    assert gw.rt.ledger.examples() == [] and c.get("/ratchet/tasks").json()["unknown_tasks"] == "teacher"
    r = c.post(CHAT, json={"messages": msgs}, headers={"X-Ratchet-Task": "bad name!"})
    assert (r.status_code, r.json()["error"]["code"], r.json()["error"]["param"]) == (400, "unknown_task", None)
    assert gw.rt.ledger.examples() == [] and gw.rt.ledger.totals()["requests"] == 1


@pytest.mark.req("FR-14.40", "FR-2.15", "INV-10")
def test_unknown_free_text_not_stored_under_teacher_policy(gw_env, tmp_path):
    """Free text no operator registered (a summary, a reply) stays out of training under RATCHET_UNKNOWN_TASKS=teacher:
    the teacher answers, nothing is stored and the student is never asked. Under the default, distill, the same request
    becomes training data, compared as a whole answer. A long-form task the operator pinned is never stored either way,
    and a short free-text task registered with a similarity field (and samples) is distilled."""
    doc = {
        "tasks": [
            {"name": "long-form-writing", "pinned": True},
            {"name": "short-summary", "format": "text", "fields": {"$": {"type": "similarity"}}, "policy": {"samples": 3}},
        ]
    }
    path = _file(tmp_path, doc)
    ask = [{"role": "user", "content": "Summarise for the team: the Pune shipment is two days late after a customs check."}]
    for policy, stored in (("teacher", 0), ("distill", 1)):
        gw = gw_env(RATCHET_UNKNOWN_TASKS=policy, RATCHET_TASKS_FILE=str(path))
        c = TestClient(gw.app)
        r = c.post(CHAT, json={"messages": ask}, headers={"X-Ratchet-Task": "team-summary"})
        assert r.status_code == 200 and r.json()["ratchet"]["served_by"] == "teacher", policy
        assert r.json()["choices"][0]["message"]["content"].startswith("[mock teacher]")  # free text, from the mock world
        assert len(gw.rt.ledger.examples(task="team-summary")) == stored, policy
        route = _rows(gw.rt.ledger, "SELECT route, stored FROM requests ORDER BY id DESC LIMIT 1")
        assert route == [{"route": "unknown_task" if policy == "teacher" else "cold_start", "stored": stored}]
        for name, kept in (("long-form-writing", 0), ("short-summary", 1)):
            r = c.post(CHAT, json={"messages": ask}, headers={"X-Ratchet-Task": name})
            assert r.status_code == 200 and len(gw.rt.ledger.examples(task=name)) == kept, (policy, name)
        assert tasks.get("short-summary").precision_kind == "proxy"


@pytest.mark.req("FR-1.3")
def test_unknown_tasks_reject_policy_400(gw_env):
    gw = gw_env(RATCHET_UNKNOWN_TASKS="reject", RATCHET_API_KEYS=f"acme:{ACME_KEY}", RATCHET_API_KEY_DAILY_REQUESTS="1")
    c = TestClient(gw.app)
    msgs = gw.rt.stream.sample(1).messages()
    registered = "default, page-read, research-plan, research-write, source-triage, ticket-triage"
    r = c.post(CHAT, json={"messages": msgs}, headers={**ACME, "X-Ratchet-Task": "nope"})
    assert r.status_code == 400 and r.json()["error"] == {
        "message": f'unknown task "nope"; registered: {registered}',
        "type": "invalid_request_error",
        "param": None,
        "code": "unknown_task",
    }
    r = c.post(CHAT, json={"messages": msgs, "metadata": {"ratchet_task": "nope"}}, headers=ACME)
    assert (r.status_code, r.json()["error"]["param"]) == (400, "metadata.ratchet_task")
    r = c.post(CHAT, json={"messages": msgs}, headers={**ACME, "X-Ratchet-Task": "bad name!"})  # not 'default'
    assert r.status_code == 400 and r.json()["error"] == {
        "message": f"'bad name!' is not a task name (1 to 64 letters, digits, '_', '.' or '-'); registered: {registered}",
        "type": "invalid_request_error",
        "param": None,
        "code": "unknown_task",
    }
    assert gw.rt.ledger.totals()["requests"] == 0  # refused before the quota: nothing logged, nothing used
    ok = c.post(CHAT, json={"messages": msgs}, headers={**ACME, "X-Ratchet-Task": "ticket-triage"})
    assert ok.status_code == 200 and ok.headers["x-ratelimit-remaining-requests"] == "0"
    with pytest.raises(tasks.UnknownTask, match=r'^unknown task "nope"; registered: '):
        gw.rt.ratchet.handle(msgs, "nope")  # direct callers get the same rule
    for i in range(25):
        tasks.register(tasks.Task(f"extra-{i:02d}", source="api"))
    with pytest.raises(tasks.UnknownTask, match=r"default, extra-00, .*, extra-18 and 11 more$"):
        gw.rt.ratchet.task_for("nope")  # at most 20 names


@pytest.mark.req("INV-13")
def test_clients_allow_list_403(gw_env):
    gw = gw_env(RATCHET_API_KEYS=f"acme:{ACME_KEY},globex:{GLOBEX_KEY}", RATCHET_ADMIN_KEYS=f"ops:{ADMIN_KEY}")
    c = TestClient(gw.app)
    url = "/ratchet/tasks/acme-triage"
    r = c.put(url, json={**SUPPORT_BODY, "clients": ["acme", "initech"]}, headers=ADMIN)
    assert (r.status_code, r.json()["errors"]) == (422, ["clients[1]: no API key is named 'initech' in RATCHET_API_KEYS"])
    assert c.put(url, json={**SUPPORT_BODY, "clients": ["acme"]}, headers=ADMIN).status_code == 200
    msgs = gw.rt.stream.sample(1).messages()
    r = c.post(CHAT, json={"messages": msgs}, headers={**GLOBEX, "X-Ratchet-Task": "acme-triage"})
    assert r.status_code == 403 and r.json()["error"] == {
        "message": "task not allowed for this key",
        "type": "permission_error",
        "param": None,
        "code": "task_not_allowed",
    }
    assert gw.rt.ledger.totals()["requests"] == 0
    assert c.post(CHAT, json={"messages": msgs}, headers={**ACME, "X-Ratchet-Task": "acme-triage"}).status_code == 200
    assert [r["client"] for r in _rows(gw.rt.ledger, "SELECT client FROM requests")] == ["acme"]
    # Rejected names list only the tasks the key may use.
    gw.rt.ratchet.unknown_tasks = "reject"
    assert "acme-triage" in c.post(CHAT, json={"messages": msgs}, headers={**ACME, "X-Ratchet-Task": "x"}).text
    assert "acme-triage" not in c.post(CHAT, json={"messages": msgs}, headers={**GLOBEX, "X-Ratchet-Task": "x"}).text
    # Without API keys a request has no name to check, so a task with clients refuses every request (fail closed).
    assert not tasks.get("acme-triage").allows(None)


# ---------- Night School: pinned rows left out at read time, and spec changes ----------
@pytest.mark.req("INV-10", "FR-2.9", "INV-2")
def test_pinning_task_excludes_existing_examples_from_dataset_and_eval(tmp_path):
    led = Ledger(":memory:")
    tasks.register(tasks.Task("notes", source="file"))
    _examples(led, 6, 4, task="notes")
    _examples(led, 5, 3, task="default", prefix="other")
    golden_before = dataset.rows(led, "golden")
    assert {r["task"] for r in dataset.rows(led, "train")} == {"notes", "default"}

    tasks.register(tasks.Task("notes", pinned=True, source="file"))  # the operator pins a task that already has data
    counts: dict[str, int] = {}
    train = dataset.rows(led, "train", counts=counts)
    assert {r["task"] for r in train} == {"default"} and len(train) == 5 and counts == {"excluded_pinned": 6}
    manifest = dataset.build(led, tmp_path)
    assert (manifest["train_examples"], manifest["golden_examples"], manifest["excluded_pinned"]) == (5, 3, 10)
    student = RecordingProvider("teacher answer")
    res = cascade_eval(lambda p, n, params: student.complete(p, "m", n=n, logprobs=True), golden_before)
    assert (res["golden_n"], res["excluded_pinned"]) == (3, 4)
    assert not any(call.messages[0]["content"].startswith("request") for call in student.calls)  # never sampled

    trainer, evaluator = RecordingTrainer(), RecordingEvaluator(REJECTED_EVAL)
    rep = run_night(led, trainer, evaluator, ReleaseGate(led, ReleasePolicy()), "base", min_new_examples=1)
    assert rep.trained and {r["task"] for r in trainer.calls[0].rows} == {"default"}
    assert {r["task"] for r in evaluator.calls[0].golden_rows} == {"default"}
    assert rep.eval["spec_hashes"] == {"default": tasks.get("default").spec_hash}

    tasks.register(tasks.Task("notes", source="file"))  # unpinned: the ledger kept the rows, so they come back
    assert len(dataset.rows(led, "train")) == 11 and "excluded_pinned" not in cascade_eval(
        lambda p, n, params: student.complete(p, "m", n=n, logprobs=True), golden_before
    )


@pytest.mark.req("FR-2.3", "INV-4")
def test_spec_change_forces_teacher_until_recalibrated():
    led = Ledger(":memory:")
    spec = {"name": "amounts", "fields": {"total": {"type": "number"}}}
    tasks.register(taskspec.parse_spec(spec, "file"))
    student, teacher = RecordingProvider('{"total": 12.5}'), RecordingProvider('{"total": 12.5}')
    r = Ratchet(Settings(teacher_model=OPEN_TEACHER, samples=3, audit_rate=0.0), led, teacher, lambda v: (student, v.adapter_uri))
    old = tasks.get("amounts").spec_hash
    vid = _version(led, "live", {"threshold": 0.5, "spec_hashes": {"amounts": old}})
    assert r.handle(_ask(1), "amounts").route == "student"

    tasks.register(taskspec.parse_spec({**spec, "fields": {"total": {"type": "number", "abs_tol": 0.01}}}, "file"))
    new = tasks.get("amounts").spec_hash
    version = led.version(vid)
    assert new != old and r.spec_changed(version, "amounts")
    assert r.threshold(version, "student-v1", tasks.get("amounts")) == SERVE_NOTHING
    assert r.threshold(version, "student-v1", tasks.get("ticket-triage")) == 0.5  # no record for it: its threshold
    assert r.threshold(version, "student-v1") == 0.5  # without a task, as before
    res = r.handle(_ask(2), "amounts")
    assert (res.route, res.served_by, res.confidence) == ("confidence", "teacher", pytest.approx(0.98, abs=0.01))

    # A night re-scores the live version under the new spec, even one without enough new data to train.
    _examples(led, 0, 2, task="amounts", prefix="amount")
    evaluator = RecordingEvaluator({"threshold": 0.5, "precision": 1.0, "coverage": 1.0, "golden_n": 2})
    gate = ReleaseGate(led, ReleasePolicy())
    rep = run_night(led, RecordingTrainer(), evaluator, gate, "base")
    assert (rep.trained, rep.reason) == (False, "not enough new data")
    assert [call.adapter_uri for call in evaluator.calls] == ["student-v1"]
    assert led.version(vid).eval["spec_hashes"] == {"amounts": new} and not r.spec_changed(led.version(vid), "amounts")
    assert [e["detail"] for e in led.events() if e["kind"] == "rescored"] == [
        f"v{vid} re-scored on the golden set: spec changed for amounts"
    ]
    assert r.handle(_ask(3), "amounts").route == "student"
    run_night(led, RecordingTrainer(), evaluator, gate, "base")
    assert len(evaluator.calls) == 1  # nothing changed since: nothing to re-score

    # A failing re-score leaves the night a skip night and the task on the teacher.
    tasks.register(taskspec.parse_spec(spec, "file"))

    def broken(adapter_uri: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        raise ConnectionError("student endpoint down")

    run_night(led, RecordingTrainer(), RecordingEvaluator(broken), gate, "base")
    assert [e["kind"] for e in led.events()][-2:] == ["skip", "rescore_failed"]
    assert r.handle(_ask(4), "amounts").route == "confidence"


# ---------- fixtures ----------
def test_fixture_paths_resolve_inside_the_fixtures_dir(tmp_path, monkeypatch):
    root = tmp_path / "fixtures"
    (root / "sub").mkdir(parents=True)
    (root / "shop.sql").write_text("CREATE TABLE t (x);")
    assert taskspec.resolve_fixture("shop.sql", root) == (root / "shop.sql").resolve()
    assert taskspec.resolve_fixture("sub/../shop.sql", str(root)) == (root / "shop.sql").resolve()
    outside = tmp_path / "secret.txt"
    outside.write_text("no")
    os.symlink(outside, root / "link.sql")
    for bad in ("../secret.txt", str(outside), "", None, "a\x00b", "link.sql"):
        with pytest.raises(ValueError, match="RATCHET_FIXTURES_DIR"):
            taskspec.resolve_fixture(bad, root)
    monkeypatch.delenv("RATCHET_FIXTURES_DIR", raising=False)
    assert Settings().fixtures_dir == "fixtures"
    monkeypatch.setenv("RATCHET_FIXTURES_DIR", str(root))
    assert taskspec.resolve_fixture("shop.sql", Settings().fixtures_dir).name == "shop.sql"
    assert compare.MAX_DEPTH == 4  # the records limit the spec parser enforces before FieldSpec does
