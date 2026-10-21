"""Text-to-SQL tasks (FR-2.56, ADR 0055): answers compared by what they return on a fixture in a sandboxed in-memory
SQLite database, the sandbox's guardrails (writes, ATTACH, PRAGMA and non-deterministic functions denied; steps, time,
memory, rows and result size bounded; the fixture unchanged), the spec (`sql` options, fixture paths and errors, round
trip, spec_hash), and what the cascade, the golden eval, the dataset and the admin API do with sql tasks."""

import json
import math
import os
import random
import sqlite3
import sys
import threading
import time

import pytest
from fakes import OPEN_TEACHER, RecordingProvider, _version
from fastapi.testclient import TestClient

from ratchet import sqlcheck, tasks, taskspec
from ratchet.compare import INVALID
from ratchet.config import Settings
from ratchet.ledger import Ledger
from ratchet.nightschool import dataset
from ratchet.nightschool.evaluate import cascade_eval
from ratchet.providers import Completion
from ratchet.router import Ratchet
from ratchet.taskspec import SpecError

SHOP = """
PRAGMA foreign_keys=OFF;
BEGIN TRANSACTION;
CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL, city TEXT);
CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers(id), total REAL, qty INTEGER,
                     status TEXT, placed TEXT);
INSERT INTO customers VALUES (1, 'Asha', 'Pune'), (2, 'Ben', 'Oslo'), (3, 'Chen', 'Pune'), (4, 'Dina', NULL);
INSERT INTO orders VALUES
  (1, 1, 10.5, 2, 'paid', '2026-01-05'), (2, 1, 20.0, 1, 'open', '2026-02-11'), (3, 2, 5.25, 4, 'paid', '2026-02-14'),
  (4, 3, 99.9, 1, 'paid', '2026-03-01'), (5, 3, 0.1, 3, 'refunded', '2026-03-02');
COMMIT;
"""
COUNT = "SELECT COUNT(*) FROM customers"
# Running SQL needs Python 3.11+ (`Connection.setlimit` caps a value's size); on 3.10 a sqlite fixture is refused.
needs_sql = pytest.mark.skipif(not sqlcheck.CAN_LIMIT, reason="running SQL needs Python 3.11+ (Connection.setlimit)")


def shop(**options) -> tasks.Task:
    return tasks.Task("shop-sql", sql=sqlcheck.SqlSpec.from_script(SHOP, "shop.sql", **options))


def agree(task: tasks.Task, a: str, b: str) -> bool:
    assert task.valid(a) and task.valid(b), (task.check(a), task.check(b))
    return task.agree(a, b)


@pytest.fixture()
def fixtures(tmp_path):
    """A RATCHET_FIXTURES_DIR holding shop.sql."""
    root = tmp_path / "fixtures"
    root.mkdir()
    (root / "shop.sql").write_text(SHOP, encoding="utf-8")
    return root


def spec(**sql) -> dict:
    return {"name": "shop-sql", "format": "sql", "sql": {"fixture": "shop.sql", **sql}}


# ---------- what agrees ----------
@pytest.mark.req("FR-2.56")
@needs_sql
def test_sql_equivalent_queries_agree():
    t = shop()
    assert agree(t, COUNT, "select count(id) from customers;")
    assert agree(t, "SELECT COUNT(*) AS n FROM customers", "SELECT COUNT(name) AS how_many FROM customers")  # names ignored
    joined = "SELECT DISTINCT c.name FROM customers c JOIN orders o ON o.customer_id = c.id"
    assert agree(t, "SELECT name FROM customers WHERE id IN (SELECT customer_id FROM orders)", joined)
    assert agree(t, "SELECT name FROM customers WHERE EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = customers.id)", joined)
    # Numbers: SUM over integers and TOTAL (a float) agree, and floats agree to 9 significant digits.
    assert agree(t, "SELECT SUM(qty) FROM orders", "SELECT TOTAL(qty) FROM orders")
    assert agree(t, "SELECT 0.1 + 0.2", "SELECT 0.3")
    assert agree(t, "SELECT ROUND(AVG(total), 2) FROM orders", "SELECT ROUND(SUM(total) / COUNT(*), 2) FROM orders")
    assert agree(t, "VALUES (1), (2)", "SELECT 1 UNION ALL SELECT 2")
    assert t.verifiable and not t.free_text and t.precision_kind == "exact" and t.format == "sql"


@pytest.mark.req("FR-2.56")
@needs_sql
def test_sql_wrong_filter_disagrees():
    t = shop()
    assert not agree(t, "SELECT name FROM customers WHERE city = 'Pune'", "SELECT name FROM customers WHERE city = 'Oslo'")
    assert not agree(t, "SELECT id FROM orders WHERE total > 10.5", "SELECT id FROM orders WHERE total >= 10.5")
    assert not agree(t, "SELECT COUNT(*) FROM orders WHERE status = 'paid'", "SELECT COUNT(*) FROM orders")
    assert not agree(t, "SELECT name FROM customers", "SELECT name, city FROM customers")  # the column count differs
    assert not agree(t, "SELECT 1", "SELECT '1'")  # an integer is not text
    assert not agree(t, "SELECT name FROM customers", "SELECT name FROM customers UNION ALL SELECT 'Asha'")  # a multiset


@pytest.mark.req("FR-2.56")
@needs_sql
def test_sql_order_by_respected_otherwise_multiset():
    t = shop()
    by_name = "SELECT name FROM customers ORDER BY name"
    assert not agree(t, by_name, "SELECT name FROM customers ORDER BY name DESC")
    assert agree(t, by_name, "select name from customers order by 1")
    # No ORDER BY on the outermost query: the rows are a multiset, whatever order SQLite returns them in.
    plain = "SELECT name FROM customers"
    assert agree(t, plain, "SELECT name FROM (SELECT name FROM customers ORDER BY name DESC)")
    assert agree(t, plain, "SELECT name FROM customers WHERE id IN (SELECT id FROM customers ORDER BY id DESC)")
    assert agree(t, plain, "WITH c AS (SELECT name FROM customers ORDER BY name DESC LIMIT 10) SELECT name FROM c")
    windowed = "SELECT name, ROW_NUMBER() OVER (ORDER BY name) AS r FROM customers"
    assert agree(t, windowed, "SELECT name, ROW_NUMBER() OVER (ORDER BY name) FROM customers ORDER BY name DESC") is False
    assert not t.key(windowed).ordered and t.key(by_name).ordered
    # An ordered result of two rows or more never equals an unordered one: the question asked for an order or did not.
    assert not agree(t, plain, by_name)
    # One row has one order.
    assert agree(t, f"{COUNT} ORDER BY 1", COUNT) and not t.key(f"{COUNT} ORDER BY 1").ordered
    # ordered: never ignores ORDER BY; always compares every result in order.
    never, always = shop(ordered="never"), shop(ordered="always")
    assert agree(never, by_name, "SELECT name FROM customers ORDER BY name DESC") and agree(never, plain, by_name)
    assert agree(always, by_name, "select name from customers order by name")
    assert not agree(always, plain, "SELECT name FROM customers ORDER BY name DESC")


