"""Password hashing and JWTs (bcrypt + PyJWT).

Token lifetimes: access 1 hour, refresh 7 days. The signing secret comes
from DATACHESS_JWT_SECRET; the fallback is dev-only and must never be used
in production (any token it mints is forgeable by anyone reading this file).
"""

import base64
import hashlib
import os
import time

import bcrypt
import jwt

ALGORITHM = "HS256"
ACCESS_TTL_SECONDS = 3600
REFRESH_TTL_SECONDS = 7 * 24 * 3600
# A fallback is a concrete value, not a second look at the same env var:
# with no DATACHESS_JWT_SECRET set, PyJWT would get None as its key and every
# mint/decode would blow up inside key preparation (500s on the endpoints).
# Dev-only: forgeable by anyone reading this file, never for production.
_DEV_SECRET = "dev-only-insecure-secret"

def _bcrypt_input(password: str) -> bytes:
    # bcrypt silently ignores everything past byte 72, so two long passwords
    # sharing a 72-byte prefix would verify against each other's hash. SHA-256
    # first (base64 to stay in bcrypt's alphabet) so the whole password counts.
    digest = hashlib.sha256(password.encode("utf-8")).digest()
    return base64.b64encode(digest)


# Verifying a wrong password against a real bcrypt hash costs ~0.3s; verifying
# against nothing would be instant, leaking "email not registered" via timing.
# Unknown-email logins are checked against this dummy hash so both failure
# modes take the same path and the same time.
_DUMMY_HASH = bcrypt.hashpw(_bcrypt_input("datachess-unknown-email-dummy"), bcrypt.gensalt())


def _secret() -> str:
    return os.environ.get("DATACHESS_JWT_SECRET", _DEV_SECRET)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_bcrypt_input(password), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_bcrypt_input(password), hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False


def verify_against_dummy(password: str) -> bool:
    bcrypt.checkpw(_bcrypt_input(password), _DUMMY_HASH)
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
