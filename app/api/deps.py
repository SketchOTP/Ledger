"""API dependencies."""

import os
from collections.abc import Generator
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlmodel import Session

from app.core.auth import user_from_token
from app.core.user_ai_settings import bind_user_ai_settings
from app.db.master_models import User
from app.db.session import get_session
from app.db.user_db import get_user_session, master_engine

AUTH_DISABLED = os.getenv("LEDGERMIND_AUTH_DISABLED", "").lower() in ("1", "true", "yes")


def get_master_db() -> Generator[Session, None, None]:
    with Session(master_engine()) as session:
        yield session


def _token_from_request(request: Request) -> str | None:
    auth = request.headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("X-Auth-Token") or request.cookies.get("lm_token")


def get_current_user(
    request: Request,
    master: Session = Depends(get_master_db),
) -> User:
    if AUTH_DISABLED:
        dev = User(
            id="dev-local",
            username="dev",
            password_hash="",
            display_name="Dev",
        )
        request.state.user = dev
        bind_user_ai_settings("dev-local")
        return dev
    user = user_from_token(master, _token_from_request(request))
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    request.state.user = user
    bind_user_ai_settings(user.id)
    return user


def get_db(
    user: User = Depends(get_current_user),
) -> Generator[Session, None, None]:
    if AUTH_DISABLED:
        yield from get_session()
        return
    yield from get_user_session(user.id)


CurrentUser = Annotated[User, Depends(get_current_user)]
