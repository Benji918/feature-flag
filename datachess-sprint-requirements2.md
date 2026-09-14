# DataChess — Feature Flag & Progressive Rollout Platform
## Sprint Requirements for Ticket Generation

> **Purpose of this document:** this is the source of truth for generating a backlog of Backend (BE) and Frontend (FE) sprint tickets for a 14-day hackathon build. Use the Phase groupings in Section 8 to sequence tickets, and the entity/endpoint definitions in Sections 3–5 as the source of acceptance criteria — field names, endpoint paths, and behaviors below should be treated as binding unless a ticket explicitly says otherwise.

---

## 1. Project Summary

DataChess is a multi-tenant feature flag and progressive rollout platform. Teams register, create a "project" per repo they own, and get an API key. Their application embeds a thin client library that checks flag state at runtime. Flags are toggled, rolled out by percentage, or killed instantly from a Vue.js dashboard — with no redeploy required on the consuming app's side. An admin role can see every project and flag across the platform.

**Stack:** Python (FastAPI) backend, Vue.js frontend, SQLite for the hackathon (swappable to Postgres later). AI features (Ollama) are additive only and never load-bearing for core functionality.

**Build window:** 14 days. Scope is deliberately trimmed — see Section 9 (Out of Scope) before creating any ticket outside what's listed here.

---

## 2. Actors / Roles

| Actor | Description | Auth mechanism |
|---|---|---|
| Anonymous visitor | Can view marketing/login page, can register | None |
| Project owner (User) | Registered human; owns one or more projects; sees only their own dashboard | JWT / session |
| Admin | A User with `is_admin = true`; sees **all** projects and flags across every user | JWT / session |
| Consuming application (SDK) | A machine — the user's running app — that checks flag state | API key (per project), no human session involved |

**Critical design rule:** dashboard auth (human, JWT) and SDK auth (machine, API key) are two entirely separate code paths. The evaluate/sync endpoints must never require or reference a JWT.

---

## 3. Data Model

### User
| Field | Type | Notes |
|---|---|---|
| id | int, PK | |
| email | string, unique | |
| hashed_password | string | bcrypt/argon2 |
| is_admin | bool, default false | |
| created_at | datetime | |

### Project
| Field | Type | Notes |
|---|---|---|
| id | int, PK | |
| user_id | int, FK → User | owner |
| name | string | |
| repo_url | string, nullable | label only, not used for GitHub API access |
| api_key_hash | string | SHA-256 hash; **raw key is never stored** |
| created_at | datetime | |

### FeatureFlag
| Field | Type | Notes |
|---|---|---|
| id | int, PK | |
| project_id | int, FK → Project | |
| key | string | unique within a project |
| description | string | |
| enabled | bool, default false | live state, owned by dashboard/API only |
| rollout_percentage | int 0–100, default 0 | live state, owned by dashboard/API only |
| default_value | bool | from config file; seeds `enabled`/`rollout_percentage` once, at creation only — never read again after that |
| created_at | datetime | set once, at creation |
| definition_updated_at | datetime | written only by sync, when `description`/`default_value` change — **never** by a dashboard PATCH |

**Contract:** "when was this flag last toggled" is answered by querying `AuditLogEntry` for the flag, not by a column on this row — a single shared timestamp touched by both the dashboard and sync would repeat the exact conflation Section 4 exists to prevent, one column to the left of the audit table. `definition_updated_at` answers a narrower, different question — "when did the config last change this flag's definition" — and only sync may write it.

### AuditLogEntry
| Field | Type | Notes |
|---|---|---|
| id | int, PK | |
| project_id | int, FK | |
| flag_id | int, FK | |
| actor | string | user email (dashboard action), or `"auto-rollback"` if Phase 5's automated kill ships — **never `"sync"`** |
| field_changed | string | `enabled` / `rollout_percentage` only |
| old_value / new_value | string | |
| timestamp | datetime | |