@pytest.mark.req("FR-2.56")
@needs_sql
def test_sql_empty_results_fall_back_to_text():
    t = shop()
    none_a = "SELECT name FROM customers WHERE city = 'Paris'"
    none_b = "SELECT name FROM customers WHERE city = 'Rome'"
    assert t.key(none_a).rows == 0
    assert not agree(t, none_a, none_b)  # both empty: the texts differ
    assert agree(t, none_a, "select  NAME\nfrom customers -- nobody\nwhere city = 'Paris';")  # the same query, normalised
    assert not agree(t, none_a, "SELECT name FROM customers WHERE city = 'Pune'")  # empty and not empty
    assert t.canonical(none_a) == "select name from customers where city = 'Paris'"
    assert sqlcheck.normalize("SELECT  COUNT(*)\nFROM t; ") == "select count ( * ) from t"
    assert sqlcheck.normalize("SELECT \"Name\" FROM T WHERE x = 'A'") == "select \"Name\" from t where x = 'A'"
    assert sqlcheck.normalize("SELECT 1; SELECT 2") is None
    loose = shop(empty_result="agree")
    assert agree(loose, none_a, none_b)
    assert not agree(loose, none_a, "SELECT name, city FROM customers WHERE city = 'Rome'")  # two columns, not one


@pytest.mark.req("FR-2.56")
@needs_sql
def test_sql_multiple_statements_invalid():
    t = shop()
    for answer in ("SELECT 1; SELECT 2", f"{COUNT}; DROP TABLE orders", f"{COUNT};\nThis query counts the customers."):
        assert t.key(answer) is INVALID and t.check(answer) == "not one query"
    for answer in (f"{COUNT};", f"{COUNT}; -- done", f"; {COUNT} ;;", "SELECT ';' AS s", "SELECT 1 /* ; */", "SELECT 1 -- ; x"):
        assert t.valid(answer), answer
    for answer in (
        "SELECT 'unterminated",
        'SELECT "unterminated',
        "SELECT 1 /* open",
        "SELECT [x",
        "EXPLAIN SELECT 1",
        "PRAGMA table_info(orders)",
        "DELETE FROM orders",
        "(SELECT 1)",
        "Here is the query: SELECT 1",
        "SELECT " + "1 + " * 10_000 + "1",  # longer than MAX_QUERY_CHARS
    ):
        assert t.check(answer) == "not one query", answer


@pytest.mark.req("FR-2.56", "FR-2.13")
@needs_sql
def test_sql_answer_cleaning():
    t = shop()
    fenced = f"<think>count rows</think>\n```sql\n{COUNT};\n```"
    assert tasks.clean_answer(t, fenced) == f"{COUNT};" and agree(t, fenced, COUNT)
    assert tasks.clean_answer(t, f"```\n{COUNT}\n```") == COUNT
    nested = f"```sql\n```sql\n{COUNT}\n```\n```"
    assert tasks.clean_answer(t, nested) == nested and not t.valid(nested)  # a fence inside the fence: not unwrapped
    assert tasks.clean_answer(t, tasks.clean_answer(t, fenced)) == tasks.clean_answer(t, fenced)  # idempotent
    assert t.check("") == "empty answer" and t.check("<think>still thinking") == "empty answer"


# ---------- the sandbox ----------
def _counts(t: tasks.Task) -> tuple[int, int]:
    sandbox = t.sql.sandbox
    customers = sandbox._conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    orders = sandbox._conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    return customers, orders


@pytest.mark.req("FR-2.56")
@needs_sql
def test_sql_write_and_attach_denied_and_fixture_unchanged(tmp_path):
    t = shop()
    before = _counts(t)
    # Writes that read as a query reach SQLite, whose authorizer denies them.
    for answer in (
        "WITH x AS (SELECT 1) INSERT INTO orders (id) SELECT 99 FROM x",
        "WITH x AS (SELECT 1) DELETE FROM orders",
        "WITH x AS (SELECT 1) UPDATE customers SET city = 'Rome'",
    ):
        assert t.key(answer) is INVALID and t.check(answer) == "sql denied", answer
    # Any statement run on the sandbox directly: denied, never a result.
    sandbox = t.sql.sandbox
    for sql in (
        "DELETE FROM orders",
        "INSERT INTO customers VALUES (9, 'Eve', 'Rome')",
        "UPDATE orders SET total = 0",
        "DROP TABLE orders",
        "CREATE TABLE notes (x)",
        "CREATE TEMP TABLE notes (x)",
        "ALTER TABLE orders ADD COLUMN y",
        f"ATTACH '{tmp_path / 'other.db'}' AS other",
        f"VACUUM INTO '{tmp_path / 'copy.db'}'",
        "BEGIN",
        "SAVEPOINT s",
        "ANALYZE",
        "PRAGMA query_only = OFF",
    ):
        assert sandbox.run(sql, 1000, 200_000) in (sqlcheck.SQL_DENIED, sqlcheck.SQL_ERROR), sql
    assert list(tmp_path.iterdir()) == []  # no file written: the fixture is the only data, in memory
    assert _counts(t) == before and agree(t, COUNT, "SELECT 4")
    # Two layers: with an authorizer that allows everything, query_only still refuses a write. (Not None: Python 3.10
    # reads set_authorizer(None) as a callback that denies everything.)
    with sandbox._lock:
        sandbox._conn.set_authorizer(lambda *_: sqlite3.SQLITE_OK)
        try:
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                sandbox._conn.execute("DELETE FROM orders")
            assert sandbox._conn.execute("PRAGMA query_only").fetchone() == (1,)
        finally:
            sandbox._conn.set_authorizer(sandbox._authorizer)
    assert _counts(t) == before


