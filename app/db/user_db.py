"""Per-user SQLite databases for isolated household data."""

from __future__ import annotations

import threading
from collections.abc import Generator
from functools import lru_cache
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

from app.config import settings

USERS_DIR = settings.data_dir / "users"
MASTER_DB_PATH = settings.data_dir / "master.db"

# Per-process record of which users' schema has already been created/migrated.
# ensure_user_db() runs create_all + all idempotent migrations (including writes),
# so doing it on every request causes write-lock contention and slow responses
# when the frontend fires many calls at once. The engine is process-cached and
# migrations are idempotent, so ensuring once per user per process is sufficient;
# reset flows clear this set via clear_engine_caches().
_ensured_users: set[str] = set()
_ensure_lock = threading.Lock()


def user_db_path(user_id: str) -> Path:
    return USERS_DIR / user_id / "ledgermind.db"


@lru_cache(maxsize=64)
def user_engine(user_id: str):
    path = user_db_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{path}"
    eng = create_engine(url, echo=False, connect_args={"check_same_thread": False})
    return eng


def _user_db_has_schema(user_id: str) -> bool:
    """True when the per-user DB has household tables (not an empty SQLite file)."""
    path = user_db_path(user_id)
    if not path.exists() or path.stat().st_size == 0:
        return False
    from sqlalchemy import text

    eng = user_engine(user_id)
    with eng.connect() as conn:
        row = conn.execute(
            text(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='onboarding_state' LIMIT 1"
            )
        ).first()
    return row is not None


def init_user_db(user_id: str) -> None:
    from app.db import models  # noqa: F401
    from app.db.session import apply_sqlite_migrations

    path = user_db_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size == 0:
        path.unlink()

    eng = user_engine(user_id)
    SQLModel.metadata.create_all(eng)
    apply_sqlite_migrations(eng)


def ensure_user_db(user_id: str) -> None:
    """Create or repair per-user schema (handles 0-byte files after reset)."""
    if not _user_db_has_schema(user_id):
        init_user_db(user_id)
        return
    from app.db import models  # noqa: F401

    eng = user_engine(user_id)
    # Idempotent: adds tables/columns introduced after the user first registered.
    SQLModel.metadata.create_all(eng)
    if settings.is_sqlite:
        from app.db.session import apply_sqlite_migrations

        apply_sqlite_migrations(eng)


def get_user_session(user_id: str) -> Generator[Session, None, None]:
    # Fast path: skip the expensive create_all + migrations once the schema has
    # been ensured for this user in this process. Double-checked locking keeps the
    # initial concurrent request burst from running the migration N times at once.
    if user_id not in _ensured_users:
        with _ensure_lock:
            if user_id not in _ensured_users:
                ensure_user_db(user_id)
                _ensured_users.add(user_id)
    from app.events.bus import event_bus

    with Session(user_engine(user_id)) as session:
        event_bus.set_session(session)
        try:
            yield session
        finally:
            event_bus.set_session(None)


def clear_engine_caches() -> None:
    user_engine.cache_clear()
    master_engine.cache_clear()
    with _ensure_lock:
        _ensured_users.clear()


@lru_cache(maxsize=1)
def master_engine():
    MASTER_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{MASTER_DB_PATH}"
    return create_engine(url, echo=False, connect_args={"check_same_thread": False})


def _username_from_email_or_id(email: str | None, user_id: str) -> str:
    local = (email or "").strip().lower().split("@", 1)[0] if email else user_id[:8]
    safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in local)[:32]
    if len(safe) < 3:
        safe = f"user_{user_id[:8]}"
    return safe


def _sqlite_migrate_users_username(eng) -> None:
    """Migrate legacy email column to username for existing master.db files."""
    from sqlalchemy import text

    with eng.connect() as conn:
        rows = conn.execute(text("PRAGMA table_info(users)")).fetchall()
        cols = {r[1] for r in rows}
        if "username" in cols:
            return
        if "email" not in cols:
            return
        conn.execute(text("ALTER TABLE users ADD COLUMN username TEXT"))
        for row in conn.execute(text("SELECT id, email FROM users")).fetchall():
            uid, email = row[0], row[1]
            conn.execute(
                text("UPDATE users SET username = :u WHERE id = :id"),
                {"u": _username_from_email_or_id(email, uid), "id": uid},
            )
        conn.commit()


def _sqlite_drop_users_email(eng) -> None:
    """Remove legacy email column after username migration (blocks new signups if left)."""
    from sqlalchemy import text

    with eng.connect() as conn:
        rows = conn.execute(text("PRAGMA table_info(users)")).fetchall()
        cols = {r[1] for r in rows}
        if "email" not in cols:
            return
        if "username" in cols:
            for row in conn.execute(text("SELECT id, email, username FROM users")).fetchall():
                uid, email, username = row[0], row[1], row[2]
                if not username:
                    conn.execute(
                        text("UPDATE users SET username = :u WHERE id = :id"),
                        {"u": _username_from_email_or_id(email, uid), "id": uid},
                    )
        conn.execute(text("DROP INDEX IF EXISTS ix_users_email"))
        conn.execute(text("ALTER TABLE users DROP COLUMN email"))
        conn.execute(
            text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_username ON users (username)")
        )
        conn.commit()


def init_master_db() -> None:
    from app.db.master_models import AuthSession, User

    eng = master_engine()
    SQLModel.metadata.create_all(eng, tables=[User.__table__, AuthSession.__table__])
    if settings.is_sqlite:
        _sqlite_migrate_users_username(eng)
        _sqlite_drop_users_email(eng)
