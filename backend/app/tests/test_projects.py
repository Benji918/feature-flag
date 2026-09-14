"""Project sprint tests: AC01/02/03/05/06 (AC04 lives in test_schema.py,
as a storage-layer test with no application code involved).

Run from the repo root:
    python3 -m pytest backend/app/tests/test_projects.py -v
"""

import hashlib
import os
import pathlib
import secrets
import sqlite3
import sys

import pytest
from fastapi.testclient import TestClient

# backend/ on sys.path so `import app` resolves regardless of CWD or runner.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from app.main import app  # noqa: E402

TEST_PASSWORD = "correct-horse-battery-staple"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATACHESS_DB_PATH", str(tmp_path / "projects.db"))
    monkeypatch.setenv("DATACHESS_JWT_SECRET", secrets.token_hex(32))
    return TestClient(app)


def _owner(client, email="owner@example.com"):
    r = client.post("/auth/register", json={"email": email, "password": TEST_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_01_create_returns_key_once(client):
    h = _owner(client)
    r = client.post("/projects", json={"name": "my-node-app"}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "my-node-app"
    assert body["repo_url"] is None
    assert body["id"]
    assert body["api_key"]  # the one and only appearance of the raw key

    r2 = client.post(
        "/projects", json={"name": "other", "repo_url": "https://git/x/y"}, headers=h
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["repo_url"] == "https://git/x/y"
    assert r2.json()["api_key"] != body["api_key"]


def test_01_name_required(client):
    h = _owner(client)
    assert client.post("/projects", json={"name": ""}, headers=h).status_code == 400
    assert client.post("/projects", json={"name": "   "}, headers=h).status_code == 400
    assert client.post("/projects", json={}, headers=h).status_code in (400, 422)
    assert client.post("/projects", json={"name": "x"}).status_code == 401


def test_02_key_never_shown_again(client):
    h = _owner(client)
    pid = client.post("/projects", json={"name": "p"}, headers=h).json()["id"]
    for body in (
        client.get("/projects", headers=h).json(),
        client.get(f"/projects/{pid}", headers=h).json(),
        client.get(f"/projects/{pid}", headers=h).json(),
    ):
        assert "api_key" not in str(body)


def test_03_stored_data_cannot_reproduce_key(client):
    h = _owner(client)
    raw = client.post("/projects", json={"name": "p"}, headers=h).json()["api_key"]
    conn = sqlite3.connect(os.environ["DATACHESS_DB_PATH"])
    conn.row_factory = sqlite3.Row
    try:
        dump = str([dict(r) for r in conn.execute("SELECT * FROM projects").fetchall()])
        dump += str([dict(r) for r in conn.execute("SELECT * FROM users").fetchall()])
        dump += str(conn.execute("SELECT sql FROM sqlite_master").fetchall())
    finally:
        conn.close()
    # The raw key appears nowhere the platform stores...
    assert raw not in dump
    # ...and what is stored is exactly the one-way hash of it.
    conn = sqlite3.connect(os.environ["DATACHESS_DB_PATH"])
    try:
        (stored,) = conn.execute("SELECT api_key_hash FROM projects").fetchone()
    finally:
        conn.close()
    assert stored == hashlib.sha256(raw.encode()).hexdigest()


def test_05_list_is_owner_scoped(client):
    a = _owner(client, "a@example.com")
    b = _owner(client, "b@example.com")
    client.post("/projects", json={"name": "a-one"}, headers=a)
    client.post("/projects", json={"name": "a-two"}, headers=a)
    client.post("/projects", json={"name": "b-one"}, headers=b)
    names_a = sorted(p["name"] for p in client.get("/projects", headers=a).json())
    names_b = sorted(p["name"] for p in client.get("/projects", headers=b).json())
    assert names_a == ["a-one", "a-two"]
    assert names_b == ["b-one"]
    assert client.get("/projects").status_code == 401


def test_06_detail_shows_empty_flags(client):
    h = _owner(client)
    pid = client.post("/projects", json={"name": "p", "repo_url": "https://git/x"}, headers=h).json()["id"]
    r = client.get(f"/projects/{pid}", headers=h)
    assert r.status_code == 200, r.text
    assert r.json() == {
        "id": pid,
        "name": "p",
        "repo_url": "https://git/x",
        "created_at": r.json()["created_at"],
        "flags": [],
    }


def test_06_non_owner_sees_same_as_missing(client):
    h = _owner(client)
    other = _owner(client, "other@example.com")
    pid = client.post("/projects", json={"name": "p"}, headers=h).json()["id"]
    as_stranger = client.get(f"/projects/{pid}", headers=other)
    as_missing = client.get("/projects/999999", headers=other)
    assert as_stranger.status_code == 404
    assert as_stranger.json() == as_missing.json()
    assert client.get(f"/projects/{pid}").status_code == 401