@pytest.mark.req("FR-2.56")
@needs_sql
def test_sql_pragma_denied():
    t = shop()
    for answer in (
        "SELECT * FROM pragma_table_info('orders')",
        "SELECT * FROM pragma_query_only",
        "SELECT * FROM pragma_function_list",
    ):
        assert t.check(answer) == "sql denied", answer
    sandbox = t.sql.sandbox
    for sql in (
        "PRAGMA query_only = OFF",
        "PRAGMA writable_schema = ON",
        "PRAGMA table_info(orders)",
        "PRAGMA case_sensitive_like = ON",
    ):
        assert sandbox.run(sql, 1000, 200_000) == sqlcheck.SQL_DENIED, sql
    assert t.check("SELECT name FROM sqlite_master WHERE type = 'table'") is None  # reading the schema is reading
    # Only the fixture's own tables and views, the schema and the JSON table-valued functions can be read: sqlite_stmt
    # lists the statements the shared connection has cached (other requests' queries) and depends on that history, and
    # dbstat exposes storage.
    assert t.valid("SELECT name FROM customers WHERE id = 1")
    for answer in (
        "SELECT * FROM sqlite_stmt",
        "SELECT sql FROM sqlite_stmt WHERE sql LIKE '%customers%'",
        "SELECT * FROM dbstat",
    ):
        assert t.check(answer) == "sql denied", answer
    for answer in ("SELECT * FROM sqlite_schema", "SELECT * FROM sqlite_temp_master", "SELECT value FROM json_each('[1, 2]')"):
        assert t.check(answer) is None, answer
    assert t.check("""SELECT key FROM json_tree('{"a": [1]}')""") is None
    viewed = tasks.Task(
        "viewed", sql=sqlcheck.SqlSpec.from_script(SHOP + "CREATE VIEW cached AS SELECT sql FROM sqlite_stmt;", "v.sql")
    )
    assert viewed.check("SELECT * FROM cached") == "sql denied" and viewed.check("SELECT COUNT(*) FROM customers") is None


@pytest.mark.req("FR-2.56", "NFR-1")
@needs_sql
def test_sql_runaway_recursive_cte_interrupted_quickly(monkeypatch):
    t = shop()
    runaway = "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) SELECT MAX(n) FROM r"
    start = time.perf_counter()
    assert t.check(runaway) == "sql steps exceeded"
    assert time.perf_counter() - start < 0.5
    cross = "SELECT COUNT(*) FROM orders a, orders b, orders c, orders d, orders e, orders f, orders g, orders h, orders i"
    assert t.check(cross) == "sql steps exceeded"
    # A query within its steps but over the wall-clock budget is stopped too, and never cached: it depends on the moment.
    patient = shop(max_steps=sqlcheck.MAX_STEPS)
    monkeypatch.setattr(sqlcheck, "QUERY_SECONDS", 0.0)
    slow = "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r WHERE n < 50000) SELECT MAX(n) FROM r"
    assert patient.check(slow) == "sql time exceeded"
    monkeypatch.setattr(sqlcheck, "QUERY_SECONDS", 60.0)  # not 1 s: a busy machine must not turn this into a timeout
    assert patient.check(slow) is None


@pytest.mark.req("FR-2.56")
@needs_sql
def test_sql_random_function_denied():
    t = shop()
    for answer in (
        "SELECT random()",
        "SELECT name FROM customers ORDER BY RANDOM() LIMIT 1",
        "SELECT randomblob(4)",
        "SELECT changes()",
        "SELECT total_changes()",
        "SELECT last_insert_rowid()",
        "SELECT load_extension('evil')",
        "SELECT sqlite_version()",
        "SELECT zeroblob(10)",
    ):
        assert t.check(answer) == "sql denied", answer
    for answer in (
        "SELECT date('now')",
        "SELECT name FROM customers, orders WHERE placed > DATE('NOW', '-30 days')",
        "SELECT strftime('%Y', 'now')",
        "SELECT datetime(placed, 'localtime') FROM orders",
        "SELECT CURRENT_DATE",
        "SELECT current_timestamp",
        "SELECT date((SELECT 'now'))",  # anywhere inside a date function's arguments
        "SELECT date(lower(' NOW '))",
        "SELECT strftime('%s', CASE WHEN 1 THEN 'now' END)",
        # No time value: SQLite reads the clock (strftime's first argument is its format).
        "SELECT date()",
        "SELECT time()",
        "SELECT datetime( /* nothing */ )",
        "SELECT julianday()",
        "SELECT unixepoch()",
        "SELECT strftime('%s')",
        "SELECT COUNT(*) FROM orders WHERE julianday() - julianday(placed) < 30",
        "SELECT id FROM orders WHERE placed > strftime('%Y-%m-%d')",
        """SELECT "date"('now')""",  # a quoted function name
        "SELECT [julianday]()",
        # A double-quoted name that matches no column is a string to SQLite.
        """SELECT date("now")""",
        """SELECT id FROM orders WHERE placed > date("now", "-30 days")""",
        """SELECT date(lower("NOW"))""",
    ):
        assert t.check(answer) == "reads the clock", answer
    # A value built at run time, or reached through a subquery, is caught when the date function runs.
    for answer in (
        "SELECT date('n' || 'ow')",
        "SELECT date(x) FROM (SELECT 'now' AS x)",
        "SELECT date('subsec')",  # as the time value, 'subsec' means now
        "SELECT datetime(placed, 'local' || 'time') FROM orders",
        "SELECT strftime('%s', 'NOW' || char(0) || 'x')",  # SQLite reads text up to its first NUL
        "SELECT julianday(CAST('now' AS BLOB))",
        "SELECT timediff(placed, upper('now')) FROM orders",
    ):
        assert t.check(answer) == "reads the clock", answer
    assert t.check("SELECT strftime('%Y', placed), date(placed, '+1 day') FROM orders") is None
    assert t.check("SELECT 'now' AS word, 'utc' AS zone") is None  # a string, not a clock read
    assert t.check("SELECT date(placed), lower('now') FROM orders") is None  # after the date function's parenthesis
    assert t.check("""SELECT date("placed"), date(NULL), strftime() FROM orders""") is None  # a column; NULL in, NULL out
    assert t.check("SELECT datetime(placed, 'subsec'), unixepoch(placed), timediff(placed, '2026-01-01') FROM orders") is None
    # The date functions the sandbox wraps give SQLite's own results.
    plain, sandbox = sqlite3.connect(":memory:"), t.sql.sandbox
    for expr in (
        "date('2026-03-01', '+1 month', 'start of month')",
        "strftime('%j %w %H', '2026-02-14 10:30')",
        "julianday('2026-01-05')",
        "unixepoch('2026-01-05 00:00:01')",
        "datetime(1767225600, 'unixepoch')",
        "timediff('2026-03-01', '2026-01-05')",
        "time('12:00', '+90 minutes')",
        "date(NULL)",
        "date('nonsense')",
    ):
        with sandbox._lock:
            ours = sandbox._conn.execute(f"SELECT {expr}").fetchone()
        assert ours == plain.execute(f"SELECT {expr}").fetchone(), expr
    # Never frozen by the cache: the outcome is the reason, whatever the query's spelling or the moment.
    assert t.key("SELECT unixepoch()") is INVALID and t.key("select  UNIXEPOCH()") is INVALID
    assert t.check("SELECT unixepoch()") == "reads the clock"
    assert set(sqlcheck.FUNCTIONS).isdisjoint({"random", "randomblob", "changes", "last_insert_rowid", "load_extension"})


