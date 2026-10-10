"""Web Push subscription API."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import Session

from app.api.deps import CurrentUser, get_db
from app.api.routes_settings import _user_id_for_ai
from app.core.web_push import (
    disable_subscription,
    public_vapid_key,
    send_test_push,
    subscription_to_dict,
    upsert_subscription,
    vapid_configured,
)

router = APIRouter(prefix="/api/push", tags=["push"])


class PushKeysBody(BaseModel):
    p256dh: str
    auth: str


class SubscribeBody(BaseModel):
    endpoint: str
    keys: PushKeysBody
    expirationTime: int | None = None
    device_label: str = ""


class UnsubscribeBody(BaseModel):
    endpoint: str


@router.get("/public-key")
def api_public_key():
    if not vapid_configured():
        raise HTTPException(status_code=503, detail="Web Push is not configured (missing VAPID keys)")
    return {"public_key": public_vapid_key()}


@router.post("/subscribe")
def api_subscribe(
    body: SubscribeBody,
    user: CurrentUser,
    request: Request,
    session: Session = Depends(get_db),
):
    user_id = _user_id_for_ai(user)
    if not body.endpoint or not body.keys.p256dh or not body.keys.auth:
        raise HTTPException(status_code=400, detail="Invalid push subscription")
    row = upsert_subscription(
        session,
        user_id=user_id,
        endpoint=body.endpoint,
        p256dh_key=body.keys.p256dh,
        auth_key=body.keys.auth,
        user_agent=request.headers.get("user-agent", ""),
        device_label=body.device_label,
    )
    session.commit()
    session.refresh(row)
    return subscription_to_dict(row)


@router.delete("/unsubscribe")
def api_unsubscribe(body: UnsubscribeBody, session: Session = Depends(get_db)):
    if not body.endpoint:
        raise HTTPException(status_code=400, detail="endpoint required")
    ok = disable_subscription(session, body.endpoint)
    session.commit()
    return {"ok": ok}


@router.post("/test")
def api_test_push(user: CurrentUser, session: Session = Depends(get_db)):
    if not vapid_configured():
        raise HTTPException(status_code=503, detail="Web Push is not configured (missing VAPID keys)")
    user_id = _user_id_for_ai(user)
    result = send_test_push(session, user_id=user_id)
    session.commit()
    if result.get("skipped") == "no_subscriptions":
        raise HTTPException(
            status_code=400,
            detail="No push subscription on this account — enable notifications first",
        )
    return result
