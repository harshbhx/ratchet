"""OpenAI chat-completions compatibility: request validation, message normalisation and the error envelope.

Standard library only (plus `tasks` for the task-name rule), so it is cheap to import and easy to test without the web
stack. The gateway runs `parse_body`, `validate_chat_request` and `normalize_messages` before anything is logged,
billed or sent to a model, and turns `APIError` into the body OpenAI SDKs read: `error.message`, `error.type`,
`error.param` and `error.code` (docs/API.md#errors, ADR 0012).

Validation is strict about what Ratchet reads or will forward, and lenient about the rest: unknown top-level keys are
kept in `ChatRequest.extra` and ignored, so a newer SDK that sends a field Ratchet does not know still works.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, fields
from typing import Any

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


# status -> (error.type, error.code) for errors raised as plain HTTP errors: the API key (401), the daily quota (429),
# a teacher failure (502), and what the web framework raises itself (404 for an unknown path, 405, 413).
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


def parse_body(raw: bytes | str) -> Any:
    """Strict JSON (no NaN or Infinity). Bad JSON, bad UTF-8 and nesting too deep to parse are all a 400."""
    try:
        return json.loads(raw, parse_constant=_no_constant)
    except RecursionError as e:
        raise APIError(400, "The request body is not valid JSON: it is nested too deeply.", code=INVALID_JSON) from e
    except ValueError as e:  # JSONDecodeError, UnicodeDecodeError, NaN, integers too long to convert
        raise APIError(400, f"The request body is not valid JSON: {e}.", code=INVALID_JSON) from e


# ---------- the validated request ----------
@dataclass(frozen=True)
class Limits:
    """Request limits from Settings (RATCHET_MAX_MESSAGES, RATCHET_MAX_INPUT_CHARS). 0 means no limit."""

    max_messages: int = 256
    max_input_chars: int = 120_000
    max_n: int = 1  # one choice per request until n > 1 is built (API-N-LOGPROBS)


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


KNOWN_FIELDS = frozenset({f.name for f in fields(ChatRequest)} - {"extra"} | {"max_completion_tokens"})


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


def _opt_object(body: Mapping[str, Any], key: str) -> dict[str, Any] | None:
    v = body.get(key)
    if v is not None and not isinstance(v, dict):
        raise _wrong_type(key, "an object", v)
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
def _message(m: Any, at: str) -> int:
    """Check one message; return the characters of text it carries."""
    if not isinstance(m, dict):
        raise _wrong_type(at, "a message object", m)
    role = m.get("role")
    if role is None:
        raise _missing(f"{at}.role")
    if not isinstance(role, str):
        raise _wrong_type(f"{at}.role", "a string", role)
    if role not in ROLES:
        raise _bad(f"{at}.role", f"Invalid value for '{at}.role': {_short(role)} is not one of {list(ROLES)}.")
    name = m.get("name")
    if name is not None and not isinstance(name, str):
        raise _wrong_type(f"{at}.name", "a string", name)
    calls = _assistant_calls(m, at) if role == "assistant" else False
    if role == "tool":
        call_id = m.get("tool_call_id")
        if call_id is None:
            raise _missing(f"{at}.tool_call_id")
        if not isinstance(call_id, str):
            raise _wrong_type(f"{at}.tool_call_id", "a string", call_id)

    content = m.get("content")
    if content is None:
        if calls:
            return 0
        if "content" not in m:
            raise _missing(f"{at}.content")
        raise _bad(
            f"{at}.content",
            f"Invalid type for '{at}.content': null is allowed only in an assistant message with tool_calls or function_call.",
            INVALID_TYPE,
        )
    if isinstance(content, str):
        return len(content)
    if not isinstance(content, list):
        raise _wrong_type(f"{at}.content", "a string or an array of content parts", content)
    if not content:
        raise _bad(f"{at}.content", f"Invalid value for '{at}.content': an array of content parts must not be empty.")
    chars = 0
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


def _assistant_calls(m: Mapping[str, Any], at: str) -> bool:
    """Whether an assistant message carries tool calls (then its content may be null)."""
    tool_calls = m.get("tool_calls")
    if tool_calls is not None:
        if not isinstance(tool_calls, list):
            raise _wrong_type(f"{at}.tool_calls", "an array of tool calls", tool_calls)
        for k, call in enumerate(tool_calls):
            if not isinstance(call, dict):
                raise _wrong_type(f"{at}.tool_calls[{k}]", "a tool call object", call)
    function_call = m.get("function_call")
    if function_call is not None and not isinstance(function_call, dict):
        raise _wrong_type(f"{at}.function_call", "an object", function_call)
    return bool(tool_calls) or function_call is not None


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
    return value


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
        message = f"Unsupported value for 'n': this gateway returns at most {limits.max_n} choice(s) per request."
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
        stream_options=_opt_object(body, "stream_options"),
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
        user=_opt_str(body, "user"),
        safety_identifier=_opt_str(body, "safety_identifier"),
        store=_opt_bool(body, "store"),
        metadata=_metadata(body.get("metadata")),
        reasoning_effort=_opt_str(body, "reasoning_effort"),
        extra={k: v for k, v in body.items() if k not in KNOWN_FIELDS},
    )


# ---------- normalisation ----------
@dataclass
class Flags:
    """What normalisation found. `multimodal`: a non-text part the teacher reads (RATCHET_TEACHER_MODALITIES), so
    the request must not go to the student or be stored."""

    multimodal: bool = False


def parse_modalities(spec: str) -> frozenset[str]:
    """RATCHET_TEACHER_MODALITIES ('text,image_url'): the content part types the teacher accepts. Text always."""
    return frozenset({TEXT_MODALITY, *(s.strip() for s in spec.split(",") if s.strip())})


def _normalize(m: dict[str, Any], i: int, modalities: frozenset[str], flags: Flags) -> dict[str, Any]:
    role = m.get("role")
    out = m
    if role == "developer":  # OpenAI's newer name for the system role; open models know only 'system'
        out = {**out, "role": "system"}
    if role == "assistant" and "audio" in out:  # a reference to audio the client got back; nothing to send on
        out = {k: v for k, v in out.items() if k != "audio"}
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
