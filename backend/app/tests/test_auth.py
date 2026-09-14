"""Auth sprint tests: AC01-07 plus the 1h access / 7d refresh lifetimes.

Run from the repo root:
    python3 -m pytest backend/test_auth.py -v
    # or without pytest:
    python3 -m unittest backend.test_auth -v

Each test gets a fresh temp SQLite file via DATACHESS_DB_PATH (read lazily
per connection, so monkeypatched env just works) and a fixed JWT secret.
"""

import pathlib
import sqlite3
import sys
import os
import jwt
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from app.main import app  # noqa: E402
from app import security  # noqa: E402

TEST_SECRET = os.environ.get("DATACHESS_JWT_SECRET")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATACHESS_DB_PATH", str(tmp_path / "auth.db"))
    monkeypatch.setenv("DATACHESS_JWT_SECRET", TEST_SECRET)
    return TestClient(app)


def _register(client, email="owner@example.com", password="s3cret-pw"):
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
    _register(client, password="s3cret-pw")
    (uid, email, stored, is_admin, created) = _stored_user_row()
    assert stored != "s3cret-pw"
    assert "s3cret-pw" not in str(_stored_user_row())
    assert stored.startswith("$2b$")  # bcrypt one-way hash
    assert security.verify_password("s3cret-pw", stored) is True
    assert security.verify_password("wrong-pw", stored) is False


def test_03_login_returns_token(client):
    _register(client)
    r = client.post("/auth/login", json={"email": "owner@example.com", "password": "s3cret-pw"})
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