@pytest.mark.req("FR-2.56")
@needs_sql
def test_sql_rows_result_size_and_value_limits(monkeypatch):
    t = shop(max_rows=3)
    assert t.check("SELECT id FROM orders") == "sql too many rows"
    assert t.check("SELECT id FROM orders LIMIT 3") is None
    monkeypatch.setattr(sqlcheck, "RESULT_BYTES", 40)
    assert shop(max_rows=4).check("SELECT name, city FROM customers") == "sql result too large"


@pytest.mark.req("FR-2.56")
@needs_sql
def test_sql_value_limit():
    doubling = "WITH RECURSIVE r(n, s) AS (SELECT 1, 'x' UNION ALL SELECT n + 1, s || s FROM r WHERE n < 30) SELECT MAX(n) FROM r"
    assert shop().check(doubling) == "sql error"  # 2**29 characters: over VALUE_BYTES long before


@pytest.mark.req("FR-2.56", "NFR-1")
@needs_sql
def test_sql_quadratic_builtins_replaced_by_linear_ones(monkeypatch):
    """One VM step is out of the progress handler's reach, so a built-in that does quadratic work in one call could hold
    the sandbox for minutes (trim(X, Y) took 15 s at 60,000 characters each). instr, replace and the two-argument trims
    are linear Python versions that give SQLite's results; LIKE and GLOB patterns are capped; json_patch and unhex are
    denied; and a query that repeats an expensive step per row still stops on time."""
    t = tasks.Task(
        "big", sql=sqlcheck.SqlSpec.from_script("CREATE TABLE big_probe (x);", "big.sql", max_steps=sqlcheck.MAX_STEPS)
    )
    n = sqlcheck.VALUE_BYTES - 1000
    a, half = f"printf('%.{n}c', 'a')", f"printf('%.{n // 2}c', 'a') || 'b'"
    start = time.perf_counter()
    for answer in (
        f"SELECT length(trim({a}, printf('%.{n}c', 'b') || 'a'))",
        f"SELECT length(rtrim({a}, printf('%.{n}c', 'b') || 'a'))",
        f"SELECT instr({a}, {half})",
        f"SELECT instr(CAST({a} AS BLOB), CAST({half} AS BLOB))",
        f"SELECT length(replace({a}, {half}, 'x'))",
    ):
        assert t.check(answer) is None, answer
    assert time.perf_counter() - start < 2.0  # milliseconds each; SQLite's own trim took 15 s on one of them
    assert t.check(f"SELECT replace({a}, 'a', 'bb')") == "sql error"  # the result would be over VALUE_BYTES
    assert t.check(f"SELECT instr(CAST({a} AS BLOB), {half})") == "sql error"  # mixed types this large: refused
    assert t.check(f"SELECT {a} LIKE '%' || printf('%.{sqlcheck.LIKE_PATTERN_BYTES}c', 'a') || 'b'") == "sql error"
    assert t.check(f"SELECT {a} LIKE '%' || printf('%.{sqlcheck.LIKE_PATTERN_BYTES - 2}c', 'a') || 'b'") is None
    assert t.check("SELECT json_patch('{}', '{}')") == t.check("SELECT unhex('41')") == "sql denied"
    monkeypatch.setattr(sqlcheck, "QUERY_SECONDS", 0.2)
    per_row = (
        f"WITH RECURSIVE r(n, s) AS (SELECT 1, {a} UNION ALL SELECT n + 1, s FROM r WHERE n < 5000) "
        f"SELECT count(*) FROM r WHERE s LIKE '%' || printf('%.{sqlcheck.LIKE_PATTERN_BYTES - 2}c', 'a') || 'b'"
    )
    start = time.perf_counter()
    assert t.check(per_row) == "sql time exceeded" and time.perf_counter() - start < 1.5  # about 2 minutes unchecked
    # SQLite calls the progress handler only where a loop jumps back, never within one row: a row whose WHERE calls LIKE
    # 400 times (up to 25 ms each, about 10 s in all) is stopped by the check LIKE makes on every call.
    pattern = f"'%' || printf('%.{sqlcheck.LIKE_PATTERN_BYTES - 2}c', 'a') || 'b'"
    one_row = f"WITH r(s, p) AS (SELECT {a}, {pattern}) SELECT COUNT(*) FROM r WHERE " + " OR ".join(["s LIKE p"] * 400)
    start = time.perf_counter()
    assert t.check(one_row) == "sql time exceeded" and time.perf_counter() - start < 1.5
    assert t.check(r"SELECT 'a' LIKE 'A', 'a_b' LIKE 'a\_b' ESCAPE '\', 'ab' GLOB 'a*', 'ab' GLOB 'A*'") is None
    assert t.check("SELECT 'a' LIKE 'a' ESCAPE 'xx'") == "sql error"
    # The Python versions give SQLite's own results: text, blobs, numbers, NULL, NUL characters and empty strings.
    sandbox, plain, rng = t.sql.sandbox, sqlite3.connect(":memory:"), random.Random(5)

    def value():
        k = rng.random()
        if k < 0.08:
            return None
        if k < 0.2:
            return rng.choice([0, 1, -3, 12, 2**40])
        if k < 0.3:
            return rng.choice([1.5, 100.0, 1e20, -0.0, 2.5e-7])
        if k < 0.85:
            return "".join(rng.choice("ab ,\u00e9\U0001f600\x00") for _ in range(rng.randint(0, 40 if k > 0.8 else 6)))
        return bytes(rng.choice(b"ab \x00\xff") for _ in range(rng.randint(0, 6)))

    def outcome(conn, sql, args):
        try:
            got = conn.execute(sql, args).fetchone()[0]
            return type(got), got
        except sqlite3.Error as e:
            return type(e), None

    for name, nargs in sqlcheck._GUARDED:
        sql = f"SELECT {name}({', '.join('?' * nargs)})"
        for _ in range(3000):
            args = tuple(value() for _ in range(nargs))
            with sandbox._lock:
                ours = outcome(sandbox._conn, sql, args)
            assert ours == outcome(plain, sql, args), (name, args)


