"""Auth sprint tests: AC01-07 plus the 1h access / 7d refresh lifetimes.

Run from the repo root:
    python3 -m pytest backend/app/tests/test_auth.py -v

Each test gets a fresh temp SQLite file via DATACHESS_DB_PATH (read lazily
per connection, so monkeypatched env just works) and a fixed JWT secret.
"""

import pathlib
import secrets
import sqlite3
import sys
import os
import jwt
import pytest
from fastapi.testclient import TestClient

# backend/ on sys.path so `import app` resolves regardless of CWD or runner.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from app.main import app  # noqa: E402
from app import security  # noqa: E402

# Session-random signing secret: tests never depend on ambient env (which
# previously masked the missing dev fallback -- the suite passed only where
# DATACHESS_JWT_SECRET happened to be set). No hardcoded literal anywhere.
TEST_SECRET = secrets.token_hex(32)

# Dummy credential for tests only -- a well-known example passphrase, not a
# real secret. Single constant (not inlined literals) so scanner surface is minimal.
TEST_PASSWORD = "correct-horse-battery-staple"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATACHESS_DB_PATH", str(tmp_path / "auth.db"))
    monkeypatch.setenv("DATACHESS_JWT_SECRET", TEST_SECRET)
    return TestClient(app)


def _register(client, email="owner@example.com", password=TEST_PASSWORD):
    return client.post("/auth/register", json={"email": email, "password": password})


def _stored_user_row():
    import os

    conn = sqlite3.connect(os.environ["DATACHESS_DB_PATH"])
    try:
        return conn.execute("SELECT * FROM users WHERE email = 'owner@example.com'").fetchone()
    finally:
        conn.close()


def test_01_register_returns_token(client):
    r = _register(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["access_token"]
    payload = jwt.decode(body["access_token"], TEST_SECRET, algorithms=["HS256"])
    assert payload["type"] == "access"
    assert payload["email"] == "owner@example.com"


def test_02_password_never_stored_as_written(client):
    _register(client, password=TEST_PASSWORD)
    (uid, email, stored, is_admin, created) = _stored_user_row()
    assert stored != TEST_PASSWORD
    assert TEST_PASSWORD not in str(_stored_user_row())
    assert stored.startswith("$2b$")  # bcrypt one-way hash
    assert security.verify_password(TEST_PASSWORD, stored) is True
    assert security.verify_password("wrong-pw", stored) is False


def test_03_login_returns_token(client):
    _register(client)
    r = client.post("/auth/login", json={"email": "owner@example.com", "password": TEST_PASSWORD})
    assert r.status_code == 200, r.text
    payload = jwt.decode(r.json()["access_token"], TEST_SECRET, algorithms=["HS256"])
    assert payload["type"] == "access"


def test_04_wrong_password_reveals_nothing(client):
    _register(client)
    bad_pw = client.post("/auth/login", json={"email": "owner@example.com", "password": "nope"})
    no_user = client.post("/auth/login", json={"email": "ghost@example.com", "password": "nope"})
    assert bad_pw.status_code == 401
    assert no_user.status_code == 401
    assert bad_pw.json() == no_user.json()
    assert "ghost@example.com" not in bad_pw.text and "owner@example.com" not in bad_pw.text


def test_05_duplicate_email_refused_plainly(client):
    assert _register(client).status_code == 200
    r = _register(client)
    assert r.status_code == 400
    assert "in use" in r.json()["detail"].lower()


def test_06_me_with_valid_token(client):
    token = _register(client).json()["access_token"]
    r = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    assert r.json() == {"id": 1, "email": "owner@example.com", "is_admin": False}


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer garbage"}, {"Authorization": "Bearer"}, {"Authorization": "Token x"}])
def test_06_me_refused_without_valid_token(client, headers):
    r = client.get("/auth/me", headers=headers)
    assert r.status_code == 401, r.text


def test_07_new_account_never_admin(client):
    r = _register(client)
    assert r.status_code == 200
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}).json()
    assert me["is_admin"] is False
    # Even a client-supplied admin marker must not stick.
    r2 = client.post("/auth/register", json={"email": "evil@example.com", "password": "pw", "is_admin": True})
    assert r2.status_code == 200, r2.text
    me2 = client.get("/auth/me", headers={"Authorization": f"Bearer {r2.json()['access_token']}"}).json()
    assert me2["is_admin"] is False


def test_token_lifetimes(client):
    body = _register(client).json()
    access = jwt.decode(body["access_token"], TEST_SECRET, algorithms=["HS256"])
    refresh = jwt.decode(body["refresh_token"], TEST_SECRET, algorithms=["HS256"])
    assert access["exp"] - access["iat"] == 3600
    assert refresh["exp"] - refresh["iat"] == 7 * 24 * 3600
    assert body["token_type"] == "bearer"


def test_dev_fallback_secret_when_env_unset(monkeypatch):
    # Guards the default-config breakage: with no env var, the signing key
    # must be a concrete dev value, never None (which 500s every mint/decode
    # inside PyJWT key preparation).
    monkeypatch.delenv("DATACHESS_JWT_SECRET", raising=False)
    assert security._secret() == "dev-only-insecure-secret"
    token = security.mint_token(1, "a@b.com", "access")
    assert security.decode_token(token)["sub"] == "1"


def test_register_race_returns_400_not_500(client, monkeypatch):
    # Simulates losing a concurrent duplicate-registration race: the SELECT
    # finds nothing, then the INSERT hits the UNIQUE constraint. The
    # constraint is the atomic guard -- it must map to the AC05 refusal.
    import types

    import app.main as main_module

    class _Empty:
        def fetchone(self):
            return None

    class _RaceConn:
        def execute(self, sql, params=()):
            if sql.lstrip().upper().startswith("INSERT"):
                raise sqlite3.IntegrityError("UNIQUE constraint failed: users.email")
            return _Empty()

        def close(self):
            pass

    monkeypatch.setattr(main_module, "db", types.SimpleNamespace(connect=lambda: _RaceConn()))
    r = client.post("/auth/register", json={"email": "racer@example.com", "password": TEST_PASSWORD})
    assert r.status_code == 400
    assert "in use" in r.json()["detail"].lower()


def test_me_rejects_refresh_token(client):
    # Identity is bounded by the 1h access lifetime; the 7d token is only for
    # the future refresh flow and proves nothing here.
    body = _register(client).json()
    r = client.get("/auth/me", headers={"Authorization": f"Bearer {body['refresh_token']}"})
    assert r.status_code == 401


def test_passwords_past_72_bytes_fully_counted(client):
    # bcrypt sees only 72 bytes; the SHA-256 pre-hash means the tail still
    # counts -- same 72-byte prefix with a different tail must NOT verify.
    prefix = "x" * 72
    good, bad = prefix + "-tail-one", prefix + "-tail-two"
    assert client.post("/auth/register", json={"email": "long@example.com", "password": good}).status_code == 200
    assert client.post("/auth/login", json={"email": "long@example.com", "password": good}).status_code == 200
    assert client.post("/auth/login", json={"email": "long@example.com", "password": bad}).status_code == 401
