"""Round-trip verification for the table-schema sprint (stdlib only).

Checks the ticket's Outcome claims against a FRESH temp database built from
backend/schema.sql -- not just table names:

  01  empty DB -> complete schema (tables + fields per Section 3)
  02  user row: email, password hash, is_admin defaults false, created_at
  03  project belongs to a user, stores ONLY the API-key hash (raw never stored)
  04  flag belongs to a project, holds key/description/enabled/rollout/default/timestamps
  05  audit row references BOTH project and flag, with actor/field/old/new/timestamp
  06  same flag key across projects OK, twice in one project rejected
  07  audit history is not silently deleted (RESTRICT on audit FKs)
  08  foreign keys are actually enforced on this connection

Usage (from the repo root):
    python3 backend/verify_schema.py [path/to/schema.sql]

Exit 0 + "ALL CHECKS PASS" on success, nonzero with the failing check first.
"""

import hashlib
import pathlib
import secrets
import sqlite3
import sys
import tempfile

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SCHEMA_PATH = pathlib.Path(__file__).resolve().parent / "schema.sql"

EXPECTED_COLUMNS = {
    "users": ["id", "email", "hashed_password", "is_admin", "created_at"],
    "projects": ["id", "user_id", "name", "repo_url", "api_key_hash", "created_at"],
    "feature_flags": [
        "id",
        "project_id",
        "key",
        "description",
        "enabled",
        "rollout_percentage",
        "default_value",
        "created_at",
        "definition_updated_at",
    ],
    "audit_log_entries": [
        "id",
        "project_id",
        "flag_id",
        "actor",
        "field_changed",
        "old_value",
        "new_value",
        "timestamp",
    ],
}


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not cond else ""))
    if not cond:
        raise SystemExit(f"FAILED: {name}" + (f" ({detail})" if detail else ""))


