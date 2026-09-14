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
  - Decision: the 0–100 range is enforced by the storage-level `CHECK` on
    purpose — it survives buggy callers and hand-run SQL where an app-level
    clamp cannot. The PATCH ticket should still add its own clamp for good
    error messages, but must not remove this `CHECK` as redundant.
- `audit_log_entries` — `id, project_id → projects, flag_id → feature_flags,`
  `actor, field_changed CHECK IN ('enabled','rollout_percentage'),`
  `old_value, new_value, timestamp`
  - Delete behavior is explicit: audit FKs are `ON DELETE RESTRICT`, so history
    can never die silently with its flag/project — deleting a referenced row
    fails until its history is handled. (Live rows cascade: project → flags.)

SQLite note: `foreign_keys` is a **per-connection** setting and is not stored
in the schema file, so every connection must run `PRAGMA foreign_keys = ON;`
itself. `create_db.py` and `verify_schema.py` both do this; any future query
layer must too.

## Verification: round-trip claims, not just table names

```bash
python3 -m pytest backend/test_schema.py -v
```

`backend/test_schema.py` (stdlib `unittest`, runs under `pytest` or
`python3 -m unittest`) builds a fresh DB from `schema.sql` per test and proves:
user write/read-back (02), project → user with hash-only key storage (03),
flag → project with all fields (04), audit → project + flag (05), per-project
key uniqueness (06), audit surviving its flag's delete (07), and live FK
enforcement (08), plus the storage-level rollout range CHECK.

`python3 backend/verify_schema.py` is a thin wrapper that runs that same
suite with no test runner needed — all assertions live in `test_schema.py`
only, so a schema change is made once and both entry points stay in agreement
by construction.

To use a different path: `python3 backend/create_db.py /tmp/my.db`.

Out of scope for this sprint (intentionally absent): endpoints, query layer,
seed/demo data, indexes beyond the uniqueness rules above.

---

# Auth sprint — register / login / me (dashboard human auth)

First sprint's tables are reused as-is (`users`); no schema change.

```bash
pip install -r backend/requirements.txt
uvicorn backend.app.main:app --reload
```

- `POST /auth/register` `{email, password}` → `{access_token, refresh_token, token_type}`
- `POST /auth/login` `{email, password}` → same shape
- `GET /auth/me` with `Authorization: Bearer <token>` → `{id, email, is_admin}`

Decisions worth knowing later:

- **Lifetimes:** access 1h, refresh 7d (both minted on register and login).
  There is deliberately **no `/auth/refresh` endpoint** yet — refresh-token
  rotation stays out of scope for this sprint; the refresh token is issued now
  so that ticket has something to redeem.
- **No user enumeration:** wrong password and unknown email return the same
  401 + `"Invalid email or password"` (unknown emails are verified against a
  dummy bcrypt hash so timing doesn't leak either).
- **Duplicate register** is 400 + `"Email already in use"`.
- **Admin can never come from register:** the body has no such field and the
  insert hardcodes `is_admin = 0`; extra fields are ignored.
- **All auth refusals are 401** (missing/malformed/invalid tokens included) —
  tokens are parsed by hand, not via HTTPBearer, which would 403 on a missing
  header.
- **Secret:** `DATACHESS_JWT_SECRET` env; the code fallback is dev-only.
- **DB:** `DATACHESS_DB_PATH` env (default `./datachess.db`); schema ensured
  per connection, FK pragma per connection — same rules as the schema sprint.
- Still no SDK/API-key paths here: dashboard (JWT) and machine (API key) auth
  stay separate per the requirements doc, and evaluate/sync never touch these
  routes.

Tests: `python3 -m pytest backend/test_auth.py -v` (AC01–07 + lifetimes).
