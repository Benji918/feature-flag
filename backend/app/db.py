"""SQLite access for the DataChess backend (stdlib only).

Same tables as backend/schema.sql. The DB path is read lazily from
DATACHESS_DB_PATH (default: <repo-root>/datachess.db) so tests can point at
a temp file per test via monkeypatched env. Every connection ensures the
schema exists (CREATE IF NOT EXISTS) and opts into FK enforcement, because
SQLite stores neither for us.
"""

import os
import pathlib
import sqlite3

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
SCHEMA_PATH = pathlib.Path(__file__).resolve().parent.parent / "schema.sql"


def db_path() -> pathlib.Path:
    return pathlib.Path(os.environ.get("DATACHESS_DB_PATH", REPO_ROOT / "datachess.db"))


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    # Per-connection: not persisted by the schema file. Without this, FK
    # clauses (tenancy boundary, audit RESTRICT) are silently unenforced.
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    return conn