**Contract:** this table only ever records changes to `enabled` and `rollout_percentage`. Sync is forbidden from writing either field (Section 4), so a sync-authored row can never exist — do not widen `field_changed` to accommodate one. Flag creation and metadata edits (`description`) are provenance already covered by `FeatureFlag.created_at`/`updated_at`; they are a different concern from this table and should not be merged into it, or the config-vs-live-state boundary this table depends on collapses.

---

## 4. Core Design Rule: Config File vs. Live State

The `flags.yaml` config file **declares flag existence only** (key, description, default). It must never be able to overwrite `enabled` or `rollout_percentage` on a flag that already exists — only the dashboard/API can change live state. Every sync ticket's acceptance criteria must include a test proving that re-syncing an existing flag does not reset its live state.

Example `flags.yaml`:
```yaml
project: my-node-app
flags:
  - key: new_checkout_flow
    description: "New checkout UI"
    default: false
  - key: dark_mode
    description: "Dark theme toggle"
    default: true
```

---

## 5. Backend (FastAPI) Requirements

### 5.1 Auth endpoints
- `POST /auth/register` — `{email, password}` → `{access_token}`
- `POST /auth/login` — `{email, password}` → `{access_token}`
- `GET /auth/me` — → `{id, email, is_admin}`

### 5.2 Project endpoints (JWT-protected)
- `POST /projects` — `{name, repo_url?}` → `{id, name, repo_url, api_key}` — **raw `api_key` is returned exactly once**, only `api_key_hash` is persisted
- `GET /projects` — returns caller's own projects; if `is_admin`, returns all projects (or use `GET /admin/projects` — pick one, don't build both)
- `GET /projects/{project_id}` — detail + flag summary; 403 if caller doesn't own it and isn't admin

### 5.3 Admin endpoint (JWT-protected, admin-only)
- `GET /admin/projects` — all projects across all users, including owner email, for the platform-wide view

### 5.4 Flag endpoints (JWT-protected, dashboard-facing)
- `GET /projects/{project_id}/flags` — list flags for a project
- `PATCH /projects/{project_id}/flags/{key}` — `{enabled?, rollout_percentage?}` → updated flag; **must write an AuditLogEntry**
- `GET /projects/{project_id}/flags/{key}/audit` — change history (stretch)

### 5.5 Sync endpoint (API-key protected, machine-facing)
- `POST /flags/sync` — header `X-API-Key`, body `{flags: [{key, description, default}]}` → upserts flag *definitions only*, per the rule in Section 4
- **On first creation** of a flag: seed `enabled = default`, `rollout_percentage = 100 if default else 0`, and set `definition_updated_at`. This is the only point at which `default_value` ever determines runtime behavior.
- **On re-sync of an existing flag:** only `description`, `default_value`, and `definition_updated_at` may be written. `enabled`/`rollout_percentage` are left untouched, per Section 4.

### 5.6 Evaluate endpoint (API-key protected, machine-facing)
- `GET /evaluate/{flag_key}?user_id=...` — header `X-API-Key` → `{enabled: bool}`
- Bucketing must be **deterministic**, not random per request:
  ```python
  bucket = int(hashlib.md5(f"{flag.key}:{user_id}".encode()).hexdigest(), 16) % 100
  return bucket < flag.rollout_percentage
  ```
- Invalid API key → `401`. This endpoint must respond in well under 100ms for realistic SDK polling.
- Unknown `flag_key` (no row for this project) → `404`. There is no row to fall back on, so `default_value` plays no role here — the caller's own SDK-side `fallback`/`default` parameter (client code, triggered on any non-200 response) governs behavior, not any server-stored value.

### 5.7 Non-functional requirements
- API keys generated via `secrets.token_urlsafe(32)`, stored only as a SHA-256 hash, shown to the user exactly once
- CORS configured for the Vue frontend origin
- Evaluate/sync paths never touch the `User` table or JWT logic at all
- SQLite via SQLAlchemy/SQLModel for the hackathon build

