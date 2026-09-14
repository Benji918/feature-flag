"""Shared dashboard-auth helper: Bearer access token -> user row or None.

Every failure mode (missing/malformed/invalid/expired token, refresh token
used as identity, deleted user) collapses to None; routes turn that into
401 (or 404 where the ticket demands indistinguishability). Narrow except on
purpose: only token problems are auth failures -- anything else surfaces.
"""

import sqlite3

import jwt
from fastapi import Request
from fastapi.security import HTTPBearer

from .. import db, security

# Declared once so OpenAPI/Swagger shows the Authorize button + per-endpoint
# locks. auto_error=False on purpose: this is docs-only. Enforcement stays in
# current_user below (hand-parsed header, all refusals 401) -- the default
# auto_error=True would 403 on a missing header and break that contract.
bearer_scheme = HTTPBearer(bearerFormat="JWT", auto_error=False, description="Access token from /auth/register or /auth/login")


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def current_user(request: Request) -> sqlite3.Row | None:
    token = _bearer_token(request)
    if token is None:
        return None
    try:
        payload = security.decode_token(token)
    except jwt.InvalidTokenError:
        return None
    if payload.get("type") != "access":
        return None
    conn = db.connect()
    try:
        return conn.execute(
            "SELECT id, email, is_admin FROM users WHERE id = ?", (payload.get("sub"),)
        ).fetchone()
    finally:
        conn.close()
