"""OpenAI chat-completions compatibility: request validation, message normalisation, the error envelope and the
response's `usage` object.

Standard library only (plus `tasks` for the task-name rule and the registry's match rules), so it is cheap to import and
easy to test without the web stack. The gateway runs `parse_body`, `validate_chat_request` and `normalize_messages`
before anything is logged, billed or sent to a model, and turns `APIError` into the body OpenAI SDKs read:
`error.message`, `error.type`, `error.param` and `error.code` (docs/API.md#errors, ADR 0012).

Validation is strict about what Ratchet reads or will forward, and lenient about the rest: unknown top-level keys are
kept in `ChatRequest.extra` and ignored, so a newer SDK that sends a field Ratchet does not know still works.

Responses carry the fields OpenAI's own do (`usage_object`, `SERVICE_TIER`; the gateway builds the body), so SDKs that
validate them, and clients that meter by `usage`, work unchanged (docs/API.md#response-200, FR-1.12).

`resolve_task` picks a request's task from the header, the metadata, the `model` field, the model aliases, the tasks'
match rules or the system prompt (docs/API.md#task-resolution, FR-1.50, ADR 0048). The `model` field only ever names a
task: it never selects an upstream model.

The other OpenAI endpoints a drop-in base URL receives (FR-1.51, ADR 0050) are checked here too: `/v1/embeddings`
(`validate_embeddings_request`, a pass-through), and `/v1/completions` and the stateless subset of `/v1/responses`
(`validate_completion_request`, `validate_responses_request`), which become a `ChatRequest` so they run the chat path.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, fields, replace
from types import MappingProxyType
from typing import Any, NamedTuple
from urllib.parse import quote

from . import tasks

ROLES = ("system", "developer", "user", "assistant", "tool", "function")
TEXT_MODALITY = "text"
RESPONSE_FORMATS = ("text", "json_object", "json_schema")
TOOL_CHOICES = ("none", "auto", "required")
FUNCTION_CALLS = ("none", "auto")
NAME_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")  # json_schema and function names, matched in full
MAX_STOP = 4
MAX_TOP_LOGPROBS = 20
MAX_LOGIT_BIAS = 100
MAX_METADATA = 16
MAX_METADATA_KEY = 64
MAX_METADATA_VALUE = 512
# `metadata.ratchet_store`, in any case (FR-13.1): "false" or "0" keeps the request's teacher answers out of the
# training set; "true" or "1" is the default spelled out. Anything else is a 400, so a typo cannot store by accident.
STORE_METADATA = {"true": True, "1": True, "false": False, "0": False}
MAX_TOOLS = 128
MAX_TOOLS_BYTES = 64 * 1024
MAX_SCHEMA_BYTES = 64 * 1024
MAX_SCHEMA_DEPTH = 16

# error.type
INVALID_REQUEST = "invalid_request_error"
# error.code
INVALID_TYPE = "invalid_type"
INVALID_VALUE = "invalid_value"
MISSING = "missing_required_parameter"
UNSUPPORTED_VALUE = "unsupported_value"
INVALID_JSON = "invalid_json"
CONTEXT_LENGTH = "context_length_exceeded"
UNSUPPORTED_CONTENT = "unsupported_content_type"
MODEL_NOT_FOUND = "model_not_found"


class APIError(Exception):
    """An error in OpenAI's shape. `body()` is what the SDKs parse into `e.code`, `e.param` and `e.type`; `detail`
    repeats the message for one release, for clients written against FastAPI's `{"detail": ...}`."""

    def __init__(
        self,
        status: int,
        message: str,
        type: str = INVALID_REQUEST,  # the OpenAI field name
        code: str | None = None,
        param: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.type = type
        self.code = code
        self.param = param
        self.headers: dict[str, str] = dict(headers or {})

    def body(self) -> dict[str, Any]:
        error = {"message": self.message, "type": self.type, "param": self.param, "code": self.code}
        return {"error": error, "detail": self.message}


# status -> (error.type, error.code) for errors raised as plain HTTP errors: the API key (401), the body limit (413), and
# what the web framework raises itself (404 for an unknown path, 405). The daily quota and teacher failures raise
# APIError with their own codes (gateway._quota, gateway._map_upstream).
STATUS_TYPES: dict[int, tuple[str, str | None]] = {
    400: (INVALID_REQUEST, None),
    401: (INVALID_REQUEST, "invalid_api_key"),
    403: ("permission_error", None),
    404: ("not_found_error", None),
    409: (INVALID_REQUEST, None),
    413: (INVALID_REQUEST, "request_too_large"),
    429: ("rate_limit_error", None),
}
ROUTE_NOT_FOUND = "Not Found"  # the detail of the router's own 404


def http_error(
    status: int, detail: str, *, method: str = "", path: str = "", headers: Mapping[str, str] | None = None
) -> APIError:
    """The envelope for a plain HTTP error. A 404 from the router (no such route) names the method and path, as
    OpenAI's API does; any other status keeps its message."""
    if status == 404 and detail == ROUTE_NOT_FOUND:
        return APIError(404, f"Unknown request URL: {method} {path}.", "not_found_error", "unknown_url", headers=headers)
    kind, code = ("api_error", None) if status >= 500 else STATUS_TYPES.get(status, (INVALID_REQUEST, None))
    return APIError(status, detail, kind, code, headers=headers)


def from_validation_errors(errors: Iterable[Mapping[str, Any]]) -> APIError:
    """A 400 from FastAPI's request validation (its 422), naming the first problem."""
    first = next(iter(errors), None)
    if first is None:
        return APIError(400, "Invalid request.", code=INVALID_VALUE)
    loc = [p for p in first.get("loc", ()) if p not in ("body", "query", "header", "path", "cookie")]
    param = _path(loc) or None
    code = MISSING if str(first.get("type", "")).startswith("missing") else INVALID_VALUE
    where = f" '{param}'" if param else ""
    return APIError(400, f"Invalid request{where}: {first.get('msg', 'invalid value')}.", code=code, param=param)


def _path(parts: Iterable[Any]) -> str:
    out = ""
    for p in parts:
        out += f"[{p}]" if isinstance(p, int) else (f".{p}" if out else str(p))
    return out


# ---------- parsing ----------
def _no_constant(name: str) -> Any:
    raise ValueError(f"{name} is not valid JSON")


SURROGATE = re.compile("[\ud800-\udfff]")
# A surrogate as a raw character, or a \uD800-\uDFFF escape (also half of a valid pair, which then needs a closer look).
MAYBE_SURROGATE = re.compile("[\ud800-\udfff]|" + r"\\u[dD][89a-fA-F]")


def parse_body(raw: bytes | str) -> Any:
    """Strict JSON (no NaN or Infinity). Bad JSON, bad UTF-8 and nesting too deep to parse are all a 400, and so is a
    string holding an unpaired UTF-16 surrogate (`"\\ud83d"` from a client that cut an emoji in half): Python's JSON
    decoder accepts it, but the string cannot be written as UTF-8, so the teacher call or the ledger would fail later."""
    try:
        # What json.loads does with bytes, done here so the surrogate check below sees the same text.
        text = raw.decode(json.detect_encoding(raw), "surrogatepass") if isinstance(raw, bytes) else raw
        body = json.loads(text, parse_constant=_no_constant)
    except RecursionError as e:
        raise APIError(400, "The request body is not valid JSON: it is nested too deeply.", code=INVALID_JSON) from e
    except ValueError as e:  # JSONDecodeError, UnicodeDecodeError, NaN, integers too long to convert
        raise APIError(400, f"The request body is not valid JSON: {e}.", code=INVALID_JSON) from e
    if MAYBE_SURROGATE.search(text):  # rare: skip the walk for almost every request
        found = lone_surrogate(body)
        if found is not None:
            param, char = found
            where = f" in '{param}'" if param else ""
            message = f"The request body is not valid JSON: an unpaired UTF-16 surrogate {char!r}{where} is not valid Unicode."
            raise APIError(400, message, code=INVALID_JSON, param=param)
    return body


def lone_surrogate(value: Any) -> tuple[str | None, str] | None:
    """(JSON path, character) of the first string, value or object key, that holds a surrogate code point. A key's path
    is its object's. Iterative, so any nesting `json.loads` accepted is fine here."""
    stack: list[tuple[Any, Any]] = [(value, None)]  # (value, where): where is (parent's where, key or index) or None
    while stack:
        v, where = stack.pop()
        strings = [(v, where)] if isinstance(v, str) else [(k, where) for k in v] if isinstance(v, dict) else []
        for s, at in strings:
            m = None if s.isascii() else SURROGATE.search(s)
            if m:
                parts: list[Any] = []
                while at is not None:
                    at, key = at
                    parts.append(key)
                return _path(reversed(parts)) or None, m.group()
        if isinstance(v, dict):
            stack.extend((child, (where, k)) for k, child in v.items())
        elif isinstance(v, list):
            stack.extend((child, (where, i)) for i, child in enumerate(v))
    return None


# ---------- the validated request ----------
DEFAULT_MAX_N = 4  # RATCHET_MAX_N
MAX_N = 128  # OpenAI's own limit on `n`; RATCHET_MAX_N may not be set higher


@dataclass(frozen=True)
class Limits:
    """Request limits from Settings (`Settings.limits()`: RATCHET_MAX_MESSAGES, RATCHET_MAX_INPUT_CHARS, RATCHET_MAX_N).
    0 means no limit for the first two; `max_n`, the most choices a request may ask for, is 1 to MAX_N, and anything
    else raises ValueError (FR-1.14)."""

    max_messages: int = 256
    max_input_chars: int = 120_000
    max_n: int = DEFAULT_MAX_N

    def __post_init__(self) -> None:
        n = self.max_n
        if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= MAX_N:
            raise ValueError(f"RATCHET_MAX_N must be a whole number from 1 to {MAX_N}, not {n!r}")


@dataclass
class ChatRequest:
    """A validated chat-completions body. Fields a client did not send are None (`stream` False, `n` 1); `max_tokens`
    is `max_completion_tokens` when both are sent; a string `stop` becomes a one-item list. `extra` holds unknown
    top-level keys: kept and ignored, so newer SDKs and third-party clients are not refused for a field Ratchet does
    not read."""

    messages: list[dict[str, Any]]
    model: str | None = None
    stream: bool = False
    stream_options: dict[str, Any] | None = None
    n: int = 1
    max_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    stop: list[str] | None = None
    seed: int | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    logit_bias: dict[str, int] | None = None
    logprobs: bool | None = None
    top_logprobs: int | None = None
    response_format: dict[str, Any] | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    parallel_tool_calls: bool | None = None
    functions: list[dict[str, Any]] | None = None
    function_call: str | dict[str, Any] | None = None
    user: str | None = None
    safety_identifier: str | None = None
    store: bool | None = None
    metadata: dict[str, str] | None = None
    reasoning_effort: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def task(self) -> str | None:
        """`metadata.ratchet_task`. The gateway gives the `X-Ratchet-Task` header precedence over it."""
        return (self.metadata or {}).get("ratchet_task")

    @property
    def include_usage(self) -> bool:
        """`stream_options.include_usage`: a streamed answer ends with a chunk that carries `usage` (and no choices).
        Read only when `stream` is true, as OpenAI does; without a stream the response always has `usage`."""
        return self.stream and bool((self.stream_options or {}).get("include_usage"))


KNOWN_FIELDS = frozenset({f.name for f in fields(ChatRequest)} - {"extra"} | {"max_completion_tokens"})


# ---------- tool calling (FR-1.41, ADR 0043) ----------
TOOL_ROLES = ("tool", "function")  # the roles that carry a tool's result


def tool_turns(messages: Iterable[Mapping[str, Any]]) -> bool:
    """Whether a conversation carries tool calls or tool results: an assistant message with `tool_calls` or the legacy
    `function_call`, or a message whose role is `tool` or `function`. Such a request continues a tool-calling exchange
    whether or not it offers tools again, so it is a tool request (`uses_tools`). O(messages), no copies."""
    for m in messages:
        if m.get("role") in TOOL_ROLES or m.get("tool_calls") or m.get("function_call") is not None:
            return True
    return False


def uses_tools(req: ChatRequest) -> bool:
    """Whether a validated request is a tool request: it offers `tools` or the legacy `functions` (an empty list offers
    nothing), or its messages carry tool calls or results (`tool_turns`). A tool request is answered by the teacher
    alone and never stored unless its task opts in (`router.Ratchet.handle`, route `tools`)."""
    return bool(req.tools) or bool(req.functions) or tool_turns(req.messages)


@dataclass(frozen=True)
class ToolParams:
    """The tool fields a request sends upstream (`tool_params`): `tools` in the current form, `tool_choice`,
    `parallel_tool_calls`, and `legacy`, whether the response must use the legacy form (`message.function_call`,
    `finish_reason: "function_call"`) because the request used only the legacy fields."""

    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    parallel_tool_calls: bool | None = None
    legacy: bool = False


def legacy_choice(function_call: str | dict[str, Any] | None) -> str | dict[str, Any] | None:
    """The legacy `function_call` as a `tool_choice`: 'auto' and 'none' as they are, `{name}` as
    `{type: 'function', function: {name}}`."""
    if isinstance(function_call, dict):
        return {"type": "function", "function": {"name": function_call.get("name")}}
    return function_call


def tool_params(req: ChatRequest) -> ToolParams:
    """The request's tool fields with the legacy ones mapped (FR-1.41): each of `functions` becomes
    `{type: 'function', function: f}` after the request's own `tools` (one whose name `tools` already has is left out),
    and `function_call` becomes the `tool_choice` when the request sent none. Without any tool to call, `tool_choice` and
    `parallel_tool_calls` mean nothing and are not sent (OpenAI refuses them; Ratchet ignores them, as before). The
    response takes the legacy form only when the request sent `functions` or `function_call` and neither `tools` nor
    `tool_choice`; such a request asks for one call (`parallel_tool_calls: false`, unless it set the field), since a
    legacy message holds one `function_call` and a second call would have to be dropped."""
    tools = list(req.tools or ())
    names = {t["function"].get("name") for t in tools if isinstance(t.get("function"), dict)}
    tools += [{"type": "function", "function": f} for f in req.functions or () if f.get("name") not in names]
    if not tools:
        return ToolParams()
    choice = req.tool_choice if req.tool_choice is not None else legacy_choice(req.function_call)
    legacy = bool(req.functions or req.function_call is not None) and not (req.tools or req.tool_choice is not None)
    parallel = req.parallel_tool_calls
    if legacy and parallel is None:
        parallel = False
    return ToolParams(tools, choice, parallel, legacy)


def _legacy_call_id(index: int, name: Any) -> str:
    """A stable id for a legacy `function_call` turned into a tool call: the same history gets the same ids, so a
    repeated request reads the same to the teacher (and to the mock world)."""
    return "call_" + hashlib.sha256(f"{index}|{name}".encode("utf-8", "surrogatepass")).hexdigest()[:24]


def legacy_history(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalised messages with the legacy function-calling turns in the current form, for the teacher: an assistant
    `function_call` becomes one entry of its `tool_calls` (a stable id, `_legacy_call_id`), and a `function` message
    that answers it becomes a `tool` message with that `tool_call_id` (its `name` dropped). Open-model servers render
    tool calls and tool results from these fields; most chat templates know no `function` role. A `function` message
    with no call before it to answer is left as it is. The same list comes back when there is nothing to change."""
    if not any(m.get("role") == "function" or m.get("function_call") is not None for m in messages):
        return messages
    out: list[dict[str, Any]] = []
    pending: str | None = None  # the id of the last legacy call not answered yet
    for i, m in enumerate(messages):
        fc = m.get("function_call")
        if m.get("role") == "assistant" and isinstance(fc, dict):
            pending = _legacy_call_id(i, fc.get("name"))
            call = {"id": pending, "type": "function", "function": {"name": fc.get("name"), "arguments": fc.get("arguments")}}
            rest = {k: v for k, v in m.items() if k != "function_call"}
            out.append({**rest, "tool_calls": [*(m.get("tool_calls") or ()), call]})
        elif m.get("role") == "function" and pending is not None:
            out.append({"role": "tool", "tool_call_id": pending, "content": m.get("content")})
            pending = None
        else:
            out.append(m)
    return out


# ---------- validation helpers ----------
def _bad(param: str | None, message: str, code: str = INVALID_VALUE) -> APIError:
    return APIError(400, message, INVALID_REQUEST, code, param)


def _kind(value: Any) -> str:
    """The JSON type of `value`, for messages."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "a boolean"
    if isinstance(value, int):
        return "an integer"
    if isinstance(value, float):
        return "a number"
    if isinstance(value, str):
        return "a string"
    if isinstance(value, list):
        return "an array"
    if isinstance(value, dict):
        return "an object"
    return type(value).__name__


def _wrong_type(param: str, expected: str, value: Any) -> APIError:
    return _bad(param, f"Invalid type for '{param}': expected {expected}, but got {_kind(value)} instead.", INVALID_TYPE)


def _missing(param: str) -> APIError:
    return _bad(param, f"Missing required parameter: '{param}'.", MISSING)


def _short(value: Any) -> str:
    text = repr(value)
    return text if len(text) <= 40 else text[:37] + "..."


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _opt_str(body: Mapping[str, Any], key: str) -> str | None:
    v = body.get(key)
    if v is not None and not isinstance(v, str):
        raise _wrong_type(key, "a string", v)
    return v


def _opt_bool(body: Mapping[str, Any], key: str) -> bool | None:
    v = body.get(key)
    if v is not None and not isinstance(v, bool):
        raise _wrong_type(key, "a boolean", v)
    return v


def _opt_number(body: Mapping[str, Any], key: str, lo: float, hi: float) -> float | None:
    v = body.get(key)
    if v is None:
        return None
    if not _is_number(v):
        raise _wrong_type(key, "a number", v)
    if not lo <= v <= hi:  # written this way round so NaN fails too
        raise _bad(key, f"Invalid value for '{key}': expected a number from {lo:g} to {hi:g}, but got {v!r} instead.")
    return v


def _opt_int(body: Mapping[str, Any], key: str, lo: int | None = None, hi: int | None = None) -> int | None:
    v = body.get(key)
    if v is None:
        return None
    if not _is_int(v):
        raise _wrong_type(key, "an integer", v)
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        want = f"at least {lo}" if hi is None else f"from {lo} to {hi}"
        raise _bad(key, f"Invalid value for '{key}': expected an integer {want}, but got {v} instead.")
    return v


def _json_bytes(value: Any) -> float:
    try:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())
    except (TypeError, ValueError, RecursionError):  # not JSON at all (Python callers only): treat as too big
        return float("inf")


def _deeper_than(value: Any, limit: int) -> bool:
    """True when `value` nests objects and arrays more than `limit` levels deep (iterative, so no recursion limit)."""
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        v, depth = stack.pop()
        children = v.values() if isinstance(v, dict) else v if isinstance(v, list) else None
        if children is None:
            continue
        if depth > limit:
            return True
        stack.extend((c, depth + 1) for c in children)
    return False


# ---------- messages ----------
# The message keys Ratchet checks, forwards and stores, by role. Any other key is dropped by `normalize_messages` (as
# unknown top-level keys are ignored), so it reaches neither the teacher nor the ledger.
MESSAGE_KEYS = frozenset({"role", "content", "name"})
ROLE_KEYS = {
    "assistant": MESSAGE_KEYS | {"refusal", "tool_calls", "function_call"},
    "tool": MESSAGE_KEYS | {"tool_call_id"},
}
TOOL_CALL_KEYS = frozenset({"id", "type", "function"})
FUNCTION_KEYS = frozenset({"name", "arguments"})


def _required_str(obj: Mapping[str, Any], key: str, at: str) -> str:
    v = obj.get(key)
    if v is None:
        raise _missing(f"{at}.{key}")
    if not isinstance(v, str):
        raise _wrong_type(f"{at}.{key}", "a string", v)
    return v


def _optional_str(obj: Mapping[str, Any], key: str, at: str) -> str:
    v = obj.get(key)
    if v is not None and not isinstance(v, str):
        raise _wrong_type(f"{at}.{key}", "a string", v)
    return v or ""


def _message(m: Any, at: str) -> int:
    """Check one message; return the characters of text it carries: content text and refusal parts, `name`, `refusal`,
    `tool_call_id`, and each tool call's id, function name and arguments (everything sent on but non-text parts)."""
    if not isinstance(m, dict):
        raise _wrong_type(at, "a message object", m)
    role = m.get("role")
    if role is None:
        raise _missing(f"{at}.role")
    if not isinstance(role, str):
        raise _wrong_type(f"{at}.role", "a string", role)
    if role not in ROLES:
        raise _bad(f"{at}.role", f"Invalid value for '{at}.role': {_short(role)} is not one of {list(ROLES)}.")
    chars = len(_optional_str(m, "name", at))
    calls = False
    if role == "assistant":
        calls, call_chars = _assistant_calls(m, at)
        chars += len(_optional_str(m, "refusal", at)) + call_chars
    if role == "tool":
        chars += len(_required_str(m, "tool_call_id", at))

    content = m.get("content")
    if content is None:
        if calls:
            return chars
        if "content" not in m:
            raise _missing(f"{at}.content")
        raise _bad(
            f"{at}.content",
            f"Invalid type for '{at}.content': null is allowed only in an assistant message with tool_calls or function_call.",
            INVALID_TYPE,
        )
    if isinstance(content, str):
        return chars + len(content)
    if not isinstance(content, list):
        raise _wrong_type(f"{at}.content", "a string or an array of content parts", content)
    if not content:
        raise _bad(f"{at}.content", f"Invalid value for '{at}.content': an array of content parts must not be empty.")
    for j, part in enumerate(content):
        p = f"{at}.content[{j}]"
        if not isinstance(part, dict):
            raise _wrong_type(p, "a content part object", part)
        kind = part.get("type")
        if kind is None:
            raise _missing(f"{p}.type")
        if not isinstance(kind, str):
            raise _wrong_type(f"{p}.type", "a string", kind)
        if kind in ("text", "refusal"):  # the part's text is under the key that names its type
            text = part.get(kind)
            if text is None:
                raise _missing(f"{p}.{kind}")
            if not isinstance(text, str):
                raise _wrong_type(f"{p}.{kind}", "a string", text)
            chars += len(text)
    return chars


def _function_call(fn: Any, at: str) -> int:
    """{name, arguments}, both strings (arguments is JSON text, as OpenAI returns it); return their characters."""
    if not isinstance(fn, dict):
        raise _wrong_type(at, "an object", fn)
    return len(_required_str(fn, "name", at)) + len(_required_str(fn, "arguments", at))


def _assistant_calls(m: Mapping[str, Any], at: str) -> tuple[bool, int]:
    """Whether an assistant message carries tool calls (then its content may be null), and their characters. Each call
    is checked in full ({id, type: 'function', function: {name, arguments}}), so a malformed one is this 400 naming the
    field, before the student's samples and the teacher's own refusal are paid for."""
    chars = 0
    tool_calls = m.get("tool_calls")
    if tool_calls is not None:
        if not isinstance(tool_calls, list):
            raise _wrong_type(f"{at}.tool_calls", "an array of tool calls", tool_calls)
        for k, call in enumerate(tool_calls):
            p = f"{at}.tool_calls[{k}]"
            if not isinstance(call, dict):
                raise _wrong_type(p, "a tool call object", call)
            chars += len(_required_str(call, "id", p))
            if _required_str(call, "type", p) != "function":
                message = f"Unsupported value for '{p}.type': only 'function' tool calls are supported."
                raise _bad(f"{p}.type", message, UNSUPPORTED_VALUE)
            if call.get("function") is None:
                raise _missing(f"{p}.function")
            chars += _function_call(call["function"], f"{p}.function")
    function_call = m.get("function_call")
    if function_call is not None:
        chars += _function_call(function_call, f"{at}.function_call")
    return bool(tool_calls) or function_call is not None, chars


def _messages(value: Any, limits: Limits) -> list[dict[str, Any]]:
    if value is None:
        raise _missing("messages")
    if not isinstance(value, list):
        raise _wrong_type("messages", "an array of message objects", value)
    if not value:
        raise _bad("messages", "Invalid value for 'messages': it must hold at least one message.")
    if limits.max_messages > 0 and len(value) > limits.max_messages:
        raise _bad("messages", f"Invalid value for 'messages': {len(value)} messages; the limit is {limits.max_messages}.")
    chars = sum(_message(m, f"messages[{i}]") for i, m in enumerate(value))
    if limits.max_input_chars > 0 and chars > limits.max_input_chars:
        raise _bad(
            "messages",
            f"The messages hold {chars} characters of text; this gateway accepts at most {limits.max_input_chars}.",
            CONTEXT_LENGTH,
        )
    return value


# ---------- other parameters ----------
def _stop(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list):
        raise _wrong_type("stop", "a string or an array of strings", value)
    if len(value) > MAX_STOP:
        raise _bad("stop", f"Invalid value for 'stop': at most {MAX_STOP} sequences, but got {len(value)}.")
    for k, s in enumerate(value):
        if not isinstance(s, str):
            raise _wrong_type(f"stop[{k}]", "a string", s)
    return value or None


def _logit_bias(value: Any) -> dict[str, int] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise _wrong_type("logit_bias", "an object mapping token ids to integers", value)
    for token, bias in value.items():
        if not _is_int(bias):
            raise _wrong_type("logit_bias", "integer bias values", bias)
        if not -MAX_LOGIT_BIAS <= bias <= MAX_LOGIT_BIAS:
            raise _bad(
                "logit_bias",
                f"Invalid value for 'logit_bias': the bias for token {_short(token)} must be from "
                f"-{MAX_LOGIT_BIAS} to {MAX_LOGIT_BIAS}, but got {bias}.",
            )
    return value


def _metadata(value: Any) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise _wrong_type("metadata", "an object of string values", value)
    if len(value) > MAX_METADATA:
        raise _bad("metadata", f"Invalid value for 'metadata': at most {MAX_METADATA} keys, but got {len(value)}.")
    for key, v in value.items():
        if not isinstance(key, str):  # JSON keys always are; Python callers may pass anything
            raise _wrong_type("metadata", "an object with string keys", key)
        if len(key) > MAX_METADATA_KEY:
            raise _bad("metadata", f"Invalid value for 'metadata': keys are at most {MAX_METADATA_KEY} characters.")
        param = f"metadata.{key}" if tasks.valid_name(key) else "metadata"
        if not isinstance(v, str):
            raise _wrong_type(param, "a string", v)
        if len(v) > MAX_METADATA_VALUE:
            raise _bad(param, f"Invalid value for '{param}': values are at most {MAX_METADATA_VALUE} characters.")
    task = value.get("ratchet_task")
    if task is not None and not tasks.valid_name(task):
        raise _bad(
            "metadata.ratchet_task",
            f"Invalid value for 'metadata.ratchet_task': {_short(task)} is not a task name "
            "(1 to 64 letters, digits, '_', '.' or '-').",
        )
    store = value.get("ratchet_store")
    if store is not None and store.strip().lower() not in STORE_METADATA:
        raise _bad(
            "metadata.ratchet_store",
            f"Invalid value for 'metadata.ratchet_store': {_short(store)} is not one of 'false', '0' (do not store) "
            "or 'true', '1'.",
        )
    return value


def _end_user(body: Mapping[str, Any], key: str) -> str | None:
    """`user` or `safety_identifier`: any string, as before Ratchet read them (only the body limit bounds it); the
    gateway hashes it whole (FR-13.2). The type error never quotes the value: it identifies a person, and errors are
    logged."""
    return _opt_str(body, key)


def _response_format(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise _wrong_type("response_format", "an object", value)
    kind = value.get("type")
    if kind is None:
        raise _missing("response_format.type")
    if not isinstance(kind, str):
        raise _wrong_type("response_format.type", "a string", kind)
    if kind not in RESPONSE_FORMATS:
        raise _bad(
            "response_format.type",
            f"Invalid value for 'response_format.type': {_short(kind)} is not one of {list(RESPONSE_FORMATS)}.",
        )
    if kind != "json_schema":
        return value
    spec = value.get("json_schema")
    if spec is None:
        raise _missing("response_format.json_schema")
    if not isinstance(spec, dict):
        raise _wrong_type("response_format.json_schema", "an object", spec)
    _name(spec.get("name"), "response_format.json_schema.name")
    strict = spec.get("strict")
    if strict is not None and not isinstance(strict, bool):
        raise _wrong_type("response_format.json_schema.strict", "a boolean", strict)
    schema = spec.get("schema")
    if schema is not None:
        param = "response_format.json_schema.schema"
        if not isinstance(schema, dict):
            raise _wrong_type(param, "an object", schema)
        if _deeper_than(schema, MAX_SCHEMA_DEPTH):
            raise _bad(param, f"Invalid value for '{param}': nested more than {MAX_SCHEMA_DEPTH} levels deep.")
        if _json_bytes(schema) > MAX_SCHEMA_BYTES:
            raise _bad(param, f"Invalid value for '{param}': larger than {MAX_SCHEMA_BYTES // 1024} KB.")
    return value


def _name(value: Any, param: str) -> str:
    if value is None:
        raise _missing(param)
    if not isinstance(value, str):
        raise _wrong_type(param, "a string", value)
    if not NAME_RE.fullmatch(value):
        raise _bad(param, f"Invalid value for '{param}': {_short(value)} must be 1 to 64 letters, digits, '_' or '-'.")
    return value


def _function_spec(fn: Any, param: str) -> None:
    if not isinstance(fn, dict):
        raise _wrong_type(param, "an object", fn)
    _name(fn.get("name"), f"{param}.name")
    parameters = fn.get("parameters")
    if parameters is not None and not isinstance(parameters, dict):
        raise _wrong_type(f"{param}.parameters", "an object", parameters)


def _tool_list(value: Any, key: str) -> list[dict[str, Any]] | None:
    """`tools` (each {type: 'function', function: {...}}) or the legacy `functions` (each {name, ...})."""
    if value is None:
        return None
    if not isinstance(value, list):
        raise _wrong_type(key, "an array", value)
    if len(value) > MAX_TOOLS:
        raise _bad(key, f"Invalid value for '{key}': at most {MAX_TOOLS} entries, but got {len(value)}.")
    for i, item in enumerate(value):
        at = f"{key}[{i}]"
        if key == "functions":
            _function_spec(item, at)
            continue
        if not isinstance(item, dict):
            raise _wrong_type(at, "a tool object", item)
        kind = item.get("type")
        if kind is None:
            raise _missing(f"{at}.type")
        if not isinstance(kind, str):
            raise _wrong_type(f"{at}.type", "a string", kind)
        if kind != "function":
            raise _bad(
                f"{at}.type",
                f"Unsupported value for '{at}.type': only 'function' tools are supported.",
                UNSUPPORTED_VALUE,
            )
        if item.get("function") is None:
            raise _missing(f"{at}.function")
        _function_spec(item["function"], f"{at}.function")
    if _json_bytes(value) > MAX_TOOLS_BYTES:
        raise _bad(key, f"Invalid value for '{key}': larger than {MAX_TOOLS_BYTES // 1024} KB.")
    return value


def _stream_options(value: Any) -> dict[str, Any] | None:
    """`stream_options`: an object whose `include_usage`, the one key Ratchet reads, is a boolean. Other keys
    (`include_obfuscation`, newer ones) are kept and ignored; the object is ignored without `stream: true`."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise _wrong_type("stream_options", "an object", value)
    include = value.get("include_usage")
    if include is not None and not isinstance(include, bool):
        raise _wrong_type("stream_options.include_usage", "a boolean", include)
    return value


def _choice(value: Any, key: str, allowed: tuple[str, ...]) -> str | dict[str, Any] | None:
    """`tool_choice` ('none' | 'auto' | 'required' | {type, function: {name}}) or `function_call` ('none' | 'auto' | {name})."""
    if value is None:
        return None
    if isinstance(value, str):
        if value not in allowed:
            raise _bad(key, f"Invalid value for '{key}': {_short(value)} is not one of {list(allowed)} or an object.")
        return value
    if not isinstance(value, dict):
        raise _wrong_type(key, "a string or an object", value)
    if key == "function_call":
        _name(value.get("name"), f"{key}.name")
        return value
    kind = value.get("type")
    if kind is None:
        raise _missing(f"{key}.type")
    if not isinstance(kind, str):
        raise _wrong_type(f"{key}.type", "a string", kind)
    if kind == "function":
        fn = value.get("function")
        if not isinstance(fn, dict):
            raise _wrong_type(f"{key}.function", "an object", fn)
        _name(fn.get("name"), f"{key}.function.name")
    return value


def validate_chat_request(body: Any, limits: Limits | None = None) -> ChatRequest:
    """Check a decoded `/v1/chat/completions` body; raise `APIError` (400) on the first problem, naming it with a JSON
    path in `param` (`messages[2].content`). O(body), no I/O. Messages are checked first, then the other fields."""
    limits = limits or Limits()
    if not isinstance(body, dict):
        raise _bad(None, f"The request body must be a JSON object, but got {_kind(body)} instead.", INVALID_TYPE)
    messages = _messages(body.get("messages"), limits)
    stream = _opt_bool(body, "stream")
    n = _opt_int(body, "n")
    if n is not None and n < 1:
        raise _bad("n", f"Invalid value for 'n': expected an integer of at least 1, but got {n} instead.")
    if n is not None and n > limits.max_n:
        message = (
            f"Unsupported value for 'n': this gateway returns at most {limits.max_n} choice(s) per request, "
            f"but got {n} (RATCHET_MAX_N)."
        )
        raise _bad("n", message, UNSUPPORTED_VALUE)
    max_completion_tokens = _opt_int(body, "max_completion_tokens", lo=1)
    max_tokens = _opt_int(body, "max_tokens", lo=1)
    logprobs = _opt_bool(body, "logprobs")
    top_logprobs = _opt_int(body, "top_logprobs", 0, MAX_TOP_LOGPROBS)
    if top_logprobs is not None and logprobs is not True:
        raise _bad("top_logprobs", "Invalid value for 'top_logprobs': it needs 'logprobs' set to true.")
    return ChatRequest(
        messages=messages,
        model=_opt_str(body, "model"),
        stream=bool(stream),
        stream_options=_stream_options(body.get("stream_options")),
        n=n or 1,
        max_tokens=max_completion_tokens if max_completion_tokens is not None else max_tokens,
        temperature=_opt_number(body, "temperature", 0, 2),
        top_p=_opt_number(body, "top_p", 0, 1),
        stop=_stop(body.get("stop")),
        seed=_opt_int(body, "seed"),
        presence_penalty=_opt_number(body, "presence_penalty", -2, 2),
        frequency_penalty=_opt_number(body, "frequency_penalty", -2, 2),
        logit_bias=_logit_bias(body.get("logit_bias")),
        logprobs=logprobs,
        top_logprobs=top_logprobs,
        response_format=_response_format(body.get("response_format")),
        tools=_tool_list(body.get("tools"), "tools"),
        tool_choice=_choice(body.get("tool_choice"), "tool_choice", TOOL_CHOICES),
        parallel_tool_calls=_opt_bool(body, "parallel_tool_calls"),
        functions=_tool_list(body.get("functions"), "functions"),
        function_call=_choice(body.get("function_call"), "function_call", FUNCTION_CALLS),
        user=_end_user(body, "user"),
        safety_identifier=_end_user(body, "safety_identifier"),
        store=_opt_bool(body, "store"),
        metadata=_metadata(body.get("metadata")),
        reasoning_effort=_opt_str(body, "reasoning_effort"),
        extra={k: v for k, v in body.items() if k not in KNOWN_FIELDS},
    )


# ---------- task resolution (FR-1.50, ADR 0048) ----------
RATCHET_MODEL = "ratchet"  # the model id any request may name; the task then comes from the request itself
# `ratchet/<task>` (or `ratchet:<task>`): a model id that names a task, for clients that can set neither a header nor
# metadata (a `model=` setting is all many frameworks and no-code tools expose). Matched in full.
MODEL_TASK = re.compile(r"ratchet[/:]([A-Za-z0-9_.-]{1,64})")
MODEL_NAMESPACE = ("ratchet/", "ratchet:")  # a model id that starts so names a task, or is a mistake
MODEL_POLICIES = ("lenient", "strict")  # RATCHET_MODEL_POLICY
AUTO_TASK_MODES = ("off", "system_prompt")  # RATCHET_AUTO_TASK
AUTO_SYSTEM_PREFIX = "auto-sys-"  # an auto task per system prompt: this and 10 hex digits of its SHA-256
AUTO_SYSTEM_DIGITS = 10
MAX_ALIASES = 256
MAX_ALIAS_CHARS = 256  # of an alias, and of a model id echoed in X-Ratchet-Model-Alias
_ALIAS = re.compile(r"[^\s,=]{1,256}")
# Where a request's task came from, in resolution order (`resolve_task`; the response's `ratchet.task_source`).
TASK_SOURCES = ("header", "metadata", "deployment", "model", "alias", "match", "response_format", "auto_system", "default")
# The request field an `unknown_task` error names, by source (the header and a deployment are not body fields).
TASK_PARAMS: Mapping[str, str] = MappingProxyType(
    {"metadata": "metadata.ratchet_task", "model": "model", "alias": "model", "response_format": "response_format"}
)
SHOWN_MODEL_CHARS = 100  # of a model id quoted in an error message


def alias_problem(model: Any, task: Any) -> str | None:
    """Why `model=task` cannot be a model alias (RATCHET_MODEL_ALIASES), or None."""
    if not isinstance(model, str) or not _ALIAS.fullmatch(model) or not model.isprintable():
        return f"{model!r} is not a model id (1 to {MAX_ALIAS_CHARS} characters, no spaces, ',' or '=')"
    if model == RATCHET_MODEL or model.startswith(MODEL_NAMESPACE):
        return f"{model!r} is a Ratchet model id already; an alias names some other model id"
    if not isinstance(task, str) or not tasks.valid_name(task):
        return f"the alias {model!r} names {task!r}, which is not a task name (1 to 64 letters, digits, '_', '.' or '-')"
    if task in tasks.RESERVED_NAMES:
        return f"the alias {model!r} names {task!r}, a reserved name (/v1/embeddings logs its requests under it)"
    return None


def parse_aliases(spec: str) -> dict[str, str]:
    """RATCHET_MODEL_ALIASES: comma-separated `model=task` pairs ('gpt-4o-mini=ticket-triage,gpt-4o=default'), spaces
    around each part ignored. One model id names one task; a task may have several aliases. The task need not be
    registered yet (the admin API may register it later); until it is, requests for the alias follow
    RATCHET_UNKNOWN_TASKS. ValueError names the variable and the entry, so a typo stops startup."""
    out: dict[str, str] = {}
    for entry in (e.strip() for e in (spec or "").split(",")):
        if not entry:
            continue
        model, sep, task = entry.partition("=")
        model, task = model.strip(), task.strip()
        if not sep:
            raise ValueError(f"RATCHET_MODEL_ALIASES: {entry!r} is not model=task")
        problem = alias_problem(model, task)
        if problem is not None:
            raise ValueError(f"RATCHET_MODEL_ALIASES: {problem}")
        if model in out:
            raise ValueError(f"RATCHET_MODEL_ALIASES: {model!r} appears twice")
        out[model] = task
    if len(out) > MAX_ALIASES:
        raise ValueError(f"RATCHET_MODEL_ALIASES: at most {MAX_ALIASES} aliases, not {len(out)}")
    return out


@dataclass(frozen=True)
class ModelRouting:
    """How the `model` field and the system prompt choose a request's task (`Settings.model_routing()`, ADR 0048):
    RATCHET_MODEL_POLICY (`lenient`: a model id Ratchet does not know is served as `ratchet`; `strict`: it is a 404
    `model_not_found`), RATCHET_MODEL_ALIASES (`aliases`: model id -> task name) and RATCHET_AUTO_TASK (`off`, or
    `system_prompt`: a request no other rule places gets a task of its own per first system message). Checked when
    built: a bad value raises ValueError naming the variable, so startup stops. Immutable."""

    policy: str = "lenient"
    aliases: Mapping[str, str] = field(default_factory=dict)
    auto_task: str = "off"

    def __post_init__(self) -> None:
        if self.policy not in MODEL_POLICIES:
            raise ValueError(f"RATCHET_MODEL_POLICY must be {' or '.join(MODEL_POLICIES)}, not {self.policy!r}")
        if self.auto_task not in AUTO_TASK_MODES:
            raise ValueError(f"RATCHET_AUTO_TASK must be {' or '.join(AUTO_TASK_MODES)}, not {self.auto_task!r}")
        if not isinstance(self.aliases, Mapping):
            raise ValueError("RATCHET_MODEL_ALIASES must map model ids to task names")
        if len(self.aliases) > MAX_ALIASES:
            raise ValueError(f"RATCHET_MODEL_ALIASES: at most {MAX_ALIASES} aliases, not {len(self.aliases)}")
        for model, task in self.aliases.items():
            problem = alias_problem(model, task)
            if problem is not None:
                raise ValueError(f"RATCHET_MODEL_ALIASES: {problem}")
        object.__setattr__(self, "aliases", MappingProxyType(dict(self.aliases)))


class ModelRef(NamedTuple):
    """What a request's `model` names (`classify_model`). `kind`: `ratchet` (that id, or none sent), `task`
    (`ratchet/<task>`), `alias` (RATCHET_MODEL_ALIASES), `unknown` (any other id) or `invalid` (in Ratchet's namespace,
    `ratchet/...`, but not a task name). `task` is the task named, for `task` and `alias`."""

    kind: str
    task: str | None = None


def classify_model(model: str | None, routing: ModelRouting) -> ModelRef:
    """What `model` names. No I/O: one regex match in full and a dict lookup. A missing or empty model counts as
    `ratchet`, as before v0.5, so clients that never sent one keep working under either policy."""
    if not model or model == RATCHET_MODEL:
        return ModelRef("ratchet")
    m = MODEL_TASK.fullmatch(model)
    if m:
        return ModelRef("task", m.group(1))
    if model.startswith(MODEL_NAMESPACE):
        return ModelRef("invalid")
    target = routing.aliases.get(model)
    return ModelRef("alias", target) if target is not None else ModelRef("unknown")


def model_not_found(model: str, param: str | None = "model", hint: str = "") -> APIError:
    """404 `model_not_found` in OpenAI's words ("The model 'gpt-4o' does not exist"), quoting at most 100 characters."""
    shown = model if len(model) <= SHOWN_MODEL_CHARS else model[: SHOWN_MODEL_CHARS - 3] + "..."
    return APIError(404, f"The model '{shown}' does not exist{hint}.", INVALID_REQUEST, MODEL_NOT_FOUND, param)


def check_model(model: str | None, routing: ModelRouting) -> str | None:
    """Whether a request's `model` may be served, checked before anything is logged or billed. None for `ratchet`, a
    `ratchet/<task>` id and an alias. For an id Ratchet does not know: under `lenient` the id itself, which the gateway
    echoes in `X-Ratchet-Model-Alias` while it serves the request as `ratchet`; under `strict` a 404 `model_not_found`.
    An id in Ratchet's namespace that names no task (`ratchet/bad name`) is a 404 under either policy: whoever writes
    `ratchet/` means a task, and serving it as another would distill its answers under the wrong one."""
    ref = classify_model(model, routing)
    if ref.kind == "invalid":
        assert model is not None
        hint = ": a Ratchet model is 'ratchet' or 'ratchet/<task>', a task name being 1 to 64 letters, digits, '_', '.' or '-'"
        raise model_not_found(model, hint=hint)
    if ref.kind != "unknown":
        return None
    assert model is not None
    if routing.policy == "strict":
        raise model_not_found(model)
    return model


# Characters a header value keeps as they are: visible ASCII but '%'. h11, the HTTP/1.1 server uvicorn runs here, refuses
# a value that starts or ends with a space, so spaces are encoded too; '%' is, so every value decodes back to the id.
_HEADER_SAFE = "".join(chr(c) for c in range(0x21, 0x7F) if chr(c) != "%")


def header_value(text: str, limit: int = MAX_ALIAS_CHARS) -> str:
    """`text` as an HTTP header value: UTF-8 percent-encoded except for visible ASCII, so a model id in any script, with
    spaces around it or control characters in it, cannot break a response that was already served and billed; at most
    `limit` characters, never cutting an escape in two."""
    out = quote(text.encode("utf-8", "replace"), safe=_HEADER_SAFE)
    if len(out) <= limit:
        return out
    cut = out[:limit]
    broken = cut.rfind("%", limit - 2)  # a '%' among the last two characters starts an escape the cut broke
    return cut[:broken] if broken != -1 else cut


def system_text(messages: Iterable[Mapping[str, Any]]) -> str | None:
    """The text of a request's first system (or developer) message, its text parts joined by newlines as
    `normalize_messages` joins them; None when it has none. Read from the validated messages, so the answer does not
    depend on RATCHET_MERGE_SYSTEM."""
    for m in messages:
        if m.get("role") not in ("system", "developer"):
            continue
        content = m.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = (p.get("text") for p in content if isinstance(p, Mapping) and p.get("type") == "text")
            return "\n".join(t for t in parts if isinstance(t, str))
        return None
    return None


def system_prompt_task(messages: Iterable[Mapping[str, Any]]) -> str | None:
    """The auto task name for a request's system prompt (RATCHET_AUTO_TASK=system_prompt): `auto-sys-` and the first 10
    hex digits of SHA-256 over its first system message (`system_text`), the same for every request with that prompt;
    None without one, or for one of whitespace only. The name holds a digest, never the prompt's text (INV-12)."""
    text = system_text(messages)
    if text is None or not text.strip():
        return None
    digest = hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()
    return AUTO_SYSTEM_PREFIX + digest[:AUTO_SYSTEM_DIGITS]


def _schema_name(req: ChatRequest) -> str | None:
    rf = req.response_format
    spec = rf.get("json_schema") if isinstance(rf, Mapping) and rf.get("type") == "json_schema" else None
    name = spec.get("name") if isinstance(spec, Mapping) else None
    return name if isinstance(name, str) else None


def _tool_names(req: ChatRequest) -> frozenset[str] | None:
    """The function names of a request's `tools` and legacy `functions`; None when it has neither."""
    names: set[str] = set()
    for tool in req.tools or ():
        fn = tool.get("function") if isinstance(tool, Mapping) else None
        if isinstance(fn, Mapping) and isinstance(fn.get("name"), str):
            names.add(fn["name"])
    for fn in req.functions or ():
        if isinstance(fn, Mapping) and isinstance(fn.get("name"), str):
            names.add(fn["name"])
    return frozenset(names) if names else None


def _holds(key: str, rule: Any, have: Any) -> bool:
    """One match rule against the request's feature (`match_task`): plain comparisons only."""
    if have is None:
        return False
    if key == "system_prefix":
        return have.startswith(rule)
    if key == "schema_name":
        return have == rule
    return have == frozenset(rule)  # tools: exactly that set of names


def match_task(req: ChatRequest, client: str | None = None) -> str | None:
    """The first registered task, in registry order (built-in, then the tasks file's, then API and auto tasks), whose
    match rules (`Task.match_rules`, a spec's `match`) all hold for the request and that `client` may use
    (`Task.allows`, so a rule never turns a request that named no task into a 403): `system_prefix` (the first system
    message starts with it), `schema_name` (`response_format.json_schema.name` is it) and `tools` (the names of the
    request's `tools` and legacy `functions` are exactly that set). Plain string and set comparisons, never a pattern,
    so no request can make this slow; each request feature is read at most once, and only for a task that has rules."""
    seen: dict[str, Any] = {}
    readers = {"system_prefix": lambda: system_text(req.messages), "schema_name": lambda: _schema_name(req)}
    for t in tasks.REGISTRY.values():  # a published dict is never changed (`tasks.register` publishes a new one)
        rules = t.match_rules
        if not rules or not t.allows(client):
            continue
        ok = True
        for key, rule in rules.items():
            if key not in seen:
                seen[key] = readers[key]() if key in readers else _tool_names(req)
            if not _holds(key, rule, seen[key]):
                ok = False
                break
        if ok:
            return t.name
    return None


def resolve_task(
    req: ChatRequest,
    header: str | None,
    routing: ModelRouting,
    *,
    client: str | None = None,
    deployment: str | None = None,
) -> tuple[str, str]:
    """(task name, source) for a request: the first of these rules that names a task (`TASK_SOURCES`,
    docs/API.md#task-resolution):

    1. the `X-Ratchet-Task` header (`header`), source `header`;
    2. `metadata.ratchet_task`, `metadata`;
    3. a `model` of `ratchet/<task>` or `ratchet:<task>`, `model`; on the Azure-style route the deployment in the URL
       (`deployment`) instead, `deployment` (the body's `model` is not read there; `ratchet` goes on to rule 5);
    4. a model alias (RATCHET_MODEL_ALIASES; also for a deployment), `alias`;
    5. the first registered task whose match rules all hold (`match_task`), `match`;
    6. an auto task from a `response_format` json_schema (ADR 0042), `response_format`: decided by the gateway
       (`gateway._schema_auto`) and `router.Ratchet.resolve`, since it registers the task, so this function returns
       rule 7 or 8 for such a request;
    7. with RATCHET_AUTO_TASK=system_prompt, `auto-sys-<digest>` of the first system message (`system_prompt_task`),
       `auto_system`;
    8. `default`.

    A name from rules 1 to 4 may be unregistered, or (a header) not even a task name: `router.Ratchet.task_for` decides
    about it as RATCHET_UNKNOWN_TASKS says. Only `auto_system` may name a task the caller has to create. No I/O and no
    registry writes. The model field never selects an upstream model (ADR 0048). Raises APIError 404
    `model_not_found` for a deployment that is neither `ratchet`, an alias nor a task name."""
    if header:
        return header, "header"
    if req.task is not None:
        return req.task, "metadata"
    if deployment is not None:
        if deployment != RATCHET_MODEL:
            m = MODEL_TASK.fullmatch(deployment)
            if m:
                return m.group(1), "deployment"
            target = routing.aliases.get(deployment)
            if target is not None:
                return target, "alias"
            if not tasks.valid_name(deployment):
                raise model_not_found(deployment, param=None, hint=": a deployment names a task")
            return deployment, "deployment"
    else:
        ref = classify_model(req.model, routing)
        if ref.kind == "task":
            assert ref.task is not None
            return ref.task, "model"
        if ref.kind == "alias":
            assert ref.task is not None
            return ref.task, "alias"
    matched = match_task(req, client)
    if matched is not None:
        return matched, "match"
    if routing.auto_task == "system_prompt":
        name = system_prompt_task(req.messages)
        if name is not None:
            return name, "auto_system"
    return "default", "default"


# ---------- responses ----------
SERVICE_TIER = "default"  # the response's `service_tier`: Ratchet has one


def usage_object(prompt_tokens: int, completion_tokens: int, reasoning_tokens: int = 0, cached_tokens: int = 0) -> dict[str, Any]:
    """A chat completion's `usage` in OpenAI's shape. Reasoning tokens are part of `completion_tokens` and cached tokens
    part of `prompt_tokens`, as in OpenAI's. Ratchet has no audio and no predicted outputs, so those counts are 0."""
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "prompt_tokens_details": {"cached_tokens": cached_tokens, "audio_tokens": 0},
        "completion_tokens_details": {
            "reasoning_tokens": reasoning_tokens,
            "audio_tokens": 0,
            "accepted_prediction_tokens": 0,
            "rejected_prediction_tokens": 0,
        },
    }


# ---------- normalisation ----------
@dataclass
class Flags:
    """What normalisation found. `multimodal`: a non-text part the teacher reads (RATCHET_TEACHER_MODALITIES), so
    the request must not go to the student or be stored."""

    multimodal: bool = False


def parse_modalities(spec: str) -> frozenset[str]:
    """RATCHET_TEACHER_MODALITIES ('text,image_url'): the content part types the teacher accepts. Text always."""
    return frozenset({TEXT_MODALITY, *(s.strip() for s in spec.split(",") if s.strip())})


def _known_call(call: dict[str, Any]) -> dict[str, Any]:
    fn = call["function"]
    if call.keys() <= TOOL_CALL_KEYS and fn.keys() <= FUNCTION_KEYS:
        return call
    return {"id": call["id"], "type": call["type"], "function": {k: fn[k] for k in ("name", "arguments")}}


def _normalize(m: dict[str, Any], i: int, modalities: frozenset[str], flags: Flags) -> dict[str, Any]:
    role = m.get("role")
    out = m
    keys = ROLE_KEYS.get(role, MESSAGE_KEYS)
    if not m.keys() <= keys:  # unknown keys (assistant `audio`, `annotations`, vendor fields): not sent on
        out = {k: v for k, v in m.items() if k in keys}
    if role == "developer":  # OpenAI's newer name for the system role; open models know only 'system'
        out = {**out, "role": "system"}
    calls = out.get("tool_calls")
    if calls:
        known = [_known_call(c) for c in calls]
        if any(a is not b for a, b in zip(known, calls, strict=True)):
            out = {**out, "tool_calls": known}
    fc = out.get("function_call")
    if fc is not None and not fc.keys() <= FUNCTION_KEYS:
        out = {**out, "function_call": {k: fc[k] for k in ("name", "arguments")}}
    content = out.get("content")
    if not isinstance(content, list):
        return out
    texts: list[str] = []
    other = False
    for j, part in enumerate(content):
        kind = part.get("type")
        if kind == "text":
            texts.append(part["text"])
        elif kind == "refusal" and role == "assistant":
            texts.append(part["refusal"])
        elif kind in modalities:
            other = True
        else:
            raise APIError(
                400,
                f"Unsupported content part type {_short(kind)} in 'messages[{i}].content[{j}]': this gateway accepts "
                f"{sorted(modalities)} (RATCHET_TEACHER_MODALITIES).",
                code=UNSUPPORTED_CONTENT,
                param=f"messages[{i}].content[{j}].type",
            )
    if other:  # the teacher gets the parts as sent
        flags.multimodal = True
        return out
    return {**out, "content": "\n".join(texts)}


def _merge_leading_system(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    k = 0
    while k < len(messages) and messages[k].get("role") == "system" and isinstance(messages[k].get("content"), str):
        k += 1
    if k < 2:
        return messages
    return [{"role": "system", "content": "\n\n".join(m["content"] for m in messages[:k])}, *messages[k:]]


def normalize_messages(
    messages: list[dict[str, Any]],
    modalities: frozenset[str] = frozenset({TEXT_MODALITY}),
    merge_system: bool = False,
) -> tuple[list[dict[str, Any]], Flags]:
    """Rewrite validated messages into the plain form the student and the ledger use: `developer` becomes `system`, a
    content list of text parts becomes one string (parts joined by newlines), an assistant refusal part becomes
    text, assistant `audio` is dropped, and with `merge_system` consecutive leading system messages become one
    (joined by blank lines). A part of any other type is a 400 (`unsupported_content_type`) unless its type is in
    `modalities`; then the message is kept as sent and `flags.multimodal` is set.

    A message whose content is already a string, and that needs none of these changes, is returned as the same
    object, and when nothing changes the same list comes back: example hashes, golden splits and mock lookups of
    plain requests stay exactly as they were."""
    flags = Flags()
    out = [_normalize(m, i, modalities, flags) for i, m in enumerate(messages)]
    if merge_system:
        out = _merge_leading_system(out)
    unchanged = len(out) == len(messages) and all(a is b for a, b in zip(out, messages, strict=True))
    return (messages if unchanged else out), flags


# ---------- the other OpenAI endpoints (FR-1.51, ADR 0050) ----------
UNSUPPORTED_PARAMETER = "unsupported_parameter"
UNSUPPORTED_ENDPOINT = "unsupported_endpoint"
DOCS_URL = "https://github.com/harshbhx/ratchet/blob/main/docs/API.md#openai-endpoints"  # the endpoint table


def unsupported_endpoint(method: str, path: str, why: str) -> APIError:
    """404 `unsupported_endpoint` for an OpenAI endpoint this gateway knows but does not serve, saying why and where the
    supported ones are listed. A path Ratchet does not know at all is `unknown_url` (`http_error`)."""
    message = f"{method} {path} is not supported by this gateway: {why}. The supported endpoints are listed at {DOCS_URL}"
    return APIError(404, message, "not_found_error", UNSUPPORTED_ENDPOINT)


def unsupported_parameter(param: str, why: str) -> APIError:
    """400 `unsupported_parameter`: a field this gateway understands but cannot honour, so serving the request without it
    would answer a different question."""
    return _bad(param, f"Unsupported parameter: '{param}' is not supported by this gateway: {why}.", UNSUPPORTED_PARAMETER)


# /v1/embeddings: a pass-through to one configured model, nothing stored (FR-1.51).
EMBEDDINGS_TASK = tasks.EMBEDDINGS_TASK  # the task an embeddings request is logged under; reserved, never a chat task
ENCODING_FORMATS = ("float", "base64")
MAX_EMBEDDING_INPUTS = 2048  # OpenAI's own limit on the items of one request
MAX_DIMENSIONS = 8192  # `dimensions`, at most: twice the largest embedding models served today
MAX_TOKEN_ID = 2**31 - 1
CHARS_PER_TOKEN = 4  # a token array counts against RATCHET_MAX_INPUT_CHARS at Ratchet's usual 4 characters a token
EMBEDDINGS_FIELDS = frozenset({"input", "model", "encoding_format", "dimensions", "user"})


@dataclass
class EmbeddingsRequest:
    """A validated `/v1/embeddings` body. `inputs` is always a list, one item per vector: non-empty strings, or non-empty
    token arrays (lists of token ids), never both. `encoding_format` is what the client gets (`float` when it sent none);
    the upstream is always asked for floats. `model` is read only to check it (`check_embeddings_model`): the configured
    model always answers. `extra` holds unknown keys, kept and ignored, never forwarded."""

    inputs: list[Any]
    model: str | None = None
    encoding_format: str = "float"
    dimensions: int | None = None
    user: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _token_array(value: list[Any], param: str) -> list[int]:
    if not value:
        raise _bad(param, f"Invalid value for '{param}': a token array must not be empty.")
    for k, token in enumerate(value):
        if not _is_int(token):
            raise _wrong_type(f"{param}[{k}]", "a token id (an integer)", token)
        if not 0 <= token <= MAX_TOKEN_ID:
            raise _bad(f"{param}[{k}]", f"Invalid value for '{param}[{k}]': a token id is 0 to {MAX_TOKEN_ID}, not {token}.")
    return value


def _embedding_inputs(value: Any) -> list[Any]:
    """`input` as a list of items: a string, one token array, an array of strings or an array of token arrays."""
    if value is None:
        raise _missing("input")
    if isinstance(value, str):
        if not value:
            raise _bad("input", "Invalid value for 'input': the text must not be empty.")
        return [value]
    if not isinstance(value, list):
        raise _wrong_type("input", "a string, an array of strings or an array of token arrays", value)
    if not value:
        raise _bad("input", "Invalid value for 'input': it must hold at least one item.")
    if _is_int(value[0]):  # one token array
        return [_token_array(value, "input")]
    if len(value) > MAX_EMBEDDING_INPUTS:
        raise _bad("input", f"Invalid value for 'input': at most {MAX_EMBEDDING_INPUTS} items, but got {len(value)}.")
    texts = isinstance(value[0], str)
    for i, item in enumerate(value):
        at = f"input[{i}]"
        if texts:
            if not isinstance(item, str):
                raise _wrong_type(at, "a string, as the first item is", item)
            if not item:
                raise _bad(at, f"Invalid value for '{at}': the text must not be empty.")
        elif isinstance(value[0], list):
            if not isinstance(item, list):
                raise _wrong_type(at, "a token array, as the first item is", item)
            _token_array(item, at)
        else:
            raise _wrong_type(at, "a string or a token array", item)
    return value


def validate_embeddings_request(body: Any, limits: Limits | None = None) -> EmbeddingsRequest:
    """Check a decoded `/v1/embeddings` body; raise `APIError` (400) on the first problem, naming it in `param`
    (`input[3]`). The inputs' text counts against RATCHET_MAX_INPUT_CHARS (a token as CHARS_PER_TOKEN characters), as a
    chat request's messages do. O(body), no I/O."""
    limits = limits or Limits()
    if not isinstance(body, dict):
        raise _bad(None, f"The request body must be a JSON object, but got {_kind(body)} instead.", INVALID_TYPE)
    inputs = _embedding_inputs(body.get("input"))
    chars = sum(len(item) if isinstance(item, str) else CHARS_PER_TOKEN * len(item) for item in inputs)
    if limits.max_input_chars > 0 and chars > limits.max_input_chars:
        message = f"The input holds {chars} characters of text; this gateway accepts at most {limits.max_input_chars}."
        raise _bad("input", message, CONTEXT_LENGTH)
    encoding = _opt_str(body, "encoding_format")
    if encoding is not None and encoding not in ENCODING_FORMATS:
        message = f"Invalid value for 'encoding_format': {_short(encoding)} is not one of {list(ENCODING_FORMATS)}."
        raise _bad("encoding_format", message)
    return EmbeddingsRequest(
        inputs=inputs,
        model=_opt_str(body, "model"),
        encoding_format=encoding or "float",
        dimensions=_opt_int(body, "dimensions", 1, MAX_DIMENSIONS),
        user=_opt_str(body, "user"),
        extra={k: v for k, v in body.items() if k not in EMBEDDINGS_FIELDS},
    )


def check_embeddings_model(model: str | None, served: str, routing: ModelRouting) -> str | None:
    """Whether an embeddings request's `model` may be served by `served`, the configured model, before anything is logged
    or billed. None for no model, `ratchet` and `served` itself. Any other id under RATCHET_MODEL_POLICY=lenient comes
    back, for the gateway to echo in `X-Ratchet-Model-Alias` while `served` answers; under `strict` it is a 404
    `model_not_found`, because vectors from another model than the one an index was built with cannot be compared with
    it. The id never selects an upstream model."""
    if not model or model in (RATCHET_MODEL, served):
        return None
    if routing.policy == "strict":
        raise model_not_found(model, hint=f": this gateway embeds with '{served}'")
    return model


# /v1/completions: the legacy text API, each prompt one user message through the chat path (FR-1.51).
COMPLETION_ONLY = ("prompt", "suffix", "echo", "best_of", "logprobs")  # read here, never passed to the chat request
MAX_PROMPTS = 16  # prompts per request: each is a cascade request of its own, logged and counted against the quota
MAX_LEGACY_LOGPROBS = 5  # the completions API's own limit on `logprobs`


def prompt_messages(prompt: str) -> list[dict[str, Any]]:
    """A prompt as chat messages: one user message, so it is answered, logged and stored exactly as that chat request."""
    return [{"role": "user", "content": prompt}]


@dataclass
class CompletionRequest:
    """A validated `/v1/completions` body: its prompts, and the chat request for the first one, which carries every other
    parameter (`requests()` gives one per prompt). `logprobs` is the legacy integer (0 to 5 alternatives per token), None
    when not sent; the chat request asks for token logprobs with that many alternatives."""

    prompts: list[str]
    chat: ChatRequest
    logprobs: int | None = None

    def requests(self) -> list[ChatRequest]:
        return [self.chat if i == 0 else replace(self.chat, messages=prompt_messages(p)) for i, p in enumerate(self.prompts)]


def _prompts(value: Any, limits: Limits) -> list[str]:
    if value is None:
        raise _missing("prompt")
    if isinstance(value, str):
        prompts = [value]
    elif isinstance(value, list):
        if not value:
            raise _bad("prompt", "Invalid value for 'prompt': it must hold at least one prompt.")
        if any(_is_int(v) or isinstance(v, list) for v in value):
            message = "Unsupported value for 'prompt': token arrays are not supported; send the prompt as text."
            raise _bad("prompt", message, UNSUPPORTED_VALUE)
        for i, v in enumerate(value):
            if not isinstance(v, str):
                raise _wrong_type(f"prompt[{i}]", "a string", v)
        if len(value) > MAX_PROMPTS:
            message = f"Unsupported value for 'prompt': at most {MAX_PROMPTS} prompts per request, but got {len(value)}."
            raise _bad("prompt", message, UNSUPPORTED_VALUE)
        prompts = value
    else:
        raise _wrong_type("prompt", "a string or an array of strings", value)
    if limits.max_input_chars > 0:
        for i, p in enumerate(prompts):
            if len(p) > limits.max_input_chars:
                param = "prompt" if isinstance(value, str) else f"prompt[{i}]"
                message = f"The prompt holds {len(p)} characters of text; this gateway accepts at most {limits.max_input_chars}."
                raise _bad(param, message, CONTEXT_LENGTH)
    return prompts


def validate_completion_request(body: Any, limits: Limits | None = None) -> CompletionRequest:
    """Check a decoded `/v1/completions` body; raise `APIError` (400) on the first problem. `prompt` is a string or up to
    MAX_PROMPTS strings (token arrays are a 400 `unsupported_value`). `suffix`, `echo` and `best_of` are a 400
    `unsupported_parameter` when they would change the answer (an empty suffix, `echo: false` and a `best_of` of 1 or `n`
    change nothing and are accepted). Every other field is a chat-completions field of the same name, checked by
    `validate_chat_request`, so its errors name it as sent. O(body), no I/O."""
    limits = limits or Limits()
    if not isinstance(body, dict):
        raise _bad(None, f"The request body must be a JSON object, but got {_kind(body)} instead.", INVALID_TYPE)
    if _opt_str(body, "suffix"):
        raise unsupported_parameter("suffix", "a chat model cannot fill in text before a suffix")
    if _opt_bool(body, "echo"):
        raise unsupported_parameter("echo", "the prompt is not sent back with the completion")
    prompts = _prompts(body.get("prompt"), limits)
    best_of = _opt_int(body, "best_of", lo=1)
    n = body.get("n") if _is_int(body.get("n")) else 1
    if best_of is not None and best_of not in (1, n):
        raise unsupported_parameter("best_of", "the gateway returns the n choices it samples, it does not rank more")
    logprobs = _opt_int(body, "logprobs", 0, MAX_LEGACY_LOGPROBS)
    chat_body = {k: v for k, v in body.items() if k not in COMPLETION_ONLY}
    chat_body["messages"] = prompt_messages(prompts[0])
    if logprobs is not None:
        chat_body.update(logprobs=True, top_logprobs=logprobs)
    return CompletionRequest(prompts, validate_chat_request(chat_body, limits), logprobs)


def legacy_logprobs(entries: list[dict[str, Any]] | None, offset: int = 0) -> dict[str, Any] | None:
    """A choice's token logprobs in the completions API's shape (`tokens`, `token_logprobs`, `top_logprobs` as a
    {token: logprob} object per token, `text_offset`) from the chat shape's `logprobs.content`, whose tokens spell the
    text exactly. Offsets count from `offset` (the prompt's length, as OpenAI counts them without `echo`). None when the
    logprobs cannot be given."""
    if entries is None:
        return None
    out: dict[str, list[Any]] = {"tokens": [], "token_logprobs": [], "top_logprobs": [], "text_offset": []}
    pos = offset
    for e in entries:
        out["tokens"].append(e["token"])
        out["token_logprobs"].append(e["logprob"])
        out["top_logprobs"].append({a["token"]: a["logprob"] for a in e.get("top_logprobs") or ()})
        out["text_offset"].append(pos)
        pos += len(e["token"])
    return out


# /v1/responses: the stateless subset, as a chat request (FR-1.51). Ratchet keeps no response state (INV-12).
RESPONSES_REFUSED: Mapping[str, str] = MappingProxyType(
    {
        "previous_response_id": "Ratchet keeps no response state; send the whole conversation in 'input'",
        "conversation": "Ratchet keeps no conversation state; send the whole conversation in 'input'",
        "prompt": "stored prompt templates live on OpenAI's side; send 'instructions' and 'input'",
    }
)
RESPONSES_FLAGS: Mapping[str, str] = MappingProxyType(  # refused only when true
    {
        "background": "a background response would need stored state; send the request and wait for the answer",
        "stream": "streamed response events are not served; use /v1/chat/completions with stream, or wait for the answer",
    }
)
# Responses fields that are accepted and have no effect here: what to include (Ratchet returns no reasoning items or
# logprobs), truncation (the input is never cut), the tier, storage (nothing is stored), and caching hints.
RESPONSES_IGNORED = frozenset(
    {
        "include",
        "truncation",
        "service_tier",
        "store",
        "top_logprobs",
        "max_tool_calls",
        "prompt_cache_key",
        "prompt_cache_retention",
        "stream_options",
    }
)
RESPONSES_READ = frozenset(
    {
        "model",
        "input",
        "instructions",
        "text",
        "tools",
        "tool_choice",
        "parallel_tool_calls",
        "max_output_tokens",
        "temperature",
        "top_p",
        "metadata",
        "user",
        "safety_identifier",
        "reasoning",
    }
)
INPUT_ROLES = ("user", "assistant", "system", "developer")
TEXT_PARTS = ("input_text", "output_text")
# `reasoning.effort` values the OpenAI SDK's `Response` model accepts (`openai.types.shared.ReasoningEffort`). A response
# echoes the value, so another one would be served and then fail to parse in the SDK; /v1/chat/completions echoes
# nothing and keeps any string.
REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


@dataclass
class ResponsesRequest:
    """A validated stateless `/v1/responses` body: the chat request it becomes (`chat`), and what the response object
    echoes back as sent (`instructions`, `text`, `tools`, `tool_choice`, `parallel_tool_calls`, the sampling fields)."""

    chat: ChatRequest
    instructions: str | None = None
    text: dict[str, Any] = field(default_factory=lambda: {"format": {"type": "text"}})
    tools: list[dict[str, Any]] = field(default_factory=list)
    tool_choice: str | dict[str, Any] = "auto"
    parallel_tool_calls: bool = True
    max_output_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    reasoning_effort: str | None = None


def _part_text(part: Mapping[str, Any], key: str, at: str) -> str:
    text = part.get(key)
    if text is None:
        raise _missing(f"{at}.{key}")
    if not isinstance(text, str):
        raise _wrong_type(f"{at}.{key}", "a string", text)
    return text


def _input_parts(content: list[Any], at: str) -> list[dict[str, Any]]:
    """A message item's content parts as chat content parts: text parts become `text` parts (joined into one string by
    `normalize_messages`, as a chat request's are), an `input_image` with a URL an `image_url` part (the teacher reads
    it only under RATCHET_TEACHER_MODALITIES). Files, audio and images by file id are a 400 `unsupported_value`."""
    if not content:
        raise _bad(f"{at}.content", f"Invalid value for '{at}.content': an array of content parts must not be empty.")
    parts: list[dict[str, Any]] = []
    for j, part in enumerate(content):
        p = f"{at}.content[{j}]"
        if not isinstance(part, dict):
            raise _wrong_type(p, "a content part object", part)
        kind = part.get("type")
        if kind is None:
            raise _missing(f"{p}.type")
        if not isinstance(kind, str):
            raise _wrong_type(f"{p}.type", "a string", kind)
        if kind in TEXT_PARTS:
            parts.append({"type": "text", "text": _part_text(part, "text", p)})
        elif kind == "refusal":
            parts.append({"type": "text", "text": _part_text(part, "refusal", p)})
        elif kind == "input_image" and isinstance(part.get("image_url"), str) and part["image_url"]:
            image: dict[str, Any] = {"url": part["image_url"]}
            if isinstance(part.get("detail"), str):
                image["detail"] = part["detail"]
            parts.append({"type": "image_url", "image_url": image})
        elif kind in ("input_image", "input_file", "input_audio"):
            message = f"Unsupported value for '{p}': {kind} parts are supported only as an image URL ('image_url')."
            raise _bad(p, message, UNSUPPORTED_VALUE)
        else:
            raise _bad(f"{p}.type", f"Invalid value for '{p}.type': {_short(kind)} is not a content part type.")
    return parts


def _output_text(value: Any, at: str) -> str:
    """A function call's output: a string, or text parts joined by newlines."""
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        raise _wrong_type(at, "a string or an array of text parts", value)
    texts = []
    for j, part in enumerate(value):
        p = f"{at}[{j}]"
        if not isinstance(part, dict) or part.get("type") not in TEXT_PARTS:
            raise _bad(p, f"Unsupported value for '{p}': a function call's output may hold text parts only.", UNSUPPORTED_VALUE)
        texts.append(_part_text(part, "text", p))
    return "\n".join(texts)


def _input_messages(value: Any) -> list[dict[str, Any]]:
    """`input` as chat messages: a string is one user message; message items keep their role (content strings, or text
    and image parts); consecutive `function_call` items become the tool calls of one assistant message (the assistant
    message item right before them, as a response's `output` lists its text and then its calls, so a tool loop is
    stored as the chat request it would be), and each `function_call_output` a tool message. `reasoning` items are
    dropped (Ratchet returns none, and a model's reasoning is never sent back to it) and split nothing. An
    `item_reference`, or a built-in tool's item, needs state Ratchet does not keep: a 400."""
    if value is None:
        raise _missing("input")
    if isinstance(value, str):
        return prompt_messages(value)
    if not isinstance(value, list):
        raise _wrong_type("input", "a string or an array of input items", value)
    if not value:
        raise _bad("input", "Invalid value for 'input': it must hold at least one item.")
    out: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] | None = None  # the tool calls of the assistant message being built
    for i, item in enumerate(value):
        at = f"input[{i}]"
        if not isinstance(item, dict):
            raise _wrong_type(at, "an input item object", item)
        kind = item.get("type", "message")
        if not isinstance(kind, str):
            raise _wrong_type(f"{at}.type", "a string", kind)
        if kind == "reasoning":
            continue
        if kind == "function_call":
            call = {
                "id": _required_str(item, "call_id", at),
                "type": "function",
                "function": {"name": _required_str(item, "name", at), "arguments": _required_str(item, "arguments", at)},
            }
            if calls is None:
                calls = []
                last = out[-1] if out else None
                if last is not None and last["role"] == "assistant" and "tool_calls" not in last:
                    last["tool_calls"] = calls  # the answer's text, then its calls: one assistant message
                else:
                    out.append({"role": "assistant", "content": None, "tool_calls": calls})
            calls.append(call)
            continue
        calls = None
        if kind == "function_call_output":
            call_id = _required_str(item, "call_id", at)
            if "output" not in item:
                raise _missing(f"{at}.output")
            out.append({"role": "tool", "tool_call_id": call_id, "content": _output_text(item["output"], f"{at}.output")})
        elif kind == "message":
            role = item.get("role")
            if role is None:
                raise _missing(f"{at}.role")
            if role not in INPUT_ROLES:
                raise _bad(f"{at}.role", f"Invalid value for '{at}.role': {_short(role)} is not one of {list(INPUT_ROLES)}.")
            content = item.get("content")
            if content is None:
                raise _missing(f"{at}.content")
            if isinstance(content, str):
                out.append({"role": role, "content": content})
            elif isinstance(content, list):
                out.append({"role": role, "content": _input_parts(content, at)})
            else:
                raise _wrong_type(f"{at}.content", "a string or an array of content parts", content)
        elif kind == "item_reference":
            message = f"Unsupported value for '{at}.type': an item_reference needs stored items, and Ratchet keeps none."
            raise _bad(f"{at}.type", message, UNSUPPORTED_VALUE)
        else:
            message = f"Unsupported value for '{at}.type': {_short(kind)} items are not supported by this gateway."
            raise _bad(f"{at}.type", message, UNSUPPORTED_VALUE)
    if not out:
        raise _bad("input", "Invalid value for 'input': it holds no message.")
    return out


def _text_format(value: Any) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """(`response_format` for the chat request, `text` to echo) from `text`: `text.format` of `json_schema` (its name,
    schema, strict and description), `json_object` or `text` (no response_format, as a chat request without one)."""
    if value is None:
        return None, {"format": {"type": "text"}}
    if not isinstance(value, dict):
        raise _wrong_type("text", "an object", value)
    fmt = value.get("format")
    if fmt is None:
        return None, {**value, "format": {"type": "text"}}
    if not isinstance(fmt, dict):
        raise _wrong_type("text.format", "an object", fmt)
    kind = fmt.get("type")
    if kind is None:
        raise _missing("text.format.type")
    if not isinstance(kind, str):
        raise _wrong_type("text.format.type", "a string", kind)
    if kind not in RESPONSE_FORMATS:
        message = f"Invalid value for 'text.format.type': {_short(kind)} is not one of {list(RESPONSE_FORMATS)}."
        raise _bad("text.format.type", message)
    if kind == "text":
        return None, value
    if kind == "json_object":
        return {"type": "json_object"}, value
    spec = {"name": _name(fmt.get("name"), "text.format.name")}
    spec.update({k: fmt[k] for k in ("schema", "strict", "description") if k in fmt})
    return {"type": "json_schema", "json_schema": spec}, value


def _responses_tools(value: Any) -> list[dict[str, Any]] | None:
    """Function tools ({type: 'function', name, description, parameters, strict}) as chat tools; a built-in tool
    (web search, file search, code interpreter, MCP, ...) is a 400 `unsupported_parameter`."""
    if value is None:
        return None
    if not isinstance(value, list):
        raise _wrong_type("tools", "an array", value)
    out = []
    for i, tool in enumerate(value):
        at = f"tools[{i}]"
        if not isinstance(tool, dict):
            raise _wrong_type(at, "a tool object", tool)
        kind = tool.get("type")
        if kind is None:
            raise _missing(f"{at}.type")
        if kind != "function":
            message = f"Unsupported parameter: built-in tool {_short(kind)} in '{at}' is not supported; only function tools are."
            raise _bad(f"{at}.type", message, UNSUPPORTED_PARAMETER)
        fn = {"name": tool.get("name")}
        fn.update({k: tool[k] for k in ("description", "parameters", "strict") if k in tool})
        out.append({"type": "function", "function": fn})
    return out


def _responses_choice(value: Any) -> str | dict[str, Any] | None:
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, dict) and value.get("type") == "function":
        return {"type": "function", "function": {"name": value.get("name")}}
    if isinstance(value, dict):
        raise unsupported_parameter("tool_choice", "only 'none', 'auto', 'required' or a function tool can be chosen")
    raise _wrong_type("tool_choice", "a string or an object", value)


