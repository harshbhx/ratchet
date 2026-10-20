"""The generic mock world: a deterministic answer for any request the ticket and research registries do not know.

Mock mode used to answer only the demo's own requests (a 400 for anything else), and the mock trainer looked every stored
row up the same way, so one example from an SDK user would have crashed Night School. `sim.world.lookup` now falls back
to this world, which picks a sub-world from what a model is sent:

- tools (`params.tools` is set, `tool_choice` is not "none", and the last message is not a tool result): a call to
  `lookup_order` or `refund` (the catalogue, `TOOLS`) with arguments read from the prompt by regex (`#48213`,
  `34.50 USD`), a call to a client's own tool with arguments built from its parameters schema, or a plain reply asking
  for the order number when there is none to look up;
- schema (`response_format` `json_schema`): an instance of the schema (`instance_from_schema`);
- json (`response_format` `json_object`): the free-text answer inside `{"answer": ...}`;
- text: `[mock teacher] ` and a restatement of the prompt, or after a tool result, of that result (`final`).

Labels come from sha256 of the request's text (`_seed`), never from a stream's RNG, so the ticket draws never move
(sim/CLAUDE.md) and one request gets the same answer in the gateway, the simulator and Night School. A template, what
the mock student's skill grows on, is keyed by what a model is sent (the sub-world and the schema name, the tool set or
the system prompt), never by the task name, which no model sees.

`SchemaStream` and `ToolStream` are the `schema` and `tools` workloads (`sim.world.make_stream`). Their prompts hold no
personal data, so redaction never changes them. Simulation for demos and tests: nothing here measures a real model.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import random
import re
from dataclasses import dataclass, field
from typing import Any

from ratchet.providers import GenParams

PREFIX = "[mock teacher] "
MOCK_TEXT = "mock text"
# The free-text worlds' one label: how the answer is framed. The right one comes from the text's hash; a wrong one is a
# different closing sentence, so the default task (whole answers compared) sees a disagreement.
FOCUS: dict[str, str] = {
    "short": "Short answer first, details on request.",
    "background": "Some background first, then the answer.",
    "steps": "Here are the steps, one at a time.",
    "summary": "In one sentence: noted, with a summary to follow.",
}
FOCI = list(FOCUS)
SNIPPET_WORDS = 12  # a restatement quotes at most this many words of the prompt
SNIPPET_CHARS = 100  # and at most this many characters
MAX_LABELS = 32  # labelled fields per schema instance; the rest keep their seeded value
MAX_DEPTH = 12  # schema nesting followed when building an instance; deeper parts get their simplest value
MAX_NODES = 2000  # values built per instance, so a schema of nested arrays cannot blow up
MAX_ITEMS = 50  # an array honours minItems up to this many items
NUMBER_SPAN = 1e12  # the widest range a number is drawn from, so bounds like +-1e308 cannot overflow
# String formats filled with a value of that format. Formats whose values look like personal data (email, ipv4,
# phone numbers) stay MOCK_TEXT, so redaction never changes a mock answer. The UUID keeps its digit runs short for the
# same reason (the card and phone patterns need long ones).
FORMATS: dict[str, str] = {
    "date": "2026-10-20",
    "date-time": "2026-10-20T09:30:00Z",
    "time": "09:30:00",
    "uuid": "3f2b8c1e-9a4d-4e6f-8b7a-5c0d1e2f3a4b",
    "uri": "https://example.org/mock",
    "url": "https://example.org/mock",
    "hostname": "example.org",
}
_TYPES = frozenset({"object", "array", "string", "integer", "number", "boolean", "null"})

# ---------- the tool catalogue ----------
REFUND_REASONS = ["damaged", "not_received", "wrong_item", "duplicate_charge", "changed_mind"]
TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "lookup_order",
            "description": "Look up an order's status by its number.",
            "parameters": {
                "type": "object",
                "properties": {"order_id": {"type": "string", "description": "The order number, digits only."}},
                "required": ["order_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "refund",
            "description": "Refund an order.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string", "description": "The order number, digits only."},
                    "amount": {"type": "number", "minimum": 0},
                    "reason": {"type": "string", "enum": REFUND_REASONS},
                },
                "required": ["order_id", "amount", "reason"],
                "additionalProperties": False,
            },
        },
    },
]
CATALOGUE = ("lookup_order", "refund")
NO_TOOL = "(none)"  # the `tool` label of a plain reply; parentheses cannot appear in a function name
ORDER_KEYS = ("order_id", "order_number", "order")  # parameter names that take the order number read from the prompt
_ORDER = re.compile(r"#\s?(\d{4,8})\b|\border(?: number| no\.?)?\s+(\d{4,8})\b", re.IGNORECASE)
_AMOUNT = re.compile(r"\b(\d{1,6}(?:\.\d{1,2})?)\s?(?:USD|EUR|GBP|dollars?|euros?)\b|[$€£]\s?(\d{1,6}(?:\.\d{1,2})?)", re.I)
_REFUND = re.compile(r"\b(?:refund|money back|reimburse|charged twice|billed twice|double[- ]charged)\b", re.IGNORECASE)
# (reason, pattern): the first that matches the prompt; `changed_mind` when none does.
_REASONS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("duplicate_charge", re.compile(r"\b(?:charged|billed) twice\b|\bdouble[- ]charged\b|\bduplicate charge\b", re.I)),
    ("damaged", re.compile(r"\b(?:damaged|broken|cracked|smashed|stopped working)\b", re.I)),
    ("not_received", re.compile(r"\b(?:never (?:arrived|came)|did(?:n't| not) arrive|not (?:arrived|received)|lost)\b", re.I)),
    ("wrong_item", re.compile(r"\bwrong (?:item|size|colou?r|product|model)\b", re.I)),
)


def _seed(*parts: Any) -> int:
    """A seed from sha256 of the parts: the same request always gets the same labels, on any machine."""
    return int(hashlib.sha256("|".join(map(str, parts)).encode("utf-8", "surrogatepass")).hexdigest()[:16], 16)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[:8]


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


# ---------- instances of a JSON schema ----------
def instance_from_schema(schema: Any, seed: int) -> Any:
    """A value that satisfies `schema` (the JSON-schema subset structured outputs use), the same for the same seed: an
    enum value and a boolean drawn with the seed, an integer or number within its bounds, 'mock text' for a string
    (a date, time, UUID or URI for those formats; padded or cut to minLength and maxLength), 1 or 2 items for an array
    (within minItems and maxItems), every property of an object (and any required one it does not describe), local
    `$ref`s (`#/$defs/...`), `anyOf`, `oneOf` (a non-null option), `allOf` and `const`. Never raises: a part it cannot
    read accepts anything and gets 'mock text', and past MAX_DEPTH or MAX_NODES a part gets its simplest value, which
    may break a minItems (a recursive or exploding schema has no small instance anyway)."""
    return _Builder(schema, random.Random(seed)).value(schema, (), 0)


def _number(x: Any) -> float | None:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    try:
        f = float(x)
    except OverflowError:
        return None
    return f if math.isfinite(f) else None


def _whole(x: Any, default: int | None) -> int | None:
    return x if isinstance(x, int) and not isinstance(x, bool) and x >= 0 else default


def _distinct(values: list[Any]) -> list[Any]:
    """`values` without repeats (as JSON), in order."""
    if all(isinstance(v, str) for v in values):  # the common enum: no JSON needed to tell strings apart
        return list(dict.fromkeys(values))
    seen: set[str] = set()
    out = []
    for v in values:
        key = _canonical(v)
        if key not in seen:
            seen.add(key)
            out.append(v)
    return out


def _is_null(schema: Any) -> bool:
    return isinstance(schema, dict) and (schema.get("type") == "null" or schema.get("const", 0) is None)


def _merge(base: dict[str, Any], extra: Any) -> dict[str, Any]:
    """`extra`'s keywords over `base`'s; properties are merged and required lists joined (enough for allOf and a $ref's
    sibling keywords)."""
    if not isinstance(extra, dict):
        return base
    out = {**base, **extra}
    props_a, props_b = base.get("properties"), extra.get("properties")
    if isinstance(props_a, dict) and isinstance(props_b, dict):
        out["properties"] = {**props_a, **props_b}
    req_a, req_b = base.get("required"), extra.get("required")
    if isinstance(req_a, list) and isinstance(req_b, list):
        out["required"] = list(dict.fromkeys([*req_a, *req_b]))
    return out


def _pointer(root: Any, ref: str) -> Any:
    """The part of `root` a local `$ref` (`#`, `#/$defs/Name`, any JSON pointer) names, or None."""
    if not ref.startswith("#"):
        return None
    node = root
    for raw in ref[1:].split("/")[1:] if ref != "#" else []:
        key = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and key in node:
            node = node[key]
        elif isinstance(node, list) and key.isdigit() and int(key) < len(node):
            node = node[int(key)]
        else:
            return None
    return node


def _type(schema: dict[str, Any]) -> str:
    """The schema's type: its first non-null `type` (a list such as ["string", "null"] is nullable), else inferred from
    its keywords, else string."""
    t = schema.get("type")
    if isinstance(t, list):
        names = [x for x in t if isinstance(x, str) and x in _TYPES]
        t = next((x for x in names if x != "null"), names[0] if names else None)
    if isinstance(t, str) and t in _TYPES:
        return t
    if any(k in schema for k in ("properties", "required", "additionalProperties")):
        return "object"
    if any(k in schema for k in ("items", "prefixItems", "minItems", "maxItems")):
        return "array"
    if any(k in schema for k in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum")):
        return "number"
    return "string"


class _Builder:
    """Builds one instance, recording each enum, boolean, integer and number value as a leaf the mock student can get
    wrong: (kind order, path, value, candidates), candidates being the values the schema allows there."""

    def __init__(self, root: Any, rng: random.Random) -> None:
        self.root = root
        self.rng = rng
        self.nodes = MAX_NODES
        self.leaves: list[tuple[int, tuple[Any, ...], Any, list[Any]]] = []
        self.record = True  # False inside an array with uniqueItems: a changed item could repeat another

    def _resolve(self, schema: Any) -> Any:
        for _ in range(32):  # a $ref chain that loops ends here
            if not (isinstance(schema, dict) and isinstance(schema.get("$ref"), str)):
                return schema
            target = _pointer(self.root, schema["$ref"])
            rest = {k: v for k, v in schema.items() if k != "$ref"}
            schema = _merge(target, rest) if isinstance(target, dict) else (rest or True)
        return {}

    def _leaf(self, kind: int, path: tuple[Any, ...], value: Any, candidates: list[Any]) -> None:
        if self.record and any(c != value for c in candidates):
            self.leaves.append((kind, path, value, candidates))

    def value(self, schema: Any, path: tuple[Any, ...], depth: int) -> Any:
        self.nodes -= 1
        schema = self._resolve(schema)
        if not isinstance(schema, dict):
            return MOCK_TEXT  # `true`, or something that is not a schema: anything is allowed
        if "const" in schema:
            return copy.deepcopy(schema["const"])
        enum = schema.get("enum")
        if isinstance(enum, list) and enum:
            values = _distinct(enum)
            v = self.rng.choice(values)
            self._leaf(0, path, v, values)
            return copy.deepcopy(v)
        if self.nodes <= 0 or depth > MAX_DEPTH:
            return self._simplest(schema)
        for key in ("anyOf", "oneOf"):
            options = schema.get(key)
            if isinstance(options, list) and options:
                real = [o for o in options if not _is_null(self._resolve(o))] or options
                rest = {k: v for k, v in schema.items() if k != key}
                return self.value(_merge(rest, self._resolve(self.rng.choice(real))), path, depth + 1)
        parts = schema.get("allOf")
        if isinstance(parts, list) and parts:
            merged = {k: v for k, v in schema.items() if k != "allOf"}
            for part in parts:
                merged = _merge(merged, self._resolve(part))
            return self.value(merged, path, depth + 1)
        kind = _type(schema)
        if kind == "object":
            return self._object(schema, path, depth)
        if kind == "array":
            return self._array(schema, path, depth)
        if kind == "integer":
            return self._integer(schema, path)
        if kind == "number":
            return self._float(schema, path)
        if kind == "boolean":
            v = self.rng.random() < 0.5
            self._leaf(1, path, v, [True, False])
            return v
        if kind == "null":
            return None
        return self._string(schema)

    def _simplest(self, schema: dict[str, Any]) -> Any:
        return {"object": {}, "array": [], "string": "", "integer": 0, "number": 0, "boolean": False, "null": None}[_type(schema)]

    def _object(self, schema: dict[str, Any], path: tuple[Any, ...], depth: int) -> dict[str, Any]:
        props = schema.get("properties")
        out: dict[str, Any] = {}
        for key, sub in (props if isinstance(props, dict) else {}).items():
            if isinstance(key, str):  # JSON has no other keys; a Python schema might
                out[key] = self.value(sub, (*path, key), depth + 1)
        required = schema.get("required")
        for key in required if isinstance(required, list) else []:
            if isinstance(key, str) and key not in out:
                out[key] = MOCK_TEXT
        return out

    def _array(self, schema: dict[str, Any], path: tuple[Any, ...], depth: int) -> list[Any]:
        lo = min(_whole(schema.get("minItems"), 0) or 0, MAX_ITEMS)
        hi = _whole(schema.get("maxItems"), None)
        n = max(self.rng.choice((1, 2)), lo)
        n = min(n, hi) if hi is not None else n
        items = schema.get("items")
        prefix = schema.get("prefixItems")
        if isinstance(items, list):  # draft 4's tuple form
            prefix, items = items, schema.get("additionalItems", {})
        prefix = prefix if isinstance(prefix, list) else []
        if items is False:
            n = min(n, len(prefix))
        unique = schema.get("uniqueItems") is True
        record, self.record = self.record, self.record and not unique
        out: list[Any] = []
        for i in range(n):
            sub = prefix[i] if i < len(prefix) else (items if isinstance(items, (dict, bool)) else {})
            out.append(self.value(sub, (*path, i), depth + 1))
        self.record = record
        if unique:  # keep the items before the first repeat, so every recorded index still exists
            seen: set[str] = set()
            for i, v in enumerate(out):
                key = _canonical(v)
                if key in seen:
                    return out[:i]
                seen.add(key)
        return out

    def _bounds(self, schema: dict[str, Any]) -> tuple[float | None, bool, float | None, bool]:
        """(lower bound, exclusive?, upper bound, exclusive?) from minimum, maximum and the exclusive forms (a number
        since draft 6, a flag on minimum and maximum in draft 4); the stricter one when both are given."""
        lo, hi = _number(schema.get("minimum")), _number(schema.get("maximum"))
        lo_x = hi_x = False
        emin, emax = schema.get("exclusiveMinimum"), schema.get("exclusiveMaximum")
        if emin is True:
            lo_x = lo is not None
        elif (e := _number(emin)) is not None and (lo is None or e >= lo):
            lo, lo_x = e, True
        if emax is True:
            hi_x = hi is not None
        elif (e := _number(emax)) is not None and (hi is None or e <= hi):
            hi, hi_x = e, True
        return lo, lo_x, hi, hi_x

    def _integer(self, schema: dict[str, Any], path: tuple[Any, ...]) -> int:
        lo_f, lo_x, hi_f, hi_x = self._bounds(schema)
        lo = None if lo_f is None else (math.floor(lo_f) + 1 if lo_x else math.ceil(lo_f))
        hi = None if hi_f is None else (math.ceil(hi_f) - 1 if hi_x else math.floor(hi_f))
        if lo is None and hi is None:
            lo, hi = 0, 100
        elif lo is None:
            lo = hi - 100  # type: ignore[operator]
        elif hi is None:
            hi = lo + 100
        assert lo is not None and hi is not None
        step = schema.get("multipleOf")
        step = step if isinstance(step, int) and not isinstance(step, bool) and step > 0 else 1
        first, last = -(-lo // step) * step, (hi // step) * step
        if first > last:  # nothing satisfies the bounds: the lower bound, as good as any
            return lo
        v = first + step * self.rng.randint(0, (last - first) // step)
        candidates = sorted({first, last, v, (first + last) // (2 * step) * step} | {x for x in (v - step, v + step)})
        self._leaf(2, path, v, [c for c in candidates if first <= c <= last])
        return v

    def _float(self, schema: dict[str, Any], path: tuple[Any, ...]) -> float:
        lo, lo_x, hi, hi_x = self._bounds(schema)
        lo = None if lo is None else lo + (0.01 if lo_x else 0.0)
        hi = None if hi is None else hi - (0.01 if hi_x else 0.0)
        if lo is None and hi is None:
            lo, hi = 0.0, 1000.0
        elif lo is None:
            lo = hi - 1000.0  # type: ignore[operator]
        elif hi is None:
            hi = lo + 1000.0
        assert lo is not None and hi is not None
        if lo > hi:  # nothing satisfies the bounds: the lower bound, as good as any
            return lo
        if not math.isfinite(hi - lo) or hi - lo > NUMBER_SPAN:  # a span like 1e308 would draw infinities
            hi = lo + 1000.0
        v = min(max(round(lo + (hi - lo) * self.rng.random(), 2), lo), hi)
        self._leaf(3, path, v, sorted({lo, hi, v, min(max(round((lo + hi) / 2, 2), lo), hi)}))
        return v

    def _string(self, schema: dict[str, Any]) -> str:
        fmt = schema.get("format")
        s = FORMATS.get(fmt, MOCK_TEXT) if isinstance(fmt, str) else MOCK_TEXT
        lo = min(_whole(schema.get("minLength"), 0) or 0, 10_000)
        hi = _whole(schema.get("maxLength"), None)
        while len(s) < lo:
            s += " text"
        return s[:hi] if hi is not None else s


def _label_key(path: tuple[Any, ...]) -> str:
    """A leaf's label name: '$' and its JSON pointer ('$/sentiment', '$/parts/0/qty'); '$' is the whole answer."""
    return "$" + "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in path)


