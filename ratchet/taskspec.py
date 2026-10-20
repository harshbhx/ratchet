"""Task specs as data (FR-2.15, ADR 0022): a tasks file (RATCHET_TASKS_FILE) or an admin API body, parsed into
`tasks.Task` with every error and where it is.

    {"tasks": [{"name": "invoice-extract",
                "description": "Vendor, total and due date from an invoice",
                "fields": {"vendor": {"type": "text", "strip_suffixes": ["inc", "ltd"]},
                           "total": {"type": "number", "abs_tol": 0.01},
                           "due_date": {"type": "date", "nullable": true}},
                "clients": ["acme"]}]}

JSON, or YAML when PyYAML is installed. Every key and option is checked: an unknown one is an error (a typo would
otherwise leave a field that compares nothing), so is a value of the wrong type or out of range, and `SpecError.errors`
lists every problem with its path (`tasks[2].fields.total.abs_tol: must be a number >= 0`), so one run finds them all.
The comparator rules stay in `compare.FieldSpec` and `tasks.Task`, which check the spec again when it is built, so this
module cannot admit a spec they refuse.

`extends` copies another task's spec when the task is defined (a task earlier or later in the same file, or a
registered one), then applies this one's keys: `fields`, `policy` and `match` merge by key (null removes the base's
entry), every other key replaces the base's, and `name` and `description` are never inherited. The result is what is
registered and stored, so later changes to the base do not reach a task that extended it.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import fields as dataclass_fields
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import compare, tasks
from .compare import FieldSpec
from .openai_compat import MAYBE_SURROGATE, lone_surrogate

if TYPE_CHECKING:  # pragma: no cover
    from .ledger import Ledger

# The keys of one task spec, in the order `document` writes them (`extends` is resolved away before that).
SPEC_KEYS = ("name", "description", "format", "fields", "pinned", "store", "match", "routing", "policy", "tools", "clients")
INPUT_KEYS = frozenset(SPEC_KEYS) | {"extends"}
MERGED = frozenset({"fields", "policy", "match"})  # `extends` merges these by key; any other key replaces the base's
MAX_JSON_DEPTH = 32  # nesting of a `routing` value
MAX_PATH = 256  # characters of a field path
_PATH = re.compile(r"\$|[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*")  # '$' or dot-separated names
_IDENT = re.compile(r"[A-Za-z0-9_]+")
YAML_SUFFIXES = (".yaml", ".yml")
LookupFn = Callable[[str], "tasks.Task | None"]


class SpecError(ValueError):
    """Every problem with a spec, each `<path>: <message>`. A ValueError, so a bad RATCHET_TASKS_FILE stops startup as a
    bad RATCHET_API_KEYS does, with all of its errors in the message."""

    def __init__(self, errors: Iterable[str], source: str = "") -> None:
        self.errors = list(errors)
        self.source = source
        head = f"{source}: " if source else ""
        n = len(self.errors)
        super().__init__(f"{head}invalid task spec ({n} error{'s' if n != 1 else ''}):\n  " + "\n  ".join(self.errors))


# ---------------------------------------------------------------------------------------------------------------------
# Field options: how each is checked and which field types take it. Every FieldSpec option is here (a test checks), so
# an option a type does not use is refused instead of ignored.


def _bool(v: Any, t: str) -> str | None:
    return None if isinstance(v, bool) else "must be true or false"


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _tolerance(v: Any, t: str) -> str | None:
    return None if _is_number(v) and v >= 0 else "must be a number >= 0"


def _share(v: Any, t: str) -> str | None:
    return None if _is_number(v) and 0 <= v <= 1 else "must be a number from 0 to 1"


def _count(v: Any, t: str) -> str | None:
    return None if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else "must be a whole number >= 0"


def _list(v: Any, ok: Callable[[Any], bool], what: str) -> str | None:
    if not isinstance(v, list):
        return f"must be a list of {what}"
    if len(v) > compare.MAX_VALUES:
        return f"must have at most {compare.MAX_VALUES} entries"
    return None if all(ok(x) for x in v) else f"must be a list of {what}"


def _strings(v: Any, t: str) -> str | None:
    return _list(v, lambda x: isinstance(x, str), "strings")


def _scalars(v: Any, t: str) -> str | None:
    return _list(v, lambda x: x is None or isinstance(x, (str, bool)) or _is_number(x), "strings, numbers, booleans or null")


def _aliases(v: Any, t: str) -> str | None:
    if not isinstance(v, Mapping) or not all(isinstance(a, str) and isinstance(b, str) for a, b in v.items()):
        return "must be an object of strings (another spelling: the label it stands for)"
    return None if len(v) <= compare.MAX_VALUES else f"must have at most {compare.MAX_VALUES} entries"


def _one_of(*choices: str) -> Callable[[Any, str], str | None]:
    def check(v: Any, t: str) -> str | None:
        return None if isinstance(v, str) and v in choices else f"must be one of {', '.join(repr(c) for c in choices)}"

    return check


_MODES = {"text": ("exact", "loose", "token_set"), "ranking": ("prefix", "set")}


def _mode(v: Any, t: str) -> str | None:
    return _one_of(*_MODES[t])(v, t)


def _split(v: Any, t: str) -> str | None:
    return None if isinstance(v, str) and v else "must be a non-empty string"


_ALL = frozenset(compare.TYPES) - compare.RESERVED_TYPES
# option -> (check, the types that take it). `item` and `fields` hold field specs and are parsed by `_item` and `_fields`.
OPTIONS: Mapping[str, tuple[Callable[[Any, str], str | None] | None, frozenset[str]]] = {
    "required": (_bool, _ALL),
    "nullable": (_bool, _ALL),
    "values": (_strings, frozenset({"label", "language", "set", "ranking"})),
    "aliases": (_aliases, frozenset({"label", "ordinal", "set", "ranking"})),
    "abs_tol": (_tolerance, frozenset({"number"})),
    "rel_tol": (_tolerance, frozenset({"number"})),
    "decimal": (_one_of(".", ","), frozenset({"number"})),
    "percent": (_one_of("number", "fraction"), frozenset({"number"})),
    "order": (_strings, frozenset({"ordinal"})),
    "tolerance": (_count, frozenset({"ordinal"})),
    "mode": (_mode, frozenset({"text", "ranking"})),
    "strip_suffixes": (_strings, frozenset({"text"})),
    "day_first": (_bool, frozenset({"date"})),
    "granularity": (_one_of("date", "datetime"), frozenset({"date"})),
    "item": (None, frozenset({"set", "ranking"})),
    "min_jaccard": (_share, frozenset({"set"})),
    "split": (_split, frozenset({"set"})),
    "empty_values": (_scalars, frozenset({"set"})),
    "fields": (None, frozenset({"records"})),
    "key": (_strings, frozenset({"records"})),
    "ordered": (_bool, frozenset({"records"})),
    "k": (_count, frozenset({"ranking"})),
}
_TYPE_LIST = ", ".join(t for t in compare.TYPES if t not in compare.RESERVED_TYPES)


def _at(where: str, key: Any) -> str:
    """`where.key`, or `key` at the top of a body."""
    return f"{where}.{key}" if where else str(key)


def _field_at(where: str, path: Any) -> str:
    """Where a field is: `fields.total`, or `fields["customer.city"]` for a path with dots (or '$')."""
    if isinstance(path, str) and _IDENT.fullmatch(path):
        return f"{where}.{path}"
    return f"{where}[{json.dumps(path, ensure_ascii=False) if isinstance(path, str) else repr(path)}]"


_FIELD_PREFIX = re.compile(r"^field .*? \([a-z_]+\): ")
_TASK_PREFIX = re.compile(r"^task .*?: ")


def _reason(e: ValueError, prefix: re.Pattern[str]) -> str:
    """FieldSpec's or Task's message without the name it starts with: the error's path already says where."""
    return prefix.sub("", str(e), count=1)