---

## 6. Frontend (Vue.js) Requirements

### 6.1 Auth
- Register page, Login page
- Route guard redirecting unauthenticated users to login

### 6.2 Project owner dashboard
- Project list view with "create project" action
- Project creation flow that displays the API key **once**, with a copy button and an explicit "you won't see this again" warning
- Project detail view: flag list, each row showing key, description, an enabled toggle, a rollout % slider, and a "kill" button (immediately sets `enabled=false`, `rollout_percentage=0`)
- Kill action requires a confirmation dialog before firing (it's immediate and irreversible in effect)
- Poll `GET /projects/{id}/flags` every 3–5 seconds so the view reflects changes made elsewhere

### 6.3 Admin view
- Admin-only route, guarded by `is_admin` from `/auth/me`; non-admins redirected away
- Table of all projects across all users (owner email, project name, flag count)
- Drill-in to any project's flags, reusing the same flag-list component as the owner view

### 6.4 Non-functional
- Loading and error states on every API-backed view
- No API keys or secrets ever logged to console or stored in localStorage in plaintext beyond the session token

---

## 7. SDK / Client Library Deliverables

Not a published package for this sprint — a single file per language, dropped into the consumer's repo.

- **Python** — `flagclient.py`: `is_enabled(flag_key, user_id, default=False)`, HTTP call with short timeout, fails closed (returns `default`) on any network/service error
- **JavaScript** — `flagClient.js`: same contract, `fetch`-based
- **Demo apps** — at least two small instrumented apps registered as separate projects, to prove the multi-repo story live in the demo

---

## 8. Suggested Phase Sequencing (for ticket ordering)

**Phase 1 — Foundation**
BE: User/Project/FeatureFlag models + migrations; register/login; project CRUD + API key generation
FE: Register/login pages; project list + create-project flow with one-time key display

**Phase 2 — Core flag engine**
BE: `/flags/sync` (upsert-safe); `/evaluate` with deterministic hashing; AuditLogEntry writes on PATCH
FE: Project detail page — flag list, toggle, rollout slider, wired to PATCH, polling

**Phase 3 — Multi-tenancy & admin**
BE: `is_admin` support; `/admin/projects`
FE: Admin route + guard; admin overview + drill-in

**Phase 4 — SDK & demo proof**
Both: Python + JS client files; two demo apps as separate projects
FE: audit log view (stretch), kill-switch confirmation dialog

**Phase 5 — Stretch / polish**
BE: SSE push instead of polling; simulated auto-rollback on error threshold; optional Ollama-generated rollback summary
FE: live-update subscription instead of polling; change notifications; short `DESIGN.md` write-up

---

## 9. Explicitly Out of Scope (do not generate tickets for these)

- Multi-language SDKs beyond Python + one JS example
- Real GitHub OAuth / repo browsing / auto-committing `flags.yaml` via PR
- Full RBAC or multiple team members per project
- Complex targeting rules (user segments, AND/OR condition builders) — % rollout + boolean enabled only
- WebSocket infrastructure — polling for MVP, SSE as the only allowed upgrade
- Browser-exposed ("public key") client-side flag evaluation

---

## 10. Competency Mapping (for demo narrative, optional ticket tagging)

| Competency | Primarily demonstrated by |
|---|---|
| System design | Config-vs-live-state separation; evaluate endpoint architecture |
| Production sense | Fail-closed SDK behavior; API key hashing; audit logging |
| Ownership | Admin visibility; kill-switch confirmation; audit trail |
| Judgment | Documented trade-offs: polling vs. SSE, config sync semantics |
| Technical depth | Deterministic hashed bucketing; sync upsert logic |
| Communication | `DESIGN.md`; dashboard UX for non-technical stakeholders |

Tickets touching these areas are worth flagging explicitly in their description, since they map directly to hackathon scoring criteria.
