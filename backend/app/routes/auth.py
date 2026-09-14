"""Dashboard auth endpoints (moved verbatim from app.main; behavior unchanged).

  POST /auth/register  {email, password} -> {access_token, refresh_token, token_type}
  POST /auth/login     {email, password} -> {access_token, refresh_token, token_type}
  GET  /auth/me        Bearer access token -> {id, email, is_admin}
"""

import sqlite3

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from .. import db, security
from ..schemas.auth import AuthIn, MeOut, TokenPair
from .deps import bearer_scheme, current_user

router = APIRouter()


def _normalize_email(email: str) -> str | None:
    email = (email or "").strip().lower()
    if "@" not in email or not email:
        return None
    return email


def _tokens(user_id: int, email: str) -> dict:
    return {
        "access_token": security.mint_token(user_id, email, "access"),
        "refresh_token": security.mint_token(user_id, email, "refresh"),
        "token_type": "bearer",
    }


@router.post("/auth/register", status_code=200, response_model=TokenPair)
def register(body: AuthIn):
    email = _normalize_email(body.email)
    if email is None:
        return JSONResponse(status_code=400, content={"detail": "Invalid email address"})
    if not body.password:
        return JSONResponse(status_code=400, content={"detail": "Password must not be empty"})
    conn = db.connect()
    try:
        exists = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if exists:
            return JSONResponse(status_code=400, content={"detail": "Email already in use"})
        try:
            cur = conn.execute(
                "INSERT INTO users (email, hashed_password, is_admin) VALUES (?, ?, 0)",
                (email, security.hash_password(body.password)),
            )
        except sqlite3.IntegrityError:
            # Lost a concurrent race: another registration of this email
            # committed between our SELECT and INSERT. The UNIQUE constraint
            # is the atomic guard, the SELECT only the fast path -- same AC05
            # refusal either way, never a 500.
            return JSONResponse(status_code=400, content={"detail": "Email already in use"})
        conn.commit()
        return _tokens(cur.lastrowid, email)
    finally:
        conn.close()


@router.post("/auth/login", status_code=200, response_model=TokenPair)
def login(body: AuthIn):
    email = _normalize_email(body.email)
    conn = db.connect()
    try:
        row = (
            conn.execute("SELECT id, email, hashed_password FROM users WHERE email = ?", (email,)).fetchone()
            if email
            else None
        )
        ok = security.verify_password(body.password, row["hashed_password"]) if row else security.verify_against_dummy(body.password or "")
        if not ok:
            # Same status + same message whether the email exists or not (AC04).
            return JSONResponse(status_code=401, content={"detail": "Invalid email or password"})
        return _tokens(row["id"], row["email"])
    finally:
        conn.close()


@router.get("/auth/me", response_model=MeOut, dependencies=[Depends(bearer_scheme)])
def me(request: Request):
    user = current_user(request)
    if user is None:
        return JSONResponse(status_code=401, content={"detail": "Invalid or expired token"})
    return {"id": user["id"], "email": user["email"], "is_admin": bool(user["is_admin"])}