def _type(raw: Mapping[str, Any], where: str, errs: list[str], scalar: bool = False) -> str | None:
    """The field's `type`, when it is one Ratchet compares with (a scalar type for a set's or ranking's `item`)."""
    t = raw.get("type")
    if "type" not in raw:
        errs.append(f"{where}.type: missing; use one of {_TYPE_LIST}")
    elif not isinstance(t, str) or t not in compare.TYPES:
        errs.append(f"{where}.type: {t!r} is not a field type; use one of {_TYPE_LIST}")
    elif t in compare.RESERVED_TYPES:
        errs.append(f"{where}.type: {t!r} is reserved for a later release")
    elif scalar and t not in compare.SCALAR_TYPES:
        errs.append(f"{where}.type: elements compare by value, so use one of {', '.join(sorted(compare.SCALAR_TYPES))}")
    else:
        return t
    return None


def _field(path: str, raw: Any, where: str, depth: int, errs: list[str], scalar: bool = False) -> FieldSpec | None:
    """One field spec ({type, ...options}) as a FieldSpec, or None after adding its errors to `errs`."""
    if not isinstance(raw, Mapping):
        errs.append(f"{where}: must be an object with a type and its options")
        return None
    before = len(errs)
    t = _type(raw, where, errs, scalar)
    kwargs: dict[str, Any] = {}
    for key, value in raw.items():
        if key == "type":
            continue
        at = _at(where, key)
        if not isinstance(key, str) or key not in OPTIONS:
            errs.append(f"{at}: unknown option; use {', '.join(sorted(OPTIONS))}")
            continue
        check, types = OPTIONS[key]
        if t is None:  # without a type, which options apply and what they mean is unknown: report the names only
            continue
        if t not in types:
            errs.append(f"{at}: does not apply to type {t!r}")
            continue
        if key == "item":
            item = _item(value, at, depth, errs)
            if item is not None:
                kwargs["item"] = item
            continue
        if key == "fields":
            kwargs["fields"] = _fields(value, at, depth + 1, errs, in_records=True)
            continue
        assert check is not None
        problem = check(value, t)
        if problem is not None:
            errs.append(f"{at}: {problem}")
            continue
        kwargs[key] = tuple(value) if isinstance(value, list) else value
    if t is None or len(errs) > before:
        return None
    try:
        return FieldSpec(path, t, **kwargs)
    except ValueError as e:  # a rule between options: an alias outside the vocabulary, a key that is not a field, ...
        errs.append(f"{where}: {_reason(e, _FIELD_PREFIX)}")
        return None