@pytest.mark.req("FR-2.56")
@pytest.mark.skipif(sqlcheck.resident_bytes() is None, reason="resident memory is read from /proc (Linux)")
@needs_sql
def test_sql_memory_guard(monkeypatch):
    """A sorter filled with large rows grows memory in few steps; the guard stops the query and does not cache that."""
    t = shop(max_steps=sqlcheck.MAX_STEPS)
    big = (
        "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r WHERE n < 40000) "
        "SELECT n, printf('%.2000c', 'x') || n FROM r ORDER BY n DESC"
    )
    monkeypatch.setattr(sqlcheck, "QUERY_SECONDS", 60.0)  # a busy machine must not turn either check into a timeout
    monkeypatch.setattr(sqlcheck, "QUERY_MEMORY_BYTES", 4 * 1024 * 1024)
    assert t.check(big) == "sql memory exceeded"
    monkeypatch.setattr(sqlcheck, "QUERY_MEMORY_BYTES", 1024 * 1024 * 1024)
    assert t.check(big) == "sql too many rows"  # run again, not read from the cache


@pytest.mark.req("FR-2.56")
@needs_sql
def test_sql_outcomes_cached_by_query_text(monkeypatch):
    script = SHOP + "CREATE TABLE cache_probe (x);"  # its own sandbox, so no other test's queries are in its cache
    t = tasks.Task("probe", sql=sqlcheck.SqlSpec.from_script(script, "probe.sql"))
    runs: list[str] = []
    real = sqlcheck.Sandbox._execute

    def counting(self, sql, max_rows, max_steps):
        runs.append(sql)
        return real(self, sql, max_rows, max_steps)

    monkeypatch.setattr(sqlcheck.Sandbox, "_execute", counting)
    for _ in range(3):
        assert t.valid(COUNT) and t.check("SELECT * FROM nope") == "sql error"
    assert runs == [COUNT, "SELECT * FROM nope"]
    long_query = f"{COUNT} WHERE name NOT IN ({', '.join(repr(f'n{i}') for i in range(sqlcheck.CACHE_CHARS // 6))})"
    assert sqlcheck.CACHE_CHARS < len(long_query) < sqlcheck.MAX_QUERY_CHARS
    assert t.valid(long_query) and t.valid(long_query) and runs[2:] == [long_query, long_query]  # too long to cache
    for i in range(sqlcheck.CACHE_SIZE + 10):
        t.valid(f"SELECT {i}")
    assert len(t.sql.sandbox._cache) == sqlcheck.CACHE_SIZE


@pytest.mark.req("FR-2.56")
@needs_sql
def test_sql_threads_share_one_sandbox_safely():
    a, b = shop(), shop(max_rows=50)
    assert a.sql.sandbox is b.sql.sandbox  # the same fixture text: one database and one lock
    queries = [COUNT, "SELECT name FROM customers ORDER BY name", "SELECT SUM(total) FROM orders", "SELECT * FROM nope"]
    want = [a.canonical(q) for q in queries]
    errors: list[BaseException] = []

    def work() -> None:
        try:
            for i in range(60):
                q = queries[i % len(queries)] + " " * (i % 7)  # different texts, same statements
                assert a.canonical(q) == want[i % len(queries)]
        except BaseException as e:  # noqa: BLE001 - reported below
            errors.append(e)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert errors == []


# ---------- the spec ----------
@pytest.mark.req("FR-2.56", "FR-2.15")
@needs_sql
def test_sql_spec_round_trip_and_spec_hash(fixtures, tmp_path):
    task = taskspec.parse_spec(spec(max_rows=50, ordered="never"), "api", fixtures_dir=fixtures)
    assert task.format == "sql" and task.sql.max_rows == 50 and task.sql.ordered == "never"
    doc = taskspec.document(task)
    assert list(doc) == list(taskspec.SPEC_KEYS)
    assert doc["sql"] == {
        "fixture": "shop.sql",
        "dialect": "sqlite",
        "ordered": "never",
        "empty_result": "text",
        "max_rows": 50,
        "max_steps": 200_000,
    }
    again = taskspec.parse_spec(doc, "api", fixtures_dir=fixtures)
    assert again == task and again.spec_hash == task.spec_hash and taskspec.document(again) == doc
    led = Ledger(":memory:")
    led.save_task("shop-sql", doc, task.spec_hash, "api", "ops")
    assert taskspec.parse_spec(led.saved_task("shop-sql")["spec"], "api", fixtures_dir=fixtures) == task
    # The hash covers the options and the fixture's text, not where the file is.
    assert taskspec.parse_spec(spec(max_rows=51, ordered="never"), "api", fixtures_dir=fixtures).spec_hash != task.spec_hash
    (fixtures / "moved").mkdir()
    (fixtures / "moved" / "shop.sql").write_text(SHOP, encoding="utf-8")
    moved = taskspec.parse_spec(spec(fixture="moved/shop.sql", max_rows=50, ordered="never"), "api", fixtures_dir=fixtures)
    assert moved.spec_hash == task.spec_hash and moved.sql.sandbox is task.sql.sandbox
    (fixtures / "shop.sql").write_text(SHOP + "INSERT INTO customers VALUES (5, 'Eve', 'Rome');", encoding="utf-8")
    edited = taskspec.parse_spec(doc, "api", fixtures_dir=fixtures)
    assert edited.spec_hash != task.spec_hash and edited != task
    assert edited.agree(COUNT, "SELECT 5") and task.agree(COUNT, "SELECT 4")
    # No other task's spec holds the key, so their hashes are what they were before the format existed.
    assert (
        all("sql" not in t.spec() for t in tasks.BUILTIN.values())
        and tasks.BUILTIN["default"].spec_hash == tasks.Task("x").spec_hash
    )
    # extends merges the sql options by key.
    base = tasks.Task("base-sql", sql=task.sql, source="api")
    tasks.register(base)
    child = taskspec.parse_spec({"name": "child", "extends": "base-sql", "sql": {"max_rows": 7}}, "api", fixtures_dir=fixtures)
    assert (child.sql.max_rows, child.sql.ordered, child.sql.fixture) == (7, "never", "shop.sql")
    # The default directory is RATCHET_FIXTURES_DIR.
    os.environ["RATCHET_FIXTURES_DIR"] = str(fixtures)
    try:
        assert taskspec.parse_spec(spec(), "api").sql.fixture == "shop.sql" and Settings().fixtures_dir == str(fixtures)
    finally:
        del os.environ["RATCHET_FIXTURES_DIR"]


