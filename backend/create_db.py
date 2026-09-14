"""Create the DataChess SQLite database in one step.

Usage (from the repo root):
    python3 backend/create_db.py [path/to/datachess.db]

With no argument, creates <repo-root>/datachess.db.

Starting from an empty database (no file, or a 0-byte file), this applies
backend/schema.sql once and leaves all four tables from
datachess-sprint-requirements2.md Section 3 in place:
users, projects, feature_flags, audit_log_entries.

Stdlib only (sqlite3). No endpoints, no query layer, no seed data.
"""

import pathlib
import sqlite3
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SCHEMA_PATH = pathlib.Path(__file__).resolve().parent / "schema.sql"
DEFAULT_DB_PATH = REPO_ROOT / "datachess.db"


def main(db_path: pathlib.Path) -> None:
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.executescript(schema_sql)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    finally:
        conn.close()
    expected = {"users", "projects", "feature_flags", "audit_log_entries"}
    missing = expected - tables
    if missing:
        raise SystemExit(f"schema incomplete, missing tables: {sorted(missing)}")
    print(f"created schema at {db_path}: {sorted(expected)}")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else str(DEFAULT_DB_PATH)
    main(pathlib.Path(arg))