def _item(raw: Any, where: str, depth: int, errs: list[str]) -> FieldSpec | str | None:
    """A set's or ranking's element spec: a scalar type's name, or a field spec of a scalar type."""
    if isinstance(raw, str):
        return raw if _type({"type": raw}, where, errs, scalar=True) is not None else None
    if not isinstance(raw, Mapping):
        errs.append(f"{where}: must be a type name or an object with a type and its options")
        return None
    return _field("$", raw, where, depth, errs, scalar=True)


def _fields(raw: Any, where: str, depth: int, errs: list[str], in_records: bool = False) -> tuple[FieldSpec, ...]:
    """A `fields` object ({path: field spec}) as FieldSpecs, in its order. `depth` counts the records it sits in."""
    if not isinstance(raw, Mapping):
        errs.append(f"{where}: must be an object of path: field spec")
        return ()
    if depth > compare.MAX_DEPTH:
        errs.append(f"{where}: records are nested more than {compare.MAX_DEPTH} deep")
        return ()
    if len(raw) > compare.MAX_FIELDS:  # refused before any is parsed, so a huge body costs little
        errs.append(f"{where}: at most {compare.MAX_FIELDS} fields")
        return ()
    out = []
    for path, spec in raw.items():
        at = _field_at(where, path)
        if not isinstance(path, str) or len(path) > MAX_PATH or not _PATH.fullmatch(path):
            errs.append(f"{at}: a path is '$' or dot-separated names of letters, digits and '_' (at most {MAX_PATH})")
            continue
        if in_records and path == "$":
            errs.append(f"{at}: a record field needs a key path, not '$'")
            continue
        f = _field(path, spec, at, depth, errs)
        if f is not None:
            out.append(f)
    return tuple(out)


def _json_problem(value: Any) -> str | None:
    """Why `value` is not plain JSON (objects with string keys, lists, strings, finite numbers, booleans, null), or None.
    Iterative, and at most MAX_JSON_DEPTH deep."""
    stack: list[tuple[Any, int]] = [(value, 0)]
    while stack:
        v, depth = stack.pop()
        if depth > MAX_JSON_DEPTH:
            return f"is nested more than {MAX_JSON_DEPTH} deep"
        if v is None or isinstance(v, (str, bool)):
            continue
        if isinstance(v, (int, float)):
            if not math.isfinite(v):
                return "must hold finite numbers"
        elif isinstance(v, list):
            stack.extend((x, depth + 1) for x in v)
        elif isinstance(v, Mapping):
            if not all(isinstance(k, str) for k in v):
                return "must have string keys"
            stack.extend((x, depth + 1) for x in v.values())
        else:
            return f"must hold JSON values, not {type(v).__name__}"
    return None