@pytest.mark.req("FR-2.56", "FR-2.15")
def test_fixture_path_outside_dir_rejected(fixtures, tmp_path):
    secret = tmp_path / "secret.sql"
    secret.write_text(SHOP, encoding="utf-8")
    os.symlink(secret, fixtures / "link.sql")
    for path, message in (
        ("../secret.sql", "is outside RATCHET_FIXTURES_DIR"),
        (str(secret), "use a path relative to RATCHET_FIXTURES_DIR"),
        ("link.sql", "is outside RATCHET_FIXTURES_DIR"),
        ("sub/../../secret.sql", "is outside RATCHET_FIXTURES_DIR"),
    ):
        with pytest.raises(SpecError) as caught:
            taskspec.parse_spec(spec(fixture=path), "api", fixtures_dir=fixtures)
        assert len(caught.value.errors) == 1 and caught.value.errors[0].startswith("sql.fixture: "), path
        assert message in caught.value.errors[0], caught.value.errors
    with pytest.raises(ValueError, match="outside RATCHET_FIXTURES_DIR"):
        sqlcheck.SqlSpec.load("../secret.sql", fixtures)
    assert taskspec.resolve_fixture("shop.sql", fixtures) == sqlcheck.resolve_fixture("shop.sql", fixtures)


@pytest.mark.req("FR-2.56", "FR-2.15")
@needs_sql
def test_sql_spec_errors_have_paths(fixtures):
    (fixtures / "broken.sql").write_text("CREATE TABLE t (x;", encoding="utf-8")
    (fixtures / "attach.sql").write_text("CREATE TABLE t (x); ATTACH 'x.db' AS x;", encoding="utf-8")
    (fixtures / "pragma.sql").write_text("PRAGMA case_sensitive_like = ON; CREATE TABLE t (x);", encoding="utf-8")
    (fixtures / "random.sql").write_text("CREATE TABLE t (x); INSERT INTO t VALUES (random());", encoding="utf-8")
    (fixtures / "empty.sql").write_text("-- nothing here", encoding="utf-8")
    (fixtures / "latin1.sql").write_bytes("CREATE TABLE t (x); INSERT INTO t VALUES ('caf\xe9');".encode("latin-1"))
    (fixtures / "big.sql").write_bytes(b"-" * (sqlcheck.MAX_FIXTURE_BYTES + 1))
    (fixtures / "dir.sql").mkdir()
    # The database a fixture builds must not depend on when, or how often, it was loaded (the same fixture_sha256 and
    # spec_hash every day), also through a column's DEFAULT, which the load authorizer never sees.
    (fixtures / "clock.sql").write_text(
        "CREATE TABLE t (x, y); INSERT INTO t VALUES (date('now'), unixepoch());", encoding="utf-8"
    )
    (fixtures / "default.sql").write_text(
        "CREATE TABLE t (x, at DEFAULT CURRENT_TIMESTAMP); INSERT INTO t (x) VALUES (1);", encoding="utf-8"
    )
    (fixtures / "uuid.sql").write_text(
        "CREATE TABLE t (x, id DEFAULT (lower(hex(randomblob(16))))); INSERT INTO t (x) VALUES (1);", encoding="utf-8"
    )
    (fixtures / "view.sql").write_text(
        "CREATE TABLE t (x); CREATE VIEW recent AS SELECT * FROM t WHERE x > julianday() - 30;", encoding="utf-8"
    )
    if hasattr(os, "mkfifo"):
        os.mkfifo(fixtures / "pipe.sql")  # opening it must not block
    doc = {
        "tasks": [
            {"name": "a", "format": "sql"},
            {"name": "b", "format": "json", "sql": {"fixture": "shop.sql"}},
            {"name": "c", "format": "sql", "fields": {"x": {"type": "label"}}, "sql": {"fixture": "shop.sql"}},
            {"name": "d", "sql": {"fixture": "", "dialect": "mysql", "ordered": "maybe", "max_rows": 0, "max_steps": 10, "x": 1}},
            {"name": "e", "sql": {"dialect": "postgres", "fixture": "shop.sql", "max_rows": 5}},
            {"name": "f", "sql": {"ordered": "auto"}},
            {"name": "g", "sql": ["shop.sql"]},
            {"name": "h", "sql": {"fixture": "missing.sql"}},
            {"name": "i", "sql": {"fixture": "broken.sql"}},
            {"name": "j", "sql": {"fixture": "attach.sql"}},
            {"name": "k", "sql": {"fixture": "pragma.sql"}},
            {"name": "l", "sql": {"fixture": "random.sql"}},
            {"name": "m", "sql": {"fixture": "empty.sql"}},
            {"name": "n", "sql": {"fixture": "latin1.sql"}},
            {"name": "o", "sql": {"fixture": "big.sql"}},
            {"name": "p", "sql": {"fixture": "dir.sql"}},
            {"name": "q", "format": "tool_calls", "sql": {"fixture": "shop.sql"}},
            {"name": "r", "sql": {"fixture": "clock.sql"}},
            {"name": "s", "sql": {"fixture": "default.sql"}},
            {"name": "t", "sql": {"fixture": "uuid.sql"}},
            {"name": "u", "sql": {"fixture": "view.sql"}},
        ]
    }
    with pytest.raises(SpecError) as caught:
        taskspec.parse_document(doc, fixtures_dir=fixtures)
    mb = sqlcheck.MAX_FIXTURE_BYTES >> 20
    clock = (
        "reads the clock while it loads (a date function given 'now' or no time value, or a CURRENT_TIMESTAMP default):"
        " its database would change from day to day"
    )
    assert caught.value.errors == [
        "tasks[0].sql: missing; a sql task needs sql.fixture (a SQL script in RATCHET_FIXTURES_DIR) or sql.dialect 'postgres'",
        "tasks[1].sql: only a task with format 'sql' takes sql options",
        "tasks[2].fields: a sql task has no fields: two answers agree when their queries return the same result",
        "tasks[3].sql.fixture: must be a path relative to RATCHET_FIXTURES_DIR",
        "tasks[3].sql.dialect: must be one of 'sqlite', 'postgres'",
        "tasks[3].sql.ordered: must be one of 'auto', 'always', 'never'",
        "tasks[3].sql.max_rows: must be a whole number from 1 to 10,000",
        "tasks[3].sql.max_steps: must be a whole number from 1,000 to 10,000,000",
        "tasks[3].sql.x: unknown key; use fixture, dialect, ordered, empty_result, max_rows, max_steps",
        "tasks[4].sql.fixture: does not apply to dialect 'postgres', which runs no SQL",
        "tasks[4].sql.max_rows: does not apply to dialect 'postgres', which runs no SQL",
        "tasks[5].sql.fixture: missing; a sqlite task runs its answers on a SQL script in RATCHET_FIXTURES_DIR",
        "tasks[6].sql: must be an object with fixture, dialect, ordered, empty_result, max_rows, max_steps",
        "tasks[7].sql.fixture: fixture 'missing.sql' cannot be read: No such file or directory",
        "tasks[8].sql.fixture: fixture 'broken.sql' cannot be loaded: near \";\": syntax error",
        "tasks[9].sql.fixture: fixture 'attach.sql' cannot be loaded: not authorized",
        "tasks[10].sql.fixture: fixture 'pragma.sql' cannot be loaded: not authorized",
        "tasks[11].sql.fixture: fixture 'random.sql' cannot be loaded: not authorized to use function: random",
        "tasks[12].sql.fixture: fixture 'empty.sql' creates no table",
        "tasks[13].sql.fixture: fixture 'latin1.sql' is not UTF-8 text",
        f"tasks[14].sql.fixture: fixture 'big.sql' is larger than {mb} MB",
        "tasks[15].sql.fixture: fixture 'dir.sql' is not a regular file",
        "tasks[16].format: 'tool_calls' is reserved for a later release",
        f"tasks[17].sql.fixture: fixture 'clock.sql' {clock}",
        f"tasks[18].sql.fixture: fixture 'default.sql' {clock}",
        "tasks[19].sql.fixture: fixture 'uuid.sql' calls random() or randomblob() while it loads (a column's DEFAULT): its"
        " database would change on every load",
        "tasks[20].sql.fixture: fixture 'view.sql': view 'recent' reads the clock, so no query on it could count",
    ]
    if hasattr(os, "mkfifo"):
        with pytest.raises(SpecError, match="fixture 'pipe.sql' is not a regular file"):
            taskspec.parse_spec(spec(fixture="pipe.sql"), "api", fixtures_dir=fixtures)
    # A task built in code is checked the same way.
    with pytest.raises(ValueError, match="only a task with format 'sql' takes sql options"):
        tasks.Task("t", format="json", sql=sqlcheck.SqlSpec(dialect="postgres"))
    with pytest.raises(ValueError, match="a sql task has no fields"):
        tasks.Task("t", fields=(tasks.FieldSpec("x"),), format="sql", sql=sqlcheck.SqlSpec(dialect="postgres"))
    with pytest.raises(ValueError, match="the fixture is not loaded"):
        sqlcheck.SqlSpec(fixture="shop.sql")
    with pytest.raises(ValueError, match="max_rows does not apply to dialect 'postgres'"):
        sqlcheck.SqlSpec(dialect="postgres", max_rows=5)
    with pytest.raises(ValueError, match="max_steps must be a whole number"):
        sqlcheck.SqlSpec.from_script(SHOP, max_steps=True)


