"""Pydantic shapes for auth endpoints."""

from pydantic import BaseModel


class AuthIn(BaseModel):
    email: str
    password: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str


class MeOut(BaseModel):
    id: int
    email: str
    is_admin: bool
