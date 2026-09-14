"""Tests for the table-schema sprint (stdlib only, no third-party deps).

Each test builds a FRESH database from backend/schema.sql and exercises one
of the ticket's acceptance criteria as an assertion:

  01  empty file -> complete schema (tables + fields per Section 3)
  02  user write/read-back, is_admin defaults false, created_at set
  03  project belongs to a user, stores ONLY the API-key hash
  04  flag belongs to a project with all required fields
  05  audit row references BOTH project and flag
  06  same key across projects OK, duplicate within one project rejected
  07  audit history survives (RESTRICT blocks deleting a flag with history)
  08  FK clauses reject orphans on a connection that opts in via
        PRAGMA foreign_keys = ON (per-connection; the schema file cannot
        enable enforcement itself, so unpragmad connections stay unchecked)
  +   rollout_percentage range CHECK enforced at the storage layer

Run from the repo root:
    python3 -m pytest backend/app/tests/test_schema.py -v
    # or without pytest:
    python3 -m unittest backend.test_schema -v
"""

import hashlib
import pathlib
import secrets
import sqlite3
import tempfile
import unittest

SCHEMA_PATH = pathlib.Path(__file__).resolve().parents[2] / "schema.sql"

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


class SchemaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conn = sqlite3.connect(pathlib.Path(self.tmp.name) / "test.db")
        self.addCleanup(self.conn.close)
        # FK enforcement is per-connection in SQLite: schema.sql cannot turn
        # it on, so every test connection must (as must all future code).
        self.conn.execute("PRAGMA foreign_keys = ON;")
        self.conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.cur = self.conn.cursor()

    def test_01_tables_and_fields(self):
        tables = {
            row[0]: [c[1] for c in self.conn.execute(f"PRAGMA table_info({row[0]})")]
            for row in self.conn.execute(
                "SELECT name FROM sqlite_master"
                " WHERE type = 'table' AND name != 'sqlite_sequence'"
            )
        }
        self.assertEqual(set(tables), set(EXPECTED_COLUMNS))
        for table, cols in EXPECTED_COLUMNS.items():
            self.assertEqual(tables[table], cols, f"fields of {table}")

    def _make_user(self, email="owner@example.com"):
        self.cur.execute(
            "INSERT INTO users (email, hashed_password) VALUES (?, ?)",
            (email, "argon2:hashed-pw"),
        )
        return self.cur.lastrowid

    def _make_project(self, user_id, name="my-node-app", raw_key=None):
        raw_key = raw_key or secrets.token_urlsafe(32)
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        self.cur.execute(
            "INSERT INTO projects (user_id, name, api_key_hash) VALUES (?, ?, ?)",
            (user_id, name, key_hash),
        )
        return self.cur.lastrowid, raw_key, key_hash

    def _make_flag(self, project_id, key="dark_mode"):
        self.cur.execute(
            'INSERT INTO feature_flags (project_id, "key", description, default_value)'
            " VALUES (?, ?, ?, ?)",
            (project_id, key, "Dark theme toggle", 1),
        )
        return self.cur.lastrowid

    def test_02_user_roundtrip_and_defaults(self):
        uid = self._make_user()
        email, pw, is_admin, created = self.cur.execute(
            "SELECT email, hashed_password, is_admin, created_at FROM users WHERE id = ?",
            (uid,),
        ).fetchone()
        self.assertEqual(email, "owner@example.com")
        self.assertEqual(pw, "argon2:hashed-pw")
        self.assertEqual(is_admin, 0)
        self.assertIsNotNone(created)

    def test_03_project_belongs_to_user_hash_only(self):
        uid = self._make_user()
        pid, raw_key, key_hash = self._make_project(uid)
        owner, stored = self.cur.execute(
            "SELECT user_id, api_key_hash FROM projects WHERE id = ?", (pid,)
        ).fetchone()
        self.assertEqual(owner, uid)
        self.assertEqual(stored, key_hash)
        self.assertEqual(len(stored), 64)
        project_cols = [c[1] for c in self.conn.execute("PRAGMA table_info(projects)")]
        self.assertNotIn("api_key", project_cols)
        self.assertNotIn("raw_key", project_cols)
        rows = list(self.conn.execute("SELECT * FROM projects"))
        self.assertNotIn(raw_key, str(rows))

    def test_key_hash_unique_at_storage(self):
        # Project-sprint AC04: one key identifies exactly one project,
        # enforced by the storage layer -- a duplicate hash inserted directly
        # (no application code involved) must be refused.
        uid = self._make_user()
        _, _, key_hash = self._make_project(uid, name="proj-a")
        with self.assertRaises(sqlite3.IntegrityError):
            self.cur.execute(
                "INSERT INTO projects (user_id, name, api_key_hash) VALUES (?, ?, ?)",
                (uid, "proj-b", key_hash),
            )

    def test_04_flag_belongs_to_project(self):
        uid = self._make_user()
        pid, _, _ = self._make_project(uid)
        fid = self._make_flag(pid)
        flag = self.cur.execute(
            'SELECT project_id, "key", description, enabled, rollout_percentage,'
            " default_value, created_at, definition_updated_at"
            " FROM feature_flags WHERE id = ?",
            (fid,),
        ).fetchone()
        self.assertEqual(flag[0], pid)
        self.assertEqual(flag[1], "dark_mode")
        self.assertEqual(flag[2], "Dark theme toggle")
        self.assertEqual(flag[5], 1)
        self.assertIsNotNone(flag[6])
        self.assertIsNotNone(flag[7])

    def test_05_audit_references_project_and_flag(self):
        uid = self._make_user()
        pid, _, _ = self._make_project(uid)
        fid = self._make_flag(pid)
        self.cur.execute(
            "INSERT INTO audit_log_entries (project_id, flag_id, actor, field_changed,"
            " old_value, new_value) VALUES (?, ?, ?, ?, ?, ?)",
            (pid, fid, "owner@example.com", "enabled", "0", "1"),
        )
        audit = self.cur.execute(
            'SELECT project_id, flag_id, actor, field_changed, old_value, new_value,'
            ' "timestamp" FROM audit_log_entries WHERE id = ?',
            (self.cur.lastrowid,),
        ).fetchone()
        self.assertEqual((audit[0], audit[1]), (pid, fid))
        self.assertEqual(audit[2:6], ("owner@example.com", "enabled", "0", "1"))
        self.assertIsNotNone(audit[6])

    def test_06_key_unique_per_project_only(self):
        uid = self._make_user()
        pid1, _, _ = self._make_project(uid, name="proj-a")
        pid2, _, _ = self._make_project(uid, name="proj-b")
        self._make_flag(pid1)
        # Same key in a different project: allowed.
        self._make_flag(pid2)
        # Same key twice in one project: rejected.
        with self.assertRaises(sqlite3.IntegrityError):
            self._make_flag(pid1)

    def test_07_audit_history_survives_flag_delete(self):
        uid = self._make_user()
        pid, _, _ = self._make_project(uid)
        fid = self._make_flag(pid)
        self.cur.execute(
            "INSERT INTO audit_log_entries (project_id, flag_id, actor, field_changed,"
            " old_value, new_value) VALUES (?, ?, ?, ?, ?, ?)",
            (pid, fid, "owner@example.com", "enabled", "0", "1"),
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.cur.execute("DELETE FROM feature_flags WHERE id = ?", (fid,))
        (surviving,) = self.cur.execute(
            "SELECT COUNT(*) FROM audit_log_entries WHERE flag_id = ?", (fid,)
        ).fetchone()
        self.assertEqual(surviving, 1)

    def test_08_orphan_project_rejected(self):
        # Proves the REFERENCES clauses bite GIVEN the per-connection pragma
        # from setUp -- not that a bare connection without it is safe (it is
        # not: SQLite defaults enforcement to OFF, and no schema file can
        # change that for other connections).
        with self.assertRaises(sqlite3.IntegrityError):
            self.cur.execute(
                "INSERT INTO projects (user_id, name, api_key_hash) VALUES (?, ?, ?)",
                (999999, "orphan", "x"),
            )

    def test_rollout_range_checked_at_storage(self):
        uid = self._make_user()
        pid, _, _ = self._make_project(uid)
        with self.assertRaises(sqlite3.IntegrityError):
            self.cur.execute(
                'INSERT INTO feature_flags (project_id, "key", description,'
                " default_value, rollout_percentage) VALUES (?, ?, ?, ?, ?)",
                (pid, "bad-rollout", "out of range", 0, 101),
            )

    def test_key_hash_index_lands_on_pre_existing_db(self):
        # Project-sprint migration finding: CREATE TABLE IF NOT EXISTS never
        # upgrades an old table, so an old-shape database (projects without
        # the unique index) must gain the guarantee when app code connects --
        # not just fresh builds. Simulated by creating the old shape (schema
        # minus the index statement), then connecting through app.db, which
        # re-applies the full schema on every connection.
        import os
        import sys

        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
        from app import db as app_db

        old_db = str(pathlib.Path(self.tmp.name) / "old.db")
        old_schema = SCHEMA_PATH.read_text(encoding="utf-8").replace(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_api_key_hash"
            " ON projects (api_key_hash);",
            "",
        )
        self.assertNotEqual(old_schema, SCHEMA_PATH.read_text(encoding="utf-8"))
        raw = sqlite3.connect(old_db)
        raw.execute("PRAGMA foreign_keys = ON;")
        raw.executescript(old_schema)
        raw.execute(
            "INSERT INTO users (email, hashed_password) VALUES (?, ?)", ("o@x.com", "h")
        )
        raw.execute(
            "INSERT INTO projects (user_id, name, api_key_hash) VALUES (?, ?, ?)",
            (1, "legacy", "ab" * 32),
        )
        raw.commit()
        raw.close()

        os.environ["DATACHESS_DB_PATH"] = old_db
        try:
            migrated = app_db.connect()
            try:
                indexes = [
                    r[0]
                    for r in migrated.execute(
                        "SELECT name FROM sqlite_master"
                        " WHERE type = 'index' AND tbl_name = 'projects'"
                    ).fetchall()
                ]
            finally:
                migrated.close()
        finally:
            del os.environ["DATACHESS_DB_PATH"]
        self.assertIn("idx_projects_api_key_hash", indexes)
        # ...and the guarantee is live, not just present:
        dup = sqlite3.connect(old_db)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                dup.execute(
                    "INSERT INTO projects (user_id, name, api_key_hash) VALUES (?, ?, ?)",
                    (1, "copycat", "ab" * 32),
                )
        finally:
            dup.close()


if __name__ == "__main__":
    unittest.main()