@pytest.mark.req("FR-2.56")
@needs_sql
def test_fixture_load_limits(monkeypatch):
    dump = "PRAGMA foreign_keys=OFF;\nBEGIN TRANSACTION;\nCREATE TABLE t (x);\nINSERT INTO t VALUES (1);\nCOMMIT;\n"
    assert sqlcheck.Sandbox(dump).run("SELECT x FROM t", 10, 1000) != sqlcheck.SQL_ERROR  # a `.dump` loads
    assert sqlcheck.Sandbox("CREATE TABLE t (x); BEGIN; INSERT INTO t VALUES (1);").run("SELECT x FROM t", 10, 1000)
    # A default that would read the clock is fine while no row takes it (a `.dump` gives every value).
    stamped = "CREATE TABLE t (x, at DEFAULT CURRENT_TIMESTAMP, d DEFAULT (date('now'))); INSERT INTO t VALUES (1, 'a', 'b');"
    assert isinstance(sqlcheck.Sandbox(stamped).run("SELECT at, date(d, '+1 day') FROM t", 10, 1000), sqlcheck.Result)
    # A value over VALUE_BYTES could never be read by a query, so it fails the load.
    with pytest.raises(ValueError, match="cannot be loaded: string or blob too big"):
        sqlcheck.Sandbox("CREATE TABLE t (s); INSERT INTO t VALUES ('" + "x" * (sqlcheck.VALUE_BYTES + 1) + "');")
    grow = "CREATE TABLE t (s); WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r WHERE n < 5000) "
    monkeypatch.setattr(sqlcheck, "MAX_FIXTURE_DB_BYTES", 1024 * 1024)
    with pytest.raises(ValueError, match="larger than 1 MB once loaded"):
        sqlcheck.Sandbox(grow + "INSERT INTO t SELECT printf('%.1000c', 'x') FROM r;")
    monkeypatch.setattr(sqlcheck, "LOAD_SECONDS", 0.0)
    with pytest.raises(ValueError, match="took longer than 0 s to load"):
        sqlcheck.Sandbox(
            "CREATE TABLE t (n); WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) INSERT INTO t SELECT n FROM r;"
        )


@pytest.mark.req("FR-2.56")
def test_sql_execution_needs_python_311(fixtures, monkeypatch):
    """Without `Connection.setlimit` (Python 3.10) one step can copy a value of up to 1 GB, which no progress handler
    sees, so a sqlite fixture is refused with a reason that says what to do; dialect postgres still works."""
    assert sqlcheck.CAN_LIMIT is (sys.version_info >= (3, 11))
    monkeypatch.setattr(sqlcheck, "CAN_LIMIT", False)
    # No sandbox another test left alive (a failed test's traceback can hold one) may answer for shop.sql.
    monkeypatch.setattr(sqlcheck, "_SANDBOXES", sqlcheck.weakref.WeakValueDictionary())
    with pytest.raises(ValueError, match="needs Python 3.11 or later"):
        sqlcheck.Sandbox("CREATE TABLE t (x); -- a script no other test loads")
    with pytest.raises(SpecError) as caught:
        taskspec.parse_spec(spec(), "api", fixtures_dir=fixtures)
    assert caught.value.errors == [
        "sql.fixture: fixture 'shop.sql': running SQL answers needs Python 3.11 or later, whose sqlite3 caps the size of a"
        " value (Connection.setlimit); use dialect 'postgres' to compare query texts only"
    ]
    assert taskspec.parse_spec({"name": "pg", "sql": {"dialect": "postgres"}}, "api").agree("SELECT 1", "select 1;")


@pytest.mark.req("FR-2.56")
def test_sql_postgres_dialect_compares_text_only():
    t = tasks.Task("pg", sql=sqlcheck.SqlSpec(dialect="postgres"), source="api")
    assert t.sql.document() == {"dialect": "postgres"} and t.spec()["sql"] == {"dialect": "postgres"}
    assert t.agree("SELECT COUNT(*) FROM orders", "select count(*)\nfrom ORDERS;")
    assert not t.agree("SELECT COUNT(*) FROM orders", "SELECT COUNT(id) FROM orders")
    assert t.valid("SELECT now()::date") and t.valid("SELECT date('now')")  # nothing runs, so nothing reads the clock
    assert t.check("SELECT 1; SELECT 2") == "not one query" and t.canonical("SELECT  1;") == "select 1"
    assert not t.verifiable and t.free_text
    parsed = taskspec.parse_spec({"name": "pg", "sql": {"dialect": "postgres"}}, "api")
    assert parsed == t and taskspec.parse_spec(taskspec.document(parsed), "api") == t