def _build(
    spec: Mapping[str, Any], where: str, source: str, known_clients: frozenset[str] | None, errs: list[str]
) -> tasks.Task | None:
    """One task spec (with `extends` already resolved) as a Task, or None after adding its errors to `errs`."""
    before = len(errs)
    for key in spec:
        if key not in INPUT_KEYS:
            errs.append(f"{_at(where, key)}: unknown key; use {', '.join(SPEC_KEYS)}, extends")
    name = spec.get("name")
    if "name" not in spec:
        errs.append(f"{_at(where, 'name')}: missing")
    elif not isinstance(name, str) or not tasks.valid_name(name):
        errs.append(f"{_at(where, 'name')}: {name!r} is not a task name (1 to 64 letters, digits, '_', '.', '-')")
    description = spec.get("description", "")
    if not isinstance(description, str) or len(description) > tasks.MAX_DESCRIPTION:
        errs.append(f"{_at(where, 'description')}: must be a string of at most {tasks.MAX_DESCRIPTION} characters")
    fmt = spec.get("format")
    usable = [f for f in tasks.FORMATS if f not in tasks.RESERVED_FORMATS]
    if fmt is not None and (not isinstance(fmt, str) or fmt not in tasks.FORMATS):
        errs.append(f"{_at(where, 'format')}: must be one of {', '.join(usable)}")
    elif fmt in tasks.RESERVED_FORMATS:
        errs.append(f"{_at(where, 'format')}: {fmt!r} is reserved for a later release")
    fields = _fields(spec.get("fields", {}), _at(where, "fields"), 0, errs)
    for key in ("pinned", "store"):
        if key in spec and not isinstance(spec[key], bool):
            errs.append(f"{_at(where, key)}: must be true or false")
    tools = spec.get("tools", "teacher")
    if tools not in tasks.TOOLS:
        errs.append(f"{_at(where, 'tools')}: must be one of {', '.join(tasks.TOOLS)}")
    policy = spec.get("policy", {})
    if not isinstance(policy, Mapping):
        errs.append(f"{_at(where, 'policy')}: must be an object")
    else:
        for key, value in policy.items():
            problem = tasks.policy_problem(key, value)
            if problem is not None:
                errs.append(f"{_at(where, 'policy')}.{key}: {problem}")
    match = spec.get("match", {})
    if not isinstance(match, Mapping):
        errs.append(f"{_at(where, 'match')}: must be an object")
    else:
        for key, value in match.items():
            problem = tasks.match_problem(key, value)
            if problem is not None:
                errs.append(f"{_at(where, 'match')}.{key}: {problem}")
    routing = spec.get("routing", {})
    problem = "must be an object" if not isinstance(routing, Mapping) else _json_problem(routing)
    if problem is not None:
        errs.append(f"{_at(where, 'routing')}: {problem}")
    clients = spec.get("clients", [])
    problem = tasks.clients_problem(clients)
    if problem is not None:
        errs.append(f"{_at(where, 'clients')}: {problem}")
    elif known_clients is not None:
        for i, client in enumerate(clients):
            if client not in known_clients:
                errs.append(f"{_at(where, 'clients')}[{i}]: no API key is named {client!r} in RATCHET_API_KEYS")
    if len(errs) > before:
        return None
    try:
        return tasks.Task(
            name,
            pinned=spec.get("pinned", False),
            format=fmt,
            fields=fields,
            policy=policy,
            routing=routing,
            store=spec.get("store", True),
            tools=tools,
            source=source,
            description=description,
            clients=tuple(clients),
            match_rules=match,
        )
    except ValueError as e:  # a rule between keys: a text task with a field other than '$', a pinned auto task, ...
        errs.append(f"{where or '$'}: {_reason(e, _TASK_PREFIX)}")
        return None


