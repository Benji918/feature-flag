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
        # Scope comes from the schema itself, not a hand-written table list:
        # every table, every row, plus the stored DDL. A leak into any table
        # -- including ones added by later tickets -- fails this proof.
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name != 'sqlite_sequence'"
            ).fetchall()
        ]
        assert set(tables) >= {"users", "projects", "feature_flags", "audit_log_entries"}
        dump = "".join(
            str([dict(r) for r in conn.execute(f'SELECT * FROM "{t}"').fetchall()])
            for t in tables
        )
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


def test_06_detail_serializes_flags_with_data(client):    # The empty case never runs the serialization loop, so the flag shape the
    # README declares fixed would otherwise go unpinned until sync lands. A
    # flag row is inserted directly (no sync endpoint exists yet) and the
    # full serialized JSON -- names, values, bool conversions -- is asserted.
    h = _owner(client)
    pid = client.post("/projects", json={"name": "p"}, headers=h).json()["id"]
    conn = sqlite3.connect(os.environ["DATACHESS_DB_PATH"])
    try:
        conn.execute(
            'INSERT INTO feature_flags (project_id, "key", description, enabled,'
            " rollout_percentage, default_value) VALUES (?, ?, ?, ?, ?, ?)",
            (pid, "dark_mode", "Dark theme toggle", 1, 50, 0),
        )
        conn.commit()
    finally:
        conn.close()
    r = client.get(f"/projects/{pid}", headers=h)
    assert r.status_code == 200, r.text
    (flag,) = r.json()["flags"]
    assert flag["key"] == "dark_mode"
    assert flag["description"] == "Dark theme toggle"
    assert flag["enabled"] is True
    assert flag["rollout_percentage"] == 50
    assert flag["default_value"] is False
    assert flag["created_at"]
    assert flag["definition_updated_at"]


def test_key_collision_retries_with_fresh_key(client, monkeypatch):
    # A genuine hash collision retries with a new key and succeeds -- the
    # creation still carries a key, not a 500.
    import app.routes.projects as proj_routes

    h = _owner(client)
    assert client.get("/projects", headers=h).status_code == 200  # token works
    colliding_raw, colliding_digest = "raw-colliding-key", "cc" * 32
    conn = sqlite3.connect(os.environ["DATACHESS_DB_PATH"])
    try:
        (owner_id,) = conn.execute("SELECT id FROM users WHERE email='owner@example.com'").fetchone()
        conn.execute(
            "INSERT INTO projects (user_id, name, api_key_hash) VALUES (?, ?, ?)",
            (owner_id, "seed", colliding_digest),
        )
        conn.commit()
    finally:
        conn.close()
    real_mint = proj_routes._mint_key
    calls = iter([(colliding_raw, colliding_digest)])
    monkeypatch.setattr(
        proj_routes, "_mint_key", lambda: next(calls, None) or real_mint()
    )
    r = client.post("/projects", json={"name": "retry-win"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["api_key"] != colliding_raw


def test_non_hash_integrity_error_is_not_a_key_error(client, monkeypatch):
    # The retry loop must read the constraint message: any IntegrityError
    # that is NOT the hash collision propagates instead of being retried
    # into a misleading 500 about keys.
    import types

    import app.routes.projects as proj_routes

    h = _owner(client)

    class _BoomConn:
        def execute(self, sql, params=()):
            raise sqlite3.IntegrityError("NOT NULL constraint failed: projects.name")

        def close(self):
            pass

    monkeypatch.setattr(
        proj_routes, "db", types.SimpleNamespace(connect=lambda: _BoomConn())
    )
    with pytest.raises(sqlite3.IntegrityError):
        client.post("/projects", json={"name": "p"}, headers=h)
