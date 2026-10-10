"""Notification inbox API."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.api.deps import CurrentUser, get_db
from app.api.routes_settings import _user_id_for_ai
from app.core.notifications import (
    dismiss_notification,
    list_inbox,
    snooze_notification,
    sync_notifications,
)

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


class DismissBody(BaseModel):
    reason: str = ""


class SnoozeBody(BaseModel):
    days: int = Field(default=1, ge=1, le=30)


@router.get("/inbox")
def api_inbox(
    user: CurrentUser,
    session: Session = Depends(get_db),
    include_dismissed: bool = Query(False),
    sync: bool = Query(True, description="Refresh alerts from household signals first"),
):
    user_id = _user_id_for_ai(user)
    if sync:
        sync_notifications(session, user_id=user_id)
    return list_inbox(session, include_dismissed=include_dismissed)


@router.post("/sync")
def api_sync(user: CurrentUser, session: Session = Depends(get_db)):
    user_id = _user_id_for_ai(user)
    return sync_notifications(session, user_id=user_id)


@router.post("/{notification_id}/dismiss")
def api_dismiss(
    notification_id: str,
    body: DismissBody,
    session: Session = Depends(get_db),
):
    try:
        return dismiss_notification(session, notification_id, reason=body.reason)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{notification_id}/snooze")
def api_snooze(
    notification_id: str,
    body: SnoozeBody,
    session: Session = Depends(get_db),
):
    try:
        return snooze_notification(session, notification_id, days=body.days)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