def _merge(base: Mapping[str, Any], child: Mapping[str, Any]) -> dict[str, Any]:
    """`child` over a copy of `base` (`extends`): MERGED keys by key, null removing the base's entry; any other key
    replaced; the base's `name` and `description` dropped."""
    out = {k: v for k, v in base.items() if k not in ("name", "description", "extends")}
    for key, value in child.items():
        if key == "extends":
            continue
        if key in MERGED and isinstance(value, Mapping) and isinstance(out.get(key), Mapping):
            merged = dict(out[key])
            for k, v in value.items():
                if v is None:
                    merged.pop(k, None)
                else:
                    merged[k] = v
            out[key] = merged
        else:
            out[key] = value
    return out


def _clients(known: Iterable[str] | None) -> frozenset[str] | None:
    return None if known is None else frozenset(known)


def parse_spec(
    obj: Any,
    source: str = "api",
    *,
    lookup: LookupFn | None = None,
    known_clients: Iterable[str] | None = None,
    where: str = "",
) -> tasks.Task:
    """One task spec (an admin API body) as a Task with `source`. `extends` names a registered task (`lookup`, default
    `tasks.lookup`), the task's own name included (a new version based on the current one). `known_clients`, when given,
    are the API key names `clients` may use. Raises SpecError with every problem; paths start at `where` (the body)."""
    lookup = tasks.lookup if lookup is None else lookup
    errs: list[str] = []
    if not isinstance(obj, Mapping):
        raise SpecError([f"{where or '$'}: must be an object (one task spec)"])
    spec: Mapping[str, Any] = obj
    if "extends" in obj:
        base_name = obj["extends"]
        base = lookup(base_name) if isinstance(base_name, str) else None
        if base is None:
            raise SpecError([f"{_at(where, 'extends')}: no registered task is named {base_name!r}"])
        spec = _merge(document(base), obj)
    task = _build(spec, where, source, _clients(known_clients), errs)
    if task is None:
        raise SpecError(errs)
    return task


def parse_document(
    doc: Any,
    source: str = "file",
    *,
    lookup: LookupFn | None = None,
    known_clients: Iterable[str] | None = None,
    origin: str = "",
) -> list[tasks.Task]:
    """A tasks document ({"tasks": [...]}) as Tasks with `source`, in its order. A task may extend another one of the
    document, before or after it, or a registered one (`lookup`); a name extending itself extends the registered task of
    that name (a file adjusting a built-in task). Names are unique, at most `tasks.MAX_TASKS`. Raises SpecError with
    every problem of every task; `origin` (the file) heads its message."""
    lookup = tasks.lookup if lookup is None else lookup
    known = _clients(known_clients)
    if not isinstance(doc, Mapping):
        raise SpecError(["$: must be an object with a 'tasks' list"], origin)
    errs = [f"{key}: unknown key; use tasks" for key in doc if key != "tasks"]
    items = doc.get("tasks")
    if not isinstance(items, list):
        raise SpecError([*errs, "tasks: must be a list of task specs"], origin)
    if len(items) > tasks.MAX_TASKS:
        raise SpecError([*errs, f"tasks: at most {tasks.MAX_TASKS} tasks"], origin)
    raw: dict[str, tuple[str, Mapping[str, Any]]] = {}
    order: list[str] = []
    for i, item in enumerate(items):
        where = f"tasks[{i}]"
        if not isinstance(item, Mapping):
            errs.append(f"{where}: must be an object (one task spec)")
            continue
        name = item.get("name")
        if not isinstance(name, str) or not tasks.valid_name(name):
            _build(item, where, source, known, errs)  # reports the name and everything else that is wrong
            continue
        if name in raw:
            errs.append(f"{where}.name: {name!r} is already defined at {raw[name][0]}")
            continue
        raw[name] = (where, item)
        order.append(name)

    built: dict[str, tuple[dict[str, Any], tasks.Task] | None] = {}

    def resolve(name: str, chain: tuple[str, ...]) -> tuple[dict[str, Any], tasks.Task] | None:
        """The merged spec and Task of a document's task, building what it extends first; None when it has errors."""
        if name in built:
            return built[name]
        where, item = raw[name]
        spec: dict[str, Any] | None = dict(item)
        base_name = item.get("extends")
        at = f"{where}.extends"
        if "extends" in item:
            if not isinstance(base_name, str) or not tasks.valid_name(base_name):
                errs.append(f"{at}: must be the name of a task")
                spec = None
            elif base_name in chain:
                errs.append(f"{at}: a cycle: {' -> '.join([*chain[chain.index(base_name) :], name, base_name])}")
                spec = None
            elif base_name in raw and base_name != name:
                base = resolve(base_name, (*chain, name))
                if base is None:
                    errs.append(f"{at}: {base_name!r} has errors")
                    spec = None
                else:
                    spec = _merge(base[0], item)
            else:
                registered = lookup(base_name)
                if registered is None:
                    errs.append(f"{at}: no task is named {base_name!r} in this file or the registry")
                    spec = None
                else:
                    spec = _merge(document(registered), item)
        task = _build(spec, where, source, known, errs) if spec is not None else None
        built[name] = (spec, task) if spec is not None and task is not None else None
        return built[name]

    out = []
    for name in order:
        done = resolve(name, ())
        if done is not None:
            out.append(done[1])
    if errs:
        raise SpecError(sorted(errs, key=_task_index), origin)  # in file order; stable within a task
    return out


