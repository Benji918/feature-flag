"""DataChess auth endpoints (dashboard auth; SDK/API-key paths stay separate).

  POST /auth/register  {email, password} -> {access_token, refresh_token, token_type}
  POST /auth/login     {email, password} -> {access_token, refresh_token, token_type}
  GET  /auth/me        Bearer access token -> {id, email, is_admin}
  (refresh tokens are minted but never accepted as identity proof)

Refusals are all 401 (never 403), so missing/malformed/invalid tokens are
indistinguishable by status. Login failures use one identical message for
unknown emails and wrong passwords, so neither case reveals registration.
Register never grants admin: the request body has no such field and the
insert hardcodes is_admin = 0 on top of the column default.
"""

import sqlite3

import jwt
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import db, security

app = FastAPI(title="DataChess")


class AuthIn(BaseModel):
    email: str
    password: str


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


@app.post("/auth/register", status_code=200)
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


@app.post("/auth/login", status_code=200)
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


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


@app.get("/auth/me")
def me(request: Request):
    token = _bearer_token(request)
    if token is None:
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})
    try:
        payload = security.decode_token(token)
    except jwt.InvalidTokenError:
        # Narrow on purpose: only token problems become 401s. Anything else
        # (e.g. a misconfigured signing key) must surface loudly, not hide
        # behind "Invalid or expired token".
        return JSONResponse(status_code=401, content={"detail": "Invalid or expired token"})
    if payload.get("type") != "access":
        # Refresh tokens prove nothing here: identity is bounded by the 1h
        # access lifetime, and the 7d token is only for the future refresh
        # flow (out of scope this sprint).
        return JSONResponse(status_code=401, content={"detail": "Invalid or expired token"})
    conn = db.connect()
    try:
        row = conn.execute("SELECT id, email, is_admin FROM users WHERE id = ?", (payload.get("sub"),)).fetchone()
    finally:
        conn.close()
    if row is None:
        return JSONResponse(status_code=401, content={"detail": "Invalid or expired token"})
    return {"id": row["id"], "email": row["email"], "is_admin": bool(row["is_admin"])}
