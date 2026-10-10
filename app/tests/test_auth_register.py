"""Registration against legacy master.db schemas."""

import sqlite3

import pytest
from fastapi.testclient import TestClient
from sqlmodel import create_engine

from app.db.user_db import clear_engine_caches, init_master_db


@pytest.fixture
def legacy_email_master_db(tmp_path, monkeypatch):
    """Simulate pre-username master.db: users.email NOT NULL, no username column."""
    db_path = tmp_path / "master.db"
    monkeypatch.setattr("app.db.user_db.MASTER_DB_PATH", db_path)
    clear_engine_caches()

    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE users (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            display_name TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE auth_sessions (
            id TEXT PRIMARY KEY,
            token TEXT NOT NULL UNIQUE,
            user_id TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        INSERT INTO users (id, email, password_hash, display_name)
        VALUES ('legacy-user', 'owner@example.com', 'hash', 'Owner')
        """
    )
    conn.execute("CREATE UNIQUE INDEX ix_users_email ON users (email)")
    conn.commit()
    conn.close()

    init_master_db()
    yield db_path
    clear_engine_caches()


def test_register_after_legacy_email_schema_migration(legacy_email_master_db, monkeypatch):
    monkeypatch.delenv("LEDGERMIND_AUTH_DISABLED", raising=False)
    from app.main import app

    client = TestClient(app)
    res = client.post(
        "/api/auth/register",
        json={
            "username": "newuser",
            "password": "1234",
            "display_name": "New User",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["user"]["username"] == "newuser"
    assert body["onboarding_required"] is True

    eng = create_engine(f"sqlite:///{legacy_email_master_db}")
    with eng.connect() as conn:
        from sqlalchemy import text

        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(users)")).fetchall()}
        assert "email" not in cols
        assert "username" in cols
        rows = conn.execute(text("SELECT username FROM users WHERE username = 'newuser'")).fetchall()
        assert len(rows) == 1
