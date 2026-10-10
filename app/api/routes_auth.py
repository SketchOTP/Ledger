"""Register, login, and session management."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.api.deps import _token_from_request, get_current_user, get_master_db
from app.core.auth import (
    create_session,
    hash_password,
    normalize_username,
    validate_password_length,
    validate_username,
    verify_password,
)
from app.core.onboarding import is_onboarding_complete
from app.db.master_models import AuthSession, User
from app.db.user_db import ensure_user_db, init_user_db, user_engine

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _user_payload(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
    }


class RegisterBody(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=4, max_length=128)
    display_name: str = ""
    remember_me: bool = True


class LoginBody(BaseModel):
    username: str
    password: str
    remember_me: bool = True


@router.post("/register")
def register(body: RegisterBody, master: Session = Depends(get_master_db)):
    try:
        validate_username(body.username)
        validate_password_length(body.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    username = normalize_username(body.username)
    if master.exec(select(User).where(User.username == username)).first():
        raise HTTPException(status_code=400, detail="Username already taken")
    user = User(
        username=username,
        password_hash=hash_password(body.password),
        display_name=body.display_name.strip() or username,
    )
    master.add(user)
    master.commit()
    master.refresh(user)
    init_user_db(user.id)
    session = create_session(master, user, remember_me=body.remember_me)
    return {
        "token": session.token,
        "user": _user_payload(user),
        "onboarding_required": True,
        "remember_me": body.remember_me,
    }


@router.post("/login")
def login(body: LoginBody, master: Session = Depends(get_master_db)):
    username = normalize_username(body.username)
    user = master.exec(select(User).where(User.username == username)).first()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or PIN")
    ensure_user_db(user.id)
    session = create_session(master, user, remember_me=body.remember_me)
    with Session(user_engine(user.id)) as user_db:
        done = is_onboarding_complete(user_db)
    return {
        "token": session.token,
        "user": _user_payload(user),
        "onboarding_required": not done,
        "remember_me": body.remember_me,
    }


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    ensure_user_db(user.id)
    with Session(user_engine(user.id)) as user_db:
        done = is_onboarding_complete(user_db)
    return {**_user_payload(user), "onboarding_required": not done}


@router.post("/logout")
def logout(request: Request, master: Session = Depends(get_master_db)):
    token = _token_from_request(request)
    if token:
        row = master.exec(select(AuthSession).where(AuthSession.token == token)).first()
        if row:
            master.delete(row)
            master.commit()
    return {"ok": True}