def main(schema_path=SCHEMA_PATH):
    with tempfile.TemporaryDirectory() as tmp:
        db_path = pathlib.Path(tmp) / "verify.db"
        conn = sqlite3.connect(db_path)
        # FK enforcement is per-connection in SQLite: a PRAGMA inside
        # schema.sql would NOT persist, so it must be set here (and in every
        # future connection), not in the schema file.
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.executescript(schema_path.read_text(encoding="utf-8"))

        tables = {
            r[0]: [c[1] for c in conn.execute(f"PRAGMA table_info({r[0]})")]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name != 'sqlite_sequence'"
            )
        }
        check("01 tables exist", set(tables) == set(EXPECTED_COLUMNS), f"got {sorted(tables)}")
        for t, cols in EXPECTED_COLUMNS.items():
            check(f"01 {t} fields", tables[t] == cols, f"got {tables[t]}")

        cur = conn.cursor()
        # 02: user round-trip
        cur.execute(
            "INSERT INTO users (email, hashed_password) VALUES (?, ?)",
            ("owner@example.com", "argon2:hashed-pw"),
        )
        uid = cur.lastrowid
        email, pw, admin, created = cur.execute(
            "SELECT email, hashed_password, is_admin, created_at FROM users WHERE id = ?", (uid,)
        ).fetchone()
        check("02 user write/read-back", (email, pw, admin) == ("owner@example.com", "argon2:hashed-pw", 0))
        check("02 is_admin defaults false", admin == 0, f"got {admin}")
        check("02 user created_at set", created is not None)

        # 03: project belongs to user, hash only
        raw_key = secrets.token_urlsafe(32)
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        cur.execute(
            "INSERT INTO projects (user_id, name, api_key_hash) VALUES (?, ?, ?)",
            (uid, "my-node-app", key_hash),
        )
        pid1 = cur.lastrowid
        owner, stored = cur.execute(
            "SELECT user_id, api_key_hash FROM projects WHERE id = ?", (pid1,)
        ).fetchone()
        check("03 project belongs to user", owner == uid)
        check("03 only hash stored", stored == key_hash and len(stored) == 64)
        project_cols = [c[1] for c in conn.execute("PRAGMA table_info(projects)")]
        check(
            "03 no raw-key column",
            "api_key_hash" in project_cols
            and not (set(project_cols) & {"api_key", "raw_key", "raw_api_key"}),
            f"got {project_cols}",
        )
        check("03 raw key in no row", raw_key not in str(list(conn.execute("SELECT * FROM projects"))))
        cur.execute(
            "INSERT INTO projects (user_id, name, api_key_hash) VALUES (?, ?, ?)",
            (uid, "second-proj", hashlib.sha256(b"x").hexdigest()),
        )
        pid2 = cur.lastrowid

        # 04: flag round-trip
        cur.execute(
            'INSERT INTO feature_flags (project_id, "key", description, default_value)'
            " VALUES (?, ?, ?, ?)",
            (pid1, "dark_mode", "Dark theme toggle", 1),
        )
        fid1 = cur.lastrowid
        flag = cur.execute(
            'SELECT project_id, "key", description, enabled, rollout_percentage,'
            " default_value, created_at, definition_updated_at"
            " FROM feature_flags WHERE id = ?",
            (fid1,),
        ).fetchone()
        check("04 flag belongs to project", flag[0] == pid1)
        check(
            "04 flag fields",
            flag[1] == "dark_mode" and flag[2] == "Dark theme toggle" and flag[5] == 1,
            f"got {flag}",
        )
        check("04 flag timestamps set", flag[6] is not None and flag[7] is not None)

        # 05: audit references both
        cur.execute(
            "INSERT INTO audit_log_entries (project_id, flag_id, actor, field_changed,"
            " old_value, new_value) VALUES (?, ?, ?, ?, ?, ?)",
            (pid1, fid1, "owner@example.com", "enabled", "0", "1"),
        )
        audit = cur.execute(
            'SELECT project_id, flag_id, actor, field_changed, old_value, new_value,'
            ' "timestamp" FROM audit_log_entries WHERE id = ?',
            (cur.lastrowid,),
        ).fetchone()
        check("05 audit references project+flag", (audit[0], audit[1]) == (pid1, fid1))
        check("05 audit fields", audit[2:6] == ("owner@example.com", "enabled", "0", "1"))
        check("05 audit timestamp set", audit[6] is not None)

        # 06: per-project key uniqueness
        cur.execute(
            'INSERT INTO feature_flags (project_id, "key", description, default_value)'
            " VALUES (?, ?, ?, ?)",
            (pid2, "dark_mode", "same key, other project", 0),
        )
        try:
            cur.execute(
                'INSERT INTO feature_flags (project_id, "key", description, default_value)'
                " VALUES (?, ?, ?, ?)",
                (pid1, "dark_mode", "dup", 0),
            )
            check("06 duplicate key in same project rejected", False, "duplicate allowed")
        except sqlite3.IntegrityError:
            print("[PASS] 06 duplicate key in same project rejected")
        print("[PASS] 06 same key in different projects allowed")

        # 07: history must not cascade away with its flag
        try:
            cur.execute("DELETE FROM feature_flags WHERE id = ?", (fid1,))
            check("07 delete flag with history blocked", False, "audit row would die silently")
        except sqlite3.IntegrityError:
            print("[PASS] 07 delete flag with history blocked (RESTRICT)")
        surviving = cur.execute(
            "SELECT COUNT(*) FROM audit_log_entries WHERE flag_id = ?", (fid1,)
        ).fetchone()[0]
        check("07 audit row survives", surviving == 1)

        # 08: FKs enforced (proves per-connection PRAGMA is on)
        try:
            cur.execute(
                "INSERT INTO projects (user_id, name, api_key_hash) VALUES (?, ?, ?)",
                (999999, "orphan", "x"),
            )
            check("08 orphan project rejected", False, "FKs not enforced")
        except sqlite3.IntegrityError:
            print("[PASS] 08 orphan project rejected (FKs enforced)")

        conn.close()
    print("ALL CHECKS PASS")


if __name__ == "__main__":
    main(pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else SCHEMA_PATH)