def _set(root: Any, path: tuple[Any, ...], value: Any) -> Any:
    if not path:
        return value
    node = root
    for p in path[:-1]:
        node = node[p]
    node[path[-1]] = value
    return root


# ---------- requests ----------
def _content_text(content: Any) -> str:
    """A message's text: the string, or its text parts joined (image and audio parts carry none)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p["text"] for p in content if isinstance(p, dict) and isinstance(p.get("text"), str))
    return ""


def _conversation(messages: list[dict[str, Any]]) -> tuple[str, str, str | None]:
    """(system prompt, the text a request is about, the tool result it ends with or None). The text is the last user
    message's, else the last message that has any; an empty list gives ''."""
    msgs = [m for m in messages if isinstance(m, dict)]
    system = _content_text(msgs[0].get("content")) if msgs and msgs[0].get("role") in ("system", "developer") else ""
    user = next((_content_text(m.get("content")) for m in reversed(msgs) if m.get("role") == "user"), None)
    if user is None:
        user = next((t for t in (_content_text(m.get("content")) for m in reversed(msgs)) if t), "")
    last = msgs[-1] if msgs else {}
    tool = _content_text(last.get("content")) if last.get("role") in ("tool", "function") else None
    return system, user, tool


def _snippet(text: str) -> str:
    """The start of `text` for a restatement: at most SNIPPET_WORDS words and SNIPPET_CHARS characters, '...' when cut.
    Reads a bounded head, so a long prompt costs nothing more."""
    head = text[: SNIPPET_CHARS * 4]
    words = head.split()
    s = " ".join(words[:SNIPPET_WORDS])
    cut = len(words) > SNIPPET_WORDS or (len(text) > len(head) and bool(text[len(head) :].strip()))
    if len(s) > SNIPPET_CHARS:
        s, cut = s[:SNIPPET_CHARS].rstrip(), True
    return s + ("..." if cut else "")