_TOOL_PARAM = re.compile(r"tools\[(\d+)\]\.function(.*)")


def responses_param(param: str | None) -> str | None:
    """A chat-request field's name (`validate_chat_request`'s `param`) as the Responses field it came from."""
    if param is None:
        return None
    if param == "max_tokens":
        return "max_output_tokens"
    if param == "reasoning_effort":
        return "reasoning.effort"
    if param.startswith("response_format.json_schema."):
        return "text.format." + param[len("response_format.json_schema.") :]
    if param.startswith("response_format"):
        return "text.format" + param[len("response_format") :]
    if param.startswith("tool_choice.function"):
        return "tool_choice" + param[len("tool_choice.function") :]
    if param.startswith("messages"):
        return "input"
    m = _TOOL_PARAM.fullmatch(param)
    return f"tools[{m.group(1)}]{m.group(2)}" if m else param


def as_responses_error(e: APIError) -> APIError:
    """`e`, raised for the chat request a Responses body became, naming the Responses field instead (`max_output_tokens`
    for `max_tokens`, `text.format.name` for `response_format.json_schema.name`, ...), in `param` and in the message."""
    new = responses_param(e.param)
    if new != e.param and e.param is not None and new is not None:
        e.message = e.message.replace(f"'{e.param}'", f"'{new}'")
        e.args = (e.message,)
        e.param = new
    return e


