-- DataChess schema — Sprint: table schema (SQLite)
-- Source of truth: datachess-sprint-requirements2.md, Section 3 (Data Model).
-- One documented step takes an empty database to this complete schema:
--   python3 backend/create_db.py
-- Out of scope for this sprint: endpoints, query layer, seed/demo data,
-- indexes beyond the uniqueness rules below.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    hashed_password TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0 CHECK (is_admin IN (0, 1)),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    repo_url TEXT NULL,
    -- SHA-256 hex of the API key. The raw key is never stored anywhere.
    api_key_hash TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS feature_flags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    "key" TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
    rollout_percentage INTEGER NOT NULL DEFAULT 0 CHECK (rollout_percentage >= 0 AND rollout_percentage <= 100),
    default_value INTEGER NOT NULL CHECK (default_value IN (0, 1)),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    -- Written only by sync when description/default_value change, never by dashboard PATCH.
    definition_updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (project_id, "key")
);

CREATE TABLE IF NOT EXISTS audit_log_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    flag_id INTEGER NOT NULL REFERENCES feature_flags (id) ON DELETE CASCADE,
    actor TEXT NOT NULL,
    field_changed TEXT NOT NULL CHECK (field_changed IN ('enabled', 'rollout_percentage')),
    old_value TEXT NOT NULL,
    new_value TEXT NOT NULL,
    "timestamp" TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
