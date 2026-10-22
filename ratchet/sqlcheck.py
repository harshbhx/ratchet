"""Text-to-SQL answers compared by what they return (FR-2.56, ADR 0055).

A task with format `sql` answers with one SQL query. Two answers agree when their queries return the same result on the
task's fixture: a small database the operator writes as a SQL script under RATCHET_FIXTURES_DIR (`SqlSpec.load`). So
`COUNT(*)` and `COUNT(id)`, or a JOIN and an IN subquery, agree although their text differs, and a query with the wrong
filter disagrees although its text is close. A result is its column count and its rows: column names are ignored,
floats are rounded to 9 significant digits and a whole float equals the integer. With `ordered: auto` the rows keep their
order when the outermost query has ORDER BY and compare as a multiset otherwise, so an ordered result never equals an
unordered one of two rows or more. Two empty results say little, so with `empty_result: text` (the default) the queries'
normalised texts are compared instead (`normalize`). Dialect `postgres` runs nothing and compares the normalised texts.

An answer that cannot count is invalid (`compare.INVALID`, with a reason that never quotes it, so it may go into an
event): not exactly one query that starts with SELECT, WITH or VALUES; a query that reads the clock or the time zone
(the fixture is fixed, so the answer should be too, and its verdict reproducible); an SQLite error; an operation the
sandbox denies; or a query that hits a limit. Clock reads are caught twice: in the text (`_clock_read`: CURRENT_DATE, a
date function without a time value such as `julianday()` or `strftime('%s')`, or 'now', 'localtime' or 'utc' as a
string or a double-quoted name anywhere in a date function's arguments), and when a date function runs (`reads_clock`:
a value built at run time, a view), so a time-dependent result is never returned or cached. An invalid student sample
gets no vote, and an invalid teacher answer is served but never stored (INV-16).

The gateway never connects to a customer database: the fixture is the only data it runs SQL on, in a private in-memory
SQLite database (`Sandbox`), standard library only:

- The fixture is at most 5 MB of UTF-8 text, loaded with `executescript` into at most 64 MB; ATTACH, DETACH, VACUUM INTO,
  `load_extension`, functions that would load different data each time (also through a column's DEFAULT: the date
  functions, CURRENT_* and random() refuse to run while it loads) and all but a few PRAGMAs are refused while it loads.
  It must create a table or a view, and no view may read the clock.
- Then `PRAGMA query_only = ON`, and an authorizer that allows reading the fixture's own tables and views, the schema
  and `json_each`/`json_tree` (not `sqlite_stmt`, which lists other requests' queries, nor `dbstat`), recursive CTEs
  and the deterministic functions in `FUNCTIONS`, and denies the rest: writes, schema changes, ATTACH, PRAGMA (the
  pragma table-valued functions too), transactions, `random()`, `changes()`, `last_insert_rowid()`, `load_extension()`.
- A progress handler, every `PROGRESS_EVERY` VM steps, stops a query after `max_steps` steps (a runaway recursive
  CTE), after `QUERY_SECONDS` (a step can do a lot of work), or once the process's resident memory has grown by
  `QUERY_MEMORY_BYTES` during the query (read from /proc on Linux): a sorter filled with large rows needs few steps.
- SQLite calls the progress handler only where a loop jumps back, never inside one step or within one row's
  expressions, so what they can do is bounded too. `Connection.setlimit` caps a value, and so a sorter's row, at
  `VALUE_BYTES` (a fixture's values too), a LIKE or GLOB pattern at `LIKE_PATTERN_BYTES` (their work is value x
  pattern), the statement's length, and attached databases at 0. The built-ins that are quadratic in two arguments are
  replaced by linear versions that give the same results (`_GUARDED`: instr, replace, two-argument trim, ltrim and
  rtrim) or not allowed (json_patch, unhex). LIKE, GLOB (about 25 ms a call at most) and the date functions run
  SQLite's own code through Python functions that check the query's time on every call (`_Watch`): one row whose WHERE
  called LIKE 200 times took 3.4 s before. The rest of the built-ins cost at most about 0.5 ms a call on a 100 KB value,
  so a hostile query stops within about `QUERY_SECONDS` + 0.3 s.
- `setlimit` needs Python 3.11: without it one step can copy a value of up to SQLite's own 1 GB cap (a query that
  doubles a string took 3 GB and 18 s on 3.10, measured), so on Python 3.10 a sqlite fixture is refused (`CAN_LIMIT`)
  and only dialect `postgres` works.
- Rows are fetched in chunks and hashed as they come: more than `max_rows` rows, or more than `RESULT_BYTES` of values,
  is invalid.

One lock per sandbox serialises its queries: Scout calls the router from several threads, and tasks with the same
fixture share one sandbox (`_SANDBOXES`). The outcomes that do not depend on timing are cached by query text.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import threading
import time
import weakref
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NoReturn

from .compare import INVALID

DIALECTS = ("sqlite", "postgres")  # postgres: compare the normalised text only, never run it
ORDERED = ("auto", "always", "never")  # auto: in order when the outermost query has ORDER BY, else as a multiset
EMPTY_RESULTS = ("text", "agree")  # two empty results: compare the normalised texts, or agree
KEYS = ("fixture", "dialect", "ordered", "empty_result", "max_rows", "max_steps")  # a spec's `sql` object
DEFAULT_MAX_ROWS = 1000
DEFAULT_MAX_STEPS = 200_000
MAX_ROWS = 10_000  # the largest `max_rows` a spec may set
MIN_STEPS, MAX_STEPS = 1_000, 10_000_000  # the range of `max_steps`

MAX_FIXTURE_BYTES = 5 * 1024 * 1024  # a fixture script's size on disk
MAX_FIXTURE_DB_BYTES = 64 * 1024 * 1024  # the fixture's database once loaded (PRAGMA max_page_count)
LOAD_SECONDS = 30.0  # a fixture that takes longer to load is refused, so startup cannot hang on one
MAX_QUERY_CHARS = 20_000  # an answer longer than this is not one query
QUERY_SECONDS = 1.0  # wall-clock time one query may take
QUERY_MEMORY_BYTES = 128 * 1024 * 1024  # growth of the process's resident memory one query may cause (Linux)
VALUE_BYTES = 100_000  # SQLITE_LIMIT_LENGTH: one value, one sorter row (and so the work one function call can do)
LIKE_PATTERN_BYTES = 100  # SQLITE_LIMIT_LIKE_PATTERN_LENGTH: LIKE and GLOB do up to value x pattern work in one step
NATIVE_WORK = 10_000_000  # the most (length x length) a guarded built-in may get on the helper connection (`_guarded`)
RESULT_BYTES = 8 * 1024 * 1024  # the values of one result, as hashed
PROGRESS_EVERY = 100  # VM steps between two calls of the progress handler (only where a loop jumps back)
FETCH_ROWS = 100  # rows fetched at a time
CACHE_SIZE = 256  # cached outcomes per sandbox
CACHE_CHARS = 4096  # a longer query is never cached

# Why an answer is invalid. Fixed strings, never the answer's text or SQLite's message (it can quote the query).
NOT_ONE_QUERY = "not one query"
READS_CLOCK = "reads the clock"
CALLS_RANDOM = "calls random"  # a fixture whose load calls random() or randomblob() (a column's DEFAULT)
SQL_ERROR = "sql error"
SQL_DENIED = "sql denied"
TOO_MANY_STEPS = "sql steps exceeded"
TOO_SLOW = "sql time exceeded"
TOO_MUCH_MEMORY = "sql memory exceeded"
TOO_MANY_ROWS = "sql too many rows"
TOO_LARGE = "sql result too large"
_TIMING = frozenset({TOO_SLOW, TOO_MUCH_MEMORY})  # outcomes that depend on the moment: never cached

# Deterministic functions a query may call: SQLite's core scalar, aggregate, window, math, JSON and date functions. Not
# random, randomblob, changes, total_changes, last_insert_rowid, load_extension, zeroblob, sqlite_* or current_*; nor
# json_patch and unhex, whose C code is quadratic in two arguments inside one VM step (see `_GUARDED`).
FUNCTIONS = frozenset(
    """
    abs avg char coalesce concat concat_ws count cume_dist date datetime dense_rank first_value format glob group_concat
    hex ifnull iif instr json json_array json_array_length json_error_position json_extract json_group_array
    json_group_object json_insert json_object json_quote json_remove json_replace json_set json_type json_valid
    julianday lag last_value lead length like likelihood likely lower ltrim max min nth_value ntile nullif octet_length
    percent_rank printf quote rank replace round row_number rtrim sign strftime string_agg substr substring sum time
    timediff total trim typeof unicode unixepoch unlikely upper acos acosh asin asinh atan atan2 atanh ceil ceiling
    cos cosh degrees exp floor ln log log10 log2 mod pi pow power radians sin sinh sqrt tan tanh trunc -> ->>
    """.split()
)
_TIME_FUNCTIONS = frozenset({"date", "time", "datetime", "julianday", "unixepoch", "strftime", "timediff"})
_CLOCK_ARGS = frozenset({"now", "localtime", "utc"})  # date-function arguments that read the clock or the time zone
_CLOCK_WORDS = frozenset({"current_date", "current_time", "current_timestamp"})
_CLOCK_VALUES = frozenset({"now", "subsec", "subsecond"})  # time values that mean the current time (`reads_clock`)
_ZONE_MODIFIERS = frozenset({"localtime", "utc"})  # modifiers that read the time zone
# What a query may read besides the fixture's own tables and views: the schema and the JSON table-valued functions. Not
# the other eponymous virtual tables: sqlite_stmt lists the statements the connection has cached (other requests'
# queries, which can quote their prompts), and dbstat and sqlite_dbpage expose storage.
ALWAYS_READABLE = frozenset(
    {"sqlite_master", "sqlite_schema", "sqlite_temp_master", "sqlite_temp_schema", "json_each", "json_tree"}
)
_QUERY_STARTS = frozenset({"select", "with", "values"})
# PRAGMAs a fixture script may set while it loads (a `.dump` starts with `PRAGMA foreign_keys=OFF`); any other is refused.
LOAD_PRAGMAS = frozenset({"foreign_keys", "defer_foreign_keys", "recursive_triggers", "user_version", "application_id"})
# Functions a fixture may not call while it loads: they would give a different database on every load.
_LOAD_DENIED = frozenset({"load_extension", "random", "randomblob", "current_date", "current_time", "current_timestamp"})

_RECURSIVE = getattr(sqlite3, "SQLITE_RECURSIVE", 33)
# Python 3.11+: `Connection.setlimit` caps a value's size. Without it (3.10) nothing bounds what one step allocates (a
# query that doubles a string reached 3 GB and 18 s there, measured), so a sqlite-dialect fixture is refused.
CAN_LIMIT = hasattr(sqlite3.Connection, "setlimit")
_READ_ACTIONS = frozenset({sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, _RECURSIVE})


# ---------------------------------------------------------------------------------------------------------------------
# Reading a query: one linear pass of one regex (every alternative is unrolled, so nothing backtracks over long input).

_TOKEN = re.compile(
    r"""
    (?P<ws>\s+)
    |(?P<comment>--[^\n]*|/\*[^*]*\*+(?:[^/*][^*]*\*+)*/)
    |(?P<string>'[^']*(?:''[^']*)*')
    |(?P<ident>"[^"]*(?:""[^"]*)*"|`[^`]*(?:``[^`]*)*`|\[[^\]]*\])
    |(?P<number>0[xX][0-9A-Fa-f]+|(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)
    |(?P<word>[^\W\d][\w$]*)
    |(?P<op>\|\||<<|>>|<=|>=|==|!=|<>|->>|->|::)
    |(?P<open>/\*|['"`\[])
    |(?P<other>.)
    """,
    re.VERBOSE | re.DOTALL,
)


@dataclass(frozen=True)
class Query:
    """One query read from an answer: `sql`, the statement as written without the `;` and comments around it (what
    runs); `text`, its normalised form (`normalize`); `ordered`, whether the outermost query has ORDER BY."""

    sql: str
    text: str
    ordered: bool


def _tokens(text: str) -> list[tuple[str, str, int, int]] | None:
    """The significant tokens of `text` as (kind, value, start, end), without whitespace and comments; None when a
    string, a quoted identifier or a block comment is not closed."""
    out = []
    for m in _TOKEN.finditer(text):
        kind = m.lastgroup
        assert kind is not None
        if kind in ("ws", "comment"):
            continue
        if kind == "open":
            return None
        out.append((kind, m.group(), m.start(), m.end()))
    return out


def _normal(tokens: list[tuple[str, str, int, int]]) -> str:
    """Tokens joined by one space, words lower-cased (SQL keywords and unquoted names are case-insensitive), strings
    and quoted names as written."""
    return " ".join(v.lower() if kind == "word" else v for kind, v, _, _ in tokens)


def _unquote(value: str) -> str:
    """A string or quoted name without its quotes ('it''s' is it's, [x] is x)."""
    inner = value[1:-1]
    return inner if value[0] == "[" else inner.replace(value[0] * 2, value[0])


def _clock_read(tokens: list[tuple[str, str, int, int]]) -> bool:
    """Whether SQL text reads the clock or the time zone: CURRENT_DATE, CURRENT_TIME or CURRENT_TIMESTAMP; a date
    function called without a time value (`date()`, `julianday()`, `strftime('%s')`: SQLite reads the clock then); or
    'now', 'localtime' or 'utc' anywhere inside a date function's arguments, as a string or as a double-quoted name
    (SQLite reads a double-quoted name that matches no column as a string). Nested calls and subqueries count."""
    calls: list[list[Any]] = []  # per open parenthesis: [the date function it calls, or '', commas so far, still empty]
    in_time = 0  # how many date functions' parentheses are open
    previous: tuple[str, str] | None = None
    for kind, value, _, _ in tokens:
        closing = kind == "other" and value == ")"
        if calls and not closing:
            calls[-1][2] = False
        if kind == "other" and value == "(":
            name = ""
            if previous is not None and previous[0] in ("word", "ident"):
                called = (previous[1] if previous[0] == "word" else _unquote(previous[1])).lower()
                name = called if called in _TIME_FUNCTIONS else ""
            calls.append([name, 0, True])
            in_time += bool(name)
        elif closing:
            if calls:
                name, commas, empty = calls.pop()
                if name:
                    in_time -= 1
                    if (empty and name not in ("strftime", "timediff")) or (name == "strftime" and not empty and not commas):
                        return True  # no time value (strftime's first argument is its format)
        elif kind == "other" and value == "," and calls:
            calls[-1][1] += 1
        elif kind == "word" and value.lower() in _CLOCK_WORDS:
            return True
        elif in_time and (kind == "string" or (kind == "ident" and value[0] == '"')):
            if _unquote(value).strip().lower() in _CLOCK_ARGS:
                return True
        previous = (kind, value)
    return False


def parse_query(text: str, check_clock: bool = True) -> Query | str:
    """The one query in a cleaned answer, or why there is not one (`NOT_ONE_QUERY`, or with `check_clock`
    `READS_CLOCK`, `_clock_read`). A `;` may end the query, and comments may sit anywhere; any other statement before or
    after it makes the answer invalid, as does a query that does not start with SELECT, WITH or VALUES (EXPLAIN, PRAGMA,
    writes)."""
    if len(text) > MAX_QUERY_CHARS:
        return NOT_ONE_QUERY
    tokens = _tokens(text)
    if tokens is None:
        return NOT_ONE_QUERY
    statements: list[list[tuple[str, str, int, int]]] = [[]]
    for tok in tokens:
        if tok[0] == "other" and tok[1] == ";":
            statements.append([])
        else:
            statements[-1].append(tok)
    found = [s for s in statements if s]
    if len(found) != 1:
        return NOT_ONE_QUERY
    stmt = found[0]
    if stmt[0][0] != "word" or stmt[0][1].lower() not in _QUERY_STARTS:
        return NOT_ONE_QUERY
    if check_clock and _clock_read(stmt):
        return READS_CLOCK
    depth, ordered = 0, False
    previous: tuple[str, str, int, int] | None = None
    for tok in stmt:
        kind, value = tok[0], tok[1]
        if kind == "other" and value == "(":
            depth += 1
        elif kind == "other" and value == ")":
            depth -= 1
        elif kind == "word" and depth == 0 and value.lower() == "by":
            if previous is not None and previous[0] == "word" and previous[1].lower() == "order":
                ordered = True
        previous = tok
    return Query(text[stmt[0][2] : stmt[-1][3]], _normal(stmt), ordered)


def normalize(text: str) -> str | None:
    """A query as `empty_result: text` and dialect `postgres` compare it: comments and the trailing `;` dropped, words
    lower-cased, one space between tokens (`SELECT  COUNT(*)\\nFROM t;` is `select count ( * ) from t`). None when the
    text is not one query."""
    q = parse_query(text, check_clock=False)
    return q.text if isinstance(q, Query) else None


# ---------------------------------------------------------------------------------------------------------------------
# Results


def _value(v: Any) -> Any:
    """A value as results compare it: a float rounded to 9 significant digits, and an integer when it is a whole number
    within 2**53 (so SUM over integers and TOTAL agree); integers, text, blobs and NULL as they are."""
    if isinstance(v, float):
        r = float(f"{v:.9g}")
        return int(r) if r.is_integer() and abs(r) <= 2**53 else r
    return v


@dataclass(frozen=True)
class Result:
    """What a query returned, reduced to what answers compare: the column and row counts, and sha256 digests of the
    rows in order and of the rows as a multiset (each with the column count)."""

    columns: int
    rows: int
    in_order: str
    as_multiset: str


@dataclass(frozen=True)
class SqlKey:
    """The key of a valid answer (`SqlSpec.key`): its result (`columns`, `rows`, whether the rows compare in order, and
    the matching digest of `Result`; zeros and '' for dialect postgres) and its normalised text."""

    columns: int
    rows: int
    ordered: bool
    result: str
    text: str


def _digest(columns: int, ordered: bool, rows: list[bytes]) -> str:
    h = hashlib.sha256(f"{columns}:{'o' if ordered else 'm'}:".encode())
    for row in rows:
        h.update(row)
    return h.hexdigest()


# ---------------------------------------------------------------------------------------------------------------------
# Resident memory (Linux): one /proc read per check, from a descriptor kept open per process.

_STATM = "/proc/self/statm"
_PAGE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
_rss: tuple[int, int] | None = None  # (pid, descriptor), -1 when /proc cannot be read
_rss_lock = threading.Lock()


def resident_bytes() -> int | None:
    """This process's resident memory, or None where it cannot be read (no /proc). The descriptor is opened again after
    a fork, because /proc/self names the process that opened it."""
    global _rss
    held, pid = _rss, os.getpid()
    if held is None or held[0] != pid:
        with _rss_lock:
            held = _rss
            if held is None or held[0] != pid:
                try:
                    fd = os.open(_STATM, os.O_RDONLY)
                except OSError:
                    fd = -1
                held = _rss = (pid, fd)
    if held[1] < 0:
        return None
    try:
        return int(os.pread(held[1], 128, 0).split()[1]) * _PAGE
    except (OSError, ValueError, IndexError, AttributeError):
        return None


class _Budget:
    """The progress handler of one query: stops it (returns 1) after `max_steps` VM steps, after QUERY_SECONDS, or once
    resident memory has grown by QUERY_MEMORY_BYTES, and says which (`reason`). Never raises."""

    __slots__ = ("calls", "limit", "deadline", "rss0", "reason")

    def __init__(self, max_steps: int) -> None:
        self.calls = 0
        self.limit = -(-max_steps // PROGRESS_EVERY)
        self.deadline = time.monotonic() + QUERY_SECONDS
        self.rss0 = resident_bytes()
        self.reason: str | None = None

    def __call__(self) -> int:
        self.calls += 1
        if self.calls >= self.limit:
            self.reason = TOO_MANY_STEPS
        elif time.monotonic() > self.deadline:
            self.reason = TOO_SLOW
        elif self.rss0 is not None:
            now = resident_bytes()
            if now is not None and now - self.rss0 > QUERY_MEMORY_BYTES:
                self.reason = TOO_MUCH_MEMORY
        return 1 if self.reason is not None else 0


class _Watch:
    """What the sandbox's own SQL functions see of the statement running: the query's budget, checked on every call
    because SQLite calls the progress handler only where a loop jumps back (one row whose WHERE called LIKE 200 times
    took 3.4 s past it), and why a function refused to run (`refused`: READS_CLOCK or CALLS_RANDOM)."""

    __slots__ = ("budget", "refused")

    def __init__(self) -> None:
        self.budget: _Budget | None = None
        self.refused: str | None = None

    def check(self) -> None:
        """Stop the query (raise) once its time is up or its progress handler has stopped it."""
        budget = self.budget
        if budget is None:
            return
        if budget.reason is None and time.monotonic() > budget.deadline:
            budget.reason = TOO_SLOW
        if budget.reason is not None:
            raise OverflowError("query stopped")

    def refuse(self, why: str) -> NoReturn:
        self.refused = why
        raise OverflowError(why)


def _clock_word(value: Any) -> str | None:
    """A date-function argument as SQLite compares it with 'now' and the modifiers: text (a blob read as text) up to its
    first NUL, here also stripped and lower-cased; None for numbers and NULL."""
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    return value.split("\x00", 1)[0].strip().lower() if isinstance(value, str) else None


def reads_clock(name: str, args: tuple[Any, ...]) -> bool:
    """Whether SQLite's date function `name` reads the clock or the time zone given these argument values: no time value
    (`date()`, `strftime('%s')`), a time value 'now' (or 'subsec', which means now too), or a 'localtime' or 'utc'
    modifier (`datetime(placed, 'subsec')` is fine: there 'subsec' is a modifier)."""
    if name == "strftime":
        if not args or args[0] is None:  # strftime() and strftime(NULL, ...) return NULL before reading a time value
            return False
        args = args[1:]
    if name == "timediff":  # two time values, no modifiers
        values, modifiers = args[:2], args[2:2]
    elif not args:
        return True
    else:
        values, modifiers = args[:1], args[1:]
    return any(_clock_word(v) in _CLOCK_VALUES for v in values) or any(_clock_word(m) in _ZONE_MODIFIERS for m in modifiers)


def _statement(name: str, nargs: int) -> str:
    return f"SELECT {name}({', '.join('?' * nargs)})"


def _time_function(name: str, watch: _Watch, helper: sqlite3.Connection) -> Callable[..., Any]:
    """SQLite's own date function `name` (run on `helper`), refused (`READS_CLOCK`) when it would read the clock or the
    time zone, so a value built at run time or a view cannot reach the clock either (the text check sees only literals)."""

    def call(*args: Any) -> Any:
        watch.check()
        if reads_clock(name, args):
            watch.refuse(READS_CLOCK)
        return helper.execute(_statement(name, len(args)), args).fetchone()[0]

    return call


def _metered(name: str, nargs: int, watch: _Watch, helper: sqlite3.Connection) -> Callable[..., Any]:
    """SQLite's own `name` (run on `helper`, which caps the pattern as the query connection does), after a check of the
    query's time: LIKE and GLOB cost up to about 25 ms a call, and one row can call them hundreds of times."""
    stmt = _statement(name, nargs)

    def call(*args: Any) -> Any:
        watch.check()
        return helper.execute(stmt, args).fetchone()[0]

    return call


def _refuser(watch: _Watch, why: str) -> Callable[..., Any]:
    """A function that never runs: CURRENT_* and random() while a fixture loads (a column's DEFAULT reaches them without
    the authorizer), so the database it builds never depends on when or how often it was loaded."""

    def call(*_: Any) -> Any:
        watch.refuse(why)

    return call


def _query_authorizer(tables: frozenset[str]) -> Callable[[int, str | None, str | None, str | None, str | None], int]:
    """An authorizer that allows reading `tables` (lower-cased names: the fixture's own tables and views, and
    ALWAYS_READABLE), SELECT, recursive CTEs and the functions in FUNCTIONS, and denies everything else."""

    def authorize(action: int, arg1: str | None, arg2: str | None, db: str | None, source: str | None) -> int:
        if action == sqlite3.SQLITE_READ:
            return sqlite3.SQLITE_OK if isinstance(arg1, str) and arg1.lower() in tables else sqlite3.SQLITE_DENY
        if action in _READ_ACTIONS:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION and isinstance(arg2, str) and arg2.lower() in FUNCTIONS:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    return authorize


def _allow_all(*_: Any) -> int:
    """Between loading and querying, to set `query_only` (the load authorizer refuses that PRAGMA). A callback rather
    than `set_authorizer(None)`, which Python 3.10 reads as an authorizer that denies everything."""
    return sqlite3.SQLITE_OK


# Built-ins whose C code does more than linear work inside one VM step, where no progress handler runs: measured on
# SQLite 3.45, trim(X, Y) took 15 s with 60,000 characters in each, and instr and replace about 8 s with 1 MB values.
# The query connection replaces them, by name and number of arguments, with versions in Python that are linear on text
# and blobs (str.find, str.replace, a set of characters) and give SQLite's results there; any other mix of types goes
# to SQLite's own function on a helper connection, under `NATIVE_WORK` (a number's text is short, so that stays cheap).
# LIKE and GLOB stay SQLite's, with the pattern capped at `LIKE_PATTERN_BYTES`.
_NATIVE = object()  # what a fast version returns for argument types it leaves to SQLite's own function


def _same_kind(*values: Any) -> bool:
    """All text, or all blobs: the types the Python versions handle exactly as SQLite does."""
    return all(type(v) is str for v in values) or all(type(v) is bytes for v in values)


def _instr(x: Any, y: Any) -> Any:
    if x is None or y is None:
        return None
    return x.find(y) + 1 if _same_kind(x, y) else _NATIVE


def _replace(x: Any, y: Any, z: Any) -> Any:
    if x is None or y is None:
        return None
    if not (type(x) is str and type(y) is str and (z is None or type(z) is str)):
        return _NATIVE
    if not y or y[0] == "\x00":  # SQLite returns X itself for an empty pattern (a C string: NUL first), before Z
        return x
    if z is None:
        return None
    if len(x) + x.count(y) * (len(z) - len(y)) > VALUE_BYTES:
        raise OverflowError("result too large")  # SQLite's own would fail too, after building it
    return x.replace(y, z)


def _trimmer(left: bool, right: bool) -> Any:
    def trim(x: Any, y: Any) -> Any:
        if x is None or y is None:
            return None
        if not (type(x) is str and type(y) is str):
            return _NATIVE
        y = y.split("\x00", 1)[0]  # SQLite reads the set as a C string: up to its first NUL
        if len(y) <= 32:  # str.strip scans the characters for each one it removes: fine for a short set
            return x.strip(y) if left and right else x.lstrip(y) if left else x.rstrip(y)
        chars, start, end = set(y), 0, len(x)
        while left and start < end and x[start] in chars:
            start += 1
        while right and end > start and x[end - 1] in chars:
            end -= 1
        return x[start:end]

    return trim


_GUARDED: dict[tuple[str, int], Any] = {
    ("instr", 2): _instr,
    ("replace", 3): _replace,
    ("trim", 2): _trimmer(True, True),
    ("ltrim", 2): _trimmer(True, False),
    ("rtrim", 2): _trimmer(False, True),
}


def _guarded(fast: Any, name: str, nargs: int, helper: sqlite3.Connection, watch: _Watch) -> Any:
    """`fast`, falling back to SQLite's own `name` on `helper` for the argument types it leaves (`_NATIVE`), refused
    when the product of the two first arguments' lengths is over NATIVE_WORK. Checks the query's time first (`_Watch`)."""
    stmt = _statement(name, nargs)

    def call(*args: Any) -> Any:
        watch.check()
        out = fast(*args)
        if out is not _NATIVE:
            return out
        work = 1
        for v in args[:2]:
            work *= len(v) if isinstance(v, (str, bytes)) else 32
        if work > NATIVE_WORK:
            raise OverflowError("too much work in one call")
        return helper.execute(stmt, args).fetchone()[0]

    return call


def _load_authorizer(action: int, arg1: str | None, arg2: str | None, db: str | None, source: str | None) -> int:
    """What a fixture script may do: build and fill tables, but not ATTACH or DETACH (VACUUM INTO attaches a file too),
    set PRAGMAs outside LOAD_PRAGMAS, or call a function in _LOAD_DENIED."""
    if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH):
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_PRAGMA:
        return sqlite3.SQLITE_OK if isinstance(arg1, str) and arg1.lower() in LOAD_PRAGMAS else sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and isinstance(arg2, str) and arg2.lower() in _LOAD_DENIED:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


# What running SQL can raise. sqlite3.Warning (some versions: "You can only execute one statement at a time") is not an
# sqlite3.Error; ValueError covers a NUL character or a lone surrogate in the text.
_SQL_ERRORS: tuple[type[BaseException], ...] = (sqlite3.Error, sqlite3.Warning, ValueError, OverflowError, MemoryError)


class Sandbox:
    """A fixture loaded into a private in-memory SQLite database that runs read-only queries under the module's
    guardrails (see the module docstring). Raises ValueError when the script cannot be loaded. `digest` is the sha256 of
    the script's UTF-8 text. Shared by every task with the same fixture text (`sandbox_for`); immutable from outside, so
    copying returns the same object."""

    def __init__(self, script: str, name: str = "fixture") -> None:
        self.name = name
        self.digest = hashlib.sha256(script.encode("utf-8", "surrogatepass")).hexdigest()
        if not CAN_LIMIT:
            raise ValueError(
                f"fixture {name!r}: running SQL answers needs Python 3.11 or later, whose sqlite3 caps the size of a value"
                " (Connection.setlimit); use dialect 'postgres' to compare query texts only"
            )
        conn = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
        # SQLite's own versions of the functions the sandbox wraps; it holds no data and runs only the fixed statements
        # of `_guarded`, `_metered` and `_time_function`, under this sandbox's lock.
        helper = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
        watch = _Watch()
        try:
            # Before the load, so a fixture value no query could read is a load error rather than a failing query.
            conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, VALUE_BYTES)
            conn.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
            helper.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, VALUE_BYTES)
            helper.setlimit(sqlite3.SQLITE_LIMIT_LIKE_PATTERN_LENGTH, LIKE_PATTERN_BYTES)
            # Before the load too: a column's DEFAULT reaches these without the load authorizer.
            for fname in sorted(_TIME_FUNCTIONS):
                conn.create_function(fname, -1, _time_function(fname, watch, helper), deterministic=True)
            for fname in sorted(_CLOCK_WORDS):
                conn.create_function(fname, 0, _refuser(watch, READS_CLOCK))
            conn.create_function("random", 0, _refuser(watch, CALLS_RANDOM))
            conn.create_function("randomblob", 1, _refuser(watch, CALLS_RANDOM))
            tables = self._load(conn, script, watch)
            conn.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 4 * MAX_QUERY_CHARS)  # after it: a script's INSERT is longer
            conn.setlimit(sqlite3.SQLITE_LIMIT_LIKE_PATTERN_LENGTH, LIKE_PATTERN_BYTES)
            conn.set_authorizer(_allow_all)  # the load authorizer refuses this PRAGMA
            conn.execute("PRAGMA query_only = ON")
            for (fname, nargs), fast in _GUARDED.items():
                conn.create_function(fname, nargs, _guarded(fast, fname, nargs, helper, watch), deterministic=True)
            for fname, nargs in (("like", 2), ("like", 3), ("glob", 2)):
                conn.create_function(fname, nargs, _metered(fname, nargs, watch, helper), deterministic=True)
            self._authorizer = _query_authorizer(tables | ALWAYS_READABLE)
            conn.set_authorizer(self._authorizer)
        except BaseException:
            conn.close()
            helper.close()
            raise
        self._helper = helper
        self._watch = watch
        self._conn = conn
        self._lock = threading.Lock()
        self._cache: OrderedDict[tuple[str, int, int], Result | str] = OrderedDict()

    def _load(self, conn: sqlite3.Connection, script: str, watch: _Watch) -> frozenset[str]:
        """Run the fixture script; the lower-cased names of the tables and views it created."""
        page = conn.execute("PRAGMA page_size").fetchone()[0]
        conn.execute(f"PRAGMA max_page_count = {MAX_FIXTURE_DB_BYTES // page}")
        conn.execute("PRAGMA temp_store = MEMORY")  # nothing a query does ever writes a file
        # The schema is the operator's, and the sandbox's own functions (the date functions, LIKE, ...) must work in its
        # views as SQLite's do, whatever SQLITE_TRUSTED_SCHEMA this SQLite was built with.
        conn.execute("PRAGMA trusted_schema = ON")
        deadline = time.monotonic() + LOAD_SECONDS
        conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 100_000)
        conn.set_authorizer(_load_authorizer)
        schema = "SELECT type, name, sql FROM sqlite_master UNION ALL SELECT type, name, sql FROM sqlite_temp_master"
        try:
            conn.executescript(script)
            if conn.in_transaction:  # a script that began a transaction and did not end it
                conn.execute("COMMIT")
            objects = conn.execute(schema).fetchall()
        except _SQL_ERRORS as e:
            if watch.refused == READS_CLOCK:
                raise ValueError(
                    f"fixture {self.name!r} reads the clock while it loads (a date function given 'now' or no time value,"
                    " or a CURRENT_TIMESTAMP default): its database would change from day to day"
                ) from None
            if watch.refused == CALLS_RANDOM:
                raise ValueError(
                    f"fixture {self.name!r} calls random() or randomblob() while it loads (a column's DEFAULT): its"
                    " database would change on every load"
                ) from None
            if time.monotonic() > deadline:
                raise ValueError(f"fixture {self.name!r} took longer than {LOAD_SECONDS:g} s to load") from None
            if "full" in str(e):
                raise ValueError(f"fixture {self.name!r} is larger than {MAX_FIXTURE_DB_BYTES >> 20} MB once loaded") from None
            raise ValueError(f"fixture {self.name!r} cannot be loaded: {e}") from None
        finally:
            conn.set_progress_handler(None, 0)
        tables = frozenset(name.lower() for kind, name, _ in objects if kind in ("table", "view"))
        if not tables:
            raise ValueError(f"fixture {self.name!r} creates no table")
        for kind, name, sql in objects:
            tokens = _tokens(sql) if kind == "view" and isinstance(sql, str) else None
            if tokens is not None and _clock_read(tokens):
                raise ValueError(f"fixture {self.name!r}: view {name!r} reads the clock, so no query on it could count")
        return tables

    def __copy__(self) -> Sandbox:
        return self

    def __deepcopy__(self, memo: dict[int, Any]) -> Sandbox:
        return self

    def run(self, sql: str, max_rows: int, max_steps: int) -> Result | str:
        """What the query returns, or why it is invalid. One query at a time per sandbox; outcomes that do not depend on
        timing are cached by query text and limits."""
        cache_key = (sql, max_rows, max_steps)
        with self._lock:
            hit = self._cache.get(cache_key)
            if hit is not None:
                self._cache.move_to_end(cache_key)
                return hit
            out = self._execute(sql, max_rows, max_steps)
            if len(sql) <= CACHE_CHARS and not (isinstance(out, str) and out in _TIMING):
                self._cache[cache_key] = out
                if len(self._cache) > CACHE_SIZE:
                    self._cache.popitem(last=False)
            return out

    def _execute(self, sql: str, max_rows: int, max_steps: int) -> Result | str:
        budget = _Budget(max_steps)
        conn, watch = self._conn, self._watch
        watch.budget, watch.refused = budget, None
        conn.set_progress_handler(budget, PROGRESS_EVERY)
        cur = None
        try:
            cur = conn.execute(sql)
            if cur.description is None:  # not a query after all
                return NOT_ONE_QUERY
            columns = len(cur.description)
            rows: list[bytes] = []
            size = 0
            while True:
                chunk = cur.fetchmany(FETCH_ROWS)
                if not chunk:
                    break
                for row in chunk:
                    if len(rows) == max_rows:
                        return TOO_MANY_ROWS
                    encoded = repr(tuple(map(_value, row))).encode("utf-8", "surrogatepass")
                    size += len(encoded)
                    if size > RESULT_BYTES:
                        return TOO_LARGE
                    rows.append(hashlib.sha256(encoded).digest())
        except _SQL_ERRORS as e:
            if budget.reason is not None:
                return budget.reason
            if watch.refused is not None:
                return watch.refused
            if isinstance(e, MemoryError):
                return TOO_MUCH_MEMORY
            message = str(e)  # SQLite's for a denied action; for a denied read, "access to t.c is prohibited"
            return SQL_DENIED if "not authorized" in message or "is prohibited" in message else SQL_ERROR
        finally:
            if cur is not None:
                cur.close()
            conn.set_progress_handler(None, 0)
            watch.budget = None
        return Result(columns, len(rows), _digest(columns, True, rows), _digest(columns, False, sorted(rows)))


# Sandboxes by fixture digest: tasks with the same fixture text share one database and its lock. Weak, so a sandbox no
# registered task holds any more is closed.
_SANDBOXES: weakref.WeakValueDictionary[str, Sandbox] = weakref.WeakValueDictionary()
_SANDBOXES_LOCK = threading.Lock()


def sandbox_for(script: str, name: str = "fixture") -> Sandbox:
    """The sandbox for a fixture script: the one already loaded from the same text, or a new one (ValueError when the
    script cannot be loaded)."""
    digest = hashlib.sha256(script.encode("utf-8", "surrogatepass")).hexdigest()
    with _SANDBOXES_LOCK:
        found = _SANDBOXES.get(digest)
        if found is None:
            found = _SANDBOXES[digest] = Sandbox(script, name)
        return found


# ---------------------------------------------------------------------------------------------------------------------
# Fixture files


def resolve_fixture(path: Any, root: str | Path) -> Path:
    """The file a task spec names as a fixture, which must sit inside RATCHET_FIXTURES_DIR (`root`) once '..' and
    symlinks are resolved, so a spec cannot make the gateway read any other file. ValueError otherwise."""
    if not isinstance(path, str) or not path or "\x00" in path:
        raise ValueError("a fixture is a relative path inside RATCHET_FIXTURES_DIR")
    if Path(path).is_absolute():
        raise ValueError(f"fixture {path!r}: use a path relative to RATCHET_FIXTURES_DIR")
    base = Path(root).resolve()
    full = (base / path).resolve()
    if not full.is_relative_to(base):
        raise ValueError(f"fixture {path!r} is outside RATCHET_FIXTURES_DIR")
    return full


def read_fixture(path: Any, root: str | Path) -> str:
    """The text of a fixture file (`resolve_fixture`): a regular file of at most MAX_FIXTURE_BYTES of UTF-8 (a byte
    order mark is dropped). Opened without blocking, so a FIFO cannot hang the caller. ValueError otherwise."""
    full = resolve_fixture(path, root)
    try:
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        fd = os.open(full, flags)
    except OSError as e:
        raise ValueError(f"fixture {path!r} cannot be read: {e.strerror or e}") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"fixture {path!r} is not a regular file")
        if info.st_size > MAX_FIXTURE_BYTES:
            raise ValueError(f"fixture {path!r} is larger than {MAX_FIXTURE_BYTES >> 20} MB")
        chunks, size = [], 0
        while size <= MAX_FIXTURE_BYTES:
            chunk = os.read(fd, min(1 << 20, MAX_FIXTURE_BYTES + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
    except OSError as e:
        raise ValueError(f"fixture {path!r} cannot be read: {e.strerror or e}") from None
    finally:
        os.close(fd)
    if size > MAX_FIXTURE_BYTES:
        raise ValueError(f"fixture {path!r} is larger than {MAX_FIXTURE_BYTES >> 20} MB")
    try:
        return b"".join(chunks).decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError(f"fixture {path!r} is not UTF-8 text") from None


# ---------------------------------------------------------------------------------------------------------------------
# A task's sql options


def option_problem(key: Any, value: Any) -> str | None:
    """Why `value` cannot be the `sql` option `key`, or None. One option at a time, so a spec can name every bad one."""
    if key not in KEYS:
        return f"unknown key; use {', '.join(KEYS)}"
    if key == "fixture":
        return None if isinstance(value, str) and value else "must be a path relative to RATCHET_FIXTURES_DIR"
    choices = {"dialect": DIALECTS, "ordered": ORDERED, "empty_result": EMPTY_RESULTS}.get(key)
    if choices is not None:
        return None if isinstance(value, str) and value in choices else f"must be one of {', '.join(repr(c) for c in choices)}"
    low, high = (1, MAX_ROWS) if key == "max_rows" else (MIN_STEPS, MAX_STEPS)
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        return f"must be a whole number from {low:,} to {high:,}"
    return None


@dataclass(frozen=True)
class SqlSpec:
    """How a `sql` task's answers compare: `dialect` `sqlite` runs each query on the `fixture` (a path inside
    RATCHET_FIXTURES_DIR, loaded into `sandbox`) and compares results (`ordered`, `empty_result`, at most `max_rows` rows
    and `max_steps` VM steps); `postgres` compares the normalised texts and takes no other option. Build one with `load`
    (a fixture file) or `from_script`. Invalid options raise ValueError. Two specs are equal when their options (the
    fixture's path among them) and the fixture's text (`fixture_sha256`) are, whichever sandbox holds it; `spec()`, and so
    the task's spec_hash, leaves the path out."""

    fixture: str | None = None
    dialect: str = "sqlite"
    ordered: str = "auto"
    empty_result: str = "text"
    max_rows: int = DEFAULT_MAX_ROWS
    max_steps: int = DEFAULT_MAX_STEPS
    sandbox: Sandbox | None = field(default=None, repr=False, compare=False)
    fixture_sha256: str = field(init=False, default="")

    def __post_init__(self) -> None:
        def fail(message: str) -> None:
            raise ValueError(f"sql: {message}")

        for key in KEYS:
            value = getattr(self, key)
            if key == "fixture" and value is None:
                continue
            problem = option_problem(key, value)
            if problem is not None:
                fail(f"{key} {problem}")
        if self.dialect == "postgres":
            defaults = SqlSpec.__dataclass_fields__
            changed = [k for k in KEYS if k != "dialect" and getattr(self, k) != defaults[k].default]
            if changed or self.sandbox is not None:
                fail(f"{(changed or ['fixture'])[0]} does not apply to dialect 'postgres', which runs no SQL")
            return
        if self.fixture is None:
            fail("a sqlite task needs a fixture (a SQL script in RATCHET_FIXTURES_DIR)")
        if not isinstance(self.sandbox, Sandbox):
            fail("the fixture is not loaded: build the spec with SqlSpec.load or SqlSpec.from_script")
        object.__setattr__(self, "fixture_sha256", self.sandbox.digest)

    @classmethod
    def load(cls, fixture: str, root: str | Path, **options: Any) -> SqlSpec:
        """A sqlite spec whose fixture is the file `fixture` inside `root` (RATCHET_FIXTURES_DIR). The options are checked
        before the file is read. ValueError for a bad option or a fixture that cannot be read or loaded."""
        for key, value in options.items():
            problem = option_problem(key, value)
            if problem is not None:
                raise ValueError(f"sql: {key} {problem}")
        script = read_fixture(fixture, root)
        return cls(fixture=fixture, sandbox=sandbox_for(script, fixture), **options)

    @classmethod
    def from_script(cls, script: str, name: str = "inline.sql", **options: Any) -> SqlSpec:
        """A sqlite spec whose fixture is `script` (code and tests; specs as data name a file, `load`)."""
        return cls(fixture=name, sandbox=sandbox_for(script, name), **options)

    @property
    def executes(self) -> bool:
        """Answers are run on the fixture (dialect `sqlite`), so agreement says they return the same rows."""
        return self.sandbox is not None

    def spec(self) -> dict[str, Any]:
        """The rules as `Task.spec` holds them: the options and the fixture's sha256 (not its path, so moving the file
        keeps the task's spec_hash, and changing its text changes it)."""
        if not self.executes:
            return {"dialect": self.dialect}
        out: dict[str, Any] = {k: getattr(self, k) for k in KEYS if k != "fixture"}
        out["fixture_sha256"] = self.fixture_sha256
        return out

    def document(self) -> dict[str, Any]:
        """The options as a spec file writes them (`taskspec.document`); `load` reads them back."""
        if not self.executes:
            return {"dialect": self.dialect}
        return {k: getattr(self, k) for k in KEYS}

    def key(self, clean: str) -> tuple[Any, str | None]:
        """A cleaned answer's `SqlKey`, or (`INVALID`, why)."""
        q = parse_query(clean, check_clock=self.executes)
        if isinstance(q, str):
            return INVALID, q
        if self.sandbox is None:
            return SqlKey(0, 0, False, "", q.text), None
        res = self.sandbox.run(q.sql, self.max_rows, self.max_steps)
        if isinstance(res, str):
            return INVALID, res
        ordered = res.rows > 1 and (self.ordered == "always" or (self.ordered == "auto" and q.ordered))
        return SqlKey(res.columns, res.rows, ordered, res.in_order if ordered else res.as_multiset, q.text), None

    def match(self, a: SqlKey, b: SqlKey) -> bool:
        """Two keys agree: the same normalised text for dialect postgres, or for two empty results under
        `empty_result: text`; otherwise the same result (column count, rows, and order when both are ordered)."""
        if not self.executes or (a.rows == 0 and b.rows == 0 and self.empty_result == "text"):
            return a.text == b.text
        return a.result == b.result

    def canonical(self, key: SqlKey) -> str:
        """What must match as one string (`Task.canonical`, for logs and tests): the normalised text where texts are
        compared, else the result's summary as JSON."""
        if not self.executes or (key.rows == 0 and self.empty_result == "text"):
            return key.text
        summary = {"columns": key.columns, "rows": key.rows, "ordered": key.ordered, "result": key.result}
        return json.dumps(summary, sort_keys=True)
