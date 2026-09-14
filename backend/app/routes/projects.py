"""Project endpoints (dashboard auth; machine auth via API key stays separate).

  POST /projects       {name, repo_url?} -> {id, name, repo_url, api_key}
  GET  /projects       -> [{id, name, repo_url, created_at}] (caller's own only)
  GET  /projects/{id}  -> {id, name, repo_url, created_at, flags: [...]}

Key rules (AC02/AC03): the raw key exists in exactly one response -- the
creation one. List and detail carry no key field at all (the shapes in
schemas/project.py have no such field), only the SHA-256 hash is stored,
and there is no endpoint that re-displays it. Tenancy: every refusal for a
project you don't own is 404 "Project not found", identical to a missing
project, so neither case reveals existence. No token at all is 401.
Owner-only for now: platform-wide admin listing is the Phase 3 ticket.
"""

import hashlib
import secrets
import sqlite3

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from .. import db
from ..schemas.project import ProjectCreate, ProjectCreated, ProjectDetail, ProjectSummary
from .deps import bearer_scheme, current_user

router = APIRouter()

_NOT_FOUND = {"detail": "Project not found"}


def _mint_key() -> tuple[str, str]:
    raw = secrets.token_urlsafe(32)
    return raw, hashlib.sha256(raw.encode("utf-8")).hexdigest()


@router.post("/projects", status_code=200, response_model=ProjectCreated, dependencies=[Depends(bearer_scheme)])
def create_project(body: ProjectCreate, request: Request):
    user = current_user(request)
    if user is None:
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})
    name = (body.name or "").strip()
    if not name:
        return JSONResponse(status_code=400, content={"detail": "Project name must not be empty"})
    repo_url = body.repo_url or None
    conn = db.connect()
    try:
        # A fresh 256-bit key colliding with an existing hash is
        # cosmologically unlikely; UNIQUE would reject it, so retry with a
        # new key rather than fail the creation.
        for _ in range(3):
            raw, digest = _mint_key()
            try:
                cur = conn.execute(
                    "INSERT INTO projects (user_id, name, repo_url, api_key_hash)"
                    " VALUES (?, ?, ?, ?)",
                    (user["id"], name, repo_url, digest),
                )
                break
            except sqlite3.IntegrityError:
                continue
        else:
            return JSONResponse(status_code=500, content={"detail": "Could not issue a unique key"})
        conn.commit()
        return {"id": cur.lastrowid, "name": name, "repo_url": repo_url, "api_key": raw}
    finally:
        conn.close()


@router.get("/projects", response_model=list[ProjectSummary], dependencies=[Depends(bearer_scheme)])
def list_projects(request: Request):
    user = current_user(request)
    if user is None:
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT id, name, repo_url, created_at FROM projects"
            " WHERE user_id = ? ORDER BY id",
            (user["id"],),
        ).fetchall()
    finally:
        conn.close()
    return [
        {"id": r["id"], "name": r["name"], "repo_url": r["repo_url"], "created_at": r["created_at"]}
        for r in rows
    ]


@router.get("/projects/{project_id}", response_model=ProjectDetail, dependencies=[Depends(bearer_scheme)])
def project_detail(project_id: int, request: Request):
    user = current_user(request)
    if user is None:
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})
    conn = db.connect()
    try:
        proj = conn.execute(
            "SELECT id, user_id, name, repo_url, created_at FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()
        # Not yours or not there: same 404, same body. Existence stays hidden.
        if proj is None or proj["user_id"] != user["id"]:
            return JSONResponse(status_code=404, content=_NOT_FOUND)
        flags = conn.execute(
            "SELECT \"key\", description, enabled, rollout_percentage, default_value,"
            " created_at, definition_updated_at FROM feature_flags"
            " WHERE project_id = ? ORDER BY \"key\"",
            (project_id,),
        ).fetchall()
    finally:
        conn.close()
    return {
        "id": proj["id"],
        "name": proj["name"],
        "repo_url": proj["repo_url"],
        "created_at": proj["created_at"],
        "flags": [
            {
                "key": f["key"],
                "description": f["description"],
                "enabled": bool(f["enabled"]),
                "rollout_percentage": f["rollout_percentage"],
                "default_value": bool(f["default_value"]),
                "created_at": f["created_at"],
                "definition_updated_at": f["definition_updated_at"],
            }
            for f in flags
        ],
    }