def _function_tools(params: GenParams | None) -> list[dict[str, Any]]:
    """The function tools a request offers, one per name, in order."""
    tools = params.tools if params is not None else None
    out: dict[str, dict[str, Any]] = {}
    for t in tools if isinstance(tools, list) else []:
        fn = t.get("function") if isinstance(t, dict) else None
        name = fn.get("name") if isinstance(fn, dict) else None
        if isinstance(name, str) and name and name not in out:
            out[name] = fn  # type: ignore[assignment]
    return list(out.values())


def _forced(choice: Any, names: list[str]) -> str | None:
    """The tool `tool_choice` forces ({"type": "function", "function": {"name": ...}}), when the request offers it."""
    fn = choice.get("function") if isinstance(choice, dict) else None
    name = fn.get("name") if isinstance(fn, dict) else None
    return name if name in names else None


def _order_id(text: str) -> str | None:
    m = _ORDER.search(text)
    return (m.group(1) or m.group(2)) if m else None


def _amount(text: str) -> float | None:
    m = _AMOUNT.search(text)
    return float(m.group(1) or m.group(2)) if m else None


def _reason(text: str) -> str:
    return next((reason for reason, pattern in _REASONS if pattern.search(text)), "changed_mind")


@dataclass
class GenericItem:
    """One request of the generic world, with the `sim.world.Item` interface the mock models read. `world` is `text`,
    `json`, `schema`, `tools` or `final` (a reply to a tool result); `task` is the task a workload sends it under
    ('default' when `lookup` built it, since a model never sees the task)."""

    text: str
    template: str
    difficulty: str
    task: str
    world: str
    labels: dict[str, Any]
    label_space: dict[str, list[Any]]
    messages_: list[dict[str, Any]] = field(default_factory=list)
    params: GenParams | None = None
    data: dict[str, Any] = field(default_factory=dict, repr=False)  # what `answer` and `calls` render from

    @property
    def fragile(self) -> bool:
        """Slices a corrupted deploy breaks (fault injection): refund calls; schema answers whose first enum takes its
        first value; free-text answers framed as steps."""
        if self.world == "tools":
            return self.labels.get("tool", self.data["decision"]) == "refund"
        if self.world == "schema":
            first = next((k for k in self.label_space if self.data["kinds"].get(k) == 0), None)
            return first is not None and self.labels[first] == self.label_space[first][0]
        return self.labels.get("focus") == "steps"

    def messages(self) -> list[dict[str, Any]]:
        return self.messages_

    def answer(self, labels: dict[str, Any]) -> str:
        """The message content a model writes for `labels`: '' for a tool call (`calls` has it)."""
        if self.world in ("text", "final"):
            return self._text(labels)  # `final`: a reply to the tool result the request ends with
        if self.world == "json":
            return json.dumps({"answer": self._text(labels)})
        if self.world == "schema":
            inst = json.loads(self.data["instance"])
            for key, value in labels.items():
                path = self.data["paths"].get(key)
                if path is not None:
                    inst = _set(inst, path, copy.deepcopy(value))
            return json.dumps(inst)
        if labels.get("tool", self.data["decision"]) != NO_TOOL:
            return ""
        if self.data["order_id"] is None and any(n in CATALOGUE for n in self.data["functions"]):
            return f'{PREFIX}About "{self.data["snippet"]}": no order to look up, so here is a short answer.'
        return f'{PREFIX}About "{self.data["snippet"]}": I can answer that without a tool.'

    def calls(self, labels: dict[str, Any], salt: str = "") -> list[dict[str, Any]] | None:
        """The tool calls a model makes for `labels` (OpenAI's `message.tool_calls`, `arguments` a JSON string), or None
        for a plain reply. `salt` varies the call id, as two calls of a real model differ in theirs."""
        if self.world != "tools":
            return None
        name = labels.get("tool", self.data["decision"])
        if name == NO_TOOL:
            return None
        fn = self.data["functions"][name]
        args = instance_from_schema(fn.get("parameters", {"type": "object"}), _seed("args", name, self.text))
        args = args if isinstance(args, dict) else {}
        props = self._properties(fn)
        oid, amount = self.data["order_id"], self.data["amount"]
        for key, prop in props.items():
            if key in ORDER_KEYS and oid is not None:
                args[key] = int(oid) if isinstance(prop, dict) and prop.get("type") == "integer" else oid
            elif key == "amount" and amount is not None:
                args[key] = amount
            elif key == "reason" and self.data["reason"] is not None:
                args[key] = labels.get("reason", self.data["reason"])
        call_id = "call_" + hashlib.sha256(f"{salt}|{name}|{self.text}".encode("utf-8", "surrogatepass")).hexdigest()[:24]
        return [{"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]

    @staticmethod
    def _properties(fn: dict[str, Any]) -> dict[str, Any]:
        params = fn.get("parameters")
        props = params.get("properties") if isinstance(params, dict) else None
        return props if isinstance(props, dict) else {}

    def _text(self, labels: dict[str, Any]) -> str:
        said = "The tool returned" if self.data.get("final") else "You asked"
        return f'{PREFIX}{said}: "{self.data["snippet"]}". {FOCUS.get(labels.get("focus"), FOCUS["short"])}'


def item(messages: list[dict[str, Any]], params: GenParams | None = None, task: str = "default") -> GenericItem:
    """The generic item for a request: its sub-world, template, labels and answers, all from what a model is sent
    (`messages` and `params`). Never raises for a request the gateway accepts; anything it cannot read counts as text."""
    system, user, tool = _conversation(messages)
    sys_key = _digest(system) if system else "-"
    functions = _function_tools(params)
    choice = params.tool_choice if params is not None else None
    rf = params.response_format if params is not None else None
    kind = rf.get("type") if isinstance(rf, dict) else None
    if functions and choice != "none" and tool is None:
        return _tool_item(messages, params, task, user, functions, choice)
    if kind == "json_schema":
        return _schema_item(messages, params, task, user if tool is None else f"{user}\n{tool}", rf)  # type: ignore[arg-type]
    as_json = kind == "json_object"
    if tool is not None:  # the reply to a tool result restates the result
        names = ",".join(sorted(fn["name"] for fn in functions)) or "-"
        world, template, text = "json" if as_json else "final", f"generic:tools:{names}:final", f"{user}\n{tool}"
        template += ":json" if as_json else ""
    else:
        world = "json" if as_json else "text"
        template, text = f"generic:{world}:{sys_key}", user
    focus = FOCI[_seed(template, text) % len(FOCI)]
    data = {"snippet": _snippet(user if tool is None else tool), "final": tool is not None}
    return GenericItem(text, template, "easy", task, world, {"focus": focus}, {"focus": FOCI}, list(messages), params, data)


def _schema_item(
    messages: list[dict[str, Any]], params: GenParams | None, task: str, text: str, rf: dict[str, Any]
) -> GenericItem:
    spec = rf.get("json_schema")
    spec = spec if isinstance(spec, dict) else {}
    name = spec.get("name") if isinstance(spec.get("name"), str) and spec.get("name") else "unnamed"
    schema = spec.get("schema") if isinstance(spec.get("schema"), dict) else {"type": "object"}
    template = f"generic:schema:{name}"
    builder = _Builder(schema, random.Random(_seed(template, _canonical(schema), text)))
    instance = builder.value(schema, (), 0)
    leaves = sorted(builder.leaves, key=lambda leaf: leaf[0])[:MAX_LABELS]  # enums first, then booleans, numbers
    keyed = [(_label_key(leaf[1]), leaf) for leaf in leaves]
    labels = {key: value for key, (_, _, value, _) in keyed}
    space = {key: candidates for key, (_, _, _, candidates) in keyed}
    data = {
        "instance": json.dumps(instance),
        "paths": {key: path for key, (_, path, _, _) in keyed},
        "kinds": {key: kind for key, (kind, _, _, _) in keyed},
    }
    difficulty = "hard" if len(labels) > 3 else "easy"
    return GenericItem(text, template, difficulty, task, "schema", labels, space, list(messages), params, data)


def _tool_item(
    messages: list[dict[str, Any]],
    params: GenParams | None,
    task: str,
    user: str,
    functions: list[dict[str, Any]],
    choice: Any,
) -> GenericItem:
    names = [fn["name"] for fn in functions]
    template = f"generic:tools:{','.join(sorted(names))}"
    seed = _seed(template, user)
    oid, amount = _order_id(user), _amount(user)
    forced = _forced(choice, names)
    offered = [n for n in CATALOGUE if n in names]
    if forced is not None:
        decision = forced
    elif offered:
        if oid is None:
            decision = NO_TOOL  # nothing to look up: ask for the order number
        elif "refund" in offered and amount is not None and (_REFUND.search(user) or "lookup_order" not in offered):
            decision = "refund"
        elif "lookup_order" in offered:
            decision = "lookup_order"
        else:
            decision = NO_TOOL
    else:
        decision = names[seed % len(names)]
    if decision == NO_TOOL and choice == "required":
        decision = names[0]
    labels: dict[str, Any] = {}
    space: dict[str, list[Any]] = {}
    options = [forced] if forced is not None else names + ([] if choice == "required" else [NO_TOOL])
    if len(options) > 1:
        labels["tool"], space["tool"] = decision, options
    by_name = {fn["name"]: fn for fn in functions}
    reason = None  # the refund's reason, when the refund tool takes one
    prop = GenericItem._properties(by_name["refund"]).get("reason") if "refund" in by_name else None
    if isinstance(prop, dict):
        enum = _distinct(prop["enum"]) if isinstance(prop.get("enum"), list) else []
        reasons = enum if len(enum) >= 2 else list(REFUND_REASONS)
        reason = _reason(user)
        if reason not in reasons:
            reason = reasons[seed % len(reasons)]
        if any(r != reason for r in reasons):  # a label only when it can be got wrong
            labels["reason"], space["reason"] = reason, reasons
    data = {
        "decision": decision,
        "functions": by_name,
        "order_id": oid,
        "amount": amount,
        "reason": reason,
        "snippet": _snippet(user),
    }
    return GenericItem(user, template, "hard", task, "tools", labels, space, list(messages), params, data)


# ---------- the schema and tools workloads ----------
PRODUCTS = ["desk lamp", "travel mug", "rain jacket", "phone case", "yoga mat", "kettle", "backpack", "board game"]
OPINIONS = [
    "works exactly as described",
    "stopped being useful after a week",
    "is fine for the price",
    "feels cheaper than it looks",
    "arrived quickly and well packed",
    "is better than the one I had before",
]
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday"]


@dataclass(frozen=True)
class SchemaCase:
    """One structured-output task of the schema workload: the task name it is sent under, its schema and prompts."""

    task: str
    name: str
    system: str
    prompts: tuple[str, ...]
    schema: dict[str, Any]

    def response_format(self) -> dict[str, Any]:
        return {"type": "json_schema", "json_schema": {"name": self.name, "schema": self.schema, "strict": True}}


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


SCHEMA_CASES: list[SchemaCase] = [
    SchemaCase(
        "review-rating",
        "review_rating",
        "Rate the product review. Reply with JSON that matches the schema.",
        ("Review of the {product}: it {opinion}. (ref {ref})", "My {product} {opinion}, so that is my verdict. Ref {ref}."),
        _object(
            {
                "sentiment": {"type": "string", "enum": ["positive", "neutral", "negative"]},
                "stars": {"type": "integer", "minimum": 1, "maximum": 5},
                "mentions_price": {"type": "boolean"},
                "summary": {"type": "string"},
            }
        ),
    ),
    SchemaCase(
        "ticket-route",
        "ticket_route",
        "Route the customer message. Reply with JSON that matches the schema.",
        (
            "The {product} I bought {opinion} and I would like some help with it. Case {ref}.",
            "Question about my {product}: it {opinion}. What should I do? (case {ref})",
        ),
        _object(
            {
                "department": {"type": "string", "enum": ["billing", "technical", "account", "shipping"]},
                "urgency": {"type": "string", "enum": ["low", "normal", "high"]},
                "needs_human": {"type": "boolean"},
                "reply": {"type": "string"},
            }
        ),
    ),
    SchemaCase(
        "meeting-extract",
        "meeting_extract",
        "Extract the meeting details. Reply with JSON that matches the schema.",
        (
            "Can we meet on {weekday} about the {product} launch? Room {ref}.",
            "Reminder: {weekday} call to review {product} returns, agenda item {ref}.",
        ),
        _object(
            {
                "kind": {"type": "string", "enum": ["meeting", "call", "deadline"]},
                "day": {"type": "string", "enum": WEEKDAYS},
                "people": {"type": "integer", "minimum": 1, "maximum": 12},
                "topics": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["budget", "hiring", "roadmap", "launch", "support"]},
                    "minItems": 1,
                    "maxItems": 2,
                },
                "notes": {"type": "string"},
            }
        ),
    ),
]
# Arrives on `drift_day`: a schema the student has never seen, nested, with records.
DRIFT_SCHEMA_CASES: list[SchemaCase] = [
    SchemaCase(
        "warranty-claim",
        "warranty_claim",
        "Assess the warranty claim. Reply with JSON that matches the schema.",
        (
            "My {product} {opinion} after {months} months. Is it still covered? Claim {ref}.",
            "Warranty claim {ref}: the {product} {opinion}, bought {months} months ago.",
        ),
        {
            **_object(
                {
                    "covered": {"type": "boolean"},
                    "decision": {"type": "string", "enum": ["approve", "deny", "review"]},
                    "months": {"type": "integer", "minimum": 0, "maximum": 36},
                    "parts": {"type": "array", "items": {"$ref": "#/$defs/part"}, "minItems": 1, "maxItems": 2},
                }
            ),
            "$defs": {
                "part": _object(
                    {
                        "part": {"type": "string", "enum": ["screen", "battery", "hinge", "speaker"]},
                        "qty": {"type": "integer", "minimum": 1, "maximum": 3},
                    }
                )
            },
        },
    ),
]