def validate_responses_request(body: Any, limits: Limits | None = None) -> ResponsesRequest:
    """Check a decoded `/v1/responses` body and turn it into a chat request; raise `APIError` (400) on the first problem,
    naming the Responses field. `instructions` becomes a leading system message, `input` the messages
    (`_input_messages`), `text.format` the `response_format`, function `tools` and `tool_choice` the chat ones,
    `max_output_tokens` `max_tokens`, `reasoning.effort` `reasoning_effort` (one of REASONING_EFFORTS, since the response
    echoes it); `model`, `temperature`, `top_p`, `metadata`, `user`, `safety_identifier` and `parallel_tool_calls` keep
    their names. What needs state Ratchet does not keep
    (`previous_response_id`, `conversation`, a stored `prompt`, `background: true`) and `stream: true` are a 400
    `unsupported_parameter`; RESPONSES_IGNORED fields are accepted and do nothing; any other field is an unknown field of
    the chat request (kept, and forwarded only when an allowlist names it). O(body), no I/O."""
    limits = limits or Limits()
    if not isinstance(body, dict):
        raise _bad(None, f"The request body must be a JSON object, but got {_kind(body)} instead.", INVALID_TYPE)
    for key, why in RESPONSES_REFUSED.items():
        if body.get(key) is not None:
            raise unsupported_parameter(key, why)
    for key, why in RESPONSES_FLAGS.items():
        if _opt_bool(body, key):
            raise unsupported_parameter(key, why)
    instructions = _opt_str(body, "instructions")
    messages = _input_messages(body.get("input"))
    if instructions:
        messages = [{"role": "system", "content": instructions}, *messages]
    response_format, text = _text_format(body.get("text"))
    tools = _responses_tools(body.get("tools"))
    reasoning = body.get("reasoning")
    if reasoning is not None and not isinstance(reasoning, dict):
        raise _wrong_type("reasoning", "an object", reasoning)
    effort = (reasoning or {}).get("effort")
    if isinstance(effort, str) and effort not in REASONING_EFFORTS:
        expected = ", ".join(REASONING_EFFORTS)
        message = f"Invalid value for 'reasoning.effort': expected one of {expected}, but got {_short(effort)} instead."
        raise _bad("reasoning.effort", message)
    mapped = {
        "model": body.get("model"),
        "max_tokens": body.get("max_output_tokens"),
        "temperature": body.get("temperature"),
        "top_p": body.get("top_p"),
        "response_format": response_format,
        "tools": tools,
        "tool_choice": _responses_choice(body.get("tool_choice")),
        "parallel_tool_calls": body.get("parallel_tool_calls"),
        "metadata": body.get("metadata"),
        "user": body.get("user"),
        "safety_identifier": body.get("safety_identifier"),
        "reasoning_effort": (reasoning or {}).get("effort"),
    }
    known = RESPONSES_READ | RESPONSES_IGNORED | RESPONSES_REFUSED.keys() | RESPONSES_FLAGS.keys()
    chat_body: dict[str, Any] = {k: v for k, v in body.items() if k not in known and k not in KNOWN_FIELDS}
    chat_body.update({k: v for k, v in mapped.items() if v is not None}, messages=messages)
    try:
        chat = validate_chat_request(chat_body, limits)
    except APIError as e:
        raise as_responses_error(e) from None
    return ResponsesRequest(
        chat=chat,
        instructions=instructions,
        text=text,
        tools=list(body.get("tools") or []),
        tool_choice=body.get("tool_choice") if body.get("tool_choice") is not None else "auto",
        parallel_tool_calls=chat.parallel_tool_calls if chat.parallel_tool_calls is not None else True,
        max_output_tokens=chat.max_tokens,
        temperature=chat.temperature,
        top_p=chat.top_p,
        reasoning_effort=chat.reasoning_effort,
    )


