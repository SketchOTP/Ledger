"""Master database — users and auth sessions (not household financial data)."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlmodel import Field, SQLModel


def _uuid() -> str:
    return str(uuid4())


def _now() -> datetime:
    # Naive UTC for SQLite DateTime columns (matches legacy utcnow storage).
    return datetime.now(UTC).replace(tzinfo=None)


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: str = Field(default_factory=_uuid, primary_key=True)
    username: str = Field(index=True, unique=True)
    password_hash: str
    display_name: str = ""
    created_at: datetime = Field(default_factory=_now)


class AuthSession(SQLModel, table=True):
    __tablename__ = "auth_sessions"

    id: str = Field(default_factory=_uuid, primary_key=True)
    token: str = Field(index=True, unique=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    expires_at: datetime
    created_at: datetime = Field(default_factory=_now)