class SchemaStream:
    """The `schema` workload: structured-output requests (`response_format` json_schema) over three schemas, a fourth
    from `drift_day`. Its own RNG, so the ticket and research draws never move."""

    def __init__(self, seed: int = 7, drift_day: int | None = 5, drift_share: float = 0.2):
        self.rng = random.Random(_seed("schema-stream", seed))
        self.drift_day = drift_day
        self.drift_share = drift_share

    def sample(self, day: int) -> GenericItem:
        pool = SCHEMA_CASES
        if self.drift_day is not None and day >= self.drift_day and self.rng.random() < self.drift_share:
            pool = DRIFT_SCHEMA_CASES
        case = self.rng.choice(pool)
        text = self.rng.choice(case.prompts).format(
            product=self.rng.choice(PRODUCTS),
            opinion=self.rng.choice(OPINIONS),
            weekday=self.rng.choice(WEEKDAYS).capitalize(),
            months=self.rng.randint(1, 30),
            ref=self.rng.randint(1000, 9999),
        )
        messages = [{"role": "system", "content": case.system}, {"role": "user", "content": text}]
        return item(messages, GenParams(response_format=case.response_format()), task=case.task)


TOOL_SYSTEM = "You are a support agent for an online shop. Use the tools to look orders up or refund them."
LOOKUP_PROMPTS = (
    "Where is my order #{oid}? It has been a week.",
    "Can you check the status of order #{oid} for me?",
    "Has order #{oid} shipped yet? The {product} was a gift.",
)
REFUND_PROMPTS = (
    "Order #{oid} arrived damaged. Please refund the {amount} USD I paid.",
    "I was charged twice for order #{oid}, please refund {amount} USD.",
    "Order #{oid} never arrived. Please refund my {amount} USD.",
    "You sent the wrong item for order #{oid}. I want a refund of {amount} USD.",
    "I changed my mind about the {product} in order #{oid}. Can I get a refund of {amount} USD?",
)
PLAIN_PROMPTS = (
    "Do you ship to Canada?",
    "What are your opening hours on {weekday}?",
    "Can I pay for a {product} with a gift card?",
)
# Arrives on `drift_day`: new wording for the same decisions.
DRIFT_TOOL_PROMPTS = (
    "The {product} from order #{oid} stopped working after a day. Refund {amount} USD please.",
    "Any news on parcel #{oid}? The tracking page has not moved.",
)