def responses_usage(
    prompt_tokens: int, completion_tokens: int, reasoning_tokens: int = 0, cached_tokens: int = 0
) -> dict[str, Any]:
    """A response's `usage` in the Responses API's shape: the same counts as a chat completion's (`usage_object`)."""
    return {
        "input_tokens": prompt_tokens,
        "input_tokens_details": {"cached_tokens": cached_tokens, "cache_write_tokens": 0},
        "output_tokens": completion_tokens,
        "output_tokens_details": {"reasoning_tokens": reasoning_tokens},
        "total_tokens": prompt_tokens + completion_tokens,
    }


# A choice's finish_reason -> (the response's status, incomplete_details.reason).
RESPONSE_STATUS: Mapping[str, tuple[str, str | None]] = MappingProxyType(
    {"length": ("incomplete", "max_output_tokens"), "content_filter": ("incomplete", "content_filter")}
)


def _item_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(24)}"


def response_output(text: str, finish_reason: str, tool_calls: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """A response's `output` items for one answer: a `message` item with one `output_text` part (left out when the answer
    is only tool calls), then a `function_call` item per tool call ({id, type: 'function', function: {name,
    arguments}} in the chat shape), its `call_id` the call's id, so a client sends the result back as a
    `function_call_output` with that `call_id`."""
    status = RESPONSE_STATUS.get(finish_reason, ("completed", None))[0]
    items: list[dict[str, Any]] = []
    if text or not tool_calls:
        part = {"type": "output_text", "text": text, "annotations": []}
        items.append({"type": "message", "id": _item_id("msg"), "status": status, "role": "assistant", "content": [part]})
    for call in tool_calls or ():
        fn = call.get("function") if isinstance(call.get("function"), Mapping) else {}
        items.append(
            {
                "type": "function_call",
                "id": _item_id("fc"),
                "call_id": str(call.get("id") or _item_id("call")),
                "name": str(fn.get("name") or ""),
                "arguments": fn.get("arguments") if isinstance(fn.get("arguments"), str) else json.dumps(fn.get("arguments")),
                "status": "completed",
            }
        )
    return items


def response_object(
    req: ResponsesRequest,
    *,
    created: int,
    model: str,
    output: list[dict[str, Any]],
    finish_reason: str,
    usage: dict[str, Any],
) -> dict[str, Any]:
    """A `response` object in the Responses API's shape for a served answer: `status` `completed`, or `incomplete` with
    the reason when the answer was cut off (`max_output_tokens`) or filtered; the request's settings echoed as sent;
    `store` false and `previous_response_id` null, since nothing is kept to continue from."""
    status, reason = RESPONSE_STATUS.get(finish_reason, ("completed", None))
    return {
        "id": _item_id("resp"),
        "object": "response",
        "created_at": created,
        "status": status,
        "background": False,
        "error": None,
        "incomplete_details": {"reason": reason} if reason else None,
        "instructions": req.instructions,
        "max_output_tokens": req.max_output_tokens,
        "model": model,
        "output": output,
        "parallel_tool_calls": req.parallel_tool_calls,
        "previous_response_id": None,
        "reasoning": {"effort": req.reasoning_effort, "summary": None},
        "service_tier": SERVICE_TIER,
        "store": False,
        "temperature": req.temperature,
        "text": req.text,
        "tool_choice": req.tool_choice,
        "tools": req.tools,
        "top_p": req.top_p,
        "truncation": "disabled",
        "usage": usage,
        "user": req.chat.user,
        "metadata": req.chat.metadata or {},
    }
