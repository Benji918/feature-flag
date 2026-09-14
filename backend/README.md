# Backend — table schema sprint (SQLite)

Source of truth for fields: `datachess-sprint-requirements2.md`, Section 3.

## One documented step: empty database → complete schema

From the repo root:

```bash
python3 backend/create_db.py
```

This applies `backend/schema.sql` once (stdlib `sqlite3` only, no
dependencies) and creates `./datachess.db` with the four tables:

- `users` — `id, email UNIQUE, hashed_password, is_admin DEFAULT 0, created_at`
- `projects` — `id, user_id → users, name, repo_url NULL, api_key_hash, created_at`
  (only the SHA-256 hash is stored; the raw API key never appears in storage)
- `feature_flags` — `id, project_id → projects, key, description, enabled DEFAULT 0,`
  `rollout_percentage DEFAULT 0 CHECK 0–100, default_value, created_at, definition_updated_at,`
  `UNIQUE (project_id, key)`
- `audit_log_entries` — `id, project_id → projects, flag_id → feature_flags,`
  `actor, field_changed CHECK IN ('enabled','rollout_percentage'),`
  `old_value, new_value, timestamp`

To use a different path: `python3 backend/create_db.py /tmp/my.db`.

Out of scope for this sprint (intentionally absent): endpoints, query layer,
seed/demo data, indexes beyond the uniqueness rules above.