class ToolStream:
    """The `tools` workload: support requests sent with the tool catalogue (`TOOLS`): order look-ups, refunds with a
    reason and an amount, and questions no tool answers. Its own RNG. Order numbers and amounts are short digit runs,
    which no redaction pattern takes for personal data."""

    def __init__(self, seed: int = 7, drift_day: int | None = 5, drift_share: float = 0.2):
        self.rng = random.Random(_seed("tool-stream", seed))
        self.drift_day = drift_day
        self.drift_share = drift_share

    def sample(self, day: int) -> GenericItem:
        if self.drift_day is not None and day >= self.drift_day and self.rng.random() < self.drift_share:
            prompts = DRIFT_TOOL_PROMPTS
        else:
            r = self.rng.random()
            prompts = LOOKUP_PROMPTS if r < 0.4 else REFUND_PROMPTS if r < 0.85 else PLAIN_PROMPTS
        text = self.rng.choice(prompts).format(
            oid=self.rng.randint(10000, 99999),
            amount=f"{self.rng.randint(5, 300)}.{self.rng.choice(('00', '50', '99'))}",
            product=self.rng.choice(PRODUCTS),
            weekday=self.rng.choice(WEEKDAYS).capitalize(),
        )
        messages = [{"role": "system", "content": TOOL_SYSTEM}, {"role": "user", "content": text}]
        return item(messages, GenParams(tools=TOOLS, tool_choice="auto"), task="order-support")
