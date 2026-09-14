-- DataChess schema — Sprint: table schema (SQLite)
-- Source of truth: datachess-sprint-requirements2.md, Section 3 (Data Model).
-- One documented step takes an empty database to this complete schema:
--   python3 backend/create_db.py
-- Out of scope for this sprint: endpoints, query layer, seed/demo data,
-- indexes beyond the uniqueness rules below.

-- NOTE: no PRAGMA here on purpose. In SQLite, foreign_keys is a
-- per-connection setting and is NOT persisted by schema scripts, so every
-- connection must run `PRAGMA foreign_keys = ON;` itself (create_db.py and
-- verify_schema.py both do; future query layers must too).

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
    -- Decision: rollout range is constrained HERE at the storage layer, not
    -- only in application code. A CHECK survives buggy callers and hand-run
    -- SQL; an app-level clamp (which the later PATCH ticket should still add
    -- for good errors) does not. So if that ticket looks redundant next to
    -- this line, keep both: the clamp is UX, this CHECK is the guarantee.
    -- Do not remove this CHECK as "redundant" without a replacement at this
    -- layer. (DESIGN.md, where this would normally live, is out of scope
    -- for this sprint, so the reasoning is recorded here instead.)
    rollout_percentage INTEGER NOT NULL DEFAULT 0 CHECK (rollout_percentage >= 0 AND rollout_percentage <= 100),
    default_value INTEGER NOT NULL CHECK (default_value IN (0, 1)),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    -- Written only by sync when description/default_value change, never by dashboard PATCH.
    definition_updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (project_id, "key")
);

-- Delete behavior is an explicit decision, not a default:
-- projects/flags cascade (a deleted owner takes its live rows), but audit
-- rows NEVER cascade: history must not die silently with its flag/project.
-- Deleting a referenced project/flag with history fails instead (RESTRICT),
-- forcing explicit handling of the audit trail.

CREATE TABLE IF NOT EXISTS audit_log_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects (id) ON DELETE RESTRICT,
    flag_id INTEGER NOT NULL REFERENCES feature_flags (id) ON DELETE RESTRICT,
    actor TEXT NOT NULL,
    field_changed TEXT NOT NULL CHECK (field_changed IN ('enabled', 'rollout_percentage')),
    old_value TEXT NOT NULL,
    new_value TEXT NOT NULL,
    "timestamp" TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- One key identifies exactly one project: two projects sharing a hash would
-- leave evaluate/sync with no correct tenant to serve, so the constraint
-- lives in storage, not application code. It is a standalone CREATE INDEX
-- (not inline UNIQUE) on purpose: CREATE TABLE IF NOT EXISTS never upgrades
-- an existing table, so an inline constraint would silently miss every
-- database created before it. This statement runs on every connect and lands
-- on old and new databases alike. If it ever fails, an old database already
-- holds duplicate hashes -- genuinely ambiguous data that must be repaired
-- by hand, and failing loudly is the correct response to that.
CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_api_key_hash ON projects (api_key_hash);
