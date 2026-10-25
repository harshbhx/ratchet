// A D1 binding for tests: the real migration files run on an in-memory SQLite database (node:sqlite), behind the part of D1's API
// the Worker uses (prepare, bind, run, all, first, batch). So the tests run the Worker's actual SQL, UPSERTs and RETURNING included.
import { readdirSync, readFileSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";

const MIGRATIONS = new URL("../migrations/", import.meta.url);

/** D1 binds numbered parameters (?1, ?2, a number used twice is one value); node:sqlite binds plain ? in order. */
function positional(sql, args) {
  if (!/\?\d/.test(sql)) return [sql, args];
  const values = [];
  const text = sql.replace(/\?(\d+)/g, (_, n) => {
    const i = Number(n) - 1;
    if (i >= args.length) throw new Error(`D1_ERROR: parameter ?${n} has no bound value`);
    values.push(args[i]);
    return "?";
  });
  return [text, values];
}

export function fakeD1() {
  const db = new DatabaseSync(":memory:");
  for (const name of readdirSync(MIGRATIONS).filter((n) => n.endsWith(".sql")).sort()) {
    db.exec(readFileSync(new URL(name, MIGRATIONS), "utf8"));
  }
  const exec = (sql, args) => {
    const [text, values] = positional(sql, args);
    return [db.prepare(text), values];
  };
  const statement = (sql, args = []) => ({
    bind: (...values) => statement(sql, values),
    run: async () => {
      const [s, v] = exec(sql, args);
      return { success: true, meta: { changes: Number(s.run(...v).changes) } };
    },
    all: async () => {
      const [s, v] = exec(sql, args);
      return { success: true, results: s.all(...v) };
    },
    first: async (column) => {
      const [s, v] = exec(sql, args);
      const row = s.get(...v);
      if (row === undefined) return null;
      return column === undefined ? row : (row[column] ?? null);
    },
  });
  return {
    sqlite: db,
    prepare: (sql) => statement(sql),
    batch: async (statements) => {
      db.exec("BEGIN");
      try {
        const out = [];
        for (const s of statements) out.push(await s.run());
        db.exec("COMMIT");
        return out;
      } catch (err) {
        db.exec("ROLLBACK");
        throw err;
      }
    },
    /** Every table's rows, for checks on what was stored. */
    dump() {
      const tables = db.prepare("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name").all().map((r) => r.name);
      return Object.fromEntries(tables.map((t) => [t, db.prepare(`SELECT * FROM "${t}"`).all()]));
    },
  };
}

/** A D1 binding whose every call fails, as when D1 is down. */
export function brokenD1() {
  const fail = async () => {
    throw new Error("D1_ERROR: database unavailable");
  };
  const statement = { bind: () => statement, run: fail, all: fail, first: fail };
  return { prepare: () => statement, batch: fail };
}

/** A D1 binding whose calls never answer. */
export function hangingD1() {
  const never = () => new Promise(() => {});
  const statement = { bind: () => statement, run: never, all: never, first: never };
  return { prepare: () => statement, batch: never };
}