_INDEX = re.compile(r"tasks\[(\d+)\]")


def _task_index(error: str) -> int:
    """The task an error is about (`tasks[3]...` is 3), or -1 for the document itself."""
    m = _INDEX.match(error)
    return int(m.group(1)) if m else -1


# ---------------------------------------------------------------------------------------------------------------------
# Writing a task back as a spec


def _field_document(f: FieldSpec) -> dict[str, Any]:
    out = f.spec()  # path, type and every option that is not its default
    del out["path"]
    item = out.get("item")
    if isinstance(item, dict):
        item.pop("path", None)
        out["item"] = item["type"] if len(item) == 1 else item
    if "fields" in out:
        out["fields"] = {sub.path: _field_document(sub) for sub in f.fields}
    return out


def document(task: tasks.Task) -> dict[str, Any]:
    """The task as a spec file writes it, every key in SPEC_KEYS: what GET /ratchet/tasks shows and the ledger's `tasks`
    table stores. `parse_spec` reads it back to an equal task (with the source it is given)."""
    return {
        "name": task.name,
        "description": task.description,
        "format": task.format,
        "fields": {f.path: _field_document(f) for f in task.fields},
        "pinned": task.pinned,
        "store": task.store,
        "match": {k: list(v) if isinstance(v, tuple) else v for k, v in task.match_rules.items()},
        "routing": json.loads(json.dumps(dict(task.routing))),
        "policy": dict(task.policy),
        "tools": task.tools,
        "clients": list(task.clients),
    }


# ---------------------------------------------------------------------------------------------------------------------
# Reading


def _no_constant(name: str) -> Any:
    raise ValueError(f"{name} is not JSON")


def loads(text: str | bytes) -> Any:
    """Strict JSON: NaN and Infinity are refused, and so is a string with an unpaired UTF-16 surrogate (Python's decoder
    accepts one, but it cannot be written as UTF-8, so storing the spec would fail). Raises SpecError naming the line and
    column of a syntax error."""
    try:
        if isinstance(text, bytes):  # as json.loads decodes bytes, so the surrogate check sees the same text
            text = text.decode(json.detect_encoding(text), "surrogatepass")
        value = json.loads(text, parse_constant=_no_constant)
    except json.JSONDecodeError as e:
        raise SpecError([f"$: not valid JSON: {e.msg} (line {e.lineno}, column {e.colno})"]) from None
    except RecursionError:
        raise SpecError(["$: not valid JSON: nested too deeply"]) from None
    except (ValueError, UnicodeDecodeError) as e:
        raise SpecError([f"$: not valid JSON: {e}"]) from None
    _check_unicode(value, text)
    return value


def _check_unicode(value: Any, text: str = "") -> None:
    """SpecError for a string (or key) holding an unpaired surrogate; `text`, when given, skips the walk when it has none."""
    if text and not MAYBE_SURROGATE.search(text):
        return
    found = lone_surrogate(value)
    if found is not None:
        where, char = found
        raise SpecError([f"{where or '$'}: an unpaired UTF-16 surrogate {char!r} is not valid Unicode"])