# ---------- the cascade, the golden eval, the dataset, the admin API ----------
def _router(teacher, student=None, threshold=0.5):
    led = Ledger(":memory:")
    s = Settings(teacher_model=OPEN_TEACHER, samples=3, audit_rate=0.0, redact_pii=True, teacher_reasoning_tokens=0)
    models = {}
    if student is not None:
        models[_version(led, "live", {"threshold": threshold})] = student
    return led, Ratchet(s, led, teacher, lambda v: (models[v.id], v.adapter_uri), seed=1)


def _ask(text="How many customers do we have?"):
    return [{"role": "user", "content": text}]


@pytest.mark.req("FR-2.56", "FR-2.13", "INV-16")
@needs_sql
def test_sql_task_in_the_cascade():
    task = shop()
    tasks.register(task, "api")
    # Three different texts of one query: they agree, so the student answers, without its fence.
    samples = [COUNT, "select count(id) from customers;", f"```sql\n{COUNT}\n```"]
    led, r = _router(RecordingProvider("SELECT 4"), student=RecordingProvider(samples), threshold=0.9)
    out = r.handle(_ask(), "shop-sql")
    assert (out.served_by, out.confidence > 0.9) == ("student", True) and "```" not in out.text
    # Samples that disagree on the result, or fail, escalate; the teacher's answer is stored without its fence.
    disagree = RecordingProvider([COUNT, "SELECT 5", "SELECT * FROM nope"])
    led, r = _router(RecordingProvider(f"```sql\n{COUNT};\n```"), student=disagree, threshold=0.9)
    out = r.handle(_ask(), "shop-sql")
    assert (out.served_by, out.route) == ("teacher", "confidence") and out.text == f"{COUNT};"
    assert [ex["completion"] for ex in led.examples()] == [f"{COUNT};"]
    # A teacher answer that fails on the fixture is served, but never stored (INV-16); the reason never quotes it.
    for answer, reason in (
        ("SELECT random()", "sql denied"),
        ("SELECT * FROM nope", "sql error"),
        ("SELECT date('now')", "reads the clock"),
        ("SELECT COUNT(*) FROM orders WHERE julianday() - julianday(placed) < 30", "reads the clock"),
        ('SELECT id FROM orders WHERE placed > date("now", "-30 days")', "reads the clock"),
    ):
        led, r = _router(RecordingProvider(answer))
        assert r.handle(_ask(), "shop-sql").text == answer
        assert led.examples() == [] and [e["detail"] for e in led.events() if e["kind"] == "skip_example"] == [
            f"shop-sql: {reason}"
        ]


@pytest.mark.req("FR-2.56", "INV-6")
@needs_sql
def test_sql_golden_eval_and_dataset():
    task = shop()
    tasks.register(task, "api")
    refs = [COUNT, "SELECT * FROM nope", "select count(id) from customers", "SELECT random()", COUNT, COUNT]
    golden = [
        {"task": "shop-sql", "messages": [{"role": "user", "content": f"q{i}"}, {"role": "assistant", "content": ref}]}
        for i, ref in enumerate(refs)
    ]

    def student(prompt, n, params=None):
        return [Completion("SELECT COUNT(name) FROM customers", "m", 10, 4, 1.0, [math.log(0.95)] * 4) for _ in range(n)]

    res = cascade_eval(student, golden, samples=3)
    assert res["golden_invalid_refs"] == 2 and res["golden_n"] == 4 and res["golden_agreement"] == 1.0
    led = Ledger(":memory:", golden_fraction=0.0)
    for i, ref in enumerate(refs):
        led.add_example("shop-sql", _ask(f"q{i}"), ref, OPEN_TEACHER, "license", "https://example.com/license")
    counts: dict[str, int] = {}
    assert len(dataset.rows(led, "train", counts=counts)) == 4 and counts == {"skipped_invalid": 2}


@pytest.mark.req("FR-2.56", "FR-2.15")
@needs_sql
def test_sql_task_through_the_admin_api_and_tasks_file(gw_env, fixtures, tmp_path):
    gw = gw_env(RATCHET_FIXTURES_DIR=str(fixtures))
    c = TestClient(gw.app)
    url = "/ratchet/tasks/shop-sql"
    first = c.put(url, json=spec()).json()
    assert first["created"] and first["task"]["verifiable"] and first["task"]["sql"]["fixture"] == "shop.sql"
    assert c.get(url).json()["sql"]["max_rows"] == 1000
    assert c.put(url, json=spec()).json()["changed"] is False
    # The same body after the fixture changed is a new spec: the fixture is read again and its hash changes.
    (fixtures / "shop.sql").write_text(SHOP + "INSERT INTO customers VALUES (5, 'Eve', 'Rome');", encoding="utf-8")
    second = c.put(url, json=spec()).json()
    assert second["changed"] and second["spec_hash"] != first["spec_hash"]
    events = [e["detail"] for e in gw.rt.ledger.events() if e["kind"] == "task_changed"]
    assert events == [f"shop-sql: spec {first['spec_hash']} -> {second['spec_hash']} by mock-admin"]
    bad = c.put(url, json=spec(fixture="../etc/passwd"))
    assert bad.status_code == 422 and bad.json()["errors"] == [
        "sql.fixture: fixture '../etc/passwd' is outside RATCHET_FIXTURES_DIR"
    ]
    valid = c.post("/ratchet/tasks/validate", json={"tasks": [{**spec(), "name": "other-sql"}]}).json()
    assert valid["valid"] and valid["tasks"][0]["name"] == "other-sql"
    msgs = [{"role": "user", "content": "How many customers?"}]
    assert c.post("/v1/chat/completions", json={"messages": msgs}, headers={"X-Ratchet-Task": "shop-sql"}).status_code == 200
    # A tasks file with a sql task loads at startup; a fixture it cannot find stops startup with the error's path.
    path = tmp_path / "tasks.json"
    path.write_text(json.dumps({"tasks": [{**spec(), "name": "file-sql"}]}), encoding="utf-8")
    gw = gw_env(RATCHET_FIXTURES_DIR=str(fixtures), RATCHET_TASKS_FILE=str(path))
    assert tasks.lookup("file-sql").sql.fixture == "shop.sql"
    path.write_text(json.dumps({"tasks": [{**spec(fixture="gone.sql"), "name": "file-sql"}]}), encoding="utf-8")
    with pytest.raises(SpecError) as caught:
        gw.Runtime(Settings(mode="mock", tasks_file=str(path), fixtures_dir=str(fixtures), mock_warmup_days=0))
    assert caught.value.errors == ["tasks[0].sql.fixture: fixture 'gone.sql' cannot be read: No such file or directory"]
