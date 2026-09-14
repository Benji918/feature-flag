"""Password hashing and JWTs (bcrypt + PyJWT).

Token lifetimes: access 1 hour, refresh 7 days. The signing secret comes
from DATACHESS_JWT_SECRET; the fallback is dev-only and must never be used
in production (any token it mints is forgeable by anyone reading this file).
"""

import os
import time

import bcrypt
import jwt

ALGORITHM = "HS256"
ACCESS_TTL_SECONDS = 3600
REFRESH_TTL_SECONDS = 7 * 24 * 3600
_DEV_SECRET = os.environ.get("DATACHESS_JWT_SECRET", "dev-only-insecure-secret")

# Verifying a wrong password against a real bcrypt hash costs ~0.3s; verifying
# against nothing would be instant, leaking "email not registered" via timing.
# Unknown-email logins are checked against this dummy hash so both failure
# modes take the same path and the same time.
_DUMMY_HASH = bcrypt.hashpw(b"datachess-unknown-email-dummy", bcrypt.gensalt())


def _secret() -> str:
    return os.environ.get("DATACHESS_JWT_SECRET", _DEV_SECRET)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False


def verify_against_dummy(password: str) -> bool:
    bcrypt.checkpw(password.encode("utf-8"), _DUMMY_HASH)
    return False


def mint_token(user_id: int, email: str, kind: str) -> str:
    now = int(time.time())
    ttl = ACCESS_TTL_SECONDS if kind == "access" else REFRESH_TTL_SECONDS
    return jwt.encode(
        {"sub": str(user_id), "email": email, "type": kind, "iat": now, "exp": now + ttl},
        _secret(),
        algorithm=ALGORITHM,
    )


def decode_token(token: str) -> dict:
    return jwt.decode(token, _secret(), algorithms=[ALGORITHM])