def _load_yaml(text: str, origin: str) -> Any:
    try:
        import yaml  # optional: pip install pyyaml
    except ImportError:
        message = "$: this is a YAML file, but PyYAML is not installed: pip install pyyaml (the yaml extra), or write it as JSON"
        raise SpecError([message], origin) from None
    try:
        doc = yaml.safe_load(text)
    except RecursionError:
        raise SpecError(["$: not valid YAML: nested too deeply"], origin) from None
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f" (line {mark.line + 1}, column {mark.column + 1})" if mark is not None else ""
        problem = getattr(e, "problem", None) or type(e).__name__
        raise SpecError([f"$: not valid YAML: {problem}{where}"], origin) from None
    try:
        _check_unicode(doc)
    except SpecError as e:
        raise SpecError(e.errors, origin) from None
    except (AttributeError, TypeError):  # a key that is not a string (YAML allows any): the spec checks refuse it
        pass
    return doc


def load_file(path: str | Path, known_clients: Iterable[str] | None = None, lookup: LookupFn | None = None) -> list[tasks.Task]:
    """The tasks of a tasks file, with source `file`: YAML for a `.yaml` or `.yml` file (PyYAML must be installed),
    JSON otherwise. Raises SpecError, which names the file and lists every error. Registers nothing."""
    p = Path(path)
    origin = f"RATCHET_TASKS_FILE {p}"
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise SpecError([f"$: cannot read the file: {getattr(e, 'strerror', None) or e}"], origin) from None
    if p.suffix.lower() in YAML_SUFFIXES:
        doc = _load_yaml(text, origin)
    else:
        try:
            doc = loads(text)
        except SpecError as e:
            raise SpecError(e.errors, origin) from None
    return parse_document(doc, "file", lookup=lookup, known_clients=known_clients, origin=origin)


def install(ledger: Ledger, path: str | Path = "", known_clients: Iterable[str] | None = None) -> list[str]:
    """Set up the registry for a gateway or a Night School run: the built-in tasks, then the tasks file's (`path`, which
    may replace a built-in task), then the API and auto tasks stored in the ledger's `tasks` table. A bad file raises
    SpecError (startup stops, every error listed). A stored task that no longer parses, or whose name a built-in or file
    task now has, is skipped: the returned lines say which and why, for the caller to log (stored tasks are data, so
    one of them must not stop the gateway the admin API would fix it through)."""
    tasks.reset_builtin()
    path = str(path).strip() if path else ""
    for task in load_file(path, known_clients) if path else []:
        tasks.register(task)
    skipped = []
    for row in ledger.saved_tasks():
        name, source = row["name"], row["source"]
        try:
            task = parse_spec(row["spec"], source if source in ("api", "auto") else "api")
            if task.name != name:
                raise SpecError([f"name: the stored spec names {task.name!r}"])
            tasks.register(task)
        except SpecError as e:
            skipped.append(f"{name}: the stored spec is no longer valid ({e.errors[0]}); it is not registered")
        except tasks.TaskConflict as e:
            skipped.append(f"{name}: {e}; the stored {source} task is not registered")
        except (tasks.RegistryFull, ValueError) as e:
            skipped.append(f"{name}: {e}; it is not registered")
    return skipped


def resolve_fixture(path: Any, root: str | Path) -> Path:
    """The file a task spec names as a fixture, which must sit inside RATCHET_FIXTURES_DIR (`root`) once '..' and
    symlinks are resolved, so a spec cannot make the gateway read any other file. ValueError otherwise. For task formats
    that check answers against a fixture (`sql`, reserved for a later release)."""
    if not isinstance(path, str) or not path or "\x00" in path:
        raise ValueError("a fixture is a relative path inside RATCHET_FIXTURES_DIR")
    if Path(path).is_absolute():
        raise ValueError(f"fixture {path!r}: use a path relative to RATCHET_FIXTURES_DIR")
    base = Path(root).resolve()
    full = (base / path).resolve()
    if not full.is_relative_to(base):
        raise ValueError(f"fixture {path!r} is outside RATCHET_FIXTURES_DIR")
    return full


def option_names() -> frozenset[str]:
    """Every FieldSpec option a spec may set (all its constructor arguments but `path` and `type`)."""
    return frozenset(f.name for f in dataclass_fields(FieldSpec) if f.init) - {"path", "type"}
