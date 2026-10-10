"""Password hashing, usernames, and session tokens."""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, select

from app.db.master_models import AuthSession, User

_USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,31}$")

SESSION_DAYS_BROWSER = 1  # sign-in without remember me (this device session)
SESSION_DAYS_REMEMBER = 365  # local install — keep signed in
MIN_PASSWORD_LENGTH = 4
PBKDF2_ITERATIONS = 120_000


def normalize_username(username: str) -> str:
    return username.strip().lower()


def validate_username(username: str) -> None:
    normalized = normalize_username(username)
    if not _USERNAME_RE.match(normalized):
        raise ValueError(
            "Username must be 3–32 characters: lowercase letters, numbers, underscore, or hyphen"
        )


def validate_password_length(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"PIN or password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > 128:
        raise ValueError("PIN or password must be at most 128 characters")


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PBKDF2_ITERATIONS,
    )
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, hex_digest = stored.split("$", 1)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PBKDF2_ITERATIONS,
    )
    return secrets.compare_digest(digest.hex(), hex_digest)


def create_session(session: Session, user: User, *, remember_me: bool = True) -> AuthSession:
    token = secrets.token_urlsafe(32)
    days = SESSION_DAYS_REMEMBER if remember_me else SESSION_DAYS_BROWSER
    row = AuthSession(
        token=token,
        user_id=user.id,
        expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(days=days),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def user_from_token(session: Session, token: str | None) -> User | None:
    if not token:
        return None
    row = session.exec(select(AuthSession).where(AuthSession.token == token)).first()
    if not row or row.expires_at < datetime.now(UTC).replace(tzinfo=None):
        return None
    return session.get(User, row.user_id)
